"""DrugSetu product lookups that fill missing catalog fields.

Only product facts are fetched: structured composition, pack size and MRP.
MRP is stored in its own column and never in `price`, so it cannot support a
saving claim. A row is only written when brand, dosage form and strength all
match the returned product exactly.
"""

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from backend.database import execute_non_query, execute_query, get_connection
from backend.medicine_data import (
    extract_pack_size, extract_release_type, make_product_key,
    normalize_medication_text, parse_composition,
)
from backend.services.api_cache import (
    ensure_api_search_cache, has_recent_api_search, record_api_search,
)
from backend.services.drug_catalog_service import ensure_catalog_schema

DRUGSETU_BASE_URL = "https://api.drugsetu.com/v1"
DRUGSETU_SOURCE = "DrugSetu India medicine database"
DRUGSETU_KEY_ENV = "DRUGSETU_API_KEY"
DRUGSETU_TIMEOUT_SECONDS = 20
DRUGSETU_MIN_INTERVAL_SECONDS = 2.0
DRUGSETU_SUCCESS_TTL_DAYS = 30
DRUGSETU_MISS_TTL_DAYS = 7
DRUGSETU_CACHE_KEY = "drugsetu:{medicine_id}"

_FORM_FAMILIES = (
    ("powder for injection", "injection"),
    ("tablet", "tablet"),
    ("capsule", "capsule"),
    ("softgel", "capsule"),
    ("syrup", "syrup"),
    ("suspension", "suspension"),
    ("emulsion", "emulsion"),
    ("injection", "injection"),
    ("infusion", "infusion"),
    ("respule", "injection"),
    ("cream", "cream"),
    ("ointment", "ointment"),
    ("lotion", "lotion"),
    ("gel", "gel"),
    ("shampoo", "shampoo"),
    ("soap", "soap"),
    ("kit", "kit"),
    ("sachet", "sachet"),
    ("granule", "powder"),
    ("powder", "powder"),
    ("inhaler", "inhaler"),
    ("spray", "spray"),
    ("suppositor", "suppository"),
    ("drop", "drops"),
    ("solution", "solution"),
    ("patch", "patch"),
    ("pouch", "pouch"),
)

_AMOUNT = re.compile(
    r"(\d+(?:\.\d+)?)\s*(mcg|µg|μg|mg|kg|gm|ml|l|iu|units?|g|%)",
    re.IGNORECASE,
)
_PACK = re.compile(
    r"\b(strip|blister|pack|bottle|box|vial|tube|sachet)\s+of\s+"
    r"(\d+(?:\.\d+)?)\s*([a-z]+)?",
    re.IGNORECASE,
)
_PACK_UNITS = {
    "tablet": "tablet", "tablets": "tablet", "capsule": "capsule", "capsules": "capsule",
    "softgel": "softgel", "softgels": "softgel", "vial": "vial", "vials": "vial",
    "ml": "ml", "g": "g", "gm": "g", "mg": "mg", "kit": "kit", "kits": "kit",
    "sachet": "sachet", "sachets": "sachet", "injection": "injection",
    "tube": "tube", "bottle": "bottle",
}
# A trailing dosage-form word names the content, not the container
# ("vial of 1 Injection" is one vial).
_FORM_WORDS = {
    "injection", "infusion", "syrup", "suspension", "cream", "lotion", "gel",
    "ointment", "powder", "solution", "spray", "drops",
}

_throttle_lock = threading.Lock()
_next_call_at = 0.0


class DrugSetuUnavailable(RuntimeError):
    """Raised when the API key is missing or rejected."""


class DrugSetuQuotaExceeded(RuntimeError):
    """Raised when the plan's request allowance is used up."""


# ============================================================
# Configuration and transport
# ============================================================

def is_configured():
    return bool(os.environ.get(DRUGSETU_KEY_ENV, "").strip())


def _throttle():
    global _next_call_at
    with _throttle_lock:
        wait = _next_call_at - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _next_call_at = time.monotonic() + DRUGSETU_MIN_INTERVAL_SECONDS


def _get(path):
    key = os.environ.get(DRUGSETU_KEY_ENV, "").strip()
    if not key:
        raise DrugSetuUnavailable("DRUGSETU_API_KEY is not set")

    request = urllib.request.Request(
        DRUGSETU_BASE_URL + path,
        headers={"X-API-Key": key, "Accept": "application/json"},
    )
    for attempt in range(3):
        _throttle()
        try:
            with urllib.request.urlopen(request, timeout=DRUGSETU_TIMEOUT_SECONDS) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            if exc.code == 429:
                if "trial limit" in body.lower() or "request limit" in body.lower():
                    raise DrugSetuQuotaExceeded(body[:200]) from exc
                time.sleep(2.5 * (attempt + 1))
                continue
            if exc.code in (401, 403):
                raise DrugSetuUnavailable(f"API key rejected ({exc.code})") from exc
            raise RuntimeError(f"DrugSetu HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            if attempt == 2:
                raise RuntimeError(f"DrugSetu request failed: {type(exc).__name__}") from exc
            time.sleep(1.5)
    raise RuntimeError("DrugSetu rate limited")


def search_products(query):
    """Return product summaries for a search query."""
    data = _get("/products/search?q=" + urllib.parse.quote(query))
    return data if isinstance(data, list) else []


def fetch_product(product_id):
    """Return the full product record, including packaging and MRP."""
    data = _get("/products/" + urllib.parse.quote(product_id))
    return data if isinstance(data, dict) else None


# ============================================================
# Matching
# ============================================================

def _token(value):
    parts = re.findall(r"[a-z0-9]+", str(value or "").lower())
    return parts[0] if parts else ""


def _form_family(value):
    haystack = normalize_medication_text(value)
    for marker, family in _FORM_FAMILIES:
        if marker in haystack:
            return family
    return None


def _strength_pairs(value):
    text = str(value or "").casefold().replace("μ", "u").replace("µ", "u")
    pairs = []
    for amount, unit in _AMOUNT.findall(text):
        unit = unit.casefold().rstrip("s")
        unit = {"gm": "g", "ug": "mcg"}.get(unit, unit)
        pairs.append((float(amount), unit))
    return sorted(pairs)


def _strength_matches(strength, composition):
    ours = _strength_pairs(strength)
    theirs = _strength_pairs(composition)
    return bool(ours) and ours == theirs


def _pack_from_text(text):
    pack = extract_pack_size(text)
    if pack and not pack.endswith(" units"):
        return pack
    match = _PACK.search(str(text or ""))
    if not match:
        return None
    container = match.group(1).casefold()
    word = (match.group(3) or "").casefold()
    unit = _PACK_UNITS.get(word)
    if unit in (None, "injection"):
        unit = container if word in _FORM_WORDS else unit
    if not unit:
        return None
    quantity = float(match.group(2))
    return f"{int(quantity) if quantity.is_integer() else quantity} {unit}"


def match_product(medicine, candidates):
    """Pick the one catalog product that matches brand, form and strength."""
    brand = _token(medicine.get("brand_name") or medicine.get("medicine_name"))
    form = _form_family(medicine.get("dosage_form"))
    if not brand or not form:
        return None

    matches = []
    for candidate in candidates or []:
        name = candidate.get("product_name")
        if _token(name) != brand:
            continue
        if _form_family(name) != form:
            continue
        if not _strength_matches(medicine.get("strength"), candidate.get("composition")):
            continue
        matches.append(candidate)

    if not matches:
        return None
    # Different compositions for the same brand/form/strength mean the match is
    # ambiguous; leave the row untouched rather than guess.
    if len({normalize_medication_text(item.get("composition")) for item in matches}) > 1:
        return None
    return matches[0]


# ============================================================
# Enrichment
# ============================================================

def _query_for(medicine):
    brand = str(medicine.get("brand_name") or medicine.get("medicine_name") or "").strip()
    numbers = re.findall(r"\d+(?:\.\d+)?", str(medicine.get("strength") or ""))
    return f"{brand} {numbers[0]}".strip() if numbers else brand


def _ingredient_rows(medicine_id, composition):
    return [
        (
            medicine_id, item["ingredient_name"], item["ingredient_name_norm"],
            item["ingredient_strength"], item["ingredient_strength_norm"],
        )
        for item in parse_composition(composition)
    ]


def _write(medicine, detail):
    medicine_id = medicine["id"]
    fields = {}

    composition = str(detail.get("composition") or "").strip()
    if not medicine.get("composition_complete") and parse_composition(composition):
        pack_size = medicine.get("pack_size") or _pack_from_text(detail.get("packaging"))
        fields["composition"] = composition
        fields["composition_complete"] = 1
        fields["composition_source"] = DRUGSETU_SOURCE
        fields["product_key"] = make_product_key(
            medicine.get("medicine_name"), medicine.get("brand_name"), medicine.get("strength"),
            medicine.get("dosage_form"), composition,
            medicine.get("release_type") or extract_release_type(detail.get("product_name")),
            pack_size,
        )

    if not medicine.get("pack_size"):
        pack_size = _pack_from_text(detail.get("packaging"))
        if pack_size:
            fields["pack_size"] = pack_size

    mrp = detail.get("mrp")
    if medicine.get("mrp") in (None, "") and isinstance(mrp, (int, float)) and mrp > 0:
        fields["mrp"] = float(mrp)
        fields["price_type"] = "mrp"

    if not fields:
        return []

    assignments = ", ".join(f"{name} = ?" for name in fields)
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE medicines SET {assignments} WHERE id = ?",
            (*fields.values(), medicine_id),
        )
        if "composition" in fields:
            cursor.execute("DELETE FROM medicine_ingredients WHERE medicine_id = ?", (medicine_id,))
            cursor.executemany(
                "INSERT INTO medicine_ingredients("
                "medicine_id, ingredient_name, ingredient_name_norm, "
                "ingredient_strength, ingredient_strength_norm"
                ") VALUES (?,?,?,?,?)",
                _ingredient_rows(medicine_id, composition),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return list(fields)


def enrich_medicine(medicine_id):
    """Fill missing composition, pack size or MRP for one medicine.

    Runs at most once per medicine per cache window, and never overwrites a
    complete composition or an existing price/MRP.
    """
    ensure_catalog_schema()
    ensure_api_search_cache()
    medicine = execute_query("SELECT * FROM medicines WHERE id = ?", (medicine_id,))
    if not medicine:
        return {"status": "not_found"}
    medicine = dict(medicine[0])

    if not is_configured():
        return {"status": "not_configured"}
    if (medicine["composition_complete"] and medicine["pack_size"]
            and medicine["mrp"] is not None):
        return {"status": "complete"}

    cache_key = DRUGSETU_CACHE_KEY.format(medicine_id=medicine_id)
    if has_recent_api_search(cache_key, DRUGSETU_SUCCESS_TTL_DAYS, DRUGSETU_MISS_TTL_DAYS):
        return {"status": "cached"}

    try:
        candidates = search_products(_query_for(medicine))
    except DrugSetuQuotaExceeded as exc:
        return {"status": "quota_exceeded", "reason": str(exc)}
    except DrugSetuUnavailable as exc:
        # A key problem must not suppress the retry window after it is fixed.
        return {"status": "unavailable", "reason": str(exc)}
    except RuntimeError as exc:
        # Transport failures are not cached, so a recovered API is used again.
        return {"status": "failed", "reason": str(exc)}

    product = match_product(medicine, candidates)
    if not product:
        record_api_search(cache_key, 0)
        return {"status": "no_match", "candidates": len(candidates)}

    try:
        detail = fetch_product(product["product_id"])
    except RuntimeError as exc:
        return {"status": "failed", "reason": str(exc)}
    if not detail:
        record_api_search(cache_key, 0)
        return {"status": "no_detail"}

    written = _write(medicine, detail)
    record_api_search(cache_key, 1 if written else 0)
    return {"status": "enriched" if written else "nothing_to_write", "fields": written}
