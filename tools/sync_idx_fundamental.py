"""
IDX Fundamental Screener Data Sync Engine
Syncs latest fundamental data from https://www.idx.co.id/id/investhub/stock-screener/
"""
import sqlite3
import time
import json
import hashlib
import random
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener
from http.cookiejar import CookieJar
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "zaiden_trader.db"
SOURCE_PAGE = "https://www.idx.co.id/id/investhub/stock-screener/"
ENDPOINT = "https://www.idx.co.id/primary/StockData/GetStockScreener"

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
    "Referer": SOURCE_PAGE,
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
}

class IdxSession:
    def __init__(self, timeout: float = 30.0) -> None:
        self.timeout = timeout
        self.cookies = CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookies))
        self.last_initialized = 0.0

    def initialize(self, force: bool = False) -> None:
        if not force and time.monotonic() - self.last_initialized < 900:
            return
        try:
            request = Request("https://www.idx.co.id/id", headers=HEADERS)
            with self.opener.open(request, timeout=self.timeout) as response:
                response.read(1024)
            self.last_initialized = time.monotonic()
        except Exception as e:
            print(f"Session init warning: {e}")

    def get(self, url: str) -> tuple[int, str, bytes]:
        self.initialize()
        request = Request(url, headers=HEADERS)
        with self.opener.open(request, timeout=self.timeout) as response:
            body = response.read()
            return response.status, response.headers.get_content_type(), body

def sync_fundamental(retries: int = 5):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    today = date.today().isoformat()
    
    session = IdxSession(30.0)
    last_error = None
    
    for attempt in range(1, retries + 1):
        try:
            status, content_type, body = session.get(ENDPOINT)
            if "json" not in content_type.lower():
                raise ValueError(f"Content-Type not json: {content_type}")
            payload = json.loads(body)
            data = payload.get("data")
            if not data and isinstance(payload, list):
                data = payload
            if not isinstance(data, list):
                raise ValueError("Payload missing data array")
                
            payload_hash = hashlib.sha256(body).hexdigest()
            print(f"[Attempt {attempt}] Fetched {len(data)} items from IDX Stock Screener.")
            
            inserted_count = 0
            for item in data:
                stock_code = str(item.get("KodeSaham") or item.get("kode_saham") or item.get("Code") or "").strip().upper()
                if not stock_code: 
                    continue
                
                cursor.execute("""
                    INSERT OR REPLACE INTO idx_fundamental_snapshots (
                        stock_code, source_as_of_date, financial_period, nama_perusahaan, sektor, subsektor,
                        industri, subindustri, papan_pencatatan, tanggal_referensi_harga, tanggal_publikasi_laporan,
                        tanggal_informasi_mulai_tersedia_bagi_publik, per, pbv, roe, roa, der, npm, revenue, market_cap,
                        change_4w, change_13w, change_26w, change_52w, change_mtd, change_ytd, payload_hash
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                """, (
                    stock_code,
                    today,
                    "Snapshot",
                    str(item.get("NamaPerusahaan") or item.get("Nama") or "").strip() or None,
                    str(item.get("Sektor") or "").strip() or None,
                    str(item.get("SubSektor") or "").strip() or None,
                    str(item.get("Industri") or "").strip() or None,
                    str(item.get("SubIndustri") or "").strip() or None,
                    str(item.get("PapanPencatatan") or "").strip() or None,
                    today,
                    item.get("TanggalPublikasi"),
                    item.get("TanggalKetersediaan"),
                    item.get("PER"),
                    item.get("PBV"),
                    item.get("ROE"),
                    item.get("ROA"),
                    item.get("DER"),
                    item.get("NPM"),
                    item.get("Revenue"),
                    item.get("MarketCap"),
                    item.get("Change4W"),
                    item.get("Change13W"),
                    item.get("Change26W"),
                    item.get("Change52W"),
                    item.get("ChangeMTD"),
                    item.get("ChangeYTD"),
                    payload_hash
                ))
                inserted_count += 1
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS idx_fundamental_sync_log (
                    tanggal TEXT PRIMARY KEY CHECK (date(tanggal) = tanggal),
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 1,
                    http_status INTEGER,
                    records_total INTEGER,
                    payload_sha256 TEXT,
                    error TEXT,
                    fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                INSERT OR REPLACE INTO idx_fundamental_sync_log (tanggal, status, records_total, payload_sha256, fetched_at, updated_at) 
                VALUES (?, 'success', ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """, (today, inserted_count, payload_hash))
            
            conn.commit()
            conn.close()
            print(f"Successfully synced {inserted_count} fundamental snapshots into SQLite database.")
            return {"status": "success", "count": inserted_count}
        except HTTPError as error:
            last_error = f"HTTP {error.code}: {error.reason}"
            print(f"Attempt {attempt} HTTPError: {last_error}")
            if error.code in {401, 403}:
                session.initialize(force=True)
        except Exception as e:
            last_error = str(e)
            print(f"Attempt {attempt} Error: {last_error}")
        
        if attempt < retries:
            wait = min(2 ** (attempt - 1), 10) + random.uniform(0.5, 1.5)
            time.sleep(wait)
            
    conn.close()
    return {"status": "error", "error": last_error}

if __name__ == '__main__':
    sync_fundamental()
