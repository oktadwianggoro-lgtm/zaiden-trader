"""
ml_weekly/pdf_report.py
Generates professional, client-shareable PDF reports for IDX Weekly
High-Confidence signals: a single-stock detail report, and a multi-page
daily digest (cover page + summary table + one detailed section per stock
+ glossary/disclaimer page) meant to be distributed to end customers.

Design intent: white/print-friendly background (not the app's dark theme —
this gets screenshotted/forwarded on WhatsApp and printed), plain-language
Indonesian explanations next to every technical term, and an honest
accuracy disclosure on every page that carries a probability number.
"""
from __future__ import annotations

import io
import json
import logging
import math
import re
import sqlite3
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional
from xml.sax.saxutils import escape as _esc

import pandas as pd

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.platypus import (
    BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer, Table,
    TableStyle, NextPageTemplate, PageBreak, KeepTogether, HRFlowable,
)
# Charts are drawn with reportlab.graphics (pure vector shapes embedded
# directly as PDF flowables) instead of matplotlib/mplfinance. Deliberate:
# matplotlib's compiled rendering backend (_backend_agg.pyd, and everything
# that transitively needs it, including the SVG backend) gets blocked outright
# on machines with Windows Smart App Control enabled ("Code Integrity...
# did not meet the Enterprise signing level requirements") — confirmed via
# Get-WinEvent on this app's own deployment machine, 2026-08-04. Every chart
# was silently failing and falling back to a generic "not enough historical
# data" message that had nothing to do with the real cause. reportlab's
# Drawing/Line/Rect/String primitives need no native rendering step at all
# (reportlab itself has been generating these PDFs' text/tables all along,
# proving it isn't affected), so this sidesteps the OS policy entirely
# rather than depending on it being relaxed.
from reportlab.graphics.shapes import Drawing, Line, Rect, String, Polygon

log = logging.getLogger(__name__)

# ── Brand palette (print-friendly: white background, strong accents) ──────────
NAVY = colors.HexColor("#0f172a")
BLUE = colors.HexColor("#2563eb")
GREEN = colors.HexColor("#059669")
RED = colors.HexColor("#dc2626")
AMBER = colors.HexColor("#d97706")
GRAY = colors.HexColor("#64748b")
LIGHT_BG = colors.HexColor("#f1f5f9")
LIGHT_GREEN_BG = colors.HexColor("#ecfdf5")
LIGHT_RED_BG = colors.HexColor("#fef2f2")
BORDER = colors.HexColor("#cbd5e1")

TIER_INFO = {
    "SANGAT_KUAT": ("Sangat Kuat", GREEN),
    "KUAT": ("Kuat", colors.HexColor("#65a30d")),
    "CUKUP": ("Cukup", AMBER),
    "LEMAH": ("Lemah", GRAY),
}

DECISION_INFO = {
    "QUALIFIED":          ("QUALIFIED", GREEN, LIGHT_GREEN_BG),
    "WATCHLIST":          ("WATCHLIST", AMBER, colors.HexColor("#fffbeb")),
    "NO_TRADE":           ("NO TRADE", RED, LIGHT_RED_BG),
    "ABSTAIN":            ("ABSTAIN", GRAY, LIGHT_BG),
    "DATA_INVALID":       ("DATA INVALID", RED, LIGHT_RED_BG),
    "MODEL_UNAVAILABLE":  ("MODEL UNAVAILABLE", RED, LIGHT_RED_BG),
    "SIGNAL_EXPIRED":     ("EXPIRED", GRAY, LIGHT_BG),
}


def _confidence_tier(prob: float) -> tuple[str, colors.Color]:
    """Fallback for rows generated before decision_status/confidence_tier
    were stored server-side (predict.compute_decision_status). Prefer
    reading data['confidence_tier'] directly — it's validation-aware; this
    raw-probability version is NOT and should not be used for new data."""
    if prob >= 0.65:
        return TIER_INFO["SANGAT_KUAT"]
    if prob >= 0.55:
        return TIER_INFO["KUAT"]
    if prob >= 0.45:
        return TIER_INFO["CUKUP"]
    return TIER_INFO["LEMAH"]


def _resolve_tier(data: dict) -> tuple[str, colors.Color]:
    tier_key = data.get("confidence_tier")
    if tier_key and tier_key in TIER_INFO:
        return TIER_INFO[tier_key]
    return _confidence_tier(float(data.get("calibrated_probability") or 0))


def _resolve_decision(data: dict) -> tuple[str, colors.Color, colors.Color]:
    status = data.get("decision_status")
    if status and status in DECISION_INFO:
        return DECISION_INFO[status]
    return ("BELUM DIEVALUASI", GRAY, LIGHT_BG)


MODEL_LABELS = {
    "hist_gradient_boosting": "HistGradientBoosting",
    "random_forest": "Random Forest",
    "logistic": "Logistic Regression",
    "gradient_boosting": "Gradient Boosting",
    "extra_trees": "Extra Trees",
    "xgboost": "XGBoost",
    "lightgbm": "LightGBM",
}

GLOSSARY = [
    ("Probabilitas Model", "Estimasi peluang harga naik ≥ target dalam periode horizon, dihasilkan oleh kombinasi beberapa model machine learning (ensemble). Bukan persentase kepastian — 65% berarti model melihat kondisi yang secara historis lebih sering diikuti kenaikan, bukan jaminan."),
    ("Tingkat Keyakinan", "Label kualitatif (Sangat Kuat/Kuat/Cukup/Lemah) berdasarkan seberapa tinggi probabilitas dibanding saham lain hari itu — alat bantu ranking, bukan ambang kelulusan tetap."),
    ("Entry / Harga Masuk", "Anggapan harga transaksi = harga penutupan tanggal sinyal (bukan tebakan harga pembukaan hari berikutnya, yang belum diketahui saat laporan dibuat) — ini konvensi yang sama persis dengan yang dipakai saat melatih model, lihat juga \"Zona Entry Bersyarat\"."),
    ("Take Profit (TP)", "Target harga jual jika prediksi benar. Dihitung dari target return yang dipakai saat melatih model (default +2% hingga +3%)."),
    ("Stop Loss (SL)", "Batas harga jual untuk membatasi kerugian jika prediksi salah (default -2%). Disiplin memakai stop loss adalah bagian penting manajemen risiko."),
    ("Risk : Reward (R:R)", "Perbandingan potensi untung terhadap potensi rugi. R:R 1.5 berarti potensi untung 1.5x lebih besar dari potensi rugi jika stop loss kena."),
    ("RSI14 (Relative Strength Index)", "Indikator momentum 0-100 dari 14 hari terakhir. Di bawah 30 biasanya dibaca \"oversold\" (berpotensi rebound), di atas 70 \"overbought\" (rawan koreksi)."),
    ("Market Regime", "Klasifikasi arah pasar (IHSG) saat ini: BULL_TREND/BULL_RANGE (kondusif), BEAR_TREND/BEAR_RANGE (tidak kondusif), NEUTRAL, atau HIGH_VOL (volatilitas tinggi)."),
    ("Foreign Flow", "Aliran dana investor asing (net beli/jual) sebagai persentase dari nilai transaksi — indikasi minat pemodal asing pada saham tersebut."),
    ("PER (Price to Earnings Ratio)", "Kelipatan harga saham terhadap laba per saham. Semakin rendah (relatif ke sektor), semakin \"murah\" secara valuasi laba — tapi perlu dibandingkan dengan sektor sejenis."),
    ("PBV (Price to Book Value)", "Kelipatan harga saham terhadap nilai buku per saham. PBV < 1 berarti harga di bawah nilai aset bersih perusahaan."),
    ("ROE (Return on Equity)", "Persentase laba bersih terhadap ekuitas — mengukur seberapa efisien perusahaan menghasilkan laba dari modal pemegang saham."),
    ("Akurasi Tervalidasi (Holdout)", "Presisi model yang diuji pada data yang TIDAK pernah dipakai untuk melatih atau memilih ambang batas — angka paling jujur untuk menggambarkan performa di dunia nyata, berbeda dari angka validasi yang biasanya lebih optimis."),
    ("Status Keputusan (QUALIFIED/WATCHLIST/dst.)", "QUALIFIED = lolos semua syarat (validasi holdout, cakupan model, risk-reward bersih). WATCHLIST = menarik secara statistik tapi belum lolos syarat penuh. NO_TRADE = expected value atau risk-reward bersih tidak memenuhi standar, atau ada risiko material (mis. corporate action). ABSTAIN = model tidak melihat keunggulan (probabilitas < 50%). MODEL_UNAVAILABLE = tidak ada model yang berhasil menilai saham ini."),
    ("Zona Entry Bersyarat", "Rentang harga di mana sinyal masih dianggap valid untuk dieksekusi. Jika harga pembukaan sesi berikutnya gap ke luar zona ini, sinyal TIDAK BOLEH dikejar — anggap tidak tersentuh (NOT_TRIGGERED), bukan kegagalan trading."),
    ("Net Risk-Reward", "Perbandingan potensi untung terhadap potensi rugi SETELAH biaya transaksi (beli, jual, slippage) — bukan angka kotor. Karena biaya round-trip dibayar baik saat untung maupun rugi, TP/SL yang simetris secara kotor (mis. +2%/-2%) selalu menghasilkan net risk-reward SEDIKIT DI BAWAH 1:1, bukan tepat 1:1."),
    ("Model Coverage", "Jumlah model dalam ensemble yang benar-benar berhasil memberi probabilitas untuk saham ini, dari total model yang dimuat. Cakupan rendah (mis. 1 dari 6) membuat keyakinan diturunkan otomatis, terlepas dari seberapa tinggi angka probabilitasnya."),
]


def _fetch_conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _add_trading_days(start_date: str, n: int) -> str:
    d = datetime.strptime(start_date, "%Y-%m-%d").date()
    added = 0
    while added < n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            added += 1
    return d.isoformat()


def _fetch_signal_row(conn: sqlite3.Connection, ticker: str, pred_date: Optional[str],
                       model_run_id: Optional[int] = None) -> Optional[dict]:
    """
    Fetch one prediction row for (ticker, pred_date). When model_run_id is
    given, it's pinned exactly — this matters because ml_weekly_predictions
    can hold rows from more than one model_run_id for the same
    (ticker, date) (e.g. right after a retrain, before old rows age out).
    Without pinning, a digest built by ranking tickers under ONE
    model_run_id could then silently display each ticker's highest
    probability across ALL model_run_ids for the detail section — a
    different number than the one it was ranked by. If model_run_id isn't
    given (single-stock lookup with no ranking context), we fall back to
    the globally highest-probability row, same as before.
    """
    if not pred_date:
        r = conn.execute(
            "SELECT MAX(prediction_date) FROM ml_weekly_predictions WHERE ticker=?", (ticker,)
        ).fetchone()
        pred_date = r[0] if r and r[0] else None
    if not pred_date:
        return None

    run_filter = "AND p.model_run_id=?" if model_run_id is not None else ""
    params = (ticker, pred_date) + ((model_run_id,) if model_run_id is not None else ())

    row = conn.execute(
        f"""SELECT p.*, s.company_name, s.listing_board,
                  f.per, f.pbv, f.roe, f.roa, f.der, f.npm, f.sektor AS fund_sektor,
                  f.market_cap, f.change_52w
           FROM ml_weekly_predictions p
           LEFT JOIN idx_stocks s ON s.code = p.ticker
           LEFT JOIN (
               SELECT f1.* FROM idx_fundamental_snapshots f1
               INNER JOIN (
                   SELECT stock_code, MAX(source_as_of_date) AS max_date
                   FROM idx_fundamental_snapshots GROUP BY stock_code
               ) f2 ON f1.stock_code = f2.stock_code AND f1.source_as_of_date = f2.max_date
           ) f ON f.stock_code = p.ticker
           WHERE p.ticker=? AND p.prediction_date=? {run_filter}
           ORDER BY p.calibrated_probability DESC LIMIT 1""",
        params,
    ).fetchone()
    if not row:
        return None

    d = dict(row)
    for jf in ("reason_codes_json", "risk_flags_json", "feature_values_json",
               "ensemble_probs_json", "model_coverage_json"):
        try:
            d[jf.replace("_json", "")] = json.loads(d.get(jf) or "null") or {}
        except Exception:
            d[jf.replace("_json", "")] = {} if jf != "reason_codes_json" and jf != "risk_flags_json" else []
    if not isinstance(d.get("reason_codes"), list):
        d["reason_codes"] = []
    if not isinstance(d.get("risk_flags"), list):
        d["risk_flags"] = []
    return d


def _fetch_price_history(conn: sqlite3.Connection, ticker: str, end_date: str, days: int = 140) -> pd.DataFrame:
    rows = conn.execute(
        """SELECT tanggal AS date,
                  COALESCE(harga_pembukaan, sebelumnya, harga_penutupan) AS open,
                  harga_tertinggi AS high, harga_terendah AS low,
                  harga_penutupan AS close, volume
           FROM ringkasan_saham_harian
           WHERE kode_saham = ? AND tanggal <= ? AND harga_penutupan IS NOT NULL AND harga_penutupan > 0
           ORDER BY tanggal DESC LIMIT ?""",
        (ticker, end_date, days),
    ).fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(r) for r in rows]).iloc[::-1].reset_index(drop=True)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")
    df["open"] = df["open"].fillna(df["close"])
    df["high"] = df["high"].fillna(df[["open", "close"]].max(axis=1))
    df["low"] = df["low"].fillna(df[["open", "close"]].min(axis=1))
    df["volume"] = df["volume"].fillna(0)
    df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"})
    return df[["Open", "High", "Low", "Close", "Volume"]]


def _fmt_price_short(v: float) -> str:
    return f"{v:,.0f}".replace(",", ".")


def _make_price_chart_drawing(df: pd.DataFrame, entry: Optional[float], target: Optional[float],
                               stop: Optional[float], ticker: str,
                               target_date: Optional[str] = None,
                               width_mm: float = 170, height_mm: float = 78) -> Optional[Drawing]:
    """
    Pure-vector daily-close price chart embedded directly as a PDF flowable
    — see the import-block comment above for why this doesn't use
    matplotlib. Shows the closing price line (what the user actually asked
    for — "tinggal dilihat aja harga close tiap harinya"), MA5/MA20,
    horizontal entry/target/stop reference lines, and a dashed projection
    arrow from the last close to the target price so the predicted
    direction is obvious without the reader connecting the dots themselves.
    """
    if df.empty or len(df) < 5:
        return None

    closes = df["Close"].astype(float).tolist()
    dates = [d.strftime("%d-%b") for d in df.index]
    ma5 = df["Close"].rolling(5, min_periods=1).mean().tolist()
    ma20 = df["Close"].rolling(20, min_periods=1).mean().tolist()
    n = len(closes)

    W = width_mm * mm
    H = height_mm * mm
    pad_left, pad_right, pad_top, pad_bottom = 42, 78, 18, 20
    plot_w = W - pad_left - pad_right
    plot_h = H - pad_top - pad_bottom
    proj_slots = max(4, n // 10)  # reserved x-space on the right for the projection arrow

    all_vals = [v for v in (closes + ma5 + ma20 + [entry, target, stop]) if v]
    vmin, vmax = min(all_vals), max(all_vals)
    vrange = (vmax - vmin) or (vmax * 0.05 or 1.0)
    vmin -= vrange * 0.10
    vmax += vrange * 0.10
    vrange = vmax - vmin

    def xf(i: float) -> float:
        return pad_left + (i / (n - 1 + proj_slots)) * plot_w

    def yf(v: float) -> float:
        return pad_bottom + ((v - vmin) / vrange) * plot_h

    d = Drawing(W, H)
    d.add(String(pad_left, H - 11, f"{ticker} — {n} hari terakhir (~3 bulan)",
                 fontSize=9, fontName="Helvetica-Bold", fillColor=NAVY))

    # Horizontal gridlines + y-axis price labels
    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        yv = vmin + frac * vrange
        y = yf(yv)
        d.add(Line(pad_left, y, W - pad_right, y, strokeColor=colors.HexColor("#e2e8f0"), strokeWidth=0.5))
        d.add(String(pad_left - 4, y - 2.5, _fmt_price_short(yv), fontSize=6.3,
                     fillColor=GRAY, textAnchor="end"))

    # A handful of x-axis date labels, evenly spaced (not every single day)
    step = max(1, n // 6)
    for i in range(0, n, step):
        d.add(String(xf(i), pad_bottom - 11, dates[i], fontSize=6, fillColor=GRAY, textAnchor="middle"))

    def _poly_line(values, color, width, dash=None):
        pts = [(xf(i), yf(v)) for i, v in enumerate(values) if v is not None]
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            ln = Line(x1, y1, x2, y2, strokeColor=color, strokeWidth=width)
            if dash:
                ln.strokeDashArray = dash
            d.add(ln)
        return pts

    _poly_line(ma20, colors.HexColor("#a78bfa"), 0.9, dash=[2, 2])
    _poly_line(ma5, colors.HexColor("#fbbf24"), 0.9, dash=[3, 2])
    close_pts = _poly_line(closes, NAVY, 1.5)

    # Horizontal entry/target/stop reference lines
    for val, col in ((entry, BLUE), (target, GREEN), (stop, RED)):
        if val:
            y = yf(val)
            ln = Line(pad_left, y, W - pad_right, y, strokeColor=col, strokeWidth=0.9)
            ln.strokeDashArray = [4, 2]
            d.add(ln)

    # Dashed projection arrow: last close -> target price, a few slots to
    # the right — the visual answer to "target prediksi harganya mau kemana".
    if entry and target and close_pts:
        last_x, last_y = close_pts[-1]
        proj_x = xf(n - 1 + proj_slots * 0.85)
        proj_y = yf(target)
        up = target >= entry
        arrow_color = GREEN if up else RED
        arrow_line = Line(last_x, last_y, proj_x, proj_y, strokeColor=arrow_color, strokeWidth=1.7)
        arrow_line.strokeDashArray = [5, 3]
        d.add(arrow_line)
        # small arrowhead
        ang = 0.5
        theta = math.atan2(proj_y - last_y, proj_x - last_x)
        for sign in (1, -1):
            hx = proj_x - 7 * math.cos(theta - sign * ang)
            hy = proj_y - 7 * math.sin(theta - sign * ang)
            d.add(Line(proj_x, proj_y, hx, hy, strokeColor=arrow_color, strokeWidth=1.7))
        d.add(String(min(proj_x + 4, W - 2), proj_y + 5, f"Target {target_date or ''}".strip(),
                     fontSize=7, fillColor=arrow_color, fontName="Helvetica-Bold", textAnchor="start" if proj_x < W - 60 else "end"))
        d.add(String(min(proj_x + 4, W - 2), proj_y - 5, f"Rp {_fmt_price_short(target)}",
                     fontSize=7, fillColor=arrow_color, fontName="Helvetica-Bold", textAnchor="start" if proj_x < W - 60 else "end"))

    # Legend
    legend_items = [("Close", NAVY, None), ("MA5", colors.HexColor("#fbbf24"), [3, 2]),
                     ("MA20", colors.HexColor("#a78bfa"), [2, 2])]
    lx = pad_left
    for label, col, dash in legend_items:
        ln = Line(lx, H - 3, lx + 12, H - 3, strokeColor=col, strokeWidth=1.6)
        if dash:
            ln.strokeDashArray = dash
        d.add(ln)
        d.add(String(lx + 15, H - 6, label, fontSize=6.3, fillColor=GRAY))
        lx += 15 + 6.5 * len(label) + 8

    return d


def _styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("BrandTitle", parent=ss["Title"], fontSize=20, textColor=colors.white, leading=24))
    ss.add(ParagraphStyle("BrandSub", parent=ss["Normal"], fontSize=10.5, textColor=colors.HexColor("#cbd5e1")))
    ss.add(ParagraphStyle("StockTicker", parent=ss["Heading1"], fontSize=18, textColor=NAVY, spaceAfter=2))
    ss.add(ParagraphStyle("StockCompany", parent=ss["Normal"], fontSize=10.5, textColor=GRAY, spaceAfter=6))
    ss.add(ParagraphStyle("SectionHeading", parent=ss["Heading3"], fontSize=11.5, textColor=NAVY, spaceBefore=10, spaceAfter=4))
    ss.add(ParagraphStyle("SigBullet", parent=ss["Normal"], fontSize=9.3, leftIndent=8, spaceAfter=2, leading=13))
    ss.add(ParagraphStyle("SmallMuted", parent=ss["Normal"], fontSize=8.7, textColor=GRAY, leading=12))
    ss.add(ParagraphStyle("CellLabel", parent=ss["Normal"], fontSize=8, textColor=GRAY))
    ss.add(ParagraphStyle("CellValue", parent=ss["Normal"], fontSize=10.5, textColor=NAVY))
    ss.add(ParagraphStyle("Disclaimer", parent=ss["Normal"], fontSize=8, textColor=GRAY, leading=11.5))
    ss.add(ParagraphStyle("GlossaryTerm", parent=ss["Normal"], fontSize=9.3, textColor=NAVY, spaceBefore=6))
    ss.add(ParagraphStyle("GlossaryDef", parent=ss["Normal"], fontSize=8.7, textColor=colors.HexColor("#334155"), leading=12))
    ss.add(ParagraphStyle("BigWarnHead", parent=ss["Heading1"], fontSize=14, textColor=colors.white, leading=17, spaceAfter=3))
    ss.add(ParagraphStyle("BigWarnBody", parent=ss["Normal"], fontSize=10, textColor=colors.white, leading=14))
    ss.add(ParagraphStyle("PlainExplain", parent=ss["Normal"], fontSize=9.3, textColor=colors.HexColor("#1e293b"), leftIndent=8, spaceAfter=1, leading=13))
    ss.add(ParagraphStyle("PlainTechCaption", parent=ss["Normal"], fontSize=7.5, textColor=GRAY, leftIndent=8, spaceAfter=7, leading=10))
    return ss


def _big_stoploss_warning(styles) -> list:
    """
    A large, impossible-to-miss warning block for the very top of every
    report we hand to a customer: this system only scores a fixed 5-trading-
    day horizon, and every trade plan assumes the reader sets a hard stop
    loss — the target/stop prices shown are a plan, not a promise, and
    without an actual stop order in place a "planned" -X% can become much
    worse before anyone reacts.
    """
    box = Table(
        [[Paragraph(
            "⚠ PERINGATAN PENTING — BACA SEBELUM MEMAKAI LAPORAN INI",
            styles["BigWarnHead"],
        )], [Paragraph(
            "Laporan ini <b>HANYA</b> untuk trader dengan horizon <b>5 hari bursa (mingguan)</b> — "
            "bukan untuk investasi jangka panjang, dan bukan untuk trading harian intraday. "
            "Setiap rencana trading di laporan ini WAJIB disertai <b>Stop Loss otomatis</b> "
            "(order stop-loss yang benar-benar dipasang di aplikasi sekuritas Anda, bukan sekadar "
            "diingat-ingat) — <b>minimal 3% dari harga entry</b>, atau memakai level Stop Loss yang "
            "tertulis di halaman rencana trading masing-masing saham bila levelnya lebih ketat dari 3%. "
            "Tanpa stop loss otomatis, kerugian yang direncanakan hanya di atas kertas — di pasar nyata, "
            "harga bisa turun jauh melewati rencana sebelum Anda sempat bereaksi secara manual. "
            "Probabilitas yang ditampilkan adalah estimasi statistik, BUKAN jaminan — sebagian sinyal "
            "akan meleset walau lolos semua penyaringan, dan itu wajar, bukan tanda sistem rusak.",
            styles["BigWarnBody"],
        )]],
        colWidths=[170 * mm],
    )
    box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), RED),
        ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (0, 0), 10), ("BOTTOMPADDING", (0, 0), (0, 0), 2),
        ("TOPPADDING", (0, 1), (0, 1), 2), ("BOTTOMPADDING", (0, 1), (0, 1), 10),
    ]))
    return [box, Spacer(1, 8)]


def _fmt_rp(v) -> str:
    if v is None:
        return "—"
    try:
        return f"Rp {round(float(v)):,}".replace(",", ".")
    except Exception:
        return "—"


def _fmt_pct(v, decimals=1) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v)*100:.{decimals}f}%"
    except Exception:
        return "—"


def _fmt_num(v, decimals=1) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.{decimals}f}"
    except Exception:
        return "—"


def _cell(label, value, styles):
    return [Paragraph(_esc(label), styles["CellLabel"]), Paragraph(_esc(str(value)), styles["CellValue"])]


def _info_grid(pairs: list[tuple[str, str]], styles, ncols=4):
    cells = [_cell(lbl, val, styles) for lbl, val in pairs]
    rows = []
    for i in range(0, len(cells), ncols):
        chunk = cells[i:i + ncols]
        while len(chunk) < ncols:
            chunk.append([Paragraph("", styles["CellLabel"]), Paragraph("", styles["CellValue"])])
        row = []
        for lab, val in chunk:
            row.append([lab, val])
        # flatten each cell into a mini 2-row layout by nesting a Table per cell
        table_row = []
        for lab, val in row:
            inner = Table([[lab], [val]], colWidths=[None])
            inner.setStyle(TableStyle([
                ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (0, 0), 1),
            ]))
            table_row.append(inner)
        rows.append(table_row)
    col_w = (170 * mm) / ncols
    t = Table(rows, colWidths=[col_w] * ncols)
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
    ]))
    return t


_REGIME_LABEL_ID = {
    "BULL_TREND": "sedang tren naik yang kuat",
    "BULL_RANGE": "cenderung positif",
    "BEAR_TREND": "sedang tren turun yang kuat",
    "BEAR_RANGE": "cenderung melemah",
    "HIGH_VOL": "sangat bergejolak/tidak stabil",
    "NEUTRAL": "netral, tidak jelas arahnya",
}


def _regime_id(code: str) -> str:
    return _REGIME_LABEL_ID.get(code, code)


# Plain-language ("bahasa awam") translations for every reason_codes /
# risk_flags string predict.generate_reason_codes / generate_risk_flags can
# produce (see predict.py) — matched by regex against the exact formats
# there, with the embedded number pulled back into the sentence so the
# explanation stays concrete instead of generic. This exists so a PDF reader
# with zero trading background can judge for themselves whether to trust a
# signal, instead of just seeing jargon like "RSI14 oversold (29)" and
# having to take the model's word for it.
_REASON_EXPLAINERS: list[tuple[re.Pattern, "callable"]] = [
    (re.compile(r"^Strong 5-day momentum \(\+?([\-\d.]+)%\)$"), lambda m:
        f"Dalam 5 hari terakhir, harga saham ini sudah naik +{m.group(1)}%. Ibaratnya seperti mobil yang sudah "
        f"mulai jalan kencang — kalau sedang kencang, biasanya masih terus melaju sebentar sebelum melambat."),
    (re.compile(r"^Strong 20-day trend \(\+?([\-\d.]+)%\)$"), lambda m:
        f"Dalam sebulan terakhir (20 hari bursa), harga saham ini sudah naik +{m.group(1)}%. Ini bukan cuma "
        f"lompatan sesaat, tapi memang tren naik yang cukup panjang — seperti tangga yang terus naik, bukan "
        f"cuma satu kali lompat lalu berhenti."),
    (re.compile(r"^RSI14 oversold \(([\-\d.]+)\)$"), lambda m:
        f"Ada alat ukur bernama RSI yang sekarang menunjukkan angka {m.group(1)} (skalanya 0–100). Di bawah 40 "
        f"artinya harga saham ini sudah 'terlalu murah dibanding beberapa hari terakhir' — mirip barang diskon "
        f"besar yang biasanya mulai menarik pembeli untuk masuk lagi."),
    (re.compile(r"^RSI14 balanced \(([\-\d.]+)\)$"), lambda m:
        f"RSI saham ini ada di angka {m.group(1)} — tidak terlalu mahal, tidak terlalu murah, pas di tengah. "
        f"Kondisi seperti ini biasanya lebih aman karena tidak dalam kondisi ekstrem yang gampang berbalik arah."),
    (re.compile(r"^Price above all key moving averages$"), lambda m:
        "Harga saham ini sekarang berada DI ATAS rata-rata harga 5, 20, 50, DAN 200 hari terakhir sekaligus. "
        "Anggap rata-rata itu 'jalur normal' harga — kalau harga di atas jalur normal di semua rentang waktu "
        "sekaligus, tandanya tren naiknya kuat dan konsisten, bukan cuma kebetulan sesaat."),
    (re.compile(r"^Near 20-day high \(breakout zone\)$"), lambda m:
        "Harga saham ini sudah mendekati harga TERTINGGI dalam 20 hari terakhir. Seperti pelari yang hampir "
        "menyentuh rekornya sendiri — kalau berhasil tembus, biasanya ada dorongan tambahan dari orang lain "
        "yang ikut membeli karena melihat harga 'pecah rekor'."),
    (re.compile(r"^Near 60-day high \(breakout zone\)$"), lambda m:
        "Harga saham ini sudah mendekati harga TERTINGGI dalam 60 hari (sekitar 3 bulan) terakhir. Seperti "
        "pelari yang hampir menyentuh rekornya sendiri — kalau berhasil tembus, biasanya ada dorongan tambahan "
        "dari orang lain yang ikut membeli karena melihat harga 'pecah rekor'."),
    (re.compile(r"^Volume expansion ([\-\d.]+)x vs 5-day avg$"), lambda m:
        f"Jumlah saham yang diperdagangkan hari-hari terakhir {m.group(1)} kali lebih banyak dari biasanya. "
        f"Artinya lebih banyak orang yang memperhatikan dan bertransaksi di saham ini — makin ramai, makin "
        f"besar kemungkinan pergerakan harganya memang nyata, bukan naik sepi tanpa ada yang berminat."),
    (re.compile(r"^Bollinger Band squeeze \(pre-breakout signal\)$"), lambda m:
        "Pergerakan harga saham ini belakangan sangat sempit dan tenang — seperti per (pegas) yang sedang "
        "ditekan. Biasanya setelah 'ditekan' cukup lama, harga akan bergerak besar ke salah satu arah, dan "
        "model mendeteksi tanda-tanda pergerakan besar itu akan segera terjadi."),
    (re.compile(r"^Price in upper Bollinger range \(([\-\d.]+)%\)$"), lambda m:
        f"Harga saat ini ada di posisi {m.group(1)}% dari 'jalur wajar' pergerakan harga (makin ke atas makin "
        f"dekat batas atas jalur itu). Artinya harga sedang kuat — tapi juga perlu diperhatikan supaya tidak "
        f"membeli di titik yang sudah terlalu tinggi."),
    (re.compile(r"^MACD histogram positive \(bullish momentum\)$"), lambda m:
        "Ada indikator lain bernama MACD yang mengukur 'kecepatan' tren harga, dan sekarang tandanya positif — "
        "artinya dorongan (momentum) harga saham ini sedang condong ke arah naik, bukan melambat."),
    (re.compile(r"^Supportive market regime \((\w+)\)$"), lambda m:
        f"Kondisi pasar saham secara keseluruhan (bukan cuma saham ini) {_regime_id(m.group(1))}. Ibaratnya "
        f"kalau semua kapal di laut sedang didorong ombak ke arah yang sama, saham secara individual juga "
        f"lebih mudah terbawa naik."),
    (re.compile(r"^Foreign net buying 5-day \(([\-\d.]+)% of turnover\)$"), lambda m:
        f"Investor asing (dari luar negeri) tercatat membeli lebih banyak dibanding menjual saham ini dalam "
        f"5 hari terakhir, sebesar {m.group(1)}% dari total nilai transaksi. Investor asing biasanya bermodal "
        f"besar dan melakukan riset mendalam, jadi ini sering dianggap sinyal positif oleh banyak trader."),
    (re.compile(r"^Strong trend efficiency \(([\-\d.]+)\)$"), lambda m:
        f"Ada angka ({m.group(1)}, skala 0–1) yang mengukur seberapa 'mulus' saham ini bergerak naik — makin "
        f"dekat ke 1, artinya harga naik dengan rapi dan konsisten, bukan naik-turun-naik-turun tanpa arah jelas."),
    (re.compile(r"^Closed near daily high \(strength\)$"), lambda m:
        "Di akhir hari perdagangan terakhir, harga saham ini ditutup dekat dengan harga TERTINGGI hari itu — "
        "bukan turun lagi menjelang penutupan. Pertanda pembeli masih kuat sampai akhir, seperti tim yang "
        "masih semangat sampai peluit akhir pertandingan."),
]

_RISK_EXPLAINERS: list[tuple[re.Pattern, "callable"]] = [
    (re.compile(r"^Low liquidity \(avg value ([\-\d.]+)M IDR\)$"), lambda m:
        f"Saham ini jarang diperdagangkan dalam jumlah besar (rata-rata sekitar Rp {m.group(1)} juta per hari). "
        f"Seperti toko yang sepi pembeli — kalau Anda mau beli/jual dalam jumlah besar, harga bisa bergerak "
        f"tidak wajar karena sedikit pihak yang siap melayani transaksi besar."),
    (re.compile(r"^High volatility \(annualized: ([\-\d.]+)%\)$"), lambda m:
        f"Saham ini punya riwayat naik-turun harga yang cukup liar (kira-kira {m.group(1)}% per tahun kalau "
        f"dirata-rata). Seperti naik roller coaster — bisa untung besar dengan cepat, tapi juga bisa rugi "
        f"besar dengan cepat."),
    (re.compile(r"^RSI14 overbought \(([\-\d.]+)\)$"), lambda m:
        f"RSI saham ini ada di angka {m.group(1)}, di atas 70 — artinya harga sudah naik cukup tinggi "
        f"belakangan ini, mirip barang yang naik harga terlalu cepat. Ada risiko harga 'istirahat' atau turun "
        f"sebentar setelah naik secepat ini."),
    (re.compile(r"^Bearish market regime \((\w+)\)$"), lambda m:
        f"Kondisi pasar saham secara keseluruhan {_regime_id(m.group(1))}. Kalau lautnya sedang surut, kapal "
        f"individual juga lebih susah melawan arus untuk naik."),
    (re.compile(r"^High volatility market regime$"), lambda m:
        "Pasar saham secara umum sedang bergejolak dan tidak stabil. Ini seperti cuaca buruk untuk semua "
        "kapal, bukan cuma saham ini — pergerakan harga jadi lebih sulit ditebak dari biasanya."),
    (re.compile(r"^Stock in 20-day downtrend \(([\-\d.]+)%\)$"), lambda m:
        f"Dalam sebulan terakhir, saham ini sebenarnya sedang turun sebesar {m.group(1)}%. Sinyal 'naik' yang "
        f"muncul sekarang bisa jadi cuma pantulan kecil di tengah tren turun yang lebih besar — perlu ekstra "
        f"hati-hati."),
    (re.compile(r"^Price near 120-day low$"), lambda m:
        "Harga saham ini sedang berada dekat titik TERENDAH dalam kurang lebih 6 bulan terakhir. Bisa jadi "
        "kesempatan beli murah, tapi bisa juga tanda ada masalah mendasar pada saham ini — perlu dicek lebih "
        "lanjut sebelum yakin."),
    (re.compile(r"^Recent inactive trading days$"), lambda m:
        "Belakangan ini ada beberapa hari di mana saham ini nyaris tidak ada transaksi sama sekali. Data yang "
        "jarang diperdagangkan seperti ini membuat perhitungan model kurang bisa diandalkan sepenuhnya."),
    (re.compile(r"^Corporate action warning \(extreme past returns\)$"), lambda m:
        "Sistem mendeteksi lonjakan harga ekstrem di masa lalu yang kemungkinan disebabkan aksi korporasi "
        "(seperti stock split, reverse split, atau rights issue), bukan pergerakan pasar biasa. Data "
        "historisnya perlu dibaca hati-hati karena bisa 'melompat' bukan karena alasan fundamental murni."),
]


def _explain_plain(text: str, table: list[tuple]) -> str:
    for pattern, fn in table:
        m = pattern.match(text)
        if m:
            try:
                return fn(m)
            except Exception:
                break
    # Fallback for any future reason/flag code this table hasn't caught up
    # with yet — never silently drop the information, just show it raw.
    return f"({_esc(text)})"


def _explain_reason_plain(reason: str) -> str:
    return _explain_plain(reason, _REASON_EXPLAINERS)


def _explain_risk_plain(flag: str) -> str:
    return _explain_plain(flag, _RISK_EXPLAINERS)


def _make_cover_overview_drawing(stocks: list[dict], conn: sqlite3.Connection, pred_date: str,
                                  months: int = 3, width_mm: float = 170, height_mm: float = 72) -> Optional[Drawing]:
    """
    One at-a-glance vector chart for the cover page: every reported stock's
    normalized ~3-month price trend, each ending in a dashed projection
    segment toward its target price so the reader sees the whole report's
    "shape" before reading individual pages. Pure reportlab shapes — see the
    import-block comment for why (no matplotlib). Purely a cover-page
    summary — every per-stock detail chart later in the report is untouched.
    """
    trading_days = max(months, 1) * 21  # ~21 trading days/month
    series_list = []
    for s in stocks:
        ticker = s["ticker"]
        rows = conn.execute(
            """SELECT tanggal AS date, harga_penutupan AS close
               FROM ringkasan_saham_harian
               WHERE kode_saham = ? AND tanggal <= ? AND harga_penutupan IS NOT NULL AND harga_penutupan > 0
               ORDER BY tanggal DESC LIMIT ?""",
            (ticker, pred_date, trading_days),
        ).fetchall()
        if len(rows) < 10:
            continue
        closes = [r["close"] for r in reversed(rows)]
        base = closes[0]
        if not base:
            continue
        pct = [(c / base - 1.0) * 100 for c in closes]

        target = s.get("target_price")
        target_pct = (target / base - 1.0) * 100 if target else None
        _, deco_color, _ = _resolve_decision(s)
        series_list.append({
            "ticker": ticker, "pct": pct, "target_pct": target_pct, "color": deco_color,
        })

    if not series_list:
        return None

    W = width_mm * mm
    H = height_mm * mm
    pad_left, pad_right, pad_top, pad_bottom = 40, 14, 14, 26
    plot_w = W - pad_left - pad_right
    plot_h = H - pad_top - pad_bottom
    n = max(len(sr["pct"]) for sr in series_list)
    proj_slots = 6

    all_pct = [v for sr in series_list for v in sr["pct"]] + \
              [sr["target_pct"] for sr in series_list if sr["target_pct"] is not None]
    vmin, vmax = min(all_pct + [0]), max(all_pct + [0])
    vrange = (vmax - vmin) or 1.0
    vmin -= vrange * 0.1
    vmax += vrange * 0.1
    vrange = vmax - vmin

    def xf(i: float) -> float:
        return pad_left + (i / (n - 1 + proj_slots)) * plot_w

    def yf(v: float) -> float:
        return pad_bottom + ((v - vmin) / vrange) * plot_h

    d = Drawing(W, H)
    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        vv = vmin + frac * vrange
        y = yf(vv)
        d.add(Line(pad_left, y, W - pad_right, y, strokeColor=colors.HexColor("#e2e8f0"), strokeWidth=0.5))
        d.add(String(pad_left - 4, y - 2.5, f"{vv:+.0f}%", fontSize=6.3, fillColor=GRAY, textAnchor="end"))
    zero_y = yf(0)
    d.add(Line(pad_left, zero_y, W - pad_right, zero_y, strokeColor=colors.HexColor("#94a3b8"), strokeWidth=0.7))

    d.add(String(pad_left, 8, f"~{months} bulan terakhir  →  garis putus-putus = proyeksi menuju target",
                 fontSize=7, fillColor=GRAY))

    lx = pad_left
    for sr in series_list:
        pts = [(xf(i), yf(v)) for i, v in enumerate(sr["pct"])]
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            d.add(Line(x1, y1, x2, y2, strokeColor=sr["color"], strokeWidth=1.4))
        if sr["target_pct"] is not None and pts:
            last_x, last_y = pts[-1]
            proj_x = xf(n - 1 + proj_slots * 0.7)
            proj_y = yf(sr["target_pct"])
            ln = Line(last_x, last_y, proj_x, proj_y, strokeColor=sr["color"], strokeWidth=1.2)
            ln.strokeDashArray = [4, 3]
            d.add(ln)
            d.add(Rect(proj_x - 1.5, proj_y - 1.5, 3, 3, fillColor=sr["color"], strokeColor=None))
        # compact legend chip
        if lx < W - pad_right - 30:
            d.add(Line(lx, H - 6, lx + 10, H - 6, strokeColor=sr["color"], strokeWidth=2))
            d.add(String(lx + 13, H - 8.5, sr["ticker"], fontSize=6.3, fillColor=NAVY))
            lx += 13 + 6.2 * len(sr["ticker"]) + 8

    return d


def _build_stock_section(data: dict, conn: sqlite3.Connection, styles, rank: Optional[int] = None) -> list:
    flow = []
    ticker = data["ticker"]
    prob = float(data.get("calibrated_probability") or 0)
    tier_label, tier_color = _resolve_tier(data)
    decision_label, decision_color, decision_bg = _resolve_decision(data)
    pred_date = data["prediction_date"]
    horizon = data.get("horizon_days") or 5
    target_date = _add_trading_days(pred_date, horizon)

    entry = data.get("next_open") or data.get("current_close")
    target = data.get("target_price")
    stop = data.get("stop_price")
    tp_pct = (target / entry - 1.0) if entry and target else None
    sl_pct = (stop / entry - 1.0) if entry and stop else None
    gross_rr = None
    if entry and target and stop and (entry - stop) != 0:
        gross_rr = (target - entry) / (entry - stop)
    net_rr = data.get("net_risk_reward")
    net_ev = data.get("net_expected_value")

    entry_zone_low = data.get("entry_zone_low")
    entry_zone_high = data.get("entry_zone_high")

    coverage = data.get("model_coverage") or {}
    n_expected = coverage.get("expected")
    n_contributed = coverage.get("contributed")
    coverage_txt = f"{n_contributed}/{n_expected} model" if n_expected else "—"

    # ── Header row: rank badge + ticker/company + probability badge ──────────
    rank_txt = f"#{rank} · " if rank else ""
    header_tbl = Table(
        [[
            Paragraph(f"{rank_txt}<b>{_esc(ticker)}</b>", styles["StockTicker"]),
            Paragraph(f"<b>{prob*100:.0f}%</b>", ParagraphStyle("ProbBig", parent=styles["StockTicker"], textColor=tier_color, alignment=TA_RIGHT)),
        ]],
        colWidths=[130 * mm, 40 * mm],
    )
    header_tbl.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    flow.append(header_tbl)

    company = data.get("company_name") or ticker
    sektor = data.get("fund_sektor") or data.get("sector_regime") or "—"
    sub_tbl = Table(
        [[
            Paragraph(f"{_esc(company)} &nbsp;·&nbsp; {_esc(data.get('listing_board') or '—')} &nbsp;·&nbsp; Sektor {_esc(str(sektor))}", styles["StockCompany"]),
            Paragraph(f'<font color="#{tier_color.hexval()[2:]}"><b>Keyakinan: {tier_label}</b></font>', ParagraphStyle("TierTag", parent=styles["StockCompany"], alignment=TA_RIGHT)),
        ]],
        colWidths=[130 * mm, 40 * mm],
    )
    flow.append(sub_tbl)

    # Decision status badge — the single most important line on the page.
    # A WATCHLIST or NO_TRADE stock must never visually read like a
    # confident buy call, so this gets its own colored bar, not a bullet.
    status_bar = Table(
        [[Paragraph(
            f'<b>STATUS: {decision_label}</b> &nbsp;·&nbsp; {_esc(data.get("decision_reason") or "")}',
            ParagraphStyle("StatusBar", parent=styles["Normal"], fontSize=9, textColor=decision_color, leading=12),
        )]],
        colWidths=[170 * mm],
    )
    status_bar.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), decision_bg),
        ("BOX", (0, 0), (-1, -1), 0.75, decision_color),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    flow.append(Spacer(1, 4))
    flow.append(status_bar)
    flow.append(HRFlowable(width="100%", thickness=1, color=BORDER, spaceBefore=6, spaceAfter=6))

    # ── Chart — ~3 bulan terakhir (63 hari bursa) + proyeksi arah target ───
    hist = _fetch_price_history(conn, ticker, pred_date, days=65)
    chart_drawing = _make_price_chart_drawing(hist, entry, target, stop, ticker, target_date=target_date)
    if chart_drawing:
        flow.append(chart_drawing)
        flow.append(Paragraph(
            "Garis putus-putus horizontal: <font color='#2563eb'><b>biru</b></font> = harga entry, "
            "<font color='#059669'><b>hijau</b></font> = target profit, "
            "<font color='#dc2626'><b>merah</b></font> = stop loss. Panah putus-putus di ujung kanan = "
            "proyeksi visual menuju target profit pada tanggal berlaku — arah &amp; besaran saja, BUKAN "
            "prediksi bentuk pergerakan harian. MA5/MA20 = rata-rata pergerakan 5 &amp; 20 hari.",
            styles["SmallMuted"],
        ))
    elif hist.empty or len(hist) < 5:
        flow.append(Paragraph(
            f"<i>Grafik tidak tersedia — data historis {ticker} kurang dari 5 hari bursa pada database saat ini.</i>",
            styles["SmallMuted"],
        ))
    else:
        flow.append(Paragraph("<i>Grafik tidak tersedia (kesalahan saat menggambar grafik).</i>", styles["SmallMuted"]))

    # ── Trade plan ───────────────────────────────────────────────────────
    flow.append(Paragraph("Rencana Trading", styles["SectionHeading"]))
    zone_txt = f"{_fmt_rp(entry_zone_low)} – {_fmt_rp(entry_zone_high)}" if entry_zone_low and entry_zone_high else "—"
    plan_pairs = [
        ("Tanggal Sinyal (data s.d.)", pred_date),
        ("Berlaku Hingga", f"{target_date} ({horizon} hari bursa)"),
        (f"Harga Penutupan {pred_date}", _fmt_rp(data.get("current_close"))),
        ("Zona Entry Bersyarat", zone_txt),
        ("Take Profit (TP)", f"{_fmt_rp(target)}  ({_fmt_pct(tp_pct)})"),
        ("Stop Loss (SL)", f"{_fmt_rp(stop)}  ({_fmt_pct(sl_pct)})"),
        ("Risk:Reward (kotor / bersih)", f"1:{gross_rr:.1f} / 1:{net_rr:.1f}" if gross_rr and gross_rr > 0 and net_rr else "—"),
        ("Expected Value (bersih)", _fmt_pct(net_ev) if net_ev is not None else "—"),
        ("Cakupan Model", coverage_txt),
        ("Regime Pasar", data.get("market_regime") or "—"),
    ]
    flow.append(_info_grid(plan_pairs, styles, ncols=4))
    flow.append(Paragraph(
        f"Sinyal dihitung dari harga <b>penutupan {pred_date}</b>. Entry HANYA berlaku bersyarat: jika harga pembukaan "
        f"hari bursa berikutnya berada <b>di luar zona entry</b> di atas (gap terlalu tinggi/rendah), "
        f"<b>jangan dikejar — anggap sinyal tidak tersentuh (NOT_TRIGGERED)</b>, bukan kegagalan trading. "
        f"Risk-reward &amp; expected value di atas sudah memperhitungkan estimasi biaya transaksi round-trip.",
        styles["SmallMuted"],
    ))

    # ── Why it might go up — plain-language explanation for every reason,
    # so a reader with zero trading background can judge the signal
    # themselves instead of just trusting a probability number blindly. ────
    flow.append(Paragraph("Mengapa Berpotensi Naik — Dijelaskan Sederhana", styles["SectionHeading"]))
    reasons = data.get("reason_codes") or []
    if reasons:
        flow.append(Paragraph(
            f"Model menghitung probabilitas <b>{prob*100:.0f}%</b> dengan menggabungkan puluhan indikator "
            f"matematis sekaligus — mirip dokter yang mendiagnosis dari banyak gejala bersamaan, bukan dari "
            f"satu tanda saja. Berikut {len(reasons)} alasan utama yang paling menonjol untuk saham ini, "
            f"masing-masing dijelaskan dalam bahasa sederhana:",
            styles["SmallMuted"],
        ))
        flow.append(Spacer(1, 3))
        for i, r in enumerate(reasons, start=1):
            flow.append(Paragraph(f"<b>{i}. {_explain_reason_plain(r)}</b>", styles["PlainExplain"]))
            flow.append(Paragraph(f"Istilah teknis: {_esc(r)}", styles["PlainTechCaption"]))
    else:
        flow.append(Paragraph("<i>Tidak ada faktor teknikal kuat yang menonjol — probabilitas berasal dari kombinasi banyak sinyal lemah, bukan dari satu alasan yang jelas dan kuat.</i>", styles["SmallMuted"]))

    # ── Risk flags — same plain-language treatment, so the case AGAINST
    # trusting the signal is just as easy to understand as the case for it. ─
    flags = data.get("risk_flags") or []
    flow.append(Paragraph("Faktor Risiko — Dijelaskan Sederhana", styles["SectionHeading"]))
    if flags:
        for i, fl in enumerate(flags, start=1):
            flow.append(Paragraph(
                f"<b>{i}. {_explain_risk_plain(fl)}</b>",
                ParagraphStyle("RiskPlainExplain", parent=styles["PlainExplain"], textColor=RED),
            ))
            flow.append(Paragraph(f"Istilah teknis: {_esc(fl)}", styles["PlainTechCaption"]))
    else:
        flow.append(Paragraph("<i>Tidak ada bendera risiko signifikan terdeteksi oleh sistem.</i>", styles["SmallMuted"]))

    # ── Technical detail table ───────────────────────────────────────────
    feat = data.get("feature_values") or {}
    flow.append(Paragraph("Detail Teknikal", styles["SectionHeading"]))
    tech_pairs = [
        ("RSI14", _fmt_num(feat.get("rsi14"), 0)),
        ("Return 5 Hari", _fmt_pct(feat.get("return_5d"))),
        ("Return 20 Hari", _fmt_pct(feat.get("return_20d"))),
        ("Return 60 Hari", _fmt_pct(feat.get("return_60d"))),
        ("Skor Volume", _fmt_num(data.get("volume_score"), 2) + "x"),
        ("Foreign Flow (skor)", _fmt_num((data.get("foreign_flow_score") or 0) * 100, 2) + "%"),
        ("Rank Sektor", _fmt_pct(feat.get("sector_rank")) if feat.get("sector_rank") is not None else "—"),
        ("Nilai Transaksi 20D", _fmt_rp(feat.get("avg_value_20d"))),
    ]
    flow.append(_info_grid(tech_pairs, styles, ncols=4))

    # ── Fundamental snapshot ─────────────────────────────────────────────
    if any(data.get(k) is not None for k in ("per", "pbv", "roe", "der")):
        flow.append(Paragraph("Ringkasan Fundamental (Zaiden Fundamental Review)", styles["SectionHeading"]))
        fund_pairs = [
            ("PER", _fmt_num(data.get("per"), 2) + "x" if data.get("per") is not None else "—"),
            ("PBV", _fmt_num(data.get("pbv"), 2) + "x" if data.get("pbv") is not None else "—"),
            ("ROE", _fmt_pct(data.get("roe") / 100 if data.get("roe") is not None else None) if data.get("roe") is not None else "—"),
            ("DER", _fmt_num(data.get("der"), 2) + "x" if data.get("der") is not None else "—"),
        ]
        flow.append(_info_grid(fund_pairs, styles, ncols=4))

    # ── Ensemble breakdown ───────────────────────────────────────────────
    ens = data.get("ensemble_probs") or {}
    if ens:
        flow.append(Paragraph("Kesepakatan Model (Ensemble)", styles["SectionHeading"]))
        n_agree = sum(1 for v in ens.values() if v >= 0.5)
        rows = [["Model", "Probabilitas"]]
        for name, p in sorted(ens.items(), key=lambda kv: -kv[1]):
            rows.append([MODEL_LABELS.get(name, name), f"{p*100:.1f}%"])
        et = Table(rows, colWidths=[100 * mm, 70 * mm])
        et.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), LIGHT_BG),
            ("TEXTCOLOR", (0, 0), (-1, 0), NAVY),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
        ]))
        flow.append(et)
        flow.append(Paragraph(f"{n_agree} dari {len(ens)} model memberi probabilitas ≥ 50% untuk saham ini.", styles["SmallMuted"]))

    return flow


def _header_footer(canvas, doc, brand_title: str, gen_ts: str):
    canvas.saveState()
    w, h = A4
    canvas.setFillColor(NAVY)
    canvas.rect(0, h - 16 * mm, w, 16 * mm, fill=1, stroke=0)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 11)
    canvas.drawString(15 * mm, h - 10.5 * mm, "ZAIDEN TRADER")
    canvas.setFont("Helvetica", 8.5)
    canvas.setFillColor(colors.HexColor("#cbd5e1"))
    canvas.drawRightString(w - 15 * mm, h - 10.5 * mm, brand_title)

    canvas.setFillColor(GRAY)
    canvas.setFont("Helvetica", 7.5)
    canvas.drawString(15 * mm, 10 * mm, f"Dibuat otomatis {gen_ts} · Bukan nasihat keuangan · Sinyal berbasis model statistik")
    canvas.drawRightString(w - 15 * mm, 10 * mm, f"Halaman {doc.page}")
    canvas.setStrokeColor(BORDER)
    canvas.line(15 * mm, 13 * mm, w - 15 * mm, 13 * mm)
    canvas.restoreState()


def _make_doc(buf, brand_title: str, gen_ts: str) -> BaseDocTemplate:
    doc = BaseDocTemplate(
        buf, pagesize=A4,
        topMargin=22 * mm, bottomMargin=18 * mm, leftMargin=15 * mm, rightMargin=15 * mm,
        title=brand_title,
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="normal")

    def _on_page(canvas, d):
        _header_footer(canvas, d, brand_title, gen_ts)

    doc.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=_on_page)])
    return doc


def _disclaimer_block(styles, holdout_precision: Optional[float], holdout_status: Optional[str]) -> list:
    flow = []
    flow.append(Paragraph("Transparansi Akurasi", ParagraphStyle("DiscHead", parent=styles["SectionHeading"], textColor=colors.white)))
    prec_txt = f"{holdout_precision*100:.0f}%" if holdout_precision is not None else "belum tersedia"
    flow.append(Paragraph(
        f"Akurasi tervalidasi model pada data <b>holdout</b> (data yang tidak pernah dipakai untuk melatih atau "
        f"memilih ambang batas) saat ini adalah <b>{prec_txt}</b> (status: {_esc(str(holdout_status or 'N/A'))}). "
        f"Ini adalah ukuran performa paling jujur — mohon jangan menganggap peluang naik yang ditampilkan di laporan "
        f"ini sebagai jaminan profit.",
        ParagraphStyle("DiscBody", parent=styles["Disclaimer"], textColor=colors.HexColor("#e2e8f0")),
    ))
    return flow


def generate_stock_pdf(db_path: str, ticker: str, pred_date: Optional[str] = None) -> Optional[bytes]:
    """Build a single-stock detail PDF. Returns None if no prediction found."""
    styles = _styles()
    conn = _fetch_conn(db_path)
    try:
        data = _fetch_signal_row(conn, ticker.upper(), pred_date)
        if not data:
            return None

        from .ensemble_eval import get_latest_ensemble_status
        ens_status = get_latest_ensemble_status(db_path)

        gen_ts = datetime.now().strftime("%d %b %Y %H:%M WIB")
        buf = io.BytesIO()
        doc = _make_doc(buf, f"Laporan Sinyal — {data['ticker']}", gen_ts)

        story = []
        story.extend(_big_stoploss_warning(styles))
        story.extend(_build_stock_section(data, conn, styles))
        story.append(Spacer(1, 10))
        story.append(HRFlowable(width="100%", thickness=1, color=BORDER, spaceAfter=6))
        prec = ens_status.get("holdout_precision") if ens_status else None
        hstat = ens_status.get("holdout_status") if ens_status else None
        prec_txt = f"{prec*100:.0f}%" if prec is not None else "belum tersedia"
        story.append(Paragraph(
            f"<b>Transparansi akurasi:</b> akurasi tervalidasi model pada data holdout saat ini {prec_txt} "
            f"(status: {_esc(str(hstat or 'N/A'))}). Gunakan laporan ini sebagai salah satu input analisis, "
            f"bukan satu-satunya dasar keputusan investasi. Selalu terapkan manajemen risiko (stop loss) Anda sendiri.",
            styles["Disclaimer"],
        ))

        doc.build(story)
        return buf.getvalue()
    finally:
        conn.close()


def generate_digest_pdf(db_path: str, pred_date: Optional[str] = None, status: str = "QUALIFIED", limit: int = 10) -> Optional[bytes]:
    """
    Build the multi-page daily digest PDF: cover + summary table + per-stock
    sections + glossary.

    status="QUALIFIED" (the default) shows ONLY signals that passed every
    gate in predict.compute_decision_status. If none did, this still
    returns a valid short PDF stating that plainly — it does NOT fall back
    to padding the report with weaker candidates just to reach `limit`.
    Pass status="ALL" for the older "top N by probability regardless of
    gate" ranked view (still useful as a watchlist, but must not be
    presented as if every row were a qualified call).

    Returns None only when there is no prediction data at all for the
    date — a genuine "nothing to report" case, distinct from "data exists
    but nothing qualified".
    """
    styles = _styles()
    conn = _fetch_conn(db_path)
    try:
        # Resolve which model_run_id to report from the predictions that
        # actually exist, not from ml_weekly_model_runs' "latest active row" —
        # those two can briefly disagree while a training pipeline is running
        # (a model gets superseded mid-training before its replacement's
        # predictions exist yet), which would otherwise silently return an
        # empty report.
        if pred_date:
            run = conn.execute(
                "SELECT model_run_id FROM ml_weekly_predictions WHERE prediction_date=? ORDER BY id DESC LIMIT 1",
                (pred_date,),
            ).fetchone()
        else:
            run = conn.execute(
                "SELECT model_run_id, prediction_date FROM ml_weekly_predictions ORDER BY prediction_date DESC, id DESC LIMIT 1"
            ).fetchone()
            if run:
                pred_date = run["prediction_date"]
        if not run or not pred_date:
            return None
        model_run_id = run["model_run_id"]

        # Honest counts across EVERYTHING analyzed that day, independent of
        # which subset is actually shown below — this is what lets the
        # cover page say "327 dianalisis, 0 QUALIFIED, 41 WATCHLIST..."
        # instead of just silently showing whatever happened to qualify.
        status_counts_rows = conn.execute(
            """SELECT COALESCE(decision_status, 'BELUM_DIEVALUASI') AS st, COUNT(*) c
               FROM ml_weekly_predictions WHERE model_run_id=? AND prediction_date=?
               GROUP BY 1""",
            (model_run_id, pred_date),
        ).fetchall()
        status_counts = {r["st"]: r["c"] for r in status_counts_rows}
        total_analyzed = sum(status_counts.values())
        if total_analyzed == 0:
            return None

        status_filter = ""
        if status == "QUALIFIED":
            status_filter = "AND decision_status = 'QUALIFIED'"
        elif status == "WATCHLIST":
            status_filter = "AND decision_status IN ('QUALIFIED', 'WATCHLIST')"
        elif status == "HIGH_CONFIDENCE":  # legacy alias, kept for old callers
            status_filter = "AND decision_status = 'QUALIFIED'"
        # status == "ALL" (or anything else): no filter — full ranked list.

        rows = conn.execute(
            f"""SELECT ticker FROM ml_weekly_predictions
                WHERE model_run_id=? AND prediction_date=? {status_filter}
                ORDER BY calibrated_probability DESC LIMIT ?""",
            (model_run_id, pred_date, limit),
        ).fetchall()
        tickers = [r["ticker"] for r in rows]

        stocks = []
        for t in tickers:
            d = _fetch_signal_row(conn, t, pred_date, model_run_id=model_run_id)
            if d:
                stocks.append(d)

        # The list is already DESC by calibrated_probability from the SQL
        # above; re-sort defensively so the displayed order can never drift
        # from what's actually shown once _fetch_signal_row's own numbers
        # are attached (they always agree now that model_run_id is pinned,
        # but this keeps the invariant explicit rather than assumed).
        stocks.sort(key=lambda s: s.get("calibrated_probability") or 0, reverse=True)

        from .ensemble_eval import get_latest_ensemble_status
        ens_status = get_latest_ensemble_status(db_path)
        prec = ens_status.get("holdout_precision") if ens_status else None
        hstat = ens_status.get("holdout_status") if ens_status else None

        gen_ts = datetime.now().strftime("%d %b %Y %H:%M WIB")
        horizon = (stocks[0].get("horizon_days") if stocks else 5) or 5
        target_date = _add_trading_days(pred_date, horizon)

        buf = io.BytesIO()
        doc = _make_doc(buf, "Laporan Sinyal Mingguan IDX", gen_ts)
        story = []

        # ── Cover ──────────────────────────────────────────────────────
        cover = Table(
            [[Paragraph("LAPORAN SINYAL MINGGUAN IDX", ParagraphStyle("CoverTitle", parent=styles["BrandTitle"], textColor=NAVY, fontSize=22))]],
            colWidths=[170 * mm],
        )
        story.append(cover)
        story.append(Spacer(1, 4))
        story.append(Paragraph(
            f"Tanggal sinyal: <b>{pred_date}</b> &nbsp;·&nbsp; Berlaku hingga: <b>{target_date}</b> "
            f"({horizon} hari bursa) &nbsp;·&nbsp; {total_analyzed} saham dianalisis",
            styles["StockCompany"],
        ))
        story.append(Spacer(1, 8))
        story.extend(_big_stoploss_warning(styles))

        # Accuracy banner box
        prec_txt = f"{prec*100:.0f}%" if prec is not None else "belum tersedia"
        banner = Table([[Paragraph(
            f"<b>Akurasi tervalidasi (holdout):</b> {prec_txt} (status: {_esc(str(hstat or 'N/A'))}). "
            f"Angka ini diukur dari data yang tidak pernah dipakai melatih model — anggap laporan ini sebagai "
            f"alat bantu ranking, bukan jaminan profit.",
            ParagraphStyle("BannerText", parent=styles["Normal"], fontSize=9, textColor=NAVY, leading=13),
        )]], colWidths=[170 * mm])
        banner.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), LIGHT_GREEN_BG if (prec or 0) >= 0.65 else LIGHT_BG),
            ("BOX", (0, 0), (-1, -1), 0.75, GREEN if (prec or 0) >= 0.65 else AMBER),
            ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(banner)
        story.append(Spacer(1, 8))

        # Honest breakdown of EVERYTHING analyzed, not just what's shown below.
        order = ["QUALIFIED", "WATCHLIST", "NO_TRADE", "ABSTAIN", "DATA_INVALID", "MODEL_UNAVAILABLE", "SIGNAL_EXPIRED", "BELUM_DIEVALUASI"]
        count_cells = []
        for st in order:
            c = status_counts.get(st, 0)
            if c == 0 and st not in ("QUALIFIED", "WATCHLIST"):
                continue
            label, color, _bg = DECISION_INFO.get(st, ("Belum Dievaluasi", GRAY, LIGHT_BG))
            count_cells.append(Paragraph(f'<font color="#{color.hexval()[2:]}"><b>{c}</b> {label}</font>',
                                          ParagraphStyle("CountCell", parent=styles["Normal"], fontSize=9)))
        if count_cells:
            ct = Table([count_cells], colWidths=[170 * mm / len(count_cells)] * len(count_cells))
            ct.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER")]))
            story.append(ct)
        story.append(Spacer(1, 10))

        if not stocks:
            from .predict import MIN_NET_RISK_REWARD
            story.append(Paragraph(
                "Tidak ada sinyal yang memenuhi standar risiko dan validasi minggu ini.",
                ParagraphStyle("EmptyMsg", parent=styles["StockTicker"], fontSize=13, textColor=RED),
            ))
            story.append(Spacer(1, 6))
            story.append(Paragraph(
                f"{total_analyzed} saham dianalisis pada {pred_date}, tidak satupun lolos seluruh syarat "
                f"(validasi holdout tervalidasi, cakupan model minimum, risk-reward bersih ≥ {MIN_NET_RISK_REWARD:.1f}, "
                f"expected value positif, bebas indikasi corporate action). Ini adalah hasil yang jujur, bukan "
                f"kegagalan sistem — pasar tidak selalu menyediakan setup yang memenuhi standar tinggi setiap minggu.",
                styles["Disclaimer"],
            ))
            if status_counts.get("WATCHLIST"):
                story.append(Spacer(1, 6))
                story.append(Paragraph(
                    f"Ada {status_counts['WATCHLIST']} saham berstatus WATCHLIST (menarik secara statistik, belum lolos "
                    f"syarat penuh) — hubungi penyedia laporan untuk daftar watchlist bila diperlukan.",
                    styles["SmallMuted"],
                ))
            doc.build(story)
            return buf.getvalue()
        story.append(Spacer(1, 12))

        overview_drawing = _make_cover_overview_drawing(stocks, conn, pred_date)
        if overview_drawing:
            story.append(Paragraph("Ringkasan Pergerakan 3 Bulan &amp; Proyeksi", styles["SectionHeading"]))
            story.append(overview_drawing)
            story.append(Paragraph(
                "Setiap garis dinormalisasi ke persentase perubahan dari titik awal ~3 bulan lalu, supaya saham "
                "dengan harga berbeda-beda bisa dibandingkan dalam satu grafik. Garis putus-putus di ujung kanan "
                "adalah proyeksi menuju target profit — BUKAN prediksi pergerakan harian, hanya visualisasi arah "
                "dan besaran target relatif terhadap tren 3 bulan terakhir.",
                styles["SmallMuted"],
            ))
            story.append(Spacer(1, 10))

        # KeepTogether: this table used to be able to split across a page
        # boundary if the cover-page content above it (warning box, accuracy
        # banner, overview chart) pushed it close to the bottom margin — the
        # header row would repeat (repeatRows=1) but the table visually tore
        # in half. Forcing the heading+table to move to the next page as one
        # atomic block instead, per user request ("jangan di pisah").
        sum_rows = [["#", "Kode", "Perusahaan", "Prob.", "Keyakinan", "Status", "Net R:R", "Sektor"]]
        for i, s in enumerate(stocks, start=1):
            prob = float(s.get("calibrated_probability") or 0)
            tier_label, _c = _resolve_tier(s)
            decision_label, _dc, _dbg = _resolve_decision(s)
            net_rr = s.get("net_risk_reward")
            sum_rows.append([
                str(i), s["ticker"], (s.get("company_name") or "")[:22],
                f"{prob*100:.0f}%", tier_label, decision_label,
                f"1:{net_rr:.1f}" if net_rr else "—",
                (s.get("fund_sektor") or s.get("sector_regime") or "—")[:14],
            ])
        sum_tbl = Table(sum_rows, colWidths=[8*mm, 16*mm, 42*mm, 14*mm, 18*mm, 24*mm, 16*mm, 22*mm], repeatRows=1)
        sum_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 8.3),
            ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_BG]),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(KeepTogether([
            Paragraph("Ringkasan Peringkat", styles["SectionHeading"]),
            sum_tbl,
        ]))
        story.append(NextPageTemplate("main"))
        story.append(PageBreak())

        # ── Per-stock sections ────────────────────────────────────────
        for i, s in enumerate(stocks, start=1):
            story.extend(_build_stock_section(s, conn, styles, rank=i))
            story.append(PageBreak())

        # ── Glossary / disclaimer page ──────────────────────────────
        story.append(Paragraph("Kamus Istilah", styles["SectionHeading"]))
        for term, desc in GLOSSARY:
            story.append(Paragraph(_esc(term), styles["GlossaryTerm"]))
            story.append(Paragraph(_esc(desc), styles["GlossaryDef"]))

        story.append(Spacer(1, 10))
        story.append(HRFlowable(width="100%", thickness=1, color=BORDER, spaceAfter=6))
        story.append(Paragraph("Peringatan Risiko &amp; Metodologi", styles["SectionHeading"]))
        story.append(Paragraph(
            "Laporan ini dihasilkan otomatis oleh sistem machine learning (ensemble beberapa model) milik Zaiden Trader "
            "berdasarkan data historis perdagangan Bursa Efek Indonesia. Probabilitas dan target harga adalah "
            "estimasi statistik, BUKAN rekomendasi atau nasihat investasi/keuangan. Kinerja masa lalu tidak "
            "menjamin hasil di masa depan. Perhitungan target/stop loss belum memasukkan biaya transaksi riil "
            "broker Anda dan asumsi eksekusi selalu terpenuhi pada harga acuan — kondisi pasar nyata (likuiditas, "
            "slippage) dapat berbeda. Selalu lakukan riset mandiri (do your own research) dan sesuaikan dengan "
            "profil risiko Anda sebelum mengambil keputusan. Zaiden Trader tidak bertanggung jawab atas "
            "kerugian yang timbul dari penggunaan laporan ini.",
            styles["Disclaimer"],
        ))

        doc.build(story)
        return buf.getvalue()
    finally:
        conn.close()
