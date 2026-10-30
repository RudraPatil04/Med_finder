"""Live lookups against the India Drug Information Service (DrugDB/NRCeS)."""

import re

import requests

from backend.medicine_data import parse_composition

DRUGDB_BASE_URL = "https://drugdb.in"
DRUGDB_TIMEOUT_SECONDS = 8
DRUGDB_SOURCE = "Drug Information Service · NRCeS Common Drug Codes for India"

_FORM_WORDS = {
    "tablet": "tablet", "tablets": "tablet", "tab": "tablet",
    "capsule": "capsule", "capsules": "capsule", "cap": "capsule",
    "injection": "injection", "syrup": "syrup", "oral solution": "solution",
}


def _clean(value):
    return " ".join(str(value or "").strip().split())


def _ingredient_core(value):
    words = re.sub(r"[^a-z0-9]+", " ", _clean(value).casefold()).split()
    salt_words = {"calcium", "sodium", "potassium", "hydrochloride", "hydrobromide", "sulfate", "sulphate", "phosphate", "maleate", "fumarate", "succinate", "besylate", "mesylate", "tartrate", "acetate", "hcl"}
    while words and words[-1] in salt_words:
        words.pop()
    return " ".join(words)


def _amount_and_unit(value):
    match = re.search(r"(\d+(?:\.\d+)?)\s*(mcg|μg|µg|mg|g|ml|iu|units?|%)?", _clean(value), re.I)
    if not match:
        return None, None
    unit = (match.group(2) or "").casefold().replace("μ", "u").replace("µ", "u")
    unit = {"milligram": "mg", "milligrams": "mg", "microgram": "mcg", "micrograms": "mcg", "gram": "g", "grams": "g", "milliliter": "ml", "milliliters": "ml"}.get(unit, unit)
    return float(match.group(1)), unit


def _target_query(medicine):
    ingredients = parse_composition(medicine.get("composition"))
    if not ingredients:
        return None, []
    chunks = []
    for ingredient in ingredients:
        strength = re.sub(r"(?i)(mcg|μg|µg|mg|g|ml|iu|units?|%)", r" \1", ingredient["ingredient_strength"])
        chunks.append(f"{ingredient['ingredient_name']} {strength}")
    form = _clean(medicine.get("dosage_form")).casefold()
    form_token = next((token for token in _FORM_WORDS if token in form), "")
    form_token = _FORM_WORDS.get(form_token, form_token)
    route = "oral" if form_token in {"tablet", "capsule", "syrup", "solution"} else ""
    release = _clean(medicine.get("release_type"))
    query = " ".join(part for part in [" ".join(chunks), route, release, form_token] if part)
    return query, ingredients


def _matches_candidate(candidate, ingredients, medicine):
    detail = candidate.get("genericDetail") or {}
    generic_name = _clean(detail.get("name") or candidate.get("generic.genericName")).casefold()
    form = _clean(detail.get("doseForm")).casefold()
    components = detail.get("composition") or []
    if not generic_name or not components:
        return False

    # Verify the ingredient identity and strength against the live structured
    # composition rather than relying on a brand-name or search-text match.
    if len(components) != len(ingredients):
        return False
    unmatched = list(components)
    for target in ingredients:
        target_name = _ingredient_core(target["ingredient_name"])
        matched_index = next((
            index for index, row in enumerate(unmatched)
            if target_name and _ingredient_core(row.get("substanceName")) == target_name
        ), None)
        if matched_index is None:
            return False
        component = unmatched.pop(matched_index)
        target_amount, target_unit = _amount_and_unit(target["ingredient_strength"])
        candidate_amount, candidate_unit = _amount_and_unit(component.get("strength"))
        candidate_unit = candidate_unit or _clean(component.get("unit")).casefold().split("/")[0]
        candidate_unit = {"milligram": "mg", "microgram": "mcg", "gram": "g"}.get(candidate_unit, candidate_unit)
        if target_amount is None or candidate_amount is None or target_amount != candidate_amount:
            return False
        if target_unit and candidate_unit and target_unit != candidate_unit:
            return False

    requested_form = _clean(medicine.get("dosage_form")).casefold()
    if requested_form and not any(word in form for word in ("tablet", "capsule", "syrup", "injection", "solution", "cream", "ointment", "suspension") if word in requested_form):
        return False

    requested_release = _clean(medicine.get("release_type")).casefold()
    if requested_release:
        release_markers = {"ir": "conventional release", "xr": "extended release", "er": "extended release", "sr": "sustained release", "cr": "controlled release", "mr": "modified release"}
        expected = release_markers.get(requested_release, requested_release)
        release_text = (generic_name + " " + " ".join(detail.get("synonyms") or [])).casefold()
        if expected not in release_text and requested_release not in release_text:
            return False
    return True


def search_live_alternatives(medicine, limit=10):
    """Find same generic-product concepts from DrugDB at request time.

    Results establish only that DrugDB records share a structured generic
    concept. DrugDB does not return product-pair BE/equivalence decisions or
    live price quotations, so these are never called verified substitutes.
    """
    query, ingredients = _target_query(dict(medicine))
    if not query or not ingredients:
        return {"success": True, "source": DRUGDB_SOURCE, "count": 0, "matches": [],
                "message": "Complete ingredient, strength, and dosage-form data are required for a live API lookup."}
    try:
        response = requests.get(
            f"{DRUGDB_BASE_URL}/search",
            params={"q": query, "type": "medicine", "detail": "full", "limit": min(max(limit * 5, 20), 100)},
            timeout=DRUGDB_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        return {"success": False, "source": DRUGDB_SOURCE, "count": 0, "matches": [],
                "message": f"The live India medicine lookup is temporarily unavailable ({type(exc).__name__})."}

    products = payload.get("results", []) if isinstance(payload, dict) else []
    matches = []
    seen = set()
    for product in products:
        if not isinstance(product, dict) or not _matches_candidate(product, ingredients, dict(medicine)):
            continue
        product_id = _clean(product.get("medicineSctid"))
        if not product_id or product_id in seen:
            continue
        seen.add(product_id)
        detail = product.get("genericDetail") or {}
        generic_text = " ".join([_clean(detail.get("name"))] + list(detail.get("synonyms") or [])).casefold()
        release_characteristics = next((
            label for phrase, label in (
                ("conventional release", "Conventional release"),
                ("immediate release", "Immediate release"),
                ("extended release", "Extended release"),
                ("sustained release", "Sustained release"),
                ("controlled release", "Controlled release"),
                ("modified release", "Modified release"),
                ("delayed release", "Delayed release"),
            ) if phrase in generic_text
        ), "Not specified by source")
        matches.append({
            "medicine_name": product.get("medicineName") or product.get("brand.brandName"),
            "brand_name": product.get("brand.brandName"),
            "manufacturer": product.get("manufacturer.manufacturerName"),
            "license_status": product.get("licenseStatus") or "Not stated",
            "composition": detail.get("name") or product.get("generic.genericName"),
            "dosage_form": detail.get("doseForm"),
            "release_characteristics": release_characteristics,
            "route": detail.get("routeOfAdministration"),
            "generic_concept_id": product.get("generic.sctid") or detail.get("genericSctid"),
            "product_concept_id": product_id,
            "product_updated_at": product.get("lastUpdatedon"),
            "generic_concept_updated_at": detail.get("lastUpdatedOn"),
            "source_url": f"{DRUGDB_BASE_URL}/search?id={product_id}&type=medicine",
            "price": None,
            "classification": "Same recorded generic concept · equivalence not confirmed",
            "verification_note": "Live catalog data supports an ingredient/strength/form match only. Product-level therapeutic equivalence and current price are not provided by this API.",
        })
        if len(matches) >= max(0, min(limit, 20)):
            break

    return {
        "success": True,
        "source": DRUGDB_SOURCE,
        "query": query,
        "count": len(matches),
        "api_total": payload.get("total", len(products)) if isinstance(payload, dict) else len(products),
        "matches": matches,
        "message": "Live catalog matches are shown for ingredient/form review; the API does not certify these products as interchangeable.",
    }
