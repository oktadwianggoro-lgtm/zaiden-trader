"""
chat.py — Zaiden AI Chat Engine
Menggunakan Google Gemini untuk menjawab pertanyaan bebas tentang data pasar saham.
Pipeline: Natural Language → SQL → Execute → Explain
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from typing import Any

from db import DB_PATH

# ── State ────────────────────────────────────────────────────────────────────
_GEMINI_KEY: str | None = None
_KEY_LOCK   = threading.Lock()

# ── Database Schema Context (untuk prompt) ────────────────────────────────────
_SCHEMA_CONTEXT = """
Kamu adalah analis saham Indonesia yang ahli dalam SQL dan pasar modal.
Kamu memiliki akses ke database SQLite dengan tabel-tabel berikut:

=== TABEL UTAMA ===

1. ringkasan_saham_harian — Data harian semua saham IDX (1.3 juta baris, 2020–sekarang)
   Kolom penting:
   - tanggal TEXT          : format 'YYYY-MM-DD'
   - kode_saham TEXT       : kode emiten (misal 'BBCA', 'BUMI', 'TLKM')
   - nama_perusahaan TEXT  : nama lengkap perusahaan
   - sebelumnya REAL       : harga penutupan hari sebelumnya
   - harga_penutupan REAL  : harga penutupan hari ini
   - perubahan REAL        : perubahan harga absolut (harga_penutupan - sebelumnya)
   - harga_tertinggi REAL  : harga tertinggi
   - harga_terendah REAL   : harga terendah
   - harga_pembukaan REAL  : harga pembukaan
   - volume INTEGER        : volume perdagangan (lot)
   - nilai_transaksi REAL  : nilai transaksi total (Rupiah)
   - frekuensi INTEGER     : jumlah transaksi
   - beli_asing REAL       : volume beli investor asing (lot)
   - jual_asing REAL       : volume jual investor asing (lot)
   NET ASING (beli - jual): (beli_asing - jual_asing) — positif = net buy asing
   PERSEN PERUBAHAN: ROUND((perubahan / sebelumnya) * 100, 2) WHERE sebelumnya > 0

2. ringkasan_broker_harian — Aktivitas broker per hari (76.500 baris, 2020–sekarang)
   - tanggal TEXT          : format 'YYYY-MM-DD'
   - kode_broker TEXT      : kode broker (misal 'XL'=Stockbit, 'BK'=JP Morgan, 'AK'=UBS)
   - nilai INTEGER         : total nilai transaksi broker (Rupiah)
   - volume INTEGER        : total volume
   - frekuensi INTEGER     : total frekuensi

3. master_broker — Daftar broker (107 broker)
   - kode_broker TEXT      : kode broker
   - nama_broker TEXT      : nama broker

4. ownership_positions — Kepemilikan saham >1% (35.995 baris)
   - record_date TEXT      : tanggal data ('YYYY-MM-DD')
   - share_code TEXT       : kode saham
   - issuer_name TEXT      : nama emiten
   - investor_name TEXT    : nama investor/pemegang saham
   - local_foreign TEXT    : 'L'=Lokal, 'F'=Asing
   - classification TEXT   : tipe investor (CP=Perusahaan, IN=Individu, MF=Reksa Dana, dll)
   - scripless INTEGER     : jumlah saham scripless
   - percentage REAL       : persentase kepemilikan

5. idx_stocks — Master data saham (963 saham)
   - code TEXT             : kode saham
   - company_name TEXT     : nama perusahaan
   - listing_board TEXT    : papan (Main/Development/New Economy/Acceleration)
   - shares INTEGER        : total saham beredar
   - listing_date TEXT     : tanggal listing

=== ATURAN PENTING ===
- Selalu gunakan LIMIT maksimal 100 baris pada hasil akhir
- Untuk mencari tanggal terbaru: SELECT MAX(tanggal) FROM ringkasan_saham_harian
- Untuk range N hari: WHERE tanggal >= date(MAX(tanggal), '-N days')
- Nama kode_broker XL = Stockbit, BK = JP Morgan, CC = CGS CIMB, AK = UBS, YP = Mandiri Sekuritas
- Hanya gunakan SELECT statement, jangan gunakan INSERT/UPDATE/DELETE/DROP
- Hindari subquery yang terlalu dalam, lebih baik pakai CTE (WITH ... AS ...)
"""

_SAFE_PATTERN = re.compile(
    r'\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|REPLACE|ATTACH|DETACH|PRAGMA)\b',
    re.IGNORECASE
)


def set_api_key(key: str) -> None:
    global _GEMINI_KEY
    with _KEY_LOCK:
        _GEMINI_KEY = key.strip() if key else None


def get_api_key() -> str | None:
    with _KEY_LOCK:
        return _GEMINI_KEY


def is_configured() -> bool:
    with _KEY_LOCK:
        return bool(_GEMINI_KEY)


def _call_gemini(prompt: str, system: str = "") -> str:
    """Call Gemini API and return text response."""
    import urllib.request
    key = get_api_key()
    if not key:
        raise RuntimeError("Gemini API key belum dikonfigurasi.")

    # Use gemini-2.0-flash-lite for speed & cost
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-lite:generateContent?key={key}"

    contents = []
    if system:
        contents.append({"role": "user", "parts": [{"text": system}]})
        contents.append({"role": "model", "parts": [{"text": "Baik, saya mengerti instruksi dan skema database-nya."}]})
    contents.append({"role": "user", "parts": [{"text": prompt}]})

    body = json.dumps({
        "contents": contents,
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 4096,
            "responseMimeType": "text/plain",
        }
    }).encode("utf-8")

    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")

    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())

    candidates = data.get("candidates", [])
    if not candidates:
        error_msg = data.get("error", {}).get("message", "Tidak ada respons dari Gemini.")
        raise RuntimeError(f"Gemini error: {error_msg}")

    return candidates[0]["content"]["parts"][0]["text"].strip()


def _validate_sql(sql: str) -> tuple[bool, str]:
    """Returns (is_safe, cleaned_sql)."""
    sql = sql.strip()
    # Strip markdown code block if any
    sql = re.sub(r'^```(?:sql)?\s*', '', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\s*```$', '', sql)
    sql = sql.strip().rstrip(';')

    if _SAFE_PATTERN.search(sql):
        return False, "Query mengandung operasi yang tidak diizinkan (non-SELECT)."
    if not sql.upper().startswith("SELECT") and not sql.upper().startswith("WITH"):
        return False, "Query harus dimulai dengan SELECT atau WITH."
    return True, sql


def _execute_sql(sql: str) -> tuple[list[str], list[list[Any]]]:
    """Execute SQL and return (columns, rows). Read-only."""
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute(sql)
        rows_raw = cur.fetchmany(200)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = [list(r) for r in rows_raw]
        return cols, rows
    finally:
        con.close()


def _build_sql_prompt(question: str, history: list[dict]) -> str:
    history_text = ""
    if history:
        last = history[-3:]  # last 3 exchanges for context
        for h in last:
            role = "User" if h["role"] == "user" else "AI"
            history_text += f"{role}: {h['content'][:200]}\n"

    return f"""Tugas: Buat satu query SQL SQLite yang menjawab pertanyaan berikut.

{f"Konteks percakapan sebelumnya:{chr(10)}{history_text}" if history_text else ""}

Pertanyaan: {question}

ATURAN OUTPUT:
- Hanya tulis SATU query SQL yang valid untuk SQLite
- Jangan tambahkan penjelasan apapun, hanya SQL
- Gunakan LIMIT maksimal 50 untuk hasil akhir
- Untuk persentase perubahan harga: ROUND((perubahan / NULLIF(sebelumnya, 0)) * 100, 2)
- Untuk NET ASING: (beli_asing - jual_asing) sebagai net_beli_asing

Tulis SQL:"""


def _build_explain_prompt(question: str, sql: str, cols: list, rows: list) -> str:
    # Summarize data for prompt (max 30 rows to avoid token limit)
    sample = rows[:30]
    data_text = ""
    if cols and sample:
        header = " | ".join(cols)
        separator = "-" * len(header)
        data_text = f"{header}\n{separator}\n"
        for row in sample:
            data_text += " | ".join(str(v) if v is not None else "—" for v in row) + "\n"
        if len(rows) > 30:
            data_text += f"... dan {len(rows)-30} baris lagi\n"

    return f"""Kamu adalah analis pasar modal Indonesia yang cerdas, komunikatif, dan profesional.

Pertanyaan pengguna: "{question}"

Query SQL yang dijalankan:
```sql
{sql}
```

Hasil data ({len(rows)} baris):
{data_text if data_text else "(Tidak ada data ditemukan)"}

Berikan respons dalam format JSON yang PERSIS seperti berikut (jangan tambahkan apapun di luar JSON):

{{
  "summary": "Rangkuman jawaban singkat 1-2 kalimat yang langsung menjawab pertanyaan",
  "insight": "Analisis mendalam 2-4 paragraf tentang data ini, temuan menarik, pola, anomali, dan konteks pasar Indonesia. Gunakan Bahasa Indonesia yang komunikatif dan profesional. Sebutkan angka-angka kunci.",
  "chart_type": "bar|line|none — pilih bar untuk ranking/perbandingan, line untuk tren waktu, none jika tidak relevan",
  "chart_x_col": "nama kolom yang dijadikan sumbu X / label (string)",
  "chart_y_col": "nama kolom yang dijadikan sumbu Y / nilai (numerik)",
  "chart_title": "Judul grafik yang deskriptif",
  "suggestions": ["pertanyaan lanjutan yang relevan 1", "pertanyaan lanjutan yang relevan 2", "pertanyaan lanjutan yang relevan 3"],
  "has_data": true
}}

Jika tidak ada data, gunakan has_data: false dan isi summary dengan penjelasan mengapa data tidak ditemukan."""


def chat_query(question: str, history: list[dict] | None = None) -> dict:
    """
    Main entry point. Returns:
    {
      sql, columns, rows, summary, insight,
      chart_type, chart_x_col, chart_y_col, chart_title,
      suggestions, has_data, error
    }
    """
    if history is None:
        history = []

    result: dict = {
        "sql": None, "columns": [], "rows": [],
        "summary": "", "insight": "",
        "chart_type": "none", "chart_x_col": "", "chart_y_col": "", "chart_title": "",
        "suggestions": [], "has_data": False, "error": None,
    }

    try:
        # ── Step 1: Generate SQL ─────────────────────────────────────────────
        sql_prompt = _build_sql_prompt(question, history)
        sql_raw = _call_gemini(sql_prompt, system=_SCHEMA_CONTEXT)

        ok, sql = _validate_sql(sql_raw)
        if not ok:
            # Retry once with explicit instructions
            retry_prompt = f"""Query sebelumnya tidak valid. Coba lagi.\nPastikan hanya SELECT statement.\nPertanyaan: {question}\nTulis hanya SQL:"""
            sql_raw2 = _call_gemini(retry_prompt, system=_SCHEMA_CONTEXT)
            ok2, sql = _validate_sql(sql_raw2)
            if not ok2:
                raise ValueError(f"Tidak dapat menghasilkan SQL yang valid: {sql}")

        result["sql"] = sql

        # ── Step 2: Execute SQL ──────────────────────────────────────────────
        try:
            cols, rows = _execute_sql(sql)
        except Exception as db_err:
            # Ask Gemini to fix SQL
            fix_prompt = f"""SQL berikut error: {db_err}\nSQL: {sql}\nPertanyaan: {question}\nPerbaiki SQL-nya:"""
            sql_fixed_raw = _call_gemini(fix_prompt, system=_SCHEMA_CONTEXT)
            _, sql_fixed = _validate_sql(sql_fixed_raw)
            result["sql"] = sql_fixed
            cols, rows = _execute_sql(sql_fixed)

        result["columns"] = cols
        result["rows"] = rows

        # ── Step 3: Explain ──────────────────────────────────────────────────
        explain_prompt = _build_explain_prompt(question, result["sql"], cols, rows)
        explain_raw = _call_gemini(explain_prompt)

        # Parse JSON from response
        json_match = re.search(r'\{[\s\S]*\}', explain_raw)
        if json_match:
            try:
                explain = json.loads(json_match.group())
                result["summary"]    = explain.get("summary", "")
                result["insight"]    = explain.get("insight", "")
                result["chart_type"] = explain.get("chart_type", "none")
                result["chart_x_col"]= explain.get("chart_x_col", "")
                result["chart_y_col"]= explain.get("chart_y_col", "")
                result["chart_title"]= explain.get("chart_title", "")
                result["suggestions"]= explain.get("suggestions", [])
                result["has_data"]   = explain.get("has_data", bool(rows))
            except json.JSONDecodeError:
                result["summary"] = explain_raw[:300]
                result["has_data"] = bool(rows)
        else:
            result["summary"] = explain_raw[:300]
            result["has_data"] = bool(rows)

    except Exception as e:
        result["error"] = str(e)
        result["summary"] = f"Terjadi kesalahan: {e}"

    return result
