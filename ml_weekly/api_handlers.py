"""
ml_weekly/api_handlers.py
API handler functions for IDX Weekly High-Confidence endpoints.
Called from app.py's handle_api_get and handle_write.
"""
from __future__ import annotations
import json
import logging
import sqlite3
import threading
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# All algorithms that make up the live ensemble. Kept in one place so the
# "train everything" pipeline and the ensemble loader always agree on
# membership instead of drifting apart as models are added by hand.
# "gradient_boosting" (sklearn's classic, non-histogram GradientBoostingClassifier)
# is deliberately excluded from the default "train everything" roster: it has
# no n_jobs (every other model here does) and doesn't histogram-bin splits,
# so it doesn't scale past roughly tens of thousands of rows — confirmed
# stuck for 90+ minutes on a single walk-forward fold once the dataset grew
# to ~380K rows x 129 features (see train_start=2020 + step_days=1 fix).
# hist_gradient_boosting is the same modeling family, implemented to actually
# handle this data volume, so nothing is lost by dropping the plain version
# from the ensemble. It's still selectable in make_model_pipeline() for
# manual single-model training on a smaller/filtered dataset if ever wanted.
ALL_MODEL_NAMES = [
    "hist_gradient_boosting", "random_forest", "logistic",
    "extra_trees", "xgboost",
]

# ── Training job state (in-memory, thread-safe) ────────────────────────────
TRAIN_LOCK = threading.Lock()
TRAIN_STATE: dict = {
    "status": "idle",
    "started_at": None,
    "finished_at": None,
    "message": "Belum ada training yang dijalankan.",
    "progress": 0,
    "result": None,
}

PREDICT_LOCK = threading.Lock()
PREDICT_STATE: dict = {
    "status": "idle",
    "started_at": None,
    "finished_at": None,
    "message": "Belum ada prediksi yang dijalankan.",
    "progress": 0,
}

ENSEMBLE_EVAL_LOCK = threading.Lock()
ENSEMBLE_EVAL_STATE: dict = {
    "status": "idle",
    "started_at": None,
    "finished_at": None,
    "message": "Belum ada evaluasi ensemble yang dijalankan.",
    "progress": 0,
    "result": None,
}

TRAIN_ALL_LOCK = threading.Lock()
TRAIN_ALL_STATE: dict = {
    "status": "idle",
    "started_at": None,
    "finished_at": None,
    "message": "Belum pernah dijalankan.",
    "progress": 0,
    "result": None,
}


def get_train_status() -> dict:
    with TRAIN_LOCK:
        return dict(TRAIN_STATE)


def get_predict_status() -> dict:
    with PREDICT_LOCK:
        return dict(PREDICT_STATE)


def get_ensemble_eval_status() -> dict:
    with ENSEMBLE_EVAL_LOCK:
        return dict(ENSEMBLE_EVAL_STATE)


def get_train_all_status() -> dict:
    with TRAIN_ALL_LOCK:
        return dict(TRAIN_ALL_STATE)


def _running_job_name() -> Optional[str]:
    """Return the name of whichever ML job is currently running, if any.

    Training, ensemble evaluation and prediction all read/write the same
    model_run rows and threshold table — running two at once is how a
    predict job ends up using a threshold computed for a different set of
    models. Callers should refuse to start a new job while another is live.
    """
    if TRAIN_STATE["status"] == "running":
        return "training"
    if PREDICT_STATE["status"] == "running":
        return "prediksi"
    if ENSEMBLE_EVAL_STATE["status"] == "running":
        return "evaluasi ensemble"
    if TRAIN_ALL_STATE["status"] == "running":
        return "pipeline lengkap (latih semua)"
    return None


def start_ensemble_eval_job(db_path: str, models_dir: Path, body: dict = None) -> dict:
    """POST /api/weekly/ensemble/evaluate — backtest the LIVE ensemble combination."""
    with ENSEMBLE_EVAL_LOCK:
        if ENSEMBLE_EVAL_STATE["status"] == "running":
            return {"ok": False, "message": "Evaluasi ensemble sedang berjalan.", "status": ENSEMBLE_EVAL_STATE}
        other = _running_job_name()
        if other:
            return {"ok": False, "message": f"Proses lain ({other}) sedang berjalan. Tunggu hingga selesai agar tidak tumpang tindih.", "status": ENSEMBLE_EVAL_STATE}
        ENSEMBLE_EVAL_STATE.update({
            "status": "running",
            "started_at": datetime.now().isoformat(),
            "finished_at": None,
            "message": "Memulai evaluasi ensemble...",
            "progress": 0,
            "result": None,
        })

    def progress_cb(pct: int, msg: str):
        with ENSEMBLE_EVAL_LOCK:
            ENSEMBLE_EVAL_STATE["progress"] = pct
            ENSEMBLE_EVAL_STATE["message"] = msg

    def run():
        from .ensemble_eval import evaluate_ensemble
        try:
            result = evaluate_ensemble(db_path, models_dir, progress_cb=progress_cb)
            with ENSEMBLE_EVAL_LOCK:
                if result.get("status") == "success":
                    ENSEMBLE_EVAL_STATE.update({
                        "status": "success",
                        "finished_at": datetime.now().isoformat(),
                        "message": (
                            f"Evaluasi selesai: threshold={result['selected_threshold']:.2f}, "
                            f"holdout={result['holdout_status']}, "
                            f"precision={result['holdout_metrics'].get('precision', 0):.1%} "
                            f"({result['holdout_metrics'].get('signal_count', 0)} sinyal)."
                        ),
                        "progress": 100,
                        "result": result,
                    })
                else:
                    ENSEMBLE_EVAL_STATE.update({
                        "status": "failed",
                        "finished_at": datetime.now().isoformat(),
                        "message": f"Gagal: {result.get('error')}",
                        "progress": 0,
                        "result": result,
                    })
        except Exception as exc:
            log.exception("Ensemble eval job failed: %s", exc)
            with ENSEMBLE_EVAL_LOCK:
                ENSEMBLE_EVAL_STATE.update({
                    "status": "failed",
                    "finished_at": datetime.now().isoformat(),
                    "message": f"Evaluasi ensemble gagal: {exc}",
                    "progress": 0,
                })

    threading.Thread(target=run, name="ml-weekly-ensemble-eval", daemon=True).start()
    return {"ok": True, "message": "Evaluasi ensemble dimulai.", "status": dict(ENSEMBLE_EVAL_STATE)}


# ── API Handlers ──────────────────────────────────────────────────────────────

def handle_status(db_path: str) -> dict:
    """GET /api/weekly/status"""
    from .db_migration import verify_source_tables_intact
    from .predict import get_source_max_date, check_data_freshness
    from .ensemble_eval import get_latest_ensemble_status
    from .config import get_config

    cfg = get_config()
    source_max_date = get_source_max_date(db_path)
    freshness = check_data_freshness(db_path)
    ensemble_status = get_latest_ensemble_status(db_path)

    # Get active model run (ensemble_member OR active fallback)
    with sqlite3.connect(db_path) as conn:
        run = conn.execute(
            """SELECT id, model_version, model_name, probability_threshold,
                      validation_status, holdout_status, source_max_date, created_at, target_definition
               FROM ml_weekly_model_runs
               WHERE status IN ('ensemble_member', 'active')
               ORDER BY id DESC LIMIT 1"""
        ).fetchone()

        # Ensemble members list
        ensemble_rows = conn.execute(
            """SELECT id, model_name, model_version, probability_threshold,
                      validation_status, holdout_status, created_at, target_definition
               FROM ml_weekly_model_runs
               WHERE status IN ('ensemble_member', 'active')
               ORDER BY id DESC"""
        ).fetchall()

        # Deduplicate by model_name (newest per algo)
        seen = set()
        ensemble_members = []
        for er in ensemble_rows:
            if er[1] not in seen:
                seen.add(er[1])
                target_def = er[7] if er[7] else "close5_2pct"
                h_days = 5
                try:
                    if target_def.startswith("close"): h_days = int(target_def.split("_")[0].replace("close", ""))
                except: pass
                
                ensemble_members.append({
                    "run_id": er[0],
                    "model_name": er[1],
                    "model_version": er[2],
                    "threshold": er[3],
                    "validation_status": er[4],
                    "holdout_status": er[5],
                    "created_at": er[6],
                    "target_definition": target_def,
                    "horizon_days": h_days
                })

        # Count predictions for latest model
        pred_count = 0
        hc_count = 0
        today_signals = 0
        if run:
            pc = conn.execute(
                "SELECT COUNT(*), SUM(CASE WHEN signal_status='HIGH_CONFIDENCE' THEN 1 ELSE 0 END) FROM ml_weekly_predictions WHERE model_run_id=?",
                (run[0],)
            ).fetchone()
            pred_count = pc[0] or 0
            hc_count = pc[1] or 0

            # Today or latest prediction date
            latest_pred = conn.execute(
                "SELECT MAX(prediction_date) FROM ml_weekly_predictions WHERE model_run_id=?",
                (run[0],)
            ).fetchone()
            if latest_pred and latest_pred[0]:
                today_signals = conn.execute(
                    "SELECT COUNT(*) FROM ml_weekly_predictions WHERE model_run_id=? AND prediction_date=? AND signal_status='HIGH_CONFIDENCE'",
                    (run[0], latest_pred[0])
                ).fetchone()[0]

    train_state = get_train_status()
    predict_state = get_predict_status()
    train_all_state = get_train_all_status()
    ensemble_eval_state = get_ensemble_eval_status()

    horizon_days = 5
    if run and run[8]:
        try:
            if run[8].startswith("close"): horizon_days = int(run[8].split("_")[0].replace("close", ""))
        except: pass

    # The live "Sinyal" tab filters on the COMBINED ensemble probability, not
    # any single member's own probability — so the confidence label shown to
    # users should describe that combination's own backtest, not just the
    # newest member's solo backtest. ensemble_status is None until
    # ensemble_eval.evaluate_ensemble() has been run at least once.
    ensemble_evaluated = ensemble_status is not None
    ensemble_stale = (
        ensemble_evaluated and run is not None and
        set(ensemble_status.get("model_names", [])) != {m["model_name"] for m in ensemble_members}
    )
    display_threshold = run[3] if run else cfg.high_confidence_threshold
    display_holdout_status = run[5] if run else "NOT_EVALUATED"
    if ensemble_evaluated and not ensemble_stale:
        display_threshold = ensemble_status["threshold"]
        display_holdout_status = ensemble_status.get("holdout_status") or display_holdout_status
    elif len(ensemble_members) >= 2:
        # Multiple members combine to produce live signals, but that
        # combination has never itself been backtested yet.
        display_holdout_status = "ENSEMBLE_NOT_EVALUATED"

    return {
        "model_active": run is not None,
        "model_run_id": run[0] if run else None,
        "model_version": run[1] if run else None,
        "model_name": run[2] if run else None,
        "threshold": display_threshold,
        "validation_status": run[4] if run else "NOT_TRAINED",
        "holdout_status": display_holdout_status,
        "model_trained_on": run[6] if run else None,
        "model_created_at": run[7] if run else None,
        "target_definition": run[8] if run else "close5_2pct",
        "horizon_days": horizon_days,
        "source_max_date": source_max_date,
        "data_fresh": freshness.get("ok", False),
        "data_freshness_days": freshness.get("days_old"),
        "data_freshness_reason": freshness.get("reason"),
        "total_predictions": pred_count,
        "hc_predictions": hc_count,
        "today_hc_signals": today_signals,
        "train_status": train_state.get("status"),
        "train_message": train_state.get("message"),
        "train_progress": train_state.get("progress"),
        "predict_status": predict_state.get("status"),
        "predict_message": predict_state.get("message"),
        "predict_progress": predict_state.get("progress"),
        "train_all_status": train_all_state.get("status"),
        "train_all_message": train_all_state.get("message"),
        "train_all_progress": train_all_state.get("progress"),
        "ensemble_eval_status": ensemble_eval_state.get("status"),
        "ensemble_eval_message": ensemble_eval_state.get("message"),
        "ensemble_eval_progress": ensemble_eval_state.get("progress"),
        "any_job_running": _running_job_name(),
        # Ensemble info
        "ensemble_members": ensemble_members,
        "ensemble_count": len(ensemble_members),
        "ensemble_ready": len(ensemble_members) >= 2,
        "ensemble_evaluated": ensemble_evaluated and not ensemble_stale,
        "ensemble_stale": ensemble_stale,
        "ensemble_backtest": ensemble_status,
        "config": {
            "horizon_days": cfg.horizon_days,
            "precision_target": cfg.precision_target,
            "high_confidence_threshold": cfg.high_confidence_threshold,
            "min_median_value_20d": cfg.min_median_value_20d,
        },
    }




def _confidence_tier(prob: float) -> dict:
    """
    Descriptive confidence banding based on the calibrated probability
    itself, independent of the (often unreachable) HIGH_CONFIDENCE gate.
    The strict threshold answers "does this clear our 90% precision bar" —
    these bands answer "how does this rank against everything else today",
    which is what lets a Top-10 view stay populated even when nothing
    clears the strict bar.
    """
    if prob >= 0.65:
        return {"tier": "SANGAT_KUAT", "label": "Sangat Kuat", "color": "#34d399"}
    if prob >= 0.55:
        return {"tier": "KUAT", "label": "Kuat", "color": "#a3e635"}
    if prob >= 0.45:
        return {"tier": "CUKUP", "label": "Cukup", "color": "#fbbf24"}
    return {"tier": "LEMAH", "label": "Lemah", "color": "#94a3b8"}


# Maps the validation-aware tier key stored on each prediction row
# (predict.compute_decision_status) to the same {tier,label,color} shape
# the frontend already expects from _confidence_tier above.
TIER_LOOKUP = {
    "SANGAT_KUAT": {"tier": "SANGAT_KUAT", "label": "Sangat Kuat", "color": "#34d399"},
    "KUAT": {"tier": "KUAT", "label": "Kuat", "color": "#a3e635"},
    "CUKUP": {"tier": "CUKUP", "label": "Cukup", "color": "#fbbf24"},
    "LEMAH": {"tier": "LEMAH", "label": "Lemah", "color": "#94a3b8"},
}

DECISION_STATUS_LABELS = {
    "QUALIFIED": {"label": "Qualified", "color": "#34d399"},
    "WATCHLIST": {"label": "Watchlist", "color": "#fbbf24"},
    "NO_TRADE": {"label": "No Trade", "color": "#f87171"},
    "ABSTAIN": {"label": "Abstain", "color": "#94a3b8"},
    "DATA_INVALID": {"label": "Data Invalid", "color": "#f87171"},
    "MODEL_UNAVAILABLE": {"label": "Model Unavailable", "color": "#f87171"},
    "SIGNAL_EXPIRED": {"label": "Expired", "color": "#94a3b8"},
    "BELUM_DIEVALUASI": {"label": "Belum Dievaluasi", "color": "#94a3b8"},
}


def handle_signals(db_path: str, query: dict) -> dict:
    """GET /api/weekly/signals"""
    def qv(name, default=""):
        vals = query.get(name)
        return vals[0] if vals else default

    date_filter = qv("date")
    signal_status = qv("status", "HIGH_CONFIDENCE")
    limit = int(qv("limit", "50"))
    min_prob = float(qv("min_prob", "0.0"))

    with sqlite3.connect(db_path) as conn:
        # Resolve the model to report from whichever model_run_id actually
        # HAS predictions, not from ml_weekly_model_runs' "latest active
        # row" — those two can briefly disagree while a training pipeline
        # is running (a model gets superseded mid-training before its
        # replacement's predictions exist yet), which would otherwise
        # return an empty signal list even though a perfectly good older
        # prediction set is sitting right there. Same fix as
        # pdf_report.generate_digest_pdf's model_run_id resolution.
        pred_run = conn.execute(
            "SELECT model_run_id FROM ml_weekly_predictions ORDER BY prediction_date DESC, id DESC LIMIT 1"
        ).fetchone()
        run = None
        if pred_run:
            run = conn.execute(
                "SELECT id, model_version, probability_threshold, target_definition FROM ml_weekly_model_runs WHERE id=?",
                (pred_run[0],),
            ).fetchone()
        if not run:
            run = conn.execute(
                "SELECT id, model_version, probability_threshold, target_definition FROM ml_weekly_model_runs WHERE status IN ('active', 'ensemble_member') ORDER BY id DESC LIMIT 1"
            ).fetchone()

        if not run:
            return {"error": "Model belum dilatih. Jalankan training terlebih dahulu.", "signals": []}

        model_run_id = run[0]
        target_def = run[3] if run[3] else "close5_2pct"
        horizon_days = 5
        try:
            if target_def.startswith("close"): horizon_days = int(target_def.split("_")[0].replace("close", ""))
        except: pass

        # Latest prediction date if not specified
        if not date_filter:
            latest = conn.execute(
                "SELECT MAX(prediction_date) FROM ml_weekly_predictions WHERE model_run_id=?",
                (model_run_id,)
            ).fetchone()
            date_filter = latest[0] if latest and latest[0] else date.today().isoformat()

        # Build query. QUALIFIED/decision-status filters take priority over
        # the legacy signal_status (threshold-only) filters — decision_status
        # is the one that actually accounts for validation state, model
        # coverage, and net risk-reward.
        status_filter = ""
        if signal_status == "QUALIFIED":
            status_filter = "AND decision_status = 'QUALIFIED'"
        elif signal_status == "DECISION_WATCHLIST":
            status_filter = "AND decision_status IN ('QUALIFIED', 'WATCHLIST')"
        elif signal_status == "HIGH_CONFIDENCE":
            status_filter = "AND signal_status = 'HIGH_CONFIDENCE'"
        elif signal_status == "WATCHLIST":
            status_filter = "AND signal_status IN ('HIGH_CONFIDENCE', 'WATCHLIST')"

        rows = conn.execute(
            f"""SELECT p.ticker, p.prediction_date, p.horizon_days,
                       p.current_close, p.next_open, p.calibrated_probability,
                       p.predicted_return, p.signal_status, p.market_regime,
                       p.sector_regime, p.reason_codes_json, p.risk_flags_json,
                       p.target_price, p.stop_price,
                       p.outcome_status, p.realized_return,
                       s.company_name, s.listing_board,
                       p.technical_score, p.volume_score, p.foreign_flow_score,
                       f.per, f.pbv, f.roe, f.der, f.sektor AS fund_sektor,
                       p.decision_status, p.confidence_tier, p.decision_reason,
                       p.model_coverage_json, p.net_risk_reward, p.net_expected_value,
                       p.entry_zone_low, p.entry_zone_high
                FROM ml_weekly_predictions p
                LEFT JOIN idx_stocks s ON s.code = p.ticker
                LEFT JOIN (
                    SELECT f1.* FROM idx_fundamental_snapshots f1
                    INNER JOIN (
                        SELECT stock_code, MAX(source_as_of_date) AS max_date
                        FROM idx_fundamental_snapshots GROUP BY stock_code
                    ) f2 ON f1.stock_code = f2.stock_code AND f1.source_as_of_date = f2.max_date
                ) f ON f.stock_code = p.ticker
                WHERE p.model_run_id = ?
                  AND p.prediction_date = ?
                  AND p.calibrated_probability >= ?
                  {status_filter}
                ORDER BY p.calibrated_probability DESC
                LIMIT ?""",
            (model_run_id, date_filter, min_prob, limit)
        ).fetchall()

    def current_close_fmt(val):
        return round(val, 2) if val else None

    signals = []
    for rank, row in enumerate(rows, start=1):
        (ticker, pred_date, horizon, close, next_open, prob, pred_ret,
         status, market_regime, sector, reasons_json, flags_json,
         target, stop, outcome, realized, company, board,
         tech_score, vol_score, ff_score,
         per, pbv, roe, der, fund_sektor,
         decision_status, confidence_tier_key, decision_reason,
         model_coverage_json, net_rr, net_ev, ez_low, ez_high) = row

        try:
            reasons = json.loads(reasons_json) if reasons_json else []
        except Exception:
            reasons = []
        try:
            flags = json.loads(flags_json) if flags_json else []
        except Exception:
            flags = []

        # Expected return vs cost (fallback for rows saved before net_risk_reward
        # was computed server-side)
        buy_fee, sell_fee, slippage = 0.00155, 0.00255, 0.001
        gross_tp = (target / next_open - 1.0) if next_open and next_open > 0 and target else None
        net_tp = (gross_tp - buy_fee - sell_fee - 2 * slippage) if gross_tp else None

        # Prefer the validation-aware tier computed at prediction time
        # (predict.compute_decision_status). Falls back to the raw-probability
        # banding only for rows generated before that migration.
        tier = TIER_LOOKUP.get(confidence_tier_key) if confidence_tier_key else None
        if tier is None:
            tier = _confidence_tier(prob)
        try:
            model_coverage = json.loads(model_coverage_json) if model_coverage_json else None
        except Exception:
            model_coverage = None

        signals.append({
            "rank": rank,
            "ticker": ticker,
            "company_name": company or ticker,
            "board": board or "",
            "prediction_date": pred_date,
            "horizon_days": horizon,
            "current_close": current_close_fmt(close),
            "next_open": current_close_fmt(next_open),
            "probability": round(prob, 4),
            "probability_pct": f"{prob:.1%}",
            "confidence_tier": tier,
            "decision_status": decision_status or "BELUM_DIEVALUASI",
            "decision_reason": decision_reason,
            "model_coverage": model_coverage,
            "net_risk_reward": round(net_rr, 3) if net_rr is not None else None,
            "net_expected_value": round(net_ev, 4) if net_ev is not None else None,
            "entry_zone_low": current_close_fmt(ez_low),
            "entry_zone_high": current_close_fmt(ez_high),
            "predicted_return": round(pred_ret, 4) if pred_ret else None,
            "signal_status": status,
            "market_regime": market_regime,
            "sector": sector or board or "Unknown",
            "reason_codes": reasons,
            "risk_flags": flags,
            "target_price": current_close_fmt(target),
            "stop_price": current_close_fmt(stop),
            "net_tp_return": round(net_tp, 4) if net_tp else None,
            "outcome_status": outcome or "PENDING",
            "realized_return": round(realized, 4) if realized else None,
            "technical_score": round(tech_score, 4) if tech_score else None,
            "volume_score": round(vol_score, 2) if vol_score else None,
            "foreign_flow_score": round(ff_score, 4) if ff_score else None,
            # Cross-linked from Zaiden Fundamental Review (idx_fundamental_snapshots)
            "fundamental": {
                "sektor": fund_sektor,
                "per": round(per, 2) if per is not None else None,
                "pbv": round(pbv, 2) if pbv is not None else None,
                "roe": round(roe, 2) if roe is not None else None,
                "der": round(der, 2) if der is not None else None,
            },
        })

    # Available prediction dates
    with sqlite3.connect(db_path) as conn:
        available_dates = [r[0] for r in conn.execute(
            "SELECT DISTINCT prediction_date FROM ml_weekly_predictions WHERE model_run_id=? ORDER BY prediction_date DESC LIMIT 30",
            (model_run_id,)
        ).fetchall()]

    from .ensemble_eval import get_latest_ensemble_status
    ens_status = get_latest_ensemble_status(db_path)

    return {
        "ok": True,
        "prediction_date": date_filter,
        "horizon_days": horizon_days,
        "model_run_id": model_run_id,
        "total_signals": len(signals),
        "signals": signals,
        "available_dates": available_dates,
        "validated_holdout_precision": ens_status.get("holdout_precision") if ens_status else None,
        "validated_holdout_status": ens_status.get("holdout_status") if ens_status else None,
    }


def handle_signal_pdf(db_path: str, ticker: str, query: dict) -> tuple[Optional[bytes], str]:
    """GET /api/weekly/signals/{ticker}/pdf — single-stock detail report. Returns (pdf_bytes_or_None, filename)."""
    from .pdf_report import generate_stock_pdf

    def qv(name, default=""):
        vals = query.get(name)
        return vals[0] if vals else default

    pred_date = qv("date") or None
    pdf = generate_stock_pdf(db_path, ticker.upper(), pred_date)
    fname = f"Sinyal_{ticker.upper()}_{pred_date or 'terbaru'}.pdf"
    return pdf, fname


def handle_signals_pdf(db_path: str, query: dict) -> tuple[Optional[bytes], str]:
    """GET /api/weekly/signals/pdf — daily digest report (cover + summary + per-stock detail). Returns (pdf_bytes_or_None, filename)."""
    from .pdf_report import generate_digest_pdf

    def qv(name, default=""):
        vals = query.get(name)
        return vals[0] if vals else default

    pred_date = qv("date") or None
    status = qv("status", "ALL")
    limit = int(qv("limit", "10"))
    pdf = generate_digest_pdf(db_path, pred_date, status=status, limit=limit)
    fname = f"Laporan_Sinyal_Mingguan_IDX_{pred_date or 'terbaru'}.pdf"
    return pdf, fname


def handle_signal_detail(db_path: str, ticker: str, query: dict) -> dict:
    """GET /api/weekly/signals/{ticker}"""
    def qv(name, default=""):
        vals = query.get(name)
        return vals[0] if vals else default

    date_filter = qv("date")

    with sqlite3.connect(db_path) as conn:
        pred_run = conn.execute(
            "SELECT model_run_id FROM ml_weekly_predictions ORDER BY prediction_date DESC, id DESC LIMIT 1"
        ).fetchone()
        run = None
        if pred_run:
            run = conn.execute(
                "SELECT id, probability_threshold FROM ml_weekly_model_runs WHERE id=?", (pred_run[0],)
            ).fetchone()
        if not run:
            run = conn.execute(
                "SELECT id, probability_threshold FROM ml_weekly_model_runs WHERE status IN ('active', 'ensemble_member') ORDER BY id DESC LIMIT 1"
            ).fetchone()

        if not run:
            return {"error": "No active model"}

        model_run_id = run[0]

        if not date_filter:
            latest = conn.execute(
                "SELECT MAX(prediction_date) FROM ml_weekly_predictions WHERE model_run_id=? AND ticker=?",
                (model_run_id, ticker)
            ).fetchone()
            date_filter = latest[0] if latest and latest[0] else date.today().isoformat()

        cur = conn.execute(
            """SELECT * FROM ml_weekly_predictions
               WHERE model_run_id=? AND ticker=? AND prediction_date=?""",
            (model_run_id, ticker, date_filter)
        )
        pred = cur.fetchone()

        if not pred:
            return {"error": f"No prediction for {ticker} on {date_filter}"}

        # Get column names (conn.execute() returns a Cursor — description
        # lives there, not on the Connection object)
        cols = [d[0] for d in cur.description] if cur.description else []
        pred_dict = dict(zip(cols, pred))

        # Historical predictions for this ticker
        history = conn.execute(
            """SELECT prediction_date, calibrated_probability, signal_status,
                      outcome_status, realized_return
               FROM ml_weekly_predictions
               WHERE model_run_id=? AND ticker=?
               ORDER BY prediction_date DESC LIMIT 20""",
            (model_run_id, ticker)
        ).fetchall()

    # Parse JSON fields
    for json_field in ["reason_codes_json", "risk_flags_json", "feature_values_json"]:
        if pred_dict.get(json_field):
            try:
                pred_dict[json_field] = json.loads(pred_dict[json_field])
            except Exception:
                pass

    pred_dict["history"] = [
        {
            "date": h[0],
            "probability": round(h[1], 4),
            "signal_status": h[2],
            "outcome": h[3],
            "realized_return": round(h[4], 4) if h[4] else None,
        }
        for h in history
    ]

    return pred_dict


def handle_validation(db_path: str) -> dict:
    """GET /api/weekly/validation"""
    with sqlite3.connect(db_path) as conn:
        runs = conn.execute(
            """SELECT id, model_version, model_name, probability_threshold,
                      validation_status, holdout_status, created_at
               FROM ml_weekly_model_runs
               ORDER BY id DESC LIMIT 5"""
        ).fetchall()

        if not runs:
            return {"error": "No model runs found", "metrics": {}}

        active_run_id = runs[0][0]

        metrics_rows = conn.execute(
            """SELECT evaluation_split, period_start, period_end, threshold,
                      signal_count, correct_count, precision, precision_ci_lower,
                      precision_ci_upper, coverage, recall, pr_auc, brier_score,
                      metrics_json
               FROM ml_weekly_backtest_metrics
               WHERE model_run_id=?
               ORDER BY evaluation_split, period_start""",
            (active_run_id,)
        ).fetchall()

    metrics_by_split = {}
    for row in metrics_rows:
        split = row[0]
        m = {
            "evaluation_split": split,
            "period_start": row[1],
            "period_end": row[2],
            "threshold": row[3],
            "signal_count": row[4],
            "correct_count": row[5],
            "precision": round(row[6], 4) if row[6] else None,
            "precision_ci_lower": round(row[7], 4) if row[7] else None,
            "precision_ci_upper": round(row[8], 4) if row[8] else None,
            "coverage": round(row[9], 4) if row[9] else None,
            "recall": round(row[10], 4) if row[10] else None,
            "pr_auc": round(row[11], 4) if row[11] else None,
            "brier_score": round(row[12], 4) if row[12] else None,
        }
        if row[13]:
            try:
                extra = json.loads(row[13])
                m.update({k: v for k, v in extra.items() if k not in m})
            except Exception:
                pass
        metrics_by_split[split] = m

    return {
        "model_runs": [
            {
                "id": r[0], "version": r[1], "name": r[2],
                "threshold": r[3], "validation_status": r[4],
                "holdout_status": r[5], "created_at": r[6],
            }
            for r in runs
        ],
        "active_run_id": active_run_id,
        "metrics": metrics_by_split,
    }


def handle_backtest(db_path: str, query: dict) -> dict:
    """GET /api/weekly/backtest"""
    def qv(name, default=""):
        vals = query.get(name)
        return vals[0] if vals else default

    run_id = int(qv("run_id", "0")) or None

    with sqlite3.connect(db_path) as conn:
        if not run_id:
            run = conn.execute(
                "SELECT id FROM ml_weekly_model_runs WHERE status IN ('active', 'ensemble_member') ORDER BY id DESC LIMIT 1"
            ).fetchone()
            run_id = run[0] if run else None

        if not run_id:
            return {"error": "No model found"}

        # Get metrics for this run
        metrics_rows = conn.execute(
            """SELECT evaluation_split, period_start, period_end, threshold,
                      signal_count, correct_count, false_positive_count,
                      precision, precision_ci_lower, precision_ci_upper,
                      coverage, recall, net_return, gross_return,
                      profit_factor, maximum_drawdown, sharpe_ratio, win_rate, expectancy,
                      metrics_json
               FROM ml_weekly_backtest_metrics
               WHERE model_run_id=?
               ORDER BY evaluation_split, period_start""",
            (run_id,)
        ).fetchall()

        # Prediction outcomes for equity curve
        outcomes = conn.execute(
            """SELECT prediction_date, signal_status, outcome_status,
                      realized_return, calibrated_probability
               FROM ml_weekly_predictions
               WHERE model_run_id=? AND outcome_status != 'PENDING'
                 AND signal_status = 'HIGH_CONFIDENCE'
               ORDER BY prediction_date""",
            (run_id,)
        ).fetchall()

    # Build equity curve
    equity = []
    cumulative = 1.0
    buy_fee, sell_fee, slippage = 0.00155, 0.00255, 0.001
    total_cost = buy_fee + sell_fee + 2 * slippage

    for oc in outcomes:
        pred_date, status, outcome, ret, prob = oc
        if ret is None:
            continue
        net_ret = ret - total_cost
        cumulative *= (1 + net_ret)
        equity.append({
            "date": pred_date,
            "net_return": round(net_ret, 4),
            "cumulative": round(cumulative, 6),
            "outcome": outcome,
            "probability": round(prob, 4) if prob else None,
        })

    # Build metrics summary
    metrics = []
    for row in metrics_rows:
        m = {
            "split": row[0], "period_start": row[1], "period_end": row[2],
            "threshold": round(row[3], 2),
            "signal_count": row[4], "correct_count": row[5], "fp_count": row[6],
            "precision": round(row[7], 4) if row[7] else None,
            "precision_ci_lower": round(row[8], 4) if row[8] else None,
            "precision_ci_upper": round(row[9], 4) if row[9] else None,
            "coverage": round(row[10], 4) if row[10] else None,
            "recall": round(row[11], 4) if row[11] else None,
            "net_return": round(row[12], 4) if row[12] else None,
            "gross_return": round(row[13], 4) if row[13] else None,
            "profit_factor": round(row[14], 2) if row[14] else None,
            "max_drawdown": round(row[15], 4) if row[15] else None,
            "sharpe": round(row[16], 2) if row[16] else None,
            "win_rate": round(row[17], 4) if row[17] else None,
            "expectancy": round(row[18], 4) if row[18] else None,
        }
        metrics.append(m)

    return {
        "run_id": run_id,
        "metrics": metrics,
        "equity_curve": equity,
        "total_trades": len(outcomes),
    }


def handle_data_quality(db_path: str) -> dict:
    """GET /api/weekly/data-quality"""
    with sqlite3.connect(db_path) as conn:
        # Main table stats
        main = conn.execute(
            """SELECT COUNT(*) as rows,
                      COUNT(DISTINCT kode_saham) as stocks,
                      COUNT(DISTINCT tanggal) as dates,
                      MIN(tanggal) as min_date,
                      MAX(tanggal) as max_date,
                      SUM(CASE WHEN harga_pembukaan IS NULL OR harga_pembukaan=0 THEN 1 ELSE 0 END) as null_open,
                      SUM(CASE WHEN volume IS NULL OR volume=0 THEN 1 ELSE 0 END) as zero_vol
               FROM ringkasan_saham_harian"""
        ).fetchone()

        # Active stocks
        active = conn.execute(
            """SELECT COUNT(DISTINCT kode_saham)
               FROM ringkasan_saham_harian
               WHERE tanggal >= DATE((SELECT MAX(tanggal) FROM ringkasan_saham_harian), '-60 days')
                 AND harga_penutupan > 0 AND volume > 0"""
        ).fetchone()

        # Data per year
        by_year = conn.execute(
            """SELECT SUBSTR(tanggal,1,4), COUNT(*), COUNT(DISTINCT kode_saham), COUNT(DISTINCT tanggal)
               FROM ringkasan_saham_harian
               GROUP BY 1 ORDER BY 1"""
        ).fetchall()

        # Broker table
        broker = conn.execute(
            """SELECT COUNT(*), COUNT(DISTINCT tanggal), MIN(tanggal), MAX(tanggal)
               FROM ringkasan_broker_harian"""
        ).fetchone()

        # Ownership table
        own = conn.execute(
            """SELECT COUNT(*), COUNT(DISTINCT share_code), COUNT(DISTINCT record_date),
                      MIN(record_date), MAX(record_date)
               FROM ownership_positions"""
        ).fetchone()

        # Job logs
        jobs = conn.execute(
            """SELECT job_name, started_at, status, error_message
               FROM ml_weekly_job_logs
               ORDER BY started_at DESC LIMIT 10"""
        ).fetchall() if _table_exists(conn, "ml_weekly_job_logs") else []

    return {
        "main_table": {
            "rows": main[0], "stocks": main[1], "dates": main[2],
            "min_date": main[3], "max_date": main[4],
            "null_open_pct": round(main[5] / max(main[0], 1), 3),
            "zero_vol_pct": round(main[6] / max(main[0], 1), 3),
            "active_stocks_60d": active[0] if active else 0,
        },
        "by_year": [
            {"year": r[0], "rows": r[1], "stocks": r[2], "trading_days": r[3]}
            for r in by_year
        ],
        "broker_table": {
            "rows": broker[0], "dates": broker[1],
            "min_date": broker[2], "max_date": broker[3],
            "note": "Market-level only, no per-stock data",
        },
        "ownership_table": {
            "rows": own[0], "stocks": own[1], "dates": own[2],
            "min_date": own[3], "max_date": own[4],
            "note": "Only 5 dates available (monthly snapshots)",
        },
        "limitations": [
            "Open price: 80% missing → model uses prev_close as entry proxy",
            "Broker per stock: Empty → broker features disabled",
            "Ownership: 5 dates only → used as static indicator only",
            "Corporate action: Not adjusted → extreme returns flagged",
            "Universe bias: Only currently listed stocks (minor survivorship bias)",
        ],
        "recent_jobs": [
            {"job": r[0], "started": r[1], "status": r[2], "error": r[3]}
            for r in jobs
        ],
    }


EVAL_LOCK = threading.Lock()
EVAL_STATE: dict = {
    "status": "idle",
    "started_at": None,
    "finished_at": None,
    "message": "Belum pernah dijalankan manual (evaluasi otomatis berjalan setiap kali prediksi baru dibuat).",
    "updated_count": None,
}


def handle_evaluation(db_path: str, query: dict) -> dict:
    """GET /api/weekly/evaluation — accumulated knowledge from matured signal outcomes."""
    from .evaluation import compute_evaluation_summary
    return compute_evaluation_summary(db_path)


def handle_evaluation_signals(db_path: str, query: dict) -> dict:
    """GET /api/weekly/evaluation/signals — individual per-signal before/after track record."""
    from .evaluation import list_evaluated_signals

    def qv(name, default=""):
        vals = query.get(name)
        return vals[0] if vals else default

    limit = max(1, min(int(qv("limit", "50") or 50), 500))
    offset = max(0, int(qv("offset", "0") or 0))
    ticker = qv("ticker", "") or None
    sort_by = qv("sort", "date") or "date"
    return list_evaluated_signals(db_path, limit=limit, offset=offset, ticker=ticker, sort_by=sort_by)


def handle_evaluation_top_signals(db_path: str, query: dict) -> dict:
    """GET /api/weekly/evaluation/top-signals — top-N signals PER prediction batch, paginated over batches."""
    from .evaluation import list_top_signals_by_batch

    def qv(name, default=""):
        vals = query.get(name)
        return vals[0] if vals else default

    top_n = max(1, min(int(qv("top_n", "10") or 10), 50))
    batch_limit = max(1, min(int(qv("batch_limit", "5") or 5), 30))
    batch_offset = max(0, int(qv("batch_offset", "0") or 0))
    return list_top_signals_by_batch(db_path, top_n=top_n, batch_limit=batch_limit, batch_offset=batch_offset)


def handle_evaluation_pending_progress(db_path: str, query: dict) -> dict:
    """GET /api/weekly/evaluation/pending-progress — in-flight (not-yet-matured)
    signal progress, paginated one actual pull (prediction_date + model_run_id)
    per page, newest first — every distinct tarikan gets its own page, even
    if two pulls landed on the same calendar date."""
    from .evaluation import list_pending_signal_progress

    def qv(name, default=""):
        vals = query.get(name)
        return vals[0] if vals else default

    batch_limit = max(1, min(int(qv("batch_limit", "1") or 1), 30))
    batch_offset = max(0, int(qv("batch_offset", "0") or 0))
    return list_pending_signal_progress(db_path, batch_limit=batch_limit, batch_offset=batch_offset)


def get_evaluation_job_status() -> dict:
    with EVAL_LOCK:
        return dict(EVAL_STATE)


def start_evaluation_job(db_path: str) -> dict:
    """
    POST /api/weekly/evaluation/run — manually force-check for newly-matured
    signals right now, instead of waiting for the next predict run (which
    already calls evaluate_matured_predictions automatically). Useful the
    first few times this feature is used, or after a gap in daily syncs.
    """
    with EVAL_LOCK:
        if EVAL_STATE["status"] == "running":
            return {"ok": False, "message": "Evaluasi sedang berjalan.", "status": EVAL_STATE}
        other = _running_job_name()
        if other:
            return {"ok": False, "message": f"Proses lain ({other}) sedang berjalan. Tunggu hingga selesai.", "status": EVAL_STATE}
        EVAL_STATE.update({
            "status": "running", "started_at": datetime.now().isoformat(),
            "finished_at": None, "message": "Memeriksa sinyal yang sudah matang...", "updated_count": None,
        })

    def run():
        from .predict import evaluate_matured_predictions
        from .config import MODELS_DIR
        try:
            updated = evaluate_matured_predictions(db_path)
            with EVAL_LOCK:
                EVAL_STATE.update({
                    "status": "success", "finished_at": datetime.now().isoformat(),
                    "message": f"Selesai: {updated} sinyal baru dievaluasi." if updated else
                               "Selesai: belum ada sinyal baru yang cukup umur untuk dievaluasi.",
                    "updated_count": updated,
                })
            _maybe_trigger_auto_retrain(db_path, MODELS_DIR, updated)
        except Exception as exc:
            log.exception("Evaluation job failed: %s", exc)
            with EVAL_LOCK:
                EVAL_STATE.update({
                    "status": "failed", "finished_at": datetime.now().isoformat(),
                    "message": f"Evaluasi gagal: {exc}",
                })

    threading.Thread(target=run, name="ml-weekly-evaluate", daemon=True).start()
    return {"ok": True, "message": "Evaluasi dimulai.", "status": dict(EVAL_STATE)}


def handle_ihsg_analysis(db_path: str) -> dict:
    """GET /api/weekly/ihsg — IHSG direction & bottom-analog dashboard."""
    from .ihsg_analysis import compute_ihsg_dashboard
    return compute_ihsg_dashboard(db_path)


def handle_get_settings(db_path: str) -> dict:
    """GET /api/weekly/settings"""
    from .config import get_config
    cfg = get_config()
    return {"settings": cfg.to_dict()}


def handle_update_settings(db_path: str, body: dict) -> dict:
    """PUT /api/weekly/settings"""
    from .config import get_config, save_config, MLConfig, reload_config
    cfg = get_config()
    cfg_dict = cfg.to_dict()

    # Only allow updating safe fields (not model paths/versions)
    readonly_fields = {"active_model_version", "champion_model_id"}
    for k, v in body.items():
        if k in cfg_dict and k not in readonly_fields:
            cfg_dict[k] = v

    new_cfg = MLConfig.from_dict(cfg_dict)
    save_config(new_cfg)
    reload_config()
    return {"ok": True, "settings": new_cfg.to_dict()}


def start_training_job(db_path: str, models_dir: Path, body: dict) -> dict:
    """POST /api/weekly/train — trigger training in background thread."""
    with TRAIN_LOCK:
        if TRAIN_STATE["status"] == "running":
            return {"ok": False, "message": "Training sedang berjalan.", "status": TRAIN_STATE}
        other = _running_job_name()
        if other:
            return {"ok": False, "message": f"Proses lain ({other}) sedang berjalan. Tunggu hingga selesai agar tidak tumpang tindih.", "status": TRAIN_STATE}
        TRAIN_STATE.update({
            "status": "running",
            "started_at": datetime.now().isoformat(),
            "finished_at": None,
            "message": "Memulai pipeline training (bulk-optimized)...",
            "progress": 0,
            "result": None,
        })

    model_name = body.get("model_name", "hist_gradient_boosting")

    def progress_cb(pct: int, msg: str):
        with TRAIN_LOCK:
            TRAIN_STATE["progress"] = pct
            TRAIN_STATE["message"] = msg

    def run():
        from .pipeline import run_full_pipeline, request_cancel
        from .config import get_config
        try:
            result = run_full_pipeline(
                db_path, models_dir, get_config(),
                model_name=model_name,
                progress_cb=progress_cb,
            )
            with TRAIN_LOCK:
                if result.get("status") == "cancelled":
                    TRAIN_STATE.update({
                        "status": "idle",
                        "finished_at": datetime.now().isoformat(),
                        "message": "Training dibatalkan oleh pengguna.",
                        "progress": 0,
                        "result": None,
                    })
                else:
                    if result.get("status") == "success":
                        # Mark model as ensemble_member so multiple models can coexist
                        new_run_id = result.get("model_run_id")
                        model_nm = model_name  # captured from outer scope
                        if new_run_id:
                            try:
                                with sqlite3.connect(db_path) as _conn:
                                    # Remove old ensemble_member for SAME algo (keep newest)
                                    _conn.execute(
                                        """UPDATE ml_weekly_model_runs
                                           SET status='superseded'
                                           WHERE status='ensemble_member'
                                             AND model_name=?
                                             AND id != ?""",
                                        (model_nm, new_run_id)
                                    )
                                    # Also supersede any old 'active' single models for this algo
                                    _conn.execute(
                                        """UPDATE ml_weekly_model_runs
                                           SET status='superseded'
                                           WHERE status='active'
                                             AND model_name=?
                                             AND id != ?""",
                                        (model_nm, new_run_id)
                                    )
                                    # Set this model to ensemble_member
                                    _conn.execute(
                                        "UPDATE ml_weekly_model_runs SET status='ensemble_member' WHERE id=?",
                                        (new_run_id,)
                                    )
                                    _conn.commit()
                                log.info("Model %d (%s) set to ensemble_member", new_run_id, model_nm)
                            except Exception as _e:
                                log.warning("Could not set ensemble_member status: %s", _e)

                        # Count how many ensemble members are now available
                        try:
                            with sqlite3.connect(db_path) as _conn:
                                em_count = _conn.execute(
                                    "SELECT COUNT(DISTINCT model_name) FROM ml_weekly_model_runs WHERE status='ensemble_member'"
                                ).fetchone()[0]
                        except Exception:
                            em_count = 1

                        TRAIN_STATE.update({
                            "status": "success",
                            "finished_at": datetime.now().isoformat(),
                            "message": (
                                f"Training selesai. Model ID: {result.get('model_run_id')}. "
                                f"Ensemble: {em_count}/3 model. "
                                f"Holdout: {result.get('holdout_status')} | "
                                f"Precision: {result.get('holdout_precision', 0):.1%}"
                            ),
                            "progress": 100,
                            "result": result,
                            "ensemble_count": em_count,
                        })
                    else:
                        TRAIN_STATE.update({
                            "status": "failed",
                            "finished_at": datetime.now().isoformat(),
                            "message": f"Gagal: {result.get('error')}",
                            "progress": 0,
                            "result": result,
                        })
        except Exception as exc:
            log.exception("Training job failed: %s", exc)
            with TRAIN_LOCK:
                TRAIN_STATE.update({
                    "status": "failed",
                    "finished_at": datetime.now().isoformat(),
                    "message": f"Training gagal: {exc}",
                    "progress": 0,
                    "result": None,
                })

    threading.Thread(target=run, name="ml-weekly-train", daemon=True).start()
    return {"ok": True, "message": "Training dimulai (optimized bulk load).", "status": dict(TRAIN_STATE)}


def cancel_training_job() -> dict:
    """POST /api/weekly/train/cancel — request training cancellation."""
    try:
        from .pipeline import request_cancel
        request_cancel()
        with TRAIN_LOCK:
            if TRAIN_STATE["status"] == "running":
                TRAIN_STATE["message"] = "Pembatalan diminta, menunggu checkpoint berikutnya..."
        return {"ok": True, "message": "Pembatalan training diminta."}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}



def run_predict_pipeline(db_path: str, models_dir: Path, pred_date: Optional[str], progress_cb=None) -> dict:
    """
    Core prediction logic: load the live ensemble, guarantee its combined
    threshold has actually been backtested (auto-running that backtest if
    it's missing or stale), then generate + save signals for pred_date.

    Callable directly (e.g. chained after training in start_train_all_job)
    or wrapped in a background thread (start_predict_job). Always returns a
    result dict — never raises — so callers can chain it safely.
    """
    def _cb(pct, msg):
        log.info("[predict %s%%] %s", pct, msg)
        if progress_cb:
            progress_cb(pct, msg)

    try:
        from .predict import (
            _assemble_predictions, save_predictions,
            evaluate_matured_predictions, check_data_freshness,
            load_ensemble_models, ensemble_predict_proba, get_source_max_date,
        )
        from .ensemble_eval import get_latest_ensemble_status, evaluate_ensemble
        from .config import get_config

        cfg = get_config()

        freshness = check_data_freshness(db_path)
        if not freshness.get("ok"):
            return {"status": "failed", "message": f"Data tidak segar: {freshness.get('reason')}"}

        # Never let a caller-requested date outrun the data that actually
        # exists. current_close is always resolved as "latest close on or
        # before pred_date" — if pred_date is a date the daily sync hasn't
        # reached yet, that silently falls back to an OLDER close while the
        # row still gets labeled with the newer date, which looks like
        # "today's price" but is actually stale by one or more trading days.
        # Clamping here means a prediction's date always matches the exact
        # close price it was computed from.
        actual_max_date = get_source_max_date(db_path)
        requested_date = pred_date
        if not actual_max_date:
            return {"status": "failed", "message": "Tidak ada data harga di database sama sekali."}
        if not pred_date or pred_date > actual_max_date:
            pred_date = actual_max_date

        _cb(10, "Memuat ensemble models...")
        members = load_ensemble_models(db_path, models_dir)

        if not members:
            return {"status": "failed", "message": "Tidak ada model tersedia. Lakukan training terlebih dahulu."}

        primary = members[0]
        model_run_id = primary["model_run_id"]
        feature_cols = primary["feature_cols"]
        model_names = [m["model_name"] for m in members]

        # The live "Sinyal" tab gates on the COMBINED ensemble probability,
        # never on any single member's own probability — so the cutoff used
        # here MUST come from a backtest of that exact combination
        # (ensemble_eval.evaluate_ensemble), not a naive average of each
        # member's individually-tuned threshold. Per-member thresholds are
        # calibrated against a completely different probability distribution
        # (the single model's own output) and averaging them produces a
        # cutoff the combined ensemble may never even reach — which is
        # exactly why signals silently stopped appearing. If no backtest
        # matches the current member set, run one now instead of guessing.
        ens_status = get_latest_ensemble_status(db_path)
        if not ens_status or set(ens_status.get("model_names", [])) != set(model_names):
            _cb(15, f"Threshold ensemble belum tervalidasi untuk kombinasi {len(members)} model saat ini — menjalankan evaluasi otomatis...")
            eval_result = evaluate_ensemble(
                db_path, models_dir, config=cfg,
                progress_cb=lambda p, m: _cb(15 + int(p * 0.25), m),
            )
            if eval_result.get("status") != "success":
                return {"status": "failed", "message": f"Evaluasi ensemble otomatis gagal: {eval_result.get('error', eval_result)}"}
            ens_status = get_latest_ensemble_status(db_path)

        threshold = ens_status["threshold"]
        log.info("Using backtested ensemble threshold=%.3f (holdout=%s)", threshold, ens_status.get("holdout_status"))

        _cb(42, f"Threshold tervalidasi: {threshold:.0%}. Ensemble {len(members)} model ({', '.join(model_names)}). Membangun fitur...")

        from .universe import build_universe, UniverseStatus
        from .features import build_all_features
        from .market_features import compute_market_features, compute_cross_sectional_ranks, compute_sector_features

        universe_df = build_universe(
            pred_date, db_path,
            min_history_days=cfg.min_history_days,
            min_median_value_20d=cfg.min_median_value_20d,
        )
        eligible = universe_df[universe_df["status"].isin([
            UniverseStatus.ELIGIBLE,
            UniverseStatus.CORPORATE_ACTION_WARNING,
        ])]["ticker"].tolist()

        if not eligible:
            return {"status": "failed", "message": f"Tidak ada saham eligible untuk {pred_date}."}

        feat_df = build_all_features(pred_date, db_path, eligible)
        mfeats = compute_market_features(pred_date, db_path)
        ranks_df = compute_cross_sectional_ranks(pred_date, db_path, eligible)
        if not ranks_df.empty:
            feat_df = feat_df.merge(ranks_df, on="ticker", how="left")
        for mkey, mval in mfeats.items():
            feat_df[mkey] = mval
        feat_df = feat_df.merge(
            universe_df[["ticker", "status", "has_corp_action_warning", "has_verified_corp_action",
                         "median_close_20d", "median_value_20d"]],
            on="ticker", how="left"
        )
        # Locked in immediately after the last merge — ensemble_predict_proba
        # and _assemble_predictions both index this exact dataframe
        # positionally, so its row order must never change between them.
        feat_df = feat_df.reset_index(drop=True)

        _cb(60, f"{len(eligible)} saham dimuat. Menjalankan ensemble inference...")

        avg_probs, ensemble_breakdown = ensemble_predict_proba(members, feat_df)

        _cb(75, "Menghitung sinyal dan menyimpan prediksi...")

        # Assemble directly from THIS feat_df + THESE probs — no second,
        # independently-rebuilt dataframe, no synthetic pipe reusing a
        # probability array by raw position against a different row order.
        # (That indirection used to be able to silently swap which ticker
        # got which model's probability; see _assemble_predictions' docstring.)
        preds = _assemble_predictions(
            feat_df=feat_df,
            probs=avg_probs,
            as_of_date=pred_date,
            db_path=db_path,
            model_run_id=model_run_id,
            feature_cols=feature_cols,
            threshold=threshold,
            horizon_days=primary.get("horizon_days", 5),
            ensemble_breakdown=ensemble_breakdown,
            n_models_expected=len(members),
            buy_fee=cfg.buy_fee,
            sell_fee=cfg.sell_fee,
            slippage=cfg.slippage,
            # These three used to be silently omitted, which meant every live
            # prediction ran on _assemble_predictions' own hardcoded defaults
            # (primary_target=0.02, take_profit=0.03, stop_loss=-0.02)
            # instead of whatever config.py/app_settings actually said — so
            # changing config.take_profit never had any real effect here,
            # no matter how many times it was edited or the server restarted.
            primary_target=cfg.primary_return_target,
            take_profit=cfg.take_profit,
            stop_loss=cfg.stop_loss,
        )

        _cb(90, f"Menyimpan {len(preds)} prediksi...")

        save_predictions(preds, db_path, model_run_id)
        newly_evaluated = evaluate_matured_predictions(db_path, pred_date)
        _maybe_trigger_auto_retrain(db_path, models_dir, newly_evaluated)

        hc_count = sum(1 for p in preds if p["signal_status"] == "HIGH_CONFIDENCE")
        top_prob = max((p["calibrated_probability"] for p in preds), default=0.0)
        _cb(100, f"Selesai: {hc_count} sinyal HC dari {len(preds)} saham.")

        clamp_note = (
            f" (diminta {requested_date}, memakai data terbaru {pred_date} karena data untuk {requested_date} belum tersedia)"
            if requested_date and requested_date != pred_date else ""
        )
        return {
            "status": "success",
            "message": (
                f"Ensemble {len(members)} model selesai: {hc_count} sinyal HC dari {len(preds)} saham "
                f"({', '.join(model_names)}) untuk {pred_date}{clamp_note}. Probabilitas tertinggi: {top_prob:.0%}."
            ),
            "ensemble_models": model_names,
            "hc_count": hc_count,
            "total_predictions": len(preds),
            "threshold_used": threshold,
            "prediction_date": pred_date,
            "requested_date": requested_date,
        }

    except Exception as exc:
        log.exception("Predict pipeline failed: %s", exc)
        return {"status": "failed", "message": f"Prediksi gagal: {exc}"}


def start_predict_job(db_path: str, models_dir: Path, body: dict) -> dict:
    """POST /api/weekly/predict — trigger prediction in background thread."""
    with PREDICT_LOCK:
        if PREDICT_STATE["status"] == "running":
            return {"ok": False, "message": "Prediksi sedang berjalan.", "status": PREDICT_STATE}
        other = _running_job_name()
        if other:
            return {"ok": False, "message": f"Proses lain ({other}) sedang berjalan. Tunggu hingga selesai agar tidak tumpang tindih.", "status": PREDICT_STATE}
        PREDICT_STATE.update({
            "status": "running",
            "started_at": datetime.now().isoformat(),
            "finished_at": None,
            "message": "Memulai prediksi...",
            "progress": 0,
        })

    # No weekday/weekend pre-check here — run_predict_pipeline always clamps
    # to the latest date that actually has synced data, so requesting a
    # weekend/holiday/not-yet-synced date resolves gracefully to the most
    # recent real trading day instead of hard-failing or silently reusing a
    # stale price under the wrong date label.
    pred_date = body.get("date") or None

    def run():
        def progress_cb(pct, msg):
            with PREDICT_LOCK:
                PREDICT_STATE["progress"] = pct
                PREDICT_STATE["message"] = msg

        from .config import MODELS_DIR
        result = run_predict_pipeline(db_path, MODELS_DIR, pred_date, progress_cb)
        with PREDICT_LOCK:
            if result.get("status") == "success":
                PREDICT_STATE.update({
                    "status": "success",
                    "finished_at": datetime.now().isoformat(),
                    "message": result["message"],
                    "progress": 100,
                    "ensemble_models": result.get("ensemble_models"),
                })
            else:
                PREDICT_STATE.update({
                    "status": "failed",
                    "finished_at": datetime.now().isoformat(),
                    "message": result.get("message", "Prediksi gagal."),
                    "progress": 0,
                })

    threading.Thread(target=run, name="ml-weekly-predict", daemon=True).start()
    return {"ok": True, "message": "Prediksi dimulai.", "status": dict(PREDICT_STATE)}


_RETRAIN_INTERVAL_DAYS = {"daily": 1, "weekly": 7, "monthly": 30}


def _is_retrain_due(db_path: str, cfg) -> bool:
    """True if enough wall-clock time has passed since the last successful
    training run to warrant an automatic retrain, per cfg.retrain_schedule."""
    if cfg.retrain_schedule == "manual":
        return False
    interval_days = _RETRAIN_INTERVAL_DAYS.get(cfg.retrain_schedule, 30)
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT MAX(created_at) FROM ml_weekly_model_runs").fetchone()
    last_trained = row[0] if row else None
    if not last_trained:
        return False  # never trained yet -> nothing for auto-retrain to build on
    try:
        last_dt = datetime.fromisoformat(str(last_trained).replace(" ", "T"))
    except ValueError:
        return False
    return (datetime.now() - last_dt).days >= interval_days


def _maybe_trigger_auto_retrain(db_path: str, models_dir: Path, newly_evaluated: int) -> None:
    """
    The "models improve themselves" loop: called after every predict run
    checks matured signals against real outcomes (evaluate_matured_predictions).
    If that turned up new evaluated signals AND the configured retrain
    schedule is due, kick off a fresh train-all in the background — the
    same one-button pipeline the user can trigger manually, just automatic.
    Fully non-blocking and safe to call from inside a predict call that is
    itself the tail end of a train-all run: start_train_all_job's own
    locking rejects the duplicate start rather than erroring.
    """
    if not newly_evaluated:
        return
    try:
        from .config import get_config
        cfg = get_config()
        if not _is_retrain_due(db_path, cfg):
            return
        log.info(
            "Auto-retrain: %d sinyal baru dievaluasi dan jadwal '%s' sudah jatuh tempo — memulai train-all otomatis.",
            newly_evaluated, cfg.retrain_schedule,
        )
        start_train_all_job(db_path, models_dir)
    except Exception:
        log.exception("Auto-retrain check gagal (non-fatal, prediksi tetap tersimpan)")


def start_train_all_job(db_path: str, models_dir: Path, body: dict = None) -> dict:
    """
    POST /api/weekly/train-all — the "one button" pipeline: sequentially
    (re)trains every ensemble algorithm, then backtests the combined
    ensemble, then generates fresh predictions. Replaces the old workflow
    of manually clicking Train (once per model choice) -> Evaluate -> Predict in
    the right order, which is exactly how threshold/member mismatches and
    duplicate/overlapping model runs happened whenever a step was skipped
    or run out of order.
    """
    with TRAIN_ALL_LOCK:
        if TRAIN_ALL_STATE["status"] == "running":
            return {"ok": False, "message": "Pipeline lengkap sedang berjalan.", "status": TRAIN_ALL_STATE}
        other = _running_job_name()
        if other:
            return {"ok": False, "message": f"Proses lain ({other}) sedang berjalan. Tunggu hingga selesai agar tidak tumpang tindih.", "status": TRAIN_ALL_STATE}
        TRAIN_ALL_STATE.update({
            "status": "running",
            "started_at": datetime.now().isoformat(),
            "finished_at": None,
            "message": "Memulai pipeline lengkap...",
            "progress": 0,
            "result": None,
        })

    def run():
        from .pipeline import run_full_pipeline, build_dataset, is_cancelled
        from .ensemble_eval import evaluate_ensemble
        from .config import get_config, MODELS_DIR

        cfg = get_config()
        n = len(ALL_MODEL_NAMES)
        model_results = []

        # Build the labeled dataset ONCE and reuse it for all 6 ensemble
        # members — they only differ in classifier algorithm, not in the
        # underlying feature/label rows, so rebuilding it per model (the old
        # behavior) was pure waste, worse now that features are computed via
        # the full unified ~130-column engine (see pipeline._vectorized_features).
        def build_pcb(pct, msg):
            with TRAIN_ALL_LOCK:
                TRAIN_ALL_STATE["progress"] = int(pct * 0.35)
                TRAIN_ALL_STATE["message"] = f"[Dataset] {msg}"

        with TRAIN_ALL_LOCK:
            TRAIN_ALL_STATE["message"] = "Membangun dataset dari seluruh riwayat harga (sekali, dipakai semua model)..."

        try:
            dataset = build_dataset(
                db_path=db_path,
                train_start=cfg.training_start,
                holdout_start=cfg.holdout_start,
                horizon_days=cfg.horizon_days,
                primary_target=cfg.primary_return_target,
                take_profit=cfg.take_profit,
                stop_loss=cfg.stop_loss,
                min_history_days=cfg.min_history_days,
                min_median_value_20d=cfg.min_median_value_20d,
                step_days=cfg.training_step_days,
                progress_cb=build_pcb,
            )
        except Exception as exc:
            if is_cancelled():
                with TRAIN_ALL_LOCK:
                    TRAIN_ALL_STATE.update({
                        "status": "failed", "finished_at": datetime.now().isoformat(),
                        "message": "Dibatalkan pengguna saat membangun dataset.", "progress": 0,
                    })
                return
            log.exception("Train-all: dataset build failed")
            with TRAIN_ALL_LOCK:
                TRAIN_ALL_STATE.update({
                    "status": "failed", "finished_at": datetime.now().isoformat(),
                    "message": f"Gagal membangun dataset: {exc}", "progress": 0,
                })
            return

        # Retire any model still marked active/ensemble_member whose
        # algorithm is no longer part of the roster (e.g. plain
        # "gradient_boosting", dropped for not scaling to full-history
        # training — see ALL_MODEL_NAMES). Without this, an old run stays
        # loaded into the live ensemble forever, silently mixing predictions
        # from a model trained on a different (and since fixed) feature set
        # in with the current ones.
        try:
            with sqlite3.connect(db_path) as _conn:
                placeholders = ",".join("?" for _ in ALL_MODEL_NAMES)
                _conn.execute(
                    f"""UPDATE ml_weekly_model_runs SET status='superseded'
                        WHERE status IN ('ensemble_member','active')
                          AND model_name NOT IN ({placeholders})""",
                    ALL_MODEL_NAMES,
                )
                _conn.commit()
        except Exception:
            log.exception("Train-all: could not retire out-of-roster models")

        for i, model_name in enumerate(ALL_MODEL_NAMES):
            base_pct = 35 + int(i / n * 55)

            def pcb(pct, msg, base=base_pct, name=model_name):
                with TRAIN_ALL_LOCK:
                    TRAIN_ALL_STATE["progress"] = base + int(pct / n * 0.55)
                    TRAIN_ALL_STATE["message"] = f"[{i+1}/{n}] {name}: {msg}"

            try:
                res = run_full_pipeline(db_path, MODELS_DIR, cfg, model_name=model_name, progress_cb=pcb, prebuilt_dataset=dataset)
                if res.get("status") == "success":
                    new_run_id = res.get("model_run_id")
                    try:
                        with sqlite3.connect(db_path) as _conn:
                            _conn.execute(
                                "UPDATE ml_weekly_model_runs SET status='superseded' WHERE status='ensemble_member' AND model_name=? AND id != ?",
                                (model_name, new_run_id),
                            )
                            _conn.execute(
                                "UPDATE ml_weekly_model_runs SET status='superseded' WHERE status='active' AND model_name=? AND id != ?",
                                (model_name, new_run_id),
                            )
                            _conn.execute(
                                "UPDATE ml_weekly_model_runs SET status='ensemble_member' WHERE id=?",
                                (new_run_id,),
                            )
                            _conn.commit()
                    except Exception as exc:
                        log.warning("Could not set ensemble_member status for %s: %s", model_name, exc)
                    model_results.append({"model": model_name, "status": "success", "run_id": new_run_id})
                else:
                    model_results.append({"model": model_name, "status": "failed", "error": res.get("error")})
            except Exception as exc:
                log.exception("Train-all: model %s failed", model_name)
                model_results.append({"model": model_name, "status": "failed", "error": str(exc)})

        n_success = sum(1 for r in model_results if r["status"] == "success")
        if n_success < 2:
            with TRAIN_ALL_LOCK:
                TRAIN_ALL_STATE.update({
                    "status": "failed",
                    "finished_at": datetime.now().isoformat(),
                    "message": f"Hanya {n_success}/{n} model berhasil dilatih — tidak cukup untuk ensemble.",
                    "progress": 100,
                    "result": {"models": model_results},
                })
            return

        with TRAIN_ALL_LOCK:
            TRAIN_ALL_STATE["progress"] = 90
            TRAIN_ALL_STATE["message"] = f"{n_success}/{n} model selesai dilatih. Mengevaluasi kombinasi ensemble..."

        def ecb(pct, msg):
            with TRAIN_ALL_LOCK:
                TRAIN_ALL_STATE["progress"] = 90 + int(pct * 0.06)
                TRAIN_ALL_STATE["message"] = msg

        eval_result = evaluate_ensemble(db_path, MODELS_DIR, config=cfg, progress_cb=ecb, prebuilt_dataset=dataset)

        with TRAIN_ALL_LOCK:
            TRAIN_ALL_STATE["progress"] = 96
            TRAIN_ALL_STATE["message"] = "Menghasilkan prediksi terbaru..."

        def pcb2(pct, msg):
            with TRAIN_ALL_LOCK:
                TRAIN_ALL_STATE["progress"] = 96 + int(pct * 0.04)
                TRAIN_ALL_STATE["message"] = msg

        # No date passed — run_predict_pipeline always resolves to whatever
        # date actually has synced data, which is simpler and safer than
        # guessing "today minus weekends" here (misses holidays, sync lag).
        pred_result = run_predict_pipeline(db_path, MODELS_DIR, None, pcb2)

        ok = eval_result.get("status") == "success" and pred_result.get("status") == "success"
        thr_txt = f"{eval_result.get('selected_threshold', 0):.0%}" if eval_result.get("status") == "success" else "?"
        with TRAIN_ALL_LOCK:
            TRAIN_ALL_STATE.update({
                "status": "success" if ok else "failed",
                "finished_at": datetime.now().isoformat(),
                "message": (
                    f"Selesai: {n_success}/{n} model dilatih, threshold ensemble={thr_txt} "
                    f"(holdout {eval_result.get('holdout_status', '?')}). {pred_result.get('message', '')}"
                ),
                "progress": 100,
                "result": {"models": model_results, "ensemble": eval_result, "predict": pred_result},
            })

    threading.Thread(target=run, name="ml-weekly-train-all", daemon=True).start()
    return {"ok": True, "message": "Pipeline lengkap dimulai: latih semua model → evaluasi ensemble → prediksi.", "status": dict(TRAIN_ALL_STATE)}


# ── Helpers ───────────────────────────────────────────────────────────────────
def current_close_fmt(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except Exception:
        return None


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        f"SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None
