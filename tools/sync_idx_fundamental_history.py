"""
IDX Fundamental History Sync (annual + quarterly financial statements)
Backfills idx_fundamental_financials from yfinance for all IDX tickers.

Coverage caveat: Yahoo Finance only retains roughly the last ~4 fiscal years
of annual statements and ~5 quarters of quarterly statements for most IDX
issuers — it does NOT go back to 2020 for most stocks. This is a limitation
of the free upstream data source, not of this script; whatever depth Yahoo
exposes per ticker is stored as-is.
"""
import sqlite3
import time
from pathlib import Path

import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "zaiden_trader.db"

ANNUAL_REVENUE_KEYS = ["Total Revenue", "Operating Revenue"]
NET_INCOME_KEYS = ["Net Income", "Net Income Common Stockholders"]
EPS_KEYS = ["Basic EPS", "Diluted EPS"]
GROSS_PROFIT_KEYS = ["Gross Profit"]
OPERATING_INCOME_KEYS = ["Operating Income", "Total Operating Income As Reported"]
TOTAL_ASSETS_KEYS = ["Total Assets"]
TOTAL_EQUITY_KEYS = ["Stockholders Equity", "Total Equity Gross Minority Interest"]
TOTAL_LIABILITIES_KEYS = ["Total Liabilities Net Minority Interest"]


def _first_row(df, keys):
    if df is None or df.empty:
        return None
    for k in keys:
        if k in df.index:
            return df.loc[k]
    return None


def _val(series, col):
    if series is None:
        return None
    try:
        v = series.get(col)
    except Exception:
        return None
    if v is None or v != v:  # NaN check
        return None
    return float(v)


def sync_history(batch_size: int = 25, sleep_between: float = 0.3, tickers: list[str] | None = None) -> dict:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=60.0)
    conn.execute("PRAGMA busy_timeout = 60000")
    cursor = conn.cursor()

    if tickers is None:
        cursor.execute("SELECT DISTINCT kode_saham FROM ringkasan_saham_harian ORDER BY kode_saham")
        tickers = [r[0] for r in cursor.fetchall() if r[0] and len(r[0]) <= 5]

    print(f"Fetching historical financials for {len(tickers)} stocks...", flush=True)

    written = 0
    failed = 0

    for i, code in enumerate(tickers):
        if i % 50 == 0:
            print(f"  progress {i}/{len(tickers)} (written={written}, failed={failed})", flush=True)

        yf_code = f"{code}.JK"
        try:
            t = yf.Ticker(yf_code)

            for period_type, income_df, bs_df in (
                ("annual", t.financials, t.balance_sheet),
                ("quarterly", t.quarterly_financials, t.quarterly_balance_sheet),
            ):
                revenue_row = _first_row(income_df, ANNUAL_REVENUE_KEYS)
                net_income_row = _first_row(income_df, NET_INCOME_KEYS)
                eps_row = _first_row(income_df, EPS_KEYS)
                gross_row = _first_row(income_df, GROSS_PROFIT_KEYS)
                opinc_row = _first_row(income_df, OPERATING_INCOME_KEYS)
                assets_row = _first_row(bs_df, TOTAL_ASSETS_KEYS)
                equity_row = _first_row(bs_df, TOTAL_EQUITY_KEYS)
                liab_row = _first_row(bs_df, TOTAL_LIABILITIES_KEYS)

                if income_df is None or income_df.empty:
                    continue

                for col in income_df.columns:
                    period_end = col.date().isoformat() if hasattr(col, "date") else str(col)[:10]
                    revenue = _val(revenue_row, col)
                    net_income = _val(net_income_row, col)
                    if revenue is None and net_income is None:
                        continue

                    fiscal_year = col.year
                    fiscal_quarter = ((col.month - 1) // 3 + 1) if period_type == "quarterly" else None

                    cursor.execute(
                        """INSERT INTO idx_fundamental_financials (
                            stock_code, period_type, period_end, fiscal_year, fiscal_quarter,
                            revenue, net_income, operating_income, gross_profit, eps,
                            total_assets, total_equity, total_liabilities, source, fetched_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'yfinance', CURRENT_TIMESTAMP)
                        ON CONFLICT(stock_code, period_type, period_end) DO UPDATE SET
                            revenue=excluded.revenue,
                            net_income=excluded.net_income,
                            operating_income=excluded.operating_income,
                            gross_profit=excluded.gross_profit,
                            eps=excluded.eps,
                            total_assets=excluded.total_assets,
                            total_equity=excluded.total_equity,
                            total_liabilities=excluded.total_liabilities,
                            fetched_at=CURRENT_TIMESTAMP""",
                        (
                            code, period_type, period_end, fiscal_year, fiscal_quarter,
                            revenue, net_income,
                            _val(opinc_row, col), _val(gross_row, col), _val(eps_row, col),
                            _val(assets_row, col), _val(equity_row, col), _val(liab_row, col),
                        ),
                    )
                    written += 1

            conn.commit()
        except Exception as exc:
            failed += 1
            if failed <= 20:
                print(f"  {code}: {exc}", flush=True)
        time.sleep(sleep_between)

    conn.execute(
        """INSERT OR REPLACE INTO idx_data_coverage (
            id_source, source_name, local_min_date, local_max_date,
            last_checked_at, historical_availability_status, historical_unavailability_reason
        ) VALUES (
            'fundamental_history', 'IDX Historical Financials (yfinance)',
            (SELECT MIN(period_end) FROM idx_fundamental_financials),
            (SELECT MAX(period_end) FROM idx_fundamental_financials),
            CURRENT_TIMESTAMP, 'Partial',
            'Yahoo Finance hanya menyimpan ~4 tahun laporan tahunan dan ~5 kuartal terakhir per emiten, bukan sejak 2020.'
        )"""
    )
    conn.commit()
    conn.close()

    print(f"Done. rows_written={written} tickers_failed={failed}", flush=True)
    return {"status": "success", "rows_written": written, "tickers_failed": failed, "tickers_total": len(tickers)}


if __name__ == "__main__":
    sync_history()
