"""Read-only analytics for the IDX daily-summary table.

The module intentionally uses only Python's standard library.  A market-wide
feature snapshot is cached by the most recent trading date so the 1.3M-row
database is scanned only once per application process (and rebuilt whenever a
new trading date appears).
"""
from __future__ import annotations

import math
import re
import statistics
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Any, Iterator, Sequence

from db import connect


CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9.-]{0,14}$")
_CACHE_LOCK = threading.RLock()
_SNAPSHOT_CACHE: tuple[str, tuple[dict[str, Any], ...]] | None = None
_OVERVIEW_CACHE: tuple[str, dict[str, Any]] | None = None
_METHODOLOGY_CACHE: tuple[str, dict[str, Any]] | None = None
_CACHE_SIGNATURE: str | None = None
_STOCK_DETAIL_CACHE: OrderedDict[tuple[str, str, int], dict[str, Any]] = OrderedDict()
_STOCK_DETAIL_CACHE_LIMIT = 32
_BREADTH_CACHE: OrderedDict[tuple[str, int], dict[str, Any]] = OrderedDict()
_BREADTH_CACHE_LIMIT = 10
_ACCUMULATION_ALL_CACHE: dict[tuple[str, str], dict[str, Any]] = {}
_BROKER_DAILY_CACHE: dict[str, dict] = {}
_BROKER_MONTHLY_CACHE: dict[str, dict] = {}
_BROKER_MASTER_CACHE: dict[str, Any] | None = None

ANALYTICS_VERSION = "3.2.0"
SCORE_COMPONENTS = (
    {"key": "price_above_sma20", "label": "Harga di atas SMA20", "weight": 20},
    {"key": "sma20_above_sma50", "label": "SMA20 di atas SMA50", "weight": 20},
    {"key": "return_20d_positive", "label": "Return 20 sesi positif", "weight": 15},
    {"key": "return_1d_positive", "label": "Return harian positif", "weight": 10},
    {"key": "volume_confirmation", "label": "Kenaikan dengan turnover/volume ≥1,2×", "weight": 10},
    {"key": "foreign_net_positive", "label": "Net foreign 20 sesi positif", "weight": 15},
    {"key": "rsi_positive_zone", "label": "RSI14 berada di 50–70", "weight": 10},
)


@contextmanager
def _reader() -> Iterator[Any]:
    connection = connect()
    try:
        connection.execute("PRAGMA query_only = ON")
        yield connection
    finally:
        connection.close()


def invalidate_cache() -> None:
    """Clear in-memory analytics after a database synchronization finishes."""
    global _SNAPSHOT_CACHE, _OVERVIEW_CACHE, _METHODOLOGY_CACHE, _CACHE_SIGNATURE, _BROKER_MASTER_CACHE
    with _CACHE_LOCK:
        _SNAPSHOT_CACHE = None
        _OVERVIEW_CACHE = None
        _METHODOLOGY_CACHE = None
        _CACHE_SIGNATURE = None
        _BROKER_MASTER_CACHE = None
        _STOCK_DETAIL_CACHE.clear()
        _BREADTH_CACHE.clear()
        _ACCUMULATION_ALL_CACHE.clear()
        _BROKER_DAILY_CACHE.clear()
        _BROKER_MONTHLY_CACHE.clear()


def _number(value: Any) -> float:
    try:
        result = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return result if math.isfinite(result) else 0.0


def _rounded(value: float | None, digits: int = 4) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(value, digits)


def _average(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _ratio(current: float, average: float | None) -> float | None:
    return current / average if average and average > 0 else None


def _return(closes: Sequence[float], sessions: int) -> float | None:
    if len(closes) <= sessions or closes[-sessions - 1] <= 0:
        return None
    return (closes[-1] / closes[-sessions - 1] - 1) * 100


def _adjusted_price_series(rows: Sequence[Any]) -> tuple[list[float], list[dict[str, Any]]]:
    """Backward-adjust closes using the official IDX previous/reference price.

    This keeps technical windows continuous across stock splits and other
    corporate actions while anchoring the latest adjusted close to the actual
    latest traded close.
    """
    if not rows:
        return [], []
    raw_closes = [_number(row["harga_penutupan"]) for row in rows]
    adjusted = [0.0] * len(rows)
    adjusted[-1] = raw_closes[-1]
    events: list[dict[str, Any]] = []
    for index in range(len(rows) - 1, 0, -1):
        close = raw_closes[index]
        reference = _number(rows[index]["sebelumnya"])
        previous_raw = raw_closes[index - 1]
        if reference <= 0:
            reference = previous_raw
        factor = close / reference if close > 0 and reference > 0 else 1.0
        adjusted[index - 1] = adjusted[index] / factor if factor > 0 else adjusted[index]
        if reference > 0 and previous_raw > 0:
            reference_gap = reference / previous_raw - 1
            if abs(reference_gap) >= 0.05:
                events.append({
                    "date": str(rows[index]["tanggal"]),
                    "previous_raw_close": _rounded(previous_raw, 2),
                    "official_reference": _rounded(reference, 2),
                    "reference_gap_pct": _rounded(reference_gap * 100, 4),
                })
    events.reverse()
    return adjusted, events


def _sma(closes: Sequence[float], sessions: int) -> float | None:
    if len(closes) < sessions:
        return None
    return sum(closes[-sessions:]) / sessions


def _rsi(closes: Sequence[float], sessions: int = 14) -> float | None:
    if len(closes) <= sessions:
        return None
    changes = [closes[index] - closes[index - 1] for index in range(len(closes) - sessions, len(closes))]
    gains = sum(max(change, 0) for change in changes) / sessions
    losses = sum(max(-change, 0) for change in changes) / sessions
    if gains == 0 and losses == 0:
        return 50.0
    if losses == 0:
        return 100.0
    relative_strength = gains / losses
    return 100 - (100 / (1 + relative_strength))


def _atr_percent(rows: Sequence[Any], sessions: int = 14) -> float | None:
    if len(rows) < sessions:
        return None
    true_range_percentages: list[float] = []
    for index in range(len(rows) - sessions, len(rows)):
        close = _number(rows[index]["harga_penutupan"])
        reference = _number(rows[index]["sebelumnya"])
        if reference <= 0 and index > 0:
            reference = _number(rows[index - 1]["harga_penutupan"])
        high = _number(rows[index]["harga_tertinggi"]) or close
        low = _number(rows[index]["harga_terendah"]) or close
        if reference > 0:
            true_range = max(high - low, abs(high - reference), abs(low - reference))
            true_range_percentages.append(true_range / reference * 100)
    return _average(true_range_percentages) if len(true_range_percentages) == sessions else None


def _volatility(closes: Sequence[float], sessions: int = 20) -> float | None:
    if len(closes) <= sessions:
        return None
    window = closes[-sessions - 1:]
    returns = [math.log(window[index] / window[index - 1]) for index in range(1, len(window)) if window[index] > 0 and window[index - 1] > 0]
    if len(returns) < 2:
        return None
    return statistics.pstdev(returns) * math.sqrt(252) * 100


def _maximum_drawdown(closes: Sequence[float], sessions: int = 60) -> float | None:
    window = list(closes[-sessions:])
    if len(window) < 2:
        return None
    peak = window[0]
    drawdown = 0.0
    for close in window:
        peak = max(peak, close)
        if peak > 0:
            drawdown = min(drawdown, close / peak - 1)
    return drawdown * 100


def _window_net(rows: Sequence[Any], sessions: int) -> float | None:
    if len(rows) < sessions:
        return None
    window = rows[-sessions:]
    return sum(_number(row["beli_asing"]) - _number(row["jual_asing"]) for row in window)


def _foreign_consistency(rows: Sequence[Any], sessions: int = 20) -> float | None:
    if len(rows) < sessions:
        return None
    window = rows[-sessions:]
    active = [row for row in window if _number(row["beli_asing"]) + _number(row["jual_asing"]) > 0]
    if not active:
        return None
    positives = sum(1 for row in active if _number(row["beli_asing"]) - _number(row["jual_asing"]) > 0)
    return positives / len(active) * 100


def _foreign_activity_counts(rows: Sequence[Any], sessions: int) -> dict[str, int] | None:
    if len(rows) < sessions:
        return None
    window = rows[-sessions:]
    nets = [_number(row["beli_asing"]) - _number(row["jual_asing"]) for row in window]
    return {
        "positive": sum(value > 0 for value in nets),
        "negative": sum(value < 0 for value in nets),
        "neutral": sum(value == 0 for value in nets),
        "sessions": sessions,
    }


def _foreign_value_estimate(rows: Sequence[Any], sessions: int) -> float | None:
    if len(rows) < sessions:
        return None
    return sum(
        (_number(row["beli_asing"]) - _number(row["jual_asing"])) * _number(row["harga_penutupan"])
        for row in rows[-sessions:]
    )


def _price_impact(rows: Sequence[Any], sessions: int = 20) -> float | None:
    if len(rows) < sessions:
        return None
    window = rows[-sessions:]
    impacts: list[float] = []
    for row in window:
        reference = _number(row["sebelumnya"])
        close = _number(row["harga_penutupan"])
        value_billions = _number(row["nilai_transaksi"]) / 1_000_000_000
        if reference > 0 and value_billions > 0:
            impacts.append(abs(close / reference - 1) * 100 / value_billions)
    return _average(impacts) if impacts else None


def _setup_score_detail(metrics: dict[str, Any]) -> tuple[float | None, list[dict[str, Any]]]:
    tests = {
        "price_above_sma20": (
            metrics["sma20"] is not None,
            metrics["sma20"] is not None and metrics["close"] > metrics["sma20"],
        ),
        "sma20_above_sma50": (
            metrics["sma20"] is not None and metrics["sma50"] is not None,
            metrics["sma20"] is not None and metrics["sma50"] is not None and metrics["sma20"] > metrics["sma50"],
        ),
        "return_20d_positive": (
            metrics["return_20d"] is not None,
            metrics["return_20d"] is not None and metrics["return_20d"] > 0,
        ),
        "return_1d_positive": (
            metrics["return_1d"] is not None,
            metrics["return_1d"] is not None and metrics["return_1d"] > 0,
        ),
        "volume_confirmation": (
            metrics["activity_confirmation_ratio"] is not None and metrics["return_1d"] is not None,
            metrics["activity_confirmation_ratio"] is not None and metrics["activity_confirmation_ratio"] >= 1.2 and (metrics["return_1d"] or 0) > 0,
        ),
        "foreign_net_positive": (
            metrics["foreign_net_20d"] is not None,
            metrics["foreign_net_20d"] is not None and metrics["foreign_net_20d"] > 0,
        ),
        "rsi_positive_zone": (
            metrics["rsi14"] is not None,
            metrics["rsi14"] is not None and 50 <= metrics["rsi14"] <= 70,
        ),
    }
    components: list[dict[str, Any]] = []
    for definition in SCORE_COMPONENTS:
        available, passed = tests[definition["key"]]
        components.append({
            **definition,
            "available": available,
            "passed": passed if available else False,
            "points": definition["weight"] if available and passed else 0,
        })
    denominator = sum(component["weight"] for component in components if component["available"])
    points = sum(component["points"] for component in components)
    score = float(points) if metrics.get("history_sessions", 0) >= 50 else None
    metrics["score_coverage_pct"] = _rounded(denominator, 2)
    return score, components


def _summarize_series(rows: Sequence[Any], required_date: str | None = None) -> dict[str, Any] | None:
    if not rows:
        return None
    latest = rows[-1]
    if required_date and latest["tanggal"] != required_date:
        return None
    raw_closes = [_number(row["harga_penutupan"]) for row in rows]
    closes, adjustment_events = _adjusted_price_series(rows)
    if not closes or not closes[-1]:
        return None

    previous_rows = rows[-21:-1] if len(rows) >= 21 else []
    recent_rows = rows[-20:]
    avg_volume = _average([_number(row["volume"]) for row in previous_rows])
    avg_value = _average([_number(row["nilai_transaksi"]) for row in previous_rows])
    avg_frequency = _average([_number(row["frekuensi"]) for row in previous_rows])
    current_volume = _number(latest["volume"])
    current_value = _number(latest["nilai_transaksi"])
    current_frequency = _number(latest["frekuensi"])
    sma20 = _sma(closes, 20)
    sma50 = _sma(closes, 50)
    sma200 = _sma(closes, 200)
    rsi14 = _rsi(closes)
    return_1d = _return(closes, 1)
    adjustment_scales = [adjusted / raw if raw > 0 else 1.0 for adjusted, raw in zip(closes, raw_closes)]
    previous_20_highs = [
        (_number(rows[index]["harga_tertinggi"]) or raw_closes[index]) * adjustment_scales[index]
        for index in range(max(0, len(rows) - 21), len(rows) - 1)
    ]
    previous_20_lows = [
        (_number(rows[index]["harga_terendah"]) or raw_closes[index]) * adjustment_scales[index]
        for index in range(max(0, len(rows) - 21), len(rows) - 1)
    ]
    current_close = closes[-1]
    shares_listed = _number(latest["saham_tercatat"])
    previous_turnovers = [
        _number(row["volume"]) / _number(row["saham_tercatat"])
        for row in previous_rows if _number(row["saham_tercatat"]) > 0
    ]
    avg_turnover = _average(previous_turnovers) if len(previous_turnovers) == 20 else None
    current_turnover = current_volume / shares_listed if shares_listed > 0 else None
    turnover_activity_ratio = _ratio(current_turnover or 0, avg_turnover)
    foreign_net_20 = _window_net(rows, 20)
    total_volume_20 = sum(_number(row["volume"]) for row in recent_rows)
    recent_rows_5 = rows[-5:]
    foreign_net_5 = _window_net(rows, 5)
    total_volume_5 = sum(_number(row["volume"]) for row in recent_rows_5)

    metrics: dict[str, Any] = {
        "code": str(latest["kode_saham"]),
        "name": str(latest["nama_perusahaan"] or latest["kode_saham"]),
        "date": str(latest["tanggal"]),
        "close": _rounded(current_close, 2),
        "raw_close": _rounded(raw_closes[-1], 2),
        "prices_adjusted": True,
        "history_sessions": len(rows),
        "change": _rounded(_number(latest["perubahan"]), 2),
        "return_1d": _rounded(return_1d),
        "return_5d": _rounded(_return(closes, 5)),
        "return_20d": _rounded(_return(closes, 20)),
        "return_60d": _rounded(_return(closes, 60)),
        "sma20": _rounded(sma20, 2),
        "sma50": _rounded(sma50, 2),
        "sma200": _rounded(sma200, 2),
        "distance_sma20": _rounded((current_close / sma20 - 1) * 100 if sma20 else None),
        "distance_sma50": _rounded((current_close / sma50 - 1) * 100 if sma50 else None),
        "rsi14": _rounded(rsi14, 2),
        "atr14_pct": _rounded(_atr_percent(rows), 4),
        "volatility20": _rounded(_volatility(closes), 4),
        "max_drawdown60": _rounded(_maximum_drawdown(closes), 4),
        "volume": int(current_volume),
        "value": _rounded(current_value, 2),
        "frequency": int(current_frequency),
        "avg_volume_20": _rounded(avg_volume, 2),
        "avg_value_20": _rounded(avg_value, 2),
        "avg_frequency_20": _rounded(avg_frequency, 2),
        "volume_ratio": _rounded(_ratio(current_volume, avg_volume), 4),
        "turnover_activity_ratio": _rounded(turnover_activity_ratio, 4),
        "activity_confirmation_ratio": _rounded(turnover_activity_ratio if turnover_activity_ratio is not None else _ratio(current_volume, avg_volume), 4),
        "value_ratio": _rounded(_ratio(current_value, avg_value), 4),
        "frequency_ratio": _rounded(_ratio(current_frequency, avg_frequency), 4),
        "active_days_20": _rounded(sum(1 for row in recent_rows if _number(row["volume"]) > 0) / 20 * 100 if len(recent_rows) == 20 else None, 2),
        "foreign_net_1d": _rounded(_number(latest["beli_asing"]) - _number(latest["jual_asing"]), 2),
        "foreign_net_5d": _rounded(foreign_net_5, 2),
        "foreign_net_20d": _rounded(foreign_net_20, 2),
        "foreign_value_estimate_5d": _rounded(_foreign_value_estimate(rows, 5), 2),
        "foreign_value_estimate_20d": _rounded(_foreign_value_estimate(rows, 20), 2),
        "foreign_consistency_5": _rounded(_foreign_consistency(rows, 5), 2),
        "foreign_consistency_20": _rounded(_foreign_consistency(rows), 2),
        "foreign_days_5": _foreign_activity_counts(rows, 5),
        "foreign_days_20": _foreign_activity_counts(rows, 20),
        "foreign_net_volume_pct_5": _rounded(foreign_net_5 / total_volume_5 * 100 if foreign_net_5 is not None and total_volume_5 else None, 5),
        "foreign_net_volume_pct_20": _rounded(foreign_net_20 / total_volume_20 * 100 if foreign_net_20 is not None and total_volume_20 else None, 5),
        "turnover_pct": _rounded(current_volume / shares_listed * 100 if shares_listed else None, 5),
        "price_impact_20": _rounded(_price_impact(rows), 6),
        "breakout_20": bool(len(previous_20_highs) >= 20 and current_close > max(previous_20_highs)),
        "new_low_20": bool(len(previous_20_lows) >= 20 and current_close < min(previous_20_lows)),
        "high_20": _rounded(max(previous_20_highs), 2) if previous_20_highs else None,
        "low_20": _rounded(min(previous_20_lows), 2) if previous_20_lows else None,
        "bid": _rounded(_number(latest["penawaran_beli"]), 2),
        "offer": _rounded(_number(latest["penawaran_jual"]), 2),
        "bid_volume": int(_number(latest["volume_penawaran_beli"])),
        "offer_volume": int(_number(latest["volume_penawaran_jual"])),
        "reference_adjustment_count": len(adjustment_events),
        "reference_adjustments": adjustment_events[-5:],
    }
    if metrics["bid"] and metrics["offer"] and metrics["bid"] + metrics["offer"] > 0:
        metrics["spread_pct"] = _rounded((metrics["offer"] - metrics["bid"]) / ((metrics["offer"] + metrics["bid"]) / 2) * 100, 5)
    else:
        metrics["spread_pct"] = None
    depth_total = metrics["bid_volume"] + metrics["offer_volume"]
    metrics["depth_imbalance"] = _rounded((metrics["bid_volume"] - metrics["offer_volume"]) / depth_total * 100 if depth_total else None, 4)

    if sma20 is not None and sma50 is not None and current_close > sma20 > sma50:
        metrics["trend"] = "Uptrend"
    elif sma20 is not None and sma50 is not None and current_close < sma20 < sma50:
        metrics["trend"] = "Downtrend"
    else:
        metrics["trend"] = "Sideways"
    if rsi14 is None:
        metrics["momentum"] = "Belum cukup data"
    elif rsi14 >= 70:
        metrics["momentum"] = "Overbought"
    elif rsi14 <= 30:
        metrics["momentum"] = "Oversold"
    elif rsi14 >= 50:
        metrics["momentum"] = "Positif"
    else:
        metrics["momentum"] = "Negatif"
    metrics["liquidity"] = "Tinggi" if (avg_value or 0) >= 50_000_000_000 else "Menengah" if (avg_value or 0) >= 10_000_000_000 else "Rendah"
    setup_score, score_components = _setup_score_detail(metrics)
    metrics["setup_score"] = _rounded(setup_score, 2)
    metrics["score_components"] = score_components
    tags: list[str] = []
    if metrics["breakout_20"]:
        tags.append("Breakout 20D")
    if (metrics["activity_confirmation_ratio"] or 0) >= 1.5:
        tags.append("Activity spike")
    if (metrics["value_ratio"] or 0) >= 1.5:
        tags.append("Value spike")
    if (metrics["foreign_net_20d"] or 0) > 0 and (metrics["foreign_consistency_20"] or 0) >= 60:
        tags.append("Akumulasi asing")
    if rsi14 is not None and rsi14 <= 30:
        tags.append("Oversold")
    metrics["tags"] = tags
    coverage_fields = (
        "return_20d", "return_60d", "sma20", "sma50", "sma200",
        "rsi14", "atr14_pct", "volatility20", "foreign_net_20d",
    )
    metrics["indicator_coverage"] = _rounded(
        sum(metrics[field] is not None for field in coverage_fields) / len(coverage_fields) * 100,
        2,
    )
    metrics["latest_active"] = current_volume > 0
    quality_flags: list[str] = []
    if current_volume <= 0:
        quality_flags.append("Tidak bertransaksi pada sesi terbaru")
    if len(rows) < 200:
        quality_flags.append("Riwayat belum cukup untuk SMA200")
    if adjustment_events:
        quality_flags.append(f"{len(adjustment_events)} penyesuaian reference price diterapkan")
    if (metrics["active_days_20"] or 0) < 60:
        quality_flags.append("Aktivitas perdagangan tidak konsisten")
    metrics["quality_flags"] = quality_flags
    return metrics


def latest_date() -> str:
    with _reader() as connection:
        value = connection.execute("SELECT MAX(tanggal) FROM ringkasan_saham_harian").fetchone()[0]
    if not value:
        raise LookupError("Tabel ringkasan_saham_harian belum berisi data.")
    return str(value)


def _data_signature() -> tuple[str, str]:
    with _reader() as connection:
        newest = connection.execute("SELECT MAX(tanggal) FROM ringkasan_saham_harian").fetchone()[0]
        if not newest:
            raise LookupError("Tabel ringkasan_saham_harian belum berisi data.")
        latest_meta = connection.execute(
            "SELECT COUNT(*), COALESCE(MAX(diimpor_pada), '') FROM ringkasan_saham_harian WHERE tanggal = ?",
            (newest,),
        ).fetchone()
        sync_updated = connection.execute(
            "SELECT COALESCE(MAX(updated_at), '') FROM idx_daily_sync_log"
        ).fetchone()[0]
    signature = f"{newest}|{latest_meta[0]}|{latest_meta[1]}|{sync_updated}"
    return str(newest), signature


def _market_snapshot() -> tuple[str, tuple[dict[str, Any], ...]]:
    global _SNAPSHOT_CACHE, _OVERVIEW_CACHE, _METHODOLOGY_CACHE, _CACHE_SIGNATURE
    newest, signature = _data_signature()
    if _SNAPSHOT_CACHE and _CACHE_SIGNATURE == signature:
        return _SNAPSHOT_CACHE
    with _CACHE_LOCK:
        if _SNAPSHOT_CACHE and _CACHE_SIGNATURE == signature:
            return _SNAPSHOT_CACHE
        with _reader() as connection:
            trading_dates = [str(row[0]) for row in connection.execute(
                "SELECT DISTINCT tanggal FROM ringkasan_saham_harian ORDER BY tanggal DESC LIMIT 261"
            )]
            if not trading_dates:
                raise LookupError("Tabel ringkasan_saham_harian belum berisi data.")
            cutoff = trading_dates[-1]
            cursor = connection.execute(
                """SELECT r.tanggal, r.kode_saham,
                          COALESCE(NULLIF(s.company_name, ''), NULLIF(r.nama_perusahaan, ''), r.kode_saham) AS nama_perusahaan,
                          r.sebelumnya, r.harga_tertinggi, r.harga_terendah, r.harga_penutupan,
                          r.perubahan, r.volume, r.nilai_transaksi, r.frekuensi,
                          r.penawaran_jual, r.volume_penawaran_jual,
                          r.penawaran_beli, r.volume_penawaran_beli,
                          r.saham_tercatat, r.beli_asing, r.jual_asing
                   FROM ringkasan_saham_harian AS r
                   LEFT JOIN idx_stocks AS s ON s.code = r.kode_saham
                   WHERE r.tanggal >= ? AND r.harga_penutupan > 0
                   ORDER BY r.kode_saham, r.tanggal""",
                (cutoff,),
            )
            features: list[dict[str, Any]] = []
            current_code: str | None = None
            series: list[Any] = []
            for row in cursor:
                code = str(row["kode_saham"])
                if current_code is not None and code != current_code:
                    summary = _summarize_series(series, newest)
                    if summary:
                        features.append(summary)
                    series = []
                current_code = code
                series.append(row)
            summary = _summarize_series(series, newest)
            if summary:
                features.append(summary)
        _SNAPSHOT_CACHE = (newest, tuple(features))
        _CACHE_SIGNATURE = signature
        _OVERVIEW_CACHE = None
        _METHODOLOGY_CACHE = None
        return _SNAPSHOT_CACHE


def screener() -> dict[str, Any]:
    newest, items = _market_snapshot()
    return {
        "as_of": newest,
        "count": len(items),
        "items": items,
        "methodology": {
            "version": ANALYTICS_VERSION,
            "price": "Indikator memakai backward-adjusted close dari reference price resmi IDX; latest close tetap harga aktual.",
            "activity_ratios": "Dibanding rata-rata 20 sesi sebelumnya; konfirmasi skor memprioritaskan turnover agar lebih tahan stock split.",
            "foreign": "Beli asing dikurangi jual asing dalam unit saham; ranking lintas saham dinormalisasi terhadap volume.",
            "score": "Condition Score v1 memakai denominator tetap 100, minimum 50 sesi, belum backtested, dan bukan rekomendasi.",
        },
    }


def overview() -> dict[str, Any]:
    global _OVERVIEW_CACHE, _CACHE_SIGNATURE
    newest, items_tuple = _market_snapshot()
    cache_key = _CACHE_SIGNATURE or newest
    if _OVERVIEW_CACHE and _OVERVIEW_CACHE[0] == cache_key:
        return _OVERVIEW_CACHE[1]
    with _CACHE_LOCK:
        if _OVERVIEW_CACHE and _OVERVIEW_CACHE[0] == cache_key:
            return _OVERVIEW_CACHE[1]
        items = list(items_tuple)
        with _reader() as connection:
            row = connection.execute(
                """SELECT COUNT(*) AS listed,
                          SUM(CASE WHEN COALESCE(volume, 0) > 0 THEN 1 ELSE 0 END) AS active,
                          SUM(CASE WHEN perubahan > 0 THEN 1 ELSE 0 END) AS advancers,
                          SUM(CASE WHEN perubahan < 0 THEN 1 ELSE 0 END) AS decliners,
                          SUM(CASE WHEN perubahan = 0 OR perubahan IS NULL THEN 1 ELSE 0 END) AS unchanged,
                          SUM(CASE WHEN (perubahan = 0 OR perubahan IS NULL)
                                    AND COALESCE(volume, 0) > 0 THEN 1 ELSE 0 END) AS unchanged_active,
                          SUM(COALESCE(volume, 0)) AS volume,
                          SUM(COALESCE(nilai_transaksi, 0)) AS value,
                          SUM(COALESCE(frekuensi, 0)) AS frequency,
                          SUM(COALESCE(beli_asing, 0)) AS foreign_buy,
                          SUM(COALESCE(jual_asing, 0)) AS foreign_sell
                   FROM ringkasan_saham_harian WHERE tanggal = ? AND harga_penutupan > 0""",
                (newest,),
            ).fetchone()
        active_returns = [item["return_1d"] for item in items if item["volume"] > 0 and item["return_1d"] is not None]
        liquid = [item for item in items if (item["avg_value_20"] or 0) >= 1_000_000_000 and item["volume"] > 0]
        above20 = [item for item in items if item["sma20"] is not None and item["volume"] > 0]
        above50 = [item for item in items if item["sma50"] is not None and item["volume"] > 0]
        advance_ratio = _number(row["advancers"]) / max(_number(row["active"]), 1) * 100
        above20_pct = sum(1 for item in above20 if item["close"] > item["sma20"]) / max(len(above20), 1) * 100
        above50_pct = sum(1 for item in above50 if item["close"] > item["sma50"]) / max(len(above50), 1) * 100
        breadth_score = advance_ratio * 0.4 + above20_pct * 0.35 + above50_pct * 0.25

        def compact_item(item: dict[str, Any]) -> dict[str, Any]:
            return {key: item.get(key) for key in ("code", "name", "close", "return_1d", "return_20d", "value", "volume_ratio", "foreign_net_20d", "setup_score")}

        payload = {
            "as_of": newest,
            "market": {
                "listed": int(_number(row["listed"])),
                "active": int(_number(row["active"])),
                "inactive": int(_number(row["listed"]) - _number(row["active"])),
                "advancers": int(_number(row["advancers"])),
                "decliners": int(_number(row["decliners"])),
                "unchanged": int(_number(row["unchanged"])),
                "unchanged_active": int(_number(row["unchanged_active"])),
                "volume": int(_number(row["volume"])),
                "value": _rounded(_number(row["value"]), 2),
                "frequency": int(_number(row["frequency"])),
                "foreign_buy": _rounded(_number(row["foreign_buy"]), 2),
                "foreign_sell": _rounded(_number(row["foreign_sell"]), 2),
                "foreign_net": _rounded(_number(row["foreign_buy"]) - _number(row["foreign_sell"]), 2),
                "median_return": _rounded(statistics.median(active_returns), 4) if active_returns else None,
                "above_sma20_pct": _rounded(above20_pct, 2),
                "above_sma50_pct": _rounded(above50_pct, 2),
                "new_high_20": sum(1 for item in items if item["breakout_20"]),
                "new_low_20": sum(1 for item in items if item["new_low_20"]),
                "breadth_score": _rounded(breadth_score, 2),
                "breadth_label": "Kuat" if breadth_score >= 65 else "Positif" if breadth_score >= 55 else "Netral" if breadth_score >= 45 else "Lemah" if breadth_score >= 35 else "Sangat lemah",
                "breadth_score_components": {
                    "advance_ratio": _rounded(advance_ratio, 2),
                    "above_sma20": _rounded(above20_pct, 2),
                    "above_sma50": _rounded(above50_pct, 2),
                    "weights": {"advance_ratio": 40, "above_sma20": 35, "above_sma50": 25},
                },
            },
            "top_gainers": [compact_item(item) for item in sorted(liquid, key=lambda item: item["return_1d"] if item["return_1d"] is not None else -math.inf, reverse=True)[:8]],
            "top_losers": [compact_item(item) for item in sorted(liquid, key=lambda item: item["return_1d"] if item["return_1d"] is not None else math.inf)[:8]],
            "most_active": [compact_item(item) for item in sorted(items, key=lambda item: item["value"] or 0, reverse=True)[:8]],
        }
        _OVERVIEW_CACHE = (cache_key, payload)
        return payload


def breadth(days: int = 90) -> dict[str, Any]:
    days = max(20, min(int(days), 2000))
    newest, signature = _data_signature()
    cache_key = (signature, days)
    with _CACHE_LOCK:
        cached = _BREADTH_CACHE.get(cache_key)
        if cached is not None:
            _BREADTH_CACHE.move_to_end(cache_key)
            return cached
    with _reader() as connection:
        dates = [str(row[0]) for row in connection.execute(
            "SELECT DISTINCT tanggal FROM ringkasan_saham_harian ORDER BY tanggal DESC LIMIT ?", (days,)
        )]
        if not dates:
            return {"as_of": newest, "days": days, "items": []}
        rows = connection.execute(
            """SELECT tanggal,
                      SUM(CASE WHEN COALESCE(volume, 0) > 0 THEN 1 ELSE 0 END) AS active,
                      SUM(CASE WHEN perubahan > 0 THEN 1 ELSE 0 END) AS advancers,
                      SUM(CASE WHEN perubahan < 0 THEN 1 ELSE 0 END) AS decliners,
                      SUM(CASE WHEN perubahan = 0 OR perubahan IS NULL THEN 1 ELSE 0 END) AS unchanged,
                      SUM(CASE WHEN (perubahan = 0 OR perubahan IS NULL)
                                AND COALESCE(volume, 0) > 0 THEN 1 ELSE 0 END) AS unchanged_active,
                      SUM(COALESCE(nilai_transaksi, 0)) AS value,
                      SUM(COALESCE(volume, 0)) AS volume,
                      SUM(COALESCE(frekuensi, 0)) AS frequency,
                      SUM(COALESCE(beli_asing, 0) - COALESCE(jual_asing, 0)) AS foreign_net
               FROM ringkasan_saham_harian
               WHERE tanggal >= ? AND harga_penutupan > 0
               GROUP BY tanggal ORDER BY tanggal""",
            (dates[-1],),
        ).fetchall()
    ad_line = 0
    items: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        advancers = int(_number(row["advancers"]))
        decliners = int(_number(row["decliners"]))
        if index > 0:
            ad_line += advancers - decliners
        active = int(_number(row["active"]))
        unchanged_active = int(_number(row["unchanged_active"]))
        items.append({
            "date": str(row["tanggal"]),
            "active": active,
            "advancers": advancers,
            "decliners": decliners,
            "unchanged": int(_number(row["unchanged"])),
            "unchanged_active": unchanged_active,
            "inactive": max(0, int(_number(row["unchanged"])) - unchanged_active),
            "breadth_pct": _rounded(advancers / max(advancers + decliners, 1) * 100, 2),
            "ad_line": ad_line,
            "value": _rounded(_number(row["value"]), 2),
            "volume": int(_number(row["volume"])),
            "frequency": int(_number(row["frequency"])),
            "foreign_net": _rounded(_number(row["foreign_net"]), 2),
        })
    payload = {"as_of": newest, "days": len(items), "requested_days": days, "items": items}
    with _CACHE_LOCK:
        _BREADTH_CACHE[cache_key] = payload
        _BREADTH_CACHE.move_to_end(cache_key)
        while len(_BREADTH_CACHE) > _BREADTH_CACHE_LIMIT:
            _BREADTH_CACHE.popitem(last=False)
    return payload


def flow_liquidity(window: int = 20, limit: int = 30, history_days: int = 252) -> dict[str, Any]:
    window = 5 if int(window) == 5 else 20
    limit = max(10, min(int(limit), 100))
    history_days = max(60, min(int(history_days), 2000))
    newest, source = _market_snapshot()
    net_key = f"foreign_net_{window}d"
    return_key = f"return_{window}d"

    def item(row: dict[str, Any]) -> dict[str, Any]:
        payload = {key: row.get(key) for key in (
            "code", "name", "close", "return_1d", return_key, net_key,
            "avg_frequency_20", "volume_ratio", "value_ratio", "frequency_ratio",
            "turnover_pct", "turnover_activity_ratio", "price_impact_20", "setup_score",
        )}
        activity_ratios = [_number(row.get(key)) for key in ("volume_ratio", "value_ratio", "frequency_ratio")]
        payload.update({
            "period_return": row.get(return_key),
            "foreign_net": row.get(net_key),
            "foreign_consistency": row.get(f"foreign_consistency_{window}"),
            "foreign_net_volume_pct": row.get(f"foreign_net_volume_pct_{window}"),
            "foreign_value_estimate": row.get(f"foreign_value_estimate_{window}d"),
            "avg_value_20": row.get("avg_value_20"),
            "activity_score": _rounded(math.exp(sum(math.log(value) for value in activity_ratios) / 3), 4) if all(value > 0 for value in activity_ratios) else None,
        })
        price_positive = _number(payload["period_return"]) >= 0
        flow_positive = _number(payload["foreign_net"]) >= 0
        payload["flow_context"] = (
            "Harga naik + flow masuk" if price_positive and flow_positive else
            "Harga turun + flow masuk" if not price_positive and flow_positive else
            "Harga naik + flow keluar" if price_positive else
            "Harga turun + flow keluar"
        )
        return payload

    flow_pct_key = f"foreign_net_volume_pct_{window}"
    eligible = [row for row in source if (row["avg_value_20"] or 0) >= 1_000_000_000]
    positive = sorted(
        [row for row in eligible if (row[flow_pct_key] or 0) > 0],
        key=lambda row: row[flow_pct_key] or 0,
        reverse=True,
    )[:limit]
    negative = sorted(
        [row for row in eligible if (row[flow_pct_key] or 0) < 0],
        key=lambda row: row[flow_pct_key] or 0,
    )[:limit]
    unusual = sorted(
        [
            row for row in eligible
            if all((row[key] or 0) > 0 for key in ("volume_ratio", "value_ratio", "frequency_ratio"))
            and math.prod(_number(row[key]) for key in ("volume_ratio", "value_ratio", "frequency_ratio")) ** (1 / 3) >= 1.2
        ],
        key=lambda row: math.exp(sum(math.log(max(_number(row[key]), 0.0001)) for key in ("volume_ratio", "value_ratio", "frequency_ratio")) / 3),
        reverse=True,
    )[:limit]
    liquid = sorted(eligible, key=lambda row: row["avg_value_20"] or 0, reverse=True)[:limit]
    market_flow = breadth(history_days)
    return {
        "as_of": newest,
        "window": window,
        "history_days": market_flow["days"],
        "history_from": market_flow["items"][0]["date"] if market_flow["items"] else None,
        "accumulation": [item(row) for row in positive],
        "distribution": [item(row) for row in negative],
        "unusual_activity": [item(row) for row in unusual],
        "most_liquid": [item(row) for row in liquid],
        "market_flow": market_flow["items"],
    }


def _validate_code(code: str) -> str:
    normalized = str(code or "").strip().upper()
    if not CODE_RE.fullmatch(normalized):
        raise ValueError("Kode saham tidak valid.")
    return normalized


def _ownership_snapshot(connection: Any, code: str) -> dict[str, Any]:
    dates = [str(row[0]) for row in connection.execute(
        "SELECT DISTINCT record_date FROM ownership_positions WHERE share_code = ? ORDER BY record_date DESC LIMIT 2",
        (code,),
    )]
    if not dates:
        return {"available": False, "current_date": None, "previous_date": None, "positions": [], "changes": []}
    current_date = dates[0]
    previous_date = dates[1] if len(dates) > 1 else None
    current = [dict(row) for row in connection.execute(
        """SELECT investor_name, classification, local_foreign, scripless + scrip AS shares, percentage
             FROM ownership_positions WHERE share_code = ? AND record_date = ?
             ORDER BY percentage DESC, shares DESC""",
        (code, current_date),
    )]
    previous: dict[str, dict[str, Any]] = {}
    if previous_date:
        previous = {str(row["investor_name"]).upper(): dict(row) for row in connection.execute(
            """SELECT investor_name, scripless + scrip AS shares, percentage
                 FROM ownership_positions WHERE share_code = ? AND record_date = ?""",
            (code, previous_date),
        )}
    changes = []
    current_names: set[str] = set()
    for row in current:
        normalized_name = str(row["investor_name"]).upper()
        current_names.add(normalized_name)
        old = previous.get(normalized_name)
        changes.append({
            "investor_name": row["investor_name"],
            "share_change": int(_number(row["shares"]) - _number(old["shares"] if old else 0)),
            "percentage_change": _rounded(_number(row["percentage"]) - _number(old["percentage"] if old else 0), 4),
            "status": "Aktif" if old else "Baru",
        })
    for normalized_name, old in previous.items():
        if normalized_name not in current_names:
            changes.append({
                "investor_name": old["investor_name"],
                "share_change": -int(_number(old["shares"])),
                "percentage_change": _rounded(-_number(old["percentage"]), 4),
                "status": "Keluar",
            })
    changes.sort(key=lambda row: abs(row["share_change"]), reverse=True)
    return {
        "available": True,
        "current_date": current_date,
        "previous_date": previous_date,
        "position_count": len(current),
        "concentration_pct": _rounded(sum(_number(row["percentage"]) for row in current), 4),
        "foreign_pct": _rounded(sum(_number(row["percentage"]) for row in current if row["local_foreign"] == "F"), 4),
        "positions": current[:10],
        "changes": changes[:10] if previous_date else [],
    }


def stock_detail(code: str, days: int = 260) -> dict[str, Any]:
    code = _validate_code(code)
    days = max(5, min(int(days), 2000))
    _, signature = _data_signature()
    cache_key = (signature, code, days)
    with _CACHE_LOCK:
        cached = _STOCK_DETAIL_CACHE.get(cache_key)
        if cached is not None:
            _STOCK_DETAIL_CACHE.move_to_end(cache_key)
            return cached
    started = time.perf_counter()
    with _reader() as connection:
        rows = list(reversed(connection.execute(
            """SELECT r.tanggal, r.kode_saham,
                      COALESCE(NULLIF(s.company_name, ''), NULLIF(r.nama_perusahaan, ''), r.kode_saham) AS nama_perusahaan,
                      r.sebelumnya, r.harga_pembukaan, r.harga_tertinggi, r.harga_terendah,
                      r.harga_penutupan, r.perubahan, r.volume, r.nilai_transaksi, r.frekuensi,
                      r.penawaran_jual, r.volume_penawaran_jual,
                      r.penawaran_beli, r.volume_penawaran_beli,
                      r.saham_tercatat, r.beli_asing, r.jual_asing
               FROM ringkasan_saham_harian AS r
               LEFT JOIN idx_stocks AS s ON s.code = r.kode_saham
               WHERE r.kode_saham = ? AND r.harga_penutupan > 0
               ORDER BY r.tanggal DESC LIMIT ?""",
            (code, days),
        ).fetchall()))
        if not rows:
            raise LookupError(f"Riwayat saham {code} tidak ditemukan.")
        master = connection.execute(
            "SELECT code, company_name, listing_date, shares, listing_board FROM idx_stocks WHERE code = ?", (code,)
        ).fetchone()
        ownership = _ownership_snapshot(connection, code)

    metrics = _summarize_series(rows)
    if not metrics:
        raise LookupError(f"Riwayat saham {code} tidak dapat dianalisis.")
    adjusted_closes, adjustment_events = _adjusted_price_series(rows)
    adjustment_dates = {event["date"] for event in adjustment_events}
    rolling_closes: list[float] = []
    history: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        raw_close = _number(row["harga_penutupan"])
        adjusted_close = adjusted_closes[index]
        scale = adjusted_close / raw_close if raw_close > 0 else 1.0
        rolling_closes.append(adjusted_close)
        raw_open = _number(row["harga_pembukaan"])
        raw_high = _number(row["harga_tertinggi"]) or raw_close
        raw_low = _number(row["harga_terendah"]) or raw_close
        reference = _number(row["sebelumnya"])
        history.append({
            "date": str(row["tanggal"]),
            "open": _rounded(raw_open * scale, 2) if raw_open > 0 else None,
            "high": _rounded(raw_high * scale, 2),
            "low": _rounded(raw_low * scale, 2),
            "close": _rounded(adjusted_close, 2),
            "raw_close": _rounded(raw_close, 2),
            "official_return": _rounded((raw_close / reference - 1) * 100 if reference > 0 else None, 4),
            "reference_adjusted": str(row["tanggal"]) in adjustment_dates,
            "volume": int(_number(row["volume"])),
            "value": _rounded(_number(row["nilai_transaksi"]), 2),
            "foreign_net": _rounded(_number(row["beli_asing"]) - _number(row["jual_asing"]), 2),
            "sma20": _rounded(_sma(rolling_closes, 20), 2),
            "sma50": _rounded(_sma(rolling_closes, 50), 2),
            "rsi14": _rounded(_rsi(rolling_closes), 2),
        })

    def level(window: int, field: str) -> float | None:
        if len(history) < window:
            return None
        key = "low" if field == "harga_terendah" else "high"
        values = [_number(row[key]) for row in history[-window:]]
        if not values:
            return None
        return _rounded(min(values) if field == "harga_terendah" else max(values), 2)

    profile = dict(master) if master else {
        "code": code,
        "company_name": rows[-1]["nama_perusahaan"],
        "listing_date": None,
        "shares": int(_number(rows[-1]["saham_tercatat"])),
        "listing_board": "Belum ada di master",
    }
    payload = {
        "as_of": str(rows[-1]["tanggal"]),
        "requested_sessions": days,
        "returned_sessions": len(history),
        "profile": profile,
        "metrics": metrics,
        "levels": {
            "support_20": level(20, "harga_terendah"),
            "resistance_20": level(20, "harga_tertinggi"),
            "support_55": level(55, "harga_terendah"),
            "resistance_55": level(55, "harga_tertinggi"),
        },
        "ownership": ownership,
        "history": history,
        "price_basis": {
            "default": "backward_adjusted_reference_price",
            "anchor": str(rows[-1]["tanggal"]),
            "adjustment_events": adjustment_events,
        },
        "notes": [
            "Grafik analitik memakai harga backward-adjusted dari reference price resmi IDX dan ditambatkan ke close terbaru.",
            "Raw close tetap dikirim untuk audit; penyesuaian ini mengurangi distorsi stock split/corporate action.",
            "Harga pembukaan historis sering kosong, sehingga grafik menekankan close serta rentang high–low.",
            "Bid/offer adalah snapshot akhir hari, bukan data real-time.",
        ],
    }
    payload["generated_ms"] = round((time.perf_counter() - started) * 1000, 2)
    with _CACHE_LOCK:
        _STOCK_DETAIL_CACHE[cache_key] = payload
        _STOCK_DETAIL_CACHE.move_to_end(cache_key)
        while len(_STOCK_DETAIL_CACHE) > _STOCK_DETAIL_CACHE_LIMIT:
            _STOCK_DETAIL_CACHE.popitem(last=False)
    return payload


def daily_rows(
    code: str = "",
    date_from: str = "",
    date_to: str = "",
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    normalized_code = _validate_code(code) if code else ""
    for value, label in ((date_from, "Tanggal awal"), (date_to, "Tanggal akhir")):
        if value:
            try:
                date.fromisoformat(value)
            except ValueError as error:
                raise ValueError(f"{label} tidak valid.") from error
    if date_from and date_to and date_from > date_to:
        raise ValueError("Tanggal awal tidak boleh melewati tanggal akhir.")
    page = max(1, int(page))
    page_size = max(20, min(int(page_size), 100))
    conditions: list[str] = []
    parameters: list[Any] = []
    if normalized_code:
        conditions.append("r.kode_saham = ?")
        parameters.append(normalized_code)
    if date_from:
        conditions.append("r.tanggal >= ?")
        parameters.append(date_from)
    if date_to:
        conditions.append("r.tanggal <= ?")
        parameters.append(date_to)
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    with _reader() as connection:
        total = int(connection.execute(f"SELECT COUNT(*) FROM ringkasan_saham_harian AS r{where}", parameters).fetchone()[0])
        pages = max(1, math.ceil(total / page_size))
        page = min(page, pages)
        offset = (page - 1) * page_size
        rows = [dict(row) for row in connection.execute(
            f"""SELECT r.tanggal, r.kode_saham, r.nama_perusahaan, r.sebelumnya,
                       r.harga_tertinggi, r.harga_terendah, r.harga_penutupan, r.perubahan,
                       r.volume, r.nilai_transaksi, r.frekuensi,
                       r.beli_asing, r.jual_asing,
                       COALESCE(r.beli_asing, 0) - COALESCE(r.jual_asing, 0) AS net_asing,
                       r.penawaran_beli, r.penawaran_jual,
                       r.volume_penawaran_beli, r.volume_penawaran_jual
                FROM ringkasan_saham_harian AS r{where}
                ORDER BY r.tanggal DESC, r.kode_saham ASC LIMIT ? OFFSET ?""",
            [*parameters, page_size, offset],
        )]
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
        "filters": {"code": normalized_code, "date_from": date_from, "date_to": date_to},
        "items": rows,
    }


def methodology() -> dict[str, Any]:
    """Return the measurable rules and current data-quality profile used by the UI."""
    global _METHODOLOGY_CACHE
    newest, signature = _data_signature()
    if _METHODOLOGY_CACHE and _METHODOLOGY_CACHE[0] == signature:
        return _METHODOLOGY_CACHE[1]
    with _CACHE_LOCK:
        if _METHODOLOGY_CACHE and _METHODOLOGY_CACHE[0] == signature:
            return _METHODOLOGY_CACHE[1]
        with _reader() as connection:
            profile = connection.execute(
                """SELECT COUNT(*) AS total_rows, MIN(tanggal) AS date_from,
                          MAX(tanggal) AS date_to, COUNT(DISTINCT tanggal) AS trading_days,
                          COUNT(DISTINCT kode_saham) AS historical_tickers
                     FROM ringkasan_saham_harian"""
            ).fetchone()
            latest_quality = connection.execute(
                """SELECT COUNT(*) AS rows_latest,
                          SUM(CASE WHEN COALESCE(volume, 0) > 0 THEN 1 ELSE 0 END) AS active,
                          SUM(CASE WHEN COALESCE(harga_pembukaan, 0) > 0 THEN 1 ELSE 0 END) AS valid_open,
                          SUM(CASE WHEN COALESCE(harga_tertinggi, 0) > 0
                                    AND COALESCE(harga_terendah, 0) > 0 THEN 1 ELSE 0 END) AS valid_high_low,
                          SUM(CASE WHEN beli_asing IS NOT NULL AND jual_asing IS NOT NULL THEN 1 ELSE 0 END) AS valid_foreign
                     FROM ringkasan_saham_harian WHERE tanggal = ?""",
                (newest,),
            ).fetchone()
            last_success = connection.execute(
                """SELECT tanggal, row_count, updated_at FROM idx_daily_sync_log
                    WHERE status = 'success' ORDER BY tanggal DESC LIMIT 1"""
            ).fetchone()
            last_attempt = connection.execute(
                """SELECT tanggal, status, row_count, updated_at, error
                     FROM idx_daily_sync_log ORDER BY updated_at DESC LIMIT 1"""
            ).fetchone()
            sync_summary = connection.execute(
                """SELECT SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS success,
                          SUM(CASE WHEN status='no_data' THEN 1 ELSE 0 END) AS no_data,
                          SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed,
                          SUM(CASE WHEN status='success' AND payload_sha256 IS NOT NULL THEN 1 ELSE 0 END) AS hashed
                     FROM idx_daily_sync_log"""
            ).fetchone()
            special_series = [str(row[0]) for row in connection.execute(
                """SELECT r.kode_saham FROM ringkasan_saham_harian AS r
                     LEFT JOIN idx_stocks AS s ON s.code=r.kode_saham
                    WHERE r.tanggal=? AND s.id IS NULL ORDER BY r.kode_saham""",
                (newest,),
            )]

        age_days = max(0, (date.today() - date.fromisoformat(newest)).days)
        freshness = "Mutakhir" if age_days <= 3 else "Perlu diperiksa" if age_days <= 7 else "Perlu sinkronisasi"
        latest_rows = max(int(_number(latest_quality["rows_latest"])), 1)
        payload: dict[str, Any] = {
            "analytics_version": ANALYTICS_VERSION,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "data_profile": {
                "database_table": "ringkasan_saham_harian",
                "source": "IDX Stock Summary / Equity EoD",
                "total_rows": int(_number(profile["total_rows"])),
                "date_from": str(profile["date_from"]),
                "date_to": str(profile["date_to"]),
                "trading_days": int(_number(profile["trading_days"])),
                "historical_tickers": int(_number(profile["historical_tickers"])),
                "calendar_age_days": age_days,
                "freshness": freshness,
                "last_success": dict(last_success) if last_success else None,
                "last_attempt": dict(last_attempt) if last_attempt else None,
                "sync_summary": dict(sync_summary),
            },
            "quality": {
                "latest_rows": int(_number(latest_quality["rows_latest"])),
                "active_pct": _rounded(_number(latest_quality["active"]) / latest_rows * 100, 2),
                "valid_open_pct": _rounded(_number(latest_quality["valid_open"]) / latest_rows * 100, 2),
                "valid_high_low_pct": _rounded(_number(latest_quality["valid_high_low"]) / latest_rows * 100, 2),
                "valid_foreign_pct": _rounded(_number(latest_quality["valid_foreign"]) / latest_rows * 100, 2),
                "master_coverage_pct": _rounded((latest_rows - len(special_series)) / latest_rows * 100, 2),
                "special_series_outside_master": special_series,
                "successful_dates_hashed_pct": _rounded(
                    _number(sync_summary["hashed"]) / max(_number(sync_summary["success"]), 1) * 100,
                    2,
                ),
                "raw_prices_adjusted": False,
                "analytics_prices_adjusted": True,
            },
            "breadth_score": {
                "formula": "40% rasio saham naik aktif + 35% saham di atas SMA20 + 25% saham di atas SMA50",
                "bands": [
                    {"minimum": 65, "label": "Kuat"},
                    {"minimum": 55, "label": "Positif"},
                    {"minimum": 45, "label": "Netral"},
                    {"minimum": 35, "label": "Lemah"},
                    {"minimum": 0, "label": "Sangat lemah"},
                ],
            },
            "setup_score": {
                "formula": "Jumlah bobot kondisi yang terpenuhi dari denominator tetap 100; skor hanya terbit setelah minimal 50 sesi",
                "components": list(SCORE_COMPONENTS),
            },
            "indicators": [
                {"key": "return_nd", "label": "Return N sesi", "formula": "Rantai close/reference resmi IDX selama N sesi − 1", "reading": "Corporate-action aware; mengukur perubahan historis, bukan prediksi."},
                {"key": "sma", "label": "SMA20/50/200", "formula": "Rata-rata backward-adjusted close selama N sesi", "reading": "Harga > SMA20 > SMA50 diklasifikasikan Uptrend."},
                {"key": "rsi14", "label": "RSI14 (Cutler)", "formula": "100 − 100/(1 + simple-average gain/simple-average loss 14 sesi)", "reading": "≤30 oversold, 50–70 momentum positif, ≥70 overbought."},
                {"key": "atr14", "label": "ATR14%", "formula": "Simple mean true-range% 14 sesi memakai reference price resmi", "reading": "Semakin tinggi, rentang risiko harian semakin besar."},
                {"key": "volatility20", "label": "Volatilitas 20D", "formula": "Population standard deviation log-return × √252", "reading": "Estimasi variasi historis tahunan, bukan prediksi."},
                {"key": "activity_ratio", "label": "Volume/value/frequency ratio", "formula": "Nilai sesi terbaru / rata-rata 20 sesi sebelumnya", "reading": "≥1,5× dianggap lonjakan aktivitas."},
                {"key": "breakout20", "label": "Breakout 20D", "formula": "Close sekarang > high tertinggi 20 sesi sebelumnya", "reading": "Hari berjalan tidak masuk pembanding."},
                {"key": "foreign", "label": "Net foreign", "formula": "Beli asing − jual asing", "reading": "Satuan saham, bukan rupiah; konsisten bila ≥60% hari positif."},
                {"key": "price_impact", "label": "Price impact", "formula": "Rata-rata |return %| / (nilai transaksi/Rp1 miliar)", "reading": "Lebih kecil umumnya menunjukkan eksekusi lebih likuid."},
            ],
            "thresholds": {
                "volume_spike": 1.5,
                "value_spike": 1.5,
                "volume_confirmation": 1.2,
                "foreign_consistency": 60,
                "liquidity_high_rupiah": 50_000_000_000,
                "liquidity_medium_rupiah": 10_000_000_000,
                "rsi_oversold": 30,
                "rsi_overbought": 70,
            },
            "limitations": [
                "Harga mentah di SQLite belum adjusted; indikator membuat seri backward-adjusted dari reference price resmi IDX.",
                "Reference adjustment mengurangi distorsi corporate action tetapi bukan pengganti tabel corporate_actions yang lengkap.",
                "Harga pembukaan historis banyak bernilai nol; chart utama memakai close serta high–low.",
                "Bid/offer adalah snapshot akhir hari dan bukan order book real-time.",
                "Database belum memuat fundamental, sektor, indeks pembanding, atau laporan keuangan.",
                "Semua skor menggambarkan kondisi historis; tidak memprediksi keuntungan masa depan.",
            ],
        }
        _METHODOLOGY_CACHE = (signature, payload)
        return payload


def get_accumulation_all(period_name: str, date_from: str | None = None, date_to: str | None = None) -> dict[str, Any]:
    global _ACCUMULATION_ALL_CACHE
    newest, signature = _data_signature()

    # Direct date range overrides period_name
    if date_from and date_to:
        start_date = date_from
        end_date   = date_to if date_to <= newest else newest
        cache_key  = (signature, f"{start_date}|{end_date}")
    else:
        end_date = newest
        cache_key = (signature, period_name)
        with _CACHE_LOCK:
            cached = _ACCUMULATION_ALL_CACHE.get(cache_key)
            if cached is not None:
                return cached

        max_date_obj = date.fromisoformat(newest)
        if period_name == "harian":
            start_date = newest
        elif period_name == "mingguan":
            start_date = (max_date_obj - timedelta(days=6)).isoformat()
        elif period_name == "bulanan":
            start_date = (max_date_obj - timedelta(days=29)).isoformat()
        elif period_name == "3_bulanan":
            start_date = (max_date_obj - timedelta(days=89)).isoformat()
        elif period_name == "6_bulanan":
            start_date = (max_date_obj - timedelta(days=179)).isoformat()
        elif period_name == "tahunan":
            start_date = (max_date_obj - timedelta(days=364)).isoformat()
        elif period_name == "2_tahunan":
            start_date = (max_date_obj - timedelta(days=2 * 365 - 1)).isoformat()
        elif period_name == "3_tahunan":
            start_date = (max_date_obj - timedelta(days=3 * 365 - 1)).isoformat()
        elif period_name == "5_tahunan":
            start_date = (max_date_obj - timedelta(days=5 * 365 - 1)).isoformat()
        elif period_name == "semua_data":
            start_date = "2020-01-02"
        else:
            raise ValueError(f"Periode tidak dikenal: {period_name}")

    # Check cache after computing start_date (for period-based path)
    if not (date_from and date_to):
        with _CACHE_LOCK:
            cached = _ACCUMULATION_ALL_CACHE.get(cache_key)
            if cached is not None:
                return cached

    query = """
        WITH period_bounds AS (
            SELECT 
                kode_saham,
                MIN(tanggal) AS min_tgl,
                MAX(tanggal) AS max_tgl
            FROM ringkasan_saham_harian
            WHERE tanggal >= ? AND tanggal <= ?
            GROUP BY kode_saham
        )
        SELECT 
            r_agg.kode_saham AS code,
            COALESCE(s.company_name, r_agg.nama_perusahaan) AS name,
            SUM(r_agg.volume) AS volume,
            SUM(r_agg.nilai_transaksi) AS value,
            SUM(r_agg.frekuensi) AS frequency,
            SUM(COALESCE(r_agg.beli_asing, 0) - COALESCE(r_agg.jual_asing, 0)) AS net_foreign,
            SUM((COALESCE(r_agg.beli_asing, 0) - COALESCE(r_agg.jual_asing, 0)) * r_agg.harga_penutupan) AS net_foreign_value_est,
            MIN(r_agg.harga_terendah) AS low,
            MAX(r_agg.harga_tertinggi) AS high,
            COUNT(r_agg.tanggal) AS active_days,
            r_start.harga_penutupan AS start_price,
            r_start.sebelumnya AS start_prev,
            r_end.harga_penutupan AS end_price
        FROM ringkasan_saham_harian r_agg
        JOIN period_bounds pb ON pb.kode_saham = r_agg.kode_saham
        JOIN ringkasan_saham_harian r_start ON r_start.kode_saham = r_agg.kode_saham AND r_start.tanggal = pb.min_tgl
        JOIN ringkasan_saham_harian r_end ON r_end.kode_saham = r_agg.kode_saham AND r_end.tanggal = pb.max_tgl
        LEFT JOIN idx_stocks s ON s.code = r_agg.kode_saham
        WHERE r_agg.tanggal >= ? AND r_agg.tanggal <= ?
        GROUP BY r_agg.kode_saham
    """

    with _reader() as connection:
        rows = [dict(row) for row in connection.execute(query, (start_date, newest, start_date, newest))]

    items = []
    for row in rows:
        vol = _number(row["volume"])
        val = _number(row["value"])
        net_f = _number(row["net_foreign"])
        net_f_val = _number(row["net_foreign_value_est"])
        
        start_price = _number(row["start_prev"] if row["start_prev"] else row["start_price"])
        end_price = _number(row["end_price"])
        change = end_price - start_price
        change_pct = (change / start_price * 100) if start_price > 0 else 0.0

        vwap = (val / vol) if vol > 0 else 0.0

        items.append({
            "code": row["code"],
            "name": row["name"],
            "active_days": row["active_days"],
            "start_price": _rounded(row["start_price"], 2),
            "end_price": _rounded(row["end_price"], 2),
            "change": _rounded(change, 2),
            "change_pct": _rounded(change_pct, 4),
            "volume": int(vol),
            "value": _rounded(val, 2),
            "frequency": int(row["frequency"]),
            "net_foreign": _rounded(net_f, 2),
            "net_foreign_value_est": _rounded(net_f_val, 2),
            "vwap": _rounded(vwap, 2),
            "low": _rounded(row["low"], 2),
            "high": _rounded(row["high"], 2),
        })

    payload = {
        "as_of": newest,
        "period": period_name if not (date_from and date_to) else "kustom",
        "start_date": start_date,
        "end_date": end_date,
        "count": len(items),
        "items": items
    }
    with _CACHE_LOCK:
        _ACCUMULATION_ALL_CACHE[cache_key] = payload
    return payload


def get_accumulation_stock(code: str, period_name: str, date_from: str | None = None, date_to: str | None = None) -> dict[str, Any]:
    code = _validate_code(code)
    newest, _ = _data_signature()

    # Direct date range — use harian grouping inside the range
    if date_from and date_to:
        start_date = date_from
        end_date   = date_to if date_to <= newest else newest
        # Use 'harian' grouping for custom ranges so every trading day shows
        expr = "tanggal"
        period_name = "kustom"
    else:
        end_date = newest
        start_date = None  # will be set below per period

    # For period-based queries, derive grouping expression and date range
    if not (date_from and date_to):
        grouping_expressions = {
            "harian": "tanggal",
            "mingguan": "strftime('%Y-W%W', tanggal)",
            "bulanan": "strftime('%Y-%m', tanggal)",
            "3_bulanan": "strftime('%Y', tanggal) || '-Q' || (((CAST(strftime('%m', tanggal) AS INTEGER) - 1) / 3) + 1)",
            "6_bulanan": "strftime('%Y', tanggal) || '-H' || (((CAST(strftime('%m', tanggal) AS INTEGER) - 1) / 6) + 1)",
            "tahunan": "strftime('%Y', tanggal)",
            "2_tahunan": "((CAST(strftime('%Y', tanggal) AS INTEGER) / 2) * 2) || '-' || (((CAST(strftime('%Y', tanggal) AS INTEGER) / 2) * 2) + 1)",
            "3_tahunan": "(((CAST(strftime('%Y', tanggal) AS INTEGER) - 2020) / 3) * 3 + 2020) || '-' || (((CAST(strftime('%Y', tanggal) AS INTEGER) - 2020) / 3) * 3 + 2022)",
            "5_tahunan": "(((CAST(strftime('%Y', tanggal) AS INTEGER) - 2020) / 5) * 5 + 2020) || '-' || (((CAST(strftime('%Y', tanggal) AS INTEGER) - 2020) / 5) * 5 + 2024)",
            "semua_data": "'Semua Data'"
        }
        expr = grouping_expressions.get(period_name)
        if not expr:
            raise ValueError(f"Periode tidak dikenal: {period_name}")
        start_date = None  # no date filtering for period-based (uses all data)

    # Build WHERE clause
    if start_date and end_date:
        date_filter = f"AND tanggal >= '{start_date}' AND tanggal <= '{end_date}'"
    else:
        date_filter = ""

    query = f"""
        WITH agg AS (
            SELECT 
                {expr} AS period_key,
                MIN(tanggal) AS min_tgl,
                MAX(tanggal) AS max_tgl,
                COUNT(tanggal) AS active_days,
                SUM(volume) AS total_volume,
                SUM(nilai_transaksi) AS total_nilai,
                SUM(frekuensi) AS total_frekuensi,
                SUM(COALESCE(beli_asing, 0) - COALESCE(jual_asing, 0)) AS net_asing,
                SUM((COALESCE(beli_asing, 0) - COALESCE(jual_asing, 0)) * harga_penutupan) AS net_asing_value_est,
                MIN(harga_terendah) AS low_price,
                MAX(harga_tertinggi) AS high_price
            FROM ringkasan_saham_harian
            WHERE kode_saham = ? {date_filter}
            GROUP BY period_key
        )
        SELECT 
            a.*,
            r_start.harga_penutupan AS start_price,
            r_start.sebelumnya AS start_prev,
            r_end.harga_penutupan AS end_price
        FROM agg a
        JOIN ringkasan_saham_harian r_start ON r_start.kode_saham = ? AND r_start.tanggal = a.min_tgl
        JOIN ringkasan_saham_harian r_end ON r_end.kode_saham = ? AND r_end.tanggal = a.max_tgl
        ORDER BY a.period_key DESC
    """

    with _reader() as connection:
        rows = [dict(row) for row in connection.execute(query, (code, code, code))]
        master = connection.execute(
            "SELECT code, company_name, listing_date, shares, listing_board FROM idx_stocks WHERE code = ?", (code,)
        ).fetchone()

    profile = dict(master) if master else {
        "code": code,
        "company_name": rows[0]["nama_perusahaan"] if rows else code,
        "listing_date": None,
        "shares": 0,
        "listing_board": "Unknown"
    }

    items = []
    for row in rows:
        vol = _number(row["total_volume"])
        val = _number(row["total_nilai"])
        net_f = _number(row["net_asing"])
        net_f_val = _number(row["net_asing_value_est"])
        
        start_price = _number(row["start_prev"] if row["start_prev"] else row["start_price"])
        end_price = _number(row["end_price"])
        change = end_price - start_price
        change_pct = (change / start_price * 100) if start_price > 0 else 0.0

        vwap = (val / vol) if vol > 0 else 0.0

        items.append({
            "period_key": row["period_key"],
            "start_date": row["min_tgl"],
            "end_date": row["max_tgl"],
            "active_days": row["active_days"],
            "start_price": _rounded(row["start_price"], 2),
            "end_price": _rounded(row["end_price"], 2),
            "change": _rounded(change, 2),
            "change_pct": _rounded(change_pct, 4),
            "volume": int(vol),
            "value": _rounded(val, 2),
            "frequency": int(row["total_frekuensi"]),
            "net_foreign": _rounded(net_f, 2),
            "net_foreign_value_est": _rounded(net_f_val, 2),
            "vwap": _rounded(vwap, 2),
            "low": _rounded(row["low_price"], 2),
            "high": _rounded(row["high_price"], 2),
        })

    return {
        "as_of": newest,
        "profile": profile,
        "period": period_name,
        "count": len(items),
        "items": items
    }


# =============================================================================
# BROKER ANALYTICS
# =============================================================================


def get_broker_master() -> dict:
    """Return full list of registered brokers from master_broker."""
    global _BROKER_MASTER_CACHE
    with _CACHE_LOCK:
        if _BROKER_MASTER_CACHE is not None:
            return _BROKER_MASTER_CACHE
    with _reader() as con:
        rows = con.execute(
            "SELECT kode_broker, nama_broker, izin, status, sumber, updated_at "
            "FROM master_broker ORDER BY kode_broker"
        ).fetchall()
    items = [dict(r) for r in rows]
    result: dict[str, Any] = {"count": len(items), "items": items}
    with _CACHE_LOCK:
        _BROKER_MASTER_CACHE = result
    return result


def get_broker_daily(
    date_from: str | None = None,
    date_to:   str | None = None,
) -> dict:
    """Per-broker aggregated totals for a date range with market share.

    When both ``date_from`` and ``date_to`` are *None*, defaults to the most
    recent available trading date (single-day view).  Either bound alone is
    treated as a one-day range.
    """
    with _reader() as con:
        # Resolve default bounds
        if not date_from and not date_to:
            row = con.execute(
                "SELECT MAX(tanggal) AS d FROM ringkasan_broker_harian"
            ).fetchone()
            latest = row["d"] if row else None
            date_from = date_to = latest
        elif not date_from:
            date_from = date_to
        elif not date_to:
            date_to = date_from

        if not date_from:
            return {
                "date_from": None, "date_to": None, "trading_days": 0,
                "total": {}, "available_dates": [], "count": 0, "items": [],
            }

        cache_key = f"{date_from}|{date_to}"
        with _CACHE_LOCK:
            if cache_key in _BROKER_DAILY_CACHE:
                return _BROKER_DAILY_CACHE[cache_key]

        rows = con.execute(
            """
            SELECT b.kode_broker,
                   m.nama_broker,
                   SUM(b.volume)    AS volume,
                   SUM(b.nilai)     AS nilai,
                   SUM(b.frekuensi) AS frekuensi,
                   COUNT(DISTINCT b.tanggal) AS active_days
            FROM ringkasan_broker_harian b
            JOIN master_broker m ON m.kode_broker = b.kode_broker
            WHERE b.tanggal BETWEEN ? AND ?
            GROUP BY b.kode_broker
            ORDER BY nilai DESC
            """,
            (date_from, date_to),
        ).fetchall()

        meta = con.execute(
            """
            SELECT COUNT(DISTINCT tanggal) AS trading_days,
                   MIN(tanggal) AS actual_from,
                   MAX(tanggal) AS actual_to
            FROM ringkasan_broker_harian
            WHERE tanggal BETWEEN ? AND ?
            """,
            (date_from, date_to),
        ).fetchone()

        total_nilai = sum(_number(r["nilai"]) for r in rows)
        total_vol   = sum(_number(r["volume"]) for r in rows)
        total_freq  = sum(_number(r["frekuensi"]) for r in rows)

        items = []
        for rank, r in enumerate(rows, 1):
            nilai  = _number(r["nilai"])
            vol    = _number(r["volume"])
            freq   = _number(r["frekuensi"])
            items.append({
                "rank":            rank,
                "code":            r["kode_broker"],
                "name":            r["nama_broker"],
                "nilai":           _rounded(nilai, 0),
                "volume":          int(vol),
                "frekuensi":       int(freq),
                "active_days":     int(r["active_days"]),
                "share_nilai":     _rounded(nilai / total_nilai * 100 if total_nilai else 0, 4),
                "share_volume":    _rounded(vol   / total_vol   * 100 if total_vol   else 0, 4),
                "share_frekuensi": _rounded(freq  / total_freq  * 100 if total_freq  else 0, 4),
            })

        dates = [r["tanggal"] for r in con.execute(
            "SELECT DISTINCT tanggal FROM ringkasan_broker_harian ORDER BY tanggal DESC LIMIT 600"
        ).fetchall()]

    result = {
        "date_from":     date_from,
        "date_to":       date_to,
        "actual_from":   meta["actual_from"],
        "actual_to":     meta["actual_to"],
        "trading_days":  int(meta["trading_days"] or 0),
        "total": {
            "nilai":     _rounded(total_nilai, 0),
            "volume":    int(total_vol),
            "frekuensi": int(total_freq),
            "brokers":   len(items),
        },
        "available_dates": dates,
        "count":           len(items),
        "items":           items,
    }
    with _CACHE_LOCK:
        _BROKER_DAILY_CACHE[cache_key] = result
    return result


def get_broker_monthly(code: str | None = None) -> dict:
    """Monthly aggregated activity — all brokers combined or one specific broker."""
    cache_key = code.upper() if code else "__all__"
    with _CACHE_LOCK:
        if cache_key in _BROKER_MONTHLY_CACHE:
            return _BROKER_MONTHLY_CACHE[cache_key]

    with _reader() as con:
        if code:
            row = con.execute(
                "SELECT kode_broker, nama_broker FROM master_broker WHERE kode_broker = ?",
                (code.upper(),),
            ).fetchone()
            if not row:
                raise LookupError(f"Broker '{code}' tidak ditemukan.")
            broker_info: dict | None = dict(row)
            rows = con.execute(
                """
                SELECT substr(tanggal,1,7) AS bulan,
                       COUNT(DISTINCT tanggal)  AS hari_aktif,
                       SUM(nilai)               AS total_nilai,
                       SUM(volume)              AS total_volume,
                       SUM(frekuensi)           AS total_frekuensi
                FROM ringkasan_broker_harian
                WHERE kode_broker = ?
                GROUP BY bulan
                ORDER BY bulan DESC
                """,
                (code.upper(),),
            ).fetchall()
        else:
            broker_info = None
            rows = con.execute(
                """
                SELECT substr(tanggal,1,7) AS bulan,
                       COUNT(DISTINCT tanggal)  AS hari_aktif,
                       SUM(nilai)               AS total_nilai,
                       SUM(volume)              AS total_volume,
                       SUM(frekuensi)           AS total_frekuensi
                FROM ringkasan_broker_harian
                GROUP BY bulan
                ORDER BY bulan DESC
                """,
            ).fetchall()

        items = []
        for r in rows:
            items.append({
                "bulan":           r["bulan"],
                "hari_aktif":      int(r["hari_aktif"]),
                "total_nilai":     _rounded(_number(r["total_nilai"]), 0),
                "total_volume":    int(_number(r["total_volume"])),
                "total_frekuensi": int(_number(r["total_frekuensi"])),
            })

    result = {"code": code, "broker_info": broker_info, "count": len(items), "items": items}
    with _CACHE_LOCK:
        _BROKER_MONTHLY_CACHE[cache_key] = result
    return result


def get_broker_profile(code: str) -> dict:
    """Full broker profile: identity + YTD stats + YTD ranking + daily history."""
    code = code.upper()
    with _reader() as con:
        row = con.execute(
            "SELECT kode_broker, nama_broker, izin, status, sumber FROM master_broker WHERE kode_broker = ?",
            (code,),
        ).fetchone()
        if not row:
            raise LookupError(f"Broker '{code}' tidak ditemukan.")
        profile = dict(row)

        ytd = con.execute(
            """
            SELECT COALESCE(SUM(nilai), 0)     AS ytd_nilai,
                   COALESCE(SUM(volume), 0)    AS ytd_volume,
                   COALESCE(SUM(frekuensi), 0) AS ytd_frekuensi,
                   COUNT(DISTINCT tanggal)      AS ytd_days
            FROM ringkasan_broker_harian
            WHERE kode_broker = ?
              AND tanggal >= strftime('%Y-01-01', 'now')
            """,
            (code,),
        ).fetchone()

        rank_row = con.execute(
            """
            WITH ranked AS (
                SELECT kode_broker,
                       RANK() OVER (ORDER BY SUM(nilai) DESC) AS rank_nilai
                FROM ringkasan_broker_harian
                WHERE tanggal >= strftime('%Y-01-01', 'now')
                GROUP BY kode_broker
            )
            SELECT rank_nilai FROM ranked WHERE kode_broker = ?
            """,
            (code,),
        ).fetchone()

        history_rows = con.execute(
            """
            SELECT tanggal, nilai, volume, frekuensi
            FROM ringkasan_broker_harian
            WHERE kode_broker = ?
            ORDER BY tanggal DESC
            LIMIT 252
            """,
            (code,),
        ).fetchall()

    history = [{
        "tanggal":   r["tanggal"],
        "nilai":     _rounded(_number(r["nilai"]), 0),
        "volume":    int(_number(r["volume"])),
        "frekuensi": int(_number(r["frekuensi"])),
    } for r in history_rows]

    return {
        "profile": {
            **profile,
            "ytd_nilai":     _rounded(_number(ytd["ytd_nilai"]), 0),
            "ytd_volume":    int(_number(ytd["ytd_volume"])),
            "ytd_frekuensi": int(_number(ytd["ytd_frekuensi"])),
            "ytd_days":      int(ytd["ytd_days"] or 0),
            "rank_ytd":      int(rank_row["rank_nilai"]) if rank_row else None,
        },
        "history": history,
    }
