"""
tests/test_ml_weekly_evaluation.py
Validates ml_weekly/evaluation.py's knowledge-aggregation logic against a
synthetic set of matured (HIT/MISS) predictions, since real predictions
take multiple trading days to mature and can't be relied on for a fast
test suite.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from db import SCHEMA_SQL
from ml_weekly.db_migration import MIGRATION_SQL
from ml_weekly.evaluation import (
    compute_evaluation_summary, _normalize_text, list_evaluated_signals, list_top_signals_by_batch,
)


@pytest.fixture
def mini_db(tmp_path: Path) -> str:
    db_path = str(tmp_path / "mini.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.executescript(MIGRATION_SQL)

    # A few trading days so "trading_days_since_oldest" has something to compute.
    for d in ["2026-07-13", "2026-07-14", "2026-07-15", "2026-07-16", "2026-07-17",
              "2026-07-20", "2026-07-21", "2026-07-22"]:
        conn.execute(
            "INSERT INTO ringkasan_saham_harian (tanggal, kode_saham, harga_penutupan, volume) VALUES (?,?,?,?)",
            (d, "TEST", 100, 1000),
        )

    def add(ticker, pred_date, outcome, ret, tier, decision, reasons, flags, regime="BULL_TREND"):
        conn.execute(
            """INSERT INTO ml_weekly_predictions (
                model_run_id, prediction_date, ticker, horizon_days, predicted_probability,
                outcome_status, realized_return, confidence_tier, decision_status,
                reason_codes_json, risk_flags_json, market_regime, evaluated_at
            ) VALUES (1, ?, ?, 5, 0.6, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
            (pred_date, ticker, outcome, ret, tier, decision,
             json.dumps(reasons), json.dumps(flags), regime),
        )

    # SANGAT_KUAT tier: 4 hits, 1 miss (80% hit rate) — should calibrate well.
    for i in range(4):
        add(f"AAA{i}", "2026-07-13", "HIT", 0.02, "SANGAT_KUAT", "QUALIFIED",
            ["RSI14 oversold (29)"], [], "BULL_TREND")
    add("AAAX", "2026-07-13", "MISS", -0.02, "SANGAT_KUAT", "QUALIFIED",
        ["RSI14 oversold (35)"], ["Stock in 20-day downtrend (-30.0%)"], "BULL_TREND")

    # LEMAH tier: 1 hit, 4 misses (20% hit rate) — should calibrate poorly.
    add("BBB0", "2026-07-14", "HIT", 0.02, "LEMAH", "NO_TRADE", ["RSI14 oversold (31)"], [], "BEAR_TREND")
    for i in range(4):
        add(f"BBB{i+1}", "2026-07-14", "MISS", -0.015, "LEMAH", "NO_TRADE",
            ["RSI14 oversold (33)"], ["Stock in 20-day downtrend (-25.0%)"], "BEAR_TREND")

    # A few still-pending rows that must NOT be counted as evaluated.
    conn.execute(
        """INSERT INTO ml_weekly_predictions (
            model_run_id, prediction_date, ticker, horizon_days, predicted_probability,
            outcome_status, confidence_tier, decision_status
        ) VALUES (1, '2026-07-22', 'PENDX', 5, 0.6, 'PENDING', 'KUAT', 'QUALIFIED')"""
    )

    conn.commit()
    conn.close()
    return db_path


def test_normalize_text_strips_trailing_numbers():
    assert _normalize_text("RSI14 oversold (29)") == "RSI14 oversold"
    assert _normalize_text("RSI14 oversold (35)") == "RSI14 oversold"
    assert _normalize_text("Stock in 20-day downtrend (-30.0%)") == "Stock in 20-day downtrend"


def test_pending_rows_excluded_from_evaluation(mini_db):
    result = compute_evaluation_summary(mini_db)
    assert result["data_maturity"]["pending_count"] == 1
    assert result["data_maturity"]["evaluated_count"] == 10
    assert result["overall"]["n"] == 10  # not 11 — PENDING must not leak in


def test_overall_hit_rate_matches_synthetic_data(mini_db):
    result = compute_evaluation_summary(mini_db)
    # 5 hits (4 SANGAT_KUAT + 1 LEMAH) out of 10 evaluated = 50%
    assert result["overall"]["hits"] == 5
    assert result["overall"]["hit_rate"] == pytest.approx(0.5, abs=1e-6)


def test_calibration_by_tier_reflects_reality(mini_db):
    """The whole point of this dashboard: prove that a higher tier actually
    resolves more often than a lower one, using real synthetic outcomes."""
    result = compute_evaluation_summary(mini_db)
    by_tier = {row["tier"]: row for row in result["by_confidence_tier"]}

    assert by_tier["SANGAT_KUAT"]["n"] == 5
    assert by_tier["SANGAT_KUAT"]["hit_rate"] == pytest.approx(0.8, abs=1e-6)

    assert by_tier["LEMAH"]["n"] == 5
    assert by_tier["LEMAH"]["hit_rate"] == pytest.approx(0.2, abs=1e-6)

    # The core validation claim: higher tier -> higher realized hit rate.
    assert by_tier["SANGAT_KUAT"]["hit_rate"] > by_tier["LEMAH"]["hit_rate"]


def test_decision_status_breakdown(mini_db):
    result = compute_evaluation_summary(mini_db)
    by_status = {row["status"]: row for row in result["by_decision_status"]}
    assert by_status["QUALIFIED"]["n"] == 5
    assert by_status["QUALIFIED"]["hits"] == 4
    assert by_status["NO_TRADE"]["n"] == 5
    assert by_status["NO_TRADE"]["hits"] == 1


def test_reason_code_normalization_groups_correctly(mini_db):
    result = compute_evaluation_summary(mini_db, min_group_n=1)
    by_text = {row["text"]: row for row in result["top_reason_codes"]}
    # "RSI14 oversold (29)" and "RSI14 oversold (35)" etc. must collapse
    # into ONE bucket "RSI14 oversold", not stay as 10 distinct strings.
    assert "RSI14 oversold" in by_text
    assert by_text["RSI14 oversold"]["n"] == 10


def test_market_regime_breakdown(mini_db):
    result = compute_evaluation_summary(mini_db)
    by_regime = {row["regime"]: row for row in result["by_market_regime"]}
    assert by_regime["BULL_TREND"]["n"] == 5
    assert by_regime["BEAR_TREND"]["n"] == 5


def test_rolling_window_caps_at_available_data(mini_db):
    result = compute_evaluation_summary(mini_db)
    rolling_by_window = {r["window"]: r for r in result["rolling"]}
    # Only 10 evaluated rows exist — the 20-window bucket must report n=10,
    # not silently pad or error.
    assert rolling_by_window[20]["n"] == 10


def test_timeline_grouped_by_date(mini_db):
    result = compute_evaluation_summary(mini_db)
    dates = {row["date"]: row for row in result["timeline"]}
    assert dates["2026-07-13"]["evaluated"] == 5
    assert dates["2026-07-13"]["hits"] == 4
    assert dates["2026-07-14"]["evaluated"] == 5
    assert dates["2026-07-14"]["hits"] == 1


def test_empty_database_returns_honest_empty_structure(tmp_path):
    db_path = str(tmp_path / "empty.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.executescript(MIGRATION_SQL)
    conn.close()

    result = compute_evaluation_summary(db_path)
    assert result["overall"]["n"] == 0
    assert result["overall"]["hit_rate"] is None
    assert result["by_confidence_tier"] == []
    assert result["timeline"] == []


@pytest.fixture
def signal_history_db(tmp_path: Path) -> str:
    """Dedicated fixture with current_close populated, since mini_db above
    doesn't set it — needed to check list_evaluated_signals' entry/exit
    price math specifically."""
    db_path = str(tmp_path / "signals.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.executescript(MIGRATION_SQL)

    def add(ticker, pred_date, outcome, entry_close, ret, tier="KUAT", decision="QUALIFIED", prob=0.6):
        conn.execute(
            """INSERT INTO ml_weekly_predictions (
                model_run_id, prediction_date, ticker, horizon_days, predicted_probability,
                calibrated_probability, current_close, outcome_status, realized_return,
                confidence_tier, decision_status, evaluated_at
            ) VALUES (1, ?, ?, 5, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
            (pred_date, ticker, prob, prob, entry_close, outcome, ret, tier, decision),
        )

    # A stock that rose 10%: entry 1000 -> exit 1100. Model was 95% sure.
    add("NAIK1", "2026-06-01", "HIT", 1000.0, 0.10, prob=0.95)
    # A stock that fell 5%: entry 2000 -> exit 1900. Model was only 55% sure.
    add("TURUN1", "2026-06-02", "MISS", 2000.0, -0.05, prob=0.55)
    # A very confident (99%) call that still missed -- top-10-by-confidence
    # must surface this, since it's earlier in time but should rank first.
    add("KONFIDEN1", "2026-05-15", "MISS", 3000.0, -0.03, prob=0.99)
    # A pending (unevaluated) row that must be excluded.
    conn.execute(
        """INSERT INTO ml_weekly_predictions (
            model_run_id, prediction_date, ticker, horizon_days, predicted_probability,
            current_close, outcome_status
        ) VALUES (1, '2026-07-20', 'PEND1', 5, 0.6, 500.0, 'PENDING')"""
    )
    conn.commit()
    conn.close()
    return db_path


def test_list_evaluated_signals_computes_before_after_price(signal_history_db):
    result = list_evaluated_signals(signal_history_db)
    assert result["total"] == 3
    by_ticker = {s["ticker"]: s for s in result["signals"]}

    up = by_ticker["NAIK1"]
    assert up["entry_price"] == 1000.0
    assert up["exit_price"] == pytest.approx(1100.0, abs=0.01)
    assert up["pct_change"] == pytest.approx(10.0, abs=0.01)
    assert up["outcome_status"] == "HIT"

    down = by_ticker["TURUN1"]
    assert down["entry_price"] == 2000.0
    assert down["exit_price"] == pytest.approx(1900.0, abs=0.01)
    assert down["pct_change"] == pytest.approx(-5.0, abs=0.01)
    assert down["outcome_status"] == "MISS"


def test_list_evaluated_signals_excludes_pending(signal_history_db):
    result = list_evaluated_signals(signal_history_db)
    tickers = {s["ticker"] for s in result["signals"]}
    assert "PEND1" not in tickers


def test_list_evaluated_signals_filters_by_ticker(signal_history_db):
    result = list_evaluated_signals(signal_history_db, ticker="naik1")  # case-insensitive
    assert result["total"] == 1
    assert result["signals"][0]["ticker"] == "NAIK1"


def _insert_model_run(conn, run_id, status, model_name="xgboost"):
    conn.execute(
        """INSERT INTO ml_weekly_model_runs (
            id, model_version, model_name, status, created_at, target_definition,
            training_start, training_end, validation_start, validation_end, holdout_start
        ) VALUES (?, 'v1', ?, ?, CURRENT_TIMESTAMP, 'close5_2pct',
                  '2020-01-02', '2023-12-31', '2024-01-06', '2024-12-31', '2025-01-06')""",
        (run_id, model_name, status),
    )


def test_stale_model_warning_fires_when_no_current_model_has_evaluated_signals(mini_db):
    # mini_db's predictions all use model_run_id=1, but no matching row is
    # ever inserted into ml_weekly_model_runs as active — simulating "the
    # model that made these signals has since been retrained/replaced".
    conn = sqlite3.connect(mini_db)
    _insert_model_run(conn, 999, "ensemble_member")  # a DIFFERENT, currently-active run
    conn.commit()
    conn.close()

    result = compute_evaluation_summary(mini_db)
    dm = result["data_maturity"]
    assert dm["current_model_evaluated_count"] == 0
    assert dm["stale_model_evaluated_count"] == 10
    assert dm["stale_model_warning"] is not None
    assert "model versi lama" in dm["stale_model_warning"]


def test_stale_model_warning_absent_when_current_model_has_evaluated_signals(mini_db):
    conn = sqlite3.connect(mini_db)
    _insert_model_run(conn, 1, "ensemble_member")  # matches the predictions' own model_run_id=1
    conn.commit()
    conn.close()

    result = compute_evaluation_summary(mini_db)
    dm = result["data_maturity"]
    assert dm["current_model_evaluated_count"] == 10
    assert dm["stale_model_evaluated_count"] == 0
    assert dm["stale_model_warning"] is None


def test_list_evaluated_signals_respects_limit_and_offset(signal_history_db):
    page1 = list_evaluated_signals(signal_history_db, limit=1, offset=0)
    page2 = list_evaluated_signals(signal_history_db, limit=1, offset=1)
    assert page1["total"] == 3
    assert len(page1["signals"]) == 1
    assert len(page2["signals"]) == 1
    assert page1["signals"][0]["ticker"] != page2["signals"][0]["ticker"]


def test_list_evaluated_signals_sort_by_probability_ranks_highest_confidence_first(signal_history_db):
    result = list_evaluated_signals(signal_history_db, sort_by="probability")
    probs = [s["signal_probability"] for s in result["signals"]]
    assert probs == sorted(probs, reverse=True)
    # The 99%-confidence call (KONFIDEN1) must rank first even though it's
    # the oldest signal and a MISS -- this view is about confidence, not
    # recency or outcome, so a top-10-by-confidence table would surface it.
    assert result["signals"][0]["ticker"] == "KONFIDEN1"
    assert result["signals"][0]["signal_probability"] == pytest.approx(99.0, abs=0.1)
    assert result["signals"][0]["outcome_status"] == "MISS"


def test_list_evaluated_signals_default_sort_is_by_date(signal_history_db):
    result = list_evaluated_signals(signal_history_db, sort_by="date")
    dates = [s["prediction_date"] for s in result["signals"]]
    assert dates == sorted(dates, reverse=True)


@pytest.fixture
def multi_batch_db(tmp_path: Path) -> str:
    """Three prediction batches ("tarikan"), each on its own date, with more
    signals per batch than any reasonable top_n — needed to test that
    'top N per batch' actually truncates PER DATE rather than pooling
    everything into one global top-N."""
    db_path = str(tmp_path / "batches.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.executescript(MIGRATION_SQL)

    def add(ticker, pred_date, outcome, prob):
        conn.execute(
            """INSERT INTO ml_weekly_predictions (
                model_run_id, prediction_date, ticker, horizon_days, predicted_probability,
                calibrated_probability, current_close, outcome_status, realized_return, evaluated_at
            ) VALUES (1, ?, ?, 5, ?, ?, 1000.0, ?, 0.01, CURRENT_TIMESTAMP)""",
            (pred_date, ticker, prob, prob, outcome),
        )

    # Batch A (newest, 2026-07-20): 15 signals. Top 10 by probability are
    # the 10 highest -- 7 of THOSE are HIT, the 8th-15th (lower prob, out
    # of top-10) are all MISS and must NOT count toward the top-10 stats.
    for i in range(15):
        prob = 0.95 - i * 0.02  # descending: rank 0 = highest prob
        outcome = "HIT" if i < 7 else "MISS"
        add(f"A{i}", "2026-07-20", outcome, prob)

    # Batch B (2026-07-15): 15 signals, top 10 have 4 HIT.
    for i in range(15):
        prob = 0.90 - i * 0.02
        outcome = "HIT" if i < 4 else "MISS"
        add(f"B{i}", "2026-07-15", outcome, prob)

    # Batch C (oldest, 2026-07-10): only 5 signals total (fewer than any
    # sensible top_n) -- must return all 5, not pad or error.
    for i in range(5):
        prob = 0.80 - i * 0.02
        outcome = "HIT" if i < 2 else "MISS"
        add(f"C{i}", "2026-07-10", outcome, prob)

    conn.commit()
    conn.close()
    return db_path


def test_top_signals_per_batch_truncates_within_each_date(multi_batch_db):
    result = list_top_signals_by_batch(multi_batch_db, top_n=10, batch_limit=10)
    assert result["total_batches"] == 3
    by_date = {b["prediction_date"]: b for b in result["batches"]}

    assert by_date["2026-07-20"]["n"] == 10
    assert by_date["2026-07-20"]["hits"] == 7
    assert by_date["2026-07-15"]["n"] == 10
    assert by_date["2026-07-15"]["hits"] == 4
    # Fewer signals than top_n in this batch -> return all of them, not padded.
    assert by_date["2026-07-10"]["n"] == 5
    assert by_date["2026-07-10"]["hits"] == 2


def test_top_signals_per_batch_aggregate_matches_user_framing(multi_batch_db):
    """The exact case the user described: N tarikan x top-10 each, how many
    hit overall — 10 + 10 + 5 = 25 signals, 7 + 4 + 2 = 13 hits."""
    result = list_top_signals_by_batch(multi_batch_db, top_n=10, batch_limit=10)
    agg = result["aggregate"]
    assert agg["total_batches"] == 3
    assert agg["total_signals"] == 25
    assert agg["total_hits"] == 13
    assert agg["hit_rate"] == pytest.approx(13 / 25, abs=1e-6)


def test_top_signals_per_batch_ordered_newest_first(multi_batch_db):
    result = list_top_signals_by_batch(multi_batch_db, top_n=10, batch_limit=10)
    dates = [b["prediction_date"] for b in result["batches"]]
    assert dates == sorted(dates, reverse=True)


def test_top_signals_per_batch_pagination(multi_batch_db):
    page1 = list_top_signals_by_batch(multi_batch_db, top_n=10, batch_limit=2, batch_offset=0)
    page2 = list_top_signals_by_batch(multi_batch_db, top_n=10, batch_limit=2, batch_offset=2)
    assert len(page1["batches"]) == 2
    assert len(page2["batches"]) == 1
    # Aggregate must reflect ALL batches regardless of which page is open.
    assert page1["aggregate"]["total_signals"] == 25
    assert page2["aggregate"]["total_signals"] == 25


def test_top_signals_per_batch_within_batch_sorted_by_probability(multi_batch_db):
    result = list_top_signals_by_batch(multi_batch_db, top_n=10, batch_limit=1, batch_offset=0)
    probs = [s["signal_probability"] for s in result["batches"][0]["signals"]]
    assert probs == sorted(probs, reverse=True)
