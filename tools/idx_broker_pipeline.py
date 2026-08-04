"""IDX broker pipeline for Zaiden Trader SQLite database.

This script pulls broker master and broker daily summary from public IDX endpoints
and writes them into dedicated tables in data/zaiden_trader.db.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "zaiden_trader.db"
DEFAULT_CHECKPOINT = ROOT / "data" / ".idx_broker_summary_checkpoint.json"

IDX_MASTER_PAGE = "https://www.idx.co.id/id/anggota-bursa-dan-partisipan/profil-anggota-bursa"
IDX_SUMMARY_PAGE = "https://www.idx.co.id/id/data-pasar/ringkasan-perdagangan/ringkasan-broker"
IDX_MASTER_URL = "https://www.idx.co.id/primary/ExchangeMember/GetBrokerSearch?start=0&length=9999"
IDX_SUMMARY_URL_TEMPLATE = "https://www.idx.co.id/primary/TradingSummary/GetBrokerSummary?start=0&length=9999&date={date}"

LOG = logging.getLogger("idx-broker-pipeline")


def normalize_code(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper().strip())


def parse_int(value: Any) -> int:
    if value in (None, "", "-", "null"):
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(round(value))
    text = str(value).strip()
    text = re.sub(r"(?i)rp|idr|lot|lembar|x", "", text)
    text = text.replace("\u00a0", "").replace(" ", "")
    text = re.sub(r"[^0-9,.-]", "", text)
    if not text:
        return 0
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        parts = text.split(",")
        text = "".join(parts) if len(parts[-1]) == 3 and len(parts) > 1 else text.replace(",", ".")
    elif "." in text:
        parts = text.split(".")
        if len(parts[-1]) == 3 and len(parts) > 1:
            text = "".join(parts)
    try:
        return int(round(float(text)))
    except ValueError:
        return 0


def parse_date_value(value: Any, fallback: date | None = None) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value in (None, ""):
        return fallback
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return fallback


def iter_weekdays(start: date, end: date):
    current = start
    while current <= end:
        if current.weekday() < 5:
            yield current
        current += timedelta(days=1)


def previous_weekday(day: date) -> date:
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def connect_db(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 60000")
    return connection


def ensure_tables(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS master_broker (
            kode_broker TEXT PRIMARY KEY,
            nama_broker TEXT NOT NULL,
            izin TEXT,
            status TEXT,
            sumber TEXT NOT NULL DEFAULT 'IDX',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS ringkasan_broker_harian (
            tanggal TEXT NOT NULL CHECK (date(tanggal) = tanggal),
            kode_broker TEXT NOT NULL,
            volume INTEGER NOT NULL DEFAULT 0,
            nilai INTEGER NOT NULL DEFAULT 0,
            frekuensi INTEGER NOT NULL DEFAULT 0,
            buy_volume INTEGER,
            buy_value INTEGER,
            buy_frequency INTEGER,
            sell_volume INTEGER,
            sell_value INTEGER,
            sell_frequency INTEGER,
            sumber TEXT NOT NULL DEFAULT 'IDX',
            source_file TEXT,
            imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (tanggal, kode_broker),
            FOREIGN KEY (kode_broker)
                REFERENCES master_broker(kode_broker)
                ON UPDATE CASCADE
                ON DELETE RESTRICT
        );

        CREATE TABLE IF NOT EXISTS broker_saham_harian (
            tanggal TEXT NOT NULL CHECK (date(tanggal) = tanggal),
            kode_saham TEXT NOT NULL COLLATE NOCASE,
            kode_broker TEXT NOT NULL,
            buy_lot INTEGER NOT NULL DEFAULT 0,
            buy_value INTEGER NOT NULL DEFAULT 0,
            buy_avg REAL NOT NULL DEFAULT 0,
            sell_lot INTEGER NOT NULL DEFAULT 0,
            sell_value INTEGER NOT NULL DEFAULT 0,
            sell_avg REAL NOT NULL DEFAULT 0,
            net_lot INTEGER NOT NULL DEFAULT 0,
            net_value INTEGER NOT NULL DEFAULT 0,
            sumber TEXT NOT NULL DEFAULT 'IMPORT',
            source_file TEXT,
            imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (tanggal, kode_saham, kode_broker),
            FOREIGN KEY (kode_broker)
                REFERENCES master_broker(kode_broker)
                ON UPDATE CASCADE
                ON DELETE RESTRICT
        );

        CREATE INDEX IF NOT EXISTS idx_ringkasan_broker_tanggal
            ON ringkasan_broker_harian(tanggal DESC);
        CREATE INDEX IF NOT EXISTS idx_ringkasan_broker_kode_tanggal
            ON ringkasan_broker_harian(kode_broker, tanggal DESC);
        """
    )
    connection.commit()


def build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
            "Referer": "https://www.idx.co.id/",
            "X-Requested-With": "XMLHttpRequest",
        }
    )
    try:
        session.get("https://www.idx.co.id/id", timeout=15)
    except Exception:
        pass
    return session


def get_json(session: requests.Session, url: str, timeout: int = 45) -> Any:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    return response.json()


def extract_record_list(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if not isinstance(payload, Mapping):
        return []
    for key in ("data", "Data", "results", "Results", "result", "Result", "aaData", "items", "Items"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, Mapping)]
        if isinstance(value, Mapping):
            nested = extract_record_list(value)
            if nested:
                return nested
    for value in payload.values():
        if isinstance(value, (list, Mapping)):
            nested = extract_record_list(value)
            if nested:
                return nested
    return []


def pick(row: Mapping[str, Any], aliases: Iterable[str], default: Any = None) -> Any:
    lowered = {str(k).strip().lower(): v for k, v in row.items()}
    for alias in aliases:
        value = lowered.get(alias.lower())
        if value not in (None, ""):
            return value
    return default


def upsert_master_rows(connection: sqlite3.Connection, rows: Iterable[Mapping[str, Any]], source: str) -> int:
    total = 0
    for row in rows:
        code = normalize_code(pick(row, ["kode_broker", "broker_code", "code", "idfirm", "firmcode", "kode"]))
        name = str(pick(row, ["nama_broker", "broker_name", "name", "firmname", "nama"], "")).strip()
        if not code or not name:
            continue
        izin = pick(row, ["izin", "license", "licence", "permission"])
        status = pick(row, ["status", "operationalstatus", "isactive"])
        connection.execute(
            """
            INSERT INTO master_broker (kode_broker, nama_broker, izin, status, sumber, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(kode_broker) DO UPDATE SET
                nama_broker = excluded.nama_broker,
                izin = COALESCE(excluded.izin, master_broker.izin),
                status = COALESCE(excluded.status, master_broker.status),
                sumber = excluded.sumber,
                updated_at = CURRENT_TIMESTAMP
            """,
            (code, name, None if izin in (None, "") else str(izin).strip(), None if status in (None, "") else str(status).strip(), source),
        )
        total += 1
    connection.commit()
    return total


def ensure_broker(connection: sqlite3.Connection, code: str, name: str | None = None) -> None:
    code = normalize_code(code)
    if not code:
        return
    name = (name or f"Broker {code}").strip()
    connection.execute(
        """
        INSERT INTO master_broker (kode_broker, nama_broker, sumber, updated_at)
        VALUES (?, ?, 'AUTO', CURRENT_TIMESTAMP)
        ON CONFLICT(kode_broker) DO UPDATE SET
            nama_broker = CASE
                WHEN excluded.nama_broker NOT LIKE 'Broker %' THEN excluded.nama_broker
                ELSE master_broker.nama_broker
            END,
            updated_at = CURRENT_TIMESTAMP
        """,
        (code, name),
    )


def upsert_summary_rows(connection: sqlite3.Connection, rows: Iterable[Mapping[str, Any]], fallback_date: date) -> int:
    total = 0
    for row in rows:
        trade_date = parse_date_value(pick(row, ["tanggal", "trade_date", "date", "tradingdate"]), fallback=fallback_date)
        code = normalize_code(pick(row, ["kode_broker", "broker_code", "code", "idfirm", "firmcode", "kode"]))
        name = pick(row, ["nama_broker", "broker_name", "name", "firmname", "nama"])
        if not trade_date or not code:
            continue

        ensure_broker(connection, code, None if name in (None, "") else str(name))
        connection.execute(
            """
            INSERT INTO ringkasan_broker_harian (
                tanggal, kode_broker, volume, nilai, frekuensi,
                buy_volume, buy_value, buy_frequency,
                sell_volume, sell_value, sell_frequency,
                sumber, source_file, imported_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'IDX_ONLINE', NULL, CURRENT_TIMESTAMP)
            ON CONFLICT(tanggal, kode_broker) DO UPDATE SET
                volume = excluded.volume,
                nilai = excluded.nilai,
                frekuensi = excluded.frekuensi,
                buy_volume = COALESCE(excluded.buy_volume, ringkasan_broker_harian.buy_volume),
                buy_value = COALESCE(excluded.buy_value, ringkasan_broker_harian.buy_value),
                buy_frequency = COALESCE(excluded.buy_frequency, ringkasan_broker_harian.buy_frequency),
                sell_volume = COALESCE(excluded.sell_volume, ringkasan_broker_harian.sell_volume),
                sell_value = COALESCE(excluded.sell_value, ringkasan_broker_harian.sell_value),
                sell_frequency = COALESCE(excluded.sell_frequency, ringkasan_broker_harian.sell_frequency),
                sumber = excluded.sumber,
                imported_at = CURRENT_TIMESTAMP
            """,
            (
                trade_date.isoformat(),
                code,
                parse_int(pick(row, ["volume", "total_volume", "totalvolume", "vol"])),
                parse_int(pick(row, ["nilai", "value", "total_value", "totalvalue", "val"])),
                parse_int(pick(row, ["frekuensi", "frequency", "total_frequency", "totalfrequency", "freq"])),
                parse_int(pick(row, ["buy_volume", "buyvolume", "bvolume", "bvol", "volume_beli"])),
                parse_int(pick(row, ["buy_value", "buyvalue", "bvalue", "bval", "nilai_beli"])),
                parse_int(pick(row, ["buy_frequency", "buyfrequency", "bfrequency", "bfreq", "frekuensi_beli"])),
                parse_int(pick(row, ["sell_volume", "sellvolume", "svolume", "svol", "volume_jual"])),
                parse_int(pick(row, ["sell_value", "sellvalue", "svalue", "sval", "nilai_jual"])),
                parse_int(pick(row, ["sell_frequency", "sellfrequency", "sfrequency", "sfreq", "frekuensi_jual"])),
            ),
        )
        total += 1
    connection.commit()
    return total


def fetch_master(connection: sqlite3.Connection) -> int:
    LOG.info("Fetch master broker dari IDX: %s", IDX_MASTER_PAGE)
    session = build_session()
    payload = get_json(session, IDX_MASTER_URL)
    rows = extract_record_list(payload)
    if not rows:
        raise RuntimeError("Respons IDX master broker kosong/tidak dikenali.")
    count = upsert_master_rows(connection, rows, "IDX_ONLINE")
    LOG.info("Master broker upsert: %s baris", count)
    return count


def load_checkpoint(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        values = payload.get("completed_dates", [])
        return {str(value) for value in values}
    except (OSError, ValueError, TypeError):
        LOG.warning("Checkpoint broker tidak terbaca, mulai tanpa resume.")
        return set()


def save_checkpoint(path: Path, completed: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(
            {
                "completed_dates": sorted(completed),
                "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    tmp.replace(path)


def fetch_summary(
    connection: sqlite3.Connection,
    start: date,
    end: date,
    delay: float,
    checkpoint_path: Path,
    resume: bool,
    max_days: int | None,
) -> tuple[int, int, int, int, int]:
    LOG.info("Fetch ringkasan broker IDX: %s s.d. %s", start, end)
    LOG.info("Halaman resmi: %s", IDX_SUMMARY_PAGE)
    session = build_session()
    inserted = 0
    success_days = 0
    empty_days = 0
    failed_days = 0
    completed = load_checkpoint(checkpoint_path) if resume else set()
    days = [day for day in iter_weekdays(start, end) if day.isoformat() not in completed]
    if max_days and max_days > 0:
        days = days[:max_days]
    if not days:
        LOG.info("Tidak ada tanggal baru untuk diproses (checkpoint aktif).")
        return inserted, success_days, empty_days, 0, failed_days

    for day in days:
        day_key = day.isoformat()
        url = IDX_SUMMARY_URL_TEMPLATE.format(date=day.strftime("%Y%m%d"))
        try:
            payload = get_json(session, url)
            rows = extract_record_list(payload)
            if not rows:
                empty_days += 1
                LOG.info("%s: tidak ada data", day)
            else:
                count = upsert_summary_rows(connection, rows, day)
                inserted += count
                success_days += 1
                LOG.info("%s: %s baris", day, count)
            completed.add(day_key)
            save_checkpoint(checkpoint_path, completed)
        except Exception as error:  # noqa: BLE001
            failed_days += 1
            LOG.error("%s: gagal tarik (%s)", day, error)
        if delay > 0:
            import time
            time.sleep(delay)
    return inserted, success_days, empty_days, len(days), failed_days


def print_stats(connection: sqlite3.Connection) -> None:
    for table in ("master_broker", "ringkasan_broker_harian", "broker_saham_harian"):
        total = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"{table}: {total:,} baris")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="IDX Broker data pipeline -> zaiden_trader.db")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Pastikan tabel broker tersedia")
    sub.add_parser("fetch-master", help="Tarik master broker IDX")

    fs = sub.add_parser("fetch-idx-summary", help="Tarik ringkasan broker harian IDX")
    fs.add_argument("--start")
    fs.add_argument("--end")
    fs.add_argument("--years", type=int, default=1)
    fs.add_argument("--delay", type=float, default=1.2)
    fs.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    fs.add_argument("--no-resume", action="store_true")
    fs.add_argument("--max-days", type=int)

    all_cmd = sub.add_parser("all", help="Init + fetch master + fetch summary")
    all_cmd.add_argument("--start")
    all_cmd.add_argument("--end")
    all_cmd.add_argument("--years", type=int, default=1)
    all_cmd.add_argument("--delay", type=float, default=1.2)
    all_cmd.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    all_cmd.add_argument("--no-resume", action="store_true")
    all_cmd.add_argument("--max-days", type=int)

    sub.add_parser("stats", help="Tampilkan statistik tabel broker")
    return parser


def resolve_range(start_raw: str | None, end_raw: str | None, years: int) -> tuple[date, date]:
    end = parse_date_value(end_raw) or previous_weekday(date.today())
    start = parse_date_value(start_raw)
    if start is None:
        try:
            start = end.replace(year=end.year - years)
        except ValueError:
            start = end.replace(month=2, day=28, year=end.year - years)
    if start > end:
        raise ValueError("Tanggal awal tidak boleh setelah tanggal akhir")
    return start, end


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    connection = connect_db(args.db)
    try:
        ensure_tables(connection)
        if args.command == "init":
            print(f"Tabel broker siap di: {args.db.resolve()}")
        elif args.command == "fetch-master":
            fetch_master(connection)
        elif args.command == "fetch-idx-summary":
            start, end = resolve_range(args.start, args.end, args.years)
            rows, days, empty, checked, failed = fetch_summary(
                connection,
                start,
                end,
                args.delay,
                args.checkpoint,
                not args.no_resume,
                args.max_days,
            )
            print(
                f"Selesai: {rows:,} baris; {days} hari berisi data; {empty} hari kosong; "
                f"{failed} hari gagal; diproses {checked} hari"
            )
        elif args.command == "all":
            try:
                fetch_master(connection)
            except Exception as error:  # noqa: BLE001
                LOG.warning("Fetch master broker gagal, lanjut pakai data yang ada: %s", error)
            start, end = resolve_range(args.start, args.end, args.years)
            rows, days, empty, checked, failed = fetch_summary(
                connection,
                start,
                end,
                args.delay,
                args.checkpoint,
                not args.no_resume,
                args.max_days,
            )
            print(
                f"Selesai all: {rows:,} baris; {days} hari berisi data; {empty} hari kosong; "
                f"{failed} hari gagal; diproses {checked} hari"
            )
        elif args.command == "stats":
            print_stats(connection)
        else:
            parser.error("Perintah tidak dikenali")
        return 0
    except Exception as exc:
        LOG.error("Gagal: %s", exc)
        return 1
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
