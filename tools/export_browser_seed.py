"""Generate the bundled browser seed from the canonical SQLite database."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db import DB_PATH  # noqa: E402

OUTPUT = ROOT / "assets" / "database.js"
OWNERSHIP_COLUMNS = [
    "id", "record_date", "share_code", "issuer_name", "investor_name", "classification",
    "local_foreign", "nationality", "domicile", "scripless", "scrip", "percentage",
]
MASTER_COLUMNS = ["id", "code", "company_name", "listing_date", "shares", "listing_board"]


def main() -> None:
    with sqlite3.connect(DB_PATH) as connection:
        ownership_rows = connection.execute(
            "SELECT " + ", ".join(OWNERSHIP_COLUMNS) + " FROM ownership_positions ORDER BY id"
        ).fetchall()
        master_rows = connection.execute(
            "SELECT id, code, company_name, COALESCE(listing_date, ''), shares, listing_board FROM idx_stocks ORDER BY id"
        ).fetchall()
    payload = {
        "version": 1,
        "ownershipColumns": OWNERSHIP_COLUMNS,
        "masterColumns": MASTER_COLUMNS,
        "ownershipRows": ownership_rows,
        "masterRows": master_rows,
    }
    OUTPUT.write_text(
        "/* Generated from data/zaiden_trader.db. */\nwindow.EQUITY_SEED="
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + ";\n",
        encoding="utf-8",
    )
    print(f"Exported {len(ownership_rows):,} ownership and {len(master_rows):,} stocks to {OUTPUT}")


if __name__ == "__main__":
    main()
