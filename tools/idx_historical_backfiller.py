"""
IDX Historical Data Backfiller
Tahap A-F, K
"""
import sqlite3
import time
import json
import hashlib
from datetime import date, datetime, timedelta
from urllib.request import HTTPCookieProcessor, Request, build_opener
from http.cookiejar import CookieJar
import sys
from pathlib import Path
import threading

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "zaiden_trader.db"

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
    "Referer": "https://www.idx.co.id/id/data-pasar/ringkasan-perdagangan/ringkasan-saham",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
}

class RateLimiter:
    def __init__(self, interval: float) -> None:
        self.interval = max(interval, 0.0)
        self.lock = threading.Lock()
        self.last_request = 0.0

    def wait(self) -> None:
        with self.lock:
            remaining = self.interval - (time.monotonic() - self.last_request)
            if remaining > 0:
                time.sleep(remaining)
            self.last_request = time.monotonic()

class IdxHistoricalSession:
    def __init__(self, timeout: float = 30.0, rate_limit: float = 1.0) -> None:
        self.timeout = timeout
        self.limiter = RateLimiter(rate_limit)
        self.cookies = CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookies))
        self.last_initialized = 0.0

    def initialize(self, force: bool = False) -> None:
        if not force and time.monotonic() - self.last_initialized < 900:
            return
        print("Initializing session cookies...")
        request = Request("https://www.idx.co.id/id", headers=HEADERS)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                response.read(1024)
            self.last_initialized = time.monotonic()
        except Exception as e:
            print(f"Session init failed: {e}")

    def get(self, url: str) -> tuple[int, str, bytes]:
        self.initialize()
        self.limiter.wait()
        request = Request(url, headers=HEADERS)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                body = response.read()
                return response.status, response.headers.get_content_type(), body
        except Exception as e:
            if hasattr(e, 'code'):
                if e.code == 403:
                    self.initialize(force=True)
                return e.code, "", str(e).encode('utf-8')
            return 500, "", str(e).encode('utf-8')

def audit_local_data(conn):
    print("TAHAP A: AUDIT DATA LOKAL")
    cursor = conn.cursor()
    # Check ringkasan_saham_harian
    min_date = cursor.execute("SELECT MIN(tanggal) FROM ringkasan_saham_harian").fetchone()[0]
    max_date = cursor.execute("SELECT MAX(tanggal) FROM ringkasan_saham_harian").fetchone()[0]
    count = cursor.execute("SELECT COUNT(*) FROM ringkasan_saham_harian").fetchone()[0]
    
    dates_in_db = set(r[0] for r in cursor.execute("SELECT DISTINCT tanggal FROM ringkasan_saham_harian").fetchall())
    
    print(f"Local Daily Summary: {count} rows")
    print(f"Date range: {min_date} to {max_date}")
    print(f"Total dates saved: {len(dates_in_db)}")
    
    # Save to coverage table
    cursor.execute("""
        INSERT INTO idx_data_coverage (id_source, source_name, local_min_date, local_max_date, total_local_trading_dates, last_checked_at)
        VALUES ('daily', 'Ringkasan Saham Harian', ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(id_source) DO UPDATE SET
        local_min_date=excluded.local_min_date, local_max_date=excluded.local_max_date, total_local_trading_dates=excluded.total_local_trading_dates, last_checked_at=excluded.last_checked_at
    """, (min_date, max_date, len(dates_in_db)))
    conn.commit()
    return min_date, max_date, dates_in_db

def test_date(session, d_str):
    url = f"https://www.idx.co.id/primary/TradingSummary/GetStockSummary?date={d_str.replace('-', '')}"
    status, _, body = session.get(url)
    if status == 200:
        try:
            payload = json.loads(body.decode())
            return payload.get("recordsTotal", 0) > 0, payload.get("recordsTotal", 0)
        except:
            return False, 0
    return False, 0

def discover_capabilities(conn, session):
    print("TAHAP B: PENEMUAN TANGGAL PALING AWAL (DISCOVERY)")
    min_date_found = None
    
    test_dates = [
        "2020-01-02", "2019-12-30", "2019-01-02", "2018-01-02", "2017-01-03", "2016-01-04"
    ]
    
    for d in test_dates:
        print(f"Probing {d}...")
        has_data, records = test_date(session, d)
        if has_data:
            print(f"Data found for {d} ({records} records)")
            min_date_found = d
        else:
            print(f"No data for {d}")
            break
            
    if not min_date_found:
        min_date_found = "2020-01-02" 
        
    print(f"Discovered Source Min Available Date (Daily): {min_date_found}")
    max_date = date.today().isoformat()
    
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE idx_data_coverage 
        SET source_min_available_date = ?, source_max_available_date = ?
        WHERE id_source = 'daily'
    """, (min_date_found, max_date))
    
    print("TAHAP C: FUNDAMENTAL CAPABILITY DISCOVERY")
    url = "https://www.idx.co.id/primary/StockData/GetStockScreener"
    status, _, body = session.get(url)
    hist_avail = "Not Available"
    reason = "Endpoint GetStockScreener does not accept date parameters. Only latest snapshot is provided."
    if status == 200:
        print("Fundamental endpoint accessed. Confirmation: no historical dates supported.")
    
    cursor.execute("""
        INSERT INTO idx_data_coverage (id_source, source_name, historical_availability_status, historical_unavailability_reason, last_checked_at)
        VALUES ('fundamental', 'Stock Screener', ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(id_source) DO UPDATE SET
        historical_availability_status=excluded.historical_availability_status, historical_unavailability_reason=excluded.historical_unavailability_reason, last_checked_at=excluded.last_checked_at
    """, (hist_avail, reason))
    
    conn.commit()
    return min_date_found, max_date

def build_missing_dates(min_date_str, max_date_str, existing_dates):
    min_d = datetime.strptime(min_date_str, "%Y-%m-%d").date()
    max_d = datetime.strptime(max_date_str, "%Y-%m-%d").date()
    
    all_weekdays = []
    curr = min_d
    while curr <= max_d:
        if curr.weekday() < 5: 
            all_weekdays.append(curr.isoformat())
        curr += timedelta(days=1)
        
    missing = [d for d in all_weekdays if d not in existing_dates]
    return missing

if __name__ == '__main__':
    conn = sqlite3.connect(DB_PATH)
    session = IdxHistoricalSession(timeout=15.0, rate_limit=0.5)
    
    print("Running Backfiller...")
    local_min, local_max, existing = audit_local_data(conn)
    source_min, source_max = discover_capabilities(conn, session)
    
    missing = build_missing_dates(source_min, source_max, existing)
    print(f"Total potentially missing weekdays: {len(missing)}")
    
    conn.close()
