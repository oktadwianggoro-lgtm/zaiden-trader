"""
tests/test_feature_parity.py
Regression guard for the train/serve feature-skew bug: pipeline.py's
training-time feature builder and features.py's live-inference feature
builder used to be two independently hand-written implementations that
silently drifted apart to the point where only ~9 of ~43 training columns
still existed at live-inference time (~21% feature coverage), which is what
was actually driving DATA_INVALID / LEMAH gating on real signals even
though price history went back to 2020 — the model simply never saw most
of its own inputs at prediction time.

The fix makes pipeline._vectorized_features() delegate to
features.build_features_for_stock() — the exact function
features.build_all_features() (used by live prediction) calls per ticker.
This test builds the same OHLCV history through BOTH real entry points
(the in-memory bulk-load path used by training, and the real SQLite ->
build_all_features path used by live prediction) and asserts their feature
columns and values actually agree. If anyone ever reintroduces a separate,
diverging feature implementation on either side, this test must fail.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from db import SCHEMA_SQL
from ml_weekly.pipeline import _vectorized_features
from ml_weekly.features import build_all_features

TICKER = "TESTX"
N_DAYS = 320
SIGNAL_DATE = None  # set in fixture


def _synthetic_ohlcv(n: int, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-02", periods=n)
    close = 1000.0 * np.cumprod(1.0 + rng.normal(0.0006, 0.015, n))
    high = close * (1.0 + np.abs(rng.normal(0.004, 0.003, n)))
    low = close * (1.0 - np.abs(rng.normal(0.004, 0.003, n)))
    open_ = low + (high - low) * rng.uniform(0.2, 0.8, n)
    volume = np.abs(rng.normal(5_000_000, 1_500_000, n))
    value = volume * close
    freq = np.abs(rng.normal(2000, 500, n))
    foreign_buy = np.abs(rng.normal(1e9, 3e8, n))
    foreign_sell = np.abs(rng.normal(1e9, 3e8, n))
    return pd.DataFrame({
        "date": dates, "ticker": TICKER,
        "open": open_, "high": high, "low": low, "close": close,
        "volume": volume, "value": value, "freq": freq,
        "foreign_buy": foreign_buy, "foreign_sell": foreign_sell,
    })


@pytest.fixture
def synthetic_history():
    return _synthetic_ohlcv(N_DAYS)


@pytest.fixture
def live_db(tmp_path: Path, synthetic_history) -> str:
    """Same OHLCV history, loaded into a real SQLite DB the way
    ringkasan_saham_harian actually looks, for the live-inference path."""
    db_path = str(tmp_path / "feature_parity.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)
    for _, r in synthetic_history.iterrows():
        conn.execute(
            """INSERT INTO ringkasan_saham_harian (
                tanggal, kode_saham, harga_pembukaan, harga_tertinggi, harga_terendah,
                harga_penutupan, volume, nilai_transaksi, frekuensi, beli_asing, jual_asing
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                r["date"].strftime("%Y-%m-%d"), TICKER, r["open"], r["high"], r["low"],
                r["close"], r["volume"], r["value"], r["freq"], r["foreign_buy"], r["foreign_sell"],
            ),
        )
    conn.commit()
    conn.close()
    return db_path


def test_training_and_live_feature_columns_match(synthetic_history, live_db):
    signal_date = synthetic_history["date"].iloc[-1].strftime("%Y-%m-%d")

    train_row = _vectorized_features(synthetic_history, signal_date, [TICKER], lookback_days=260)
    live_row = build_all_features(signal_date, live_db, [TICKER])

    assert not train_row.empty, "training-side feature builder returned nothing"
    assert not live_row.empty, "live-side feature builder returned nothing"

    meta_cols = {"ticker", "signal_date", "close", "n_days"}
    train_cols = set(train_row.columns) - meta_cols
    live_cols = set(live_row.columns) - meta_cols

    missing_at_inference = train_cols - live_cols
    coverage = len(train_cols & live_cols) / len(train_cols)

    assert coverage >= 0.95, (
        f"Only {coverage:.0%} of training feature columns exist at live inference "
        f"(missing: {sorted(missing_at_inference)}). This is the exact train/serve "
        f"skew bug this test exists to catch — training and live prediction must "
        f"use the same feature-computation function."
    )


def test_training_and_live_feature_values_agree(synthetic_history, live_db):
    """Not just the same column names — the same underlying computation."""
    signal_date = synthetic_history["date"].iloc[-1].strftime("%Y-%m-%d")

    train_row = _vectorized_features(synthetic_history, signal_date, [TICKER], lookback_days=260).iloc[0]
    live_row = build_all_features(signal_date, live_db, [TICKER]).iloc[0]

    for col in ["return_1d", "return_5d", "return_20d", "rsi14"]:
        assert col in train_row and col in live_row, f"{col} missing from one side"
        t, l = float(train_row[col]), float(live_row[col])
        assert t == pytest.approx(l, abs=1e-6), (
            f"{col} disagrees between training ({t}) and live ({l}) feature builders"
        )
