"""Sinkronkan Ringkasan Indeks harian IDX (IHSG/COMPOSITE, LQ45, dst) ke SQLite.

Sumber resmi (endpoint yang sama yang menyuplai kartu index di idx.co.id):
https://www.idx.co.id/primary/TradingSummary/GetIndexSummary?date=YYYYMMDD

Proses aman dilanjutkan: setiap tanggal dicatat di idx_index_sync_log dan setiap
snapshot harian ditulis dalam satu transaksi SQLite. Mengikuti pola yang sama
persis dengan tools/sync_idx_daily.py agar konsisten (rate limiter, exponential
backoff, upsert idempoten by (tanggal, index_code)).
"""
from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
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
# length=200/start=0 override the endpoint's default DataTables page size of 10 —
# without it, only the first 10 of ~45 index codes come back (recordsTotal still
# reports the true total, so a naive total==len(data) check silently misses this).
ENDPOINT = "https://www.idx.co.id/primary/TradingSummary/GetIndexSummary?date={}&length=200&start=0"

# IDX baru mulai menyediakan data pada endpoint ini sejak awal Januari 2020;
# tanggal-tanggal sebelumnya konsisten mengembalikan recordsTotal=0.
EARLIEST_AVAILABLE = date(2020, 1, 1)

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
    "tanggal", "index_code", "previous", "highest", "lowest", "close", "change",
    "volume", "value", "frequency", "number_of_stock", "market_capital",
]


@dataclass
class FetchResult:
    day: date
    status: str
    rows: list[tuple[Any, ...]]
    records_total: int
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


def numeric(item: dict[str, Any], key: str) -> float | None:
    value = item.get(key)
    if value is None or value == "":
        return None
    return float(value)


def row_from_item(item: dict[str, Any], requested_day: date) -> tuple[Any, ...]:
    code = str(item.get("IndexCode") or "").strip().upper()
    if not code:
        raise ValueError("IndexCode kosong")
    raw_date = str(item.get("Date") or "").strip()
    if raw_date:
        parsed = datetime.fromisoformat(raw_date.replace("Z", "+00:00")).date()
        if parsed != requested_day:
            raise ValueError(f"Tanggal record {parsed} tidak sama dengan request {requested_day}")
    return (
        requested_day.isoformat(), code,
        numeric(item, "Previous"), numeric(item, "Highest"), numeric(item, "Lowest"),
        numeric(item, "Close"), numeric(item, "Change"), numeric(item, "Volume"),
        numeric(item, "Value"), numeric(item, "Frequency"),
        numeric(item, "NumberOfStock"), numeric(item, "MarketCapital"),
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
            # IDX's own archive occasionally repeats a row verbatim for one
            # index_code on a given date (seen e.g. 2021-08-06: same values,
            # different internal IndexSummaryID). That's a harmless duplicate,
            # not a conflict — dedupe silently. Only raise if two rows for the
            # same code carry DIFFERENT values, which would be a real problem.
            by_code: dict[str, tuple] = {}
            for row in rows:
                code = row[1]
                if code in by_code and by_code[code] != row:
                    raise ValueError(f"Kode indeks {code} punya dua data berbeda pada tanggal yang sama")
                by_code[code] = row
            rows = list(by_code.values())
            return FetchResult(
                day, "success" if rows else "no_data", rows, total, attempt, status,
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
    return FetchResult(day, "failed", [], 0, retries, last_status, last_error)


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
            "SELECT tanggal, status FROM idx_index_sync_log WHERE tanggal BETWEEN ? AND ?",
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
            placeholders = ", ".join("?" for _ in DB_COLUMNS)
            update_cols = ", ".join(
                f"{c}=excluded.{c}" for c in DB_COLUMNS if c not in ("tanggal", "index_code")
            )
            connection.executemany(
                f"""INSERT INTO idx_index_daily ({', '.join(DB_COLUMNS)}, fetched_at, updated_at)
                    VALUES ({placeholders}, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT(tanggal, index_code) DO UPDATE SET
                        {update_cols}, updated_at=CURRENT_TIMESTAMP""",
                result.rows,
            )
        connection.execute(
            """
            INSERT INTO idx_index_sync_log(
                tanggal, status, attempts, http_status, index_count, error, fetched_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(tanggal) DO UPDATE SET
                status=excluded.status, attempts=idx_index_sync_log.attempts+excluded.attempts,
                http_status=excluded.http_status, index_count=excluded.index_count,
                error=excluded.error, fetched_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
            """,
            (
                day_text, result.status, result.attempts, result.http_status,
                len(result.rows), result.error,
            ),
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Sinkronisasi Ringkasan Indeks IDX (IHSG dst) ke SQLite")
    parser.add_argument("--start", default=EARLIEST_AVAILABLE.isoformat())
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
                f"{len(result.rows)} indeks"
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
    total = connection.execute("SELECT COUNT(*) FROM idx_index_daily").fetchone()[0]
    distinct_days = connection.execute(
        "SELECT COUNT(DISTINCT tanggal) FROM idx_index_daily WHERE index_code = 'COMPOSITE'"
    ).fetchone()[0]
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    connection.close()
    print(
        f"Selesai: sukses={success}, kosong={empty}, gagal={failed}, "
        f"baris tahap ini={inserted}, total_db={total}, hari_ihsg_db={distinct_days}, "
        f"integrity={integrity}",
        flush=True,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
