"""
IDX Fundamental Sync Tool using yfinance
Populates PER, PBV, ROE, ROA, DER, NPM, Market Cap, and Sektor for all IDX stocks.
"""
import sqlite3
import time
from datetime import date
from pathlib import Path
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "zaiden_trader.db"


def _sane(value, lo, hi):
    """Reject implausible ratios (bad upstream data, e.g. Yahoo book-value glitches
    that produce PBV in the thousands) instead of storing them as if valid."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return v if lo <= v <= hi else None


def sync_fundamental_yf(batch_size: int = 50):
    print("Starting Fundamental Sync via yfinance...")
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=60.0)
    conn.execute("PRAGMA busy_timeout = 60000")
    cursor = conn.cursor()
    
    # Get all stock codes from SQLite database
    cursor.execute("SELECT DISTINCT kode_saham FROM ringkasan_saham_harian ORDER BY kode_saham")
    stocks = [r[0] for r in cursor.fetchall() if r[0] and len(r[0]) <= 5]
    
    print(f"Found {len(stocks)} stocks to sync fundamental metrics.")
    today = date.today().isoformat()
    
    updated_count = 0
    
    for i in range(0, len(stocks), batch_size):
        batch = stocks[i:i+batch_size]
        yf_symbols = [f"{s}.JK" for s in batch]
        symbol_str = " ".join(yf_symbols)
        
        print(f"Fetching batch {i//batch_size + 1}/{(len(stocks)+batch_size-1)//batch_size} ({len(batch)} stocks)...")
        
        try:
            tickers = yf.Tickers(symbol_str)
            for code in batch:
                yf_code = f"{code}.JK"
                try:
                    t = tickers.tickers.get(yf_code)
                    if not t:
                        continue
                    info = t.info
                    if not info or not isinstance(info, dict):
                        continue
                        
                    per = _sane(info.get("trailingPE"), -1000, 1000)
                    pbv = _sane(info.get("priceToBook"), -100, 100)

                    # ROE & ROA are returned as fractions (e.g. 0.2297 for 22.97%)
                    roe = _sane(round(info.get("returnOnEquity") * 100, 2), -500, 500) if info.get("returnOnEquity") is not None else None
                    roa = _sane(round(info.get("returnOnAssets") * 100, 2), -500, 500) if info.get("returnOnAssets") is not None else None

                    der = _sane(info.get("debtToEquity"), -1000, 1000)
                    npm = _sane(round(info.get("profitMargins") * 100, 2), -1000, 1000) if info.get("profitMargins") is not None else None
                    revenue = info.get("totalRevenue")
                    market_cap = info.get("marketCap")
                    
                    sektor = info.get("sector")
                    subsektor = info.get("industry")
                    nama = info.get("longName") or info.get("shortName")
                    
                    cursor.execute("""
                        INSERT INTO idx_fundamental_snapshots (
                            stock_code, source_as_of_date, financial_period, nama_perusahaan, sektor, subsektor,
                            per, pbv, roe, roa, der, npm, revenue, market_cap, waktu_sinkronisasi
                        ) VALUES (?, ?, 'Snapshot', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(stock_code, source_as_of_date, financial_period) DO UPDATE SET
                            nama_perusahaan=COALESCE(EXCLUDED.nama_perusahaan, idx_fundamental_snapshots.nama_perusahaan),
                            sektor=COALESCE(EXCLUDED.sektor, idx_fundamental_snapshots.sektor),
                            subsektor=COALESCE(EXCLUDED.subsektor, idx_fundamental_snapshots.subsektor),
                            per=EXCLUDED.per,
                            pbv=EXCLUDED.pbv,
                            roe=EXCLUDED.roe,
                            roa=EXCLUDED.roa,
                            der=EXCLUDED.der,
                            npm=EXCLUDED.npm,
                            revenue=COALESCE(EXCLUDED.revenue, idx_fundamental_snapshots.revenue),
                            market_cap=COALESCE(EXCLUDED.market_cap, idx_fundamental_snapshots.market_cap),
                            waktu_sinkronisasi=CURRENT_TIMESTAMP
                    """, (code, today, nama, sektor, subsektor, per, pbv, roe, roa, der, npm, revenue, market_cap))
                    
                    updated_count += 1
                except Exception as ex:
                    pass
            
            conn.commit()
            time.sleep(0.5)
        except Exception as e:
            print(f"Batch fetch warning: {e}")
            
    # Update coverage statistics
    cursor.execute("""
        INSERT OR REPLACE INTO idx_data_coverage (
            id_source, source_name, local_min_date, local_max_date, 
            total_local_trading_dates, last_checked_at, historical_availability_status
        ) VALUES (
            'fundamental', 'IDX Fundamental & Valuasi (yfinance)', ?, ?, ?, CURRENT_TIMESTAMP, 'Active'
        )
    """, (today, today, updated_count))
    
    conn.commit()
    conn.close()
    print(f"Completed fundamental sync! Updated {updated_count} stocks with real PER, PBV, ROE, DER, NPM metrics.")
    return {"status": "success", "updated_count": updated_count}

if __name__ == "__main__":
    sync_fundamental_yf()
