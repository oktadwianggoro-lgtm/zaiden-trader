"""
ml_weekly/pipeline.py
Main orchestration pipeline: dataset building, training, evaluation.
OPTIMIZED: bulk data load instead of per-date SQL queries.
"""
from __future__ import annotations
import json
import logging
import os
import sqlite3
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# ── Global cancel flag ────────────────────────────────────────────────────────
_CANCEL_REQUESTED = False

def request_cancel():
    global _CANCEL_REQUESTED
    _CANCEL_REQUESTED = True

def reset_cancel():
    global _CANCEL_REQUESTED
    _CANCEL_REQUESTED = False

def is_cancelled():
    return _CANCEL_REQUESTED


# ── Feature-building worker pool ──────────────────────────────────────────────
# Each worker process gets its own copy of df_all ONCE at pool startup (via
# the initializer below), not re-sent on every task — only the small
# (signal_date, tickers, lookback_days) args are pickled per task.
_WORKER_DF_ALL: Optional[pd.DataFrame] = None


def _init_feature_worker(df_all: pd.DataFrame) -> None:
    global _WORKER_DF_ALL
    _WORKER_DF_ALL = df_all


def _feature_worker_task(signal_date: str, eligible_tickers: list[str], lookback_days: int) -> pd.DataFrame:
    return _vectorized_features(_WORKER_DF_ALL, signal_date, eligible_tickers, lookback_days)


def _bulk_load_ohlcv(db_path: str, train_start: str) -> pd.DataFrame:
    """Load ALL OHLCV data in one SQL query. Much faster than per-date queries."""
    log.info("Bulk loading OHLCV data from %s...", train_start)
    with sqlite3.connect(db_path) as conn:
        df = pd.read_sql("""
            SELECT
                tanggal              AS date,
                kode_saham           AS ticker,
                harga_pembukaan      AS open,
                harga_tertinggi      AS high,
                harga_terendah       AS low,
                harga_penutupan      AS close,
                volume,
                nilai_transaksi      AS value,
                frekuensi            AS freq,
                beli_asing           AS foreign_buy,
                jual_asing           AS foreign_sell
            FROM ringkasan_saham_harian
            WHERE tanggal >= ?
              AND harga_penutupan > 0
              AND volume > 0
            ORDER BY kode_saham, tanggal
        """, conn, params=(train_start,))

    df['date'] = pd.to_datetime(df['date'])
    log.info("Bulk load complete: %d rows, %d stocks, %d dates",
             len(df), df['ticker'].nunique(), df['date'].nunique())
    return df


def validate_data_quality(df_all: pd.DataFrame) -> None:
    """Validate that the bulk loaded data meets quality gates for training."""
    if df_all.empty:
        raise ValueError("Data kosong. Periksa koneksi database.")
    
    max_date = df_all['date'].max()
    days_stale = (pd.Timestamp.today().normalize() - max_date).days
    if days_stale > 14:
        raise ValueError(f"Data terlalu usang (stale). Data terakhir: {max_date.date()} ({days_stale} hari lalu). Lakukan auto-sync sebelum training.")
        
    null_open_pct = (df_all['open'].isna() | (df_all['open'] == 0)).mean()
    if null_open_pct > 0.95:
        log.warning("Peringatan: >95%% data open kosong. Model akan beradaptasi dengan proxy entry_price.")
        
    zero_vol_pct = (df_all['volume'] == 0).mean()
    if zero_vol_pct > 0.5:
        raise ValueError(f"Kualitas data buruk (Quality Gate Blocked): >50% data volume 0 ({zero_vol_pct:.1%}).")


def _vectorized_features(df_all: pd.DataFrame, signal_date: str,
                         eligible_tickers: list[str],
                         lookback_days: int = 260) -> pd.DataFrame:
    """
    Compute features for all eligible tickers at signal_date using
    pre-loaded bulk data. Returns one row per ticker.

    Delegates the actual per-ticker computation to
    features.build_features_for_stock — the SAME function live prediction
    uses (see predict.py -> features.build_all_features). This function used
    to have its own separate, much simpler set of ~43 feature formulas that
    only shared ~9 column names with features.py's ~130-column live output.
    Live signals were therefore missing ~80% of the columns the model was
    actually trained on (silently filled with NaN by predict.py), which is
    the root cause behind spurious DATA_INVALID / low-confidence gating even
    when price history goes back to 2020 — the model's feature_coverage was
    genuinely ~20%, gating was doing exactly what it was supposed to do with
    a badly broken train/serve feature pipeline. Calling the same function
    here guarantees the two paths can never drift apart like that again.
    """
    from .features import build_features_for_stock

    sig_dt = pd.Timestamp(signal_date)

    # Slice: only past data up to and including signal_date
    hist = df_all[
        (df_all['date'] <= sig_dt) &
        (df_all['ticker'].isin(eligible_tickers))
    ]

    if hist.empty:
        return pd.DataFrame()

    rows = []
    for ticker, grp in hist.groupby('ticker', sort=False):
        grp = grp.sort_values('date').tail(lookback_days)
        if len(grp) < 20:
            continue
        feats = build_features_for_stock(grp, signal_date)
        if not feats:
            continue
        feats['signal_date'] = signal_date
        feats['ticker'] = ticker
        feats['close'] = float(grp['close'].iloc[-1])
        rows.append(feats)

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def build_dataset(
    db_path: str,
    train_start: str = "2022-01-01",
    holdout_start: str = "2025-01-01",
    horizon_days: int = 5,
    primary_target: float = 0.02,
    take_profit: float = 0.03,
    stop_loss: float = -0.02,
    min_history_days: int = 120,
    min_median_value_20d: float = 2e8,
    step_days: int = 1,
    max_rows: int = 2_000_000,
    lookback_days: int = 260,
    n_workers: Optional[int] = None,
    progress_cb=None,
) -> pd.DataFrame:
    """
    Build labeled dataset using BULK SQL load + vectorized feature computation.
    Much faster than per-date SQL queries.
    """
    from .universe import get_eligible_tickers
    from .labels import build_labels, get_trading_dates
    from .market_features import compute_market_features

    reset_cancel()
    log.info("Building dataset from %s (OPTIMIZED bulk load)...", train_start)

    if progress_cb: progress_cb(5, "Memuat data OHLCV bulk dari database...")

    # Step 1: Bulk load ALL OHLCV data once
    df_all = _bulk_load_ohlcv(db_path, train_start)
    
    # Step 1.5: Data Quality Gate
    validate_data_quality(df_all)

    all_dates_in_db = sorted(df_all['date'].dt.strftime('%Y-%m-%d').unique().tolist())
    if not all_dates_in_db:
        raise ValueError("Tidak ada data tersedia sejak " + train_start)

    # Filter dates with enough lookback
    available_dates = all_dates_in_db[lookback_days:]
    if step_days > 1:
        available_dates = available_dates[::step_days]

    log.info("Signal dates: %d (step=%d)", len(available_dates), step_days)
    if progress_cb: progress_cb(10, f"Tanggal sinyal: {len(available_dates)}, menghitung fitur...")

    # Step 2: Get eligible tickers ONCE (use latest date)
    latest_date = available_dates[-1]
    try:
        base_tickers = get_eligible_tickers(
            latest_date, db_path,
            min_history_days=min_history_days,
            min_median_value_20d=min_median_value_20d,
        )
    except Exception:
        # Fallback: use all tickers that appear in bulk data
        val_counts = df_all.groupby('ticker')['value'].median()
        base_tickers = val_counts[val_counts >= min_median_value_20d].index.tolist()

    log.info("Eligible tickers: %d", len(base_tickers))
    if progress_cb: progress_cb(15, f"Saham eligible: {len(base_tickers)}, membangun fitur per tanggal...")

    # Step 3: Feature computation per date, parallelized across processes.
    # features.build_features_for_stock (used by _vectorized_features since
    # the train/serve feature-parity fix) computes ~130 columns per ticker
    # via pandas rolling/ewm ops — roughly 30-80x heavier per date than the
    # old hand-rolled numpy version. At one signal date per trading day
    # across a 5-year history that's hours of work single-threaded, so this
    # farms dates out across worker processes (each date's tickers are
    # independent — no shared state needed beyond the read-only df_all,
    # which is sent to each worker once at pool startup, not per task).
    all_rows = []
    total = len(available_dates)
    workers = max(1, min(n_workers or (os.cpu_count() or 4) - 2, 8))
    batch_size = max(workers * 4, workers)

    log.info("Building features with %d worker process(es)...", workers)

    with ProcessPoolExecutor(
        max_workers=workers, initializer=_init_feature_worker, initargs=(df_all,)
    ) as executor:
        i = 0
        while i < total:
            if is_cancelled():
                log.info("Training cancelled at date index %d", i)
                break

            batch = available_dates[i: i + batch_size]
            futures = {
                executor.submit(_feature_worker_task, d, base_tickers, lookback_days): d
                for d in batch
            }
            for future in as_completed(futures):
                signal_date = futures[future]
                try:
                    feat_df = future.result()
                    if not feat_df.empty:
                        all_rows.append(feat_df)
                except Exception as exc:
                    log.warning("Error on %s: %s", signal_date, exc)

            i += len(batch)
            pct = 15 + int((i / total) * 55)  # 15% -> 70%
            row_count = sum(len(r) for r in all_rows)
            log.info("Features: %d/%d dates, %d rows so far", i, total, row_count)
            if progress_cb:
                progress_cb(pct, f"Fitur: {i}/{total} tanggal, {row_count:,} baris...")

            if row_count >= max_rows:
                log.info("Reached max_rows=%d, stopping", max_rows)
                break

    if not all_rows:
        raise ValueError("Tidak ada fitur yang berhasil dibangun.")

    feat_all = pd.concat(all_rows, ignore_index=True)
    log.info("Features built: %d rows", len(feat_all))
    if progress_cb: progress_cb(72, f"Fitur selesai: {len(feat_all):,} baris. Menghitung label (vectorized)...")

    # Step 4: VECTORIZED label computation — group by ticker, then use shift/rolling
    # Pre-index df_all by ticker for fast lookup
    log.info("Building labels (vectorized)...")

    # Create a shifted future close for each ticker using groupby shift
    df_sorted = df_all.sort_values(['ticker', 'date']).copy()
    df_sorted['close_fwd5'] = df_sorted.groupby('ticker')['close'].shift(-horizon_days)

    # For high/low: compute rolling max/min over NEXT horizon_days
    # Trick: reverse the series, apply rolling, reverse back
    def rolling_forward_max(series, window):
        return series[::-1].rolling(window, min_periods=1).max()[::-1].shift(-(window-1))

    def rolling_forward_min(series, window):
        return series[::-1].rolling(window, min_periods=1).min()[::-1].shift(-(window-1))

    df_sorted['high_fwd5'] = df_sorted.groupby('ticker')['high'].transform(
        lambda s: rolling_forward_max(s, horizon_days)
    )
    df_sorted['low_fwd5'] = df_sorted.groupby('ticker')['low'].transform(
        lambda s: rolling_forward_min(s, horizon_days)
    )

    # date → str for merging
    df_sorted['date_str'] = df_sorted['date'].dt.strftime('%Y-%m-%d')

    # Build lookup: (ticker, signal_date) → (close, close_fwd5, high_fwd5, low_fwd5)
    lookup = df_sorted[['ticker', 'date_str', 'close', 'close_fwd5', 'high_fwd5', 'low_fwd5']].rename(
        columns={'date_str': 'signal_date', 'close': 'entry_price'}
    )

    # Merge features with labels
    merged = feat_all[['signal_date', 'ticker']].merge(lookup, on=['signal_date', 'ticker'], how='left')

    # Compute outcomes vectorized
    merged['trade_return'] = (merged['close_fwd5'] - merged['entry_price']) / merged['entry_price'].replace(0, np.nan)
    merged['tp_hit'] = (merged['high_fwd5'] - merged['entry_price']) / merged['entry_price'].replace(0, np.nan) >= take_profit
    merged['sl_hit'] = (merged['low_fwd5']  - merged['entry_price']) / merged['entry_price'].replace(0, np.nan) <= stop_loss

    # Label logic: TP > SL win, SL alone lose, else return-based
    merged['weekly_bullish'] = np.where(
        merged['tp_hit'] & merged['sl_hit'],
        np.where(merged['trade_return'] > 0, 1, 0),
        np.where(merged['tp_hit'], 1,
        np.where(merged['sl_hit'], 0,
        np.where(merged['trade_return'] >= primary_target, 1, 0)))
    )
    merged['has_labels'] = np.where(merged['close_fwd5'].notna(), 1, 0)
    merged['is_inference'] = 0

    labels_df = merged[merged['has_labels'] == 1][
        ['signal_date', 'ticker', 'entry_price', 'close_fwd5',
         'trade_return', 'weekly_bullish', 'has_labels', 'is_inference']
    ].rename(columns={'close_fwd5': 'exit_price'})

    if labels_df.empty:
        raise ValueError("Tidak ada label yang berhasil dibangun.")

    log.info("Labels built: %d rows, %.1f%% positive",
             len(labels_df), labels_df['weekly_bullish'].mean() * 100)

    if progress_cb: progress_cb(80, f"Label selesai: {len(labels_df):,} baris. Menggabungkan dataset...")



    # Merge
    df = feat_all.merge(
        labels_df[['signal_date', 'ticker', 'entry_price', 'exit_price',
                   'trade_return', 'weekly_bullish', 'has_labels', 'is_inference']],
        on=['signal_date', 'ticker'], how='inner',
    )

    log.info("Dataset merged: %d rows, %.1f%% positive",
             len(df), df['weekly_bullish'].mean() * 100)

    if progress_cb: progress_cb(85, f"Dataset siap: {len(df):,} baris")
    return df


def run_full_pipeline(
    db_path: str,
    models_dir: Path,
    config=None,
    model_name: str = "hist_gradient_boosting",
    force_retrain: bool = False,
    progress_cb=None,
    prebuilt_dataset: Optional[pd.DataFrame] = None,
) -> dict:
    """
    Full pipeline: build dataset → train → validate → evaluate holdout.
    Optimized with bulk data loading.

    prebuilt_dataset: if given, skips the (expensive — see build_dataset's
    docstring) dataset-building step and trains directly on this DataFrame.
    Every ensemble member uses the identical feature/label rows (only the
    classifier algorithm differs between them), so train-all builds this
    once via build_dataset() and passes it to all 6 calls instead of
    rebuilding — a straight 6x cut in wall-clock for the "one button"
    pipeline, independent of how many signal dates it covers.
    """
    from .config import get_config, MODELS_DIR
    from .db_migration import run_migration, log_job_start, log_job_end
    from .train import (
        walk_forward_train, train_final_model, evaluate_on_holdout,
        save_model, wilson_ci,
    )
    from .predict import get_source_max_date

    if config is None:
        config = get_config()
    if models_dir is None:
        models_dir = MODELS_DIR

    # Ensure tables exist
    migration_result = run_migration(db_path)
    if migration_result["status"] != "success":
        return {"error": f"Migration failed: {migration_result['errors']}"}

    job_id = log_job_start("full_pipeline", db_path, {
        "model_name": model_name,
        "force_retrain": force_retrain,
    })

    def _cb(pct, msg):
        log.info("[%d%%] %s", pct, msg)
        if progress_cb:
            progress_cb(pct, msg)

    try:
        source_max_date = get_source_max_date(db_path)
        log.info("Source max date: %s", source_max_date)

        if prebuilt_dataset is not None:
            _cb(85, f"Memakai dataset yang sudah dibangun ({len(prebuilt_dataset):,} baris)...")
            df = prebuilt_dataset
        else:
            _cb(5, "Memulai build dataset...")
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
                progress_cb=_cb,
            )

        if is_cancelled():
            log_job_end(job_id, "cancelled", error="User cancelled")
            return {"status": "cancelled"}

        # Feature columns
        exclude_cols = {
            "signal_date", "ticker", "n_days", "close",
            "weekly_bullish", "trade_return", "entry_price", "exit_price",
            "is_inference", "has_labels",
        }
        feature_cols = [c for c in df.columns if c not in exclude_cols and
                        pd.api.types.is_numeric_dtype(df[c])]

        log.info("Feature columns: %d", len(feature_cols))
        _cb(86, f"Walk-forward validation ({len(feature_cols)} fitur)...")

        # Walk-forward validation
        wf_result = walk_forward_train(
            df=df,
            feature_cols=feature_cols,
            train_start=config.training_start,
            train_end=config.training_end,
            val_start=config.validation_start,
            val_end=config.validation_end,
            model_name=model_name,
            seed=config.random_seed,
            precision_target=config.precision_target,
            min_signals=config.min_validation_signals,
        )

        if "error" in wf_result:
            log_job_end(job_id, "failed", error=wf_result["error"])
            return wf_result

        threshold = wf_result["selected_threshold"]
        val_metrics = wf_result["validation_metrics"]
        _cb(92, "Training model final...")

        log.info("Validation: threshold=%.2f, precision=%.2f%%, signals=%d",
                 threshold, val_metrics.get("precision", 0) * 100, val_metrics.get("signal_count", 0))

        # Train final model
        pipe, cal_pipe, feature_cols = train_final_model(
            df=df,
            feature_cols=feature_cols,
            train_end=config.validation_end,
            model_name=model_name,
            seed=config.random_seed,
        )

        # Register model run
        model_version = datetime.now().strftime("%Y%m%d%H%M")
        val_status = "PROVISIONAL" if val_metrics.get("signal_count", 0) < config.min_validation_signals else \
                     ("VERIFIED" if val_metrics.get("precision", 0) >= config.precision_target else "NOT_VERIFIED")

        with sqlite3.connect(db_path) as conn:
            cur = conn.execute(
                """INSERT INTO ml_weekly_model_runs (
                    model_version, model_name, feature_set, target_definition,
                    training_start, training_end, validation_start, validation_end,
                    holdout_start, holdout_end, probability_threshold,
                    feature_list_json, source_max_date, status, validation_status,
                    notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    model_version, model_name, "price_volume_foreign",
                    f"close5_{int(config.primary_return_target*100)}pct",
                    config.training_start, config.training_end,
                    config.validation_start, config.validation_end,
                    config.holdout_start, source_max_date,
                    threshold, json.dumps(feature_cols), source_max_date,
                    "trained", val_status,
                    f"Walk-forward: {len(wf_result.get('walk_forward_folds', []))} folds",
                )
            )
            model_run_id = cur.lastrowid

            conn.execute(
                """INSERT INTO ml_weekly_backtest_metrics (
                    model_run_id, evaluation_split, period_start, period_end,
                    threshold, signal_count, correct_count, false_positive_count,
                    precision, precision_ci_lower, precision_ci_upper, coverage,
                    recall, pr_auc, brier_score, metrics_json
                ) VALUES (?, 'validation', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    model_run_id,
                    config.validation_start, config.validation_end,
                    threshold,
                    val_metrics.get("signal_count", 0),
                    val_metrics.get("correct_count", 0),
                    val_metrics.get("false_positive_count", 0),
                    val_metrics.get("precision", 0),
                    val_metrics.get("precision_ci_lower", 0),
                    val_metrics.get("precision_ci_upper", 1),
                    val_metrics.get("coverage", 0),
                    val_metrics.get("recall", 0),
                    val_metrics.get("pr_auc"),
                    val_metrics.get("brier_score"),
                    json.dumps(val_metrics),
                )
            )
            conn.commit()

        _cb(94, "Menyimpan model ke disk...")
        artifact_paths = save_model(cal_pipe, cal_pipe, model_run_id, model_version, models_dir)

        _cb(96, "Evaluasi holdout...")
        holdout_metrics = evaluate_on_holdout(
            pipe=cal_pipe, df=df, feature_cols=feature_cols,
            holdout_start=config.holdout_start, threshold=threshold,
            buy_fee=config.buy_fee, sell_fee=config.sell_fee, slippage=config.slippage,
        )

        holdout_status = holdout_metrics.get("holdout_status", "NOT_EVALUATED")

        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """INSERT INTO ml_weekly_backtest_metrics (
                    model_run_id, evaluation_split, period_start, period_end,
                    threshold, signal_count, correct_count, false_positive_count,
                    precision, precision_ci_lower, precision_ci_upper, coverage,
                    recall, pr_auc, brier_score, metrics_json
                ) VALUES (?, 'holdout', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    model_run_id,
                    config.holdout_start, holdout_metrics.get("holdout_end", ""),
                    threshold,
                    holdout_metrics.get("signal_count", 0),
                    holdout_metrics.get("correct_count", 0),
                    holdout_metrics.get("false_positive_count", 0),
                    holdout_metrics.get("precision", 0),
                    holdout_metrics.get("precision_ci_lower", 0),
                    holdout_metrics.get("precision_ci_upper", 1),
                    holdout_metrics.get("coverage", 0),
                    holdout_metrics.get("recall", 0),
                    holdout_metrics.get("pr_auc"),
                    holdout_metrics.get("brier_score"),
                    json.dumps(holdout_metrics),
                )
            )
            conn.execute(
                """UPDATE ml_weekly_model_runs
                   SET status='active', holdout_status=?, completed_at=?,
                       probability_threshold=?
                   WHERE id=?""",
                (holdout_status, datetime.now().isoformat(), threshold, model_run_id)
            )
            conn.commit()

        log_job_end(job_id, "success", records=len(df), source_max_date=source_max_date)
        _cb(100, "Training selesai!")

        return {
            "status": "success",
            "model_run_id": model_run_id,
            "model_version": model_version,
            "model_name": model_name,
            "threshold": threshold,
            "feature_count": len(feature_cols),
            "dataset_rows": len(df),
            "validation_status": val_status,
            "holdout_status": holdout_status,
            "validation_precision": round(val_metrics.get("precision", 0), 4),
            "holdout_precision": round(holdout_metrics.get("precision", 0), 4),
            "artifact_paths": artifact_paths,
            "source_max_date": source_max_date,
        }

    except Exception as exc:
        log.exception("Pipeline failed: %s", exc)
        log_job_end(job_id, "failed", error=str(exc))
        return {"error": str(exc), "status": "failed"}
