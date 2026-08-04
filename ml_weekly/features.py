"""
ml_weekly/features.py
Feature engineering from price, volume, and foreign flow data.
All features use only data up to and including as_of_date.
No future data is ever touched.
"""
from __future__ import annotations
import sqlite3
import logging
from typing import Optional
import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# ── Helper Functions ──────────────────────────────────────────────────────────
def _ema(s: pd.Series, period: int) -> pd.Series:
    return s.ewm(span=period, adjust=False, min_periods=period).mean()

def _sma(s: pd.Series, period: int) -> pd.Series:
    return s.rolling(period, min_periods=period).mean()

def _stddev(s: pd.Series, period: int) -> pd.Series:
    return s.rolling(period, min_periods=period).std(ddof=1)

def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, adjust=False, min_periods=period).mean()


def build_features_for_stock(
    df: pd.DataFrame,
    as_of_date: str,
) -> dict:
    """
    Build all features for a single stock up to as_of_date.
    df must be sorted by date ascending, only rows <= as_of_date.
    Returns dict of feature_name -> value (scalar).
    """
    if df.empty or len(df) < 5:
        return {}

    close = df["close"].astype(float)
    high = df["high"].astype(float) if "high" in df.columns else close.copy()
    low = df["low"].astype(float) if "low" in df.columns else close.copy()
    volume = df["volume"].fillna(0).astype(float) if "volume" in df.columns else pd.Series(0, index=df.index)
    value = df["value"].fillna(0).astype(float) if "value" in df.columns else pd.Series(0, index=df.index)
    freq = df["freq"].fillna(0).astype(float) if "freq" in df.columns else pd.Series(0, index=df.index)
    foreign_buy = df["foreign_buy"].fillna(0).astype(float) if "foreign_buy" in df.columns else pd.Series(0, index=df.index)
    foreign_sell = df["foreign_sell"].fillna(0).astype(float) if "foreign_sell" in df.columns else pd.Series(0, index=df.index)

    n = len(close)
    features = {}

    # ── A. Price Momentum ─────────────────────────────────────────────────────
    for lag in [1, 2, 3, 5, 10, 20, 60]:
        if n > lag:
            ret = float(close.iloc[-1] / close.iloc[-1 - lag] - 1.0)
            features[f"return_{lag}d"] = ret
            features[f"log_return_{lag}d"] = float(np.log(close.iloc[-1] / close.iloc[-1 - lag])) if close.iloc[-1 - lag] > 0 else np.nan
        else:
            features[f"return_{lag}d"] = np.nan
            features[f"log_return_{lag}d"] = np.nan

    # Consecutive up/down days
    daily_rets = close.pct_change().dropna()
    if len(daily_rets) >= 2:
        last_sign = np.sign(daily_rets.iloc[-1])
        up_streak = 0
        dn_streak = 0
        for r in reversed(daily_rets.values):
            if r > 0:
                up_streak += 1
                dn_streak = 0 if up_streak == 1 else dn_streak
            elif r < 0:
                dn_streak += 1
                up_streak = 0 if dn_streak == 1 else up_streak
            else:
                break
            if r > 0 and last_sign <= 0:
                break
            if r < 0 and last_sign >= 0:
                break
        features["up_streak"] = up_streak
        features["dn_streak"] = dn_streak
    else:
        features["up_streak"] = 0
        features["dn_streak"] = 0

    # 52W position / rolling high/low
    for window in [20, 60, 120, 252]:
        if n > window:
            sub = close.iloc[-window:]
            h = float(sub.max())
            l = float(sub.min())
            c = float(close.iloc[-1])
            features[f"close_vs_high_{window}d"] = (c - h) / h if h > 0 else 0.0
            features[f"close_vs_low_{window}d"] = (c - l) / l if l > 0 else 0.0
            features[f"range_pct_{window}d"] = (h - l) / l if l > 0 else 0.0
        else:
            features[f"close_vs_high_{window}d"] = np.nan
            features[f"close_vs_low_{window}d"] = np.nan
            features[f"range_pct_{window}d"] = np.nan

    # Gap open (use prev close vs current open if available)
    if "open" in df.columns and n >= 2:
        today_open = float(df["open"].iloc[-1]) if not pd.isna(df["open"].iloc[-1]) and df["open"].iloc[-1] > 0 else None
        prev_close = float(close.iloc[-2])
        if today_open and prev_close > 0:
            features["gap_open"] = today_open / prev_close - 1.0
            features["open_to_close"] = float(close.iloc[-1]) / today_open - 1.0
        else:
            features["gap_open"] = np.nan
            features["open_to_close"] = np.nan
    else:
        features["gap_open"] = np.nan
        features["open_to_close"] = np.nan

    # ── B. Candlestick Structure ──────────────────────────────────────────────
    today_h = float(high.iloc[-1])
    today_l = float(low.iloc[-1])
    today_c = float(close.iloc[-1])
    today_o = float(df["open"].iloc[-1]) if "open" in df.columns and not pd.isna(df["open"].iloc[-1]) and df["open"].iloc[-1] > 0 else today_c

    daily_range = today_h - today_l if today_h > today_l else np.nan
    body = abs(today_c - today_o)
    upper_wick = today_h - max(today_c, today_o)
    lower_wick = min(today_c, today_o) - today_l

    features["daily_range"] = daily_range / today_c if daily_range and today_c > 0 else np.nan
    features["body_pct"] = body / daily_range if daily_range and daily_range > 0 else np.nan
    features["upper_wick_pct"] = upper_wick / daily_range if daily_range and daily_range > 0 else np.nan
    features["lower_wick_pct"] = lower_wick / daily_range if daily_range and daily_range > 0 else np.nan
    features["close_location"] = (today_c - today_l) / daily_range if daily_range and daily_range > 0 else 0.5
    features["bullish_candle"] = 1.0 if today_c >= today_o else 0.0

    # ── C. Trend (SMA/EMA) ────────────────────────────────────────────────────
    for period in [5, 10, 20, 50, 100, 200]:
        sma = _sma(close, period)
        if not pd.isna(sma.iloc[-1]) and sma.iloc[-1] > 0:
            features[f"sma{period}"] = float(sma.iloc[-1])
            features[f"close_vs_sma{period}"] = float(close.iloc[-1] / sma.iloc[-1] - 1.0)
        else:
            features[f"sma{period}"] = np.nan
            features[f"close_vs_sma{period}"] = np.nan

    for period in [5, 10, 20, 50, 100]:
        ema = _ema(close, period)
        if not pd.isna(ema.iloc[-1]) and ema.iloc[-1] > 0:
            features[f"ema{period}"] = float(ema.iloc[-1])
            features[f"close_vs_ema{period}"] = float(close.iloc[-1] / ema.iloc[-1] - 1.0)
        else:
            features[f"ema{period}"] = np.nan
            features[f"close_vs_ema{period}"] = np.nan

    # Golden/Death cross
    sma50 = _sma(close, 50)
    sma200 = _sma(close, 200)
    if not (pd.isna(sma50.iloc[-1]) or pd.isna(sma200.iloc[-1])) and sma200.iloc[-1] > 0:
        features["golden_cross"] = 1.0 if sma50.iloc[-1] > sma200.iloc[-1] else 0.0
        features["sma50_vs_sma200"] = float(sma50.iloc[-1] / sma200.iloc[-1] - 1.0)
    else:
        features["golden_cross"] = np.nan
        features["sma50_vs_sma200"] = np.nan

    # MA alignment (price > EMA20 > EMA50 > EMA100)
    e20 = _ema(close, 20)
    e50 = _ema(close, 50)
    e100 = _ema(close, 100)
    if not any(pd.isna(x.iloc[-1]) for x in [e20, e50, e100]):
        c = close.iloc[-1]
        features["ma_aligned_bull"] = 1.0 if (c > e20.iloc[-1] > e50.iloc[-1] > e100.iloc[-1]) else 0.0
        features["ma_aligned_bear"] = 1.0 if (c < e20.iloc[-1] < e50.iloc[-1] < e100.iloc[-1]) else 0.0
    else:
        features["ma_aligned_bull"] = np.nan
        features["ma_aligned_bear"] = np.nan

    # SMA slope
    if n >= 22 and not pd.isna(_sma(close, 20).iloc[-1]):
        sma20_now = float(_sma(close, 20).iloc[-1])
        sma20_5ago = float(_sma(close, 20).iloc[-6]) if n >= 26 else np.nan
        features["sma20_slope"] = (sma20_now / sma20_5ago - 1.0) if sma20_5ago and sma20_5ago > 0 else np.nan
    else:
        features["sma20_slope"] = np.nan

    # Efficiency ratio (trending vs choppy)
    if n >= 21:
        net_move = abs(float(close.iloc[-1]) - float(close.iloc[-21]))
        path = float(close.diff().abs().iloc[-20:].sum())
        features["efficiency_ratio_20d"] = net_move / path if path > 0 else 0.0
    else:
        features["efficiency_ratio_20d"] = np.nan

    # ── D. Momentum Oscillators ───────────────────────────────────────────────
    for period in [2, 5, 7, 14, 21]:
        rsi_val = _rsi(close, period)
        features[f"rsi{period}"] = float(rsi_val.iloc[-1]) if not pd.isna(rsi_val.iloc[-1]) else np.nan

    # Stochastic %K
    if n >= 14:
        h14 = high.rolling(14).max()
        l14 = low.rolling(14).min()
        stoch_k = 100 * (close - l14) / (h14 - l14 + 1e-10)
        features["stoch_k"] = float(stoch_k.iloc[-1]) if not pd.isna(stoch_k.iloc[-1]) else np.nan
        stoch_d = stoch_k.rolling(3).mean()
        features["stoch_d"] = float(stoch_d.iloc[-1]) if not pd.isna(stoch_d.iloc[-1]) else np.nan
    else:
        features["stoch_k"] = np.nan
        features["stoch_d"] = np.nan

    # MACD
    if n >= 26:
        macd_line = _ema(close, 12) - _ema(close, 26)
        macd_sig = _ema(macd_line, 9)
        macd_hist = macd_line - macd_sig
        c_norm = float(close.iloc[-1]) or 1.0
        features["macd_line"] = float(macd_line.iloc[-1]) / c_norm if not pd.isna(macd_line.iloc[-1]) else np.nan
        features["macd_signal"] = float(macd_sig.iloc[-1]) / c_norm if not pd.isna(macd_sig.iloc[-1]) else np.nan
        features["macd_hist"] = float(macd_hist.iloc[-1]) / c_norm if not pd.isna(macd_hist.iloc[-1]) else np.nan
        features["macd_bullish"] = 1.0 if (not pd.isna(macd_hist.iloc[-1]) and macd_hist.iloc[-1] > 0) else 0.0
    else:
        features["macd_line"] = np.nan
        features["macd_signal"] = np.nan
        features["macd_hist"] = np.nan
        features["macd_bullish"] = np.nan

    # Williams %R
    if n >= 14:
        h14 = high.rolling(14).max()
        l14 = low.rolling(14).min()
        wr = -100 * (h14 - close) / (h14 - l14 + 1e-10)
        features["williams_r"] = float(wr.iloc[-1]) if not pd.isna(wr.iloc[-1]) else np.nan
    else:
        features["williams_r"] = np.nan

    # ROC 10
    if n >= 11:
        features["roc10"] = float(close.iloc[-1] / close.iloc[-11] - 1.0)
    else:
        features["roc10"] = np.nan

    # ── E. Volatility ─────────────────────────────────────────────────────────
    for period in [5, 10, 14, 20]:
        atr = _atr(high, low, close, period)
        if not pd.isna(atr.iloc[-1]) and close.iloc[-1] > 0:
            features[f"atr{period}"] = float(atr.iloc[-1])
            features[f"atr{period}_pct"] = float(atr.iloc[-1] / close.iloc[-1])
        else:
            features[f"atr{period}"] = np.nan
            features[f"atr{period}_pct"] = np.nan

    for period in [5, 10, 20, 60]:
        if n > period:
            ret_series = close.pct_change()
            ret_std = ret_series.rolling(period).std(ddof=1)
            features[f"hvol{period}"] = float(ret_std.iloc[-1] * np.sqrt(252)) if not pd.isna(ret_std.iloc[-1]) else np.nan
            
            downside_ret = ret_series.where(ret_series < 0, 0)
            downside_std = downside_ret.rolling(period).std(ddof=1)
            features[f"downside_vol{period}"] = float(downside_std.iloc[-1] * np.sqrt(252)) if not pd.isna(downside_std.iloc[-1]) else np.nan
        else:
            features[f"hvol{period}"] = np.nan
            features[f"downside_vol{period}"] = np.nan

    # Bollinger Bands
    if n >= 20:
        sma20_s = _sma(close, 20)
        std20_s = _stddev(close, 20)
        bb_upper = sma20_s + 2 * std20_s
        bb_lower = sma20_s - 2 * std20_s
        bb_width = (bb_upper - bb_lower) / sma20_s.replace(0, np.nan)
        bb_pos = (close - bb_lower) / (bb_upper - bb_lower + 1e-10)

        features["bb_width"] = float(bb_width.iloc[-1]) if not pd.isna(bb_width.iloc[-1]) else np.nan
        features["bb_position"] = float(bb_pos.iloc[-1]) if not pd.isna(bb_pos.iloc[-1]) else np.nan

        # Squeeze: BB width at N-period low (volatility contraction)
        if n >= 25:
            bb_width_20 = bb_width.rolling(20).min()
            features["bb_squeeze"] = 1.0 if (not pd.isna(bb_width.iloc[-1]) and
                                              not pd.isna(bb_width_20.iloc[-1]) and
                                              bb_width.iloc[-1] <= bb_width_20.iloc[-1] * 1.1) else 0.0
        else:
            features["bb_squeeze"] = np.nan
    else:
        features["bb_width"] = np.nan
        features["bb_position"] = np.nan
        features["bb_squeeze"] = np.nan

    # Volatility percentile
    if n >= 60:
        hvol20_series = close.pct_change().rolling(20).std(ddof=1) * np.sqrt(252)
        current_hvol = hvol20_series.iloc[-1]
        hist_hvol = hvol20_series.iloc[-60:].dropna()
        if len(hist_hvol) >= 10 and not pd.isna(current_hvol):
            pct = float((hist_hvol <= current_hvol).mean())
            features["hvol_percentile_60d"] = pct
        else:
            features["hvol_percentile_60d"] = np.nan
    else:
        features["hvol_percentile_60d"] = np.nan

    # ── F. Volume & Liquidity ─────────────────────────────────────────────────
    if n >= 2 and volume.iloc[-1] > 0:
        features["volume_today"] = float(volume.iloc[-1])
        features["value_today"] = float(value.iloc[-1])
        features["freq_today"] = float(freq.iloc[-1])

        for period in [5, 10, 20, 60]:
            if n > period:
                vol_ma = float(volume.iloc[-period:].mean())
                val_ma = float(value.iloc[-period:].mean())
                features[f"rel_volume_{period}d"] = float(volume.iloc[-1]) / (vol_ma + 1e-10)
                features[f"rel_value_{period}d"] = float(value.iloc[-1]) / (val_ma + 1e-10)
                features[f"avg_value_{period}d"] = val_ma
            else:
                features[f"rel_volume_{period}d"] = np.nan
                features[f"rel_value_{period}d"] = np.nan
                features[f"avg_value_{period}d"] = np.nan

        # OBV-like: accumulation indicator
        close_chg = close.diff()
        obv_dir = np.sign(close_chg)
        obv = (obv_dir * volume).cumsum()
        if n >= 20 and not pd.isna(obv.iloc[-1]):
            obv_ma20 = float(_sma(obv, 20).iloc[-1])
            obv_now = float(obv.iloc[-1])
            features["obv_vs_ma20"] = (obv_now - obv_ma20) / (abs(obv_ma20) + 1e-10)
        else:
            features["obv_vs_ma20"] = np.nan

        # Volume-price confirmation (vol expanding with price rising)
        if n >= 5:
            vol_trend = volume.iloc[-5:].values
            price_trend = close.iloc[-5:].values
            if not np.any(np.isnan(vol_trend)) and not np.any(np.isnan(price_trend)):
                features["vol_price_corr_5d"] = float(np.corrcoef(vol_trend, price_trend)[0, 1])
            else:
                features["vol_price_corr_5d"] = np.nan
        else:
            features["vol_price_corr_5d"] = np.nan

        # Zero volume frequency (measure of inactivity)
        if n >= 20:
            features["zero_vol_pct_20d"] = float((volume.iloc[-20:] == 0).mean())
        else:
            features["zero_vol_pct_20d"] = np.nan

        # MFI (Money Flow Index)
        if n >= 14:
            typical_price = (high + low + close) / 3
            mf = typical_price * volume
            pos_mf = mf.where(typical_price > typical_price.shift(1), 0)
            neg_mf = mf.where(typical_price < typical_price.shift(1), 0)
            pos_mf_sum = pos_mf.rolling(14).sum()
            neg_mf_sum = neg_mf.rolling(14).sum()
            mfi = 100 - (100 / (1 + pos_mf_sum / (neg_mf_sum + 1e-10)))
            features["mfi14"] = float(mfi.iloc[-1]) if not pd.isna(mfi.iloc[-1]) else np.nan
        else:
            features["mfi14"] = np.nan

        # Amihud illiquidity proxy
        if n >= 20 and "value" in df.columns:
            ret_abs = close.pct_change().abs()
            amihud_daily = ret_abs / (value + 1)
            features["amihud_20d"] = float(amihud_daily.iloc[-20:].mean())
        else:
            features["amihud_20d"] = np.nan

    else:
        for key in ["volume_today", "value_today", "freq_today",
                    "rel_volume_5d", "rel_volume_10d", "rel_volume_20d", "rel_volume_60d",
                    "rel_value_5d", "rel_value_10d", "rel_value_20d", "rel_value_60d",
                    "avg_value_5d", "avg_value_10d", "avg_value_20d", "avg_value_60d",
                    "obv_vs_ma20", "vol_price_corr_5d", "zero_vol_pct_20d",
                    "mfi14", "amihud_20d"]:
            features[key] = np.nan

    # ── G. Foreign Flow Features ──────────────────────────────────────────────
    foreign_net = foreign_buy - foreign_sell
    for period in [1, 5, 10, 20]:
        if n >= period and foreign_buy.sum() > 0:
            fn_period = float(foreign_net.iloc[-period:].sum())
            val_period = float(value.iloc[-period:].sum() + 1)
            features[f"foreign_net_{period}d"] = fn_period / val_period
            features[f"foreign_buy_{period}d"] = float(foreign_buy.iloc[-period:].sum()) / val_period
            features[f"foreign_sell_{period}d"] = float(foreign_sell.iloc[-period:].sum()) / val_period
        else:
            features[f"foreign_net_{period}d"] = 0.0
            features[f"foreign_buy_{period}d"] = 0.0
            features[f"foreign_sell_{period}d"] = 0.0

    return features


def build_all_features(
    as_of_date: str,
    db_path: str,
    tickers: list[str],
    lookback_days: int = 252,
) -> pd.DataFrame:
    """
    Build features for all eligible tickers as of a given date.
    Returns DataFrame with ticker as index and features as columns.
    """
    # Load data: use a lookback window of ~350 calendar days
    from datetime import datetime, timedelta
    dt = datetime.strptime(as_of_date, "%Y-%m-%d")
    start_load = (dt - timedelta(days=lookback_days + 100)).strftime("%Y-%m-%d")

    with sqlite3.connect(db_path) as conn:
        df_raw = pd.read_sql_query(
            """SELECT tanggal, kode_saham,
                      harga_penutupan AS close,
                      harga_pembukaan AS open,
                      harga_tertinggi AS high,
                      harga_terendah  AS low,
                      COALESCE(volume, 0) AS volume,
                      COALESCE(nilai_transaksi, 0) AS value,
                      COALESCE(frekuensi, 0) AS freq,
                      COALESCE(beli_asing, 0) AS foreign_buy,
                      COALESCE(jual_asing, 0) AS foreign_sell
               FROM ringkasan_saham_harian
               WHERE tanggal >= ? AND tanggal <= ?
                 AND kode_saham IN ({})
                 AND harga_penutupan IS NOT NULL AND harga_penutupan > 0
               ORDER BY kode_saham, tanggal""".format(
                ",".join(["?" for _ in tickers])
            ),
            conn,
            params=[start_load, as_of_date] + list(tickers),
        )

    if df_raw.empty:
        return pd.DataFrame()

    all_features = []
    for ticker, grp in df_raw.groupby("kode_saham"):
        grp = grp.sort_values("tanggal").reset_index(drop=True)
        feats = build_features_for_stock(grp, as_of_date)
        if feats:
            feats["ticker"] = ticker
            feats["signal_date"] = as_of_date
            feats["n_days"] = len(grp)
            all_features.append(feats)

    if not all_features:
        return pd.DataFrame()

    return pd.DataFrame(all_features)


def get_feature_names() -> list[str]:
    """Return the list of all feature column names (excluding ticker and metadata)."""
    excluded = {"ticker", "signal_date", "n_days", "feature_as_of_date"}
    # Compute from a dummy run
    dummy = pd.DataFrame({
        "tanggal": pd.date_range("2020-01-01", periods=260, freq="B").strftime("%Y-%m-%d"),
        "close": np.cumprod(1 + np.random.randn(260) * 0.01) * 1000,
        "open": np.cumprod(1 + np.random.randn(260) * 0.01) * 1000,
        "high": np.cumprod(1 + np.random.randn(260) * 0.01) * 1010,
        "low": np.cumprod(1 + np.random.randn(260) * 0.01) * 990,
        "volume": np.abs(np.random.randn(260) * 1e6),
        "value": np.abs(np.random.randn(260) * 1e9),
        "freq": np.abs(np.random.randn(260) * 500),
        "foreign_buy": np.abs(np.random.randn(260) * 1e8),
        "foreign_sell": np.abs(np.random.randn(260) * 1e8),
    })
    f = build_features_for_stock(dummy, "2020-12-31")
    return [k for k in f.keys() if k not in excluded]
