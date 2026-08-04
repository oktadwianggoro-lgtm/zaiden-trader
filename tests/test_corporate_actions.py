"""
tests/test_corporate_actions.py
Validates the corporate-action detector (tools/detect_corporate_actions.py)
against synthetic split / capital-raise / no-op scenarios, and checks that
re-running it is idempotent (safe for the daily scheduled task).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from db import SCHEMA_SQL
from tools.detect_corporate_actions import detect_for_all_stocks


@pytest.fixture
def mini_db(tmp_path: Path) -> str:
    db_path = str(tmp_path / "mini.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)  # creates idx_corporate_actions + ringkasan_saham_harian

    def add_row(code, d, close, prev, shares, volume=1000):
        conn.execute(
            """INSERT INTO ringkasan_saham_harian
               (tanggal, kode_saham, sebelumnya, harga_penutupan, volume, saham_tercatat)
               VALUES (?,?,?,?,?,?)""",
            (d, code, prev, close, volume, shares),
        )

    # SPLT: clean 5:1 split on day 3 — price divides by 5, shares multiply by 5
    add_row("SPLT", "2026-01-01", 1000, 1000, 1_000_000)
    add_row("SPLT", "2026-01-02", 1000, 1000, 1_000_000)
    add_row("SPLT", "2026-01-03", 200, 1000, 5_000_000)

    # RASE: 10% capital raise — shares +10%, price essentially unchanged
    add_row("RASE", "2026-01-01", 500, 500, 2_000_000)
    add_row("RASE", "2026-01-02", 505, 500, 2_000_000)
    add_row("RASE", "2026-01-03", 505, 505, 2_200_000)

    # STBL: nothing happens, ever
    add_row("STBL", "2026-01-01", 300, 300, 500_000)
    add_row("STBL", "2026-01-02", 305, 300, 500_000)
    add_row("STBL", "2026-01-03", 310, 305, 500_000)

    conn.commit()
    conn.close()
    return db_path


def test_clean_split_detected_as_high_confidence(mini_db):
    result = detect_for_all_stocks(mini_db)
    assert result["status"] == "success"

    conn = sqlite3.connect(mini_db)
    row = conn.execute(
        "SELECT event_type, confidence, shares_ratio, price_ratio FROM idx_corporate_actions "
        "WHERE stock_code='SPLT' AND event_date='2026-01-03'"
    ).fetchone()
    conn.close()

    assert row is not None
    event_type, confidence, shares_ratio, price_ratio = row
    assert event_type == "SPLIT_LIKE"
    assert confidence == "HIGH"
    assert shares_ratio == pytest.approx(5.0, abs=0.01)
    assert price_ratio == pytest.approx(5.0, abs=0.01)


def test_capital_raise_detected_as_medium_confidence_capital_change(mini_db):
    detect_for_all_stocks(mini_db)
    conn = sqlite3.connect(mini_db)
    row = conn.execute(
        "SELECT event_type, confidence FROM idx_corporate_actions "
        "WHERE stock_code='RASE' AND event_date='2026-01-03'"
    ).fetchone()
    conn.close()

    assert row is not None
    event_type, confidence = row
    # Shares moved (+10%) but price didn't move inversely -> capital change,
    # not a split, and never HIGH confidence.
    assert event_type == "CAPITAL_CHANGE"
    assert confidence == "MEDIUM"


def test_stable_shares_produce_no_events(mini_db):
    detect_for_all_stocks(mini_db)
    conn = sqlite3.connect(mini_db)
    count = conn.execute(
        "SELECT COUNT(*) FROM idx_corporate_actions WHERE stock_code='STBL'"
    ).fetchone()[0]
    conn.close()
    assert count == 0


def test_rerun_is_idempotent_no_duplicate_rows(mini_db):
    """The daily scheduled task re-runs this every day — running it twice on
    unchanged data must not create duplicate rows (UNIQUE stock_code+event_date
    with ON CONFLICT DO UPDATE)."""
    detect_for_all_stocks(mini_db)
    first_count = sqlite3.connect(mini_db).execute("SELECT COUNT(*) FROM idx_corporate_actions").fetchone()[0]

    detect_for_all_stocks(mini_db)
    second_count = sqlite3.connect(mini_db).execute("SELECT COUNT(*) FROM idx_corporate_actions").fetchone()[0]

    assert first_count == second_count
    assert first_count > 0
