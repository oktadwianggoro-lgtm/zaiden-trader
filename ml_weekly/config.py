"""
ml_weekly/config.py
Default configuration for IDX Weekly High-Confidence ML system.
All parameters are overridable via database app_settings or API.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from pathlib import Path
import json
import sqlite3

# ── Paths ────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "zaiden_trader.db"
MODELS_DIR = ROOT / "data" / "ml_models"
MODELS_DIR.mkdir(exist_ok=True)

# ── Default Settings ──────────────────────────────────────────────────────────
@dataclass
class MLConfig:
    # --- Trading Parameters ---
    horizon_days: int = 5                    # Prediction horizon (trading days)
    primary_return_target: float = 0.02      # +2% close-to-close — TRAINING LABEL bar only
                                              # (target_definition="close5_2pct"); do not
                                              # change to move the displayed take-profit —
                                              # use take_profit below for that.
    take_profit: float = 0.05                # +5% TP shown in trade plan/PDF (was 3%, then
                                              # 3.5%, bumped again to 5% same day per user
                                              # request — with SL at -2% this gives 2.5:1
                                              # gross / ~1.68:1 net after round-trip costs.
                                              # The earlier 2%/2% bug gave 1:1 gross, which
                                              # real transaction costs almost always pushed
                                              # under the 1.0 net risk-reward gate)
    stop_loss: float = -0.02                 # -2% SL
    ambiguous_same_day_policy: str = "loss"  # "loss" or "exclude"

    # --- Universe Filters ---
    # min_median_value_20d/freq_20d lowered 2026-08-04 from 1e9/100 (which let
    # through only ~373/989 actively-traded stocks) to broaden coverage per
    # user request, while staying above config.min_avg_daily_value_position
    # (100M — the position-sizing floor elsewhere in this system, so a stock
    # eligible for a signal is always liquid enough to actually take a real
    # position in). Manipulation risk from casting a wider net is handled
    # separately by the gorengan (pump-and-dump) filter in universe.py, not
    # by liquidity alone — a thin float can pump on real IDR volume too.
    min_history_days: int = 120              # Min trading days in history
    min_median_value_20d: float = 2e8        # Min 20d median value (IDR): 200M
    min_median_freq_20d: float = 30.0        # Min 20d median frequency
    min_price: float = 50.0                  # Min median price (filter penny stocks)
    max_zero_vol_ratio: float = 0.20         # Max fraction of zero-volume days

    # --- ML Training ---
    random_seed: int = 42
    training_step_days: int = 1              # 1 = use every trading day (no sampling)
    training_start: str = "2020-01-02"
    training_end: str = "2023-12-31"
    validation_start: str = "2024-01-06"
    validation_end: str = "2024-12-31"
    holdout_start: str = "2025-01-06"        # Never used for tuning

    # Walk-forward
    wf_train_months: int = 24               # Training window months
    wf_val_months: int = 3                  # Validation window months
    wf_purge_days: int = 5                  # Purge between train/val
    wf_embargo_days: int = 5                # Embargo after validation

    # --- Signal Thresholds ---
    min_probability: float = 0.70           # Minimum to show in watchlist
    high_confidence_threshold: float = 0.85 # Default threshold for HC signal
    precision_target: float = 0.90          # Target precision
    min_validation_signals: int = 30        # Min signals for validation
    min_holdout_signals: int = 50           # Min signals for holdout evaluation
    min_distinct_stocks: int = 5            # Min different stocks in signals

    # --- Transaction Costs (configurable per user) ---
    buy_fee: float = 0.00155               # 0.155% BEI standard
    sell_fee: float = 0.00255              # 0.255% (incl. 0.1% tax)
    slippage: float = 0.001               # 0.1% slippage estimate
    min_avg_daily_value_position: float = 1e8  # Min ADV for position (IDR)
    max_position_pct_adv: float = 0.05    # Max 5% of ADV per position

    # --- Feature Flags ---
    enable_broker_features: bool = False   # False: broker_saham_harian is empty
    enable_ownership_features: bool = False # False: only 5 dates available
    enable_foreign_flow_features: bool = True  # True: beli_asing/jual_asing available

    # --- Model Registry ---
    active_model_version: str = ""        # Empty = no active model yet
    champion_model_id: int = 0            # ID in ml_weekly_model_runs

    # --- Retraining ---
    # Auto-retrain trigger: after each predict run evaluates newly-matured
    # signals (see api_handlers._maybe_trigger_auto_retrain), a fresh
    # train-all is kicked off in the background once this much time has
    # passed since the last successful training run AND at least one new
    # signal was actually evaluated. "manual" disables this entirely.
    retrain_schedule: str = "weekly"      # "daily" | "weekly" | "monthly" | "manual"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "MLConfig":
        valid = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in valid}
        return cls(**filtered)

    def total_cost(self) -> float:
        """One-way round-trip cost estimate."""
        return self.buy_fee + self.sell_fee + 2 * self.slippage

    def net_return_needed(self) -> float:
        """Minimum gross return needed to break even."""
        return self.total_cost()


# ── Load from DB ─────────────────────────────────────────────────────────────
def load_config() -> MLConfig:
    """Load MLConfig from database app_settings, fallback to defaults."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key = 'ml_weekly_config'"
            ).fetchone()
            if row and row[0]:
                d = json.loads(row[0])
                return MLConfig.from_dict(d)
    except Exception:
        pass
    return MLConfig()


def save_config(cfg: MLConfig) -> None:
    """Save MLConfig to database app_settings."""
    import datetime
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)",
            ("ml_weekly_config", json.dumps(cfg.to_dict()),
             datetime.datetime.now().isoformat())
        )
        conn.commit()


# ── Global instance ───────────────────────────────────────────────────────────
_cfg: MLConfig | None = None

def get_config() -> MLConfig:
    global _cfg
    if _cfg is None:
        _cfg = load_config()
    return _cfg

def reload_config() -> MLConfig:
    global _cfg
    _cfg = load_config()
    return _cfg
