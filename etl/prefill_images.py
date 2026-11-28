r"""Pre-fill verified product photos for the medicines most likely to be searched.

    venv\Scripts\python.exe -m etl.prefill_images              # dry run: count targets only
    venv\Scripts\python.exe -m etl.prefill_images --apply      # look up and store
    venv\Scripts\python.exe -m etl.prefill_images --apply --cards 20 --max-requests 300

Targets: the top search results for every query in eval/search_eval.py, plus
every medicine with a current listing price (the demo set with verified
savings). Only rows whose photo is missing or the shared 1mg placeholder are
looked up. One PharmEasy search per brand serves all of that brand's rows, a
pause separates requests, and the run stops at the first refusal (PharmEasy
answers HTTP 405 after a few hundred requests).

A photo is stored only for the listing that is this exact product
(backend/services/image_lookup.py); every other row keeps its drawing.
"""
import argparse
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

from backend.database import execute_query
from backend.recommendation_service import PRICE_MAX_AGE_DAYS
from backend.services import medicine_service
from backend.services.api_cache import ensure_api_search_cache
from backend.services.image_lookup import (
    image_from_items, needs_image, record_attempt, store_image,
)
from backend.services.price_comparison_service import _search_payload
from eval.image_coverage import queries

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def targets(cards):
    rows = {}
    for q in queries(1000):
        result = medicine_service.search_medicines_detailed(q, enable_api_fallback=False, allow_correction=True)
        for row in [dict(r) for r in result["rows"]][:cards]:
            rows.setdefault(row["id"], row)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=PRICE_MAX_AGE_DAYS)).isoformat()
    for row in execute_query("SELECT * FROM medicines WHERE price_checked_at >= ?", (cutoff,)):
        rows.setdefault(row["id"], dict(row))
    return [row for row in rows.values() if needs_image(row)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--cards", type=int, default=12)
    parser.add_argument("--max-requests", type=int, default=400)
    parser.add_argument("--pause", type=float, default=1.0)
    args = parser.parse_args()

    pending = targets(args.cards)
    groups = {}
    for row in pending:
        query = str(row.get("brand_name") or row.get("medicine_name") or "").strip()
        groups.setdefault(query.casefold(), (query, []))[1].append(row)
    print(f"{len(pending)} rows need a photo, {len(groups)} pharmacy searches")
    if not args.apply:
        return

    ensure_api_search_cache()
    requests_made = stored = 0
    for query, rows in groups.values():
        if requests_made >= args.max_requests:
            print("request budget reached; run again later to continue")
            break
        try:
            items = _search_payload(query)
        except requests.RequestException as exc:
            print(f"stopped at {query!r}: {type(exc).__name__} {exc}")
            break
        requests_made += 1
        for row in rows:
            url = image_from_items(items, row)
            if url:
                store_image(row["id"], url)
                stored += 1
            record_attempt(row["id"], bool(url))
            print(f"{'OK' if url else '--'} {row['medicine_name']}")
        time.sleep(args.pause)
    print(f"\nsearches={requests_made} photos stored={stored} of {len(pending)} rows")


if __name__ == "__main__":
    main()
