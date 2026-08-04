"""
tools/detect_corporate_actions.py
Detects stock splits, reverse splits, and other share-count-changing
corporate actions from ringkasan_saham_harian's saham_tercatat (listed
shares outstanding) column — which, unlike open price, is 100% populated
across all 1.3M+ rows.

Method: for each ticker, walk consecutive traded days and flag any day
where saham_tercatat changes by more than 1% from the prior traded day.
Classify using the companion price move:
  - SPLIT_LIKE (HIGH confidence): shares ratio and the inverse price ratio
    are close to each other (market cap roughly preserved) AND the shares
    change is large (>5%) — consistent with a genuine split/reverse split,
    where the exchange mechanically re-prices on the same day.
  - CAPITAL_CHANGE (MEDIUM confidence): shares changed but price did not
    move proportionally — consistent with a rights issue, private
    placement, or buyback (capital raised/returned, not a pure re-slicing
    of the same equity).

Purely additive: never mutates ringkasan_saham_harian. Writes to
idx_corporate_actions, a separate table, idempotently (UNIQUE stock_code +
event_date; safe to re-run daily).
"""
from __future__ import annotations
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "zaiden_trader.db"

SHARES_CHANGE_THRESHOLD = 0.01   # >1% day-over-day change in listed shares triggers a candidate
SPLIT_SHARES_THRESHOLD = 0.05    # >5% change required to consider it "split-sized"
SPLIT_MATCH_TOLERANCE = 0.25     # shares ratio and inverse price ratio must agree within 25% (relative).
# Validated against known real IDX splits: BBCA 2021-10-13 (1:5, shares/price
# ratio both ~5.0, exact match), BBNI 2023-10-06 (1:2, ~2.0 both). BYAN's real
# 2022-12-02 1:10 split showed shares_ratio=10.0 vs price_ratio=8.34 (~17%
# relative gap, likely same-day volatility unrelated to the split) — a 0.15
# tolerance missed it; 0.25 catches it without pulling in genuine capital
# changes (those cluster at >50% relative gap in spot-checks).


def detect_for_all_stocks(db_path: str | None = None) -> dict:
    db_path = db_path or str(DB_PATH)
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.execute("PRAGMA busy_timeout = 60000")
    conn.row_factory = sqlite3.Row

    tickers = [r[0] for r in conn.execute(
        "SELECT DISTINCT kode_saham FROM ringkasan_saham_harian WHERE LENGTH(kode_saham)<=5 ORDER BY kode_saham"
    ).fetchall()]

    total_events = 0
    high_confidence = 0
    stocks_with_events = 0

    for code in tickers:
        rows = conn.execute(
            """SELECT tanggal, saham_tercatat, harga_penutupan
               FROM ringkasan_saham_harian
               WHERE kode_saham = ? AND volume > 0
                 AND harga_penutupan IS NOT NULL AND harga_penutupan > 0
                 AND saham_tercatat IS NOT NULL AND saham_tercatat > 0
               ORDER BY tanggal""",
            (code,),
        ).fetchall()

        if len(rows) < 2:
            continue

        events = []
        prev = rows[0]
        for row in rows[1:]:
            shares_before, shares_after = prev["saham_tercatat"], row["saham_tercatat"]
            shares_ratio = shares_after / shares_before

            if abs(shares_ratio - 1.0) > SHARES_CHANGE_THRESHOLD:
                price_before, price_after = prev["harga_penutupan"], row["harga_penutupan"]
                price_ratio = price_before / price_after if price_after else None

                event_type, confidence = "CAPITAL_CHANGE", "MEDIUM"
                if price_ratio and abs(shares_ratio - 1.0) > SPLIT_SHARES_THRESHOLD:
                    # Magnitude check: shares ratio and inverse price ratio
                    # should roughly cancel out (market cap ~preserved).
                    rel_diff = abs(shares_ratio - price_ratio) / max(shares_ratio, price_ratio)
                    # Direction check: more shares must come with a genuinely
                    # LOWER price (and fewer shares with a genuinely HIGHER
                    # price) — not just "happened to be within tolerance
                    # while basically flat". Without this, a moderate share
                    # increase on a day the price barely moved can slip
                    # through the magnitude check alone (caught by this
                    # module's own test suite).
                    direction_ok = (
                        (shares_ratio > 1.0 and price_ratio > 1.02) or
                        (shares_ratio < 1.0 and price_ratio < 0.98)
                    )
                    if rel_diff <= SPLIT_MATCH_TOLERANCE and direction_ok:
                        event_type, confidence = "SPLIT_LIKE", "HIGH"

                events.append((
                    code, row["tanggal"], shares_before, shares_after, shares_ratio,
                    price_before, price_after, price_ratio, event_type, confidence,
                ))

            prev = row

        if events:
            stocks_with_events += 1
            for ev in events:
                conn.execute(
                    """INSERT INTO idx_corporate_actions (
                        stock_code, event_date, shares_before, shares_after, shares_ratio,
                        price_before, price_after, price_ratio, event_type, confidence
                    ) VALUES (?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(stock_code, event_date) DO UPDATE SET
                        shares_before=excluded.shares_before, shares_after=excluded.shares_after,
                        shares_ratio=excluded.shares_ratio, price_before=excluded.price_before,
                        price_after=excluded.price_after, price_ratio=excluded.price_ratio,
                        event_type=excluded.event_type, confidence=excluded.confidence,
                        detected_at=CURRENT_TIMESTAMP""",
                    ev,
                )
                total_events += 1
                if ev[9] == "HIGH":
                    high_confidence += 1
            conn.commit()

    conn.close()
    return {
        "status": "success",
        "tickers_scanned": len(tickers),
        "stocks_with_events": stocks_with_events,
        "total_events": total_events,
        "high_confidence_split_like": high_confidence,
    }


if __name__ == "__main__":
    print(detect_for_all_stocks())
