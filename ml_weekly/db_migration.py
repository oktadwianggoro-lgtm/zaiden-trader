"""
ml_weekly/db_migration.py
Safe, non-destructive migration to create ml_weekly_* tables.
Source tables (ringkasan_saham_harian, etc.) are NEVER modified.
"""
from __future__ import annotations
import sqlite3
import datetime
import logging

log = logging.getLogger(__name__)


MIGRATION_SQL = """
-- ============================================================
-- ml_weekly_model_runs
-- Registry of all training runs (experiment tracking)
-- ============================================================
CREATE TABLE IF NOT EXISTS ml_weekly_model_runs (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    model_version         TEXT    NOT NULL,
    model_name            TEXT    NOT NULL,
    feature_set           TEXT    NOT NULL DEFAULT 'price_volume',
    target_definition     TEXT    NOT NULL DEFAULT 'close5_2pct',
    training_start        TEXT    NOT NULL,
    training_end          TEXT    NOT NULL,
    validation_start      TEXT    NOT NULL,
    validation_end        TEXT    NOT NULL,
    holdout_start         TEXT    NOT NULL,
    holdout_end           TEXT,
    probability_threshold REAL    NOT NULL DEFAULT 0.85,
    model_parameters_json TEXT,
    feature_list_json     TEXT,
    dataset_hash          TEXT,
    source_max_date       TEXT,
    status                TEXT    NOT NULL DEFAULT 'pending',
    validation_status     TEXT    NOT NULL DEFAULT 'NOT_VERIFIED',
    holdout_status        TEXT    NOT NULL DEFAULT 'NOT_EVALUATED',
    notes                 TEXT,
    created_at            TEXT    NOT NULL DEFAULT (datetime('now')),
    completed_at          TEXT
);

CREATE INDEX IF NOT EXISTS idx_mlwr_status ON ml_weekly_model_runs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_mlwr_version ON ml_weekly_model_runs(model_version);

-- ============================================================
-- ml_weekly_predictions
-- Per-stock, per-date predictions
-- ============================================================
CREATE TABLE IF NOT EXISTS ml_weekly_predictions (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    model_run_id          INTEGER NOT NULL REFERENCES ml_weekly_model_runs(id),
    prediction_date       TEXT    NOT NULL,
    ticker                TEXT    NOT NULL,
    horizon_days          INTEGER NOT NULL DEFAULT 5,
    current_close         REAL,
    next_open             REAL,
    predicted_probability REAL    NOT NULL,
    calibrated_probability REAL,
    predicted_return      REAL,
    expected_upside       REAL,
    expected_drawdown     REAL,
    signal_status         TEXT    NOT NULL DEFAULT 'NO_SIGNAL',
    signal_threshold      REAL,
    technical_score       REAL,
    volume_score          REAL,
    foreign_flow_score    REAL,
    market_regime         TEXT,
    sector_regime         TEXT,
    reason_codes_json     TEXT,
    risk_flags_json       TEXT,
    feature_values_json   TEXT,
    ensemble_probs_json   TEXT,
    target_price          REAL,
    stop_price            REAL,
    realized_return       REAL,
    realized_label        INTEGER,
    outcome_status        TEXT    DEFAULT 'PENDING',
    -- Decision/risk-gate fields: see predict.compute_decision_status()
    decision_status       TEXT,   -- QUALIFIED|WATCHLIST|NO_TRADE|ABSTAIN|DATA_INVALID|MODEL_UNAVAILABLE|SIGNAL_EXPIRED
    confidence_tier       TEXT,   -- SANGAT_KUAT|KUAT|CUKUP|LEMAH, capped by holdout validation status
    decision_reason       TEXT,
    model_coverage_json   TEXT,   -- {"expected": N, "contributed": M}
    net_risk_reward       REAL,   -- reward:risk ratio after transaction costs
    net_expected_value    REAL,   -- prob*net_reward - (1-prob)*net_risk
    entry_zone_low        REAL,
    entry_zone_high       REAL,
    created_at            TEXT    NOT NULL DEFAULT (datetime('now')),
    evaluated_at          TEXT,
    UNIQUE(model_run_id, prediction_date, ticker, horizon_days)
);

CREATE INDEX IF NOT EXISTS idx_mlwp_date ON ml_weekly_predictions(prediction_date);
CREATE INDEX IF NOT EXISTS idx_mlwp_ticker ON ml_weekly_predictions(ticker);
CREATE INDEX IF NOT EXISTS idx_mlwp_status ON ml_weekly_predictions(signal_status);
CREATE INDEX IF NOT EXISTS idx_mlwp_model ON ml_weekly_predictions(model_run_id);
CREATE INDEX IF NOT EXISTS idx_mlwp_prob ON ml_weekly_predictions(calibrated_probability DESC);
CREATE INDEX IF NOT EXISTS idx_mlwp_outcome ON ml_weekly_predictions(outcome_status, prediction_date);

-- ============================================================
-- ml_weekly_backtest_metrics
-- Metrics per fold / evaluation split
-- ============================================================
CREATE TABLE IF NOT EXISTS ml_weekly_backtest_metrics (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    model_run_id          INTEGER NOT NULL REFERENCES ml_weekly_model_runs(id),
    evaluation_split      TEXT    NOT NULL,  -- 'train', 'validation', 'holdout', 'wf_fold_N'
    period_start          TEXT    NOT NULL,
    period_end            TEXT    NOT NULL,
    threshold             REAL    NOT NULL,
    signal_count          INTEGER NOT NULL DEFAULT 0,
    correct_count         INTEGER NOT NULL DEFAULT 0,
    false_positive_count  INTEGER NOT NULL DEFAULT 0,
    precision             REAL,
    precision_ci_lower    REAL,
    precision_ci_upper    REAL,
    coverage              REAL,
    recall                REAL,
    pr_auc                REAL,
    brier_score           REAL,
    gross_return          REAL,
    net_return            REAL,
    profit_factor         REAL,
    maximum_drawdown      REAL,
    sharpe_ratio          REAL,
    win_rate              REAL,
    expectancy            REAL,
    metrics_json          TEXT,
    notes                 TEXT,
    created_at            TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_mlwbm_run ON ml_weekly_backtest_metrics(model_run_id);
CREATE INDEX IF NOT EXISTS idx_mlwbm_split ON ml_weekly_backtest_metrics(evaluation_split, period_start);

-- ============================================================
-- ml_weekly_feature_registry
-- Catalog of all features used
-- ============================================================
CREATE TABLE IF NOT EXISTS ml_weekly_feature_registry (
    feature_name          TEXT    PRIMARY KEY,
    feature_group         TEXT    NOT NULL,
    description           TEXT,
    formula               TEXT,
    lookback              INTEGER,
    data_source           TEXT    NOT NULL DEFAULT 'ringkasan_saham_harian',
    availability_rule     TEXT,
    enabled               INTEGER NOT NULL DEFAULT 1,
    created_at            TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at            TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ============================================================
-- ml_weekly_thresholds
-- Locked thresholds per model version / regime
-- ============================================================
CREATE TABLE IF NOT EXISTS ml_weekly_thresholds (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    model_run_id          INTEGER NOT NULL REFERENCES ml_weekly_model_runs(id),
    threshold_type        TEXT    NOT NULL DEFAULT 'global',  -- 'global', 'regime_*'
    market_regime         TEXT,
    threshold_value       REAL    NOT NULL,
    validation_precision  REAL,
    validation_coverage   REAL,
    validation_signals    INTEGER,
    approval_status       TEXT    NOT NULL DEFAULT 'pending',  -- 'pending', 'approved', 'rejected'
    effective_date        TEXT    NOT NULL,
    notes                 TEXT,
    created_at            TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_mlwt_run ON ml_weekly_thresholds(model_run_id, threshold_type);

-- ============================================================
-- ml_weekly_job_logs
-- Pipeline execution logs
-- ============================================================
CREATE TABLE IF NOT EXISTS ml_weekly_job_logs (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name              TEXT    NOT NULL,
    started_at            TEXT    NOT NULL,
    completed_at          TEXT,
    source_max_date       TEXT,
    records_processed     INTEGER DEFAULT 0,
    status                TEXT    NOT NULL DEFAULT 'running',  -- 'running', 'success', 'failed'
    error_message         TEXT,
    metadata_json         TEXT
);

CREATE INDEX IF NOT EXISTS idx_mlwjl_job ON ml_weekly_job_logs(job_name, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_mlwjl_status ON ml_weekly_job_logs(status, started_at DESC);
"""


def run_migration(db_path: str | None = None) -> dict:
    """
    Run non-destructive migration: only creates new ml_weekly_* tables.
    Never touches source tables.
    Returns status dict.
    """
    from .config import DB_PATH as DEFAULT_DB
    path = db_path or str(DEFAULT_DB)

    started = datetime.datetime.now().isoformat()
    tables_created = []
    tables_existing = []
    errors = []

    try:
        with sqlite3.connect(path) as conn:
            # Verify source tables are untouched
            existing = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}

            # Execute migration
            conn.executescript(MIGRATION_SQL)
            conn.commit()

            # Report what was created
            after = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'ml_weekly_%'"
            )}

            tables_created = sorted(after - (existing & after))
            tables_existing = sorted(after & existing & {t for t in existing if t.startswith("ml_weekly_")})

        log.info("Migration complete: created=%s", tables_created)
        return {
            "status": "success",
            "started_at": started,
            "completed_at": datetime.datetime.now().isoformat(),
            "tables_created": tables_created,
            "tables_existing": tables_existing,
            "errors": errors,
        }

    except Exception as exc:
        log.exception("Migration failed: %s", exc)
        return {
            "status": "failed",
            "started_at": started,
            "completed_at": datetime.datetime.now().isoformat(),
            "tables_created": tables_created,
            "tables_existing": tables_existing,
            "errors": [str(exc)],
        }


def verify_source_tables_intact(db_path: str | None = None) -> bool:
    """Verify that source tables have not been modified."""
    from .config import DB_PATH as DEFAULT_DB
    path = db_path or str(DEFAULT_DB)

    required_tables = {
        "ringkasan_saham_harian",
        "ringkasan_broker_harian",
        "ownership_positions",
        "idx_stocks",
        "idx_daily_sync_log",
        "master_broker",
    }

    with sqlite3.connect(path) as conn:
        existing = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}

    missing = required_tables - existing
    if missing:
        log.error("Source tables missing: %s", missing)
        return False
    return True


def log_job_start(job_name: str, db_path: str | None = None, metadata: dict | None = None) -> int:
    """Log a pipeline job start. Returns job id."""
    import json as _json
    from .config import DB_PATH as DEFAULT_DB
    path = db_path or str(DEFAULT_DB)

    with sqlite3.connect(path) as conn:
        cur = conn.execute(
            """INSERT INTO ml_weekly_job_logs
               (job_name, started_at, status, metadata_json)
               VALUES (?, ?, 'running', ?)""",
            (job_name, datetime.datetime.now().isoformat(),
             _json.dumps(metadata or {}))
        )
        conn.commit()
        return cur.lastrowid


def log_job_end(job_id: int, status: str, records: int = 0,
                error: str | None = None, source_max_date: str | None = None,
                db_path: str | None = None) -> None:
    """Update a job log with completion status."""
    from .config import DB_PATH as DEFAULT_DB
    path = db_path or str(DEFAULT_DB)

    with sqlite3.connect(path) as conn:
        conn.execute(
            """UPDATE ml_weekly_job_logs
               SET completed_at=?, status=?, records_processed=?,
                   error_message=?, source_max_date=?
               WHERE id=?""",
            (datetime.datetime.now().isoformat(), status, records,
             error, source_max_date, job_id)
        )
        conn.commit()


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    result = run_migration()
    import json
    print(json.dumps(result, indent=2))
    if result["status"] != "success":
        sys.exit(1)
