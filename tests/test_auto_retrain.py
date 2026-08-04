"""
tests/test_auto_retrain.py
Validates the "models improve themselves" auto-retrain trigger: after each
predict run evaluates newly-matured signals against real outcomes, a fresh
train-all should kick off automatically once enough wall-clock time has
passed per config.retrain_schedule — but only if there's actually new
evaluated data, and never when retrain_schedule='manual'.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from db import SCHEMA_SQL
from ml_weekly.db_migration import MIGRATION_SQL
from ml_weekly.api_handlers import _is_retrain_due, _maybe_trigger_auto_retrain
from ml_weekly.config import MLConfig


@pytest.fixture
def db_with_last_trained(tmp_path: Path):
    def make(days_ago: float | None):
        db_path = str(tmp_path / f"retrain_{days_ago}.db")
        conn = sqlite3.connect(db_path)
        conn.executescript(SCHEMA_SQL)
        conn.executescript(MIGRATION_SQL)
        if days_ago is not None:
            created_at = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                """INSERT INTO ml_weekly_model_runs (
                    model_version, model_name, status, created_at, target_definition,
                    training_start, training_end, validation_start, validation_end, holdout_start
                ) VALUES ('v1', 'xgboost', 'active', ?, 'close5_2pct',
                          '2020-01-02', '2023-12-31', '2024-01-06', '2024-12-31', '2025-01-06')""",
                (created_at,),
            )
        conn.commit()
        conn.close()
        return db_path
    return make


def test_manual_schedule_never_due(db_with_last_trained):
    db_path = db_with_last_trained(365)
    cfg = MLConfig(retrain_schedule="manual")
    assert _is_retrain_due(db_path, cfg) is False


def test_never_trained_before_is_not_due(db_with_last_trained):
    db_path = db_with_last_trained(None)
    cfg = MLConfig(retrain_schedule="daily")
    assert _is_retrain_due(db_path, cfg) is False


def test_weekly_schedule_due_after_eight_days(db_with_last_trained):
    db_path = db_with_last_trained(8)
    cfg = MLConfig(retrain_schedule="weekly")
    assert _is_retrain_due(db_path, cfg) is True


def test_weekly_schedule_not_due_after_two_days(db_with_last_trained):
    db_path = db_with_last_trained(2)
    cfg = MLConfig(retrain_schedule="weekly")
    assert _is_retrain_due(db_path, cfg) is False


def test_monthly_schedule_not_due_after_eight_days(db_with_last_trained):
    db_path = db_with_last_trained(8)
    cfg = MLConfig(retrain_schedule="monthly")
    assert _is_retrain_due(db_path, cfg) is False


def test_no_trigger_when_nothing_newly_evaluated(db_with_last_trained):
    db_path = db_with_last_trained(30)
    with patch("ml_weekly.api_handlers.start_train_all_job") as mock_start:
        _maybe_trigger_auto_retrain(db_path, Path("/tmp/models"), newly_evaluated=0)
        mock_start.assert_not_called()


def test_triggers_when_due_and_newly_evaluated(db_with_last_trained):
    db_path = db_with_last_trained(30)
    with patch("ml_weekly.config.get_config", return_value=MLConfig(retrain_schedule="weekly")), \
         patch("ml_weekly.api_handlers.start_train_all_job") as mock_start:
        _maybe_trigger_auto_retrain(db_path, Path("/tmp/models"), newly_evaluated=3)
        mock_start.assert_called_once_with(db_path, Path("/tmp/models"))


def test_does_not_trigger_when_not_yet_due(db_with_last_trained):
    db_path = db_with_last_trained(1)
    with patch("ml_weekly.config.get_config", return_value=MLConfig(retrain_schedule="weekly")), \
         patch("ml_weekly.api_handlers.start_train_all_job") as mock_start:
        _maybe_trigger_auto_retrain(db_path, Path("/tmp/models"), newly_evaluated=3)
        mock_start.assert_not_called()


def test_exception_in_trigger_is_non_fatal(db_with_last_trained):
    db_path = db_with_last_trained(30)
    with patch("ml_weekly.config.get_config", side_effect=RuntimeError("boom")):
        # Must not raise -- a broken auto-retrain check should never take
        # down the predict pipeline that already saved real predictions.
        _maybe_trigger_auto_retrain(db_path, Path("/tmp/models"), newly_evaluated=3)
