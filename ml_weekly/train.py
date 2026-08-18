"""
ml_weekly/train.py
Training pipeline with walk-forward validation, calibration, and
threshold selection on validation data only.

Critical: holdout is NEVER touched during training or threshold selection.
"""
from __future__ import annotations
import json
import logging
import sqlite3
import warnings
from pathlib import Path
from datetime import datetime, date
from typing import Optional

import numpy as np
import pandas as pd
import joblib
from sklearn.ensemble import (
    RandomForestClassifier,
    HistGradientBoostingClassifier,
    GradientBoostingClassifier,
    ExtraTreesClassifier,
)
from sklearn.linear_model import LogisticRegression

try:
    from xgboost import XGBClassifier
except (ImportError, OSError):
    XGBClassifier = None

try:
    from lightgbm import LGBMClassifier
except (ImportError, OSError):
    LGBMClassifier = None
from sklearn.preprocessing import StandardScaler
from sklearn.calibration import CalibratedClassifierCV, calibration_curve
try:
    from sklearn.frozen import FrozenEstimator
except ImportError:
    FrozenEstimator = None
from sklearn.metrics import (
    precision_score, recall_score, f1_score,
    brier_score_loss, average_precision_score,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer

warnings.filterwarnings("ignore")
log = logging.getLogger(__name__)


def _make_prefit_calibrator(pipe, method: str = "isotonic") -> CalibratedClassifierCV:
    """
    Wrap an already-fitted estimator for calibration on separate held-out data.
    sklearn >=1.6 removed cv="prefit" in favor of wrapping with FrozenEstimator.
    """
    if FrozenEstimator is not None:
        return CalibratedClassifierCV(FrozenEstimator(pipe), method=method)
    return CalibratedClassifierCV(pipe, cv="prefit", method=method)


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson confidence interval for a proportion k/n."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z**2 / n
    center = (p + z**2 / (2 * n)) / denom
    half = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def make_model_pipeline(
    model_name: str,
    seed: int = 42,
) -> Pipeline:
    """Create sklearn Pipeline for a given model name."""
    imputer = SimpleImputer(strategy="median")

    if model_name == "logistic":
        # solver="saga" earns its keep on L1/elasticnet penalties or very
        # sparse data — neither applies here (plain L2, dense features from
        # StandardScaler), so it was just paying saga's slow-convergence
        # cost for nothing. Confirmed stuck 30+ min fitting a single fold at
        # ~380K rows x 129 features (full 2020+ history, see step_days fix)
        # before this change. lbfgs is the standard solver for this exact
        # configuration and converges in a fraction of the time.
        clf = LogisticRegression(
            C=0.1, max_iter=1000, random_state=seed,
            class_weight="balanced", solver="lbfgs",
        )
    elif model_name == "random_forest":
        clf = RandomForestClassifier(
            n_estimators=300, max_depth=8, min_samples_leaf=20,
            random_state=seed, class_weight="balanced",
            n_jobs=-1,
        )
    elif model_name == "hist_gradient_boosting":
        clf = HistGradientBoostingClassifier(
            max_iter=300, max_leaf_nodes=31,
            min_samples_leaf=20, learning_rate=0.05,
            random_state=seed,
        )
    elif model_name == "gradient_boosting":
        clf = GradientBoostingClassifier(
            n_estimators=200, max_depth=4,
            min_samples_leaf=20, learning_rate=0.05,
            random_state=seed, subsample=0.8,
        )
    elif model_name == "extra_trees":
        clf = ExtraTreesClassifier(
            n_estimators=300, max_depth=8, min_samples_leaf=20,
            random_state=seed, class_weight="balanced",
            n_jobs=-1,
        )
    elif model_name == "xgboost":
        if XGBClassifier is None:
            raise ValueError("XGBoost is not installed. Run 'pip install xgboost'")
        clf = XGBClassifier(
            n_estimators=300, max_depth=6, learning_rate=0.05,
            random_state=seed, scale_pos_weight=3.0,
            n_jobs=-1, eval_metric="logloss"
        )
    elif model_name == "lightgbm":
        if LGBMClassifier is None:
            raise ValueError("LightGBM is not installed. Run 'pip install lightgbm'")
        clf = LGBMClassifier(
            n_estimators=300, max_depth=6, learning_rate=0.05,
            random_state=seed, class_weight="balanced",
            n_jobs=-1, verbose=-1
        )
    else:
        raise ValueError(f"Unknown model: {model_name}")

    # HistGradientBoosting, LightGBM, XGBoost handle NaN natively
    if model_name in ("hist_gradient_boosting", "lightgbm", "xgboost"):
        steps = [("clf", clf)]
    else:
        steps = [
            ("imputer", imputer),
            ("scaler", StandardScaler()),
            ("clf", clf),
        ]

    return Pipeline(steps)


def evaluate_predictions(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    threshold: float,
    buy_fee: float = 0.00155,
    sell_fee: float = 0.00255,
    slippage: float = 0.001,
    returns: Optional[np.ndarray] = None,
) -> dict:
    """Compute evaluation metrics at a given threshold."""
    y_pred = (y_prob >= threshold).astype(int)
    total = int(len(y_true))
    signals = int(y_pred.sum())
    correct = int((y_pred & y_true).sum())
    fps = int(y_pred.sum()) - correct

    precision = correct / signals if signals > 0 else 0.0
    ci_lo, ci_hi = wilson_ci(correct, signals)
    coverage = signals / total if total > 0 else 0.0

    positives = int(y_true.sum())
    recall = correct / positives if positives > 0 else 0.0

    metrics = {
        "threshold": threshold,
        "total_candidates": total,
        "signal_count": signals,
        "correct_count": correct,
        "false_positive_count": fps,
        "precision": precision,
        "precision_ci_lower": ci_lo,
        "precision_ci_upper": ci_hi,
        "coverage": coverage,
        "recall": recall,
        "brier_score": float(brier_score_loss(y_true, y_prob)) if len(np.unique(y_true)) > 1 else np.nan,
        "pr_auc": float(average_precision_score(y_true, y_prob)) if len(np.unique(y_true)) > 1 else np.nan,
        "roc_auc": float(roc_auc_score(y_true, y_prob)) if len(np.unique(y_true)) > 1 else np.nan,
    }

    # Trading metrics
    if returns is not None and signals > 0:
        signal_returns = returns[y_pred == 1]
        total_cost = buy_fee + sell_fee + 2 * slippage
        net_returns = signal_returns - total_cost

        wins = net_returns[net_returns > 0]
        losses = net_returns[net_returns <= 0]

        metrics["gross_return"] = float(signal_returns.mean())
        metrics["net_return"] = float(net_returns.mean())
        metrics["win_rate"] = float(len(wins) / len(net_returns))
        metrics["avg_win"] = float(wins.mean()) if len(wins) > 0 else 0.0
        metrics["avg_loss"] = float(losses.mean()) if len(losses) > 0 else 0.0
        metrics["profit_factor"] = float(
            wins.sum() / abs(losses.sum())
        ) if len(losses) > 0 and losses.sum() != 0 else np.nan
        metrics["expectancy"] = float(net_returns.mean())

    return metrics


def select_threshold(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    precision_target: float = 0.90,
    min_signals: int = 30,
    returns: Optional[np.ndarray] = None,
) -> tuple[float, dict]:
    """
    Select threshold on validation data by maximizing coverage
    with constraint precision >= precision_target.
    Returns (best_threshold, metrics_dict).
    """
    candidates = np.arange(0.50, 0.99, 0.01)
    best_threshold = 0.85
    best_coverage = 0.0
    best_metrics = {}
    highest_precision_threshold = None
    highest_precision_metrics = None

    for thr in candidates:
        m = evaluate_predictions(y_true, y_prob, thr, returns=returns)
        if m["signal_count"] >= min_signals:
            # Tracked regardless of whether it clears precision_target, so the
            # fallback below has a real "best we could actually do" answer
            # instead of a number disconnected from what the model outputs.
            if highest_precision_metrics is None or m["precision"] > highest_precision_metrics["precision"]:
                highest_precision_threshold = thr
                highest_precision_metrics = m
        if (
            m["signal_count"] >= min_signals and
            m["precision"] >= precision_target and
            m["precision_ci_lower"] >= precision_target - 0.05 and  # allow 5pp below
            m["coverage"] > best_coverage
        ):
            best_coverage = m["coverage"]
            best_threshold = thr
            best_metrics = m

    if not best_metrics:
        # No threshold clears precision_target with enough signals. Fall back
        # to whichever candidate had the HIGHEST actual precision (not a
        # hardcoded number unrelated to what this model outputs — see
        # config.precision_target's 2026-08-08 note for why a fixed 0.90
        # fallback here silently made QUALIFIED unreachable for every retrain
        # regardless of real model skill). Still flagged as unverified so
        # downstream gating (compute_decision_status) keeps treating it as
        # WATCHLIST-at-best until a holdout evaluation actually confirms it.
        if highest_precision_metrics is not None:
            best_threshold = highest_precision_threshold
            best_metrics = highest_precision_metrics
        else:
            best_threshold = 0.85
            best_metrics = evaluate_predictions(y_true, y_prob, best_threshold, returns=returns)
        best_metrics["threshold_warning"] = "NO_THRESHOLD_MEETS_PRECISION_TARGET"

    return float(best_threshold), best_metrics


def walk_forward_train(
    df: pd.DataFrame,
    feature_cols: list[str],
    label_col: str = "weekly_bullish",
    date_col: str = "signal_date",
    return_col: str = "trade_return",
    train_start: str = "2020-01-02",
    train_end: str = "2023-12-31",
    val_start: str = "2024-01-01",
    val_end: str = "2024-12-31",
    wf_train_months: int = 24,
    wf_val_months: int = 3,
    purge_days: int = 5,
    embargo_days: int = 5,
    model_name: str = "hist_gradient_boosting",
    seed: int = 42,
    precision_target: float = 0.90,
    min_signals: int = 30,
) -> dict:
    """
    Walk-forward cross-validation on training+validation data.
    Returns results dict with per-fold metrics and overall validation metrics.
    """
    from dateutil.relativedelta import relativedelta

    df = df.copy()
    df[date_col] = pd.to_datetime(df[date_col])
    df = df.sort_values(date_col).reset_index(drop=True)

    # Filter to train+val period only
    df_tv = df[
        (df[date_col] >= pd.Timestamp(train_start)) &
        (df[date_col] <= pd.Timestamp(val_end))
    ].copy()

    df_tv = df_tv.dropna(subset=[label_col])
    df_tv = df_tv[df_tv[label_col].isin([0, 1])]

    if df_tv.empty:
        return {"error": "No data in train+val period after filtering"}

    log.info(
        "Walk-forward: %d rows, %d positive, dates %s to %s",
        len(df_tv),
        int(df_tv[label_col].sum()),
        df_tv[date_col].min().date(),
        df_tv[date_col].max().date(),
    )

    # Generate fold boundaries
    folds = []
    fold_train_start = pd.Timestamp(train_start)
    while True:
        fold_train_end = fold_train_start + relativedelta(months=wf_train_months)
        fold_val_start = fold_train_end + pd.Timedelta(days=purge_days)
        fold_val_end = fold_val_start + relativedelta(months=wf_val_months)

        if fold_val_start > pd.Timestamp(val_end):
            break
        fold_val_end = min(fold_val_end, pd.Timestamp(val_end))

        folds.append((fold_train_start, fold_train_end, fold_val_start, fold_val_end))
        fold_train_start = fold_train_start + relativedelta(months=3)  # slide by 3 months

    if not folds:
        # Single fold
        folds = [(
            pd.Timestamp(train_start),
            pd.Timestamp(train_end),
            pd.Timestamp(val_start),
            pd.Timestamp(val_end),
        )]

    fold_results = []
    all_val_probs = []
    all_val_true = []
    all_val_returns = []

    for fold_idx, (ts, te, vs, ve) in enumerate(folds):
        train_mask = (df_tv[date_col] >= ts) & (df_tv[date_col] <= te)
        val_mask = (df_tv[date_col] >= vs) & (df_tv[date_col] <= ve)

        X_train = df_tv.loc[train_mask, feature_cols]
        y_train = df_tv.loc[train_mask, label_col].values.astype(int)
        X_val = df_tv.loc[val_mask, feature_cols]
        y_val = df_tv.loc[val_mask, label_col].values.astype(int)
        r_val = df_tv.loc[val_mask, return_col].values if return_col in df_tv.columns else None

        if len(X_train) < 100 or len(X_val) < 20:
            log.warning("Fold %d: insufficient data (train=%d, val=%d)", fold_idx, len(X_train), len(X_val))
            continue

        if y_train.sum() < 10 or (y_train == 0).sum() < 10:
            log.warning("Fold %d: insufficient class diversity", fold_idx)
            continue

        # Train
        pipe = make_model_pipeline(model_name, seed)
        pipe.fit(X_train, y_train)

        # Calibrate
        try:
            cal = _make_prefit_calibrator(pipe, method="isotonic")
            cal.fit(X_val[:max(len(X_val)//2, 10)], y_val[:max(len(y_val)//2, 10)])
            probs = cal.predict_proba(X_val)[:, 1]
        except Exception as exc:
            log.warning("Fold %d calibration failed: %s — using uncalibrated", fold_idx, exc)
            probs = pipe.predict_proba(X_val)[:, 1]

        fold_metrics = evaluate_predictions(
            y_val, probs, threshold=0.70,  # fixed for fold reporting
            returns=r_val
        )
        fold_metrics["fold"] = fold_idx
        fold_metrics["train_start"] = ts.date().isoformat()
        fold_metrics["train_end"] = te.date().isoformat()
        fold_metrics["val_start"] = vs.date().isoformat()
        fold_metrics["val_end"] = ve.date().isoformat()
        fold_results.append(fold_metrics)

        all_val_probs.extend(probs.tolist())
        all_val_true.extend(y_val.tolist())
        if r_val is not None:
            all_val_returns.extend(r_val.tolist())

    if not all_val_probs:
        return {"error": "No validation predictions generated"}

    all_val_probs = np.array(all_val_probs)
    all_val_true = np.array(all_val_true)
    all_val_returns = np.array(all_val_returns) if all_val_returns else None

    # Select threshold on all validation data
    best_threshold, val_metrics = select_threshold(
        all_val_true, all_val_probs,
        precision_target=precision_target,
        min_signals=min_signals,
        returns=all_val_returns,
    )

    return {
        "walk_forward_folds": fold_results,
        "validation_metrics": val_metrics,
        "selected_threshold": best_threshold,
        "total_val_samples": len(all_val_true),
        "total_val_positives": int(all_val_true.sum()),
    }


def train_final_model(
    df: pd.DataFrame,
    feature_cols: list[str],
    label_col: str = "weekly_bullish",
    date_col: str = "signal_date",
    return_col: str = "trade_return",
    train_end: str = "2024-12-31",  # train on all data up to val_end
    model_name: str = "hist_gradient_boosting",
    seed: int = 42,
) -> tuple:
    """
    Train final model on all train+val data (up to train_end).
    Returns (pipeline, calibrated_pipeline, feature_cols).
    This model is NOT evaluated — that's done on holdout separately.
    """
    df = df.copy()
    df[date_col] = pd.to_datetime(df[date_col])
    df = df.sort_values(date_col)

    df_train = df[df[date_col] <= pd.Timestamp(train_end)].copy()
    df_train = df_train.dropna(subset=[label_col])
    df_train = df_train[df_train[label_col].isin([0, 1])]

    if df_train.empty:
        raise ValueError("No training data")

    X_train = df_train[feature_cols]
    y_train = df_train[label_col].values.astype(int)

    log.info(
        "Final model training: %d rows, %.1f%% positive, model=%s",
        len(X_train), y_train.mean() * 100, model_name
    )

    pipe = make_model_pipeline(model_name, seed)
    pipe.fit(X_train, y_train)

    # Calibrate on most recent 20% of training data
    n_cal = max(len(X_train) // 5, 100)
    X_cal = X_train.iloc[-n_cal:]
    y_cal = y_train[-n_cal:]

    try:
        cal = _make_prefit_calibrator(pipe, method="isotonic")
        cal.fit(X_cal, y_cal)
        return pipe, cal, feature_cols
    except Exception as exc:
        log.warning("Calibration failed: %s — using uncalibrated", exc)
        return pipe, pipe, feature_cols


def evaluate_on_holdout(
    pipe,
    df: pd.DataFrame,
    feature_cols: list[str],
    label_col: str = "weekly_bullish",
    date_col: str = "signal_date",
    return_col: str = "trade_return",
    holdout_start: str = "2025-01-01",
    threshold: float = 0.85,
    buy_fee: float = 0.00155,
    sell_fee: float = 0.00255,
    slippage: float = 0.001,
) -> dict:
    """
    Evaluate model on UNTOUCHED holdout period.
    Should be called ONLY ONCE per model version.
    """
    df = df.copy()
    df[date_col] = pd.to_datetime(df[date_col])
    df_holdout = df[df[date_col] >= pd.Timestamp(holdout_start)].copy()
    df_holdout = df_holdout.dropna(subset=[label_col])
    df_holdout = df_holdout[df_holdout[label_col].isin([0, 1])]

    if df_holdout.empty:
        return {"error": "No holdout data"}
    if len(df_holdout) < 50:
        return {"error": f"Insufficient holdout data: {len(df_holdout)} rows"}

    X_holdout = df_holdout[feature_cols]
    y_holdout = df_holdout[label_col].values.astype(int)
    r_holdout = df_holdout[return_col].values if return_col in df_holdout.columns else None

    try:
        probs = pipe.predict_proba(X_holdout)[:, 1]
    except Exception as exc:
        return {"error": f"Prediction failed: {exc}"}

    metrics = evaluate_predictions(
        y_holdout, probs, threshold=threshold,
        buy_fee=buy_fee, sell_fee=sell_fee, slippage=slippage,
        returns=r_holdout,
    )
    metrics["holdout_start"] = holdout_start
    metrics["holdout_end"] = df_holdout[date_col].max().date().isoformat()
    metrics["total_holdout_rows"] = len(df_holdout)
    metrics["positive_rate"] = float(y_holdout.mean())

    # Precision by period (quarterly)
    df_holdout = df_holdout.copy()
    df_holdout["_prob"] = probs
    df_holdout["_signal"] = (probs >= threshold).astype(int)
    df_holdout["_quarter"] = df_holdout[date_col].dt.to_period("Q").astype(str)

    quarterly = []
    for q, grp in df_holdout.groupby("_quarter"):
        if grp["_signal"].sum() >= 5:
            q_prec = float((grp["_signal"] & grp[label_col]).sum() / grp["_signal"].sum())
        else:
            q_prec = np.nan
        quarterly.append({
            "quarter": q,
            "signals": int(grp["_signal"].sum()),
            "precision": q_prec,
        })
    metrics["quarterly_precision"] = quarterly

    # Determine status
    if metrics["signal_count"] < 30:
        metrics["holdout_status"] = "INSUFFICIENT_EVIDENCE"
    elif metrics["precision"] >= 0.90 and metrics["precision_ci_lower"] >= 0.85:
        metrics["holdout_status"] = "VERIFIED_ABOVE_90"
    elif metrics["precision"] >= 0.90:
        metrics["holdout_status"] = "PROVISIONAL_ABOVE_90"
    else:
        metrics["holdout_status"] = "NOT_VERIFIED"

    return metrics


def save_model(
    pipe,
    cal_pipe,
    model_run_id: int,
    model_version: str,
    models_dir: Path,
) -> dict:
    """Save model artifacts to disk."""
    models_dir.mkdir(exist_ok=True)
    model_path = models_dir / f"model_v{model_version}_{model_run_id}.pkl"
    cal_path = models_dir / f"calibrated_v{model_version}_{model_run_id}.pkl"

    joblib.dump(pipe, model_path)
    joblib.dump(cal_pipe, cal_path)

    return {
        "model_path": str(model_path),
        "calibrated_path": str(cal_path),
    }


def load_model(model_run_id: int, models_dir: Path, model_version: str = None):
    """Load calibrated model from disk."""
    if model_version:
        cal_path = models_dir / f"calibrated_v{model_version}_{model_run_id}.pkl"
    else:
        # Find by id
        candidates = list(models_dir.glob(f"calibrated_v*_{model_run_id}.pkl"))
        if not candidates:
            raise FileNotFoundError(f"No calibrated model found for run {model_run_id}")
        cal_path = candidates[0]

    return joblib.load(cal_path)
