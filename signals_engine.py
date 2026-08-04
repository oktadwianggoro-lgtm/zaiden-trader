"""
Technical Signal Engine — get_screener_signals()
Menghitung sinyal beli berdasarkan indikator teknikal standar internasional
untuk 7 horizon waktu: 1H (intraday), 1W, 1M, 3M, 6M, 1Y, >1Y.

Indikator:
- Trend  : SMA 5/10/20/50/100/200, EMA 9/21/55, MACD(12,26,9), ADX 14
- Momentum: RSI 14, Stochastic(14,3,3), Williams%R 14, CCI 20
- Volatility: ATR 14, Bollinger Bands(20,2σ), Donchian 20
- Volume : OBV, Volume ratio, MFI 14
- Foreign: Net foreign 5/20/60d cumulative + momentum

TP/SL: ATR-multiple (min RR 2:1), dikombinasi Fibonacci & SMA support.
"""

import sys, os, threading
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from analytics import _reader, _number, _rounded, _data_signature, _CACHE_LOCK, ANALYTICS_VERSION

# -------------------------------------------------------------------
_SIGNALS_CACHE: dict[str, dict] = {}

HORIZONS = {
    "1d": {
        "label": "1 Hari",
        "days": 5,
        "description": "Sinyal beli untuk potensi kenaikan intraday s/d besok",
        "tp_atr": 1.5, "sl_atr": 1.0, "min_atr_pct": 0.3,
    },
    "1w": {
        "label": "1 Minggu",
        "days": 5,
        "description": "Potensi kenaikan dalam 5 hari trading ke depan",
        "tp_atr": 2.5, "sl_atr": 1.5, "min_atr_pct": 0.5,
    },
    "1m": {
        "label": "1 Bulan",
        "days": 20,
        "description": "Potensi kenaikan dalam 20 hari trading (~1 bulan)",
        "tp_atr": 4.0, "sl_atr": 2.0, "min_atr_pct": 0.8,
    },
    "3m": {
        "label": "3 Bulan",
        "days": 60,
        "description": "Potensi kenaikan dalam 60 hari trading (~3 bulan)",
        "tp_atr": 6.0, "sl_atr": 3.0, "min_atr_pct": 1.0,
    },
    "6m": {
        "label": "6 Bulan",
        "days": 120,
        "description": "Potensi kenaikan dalam 120 hari trading (~6 bulan)",
        "tp_atr": 8.0, "sl_atr": 4.0, "min_atr_pct": 1.5,
    },
    "1y": {
        "label": "1 Tahun",
        "days": 250,
        "description": "Potensi kenaikan dalam ~1 tahun (250 hari trading)",
        "tp_atr": 12.0, "sl_atr": 5.0, "min_atr_pct": 2.0,
    },
    "gt1y": {
        "label": "> 1 Tahun",
        "days": 500,
        "description": "Potensi kenaikan jangka panjang lebih dari 1 tahun",
        "tp_atr": 20.0, "sl_atr": 7.0, "min_atr_pct": 3.0,
    },
}


# ─── Pure-Python TA helpers ──────────────────────────────────────────────────

def _sma(prices: list[float], n: int) -> list[float | None]:
    """Simple Moving Average — returns list same length as prices."""
    out = [None] * len(prices)
    for i in range(n - 1, len(prices)):
        out[i] = sum(prices[i - n + 1: i + 1]) / n
    return out


def _ema(prices: list[float], n: int) -> list[float | None]:
    """Exponential Moving Average."""
    out: list[float | None] = [None] * len(prices)
    if len(prices) < n:
        return out
    # seed with SMA
    seed = sum(prices[:n]) / n
    out[n - 1] = seed
    k = 2.0 / (n + 1)
    for i in range(n, len(prices)):
        out[i] = prices[i] * k + out[i - 1] * (1 - k)  # type: ignore[operator]
    return out


def _rsi(closes: list[float], n: int = 14) -> list[float | None]:
    """RSI Wilder smoothing."""
    out: list[float | None] = [None] * len(closes)
    if len(closes) < n + 1:
        return out
    gains, losses = [], []
    for i in range(1, n + 1):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))
    avg_g = sum(gains) / n
    avg_l = sum(losses) / n
    for i in range(n, len(closes)):
        if i > n:
            delta = closes[i] - closes[i - 1]
            avg_g = (avg_g * (n - 1) + max(delta, 0)) / n
            avg_l = (avg_l * (n - 1) + max(-delta, 0)) / n
        rs = avg_g / avg_l if avg_l > 0 else float('inf')
        out[i] = 100 - 100 / (1 + rs)
    return out


def _atr(highs: list[float], lows: list[float], closes: list[float], n: int = 14) -> list[float | None]:
    """Average True Range (Wilder)."""
    out: list[float | None] = [None] * len(closes)
    if len(closes) < n + 1:
        return out
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    if len(trs) < n:
        return out
    atr_val = sum(trs[:n]) / n
    out[n] = atr_val
    for i in range(n + 1, len(closes)):
        atr_val = (atr_val * (n - 1) + trs[i - 1]) / n
        out[i] = atr_val
    return out


def _macd(closes: list[float], fast=12, slow=26, signal=9):
    """MACD — returns (macd_line, signal_line, histogram)."""
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    macd_line: list[float | None] = []
    for f, s in zip(ema_fast, ema_slow):
        macd_line.append((f - s) if f is not None and s is not None else None)  # type: ignore[operator]
    # signal = EMA9 of macd_line (only over non-None)
    macd_vals = [v for v in macd_line if v is not None]
    sig_raw = _ema(macd_vals, signal)
    # map back
    sig_line: list[float | None] = [None] * len(macd_line)
    j = 0
    for i, v in enumerate(macd_line):
        if v is not None:
            sig_line[i] = sig_raw[j]
            j += 1
    histogram = [
        (m - s) if m is not None and s is not None else None
        for m, s in zip(macd_line, sig_line)
    ]
    return macd_line, sig_line, histogram


def _stoch(highs, lows, closes, k_period=14, d_period=3):
    """Stochastic %K and %D."""
    pct_k: list[float | None] = [None] * len(closes)
    for i in range(k_period - 1, len(closes)):
        lo = min(lows[i - k_period + 1: i + 1])
        hi = max(highs[i - k_period + 1: i + 1])
        if hi != lo:
            pct_k[i] = 100 * (closes[i] - lo) / (hi - lo)
        else:
            pct_k[i] = 50.0
    pct_d = _sma([v if v is not None else float('nan') for v in pct_k], d_period)
    return pct_k, pct_d


def _obv(closes, volumes):
    """On-Balance Volume."""
    obv_vals = [0.0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            obv_vals.append(obv_vals[-1] + volumes[i])
        elif closes[i] < closes[i - 1]:
            obv_vals.append(obv_vals[-1] - volumes[i])
        else:
            obv_vals.append(obv_vals[-1])
    return obv_vals


def _adx(highs, lows, closes, n=14):
    """ADX — trend strength (simplified Wilder)."""
    if len(closes) < n * 2:
        return [None] * len(closes)
    pdi, mdi, adx_out = [], [], [None] * len(closes)
    tr_buf, pdm_buf, mdm_buf = [], [], []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        pdm = max(highs[i] - highs[i - 1], 0) if highs[i] - highs[i - 1] > lows[i - 1] - lows[i] else 0
        mdm = max(lows[i - 1] - lows[i], 0) if lows[i - 1] - lows[i] > highs[i] - highs[i - 1] else 0
        tr_buf.append(tr); pdm_buf.append(pdm); mdm_buf.append(mdm)
    if len(tr_buf) < n:
        return adx_out
    atr14 = sum(tr_buf[:n]); pdi14 = sum(pdm_buf[:n]); mdi14 = sum(mdm_buf[:n])
    pdi.append(100 * pdi14 / atr14 if atr14 else 0)
    mdi.append(100 * mdi14 / atr14 if atr14 else 0)
    for i in range(n, len(tr_buf)):
        atr14 = atr14 - atr14 / n + tr_buf[i]
        pdi14 = pdi14 - pdi14 / n + pdm_buf[i]
        mdi14 = mdi14 - mdi14 / n + mdm_buf[i]
        pdi.append(100 * pdi14 / atr14 if atr14 else 0)
        mdi.append(100 * mdi14 / atr14 if atr14 else 0)
    # ADX = smooth of DX
    dx_list = [abs(p - m) / (p + m) * 100 if (p + m) > 0 else 0 for p, m in zip(pdi, mdi)]
    if len(dx_list) < n:
        return adx_out
    adx_val = sum(dx_list[:n]) / n
    offset = n  # offset into original closes array
    adx_out[offset + n] = adx_val
    for i in range(n, len(dx_list)):
        adx_val = (adx_val * (n - 1) + dx_list[i]) / n
        adx_out[offset + i] = adx_val
    return adx_out


def _bb(closes, n=20, std_mult=2.0):
    """Bollinger Bands — returns (upper, mid, lower)."""
    mid = _sma(closes, n)
    upper: list[float | None] = [None] * len(closes)
    lower: list[float | None] = [None] * len(closes)
    for i in range(n - 1, len(closes)):
        window = closes[i - n + 1: i + 1]
        avg = sum(window) / n
        std = (sum((x - avg) ** 2 for x in window) / n) ** 0.5
        upper[i] = avg + std_mult * std
        lower[i] = avg - std_mult * std
    return upper, mid, lower


def _tick_round(price: float) -> float:
    """Round to IDX tick size."""
    if price < 200:      return round(price)
    if price < 500:      return round(price / 2) * 2
    if price < 2000:     return round(price / 5) * 5
    if price < 5000:     return round(price / 25) * 25
    return round(price / 50) * 50


def _fib_levels(low: float, high: float) -> dict[str, float]:
    """Fibonacci retracement + extension levels."""
    rng = high - low
    return {
        "0":    high,
        "23.6": high - 0.236 * rng,
        "38.2": high - 0.382 * rng,
        "50.0": high - 0.500 * rng,
        "61.8": high - 0.618 * rng,
        "78.6": high - 0.786 * rng,
        "100":  low,
        "127.2": high + 0.272 * rng,
        "161.8": high + 0.618 * rng,
    }


# ─── Per-stock scoring engine ────────────────────────────────────────────────

def _score_stock(rows: list[dict], horizon: str) -> dict | None:
    """Score a single stock for a given horizon. Returns None if insufficient data."""
    cfg = HORIZONS[horizon]
    MIN_ROWS = 260  # need at least 1 year for SMA200

    if len(rows) < MIN_ROWS:
        return None

    # unpack time-series (oldest → newest)
    closes  = [_number(r["harga_penutupan"])   for r in rows]
    highs   = [_number(r["harga_tertinggi"])    for r in rows]
    lows    = [_number(r["harga_terendah"])     for r in rows]
    opens   = [_number(r["harga_pembukaan"])    for r in rows]
    volumes = [_number(r["volume"])              for r in rows]
    net_for = [_number(r["beli_asing"]) - _number(r["jual_asing"]) for r in rows]
    vals    = [_number(r["nilai_transaksi"])    for r in rows]

    n = len(closes)
    close = closes[-1]
    if close <= 0:
        return None

    # ── Compute indicators ───────────────────────────────────────────
    sma5   = _sma(closes, 5)
    sma10  = _sma(closes, 10)
    sma20  = _sma(closes, 20)
    sma50  = _sma(closes, 50)
    sma100 = _sma(closes, 100)
    sma200 = _sma(closes, 200)
    ema9   = _ema(closes, 9)
    ema21  = _ema(closes, 21)
    ema55  = _ema(closes, 55)

    rsi14 = _rsi(closes, 14)
    atr14 = _atr(highs, lows, closes, 14)

    macd_line, sig_line, macd_hist = _macd(closes)
    stoch_k, stoch_d  = _stoch(highs, lows, closes)
    bb_upper, bb_mid, bb_lower = _bb(closes, 20, 2.0)
    obv_vals = _obv(closes, volumes)
    adx_vals = _adx(highs, lows, closes, 14)

    # Last values (safe access)
    def last(lst, offset=0):
        idx = -(1 + offset)
        v = lst[idx] if lst and abs(idx) <= len(lst) else None
        return v if (v is not None and not (isinstance(v, float) and (v != v))) else None

    rsi   = last(rsi14)
    atr   = last(atr14)
    s200  = last(sma200)
    s100  = last(sma100)
    s50   = last(sma50)
    s20   = last(sma20)
    s5    = last(sma5)
    e9    = last(ema9)
    e21   = last(ema21)
    e55   = last(ema55)
    macd  = last(macd_line)
    msig  = last(sig_line)
    mhist = last(macd_hist)
    mhist1= last(macd_hist, 1)   # previous bar histogram
    stk   = last(stoch_k)
    std   = last(stoch_d)
    stk1  = last(stoch_k, 1)
    bbu   = last(bb_upper)
    bbl   = last(bb_lower)
    bbm   = last(bb_mid)
    adx   = last(adx_vals)
    obv_now  = obv_vals[-1]
    obv_prev = obv_vals[-6] if len(obv_vals) >= 6 else obv_vals[0]

    if atr is None or atr <= 0:
        return None

    atr_pct = atr / close * 100

    # Foreign flow
    nf_5d  = sum(net_for[-5:])
    nf_20d = sum(net_for[-20:])
    nf_60d = sum(net_for[-60:]) if n >= 60 else None
    nf_prev_5d = sum(net_for[-10:-5]) if n >= 10 else 0

    # Volume
    vol_avg20 = sum(volumes[-20:]) / 20
    vol_now   = volumes[-1]
    vol_ratio = vol_now / vol_avg20 if vol_avg20 > 0 else 1

    # Returns
    ret_1d  = (closes[-1] - closes[-2]) / closes[-2] * 100  if n >= 2  else 0
    ret_5d  = (closes[-1] - closes[-6]) / closes[-6] * 100  if n >= 6  else 0
    ret_20d = (closes[-1] - closes[-21]) / closes[-21] * 100 if n >= 21 else 0
    ret_60d = (closes[-1] - closes[-61]) / closes[-61] * 100 if n >= 61 else 0
    ret_250d= (closes[-1] - closes[-251]) / closes[-251] * 100 if n >= 251 else 0

    # 52-week range
    high_52w = max(highs[-252:]) if n >= 252 else max(highs)
    low_52w  = min(lows[-252:])  if n >= 252 else min(lows)
    pct_from_52w_high = (close - high_52w) / high_52w * 100
    pct_from_52w_low  = (close - low_52w)  / low_52w  * 100

    # ── Scoring per horizon ──────────────────────────────────────────
    score   = 0
    max_sc  = 0
    reasons = []
    indicators_out = {}

    def add(pts, cond, label):
        nonlocal score, max_sc
        max_sc += pts
        if cond:
            score += pts
            reasons.append(label)

    # ── SMA/EMA Trend ──
    if s200 is not None:
        add(10, close > s200, "Harga di atas SMA200")
        add(8,  s200 > 0 and (s100 or 0) > s200, "SMA100 > SMA200 (bullish alignment)")
    if s100 is not None:
        add(8, close > s100, "Harga di atas SMA100")
    if s50 is not None:
        add(8, close > s50, "Harga di atas SMA50")
        add(8, s50 is not None and s100 is not None and s50 > s100, "SMA50 > SMA100")
    if s20 is not None:
        add(6, close > s20, "Harga di atas SMA20")
        add(6, s20 is not None and s50 is not None and s20 > s50, "Golden Cross SMA20 > SMA50")
    if e9 is not None and e21 is not None:
        add(6, e9 > e21, "EMA9 > EMA21 (short-term bullish)")
    if e21 is not None and e55 is not None:
        add(6, e21 > e55, "EMA21 > EMA55 (medium-term bullish)")

    # ── MACD ──
    if macd is not None and msig is not None:
        add(10, macd > msig, "MACD di atas signal line")
        add(6,  macd > 0,    "MACD positif (bullish momentum)")
        if mhist is not None and mhist1 is not None:
            add(8, mhist > 0 and mhist > mhist1, "MACD histogram naik (momentum menguat)")
            add(8, mhist1 <= 0 < mhist,          "MACD histogram cross ke positif (beli)")

    # ── RSI ──
    if rsi is not None:
        indicators_out["rsi14"] = round(rsi, 1)
        if horizon in ("1d", "1w"):
            add(12, 35 <= rsi <= 55, "RSI oversold recovery (35-55)")
            add(8,  rsi < 40,        "RSI mendekati oversold (potensi reversal)")
        elif horizon in ("1m", "3m"):
            add(12, 45 <= rsi <= 65, "RSI di zona healthy uptrend (45-65)")
        else:
            add(10, 40 <= rsi <= 70, "RSI stabil (40-70)")
        add(6, rsi > 50 and ret_5d > 0, "RSI > 50 dengan momentum positif")

    # ── Stochastic ──
    if stk is not None and std is not None:
        indicators_out["stoch_k"] = round(stk, 1)
        if stk1 is not None:
            add(8, stk1 < 20 and stk > stk1 and stk > std, "Stochastic golden cross dari oversold")
        add(6, 20 <= stk <= 80 and stk > std, "Stochastic %K > %D (bullish)")

    # ── ADX ──
    if adx is not None:
        indicators_out["adx14"] = round(adx, 1)
        add(8, adx > 25, "ADX > 25 (trend kuat)")
        add(6, adx > 35, "ADX > 35 (trend sangat kuat)")

    # ── Bollinger Bands ──
    if bbl is not None and bbu is not None and bbm is not None:
        bb_pct = (close - bbl) / (bbu - bbl) * 100 if bbu != bbl else 50
        indicators_out["bb_pct"] = round(bb_pct, 1)
        if horizon in ("1d", "1w"):
            add(10, close <= bbl * 1.02, "Harga di dekat lower BB (potensi bounce)")
            add(6,  bb_pct < 20,         "Harga di bawah 20% BB (oversold area)")
        add(6, close > bbm and bb_pct > 50, "Harga di atas mid BB (bullish)")

    # ── Volume ──
    indicators_out["vol_ratio"] = round(vol_ratio, 2)
    if horizon in ("1d", "1w"):
        add(10, vol_ratio >= 1.5, "Volume 50%+ di atas rata-rata (konfirmasi kuat)")
        add(6,  vol_ratio >= 1.2, "Volume di atas rata-rata (konfirmasi)")
    elif horizon in ("1m", "3m"):
        add(8, vol_ratio >= 1.2, "Volume breakout")
    else:
        add(6, vol_ratio >= 1.0, "Volume normal")

    # ── OBV ──
    add(6, obv_now > obv_prev, "OBV naik (akumulasi volume)")

    # ── Foreign Flow ──
    indicators_out["net_foreign_5d"] = int(nf_5d)
    indicators_out["net_foreign_20d"] = int(nf_20d)
    if horizon in ("1d",):
        add(10, nf_5d > 0,  "Net foreign beli 5 hari terakhir")
        add(6,  nf_5d > nf_prev_5d, "Akselerasi net foreign beli")
    elif horizon in ("1w", "1m"):
        add(10, nf_5d > 0,  "Net foreign beli 5 hari terakhir")
        add(8,  nf_20d > 0, "Net foreign beli 20 hari terakhir")
    elif horizon in ("3m", "6m"):
        add(8, nf_20d > 0, "Net foreign positif 20 hari")
        if nf_60d is not None:
            add(10, nf_60d > 0, "Net foreign positif 60 hari (akumulasi kuat)")
    else:
        if nf_60d is not None:
            add(10, nf_60d > 0, "Net foreign positif 60 hari")

    # ── Momentum ──
    if horizon in ("1d",):
        add(8, ret_1d < -1.5 and vol_ratio > 1.2, "Pullback bervolume tinggi (reversal opportunity)")
        add(6, -3 < ret_1d < 0, "Minor dip (beli di weakness)")
    elif horizon in ("1w",):
        add(8, -5 < ret_5d < 2, "Pullback 5 hari (beli di weakness)")
        add(6, ret_5d > 0, "Momentum 5 hari positif")
    elif horizon in ("1m",):
        add(8, ret_20d > 0, "Momentum 20 hari positif")
    elif horizon in ("3m", "6m"):
        add(8, ret_60d > 0, "Momentum 60 hari positif")
    else:
        add(10, ret_250d > 0, "Momentum 1 tahun positif (trend panjang)")

    # ── 52-week position ──
    if horizon in ("6m", "1y", "gt1y"):
        # Long-term: prefer stocks near 52w low (value), not 52w high
        add(8, -40 < pct_from_52w_high < -5, "Harga masih jauh dari 52w high (potensi rebound)")
    elif horizon in ("1m", "3m"):
        add(6, pct_from_52w_high < -10, "Diskon dari high 52 minggu")

    # ── Liquidity filter ──
    avg_val = sum(vals[-20:]) / 20 if n >= 20 else vals[-1]
    if avg_val < 1e9:  # < 1 milyar/hari — terlalu illiquid
        score = score // 3  # heavy penalty

    # ── Final ─────────────────────────────────────────────────────────
    if max_sc == 0:
        return None

    raw_pct = score / max_sc * 100

    # Horizon-specific weight multipliers
    horizon_weights = {"1d": 1.0, "1w": 1.0, "1m": 1.0, "3m": 0.95, "6m": 0.9, "1y": 0.85, "gt1y": 0.80}
    final_score = min(round(raw_pct * horizon_weights[horizon]), 100)

    if final_score < 40:
        return None  # not worth showing

    # Signal label
    if final_score >= 78:
        signal = "STRONG BUY"
    elif final_score >= 63:
        signal = "BUY"
    elif final_score >= 50:
        signal = "SPECULATIVE BUY"
    else:
        signal = "WATCH"

    # ── TP / SL calculation ──────────────────────────────────────────
    tp_mult = cfg["tp_atr"]
    sl_mult = cfg["sl_atr"]

    # Primary: ATR-based
    tp_atr = close + atr * tp_mult
    sl_atr = close - atr * sl_mult

    # Refine SL: use SMA as dynamic support (don't go below key SMA)
    sl_ref = sl_atr
    if horizon == "1d" and s5 is not None and s5 < close:
        sl_ref = max(sl_atr, s5 * 0.995)
    elif horizon == "1w" and s20 is not None and s20 < close:
        sl_ref = max(sl_atr, s20 * 0.99)
    elif horizon == "1m" and s50 is not None and s50 < close:
        sl_ref = max(sl_atr, s50 * 0.98)
    elif horizon in ("3m",) and s100 is not None and s100 < close:
        sl_ref = max(sl_atr, s100 * 0.97)
    elif horizon in ("6m", "1y", "gt1y") and s200 is not None and s200 < close:
        sl_ref = max(sl_atr, s200 * 0.96)

    # Fibonacci TP refinement
    fib = _fib_levels(low_52w, high_52w)
    # Find nearest Fibonacci extension above current price
    fib_tp = None
    for lvl in ["127.2", "161.8"]:
        if fib[lvl] > close * 1.01:
            fib_tp = fib[lvl]
            break
    # Blend: 70% ATR, 30% Fibonacci (when available)
    if fib_tp is not None:
        tp_final = tp_atr * 0.7 + fib_tp * 0.3
    else:
        tp_final = tp_atr

    # Ensure minimum RR ratio of 1.5:1
    upside = tp_final - close
    downside = close - sl_ref
    if downside > 0 and upside / downside < 1.5:
        tp_final = close + downside * 2.0  # force RR to 2:1

    tp = _tick_round(tp_final)
    sl = _tick_round(sl_ref)

    # Ensure valid
    if tp <= close or sl >= close:
        return None

    upside_pct  = (tp - close) / close * 100
    downside_pct = (close - sl) / close * 100
    rr_ratio = round(upside_pct / downside_pct, 2) if downside_pct > 0 else 0

    if rr_ratio < 1.2:
        return None  # bad risk/reward

    # Detect trend label
    trend = "sideways"
    if s200 is not None and s100 is not None and s50 is not None:
        if close > s200 and s50 > s100 > s200:
            trend = "strong uptrend"
        elif close > s200 and s20 and close > s20:
            trend = "uptrend"
        elif close < s200 and s50 and close < s50:
            trend = "downtrend"
        elif close < s200:
            trend = "below MA200"

    # Foreign flow label
    ff_label = "neutral"
    if nf_20d > 0 and nf_5d > 0:
        ff_label = "accumulation"
    elif nf_20d < 0 and nf_5d < 0:
        ff_label = "distribution"
    elif nf_5d > 0:
        ff_label = "recent buying"

    # Volume trend
    vol_label = "normal"
    if vol_ratio >= 1.5:
        vol_label = "very high"
    elif vol_ratio >= 1.2:
        vol_label = "high"
    elif vol_ratio < 0.6:
        vol_label = "low"

    # BB position
    bb_pos = "middle"
    if bbl is not None and bbu is not None and bbu > bbl:
        bb_pct2 = (close - bbl) / (bbu - bbl) * 100
        if bb_pct2 < 25:
            bb_pos = "near lower band"
        elif bb_pct2 > 75:
            bb_pos = "near upper band"

    # Reason summary (top 3)
    top_reasons = reasons[:4]

    return {
        "score": final_score,
        "signal": signal,
        "close": close,
        "tp": tp,
        "sl": sl,
        "rr_ratio": rr_ratio,
        "upside_pct": round(upside_pct, 2),
        "downside_pct": round(downside_pct, 2),
        "atr": round(atr, 0),
        "atr_pct": round(atr_pct, 2),
        "rsi14": indicators_out.get("rsi14"),
        "macd_signal": "bullish" if (macd is not None and msig is not None and macd > msig) else "bearish",
        "adx14": indicators_out.get("adx14"),
        "stoch_k": indicators_out.get("stoch_k"),
        "vol_ratio": indicators_out.get("vol_ratio"),
        "bb_position": bb_pos,
        "trend": trend,
        "foreign_flow": ff_label,
        "volume_trend": vol_label,
        "net_foreign_5d": indicators_out.get("net_foreign_5d", 0),
        "net_foreign_20d": indicators_out.get("net_foreign_20d", 0),
        "ret_1d": round(ret_1d, 2),
        "ret_5d": round(ret_5d, 2),
        "ret_20d": round(ret_20d, 2),
        "pct_from_52w_high": round(pct_from_52w_high, 2),
        "pct_from_52w_low": round(pct_from_52w_low, 2),
        "reasons": top_reasons,
    }


# ─── Main public function ────────────────────────────────────────────────────

def get_screener_signals(horizon: str = "1w", limit: int = 20) -> dict:
    """Return top-N BUY signals for the given horizon — bulk-optimised."""
    global _SIGNALS_CACHE

    if horizon not in HORIZONS:
        raise ValueError(f"Horizon tidak valid: {horizon}")

    newest, signature = _data_signature()
    cache_key = f"{signature}|{horizon}|{limit}"
    with _CACHE_LOCK:
        if cache_key in _SIGNALS_CACHE:
            return _SIGNALS_CACHE[cache_key]

    cfg = HORIZONS[horizon]

    # ── Bulk fetch: single query for all eligible stocks ────────────────
    #   We need at least 260 rows per stock for meaningful TA.
    #   Pull ALL rows for eligible codes in ONE round-trip.
    ROWS_NEEDED = 260

    with _reader() as con:
        # Step 1: eligible codes (active, enough history, traded today)
        eligible_codes = [
            r[0] for r in con.execute("""
                SELECT kode_saham
                FROM ringkasan_saham_harian
                WHERE (tanggal_delisting IS NULL OR tanggal_delisting = '')
                GROUP BY kode_saham
                HAVING COUNT(*) >= ?
                   AND MAX(tanggal) = ?
                ORDER BY SUM(nilai_transaksi) DESC
                LIMIT 600
            """, (ROWS_NEEDED, newest)).fetchall()
        ]

        if not eligible_codes:
            return {"horizon": horizon, "horizon_label": cfg["label"],
                    "horizon_description": cfg["description"], "as_of": newest,
                    "total_screened": 0, "total_signals": 0, "count": 0, "items": []}

        # Step 2: bulk fetch all rows for eligible codes
        #   Split into batches of 100 to keep query length manageable
        all_rows: dict[str, list] = {}
        BATCH = 100
        for start in range(0, len(eligible_codes), BATCH):
            batch = eligible_codes[start: start + BATCH]
            ph = ",".join(["?" for _ in batch])
            rows_raw = con.execute(f"""
                SELECT kode_saham, tanggal, harga_pembukaan, harga_tertinggi, harga_terendah,
                       harga_penutupan, volume, nilai_transaksi, beli_asing, jual_asing,
                       nama_perusahaan
                FROM ringkasan_saham_harian
                WHERE kode_saham IN ({ph})
                ORDER BY kode_saham, tanggal ASC
            """, batch).fetchall()
            for r in rows_raw:
                all_rows.setdefault(r[0], []).append(dict(r))

    # ── Score each stock ────────────────────────────────────────────────
    results = []
    for code in eligible_codes:
        rows = all_rows.get(code)
        if not rows or len(rows) < ROWS_NEEDED:
            continue
        name = rows[-1].get("nama_perusahaan", code)
        try:
            res = _score_stock(rows, horizon)
        except Exception:
            continue
        if res is None:
            continue
        results.append({
            "code": code,
            "name": name,
            "as_of": rows[-1]["tanggal"],
            **res,
        })

    # Sort by score DESC, then RR ratio DESC
    results.sort(key=lambda x: (-x["score"], -x["rr_ratio"]))
    top = results[:limit]

    for i, r in enumerate(top):
        r["rank"] = i + 1

    payload = {
        "horizon": horizon,
        "horizon_label": cfg["label"],
        "horizon_description": cfg["description"],
        "as_of": newest,
        "total_screened": len(eligible_codes),
        "total_signals": len(results),
        "count": len(top),
        "items": top,
    }

    with _CACHE_LOCK:
        _SIGNALS_CACHE[cache_key] = payload

    return payload
