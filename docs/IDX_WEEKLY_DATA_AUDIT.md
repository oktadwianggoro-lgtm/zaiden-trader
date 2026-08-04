# IDX Weekly High-Confidence — Data Audit Report

**Dibuat:** 2026-07-22  
**Database:** `data/zaiden_trader.db` (SQLite, ~410 MB)  
**Koneksi:** Read-only audit

---

## 1. Ringkasan Eksekutif

| Item | Nilai |
|---|---|
| Total baris OHLCV | 1,326,376 |
| Jumlah saham unik | 989 |
| Rentang tanggal | 2020-01-02 → 2026-07-22 |
| Hari perdagangan unik | 1,575 |
| Saham dengan ≥5 tahun data | 728 |
| Saham aktif (60 hari terakhir) | 872 |
| Duplicate (tanggal, kode) | **0** |

---

## 2. Tabel dan Kolom

### 2.1 `ringkasan_saham_harian` (1,326,376 baris)

| Kolom | Tipe | Not Null | PK | Keterangan |
|---|---|---|---|---|
| `tanggal` | TEXT | ✅ | 1 | Format: YYYY-MM-DD |
| `kode_saham` | TEXT | ✅ | 2 | Kode ticker IDX |
| `nama_perusahaan` | TEXT | ❌ | — | Nama emiten |
| `sebelumnya` | REAL | ❌ | — | Harga penutupan hari sebelumnya |
| `harga_pembukaan` | REAL | ❌ | — | Harga open (80% NULL!) |
| `perdagangan_pertama` | REAL | ❌ | — | Harga perdagangan pertama |
| `harga_tertinggi` | REAL | ❌ | — | High |
| `harga_terendah` | REAL | ❌ | — | Low |
| `harga_penutupan` | REAL | ❌ | — | Close (0% NULL) |
| `perubahan` | REAL | ❌ | — | Perubahan harga nominal |
| `volume` | INTEGER | ❌ | — | Volume lot |
| `nilai_transaksi` | REAL | ❌ | — | Nilai transaksi (IDR) |
| `frekuensi` | INTEGER | ❌ | — | Jumlah transaksi |
| `indeks_individual` | REAL | ❌ | — | Indeks individual saham |
| `penawaran_jual` | REAL | ❌ | — | Best ask price |
| `volume_penawaran_jual` | INTEGER | ❌ | — | Volume ask |
| `penawaran_beli` | REAL | ❌ | — | Best bid price |
| `volume_penawaran_beli` | INTEGER | ❌ | — | Volume bid |
| `saham_tercatat` | INTEGER | ❌ | — | Jumlah saham listed |
| `saham_dapat_diperdagangkan` | INTEGER | ❌ | — | Float shares |
| `bobot_indeks` | REAL | ❌ | — | Index weight |
| `jual_asing` | REAL | ❌ | — | Foreign sell value |
| `beli_asing` | REAL | ❌ | — | Foreign buy value |
| `tanggal_delisting` | TEXT | ❌ | — | Tanggal delisting (semua NULL) |
| `volume_non_reguler` | INTEGER | ❌ | — | Volume non-reguler |
| `nilai_non_reguler` | REAL | ❌ | — | Nilai non-reguler |
| `frekuensi_non_reguler` | INTEGER | ❌ | — | Frekuensi non-reguler |
| `sumber_file` | TEXT | ❌ | — | File sumber |
| `diimpor_pada` | TEXT | ✅ | — | Timestamp import |
| `id_stock_summary` | INTEGER | ❌ | — | ID eksternal |
| `remarks` | TEXT | ❌ | — | Keterangan tambahan |

**Index:**
- PRIMARY KEY: `(tanggal, kode_saham)` — UNIQUE ✅
- `idx_ringkasan_saham_kode_tanggal (kode_saham, tanggal)`
- `idx_ringkasan_saham_tanggal (tanggal)`
- `idx_ringkasan_saham_volume (volume)`

### 2.2 `ringkasan_broker_harian` (76,953 baris)

| Kolom | Tipe | Keterangan |
|---|---|---|
| `tanggal` | TEXT | PK1 |
| `kode_broker` | TEXT | PK2 — Kode broker (AD, YP, dll.) |
| `volume` | INTEGER | Total volume broker |
| `nilai` | INTEGER | Total nilai broker |
| `frekuensi` | INTEGER | Total frekuensi |
| `buy_volume` | INTEGER | Volume beli (semua 0!) |
| `buy_value` | INTEGER | Nilai beli (semua 0!) |
| `sell_volume` | INTEGER | Volume jual (semua 0!) |
| `sell_value` | INTEGER | Nilai jual (semua 0!) |

> ⚠️ **CRITICAL:** Tabel ini adalah **ringkasan per broker**, BUKAN per saham per broker.
> Kolom buy/sell semuanya 0. Hanya aggregate volume/nilai total seluruh perdagangan broker.
> **TIDAK DAPAT DIGUNAKAN** untuk mendeteksi akumulasi broker pada saham tertentu.

### 2.3 `broker_saham_harian` (**0 baris — KOSONG**)

> ❌ Tabel ini seharusnya berisi data flow broker per saham, namun belum terisi.

### 2.4 `ownership_positions` (35,995 baris)

| Item | Nilai |
|---|---|
| Saham unik | 956 |
| Tanggal berbeda | **5** (Feb 2026, Mar 2026, Apr 2026, Mei 2026, Jun 2026) |
| Rentang | 2026-02-27 → 2026-06-30 |
| Klasifikasi | CP, ID, IB, IS, SC, OT, PF, MF, FD, + lainnya |
| Local/Foreign | L (Local), F (Foreign), N (Nominal) |

> ⚠️ **CRITICAL:** Hanya 5 tanggal dalam 5 bulan. Tidak dapat digunakan sebagai
> dynamic feature dalam ML. Hanya berguna sebagai snapshot statis terkini.

### 2.5 `idx_stocks` (963 baris)

| Papan | Jumlah |
|---|---|
| Main | 270 |
| Development | 495 |
| Acceleration | 41 |
| Watchlist | 156 |
| Ekonomi Baru | 1 |

- 26 saham ada di `ringkasan_saham_harian` tetapi TIDAK ada di `idx_stocks`
- 0 saham di `idx_stocks` tidak ada data harga (coverage sempurna)

### 2.6 View `v_ohlcv_harian` (1,326,376 baris)

Alias view dengan kolom: tanggal, kode_saham, nama_perusahaan, open, high, low, close, volume, nilai_transaksi, frekuensi

---

## 3. Kualitas Data

### 3.1 Missing Values

| Kolom | Null/Zero | Persentase | Dampak ML |
|---|---|---|---|
| `harga_pembukaan` (open) | 1,060,190 | **79.9%** | 🔴 Kritis — gunakan close sebagai entry |
| `harga_tertinggi` (high) | 160,171 | 12.1% | 🟡 Moderate — hari tidak aktif |
| `harga_terendah` (low) | 160,171 | 12.1% | 🟡 Moderate — hari tidak aktif |
| `volume` = 0 | 160,171 | 12.1% | 🟡 Moderate — filter saat inference |
| `nilai_transaksi` = 0 | 160,171 | 12.1% | 🟡 Moderate |
| `frekuensi` = 0 | 160,171 | 12.1% | 🟡 Moderate |
| `harga_penutupan` (close) | **0** | 0% | ✅ Aman |
| `sebelumnya` (prev) | 0 | 0% | ✅ Aman |
| `nama_perusahaan` | 0 | 0% | ✅ Aman |

> Note: high=0 dan low=0 berkorelasi sempurna dengan volume=0 (hari tidak aktif / tidak ada perdagangan)

### 3.2 Duplikat

- Duplikat `(tanggal, kode_saham)` di `ringkasan_saham_harian`: **0** ✅

### 3.3 Corporate Action (Extreme Returns)

Deteksi perubahan harga >30% dalam satu hari:

| Saham | Tanggal | Prev | Close | Return |
|---|---|---|---|---|
| MKNT | 2023-12-13 | 1 | 2 | +100% |
| SBAT | 2024-03-26 | 1 | 2 | +100% |
| ... | ... | 1 | 2 | +100% |

> **Analisis:** Kasus ekstrem adalah penny stocks dengan harga 1 → 2 Rupiah.
> Ini bukan stock split (yang biasanya harga tinggi → rendah), melainkan
> pergerakan normal pada saham harga sangat rendah.
> Data tampaknya **belum fully adjusted** untuk corporate action.
>
> **Tindakan:** Filter saham dengan median harga < Rp 50 dari universe ML.
> Tandai extreme returns sebagai `corporate_action_flag = True`.

---

## 4. Distribusi Data per Tahun

| Tahun | Baris | Saham Unik | Hari Perdagangan |
|---|---|---|---|
| 2020 | 168,727 | 722 | 242 |
| 2021 | 182,704 | 770 | 247 |
| 2022 | 196,321 | 828 | 246 |
| 2023 | 209,045 | 907 | 239 |
| 2024 | 220,952 | 948 | 237 |
| 2025 | 225,871 | 973 | 236 |
| 2026 (Jan–Jul) | 122,756 | 965 | 128 |
| **Total** | **1,326,376** | **989** | **1,575** |

Rata-rata 240 hari perdagangan per tahun (sesuai kalender IDX).

---

## 5. Cakupan Data per Saham

| Kategori | Jumlah Saham | Threshold |
|---|---|---|
| ≥5 tahun (≥1,260 hari) | 728 | ML-eligible (primary) |
| ≥2 tahun (≥504 hari) | 932 | ML-eligible (minimal) |
| ≥1 tahun (≥252 hari) | 964 | Insufficient history |
| ≥6 bulan (≥120 hari) | 978 | Too new |

Rata-rata hari per saham: **1,341 hari** (~5.6 tahun)

---

## 6. Saham Aktif vs Tidak Aktif (60 hari terakhir)

- **Aktif** (harga > 0, volume > 0): 872 saham
- **Tidak aktif / suspend**: ~117 saham dalam universe

---

## 7. Kualitas Join Antar Tabel

| Join | Keterangan |
|---|---|
| `ringkasan_saham_harian` ⟵ `idx_stocks` | 26 saham tanpa master data |
| `ringkasan_saham_harian` ⟵ `ringkasan_broker_harian` | Tidak dapat join (tidak ada kode_saham di broker) |
| `ringkasan_saham_harian` ⟵ `ownership_positions` | JOIN via share_code, hanya 5 tanggal |

---

## 8. Kalender Perdagangan

Hari perdagangan per tahun konsisten (~237–247 hari), sesuai dengan kalender BEI.
Tidak ada missing trading day yang mencurigakan.

---

## 9. Kemungkinan Stock Split / Corporate Action

Data harga **TIDAK** ter-adjust otomatis untuk:
- Stock split / reverse split
- Rights issue
- Pembagian dividen saham

**Indikator:**
- Extreme daily returns (>30%) ditemukan di penny stocks (harga 1→2)
- Tidak ada perubahan drastis pada saham harga menengah-tinggi

**Rekomendasi:**
1. Filter saham dengan median harga < Rp 50 dari training universe
2. Tandai hari dengan `|daily_return| > 0.25` sebagai `suspect_corp_action`
3. Keluarkan 10 hari setelah event dari training (embargo)
4. Tampilkan warning di UI untuk saham yang memiliki flag ini

---

## 10. Kesimpulan dan Rekomendasi

### Feature yang Dapat Digunakan (✅)
- **Price & Returns:** 100% coverage untuk close, ~88% untuk high/low
- **Volume & Value & Frequency:** ~88% coverage
- **Foreign Flow:** `beli_asing`, `jual_asing` — tersedia tapi perlu cek coverage
- **Market Regime:** Dapat dihitung dari aggregate seluruh saham
- **Sector Momentum:** Dapat dihitung dari `idx_stocks.listing_board` sebagai proxy sektor

### Feature yang TIDAK Tersedia (❌)
- **Broker flow per saham:** `broker_saham_harian` kosong
- **Broker accumulation:** `ringkasan_broker_harian` tidak ada per-saham
- **Ownership dynamics:** `ownership_positions` hanya 5 tanggal
- **Open price yang akurat:** 80% null, gunakan `sebelumnya` sebagai proxy

### Strategi Data untuk ML
1. Gunakan `sebelumnya` (prev close) sebagai entry price proxy ketika `harga_pembukaan` null
2. Filter universe: hanya saham dengan ≥120 hari aktif sebelum tanggal prediksi
3. Filter universe: median volume 20 hari > threshold minimum
4. Exclude saham dengan harga median < Rp 50
5. Gunakan LEFT JOIN untuk semua tabel tambahan
6. Model utama: price-volume-foreign flow (tidak ada broker per saham)

### Validation Split yang Direkomendasikan
```
Training:   2020-01-02 → 2023-12-31  (4 tahun, ~1,007 hari × 842 saham avg)
Validation: 2024-01-01 → 2024-12-31  (237 hari, untuk threshold selection)
Holdout:    2025-01-01 → 2026-07-22  (untouched, ~444 hari, untuk final eval)
```

---

*Dokumen ini dihasilkan secara otomatis dari inspeksi database langsung.*
*Tidak menggunakan asumsi atau data eksternal.*
