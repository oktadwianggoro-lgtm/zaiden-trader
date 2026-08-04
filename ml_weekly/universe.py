"""
ml_weekly/universe.py
Point-in-time eligible universe builder.

Key principle: For any date T, only use information available BEFORE T.
No survivorship bias: use stocks available at T, not current membership.
"""
from __future__ import annotations
import sqlite3
import logging
from enum import Enum

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


class UniverseStatus(str, Enum):
    ELIGIBLE = "ELIGIBLE"
    LOW_LIQUIDITY = "LOW_LIQUIDITY"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    SUSPENDED = "SUSPENDED_OR_INACTIVE"
    PENNY_STOCK = "PENNY_STOCK"
    MISSING_DATA = "MISSING_DATA"
    CORPORATE_ACTION_WARNING = "CORPORATE_ACTION_WARNING"
    GORENGAN_SUSPECTED = "GORENGAN_SUSPECTED"


# Days within the lookback window with an extreme single-day move (near IDX's
# auto-reject band) required before the statistical side of the gorengan
# filter fires. One extreme day is often a real news event or corporate
# action (already handled by has_corp_action_warning) — repeated extreme
# swings clustered together is the actual pump-and-dump signature.
GORENGAN_EXTREME_MOVE_THRESHOLD = 0.20
GORENGAN_MIN_EXTREME_DAYS = 4
GORENGAN_LOOKBACK_DAYS = 120


def build_universe(
    as_of_date: str,
    db_path: str,
    min_history_days: int = 120,
    min_median_value_20d: float = 2e8,
    min_median_freq_20d: float = 30.0,
    min_price: float = 50.0,
    max_zero_vol_ratio: float = 0.20,
    lookback_days: int = 252,
    conn: sqlite3.Connection | None = None,
) -> pd.DataFrame:
    """
    Build point-in-time eligible universe for a given as_of_date.

    Returns DataFrame with columns:
        ticker, status, history_days, median_close, median_value_20d,
        median_freq_20d, zero_vol_ratio, last_active_date,
        has_corp_action_warning, days_since_active
    """
    should_close = conn is None
    if conn is None:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

    try:
        # Get all stocks with data up to as_of_date (full history count)
        q = """
        SELECT
            kode_saham,
            COUNT(DISTINCT tanggal) AS history_days,
            MAX(tanggal) AS last_active_date,
            AVG(harga_penutupan) AS avg_close,
            SUM(CASE WHEN (volume IS NULL OR volume = 0) THEN 1.0 ELSE 0.0 END)
                / COUNT(*) AS zero_vol_ratio
        FROM ringkasan_saham_harian
        WHERE tanggal <= ? AND harga_penutupan IS NOT NULL
        GROUP BY kode_saham
        HAVING COUNT(*) >= 1
        """
        df_base = pd.read_sql_query(q, conn, params=(as_of_date,))

        if df_base.empty:
            return pd.DataFrame()

        # Get 20-day stats
        q20 = """
        SELECT kode_saham,
            -- Use last 20 trading days up to as_of_date for each stock
            AVG(harga_penutupan) AS close_20d,
            AVG(nilai_transaksi) AS value_20d,
            AVG(frekuensi) AS freq_20d
        FROM (
            SELECT kode_saham, harga_penutupan, nilai_transaksi, frekuensi,
                ROW_NUMBER() OVER (PARTITION BY kode_saham ORDER BY tanggal DESC) AS rn
            FROM ringkasan_saham_harian
            WHERE tanggal <= ? AND harga_penutupan IS NOT NULL AND harga_penutupan > 0
        ) t
        WHERE rn <= 20
        GROUP BY kode_saham
        """
        df_20 = pd.read_sql_query(q20, conn, params=(as_of_date,))

        # Corporate action warning. Two signals, combined:
        #  1. Verified events from idx_corporate_actions (detected from
        #     saham_tercatat share-count changes, cross-checked against the
        #     matching price move — see tools/detect_corporate_actions.py).
        #     This catches real splits/reverse-splits/rights issues with
        #     concrete evidence, not just a big price move.
        #  2. The older extreme-return heuristic, kept as a fallback for
        #     whatever the share-count method might miss (e.g. a single
        #     bad print, suspension-then-gap-resume).
        # Either signal is enough to warn — but callers that need to
        # distinguish "confirmed" from "merely suspicious" can join
        # idx_corporate_actions directly for the confidence/ratio detail.
        try:
            q_ca_verified = """
            SELECT DISTINCT stock_code AS kode_saham
            FROM idx_corporate_actions
            WHERE event_date <= ? AND event_date >= DATE(?, '-120 days')
                AND confidence = 'HIGH'
            """
            verified_ca_stocks = set(pd.read_sql_query(q_ca_verified, conn, params=(as_of_date, as_of_date))["kode_saham"].tolist())
        except Exception:
            verified_ca_stocks = set()  # table may not exist yet on an unmigrated DB

        q_ca = """
        SELECT DISTINCT kode_saham
        FROM ringkasan_saham_harian
        WHERE tanggal <= ? AND tanggal >= DATE(?, '-120 days')
            AND sebelumnya > 0
            AND ABS((harga_penutupan - sebelumnya) / sebelumnya) > 0.25
        """
        heuristic_ca_stocks = set(pd.read_sql_query(q_ca, conn, params=(as_of_date, as_of_date))["kode_saham"].tolist())
        ca_stocks = verified_ca_stocks | heuristic_ca_stocks

        # ── Gorengan (pump-and-dump) detection ──────────────────────────────
        # Two independent signals, either is enough to exclude:
        #   1. IDX's own "Papan Pemantauan Khusus" (Special Monitoring Board)
        #      designation — authoritative, not a guess.
        #   2. Statistical: repeated extreme single-day moves clustered in a
        #      short window, which legitimate blue-chips essentially never
        #      show (one big move is often real news; several is a pattern).
        try:
            q_watchlist_board = "SELECT code AS kode_saham FROM idx_stocks WHERE listing_board = 'Watchlist'"
            watchlist_board_stocks = set(pd.read_sql_query(q_watchlist_board, conn)["kode_saham"].tolist())
        except Exception:
            watchlist_board_stocks = set()

        q_gorengan_stat = """
        SELECT kode_saham, COUNT(*) AS extreme_days
        FROM ringkasan_saham_harian
        WHERE tanggal <= ? AND tanggal >= DATE(?, ?)
            AND sebelumnya > 0
            AND ABS((harga_penutupan - sebelumnya) / sebelumnya) >= ?
        GROUP BY kode_saham
        HAVING COUNT(*) >= ?
        """
        gorengan_stat_stocks = set(pd.read_sql_query(
            q_gorengan_stat, conn,
            params=(as_of_date, as_of_date, f"-{GORENGAN_LOOKBACK_DAYS} days",
                    GORENGAN_EXTREME_MOVE_THRESHOLD, GORENGAN_MIN_EXTREME_DAYS),
        )["kode_saham"].tolist())

        gorengan_stocks = watchlist_board_stocks | gorengan_stat_stocks

        # Days since last active (last day with volume > 0)
        q_active = """
        SELECT kode_saham, MAX(tanggal) AS last_active_date
        FROM ringkasan_saham_harian
        WHERE tanggal <= ? AND volume > 0 AND harga_penutupan > 0
        GROUP BY kode_saham
        """
        df_active = pd.read_sql_query(q_active, conn, params=(as_of_date,))

        # Merge
        df = df_base.merge(df_20, on="kode_saham", how="left")
        df = df.merge(df_active.rename(columns={"last_active_date": "last_vol_date"}),
                      on="kode_saham", how="left")

        df["has_corp_action_warning"] = df["kode_saham"].isin(ca_stocks)
        df["has_verified_corp_action"] = df["kode_saham"].isin(verified_ca_stocks)
        df["has_gorengan_flag"] = df["kode_saham"].isin(gorengan_stocks)
        df["on_watchlist_board"] = df["kode_saham"].isin(watchlist_board_stocks)

        # Calculate days since active
        as_of_dt = pd.to_datetime(as_of_date)
        df["last_vol_date_dt"] = pd.to_datetime(df["last_vol_date"], errors="coerce")
        df["days_since_active"] = (as_of_dt - df["last_vol_date_dt"]).dt.days.fillna(9999).astype(int)

        # Fill NaN
        df["value_20d"] = df["value_20d"].fillna(0)
        df["freq_20d"] = df["freq_20d"].fillna(0)
        df["close_20d"] = df["close_20d"].fillna(0)
        df["zero_vol_ratio"] = df["zero_vol_ratio"].fillna(1.0)

        # Classify status — return plain strings for pandas 3.x compatibility
        def classify(row) -> str:
            if row["history_days"] < min_history_days:
                return UniverseStatus.INSUFFICIENT_HISTORY.value
            if row["close_20d"] < min_price and row["close_20d"] > 0:
                return UniverseStatus.PENNY_STOCK.value
            if row["days_since_active"] > 10:
                return UniverseStatus.SUSPENDED.value
            if row["zero_vol_ratio"] > max_zero_vol_ratio:
                return UniverseStatus.SUSPENDED.value
            if row["has_gorengan_flag"]:
                return UniverseStatus.GORENGAN_SUSPECTED.value
            if row["value_20d"] < min_median_value_20d:
                return UniverseStatus.LOW_LIQUIDITY.value
            if row["freq_20d"] < min_median_freq_20d:
                return UniverseStatus.LOW_LIQUIDITY.value
            if row["has_corp_action_warning"]:
                return UniverseStatus.CORPORATE_ACTION_WARNING.value
            return UniverseStatus.ELIGIBLE.value

        df["status"] = df.apply(classify, axis=1)

        result = df[[
            "kode_saham", "status", "history_days", "close_20d",
            "value_20d", "freq_20d", "zero_vol_ratio",
            "last_vol_date", "has_corp_action_warning", "has_verified_corp_action",
            "has_gorengan_flag", "on_watchlist_board", "days_since_active"
        ]].rename(columns={
            "kode_saham": "ticker",
            "close_20d": "median_close_20d",
            "value_20d": "median_value_20d",
            "freq_20d": "median_freq_20d",
        })

        return result

    finally:
        if should_close:
            conn.close()


def get_eligible_tickers(
    as_of_date: str,
    db_path: str,
    include_corp_action: bool = True,
    **kwargs,
) -> list[str]:
    """Return list of eligible tickers for a given date."""
    df = build_universe(as_of_date, db_path, **kwargs)
    statuses = [UniverseStatus.ELIGIBLE.value]
    if include_corp_action:
        statuses.append(UniverseStatus.CORPORATE_ACTION_WARNING.value)
    return df[df["status"].isin(statuses)]["ticker"].tolist()


def build_universe_series(
    trading_dates: list[str],
    db_path: str,
    **kwargs,
) -> dict[str, list[str]]:
    """
    Build point-in-time universe for multiple dates.
    Used in walk-forward validation.
    Returns {date: [eligible_tickers]} mapping.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    result = {}

    try:
        for dt in trading_dates:
            eligible = get_eligible_tickers(dt, db_path, conn=conn, **kwargs)
            result[dt] = eligible
            log.debug("Universe %s: %d eligible stocks", dt, len(eligible))
    finally:
        conn.close()

    return result
