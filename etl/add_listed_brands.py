"""
============================================================
MED FINDER
ETL MODULE : ADD LISTED BRANDS

Purpose:
Some well-known brands (Zincovit, Digene, Liv 52, ...) are not in the
source dataset, and the live PharmEasy search returns them without a
composition, so the normal catalog fallback drops them and a search for
them finds nothing.

This tool saves PharmEasy listings for named brands:

  - a listing that carries a composition is saved through the normal
    catalog path (`save_catalog_medicine`), so it can be compared;
  - a listing without one is saved as a search-only row: name, form,
    pack, listing price and date, `composition = NULL`,
    `composition_complete = 0`. Nothing is guessed, so such a row never
    gets alternatives or a savings claim.

Only listings whose title starts with the requested brand are kept.
Dry run by default.

Usage:
    python -m etl.add_listed_brands Zincovit Digene "Liv 52"
    python -m etl.add_listed_brands --apply Zincovit Digene "Liv 52"

Undo: the ids saved are printed; rows carry
api_source = 'PharmEasy live catalog'.
============================================================
"""

import argparse
import re
import sys
from datetime import datetime, timezone

from backend.medicine_data import extract_release_type, normalize_medication_text
from backend.services import drug_catalog_service as catalog
from backend.services.price_comparison_service import _search_payload

_TITLE_CUT = re.compile(r"\s+(?:strip|bottle|tube|box|pack|packet|jar|sachet|carton)\s+of\b|\s*\|", re.I)


def _title_matches(title, brand):
    return normalize_medication_text(title).startswith(normalize_medication_text(brand)) or \
        normalize_medication_text(re.sub(r"^himalaya\s+", "", title, flags=re.I)).startswith(
            normalize_medication_text(brand))


def search_only_record(item):
    """Map a PharmEasy listing that has no composition, or return None."""
    title = catalog._clean(item.get("name"))
    form = catalog._dosage_form(title + " " + str(item.get("measurementUnit") or ""))
    if not title or not form:
        return None
    name = _TITLE_CUT.split(title)[0].strip(" -")
    if form.casefold() not in name.casefold():
        name = f"{name} {form}"
    price = float(item.get("salePriceDecimal") or 0) or None
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rx_required = item.get("isRxRequired")
    image = item.get("image")
    return {
        "medicine_name": name,
        "brand_name": name,
        "strength": None,
        "dosage_form": form,
        "medicine_category": "Unclassified",
        "composition": None,
        "price": price,
        "price_source": "PharmEasy listing price" if price else None,
        "price_checked_at": now if price else None,
        "prescription_required": ("Yes" if rx_required in (True, 1)
                                  else "No" if rx_required in (False, 0) else "Unknown"),
        "image_url": image if isinstance(image, str) and image.startswith("http") else None,
        "release_type": extract_release_type(title),
        "pack_size": catalog._pack_size_from_item(item),
        "composition_complete": 0,
        "rxnorm_rxcui": None,
        "source": "api",
        "api_source": catalog.PHARMEASY_SOURCE,
        "api_record_id": str(item.get("productId") or "") or None,
        "api_fetched_at": now,
    }


def listings_for(brand):
    found = []
    for item in _search_payload(brand):
        if not isinstance(item, dict) or not _title_matches(item.get("name") or "", brand):
            continue
        record = catalog._map_pharmeasy_item(item)
        kind = "with composition"
        if record is None:
            record = search_only_record(item)
            kind = "search only"
        if record:
            found.append((kind, record))
    return found


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("brands", nargs="+")
    parser.add_argument("--apply", action="store_true", help="write the rows (default: dry run)")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    catalog.ensure_catalog_schema()
    for brand in args.brands:
        rows = listings_for(brand)
        print(f"== {brand}: {len(rows)} listing(s)")
        for kind, record in rows:
            line = (f"   [{kind}] {record['medicine_name']} | {record.get('dosage_form')} | "
                    f"{record.get('pack_size')} | Rs {record.get('price')}")
            if args.apply:
                record = {k: v for k, v in record.items() if not k.startswith("_")}
                medicine_id = catalog.save_catalog_medicine(record)
                line += f" -> id {medicine_id}"
            print(line)
    if not args.apply:
        print("\nDry run. Re-run with --apply to save these rows.")


if __name__ == "__main__":
    main()
