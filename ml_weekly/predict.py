"""
ml_weekly/predict.py
Inference pipeline: generate predictions for the latest signal date.
"""
from __future__ import annotations
import json
import logging
import sqlite3
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


# ── Ensemble helpers ──────────────────────────────────────────────────────────

def load_ensemble_models(db_path: str, models_dir: Path) -> list[dict]:
    """
    Load all models with status='ensemble_member' from DB + disk.
    Returns list of {model_run_id, model_name, model_version, threshold,
                     feature_cols, pipe, weight}
    Falls back to single 'active' model if no ensemble members.
    """
    from .train import load_model

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """SELECT r.id, r.model_name, r.model_version, r.probability_threshold,
                      r.feature_list_json, r.validation_status, r.created_at, r.target_definition,
                      m.pr_auc
               FROM ml_weekly_model_runs r
               LEFT JOIN ml_weekly_backtest_metrics m 
                 ON m.model_run_id = r.id AND m.evaluation_split = 'validation'
               WHERE r.status IN ('ensemble_member', 'active')
               ORDER BY r.id DESC"""
        ).fetchall()

    if not rows:
        return []

    # Prefer ensemble_member; deduplicate by model_name (keep newest per algo)
    seen_names = set()
    members = []
    for row in rows:
        run_id, name, version, threshold, feat_json, val_status, created_at, target_def, pr_auc = row
        
        horizon_days = 5
        if target_def and target_def.startswith("close"):
            try:
                horizon_days = int(target_def.split("_")[0].replace("close", ""))
            except ValueError:
                pass
                
        if name in seen_names:
            continue
        seen_names.add(name)
        try:
            pipe = load_model(run_id, models_dir, version)
            feature_cols = json.loads(feat_json) if feat_json else []
            members.append({
                "model_run_id": run_id,
                "model_name": name,
                "model_version": version,
                "threshold": threshold or 0.85,
                "feature_cols": feature_cols,
                "pipe": pipe,
                "val_status": val_status or "UNKNOWN",
                "weight": max(float(pr_auc) if pr_auc else 0.5, 0.01),  # minimum weight 0.01
                "horizon_days": horizon_days,
            })
            log.info("Ensemble: loaded %s (run_id=%d, weight=%.3f)", name, run_id, members[-1]["weight"])
        except Exception as exc:
            log.warning("Could not load model run_id=%d (%s): %s", run_id, name, exc)

    return members


def ensemble_predict_proba(members: list[dict], X_df: pd.DataFrame) -> tuple[np.ndarray, list[dict]]:
    """
    Run all ensemble models on X_df, return averaged probabilities.
    Returns (avg_probs array, per_model_probs list of dicts).
    """
    all_probs = []
    weights = []
    breakdown = []

    for m in members:
        feature_cols = m["feature_cols"]
        w = m.get("weight", 1.0)
        # Align features
        missing = [c for c in feature_cols if c not in X_df.columns]
        X = X_df.copy()
        for c in missing:
            X[c] = np.nan
        try:
            probs = m["pipe"].predict_proba(X[feature_cols])[:, 1]
            all_probs.append(probs)
            weights.append(w)
            breakdown.append({
                "model_name": m["model_name"],
                "model_run_id": m["model_run_id"],
                "probs": probs,
                "weight": w,
            })
            log.info("Ensemble %s: mean_prob=%.3f", m["model_name"], float(np.nanmean(probs)))
        except Exception as exc:
            log.warning("Ensemble model %s predict failed: %s", m["model_name"], exc)

    if not all_probs:
        raise ValueError("All ensemble models failed to predict")

    # Dynamic proportional weighting of probabilities
    probs_stack = np.stack(all_probs, axis=0) # shape: (num_models, num_samples)
    base_w = np.array(weights).reshape(-1, 1)
    
    # Weight each model's prediction proportionally to its own probability (squared for emphasis)
    # This gives higher influence to models that are highly confident.
    dynamic_w = base_w * (probs_stack ** 2)
    dynamic_w_sum = np.nansum(dynamic_w, axis=0)
    
    # Avoid division by zero
    dynamic_w_norm = np.divide(dynamic_w, dynamic_w_sum, out=np.zeros_like(dynamic_w), where=dynamic_w_sum!=0)
    
    avg_probs = np.nansum(probs_stack * dynamic_w_norm, axis=0)
    return avg_probs, breakdown




def get_source_max_date(db_path: str) -> str:
    """Return the most recent date in ringkasan_saham_harian."""
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT MAX(tanggal) FROM ringkasan_saham_harian WHERE volume > 0"
        ).fetchone()
        return row[0] if row and row[0] else ""


def check_data_freshness(db_path: str, max_stale_days: int = 5) -> dict:
    """Check if data is fresh enough to generate predictions."""
    max_date = get_source_max_date(db_path)
    if not max_date:
        return {"ok": False, "reason": "No data in database", "max_date": None}

    max_dt = datetime.strptime(max_date, "%Y-%m-%d").date()
    today = date.today()
    days_old = (today - max_dt).days

    # Allow up to max_stale_days (some lag for weekends / holidays)
    ok = days_old <= max_stale_days
    return {
        "ok": ok,
        "max_date": max_date,
        "days_old": days_old,
        "reason": None if ok else f"Data is {days_old} days old (max allowed: {max_stale_days})",
    }


def generate_reason_codes(
    features: dict,
    prob: float,
    threshold: float,
) -> list[str]:
    """Generate human-readable reason codes from feature values."""
    reasons = []

    # Price momentum
    ret_5d = features.get("return_5d", np.nan)
    ret_20d = features.get("return_20d", np.nan)
    if not np.isnan(ret_5d) and ret_5d > 0.03:
        reasons.append(f"Strong 5-day momentum (+{ret_5d:.1%})")
    if not np.isnan(ret_20d) and ret_20d > 0.05:
        reasons.append(f"Strong 20-day trend (+{ret_20d:.1%})")

    # RSI conditions
    rsi14 = features.get("rsi14", np.nan)
    if not np.isnan(rsi14):
        if rsi14 < 40:
            reasons.append(f"RSI14 oversold ({rsi14:.0f})")
        elif 45 <= rsi14 <= 60:
            reasons.append(f"RSI14 balanced ({rsi14:.0f})")

    # MA alignment
    if features.get("ma_aligned_bull", 0) == 1.0:
        reasons.append("Price above all key moving averages")

    # Breakout
    high_20 = features.get("close_vs_high_20d", np.nan)
    high_60 = features.get("close_vs_high_60d", np.nan)
    if not np.isnan(high_20) and high_20 >= -0.01:
        reasons.append("Near 20-day high (breakout zone)")
    elif not np.isnan(high_60) and high_60 >= -0.02:
        reasons.append("Near 60-day high (breakout zone)")

    # Volume expansion
    rel_vol_5 = features.get("rel_volume_5d", np.nan)
    if not np.isnan(rel_vol_5) and rel_vol_5 > 1.5:
        reasons.append(f"Volume expansion {rel_vol_5:.1f}x vs 5-day avg")

    # Bollinger squeeze
    if features.get("bb_squeeze", 0) == 1.0:
        reasons.append("Bollinger Band squeeze (pre-breakout signal)")

    # BB position
    bb_pos = features.get("bb_position", np.nan)
    if not np.isnan(bb_pos) and bb_pos > 0.7:
        reasons.append(f"Price in upper Bollinger range ({bb_pos:.0%})")

    # MACD
    if features.get("macd_bullish", 0) == 1.0:
        reasons.append("MACD histogram positive (bullish momentum)")

    # Market regime
    regime = features.get("market_regime", "")
    if regime in ("BULL_TREND", "BULL_RANGE"):
        reasons.append(f"Supportive market regime ({regime})")

    # Foreign flow
    fn_5d = features.get("foreign_net_5d", 0.0)
    if not np.isnan(fn_5d) and fn_5d > 0.01:
        reasons.append(f"Foreign net buying 5-day ({fn_5d:.1%} of turnover)")

    # Efficiency ratio
    er = features.get("efficiency_ratio_20d", np.nan)
    if not np.isnan(er) and er > 0.6:
        reasons.append(f"Strong trend efficiency ({er:.2f})")

    # Close location
    cl = features.get("close_location", np.nan)
    if not np.isnan(cl) and cl > 0.7:
        reasons.append("Closed near daily high (strength)")

    return reasons[:6]  # max 6 reasons


def generate_risk_flags(features: dict) -> list[str]:
    """Generate risk flags from feature values."""
    flags = []

    # Low liquidity
    avg_val = features.get("avg_value_20d", np.nan)
    if not np.isnan(avg_val) and avg_val < 1e9:
        flags.append(f"Low liquidity (avg value {avg_val/1e6:.0f}M IDR)")

    # High volatility
    hvol20 = features.get("hvol20", np.nan)
    if not np.isnan(hvol20) and hvol20 > 0.40:
        flags.append(f"High volatility (annualized: {hvol20:.0%})")

    # Overbought
    rsi14 = features.get("rsi14", np.nan)
    if not np.isnan(rsi14) and rsi14 > 70:
        flags.append(f"RSI14 overbought ({rsi14:.0f})")

    # Bearish regime
    regime = features.get("market_regime", "")
    if regime in ("BEAR_TREND", "BEAR_RANGE"):
        flags.append(f"Bearish market regime ({regime})")
    elif regime == "HIGH_VOL":
        flags.append("High volatility market regime")

    # Downtrend
    ret_20 = features.get("return_20d", np.nan)
    if not np.isnan(ret_20) and ret_20 < -0.10:
        flags.append(f"Stock in 20-day downtrend ({ret_20:.1%})")

    # Near 52W low
    vs_low = features.get("close_vs_low_120d", np.nan)
    if not np.isnan(vs_low) and vs_low < 0.05:
        flags.append("Price near 120-day low")

    # Missing data
    if features.get("zero_vol_pct_20d", 0) > 0.1:
        flags.append("Recent inactive trading days")

    # Corp action warning
    if features.get("has_corp_action_warning", False):
        flags.append("Corporate action warning (extreme past returns)")

    return flags


# ── Decision status ────────────────────────────────────────────────────────
# A signal is never just "a probability" — it's a decision, and the decision
# must be defensible even when the underlying evidence is weak. In
# particular: a raw probability can look high while the ensemble backtest
# that would justify trusting it has never cleared the precision bar. The
# tier label must reflect THAT gap, not just where a stock ranks against
# today's other candidates.
DECISION_STATUSES = (
    "QUALIFIED", "WATCHLIST", "NO_TRADE", "ABSTAIN",
    "DATA_INVALID", "MODEL_UNAVAILABLE", "SIGNAL_EXPIRED",
)

VERIFIED_HOLDOUT_STATUSES = {"VERIFIED_ABOVE_90", "PROVISIONAL_ABOVE_90"}

MIN_NET_RISK_REWARD = 1.0  # net reward must be at least this many times net risk
MIN_MODEL_COVERAGE_FRACTION = 0.5  # at least half the loaded ensemble members must have scored this ticker

# Forensic audit finding: pipeline.py's TRAINING feature builder
# (_vectorized_features) and features.py's LIVE INFERENCE feature builder
# (build_all_features) do not produce the same column schema — names like
# ma5/ma10/ma20/vol_ratio/nf_5d/bb_pct exist in the model's trained
# feature_cols but are never populated by the live builder, so they get
# silently padded to NaN and then median-imputed to a constant at
# inference time. This was NOT fixed in this pass (unifying two feature
# pipelines safely needs its own dedicated validation pass against the
# holdout numbers) but a ticker whose real feature coverage is this thin
# must not be presented with the same confidence as one with full
# coverage — hence this gate.
MIN_FEATURE_COVERAGE_FRACTION = 0.5


def _confidence_tier_capped(prob: float, holdout_status: Optional[str], coverage_ok: bool) -> str:
    """
    Probability-based tier, but capped whenever the evidence backing it is
    weak: an unvalidated holdout (status not in VERIFIED_HOLDOUT_STATUSES)
    or thin model coverage caps the label at "Cukup" no matter how high the
    raw probability is. A single model agreeing at 90% is not "Sangat Kuat"
    just because 90 is a big number — it hasn't been shown to generalize.
    """
    validated = holdout_status in VERIFIED_HOLDOUT_STATUSES
    if not validated or not coverage_ok:
        return "CUKUP" if prob >= 0.55 else "LEMAH"
    if prob >= 0.65:
        return "SANGAT_KUAT"
    if prob >= 0.55:
        return "KUAT"
    if prob >= 0.45:
        return "CUKUP"
    return "LEMAH"


def _compute_net_risk_reward(
    entry_price: float, target_price: float, stop_price: float,
    buy_fee: float, sell_fee: float, slippage: float,
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Returns (net_reward_pct, net_risk_pct, net_risk_reward_ratio), all
    after round-trip transaction costs — matches the cost convention used
    elsewhere in this app (handle_signals' net_tp_return)."""
    if not entry_price or entry_price <= 0:
        return None, None, None
    total_cost = buy_fee + sell_fee + 2 * slippage
    gross_reward = (target_price - entry_price) / entry_price
    gross_risk = (entry_price - stop_price) / entry_price
    net_reward = gross_reward - total_cost
    net_risk = gross_risk + total_cost  # a losing exit still pays the same round-trip cost
    if net_risk <= 0:
        return net_reward, net_risk, None
    return net_reward, net_risk, net_reward / net_risk


def compute_decision_status(
    prob: float,
    n_models_expected: int,
    n_models_contributed: int,
    holdout_status: Optional[str],
    net_risk_reward: Optional[float],
    expected_value: Optional[float],
    has_corp_action_warning: bool,
    feature_coverage: float = 1.0,
    min_net_rr: float = MIN_NET_RISK_REWARD,
) -> tuple[str, str, str]:
    """
    Gate a candidate down to one of DECISION_STATUSES, with a matching
    (capped) confidence tier and a human-readable reason. Order matters —
    hard failures (no model, no valid economics) are checked before the
    softer QUALIFIED-vs-WATCHLIST distinction.

    feature_coverage: fraction of the model's expected feature columns that
    were actually non-null for this ticker BEFORE imputation. Thin coverage
    means the model is effectively scoring imputed constants, not this
    stock's real technicals — see MIN_FEATURE_COVERAGE_FRACTION docstring.
    """
    if n_models_contributed <= 0:
        return "MODEL_UNAVAILABLE", "LEMAH", "Tidak ada model yang berhasil menghasilkan probabilitas untuk saham ini."

    if feature_coverage < MIN_FEATURE_COVERAGE_FRACTION:
        return ("DATA_INVALID", "LEMAH",
                f"Hanya {feature_coverage:.0%} fitur teknikal tersedia untuk saham ini (sisanya kosong dan diisi nilai "
                f"median) — probabilitas tidak dapat dipercaya karena model kekurangan data nyata untuk saham ini.")

    if has_corp_action_warning:
        return ("NO_TRADE", "LEMAH",
                "Terindikasi corporate action / pergerakan harga ekstrem yang belum terverifikasi — "
                "risiko material yang dapat menggagalkan validitas sinyal.")

    if expected_value is None or net_risk_reward is None or expected_value <= 0 or net_risk_reward < min_net_rr:
        coverage_ok = n_models_expected > 0 and (n_models_contributed / n_models_expected) >= MIN_MODEL_COVERAGE_FRACTION
        tier = _confidence_tier_capped(prob, holdout_status, coverage_ok)
        return ("NO_TRADE", tier,
                f"Expected value setelah biaya transaksi tidak positif atau risk-reward bersih "
                f"({'N/A' if net_risk_reward is None else f'{net_risk_reward:.2f}'}) di bawah ambang {min_net_rr:.1f}.")

    if prob < 0.50:
        return "ABSTAIN", "LEMAH", "Probabilitas model di bawah 50% — model tidak melihat keunggulan (edge) pada saham ini."

    coverage_ok = n_models_expected > 0 and (n_models_contributed / n_models_expected) >= MIN_MODEL_COVERAGE_FRACTION
    validated = holdout_status in VERIFIED_HOLDOUT_STATUSES
    tier = _confidence_tier_capped(prob, holdout_status, coverage_ok)

    if validated and coverage_ok:
        return "QUALIFIED", tier, "Memenuhi syarat validasi holdout, cakupan model, dan risk-reward bersih minimum."

    reasons = []
    if not validated:
        reasons.append(f"status validasi holdout saat ini {holdout_status or 'belum pernah dievaluasi'} (belum tervalidasi ≥90%)")
    if not coverage_ok:
        reasons.append(f"hanya {n_models_contributed}/{n_models_expected} model yang berhasil menilai saham ini")
    return "WATCHLIST", tier, "Menarik secara statistik tapi belum lolos syarat penuh: " + "; ".join(reasons) + "."


def _assemble_predictions(
    feat_df: pd.DataFrame,
    probs: np.ndarray,
    as_of_date: str,
    db_path: str,
    model_run_id: int,
    feature_cols: list[str],
    threshold: float,
    horizon_days: int = 5,
    primary_target: float = 0.02,
    take_profit: float = 0.03,
    stop_loss: float = -0.02,
    atr_multiplier: float = 2.0,
    ensemble_breakdown: list[dict] = None,
    n_models_expected: int = 1,
    buy_fee: float = 0.00155,
    sell_fee: float = 0.00255,
    slippage: float = 0.001,
) -> list[dict]:
    """
    Turn an already-built feature dataframe + an already-computed probability
    array into prediction rows.

    CRITICAL INVARIANT: probs[i] MUST correspond to feat_df row i. This
    function accesses both purely by positional index (never by a separately
    rebuilt dataframe, never by pandas index labels) so that alignment can
    never silently drift. A previous version of this pipeline rebuilt
    feat_df a second time inside predict_for_date and reused a probability
    array computed against the FIRST build by raw array position — if the
    two builds ever ordered rows differently, a ticker could silently
    receive another ticker's probability (observed in production: two
    unrelated tickers ending up with byte-identical ensemble breakdowns).
    Do not reintroduce a second feat_df build in this code path.
    """
    from .market_features import compute_market_features, compute_sector_features

    feat_df = feat_df.reset_index(drop=True)
    probs = np.asarray(probs)
    if len(probs) != len(feat_df):
        raise ValueError(
            f"probs length ({len(probs)}) does not match feat_df rows ({len(feat_df)}) — "
            "refusing to guess ticker-to-probability alignment."
        )

    eligible = feat_df["ticker"].tolist()
    if len(set(eligible)) != len(eligible):
        log.warning("feat_df contains duplicate tickers — predictions for duplicates may be inconsistent")

    mfeats = compute_market_features(as_of_date, db_path)
    sector_feats = compute_sector_features(as_of_date, db_path, eligible)

    # Build per-ticker ensemble breakdown lookup: {ticker: {model_name: prob}}
    # — positionally aligned to THIS feat_df, never a separately-built one.
    ensemble_ticker_probs: dict[str, dict] = {}
    if ensemble_breakdown:
        for bd in ensemble_breakdown:
            model_nm = bd["model_name"]
            bd_probs = np.asarray(bd["probs"])
            if len(bd_probs) != len(feat_df):
                log.warning(
                    "Ensemble breakdown for %s has %d probs but feat_df has %d rows — skipping this model's breakdown",
                    model_nm, len(bd_probs), len(feat_df),
                )
                continue
            for i, ticker in enumerate(eligible):
                ensemble_ticker_probs.setdefault(ticker, {})[model_nm] = round(float(bd_probs[i]), 4)

    # Get current prices using the latest available date <= as_of_date
    with sqlite3.connect(db_path) as conn:
        latest_date_row = conn.execute("SELECT MAX(tanggal) FROM ringkasan_saham_harian WHERE tanggal <= ?", (as_of_date,)).fetchone()
        latest_date = latest_date_row[0] if latest_date_row and latest_date_row[0] else as_of_date

        price_rows = conn.execute(
            """SELECT kode_saham, harga_penutupan, COALESCE(harga_pembukaan, sebelumnya) as entry_proxy
               FROM ringkasan_saham_harian
               WHERE tanggal = ? AND kode_saham IN ({})""".format(
                ",".join(["?" for _ in eligible])
            ),
            [latest_date] + list(eligible)
        ).fetchall()

    price_map = {r[0]: (r[1], r[2]) for r in price_rows}

    # ATR for TP/SL
    with sqlite3.connect(db_path) as conn:
        atr_rows = conn.execute(
            """SELECT kode_saham,
                      AVG(ABS(harga_tertinggi - harga_terendah)) as avg_range
               FROM ringkasan_saham_harian
               WHERE tanggal <= ? AND tanggal >= DATE(?, '-30 days')
                 AND kode_saham IN ({})
                 AND harga_tertinggi IS NOT NULL
               GROUP BY kode_saham""".format(
                ",".join(["?" for _ in eligible])
            ),
            [as_of_date, as_of_date] + list(eligible)
        ).fetchall()
    atr_map = {r[0]: r[1] for r in atr_rows}

    from .ensemble_eval import get_latest_ensemble_status
    ens_status = get_latest_ensemble_status(db_path)
    holdout_status = ens_status.get("holdout_status") if ens_status else None

    results = []
    market_regime = mfeats.get("market_regime", "NEUTRAL")

    for i in range(len(feat_df)):
        row = feat_df.iloc[i]
        ticker = row["ticker"]
        prob = float(probs[i])
        current_close, _open_same_day = price_map.get(ticker, (0, 0))

        if not current_close or current_close <= 0:
            continue

        # Entry MUST equal what the model was actually trained against.
        # pipeline.py's build_dataset() labels each signal using
        # entry_price = that day's own CLOSE (see its 'close': 'entry_price'
        # rename) — i.e. the model learned "if you could transact at
        # today's close, what happens over the next N sessions", not
        # anything involving an open price. This function used to instead
        # substitute today's own OPEN price (COALESCE(harga_pembukaan,
        # sebelumnya) from the same row as current_close) as "entry",
        # which is a different value from what was trained on AND, on
        # volatile/thin IDX small caps, can differ wildly from the close
        # being shown as "current price" — producing entry prices that
        # looked unrelated to (sometimes far above) the stock's own
        # current price. Anchoring on current_close keeps this consistent
        # with training and with the entry-zone band computed below.
        entry_price = current_close
        # The displayed/tradeable exit target uses take_profit, NOT
        # primary_target — primary_target (2%) is the statistical label
        # threshold the model was actually trained and backtested against
        # (target_definition="close5_2pct" everywhere else in this app) and
        # must stay untouched for evaluation to remain apples-to-apples (see
        # expected_upside below). take_profit is the separate, real-money
        # exit level shown in the trade plan; it used to be computed from
        # primary_target by mistake (take_profit was accepted as a param and
        # threaded all the way from config but never actually used), which
        # made every trade plan's gross reward exactly equal to its risk
        # (2% TP vs 2% SL) — a 1:1 gross ratio that net transaction costs
        # almost always push under the 1.0 net risk-reward gate, silently
        # forcing most signals to NO_TRADE regardless of how good the
        # underlying probability was.
        target_price = round(entry_price * (1 + take_profit), 0)
        stop_price = round(entry_price * (1 + stop_loss), 0)

        # Signal status based on averaged prob
        if prob >= threshold:
            signal_status = "HIGH_CONFIDENCE"
        elif prob >= threshold * 0.85:
            signal_status = "WATCHLIST"
        else:
            signal_status = "NO_SIGNAL"

        # Feature dict for reason codes
        feat_dict = {c: row.get(c, np.nan) for c in feature_cols}
        n_present = sum(1 for v in feat_dict.values() if pd.notna(v))
        feature_coverage = (n_present / len(feature_cols)) if feature_cols else 1.0
        feat_dict["market_regime"] = market_regime
        feat_dict["has_corp_action_warning"] = bool(row.get("has_corp_action_warning", False))
        feat_dict["avg_value_20d"] = row.get("avg_value_20d", np.nan)

        # Add sector
        sf = sector_feats.get(ticker, {})
        feat_dict.update(sf)

        reasons = generate_reason_codes(feat_dict, prob, threshold)
        risk_flags = generate_risk_flags(feat_dict)

        # Distinguish a CONFIRMED corporate action (real share-count change,
        # cross-checked against the matching price move — see
        # tools/detect_corporate_actions.py) from the older return-outlier
        # heuristic, which can also fire on a genuine large rally/crash that
        # has nothing to do with a split.
        if row.get("has_verified_corp_action"):
            risk_flags.append("Terverifikasi: perubahan jumlah saham beredar terdeteksi dalam 120 hari terakhir (kemungkinan stock split/reverse split/rights issue)")
        elif row.get("has_corp_action_warning"):
            risk_flags.append("Past extreme return detected (possible corp action, belum terverifikasi via jumlah saham beredar)")

        # Matches the actual trade plan (target_price now uses take_profit,
        # not primary_target) — this is "expected P&L if you follow the plan
        # shown", not the model's internal training-label statistic.
        expected_return = prob * take_profit + (1 - prob) * stop_loss

        # Ensemble per-model probs for this ticker
        ticker_ensemble = ensemble_ticker_probs.get(ticker, {})
        ensemble_json = json.dumps(ticker_ensemble) if ticker_ensemble else None
        n_models_contributed = len(ticker_ensemble) if ticker_ensemble else 1  # 1 = single-model (non-ensemble) call

        # Net risk-reward and expected value AFTER real transaction costs —
        # a gross 1:1 TP/SL is not actually breakeven odds once fees and
        # slippage are paid on both legs.
        net_reward, net_risk, net_rr = _compute_net_risk_reward(
            entry_price, target_price, stop_price, buy_fee, sell_fee, slippage,
        )
        expected_value = (prob * net_reward - (1 - prob) * net_risk) if (net_reward is not None and net_risk is not None) else None

        decision_status, confidence_tier, decision_reason = compute_decision_status(
            prob=prob,
            n_models_expected=n_models_expected,
            n_models_contributed=n_models_contributed,
            holdout_status=holdout_status,
            net_risk_reward=net_rr,
            expected_value=expected_value,
            has_corp_action_warning=bool(row.get("has_corp_action_warning", False)),
            feature_coverage=feature_coverage,
        )

        # Entry is conditional, not a promise: it's only valid if next
        # session's open still falls within a band around today's close.
        # A gap beyond that band invalidates the setup entirely (see
        # SIGNAL_EXPIRED / NOT_TRIGGERED handling downstream).
        entry_zone_low = round(current_close * 0.98, 0)
        entry_zone_high = round(current_close * 1.03, 0)

        # Safe feature values JSON (no NaN)
        feat_vals = {}
        for k, v in feat_dict.items():
            try:
                fv = float(v)
                feat_vals[k] = None if (fv != fv or fv == float('inf') or fv == float('-inf')) else round(fv, 6)
            except (TypeError, ValueError):
                pass

        pred = {
            "model_run_id": model_run_id,
            "prediction_date": as_of_date,
            "ticker": ticker,
            "horizon_days": horizon_days,
            "current_close": float(current_close),
            "next_open": float(entry_price),
            "predicted_probability": round(prob, 4),
            "calibrated_probability": round(prob, 4),
            "predicted_return": round(expected_return, 4),
            # Deliberately primary_target (2%, the training-label threshold),
            # NOT take_profit (3.5%+, the displayed trade-plan target) —
            # evaluate_matured_predictions() uses this as the HIT/MISS bar
            # (see its "realized_return >= expected_upside" check), and that
            # must stay anchored to what the model was actually trained and
            # backtested to predict, or live evaluation results stop being
            # comparable to the model's own holdout/validation precision.
            "expected_upside": round(primary_target, 4),
            "expected_drawdown": round(stop_loss, 4),
            "signal_status": signal_status,
            "signal_threshold": threshold,
            "technical_score": round(prob, 4),
            "volume_score": float(row.get("rel_volume_20d", 1.0) or 1.0),
            "foreign_flow_score": float(row.get("foreign_net_5d", 0.0) or 0.0),
            "market_regime": market_regime,
            "sector_regime": sf.get("sector", "Unknown"),
            "reason_codes_json": json.dumps(reasons),
            "risk_flags_json": json.dumps(risk_flags),
            "feature_values_json": json.dumps(feat_vals),
            "ensemble_probs_json": ensemble_json,   # NEW: per-model breakdown
            "target_price": float(target_price),
            "stop_price": float(stop_price),
            "realized_return": None,
            "realized_label": None,
            "outcome_status": "PENDING",
            # ── Decision / risk-gate fields ─────────────────────────────
            "decision_status": decision_status,
            "confidence_tier": confidence_tier,
            "decision_reason": decision_reason,
            "model_coverage_json": json.dumps({
                "expected": n_models_expected, "contributed": n_models_contributed,
                "feature_coverage": round(feature_coverage, 3),
            }),
            "net_risk_reward": round(net_rr, 3) if net_rr is not None else None,
            "net_expected_value": round(expected_value, 4) if expected_value is not None else None,
            "entry_zone_low": float(entry_zone_low),
            "entry_zone_high": float(entry_zone_high),
        }
        results.append(pred)

    # Defense-in-depth: two DIFFERENT tickers ending up with byte-identical
    # ensemble breakdowns is the exact symptom of the alignment bug this
    # function was rewritten to eliminate. If it ever recurs (e.g. a future
    # refactor reintroduces a second dataframe build), fail loudly in logs
    # instead of silently shipping swapped predictions.
    seen_breakdowns: dict[str, str] = {}
    for r in results:
        ej = r.get("ensemble_probs_json")
        if not ej:
            continue
        if ej in seen_breakdowns and seen_breakdowns[ej] != r["ticker"]:
            log.error(
                "ALIGNMENT WARNING: %s and %s have byte-identical ensemble breakdowns (%s) — "
                "possible ticker/probability misalignment upstream.",
                seen_breakdowns[ej], r["ticker"], ej,
            )
        else:
            seen_breakdowns[ej] = r["ticker"]

    # Sort by probability descending
    results.sort(key=lambda x: x["calibrated_probability"], reverse=True)
    log.info(
        "Predictions for %s: %d total, %d HC, %d watchlist",
        as_of_date,
        len(results),
        sum(1 for r in results if r["signal_status"] == "HIGH_CONFIDENCE"),
        sum(1 for r in results if r["signal_status"] == "WATCHLIST"),
    )
    return results


def predict_for_date(
    as_of_date: str,
    db_path: str,
    model_run_id: int,
    model_pipe,
    feature_cols: list[str],
    threshold: float,
    horizon_days: int = 5,
    primary_target: float = 0.02,
    take_profit: float = 0.03,
    stop_loss: float = -0.02,
    min_history_days: int = 120,
    min_median_value_20d: float = 1e9,
    atr_multiplier: float = 2.0,
    ensemble_breakdown: list[dict] = None,
) -> list[dict]:
    """
    Standalone single-model prediction: builds the universe/features once
    and scores it with model_pipe directly. Used for ad-hoc/backfill runs
    with a real sklearn-compatible pipeline (predict_proba). The live
    ensemble path (api_handlers.run_predict_pipeline) does NOT call this —
    it builds feat_df once and calls _assemble_predictions() directly, to
    avoid ever having two independently-built feature dataframes in play.
    """
    from .universe import build_universe, UniverseStatus
    from .features import build_all_features
    from .market_features import compute_market_features, compute_cross_sectional_ranks

    universe_df = build_universe(
        as_of_date, db_path,
        min_history_days=min_history_days,
        min_median_value_20d=min_median_value_20d,
    )
    eligible = universe_df[universe_df["status"].isin([
        UniverseStatus.ELIGIBLE,
        UniverseStatus.CORPORATE_ACTION_WARNING,
    ])]["ticker"].tolist()

    if not eligible:
        log.warning("No eligible stocks for %s", as_of_date)
        return []

    log.info("Computing features for %d eligible stocks on %s", len(eligible), as_of_date)

    feat_df = build_all_features(as_of_date, db_path, eligible)
    if feat_df.empty:
        return []

    mfeats = compute_market_features(as_of_date, db_path)
    ranks_df = compute_cross_sectional_ranks(as_of_date, db_path, eligible)
    if not ranks_df.empty:
        feat_df = feat_df.merge(ranks_df, on="ticker", how="left")
    for mkey, mval in mfeats.items():
        feat_df[mkey] = mval
    feat_df = feat_df.merge(
        universe_df[["ticker", "status", "has_corp_action_warning", "has_verified_corp_action",
                     "median_close_20d", "median_value_20d"]],
        on="ticker", how="left"
    )
    for c in feature_cols:
        if c not in feat_df.columns:
            feat_df[c] = np.nan
    feat_df = feat_df.reset_index(drop=True)

    X = feat_df[feature_cols]
    try:
        probs = model_pipe.predict_proba(X)[:, 1]
    except Exception as exc:
        log.error("Prediction failed: %s", exc)
        return []

    return _assemble_predictions(
        feat_df, probs, as_of_date, db_path, model_run_id, feature_cols, threshold,
        horizon_days=horizon_days, primary_target=primary_target, take_profit=take_profit,
        stop_loss=stop_loss, atr_multiplier=atr_multiplier, ensemble_breakdown=ensemble_breakdown,
    )



def save_predictions(
    predictions: list[dict],
    db_path: str,
    model_run_id: int,
    skip_existing: bool = True,
) -> int:
    """
    Save predictions to ml_weekly_predictions table. Returns rows written.

    Always upserts on (model_run_id, prediction_date, ticker, horizon_days):
    re-running predict for a date that's already saved (same model_run_id —
    e.g. after fixing the live ensemble's member list without retraining, as
    happened once already) now actually refreshes the stored row instead of
    silently no-oping. INSERT OR IGNORE used to be the default here, which
    meant a "success" predict run could leave stale/contaminated rows
    untouched in the DB while reporting fresh-looking numbers back to the
    caller. realized_return/realized_label/outcome_status/evaluated_at are
    deliberately excluded from the update so a later predict run can never
    erase real evaluation history written by evaluate_matured_predictions().
    skip_existing is kept for signature compatibility but no longer changes
    behavior — plain INSERT OR IGNORE had no safe use left once evaluation
    fields are protected by the upsert itself.
    """
    if not predictions:
        return 0

    # Ensure newer columns exist (dynamic migration, same pattern as
    # ensemble_probs_json below — avoids a full schema migration step for
    # incremental additions to this table).
    new_cols = {
        "ensemble_probs_json": "TEXT",
        "decision_status": "TEXT",
        "confidence_tier": "TEXT",
        "decision_reason": "TEXT",
        "model_coverage_json": "TEXT",
        "net_risk_reward": "REAL",
        "net_expected_value": "REAL",
        "entry_zone_low": "REAL",
        "entry_zone_high": "REAL",
    }
    with sqlite3.connect(db_path) as conn:
        cols_info = conn.execute("PRAGMA table_info(ml_weekly_predictions)").fetchall()
        existing_cols = {c[1] for c in cols_info}
        for col_name, col_type in new_cols.items():
            if col_name not in existing_cols:
                try:
                    conn.execute(f"ALTER TABLE ml_weekly_predictions ADD COLUMN {col_name} {col_type}")
                    conn.commit()
                    log.info("Added %s column to ml_weekly_predictions", col_name)
                except Exception:
                    pass

    cols = [
        "model_run_id", "prediction_date", "ticker", "horizon_days",
        "current_close", "next_open", "predicted_probability", "calibrated_probability",
        "predicted_return", "expected_upside", "expected_drawdown",
        "signal_status", "signal_threshold", "technical_score", "volume_score",
        "foreign_flow_score", "market_regime", "sector_regime",
        "reason_codes_json", "risk_flags_json", "feature_values_json",
        "ensemble_probs_json",
        "target_price", "stop_price", "realized_return", "realized_label",
        "outcome_status",
        "decision_status", "confidence_tier", "decision_reason", "model_coverage_json",
        "net_risk_reward", "net_expected_value", "entry_zone_low", "entry_zone_high",
    ]

    # Never overwritten on conflict — these belong to evaluate_matured_predictions(),
    # not to a re-run predict pass.
    evaluation_owned = {"realized_return", "realized_label", "outcome_status"}
    update_cols = [c for c in cols if c not in evaluation_owned and c not in ("model_run_id", "prediction_date", "ticker", "horizon_days")]
    update_clause = ", ".join(f"{c}=excluded.{c}" for c in update_cols)

    inserted = 0
    with sqlite3.connect(db_path) as conn:
        for pred in predictions:
            try:
                vals = [pred.get(c) for c in cols]
                conn.execute(
                    f"""INSERT INTO ml_weekly_predictions ({','.join(cols)})
                        VALUES ({','.join(['?' for _ in cols])})
                        ON CONFLICT(model_run_id, prediction_date, ticker, horizon_days)
                        DO UPDATE SET {update_clause}""",
                    vals
                )
                inserted += 1
            except Exception as exc:
                log.warning("Failed to save prediction for %s: %s", pred.get("ticker"), exc)
        conn.commit()

    return inserted




def evaluate_matured_predictions(
    db_path: str,
    as_of_date: Optional[str] = None,
    horizon_days: int = 5,
) -> int:
    """
    For predictions that have matured (prediction_date + 5 trading days <= today),
    compute realized_return and realized_label, update outcome_status.
    Returns count updated.
    """
    if as_of_date is None:
        as_of_date = date.today().isoformat()

    # Get all pending predictions
    with sqlite3.connect(db_path) as conn:
        pending = pd.read_sql_query(
            """SELECT id, prediction_date, ticker, current_close, next_open,
                      expected_upside, expected_drawdown, target_price, stop_price
               FROM ml_weekly_predictions
               WHERE outcome_status = 'PENDING'
                 AND prediction_date <= DATE(?, ?)""",
            conn,
            params=(as_of_date, f"-{horizon_days * 2} days"),
        )

    if pending.empty:
        return 0

    updated = 0
    with sqlite3.connect(db_path) as conn:
        for _, row in pending.iterrows():
            pred_date = row["prediction_date"]
            ticker = row["ticker"]

            # Find the 5th trading day after prediction date
            trading_days = conn.execute(
                """SELECT DISTINCT tanggal FROM ringkasan_saham_harian
                   WHERE tanggal > ? AND kode_saham = ?
                     AND volume > 0 AND harga_penutupan IS NOT NULL
                   ORDER BY tanggal LIMIT ?""",
                (pred_date, ticker, horizon_days)
            ).fetchall()

            if len(trading_days) < horizon_days:
                continue  # Not enough future data yet

            exit_date = trading_days[-1][0]

            # Get exit price
            exit_row = conn.execute(
                "SELECT harga_penutupan FROM ringkasan_saham_harian WHERE tanggal=? AND kode_saham=?",
                (exit_date, ticker)
            ).fetchone()

            if not exit_row or not exit_row[0]:
                continue

            exit_price = float(exit_row[0])
            entry_price = float(row["next_open"] or row["current_close"])

            if entry_price <= 0:
                continue

            realized_return = exit_price / entry_price - 1.0
            realized_label = 1 if realized_return >= float(row["expected_upside"] or 0.02) else 0
            outcome_status = "HIT" if realized_label == 1 else "MISS"

            conn.execute(
                """UPDATE ml_weekly_predictions
                   SET realized_return=?, realized_label=?, outcome_status=?, evaluated_at=?
                   WHERE id=?""",
                (round(realized_return, 4), realized_label, outcome_status,
                 datetime.now().isoformat(), int(row["id"]))
            )
            updated += 1

        conn.commit()

    log.info("Evaluated %d matured predictions", updated)
    return updated
