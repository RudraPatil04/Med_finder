"""
Explain a search outcome to the user when it cannot give a price.

Two jobs:
1. Try one more honest way to help: for a US-only (RxNorm) record, find the
   active ingredient and list priced Indian catalog products that contain
   ONLY that ingredient. These are listed as "same ingredient", never as
   substitutes - strength/form may differ.
2. Otherwise say plainly why there is no price, so a blank card is never
   mistaken for "free" or "broken".
"""

from backend.database import execute_query
from backend.services.external_api_service import lookup_rxnorm_ingredient

RELATED_LIMIT = 5

CAUTION = (
    "Same ingredient does not mean same medicine or dose. "
    "Confirm with a doctor or pharmacist before using or switching."
)


def _is_unpriced_foreign(row):
    return (row.get("source") == "api" and not row.get("price")
            and not row.get("composition_complete"))


def related_by_ingredient(medicines):
    """Priced Indian-catalog single-ingredient products for a US-only record."""
    for row in medicines:
        if not _is_unpriced_foreign(row) or not row.get("rxnorm_rxcui"):
            continue
        ingredient = lookup_rxnorm_ingredient(row["rxnorm_rxcui"])
        if not ingredient:
            continue
        try:
            rows = execute_query(
                """
                SELECT m.id, m.medicine_name, m.brand_name, m.strength,
                       m.dosage_form, m.price, m.price_source, m.price_checked_at
                FROM medicine_ingredients i
                JOIN medicines m ON m.id = i.medicine_id
                WHERE i.ingredient_name_norm = ?
                  AND m.price > 0 AND m.source != 'api'
                  AND (SELECT COUNT(*) FROM medicine_ingredients x
                       WHERE x.medicine_id = m.id) = 1
                ORDER BY m.price ASC
                LIMIT ?
                """,
                (ingredient.lower(), RELATED_LIMIT),
            )
        except Exception as error:
            print(f"Ingredient lookup failed: {error}")
            return ingredient, []
        return ingredient, [dict(r) for r in rows]
    return None, []


def build_notice(keyword, medicines, issues, ingredient=None, related=None):
    """Return {kind, message} or None when the results need no caution."""
    issues = list(issues or [])
    related = related or []

    if not medicines:
        if "cached_miss" in issues:
            return {"kind": "recently_searched", "message": (
                f"\"{keyword}\" was searched a short while ago and nothing was found. "
                "To avoid overloading the online catalogs, a repeat lookup is paused for "
                "up to a day. Try the generic (ingredient) name or check the spelling.")}
        unreachable = [i for i in issues if i != "cached_miss"]
        if unreachable:
            return {"kind": "lookup_incomplete", "message": (
                "Some online catalogs could not be reached (" + ", ".join(unreachable) +
                "), so this search may be incomplete. Please try again in a moment.")}
        return {"kind": "not_found", "message": (
            f"No match for \"{keyword}\" in the local catalog, the India drug catalog, "
            "PharmEasy or RxNorm. Check the spelling, or try the generic name or ingredient. "
            "A pharmacist can also confirm what is sold under that name.")}

    if any(_is_unpriced_foreign(m) for m in medicines):
        text = ("This result comes only from the US RxNorm drug database. It has no Indian "
                "price and no verified composition, so it cannot be compared and may not be "
                "sold in India.")
        if ingredient and related:
            text += (f" Indian-catalog products whose only ingredient is {ingredient} "
                     f"are listed below. {CAUTION}")
        elif ingredient:
            text += (f" Its active ingredient is {ingredient}, but no priced single-ingredient "
                     "Indian product with that ingredient was found.")
        text += " Ask a doctor or pharmacist about what is available locally."
        return {"kind": "no_indian_price", "message": text,
                "ingredient": ingredient}

    if any(not m.get("price") for m in medicines):
        return {"kind": "some_prices_missing", "message": (
            "Some results have no recorded price, so they are not included in any price comparison.")}

    unreachable = [i for i in issues if i != "cached_miss"]
    if unreachable:
        return {"kind": "lookup_incomplete", "message": (
            "Some online catalogs could not be reached (" + ", ".join(unreachable) +
            "), so these results may be incomplete.")}
    return None
