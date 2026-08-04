"""SQLite connection and schema bootstrap utilities for Zaiden Trader."""
from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "zaiden_trader.db"


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS idx_stocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL COLLATE NOCASE UNIQUE,
    company_name TEXT NOT NULL,
    listing_date TEXT CHECK (listing_date IS NULL OR listing_date = '' OR date(listing_date) = listing_date),
    shares INTEGER NOT NULL DEFAULT 0 CHECK (shares >= 0),
    listing_board TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ownership_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_date TEXT NOT NULL CHECK (date(record_date) = record_date),
    share_code TEXT NOT NULL COLLATE NOCASE,
    issuer_name TEXT NOT NULL,
    investor_name TEXT NOT NULL COLLATE NOCASE,
    classification TEXT,
    local_foreign TEXT NOT NULL CHECK (local_foreign IN ('L', 'F', 'N')),
    nationality TEXT,
    domicile TEXT,
    scripless INTEGER NOT NULL DEFAULT 0 CHECK (scripless >= 0),
    scrip INTEGER NOT NULL DEFAULT 0 CHECK (scrip >= 0),
    percentage REAL NOT NULL CHECK (percentage >= 0 AND percentage <= 100),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_ownership_position UNIQUE (record_date, share_code, investor_name),
    CONSTRAINT fk_ownership_stock FOREIGN KEY (share_code)
        REFERENCES idx_stocks(code) ON UPDATE CASCADE ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_ownership_record_date ON ownership_positions(record_date DESC);
CREATE INDEX IF NOT EXISTS idx_ownership_month ON ownership_positions(substr(record_date, 1, 7));
CREATE INDEX IF NOT EXISTS idx_ownership_share_code ON ownership_positions(share_code);
CREATE INDEX IF NOT EXISTS idx_ownership_investor_name ON ownership_positions(investor_name);
CREATE INDEX IF NOT EXISTS idx_ownership_origin ON ownership_positions(local_foreign);
CREATE INDEX IF NOT EXISTS idx_stock_listing_board ON idx_stocks(listing_board);

CREATE TRIGGER IF NOT EXISTS trg_idx_stocks_updated_at
AFTER UPDATE ON idx_stocks
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE idx_stocks SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_ownership_updated_at
AFTER UPDATE ON ownership_positions
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE ownership_positions SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE VIEW IF NOT EXISTS v_ownership_positions AS
SELECT
    p.*,
    p.scripless + p.scrip AS total_shares,
    s.company_name AS master_company_name,
    s.listing_board
FROM ownership_positions AS p
JOIN idx_stocks AS s ON s.code = p.share_code;

-- Satu baris untuk satu saham pada satu hari bursa. Kolom mengikuti format
-- Stock Summary / Equity EoD IDX agar file resmi dapat diimpor tanpa tabel
-- per tahun.
CREATE TABLE IF NOT EXISTS ringkasan_saham_harian (
    tanggal TEXT NOT NULL CHECK (date(tanggal) = tanggal),
    kode_saham TEXT NOT NULL COLLATE NOCASE,
    id_stock_summary INTEGER,
    nama_perusahaan TEXT,
    remarks TEXT,
    sebelumnya REAL,
    harga_pembukaan REAL,
    perdagangan_pertama REAL,
    harga_tertinggi REAL,
    harga_terendah REAL,
    harga_penutupan REAL,
    perubahan REAL,
    volume INTEGER CHECK (volume IS NULL OR volume >= 0),
    nilai_transaksi REAL CHECK (nilai_transaksi IS NULL OR nilai_transaksi >= 0),
    frekuensi INTEGER CHECK (frekuensi IS NULL OR frekuensi >= 0),
    indeks_individual REAL,
    penawaran_jual REAL,
    volume_penawaran_jual INTEGER CHECK (volume_penawaran_jual IS NULL OR volume_penawaran_jual >= 0),
    penawaran_beli REAL,
    volume_penawaran_beli INTEGER CHECK (volume_penawaran_beli IS NULL OR volume_penawaran_beli >= 0),
    saham_tercatat INTEGER CHECK (saham_tercatat IS NULL OR saham_tercatat >= 0),
    saham_dapat_diperdagangkan INTEGER CHECK (saham_dapat_diperdagangkan IS NULL OR saham_dapat_diperdagangkan >= 0),
    bobot_indeks REAL,
    jual_asing REAL CHECK (jual_asing IS NULL OR jual_asing >= 0),
    beli_asing REAL CHECK (beli_asing IS NULL OR beli_asing >= 0),
    tanggal_delisting TEXT,
    volume_non_reguler INTEGER CHECK (volume_non_reguler IS NULL OR volume_non_reguler >= 0),
    nilai_non_reguler REAL CHECK (nilai_non_reguler IS NULL OR nilai_non_reguler >= 0),
    frekuensi_non_reguler INTEGER CHECK (frekuensi_non_reguler IS NULL OR frekuensi_non_reguler >= 0),
    sumber_file TEXT,
    diimpor_pada TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (tanggal, kode_saham)
);

CREATE INDEX IF NOT EXISTS idx_ringkasan_saham_kode_tanggal
    ON ringkasan_saham_harian(kode_saham, tanggal DESC);
CREATE INDEX IF NOT EXISTS idx_ringkasan_saham_tanggal
    ON ringkasan_saham_harian(tanggal DESC);
CREATE INDEX IF NOT EXISTS idx_ringkasan_saham_volume
    ON ringkasan_saham_harian(volume DESC);

CREATE TABLE IF NOT EXISTS idx_daily_sync_log (
    tanggal TEXT PRIMARY KEY CHECK (date(tanggal) = tanggal),
    status TEXT NOT NULL CHECK (status IN ('success', 'no_data', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 1,
    http_status INTEGER,
    records_total INTEGER,
    row_count INTEGER NOT NULL DEFAULT 0,
    payload_sha256 TEXT,
    error TEXT,
    fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_daily_sync_status
    ON idx_daily_sync_log(status, tanggal);

-- Ringkasan indeks harian IDX (IHSG/COMPOSITE, LQ45, dst) dari endpoint resmi
-- GetIndexSummary. Semua indeks yang dikembalikan IDX disimpan agar tersedia
-- untuk analisis di masa depan, meski dashboard saat ini hanya memakai COMPOSITE.
CREATE TABLE IF NOT EXISTS idx_index_daily (
    tanggal TEXT NOT NULL CHECK (date(tanggal) = tanggal),
    index_code TEXT NOT NULL,
    previous REAL,
    highest REAL,
    lowest REAL,
    close REAL,
    change REAL,
    volume REAL,
    value REAL,
    frequency REAL,
    number_of_stock REAL,
    market_capital REAL,
    fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (tanggal, index_code)
);

CREATE INDEX IF NOT EXISTS idx_index_daily_code_tanggal
    ON idx_index_daily(index_code, tanggal DESC);

CREATE TABLE IF NOT EXISTS idx_index_sync_log (
    tanggal TEXT PRIMARY KEY CHECK (date(tanggal) = tanggal),
    status TEXT NOT NULL CHECK (status IN ('success', 'no_data', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 1,
    http_status INTEGER,
    index_count INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_index_sync_status
    ON idx_index_sync_log(status, tanggal);

CREATE TABLE IF NOT EXISTS idx_daily_derived_features (
    tanggal TEXT NOT NULL,
    kode_saham TEXT NOT NULL,
    return_1d REAL,
    return_5d REAL,
    return_20d REAL,
    return_60d REAL,
    volatility_5d REAL,
    volatility_20d REAL,
    volatility_60d REAL,
    ma_5d REAL,
    ma_20d REAL,
    ma_60d REAL,
    zscore_5d REAL,
    zscore_20d REAL,
    zscore_60d REAL,
    drawdown_20d REAL,
    drawdown_60d REAL,
    volume_ma_5d REAL,
    volume_ma_20d REAL,
    volume_zscore_20d REAL,
    foreign_net_1d REAL,
    foreign_net_5d REAL,
    foreign_net_20d REAL,
    foreign_ratio REAL,
    turnover_ratio REAL,
    value_ma_20d REAL,
    calculated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (tanggal, kode_saham)
);

CREATE TABLE IF NOT EXISTS idx_fundamental_sync_log (
    tanggal TEXT PRIMARY KEY CHECK (date(tanggal) = tanggal),
    status TEXT NOT NULL CHECK (status IN ('success', 'no_change', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 1,
    http_status INTEGER,
    records_total INTEGER,
    payload_sha256 TEXT,
    error TEXT,
    fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS idx_fundamental_snapshots (
    stock_code TEXT NOT NULL,
    source_as_of_date TEXT NOT NULL,
    financial_period TEXT NOT NULL,
    nama_perusahaan TEXT,
    sektor TEXT,
    subsektor TEXT,
    industri TEXT,
    subindustri TEXT,
    papan_pencatatan TEXT,
    tanggal_referensi_harga TEXT,
    tanggal_publikasi_laporan TEXT,
    tanggal_informasi_mulai_tersedia_bagi_publik TEXT,
    per REAL,
    pbv REAL,
    roe REAL,
    roa REAL,
    der REAL,
    npm REAL,
    revenue REAL,
    market_cap REAL,
    change_4w REAL,
    change_13w REAL,
    change_26w REAL,
    change_52w REAL,
    change_mtd REAL,
    change_ytd REAL,
    payload_hash TEXT,
    waktu_sinkronisasi TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code, source_as_of_date, financial_period)
);

CREATE INDEX IF NOT EXISTS idx_fundamental_stock_date
    ON idx_fundamental_snapshots(stock_code, source_as_of_date DESC);

-- Historical annual/quarterly income-statement line items per stock, used by
-- the Growth Consistency (Compounder) screener. Depth is limited by what the
-- upstream source (yfinance) exposes — typically ~4 fiscal years / ~5 quarters,
-- not the full 2020-present range, since Yahoo Finance does not retain older
-- filings for most IDX issuers.
CREATE TABLE IF NOT EXISTS idx_fundamental_financials (
    stock_code TEXT NOT NULL,
    period_type TEXT NOT NULL CHECK (period_type IN ('annual', 'quarterly')),
    period_end TEXT NOT NULL CHECK (date(period_end) = period_end),
    fiscal_year INTEGER,
    fiscal_quarter INTEGER,
    revenue REAL,
    net_income REAL,
    operating_income REAL,
    gross_profit REAL,
    eps REAL,
    total_assets REAL,
    total_equity REAL,
    total_liabilities REAL,
    source TEXT NOT NULL DEFAULT 'yfinance',
    fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code, period_type, period_end)
);

CREATE INDEX IF NOT EXISTS idx_fund_fin_stock
    ON idx_fundamental_financials(stock_code, period_type, period_end DESC);

-- Per-period valuation ratios (PER/PBV computed from price-at-period-end +
-- financials-at-that-period), derived from idx_fundamental_financials. Kept
-- as its own table with its own rows -- never written into or derived by
-- scanning ringkasan_saham_harian's 1.3M+ rows at read time.
CREATE TABLE IF NOT EXISTS idx_fundamental_ratio_history (
    stock_code TEXT NOT NULL,
    period_type TEXT NOT NULL CHECK (period_type IN ('quarterly','semester','nine_month','annual','projection')),
    period_label TEXT NOT NULL,
    period_end TEXT NOT NULL CHECK (date(period_end) = period_end),
    net_income_cumulative REAL,
    revenue_cumulative REAL,
    annualized_net_income REAL,
    annualized_eps REAL,
    book_value_per_share REAL,
    price_close REAL,
    per REAL,
    pbv REAL,
    market_cap REAL,
    shares_outstanding REAL,
    is_projected INTEGER NOT NULL DEFAULT 0,
    computed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code, period_type, period_label)
);

CREATE INDEX IF NOT EXISTS idx_fund_ratio_hist_stock
    ON idx_fundamental_ratio_history(stock_code, period_end DESC);

-- Detected corporate-action events (stock splits, reverse splits, rights
-- issues, private placements) inferred from day-over-day changes in
-- saham_tercatat (listed shares outstanding), which is 100% populated in
-- ringkasan_saham_harian unlike open price. Never written by mutating that
-- source table -- this is purely additive so historical charts/backtests
-- can look up known events and adjust on demand instead of being silently
-- wrong. event_type is a best-effort classification, not a verified feed
-- of official IDX corporate action announcements -- see confidence.
CREATE TABLE IF NOT EXISTS idx_corporate_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code TEXT NOT NULL,
    event_date TEXT NOT NULL CHECK (date(event_date) = event_date),
    shares_before REAL,
    shares_after REAL,
    shares_ratio REAL,
    price_before REAL,
    price_after REAL,
    price_ratio REAL,
    event_type TEXT NOT NULL CHECK (event_type IN ('SPLIT_LIKE', 'CAPITAL_CHANGE')),
    confidence TEXT NOT NULL CHECK (confidence IN ('HIGH', 'MEDIUM')),
    detected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (stock_code, event_date)
);

CREATE INDEX IF NOT EXISTS idx_corp_actions_stock
    ON idx_corporate_actions(stock_code, event_date DESC);

CREATE TABLE IF NOT EXISTS riwayat_impor_ringkasan_saham (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nama_file TEXT NOT NULL,
    checksum_sha256 TEXT NOT NULL UNIQUE,
    jumlah_baris INTEGER NOT NULL,
    diimpor_pada TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE VIEW IF NOT EXISTS v_ohlcv_harian AS
SELECT tanggal, kode_saham, nama_perusahaan,
       harga_pembukaan AS open, harga_tertinggi AS high,
       harga_terendah AS low, harga_penutupan AS close, volume,
       nilai_transaksi, frekuensi
FROM ringkasan_saham_harian;

-- Tabel broker dibuat terpisah agar data tarik broker tidak bercampur
-- dengan ringkasan saham harian emiten.
CREATE TABLE IF NOT EXISTS master_broker (
    kode_broker TEXT PRIMARY KEY,
    nama_broker TEXT NOT NULL,
    izin TEXT,
    status TEXT,
    sumber TEXT NOT NULL DEFAULT 'IDX',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ringkasan_broker_harian (
    tanggal TEXT NOT NULL CHECK (date(tanggal) = tanggal),
    kode_broker TEXT NOT NULL,
    volume INTEGER NOT NULL DEFAULT 0,
    nilai INTEGER NOT NULL DEFAULT 0,
    frekuensi INTEGER NOT NULL DEFAULT 0,
    buy_volume INTEGER,
    buy_value INTEGER,
    buy_frequency INTEGER,
    sell_volume INTEGER,
    sell_value INTEGER,
    sell_frequency INTEGER,
    sumber TEXT NOT NULL DEFAULT 'IDX',
    source_file TEXT,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (tanggal, kode_broker),
    FOREIGN KEY (kode_broker)
        REFERENCES master_broker(kode_broker)
        ON UPDATE CASCADE
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS broker_saham_harian (
    tanggal TEXT NOT NULL CHECK (date(tanggal) = tanggal),
    kode_saham TEXT NOT NULL COLLATE NOCASE,
    kode_broker TEXT NOT NULL,
    buy_lot INTEGER NOT NULL DEFAULT 0,
    buy_value INTEGER NOT NULL DEFAULT 0,
    buy_avg REAL NOT NULL DEFAULT 0,
    sell_lot INTEGER NOT NULL DEFAULT 0,
    sell_value INTEGER NOT NULL DEFAULT 0,
    sell_avg REAL NOT NULL DEFAULT 0,
    net_lot INTEGER NOT NULL DEFAULT 0,
    net_value INTEGER NOT NULL DEFAULT 0,
    sumber TEXT NOT NULL DEFAULT 'IMPORT',
    source_file TEXT,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (tanggal, kode_saham, kode_broker),
    FOREIGN KEY (kode_broker)
        REFERENCES master_broker(kode_broker)
        ON UPDATE CASCADE
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_ringkasan_broker_tanggal
    ON ringkasan_broker_harian(tanggal DESC);
CREATE INDEX IF NOT EXISTS idx_ringkasan_broker_kode_tanggal
    ON ringkasan_broker_harian(kode_broker, tanggal DESC);
CREATE INDEX IF NOT EXISTS idx_broker_saham_broker_tanggal
    ON broker_saham_harian(kode_broker, tanggal DESC, net_value DESC);
CREATE INDEX IF NOT EXISTS idx_broker_saham_saham_tanggal
    ON broker_saham_harian(kode_saham, tanggal DESC, net_value DESC);

INSERT OR IGNORE INTO app_settings(key, value) VALUES
    ('application_name', 'Zaiden Trader'),
    ('database_engine', 'SQLite');
"""


def migrate() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH, timeout=30) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        # WAL lets the live server keep reading while a background job (training,
        # prediction, fundamental sync) writes — DELETE mode serializes all
        # readers/writers and was causing "database is locked" errors.
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.executescript(SCHEMA_SQL)
        # Kolom tambahan untuk database versi 2 yang sudah pernah dibuat.
        columns = {
            row[1] for row in connection.execute(
                "PRAGMA table_info(ringkasan_saham_harian)"
            )
        }
        if "id_stock_summary" not in columns:
            connection.execute(
                "ALTER TABLE ringkasan_saham_harian ADD COLUMN id_stock_summary INTEGER"
            )
        if "remarks" not in columns:
            connection.execute(
                "ALTER TABLE ringkasan_saham_harian ADD COLUMN remarks TEXT"
            )
        connection.execute("PRAGMA user_version = 3")


def schema_version() -> int:
    with connect() as connection:
        row = connection.execute("PRAGMA user_version").fetchone()
        return int(row[0])
