# Zaiden Trader — HTML + SQLite

Zaiden Trader adalah aplikasi HTML lokal dengan database fisik SQLite di
`data/zaiden_trader.db`. Data tidak dikirim ke internet: browser hanya
berkomunikasi dengan server Python lokal di komputer Anda.

## Menjalankan aplikasi

Klik dua kali `BUKA-APLIKASI.bat`. Launcher akan menjalankan `app.py`, lalu
membuka halaman HTML di `http://127.0.0.1:8899` (atau port berikutnya bila port
tersebut sedang dipakai).

Jangan membuka `index.html` dengan alamat `file://`. Browser tidak diizinkan
membaca file SQLite secara langsung, sehingga HTML perlu dibuka melalui
launcher lokal tersebut.

## Menu aplikasi

- **Market Pulse** — breadth pasar, advance/decline, aktivitas, new high/low,
  SMA participation, dan foreign flow pasar.
- **Technical Screener** — return multi-periode, SMA20/50/200, RSI14, ATR14,
  volatilitas, activity ratio, breakout, likuiditas, quality flag, dan flow.
- **Stock Lab** — riwayat harga, SMA, volume, RSI, level statistik, order book
  akhir hari, serta integrasi pemegang saham di atas 1%. Rentang dapat dipilih
  dari 1 minggu sampai seluruh data yang tersedia (saat ini sekitar 6 tahun).
  Grafik harga dapat diklik untuk membaca angka pada satu tanggal.
- **Flow & Likuiditas** — akumulasi/distribusi asing, unusual activity, dan
  peringkat likuiditas. Riwayat grafik dapat dipilih dari 60 sesi sampai
  seluruh data 2020–sekarang, lalu digeser ke kiri untuk melihat periode lama.
- **Data Harian** — melihat dan memfilter tabel `ringkasan_saham_harian` tanpa
  membuka seluruh tabel sekaligus, serta memperbarui tanggal terbaru dari IDX.
- Menu lama **Dashboard**, **Kepemilikan >1%**, dan **Master Saham IDX** tetap
  tersedia beserta fungsi input, edit, hapus, dan ekspor.

## Database dan backup

Semua perubahan dari formulir disimpan ke file yang sama:

```text
data/zaiden_trader.db
```

Tombol **Backup database** membuat salinan `.db` yang konsisten. File backup
besar dikirim secara streaming agar tidak memenuhi memori komputer.

Data yang ditarik untuk broker disimpan di tabel baru terpisah pada database
yang sama agar tidak tercampur dengan data emiten harian:

- `master_broker`
- `ringkasan_broker_harian`
- `broker_saham_harian`

Indikator harga menggunakan seri backward-adjusted yang dibentuk dari
`sebelumnya`/reference price resmi IDX dan ditambatkan ke close terbaru. Raw
price di SQLite tidak diubah. Setiap menu memiliki panel **Cara Baca &
Metodologi** berisi rumus, bobot, ambang, freshness, kualitas data, dan batasan.
Condition Score v1 belum backtested dan bukan rekomendasi jual atau beli.

Jika HTML baru mendeteksi server lama yang masih aktif, aplikasi akan meminta
server dijalankan ulang alih-alih menampilkan jumlah data `0`. Launcher terbaru
juga mencegah beberapa proses memakai port yang sama.

## Kecepatan Stock Lab

Stock Lab mengambil hanya sesi yang dipilih, bukan seluruh database. Hasil
analisis yang sudah dibuka disimpan sementara di memori browser dan server;
memilih kembali kode/periode yang sama akan tampil hampir seketika. Grafik
panjang diringkas secara visual tanpa mengubah perhitungan indikator atau data
asli. Tombol muat ulang di samping tombol **Analisis** mengabaikan cache bila
Anda baru memperbarui data.

## Persyaratan

Python 3.10 atau lebih baru. Launcher otomatis memprioritaskan Python yang
tersedia di lingkungan Codex, lalu mencoba `py -3` atau `python`.

## Tarik Data Broker IDX ke Tabel Baru

Gunakan script berikut untuk menarik data broker dan menyimpannya ke tabel baru
di `data/zaiden_trader.db`:

- `tools/idx_broker_pipeline.py`

Perintah umum:

1. Inisialisasi tabel broker:
  `py tools/idx_broker_pipeline.py --db data/zaiden_trader.db init`
2. Tarik ringkasan broker harian (contoh 1 tahun):
  `py tools/idx_broker_pipeline.py --db data/zaiden_trader.db fetch-idx-summary --years 1`
3. Lihat statistik isi tabel:
  `py tools/idx_broker_pipeline.py --db data/zaiden_trader.db stats`

Tabel tujuan:

- `master_broker`
- `ringkasan_broker_harian`
- `broker_saham_harian`

Catatan: endpoint master broker IDX dapat menolak akses otomatis (HTTP 403).
Saat itu terjadi, data broker tetap bisa masuk dari feed ringkasan harian dengan
mekanisme upsert tanpa duplikasi pada kunci utama tabel.

### Auto Sync Broker Jam 19:00 (Bertahap dari 2020)

Sudah tersedia mode otomatis harian khusus broker:

- Runner: `tools/run_idx_broker_sync.bat`
- Registrasi task: `tools/register_idx_broker_task.ps1`
- Launcher setup: `ATUR-AUTO-SYNC-BROKER-19.bat`
- Nama task: `ZaidenTrader-IdxBrokerDailySync-1900`

Karakteristik:

- Jalan tiap hari jam 19:00.
- Hanya berjalan jika internet tersedia.
- Tarik `ringkasan_broker_harian` mulai dari `2020-01-01`.
- Bertahap per eksekusi (`--max-days 40`) agar beban stabil.
- Resume otomatis pakai checkpoint: `data/.idx_broker_summary_checkpoint.json`.
