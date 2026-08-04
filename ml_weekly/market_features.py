"""
ml_weekly/market_features.py
Market and sector regime features, computed cross-sectionally.
All cross-sectional features are normalized per trading date.
"""
from __future__ import annotations
import sqlite3
import logging
from typing import Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# Market regime classification thresholds
REGIME_TREND_THRESHOLD = 0.55   # % stocks above SMA20 for "trending"
REGIME_BREADTH_THRESHOLD = 0.50  # % stocks up for "bullish"
REGIME_VOL_HIGH = 0.60          # vol percentile for "high vol"


def compute_market_features(
    as_of_date: str,
    db_path: str,
    lookback_days: int = 120,
) -> dict:
    """
    Compute market-level features as of a given date.
    Returns dict of feature_name -> scalar value.
    """
    from datetime import datetime, timedelta
    dt = datetime.strptime(as_of_date, "%Y-%m-%d")
    start = (dt - timedelta(days=lookback_days + 50)).strftime("%Y-%m-%d")

    with sqlite3.connect(db_path) as conn:
        df = pd.read_sql_query(
            """SELECT tanggal, kode_saham, harga_penutupan AS close,
                      COALESCE(nilai_transaksi, 0) AS value,
                      COALESCE(volume, 0) AS volume
               FROM ringkasan_saham_harian
               WHERE tanggal >= ? AND tanggal <= ?
                 AND harga_penutupan IS NOT NULL AND harga_penutupan > 0
                 AND volume > 0
               ORDER BY tanggal, kode_saham""",
            conn,
            params=(start, as_of_date),
        )

    if df.empty:
        return _empty_market_features()

    # Pivot to wide
    close_wide = df.pivot(index="tanggal", columns="kode_saham", values="close").sort_index()
    value_wide = df.pivot(index="tanggal", columns="kode_saham", values="value").sort_index()

    trading_dates = sorted(close_wide.index.tolist())
    if len(trading_dates) < 5:
        return _empty_market_features()

    feats = {}

    # Market equal-weight return (last N days)
    daily_returns = close_wide.pct_change()

    for lag in [1, 5, 20, 60]:
        if len(trading_dates) > lag:
            market_ret = float(daily_returns.iloc[-lag:].mean().mean())
            feats[f"market_return_{lag}d"] = market_ret
        else:
            feats[f"market_return_{lag}d"] = np.nan

    # Market breadth (% stocks up today)
    today_ret = daily_returns.iloc[-1].dropna()
    feats["market_breadth_1d"] = float((today_ret > 0).mean()) if len(today_ret) > 0 else np.nan

    # % stocks above SMA20
    sma20 = close_wide.rolling(20).mean()
    if not sma20.empty and len(close_wide) >= 20:
        above_sma20 = (close_wide.iloc[-1] > sma20.iloc[-1]).dropna()
        feats["pct_above_sma20"] = float(above_sma20.mean()) if len(above_sma20) > 0 else np.nan
    else:
        feats["pct_above_sma20"] = np.nan

    # % stocks above SMA50
    sma50 = close_wide.rolling(50).mean()
    if not sma50.empty and len(close_wide) >= 50:
        above_sma50 = (close_wide.iloc[-1] > sma50.iloc[-1]).dropna()
        feats["pct_above_sma50"] = float(above_sma50.mean()) if len(above_sma50) > 0 else np.nan
    else:
        feats["pct_above_sma50"] = np.nan

    # % stocks up in last 5 days
    if len(trading_dates) >= 6:
        ret5 = (close_wide.iloc[-1] / close_wide.iloc[-6] - 1.0).dropna()
        feats["pct_stocks_up_5d"] = float((ret5 > 0).mean())
    else:
        feats["pct_stocks_up_5d"] = np.nan

    # New 20-day highs and lows
    if len(close_wide) >= 20:
        high20 = close_wide.rolling(20).max()
        low20 = close_wide.rolling(20).min()
        new_highs = (close_wide.iloc[-1] >= high20.iloc[-1]).dropna()
        new_lows = (close_wide.iloc[-1] <= low20.iloc[-1]).dropna()
        feats["new_highs_20d"] = float(new_highs.mean())
        feats["new_lows_20d"] = float(new_lows.mean())
        feats["high_low_ratio"] = float(new_highs.sum() / (new_lows.sum() + 1))
    else:
        feats["new_highs_20d"] = np.nan
        feats["new_lows_20d"] = np.nan
        feats["high_low_ratio"] = np.nan

    # Cross-sectional dispersion (std of daily returns)
    feats["market_dispersion"] = float(today_ret.std()) if len(today_ret) > 5 else np.nan

    # Market volatility (std of equal-weight index)
    ew_index = daily_returns.mean(axis=1)
    if len(ew_index.dropna()) >= 20:
        feats["market_hvol20"] = float(ew_index.rolling(20).std().iloc[-1] * np.sqrt(252))
    else:
        feats["market_hvol20"] = np.nan

    # Advance-Decline ratio
    feats["advance_decline_ratio"] = float(
        (today_ret > 0).sum() / ((today_ret < 0).sum() + 1)
    ) if len(today_ret) > 0 else np.nan

    # Median return
    feats["market_median_return"] = float(today_ret.median()) if len(today_ret) > 0 else np.nan

    # Market volume
    if not value_wide.empty:
        feats["market_total_value"] = float(value_wide.iloc[-1].sum())
        if len(value_wide) >= 20:
            feats["market_value_vs_ma20"] = float(
                value_wide.iloc[-1].sum() / (value_wide.iloc[-20:].sum(axis=1).mean() + 1)
            )
        else:
            feats["market_value_vs_ma20"] = np.nan
    else:
        feats["market_total_value"] = np.nan
        feats["market_value_vs_ma20"] = np.nan

    # ── Determine Market Regime ────────────────────────────────────────────────
    regime = classify_market_regime(feats)
    feats["market_regime"] = regime

    feats["regime_bullish"] = 1.0 if regime in ("BULL_TREND", "BULL_RANGE") else 0.0
    feats["regime_bearish"] = 1.0 if regime in ("BEAR_TREND", "BEAR_RANGE") else 0.0
    feats["regime_trending"] = 1.0 if regime in ("BULL_TREND", "BEAR_TREND") else 0.0
    feats["regime_volatile"] = 1.0 if regime in ("HIGH_VOL",) else 0.0

    return feats


def classify_market_regime(mfeats: dict) -> str:
    """
    Classify market into one of:
    BULL_TREND, BULL_RANGE, BEAR_TREND, BEAR_RANGE, HIGH_VOL, NEUTRAL
    """
    pct_above_sma20 = mfeats.get("pct_above_sma20", np.nan)
    breadth_1d = mfeats.get("market_breadth_1d", np.nan)
    ret_20d = mfeats.get("market_return_20d", np.nan)
    hvol20 = mfeats.get("market_hvol20", np.nan)

    # High volatility check
    if not pd.isna(hvol20) and hvol20 > 0.30:  # >30% annualized vol
        return "HIGH_VOL"

    if pd.isna(pct_above_sma20) or pd.isna(breadth_1d):
        return "NEUTRAL"

    if pct_above_sma20 > 0.60 and not pd.isna(ret_20d) and ret_20d > 0:
        return "BULL_TREND"
    elif pct_above_sma20 > 0.45:
        return "BULL_RANGE"
    elif pct_above_sma20 < 0.30 and not pd.isna(ret_20d) and ret_20d < 0:
        return "BEAR_TREND"
    elif pct_above_sma20 < 0.45:
        return "BEAR_RANGE"
    else:
        return "NEUTRAL"


def compute_cross_sectional_ranks(
    as_of_date: str,
    db_path: str,
    tickers: list[str],
) -> pd.DataFrame:
    """
    Compute cross-sectional ranks for a set of tickers on a given date.
    Returns DataFrame with ticker as index and rank columns.
    """
    from datetime import datetime, timedelta
    dt = datetime.strptime(as_of_date, "%Y-%m-%d")
    start = (dt - timedelta(days=100)).strftime("%Y-%m-%d")

    with sqlite3.connect(db_path) as conn:
        df = pd.read_sql_query(
            """SELECT tanggal, kode_saham,
                      harga_penutupan AS close,
                      COALESCE(nilai_transaksi, 0) AS value,
                      COALESCE(frekuensi, 0) AS freq
               FROM ringkasan_saham_harian
               WHERE tanggal >= ? AND tanggal <= ?
                 AND harga_penutupan IS NOT NULL AND harga_penutupan > 0
               ORDER BY tanggal, kode_saham""",
            conn,
            params=(start, as_of_date),
        )

    if df.empty:
        return pd.DataFrame()

    # Only latest date's data
    latest = df[df["tanggal"] == as_of_date].copy()
    if latest.empty:
        # Use most recent available
        latest = df[df["tanggal"] == df["tanggal"].max()].copy()

    # Return percentile ranks
    latest["ret_1d"] = None  # placeholder

    # Compute returns for ranking
    close_wide = df.pivot(index="tanggal", columns="kode_saham", values="close").sort_index()
    if len(close_wide) >= 2:
        ret_1d = (close_wide.iloc[-1] / close_wide.iloc[-2] - 1.0).rename("ret_1d")
        ret_5d = (close_wide.iloc[-1] / close_wide.iloc[-6] - 1.0).rename("ret_5d") if len(close_wide) >= 6 else None
        ret_20d = (close_wide.iloc[-1] / close_wide.iloc[-21] - 1.0).rename("ret_20d") if len(close_wide) >= 21 else None

        ranks = pd.DataFrame(index=close_wide.columns)
        ranks["return_rank_1d"] = ret_1d.rank(pct=True)
        if ret_5d is not None:
            ranks["return_rank_5d"] = ret_5d.rank(pct=True)
        if ret_20d is not None:
            ranks["return_rank_20d"] = ret_20d.rank(pct=True)

        # Value rank
        latest_val = latest.set_index("kode_saham")["value"]
        ranks["value_rank"] = latest_val.rank(pct=True)

        # Filter to requested tickers
        ranks = ranks.loc[ranks.index.isin(tickers)]
        ranks.index.name = "ticker"
        return ranks.reset_index()

    return pd.DataFrame()


def compute_sector_features(
    as_of_date: str,
    db_path: str,
    tickers: list[str],
) -> dict[str, dict]:
    """
    Compute listing-board level (proxy for sector) features.
    Returns {ticker: {sector_return_5d, sector_rank, ...}}
    """
    # Get listing board info
    with sqlite3.connect(db_path) as conn:
        stocks = pd.read_sql_query(
            "SELECT code, listing_board FROM idx_stocks WHERE code IN ({})".format(
                ",".join(["?" for _ in tickers])
            ),
            conn, params=list(tickers)
        )

    if stocks.empty:
        return {}

    ticker_to_board = dict(zip(stocks["code"], stocks["listing_board"]))

    from datetime import datetime, timedelta
    dt = datetime.strptime(as_of_date, "%Y-%m-%d")
    start = (dt - timedelta(days=30)).strftime("%Y-%m-%d")

    with sqlite3.connect(db_path) as conn:
        df = pd.read_sql_query(
            """SELECT r.tanggal, r.kode_saham, r.harga_penutupan AS close,
                      s.listing_board
               FROM ringkasan_saham_harian r
               LEFT JOIN idx_stocks s ON s.code = r.kode_saham
               WHERE r.tanggal >= ? AND r.tanggal <= ?
                 AND r.harga_penutupan IS NOT NULL AND r.harga_penutupan > 0
                 AND r.volume > 0
               ORDER BY r.tanggal""",
            conn, params=(start, as_of_date)
        )

    if df.empty:
        return {}

    close_wide = df.pivot(index="tanggal", columns="kode_saham", values="close").sort_index()
    board_map = df[["kode_saham", "listing_board"]].drop_duplicates().set_index("kode_saham")["listing_board"]

    result = {}
    if len(close_wide) >= 6:
        ret_5d = (close_wide.iloc[-1] / close_wide.iloc[-6] - 1.0).dropna()

        for tk in tickers:
            board = ticker_to_board.get(tk, "Unknown")
            board_stocks = board_map[board_map == board].index.tolist()
            board_ret = ret_5d.reindex(board_stocks).dropna()
            tk_ret = ret_5d.get(tk, np.nan)

            result[tk] = {
                "sector": board,
                "sector_return_5d": float(board_ret.mean()) if len(board_ret) > 0 else np.nan,
                "sector_rank": float((board_ret < tk_ret).mean()) if len(board_ret) > 1 and not pd.isna(tk_ret) else np.nan,
                "sector_n_stocks": len(board_stocks),
            }

    return result


def _empty_market_features() -> dict:
    keys = [
        "market_return_1d", "market_return_5d", "market_return_20d", "market_return_60d",
        "market_breadth_1d", "pct_above_sma20", "pct_above_sma50", "pct_stocks_up_5d",
        "new_highs_20d", "new_lows_20d", "high_low_ratio",
        "market_dispersion", "market_hvol20", "advance_decline_ratio",
        "market_median_return", "market_total_value", "market_value_vs_ma20",
        "market_regime", "regime_bullish", "regime_bearish", "regime_trending", "regime_volatile",
    ]
    return {k: np.nan for k in keys}
