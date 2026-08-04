"""
ml_weekly/evaluation.py
Turns matured signal outcomes (ml_weekly_predictions.outcome_status/realized_return,
populated by predict.evaluate_matured_predictions) into accumulated knowledge:
calibration by confidence tier, validation of the decision-status gate, rolling
track record, and which reason codes / risk flags actually correlated with real
outcomes. This is meant to grow richer every day as more signals mature — it
reports honestly on however little or much history currently exists rather
than padding or faking a track record.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from typing import Optional

import pandas as pd

from .train import wilson_ci

MIN_HORIZON_DAYS = 5

TIER_ORDER = ["SANGAT_KUAT", "KUAT", "CUKUP", "LEMAH"]
TIER_LABELS = {
    "SANGAT_KUAT": "Sangat Kuat", "KUAT": "Kuat", "CUKUP": "Cukup", "LEMAH": "Lemah",
    None: "Belum Dievaluasi (data lama)",
}

DECISION_LABELS = {
    "QUALIFIED": "Qualified", "WATCHLIST": "Watchlist", "NO_TRADE": "No Trade",
    "ABSTAIN": "Abstain", "DATA_INVALID": "Data Invalid",
    "MODEL_UNAVAILABLE": "Model Unavailable", "SIGNAL_EXPIRED": "Expired",
    None: "Belum Dievaluasi (data lama)",
}

# Strips trailing parenthetical numbers/percentages so "RSI14 oversold (29)"
# and "RSI14 oversold (31)" group together as the same underlying reason.
_TRAILING_PAREN_RE = re.compile(r"\s*\([^)]*\)\s*$")


def _normalize_text(text: str) -> str:
    t = _TRAILING_PAREN_RE.sub("", text).strip()
    return t or text.strip()


def _wilson(k: int, n: int) -> tuple[float, float]:
    if n <= 0:
        return (0.0, 1.0)
    return wilson_ci(k, n)


def _rate_row(n: int, hits: int, returns: Optional[list[float]] = None) -> dict:
    lo, hi = _wilson(hits, n)
    row = {
        "n": n,
        "hits": hits,
        "misses": n - hits,
        "hit_rate": round(hits / n, 4) if n else None,
        "hit_rate_ci_lower": round(lo, 4) if n else None,
        "hit_rate_ci_upper": round(hi, 4) if n else None,
    }
    if returns:
        row["avg_return"] = round(sum(returns) / len(returns), 4)
    else:
        row["avg_return"] = None
    return row


def compute_evaluation_summary(db_path: str, min_group_n: int = 3, top_n_reasons: int = 12) -> dict:
    """
    Aggregate every evaluated (HIT/MISS) prediction into a knowledge report.
    Returns a dict with honest sample sizes throughout — callers/UI should
    treat any bucket with a tiny n as low-confidence, not hide it, since
    that transparency is the point of this dashboard.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    maturity_row = conn.execute(
        """SELECT
             MIN(prediction_date) AS oldest, MAX(prediction_date) AS newest,
             COUNT(*) AS total,
             SUM(CASE WHEN outcome_status IN ('HIT','MISS') THEN 1 ELSE 0 END) AS evaluated,
             SUM(CASE WHEN outcome_status='PENDING' THEN 1 ELSE 0 END) AS pending
           FROM ml_weekly_predictions"""
    ).fetchone()

    oldest = maturity_row["oldest"]
    newest_source_date = conn.execute("SELECT MAX(tanggal) FROM ringkasan_saham_harian").fetchone()[0]
    trading_days_elapsed = None
    if oldest and newest_source_date:
        trading_days_elapsed = conn.execute(
            "SELECT COUNT(DISTINCT tanggal) FROM ringkasan_saham_harian WHERE tanggal > ? AND tanggal <= ? AND volume > 0",
            (oldest, newest_source_date),
        ).fetchone()[0]

    data_maturity = {
        "oldest_prediction_date": oldest,
        "newest_prediction_date": maturity_row["newest"],
        "total_predictions": maturity_row["total"] or 0,
        "evaluated_count": maturity_row["evaluated"] or 0,
        "pending_count": maturity_row["pending"] or 0,
        "trading_days_since_oldest": trading_days_elapsed,
        "min_horizon_days": MIN_HORIZON_DAYS,
        "note": (
            f"Prediksi tertua ({oldest}) baru berumur {trading_days_elapsed} hari bursa — "
            f"butuh minimal {MIN_HORIZON_DAYS} hari bursa sebelum outcome-nya bisa diverifikasi."
            if oldest and trading_days_elapsed is not None and trading_days_elapsed < MIN_HORIZON_DAYS
            else None
        ),
    }

    rows = conn.execute(
        """SELECT model_run_id, prediction_date, ticker, outcome_status, realized_return, evaluated_at,
                  confidence_tier, decision_status, reason_codes_json, risk_flags_json,
                  ensemble_probs_json, market_regime, sector_regime
           FROM ml_weekly_predictions
           WHERE outcome_status IN ('HIT','MISS')
           ORDER BY prediction_date ASC, id ASC"""
    ).fetchall()

    # Every retrain (bug fix, feature change, new model dropped/added — see
    # api_handlers.ALL_MODEL_NAMES retirement logic) makes older evaluated
    # signals describe a DIFFERENT system than the one currently live. Flag
    # it explicitly rather than silently blending "how the old, since-fixed
    # pipeline performed" into what reads as "how the current model performs" —
    # that mislabeling is exactly what caused real confusion once already.
    current_run_ids = {
        r[0] for r in conn.execute(
            "SELECT id FROM ml_weekly_model_runs WHERE status IN ('ensemble_member','active')"
        ).fetchall()
    }
    conn.close()

    stale_evaluated = sum(1 for r in rows if r["model_run_id"] not in current_run_ids)
    current_evaluated = len(rows) - stale_evaluated
    data_maturity["stale_model_evaluated_count"] = stale_evaluated
    data_maturity["current_model_evaluated_count"] = current_evaluated
    data_maturity["stale_model_warning"] = (
        f"Semua {stale_evaluated} sinyal yang sudah dievaluasi di bawah ini berasal dari versi model "
        f"SEBELUMNYA (sudah diganti/diperbaiki) — belum ada satupun sinyal dari model yang aktif "
        f"sekarang yang cukup umur untuk dievaluasi (perlu {MIN_HORIZON_DAYS} hari bursa sejak sinyal "
        f"dibuat). Statistik di bawah mencerminkan performa model versi lama, BUKAN model saat ini."
        if stale_evaluated > 0 and current_evaluated == 0
        else None
    )

    if not rows:
        return {
            "data_maturity": data_maturity,
            "overall": _rate_row(0, 0),
            "by_confidence_tier": [],
            "by_decision_status": [],
            "rolling": [],
            "top_reason_codes": [],
            "top_risk_flags": [],
            "by_market_regime": [],
            "timeline": [],
        }

    df = pd.DataFrame([dict(r) for r in rows])
    df["is_hit"] = (df["outcome_status"] == "HIT").astype(int)

    overall = _rate_row(len(df), int(df["is_hit"].sum()), df["realized_return"].dropna().tolist())

    # ── Calibration: does a higher confidence tier actually hit more? ──────
    by_tier = []
    for tier in TIER_ORDER + [None]:
        sub = df[df["confidence_tier"] == tier] if tier else df[df["confidence_tier"].isna()]
        if sub.empty:
            continue
        row = _rate_row(len(sub), int(sub["is_hit"].sum()), sub["realized_return"].dropna().tolist())
        row["tier"] = tier or "BELUM_DIEVALUASI"
        row["label"] = TIER_LABELS.get(tier, tier)
        by_tier.append(row)

    # ── Does the decision-status gate actually separate good from bad? ────
    by_decision = []
    for status, sub in df.groupby(df["decision_status"].fillna("BELUM_DIEVALUASI")):
        row = _rate_row(len(sub), int(sub["is_hit"].sum()), sub["realized_return"].dropna().tolist())
        row["status"] = status
        row["label"] = DECISION_LABELS.get(None if status == "BELUM_DIEVALUASI" else status, status)
        by_decision.append(row)
    by_decision.sort(key=lambda r: -r["n"])

    # ── Rolling track record (most recent N evaluated signals) ────────────
    rolling = []
    for window in (20, 60, 120):
        tail = df.tail(window)
        if tail.empty:
            continue
        row = _rate_row(len(tail), int(tail["is_hit"].sum()), tail["realized_return"].dropna().tolist())
        row["window"] = window
        rolling.append(row)

    # ── Which reasons/risk flags actually correlated with real outcomes ───
    def _tally(col: str) -> list[dict]:
        counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # [n, hits]
        for _, r in df.iterrows():
            try:
                items = json.loads(r[col]) if r[col] else []
            except Exception:
                items = []
            seen_this_row = set()
            for item in items:
                key = _normalize_text(str(item))
                if key in seen_this_row:
                    continue
                seen_this_row.add(key)
                counts[key][0] += 1
                counts[key][1] += int(r["is_hit"])
        out = []
        for text, (n, hits) in counts.items():
            if n < min_group_n:
                continue
            row = _rate_row(n, hits)
            row["text"] = text
            out.append(row)
        out.sort(key=lambda r: -r["n"])
        return out[:top_n_reasons]

    top_reasons = _tally("reason_codes_json")
    top_flags = _tally("risk_flags_json")

    # ── Performance by market regime (was the regime filter actually useful?) ─
    by_regime = []
    for regime, sub in df.groupby(df["market_regime"].fillna("UNKNOWN")):
        row = _rate_row(len(sub), int(sub["is_hit"].sum()), sub["realized_return"].dropna().tolist())
        row["regime"] = regime
        by_regime.append(row)
    by_regime.sort(key=lambda r: -r["n"])

    # ── Timeline for charting ───────────────────────────────────────────
    timeline = []
    for pdate, sub in df.groupby("prediction_date"):
        timeline.append({
            "date": pdate,
            "evaluated": len(sub),
            "hits": int(sub["is_hit"].sum()),
            "misses": len(sub) - int(sub["is_hit"].sum()),
            "avg_return": round(sub["realized_return"].dropna().mean(), 4) if sub["realized_return"].notna().any() else None,
        })
    timeline.sort(key=lambda r: r["date"])

    return {
        "data_maturity": data_maturity,
        "overall": overall,
        "by_confidence_tier": by_tier,
        "by_decision_status": by_decision,
        "rolling": rolling,
        "top_reason_codes": top_reasons,
        "top_risk_flags": top_flags,
        "by_market_regime": by_regime,
        "timeline": timeline,
    }


_SIGNAL_SORT_COLUMNS = {
    "date": "prediction_date DESC, id DESC",
    "probability": "calibrated_probability DESC, prediction_date DESC",
}


def list_evaluated_signals(
    db_path: str, limit: int = 50, offset: int = 0, ticker: Optional[str] = None,
    sort_by: str = "date",
) -> dict:
    """
    The literal "sinyal ini, setelah N hari, harga naik/turun berapa persen"
    view: one row per matured signal with the price it was issued at, the
    price after horizon_days, and the realized percent move — the raw
    evidence the aggregate stats in compute_evaluation_summary() are built
    from, so a user can check any individual call instead of only trusting
    a summary number.

    sort_by="probability" ranks by the probability the model actually gave
    at signal time (highest first) instead of recency — "our most confident
    calls, did they actually pay off" — using calibrated_probability, the
    same field the live Sinyal tab ranks and displays.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    where = ["outcome_status IN ('HIT','MISS')"]
    params: list = []
    if ticker:
        where.append("ticker = ?")
        params.append(ticker.strip().upper())
    where_sql = " AND ".join(where)
    order_sql = _SIGNAL_SORT_COLUMNS.get(sort_by, _SIGNAL_SORT_COLUMNS["date"])

    total = conn.execute(
        f"SELECT COUNT(*) FROM ml_weekly_predictions WHERE {where_sql}", params
    ).fetchone()[0]

    rows = conn.execute(
        f"""SELECT ticker, prediction_date, horizon_days, current_close, realized_return,
                   outcome_status, confidence_tier, decision_status, evaluated_at,
                   calibrated_probability
           FROM ml_weekly_predictions
           WHERE {where_sql}
           ORDER BY {order_sql}
           LIMIT ? OFFSET ?""",
        params + [limit, offset],
    ).fetchall()
    conn.close()

    signals = []
    for r in rows:
        entry = r["current_close"]
        ret = r["realized_return"]
        exit_price = round(entry * (1.0 + ret), 2) if entry is not None and ret is not None else None
        signals.append({
            "ticker": r["ticker"],
            "prediction_date": r["prediction_date"],
            "signal_probability": round(r["calibrated_probability"] * 100.0, 1) if r["calibrated_probability"] is not None else None,
            "horizon_days": r["horizon_days"],
            "entry_price": entry,
            "exit_price": exit_price,
            "pct_change": round(ret * 100.0, 2) if ret is not None else None,
            "outcome_status": r["outcome_status"],
            "confidence_tier": r["confidence_tier"],
            "decision_status": r["decision_status"],
            "evaluated_at": r["evaluated_at"],
        })

    return {"total": total, "limit": limit, "offset": offset, "signals": signals}


def list_top_signals_by_batch(
    db_path: str, top_n: int = 10, batch_limit: int = 5, batch_offset: int = 0,
) -> dict:
    """
    "Top N PER TARIKAN" view — not one flat top-N pooled across all history.
    Every distinct prediction_date is one prediction run ("tarikan"); within
    each batch, only that batch's own top-N signals by the probability the
    model actually gave at the time are shown. So "4 tarikan x 10 = 40, dari
    situ berapa yang berhasil" is answerable exactly as asked, and grows
    batch by batch as more signals mature — paginated over BATCHES
    (batch_offset/batch_limit), with an aggregate computed across ALL
    batches (not just the page being viewed) so the running total is always
    correct regardless of which page is open.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    dates = [r[0] for r in conn.execute(
        """SELECT DISTINCT prediction_date FROM ml_weekly_predictions
           WHERE outcome_status IN ('HIT','MISS')
           ORDER BY prediction_date DESC"""
    ).fetchall()]
    total_batches = len(dates)
    page_dates = dates[batch_offset: batch_offset + batch_limit]

    def _row_to_signal(r: sqlite3.Row) -> dict:
        entry = r["current_close"]
        ret = r["realized_return"]
        exit_price = round(entry * (1.0 + ret), 2) if entry is not None and ret is not None else None
        return {
            "ticker": r["ticker"],
            "signal_probability": round(r["calibrated_probability"] * 100.0, 1) if r["calibrated_probability"] is not None else None,
            "horizon_days": r["horizon_days"],
            "entry_price": entry,
            "exit_price": exit_price,
            "pct_change": round(ret * 100.0, 2) if ret is not None else None,
            "outcome_status": r["outcome_status"],
            "confidence_tier": r["confidence_tier"],
            "decision_status": r["decision_status"],
        }

    batches = []
    for pdate in page_dates:
        rows = conn.execute(
            """SELECT ticker, horizon_days, current_close, realized_return,
                      outcome_status, confidence_tier, decision_status, calibrated_probability
               FROM ml_weekly_predictions
               WHERE outcome_status IN ('HIT','MISS') AND prediction_date = ?
               ORDER BY calibrated_probability DESC
               LIMIT ?""",
            (pdate, top_n),
        ).fetchall()
        signals = [_row_to_signal(r) for r in rows]
        hits = sum(1 for s in signals if s["outcome_status"] == "HIT")
        batches.append({
            "prediction_date": pdate,
            "n": len(signals),
            "hits": hits,
            "hit_rate": round(hits / len(signals), 4) if signals else None,
            "signals": signals,
        })

    # Aggregate across EVERY batch (not just this page) via a window
    # function — cheaper and more honest than looping in Python and risking
    # drift from what's actually paginated.
    agg_row = conn.execute(
        """
        WITH ranked AS (
            SELECT outcome_status,
                   ROW_NUMBER() OVER (PARTITION BY prediction_date ORDER BY calibrated_probability DESC) AS rn
            FROM ml_weekly_predictions
            WHERE outcome_status IN ('HIT','MISS')
        )
        SELECT COUNT(*), SUM(CASE WHEN outcome_status='HIT' THEN 1 ELSE 0 END)
        FROM ranked WHERE rn <= ?
        """,
        (top_n,),
    ).fetchone()
    conn.close()

    total_signals, total_hits = agg_row[0] or 0, agg_row[1] or 0
    lo, hi = _wilson(total_hits, total_signals)

    return {
        "top_n": top_n,
        "total_batches": total_batches,
        "batch_limit": batch_limit,
        "batch_offset": batch_offset,
        "batches": batches,
        "aggregate": {
            "total_batches": total_batches,
            "total_signals": total_signals,
            "total_hits": total_hits,
            "hit_rate": round(total_hits / total_signals, 4) if total_signals else None,
            "hit_rate_ci_lower": round(lo, 4) if total_signals else None,
            "hit_rate_ci_upper": round(hi, 4) if total_signals else None,
        },
    }
