"""
============================================================
MED FINDER
ETL MODULE : BACKFILL COMPOSITIONS

Purpose:
Fill missing compositions from the free DrugDB (HealthPilot) catalog,
one medicine at a time, and let the run stop and resume.

Only a same-brand, same-dosage-form, same-strength catalog record
is accepted, and complete compositions are never overwritten.

Progress is stored in the database (table: backfill_state), so a
restart continues at the last processed medicine instead of
starting over.

Usage:
    python -m etl.backfill_compositions --sample 200 --dry-run
    python -m etl.backfill_compositions --sample 200
    python -m etl.backfill_compositions --limit 500
    python -m etl.backfill_compositions
    python -m etl.backfill_compositions --status
    python -m etl.backfill_compositions --reset
============================================================
"""

import argparse
import random
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent

if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from backend.database import execute_non_query, execute_query
from backend.services.drug_catalog_service import (
    enrich_medicine_composition,
    find_composition_record,
)

STATE_TABLE = "backfill_state"
CURSOR_NAME = "composition:v1"
BATCH_SIZE = 200
DEFAULT_DELAY_SECONDS = 2.0
MIN_DELAY_SECONDS = 0.5
MAX_CONSECUTIVE_ERRORS = 5

# A row can only be matched when it has a brand to look up.
ELIGIBLE_WHERE = (
    "COALESCE(composition_complete, 0) = 0 "
    "AND TRIM(COALESCE(brand_name, '')) <> ''"
)
NO_BRAND_WHERE = (
    "COALESCE(composition_complete, 0) = 0 "
    "AND TRIM(COALESCE(brand_name, '')) = ''"
)


# ============================================================
# State
# ============================================================

def ensure_state_table():
    execute_non_query(
        f"CREATE TABLE IF NOT EXISTS {STATE_TABLE} ("
        "name TEXT PRIMARY KEY, "
        "value TEXT NOT NULL, "
        "updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )


def read_cursor():
    rows = execute_query(f"SELECT value FROM {STATE_TABLE} WHERE name = ?", (CURSOR_NAME,))
    return int(rows[0]["value"]) if rows else 0


def write_cursor(medicine_id):
    execute_non_query(
        f"INSERT INTO {STATE_TABLE}(name, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
        "ON CONFLICT(name) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP",
        (CURSOR_NAME, str(medicine_id)),
    )


def reset_cursor():
    execute_non_query(f"DELETE FROM {STATE_TABLE} WHERE name = ?", (CURSOR_NAME,))


# ============================================================
# Row selection
# ============================================================

def count_remaining(after_id):
    rows = execute_query(
        f"SELECT COUNT(*) AS total FROM medicines WHERE id > ? AND {ELIGIBLE_WHERE}",
        (after_id,),
    )
    return rows[0]["total"]


def count_pool():
    return execute_query(f"SELECT COUNT(*) AS total FROM medicines WHERE {ELIGIBLE_WHERE}")[0]["total"]


def count_without_brand():
    return execute_query(f"SELECT COUNT(*) AS total FROM medicines WHERE {NO_BRAND_WHERE}")[0]["total"]


def next_batch(after_id, limit):
    rows = execute_query(
        f"SELECT * FROM medicines WHERE id > ? AND {ELIGIBLE_WHERE} ORDER BY id LIMIT ?",
        (after_id, limit),
    )
    return [dict(row) for row in rows]


def load_row(medicine_id):
    rows = execute_query("SELECT * FROM medicines WHERE id = ?", (medicine_id,))
    return dict(rows[0]) if rows else None


def sample_ids(count, seed):
    ids = [
        row["id"]
        for row in execute_query(f"SELECT id FROM medicines WHERE {ELIGIBLE_WHERE} ORDER BY id")
    ]
    return random.Random(seed).sample(ids, min(count, len(ids)))


def iter_rows(after_id, limit):
    """Keyset pagination so filling a row mid-run cannot skip or repeat another."""
    cursor = after_id
    fetched = 0
    while limit is None or fetched < limit:
        size = BATCH_SIZE if limit is None else min(BATCH_SIZE, limit - fetched)
        rows = next_batch(cursor, size)
        if not rows:
            return
        for row in rows:
            yield row
            fetched += 1
            cursor = row["id"]


# ============================================================
# Running
# ============================================================

def describe(row):
    text = " ".join(str(row.get(key) or "") for key in ("medicine_name", "brand_name"))
    return " ".join(text.split())[:48]


def lookup(row, dry_run):
    if dry_run:
        return find_composition_record(row) is not None
    return enrich_medicine_composition(row, use_cache=False, raise_errors=True)


def run(rows, total, delay, resume, dry_run):
    filled = empty = errors = processed = 0
    consecutive_errors = 0
    started = time.time()
    interrupted = False

    try:
        for processed, row in enumerate(rows, start=1):
            try:
                outcome = "filled" if lookup(row, dry_run) else "empty"
                consecutive_errors = 0
            except Exception as exc:
                outcome = "error"
                errors += 1
                consecutive_errors += 1
                print(f"  lookup failed for id {row['id']}: {type(exc).__name__}: {exc}")

            if outcome == "filled":
                filled += 1
            elif outcome == "empty":
                empty += 1

            if resume and outcome != "error":
                write_cursor(row["id"])

            print(f"[{processed:>6}/~{total}] id={row['id']:<8} {describe(row):<50} {outcome}")

            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                print(
                    f"\nStopped: {consecutive_errors} consecutive lookups failed. "
                    "The network or the catalog may be blocking requests; "
                    "the cursor was left before the failing row, so rerunning resumes here."
                )
                break

            if processed < total:
                time.sleep(delay)
    except KeyboardInterrupt:
        interrupted = True
        print("\nInterrupted.")

    elapsed = time.time() - started
    attempted = filled + empty
    rate = (filled / attempted * 100) if attempted else 0.0
    print(
        f"\nProcessed {processed} row(s) in {elapsed / 60:.1f} min "
        f"({elapsed / max(processed, 1):.1f} s/row)\n"
        f"  filled : {filled}\n"
        f"  empty  : {empty}\n"
        f"  errors : {errors}\n"
        f"  fill rate: {rate:.0f}% of {attempted} completed lookup(s)"
    )
    if resume and not interrupted and errors == 0:
        print(f"  cursor : id {read_cursor()}")
    elif resume:
        print(f"  cursor : id {read_cursor()} (rerun to continue from here)")
    return filled, empty, errors, interrupted


def print_status():
    cursor = read_cursor()
    pool = count_pool()
    print(f"Medicines missing a composition : {pool + count_without_brand()}")
    print(f"  ...with a brand to look up    : {pool}")
    print(f"  ...without a brand (skipped)  : {count_without_brand()}")
    if cursor:
        print(f"Cursor                          : id {cursor}")
        print(f"Remaining after the cursor      : {count_remaining(cursor)}")
    else:
        print("Cursor                          : none (next run starts at the first row)")


def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="python -m etl.backfill_compositions",
        description="Fill missing compositions from the free DrugDB catalog.",
        epilog=(
            "The full pool is large: at the default 2 s delay, 77k rows take about 43 hours. "
            "Run --sample first to measure the fill rate, and lower --delay only if the "
            "catalog tolerates it."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--sample", type=int, metavar="N",
                      help="try N randomly chosen rows; does not move the cursor")
    mode.add_argument("--limit", type=int, metavar="N",
                      help="stop after N rows of a resuming run")
    parser.add_argument("--dry-run", action="store_true",
                        help="look up and match, but write nothing and leave the cursor alone")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_SECONDS,
                        help=f"seconds between lookups (default {DEFAULT_DELAY_SECONDS})")
    parser.add_argument("--seed", type=int, default=0,
                        help="seed for --sample so reruns pick the same rows (default 0)")
    parser.add_argument("--status", action="store_true", help="show pool size and cursor, then exit")
    parser.add_argument("--reset", action="store_true", help="clear the cursor before running")
    args = parser.parse_args(argv)

    if args.sample is not None and args.sample < 1:
        parser.error("--sample must be at least 1")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    if args.delay < MIN_DELAY_SECONDS:
        parser.error(f"--delay must be at least {MIN_DELAY_SECONDS} s; the catalog is a public service")
    return args


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    args = parse_args(argv)
    ensure_state_table()

    if args.status:
        print_status()
        return 0

    if args.reset:
        reset_cursor()
        print("Cursor cleared.")

    if args.sample is not None:
        ids = sample_ids(args.sample, args.seed)
        rows = (
            row for row in (load_row(medicine_id) for medicine_id in ids)
            if row and not row.get("composition_complete")
        )
        print(f"Sampling {len(ids)} row(s) (seed {args.seed})"
              f"{' - dry run, nothing will be written' if args.dry_run else ''}.\n")
        _, _, errors, interrupted = run(rows, len(ids), args.delay, resume=False, dry_run=args.dry_run)
    else:
        cursor = read_cursor()
        remaining = count_remaining(cursor)
        planned = remaining if args.limit is None else min(remaining, args.limit)
        print(f"Resuming after id {cursor}" if cursor else "Starting from the first row")
        print(f"{remaining} row(s) left; this run covers {planned}"
              f"{' - dry run, nothing will be written' if args.dry_run else ''}.\n")
        _, _, errors, interrupted = run(
            iter_rows(cursor, args.limit), planned, args.delay,
            resume=not args.dry_run, dry_run=args.dry_run,
        )

    if interrupted:
        return 130
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
