# Zaiden BDM Gap Analysis

## 1. Ringkasan Audit Baseline Zaiden Trader

Berdasarkan audit repositori dan database `zaiden_trader.db` per 26 Juli 2026, berikut baseline tabel eksisting:

- `ringkasan_saham_harian`: ~1.328.306 baris (2020-01-02 hingga 2026-07-24). Ini adalah *source of truth* untuk harga, volume, nilai, frekuensi, dan foreign flow EOD. Kualitas sangat baik dan rentang tanggal konsisten. **Perlakuan: REUSE as read-only.**
- `ringkasan_broker_harian`: ~76.953 baris (2020-01-02 hingga 2026-07-17). Ini adalah agregat broker pasar, *bukan* data broker per saham. **Perlakuan: REUSE, tapi tidak dapat digunakan untuk indikator Top Buy/Sell per saham.**
- `ownership_positions`: ~35.995 baris. **Perlakuan: REUSE untuk analisis kepemilikan saham.**
- `idx_stocks`: ~963 baris. **Perlakuan: REUSE sebagai master data emiten.**
- `master_broker`: ~107 baris. **Perlakuan: REUSE.**
- `idx_daily_sync_log`: ~1.713 baris. **Perlakuan: REUSE untuk audit sinkronisasi.**
- Tabel ML Existing: Terdapat log prediksi dan feature registry (`ml_weekly_*`). **Perlakuan: REUSE, model machine learning saat ini harus terintegrasi.**
- `idx_fundamental_snapshots` & `idx_daily_derived_features`: Ditemukan dengan status 0 baris, sebelumnya merupakan inisiasi tahap awal yang masih kosong. **Perlakuan: ENHANCE/POPULATE sesuai rancangan baru.**

## 2. 94-Feature Gap Matrix (NeoBDM Concepts vs Zaiden Trader)

Seluruh 94 indikator dari kamus `NEO BDN.xlsx` diklasifikasi ulang sesuai data riil yang dimiliki Zaiden Trader.

| Fitur workbook / NeoBDM concept | Kategori | Klasifikasi | Tabel Zaiden Trader Target | Status & Tindakan |
|---|---|---|---|---|
| Mcap (T) | Emiten | DIRECT | `idx_fundamental_snapshots` | ENHANCE - Ambil dari official IDX Snapshot |
| Tval (B) | Emiten | DIRECT | `ringkasan_saham_harian` | EXISTING - Gunakan kolom `nilai_transaksi` |
| Suspend | Emiten | DIRECT | `idx_security_status_events` | NEW - Event-based sinkronisasi dari IDX |
| FCA | Emiten | DIRECT | `idx_security_status_events` | NEW - Event-based dari IDX Papan Khusus |
| Syariah | Emiten | DIRECT | `idx_security_status_events` | NEW - Event-based dari IDX Islamic |
| [Metode][Periode] (Akum/Dist) | Transaksi | DERIVED_INTERNAL | `stock_flow_metrics` | NEW - Hitung dari `beli_asing` & `jual_asing` |
| %[Metode][Periode] | Transaksi | DERIVED_INTERNAL | `stock_flow_metrics` | NEW |
| Comp [Metode], Comp Any, dst (4) | Compatibility | DERIVED_INTERNAL | `stock_compatibility_scores` | NEW - Buat Strategy Compatibility Score internal |
| Likuiditas, Likuid (2) | Compatibility | DERIVED_INTERNAL | `stock_liquidity_metrics` | NEW - Buat skor berbasis volume/frekuensi/Amihud |
| Cross | Compatibility | DERIVED_INTERNAL | `stock_risk_flags` | NEW - Buat `CROSSING_PROXY` |
| F-I Cross | Compatibility | REQUIRES_LICENSED_DATA | - | BLOCKED - Data institutional tidak tersedia |
| I-Z Cross | Compatibility | UNAVAILABLE | - | BLOCKED - Konsep 'Zombie' tidak berdasar/tidak ada data |
| Clean, Pinky (2) | Compatibility | DERIVED_INTERNAL | `stock_risk_flags` | NEW - Ganti nama menjadi `Transaction Quality Score` / `Statistical Anomaly` |
| Top[N] Buy, Top[N] Sell, Tektoker (3) | Broker | REQUIRES_LICENSED_DATA | `broker_saham_harian` | BLOCKED - Data broker per saham tidak ada di Zaiden Trader |
| High, Low, Close, Chg[N], MA[N], EMA[N] (6) | Harga | DERIVED_STANDARD | `ringkasan_saham_harian` & `stock_technical_eod` | EXISTING / ENHANCE |
| Volume, VolChg[N], MAVol[N], RVol[N], Unusual (5) | Volume | DERIVED_STANDARD | `ringkasan_saham_harian` & `stock_technical_eod` | EXISTING / ENHANCE |
| ATR14, Volatility, RSI, Stoch (7), MACD (4), BB (3), SAR (4) (21) | Teknikal Modern | DERIVED_STANDARD | `stock_technical_eod` | NEW - Hitung dengan standard library teknikal |
| EveningStar dkk (26 pola) | Candlestick | DERIVED_STANDARD | `stock_pattern_events` | NEW - Deteksi pola event-based |
| RotStrength, RotPhase, RotDir (3) | Rotation | DERIVED_STANDARD | `stock_rotation_metrics` | NEW - Hitung relative performance vs IHSG |
| Bpos, BposChg, Scripless, ScriplessChg, Total KDA 1%, FreeFloat, Holder (7) | Kepemilikan | DIRECT | `ownership_positions` & `idx_major_shareholder_snapshots` | EXISTING / ENHANCE |
| Buyback, Nominee (2) | Kepemilikan | UNAVAILABLE | `stock_risk_flags` | BLOCKED - Anomaly flag pengganti untuk Nominee, Buyback perlu disclosure resmi |
| Proba m[periode], Exp m[periode] (2) | Seasonality | DERIVED_STANDARD | `stock_seasonality` | NEW - Berdasarkan historis `ringkasan_saham_harian` |
| SMACD GC | Member Request | DERIVED_STANDARD | `stock_technical_eod` | NEW - Custom MACD (2,10,2) |

**Kesimpulan:**
- 79 dari 94 fitur dapat diimplementasi penuh atau dibuat padanan internalnya yang transparan.
- 15 fitur (Top Broker, Tektoker, F-I Cross, I-Z Cross, Nominee) di-block atau membutuhkan data berlisensi yang tidak dimiliki saat ini. Status mereka akan di-set `NOT_AVAILABLE`.
