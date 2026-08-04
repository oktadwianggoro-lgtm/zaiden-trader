# Zaiden BDM Source Lineage

Dokumen yang memetakan asal-usul data dari titik ekstrak hingga metric turunan.

1. **OHLCV dan Broker Agregat**
   - Sumber Awal: File IDX (Ringkasan Saham, Ringkasan Broker).
   - Frekuensi: Harian EOD.
   - Tabel Raw: `ringkasan_saham_harian` (1.3M baris), `ringkasan_broker_harian`.
   - Turunan: `stock_technical_eod`, `stock_flow_metrics`, `stock_seasonality`.
   - Rentang Ketersediaan: 02 Jan 2020 hingga Sekarang.

2. **Fundamental & Market Cap**
   - Sumber Awal: IDX Stock Screener API/File.
   - Frekuensi: Snapshot Harian/Mingguan.
   - Tabel Raw: `idx_fundamental_snapshots`.
   - Turunan: Modul Screener "Fundamental & Valuasi".
   - Rentang Ketersediaan: Realtime / Latest Snapshot (Historis dari IDX dikosongkan).

3. **Kepemilikan dan Free Float**
   - Sumber Awal: Data Kepemilikan IDX >1% dan KSEI.
   - Frekuensi: Harian/Bulanan.
   - Tabel Raw: `ownership_positions`.
   - Turunan: Modul "Zaiden Stock 360" tab Ownership.
   - Rentang Ketersediaan: Berdasarkan snapshot yang tersimpan di DB.
