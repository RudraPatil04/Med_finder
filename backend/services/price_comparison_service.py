"""Request-time, composition- and pack-matched PharmEasy price comparison."""

import json
import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from backend.medicine_data import extract_release_type, normalize_medication_text, parse_composition

PHARMEASY_SEARCH_URL = "https://pharmeasy.in/search/all"
PHARMEASY_TIMEOUT_SECONDS = 10
PRICE_SOURCE = "PharmEasy listing price"


def _number(value):
    try:
        return float(str(value).replace(",", "").strip()) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _ingredient_core(value):
    words = re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).split()
    salts = {"calcium", "sodium", "potassium", "hydrochloride", "hydrobromide", "sulfate", "sulphate", "phosphate", "maleate", "fumarate", "succinate", "besylate", "mesylate", "tartrate", "acetate", "hcl"}
    while words and words[-1] in salts:
        words.pop()
    return _INGREDIENT_SYNONYMS.get(" ".join(words), " ".join(words))


# Pharmacy listings spell aliases out (`Paracetamol / Acetaminophen`) while the
# local dataset keeps one generic name. Collapse the common pairs before the
# signatures are compared; unmatched strings pass through unchanged.
_INGREDIENT_SYNONYMS = {
    "paracetamol acetaminophen": "paracetamol",
    "acetaminophen": "paracetamol",
    "vitamin b1 thiamine": "thiamine",
    "vitamin b2 riboflavin": "riboflavin",
    "vitamin b3 nicotinic acid niacin": "niacinamide",
    "vitamin b6 pyridoxine": "pyridoxine",
    "vitamin b7 biotin vitamin h": "biotin",
    "vitamin b9 folic acid folate": "folic acid",
    "vitamin b12 mecobalamin cynocobalamin methylcobalamin": "methylcobalamin",
    "vitamin c ascorbic acid": "ascorbic acid",
}


def _signature_from_composition(composition):
    parsed = parse_composition(composition)
    signature = []
    for row in parsed:
        amount = re.search(r"\d+(?:\.\d+)?", row["ingredient_strength"])
        unit = re.search(r"(?:mcg|μg|µg|mg|g|ml|iu|units?|%)", row["ingredient_strength"], re.I)
        if not amount:
            return ()
        numeric = float(amount.group())
        strength = f"{numeric:g}{(unit.group().casefold() if unit else '')}"
        signature.append((_ingredient_core(row["ingredient_name"]), strength))
    return tuple(sorted(signature))


def _search_payload(query):
    response = requests.get(
        PHARMEASY_SEARCH_URL,
        params={"name": query},
        headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html,application/xhtml+xml"},
        timeout=PHARMEASY_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        return []
    data = json.loads(script.string)
    props = data.get("props", {}).get("pageProps", {})
    items = (props.get("productList") or []) + (props.get("genericsProductList") or [])
    # PharmEasy sometimes nests its suggested cheaper product in each result.
    expanded = list(items)
    for item in items:
        nested = (item.get("productSubstitutionAttributes") or {}).get("substituteProduct")
        if isinstance(nested, dict):
            expanded.append(nested)
    unique = {}
    for item in expanded:
        if isinstance(item, dict):
            key = item.get("productId") or item.get("slug") or item.get("name")
            if key:
                unique.setdefault(str(key), item)
    return list(unique.values())


def _item_signature(item):
    entries = item.get("compositions") or []
    parsed = []
    for entry in entries:
        if isinstance(entry, dict):
            parsed.extend(parse_composition(entry.get("name")))
    if not parsed:
        return ()
    signature = []
    for row in parsed:
        amount = re.search(r"\d+(?:\.\d+)?", row["ingredient_strength"])
        unit = re.search(r"(?:mcg|μg|µg|mg|g|ml|iu|units?|%)", row["ingredient_strength"], re.I)
        if not amount:
            return ()
        signature.append((_ingredient_core(row["ingredient_name"]), f"{float(amount.group()):g}{unit.group().casefold() if unit else ''}"))
    return tuple(sorted(signature))


def _pack_quantity(item):
    quantity = _number(item.get("packQuantityValue"))
    label = " ".join(str(item.get(key) or "") for key in ("measurementUnit", "subtitleText", "name")).casefold()
    unit_match = re.search(r"\b(tablet|tablets|capsule|capsules|softgel|softgels|ml|sachet|sachets)\b", label)
    unit = unit_match.group(1).rstrip("s") if unit_match else None
    if not quantity:
        # Listings carry the pack only in text: "10 Tablet(s) in Strip".
        quantity_match = re.search(r"(\d+)\s*(?:tablet|capsule|softgel|ml|sachet)", label)
        quantity = int(quantity_match.group(1)) if quantity_match else None
    return (int(quantity), unit) if quantity and unit else None


def _requested_pack(medicine):
    value = str(medicine.get("pack_size") or "")
    match = re.search(r"(\d+)\s*(tablets?|capsules?|ml|sachets?)", value, re.I)
    if not match:
        return None
    return int(match.group(1)), match.group(2).casefold().rstrip("s")


def _has_form(item, form):
    query = str(form or "").casefold()
    label = " ".join(str(item.get(key) or "") for key in ("name", "subtitleText", "measurementUnit", "compositions")).casefold()
    aliases = {
        "tablet": ("tablet", "tab"), "capsule": ("capsule", "cap"),
        "syrup": ("syrup", "oral liquid"), "injection": ("injection", "injectable"),
        "cream": ("cream",), "ointment": ("ointment",), "solution": ("solution",),
    }
    terms = next((values for key, values in aliases.items() if key in query), ())
    return bool(terms) and any(term in label for term in terms)


def _matches_brand(item, medicine):
    expected = normalize_medication_text(medicine.get("brand_name"))
    if not expected:
        expected = normalize_medication_text(medicine.get("medicine_name"))
    actual_brand = normalize_medication_text(item.get("consumerBrandName"))
    if actual_brand:
        if actual_brand == expected:
            return True
        # The dataset brand sometimes keeps the strength ("Dolo 650").
        remainder = expected[len(actual_brand):] if expected.startswith(actual_brand) else ""
        return bool(remainder) and re.fullmatch(
            r"\d+(?:mg|mcg|g|ml)?(?:tablet|tablets|tab|capsule|capsules|cap)?", remainder
        ) is not None
    # A missing brand field is the only time the title is used.
    actual_name = normalize_medication_text(item.get("name"))
    return bool(actual_name and expected) and actual_name.startswith(expected)


def _as_listing(item, checked_at):
    sale_price = _number(item.get("salePriceDecimal"))
    mrp = _number(item.get("mrpDecimal"))
    return {
        "medicine_name": item.get("name"),
        "brand_name": item.get("consumerBrandName"),
        "manufacturer": item.get("manufacturer"),
        "price": sale_price,
        "mrp": mrp,
        "pack_size": item.get("measurementUnit") or item.get("subtitleText"),
        "pack_quantity": item.get("packQuantityValue"),
        "price_per_unit": item.get("pricePerUnit"),
        "available": bool((item.get("productAvailabilityFlags") or {}).get("isAvailable", True)),
        "source": PRICE_SOURCE,
        "checked_at": checked_at,
        "source_url": f"https://pharmeasy.in/online-medicine-order/{item.get('slug')}" if item.get("slug") else "https://pharmeasy.in/",
    }


_ALLOWED_NAME_WORDS = {
    "tablet", "tablets", "tab", "tabs", "capsule", "capsules", "cap", "caps",
    "softgel", "softgels", "strip", "of", "in", "bottle", "tube", "sachet",
    "sachets", "pack", "box", "blister", "ml", "mg", "mcg", "g", "iu",
    "percent", "oral", "film", "coated", "sr", "er", "xr", "cr", "mr", "xl",
    "dt", "od", "suspension", "syrup", "injection", "cream", "ointment", "gel",
    "drops", "drop", "spray", "inhaler", "solution", "each", "x", "s", "no",
    "nos", "unit", "units", "dose", "vial", "ampoule", "respules", "lotion",
    "shampoo", "soap", "powder", "granules", "emulsion", "infusion", "nasal",
    "eye", "ear", "inhalation",
}


def _name_matches_product(item, medicine):
    """Reject sub-brands the listing adds ("Crocin Advance") for a plain brand."""
    brand_words = set(re.findall(r"[a-z]+", str(medicine.get("brand_name") or "").casefold()))
    allowed = _ALLOWED_NAME_WORDS | brand_words
    return all(token in allowed for token in re.findall(r"[a-z]+", str(item.get("name") or "").casefold()))


def _pick_reference_item(items, medicine, signature, expected_release):
    return next((
        item for item in items
        if _matches_brand(item, medicine)
        and _name_matches_product(item, medicine)
        and _item_signature(item) == signature
        and _has_form(item, medicine.get("dosage_form"))
        and (not expected_release or extract_release_type(item.get("name")) == expected_release)
    ), None)


def find_reference_listing(medicine):
    """Return the exact current listing for this brand, or None.

    The listing must match the recorded ingredient signature, dosage form and
    release type, so a price is never attached to a different product.
    """
    medicine = dict(medicine)
    signature = _signature_from_composition(medicine.get("composition"))
    if not signature or not medicine.get("dosage_form"):
        return None
    query = str(medicine.get("brand_name") or medicine.get("medicine_name") or "").strip()
    if len(query) < 3:
        return None
    expected_release = str(medicine.get("release_type") or "").casefold()
    item = _pick_reference_item(_search_payload(query), medicine, signature, expected_release)
    if not item:
        return None
    listing = _as_listing(item, datetime.now(timezone.utc).isoformat(timespec="seconds"))
    if not listing.get("price") or listing["price"] <= 0:
        return None
    pack = _pack_quantity(item)
    if pack:
        listing["price_per_unit_value"] = round(listing["price"] / pack[0], 4)
        listing["pack_norm"] = f"{pack[0]} {pack[1]}" if pack[1] == "ml" else f"{pack[0]} {pack[1]}s"
    return {"listing": listing, "pack": pack, "item": item}


def compare_current_prices(medicine, limit=10):
    """Return lower current listings only when exact ingredients, form and pack compare."""
    medicine = dict(medicine)
    signature = _signature_from_composition(medicine.get("composition"))
    pack = _requested_pack(medicine)
    if not signature or not medicine.get("dosage_form"):
        return {"success": True, "comparison_available": False, "reference": None, "matches": [],
                "message": "Complete active ingredients, strengths, and dosage form are required for a price comparison."}

    ingredient_query = " ".join(
        f"{ingredient['ingredient_name']} {ingredient['ingredient_strength']}"
        for ingredient in parse_composition(medicine.get("composition"))
    ) + " " + str(medicine.get("dosage_form"))
    reference_query = str(medicine.get("brand_name") or medicine.get("medicine_name") or "").strip()
    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        catalog = _search_payload(ingredient_query)
        reference_items = catalog if normalize_medication_text(reference_query) == normalize_medication_text(ingredient_query) else _search_payload(reference_query)
    except (requests.RequestException, ValueError, TypeError) as exc:
        return {"success": False, "comparison_available": False, "reference": None, "matches": [],
                "message": f"Live pharmacy pricing is temporarily unavailable ({type(exc).__name__})."}

    expected_release = str(medicine.get("release_type") or "").casefold()
    reference_item = _pick_reference_item(reference_items, medicine, signature, expected_release)
    reference = _as_listing(reference_item, checked_at) if reference_item else None
    compare_pack = pack or (_pack_quantity(reference_item) if reference_item else None)
    if reference and reference.get("price") and compare_pack:
        reference["price_per_unit_value"] = round(reference["price"] / compare_pack[0], 4)

    same_product_candidates = []
    for item in catalog:
        item_signature = _item_signature(item)
        item_pack = _pack_quantity(item)
        if item_signature != signature or not _has_form(item, medicine.get("dosage_form")):
            continue
        candidate_release = extract_release_type(item.get("name"))
        if expected_release and candidate_release != expected_release:
            continue
        if not expected_release and candidate_release:
            continue
        if compare_pack and item_pack != compare_pack:
            continue
        if not item.get("productAvailabilityFlags", {}).get("isAvailable", True):
            continue
        listing = _as_listing(item, checked_at)
        if not listing["price"] or listing["price"] <= 0:
            continue
        if reference_item and str(item.get("productId")) == str(reference_item.get("productId")):
            continue
        same_product_candidates.append((item, listing, item_pack))

    cheaper = []
    if not same_product_candidates:
        available = False
        message = "No current listing with the same ingredients, form and pack size is available for comparison."
    elif reference and reference.get("price") and compare_pack:
        for item, listing, item_pack in same_product_candidates:
            if listing["price"] >= reference["price"]:
                continue
            listing["savings_amount"] = round(reference["price"] - listing["price"], 2)
            listing["savings_percent"] = round(listing["savings_amount"] / reference["price"] * 100, 1)
            cheaper.append(listing)
        cheaper.sort(key=lambda row: row["price"])
        available = True
        message = "Prices are live PharmEasy listings for the same recorded ingredients, form, release characteristics, and pack size."
    else:
        # Show current matching prices for context, but never claim cheaper without
        # a current price for the searched product and a comparable pack.
        cheaper = [listing for _, listing, _ in same_product_candidates]
        cheaper.sort(key=lambda row: row["price"])
        available = False
        if not reference:
            message = "Current matching pharmacy prices are shown, but the searched product has no exact current listing here, so cheaper-than-reference cannot be confirmed."
        else:
            message = "Current matching pharmacy prices are shown, but the searched product pack is unknown, so a pack-matched savings comparison cannot be confirmed."

    return {
        "success": True,
        "comparison_available": available,
        "source": PRICE_SOURCE,
        "checked_at": checked_at,
        "reference": reference,
        "reference_pack": compare_pack,
        "matches": cheaper[:max(0, min(limit, 20))],
        "count": len(cheaper),
        "message": message,
    }
