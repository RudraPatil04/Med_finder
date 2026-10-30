"""Persist a current pharmacy listing price for a catalog-added medicine.

A price is only written when the live listing matches the recorded brand,
ingredient signature, dosage form and release type, and only when the row has
no price yet. Dataset prices are never overwritten.
"""

import os
from datetime import datetime, timezone

import requests

from backend.database import execute_non_query
from backend.services.api_cache import has_recent_api_search, record_api_search
from backend.services.price_comparison_service import find_reference_listing

PRICE_ATTEMPT_KEY = "price:{medicine_id}"
PRICE_SUCCESS_TTL_DAYS = 7
PRICE_MISS_TTL_DAYS = 1
ENRICHMENT_FLAG = "MEDFINDER_PRICE_ENRICHMENT"


def price_enrichment_enabled():
    value = os.environ.get(ENRICHMENT_FLAG, "1").strip().casefold()
    return value not in {"0", "false", "off", "no"}


def _pack_label(pack):
    if not pack:
        return None
    quantity, unit = pack
    return f"{quantity} {unit}" if unit == "ml" else f"{quantity} {unit}s"


def persist_reference_price(medicine_id, listing, pack=None):
    """Write a live listing price to a row that has none; return True if written."""
    price = float(listing.get("price") or 0)
    if price <= 0:
        return False
    pack_size = _pack_label(pack) or listing.get("pack_norm") or listing.get("pack_size")
    affected = execute_non_query(
        """UPDATE medicines SET price = ?, price_source = ?, price_checked_at = ?,
             pack_size = coalesce(pack_size, ?)
           WHERE id = ? AND (price IS NULL OR price <= 0)""",
        (
            price,
            listing.get("source") or "PharmEasy listing price",
            listing.get("checked_at") or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            pack_size,
            medicine_id,
        ),
    )
    return affected > 0


def enrich_medicine_price(medicine, force=False):
    """Look up and store one live price for a medicine that has none."""
    if not price_enrichment_enabled():
        return False
    medicine_id = medicine.get("id")
    if not medicine_id:
        return False
    if not force and float(medicine.get("price") or 0) > 0:
        return False
    if not (
        medicine.get("composition_complete")
        and medicine.get("brand_name")
        and medicine.get("dosage_form")
        and medicine.get("composition")
    ):
        return False

    attempt_key = PRICE_ATTEMPT_KEY.format(medicine_id=medicine_id)
    try:
        if has_recent_api_search(attempt_key, PRICE_SUCCESS_TTL_DAYS, PRICE_MISS_TTL_DAYS):
            return False
    except Exception:
        pass

    try:
        reference = find_reference_listing(medicine)
    except (requests.RequestException, ValueError, TypeError) as exc:
        print(f"Price lookup failed for medicine {medicine_id} ({type(exc).__name__})")
        return False

    if not reference:
        try:
            record_api_search(attempt_key, 0)
        except Exception:
            pass
        return False

    if not persist_reference_price(medicine_id, reference["listing"], reference.get("pack")):
        return False
    try:
        record_api_search(attempt_key, 1)
    except Exception:
        pass
    print(
        f"Stored live price Rs. {reference['listing']['price']} for medicine "
        f"{medicine_id} ({reference['listing'].get('medicine_name')})"
    )
    return True
