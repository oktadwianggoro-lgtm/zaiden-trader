# Zaiden BDM Data Dictionary

Berisi definisi data turunan (derived metrics) yang akan digunakan di sistem.

| Feature | Type | Definition | Source |
|---|---|---|---|
| MCap | Numeric | Market Capitalization Snapshot dari IDX | IDX Stock Screener |
| EOD MCap | Numeric | Close Price * Listed Shares | `ringkasan_saham_harian` |
| Return_1D, 5D, 20D | Numeric | Persentase perubahan harga penutupan | `ringkasan_saham_harian` |
| MA_5, 20, 60, 200 | Numeric | Simple Moving Average dari harga penutupan | `ringkasan_saham_harian` |
| Volatility | Numeric | Standar deviasi annualized dari return harian | `ringkasan_saham_harian` |
| Z-Score | Numeric | (Close - MA) / StdDev(Close) dalam rentang tertentu | `ringkasan_saham_harian` |
| Drawdown | Numeric | (Close - Max(Close_N)) / Max(Close_N) | `ringkasan_saham_harian` |
| Foreign Net | Numeric | Net volume/value transaksi asing harian | `ringkasan_saham_harian` |
| RSI14 | Numeric | Relative Strength Index 14 hari | `ringkasan_saham_harian` |
| MACD | Numeric | Moving Average Convergence Divergence | `ringkasan_saham_harian` |
| Bollinger Bands | Numeric | Pita volatilitas (Upper, Mid, Lower) | `ringkasan_saham_harian` |
| Rotation Strength | Numeric | Relative strength momentum terhadap IHSG | `ringkasan_saham_harian` & indeks |
