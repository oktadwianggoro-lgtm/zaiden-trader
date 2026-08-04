import sqlite3
import threading
from datetime import datetime
from typing import Any
from db import connect

GROWTH_SYNC_LOCK = threading.Lock()
GROWTH_SYNC_STATE: dict[str, Any] = {
    "status": "idle",
    "started_at": None,
    "finished_at": None,
    "message": "Belum ada sinkronisasi data historis fundamental.",
    "result": None,
}


def get_growth_sync_status() -> dict[str, Any]:
    with GROWTH_SYNC_LOCK:
        return dict(GROWTH_SYNC_STATE)


def start_growth_sync_job() -> dict[str, Any]:
    """POST /api/analytics/bdm-growth/sync — backfill idx_fundamental_financials
    in the background (takes several minutes for ~900 tickers)."""
    with GROWTH_SYNC_LOCK:
        if GROWTH_SYNC_STATE["status"] == "running":
            return {"ok": False, "message": "Sinkronisasi sedang berjalan.", "status": GROWTH_SYNC_STATE}
        GROWTH_SYNC_STATE.update({
            "status": "running",
            "started_at": datetime.now().isoformat(),
            "finished_at": None,
            "message": "Memulai sinkronisasi data historis fundamental (~5-15 menit)...",
            "result": None,
        })

    def run():
        from tools.sync_idx_fundamental_history import sync_history
        from tools.build_fundamental_ratio_history import build_ratio_history
        from tools.detect_corporate_actions import detect_for_all_stocks
        try:
            result = sync_history()
            with GROWTH_SYNC_LOCK:
                GROWTH_SYNC_STATE["message"] = "Menghitung PER/PBV per periode (kuartalan/semester/9-bulan/tahunan)..."
            ratio_result = build_ratio_history()
            with GROWTH_SYNC_LOCK:
                GROWTH_SYNC_STATE["message"] = "Mendeteksi corporate action (stock split/reverse split/rights issue)..."
            ca_result = detect_for_all_stocks()
            with GROWTH_SYNC_LOCK:
                GROWTH_SYNC_STATE.update({
                    "status": "success",
                    "finished_at": datetime.now().isoformat(),
                    "message": (
                        f"Selesai: {result['rows_written']} baris laporan keuangan ({result['tickers_failed']} emiten gagal), "
                        f"{ratio_result['rows_written']} baris riwayat valuasi untuk {ratio_result['stocks_covered']} saham, "
                        f"{ca_result['total_events']} event corporate action terdeteksi di {ca_result['stocks_with_events']} saham."
                    ),
                    "result": {**result, "ratio_history": ratio_result, "corporate_actions": ca_result},
                })
        except Exception as exc:
            with GROWTH_SYNC_LOCK:
                GROWTH_SYNC_STATE.update({
                    "status": "failed",
                    "finished_at": datetime.now().isoformat(),
                    "message": f"Gagal: {exc}",
                })

    threading.Thread(target=run, name="fundamental-history-sync", daemon=True).start()
    return {"ok": True, "message": "Sinkronisasi data historis dimulai.", "status": dict(GROWTH_SYNC_STATE)}


def get_bdm_valuation_history(stock_code: str) -> dict[str, Any]:
    """
    GET /api/analytics/bdm-valuation-history?code=XXX
    Per-period PER/PBV timeline (quarterly / semester / nine_month / annual)
    plus one trend-based projection row. This is what actually answers
    "PBV/PER berubah tiap kuartal/tahun" — not a single snapshot.
    """
    with connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT period_type, period_label, period_end, net_income_cumulative,
                      revenue_cumulative, annualized_net_income, annualized_eps,
                      book_value_per_share, price_close, per, pbv, market_cap, is_projected
               FROM idx_fundamental_ratio_history
               WHERE stock_code = ?
               ORDER BY period_end ASC""",
            (stock_code.upper(),),
        ).fetchall()
        name_row = conn.execute(
            "SELECT nama_perusahaan, sektor FROM idx_fundamental_snapshots WHERE stock_code=? ORDER BY source_as_of_date DESC LIMIT 1",
            (stock_code.upper(),),
        ).fetchone()

    if not rows:
        return {"stock_code": stock_code.upper(), "items": [], "error": "Belum ada riwayat valuasi. Jalankan 'Sinkronkan Data Historis' di tab Growth Screener."}

    return {
        "stock_code": stock_code.upper(),
        "nama_perusahaan": name_row["nama_perusahaan"] if name_row else None,
        "sektor": name_row["sektor"] if name_row else None,
        "items": [dict(r) for r in rows],
        "projection_note": (
            "Baris 'Proyeksi' adalah estimasi tren linier dari pertumbuhan laba tahunan historis, "
            "BUKAN prediksi analis atau rekomendasi investasi. PER/PBV per-periode dihitung dari laporan "
            "keuangan mentah (yfinance); untuk sebagian emiten kecil skalanya tidak konsisten dan nilai "
            "yang tidak masuk akal disembunyikan (bukan dipaksakan tampil) — gunakan snapshot di tab "
            "'Fundamental & Valuasi' sebagai angka PER/PBV hari-ini yang lebih andal."
        ),
    }


def get_bdm_growth_screener() -> dict[str, Any]:
    """
    'Compounder' screener: classify stocks by consistency of profit growth
    using annual and quarterly financials (idx_fundamental_financials).

    Caveat surfaced to the caller: depth is whatever yfinance exposes per
    ticker (typically ~4 fiscal years / ~5 quarters), not the full 2020-present
    history, since Yahoo Finance does not retain older filings for most IDX
    issuers.
    """
    with connect() as conn:
        conn.row_factory = sqlite3.Row

        rows = conn.execute(
            """SELECT stock_code, period_type, period_end, fiscal_year, fiscal_quarter,
                      revenue, net_income
               FROM idx_fundamental_financials
               ORDER BY stock_code, period_type, period_end ASC"""
        ).fetchall()

        names = {
            r["stock_code"]: r["nama_perusahaan"]
            for r in conn.execute(
                """SELECT stock_code, nama_perusahaan FROM (
                    SELECT stock_code, nama_perusahaan,
                           ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY source_as_of_date DESC) rn
                    FROM idx_fundamental_snapshots WHERE nama_perusahaan IS NOT NULL
                ) WHERE rn = 1"""
            ).fetchall()
        }
        sektors = {
            r["stock_code"]: r["sektor"]
            for r in conn.execute(
                """SELECT stock_code, sektor FROM (
                    SELECT stock_code, sektor,
                           ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY source_as_of_date DESC) rn
                    FROM idx_fundamental_snapshots WHERE sektor IS NOT NULL
                ) WHERE rn = 1"""
            ).fetchall()
        }
        market_caps = {
            r["stock_code"]: r["market_cap"]
            for r in conn.execute(
                """SELECT stock_code, market_cap FROM (
                    SELECT stock_code, market_cap,
                           ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY source_as_of_date DESC) rn
                    FROM idx_fundamental_snapshots WHERE market_cap IS NOT NULL
                ) WHERE rn = 1"""
            ).fetchall()
        }
        current_per = {
            r["stock_code"]: (r["per"], r["pbv"])
            for r in conn.execute(
                """SELECT stock_code, per, pbv FROM (
                    SELECT stock_code, per, pbv,
                           ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY period_end DESC) rn
                    FROM idx_fundamental_ratio_history WHERE is_projected = 0
                ) WHERE rn = 1"""
            ).fetchall()
        }

    by_stock: dict[str, dict[str, list]] = {}
    for r in rows:
        d = by_stock.setdefault(r["stock_code"], {"annual": [], "quarterly": []})
        d[r["period_type"]].append(r)

    def growth_streak(seq: list, key: str) -> tuple[int, int]:
        """Count consecutive growth periods ending at the most recent one.
        Returns (streak, total_comparisons)."""
        vals = [r[key] for r in seq if r[key] is not None]
        if len(vals) < 2:
            return 0, 0
        streak = 0
        for i in range(len(vals) - 1, 0, -1):
            if vals[i] > vals[i - 1]:
                streak += 1
            else:
                break
        return streak, len(vals) - 1

    def cagr(seq: list, key: str) -> float | None:
        vals = [r[key] for r in seq if r[key] is not None]
        if len(vals) < 2 or vals[0] <= 0 or vals[-1] <= 0:
            return None
        years = len(vals) - 1
        try:
            return round((((vals[-1] / vals[0]) ** (1 / years)) - 1) * 100, 2)
        except (ValueError, ZeroDivisionError):
            return None

    items = []
    summary = {"consistent_compounder": 0, "growing": 0, "declining": 0, "volatile": 0}

    for code, d in by_stock.items():
        annual = d["annual"]
        quarterly = d["quarterly"]
        if len(annual) < 2 and len(quarterly) < 2:
            continue

        ni_streak, ni_total = growth_streak(annual, "net_income")
        rev_streak, rev_total = growth_streak(annual, "revenue")
        q_ni_streak, q_ni_total = growth_streak(quarterly, "net_income")

        if ni_total >= 2 and ni_streak == ni_total:
            category = "consistent_compounder"
        elif ni_total >= 1 and ni_streak >= 1:
            category = "growing"
        elif ni_total >= 1 and ni_streak == 0:
            category = "declining"
        else:
            category = "volatile"
        summary[category] += 1

        cur_per, cur_pbv = current_per.get(code, (None, None))
        items.append({
            "stock_code": code,
            "nama_perusahaan": names.get(code),
            "sektor": sektors.get(code),
            "market_cap": market_caps.get(code),
            "per": cur_per,
            "pbv": cur_pbv,
            "category": category,
            "annual_periods": ni_total + 1 if ni_total else len(annual),
            "net_income_growth_streak_years": ni_streak,
            "revenue_growth_streak_years": rev_streak,
            "quarterly_net_income_growth_streak": q_ni_streak,
            "net_income_cagr_pct": cagr(annual, "net_income"),
            "revenue_cagr_pct": cagr(annual, "revenue"),
            "latest_annual_net_income": next((r["net_income"] for r in reversed(annual) if r["net_income"] is not None), None),
            "latest_annual_period": annual[-1]["period_end"] if annual else None,
            "latest_quarterly_period": quarterly[-1]["period_end"] if quarterly else None,
        })

    items.sort(key=lambda x: (x["net_income_growth_streak_years"], x["net_income_cagr_pct"] or -999), reverse=True)

    return {
        "summary": {**summary, "total_analyzed": len(items)},
        "items": items,
        "coverage_note": (
            "Kedalaman data historis mengikuti ketersediaan yfinance per emiten "
            "(umumnya ~4 tahun buku / ~5 kuartal terakhir), bukan penuh sejak 2020."
        ),
    }


def get_bdm_screener() -> dict[str, Any]:
    with connect() as conn:
        conn.row_factory = sqlite3.Row
        
        latest = conn.execute("SELECT MAX(tanggal) FROM ringkasan_saham_harian").fetchone()[0]
        if not latest:
            return {"items": []}
            
        query = """
        SELECT r.kode_saham, r.nama_perusahaan, r.harga_penutupan, r.perubahan,
               r.volume, r.nilai_transaksi,
               t.rsi14, t.macd, t.macd_signal, t.bb_upper, t.bb_lower, t.stoch_k, t.stoch_d, t.volatility,
               l.liquidity_score, l.liquidity_level,
               f.net_value AS foreign_net_20d, f.net_pct_of_total_value AS foreign_net_pct
        FROM ringkasan_saham_harian r
        LEFT JOIN stock_technical_eod t ON r.kode_saham = t.security_id AND r.tanggal = t.trade_date
        LEFT JOIN stock_liquidity_metrics l ON r.kode_saham = l.security_id AND r.tanggal = l.as_of_date
        LEFT JOIN stock_flow_metrics f ON r.kode_saham = f.security_id AND r.tanggal = f.as_of_date AND f.period_code = '20D'
        WHERE r.tanggal = ? AND r.harga_penutupan > 0
        """
        rows = conn.execute(query, (latest,)).fetchall()
        
        items = []
        for row in rows:
            items.append({
                "code": row["kode_saham"],
                "name": row["nama_perusahaan"],
                "close": row["harga_penutupan"],
                "change": row["perubahan"],
                "volume": row["volume"],
                "value": row["nilai_transaksi"],
                "rsi14": row["rsi14"],
                "macd": row["macd"],
                "macd_signal": row["macd_signal"],
                "bb_upper": row["bb_upper"],
                "bb_lower": row["bb_lower"],
                "stoch_k": row["stoch_k"],
                "stoch_d": row["stoch_d"],
                "volatility": row["volatility"],
                "liquidity_score": row["liquidity_score"],
                "liquidity_level": row["liquidity_level"],
                "foreign_net_20d": row["foreign_net_20d"],
                "foreign_net_pct": row["foreign_net_pct"]
            })
            
    return {
        "as_of": latest,
        "count": len(items),
        "items": items
    }

def get_bdm_overview(ticker: str) -> dict[str, Any]:
    with connect() as conn:
        conn.row_factory = sqlite3.Row
        
        latest_row = conn.execute("SELECT MAX(tanggal) FROM ringkasan_saham_harian WHERE kode_saham = ?", (ticker,)).fetchone()
        latest = latest_row[0] if latest_row else None
        if not latest:
            return {"error": "Ticker not found"}
            
        query = """
        SELECT r.*, 
               t.rsi14, t.macd, t.macd_signal, t.macd_hist, t.bb_upper, t.bb_mid, t.bb_lower, 
               t.stoch_k, t.stoch_d, t.atr14, t.atr_pct, t.volatility, t.smacd_macd, t.smacd_signal,
               l.liquidity_score, l.liquidity_level, l.median_value_20d, l.median_frequency_20d,
               f.net_value AS foreign_net_20d, f.net_pct_of_total_value AS foreign_net_pct
        FROM ringkasan_saham_harian r
        LEFT JOIN stock_technical_eod t ON r.kode_saham = t.security_id AND r.tanggal = t.trade_date
        LEFT JOIN stock_liquidity_metrics l ON r.kode_saham = l.security_id AND r.tanggal = l.as_of_date
        LEFT JOIN stock_flow_metrics f ON r.kode_saham = f.security_id AND r.tanggal = f.as_of_date AND f.period_code = '20D'
        WHERE r.kode_saham = ? AND r.tanggal = ?
        """
        row = conn.execute(query, (ticker, latest)).fetchone()
        
        if not row:
            return {"error": "Data not found"}
            
        return {
            "code": row["kode_saham"],
            "name": row["nama_perusahaan"],
            "date": row["tanggal"],
            "close": row["harga_penutupan"],
            "change": row["perubahan"],
            "volume": row["volume"],
            "value": row["nilai_transaksi"],
            "technical": {
                "rsi14": row["rsi14"],
                "macd": row["macd"],
                "macd_signal": row["macd_signal"],
                "macd_hist": row["macd_hist"],
                "smacd_macd": row["smacd_macd"],
                "smacd_signal": row["smacd_signal"],
                "bb_upper": row["bb_upper"],
                "bb_mid": row["bb_mid"],
                "bb_lower": row["bb_lower"],
                "stoch_k": row["stoch_k"],
                "stoch_d": row["stoch_d"],
                "atr14": row["atr14"],
                "atr_pct": row["atr_pct"],
                "volatility": row["volatility"]
            },
            "liquidity": {
                "score": row["liquidity_score"],
                "level": row["liquidity_level"],
                "median_value_20d": row["median_value_20d"],
                "median_freq_20d": row["median_frequency_20d"]
            },
            "flow": {
                "foreign_net_20d": row["foreign_net_20d"],
                "foreign_net_pct": row["foreign_net_pct"]
            }
        }

def get_bdm_coverage() -> dict[str, Any]:
    with connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM idx_data_coverage").fetchall()
        result = {}
        for row in rows:
            result[row["id_source"]] = dict(row)

        if "daily" in result:
            result["daily"]["total_rows"] = conn.execute(
                "SELECT COUNT(*) FROM ringkasan_saham_harian"
            ).fetchone()[0]
            result["daily"]["total_stocks"] = conn.execute(
                "SELECT COUNT(DISTINCT kode_saham) FROM ringkasan_saham_harian"
            ).fetchone()[0]

        if "fundamental" in result:
            latest_date = result["fundamental"].get("local_max_date")
            field_counts = {}
            for field in ("per", "pbv", "roe", "roa", "der"):
                field_counts[field] = conn.execute(
                    f"SELECT COUNT(*) FROM idx_fundamental_snapshots "
                    f"WHERE source_as_of_date = ? AND {field} IS NOT NULL",
                    (latest_date,),
                ).fetchone()[0]
            total = result["fundamental"].get("total_local_trading_dates") or 0
            loss_making = conn.execute(
                """SELECT COUNT(*) FROM idx_fundamental_snapshots
                   WHERE source_as_of_date = ? AND per IS NULL AND roe IS NOT NULL AND roe < 0""",
                (latest_date,),
            ).fetchone()[0]
            result["fundamental"]["field_coverage"] = field_counts
            result["fundamental"]["field_coverage_total"] = total
            result["fundamental"]["per_null_loss_making"] = loss_making

        if "fundamental_history" in result:
            result["fundamental_history"]["total_stocks_covered"] = conn.execute(
                "SELECT COUNT(DISTINCT stock_code) FROM idx_fundamental_financials"
            ).fetchone()[0]

        return result

def get_bdm_historical(code: str, date_start: str, date_end: str, page: int, limit: int) -> dict[str, Any]:
    with connect() as conn:
        conn.row_factory = sqlite3.Row
        
        where_clauses = []
        params = []
        
        if code:
            where_clauses.append("r.kode_saham = ?")
            params.append(code.upper())
            
        if date_start:
            where_clauses.append("r.tanggal >= ?")
            params.append(date_start)
            
        if date_end:
            where_clauses.append("r.tanggal <= ?")
            params.append(date_end)
            
        where_str = " AND ".join(where_clauses)
        if where_str:
            where_str = "WHERE " + where_str
            
        count_query = f"SELECT COUNT(*) FROM ringkasan_saham_harian r {where_str}"
        total = conn.execute(count_query, params).fetchone()[0]
        
        offset = (page - 1) * limit
        # Add point in time join for fundamental data (Tahap H)
        query = f"""
        SELECT r.tanggal, r.kode_saham, r.harga_pembukaan, r.harga_tertinggi, r.harga_terendah, r.harga_penutupan,
               r.perubahan, r.volume, r.nilai_transaksi, r.frekuensi, r.saham_tercatat, r.kapitalisasi_pasar,
               f.per, f.pbv, f.roe, f.der, f.market_cap as fun_market_cap
        FROM (
            SELECT r.*, r.harga_penutupan * r.saham_tercatat as kapitalisasi_pasar
            FROM ringkasan_saham_harian r
            {where_str}
            ORDER BY r.tanggal DESC
            LIMIT ? OFFSET ?
        ) r
        LEFT JOIN (
            SELECT f1.*
            FROM idx_fundamental_snapshots f1
            INNER JOIN (
                SELECT stock_code, MAX(source_as_of_date) as max_date
                FROM idx_fundamental_snapshots
                GROUP BY stock_code
            ) f2 ON f1.stock_code = f2.stock_code AND f1.source_as_of_date = f2.max_date
            -- Note: Since IDX only provides current snapshots, we join with the latest snapshot we have.
            -- A true point-in-time join would use: source_as_of_date <= r.tanggal 
            -- but since we just started saving today, this would yield NULL for all historical rows.
        ) f ON r.kode_saham = f.stock_code
        """
        params.extend([limit, offset])
        
        rows = conn.execute(query, params).fetchall()
        items = [dict(row) for row in rows]
        
    return {
        "total": total,
        "page": page,
        "limit": limit,
        "items": items
    }

def get_bdm_fundamental() -> dict[str, Any]:
    with connect() as conn:
        conn.row_factory = sqlite3.Row
        
        query = """
        SELECT stock_code, nama_perusahaan, sektor, per, pbv, roe, der, market_cap
        FROM (
            SELECT stock_code, nama_perusahaan, sektor, per, pbv, roe, der, market_cap,
                   ROW_NUMBER() OVER (
                       PARTITION BY stock_code 
                       ORDER BY (CASE WHEN per IS NOT NULL OR pbv IS NOT NULL OR roe IS NOT NULL THEN 1 ELSE 0 END) DESC, 
                                source_as_of_date DESC, 
                                waktu_sinkronisasi DESC
                   ) as rn
            FROM idx_fundamental_snapshots
        )
        WHERE rn = 1
        ORDER BY stock_code ASC
        """
        rows = conn.execute(query).fetchall()
        items = [dict(row) for row in rows]
        
    return {
        "count": len(items),
        "items": items
    }

def get_bdm_quant_analysis() -> dict[str, Any]:
    with connect() as conn:
        conn.row_factory = sqlite3.Row
        
        query = """
        SELECT f.stock_code, f.nama_perusahaan, f.sektor, f.per, f.pbv, f.roe, f.roa, f.der, f.market_cap,
               stat.min_close, stat.max_close, stat.latest_close, stat.total_days
        FROM (
            SELECT stock_code, nama_perusahaan, sektor, per, pbv, roe, roa, der, market_cap,
                   ROW_NUMBER() OVER (
                       PARTITION BY stock_code 
                       ORDER BY (CASE WHEN per IS NOT NULL OR pbv IS NOT NULL OR roe IS NOT NULL THEN 1 ELSE 0 END) DESC, 
                                source_as_of_date DESC, 
                                waktu_sinkronisasi DESC
                   ) as rn
            FROM idx_fundamental_snapshots
        ) f
        LEFT JOIN (
            SELECT kode_saham, 
                   MIN(harga_penutupan) as min_close, 
                   MAX(harga_penutupan) as max_close,
                   COUNT(*) as total_days,
                   harga_penutupan as latest_close
            FROM ringkasan_saham_harian
            GROUP BY kode_saham
        ) stat ON f.stock_code = stat.kode_saham
        WHERE f.rn = 1
        """
        rows = conn.execute(query).fetchall()
        
        items = []
        super_value_count = 0
        growth_count = 0
        value_trap_count = 0
        overvalued_count = 0
        
        for r in rows:
            code = r["stock_code"]
            per = r["per"]
            pbv = r["pbv"]
            roe = r["roe"]
            roa = r["roa"]
            der = r["der"]
            min_c = r["min_close"]
            max_c = r["max_close"]
            latest_c = r["latest_close"]
            
            # Position in historic 52W/Range (%)
            pos_pct = None
            if min_c and max_c and latest_c and max_c > min_c:
                pos_pct = round(((latest_c - min_c) / (max_c - min_c)) * 100, 1)
                
            # Classifications
            categories = []
            
            # 1. Super Value
            if (roe and roe >= 12) and (pbv and pbv <= 1.5) and (der is None or der <= 1.5):
                categories.append("super_value")
                super_value_count += 1
                
            # 2. Quality Growth
            if (roe and roe >= 15) or (roa and roa >= 8):
                categories.append("quality_growth")
                growth_count += 1
                
            # 3. Value Trap
            if (pbv and pbv <= 0.8) and ((roe and roe < 0) or (der and der > 2.5)):
                categories.append("value_trap")
                value_trap_count += 1
                
            # 4. Overvalued
            if (pbv and pbv >= 4.0) and (roe is None or roe < 5):
                categories.append("overvalued")
                overvalued_count += 1
                
            if not categories:
                categories.append("neutral")
                
            items.append({
                "stock_code": code,
                "nama_perusahaan": r["nama_perusahaan"],
                "sektor": r["sektor"],
                "per": per,
                "pbv": pbv,
                "roe": roe,
                "der": der,
                "latest_close": latest_c,
                "min_close": min_c,
                "max_close": max_c,
                "range_pos_pct": pos_pct,
                "categories": categories
            })
            
    return {
        "summary": {
            "super_value": super_value_count,
            "quality_growth": growth_count,
            "value_trap": value_trap_count,
            "overvalued": overvalued_count,
            "total_analyzed": len(items)
        },
        "items": items
    }
