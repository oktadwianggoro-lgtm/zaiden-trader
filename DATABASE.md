# Struktur Database

Database menggunakan SQLite dan berada di `data/zaiden_trader.db`. Foreign key dan validasi juga diterapkan di tingkat database, bukan hanya di formulir HTML.

## Tabel `ringkasan_saham_harian`

Tabel ini menyimpan satu saham untuk satu tanggal bursa, dengan kunci utama
`tanggal + kode_saham`. Ini adalah lokasi tunggal untuk seluruh data Stock
Summary / Equity EoD IDX dari 2020 dan tahun berikutnya. Kolomnya mencakup
OHLC, volume, nilai/frekuensi transaksi, antrian bid/offer, saham tercatat,
dan transaksi asing.

Impor file resmi IDX (CSV, XLS, atau XLSX) dengan:

```powershell
python tools\import_idx_summary.py --input "D:\folder-file-eod" --recursive
```

File yang sama tidak akan masuk dua kali: checksum dan riwayatnya disimpan di
`riwayat_impor_ringkasan_saham`. Data dengan tanggal+saham yang sudah ada akan
diperbarui (upsert).

### Sinkronisasi langsung dari halaman IDX

Data historis dari halaman Ringkasan Saham resmi IDX dapat dilanjutkan kapan
saja dengan:

```powershell
python tools\sync_idx_daily.py
```

Sinkronisasi menggunakan endpoint yang dipakai halaman IDX, memvalidasi setiap
snapshot, lalu menulisnya secara atomik ke `ringkasan_saham_harian`. Status per
tanggal disimpan di `idx_daily_sync_log`, sehingga proses aman dijalankan ulang:
tanggal lama dilewati, sedangkan beberapa tanggal terbaru diperiksa kembali.

Hasil sinkronisasi 12 Juli 2026:

- rentang data: 2 Januari 2020–10 Juli 2026;
- 1.318.656 baris dari 1.567 hari bursa;
- 136 hari kerja tanpa data perdagangan;
- 0 duplikat, 0 tanggal gagal, dan pemeriksaan integritas SQLite `ok`.

## Tabel `idx_stocks`

| Kolom | Tipe | Keterangan |
|---|---|---|
| `id` | INTEGER | Primary key |
| `code` | TEXT | Kode saham unik, case-insensitive |
| `company_name` | TEXT | Nama perusahaan |
| `listing_date` | TEXT | Tanggal listing ISO `YYYY-MM-DD` |
| `shares` | INTEGER | Jumlah saham beredar, tidak boleh negatif |
| `listing_board` | TEXT | Papan pencatatan |
| `created_at` | TEXT | Waktu dibuat |
| `updated_at` | TEXT | Waktu terakhir diperbarui |

## Tabel `ownership_positions`

| Kolom | Tipe | Keterangan |
|---|---|---|
| `id` | INTEGER | Primary key |
| `record_date` | TEXT | Tanggal posisi ISO `YYYY-MM-DD` |
| `share_code` | TEXT | Foreign key ke `idx_stocks.code` |
| `issuer_name` | TEXT | Nama emiten pada snapshot tersebut |
| `investor_name` | TEXT | Nama investor |
| `classification` | TEXT | Klasifikasi investor |
| `local_foreign` | TEXT | `L`, `F`, atau `N` |
| `nationality` | TEXT | Nasionalitas |
| `domicile` | TEXT | Domisili |
| `scripless` | INTEGER | Saham scripless |
| `scrip` | INTEGER | Saham berbentuk warkat |
| `percentage` | REAL | Persentase 0–100 |
| `created_at` | TEXT | Waktu dibuat |
| `updated_at` | TEXT | Waktu terakhir diperbarui |

Kombinasi `record_date + share_code + investor_name` harus unik. Master saham yang masih dirujuk oleh data kepemilikan tidak dapat dihapus.

View `v_ownership_positions` menyediakan kolom turunan `total_shares` serta informasi master saham.

## Menambahkan tabel baru

1. Tambahkan definisi tabel/index/triggers di konstanta `SCHEMA_SQL` pada `db.py`.
2. Jalankan ulang aplikasi.
3. `db.py` akan memastikan skema terpasang dengan pola idempoten (`IF NOT EXISTS`).

Versi skema disimpan melalui `PRAGMA user_version`.

## Mesin analitik

Perhitungan berada di `analytics.py` dan bersifat read-only terhadap tabel
harian. Stock Lab membaca sesuai rentang yang dipilih (5 sampai 2.000 sesi;
opsi terakhir mencakup seluruh data yang tersedia), menghitung fitur tiap
saham, lalu menyimpan snapshot kecil dengan cache LRU di memori. Cache memakai
signature `MAX(tanggal) + jumlah baris terbaru + waktu impor + waktu sync`,
sehingga koreksi pada tanggal yang sama maupun tanggal bursa baru dapat memicu
perhitungan ulang. Browser juga menyimpan 12 kombinasi kode/periode terakhir
selama aplikasi tetap terbuka.

Indikator yang tersedia meliputi:

- return 1, 5, 20, dan 60 sesi;
- SMA20, SMA50, SMA200, RSI14, ATR14%, dan volatilitas 20 sesi;
- volume, nilai, dan frekuensi dibanding rata-rata 20 sesi sebelumnya;
- breakout/new low 20 sesi, maksimum drawdown 60 sesi, dan turnover;
- net foreign 1, 5, dan 20 sesi dalam unit saham;
- spread serta depth imbalance dari snapshot bid/offer akhir hari;
- Condition Score v1 dengan tujuh bobot tetap dan minimum 50 sesi;
- metadata cakupan indikator, reference adjustment, freshness, dan kualitas.

Raw price di tabel tidak diubah. Return, SMA, RSI, volatilitas, breakout, dan
level Stock Lab menggunakan seri backward-adjusted yang dibentuk dari reference
price `sebelumnya` resmi IDX dan ditambatkan ke close terbaru. Hal ini mengurangi
distorsi stock split/corporate action. Karena harga pembukaan historis banyak
bernilai nol, Stock Lab menekankan adjusted close serta rentang high–low.

## Endpoint analitik lokal

| Endpoint | Kegunaan |
|---|---|
| `/api/analytics/overview` | Snapshot dan breadth pasar terbaru |
| `/api/analytics/methodology` | Rumus, ambang, freshness, dan profil kualitas |
| `/api/analytics/breadth?days=90` | Seri advance–decline/foreign flow 20–2.000 sesi |
| `/api/analytics/screener` | Fitur teknikal seluruh saham terbaru |
| `/api/analytics/stock?code=BBCA&days=260` | Stock Lab satu saham |
| `/api/analytics/flow-liquidity?window=20&history_days=252` | Ranking flow dan riwayat pasar |
| `/api/analytics/daily` | Penjelajah tabel harian terfilter dan paginated |
| `/api/analytics/sync-status` | Status pembaruan data IDX dari aplikasi |

`POST /api/analytics/sync` memeriksa ulang 35 hari kalender terakhir secara
background. Proses tetap memakai validasi, checksum, log tanggal, dan upsert
atomik dari `tools/sync_idx_daily.py`.

Endpoint hanya aktif di server lokal `127.0.0.1`; aplikasi tidak mengirim data
database ke layanan eksternal.
