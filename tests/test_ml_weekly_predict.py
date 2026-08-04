"""
tests/test_ml_weekly_predict.py

Regression tests for the ml_weekly prediction pipeline. The primary test in
this file (test_assemble_predictions_never_swaps_tickers) guards against a
real production bug found by forensic audit: predict_for_date used to
rebuild its feature dataframe a SECOND time, independently of the one
ensemble_predict_proba had already scored, then reused the first build's
probability array by raw array position against the second build's row
order. When the two builds ordered rows differently, a ticker could
silently receive a different ticker's probability — observed in
production as two unrelated stocks (RLCO, BEEF) getting byte-identical
ensemble breakdowns. _assemble_predictions() replaced that indirection;
these tests exist so it can never quietly regress.

Run with: pytest tests/test_ml_weekly_predict.py -v
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ml_weekly.predict import (
    _assemble_predictions, ensemble_predict_proba,
    compute_decision_status, _confidence_tier_capped, _compute_net_risk_reward,
)
from ml_weekly.pdf_report import _add_trading_days


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def mini_db(tmp_path: Path) -> str:
    """A throwaway SQLite DB with just enough schema for _assemble_predictions
    to run: a few days of price history for three tickers, and a listing
    board lookup. Does not touch the real application database."""
    db_path = str(tmp_path / "mini.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE ringkasan_saham_harian (
            tanggal TEXT, kode_saham TEXT, harga_pembukaan REAL,
            harga_tertinggi REAL, harga_terendah REAL, harga_penutupan REAL,
            sebelumnya REAL, volume INTEGER, nilai_transaksi REAL
        )
    """)
    conn.execute("CREATE TABLE idx_stocks (code TEXT, listing_board TEXT)")

    tickers = {"AAAA": 1000.0, "BBBB": 2000.0, "CCCC": 500.0}
    dates = ["2026-07-20", "2026-07-21", "2026-07-22", "2026-07-23", "2026-07-24", "2026-07-27"]
    for t, base in tickers.items():
        conn.execute("INSERT INTO idx_stocks VALUES (?, ?)", (t, "Main"))
        for i, d in enumerate(dates):
            px = base * (1 + 0.001 * i)
            conn.execute(
                "INSERT INTO ringkasan_saham_harian VALUES (?,?,?,?,?,?,?,?,?)",
                (d, t, px, px * 1.01, px * 0.99, px, px * 0.995, 100000, px * 100000),
            )
    conn.commit()
    conn.close()
    return db_path


def _feat_df(tickers: list[str]) -> pd.DataFrame:
    return pd.DataFrame({
        "ticker": tickers,
        "dummy_feat": [float(i) for i in range(len(tickers))],
    })


# ── Core alignment invariant ─────────────────────────────────────────────────

def test_assemble_predictions_never_swaps_tickers(mini_db):
    """Each ticker must receive exactly the probability at its own row
    index — not another ticker's, regardless of row order."""
    as_of = "2026-07-27"
    feature_cols = ["dummy_feat"]

    # Deliberately NOT alphabetical / NOT insertion order.
    order = ["BBBB", "AAAA", "CCCC"]
    feat_df = _feat_df(order)
    probs = np.array([0.11, 0.77, 0.33])  # index-aligned to `order`

    results = _assemble_predictions(
        feat_df, probs, as_of, mini_db, model_run_id=1,
        feature_cols=feature_cols, threshold=0.5,
    )
    by_ticker = {r["ticker"]: r["calibrated_probability"] for r in results}

    assert by_ticker["BBBB"] == pytest.approx(0.11, abs=1e-4)
    assert by_ticker["AAAA"] == pytest.approx(0.77, abs=1e-4)
    assert by_ticker["CCCC"] == pytest.approx(0.33, abs=1e-4)


def test_assemble_predictions_consistent_under_row_reordering(mini_db):
    """Reversing row order (as a second, independent build would) must
    still assign the correct probability to each ticker — proving
    correctness isn't an accident of one particular ordering."""
    as_of = "2026-07-27"
    feature_cols = ["dummy_feat"]

    order_a = ["BBBB", "AAAA", "CCCC"]
    probs_a = np.array([0.11, 0.77, 0.33])
    results_a = _assemble_predictions(
        _feat_df(order_a), probs_a, as_of, mini_db, model_run_id=1,
        feature_cols=feature_cols, threshold=0.5,
    )

    order_b = list(reversed(order_a))
    probs_b = np.array(list(reversed(probs_a)))
    results_b = _assemble_predictions(
        _feat_df(order_b), probs_b, as_of, mini_db, model_run_id=1,
        feature_cols=feature_cols, threshold=0.5,
    )

    a_map = {r["ticker"]: r["calibrated_probability"] for r in results_a}
    b_map = {r["ticker"]: r["calibrated_probability"] for r in results_b}
    assert a_map == pytest.approx(b_map, abs=1e-4)


def test_assemble_predictions_rejects_length_mismatch(mini_db):
    """If probs and feat_df disagree on length, fail loudly instead of
    guessing an alignment."""
    feat_df = _feat_df(["AAAA", "BBBB", "CCCC"])
    bad_probs = np.array([0.5, 0.5])  # wrong length
    with pytest.raises(ValueError):
        _assemble_predictions(
            feat_df, bad_probs, "2026-07-27", mini_db, model_run_id=1,
            feature_cols=["dummy_feat"], threshold=0.5,
        )


def test_ensemble_breakdown_stays_aligned_per_ticker(mini_db):
    """Per-model probabilities embedded in ensemble_probs_json must match
    the ticker they were actually computed for."""
    as_of = "2026-07-27"
    order = ["CCCC", "BBBB", "AAAA"]
    feat_df = _feat_df(order)
    probs = np.array([0.5, 0.5, 0.5])
    breakdown = [
        {"model_name": "logistic", "probs": np.array([0.1, 0.2, 0.3])},
        {"model_name": "random_forest", "probs": np.array([0.9, 0.8, 0.7])},
    ]

    results = _assemble_predictions(
        feat_df, probs, as_of, mini_db, model_run_id=1,
        feature_cols=["dummy_feat"], threshold=0.5, ensemble_breakdown=breakdown,
    )
    by_ticker = {r["ticker"]: json.loads(r["ensemble_probs_json"]) for r in results}

    assert by_ticker["CCCC"]["logistic"] == pytest.approx(0.1, abs=1e-4)
    assert by_ticker["BBBB"]["logistic"] == pytest.approx(0.2, abs=1e-4)
    assert by_ticker["AAAA"]["logistic"] == pytest.approx(0.3, abs=1e-4)
    assert by_ticker["CCCC"]["random_forest"] == pytest.approx(0.9, abs=1e-4)

    # Two DIFFERENT tickers with genuinely different features must not
    # receive identical breakdown dicts (the exact symptom that surfaced
    # the original bug in production).
    dicts_as_json = [r["ensemble_probs_json"] for r in results]
    assert len(set(dicts_as_json)) == len(dicts_as_json), "two tickers got identical ensemble breakdowns"


# ── Missing/failed model must not become a fake 0% ──────────────────────────

def test_failed_model_is_excluded_not_zeroed():
    """A model whose predict_proba() raises must be dropped from the
    ensemble, not silently recorded as a legitimate 0% probability —
    those are semantically very different (model error vs. genuine
    high-confidence bearish read)."""
    X_df = pd.DataFrame({"ticker": ["AAAA", "BBBB"], "f1": [1.0, 2.0]})

    class WorkingModel:
        def predict_proba(self, X):
            n = len(X)
            return np.column_stack([np.full(n, 0.4), np.full(n, 0.6)])

    class BrokenModel:
        def predict_proba(self, X):
            raise RuntimeError("simulated inference failure")

    members = [
        {"model_name": "working", "model_run_id": 1, "feature_cols": ["f1"],
         "pipe": WorkingModel(), "weight": 1.0},
        {"model_name": "broken", "model_run_id": 2, "feature_cols": ["f1"],
         "pipe": BrokenModel(), "weight": 1.0},
    ]

    avg_probs, breakdown = ensemble_predict_proba(members, X_df)

    model_names_in_breakdown = {b["model_name"] for b in breakdown}
    assert "broken" not in model_names_in_breakdown, "failed model must be excluded, not zeroed"
    assert "working" in model_names_in_breakdown
    # Average should equal the single surviving model's output, not be
    # diluted by a phantom 0.0 from the broken one.
    assert avg_probs[0] == pytest.approx(0.6, abs=1e-4)


# ── Decision status gating ───────────────────────────────────────────────────

def test_unvalidated_holdout_never_yields_qualified_or_sangat_kuat():
    """The single most important honesty rule in this system: a holdout
    that has never cleared the precision bar must never produce a
    QUALIFIED decision or a 'Sangat Kuat' label, no matter how high the
    raw probability is."""
    status, tier, _reason = compute_decision_status(
        prob=0.95, n_models_expected=6, n_models_contributed=6,
        holdout_status="NOT_VERIFIED", net_risk_reward=2.0, expected_value=0.05,
        has_corp_action_warning=False,
    )
    assert status != "QUALIFIED"
    assert tier != "SANGAT_KUAT"


def test_verified_holdout_with_full_coverage_can_qualify():
    status, tier, _reason = compute_decision_status(
        prob=0.90, n_models_expected=6, n_models_contributed=6,
        holdout_status="VERIFIED_ABOVE_90", net_risk_reward=2.0, expected_value=0.05,
        has_corp_action_warning=False,
    )
    assert status == "QUALIFIED"
    assert tier == "SANGAT_KUAT"


def test_single_model_coverage_caps_tier_even_if_validated():
    """One model out of six contributing is thin evidence regardless of
    what the ensemble's own holdout achieved."""
    status, tier, reason = compute_decision_status(
        prob=0.95, n_models_expected=6, n_models_contributed=1,
        holdout_status="VERIFIED_ABOVE_90", net_risk_reward=2.0, expected_value=0.05,
        has_corp_action_warning=False,
    )
    assert status != "QUALIFIED"
    assert tier != "SANGAT_KUAT"
    assert "model" in reason.lower()


def test_corp_action_warning_forces_no_trade():
    status, _tier, reason = compute_decision_status(
        prob=0.99, n_models_expected=6, n_models_contributed=6,
        holdout_status="VERIFIED_ABOVE_90", net_risk_reward=5.0, expected_value=0.10,
        has_corp_action_warning=True,
    )
    assert status == "NO_TRADE"
    assert "corporate action" in reason.lower()


def test_negative_expected_value_forces_no_trade():
    status, _tier, _reason = compute_decision_status(
        prob=0.60, n_models_expected=6, n_models_contributed=6,
        holdout_status="VERIFIED_ABOVE_90", net_risk_reward=0.5, expected_value=-0.01,
        has_corp_action_warning=False,
    )
    assert status == "NO_TRADE"


def test_low_probability_abstains_not_no_trade():
    status, _tier, _reason = compute_decision_status(
        prob=0.30, n_models_expected=6, n_models_contributed=6,
        holdout_status="VERIFIED_ABOVE_90", net_risk_reward=2.0, expected_value=0.02,
        has_corp_action_warning=False,
    )
    assert status == "ABSTAIN"


def test_thin_feature_coverage_is_data_invalid():
    """A ticker whose real technical features are mostly missing (padded
    to NaN then median-imputed) must not be scored with the same
    confidence as one with full coverage — even if every other gate would
    otherwise pass."""
    status, tier, reason = compute_decision_status(
        prob=0.90, n_models_expected=6, n_models_contributed=6,
        holdout_status="VERIFIED_ABOVE_90", net_risk_reward=2.0, expected_value=0.05,
        has_corp_action_warning=False, feature_coverage=0.2,
    )
    assert status == "DATA_INVALID"
    assert tier == "LEMAH"
    assert "fitur" in reason.lower()


def test_no_contributing_models_is_model_unavailable():
    status, _tier, _reason = compute_decision_status(
        prob=0.80, n_models_expected=6, n_models_contributed=0,
        holdout_status="VERIFIED_ABOVE_90", net_risk_reward=2.0, expected_value=0.05,
        has_corp_action_warning=False,
    )
    assert status == "MODEL_UNAVAILABLE"


def test_net_risk_reward_accounts_for_round_trip_cost_on_both_legs():
    """A gross-symmetric TP/SL (e.g. +2%/-2%) is NOT a 1:1 net risk-reward
    once round-trip transaction costs are paid — the cost eats the reward
    side and adds to the realized loss on the risk side."""
    net_reward, net_risk, net_rr = _compute_net_risk_reward(
        entry_price=100.0, target_price=102.0, stop_price=98.0,
        buy_fee=0.00155, sell_fee=0.00255, slippage=0.001,
    )
    total_cost = 0.00155 + 0.00255 + 2 * 0.001
    assert net_reward == pytest.approx(0.02 - total_cost, abs=1e-6)
    assert net_risk == pytest.approx(0.02 + total_cost, abs=1e-6)
    assert net_rr < 1.0, "symmetric gross TP/SL must be WORSE than 1:1 net of costs, not equal"


# ── Take-profit target must use take_profit, not primary_target ────────────

def test_target_price_uses_take_profit_not_primary_target(mini_db):
    """target_price used to be computed from primary_target (2%, the
    training-label threshold) even though take_profit (a separate, higher
    parameter meant for the actual displayed trade plan) was accepted and
    threaded through the whole call chain — it was just silently unused.
    With TP=SL=2% gross-symmetric, net risk-reward after transaction costs
    is always < 1.0 (see test_net_risk_reward_accounts_for_round_trip_cost),
    which forced nearly every signal to NO_TRADE regardless of how good the
    model's probability was. This test fails if that regresses."""
    as_of = "2026-07-27"
    feat_df = _feat_df(["AAAA"])
    probs = np.array([0.6])
    results = _assemble_predictions(
        feat_df, probs, as_of, mini_db, model_run_id=1,
        feature_cols=["dummy_feat"], threshold=0.5,
        primary_target=0.02, take_profit=0.035, stop_loss=-0.02,
    )
    r = results[0]
    entry = r["current_close"]
    expected_target = round(entry * 1.035, 0)
    assert r["target_price"] == pytest.approx(expected_target, abs=1e-6)
    # And explicitly NOT the old (buggy) 2% value.
    assert r["target_price"] != pytest.approx(round(entry * 1.02, 0), abs=1e-6)


def test_expected_upside_stays_anchored_to_primary_target(mini_db):
    """expected_upside feeds evaluate_matured_predictions()'s HIT/MISS bar
    and must stay at primary_target (matching target_definition="close5_2pct"
    and the model's actual backtested precision) even though target_price
    now uses the separate, higher take_profit value — otherwise live
    evaluation would silently grade signals against a different bar than
    the one the model was validated on."""
    as_of = "2026-07-27"
    feat_df = _feat_df(["AAAA"])
    probs = np.array([0.6])
    results = _assemble_predictions(
        feat_df, probs, as_of, mini_db, model_run_id=1,
        feature_cols=["dummy_feat"], threshold=0.5,
        primary_target=0.02, take_profit=0.035, stop_loss=-0.02,
    )
    assert results[0]["expected_upside"] == pytest.approx(0.02, abs=1e-6)


# ── Entry price must match training semantics ───────────────────────────────

def test_entry_price_matches_training_convention_not_same_day_open(mini_db):
    """pipeline.py's build_dataset() labels every signal using entry_price
    = that day's own CLOSE (see its 'close': 'entry_price' rename). Live
    inference must use the same convention — NOT a same-day open price,
    which can differ wildly from the close on volatile IDX small caps and
    has nothing to do with what the model was actually trained to predict
    relative to."""
    as_of = "2026-07-27"
    feat_df = _feat_df(["AAAA"])
    probs = np.array([0.6])
    results = _assemble_predictions(
        feat_df, probs, as_of, mini_db, model_run_id=1,
        feature_cols=["dummy_feat"], threshold=0.5,
    )
    assert len(results) == 1
    r = results[0]
    assert r["next_open"] == pytest.approx(r["current_close"], abs=1e-6)


# ── Trading calendar ─────────────────────────────────────────────────────────

def test_five_session_horizon_skips_weekends():
    """t -> t+5 trading days must skip Saturday/Sunday, not just add 5
    calendar days."""
    # 2026-07-27 is a Monday in this dataset's timeline.
    target = _add_trading_days("2026-07-27", 5)
    # Mon +5 sessions = Tue,Wed,Thu,Fri,Mon(next week) -> 2026-08-03
    assert target == "2026-08-03"
