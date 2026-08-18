"""Local HTTP application backed by the physical SQLite database."""
from __future__ import annotations

import argparse
import gzip
import json
import math
import mimetypes
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import webbrowser
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import urlopen

from analytics import (
    ANALYTICS_VERSION,
    breadth,
    daily_rows,
    flow_liquidity,
    invalidate_cache,
    methodology,
    overview,
    screener,
    stock_detail,
    get_accumulation_all,
    get_accumulation_stock,
    get_broker_master,
    get_broker_daily,
    get_broker_monthly,
    get_broker_profile,
)
import chat as _chat
from signals_engine import get_screener_signals, HORIZONS as SIGNAL_HORIZONS
from bdm_api import (
    get_bdm_screener, get_bdm_overview, get_bdm_coverage, get_bdm_historical,
    get_bdm_fundamental, get_bdm_quant_analysis, get_bdm_growth_screener,
    start_growth_sync_job, get_growth_sync_status, get_bdm_valuation_history,
)
from db import DB_PATH, ROOT, connect, migrate, schema_version

# ── IDX Weekly High-Confidence ML system ─────────────────────────────────────
try:
    from ml_weekly.api_handlers import (
        handle_status as ml_weekly_status,
        handle_signals as ml_weekly_signals,
        handle_signal_detail as ml_weekly_signal_detail,
        handle_signal_pdf as ml_weekly_signal_pdf,
        handle_signals_pdf as ml_weekly_signals_pdf,
        handle_validation as ml_weekly_validation,
        handle_backtest as ml_weekly_backtest,
        handle_data_quality as ml_weekly_data_quality,
        handle_get_settings as ml_weekly_get_settings,
        handle_update_settings as ml_weekly_update_settings,
        start_training_job as ml_weekly_train,
        start_predict_job as ml_weekly_predict,
        get_train_status as ml_weekly_train_status,
        get_predict_status as ml_weekly_predict_status,
        cancel_training_job as ml_weekly_cancel_train,
        start_ensemble_eval_job as ml_weekly_ensemble_eval,
        get_ensemble_eval_status as ml_weekly_ensemble_eval_status,
        start_train_all_job as ml_weekly_train_all,
        get_train_all_status as ml_weekly_train_all_status,
        handle_evaluation as ml_weekly_evaluation,
        handle_evaluation_signals as ml_weekly_evaluation_signals,
        handle_evaluation_top_signals as ml_weekly_evaluation_top_signals,
        handle_evaluation_pending_progress as ml_weekly_evaluation_pending_progress,
        start_evaluation_job as ml_weekly_evaluation_run,
        get_evaluation_job_status as ml_weekly_evaluation_run_status,
        handle_ihsg_analysis as ml_weekly_ihsg,
    )
    from ml_weekly.db_migration import run_migration as ml_weekly_migrate
    from ml_weekly.config import MODELS_DIR as ML_MODELS_DIR
    ML_WEEKLY_AVAILABLE = True
except ImportError as _ml_import_err:
    ML_WEEKLY_AVAILABLE = False
    import warnings
    warnings.warn(f"ml_weekly module not available: {_ml_import_err}")


OWNERSHIP_FIELDS = """id, record_date, share_code, issuer_name, investor_name,
    classification, local_foreign, nationality, domicile, scripless, scrip,
    (scripless + scrip) AS total_shares, percentage, created_at, updated_at"""
STOCK_FIELDS = """id, code, company_name, COALESCE(listing_date, '') AS listing_date,
    shares, listing_board, created_at, updated_at"""
API_VERSION = 3
SYNC_LOCK = threading.Lock()
SYNC_STATE: dict[str, object] = {
    "status": "idle",
    "started_at": None,
    "finished_at": None,
    "message": "Siap memeriksa data IDX terbaru.",
    "return_code": None,
}


def sync_status() -> dict[str, object]:
    with SYNC_LOCK:
        return dict(SYNC_STATE)


def run_recent_sync() -> None:
    sync_script = ROOT / "tools" / "sync_idx_daily.py"
    if not sync_script.exists():
        with SYNC_LOCK:
            SYNC_STATE.update({
                "status": "failed",
                "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "message": f"Script sinkronisasi tidak ditemukan: {sync_script}\nPastikan folder tools/ ada dan lengkap.",
                "return_code": None,
            })
        return

    command = [
        sys.executable,
        str(sync_script),
        "--start", (date.today() - timedelta(days=35)).isoformat(),
        "--end", date.today().isoformat(),
        "--workers", "2",
        "--delay", "0.8",
        "--refresh-recent", "14",
        "--retries", "7",
    ]
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20 * 60,
            check=False,
        )
        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        # Build informative output - stdout is progress, stderr is errors
        parts = [p for p in (stdout, stderr) if p]
        output = "\n".join(parts) if parts else "Sinkronisasi selesai."
        # Determine status: returncode 0 = ok, 1 = partial failure (some days failed), other = error
        if result.returncode == 0:
            status = "success"
        elif result.returncode == 1 and stdout and "gagal=0" in stdout:
            status = "success"  # returncode 1 but all days ok
        elif result.returncode is not None and result.returncode <= 1:
            status = "failed" if "gagal=" in stdout and "gagal=0" not in stdout else "success"
        else:
            status = "failed"

        # Ringkasan Indeks (IHSG dst) — sumber terpisah dari GetIndexSummary,
        # dijalankan setelah ringkasan saham agar satu klik tombol memperbarui
        # keduanya. Kegagalan di sini tidak menimpa status sinkronisasi saham.
        index_script = ROOT / "tools" / "sync_idx_index_daily.py"
        if index_script.exists():
            try:
                index_result = subprocess.run(
                    [
                        sys.executable, str(index_script),
                        "--start", (date.today() - timedelta(days=35)).isoformat(),
                        "--end", date.today().isoformat(),
                        "--workers", "2", "--delay", "0.8",
                        "--refresh-recent", "14", "--retries", "7",
                    ],
                    cwd=ROOT, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=14 * 60, check=False,
                )
                index_stdout = (index_result.stdout or "").strip()
                index_output = "\n".join(
                    p for p in (index_stdout, (index_result.stderr or "").strip()) if p
                )
                output = f"{output}\n\n[Indeks IHSG]\n{index_output}".strip()
                # The banner's status must reflect BOTH legs, not just the stock
                # sync computed above — otherwise a failed index leg can render
                # as a green "success" banner (or vice versa) while the visible
                # message text says otherwise.
                if index_result.returncode not in (0, None) and not (
                    index_result.returncode == 1 and "gagal=0" in index_stdout
                ):
                    status = "failed"
            except Exception as index_error:
                output = f"{output}\n\n[Indeks IHSG] Error: {type(index_error).__name__}: {index_error}".strip()
                status = "failed"

        if status == "failed" and "gagal=" in output:
            output += (
                "\n\nCatatan: IDX kadang membatasi permintaan otomatis selama beberapa "
                "menit (bukan error permanen di aplikasi). Tanggal yang gagal akan "
                "dicoba ulang otomatis pada sinkronisasi berikutnya — jika masih gagal "
                "setelah beberapa kali coba dalam rentang waktu berbeda, baru itu tanda "
                "ada masalah nyata."
            )

        with SYNC_LOCK:
            SYNC_STATE.update({
                "status": status,
                "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "message": output[-4000:],
                "return_code": result.returncode,
            })
        invalidate_cache()
    except subprocess.TimeoutExpired:
        with SYNC_LOCK:
            SYNC_STATE.update({
                "status": "failed",
                "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "message": "Sinkronisasi melebihi batas waktu 20 menit. Coba lagi nanti.",
                "return_code": None,
            })
    except FileNotFoundError:
        with SYNC_LOCK:
            SYNC_STATE.update({
                "status": "failed",
                "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "message": f"Python tidak ditemukan: {sys.executable}\nPastikan Python terinstall dan tersedia di PATH.",
                "return_code": None,
            })
    except Exception as error:
        with SYNC_LOCK:
            SYNC_STATE.update({
                "status": "failed",
                "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "message": f"Error sinkronisasi: {type(error).__name__}: {error}",
                "return_code": None,
            })


def start_recent_sync() -> tuple[bool, dict[str, object]]:
    with SYNC_LOCK:
        if SYNC_STATE["status"] == "running":
            return False, dict(SYNC_STATE)
        SYNC_STATE.update({
            "status": "running",
            "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "finished_at": None,
            "message": "Memeriksa 35 hari kalender terakhir dari sumber resmi IDX…",
            "return_code": None,
        })
        payload = dict(SYNC_STATE)
    threading.Thread(target=run_recent_sync, name="idx-recent-sync", daemon=True).start()
    return True, payload


# ── Fundamental sync ──
FUNDA_SYNC_LOCK = threading.Lock()
FUNDA_SYNC_STATE: dict[str, object] = {
    "status": "idle",
    "started_at": None,
    "finished_at": None,
    "message": "Siap menyinkronkan data fundamental.",
    "return_code": None,
}

def funda_sync_status() -> dict[str, object]:
    with FUNDA_SYNC_LOCK:
        return dict(FUNDA_SYNC_STATE)

def run_funda_sync() -> None:
    try:
        from tools.sync_idx_fundamental_yfinance import sync_fundamental_yf
        res = sync_fundamental_yf()
        with FUNDA_SYNC_LOCK:
            FUNDA_SYNC_STATE.update({
                "status": "success",
                "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "message": f"Sinkronisasi fundamental YF berhasil. {res}"
            })
    except Exception as e:
        with FUNDA_SYNC_LOCK:
            FUNDA_SYNC_STATE.update({
                "status": "failed",
                "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "message": f"Error: {e}"
            })

def start_funda_sync() -> tuple[bool, dict[str, object]]:
    with FUNDA_SYNC_LOCK:
        if FUNDA_SYNC_STATE["status"] == "running":
            return False, dict(FUNDA_SYNC_STATE)
        FUNDA_SYNC_STATE.update({
            "status": "running",
            "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "finished_at": None,
            "message": "Sedang menyinkronkan data fundamental dari yfinance...",
            "return_code": None,
        })
        payload = dict(FUNDA_SYNC_STATE)
    threading.Thread(target=run_funda_sync, name="idx-funda-sync", daemon=True).start()
    return True, payload

# ── Broker sync ──────────────────────────────────────────────────────────────
BROKER_SYNC_LOCK = threading.Lock()
BROKER_SYNC_STATE: dict[str, object] = {
    "status": "idle",
    "started_at": None,
    "finished_at": None,
    "message": "Siap memeriksa data broker IDX terbaru.",
    "return_code": None,
}


def broker_sync_status() -> dict[str, object]:
    with BROKER_SYNC_LOCK:
        return dict(BROKER_SYNC_STATE)


def run_broker_sync() -> None:
    broker_script = ROOT / "tools" / "idx_broker_pipeline.py"
    if not broker_script.exists():
        with BROKER_SYNC_LOCK:
            BROKER_SYNC_STATE.update({
                "status": "failed",
                "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "message": f"Script broker tidak ditemukan: {broker_script}",
                "return_code": None,
            })
        return

    # Run: fetch-master + fetch-idx-summary for last 35 days
    start_date = (date.today() - timedelta(days=35)).isoformat()
    end_date = date.today().isoformat()
    command = [
        sys.executable,
        str(broker_script),
        "all",
        "--start", start_date,
        "--end", end_date,
        "--delay", "1.5",
    ]
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30 * 60,
            check=False,
        )
        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        parts = [p for p in (stdout, stderr) if p]
        output = "\n".join(parts) if parts else "Sinkronisasi broker selesai."
        status = "success" if result.returncode == 0 else "failed"
        with BROKER_SYNC_LOCK:
            BROKER_SYNC_STATE.update({
                "status": status,
                "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "message": output[-4000:],
                "return_code": result.returncode,
            })
        invalidate_cache()
    except subprocess.TimeoutExpired:
        with BROKER_SYNC_LOCK:
            BROKER_SYNC_STATE.update({
                "status": "failed",
                "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "message": "Sinkronisasi broker melebihi batas waktu 30 menit.",
                "return_code": None,
            })
    except Exception as error:
        with BROKER_SYNC_LOCK:
            BROKER_SYNC_STATE.update({
                "status": "failed",
                "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "message": f"Error sinkronisasi broker: {type(error).__name__}: {error}",
                "return_code": None,
            })


def start_broker_sync() -> tuple[bool, dict[str, object]]:
    with BROKER_SYNC_LOCK:
        if BROKER_SYNC_STATE["status"] == "running":
            return False, dict(BROKER_SYNC_STATE)
        BROKER_SYNC_STATE.update({
            "status": "running",
            "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "finished_at": None,
            "message": f"Mengunduh data broker IDX 35 hari terakhir…",
            "return_code": None,
        })
        payload = dict(BROKER_SYNC_STATE)
    threading.Thread(target=run_broker_sync, name="idx-broker-sync", daemon=True).start()
    return True, payload




def ownership_payload(data: dict) -> dict:
    required = ("record_date", "share_code", "issuer_name", "investor_name", "local_foreign", "scripless", "scrip", "percentage")
    if any(data.get(key) in (None, "") for key in required):
        raise ValueError("Lengkapi semua kolom wajib kepemilikan.")
    try:
        date.fromisoformat(str(data["record_date"]))
    except ValueError as error:
        raise ValueError("Tanggal posisi tidak valid.") from error
    origin = str(data["local_foreign"]).upper()
    if origin not in {"L", "F", "N"}:
        raise ValueError("Asal investor harus L, F, atau N.")
    try:
        if isinstance(data["scripless"], float) and not data["scripless"].is_integer():
            raise ValueError
        if isinstance(data["scrip"], float) and not data["scrip"].is_integer():
            raise ValueError
        scripless = int(data["scripless"])
        scrip = int(data["scrip"])
        percentage = float(data["percentage"])
    except (TypeError, ValueError) as error:
        raise ValueError("Jumlah saham dan persentase harus berupa angka.") from error
    if scripless < 0 or scrip < 0 or not math.isfinite(percentage) or not 0 <= percentage <= 100:
        raise ValueError("Jumlah saham atau persentase berada di luar batas yang diizinkan.")
    result = {
        "record_date": str(data["record_date"]),
        "share_code": str(data["share_code"]).strip().upper(),
        "issuer_name": str(data["issuer_name"]).strip(),
        "investor_name": str(data["investor_name"]).strip(),
        "classification": str(data.get("classification") or "").strip(),
        "local_foreign": origin,
        "nationality": str(data.get("nationality") or "").strip(),
        "domicile": str(data.get("domicile") or "").strip(),
        "scripless": scripless,
        "scrip": scrip,
        "percentage": percentage,
    }
    if not result["share_code"] or not result["issuer_name"] or not result["investor_name"]:
        raise ValueError("Kode saham, nama emiten, dan investor tidak boleh kosong.")
    return result


def stock_payload(data: dict) -> dict:
    if any(data.get(key) in (None, "") for key in ("code", "company_name", "shares", "listing_board")):
        raise ValueError("Lengkapi semua kolom wajib master saham.")
    listing_date = str(data.get("listing_date") or "")
    if listing_date:
        try:
            date.fromisoformat(listing_date)
        except ValueError as error:
            raise ValueError("Tanggal listing tidak valid.") from error
    try:
        if isinstance(data["shares"], float) and not data["shares"].is_integer():
            raise ValueError
        shares = int(data["shares"])
    except (TypeError, ValueError) as error:
        raise ValueError("Jumlah saham harus berupa bilangan bulat.") from error
    if shares < 0:
        raise ValueError("Jumlah saham tidak boleh negatif.")
    result = {
        "code": str(data["code"]).strip().upper(),
        "company_name": str(data["company_name"]).strip(),
        "listing_date": listing_date or None,
        "shares": shares,
        "listing_board": str(data["listing_board"]).strip(),
    }
    if not result["code"] or not result["company_name"] or not result["listing_board"]:
        raise ValueError("Kode, nama perusahaan, dan papan pencatatan tidak boleh kosong.")
    return result


# ── Watchlist data helper ─────────────────────────────────────────────────
def _compute_rsi14(closes: list[float]) -> float | None:
    """Compute RSI-14 from a list of closing prices."""
    if len(closes) < 15:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(abs(min(d, 0.0)))
    ag = sum(gains[-14:]) / 14
    al = sum(losses[-14:]) / 14
    if al == 0:
        return 100.0
    return round(100 - 100 / (1 + ag / al), 1)


def _compute_sma(closes: list[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def _detect_trend(closes: list[float]) -> str:
    if not closes:
        return "—"
    p = closes[-1]
    s20 = _compute_sma(closes, 20)
    s50 = _compute_sma(closes, 50)
    if s20 and s50:
        if p > s20 and p > s50:
            return "Uptrend"
        if p < s20 and p < s50:
            return "Downtrend"
    elif s20:
        return "Uptrend" if p > s20 else "Downtrend"
    return "Sideways"


def _simple_signal(rsi: float | None, trend: str, change_pct: float) -> tuple[str, str]:
    """Return (signal_label, signal_class)."""
    if rsi is None:
        return ("—", "neutral")
    if rsi <= 30 and trend == "Uptrend":
        return ("Strong Buy", "strong-buy")
    if rsi <= 40 and trend in ("Uptrend", "Sideways"):
        return ("Buy", "buy")
    if rsi >= 70 and trend == "Downtrend":
        return ("Strong Sell", "strong-sell")
    if rsi >= 65 and trend == "Downtrend":
        return ("Sell", "sell")
    if trend == "Uptrend" and change_pct >= 0:
        return ("Accumulate", "buy")
    if trend == "Downtrend" and change_pct < 0:
        return ("Caution", "sell")
    return ("Netral", "neutral")


def _watchlist_data(codes: list[str]) -> dict:
    ph = ",".join(["?" for _ in codes])
    try:
        with connect() as conn:
            # 1. Latest price row per code
            rows = conn.execute(f"""
                SELECT r.kode_saham,
                       COALESCE(s.company_name, r.nama_perusahaan, r.kode_saham) AS nama,
                       r.tanggal, r.harga_penutupan,
                       COALESCE(r.volume, 0) AS volume,
                       COALESCE(r.nilai_transaksi, 0) AS nilai,
                       COALESCE(r.frekuensi, 0) AS frekuensi,
                       COALESCE(r.perubahan, 0) AS perubahan,
                       CASE WHEN COALESCE(r.sebelumnya, 0) > 0
                            THEN ROUND(r.perubahan / r.sebelumnya * 100, 2)
                            ELSE 0 END AS persen,
                       COALESCE(s.listing_board, '') AS board,
                       COALESCE(s.shares, 0) AS shares_out
                FROM ringkasan_saham_harian r
                INNER JOIN (
                    SELECT kode_saham, MAX(tanggal) AS mx
                    FROM ringkasan_saham_harian WHERE kode_saham IN ({ph})
                    GROUP BY kode_saham
                ) latest ON r.kode_saham = latest.kode_saham AND r.tanggal = latest.mx
                LEFT JOIN idx_stocks s ON s.code = r.kode_saham
            """, codes).fetchall()

            # 2. Full history for sparkline + RSI + 52W
            hist = conn.execute(f"""
                SELECT kode_saham, tanggal, harga_penutupan, COALESCE(volume, 0)
                FROM ringkasan_saham_harian
                WHERE kode_saham IN ({ph})
                  AND harga_penutupan IS NOT NULL AND harga_penutupan > 0
                ORDER BY kode_saham, tanggal ASC
            """, codes).fetchall()

    except Exception as e:
        return {"items": [], "as_of": "", "error": str(e)}

    # Build price history per code
    hist_map: dict[str, list] = {}
    for r in hist:
        hist_map.setdefault(r[0], []).append((r[1], float(r[2]), int(r[3] or 0)))

    items = []
    as_of = ""
    for row in rows:
        code = row[0]
        as_of = row[2] or as_of
        price = float(row[3] or 0)
        vol = int(row[4] or 0)
        val = float(row[5] or 0)
        freq = int(row[6] or 0)
        chg = float(row[7])
        chg_pct = float(row[8])
        hist_prices = [h[1] for h in hist_map.get(code, [])]

        # Indicators
        rsi = _compute_rsi14(hist_prices)
        trend = _detect_trend(hist_prices)
        signal_label, signal_class = _simple_signal(rsi, trend, chg_pct)

        # ATR-14 for TP/SL suggestion
        atr = None
        if len(hist_prices) >= 15:
            diffs = [abs(hist_prices[i] - hist_prices[i-1]) for i in range(1, len(hist_prices))]
            atr = round(sum(diffs[-14:]) / 14, 0)

        # 52-week high/low (last ~252 sessions)
        recent = hist_prices[-252:] if len(hist_prices) >= 252 else hist_prices
        w52_hi = max(recent) if recent else None
        w52_lo = min(recent) if recent else None

        # Sparkline: last 30 days
        sparkline = [h[1] for h in hist_map.get(code, [])[-30:]]

        # Volume avg-20
        vol_hist = [h[2] for h in hist_map.get(code, [])[-21:-1]]
        vol_avg20 = int(sum(vol_hist) / len(vol_hist)) if vol_hist else 0
        vol_ratio = round(vol / vol_avg20, 2) if vol_avg20 else None

        # Suggested TP/SL (1W horizon, ATR × 2.5 / × 1.5)
        tp_sug = round(price + (atr * 2.5), 0) if atr else None
        sl_sug = round(price - (atr * 1.5), 0) if atr else None

        items.append({
            "code": code,
            "name": row[1],
            "date": row[2] or "",
            "price": price,
            "change": chg,
            "change_pct": round(chg_pct, 2),
            "volume": vol,
            "value": val,
            "freq": freq,
            "board": row[9],
            "rsi": rsi,
            "trend": trend,
            "signal": signal_label,
            "signal_class": signal_class,
            "atr": atr,
            "w52_hi": w52_hi,
            "w52_lo": w52_lo,
            "vol_avg20": vol_avg20,
            "vol_ratio": vol_ratio,
            "tp_sug": tp_sug,
            "sl_sug": sl_sug,
            "sparkline": sparkline,
        })

    # Preserve requested order
    code_map = {item["code"]: item for item in items}
    ordered = [code_map[c] for c in codes if c in code_map]
    # Add stubs for not-found codes
    found = {i["code"] for i in items}
    for c in codes:
        if c not in found:
            ordered.append({"code": c, "name": c, "price": 0, "change_pct": 0,
                            "signal": "—", "signal_class": "neutral",
                            "sparkline": [], "error": "Data tidak ditemukan"})

    return {"items": ordered, "as_of": as_of}

class AppHandler(BaseHTTPRequestHandler):
    server_version = "ZaidenTrader/3.1"

    def log_message(self, format_string: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {format_string % args}")

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def send_bytes(self, body: bytes, content_type: str, status: int = 200, filename: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, payload, status: int = 200) -> None:
        def _sanitize(obj):
            """Recursively replace NaN/Inf with None so JSON is valid."""
            if isinstance(obj, float):
                import math
                return None if (math.isnan(obj) or math.isinf(obj)) else obj
            if isinstance(obj, dict):
                return {k: _sanitize(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_sanitize(v) for v in obj]
            return obj

        body = json.dumps(_sanitize(payload), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if "gzip" in self.headers.get("Accept-Encoding", "") and len(body) > 1024:
            body = gzip.compress(body, compresslevel=5)
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_bytes(body, "application/json; charset=utf-8", status)


    def send_file(self, file_path: Path, content_type: str, filename: str) -> None:
        """Stream a large file without loading the full database into memory."""
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(file_path.stat().st_size))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        with file_path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                self.wfile.write(chunk)

    def read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("Content-Length tidak valid.") from error
        if length <= 0 or length > 2_000_000:
            raise ValueError("Ukuran permintaan tidak valid.")
        try:
            payload = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("Format JSON tidak valid.") from error
        if not isinstance(payload, dict):
            raise ValueError("Isi permintaan harus berupa objek JSON.")
        return payload

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/"):
            self.handle_api_get(path, parse_qs(parsed.query))
        else:
            self.serve_static(path)

    def do_POST(self) -> None:
        parsed_path = urlparse(self.path).path
        if parsed_path == "/api/analytics/sync":
            started, payload = start_recent_sync()
            self.send_json(payload, 202 if started else 409)
            return
        if parsed_path == "/api/broker/sync":
            started, payload = start_broker_sync()
            self.send_json(payload, 202 if started else 409)
            return
        # ── IDX Weekly High-Confidence ML ─────────────────────────────────────────
        if ML_WEEKLY_AVAILABLE and parsed_path == "/api/weekly/train/cancel":
            result = ml_weekly_cancel_train()
            return self.send_json(result)

        if ML_WEEKLY_AVAILABLE and parsed_path == "/api/weekly/train":

            try:
                body = self.read_json()
                result = ml_weekly_train(str(DB_PATH), ML_MODELS_DIR, body)
                return self.send_json(result, 202)
            except Exception as exc:
                return self.send_json({"error": str(exc)}, 500)
        if ML_WEEKLY_AVAILABLE and parsed_path == "/api/weekly/predict":
            try:
                body = self.read_json()
                result = ml_weekly_predict(str(DB_PATH), ML_MODELS_DIR, body)
                return self.send_json(result, 202)
            except Exception as exc:
                return self.send_json({"error": str(exc)}, 500)
        if ML_WEEKLY_AVAILABLE and parsed_path == "/api/weekly/ensemble/evaluate":
            try:
                result = ml_weekly_ensemble_eval(str(DB_PATH), ML_MODELS_DIR)
                return self.send_json(result, 202)
            except Exception as exc:
                return self.send_json({"error": str(exc)}, 500)
        if ML_WEEKLY_AVAILABLE and parsed_path == "/api/weekly/train-all":
            try:
                result = ml_weekly_train_all(str(DB_PATH), ML_MODELS_DIR, {})
                return self.send_json(result, 202)
            except Exception as exc:
                return self.send_json({"error": str(exc)}, 500)
        if ML_WEEKLY_AVAILABLE and parsed_path == "/api/weekly/evaluation/run":
            try:
                result = ml_weekly_evaluation_run(str(DB_PATH))
                return self.send_json(result, 202)
            except Exception as exc:
                return self.send_json({"error": str(exc)}, 500)
        if parsed_path == "/api/chat":
            try:
                body = self.read_json()
                question = str(body.get("question", "")).strip()
                history  = body.get("history", [])
                if not question:
                    return self.send_json({"error": "Pertanyaan tidak boleh kosong."}, 400)
                if not _chat.is_configured():
                    return self.send_json({"error": "API key Gemini belum dikonfigurasi."}, 403)
                result = _chat.chat_query(question, history)
                return self.send_json(result)
            except Exception as exc:
                return self.send_json({"error": str(exc)}, 500)
        if parsed_path == "/api/chat/key":
            try:
                body = self.read_json()
                key  = str(body.get("key", "")).strip()
                if not key:
                    return self.send_json({"error": "API key tidak boleh kosong."}, 400)
                _chat.set_api_key(key)
                return self.send_json({"ok": True, "message": "API key berhasil disimpan."})
            except Exception as exc:
                return self.send_json({"error": str(exc)}, 500)
        self.handle_write("POST")

    def do_PUT(self) -> None:
        parsed_path = urlparse(self.path).path
        # IDX Weekly settings PUT
        if ML_WEEKLY_AVAILABLE and parsed_path == "/api/weekly/settings":
            try:
                body = self.read_json()
                result = ml_weekly_update_settings(str(DB_PATH), body)
                return self.send_json(result)
            except Exception as exc:
                return self.send_json({"error": str(exc)}, 500)
        self.handle_write("PUT")

    def do_DELETE(self) -> None:
        self.handle_delete()

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def serve_static(self, request_path: str) -> None:
        if request_path in {"", "/"}:
            file_path = ROOT / "index.html"
        elif request_path == "/index.html":
            file_path = ROOT / "index.html"
        elif request_path == "/assets/brand-icon.png":
            file_path = (ROOT / "icon" / "icon.png").resolve()
            if not file_path.is_file():
                return self.send_json({"error": "File tidak ditemukan."}, 404)
        elif request_path == "/favicon.ico":
            file_path = (ROOT / "icon" / "icon.png").resolve()
            if not file_path.is_file():
                return self.send_json({"error": "File tidak ditemukan."}, 404)
        elif request_path.startswith("/assets/"):
            relative = Path(unquote(request_path.lstrip("/")))
            file_path = (ROOT / relative).resolve()
            assets_root = (ROOT / "assets").resolve()
            if assets_root not in file_path.parents or file_path.suffix.lower() not in {".css", ".js", ".png", ".svg", ".ico"}:
                return self.send_json({"error": "File tidak ditemukan."}, 404)
        elif request_path.startswith("/icon/"):
            relative = Path(unquote(request_path.lstrip("/")))
            file_path = (ROOT / relative).resolve()
            icon_root = (ROOT / "icon").resolve()
            if icon_root not in file_path.parents or file_path.suffix.lower() not in {".png", ".svg", ".ico", ".jpg", ".jpeg", ".webp"}:
                return self.send_json({"error": "File tidak ditemukan."}, 404)
        else:
            return self.send_json({"error": "Halaman tidak ditemukan."}, 404)
        if not file_path.is_file():
            return self.send_json({"error": "File tidak ditemukan."}, 404)
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        self.send_bytes(file_path.read_bytes(), f"{content_type}; charset=utf-8" if content_type.startswith("text/") or content_type == "application/javascript" else content_type)


    def handle_api_get(self, path: str, query: dict[str, list[str]] | None = None) -> None:
        query = query or {}

        def query_value(name: str, default: str = "") -> str:
            values = query.get(name)
            return values[0] if values else default

        try:
            if path == "/api/chat/status":
                return self.send_json({
                    "configured": _chat.is_configured(),
                    "model": "gemini-2.0-flash-lite",
                })
            if path == "/api/health":
                with connect() as connection:
                    tables = [row[0] for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                    )]
                    owners = connection.execute("SELECT COUNT(*) FROM ownership_positions").fetchone()[0]
                    stocks = connection.execute("SELECT COUNT(*) FROM idx_stocks").fetchone()[0]
                    daily = connection.execute("SELECT COUNT(*), MIN(tanggal), MAX(tanggal) FROM ringkasan_saham_harian").fetchone()
                return self.send_json({
                    "ok": True,
                    "api_version": API_VERSION,
                    "analytics_version": ANALYTICS_VERSION,
                    "analytics_ready": bool(daily[0] and daily[2]),
                    "engine": "SQLite",
                    "database": DB_PATH.name,
                    "schema_version": schema_version(),
                    "tables": tables,
                    "ownership": owners,
                    "stocks": stocks,
                    "daily_rows": daily[0],
                    "daily_from": daily[1],
                    "daily_to": daily[2],
                })
            if path == "/api/bootstrap":
                with connect() as connection:
                    ownership = [dict(row) for row in connection.execute(f"SELECT {OWNERSHIP_FIELDS} FROM ownership_positions ORDER BY record_date DESC, share_code, investor_name")]
                    stocks = [dict(row) for row in connection.execute(f"SELECT {STOCK_FIELDS} FROM idx_stocks ORDER BY code")]
                return self.send_json({"ownership": ownership, "stocks": stocks})
            if path == "/api/schema":
                with connect() as connection:
                    tables = {}
                    names = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
                    for name in names:
                        tables[name] = [dict(row) for row in connection.execute(f"PRAGMA table_info('{name}')")]
                return self.send_json({"database": DB_PATH.name, "schema_version": schema_version(), "tables": tables})
            if path == "/api/database/backup":
                return self.send_database_backup()
            if path == "/api/analytics/overview":
                return self.send_json(overview())
            if path == "/api/analytics/methodology":
                return self.send_json(methodology())
            if path == "/api/analytics/sync-status":
                return self.send_json(sync_status())
            if path == "/api/analytics/sync-idx-now-status":
                return self.send_json(funda_sync_status())
            if path == "/api/broker/sync-status":
                return self.send_json(broker_sync_status())
            if path == "/api/analytics/bdm-screener":
                return self.send_json(get_bdm_screener())
            if path == "/api/analytics/bdm-overview":
                return self.send_json(get_bdm_overview(query_value("code")))

            if path == "/api/analytics/bdm-coverage":
                return self.send_json(get_bdm_coverage())
                
            if path == "/api/analytics/bdm-historical":
                code = query.get("code", [""])[0]
                date_start = query.get("start", [""])[0]
                date_end = query.get("end", [""])[0]
                page = int(query.get("page", ["1"])[0])
                limit = int(query.get("limit", ["100"])[0])
                return self.send_json(get_bdm_historical(code, date_start, date_end, page, limit))
                
            if path == "/api/analytics/bdm-fundamental":
                return self.send_json(get_bdm_fundamental())
                
            if path == "/api/analytics/bdm-growth":
                return self.send_json(get_bdm_growth_screener())
            if path == "/api/analytics/bdm-valuation-history":
                code = query_value("code")
                if not code:
                    return self.send_json({"error": "Parameter 'code' diperlukan."}, 400)
                return self.send_json(get_bdm_valuation_history(code))
            if path == "/api/analytics/bdm-growth/sync/status":
                return self.send_json(get_growth_sync_status())
            if path == "/api/analytics/bdm-quant":
                return self.send_json(get_bdm_quant_analysis())

            if path == "/api/analytics/breadth":
                return self.send_json(breadth(int(query_value("days", "90"))))
            if path == "/api/analytics/screener":
                return self.send_json(screener())
            if path == "/api/analytics/stock":
                return self.send_json(stock_detail(query_value("code"), int(query_value("days", "260"))))
            if path == "/api/analytics/flow-liquidity":
                return self.send_json(flow_liquidity(
                    int(query_value("window", "20")),
                    int(query_value("limit", "30")),
                    int(query_value("history_days", "252")),
                ))
            if path == "/api/analytics/daily":
                return self.send_json(daily_rows(
                    code=query_value("code"),
                    date_from=query_value("from"),
                    date_to=query_value("to"),
                    page=int(query_value("page", "1")),
                    page_size=int(query_value("page_size", "50")),
                ))
            if path == "/api/analytics/accumulation":
                code    = query_value("code")
                period  = query_value("period", "bulanan")
                df      = query_value("date_from") or None
                dt      = query_value("date_to")   or None
                if code:
                    return self.send_json(get_accumulation_stock(code, period, date_from=df, date_to=dt))
                else:
                    return self.send_json(get_accumulation_all(period, date_from=df, date_to=dt))
            if path == "/api/screener/signals":
                horizon = query_value("horizon", "1w")
                limit   = min(int(query_value("limit") or "30"), 100)
                return self.send_json(get_screener_signals(horizon, limit))
            if path == "/api/screener/horizons":
                return self.send_json({
                    "horizons": [
                        {"id": k, "label": v["label"], "description": v["description"]}
                        for k, v in SIGNAL_HORIZONS.items()
                    ]
                })
            if path == "/api/watchlist/data":
                codes_raw = query_value("codes", "")
                codes = [c.strip().upper() for c in codes_raw.split(",") if c.strip()][:60]
                if not codes:
                    return self.send_json({"items": [], "as_of": ""})
                return self.send_json(_watchlist_data(codes))
            if path == "/api/broker/master":
                return self.send_json(get_broker_master())
            if path == "/api/broker/daily":
                # Support both legacy ?date= and new ?date_from=&date_to=
                legacy = query_value("date")
                df = query_value("date_from") or legacy
                dt = query_value("date_to")   or legacy
                return self.send_json(get_broker_daily(date_from=df or None, date_to=dt or None))
            if path == "/api/broker/monthly":
                return self.send_json(get_broker_monthly(query_value("code") or None))
            if path == "/api/broker/profile":
                code = query_value("code")
                if not code:
                    return self.send_json({"error": "Parameter 'code' diperlukan."}, 400)
                return self.send_json(get_broker_profile(code))
            # ── IDX Weekly High-Confidence ML routes ───────────────────────
            if ML_WEEKLY_AVAILABLE and path.startswith("/api/weekly/"):
                try:
                    if path == "/api/weekly/status":
                        return self.send_json(ml_weekly_status(str(DB_PATH)))
                    if path == "/api/weekly/signals/pdf":
                        pdf_bytes, fname = ml_weekly_signals_pdf(str(DB_PATH), query)
                        if pdf_bytes is None:
                            return self.send_json({"error": "Tidak ada prediksi untuk dijadikan laporan PDF."}, 404)
                        return self.send_bytes(pdf_bytes, "application/pdf", filename=fname)
                    if path.startswith("/api/weekly/signals/") and path.endswith("/pdf"):
                        ticker = path.split("/")[-2].upper()
                        pdf_bytes, fname = ml_weekly_signal_pdf(str(DB_PATH), ticker, query)
                        if pdf_bytes is None:
                            return self.send_json({"error": f"Tidak ada prediksi untuk {ticker}."}, 404)
                        return self.send_bytes(pdf_bytes, "application/pdf", filename=fname)
                    if path == "/api/weekly/signals":
                        # Check for ticker detail: /api/weekly/signals/BBCA
                        return self.send_json(ml_weekly_signals(str(DB_PATH), query))
                    if path.startswith("/api/weekly/signals/"):
                        ticker = path.split("/")[-1].upper()
                        return self.send_json(ml_weekly_signal_detail(str(DB_PATH), ticker, query))
                    if path == "/api/weekly/validation":
                        return self.send_json(ml_weekly_validation(str(DB_PATH)))
                    if path == "/api/weekly/backtest":
                        return self.send_json(ml_weekly_backtest(str(DB_PATH), query))
                    if path == "/api/weekly/data-quality":
                        return self.send_json(ml_weekly_data_quality(str(DB_PATH)))
                    if path == "/api/weekly/evaluation":
                        return self.send_json(ml_weekly_evaluation(str(DB_PATH), query))
                    if path == "/api/weekly/evaluation/signals":
                        return self.send_json(ml_weekly_evaluation_signals(str(DB_PATH), query))
                    if path == "/api/weekly/evaluation/top-signals":
                        return self.send_json(ml_weekly_evaluation_top_signals(str(DB_PATH), query))
                    if path == "/api/weekly/evaluation/pending-progress":
                        return self.send_json(ml_weekly_evaluation_pending_progress(str(DB_PATH), query))
                    if path == "/api/weekly/evaluation/run/status":
                        return self.send_json(ml_weekly_evaluation_run_status())
                    if path == "/api/weekly/ihsg":
                        return self.send_json(ml_weekly_ihsg(str(DB_PATH)))
                    if path == "/api/weekly/settings":
                        return self.send_json(ml_weekly_get_settings(str(DB_PATH)))
                    if path == "/api/weekly/train/status":
                        return self.send_json(ml_weekly_train_status())
                    if path == "/api/weekly/predict/status":
                        return self.send_json(ml_weekly_predict_status())
                    if path == "/api/weekly/ensemble/evaluate/status":
                        return self.send_json(ml_weekly_ensemble_eval_status())
                    if path == "/api/weekly/train-all/status":
                        return self.send_json(ml_weekly_train_all_status())
                    return self.send_json({"error": f"Weekly endpoint tidak ditemukan: {path}"}, 404)
                except Exception as _wkly_err:
                    return self.send_json({"error": str(_wkly_err)}, 500)
            if not ML_WEEKLY_AVAILABLE and path.startswith("/api/weekly/"):
                return self.send_json({"error": "Modul ml_weekly tidak tersedia. Pastikan numpy, pandas, scikit-learn terinstall."}, 503)
            item = self.route_item(path)
            if item:
                resource, record_id = item
                table, fields = ("ownership_positions", OWNERSHIP_FIELDS) if resource == "ownership" else ("idx_stocks", STOCK_FIELDS)
                with connect() as connection:
                    row = connection.execute(f"SELECT {fields} FROM {table} WHERE id = ?", (record_id,)).fetchone()
                return self.send_json(dict(row) if row else {"error": "Data tidak ditemukan."}, 200 if row else 404)
            self.send_json({"error": "Endpoint tidak ditemukan."}, 404)
        except ValueError as error:
            self.send_json({"error": str(error)}, 400)
        except LookupError as error:
            self.send_json({"error": str(error)}, 404)
        except Exception as error:
            self.send_json({"error": str(error)}, 500)

    @staticmethod
    def route_item(path: str) -> tuple[str, int] | None:
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "api" and parts[1] in {"ownership", "stocks"} and parts[2].isdigit():
            return parts[1], int(parts[2])
        return None

    def handle_write(self, method: str) -> None:
        path = urlparse(self.path).path
        try:
            if method == "POST":
                if path == "/api/analytics/sync-idx-now":
                    try:
                        started, payload = start_funda_sync()
                        return self.send_json(payload, 202 if started else 409)
                    except Exception as e:
                        return self.send_json({"error": f"Gagal sinkronisasi: {str(e)}"}, 500)
                if path == "/api/analytics/bdm-growth/sync":
                    result = start_growth_sync_job()
                    return self.send_json(result, 202)
                parts = path.strip("/").split("/")
                if len(parts) != 2 or parts[0] != "api" or parts[1] not in {"ownership", "stocks"}:
                    return self.send_json({"error": "Endpoint tidak ditemukan."}, 404)
                resource, record_id = parts[1], None
            else:
                item = self.route_item(path)
                if not item:
                    return self.send_json({"error": "Endpoint tidak ditemukan."}, 404)
                resource, record_id = item
            data = self.read_json()
            if resource == "ownership":
                saved = self.write_ownership(data, record_id)
            else:
                saved = self.write_stock(data, record_id)
            self.send_json(saved, 201 if method == "POST" else 200)
        except ValueError as error:
            self.send_json({"error": str(error)}, 400)
        except LookupError as error:
            self.send_json({"error": str(error)}, 404)
        except sqlite3.IntegrityError as error:
            message = str(error)
            if "UNIQUE" in message:
                message = "Data dengan kunci yang sama sudah ada di database."
            elif "FOREIGN KEY" in message:
                message = "Kode saham belum ada di master atau masih digunakan oleh data kepemilikan."
            else:
                message = "Data melanggar aturan validasi SQLite."
            self.send_json({"error": message}, 409)
        except Exception as error:
            self.send_json({"error": str(error)}, 500)

    def write_ownership(self, data: dict, record_id: int | None) -> dict:
        values = ownership_payload(data)
        with connect() as connection:
            if record_id is None:
                cursor = connection.execute(
                    """INSERT INTO ownership_positions(
                           record_date, share_code, issuer_name, investor_name, classification,
                           local_foreign, nationality, domicile, scripless, scrip, percentage
                       ) VALUES(
                           :record_date, :share_code, :issuer_name, :investor_name, :classification,
                           :local_foreign, :nationality, :domicile, :scripless, :scrip, :percentage
                       )""",
                    values,
                )
                record_id = cursor.lastrowid
            else:
                values["id"] = record_id
                cursor = connection.execute(
                    """UPDATE ownership_positions SET
                           record_date=:record_date, share_code=:share_code, issuer_name=:issuer_name,
                           investor_name=:investor_name, classification=:classification,
                           local_foreign=:local_foreign, nationality=:nationality, domicile=:domicile,
                           scripless=:scripless, scrip=:scrip, percentage=:percentage
                       WHERE id=:id""",
                    values,
                )
                if not cursor.rowcount:
                    raise LookupError("Data kepemilikan tidak ditemukan.")
            row = connection.execute(f"SELECT {OWNERSHIP_FIELDS} FROM ownership_positions WHERE id = ?", (record_id,)).fetchone()
        return dict(row)

    def write_stock(self, data: dict, record_id: int | None) -> dict:
        values = stock_payload(data)
        with connect() as connection:
            if record_id is None:
                cursor = connection.execute(
                    """INSERT INTO idx_stocks(code, company_name, listing_date, shares, listing_board)
                       VALUES(:code, :company_name, :listing_date, :shares, :listing_board)""",
                    values,
                )
                record_id = cursor.lastrowid
            else:
                values["id"] = record_id
                cursor = connection.execute(
                    """UPDATE idx_stocks SET code=:code, company_name=:company_name,
                           listing_date=:listing_date, shares=:shares, listing_board=:listing_board
                       WHERE id=:id""",
                    values,
                )
                if not cursor.rowcount:
                    raise LookupError("Master saham tidak ditemukan.")
            row = connection.execute(f"SELECT {STOCK_FIELDS} FROM idx_stocks WHERE id = ?", (record_id,)).fetchone()
        return dict(row)

    def handle_delete(self) -> None:
        path = urlparse(self.path).path
        item = self.route_item(path)
        if not item:
            return self.send_json({"error": "Endpoint tidak ditemukan."}, 404)
        resource, record_id = item
        table = "ownership_positions" if resource == "ownership" else "idx_stocks"
        try:
            with connect() as connection:
                cursor = connection.execute(f"DELETE FROM {table} WHERE id = ?", (record_id,))
                if not cursor.rowcount:
                    return self.send_json({"error": "Data tidak ditemukan."}, 404)
            self.send_json({"ok": True})
        except sqlite3.IntegrityError:
            self.send_json({"error": "Saham masih digunakan oleh data kepemilikan dan tidak dapat dihapus."}, 409)
        except Exception as error:
            self.send_json({"error": str(error)}, 500)

    def send_database_backup(self) -> None:
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as temporary:
                temporary_path = Path(temporary.name)
            source = connect()
            target = sqlite3.connect(temporary_path)
            try:
                source.backup(target)
            finally:
                target.close()
                source.close()
            filename = f"zaiden_trader_backup_{date.today().isoformat()}.db"
            self.send_file(temporary_path, "application/vnd.sqlite3", filename)
        finally:
            if temporary_path:
                temporary_path.unlink(missing_ok=True)


class ExclusiveThreadingHTTPServer(ThreadingHTTPServer):
    """Prevent duplicate Zaiden servers from sharing one Windows port."""

    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            try:
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            except OSError:
                pass
        super().server_bind()


def compatible_server(port: int) -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=0.4) as response:
            payload = json.loads(response.read())
        return bool(
            payload.get("ok")
            and int(payload.get("api_version", 0)) >= API_VERSION
            and payload.get("analytics_ready")
        )
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Zaiden Trader SQLite application")
    parser.add_argument("--port", type=int, default=8899)
    parser.add_argument("--port-tries", type=int, default=15)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    migrate()
    if args.port_tries < 1:
        raise SystemExit("Nilai --port-tries minimal 1.")
    server = None
    active_port = None
    for offset in range(args.port_tries):
        candidate_port = args.port + offset
        if compatible_server(candidate_port):
            address = f"http://127.0.0.1:{candidate_port}"
            print(f"Zaiden Trader sudah aktif: {address}")
            if not args.no_browser:
                webbrowser.open(address)
            return
        try:
            server = ExclusiveThreadingHTTPServer(("127.0.0.1", candidate_port), AppHandler)
            active_port = candidate_port
            break
        except OSError:
            continue
    if server is None or active_port is None:
        raise SystemExit(
            f"Semua port dari {args.port} sampai {args.port + args.port_tries - 1} sedang dipakai."
        )
    address = f"http://127.0.0.1:{active_port}"
    print(f"Zaiden Trader aktif: {address}")
    print(f"Database SQLite: {DB_PATH}")
    if active_port != args.port:
        print(f"Port {args.port} sedang dipakai, menggunakan port {active_port}.")
    if not args.no_browser:
        threading.Timer(0.7, lambda: webbrowser.open(address)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nAplikasi dihentikan.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
