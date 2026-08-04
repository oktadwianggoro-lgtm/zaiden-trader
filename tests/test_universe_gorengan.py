"""
tests/test_universe_gorengan.py
Validates ml_weekly/universe.py's pump-and-dump ("gorengan") detection —
added 2026-08-04 alongside lowering the liquidity thresholds (to include
more stocks per user request) so the wider net doesn't let manipulated
stocks back in. Two independent signals: IDX's own "Watchlist" board
designation, and repeated extreme single-day moves clustered in a short
window (a pattern real blue-chips essentially never show).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from db import SCHEMA_SQL
from ml_weekly.universe import build_universe, UniverseStatus, GORENGAN_MIN_EXTREME_DAYS

N_DAYS = 150


def _seed_stock(conn, ticker, closes, board="Main", freq=200, value=5e8):
    conn.execute(
        "INSERT INTO idx_stocks (code, company_name, shares, listing_board) VALUES (?, ?, 1000000, ?)",
        (ticker, f"{ticker} Tbk.", board),
    )
    dates = [f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(len(closes))]
    prev = None
    for d, c in zip(dates, closes):
        conn.execute(
            """INSERT INTO ringkasan_saham_harian (
                tanggal, kode_saham, harga_penutupan, sebelumnya, perubahan, volume, nilai_transaksi, frekuensi
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (d, ticker, c, prev, (c - prev) if prev else None, 100000, value, freq),
        )
        prev = c


@pytest.fixture
def universe_db(tmp_path: Path):
    db_path = str(tmp_path / "universe.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)

    # A stable, boring blue-chip-like stock: small daily drift, no extreme days.
    stable = [1000.0 * (1.0005 ** i) for i in range(N_DAYS)]
    _seed_stock(conn, "STBL1", stable, board="Main")

    # A stock on IDX's own Watchlist (Special Monitoring) board — flagged
    # regardless of its price action.
    _seed_stock(conn, "WLST1", stable, board="Watchlist")

    # A stock with 5 extreme (>=20%) single-day moves scattered through the
    # window — the statistical pump-and-dump signature.
    gorengan_closes = [100.0] * N_DAYS
    price = 100.0
    extreme_at = [20, 40, 60, 80, 100]
    for i in range(N_DAYS):
        if i in extreme_at:
            price *= 1.25
        else:
            price *= 1.001
        gorengan_closes[i] = price
    _seed_stock(conn, "GRNG1", gorengan_closes, board="Main")

    # A stock with just ONE big move (e.g. a real news event / earnings
    # surprise) — must NOT be flagged as gorengan from that alone.
    one_jump_closes = [100.0] * N_DAYS
    price = 100.0
    for i in range(N_DAYS):
        if i == 50:
            price *= 1.30
        else:
            price *= 1.001
        one_jump_closes[i] = price
    _seed_stock(conn, "NEWS1", one_jump_closes, board="Main")

    # A moderate-liquidity stock that clears the NEW (lowered) thresholds
    # but would have failed the old 1B IDR / 100 freq thresholds.
    _seed_stock(conn, "MODL1", stable, board="Main", freq=50, value=3e8)

    conn.commit()
    conn.close()
    return db_path


def _status_for(df, ticker):
    row = df[df["ticker"] == ticker]
    assert len(row) == 1, f"{ticker} not found in universe"
    return row.iloc[0]["status"]


def test_stable_stock_is_eligible(universe_db):
    df = build_universe("2026-05-25", universe_db, min_history_days=100)
    assert _status_for(df, "STBL1") == UniverseStatus.ELIGIBLE.value


def test_watchlist_board_stock_flagged_as_gorengan(universe_db):
    df = build_universe("2026-05-25", universe_db, min_history_days=100)
    assert _status_for(df, "WLST1") == UniverseStatus.GORENGAN_SUSPECTED.value


def test_repeated_extreme_moves_flagged_as_gorengan(universe_db):
    df = build_universe("2026-05-25", universe_db, min_history_days=100)
    assert _status_for(df, "GRNG1") == UniverseStatus.GORENGAN_SUSPECTED.value


def test_single_extreme_move_not_flagged_as_gorengan(universe_db):
    # A single +30% day correctly trips the pre-existing (separate)
    # corporate-action heuristic — the point of this test is that it must
    # NOT be GORENGAN_SUSPECTED, since one big move isn't the repeated-
    # clustering pattern that heuristic is meant to catch.
    df = build_universe("2026-05-25", universe_db, min_history_days=100)
    status = _status_for(df, "NEWS1")
    assert status != UniverseStatus.GORENGAN_SUSPECTED.value
    assert status == UniverseStatus.CORPORATE_ACTION_WARNING.value


def test_moderate_liquidity_stock_eligible_under_new_thresholds(universe_db):
    df = build_universe(
        "2026-05-25", universe_db, min_history_days=100,
        min_median_value_20d=2e8, min_median_freq_20d=30.0,
    )
    assert _status_for(df, "MODL1") == UniverseStatus.ELIGIBLE.value


def test_moderate_liquidity_stock_excluded_under_old_thresholds(universe_db):
    df = build_universe(
        "2026-05-25", universe_db, min_history_days=100,
        min_median_value_20d=1e9, min_median_freq_20d=100.0,
    )
    assert _status_for(df, "MODL1") == UniverseStatus.LOW_LIQUIDITY.value


def test_gorengan_min_extreme_days_constant_is_reasonable():
    # Documents the calibration choice rather than testing a magic number blind.
    assert GORENGAN_MIN_EXTREME_DAYS >= 3
