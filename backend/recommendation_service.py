"""Return evidence-backed equivalents plus clearly-labelled same-composition options."""

import re

from backend.database import execute_query
from backend.services.drug_catalog_service import enrich_medicine_composition
from backend.services.price_enrichment_service import enrich_medicine_price


PRICE_MAX_AGE_DAYS = 30
MARKET_JURISDICTION = "IN"
PACK_QUANTITY = re.compile(r"(\d+)\s*(tablets?|capsules?|softgels?|ml|sachets?|units?)\b", re.I)


def _ingredient_signature(medicine_id):
    rows = execute_query(
        "SELECT ingredient_name_norm, ingredient_strength_norm "
        "FROM medicine_ingredients WHERE medicine_id = ?",
        (medicine_id,),
    )
    return tuple(sorted((row["ingredient_name_norm"], row["ingredient_strength_norm"]) for row in rows))


def _fresh_price(value):
    return bool(value) and execute_query(
        "SELECT 1 WHERE datetime(?) >= datetime('now', ?) LIMIT 1",
        (value, f"-{PRICE_MAX_AGE_DAYS} days"),
    ) != []


def _pack_quantity(pack_size):
    match = PACK_QUANTITY.search(str(pack_size or ""))
    if not match:
        return None
    unit = match.group(2).casefold().rstrip("s")
    return int(match.group(1)), unit


def _price_per_unit(price, pack_size):
    quantity = _pack_quantity(pack_size)
    price = float(price or 0)
    if not quantity or price <= 0:
        return None
    return price / quantity[0], quantity[1]


def _candidate_rows(medicine, signature, target_id, require_release_match=False,
                    require_pack_match=True):
    if not signature:
        return []
    sql = """SELECT m.*,
                    GROUP_CONCAT(i.ingredient_name_norm || ':' || i.ingredient_strength_norm, '|') AS ingredient_key
             FROM medicines m
             JOIN medicine_ingredients i ON i.medicine_id = m.id
             WHERE m.id != ?
               AND m.composition_complete = 1
               AND lower(m.strength) = lower(?)
               AND lower(m.dosage_form) = lower(?)"""
    params = [target_id, medicine["strength"], medicine["dosage_form"]]
    if require_pack_match:
        sql += " AND lower(m.pack_size) = lower(?)"
        params.append(medicine["pack_size"])
    if require_release_match:
        sql += " AND m.release_type IS NOT NULL AND lower(m.release_type) = lower(?)"
        params.append(medicine["release_type"])
    sql += " GROUP BY m.id"

    matches = []
    for row in execute_query(sql, tuple(params)):
        item = dict(row)
        parts = item.pop("ingredient_key", "").split("|")
        candidate_signature = tuple(sorted(
            tuple(part.split(":", 1)) for part in parts if ":" in part
        ))
        if candidate_signature == signature:
            matches.append(item)
    return matches


def _equivalence_evidence(reference_key, candidate_key):
    return execute_query(
        """SELECT evidence_type, reference_product, source_url, verified_at
           FROM medicine_equivalence_evidence
           WHERE reference_product_key = ? AND candidate_product_key = ?
             AND jurisdiction = ? AND status = 'confirmed'
             AND source_url IS NOT NULL AND trim(source_url) != ''
           LIMIT 1""",
        (reference_key, candidate_key, MARKET_JURISDICTION),
    )


def get_substitutes(medicine_id, limit=5):
    """Return verified alternatives plus cheaper same-composition catalog options.

    Verified substitutes still require product-specific equivalence evidence and
    a recent comparable price. Same-composition products are returned separately
    and explicitly labelled as not verified for interchangeability.
    """
    target_rows = execute_query("SELECT * FROM medicines WHERE id = ? LIMIT 1", (medicine_id,))
    if not target_rows:
        return {"success": False, "message": "Medicine not found", "data": []}

    medicine = dict(target_rows[0])
    # Imported rows sometimes lost their ingredient list; fill it from the live
    # India catalog once so composition-based matching can work for this record.
    if not medicine.get("composition_complete") and medicine.get("brand_name"):
        if enrich_medicine_composition(medicine):
            medicine = dict(execute_query("SELECT * FROM medicines WHERE id = ? LIMIT 1", (medicine_id,))[0])
    # Catalog-added rows have no price, which blocks every savings claim. Look up
    # one exact live listing before ranking cheaper alternatives.
    if float(medicine.get("price") or 0) <= 0 and medicine.get("brand_name"):
        if enrich_medicine_price(medicine):
            medicine = dict(execute_query("SELECT * FROM medicines WHERE id = ? LIMIT 1", (medicine_id,))[0])
    signature = _ingredient_signature(medicine_id)
    potential_matchable = bool(
        medicine.get("composition_complete")
        and signature
        and medicine.get("strength")
        and medicine.get("dosage_form")
        and medicine.get("product_key")
    )
    matchable = potential_matchable and bool(medicine.get("release_type"))

    confirmed = []
    potential = (
        _candidate_rows(
            medicine, signature, medicine_id,
            require_pack_match=bool(medicine.get("pack_size")),
        )
        if potential_matchable else []
    )
    # Keep catalog candidates available for transparent review, separate from
    # the evidence-backed alternatives list and without savings calculations.
    potential_by_key = {}
    for item in potential:
        key = item.get("product_key") or f"row:{item['id']}"
        potential_by_key.setdefault(key, item)
    potential_matches = sorted(
        potential_by_key.values(),
        key=lambda item: (item.get("medicine_name") or "").casefold(),
    )
    potential_count = len(potential_matches)

    # Same active ingredients, strength, form and release type, ranked by the
    # cheaper price per tablet/ml so different pack sizes can be compared.
    same_content = []
    reference_unit = _price_per_unit(medicine.get("price"), medicine.get("pack_size"))
    if potential_matchable:
        reference_release = str(medicine.get("release_type") or "").casefold()
        reference_pack = str(medicine.get("pack_size") or "").casefold()
        for item in _candidate_rows(medicine, signature, medicine_id, require_pack_match=False):
            price = float(item.get("price") or 0)
            if price <= 0:
                continue
            if str(item.get("release_type") or "").casefold() != reference_release:
                continue
            savings = None
            amount = 0.0
            basis = None
            candidate_unit = _price_per_unit(price, item.get("pack_size"))
            if reference_unit and candidate_unit and candidate_unit[1] == reference_unit[1]:
                if candidate_unit[0] < reference_unit[0]:
                    amount = round(reference_unit[0] - candidate_unit[0], 4)
                    savings = round(amount / reference_unit[0] * 100, 1)
                    basis = "per unit"
                    item["price_per_unit_value"] = round(candidate_unit[0], 4)
                    item["reference_price_per_unit"] = round(reference_unit[0], 4)
            elif str(item.get("pack_size") or "").casefold() == reference_pack:
                original_price = float(medicine.get("price") or 0)
                if original_price > price:
                    amount = round(original_price - price, 2)
                    savings = round(amount / original_price * 100, 1)
                    basis = "per pack"
            if savings is None:
                continue
            item["savings_amount"] = amount
            item["savings_percent"] = savings
            item["savings_basis"] = basis
            item["price_is_current"] = _fresh_price(item.get("price_checked_at"))
            item["savings_reliable"] = bool(
                item["price_is_current"] and _fresh_price(medicine.get("price_checked_at"))
            )
            item["match_classification"] = "Same active ingredients and strength - not verified as interchangeable"
            same_content.append(item)

        unique_same = {}
        for item in same_content:
            key = item.get("product_key") or f"row:{item['id']}"
            if key not in unique_same or float(item["price"]) < float(unique_same[key]["price"]):
                unique_same[key] = item
        same_content = sorted(unique_same.values(), key=lambda item: (
            item.get("price_per_unit_value", float(item.get("price") or 0)),
            float(item.get("price") or 0),
        ))

    # A record imported from the live catalog has no price, so a cheaper-than-
    # reference claim is impossible. Still expose the cheapest catalog products
    # with the same recorded ingredients, strength, form and release.
    reference_has_price = float(medicine.get("price") or 0) > 0
    price_ranked = []
    if potential_matchable and not reference_has_price:
        reference_release = str(medicine.get("release_type") or "").casefold()
        unique_options = {}
        for item in _candidate_rows(medicine, signature, medicine_id, require_pack_match=False):
            price = float(item.get("price") or 0)
            if price <= 0:
                continue
            if str(item.get("release_type") or "").casefold() != reference_release:
                continue
            unit = _price_per_unit(price, item.get("pack_size"))
            if unit:
                item["price_per_unit_value"] = round(unit[0], 4)
            item["match_classification"] = "Same active ingredients and strength - not verified as interchangeable"
            key = item.get("product_key") or f"row:{item['id']}"
            if key not in unique_options or price < float(unique_options[key]["price"]):
                unique_options[key] = item
        price_ranked = sorted(unique_options.values(), key=lambda item: (
            item.get("price_per_unit_value", float(item.get("price") or 0)),
            float(item.get("price") or 0),
        ))

    if matchable:
        if _fresh_price(medicine.get("price_checked_at")) and medicine.get("price_source"):
            for item in _candidate_rows(medicine, signature, medicine_id, require_release_match=True):
                if not item.get("product_key") or not item.get("price_source"):
                    continue
                if not _fresh_price(item.get("price_checked_at")):
                    continue
                evidence = _equivalence_evidence(medicine["product_key"], item["product_key"])
                if not evidence:
                    continue
                price = float(item.get("price") or 0)
                original_price = float(medicine.get("price") or 0)
                if price <= 0 or original_price <= price:
                    continue
                evidence_data = dict(evidence[0])
                amount = round(original_price - price, 2)
                confirmed.append({
                    **item,
                    "savings_amount": amount,
                    "savings_percent": round(amount / original_price * 100, 1),
                    "equivalence_evidence": evidence_data,
                })

    unverified_matches = []
    for item in potential_matches[:max(0, min(limit, 20))]:
        gaps = ["No product-level equivalence evidence found"]
        if not medicine.get("release_type"):
            gaps.append("Reference product release type is unknown")
        if not item.get("release_type"):
            gaps.append("Candidate release type is unknown")
        elif medicine.get("release_type") and item["release_type"].casefold() != medicine["release_type"].casefold():
            gaps.append("Release type differs from reference")
        item["verification_gaps"] = gaps
        item["price_is_current"] = _fresh_price(item.get("price_checked_at"))
        if not _fresh_price(medicine.get("price_checked_at")) or not item["price_is_current"]:
            gaps.append("Recent comparable price is unavailable")
        unverified_matches.append(item)

    # Multiple imported rows may describe the same product key.
    unique = {}
    for item in confirmed:
        key = item["product_key"]
        if key not in unique or float(item["price"]) < float(unique[key]["price"]):
            unique[key] = item
    confirmed = sorted(unique.values(), key=lambda item: float(item["price"]))[:max(0, limit)]
    best = confirmed[0] if confirmed else None

    confirmed_ids = {str(item.get("id")) for item in confirmed}
    same_content = [item for item in same_content if str(item.get("id")) not in confirmed_ids]

    if confirmed:
        status = "verified_alternatives"
        message = "Product-specific equivalence evidence and recent comparable prices are available."
    elif same_content:
        status = "cheaper_same_composition"
        message = (
            f"{len(same_content)} cheaper products with the same recorded ingredients and strength were found. "
            "They are catalog matches, not verified therapeutic equivalents."
        )
    elif not potential_matchable:
        status = "insufficient_product_data"
        message = "The medicine lacks complete ingredient, strength, form, release, pack, or product identity data."
    elif price_ranked:
        status = "reference_price_unknown"
        message = (
            "This product has no recorded price, so a cheaper-than-reference comparison cannot be made. "
            f"{len(price_ranked)} catalog products with the same recorded ingredients and strength are listed by price below."
        )
    elif potential_count:
        status = "no_verified_alternatives"
        message = (
            f"{potential_count} same-ingredient products were found, but none had a verifiable lower "
            "comparable price. Catalog matches are listed below for review; they are not confirmed as interchangeable."
        )
    elif not matchable:
        status = "no_verified_alternatives"
        message = "Release characteristics are not verified, so no therapeutic equivalent can be confirmed."
    else:
        status = "no_verified_alternatives"
        message = "No product with the same recorded ingredients, strength, form, release and pack was found."

    return {
        "success": True,
        "original_medicine": medicine,
        "count": len(confirmed),
        "potential_match_count": potential_count,
        "unverified_matches": unverified_matches,
        "same_composition_alternatives": same_content[:max(0, min(limit, 20))],
        "same_composition_count": len(same_content),
        "same_composition_options": price_ranked[:max(0, min(limit, 20))],
        "same_composition_option_count": len(price_ranked),
        "reference_price_available": reference_has_price,
        "max_savings_percent": max(
            [item["savings_percent"] for item in confirmed]
            + [item["savings_percent"] for item in same_content[:max(0, min(limit, 20))]]
            or [0]
        ),
        "max_savings_amount": best["savings_amount"] if best else 0.0,
        "substitutes": confirmed,
        "comparison_status": status,
        "message": message,
    }
