"""Return evidence-backed equivalents plus clearly-labelled same-composition options."""

import re

from backend.database import execute_query
from backend.medicine_data import (
    form_variant,
    is_modified_release,
    normalize_dosage_form,
    normalize_medication_text,
    restricted_reason,
)
from backend.services.drug_catalog_service import enrich_medicine_composition
from backend.services.price_enrichment_service import enrich_medicine_price


PRICE_MAX_AGE_DAYS = 30
# A published generic price list is an edition, not a live listing, so it stays
# usable for a year instead of the 30-day window applied to scraped listings.
REFERENCE_PRICE_MAX_AGE_DAYS = 400
MARKET_JURISDICTION = "IN"
PACK_QUANTITY = re.compile(r"(\d+)\s*(tablets?|capsules?|softgels?|ml|sachets?|units?)\b", re.I)


def _ingredient_signature(medicine_id):
    rows = execute_query(
        "SELECT ingredient_name_norm, ingredient_strength_norm "
        "FROM medicine_ingredients WHERE medicine_id = ?",
        (medicine_id,),
    )
    return tuple(sorted((row["ingredient_name_norm"], row["ingredient_strength_norm"]) for row in rows))


_AMOUNT_PART = re.compile(r"^([\d.]+)\s*([a-z%µμ]*)$")


def _precise_amount(text):
    """'12.50 mg' -> '12.5mg', '1.0mg/5ml' -> '1mg/5ml'; keeps decimals, unlike the stored key."""
    pieces = []
    for piece in str(text or "").casefold().replace("µ", "u").replace("μ", "u").split("/"):
        piece = piece.strip()
        match = _AMOUNT_PART.match(piece)
        if match:
            try:
                piece = "%g%s" % (float(match.group(1)), match.group(2))
            except ValueError:
                pass
        pieces.append(piece.replace(" ", ""))
    return "/".join(pieces)


def _parse_precise(key):
    parts = []
    for part in str(key or "").split("|"):
        if "\x1f" in part:
            name, strength = part.split("\x1f", 1)
            parts.append((name, _precise_amount(strength)))
    return tuple(sorted(parts))


def _precise_signature(medicine_id):
    rows = execute_query(
        "SELECT ingredient_name_norm, ingredient_strength FROM medicine_ingredients WHERE medicine_id = ?",
        (medicine_id,),
    )
    return tuple(sorted((row["ingredient_name_norm"], _precise_amount(row["ingredient_strength"]))
                        for row in rows))


def _strength_consistent(strength, precise):
    """True when a strength column only restates amounts from the product's own composition."""
    whole = _precise_amount(strength)
    amounts = [amount for _, amount in precise]
    if not whole:
        return False
    if whole in amounts:
        return True
    remaining = list(amounts)
    for part in whole.split("/"):
        if part not in remaining:
            return False
        remaining.remove(part)
    return True


def _strength_agrees(reference_strength, candidate_strength, precise):
    """Same strength column, or both columns consistent with the (identical) composition."""
    if str(reference_strength or "").strip().casefold() == str(candidate_strength or "").strip().casefold():
        return bool(str(reference_strength or "").strip())
    return (_strength_consistent(reference_strength, precise)
            and _strength_consistent(candidate_strength, precise))


def _fresh_price(value):
    return _fresh_within(value, PRICE_MAX_AGE_DAYS)


def _fresh_within(value, days):
    return bool(value) and execute_query(
        "SELECT 1 WHERE datetime(?) >= datetime('now', ?) LIMIT 1",
        (value, f"-{days} days"),
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


def _catalog_duplicate_key(item):
    return tuple(
        normalize_medication_text(item.get(field))
        for field in ("medicine_name", "brand_name", "strength", "dosage_form", "pack_size", "release_type")
    )


def _dedupe_catalog_rows(items):
    """Collapse duplicate listings, keeping the cheapest row for each product."""
    by_product = {}
    for item in items:
        key = item.get("product_key") or _catalog_duplicate_key(item)
        current = by_product.get(key)
        if current is None or float(item.get("price") or 0) < float(current.get("price") or 0):
            by_product[key] = item
    by_listing = {}
    for item in by_product.values():
        key = _catalog_duplicate_key(item)
        current = by_listing.get(key)
        if current is None or float(item.get("price") or 0) < float(current.get("price") or 0):
            by_listing[key] = item
    return list(by_listing.values())


def _candidate_rows(medicine, signature, target_id, require_release_match=False,
                    require_pack_match=True):
    if not signature:
        return []
    precise = _precise_signature(target_id)
    # Candidates are found through the reference's rarest ingredient rather than the
    # strength column, which combination products fill inconsistently ("200mg" on one
    # row, "200mg/500mg" on another). Strength agreement is checked per row below.
    pivot_name, pivot_strength = min(signature, key=lambda part: execute_query(
        "SELECT COUNT(*) AS n FROM medicine_ingredients WHERE ingredient_name_norm = ?",
        (part[0],),
    )[0]["n"])
    sql = """SELECT m.*,
                    GROUP_CONCAT(i.ingredient_name_norm || ':' || i.ingredient_strength_norm, '|') AS ingredient_key,
                    GROUP_CONCAT(i.ingredient_name_norm || char(31) || COALESCE(i.ingredient_strength, ''), '|') AS precise_key
             FROM medicines m
             JOIN medicine_ingredients i ON i.medicine_id = m.id
             WHERE m.id IN (SELECT medicine_id FROM medicine_ingredients
                            WHERE ingredient_name_norm = ? AND ingredient_strength_norm = ?)
               AND m.id != ?
               AND m.composition_complete = 1
               AND lower(m.dosage_form) = lower(?)"""
    params = [pivot_name, pivot_strength, target_id, medicine["dosage_form"]]
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
        if candidate_signature != signature:
            continue
        # The stored strength key drops decimal points (12.5mg and 125mg both read
        # "125mg"), so the recorded strengths are compared again with their decimals.
        if _parse_precise(item.pop("precise_key", "")) != precise:
            continue
        if not _strength_agrees(medicine.get("strength"), item.get("strength"), precise):
            continue
        # A dispersible, mouth-dissolving or vaginal tablet is not a plain tablet.
        if form_variant(item.get("medicine_name")) != form_variant(medicine.get("medicine_name")):
            continue
        # A modified-release product whose release column is empty would otherwise
        # pass as a plain immediate-release equivalent, so the name is checked too.
        if is_modified_release(item.get("release_type"), item.get("medicine_name"),
                               item.get("brand_name")):
            continue
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


def _reference_table_ready():
    return bool(execute_query(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'reference_prices' LIMIT 1"
    ))


def _composition_key(medicine, signature):
    """Same key the Jan Aushadhi importer writes: <form family>|<ingredient:strength>+..."""
    if not signature:
        return None
    parts = sorted("%s:%s" % (name, strength) for name, strength in signature)
    return "%s|%s" % (normalize_dosage_form(medicine.get("dosage_form")), "+".join(parts))


def _reference_price_options(medicine, signature, limit=5):
    """Published generic list prices for the same recorded composition and form.

    These are reference prices for a generic product, not a listing of the
    searched brand, so they never establish interchangeability.
    """
    key = _composition_key(medicine, signature)
    if not key or not _reference_table_ready():
        return [], 0

    rows = [dict(row) for row in execute_query(
        "SELECT * FROM reference_prices WHERE composition_key = ? "
        "ORDER BY COALESCE(price_per_unit, price_value) LIMIT 20",
        (key,),
    )]
    # The key carries only the form family, so "Ondansetron Orally Disintegrating
    # Tablets" and plain tablets share it; keep the list row of the same variant.
    variant = form_variant(medicine.get("medicine_name"))
    rows = [row for row in rows if form_variant(row.get("generic_name")) == variant]
    if not rows:
        return [], 0
    # When a current edition lists this composition, older editions are superseded.
    current_rows = [row for row in rows
                    if _fresh_within(row.get("observed_at"), REFERENCE_PRICE_MAX_AGE_DAYS)]
    if current_rows:
        rows = current_rows

    reference_unit = _price_per_unit(medicine.get("price"), medicine.get("pack_size"))
    reference_current = _fresh_price(medicine.get("price_checked_at"))

    options = []
    for row in rows[:max(0, limit)]:
        observed = row.get("observed_at")
        dated = _fresh_within(observed, REFERENCE_PRICE_MAX_AGE_DAYS)
        unit_price = row.get("price_per_unit")
        option = {
            "source": row.get("source"),
            "edition": row.get("source_edition"),
            "product_name": row.get("generic_name"),
            "composition": row.get("composition"),
            "pack": row.get("pack_text"),
            "price": row.get("price_value"),
            "price_per_unit": unit_price,
            "unit": row.get("pack_unit"),
            "price_kind": row.get("price_kind"),
            "observed_at": observed,
            "price_is_current": dated,
            "match_classification": (
                "Same recorded ingredients, strength and form - a published generic "
                "list price, not a listing of this brand and not verified as interchangeable"
            ),
        }
        option_unit = str(row.get("pack_unit") or "").casefold()
        if reference_unit and unit_price and reference_unit[1] == option_unit:
            option["reference_price_per_unit"] = round(reference_unit[0], 4)
            option["difference_per_unit"] = round(reference_unit[0] - unit_price, 4)
            if reference_current and dated and 0 < unit_price < reference_unit[0]:
                difference = reference_unit[0] - unit_price
                option["savings_amount"] = round(difference, 4)
                option["savings_percent"] = round(difference / reference_unit[0] * 100, 1)
                option["savings_basis"] = "per unit"
            elif 0 < unit_price < reference_unit[0]:
                difference = reference_unit[0] - unit_price
                option["estimated_savings_amount"] = round(difference, 4)
                option["estimated_savings_percent"] = round(difference / reference_unit[0] * 100, 1)
                option["estimated_savings_basis"] = "per unit, prices not both confirmed as current"
        options.append(option)
    return options, len(rows)


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
    # Modified-release products, supervised forms and narrow-therapeutic-index
    # ingredients are not offered as cheaper interchangeable options at all.
    blocked_reason = restricted_reason(medicine, [name for name, _ in signature])
    blocked = blocked_reason is not None

    reference_price_options, reference_price_option_count = (
        ([], 0) if blocked
        else _reference_price_options(medicine, signature, limit=5)
    )

    confirmed = []
    potential = (
        _candidate_rows(
            medicine, signature, medicine_id,
            require_pack_match=bool(medicine.get("pack_size")),
        )
        if potential_matchable and not blocked else []
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

    # Same active ingredients, strength, form and release type. A saving is only
    # claimed when the reference and the candidate both have a current listing
    # price; otherwise the products are shown as catalog options with unverified
    # prices, ranked by price per unit.
    reference_has_price = float(medicine.get("price") or 0) > 0
    reference_price_current = reference_has_price and _fresh_price(medicine.get("price_checked_at"))
    reference_unit = _price_per_unit(medicine.get("price"), medicine.get("pack_size"))
    reference_pack = str(medicine.get("pack_size") or "").strip().casefold()

    cheaper_same_content = []
    catalog_options = []
    if potential_matchable and not blocked:
        reference_release = str(medicine.get("release_type") or "").casefold()
        for item in _candidate_rows(medicine, signature, medicine_id, require_pack_match=False):
            price = float(item.get("price") or 0)
            if price <= 0:
                continue
            if str(item.get("release_type") or "").casefold() != reference_release:
                continue
            candidate_unit = _price_per_unit(price, item.get("pack_size"))
            if not candidate_unit:
                # A price without a pack size cannot be normalized per unit or pack.
                continue
            item["price_per_unit_value"] = round(candidate_unit[0], 4)
            item["price_is_current"] = _fresh_price(item.get("price_checked_at"))
            item["match_classification"] = "Same active ingredients and strength - not verified as interchangeable"

            savings = None
            amount = 0.0
            basis = None
            both_current = reference_price_current and item["price_is_current"]
            if both_current and reference_unit and candidate_unit[1] == reference_unit[1]:
                if candidate_unit[0] < reference_unit[0]:
                    amount = round(reference_unit[0] - candidate_unit[0], 4)
                    savings = round(amount / reference_unit[0] * 100, 1)
                    basis = "per unit"
                    item["reference_price_per_unit"] = round(reference_unit[0], 4)
            elif both_current and reference_pack and str(item.get("pack_size") or "").strip().casefold() == reference_pack:
                original_price = float(medicine.get("price") or 0)
                if original_price > price:
                    amount = round(original_price - price, 2)
                    savings = round(amount / original_price * 100, 1)
                    basis = "per pack"

            if savings is None:
                # Old or undated prices cannot support a saving claim, but a per-unit
                # difference between recorded catalog prices is shown as an estimate,
                # under separate keys so it is never mistaken for a verified saving.
                if (
                    reference_has_price and reference_unit
                    and candidate_unit[1] == reference_unit[1]
                    and candidate_unit[0] < reference_unit[0]
                ):
                    estimate = reference_unit[0] - candidate_unit[0]
                    item["estimated_savings_amount"] = round(estimate, 4)
                    item["estimated_savings_percent"] = round(estimate / reference_unit[0] * 100, 1)
                    item["estimated_savings_basis"] = "per unit, catalog prices not confirmed as current"
                catalog_options.append(item)
                continue
            item["savings_amount"] = amount
            item["savings_percent"] = savings
            item["savings_basis"] = basis
            item["savings_reliable"] = True
            cheaper_same_content.append(item)

        cheaper_same_content = sorted(
            _dedupe_catalog_rows(cheaper_same_content),
            key=lambda item: (
                item.get("price_per_unit_value") or float(item.get("price") or 0),
                float(item.get("price") or 0),
            ),
        )
        catalog_options = sorted(
            _dedupe_catalog_rows(catalog_options),
            key=lambda item: (
                item.get("price_per_unit_value") or float(item.get("price") or 0),
                float(item.get("price") or 0),
            ),
        )

    if matchable and not blocked:
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
    cheaper_same_content = [item for item in cheaper_same_content if str(item.get("id")) not in confirmed_ids]

    if blocked:
        status = "restricted_class"
        message = (
            f"Cheaper options are not shown for this medicine because {blocked_reason}. "
            "Switching between products of this type needs prescriber or pharmacist review."
        )
    elif confirmed:
        status = "verified_alternatives"
        message = "Product-specific equivalence evidence and recent comparable prices are available."
    elif cheaper_same_content:
        status = "cheaper_same_composition"
        message = (
            f"{len(cheaper_same_content)} products with the same recorded ingredients and strength have a current "
            "listing price below this product. They are catalog matches, not verified therapeutic equivalents."
        )
    elif not potential_matchable:
        status = "insufficient_product_data"
        message = "The medicine lacks complete ingredient, strength, form, release, pack, or product identity data."
    elif catalog_options and not reference_has_price:
        status = "reference_price_unknown"
        message = (
            "This product has no recorded price, so a cheaper-than-reference comparison cannot be made. "
            f"{len(catalog_options)} catalog products with the same recorded ingredients and strength are listed by price below."
        )
    elif catalog_options and not reference_price_current:
        status = "catalog_price_unverified"
        message = (
            "The recorded price for this product is not a current listing, so no saving can be claimed. "
            f"{len(catalog_options)} same-composition catalog products are listed by their unverified catalog prices below."
        )
    elif catalog_options and not reference_unit:
        status = "reference_pack_unknown"
        message = (
            "This product's pack size is not recorded, so a per-unit saving cannot be confirmed. "
            f"{len(catalog_options)} same-composition catalog products are listed by their catalog prices below."
        )
    elif catalog_options:
        status = "catalog_price_unverified"
        message = (
            "No same-composition product with a current listing price is cheaper than this product. "
            f"{len(catalog_options)} catalog products are listed by their unverified catalog prices below."
        )
    elif potential_count:
        priced_matches = [item for item in potential_matches if float(item.get("price") or 0) > 0]
        status = "no_verified_alternatives"
        if priced_matches and all(not item.get("pack_size") for item in priced_matches):
            message = (
                f"{len(priced_matches)} same-ingredient products were found, but none records a pack size, "
                "so a per-unit price comparison cannot be made. They are listed below for review; "
                "they are not confirmed as interchangeable."
            )
        else:
            message = (
                f"{potential_count} same-ingredient products were found, but none had a verifiable lower "
                "comparable price. Catalog matches are listed below for review; they are not confirmed as interchangeable."
            )
    else:
        status = "no_verified_alternatives"
        message = "No product with the same recorded ingredients, strength, form and pack was found."

    return {
        "success": True,
        "original_medicine": medicine,
        "count": len(confirmed),
        "potential_match_count": potential_count,
        "unverified_matches": unverified_matches,
        "same_composition_alternatives": cheaper_same_content[:max(0, min(limit, 20))],
        "same_composition_count": len(cheaper_same_content),
        "same_composition_options": catalog_options[:max(0, min(limit, 20))],
        "same_composition_option_count": len(catalog_options),
        "reference_price_options": reference_price_options,
        "reference_price_option_count": reference_price_option_count,
        "reference_price_available": reference_has_price,
        "reference_price_is_current": reference_price_current,
        "max_savings_percent": max(
            [item["savings_percent"] for item in confirmed]
            + [item["savings_percent"] for item in cheaper_same_content[:max(0, min(limit, 20))]]
            or [0]
        ),
        "max_savings_amount": best["savings_amount"] if best else 0.0,
        "max_estimated_savings_percent": max(
            [item.get("estimated_savings_percent") or 0
             for item in catalog_options[:max(0, min(limit, 20))]] or [0]
        ),
        "substitutes": confirmed,
        "comparison_status": status,
        "restricted_class_reason": blocked_reason,
        "message": message,
    }
