"""Sinkronkan Ringkasan Saham harian IDX ke SQLite secara bertahap.

Sumber resmi:
https://www.idx.co.id/primary/TradingSummary/GetStockSummary?date=YYYYMMDD

Proses aman dilanjutkan: setiap tanggal dicatat di idx_daily_sync_log dan setiap
snapshot harian ditulis dalam satu transaksi SQLite.
"""
from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
import hashlib
import json
import random
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "zaiden_trader.db"
SOURCE_PAGE = "https://www.idx.co.id/id/data-pasar/ringkasan-perdagangan/ringkasan-saham"
ENDPOINT = "https://www.idx.co.id/primary/TradingSummary/GetStockSummary?date={}"

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

DB_COLUMNS = [
    "tanggal", "kode_saham", "id_stock_summary", "nama_perusahaan", "remarks",
    "sebelumnya", "harga_pembukaan", "perdagangan_pertama", "harga_tertinggi",
    "harga_terendah", "harga_penutupan", "perubahan", "volume", "nilai_transaksi",
    "frekuensi", "indeks_individual", "penawaran_jual", "volume_penawaran_jual",
    "penawaran_beli", "volume_penawaran_beli", "saham_tercatat",
    "saham_dapat_diperdagangkan", "bobot_indeks", "jual_asing", "beli_asing",
    "tanggal_delisting", "volume_non_reguler", "nilai_non_reguler",
    "frekuensi_non_reguler", "sumber_file",
]

INTEGER_FIELDS = {
    "IDStockSummary", "Volume", "Frequency", "OfferVolume", "BidVolume",
    "ListedShares", "TradebleShares", "ForeignSell", "ForeignBuy",
    "NonRegularVolume", "NonRegularFrequency",
}


@dataclass
class FetchResult:
    day: date
    status: str
    rows: list[tuple[Any, ...]]
    records_total: int
    payload_hash: str | None
    attempts: int
    http_status: int | None
    error: str | None = None


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


class IdxSession:
    def __init__(self, timeout: float, limiter: RateLimiter) -> None:
        self.timeout = timeout
        self.limiter = limiter
        self.cookies = CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookies))
        self.last_initialized = 0.0

    def initialize(self, force: bool = False) -> None:
        if not force and time.monotonic() - self.last_initialized < 900:
            return
        request = Request("https://www.idx.co.id/id", headers=HEADERS)
        with self.opener.open(request, timeout=self.timeout) as response:
            response.read(1024)
        self.last_initialized = time.monotonic()

    def get(self, url: str) -> tuple[int, str, bytes]:
        self.initialize()
        self.limiter.wait()
        request = Request(url, headers=HEADERS)
        with self.opener.open(request, timeout=self.timeout) as response:
            body = response.read()
            return response.status, response.headers.get_content_type(), body


def parse_day(raw: str) -> date:
    return datetime.strptime(raw, "%Y-%m-%d").date()


def iso_from_api(raw: Any, fallback: date) -> str:
    text = str(raw or "").strip()
    if not text:
        return fallback.isoformat()
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    if parsed != fallback:
        raise ValueError(f"Tanggal record {parsed} tidak sama dengan request {fallback}")
    return parsed.isoformat()


def numeric(item: dict[str, Any], key: str) -> int | float | None:
    value = item.get(key)
    if value is None or value == "":
        return None
    if key in INTEGER_FIELDS:
        return int(value)
    return float(value)


def row_from_item(item: dict[str, Any], requested_day: date) -> tuple[Any, ...]:
    ticker = str(item.get("StockCode") or "").strip().upper()
    if not ticker:
        raise ValueError("StockCode kosong")
    delisting = str(item.get("DelistingDate") or "").strip() or None
    if delisting:
        delisting = datetime.fromisoformat(delisting.replace("Z", "+00:00")).date().isoformat()
    return (
        iso_from_api(item.get("Date"), requested_day), ticker,
        numeric(item, "IDStockSummary"), str(item.get("StockName") or "").strip() or None,
        str(item.get("Remarks") or "").strip() or None,
        numeric(item, "Previous"), numeric(item, "OpenPrice"), numeric(item, "FirstTrade"),
        numeric(item, "High"), numeric(item, "Low"), numeric(item, "Close"),
        numeric(item, "Change"), numeric(item, "Volume"), numeric(item, "Value"),
        numeric(item, "Frequency"), numeric(item, "IndexIndividual"),
        numeric(item, "Offer"), numeric(item, "OfferVolume"), numeric(item, "Bid"),
        numeric(item, "BidVolume"), numeric(item, "ListedShares"),
        numeric(item, "TradebleShares"), numeric(item, "WeightForIndex"),
        numeric(item, "ForeignSell"), numeric(item, "ForeignBuy"), delisting,
        numeric(item, "NonRegularVolume"), numeric(item, "NonRegularValue"),
        numeric(item, "NonRegularFrequency"), "idx-primary-api",
    )


def fetch_day(session: IdxSession, day: date, retries: int) -> FetchResult:
    url = ENDPOINT.format(day.strftime("%Y%m%d"))
    last_error: str | None = None
    last_status: int | None = None
    for attempt in range(1, retries + 1):
        try:
            status, content_type, body = session.get(url)
            last_status = status
            if content_type != "application/json":
                raise ValueError(f"Content-Type tidak valid: {content_type}")
            payload = json.loads(body)
            data = payload.get("data")
            if not isinstance(data, list):
                raise ValueError("Respons tidak memiliki array data")
            total = int(payload.get("recordsTotal", len(data)))
            if total != len(data):
                raise ValueError(f"Data tidak lengkap: recordsTotal={total}, data={len(data)}")
            rows = [row_from_item(item, day) for item in data]
            tickers = [row[1] for row in rows]
            if len(tickers) != len(set(tickers)):
                raise ValueError("Kode saham duplikat pada snapshot harian")
            digest = hashlib.sha256(body).hexdigest()
            return FetchResult(
                day, "success" if rows else "no_data", rows, total, digest,
                attempt, status,
            )
        except HTTPError as error:
            last_status = error.code
            last_error = f"HTTP {error.code}: {error.reason}"
            if error.code in {401, 403}:
                session.initialize(force=True)
            if error.code not in {401, 403, 408, 429} and error.code < 500:
                break
        except (URLError, TimeoutError, ValueError, json.JSONDecodeError) as error:
            last_error = str(error)
        if attempt < retries:
            wait = min(2 ** (attempt - 1), 30) + random.uniform(0.1, 0.6)
            time.sleep(wait)
    return FetchResult(day, "failed", [], 0, None, retries, last_status, last_error)


def iter_weekdays(start: date, end: date):
    current = start
    while current <= end:
        if current.weekday() < 5:
            yield current
        current += timedelta(days=1)


def pending_days(connection: sqlite3.Connection, start: date, end: date, refresh: int):
    recent_cutoff = end - timedelta(days=max(refresh, 0))
    statuses = {
        row[0]: row[1]
        for row in connection.execute(
            "SELECT tanggal, status FROM idx_daily_sync_log WHERE tanggal BETWEEN ? AND ?",
            (start.isoformat(), end.isoformat()),
        )
    }
    for day in iter_weekdays(start, end):
        status = statuses.get(day.isoformat())
        if status == "success" and day < recent_cutoff:
            continue
        if status == "no_data" and day < recent_cutoff:
            continue
        yield day


def save_result(connection: sqlite3.Connection, result: FetchResult) -> None:
    day_text = result.day.isoformat()
    with connection:
        if result.status == "success":
            # Snapshot yang valid menggantikan snapshot tanggal yang sama secara atomik.
            connection.execute("DELETE FROM ringkasan_saham_harian WHERE tanggal = ?", (day_text,))
            placeholders = ", ".join("?" for _ in DB_COLUMNS)
            connection.executemany(
                f"INSERT INTO ringkasan_saham_harian ({', '.join(DB_COLUMNS)}) VALUES ({placeholders})",
                result.rows,
            )
        connection.execute(
            """
            INSERT INTO idx_daily_sync_log(
                tanggal, status, attempts, http_status, records_total, row_count,
                payload_sha256, error, fetched_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(tanggal) DO UPDATE SET
                status=excluded.status, attempts=idx_daily_sync_log.attempts+excluded.attempts,
                http_status=excluded.http_status, records_total=excluded.records_total,
                row_count=excluded.row_count, payload_sha256=excluded.payload_sha256,
                error=excluded.error, fetched_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
            """,
            (
                day_text, result.status, result.attempts, result.http_status,
                result.records_total, len(result.rows), result.payload_hash, result.error,
            ),
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Sinkronisasi Ringkasan Saham IDX ke SQLite")
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--delay", type=float, default=0.5, help="Jeda global antar-request (detik)")
    parser.add_argument("--workers", type=int, default=3, help="Jumlah request paralel (maksimum 4)")
    parser.add_argument("--retries", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--refresh-recent", type=int, default=10)
    parser.add_argument("--max-days", type=int, help="Batasi jumlah tanggal untuk satu tahap")
    args = parser.parse_args()

    start, end = parse_day(args.start), parse_day(args.end)
    if start > end:
        parser.error("--start tidak boleh sesudah --end")

    sys.path.insert(0, str(ROOT))
    from db import migrate
    migrate()

    connection = sqlite3.connect(DB_PATH, timeout=60)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 60000")
    days = list(pending_days(connection, start, end, args.refresh_recent))
    if args.max_days:
        days = days[: args.max_days]
    print(f"Database: {DB_PATH}", flush=True)
    print(f"Rentang: {start} s.d. {end}; tanggal yang akan dicek: {len(days)}", flush=True)
    if not days:
        print("Tidak ada tanggal yang perlu disinkronkan.", flush=True)
        connection.close()
        return 0

    worker_count = min(max(args.workers, 1), 4)
    limiter = RateLimiter(args.delay)
    local = threading.local()

    def run_fetch(day: date) -> FetchResult:
        if not hasattr(local, "session"):
            local.session = IdxSession(args.timeout, limiter)
        return fetch_day(local.session, day, max(args.retries, 1))

    success = empty = failed = inserted = 0
    day_iterator = iter(days)
    futures: dict[Future[FetchResult], date] = {}
    completed = 0
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        for _ in range(worker_count):
            try:
                day = next(day_iterator)
            except StopIteration:
                break
            futures[executor.submit(run_fetch, day)] = day
        while futures:
            future = next(as_completed(futures))
            futures.pop(future)
            result = future.result()
            save_result(connection, result)
            completed += 1
            if result.status == "success":
                success += 1
                inserted += len(result.rows)
            elif result.status == "no_data":
                empty += 1
            else:
                failed += 1
            print(
                f"[{completed}/{len(days)}] {result.day} {result.status}: "
                f"{len(result.rows)} baris"
                + (f" ({result.error})" if result.error else ""),
                flush=True,
            )
            try:
                next_day = next(day_iterator)
            except StopIteration:
                continue
            futures[executor.submit(run_fetch, next_day)] = next_day

    connection.execute("PRAGMA optimize")
    connection.commit()
    total = connection.execute("SELECT COUNT(*) FROM ringkasan_saham_harian").fetchone()[0]
    distinct_days = connection.execute(
        "SELECT COUNT(DISTINCT tanggal) FROM ringkasan_saham_harian"
    ).fetchone()[0]
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    connection.close()
    print(
        f"Selesai: sukses={success}, kosong={empty}, gagal={failed}, "
        f"baris tahap ini={inserted}, total_db={total}, hari_db={distinct_days}, "
        f"integrity={integrity}",
        flush=True,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
