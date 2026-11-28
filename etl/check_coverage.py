"""
============================================================
MED FINDER
ETL MODULE : CHECK SEARCH COVERAGE

Answers "which medicines would show no result?" without typing them one by one.

  python -m etl.check_coverage                       # check datasets/common_medicine_names.txt
  python -m etl.check_coverage --file my_list.txt    # one name per line ('#' = comment)
  python -m etl.check_coverage Zincovit "Liv 52"     # check names given on the command line
  python -m etl.check_coverage --misses              # searches people made that found nothing

Each name is searched in the local database only (no internet, nothing is
written). The report lists the names that found nothing and saves them to
reports/missing_names.txt.

To fill the gap, add --add: missing names are looked up on PharmEasy through
etl.add_listed_brands (dry run). Add --apply to save them; the database is
copied to database/medfinder.before-coverage-<time>.db first. Rows without a
composition are saved search-only (no ingredients, no alternatives, no
savings claim), exactly like add_listed_brands.
============================================================
"""

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LIST = ROOT / "datasets" / "common_medicine_names.txt"
REPORT = ROOT / "reports" / "missing_names.txt"


def read_names(path):
    names, seen = [], set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        name = line.split("#", 1)[0].strip()
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            names.append(name)
    return names


def is_found(name):
    from backend.services.medicine_service import search_medicines_detailed
    result = search_medicines_detailed(name, limit=5, enable_api_fallback=False,
                                       allow_correction=False)
    return bool(result["rows"])


def check(names):
    """Return (found, missing) lists."""
    found, missing = [], []
    for name in names:
        (found if is_found(name) else missing).append(name)
    return found, missing


def backup_database():
    from backend import database
    source = Path(database.MEDICINE_DATABASE)
    target = source.with_name(f"{source.stem}.before-coverage-{datetime.now():%Y%m%d-%H%M%S}.db")
    shutil.copy2(source, target)
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("names", nargs="*", help="names to check (default: the starter list)")
    parser.add_argument("--file", help="text file, one name per line")
    parser.add_argument("--misses", action="store_true", help="show searches that found nothing")
    parser.add_argument("--add", action="store_true", help="look missing names up on PharmEasy")
    parser.add_argument("--apply", action="store_true", help="with --add: save what was found")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if args.misses:
        from backend.services.search_miss_log import list_misses
        misses = list_misses()
        print(f"{len(misses)} search(es) found nothing (most frequent first):")
        for row in misses:
            print(f"  {row['miss_count']:>4}x  {row['query']}   (last {row['last_seen'][:10]})")
        if not args.add:
            return 0
        names = [row["query"] for row in misses]
    else:
        names = args.names or read_names(args.file or DEFAULT_LIST)

    from backend.services.drug_catalog_service import ensure_catalog_schema
    ensure_catalog_schema()
    found, missing = check(names)
    total = len(names)
    print(f"\nChecked {total} name(s): {len(found)} found, {len(missing)} missing"
          + (f" ({len(found) * 100 / total:.1f}% covered)" if total else ""))
    for name in missing:
        print(f"  missing: {name}")
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text("\n".join(missing) + ("\n" if missing else ""), encoding="utf-8")
    print(f"Saved the missing names to {REPORT.relative_to(ROOT)}")

    if args.add and missing:
        if args.apply:
            print(f"Backed up the database to {backup_database().name}")
        from etl import add_listed_brands
        add_listed_brands.main((["--apply"] if args.apply else []) + missing)
        if args.apply:
            from backend.services.search_miss_log import forget_miss
            still = set(check(missing)[1])
            for name in missing:
                if name not in still:
                    forget_miss(name)
            print(f"\nAfter adding: {len(missing) - len(still)} of {len(missing)} now found.")
            for name in still:
                print(f"  still missing (not on PharmEasy either): {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
