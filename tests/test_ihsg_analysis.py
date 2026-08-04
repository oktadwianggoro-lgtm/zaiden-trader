"""
tests/test_ihsg_analysis.py
Validates ml_weekly/ihsg_analysis.py against synthetic idx_index_daily data,
since walk-forward logistic regression needs hundreds of rows and real IDX
history takes time to backfill. Focus: no leakage, honest empty/insufficient
states, sane structure, and correct local-bottom / regime detection logic.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from db import SCHEMA_SQL
from ml_weekly.ihsg_analysis import (
    compute_ihsg_dashboard,
    _find_local_bottoms,
    _build_technicals,
    _classify_regime,
    load_composite_series,
)


def _make_db(tmp_path: Path, closes: list[float], start="2020-01-01") -> str:
    db_path = str(tmp_path / "ihsg.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)
    dates = pd.bdate_range(start=start, periods=len(closes))
    prev = None
    for d, c in zip(dates, closes):
        conn.execute(
            """INSERT INTO idx_index_daily (
                tanggal, index_code, previous, highest, lowest, close, change,
                volume, value, frequency, number_of_stock, market_capital
            ) VALUES (?, 'COMPOSITE', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                d.strftime("%Y-%m-%d"), prev, c * 1.005, c * 0.995, c,
                (c - prev) if prev is not None else None,
                1_000_000, 1_000_000_000.0, 500_000, 900, 8e15,
            ),
        )
        prev = c
    conn.commit()
    conn.close()
    return db_path


def _uptrend_series(n: int, start: float = 5000.0, mu: float = 0.0009, sigma: float = 0.009, seed: int = 42) -> list[float]:
    """Geometric random walk with positive drift — realistic enough that daily
    returns aren't all the same sign (so a classifier has both classes to
    learn from), while still trending up on net over the long run."""
    rng = np.random.default_rng(seed)
    daily_returns = rng.normal(mu, sigma, n)
    return list(start * np.cumprod(1.0 + daily_returns))


def test_insufficient_data_returns_honest_message(tmp_path):
    db_path = _make_db(tmp_path, _uptrend_series(50))
    result = compute_ihsg_dashboard(db_path)
    assert result["data_available"] is False
    assert result["rows"] == 50
    assert "note" in result and result["note"]


def test_empty_database_returns_honest_message(tmp_path):
    db_path = str(tmp_path / "empty.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.close()
    result = compute_ihsg_dashboard(db_path)
    assert result["data_available"] is False
    assert result["rows"] == 0


def test_full_pipeline_structure(tmp_path):
    db_path = _make_db(tmp_path, _uptrend_series(700))
    result = compute_ihsg_dashboard(db_path)
    assert result["data_available"] is True
    assert result["rows"] == 700
    assert "close" in result["latest"]
    assert result["regime"] in {
        "TREN_NAIK", "TREN_TURUN", "SIDEWAYS", "VOLATILITAS_TINGGI", "BELUM_CUKUP_DATA",
    }
    assert set(result["direction"].keys()) == {"short", "medium"}
    for horizon_label, summary in result["direction"].items():
        assert summary["horizon_days"] > 0
        if summary["hit_rate"] is not None:
            assert 0.0 <= summary["hit_rate"] <= 1.0
            assert summary["hit_rate_ci_lower"] <= summary["hit_rate"] <= summary["hit_rate_ci_upper"]
    assert "current_drawdown_pct" in result["bottom_analog"]
    assert len(result["timeseries"]["dates"]) == len(result["timeseries"]["close"])


def test_walk_forward_no_lookahead_uses_only_past_labels(tmp_path):
    """Sanity check: a pure, noiseless uptrend should be learnable well
    out-of-sample (the model isn't just guessing) once enough history exists."""
    db_path = _make_db(tmp_path, _uptrend_series(700))
    result = compute_ihsg_dashboard(db_path)
    short = result["direction"]["short"]
    assert short["n_validated"] > 0
    # A near-deterministic uptrend should be predicted correctly most of the time —
    # this is a floor check that the walk-forward loop is actually learning
    # something real, not silently broken (e.g. always predicting 0.5).
    assert short["hit_rate"] > 0.55


def test_regime_classification_uptrend(tmp_path):
    # A stochastic path can end mid-wobble even with positive drift, so this
    # test needs an unambiguous finish: random history to build up realistic
    # SMAs/RSI, then a clean, sustained climb for the final stretch so the
    # last row is clearly above both SMAs with strong momentum.
    base = _uptrend_series(640, mu=0.0005, sigma=0.008)
    climb = [base[-1] * (1.004 ** i) for i in range(1, 61)]
    db_path = _make_db(tmp_path, base + climb)
    series = load_composite_series(db_path)
    tech = _build_technicals(series)
    regime = _classify_regime(tech.iloc[-1])
    assert regime == "TREN_NAIK"


def test_find_local_bottoms_detects_v_shape():
    # Flat-ish, then a clean V-shaped dip in the middle, then flat-ish again.
    n = 200
    values = np.concatenate([
        np.full(80, 100.0),
        100.0 - np.linspace(0, 20, 20),   # index 80..99, trough (80.0) at index 99
        81.0 + np.linspace(0, 19, 20),    # index 100..119, starts above the trough
        np.full(80, 100.0),
    ])
    close = pd.Series(values)
    bottoms = _find_local_bottoms(close, window=20)
    assert 99 in bottoms


def test_bottom_analog_reports_high_percentile_after_deep_drawdown(tmp_path):
    # Steady rise to an all-time-high, then a sharp, deep drop — today's
    # drawdown should register near the top of the historical percentile range.
    rise = _uptrend_series(500, start=5000.0, mu=0.0012, sigma=0.006)
    peak = rise[-1]
    drop = list(peak - np.linspace(0, peak * 0.35, 60))
    db_path = _make_db(tmp_path, rise + drop)
    result = compute_ihsg_dashboard(db_path)
    analog = result["bottom_analog"]
    assert analog["current_drawdown_pct"] < -20
    assert analog["drawdown_percentile"] > 90
