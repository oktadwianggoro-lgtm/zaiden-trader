"""
ml_weekly/ensemble_eval.py
Honest backtest of the ACTUAL ensemble combination used to generate live
signals (predict.ensemble_predict_proba's dynamic-weighted average), instead
of only trusting each individual member's solo backtest.

Why this exists: predictions served to users are produced by combining all
ensemble members' probabilities, but until now only each individual model's
own probability output had ever been backtested. The combined output had
never been evaluated as a unit, so the "holdout_status" shown in the UI
(copied from the single newest member) did not describe the method that
actually filters signals.
"""
from __future__ import annotations
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


def evaluate_ensemble(
    db_path: str, models_dir: Path, config=None, progress_cb=None,
    prebuilt_dataset: Optional[pd.DataFrame] = None,
) -> dict:
    """
    Build the labeled dataset once, run the live ensemble combination on the
    validation and holdout splits, select a threshold on validation only
    (never touching holdout for tuning), then evaluate holdout at that
    threshold. Persists results and returns a summary dict.

    prebuilt_dataset: pass the same dataset already built for training (see
    pipeline.run_full_pipeline's prebuilt_dataset) to skip rebuilding it a
    7th time when this runs as part of train-all.
    """
    from .config import get_config
    from .pipeline import build_dataset
    from .predict import load_ensemble_models, ensemble_predict_proba
    from .train import select_threshold, evaluate_predictions, wilson_ci

    if config is None:
        config = get_config()

    def _cb(pct, msg):
        log.info("[ensemble-eval %d%%] %s", pct, msg)
        if progress_cb:
            progress_cb(pct, msg)

    members = load_ensemble_models(db_path, models_dir)
    if not members:
        return {"error": "Tidak ada model ensemble tersedia."}

    primary_run_id = members[0]["model_run_id"]
    model_names = [m["model_name"] for m in members]

    if prebuilt_dataset is not None:
        _cb(50, f"Memakai dataset yang sudah dibangun ({len(prebuilt_dataset):,} baris)...")
        df = prebuilt_dataset
    else:
        _cb(5, f"Membangun dataset untuk evaluasi ensemble ({len(members)} model)...")
        df = build_dataset(
            db_path=db_path,
            train_start=config.training_start,
            holdout_start=config.holdout_start,
            horizon_days=config.horizon_days,
            primary_target=config.primary_return_target,
            take_profit=config.take_profit,
            stop_loss=config.stop_loss,
            min_history_days=config.min_history_days,
            min_median_value_20d=config.min_median_value_20d,
            step_days=config.training_step_days,
            progress_cb=lambda p, m: _cb(5 + int(p * 0.5), m),
        )

    df = df.dropna(subset=["weekly_bullish"]).copy()

    val_mask = (df["signal_date"] >= config.validation_start) & (df["signal_date"] <= config.validation_end)
    hold_mask = df["signal_date"] >= config.holdout_start

    df_val = df[val_mask]
    df_hold = df[hold_mask]

    if df_val.empty or df_hold.empty:
        return {"error": "Data validasi/holdout kosong untuk evaluasi ensemble."}

    _cb(60, "Menjalankan kombinasi ensemble pada data validasi...")
    val_probs, _ = ensemble_predict_proba(members, df_val)
    y_val = df_val["weekly_bullish"].values.astype(int)
    r_val = df_val["trade_return"].values if "trade_return" in df_val.columns else None

    threshold, val_metrics = select_threshold(
        y_val, val_probs,
        precision_target=config.precision_target,
        min_signals=config.min_validation_signals,
        returns=r_val,
    )

    _cb(80, "Menjalankan kombinasi ensemble pada data holdout...")
    hold_probs, _ = ensemble_predict_proba(members, df_hold)
    y_hold = df_hold["weekly_bullish"].values.astype(int)
    r_hold = df_hold["trade_return"].values if "trade_return" in df_hold.columns else None

    hold_metrics = evaluate_predictions(y_hold, hold_probs, threshold=threshold, returns=r_hold)

    # Quarterly precision breakdown (mirrors train.evaluate_on_holdout)
    df_hold = df_hold.copy()
    df_hold["_signal"] = (hold_probs >= threshold).astype(int)
    df_hold["_quarter"] = pd.to_datetime(df_hold["signal_date"]).dt.to_period("Q").astype(str)
    quarterly = []
    for q, grp in df_hold.groupby("_quarter"):
        sig = int(grp["_signal"].sum())
        if sig >= 5:
            prec = float((grp["_signal"] & grp["weekly_bullish"]).sum() / sig)
        else:
            prec = None
        quarterly.append({"quarter": q, "signals": sig, "precision": prec})
    hold_metrics["quarterly_precision"] = quarterly
    hold_metrics["holdout_start"] = config.holdout_start
    hold_metrics["holdout_end"] = df_hold["signal_date"].max()
    hold_metrics["total_holdout_rows"] = int(len(df_hold))
    hold_metrics["positive_rate"] = float(y_hold.mean())

    if hold_metrics["signal_count"] < config.min_holdout_signals:
        holdout_status = "INSUFFICIENT_EVIDENCE"
    elif hold_metrics["precision"] >= config.precision_target and hold_metrics["precision_ci_lower"] >= config.precision_target - 0.05:
        holdout_status = "VERIFIED_ABOVE_90"
    elif hold_metrics["precision"] >= config.precision_target:
        holdout_status = "PROVISIONAL_ABOVE_90"
    else:
        holdout_status = "NOT_VERIFIED"
    hold_metrics["holdout_status"] = holdout_status

    now = datetime.now().isoformat()

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO ml_weekly_backtest_metrics (
                model_run_id, evaluation_split, period_start, period_end,
                threshold, signal_count, correct_count, false_positive_count,
                precision, precision_ci_lower, precision_ci_upper, coverage,
                recall, pr_auc, brier_score, metrics_json, notes
            ) VALUES (?, 'ensemble_validation', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                primary_run_id, config.validation_start, config.validation_end,
                threshold,
                val_metrics.get("signal_count", 0), val_metrics.get("correct_count", 0),
                val_metrics.get("false_positive_count", 0), val_metrics.get("precision", 0),
                val_metrics.get("precision_ci_lower", 0), val_metrics.get("precision_ci_upper", 1),
                val_metrics.get("coverage", 0), val_metrics.get("recall", 0),
                val_metrics.get("pr_auc"), val_metrics.get("brier_score"),
                json.dumps(val_metrics, default=float),
                f"Ensemble of {len(members)}: {', '.join(model_names)}",
            )
        )
        conn.execute(
            """INSERT INTO ml_weekly_backtest_metrics (
                model_run_id, evaluation_split, period_start, period_end,
                threshold, signal_count, correct_count, false_positive_count,
                precision, precision_ci_lower, precision_ci_upper, coverage,
                recall, pr_auc, brier_score, metrics_json, notes
            ) VALUES (?, 'ensemble_holdout', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                primary_run_id, config.holdout_start, hold_metrics.get("holdout_end", ""),
                threshold,
                hold_metrics.get("signal_count", 0), hold_metrics.get("correct_count", 0),
                hold_metrics.get("false_positive_count", 0), hold_metrics.get("precision", 0),
                hold_metrics.get("precision_ci_lower", 0), hold_metrics.get("precision_ci_upper", 1),
                hold_metrics.get("coverage", 0), hold_metrics.get("recall", 0),
                hold_metrics.get("pr_auc"), hold_metrics.get("brier_score"),
                json.dumps(hold_metrics, default=float),
                f"Ensemble of {len(members)}: {', '.join(model_names)}",
            )
        )

        approval = "approved" if holdout_status in ("VERIFIED_ABOVE_90", "PROVISIONAL_ABOVE_90") else "pending"
        conn.execute(
            """INSERT INTO ml_weekly_thresholds (
                model_run_id, threshold_type, threshold_value,
                validation_precision, validation_coverage, validation_signals,
                approval_status, effective_date, notes
            ) VALUES (?, 'ensemble_dynamic_weighted', ?, ?, ?, ?, ?, ?, ?)""",
            (
                primary_run_id, threshold,
                val_metrics.get("precision", 0), val_metrics.get("coverage", 0),
                val_metrics.get("signal_count", 0),
                approval, now,
                json.dumps({"model_names": model_names, "holdout_status": holdout_status,
                            "holdout_precision": hold_metrics.get("precision"),
                            "holdout_signal_count": hold_metrics.get("signal_count")}),
            )
        )
        conn.commit()

    _cb(100, f"Evaluasi ensemble selesai: threshold={threshold:.2f}, holdout_status={holdout_status}")

    return {
        "status": "success",
        "primary_run_id": primary_run_id,
        "model_names": model_names,
        "selected_threshold": threshold,
        "validation_metrics": val_metrics,
        "holdout_metrics": hold_metrics,
        "holdout_status": holdout_status,
    }


def get_latest_ensemble_status(db_path: str) -> Optional[dict]:
    """Return the most recently computed ensemble-level backtest status, or None
    (including when the schema hasn't been migrated yet — callers treat that
    the same as "never evaluated")."""
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                """SELECT threshold_value, validation_precision, validation_coverage,
                          validation_signals, approval_status, effective_date, notes, model_run_id
                   FROM ml_weekly_thresholds
                   WHERE threshold_type = 'ensemble_dynamic_weighted'
                   ORDER BY id DESC LIMIT 1"""
            ).fetchone()
    except sqlite3.OperationalError:
        return None
    if not row:
        return None

    notes = json.loads(row[6]) if row[6] else {}
    return {
        "model_run_id": row[7],
        "threshold": row[0],
        "validation_precision": row[1],
        "validation_coverage": row[2],
        "validation_signal_count": row[3],
        "approval_status": row[4],
        "effective_date": row[5],
        "model_names": notes.get("model_names", []),
        "holdout_status": notes.get("holdout_status"),
        "holdout_precision": notes.get("holdout_precision"),
        "holdout_signal_count": notes.get("holdout_signal_count"),
    }
