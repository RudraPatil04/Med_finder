"""Refresh current PharmEasy listing prices for a few medicines and their
cheapest same-composition alternatives.

A saving is only shown when both sides carry a price checked within
PRICE_MAX_AGE_DAYS, so a demo medicine needs a fresh price on itself and on
at least one alternative. This script looks up each one with
find_reference_listing(), which only accepts a listing that matches the
recorded brand, ingredient signature, dosage form and release type, and
writes that dated price over the old dataset price.

Dry run by default. Back up database/medfinder.db before --apply.

    python -m etl.refresh_listing_prices "Dolo 650" "Pan 40" --apply
    python -m etl.refresh_listing_prices --demo --apply
"""

import argparse
import sys
import time

import requests

from backend.database import execute_non_query, execute_query
from backend.recommendation_service import get_substitutes
from backend.services.price_comparison_service import find_reference_listing

DEMO_MEDICINES = [
    "Dolo 650", "Crocin Advance", "Calpol 500", "Pan 40", "Pantocid 40",
    "Omez 20", "Azithral 500", "Augmentin 625 Duo", "Cetzine", "Allegra 120",
    "Montair LC", "Telma 40", "Amlokind 5", "Atorva 10", "Rosuvas 10",
    "Zerodol SP", "Combiflam", "Shelcal 500", "Metrogyl 400", "Taxim-O 200",
]


def find_medicine(name):
    rows = execute_query(
        "SELECT * FROM medicines WHERE medicine_name LIKE ? AND composition_complete = 1 "
        "ORDER BY (medicine_name LIKE ?) DESC, length(medicine_name), id LIMIT 1",
        (name + "%", name + " Tablet%"),
    )
    return dict(rows[0]) if rows else None


def write_price(medicine_id, listing, pack_label):
    return execute_non_query(
        "UPDATE medicines SET price = ?, price_source = ?, price_checked_at = ?, "
        "pack_size = coalesce(?, pack_size) WHERE id = ?",
        (
            float(listing["price"]),
            listing.get("source") or "PharmEasy listing price",
            listing["checked_at"],
            pack_label,
            medicine_id,
        ),
    )


def refresh_one(medicine, apply, delay):
    try:
        reference = find_reference_listing(medicine)
    except (requests.RequestException, ValueError, TypeError) as exc:
        print(f"    lookup failed ({type(exc).__name__})")
        reference = None
    time.sleep(delay)
    if not reference:
        return None
    listing = reference["listing"]
    if apply:
        write_price(medicine["id"], listing, listing.get("pack_norm"))
    return listing


def run(args):
    names = args.names or (DEMO_MEDICINES if args.demo else [])
    if not names:
        print("Give medicine names or --demo.")
        return 2

    summary = []
    for name in names:
        medicine = find_medicine(name)
        if not medicine:
            print(f"{name}: not in catalog")
            summary.append((name, None, 0))
            continue
        print(f"{name}: {medicine['medicine_name']} (id {medicine['id']}, old Rs. {medicine['price']})")
        listing = refresh_one(medicine, args.apply, args.delay)
        if not listing:
            print("    no exact PharmEasy listing for this brand")
            summary.append((name, medicine, 0))
            continue
        print(f"    live Rs. {listing['price']} ({listing.get('pack_norm') or '-'})")

        options = get_substitutes(medicine["id"], limit=args.candidates).get(
            "same_composition_options") or []
        fresh = 0
        for option in options:
            if fresh >= args.alternatives:
                break
            option_listing = refresh_one(option, args.apply, args.delay)
            if option_listing:
                fresh += 1
                print(f"    alt {option['medicine_name']}: Rs. {option_listing['price']}")
        summary.append((name, medicine, fresh))

    print("\nSummary (alternatives with a fresh price):")
    for name, medicine, fresh in summary:
        print(f"  {name:<20} {'-' if medicine is None else fresh}")
    print("\n" + ("Prices written." if args.apply else "DRY RUN - nothing was written."))
    return 0


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="python -m etl.refresh_listing_prices")
    parser.add_argument("names", nargs="*", help="medicine names (prefix match)")
    parser.add_argument("--demo", action="store_true", help="use the built-in list of 20 common medicines")
    parser.add_argument("--apply", action="store_true", help="write prices (default is a dry run)")
    parser.add_argument("--candidates", type=int, default=15,
                        help="cheapest same-composition options to try per medicine")
    parser.add_argument("--alternatives", type=int, default=3,
                        help="stop after this many alternatives get a fresh price")
    parser.add_argument("--delay", type=float, default=0.7, help="seconds between lookups")
    return parser.parse_args(argv)


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    return run(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
