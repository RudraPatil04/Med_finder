"""
============================================================
MED FINDER
ETL MODULE : IMPORT JAN AUSHADHI PRICE LIST

Purpose:
Load the PMBJP (Jan Aushadhi) product and MRP list into
reference_prices, converted to a per-unit basis so it can be
compared against a medicine the user is holding.

This is a REFERENCE price, not a listing of the brand the user
searched for:
  - the price is the PMBJP list MRP for the generic product
  - PMBI publishes editions, not per-row dates, so observed_at
    is only set when --edition-date is supplied
  - a row here never makes a product "equivalent" to another

The PDF is parsed with PyMuPDF table extraction. Rows are keyed by
(source, source_edition, drug_code), so re-importing the same
edition updates rather than duplicates. Progress is stored in
backfill_state, so a long run can stop and resume by page.
composition_key is derived from medicine_ingredients, so re-run this
import after `python -m etl.build_database` replaces that table.

Usage:
    python -m etl.import_jan_aushadhi --pdf path.pdf --dry-run
    python -m etl.import_jan_aushadhi --pdf path.pdf --edition 2025
    python -m etl.import_jan_aushadhi --pdf path.pdf --edition 2026 \
        --edition-date 2026-04-01 --replace
    python -m etl.import_jan_aushadhi --status
    python -m etl.import_jan_aushadhi --reset
============================================================
"""

import argparse
import re
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent

if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from backend.database import execute_non_query, execute_query
from backend.medicine_data import (
    COUNTABLE_DOSAGE_FORMS,
    normalize_dosage_form,
    normalize_measure,
    normalize_medication_text,
)

STATE_TABLE = "backfill_state"
CURSOR_NAME = "jan_aushadhi:v1"
PRICE_TABLE = "reference_prices"
SOURCE_NAME = "Jan Aushadhi (PMBJP)"
PRICE_KIND = "reference_generic"
CURRENCY = "INR"

# ------------------------------------------------------------
# Name / form parsing
# ------------------------------------------------------------

# Words that describe the formulation, not the active ingredient.
FORM_WORDS = (
    "gastro-resistant", "gastro resistant", "dispersible", "film-coated",
    "film coated", "chewable", "sustained release", "prolonged release",
    "extended release", "controlled release", "modified release",
    "delayed release", "effervescent", "plain", "forte", "sr", "xr", "er",
    "cr", "mr", "dt", "odt", "md",
    "tablets", "tablet", "tabs", "tab", "capsules", "capsule", "caps",
    "cap", "syrup", "suspension", "solution", "injection", "infusion",
    "gel", "cream", "ointment", "lotion", "drops", "drop", "inhaler",
    "spray", "granules", "powder", "mouthwash", "gargle", "lozenges",
    "lozenge", "suppositories", "suppository", "pessary", "patch",
    "sachet", "oral", "ip", "bp", "usp", "nf", "usp", "wfi", "for",
)

STRENGTH_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(mcg|\u00b5g|ug|mg|g|ml|l|iu|%)(?:\s*(w/v|w/w))?",
    re.IGNORECASE,
)
PER_RE = re.compile(r"per\s*(\d+(?:\.\d+)?)\s*(ml|g|l)\b", re.IGNORECASE)

RELEASE_MARKERS = (
    (r"\b(xr)\b", "XR"),
    (r"\b(er)\b", "ER"),
    (r"\b(sr)\b", "SR"),
    (r"\b(cr)\b", "CR"),
    (r"\b(mr)\b", "MR"),
    (r"gastro[-\s]?resistant", "GR"),
    (r"dispersible", "DT"),
    (r"sustained release", "SR"),
    (r"prolonged release", "SR"),
    (r"extended release", "ER"),
    (r"controlled release", "CR"),
    (r"modified release", "MR"),
    (r"delayed release", "DR"),
)


def detect_release(text):
    lowered = str(text or "").lower()
    for pattern, label in RELEASE_MARKERS:
        if re.search(pattern, lowered):
            return label
    return None


def normalize_strength(value):
    text = str(value or "").strip().lower()
    text = text.replace("\u00b5", "u").replace("\u03bc", "u")
    text = text.replace("ug", "mcg")
    text = re.sub(r"\s+", "", text)
    return text


def _composition_text(name):
    """
    Prefer a parenthetical that names an ingredient with its strength.
    Otherwise drop parentheticals, re-attaching a bare strength such as (70%).
    """
    groups = re.findall(r"\(([^()]+)\)", name)
    for group in groups:
        match = STRENGTH_RE.search(group)
        if match and re.search(r"[A-Za-z]{3,}", group[:match.start()]):
            return group

    stripped = re.sub(r"\([^()]*\)", " ", name)
    if STRENGTH_RE.search(stripped):
        return stripped
    for group in groups:
        if STRENGTH_RE.search(group):
            return "%s %s" % (stripped, group)
    return stripped


def _clean_ingredient(part):
    text = part.lower()
    for word in FORM_WORDS:
        text = re.sub(r"\b%s\b" % re.escape(word), " ", text)
    text = re.sub(r"[^a-z0-9%\s.-]", " ", text)
    text = " ".join(text.split())
    return text.strip(" -.")


def parse_name(raw_name):
    """
    Return (ingredients, form, release) or None.

    ingredients is a list of {"name", "strength"} where every component
    carries a strength. Anything ambiguous returns None - this importer
    never guesses a composition.
    """
    name = " ".join(str(raw_name or "").split())
    if not name:
        return None

    form = normalize_dosage_form(name)
    release = detect_release(name)
    composition = _composition_text(name)

    parts = [p for p in re.split(r"\s+and\s+|,\s+|\+", composition) if p.strip()]
    ingredients = []
    for part in parts:
        # 15,00,000 IU is one number; a comma inside digits is not a separator.
        part = re.sub(r"(?<=\d),(?=\d)", "", part)
        strength_match = STRENGTH_RE.search(part)
        if not strength_match:
            return None
        strength = normalize_strength(strength_match.group(0))
        per_match = PER_RE.search(part)
        if per_match:
            strength = "%s/%s" % (strength, normalize_strength(per_match.group(0)[3:]))
        elif re.search(r"\bper\s*ml\b", part, re.IGNORECASE):
            strength = "%s/ml" % strength
        remainder = (part[:strength_match.start()] + " " + part[strength_match.end():])
        ingredient = _clean_ingredient(remainder)
        if not ingredient:
            return None
        ingredients.append({"name": ingredient, "strength": strength})

    if not ingredients:
        return None
    return ingredients, form, release


# ------------------------------------------------------------
# Pack and price parsing
# ------------------------------------------------------------

WORD_UNITS = {
    "vial": "vial",
    "vial & wfi": "vial",
    "ampoule": "ampoule",
    "tube": "tube",
    "bottle": "bottle",
    "strip": "strip",
    "pack": "pack",
    "inhaler": "inhaler",
    "piece": "unit",
    "pieces": "unit",
}


def parse_pack(text, form):
    """Return (quantity, unit) or None. More than one number is ambiguous."""
    raw = " ".join(str(text or "").lower().split())
    if not raw:
        return None
    if len(re.findall(r"\d+(?:\.\d+)?", raw)) > 1:
        # "1's Tin 250g" mixes pack count and net weight.
        return None

    match = re.match(r"^(\d+(?:\.\d+)?)\s*'?s\b", raw)
    if match:
        quantity = float(match.group(1))
        unit = form if form in COUNTABLE_DOSAGE_FORMS else "unit"
        return quantity, unit

    match = re.match(r"^(\d+(?:\.\d+)?)\s*(ml|g|gm|l)\b", raw)
    if match:
        quantity = float(match.group(1))
        unit = "g" if match.group(2) == "gm" else match.group(2)
        return quantity, unit

    match = re.match(r"^(\d+(?:\.\d+)?)\s*(mdi|md|doses?|dose)\b", raw)
    if match:
        return float(match.group(1)), "dose"

    if raw in WORD_UNITS:
        return 1.0, WORD_UNITS[raw]

    for word, unit in WORD_UNITS.items():
        if word in raw:
            return 1.0, unit
    return None


DECIMAL_COMMA = re.compile(r"^\d+,\d{2}$")


def parse_price(text):
    """Return (price, note). A comma decimal is rejected, not guessed."""
    raw = str(text or "").strip().replace("\u20b9", "").replace(" ", "")
    if not raw:
        return None, "missing price"
    if DECIMAL_COMMA.match(raw):
        return None, "comma decimal (corrupt mirror)"
    try:
        value = float(raw)
    except ValueError:
        return None, "unparseable price"
    if value == 0:
        return None, "zero MRP (not priced)"
    if value < 0:
        return None, "negative price"
    return value, None


def per_unit(price, quantity, unit):
    if not quantity or quantity <= 0:
        return None
    return round(price / quantity, 6)


# ------------------------------------------------------------
# Schema and state
# ------------------------------------------------------------

def ensure_schema():
    execute_non_query(
        f"CREATE TABLE IF NOT EXISTS {STATE_TABLE} ("
        "name TEXT PRIMARY KEY, "
        "value TEXT NOT NULL, "
        "updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    execute_non_query(
        f"CREATE TABLE IF NOT EXISTS {PRICE_TABLE} ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "source TEXT NOT NULL, "
        "source_edition TEXT NOT NULL, "
        "source_url TEXT, "
        "drug_code TEXT NOT NULL, "
        "generic_name TEXT NOT NULL, "
        "composition TEXT, "
        "composition_key TEXT, "
        "pack_text TEXT, "
        "pack_quantity REAL, "
        "pack_unit TEXT, "
        "price_kind TEXT NOT NULL, "
        "price_value REAL NOT NULL, "
        "price_per_unit REAL, "
        "currency TEXT NOT NULL DEFAULT 'INR', "
        "release_type TEXT, "
        "observed_at TEXT, "
        "imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "UNIQUE(source, source_edition, drug_code))"
    )
    execute_non_query(
        f"CREATE INDEX IF NOT EXISTS idx_reference_prices_composition "
        f"ON {PRICE_TABLE}(composition_key)"
    )


def table_exists(name):
    rows = execute_query(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    )
    return bool(rows)


def read_cursor():
    if not table_exists(STATE_TABLE):
        return 0
    rows = execute_query(f"SELECT value FROM {STATE_TABLE} WHERE name = ?", (CURSOR_NAME,))
    return int(rows[0]["value"]) if rows else 0


def write_cursor(page):
    execute_non_query(
        f"INSERT INTO {STATE_TABLE}(name, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
        "ON CONFLICT(name) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP",
        (CURSOR_NAME, str(page)),
    )


def reset_cursor():
    ensure_schema()
    execute_non_query(f"DELETE FROM {STATE_TABLE} WHERE name = ?", (CURSOR_NAME,))


def catalogue_keys():
    """(form|composition) key -> number of medicines carrying exactly that."""
    forms = {
        row["id"]: row["dosage_form"]
        for row in execute_query("SELECT id, dosage_form FROM medicines")
    }
    parts = {}
    for row in execute_query(
        "SELECT medicine_id, ingredient_name_norm, ingredient_strength_norm "
        "FROM medicine_ingredients"
    ):
        parts.setdefault(row["medicine_id"], []).append(
            "%s:%s" % (row["ingredient_name_norm"], row["ingredient_strength_norm"])
        )

    counts = {}
    for medicine_id, items in parts.items():
        key = "%s|%s" % (normalize_dosage_form(forms.get(medicine_id)), "+".join(sorted(items)))
        counts[key] = counts.get(key, 0) + 1
    return counts


def composition_key(ingredients, form):
    # normalize_measure matches how medicine_ingredients.ingredient_strength_norm
    # was built, so a reference price can be joined to the catalogue.
    parts = sorted(
        "%s:%s" % (normalize_medication_text(item["name"]), normalize_measure(item["strength"]))
        for item in ingredients
    )
    return "%s|%s" % (form, "+".join(parts))


def composition_label(ingredients):
    return " + ".join("%s (%s)" % (item["name"].title(), item["strength"]) for item in ingredients)


# ------------------------------------------------------------
# PDF reading
# ------------------------------------------------------------

def iter_csv_rows(csv_path, start_page=1):
    """The janaushadhi.gov.in "Product List" export has the same columns as the
    PDF table (Sr No, Drug Code, Generic Name, Unit Size, MRP, Group Name).
    The whole file counts as page 1 for the resume cursor."""
    import csv

    if start_page > 1:
        return
    with open(csv_path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        header_text = " ".join(header or []).lower()
        if header and not ("generic" in header_text or "mrp" in header_text):
            yield 1, header
        for row in reader:
            if row and any(cell.strip() for cell in row):
                yield 1, row


def iter_table_rows(pdf_path, start_page=1):
    if str(pdf_path).lower().endswith(".csv"):
        yield from iter_csv_rows(pdf_path, start_page)
        return

    import fitz

    document = fitz.open(str(pdf_path))
    try:
        for page_index in range(document.page_count):
            page_number = page_index + 1
            if page_number < start_page:
                continue
            for table in document[page_index].find_tables().tables:
                rows = table.extract()
                if not rows:
                    continue
                header = " ".join(str(cell or "") for cell in rows[0]).lower()
                has_header = "generic" in header or "mrp" in header
                for row in rows[1 if has_header else 0:]:
                    if not row or not any(cell for cell in row):
                        continue
                    yield page_number, row
    finally:
        document.close()


def row_to_record(row):
    """Map a raw table row to a record, or return (None, reason)."""
    cells = [" ".join(str(cell or "").split()) for cell in row]
    if len(cells) < 5:
        return None, "short row"
    sr_no, drug_code, generic_name, pack_text, mrp_text = cells[:5]
    if not generic_name:
        return None, "empty name"

    parsed = parse_name(generic_name)
    if parsed is None:
        return None, "unparseable name"
    ingredients, form, release = parsed

    pack = parse_pack(pack_text, form)
    if pack is None:
        return None, "unparseable pack size"
    quantity, unit = pack

    price, price_note = parse_price(mrp_text)
    if price is None:
        return None, price_note

    return {
        "drug_code": drug_code or sr_no,
        "generic_name": generic_name,
        "composition": composition_label(ingredients),
        "composition_key": composition_key(ingredients, form),
        "pack_text": pack_text,
        "pack_quantity": quantity,
        "pack_unit": unit,
        "price_value": price,
        "price_per_unit": per_unit(price, quantity, unit),
        "release_type": release,
        "form": form,
    }, None


def write_record(record, edition, edition_date, source_url):
    execute_non_query(
        f"INSERT INTO {PRICE_TABLE}("
        "source, source_edition, source_url, drug_code, generic_name, composition, "
        "composition_key, pack_text, pack_quantity, pack_unit, price_kind, price_value, "
        "price_per_unit, currency, release_type, observed_at, imported_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, CURRENT_TIMESTAMP) "
        "ON CONFLICT(source, source_edition, drug_code) DO UPDATE SET "
        "source_url = excluded.source_url, generic_name = excluded.generic_name, "
        "composition = excluded.composition, composition_key = excluded.composition_key, "
        "pack_text = excluded.pack_text, pack_quantity = excluded.pack_quantity, "
        "pack_unit = excluded.pack_unit, price_kind = excluded.price_kind, "
        "price_value = excluded.price_value, price_per_unit = excluded.price_per_unit, "
        "currency = excluded.currency, release_type = excluded.release_type, "
        "observed_at = excluded.observed_at, imported_at = CURRENT_TIMESTAMP",
        (
            SOURCE_NAME, edition, source_url, record["drug_code"],
            record["generic_name"], record["composition"], record["composition_key"],
            record["pack_text"], record["pack_quantity"], record["pack_unit"],
            PRICE_KIND, record["price_value"], record["price_per_unit"], CURRENCY,
            record["release_type"], edition_date,
        ),
    )


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

def run(args):
    if not Path(args.pdf).exists():
        print(f"File not found: {args.pdf}")
        return 2

    cursor = 0 if args.dry_run else read_cursor()
    if cursor:
        print(f"Resuming after page {cursor}")

    keys = catalogue_keys()
    print(f"Catalogue composition keys: {len(keys):,}")

    counts = {"parsed": 0, "written": 0, "matched": 0}
    reasons = {}
    matched_keys = set()
    examples = []
    current_page = cursor
    completed_page = cursor
    started = time.time()
    processed = 0
    stopped_early = False

    try:
        for page_number, row in iter_table_rows(args.pdf, start_page=cursor + 1):
            if page_number != current_page:
                if not args.dry_run and current_page:
                    write_cursor(current_page)
                    completed_page = current_page
                current_page = page_number

            processed += 1
            record, reason = row_to_record(row)
            if record is None:
                reasons[reason] = reasons.get(reason, 0) + 1
                continue

            counts["parsed"] += 1
            hit = keys.get(record["composition_key"], 0)
            if hit:
                counts["matched"] += 1
                matched_keys.add(record["composition_key"])

            if not args.dry_run:
                write_record(record, args.edition, args.edition_date, args.source_url)
                counts["written"] += 1

            if len(examples) < 12:
                examples.append((record, hit))

            if args.limit and counts["parsed"] >= args.limit:
                stopped_early = True
                break
    except KeyboardInterrupt:
        stopped_early = True
        print("\nInterrupted.")

    # Only a fully consumed page can be marked done; a limited or interrupted
    # run leaves the cursor before the page it stopped on.
    if not args.dry_run and not stopped_early and current_page:
        write_cursor(current_page)
        completed_page = current_page

    print("\nExample rows (composition, pack, MRP, per unit, catalogue matches):")
    for record, hit in examples:
        unit = record["price_per_unit"]
        print(
            "  %-52s %-8s %8.2f %10s  %d"
            % (
                record["composition"][:52], record["pack_text"], record["price_value"],
                ("%.2f" % unit) if unit is not None else "-", hit,
            )
        )

    print("\nSkipped rows by reason:")
    if reasons:
        for reason, count in sorted(reasons.items(), key=lambda item: -item[1]):
            print(f"  {reason:<34} {count}")
    else:
        print("  none")

    elapsed = time.time() - started
    print(
        "\n%s in %.1f s\n"
        "  rows read        : %d\n"
        "  parsed           : %d\n"
        "  written          : %d\n"
        "  matched catalogue: %d (distinct compositions: %d)\n"
        "  skipped          : %d"
        % (
            "DRY RUN - nothing was written" if args.dry_run else "Import complete",
            elapsed, processed, counts["parsed"], counts["written"],
            counts["matched"], len(matched_keys), processed - counts["parsed"],
        )
    )
    if not args.dry_run:
        print(f"  cursor           : page {completed_page}")
    if args.edition_date is None and not args.dry_run:
        print("  note: no --edition-date supplied, so observed_at is NULL "
              "and this price cannot satisfy a freshness check.")
    return 0


def print_status():
    if not table_exists(PRICE_TABLE):
        print("No reference_prices table yet (nothing imported).")
    else:
        rows = execute_query(
            f"SELECT source_edition, COUNT(*) AS n, MIN(observed_at) AS observed, "
            f"MAX(imported_at) AS imported FROM {PRICE_TABLE} "
            f"GROUP BY source_edition ORDER BY source_edition"
        )
        if not rows:
            print("reference_prices is empty.")
        for row in rows:
            print(
                f"edition {row['source_edition']:<8} rows {row['n']:<6} "
                f"observed_at {row['observed'] or 'NULL':<12} imported {row['imported']}"
            )
    cursor = read_cursor()
    print(f"cursor: page {cursor}" if cursor else "cursor: none")


def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="python -m etl.import_jan_aushadhi",
        description="Import the PMBJP (Jan Aushadhi) product/MRP list as reference prices.",
    )
    parser.add_argument("--pdf", help="path to the PMBJP product list: the PDF, or the "
                             "site's Product List .csv export")
    parser.add_argument("--edition", default="", help="edition label, e.g. 2025")
    parser.add_argument("--edition-date", default=None,
                        help="observation date (YYYY-MM-DD); PMBI publishes editions, not row dates")
    parser.add_argument("--source-url", default=None, help="URL the PDF came from, for provenance")
    parser.add_argument("--dry-run", action="store_true",
                        help="parse and report only; writes nothing and leaves the cursor alone")
    parser.add_argument("--limit", type=int, metavar="N", help="stop after N parsed rows")
    parser.add_argument("--replace", action="store_true",
                        help="delete existing rows for this edition before importing")
    parser.add_argument("--status", action="store_true", help="show imported editions and cursor")
    parser.add_argument("--reset", action="store_true", help="clear the cursor")
    args = parser.parse_args(argv)

    if args.status or args.reset:
        return args
    if not args.pdf:
        parser.error("--pdf is required (or use --status / --reset)")
    if not args.edition:
        parser.error("--edition is required so re-imports update the same rows")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    return args


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    args = parse_args(argv)

    if args.status:
        print_status()
        return 0

    if args.reset:
        reset_cursor()
        print("Cursor cleared.")
        if not args.pdf:
            return 0

    if not args.dry_run:
        ensure_schema()
        if args.replace:
            execute_non_query(
                f"DELETE FROM {PRICE_TABLE} WHERE source = ? AND source_edition = ?",
                (SOURCE_NAME, args.edition),
            )
            print(f"Removed existing rows for edition {args.edition}.")

    print(
        f"Source: {SOURCE_NAME}\n"
        f"Edition: {args.edition}"
        f"{'  observed ' + args.edition_date if args.edition_date else '  (no observation date)'}\n"
        f"{'DRY RUN - nothing will be written' if args.dry_run else 'Writing to ' + PRICE_TABLE}\n"
    )
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
