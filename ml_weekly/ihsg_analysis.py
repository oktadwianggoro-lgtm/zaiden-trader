"""
ml_weekly/ihsg_analysis.py
Analisis & prediksi arah IHSG (Jakarta Composite Index) berbasis data resmi IDX
(idx_index_daily, index_code='COMPOSITE' — lihat tools/sync_idx_index_daily.py).

Dua horizon probabilitas arah dihasilkan lewat walk-forward logistic regression
(expanding window, retrain berkala, tanpa leakage — model di hari t hanya boleh
memakai label yang sudah pasti diketahui sebelum t):
  - 5 hari bursa (~1 minggu) — arah taktis jangka pendek.
  - 60 hari bursa (~3 bulan) — arah tren jangka menengah.

"Mencari bottom" TIDAK diberikan sebagai tanggal pasti (tidak ada metode yang
bisa jujur melakukan itu). Sebagai gantinya, drawdown hari ini dari all-time-high
dibandingkan terhadap seluruh episode historis dengan kedalaman serupa, lalu
dilaporkan statistik dasar (median/rentang jarak ke titik balik lokal berikutnya)
sebagai analog historis probabilistik, bukan janji.
"""
from __future__ import annotations

import sqlite3
import warnings
from typing import Optional

import numpy as np
import pandas as pd

from .train import wilson_ci

warnings.filterwarnings("ignore")

COMPOSITE_CODE = "COMPOSITE"
HORIZONS = {"short": 5, "medium": 60}
MIN_TRAIN_ROWS = 300
RETRAIN_EVERY = 20
LOCAL_EXTREMUM_WINDOW = 20
ANALOG_BAND_PCT = 5.0


# ── Helpers ──────────────────────────────────────────────────────────────────
def _ema(s: pd.Series, period: int) -> pd.Series:
    return s.ewm(span=period, adjust=False, min_periods=period).mean()


def _sma(s: pd.Series, period: int) -> pd.Series:
    return s.rolling(period, min_periods=period).mean()


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def load_composite_series(db_path: str) -> pd.DataFrame:
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(
        """SELECT tanggal AS date, previous, highest, lowest, close, change, volume, value
           FROM idx_index_daily
           WHERE index_code = ?
           ORDER BY tanggal ASC""",
        conn,
        params=(COMPOSITE_CODE,),
    )
    conn.close()
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    for col in ("previous", "highest", "lowest", "close", "change", "volume", "value"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["close"]).reset_index(drop=True)


def _build_technicals(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    close = out["close"]
    out["sma20"] = _sma(close, 20)
    out["sma50"] = _sma(close, 50)
    out["sma200"] = _sma(close, 200)
    ema12, ema26 = _ema(close, 12), _ema(close, 26)
    out["macd"] = ema12 - ema26
    out["macd_signal"] = _ema(out["macd"], 9)
    out["macd_hist"] = out["macd"] - out["macd_signal"]
    out["rsi14"] = _rsi(close, 14)
    bb_mid = _sma(close, 20)
    bb_std = close.rolling(20, min_periods=20).std(ddof=1)
    out["bb_upper"] = bb_mid + 2 * bb_std
    out["bb_lower"] = bb_mid - 2 * bb_std
    bb_range = (out["bb_upper"] - out["bb_lower"]).replace(0, np.nan)
    out["bb_pctb"] = (close - out["bb_lower"]) / bb_range
    out["daily_return"] = close.pct_change()
    out["volatility_20d"] = out["daily_return"].rolling(20, min_periods=20).std(ddof=1)
    out["ath_to_date"] = close.cummax()
    out["drawdown_pct"] = (close / out["ath_to_date"] - 1.0) * 100.0
    for lag in (1, 5, 10, 20, 60):
        out[f"return_{lag}d"] = close.pct_change(lag)
    out["pct_from_sma20"] = close / out["sma20"] - 1.0
    out["pct_from_sma50"] = close / out["sma50"] - 1.0
    out["pct_from_sma200"] = close / out["sma200"] - 1.0
    return out


FEATURE_COLUMNS = [
    "return_1d", "return_5d", "return_10d", "return_20d", "return_60d",
    "rsi14", "macd_hist", "bb_pctb", "volatility_20d",
    "pct_from_sma20", "pct_from_sma50", "pct_from_sma200", "drawdown_pct",
]


def _fit_predict(train_X: np.ndarray, train_y: np.ndarray, x_row: np.ndarray) -> Optional[float]:
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler

    if len(np.unique(train_y)) < 2 or len(train_y) < 30:
        return None
    scaler = StandardScaler()
    train_X_scaled = scaler.fit_transform(train_X)
    model = LogisticRegression(max_iter=300, class_weight="balanced")
    try:
        model.fit(train_X_scaled, train_y)
    except Exception:
        return None
    x_scaled = scaler.transform(x_row.reshape(1, -1))
    return float(model.predict_proba(x_scaled)[0, 1])


def _walk_forward_direction(tech: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """
    Expanding-window walk-forward probability that close[t+horizon] > close[t].
    Model refit every RETRAIN_EVERY trading days on all labels resolvable
    strictly before t (j + horizon <= t - 1), then frozen and applied forward
    until the next refit point — same no-leakage discipline as the rest of
    this app's ML pipeline.
    """
    n = len(tech)
    label = (tech["close"].shift(-horizon) > tech["close"]).astype(float)
    feats = tech[FEATURE_COLUMNS].values
    valid_feat_row = ~np.isnan(feats).any(axis=1)

    dates, probs, actual_labels = [], [], []
    model_cache: Optional[tuple] = None

    for t in range(MIN_TRAIN_ROWS, n):
        if not valid_feat_row[t]:
            continue
        if model_cache is None or (t - MIN_TRAIN_ROWS) % RETRAIN_EVERY == 0:
            train_end = t - horizon  # rows [0, train_end) have fully known labels
            train_mask = valid_feat_row[:train_end] & label[:train_end].notna().values
            if train_mask.sum() >= 30:
                train_X = feats[:train_end][train_mask]
                train_y = label[:train_end][train_mask].values
                from sklearn.linear_model import LogisticRegression
                from sklearn.preprocessing import StandardScaler
                scaler = StandardScaler().fit(train_X)
                try:
                    model = LogisticRegression(max_iter=300, class_weight="balanced")
                    model.fit(scaler.transform(train_X), train_y)
                    model_cache = (model, scaler)
                except Exception:
                    model_cache = None
        if model_cache is None:
            continue
        model, scaler = model_cache
        p = float(model.predict_proba(scaler.transform(feats[t].reshape(1, -1)))[0, 1])
        dates.append(tech["date"].iloc[t])
        probs.append(p)
        actual_labels.append(label.iloc[t] if not pd.isna(label.iloc[t]) else None)

    return pd.DataFrame({"date": dates, "probability_up": probs, "actual_up": actual_labels})


def _latest_live_prediction(tech: pd.DataFrame, horizon: int) -> Optional[dict]:
    n = len(tech)
    label = (tech["close"].shift(-horizon) > tech["close"]).astype(float)
    feats = tech[FEATURE_COLUMNS].values
    valid_feat_row = ~np.isnan(feats).any(axis=1)
    last_idx = n - 1
    if not valid_feat_row[last_idx]:
        return None
    train_end = last_idx - horizon
    if train_end < MIN_TRAIN_ROWS:
        return None
    train_mask = valid_feat_row[:train_end] & label[:train_end].notna().values
    if train_mask.sum() < 30:
        return None
    p = _fit_predict(feats[:train_end][train_mask], label[:train_end][train_mask].values, feats[last_idx])
    if p is None:
        return None
    return {"probability_up": round(p, 4), "as_of_date": tech["date"].iloc[last_idx].strftime("%Y-%m-%d")}


def _direction_summary(wf: pd.DataFrame, horizon_label: str, horizon_days: int) -> dict:
    resolved = wf.dropna(subset=["actual_up"])
    n = len(resolved)
    if n == 0:
        return {
            "horizon_label": horizon_label, "horizon_days": horizon_days,
            "n_validated": 0, "hit_rate": None, "hit_rate_ci_lower": None,
            "hit_rate_ci_upper": None,
        }
    predicted_up = resolved["probability_up"] >= 0.5
    correct = (predicted_up == resolved["actual_up"].astype(bool))
    hits = int(correct.sum())
    lo, hi = wilson_ci(hits, n)
    return {
        "horizon_label": horizon_label,
        "horizon_days": horizon_days,
        "n_validated": n,
        "hit_rate": round(hits / n, 4),
        "hit_rate_ci_lower": round(lo, 4),
        "hit_rate_ci_upper": round(hi, 4),
    }


def _classify_regime(row: pd.Series) -> str:
    close, sma50, sma200, rsi = row["close"], row["sma50"], row["sma200"], row["rsi14"]
    vol = row["volatility_20d"]
    if pd.isna(sma50) or pd.isna(sma200) or pd.isna(rsi):
        return "BELUM_CUKUP_DATA"
    if not pd.isna(vol) and vol > 0.02:
        return "VOLATILITAS_TINGGI"
    if close > sma50 > sma200 and rsi > 50:
        return "TREN_NAIK"
    if close < sma50 < sma200 and rsi < 50:
        return "TREN_TURUN"
    return "SIDEWAYS"


def _find_local_bottoms(close: pd.Series, window: int = LOCAL_EXTREMUM_WINDOW) -> list[int]:
    """Index positions of local minima: lower than every point in +/- window."""
    n = len(close)
    bottoms = []
    values = close.values
    for i in range(window, n - window):
        segment = values[i - window: i + window + 1]
        if values[i] == segment.min() and (segment == values[i]).sum() == 1:
            bottoms.append(i)
    return bottoms


def _drawdown_bottom_analog(tech: pd.DataFrame) -> dict:
    close = tech["close"]
    drawdown = tech["drawdown_pct"]
    n = len(tech)
    current_dd = float(drawdown.iloc[-1]) if not pd.isna(drawdown.iloc[-1]) else None
    if current_dd is None or n < LOCAL_EXTREMUM_WINDOW * 2 + 10:
        return {
            "current_drawdown_pct": current_dd, "drawdown_percentile": None,
            "similar_episodes": 0, "median_sessions_to_next_bottom": None,
            "sessions_to_next_bottom_range": None, "note": "Data belum cukup panjang untuk analisis analog historis.",
        }

    # Severity percentile: fraction of historical days that were shallower
    # (less negative / better) than today. A deep drawdown -> almost all
    # history is "better than today" -> percentile close to 100 (rare/severe).
    dd_hist = drawdown.dropna()
    percentile = float((dd_hist >= current_dd).mean() * 100.0)

    bottoms = set(_find_local_bottoms(close))
    # Untuk setiap hari bursa historis (di luar window akhir, agar "jarak ke
    # bottom berikutnya" bisa dihitung penuh) dengan drawdown dalam pita +-5pp
    # dari drawdown hari ini, cari jarak (hari bursa) ke local bottom berikutnya.
    lo_band, hi_band = current_dd - ANALOG_BAND_PCT, current_dd + ANALOG_BAND_PCT
    distances = []
    search_limit = n - LOCAL_EXTREMUM_WINDOW  # bottoms need window of lookahead to be confirmed
    for i in range(LOCAL_EXTREMUM_WINDOW, search_limit):
        dd_i = drawdown.iloc[i]
        if pd.isna(dd_i) or not (lo_band <= dd_i <= hi_band):
            continue
        next_bottom = next((b for b in sorted(bottoms) if b > i), None)
        if next_bottom is not None:
            distances.append(next_bottom - i)

    if not distances:
        return {
            "current_drawdown_pct": round(current_dd, 2), "drawdown_percentile": round(percentile, 1),
            "similar_episodes": 0, "median_sessions_to_next_bottom": None,
            "sessions_to_next_bottom_range": None,
            "note": "Belum ada episode historis dengan kedalaman drawdown serupa (+/-5pp) yang bisa dijadikan pembanding.",
        }

    distances_arr = np.array(distances)
    return {
        "current_drawdown_pct": round(current_dd, 2),
        "drawdown_percentile": round(percentile, 1),
        "similar_episodes": len(distances),
        "median_sessions_to_next_bottom": int(np.median(distances_arr)),
        "sessions_to_next_bottom_range": [int(distances_arr.min()), int(distances_arr.max())],
        "note": (
            f"Dari {len(distances)} hari bursa historis dengan drawdown serupa (+/-{ANALOG_BAND_PCT:.0f}pp), "
            f"jarak median ke titik balik lokal berikutnya adalah {int(np.median(distances_arr))} hari bursa "
            f"(rentang {int(distances_arr.min())}-{int(distances_arr.max())} hari). "
            "Ini statistik dasar historis (base rate), BUKAN prediksi tanggal pasti."
        ),
    }


def compute_ihsg_dashboard(db_path: str) -> dict:
    raw = load_composite_series(db_path)
    if raw.empty or len(raw) < MIN_TRAIN_ROWS + 30:
        return {
            "data_available": False,
            "rows": len(raw),
            "min_rows_required": MIN_TRAIN_ROWS + 30,
            "note": (
                f"Data IHSG baru {len(raw)} hari bursa. Butuh minimal {MIN_TRAIN_ROWS + 30} hari bursa "
                "riwayat untuk melatih model walk-forward secara jujur (tanpa memaksakan hasil pada data yang terlalu sedikit)."
            ),
        }

    tech = _build_technicals(raw)

    latest = tech.iloc[-1]
    regime = _classify_regime(latest)

    direction: dict = {}
    for label, horizon in HORIZONS.items():
        wf = _walk_forward_direction(tech, horizon)
        summary = _direction_summary(wf, label, horizon)
        live = _latest_live_prediction(tech, horizon)
        summary["live_prediction"] = live
        direction[label] = summary

    bottom_analog = _drawdown_bottom_analog(tech)

    chart_tail = tech.tail(500).copy()
    timeseries = {
        "dates": chart_tail["date"].dt.strftime("%Y-%m-%d").tolist(),
        "close": [None if pd.isna(v) else round(float(v), 2) for v in chart_tail["close"]],
        "sma50": [None if pd.isna(v) else round(float(v), 2) for v in chart_tail["sma50"]],
        "sma200": [None if pd.isna(v) else round(float(v), 2) for v in chart_tail["sma200"]],
        "drawdown_pct": [None if pd.isna(v) else round(float(v), 2) for v in chart_tail["drawdown_pct"]],
    }

    return {
        "data_available": True,
        "rows": len(raw),
        "as_of_date": latest["date"].strftime("%Y-%m-%d"),
        "latest": {
            "close": round(float(latest["close"]), 2),
            "change_pct": round(float(latest["close"] / raw["close"].iloc[-2] - 1.0) * 100.0, 2) if len(raw) > 1 else None,
            "rsi14": None if pd.isna(latest["rsi14"]) else round(float(latest["rsi14"]), 1),
            "sma50": None if pd.isna(latest["sma50"]) else round(float(latest["sma50"]), 2),
            "sma200": None if pd.isna(latest["sma200"]) else round(float(latest["sma200"]), 2),
            "drawdown_pct": None if pd.isna(latest["drawdown_pct"]) else round(float(latest["drawdown_pct"]), 2),
            "volatility_20d_pct": None if pd.isna(latest["volatility_20d"]) else round(float(latest["volatility_20d"]) * 100.0, 2),
        },
        "regime": regime,
        "direction": direction,
        "bottom_analog": bottom_analog,
        "timeseries": timeseries,
    }
