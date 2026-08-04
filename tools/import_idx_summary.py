"""Import file Stock Summary / Equity EoD IDX ke zaiden_trader.db.

Gunakan hanya file yang diperoleh secara resmi dari IDX atau penyedia data
berlisensi. Contoh:
  python tools/import_idx_summary.py --input "D:\\IDX-EOD" --recursive
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sqlite3
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "zaiden_trader.db"

COLUMNS = [
    "tanggal", "kode_saham", "nama_perusahaan", "sebelumnya", "harga_pembukaan",
    "perdagangan_pertama", "harga_tertinggi", "harga_terendah", "harga_penutupan",
    "perubahan", "volume", "nilai_transaksi", "frekuensi", "indeks_individual",
    "penawaran_jual", "volume_penawaran_jual", "penawaran_beli", "volume_penawaran_beli",
    "saham_tercatat", "saham_dapat_diperdagangkan", "bobot_indeks", "jual_asing",
    "beli_asing", "tanggal_delisting", "volume_non_reguler", "nilai_non_reguler",
    "frekuensi_non_reguler", "sumber_file",
]

ALIASES = {
    "tanggal": ["date", "trading date", "trade date", "tanggal"],
    "kode_saham": ["code", "ticker", "symbol", "stock code", "kode saham"],
    "nama_perusahaan": ["name", "company", "company name", "nama perusahaan"],
    "sebelumnya": ["previous", "prev", "previous price", "sebelumnya"],
    "harga_pembukaan": ["open", "opening price", "harga pembukaan"],
    "perdagangan_pertama": ["first", "first trade", "perdagangan pertama"],
    "harga_tertinggi": ["high", "highest", "harga tertinggi"],
    "harga_terendah": ["low", "lowest", "harga terendah"],
    "harga_penutupan": ["close", "closing price", "harga penutupan"],
    "perubahan": ["change", "perubahan"],
    "volume": ["volume"],
    "nilai_transaksi": ["value", "trading value", "nilai transaksi"],
    "frekuensi": ["frequency", "freq", "frekuensi"],
    "indeks_individual": ["individual index", "indeks individual"],
    "penawaran_jual": ["offer", "ask", "penawaran jual"],
    "volume_penawaran_jual": ["offer volume", "ask volume", "volume penawaran jual"],
    "penawaran_beli": ["bid", "penawaran beli"],
    "volume_penawaran_beli": ["bid volume", "volume penawaran beli"],
    "saham_tercatat": ["listed shares", "saham tercatat"],
    "saham_dapat_diperdagangkan": ["tradable shares", "saham dapat diperdagangkan"],
    "bobot_indeks": ["index weight", "bobot indeks"],
    "jual_asing": ["foreign sell", "jual asing"],
    "beli_asing": ["foreign buy", "beli asing"],
    "tanggal_delisting": ["delisting date", "tanggal delisting"],
    "volume_non_reguler": ["non regular volume", "volume non reguler"],
    "nilai_non_reguler": ["non regular value", "nilai non reguler"],
    "frekuensi_non_reguler": ["non regular frequency", "frekuensi non reguler"],
}


def normal(value: object) -> str:
    return re.sub(r"\s+", " ", str(value).strip().lower().replace("_", " "))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def number(value: object, integer: bool = False):
    if pd.isna(value) or str(value).strip() in {"", "-", "nan", "None"}:
        return None
    text = str(value).strip().replace(" ", "")
    # IDX exports commonly use dots for digit grouping and commas for decimals.
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    elif text.count(".") > 1:
        text = text.replace(".", "")
    elif re.fullmatch(r"[+-]?\d{1,3}(?:\.\d{3})+", text):
        text = text.replace(".", "")
    result = float(text)
    return int(result) if integer else result


def read_file(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path, sep=None, engine="python")
    if suffix in {".xls", ".xlsx"}:
        return pd.read_excel(path)
    raise ValueError(f"Format tidak didukung: {path.suffix}")


def map_columns(frame: pd.DataFrame) -> pd.DataFrame:
    lookup = {normal(col): col for col in frame.columns}
    selected: dict[str, object] = {}
    for destination, alternatives in ALIASES.items():
        source = next((lookup[normal(name)] for name in alternatives if normal(name) in lookup), None)
        if source is not None:
            selected[destination] = frame[source]
    missing = {"tanggal", "kode_saham"} - set(selected)
    if missing:
        raise ValueError("Kolom wajib tidak ditemukan: " + ", ".join(sorted(missing)))
    result = pd.DataFrame(selected)
    for col in COLUMNS:
        if col not in result:
            result[col] = None
    def parse_date(value: object):
        raw = str(value).strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            return raw
        parsed = pd.to_datetime(raw, dayfirst=True, errors="coerce")
        return None if pd.isna(parsed) else parsed.strftime("%Y-%m-%d")
    result["tanggal"] = result["tanggal"].map(parse_date)
    result["kode_saham"] = result["kode_saham"].astype(str).str.strip().str.upper()
    result = result.dropna(subset=["tanggal"]).query("kode_saham != '' and kode_saham != 'NAN'")
    integer_columns = {"volume", "frekuensi", "volume_penawaran_jual", "volume_penawaran_beli", "saham_tercatat", "saham_dapat_diperdagangkan", "volume_non_reguler", "frekuensi_non_reguler"}
    for col in COLUMNS:
        if col not in {"tanggal", "kode_saham", "nama_perusahaan", "tanggal_delisting"}:
            result[col] = result[col].map(lambda value: number(value, col in integer_columns))
    return result[COLUMNS]


def ensure_schema(connection: sqlite3.Connection) -> None:
    sys.path.insert(0, str(ROOT))
    from db import migrate
    migrate()


def import_one(path: Path, connection: sqlite3.Connection) -> tuple[int, bool]:
    checksum = sha256(path)
    if connection.execute("SELECT 1 FROM riwayat_impor_ringkasan_saham WHERE checksum_sha256 = ?", (checksum,)).fetchone():
        return 0, True
    frame = map_columns(read_file(path))
    frame["sumber_file"] = path.name
    placeholders = ", ".join("?" for _ in COLUMNS)
    updates = ", ".join(f"{col}=excluded.{col}" for col in COLUMNS if col not in {"tanggal", "kode_saham"})
    sql = f"INSERT INTO ringkasan_saham_harian ({', '.join(COLUMNS)}) VALUES ({placeholders}) ON CONFLICT(tanggal, kode_saham) DO UPDATE SET {updates}, diimpor_pada=CURRENT_TIMESTAMP"
    rows = [tuple(None if pd.isna(value) else value for value in row) for row in frame.itertuples(index=False, name=None)]
    connection.executemany(sql, rows)
    connection.execute("INSERT INTO riwayat_impor_ringkasan_saham(nama_file, checksum_sha256, jumlah_baris) VALUES (?, ?, ?)", (path.name, checksum, len(rows)))
    return len(rows), False


def main() -> int:
    parser = argparse.ArgumentParser(description="Impor Stock Summary/EoD IDX ke SQLite")
    parser.add_argument("--input", required=True, help="File CSV/XLS/XLSX atau folder file resmi IDX")
    parser.add_argument("--recursive", action="store_true", help="Cari file di subfolder")
    args = parser.parse_args()
    target = Path(args.input)
    files = [target] if target.is_file() else sorted(target.glob("**/*") if args.recursive else target.glob("*"))
    files = [path for path in files if path.suffix.lower() in {".csv", ".xls", ".xlsx"}]
    if not files:
        print("Tidak ada CSV/XLS/XLSX untuk diimpor.")
        return 2
    with sqlite3.connect(DB_PATH) as connection:
        ensure_schema(connection)
        imported = skipped = 0
        for path in files:
            try:
                count, already_done = import_one(path, connection)
                imported += count
                skipped += int(already_done)
                print(f"{'Lewati' if already_done else 'Impor'}: {path.name} ({count} baris)")
            except Exception as error:
                connection.rollback()
                print(f"Gagal: {path.name}: {error}", file=sys.stderr)
        connection.commit()
    print(f"Selesai. Baris diimpor: {imported}; file sudah pernah diimpor: {skipped}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
