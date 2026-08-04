"""
Builds idx_fundamental_ratio_history from idx_fundamental_financials.

PER/PBV are NOT static — they move every quarter as price and earnings move.
This script computes valuation ratios at each available reporting point:
  - quarterly    : each individual reported quarter (annualized x4)
  - semester     : Q1+Q2 cumulative ("6 bulanan", annualized x2)
  - nine_month   : Q1+Q2+Q3 cumulative ("9 bulanan", annualized x4/3)
  - annual       : full fiscal year (as reported, or Q1..Q4 summed)
  - projection   : one period ahead, via simple trend extrapolation on
                    annual net income (NOT an analyst forecast)

Price is looked up from ringkasan_saham_harian (last close on/before the
period-end date) but nothing is written back into that table — all output
rows land in idx_fundamental_ratio_history, a separate table.
"""
from __future__ import annotations
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "zaiden_trader.db"


def _sane_ratio(value: float | None, bound: float) -> float | None:
    """
    Reject implausible PER/PBV. Some IDX small/mid caps have inconsistent
    unit scaling between yfinance's raw financial-statement net_income and
    its own computed trailingPE (verified against idx_fundamental_snapshots,
    e.g. DSSA: statement-derived PER > 1e8 vs Yahoo's own trailingPE=34.6) —
    treat wildly out-of-range results as unreliable rather than display them.
    """
    if value is None or value != value:
        return None
    return value if abs(value) <= bound else None


def _price_on_or_before(conn: sqlite3.Connection, code: str, date: str) -> float | None:
    row = conn.execute(
        """SELECT harga_penutupan FROM ringkasan_saham_harian
           WHERE kode_saham = ? AND tanggal <= ? AND harga_penutupan > 0
           ORDER BY tanggal DESC LIMIT 1""",
        (code, date),
    ).fetchone()
    return float(row[0]) if row and row[0] else None


def build_ratio_history() -> dict:
    conn = sqlite3.connect(str(DB_PATH), timeout=60.0)
    conn.execute("PRAGMA busy_timeout = 60000")
    conn.row_factory = sqlite3.Row

    shares_map = {
        r["code"]: r["shares"]
        for r in conn.execute("SELECT code, shares FROM idx_stocks WHERE shares > 0")
    }

    quarters = conn.execute(
        """SELECT stock_code, fiscal_year, fiscal_quarter, period_end,
                  net_income, revenue, total_equity
           FROM idx_fundamental_financials
           WHERE period_type = 'quarterly' AND fiscal_year IS NOT NULL AND fiscal_quarter IS NOT NULL
           ORDER BY stock_code, fiscal_year, fiscal_quarter"""
    ).fetchall()

    annuals = conn.execute(
        """SELECT stock_code, fiscal_year, period_end, net_income, revenue, total_equity
           FROM idx_fundamental_financials WHERE period_type = 'annual' AND fiscal_year IS NOT NULL"""
    ).fetchall()
    annual_map: dict[tuple, sqlite3.Row] = {(r["stock_code"], r["fiscal_year"]): r for r in annuals}

    by_stock: dict[str, dict[int, dict]] = {}
    for r in quarters:
        by_stock.setdefault(r["stock_code"], {})[
            (r["fiscal_year"], r["fiscal_quarter"])
        ] = r

    rows_out = []

    for code, qmap in by_stock.items():
        shares = shares_map.get(code)
        if not shares:
            continue

        years = sorted({fy for fy, _ in qmap.keys()})
        annual_ni_series = []  # for projection

        for year in years:
            q = {n: qmap.get((year, n)) for n in (1, 2, 3, 4)}

            windows = []
            if q[1] is not None:
                windows.append(("quarterly", f"{year}-Q1", q[1]["period_end"], q[1]["net_income"], q[1]["revenue"], q[1]["total_equity"], 4.0))
            if q[2] is not None:
                windows.append(("quarterly", f"{year}-Q2", q[2]["period_end"], q[2]["net_income"], q[2]["revenue"], q[2]["total_equity"], 4.0))
            if q[3] is not None:
                windows.append(("quarterly", f"{year}-Q3", q[3]["period_end"], q[3]["net_income"], q[3]["revenue"], q[3]["total_equity"], 4.0))
            if q[4] is not None:
                windows.append(("quarterly", f"{year}-Q4", q[4]["period_end"], q[4]["net_income"], q[4]["revenue"], q[4]["total_equity"], 4.0))

            if q[1] is not None and q[2] is not None:
                ni = q[1]["net_income"] + q[2]["net_income"] if q[1]["net_income"] is not None and q[2]["net_income"] is not None else None
                rev = q[1]["revenue"] + q[2]["revenue"] if q[1]["revenue"] is not None and q[2]["revenue"] is not None else None
                windows.append(("semester", f"{year}-H1", q[2]["period_end"], ni, rev, q[2]["total_equity"], 2.0))

            if q[1] is not None and q[2] is not None and q[3] is not None:
                vals = [q[1]["net_income"], q[2]["net_income"], q[3]["net_income"]]
                ni = sum(vals) if all(v is not None for v in vals) else None
                rvals = [q[1]["revenue"], q[2]["revenue"], q[3]["revenue"]]
                rev = sum(rvals) if all(v is not None for v in rvals) else None
                windows.append(("nine_month", f"{year}-9M", q[3]["period_end"], ni, rev, q[3]["total_equity"], 4.0 / 3.0))

            ann = annual_map.get((code, year))
            if ann is not None:
                windows.append(("annual", f"{year}", ann["period_end"], ann["net_income"], ann["revenue"], ann["total_equity"], 1.0))
                if ann["net_income"] is not None:
                    annual_ni_series.append((year, ann["net_income"]))
            elif all(q[n] is not None for n in (1, 2, 3, 4)):
                vals = [q[n]["net_income"] for n in (1, 2, 3, 4)]
                rvals = [q[n]["revenue"] for n in (1, 2, 3, 4)]
                ni = sum(vals) if all(v is not None for v in vals) else None
                rev = sum(rvals) if all(v is not None for v in rvals) else None
                windows.append(("annual", f"{year}", q[4]["period_end"], ni, rev, q[4]["total_equity"], 1.0))
                if ni is not None:
                    annual_ni_series.append((year, ni))

            for period_type, label, period_end, ni_cum, rev_cum, equity, factor in windows:
                price = _price_on_or_before(conn, code, period_end)
                annualized_ni = ni_cum * factor if ni_cum is not None else None
                eps = (annualized_ni / shares) if annualized_ni is not None and shares else None
                bvps = (equity / shares) if equity is not None and shares else None
                per = _sane_ratio((price / eps) if price and eps and eps > 0 else None, 500)
                pbv = _sane_ratio((price / bvps) if price and bvps and bvps > 0 else None, 100)
                mcap = (price * shares) if price else None

                rows_out.append((
                    code, period_type, label, period_end, ni_cum, rev_cum,
                    annualized_ni, eps, bvps, price, per, pbv, mcap, shares, 0,
                ))

        # Trend-extrapolation projection (one year ahead), NOT an analyst forecast.
        annual_ni_series.sort(key=lambda x: x[0])
        if len(annual_ni_series) >= 2:
            growth_rates = []
            for i in range(1, len(annual_ni_series)):
                prev, cur = annual_ni_series[i - 1][1], annual_ni_series[i][1]
                if prev and prev > 0 and cur is not None:
                    growth_rates.append(cur / prev - 1.0)
            last_year, last_ni = annual_ni_series[-1]
            if growth_rates and last_ni and last_ni > 0:
                g = sum(growth_rates) / len(growth_rates)
                projected_ni = last_ni * (1 + g)
                eps_proj = projected_ni / shares if shares else None
                latest_price = _price_on_or_before(conn, code, "2099-12-31")
                last_ann = annual_map.get((code, last_year))
                bvps_latest = (last_ann["total_equity"] / shares) if last_ann and last_ann["total_equity"] and shares else None
                per_proj = _sane_ratio((latest_price / eps_proj) if latest_price and eps_proj and eps_proj > 0 else None, 500)
                pbv_proj = _sane_ratio((latest_price / bvps_latest) if latest_price and bvps_latest and bvps_latest > 0 else None, 100)
                mcap_proj = latest_price * shares if latest_price else None
                rows_out.append((
                    code, "projection", f"{last_year + 1}-Proyeksi",
                    f"{last_year + 1}-12-31", projected_ni, None,
                    projected_ni, eps_proj, bvps_latest, latest_price,
                    per_proj, pbv_proj, mcap_proj, shares, 1,
                ))

    conn.execute("DELETE FROM idx_fundamental_ratio_history")
    conn.executemany(
        """INSERT INTO idx_fundamental_ratio_history (
            stock_code, period_type, period_label, period_end,
            net_income_cumulative, revenue_cumulative, annualized_net_income,
            annualized_eps, book_value_per_share, price_close, per, pbv,
            market_cap, shares_outstanding, is_projected
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        rows_out,
    )
    conn.commit()
    n_stocks = len({r[0] for r in rows_out})
    conn.close()
    return {"status": "success", "rows_written": len(rows_out), "stocks_covered": n_stocks}


if __name__ == "__main__":
    print(build_ratio_history())
