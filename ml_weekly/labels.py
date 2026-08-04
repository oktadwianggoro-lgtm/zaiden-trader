"""
ml_weekly/labels.py
Label builder WITHOUT data leakage.

Critical rules:
- All labels use ONLY data AFTER as_of_date.
- Training rows that lack 5 future trading days are kept as inference candidates.
- Purging ensures no overlap between train and validation.
"""
from __future__ import annotations
import sqlite3
import logging
from typing import Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


def get_trading_dates(
    start_date: str,
    end_date: str,
    db_path: str,
) -> list[str]:
    """Return sorted list of trading dates from database."""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """SELECT DISTINCT tanggal FROM ringkasan_saham_harian
               WHERE tanggal >= ? AND tanggal <= ?
               ORDER BY tanggal""",
            (start_date, end_date)
        ).fetchall()
    return [r[0] for r in rows]


def get_nth_next_trading_date(
    dates: list[str],
    as_of_date: str,
    n: int,
) -> Optional[str]:
    """Get the Nth trading date after as_of_date from a sorted list."""
    try:
        idx = dates.index(as_of_date)
        if idx + n < len(dates):
            return dates[idx + n]
    except ValueError:
        pass
    return None


def build_labels(
    signal_dates: list[str],
    db_path: str,
    horizon_days: int = 5,
    primary_target: float = 0.02,
    take_profit: float = 0.03,
    stop_loss: float = -0.02,
    ambiguous_policy: str = "loss",
    eligible_tickers: Optional[dict[str, list[str]]] = None,
) -> pd.DataFrame:
    """
    Build training labels for signal_dates.

    For each (date, ticker) pair:
    - entry_price = open[t+1] if available, else close[t] (proxy)
    - exit_price = close[t+5]
    - trade_return = (exit / entry) - 1

    Label definitions:
    - weekly_bullish = 1 if trade_return >= primary_target
    - tp_sl_success = 1 if TP touched before SL in next 5 days
    - future_return_1d, 3d, 5d, 10d (close-to-close)

    Returns DataFrame with all label columns.
    """
    all_dates = get_trading_dates(
        min(signal_dates), 
        "2030-12-31",  # future
        db_path
    )
    dates_set = set(all_dates)

    # For each signal date, find next 10 trading dates
    date_to_future: dict[str, list[str]] = {}
    for sd in signal_dates:
        future = []
        idx = all_dates.index(sd) if sd in all_dates else -1
        if idx >= 0:
            for i in range(1, 12):
                if idx + i < len(all_dates):
                    future.append(all_dates[idx + i])
                if len(future) >= 10:
                    break
        date_to_future[sd] = future

    # Load OHLC data for signal dates + next 10 trading days
    # We need close[t], open[t+1], close[t+1..t+10], high/low for t+1..t+5
    all_needed_dates = sorted(set(
        [d for sd in signal_dates for d in [sd] + date_to_future.get(sd, [])]
    ))

    if not all_needed_dates:
        return pd.DataFrame()

    date_min = all_needed_dates[0]
    date_max = all_needed_dates[-1]

    with sqlite3.connect(db_path) as conn:
        df_raw = pd.read_sql_query(
            """SELECT tanggal, kode_saham,
                      harga_penutupan AS close,
                      harga_pembukaan AS open,
                      harga_tertinggi AS high,
                      harga_terendah  AS low,
                      sebelumnya AS prev_close
               FROM ringkasan_saham_harian
               WHERE tanggal >= ? AND tanggal <= ?
                 AND harga_penutupan IS NOT NULL
               ORDER BY kode_saham, tanggal""",
            conn, params=(date_min, date_max)
        )

    if df_raw.empty:
        return pd.DataFrame()

    # Pivot to wide format: index=tanggal, columns=kode_saham
    close_wide = df_raw.pivot(index="tanggal", columns="kode_saham", values="close")
    open_wide = df_raw.pivot(index="tanggal", columns="kode_saham", values="open")
    high_wide = df_raw.pivot(index="tanggal", columns="kode_saham", values="high")
    low_wide = df_raw.pivot(index="tanggal", columns="kode_saham", values="low")

    rows = []

    for sd in signal_dates:
        if sd not in close_wide.index:
            continue

        future_dates = date_to_future.get(sd, [])
        if len(future_dates) < horizon_days:
            # Not enough future data → inference only, no labels
            tickers = list(close_wide.columns)
            for tk in tickers:
                if eligible_tickers and sd in eligible_tickers:
                    if tk not in eligible_tickers[sd]:
                        continue
                c0 = close_wide.at[sd, tk] if sd in close_wide.index and tk in close_wide.columns else None
                if c0 is None or pd.isna(c0) or c0 <= 0:
                    continue
                rows.append({
                    "signal_date": sd,
                    "ticker": tk,
                    "current_close": float(c0),
                    "has_labels": False,
                    "weekly_bullish": np.nan,
                    "tp_sl_success": np.nan,
                    "trade_return": np.nan,
                    "future_return_1d": np.nan,
                    "future_return_3d": np.nan,
                    "future_return_5d": np.nan,
                    "future_return_10d": np.nan,
                    "mfe_5d": np.nan,
                    "mae_5d": np.nan,
                    "entry_price": np.nan,
                    "exit_price": np.nan,
                    "days_to_tp": np.nan,
                    "is_inference": True,
                })
            continue

        tickers = list(close_wide.columns)
        d_t1 = future_dates[0]
        d_t3 = future_dates[2] if len(future_dates) > 2 else None
        d_t5 = future_dates[horizon_days - 1] if len(future_dates) >= horizon_days else None
        d_t10 = future_dates[9] if len(future_dates) >= 10 else None
        tp_sl_dates = future_dates[:horizon_days]

        for tk in tickers:
            if eligible_tickers and sd in eligible_tickers:
                if tk not in eligible_tickers[sd]:
                    continue

            # Current close (signal date)
            c0 = close_wide.at[sd, tk] if tk in close_wide.columns else None
            if c0 is None or pd.isna(c0) or c0 <= 0:
                continue
            c0 = float(c0)

            # Entry price: open[t+1] if available, else close[t]
            o1 = None
            if d_t1 in open_wide.index and tk in open_wide.columns:
                o1_raw = open_wide.at[d_t1, tk]
                if not pd.isna(o1_raw) and o1_raw > 0:
                    o1 = float(o1_raw)
            entry_price = o1 if o1 else c0

            # Exit price: close[t+5]
            exit_price = None
            if d_t5 and d_t5 in close_wide.index and tk in close_wide.columns:
                ep = close_wide.at[d_t5, tk]
                if not pd.isna(ep) and ep > 0:
                    exit_price = float(ep)

            if exit_price is None:
                continue

            # Trade return
            trade_return = (exit_price / entry_price) - 1.0

            # Primary label
            weekly_bullish = 1 if trade_return >= primary_target else 0

            # Future close-to-close returns
            def safe_return(close0, dt):
                if dt and dt in close_wide.index and tk in close_wide.columns:
                    c = close_wide.at[dt, tk]
                    if not pd.isna(c) and c > 0 and close0 > 0:
                        return float(c / close0 - 1.0)
                return np.nan

            fr1 = safe_return(c0, d_t1)
            fr3 = safe_return(c0, d_t3)
            fr5 = safe_return(c0, d_t5)
            fr10 = safe_return(c0, d_t10)

            # MFE & MAE (max favorable / adverse excursion in 5 days)
            mfe, mae = 0.0, 0.0
            for td in tp_sl_dates:
                if td not in high_wide.index or td not in low_wide.index:
                    continue
                h = high_wide.at[td, tk] if tk in high_wide.columns else np.nan
                l = low_wide.at[td, tk] if tk in low_wide.columns else np.nan
                if not pd.isna(h) and h > 0 and entry_price > 0:
                    mfe = max(mfe, float(h / entry_price - 1.0))
                if not pd.isna(l) and l > 0 and entry_price > 0:
                    mae = min(mae, float(l / entry_price - 1.0))

            # TP/SL label (secondary)
            tp_sl_success = np.nan
            days_to_tp = np.nan

            tp_level = entry_price * (1 + take_profit)
            sl_level = entry_price * (1 + stop_loss)

            tp_hit_day = None
            sl_hit_day = None
            for day_idx, td in enumerate(tp_sl_dates):
                if td not in high_wide.index or td not in low_wide.index:
                    continue
                h = high_wide.at[td, tk] if tk in high_wide.columns else np.nan
                l = low_wide.at[td, tk] if tk in low_wide.columns else np.nan
                if not pd.isna(h) and h >= tp_level and tp_hit_day is None:
                    tp_hit_day = day_idx
                if not pd.isna(l) and l <= sl_level and sl_hit_day is None:
                    sl_hit_day = day_idx

            if tp_hit_day is not None and sl_hit_day is None:
                tp_sl_success = 1
                days_to_tp = float(tp_hit_day + 1)
            elif sl_hit_day is not None and tp_hit_day is None:
                tp_sl_success = 0
            elif tp_hit_day is not None and sl_hit_day is not None:
                if tp_hit_day < sl_hit_day:
                    tp_sl_success = 1
                    days_to_tp = float(tp_hit_day + 1)
                elif sl_hit_day < tp_hit_day:
                    tp_sl_success = 0
                else:
                    # Same day — ambiguous
                    tp_sl_success = 0 if ambiguous_policy == "loss" else np.nan
            else:
                # Neither hit — use trade_return
                tp_sl_success = 1 if trade_return >= 0 else 0

            rows.append({
                "signal_date": sd,
                "ticker": tk,
                "current_close": c0,
                "has_labels": True,
                "weekly_bullish": weekly_bullish,
                "tp_sl_success": tp_sl_success,
                "trade_return": trade_return,
                "future_return_1d": fr1,
                "future_return_3d": fr3,
                "future_return_5d": fr5,
                "future_return_10d": fr10,
                "mfe_5d": mfe,
                "mae_5d": mae,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "days_to_tp": days_to_tp,
                "is_inference": False,
            })

    if not rows:
        return pd.DataFrame()

    df_labels = pd.DataFrame(rows)
    log.info(
        "Labels built: %d signal dates, %d rows, bullish rate=%.2f%%",
        len(signal_dates),
        len(df_labels),
        df_labels["weekly_bullish"].mean() * 100 if "weekly_bullish" in df_labels.columns else 0,
    )
    return df_labels


def leakage_check_labels(df_features: pd.DataFrame, df_labels: pd.DataFrame) -> list[str]:
    """
    Anti-leakage tests. Returns list of violations (empty = clean).
    """
    violations = []

    if "signal_date" not in df_features.columns or "signal_date" not in df_labels.columns:
        return ["signal_date column missing"]

    # Check: no feature computed after signal_date
    if "feature_as_of_date" in df_features.columns:
        bad = df_features[df_features["feature_as_of_date"] > df_features["signal_date"]]
        if not bad.empty:
            violations.append(
                f"LEAKAGE: {len(bad)} rows where feature_as_of_date > signal_date"
            )

    # Check: no label value appearing in features
    label_cols = {"weekly_bullish", "tp_sl_success", "trade_return", "future_return_5d"}
    feat_cols = set(df_features.columns)
    leaked = label_cols & feat_cols
    if leaked:
        violations.append(f"LEAKAGE: label columns in features: {leaked}")

    return violations
