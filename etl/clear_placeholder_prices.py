r"""Clear placeholder catalog prices (Rs. 1 or less) so the UI says "Price not available".

    venv\Scripts\python.exe -m etl.clear_placeholder_prices            # dry run
    venv\Scripts\python.exe -m etl.clear_placeholder_prices --apply
    venv\Scripts\python.exe -m etl.clear_placeholder_prices --restore  # put the old values back

The source dataset records 61 products at exactly Rs. 1 (Setra 50 mg, Aben,
Isoniazid 300 mg, Randex, ...). Products with the same composition, strength
and form cost a median 11 times more, so these are placeholders, not prices.
Left in place they would also show up as the "cheapest" option. Only rows
whose price came from the source dataset are touched; the old value is kept in
the placeholder_prices table. Re-run after etl/build_database.py.
"""
import argparse
import sqlite3
import sys
from datetime import datetime, timezone

from backend.config import MEDICINE_DATABASE as DATABASE_PATH

THRESHOLD = 1.0
WHERE = "price > 0 AND price <= ? AND coalesce(price_source, 'Source dataset') = 'Source dataset'"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--restore", action="store_true")
    args = parser.parse_args()
    connection = sqlite3.connect(DATABASE_PATH, timeout=30)
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS placeholder_prices ("
            "medicine_id INTEGER PRIMARY KEY, price REAL, price_source TEXT, cleared_at TEXT)"
        )
        if args.restore:
            restored = connection.execute(
                "UPDATE medicines SET price = (SELECT p.price FROM placeholder_prices p WHERE p.medicine_id = medicines.id), "
                "price_source = (SELECT p.price_source FROM placeholder_prices p WHERE p.medicine_id = medicines.id) "
                "WHERE id IN (SELECT medicine_id FROM placeholder_prices)"
            ).rowcount
            connection.execute("DELETE FROM placeholder_prices")
            connection.commit()
            print(f"restored {restored} prices")
            return
        rows = connection.execute(
            f"SELECT id, medicine_name, price, price_source FROM medicines WHERE {WHERE}", (THRESHOLD,)
        ).fetchall()
        for row in rows[:10]:
            print(f"  {row[0]:>7} Rs. {row[2]:g}  {row[1]}")
        print(f"{len(rows)} placeholder prices{'' if args.apply else ' (dry run, pass --apply)'}")
        if not args.apply or not rows:
            return
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        connection.executemany(
            "INSERT OR REPLACE INTO placeholder_prices VALUES (?, ?, ?, ?)",
            [(row[0], row[2], row[3], now) for row in rows],
        )
        connection.execute(f"UPDATE medicines SET price = NULL WHERE {WHERE}", (THRESHOLD,))
        connection.commit()
        print("cleared")
    finally:
        connection.close()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
