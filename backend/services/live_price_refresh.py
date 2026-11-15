"""Fetch current listing prices while a user looks at cheaper alternatives.

A saving is only claimed when the searched medicine and an alternative both
carry a price checked within PRICE_MAX_AGE_DAYS. Most catalog prices are old,
so before ranking alternatives this module looks up the exact PharmEasy
listing for the searched medicine and its cheapest same-composition options,
in parallel and within a short time budget, and stores the dated prices.

Only a listing that matches the recorded brand, ingredient signature, dosage
form and release type is accepted (find_reference_listing). A failed or
missing lookup changes nothing: the product keeps its old price and stays
"unverified". Each attempt is cached so a page reload does not repeat it.
"""

import os
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone

from backend.database import execute_non_query
from backend.recommendation_service import PRICE_MAX_AGE_DAYS, get_substitutes
from backend.services.api_cache import (
    ensure_api_search_cache, has_recent_api_search, record_api_search,
)
from backend.services.price_comparison_service import find_reference_listing

LIVE_PRICE_FLAG = "MEDFINDER_LIVE_PRICES"
ATTEMPT_KEY = "live-price:{medicine_id}"
# A found price stays valid for PRICE_MAX_AGE_DAYS; retry a miss the next day.
HIT_TTL_DAYS = 7
MISS_TTL_DAYS = 1
DEFAULT_ALTERNATIVES = 8
DEFAULT_BUDGET_SECONDS = 12.0
SKIP_STATUSES = {"restricted_class", "insufficient_product_data"}


def live_prices_enabled():
    value = os.environ.get(LIVE_PRICE_FLAG, "1").strip().casefold()
    return value not in {"0", "false", "off", "no"}


def _is_fresh(checked_at):
    if not checked_at:
        return False
    try:
        checked = datetime.fromisoformat(str(checked_at).replace("Z", "+00:00"))
    except ValueError:
        return False
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=timezone.utc)
    return checked >= datetime.now(timezone.utc) - timedelta(days=PRICE_MAX_AGE_DAYS)


def _recently_attempted(medicine_id):
    try:
        return has_recent_api_search(
            ATTEMPT_KEY.format(medicine_id=medicine_id), HIT_TTL_DAYS, MISS_TTL_DAYS
        )
    except Exception:
        return False


def _record_attempt(medicine_id, found):
    try:
        record_api_search(ATTEMPT_KEY.format(medicine_id=medicine_id), 1 if found else 0)
    except Exception:
        pass


def _needs_price(medicine):
    return (
        medicine
        and medicine.get("id")
        and medicine.get("composition")
        and medicine.get("dosage_form")
        and not _is_fresh(medicine.get("price_checked_at"))
        and not _recently_attempted(medicine["id"])
    )


_ERROR = object()
# After the pharmacy site refuses requests (rate limit, block), stop asking for a while.
COOLDOWN_SECONDS = 15 * 60
_cooldown_until = 0.0


def _in_cooldown():
    return time.monotonic() < _cooldown_until


def _start_cooldown():
    global _cooldown_until
    _cooldown_until = time.monotonic() + COOLDOWN_SECONDS


def _lookup(medicine):
    try:
        return find_reference_listing(medicine)
    except Exception:
        # A request error says nothing about whether the product is listed.
        return _ERROR


def _store(medicine_id, listing):
    execute_non_query(
        "UPDATE medicines SET price = ?, price_source = ?, price_checked_at = ?, "
        "pack_size = coalesce(?, pack_size) WHERE id = ?",
        (
            float(listing["price"]),
            listing.get("source") or "PharmEasy listing price",
            listing.get("checked_at") or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            listing.get("pack_norm"),
            medicine_id,
        ),
    )


def refresh_prices(medicines, budget_seconds=DEFAULT_BUDGET_SECONDS):
    """Look up the given medicines in parallel; store the matches. Returns the count stored."""
    if _in_cooldown():
        return 0
    targets = [dict(item) for item in medicines if _needs_price(item)]
    if not targets:
        return 0
    executor = ThreadPoolExecutor(max_workers=min(4, len(targets)))
    try:
        futures = {executor.submit(_lookup, item): item for item in targets}
        done, _ = wait(futures, timeout=budget_seconds)
    finally:
        # Lookups still running after the budget are abandoned, not awaited.
        executor.shutdown(wait=False, cancel_futures=True)

    try:
        ensure_api_search_cache()
    except Exception:
        pass
    stored = 0
    errors = 0
    for future in done:
        item = futures[future]
        reference = future.result()
        if reference is _ERROR:
            errors += 1
            continue
        listing = (reference or {}).get("listing") or {}
        found = float(listing.get("price") or 0) > 0
        if found:
            _store(item["id"], listing)
            stored += 1
        _record_attempt(item["id"], found)
    if errors and errors * 2 >= len(done):
        _start_cooldown()
    return stored


def substitutes_with_live_prices(medicine_id, limit=5,
                                 alternatives=DEFAULT_ALTERNATIVES,
                                 budget_seconds=DEFAULT_BUDGET_SECONDS):
    """get_substitutes(), after refreshing prices that are too old to support a saving."""
    result = get_substitutes(medicine_id, limit=max(limit, alternatives))
    if not live_prices_enabled() or not result.get("success"):
        return get_substitutes(medicine_id, limit=limit) if result.get("success") else result
    if result.get("comparison_status") in SKIP_STATUSES:
        return get_substitutes(medicine_id, limit=limit)

    medicine = result.get("original_medicine") or {}
    # The cheapest options first: those are the ones a saving would be shown for.
    candidates = [medicine] + list(result.get("same_composition_options") or [])[:alternatives]
    stored = refresh_prices(candidates, budget_seconds=budget_seconds)

    refreshed = get_substitutes(medicine_id, limit=limit)
    refreshed["live_prices_checked"] = stored
    return refreshed
