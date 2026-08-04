# Berkontribusi ke Zaiden Trader

Panduan ini untuk kolaborator yang mengembangkan aplikasi bersama lewat GitHub.

## 1. Setup awal

```bash
git clone <url-repo-ini>
cd "Aplikasi Zaiden traider"
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
```

`data/` (termasuk `zaiden_trader.db`) **tidak ikut di-clone** — file ini
sengaja di-`.gitignore` karena berisi data trading pribadi dan ukurannya
>1GB. Setiap kolaborator membangun database lokalnya sendiri:

```bash
python -c "from db import migrate; migrate()"   # buat skema kosong
python tools/sync_idx_daily.py --start 2020-01-01   # tarik data harga IDX
```

Lihat `DATABASE.md` untuk skema lengkap, dan `tools/` untuk script sync
lainnya (fundamental, broker, indeks IHSG).

## 2. Alur kerja: branch + Pull Request

Jangan push langsung ke `main`. Untuk setiap perubahan:

```bash
git checkout main
git pull
git checkout -b nama-fitur-anda      # mis. fix-signal-chart, add-sector-filter

# ...kerjakan perubahan...

git add <file-yang-relevan>          # jangan `git add -A` sembarangan
git commit -m "Deskripsi singkat perubahan"
git push -u origin nama-fitur-anda
```

Lalu buka Pull Request di GitHub dari branch Anda ke `main`. Beri
deskripsi singkat apa yang berubah dan kenapa. Kolaborator lain me-review
sebelum di-merge — ini mencegah dua orang menimpa pekerjaan satu sama lain
tanpa sadar.

## 3. Sebelum membuat PR

```bash
python -m pytest          # semua test harus lulus (scoped ke tests/ via pytest.ini)
```

Tambahkan test baru untuk bug fix atau fitur baru bila memungkinkan — lihat
`tests/` untuk contoh pola yang sudah dipakai (fixture SQLite sintetis,
tidak menyentuh database asli).

## 4. Struktur proyek (ringkas)

| Path | Isi |
|---|---|
| `app.py` | Server HTTP utama (routing semua `/api/...`) |
| `db.py` | Skema database inti (`SCHEMA_SQL`) |
| `ml_weekly/` | Sistem sinyal ML "IDX Weekly High-Confidence" (training, prediksi, evaluasi, laporan PDF) |
| `tools/` | Script sinkronisasi data IDX (harga harian, fundamental, broker, indeks) |
| `assets/` | Frontend (JS/CSS), tanpa framework |
| `tests/` | Test suite (pytest, database sintetis per test) |
| `index.html` | Halaman utama aplikasi |

## 5. Menjalankan aplikasi

Klik dua kali `BUKA-APLIKASI.bat` (Windows) — lihat `README.md` untuk detail.

## 6. Hal-hal penting yang sudah pernah jadi masalah

- **Jangan** pakai `matplotlib`/`mplfinance` untuk grafik apa pun di laporan
  PDF — pada mesin dengan Windows Smart App Control aktif, native
  renderer-nya diblokir total dan gagal secara diam-diam. Grafik PDF
  memakai `reportlab.graphics` (vector shapes murni, lihat
  `ml_weekly/pdf_report.py`).
- Kalau menambah parameter baru ke fungsi prediksi (`_assemble_predictions`
  dkk.), pastikan pemanggilnya benar-benar meneruskan nilai dari
  `config.py`/`app_settings` — jangan asumsikan default di signature fungsi
  otomatis konsisten dengan config; pernah terjadi parameter diam-diam
  terlewat dan memakai default lama meski config sudah diubah.
- Selalu install package baru ke `.venv` (bukan Python global) — lihat
  catatan di `requirements.txt`.
