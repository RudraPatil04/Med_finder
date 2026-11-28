"""Live lookups against the India Drug Information Service (DrugDB/NRCeS)."""

import logging
import os
import re

import requests

from backend.medicine_data import (
    extract_pack_size, extract_release_type, normalize_medication_text, parse_composition,
)

logger = logging.getLogger(__name__)

DRUGDB_BASE_URL = "https://drugdb.in"
DRUGDB_TIMEOUT_SECONDS = 8
DRUGDB_SOURCE = "DrugDB · HealthPilot India drug catalog"

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


def _record_matches(record, ingredients, medicine):
    """Exact ingredient/strength/form/release check against a DrugDB v1 record."""
    components = parse_composition(record.get("composition"))
    if not components or len(components) != len(ingredients):
        return False
    unmatched = list(components)
    for target in ingredients:
        target_name = _ingredient_core(target["ingredient_name"])
        target_amount, target_unit = _amount_and_unit(target["ingredient_strength"])
        index = next((
            i for i, row in enumerate(unmatched)
            if target_name and _ingredient_core(row["ingredient_name"]) == target_name
            and _amount_and_unit(row["ingredient_strength"]) == (target_amount, target_unit)
        ), None)
        if index is None or target_amount is None:
            return False
        unmatched.pop(index)
    requested_form = _clean(medicine.get("dosage_form")).casefold()
    form = _clean(record.get("dosage_form") or record.get("name")).casefold()
    form_word = next((token for token in _FORM_WORDS if token in requested_form), "")
    if form_word and _FORM_WORDS[form_word] not in form:
        return False
    requested_release = _clean(medicine.get("release_type")).upper()
    if requested_release == "IR":
        requested_release = ""
    if (extract_release_type(record.get("name")) or "") != requested_release:
        return False
    return True


def search_live_alternatives(medicine, limit=10):
    """Find same-composition products in the live DrugDB catalog at request time.

    A match only shows that a catalog record lists the same ingredients,
    strengths, form and release markers. DrugDB gives no equivalence decision
    and no current selling price, so these are never called substitutes.
    """
    medicine = dict(medicine)
    ingredients = parse_composition(medicine.get("composition"))
    if not ingredients or not medicine.get("dosage_form"):
        return {"success": True, "source": DRUGDB_SOURCE, "count": 0, "matches": [],
                "message": "Complete ingredient, strength, and dosage-form data are required for a live catalog lookup."}
    query = " ".join(f"{row['ingredient_name']} {row['ingredient_strength']}" for row in ingredients)
    headers = {"User-Agent": "MED-FINDER/1.0", "Accept": "application/json"}
    if os.environ.get("DRUGDB_API_KEY"):
        headers["X-Api-Key"] = os.environ["DRUGDB_API_KEY"]
    try:
        response = requests.get(
            f"{DRUGDB_BASE_URL}/v1/search",
            params={"q": query, "type": "drug", "limit": 50},
            headers=headers,
            timeout=DRUGDB_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Live DrugDB lookup failed for %r: %s", query, exc)
        return {"success": False, "source": DRUGDB_SOURCE, "count": 0, "matches": [],
                "message": "The live catalog lookup is unavailable right now."}

    records = payload.get("results", []) if isinstance(payload, dict) else []
    own_name = normalize_medication_text(medicine.get("medicine_name"))
    matches = []
    seen = set()
    for record in records:
        if not isinstance(record, dict) or not _record_matches(record, ingredients, medicine):
            continue
        product_id = _clean(record.get("product_id"))
        if not product_id or product_id in seen or normalize_medication_text(record.get("name")) == own_name:
            continue
        seen.add(product_id)
        matches.append({
            "medicine_name": _clean(record.get("name")) or "Catalog medicine",
            "manufacturer": _clean(record.get("manufacturer")),
            "composition": _clean(record.get("composition")),
            "dosage_form": _clean(record.get("dosage_form")),
            "pack_size": extract_pack_size(record.get("packaging")),
            "release_characteristics": extract_release_type(record.get("name")) or "No release marker in name",
            "license_status": "Not stated",
            "product_concept_id": product_id,
            "source_url": None,
            "price": None,
            "classification": "Same recorded composition · equivalence not confirmed",
            "verification_note": "The live catalog lists the same ingredients, strength and form. It does not confirm therapeutic equivalence or give a current price.",
        })
        if len(matches) >= max(0, min(limit, 20)):
            break

    return {
        "success": True,
        "source": DRUGDB_SOURCE,
        "query": query,
        "count": len(matches),
        "api_total": payload.get("products", len(records)) if isinstance(payload, dict) else len(records),
        "matches": matches,
        "message": "Live catalog matches are shown for ingredient/form review; the catalog does not certify these products as interchangeable.",
    }
