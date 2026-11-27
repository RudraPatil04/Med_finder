"""
============================================================
MED FINDER
MEDICINE SERVICE
Version : 2.0

Author : Naman
============================================================
"""

import re
import time

from backend.database import execute_query, get_connection
from backend.services.api_cache import (
    ensure_api_search_cache, has_recent_api_search, record_api_search,
)
from backend.services.drug_catalog_service import (
    ensure_catalog_schema, fetch_medicines, save_catalog_medicine,
    search_catalog, search_pharmeasy_catalog,
)
from backend.services.external_api_service import search_rxnorm, save_api_medicine, note_issue
from backend.medicine_data import (
    normalize_medication_text, pack_quantity, parse_composition, restricted_reason,
)
from backend.services.search_ranking import (
    Query, best_tier, correct_query, generic_ingredient, rank_records,
)


SEARCH_CACHE_VERSION = "catalog-v2"


def _normalized_search_key(keyword):
    # Bump the prefix whenever the provider set changes; a cached miss from an
    # older provider set must not suppress the new sources.
    return f"{SEARCH_CACHE_VERSION}:{' '.join((keyword or '').casefold().split())}"


def _search_ingredient_key(keyword):
    """Return a likely single active-ingredient query, or None for brand/combination searches."""
    text = (keyword or "").strip().casefold()
    if re.search(r"\s/\s|\+|\band\b", text):
        return None
    text = re.sub(r"\d+(?:\.\d+)?\s*(?:mcg|μg|µg|mg|g|kg|ml|l|iu|units?|%)?", " ", text)
    text = re.sub(
        r"\b(?:oral|tablet|tablets|tab|capsule|capsules|cap|syrup|suspension|injection|cream|ointment|gel|drops|spray|inhaler|solution|immediate|extended|sustained|controlled|modified|delayed|release|xr|er|sr|cr|mr|xl)\b",
        " ", text,
    )
    key = normalize_medication_text(text)
    return key if len(key) >= 4 else None


def _record_ingredient_keys(record):
    """Read active ingredients from structured composition or RxNorm product text."""
    get = record.get if isinstance(record, dict) else lambda key, default=None: record[key] if key in record.keys() else default
    parsed = parse_composition(get("composition"))
    name = str(get("medicine_name") or "")
    # RxNorm spells multi-ingredient products with a spaced slash. Its text may
    # be malformed as a single composition component, so parse those segments
    # from the product name instead of trusting that one-component parse.
    has_rxnorm_combo_separator = bool(re.search(r"\s/\s", name))
    if parsed and not (len(parsed) == 1 and has_rxnorm_combo_separator):
        return [item["ingredient_name_norm"] for item in parsed]
    # Only RxNorm spells names as "ingredient 10 MG Oral Tablet" (spaced,
    # upper-case unit). An Indian brand row without a composition ("Ondem 4mg
    # Tablet") must not have its brand guessed as an ingredient: that guess
    # made brand searches keep only the guessed rows.
    if not (has_rxnorm_combo_separator
            or re.search(r"\d\s+(?:MG|MCG|ML|UNT|MEQ)\b", name)):
        return []

    # RxNorm product names commonly use "ingredient strength / ingredient strength".
    parts = re.split(r"\s+/\s+", name)
    ingredient_keys = []
    for part in parts:
        match = re.match(
            r"\s*([A-Za-z][A-Za-z\s-]*?)(?:\s*\([^)]*\))?\s+\d+(?:\.\d+)?\s*(?:mcg|μg|µg|mg|g|ml|iu|units?|%)\b",
            part, re.I,
        )
        if match:
            ingredient = re.sub(r"\([^)]*\)", " ", match.group(1))
            key = normalize_medication_text(ingredient)
            if key:
                ingredient_keys.append(key)
    return ingredient_keys


def _filter_single_ingredient_search(keyword, records):
    """When a generic ingredient is searched, omit products containing extra actives."""
    key = _search_ingredient_key(keyword)
    if key:
        key = _generic_key(keyword) or key
    if not key:
        return records
    parsed = [(record, _record_ingredient_keys(record)) for record in records]

    def is_requested(ingredient):
        return ingredient == key or ingredient.startswith(key) or key.startswith(ingredient)

    if not any(any(is_requested(ingredient) for ingredient in ingredients) for _, ingredients in parsed):
        return records
    return [
        record for record, ingredients in parsed
        if len(ingredients) == 1 and is_requested(ingredients[0])
    ]


def _search_strength_pattern(keyword):
    """Return a GLOB pattern for a printed strength ("500", "10mg"), or None.

    A number glued to letters ("Vitamin B12") is part of the name, not a strength.
    """
    match = re.search(
        r"(?<![a-z0-9])(\d+(?:\.\d+)?)\s*(mcg|μg|µg|mg|g|ml|iu|units?|%)?(?![a-z0-9])",
        keyword or "", re.I,
    )
    if not match:
        return None
    number = match.group(1)
    unit = (match.group(2) or "").casefold().replace("μ", "u").replace("µ", "u").rstrip("s")
    if unit:
        return f"{number}{unit}*"
    return f"{number}[^0-9]*"


def _record_value(record, field):
    if isinstance(record, dict):
        return record.get(field)
    try:
        return record[field]
    except (IndexError, KeyError):
        return None


def _search_duplicate_key(record):
    # Pack size is intentionally excluded: search cards do not show it, so two
    # pack sizes of one product would look like a duplicated suggestion.
    return tuple(
        normalize_medication_text(_record_value(record, field))
        for field in ("medicine_name", "brand_name", "strength", "dosage_form")
    )


def _dedupe_search_records(records):
    """Collapse duplicate catalog rows, keeping the cheapest priced copy."""
    unique = {}
    for record in records:
        key = _search_duplicate_key(record)
        current = unique.get(key)
        if current is None:
            unique[key] = record
            continue
        current_price = float(_record_value(current, "price") or 0)
        new_price = float(_record_value(record, "price") or 0)
        if new_price and (not current_price or new_price < current_price):
            unique[key] = record
    return list(unique.values())


# ============================================================
# Search Suggestions
# ============================================================

PRICE_MAX_AGE_DAYS = 30
SUGGESTION_CANDIDATES = 200
SUGGESTION_CACHE_LIMIT = 4000
SUGGESTION_SCAN_LIMIT = 60
SUGGESTION_SECONDS = 0.5
# Every seed is a brand or name prefix that the catalog actually records and
# that resolves to a product with a priced same-composition sibling.
SUGGESTION_SEEDS = (
    "dolo", "crocin", "calpol", "combiflam",
    "augmentin", "azithral", "montek", "zerodol",
)

# A suggestion must be able to show a priced comparison, so a candidate needs
# another product with the same recorded ingredients, strength, form and
# release that also carries a price inside a countable pack.
_PRICED_OPTION_SQL = """
SELECT 1 FROM medicines c INDEXED BY idx_group_price
WHERE c.id != :id AND c.composition_complete = 1
  AND c.strength = :strength COLLATE NOCASE
  AND c.dosage_form = :form COLLATE NOCASE
  AND lower(COALESCE(c.release_type, '')) = :release
  AND c.price > 0 AND c.pack_size IS NOT NULL AND trim(c.pack_size) != ''
  AND (SELECT COUNT(*) FROM medicine_ingredients x WHERE x.medicine_id = c.id) = :ingredient_count
  AND NOT EXISTS (
      SELECT 1 FROM medicine_ingredients x
      WHERE x.medicine_id = c.id AND NOT EXISTS (
          SELECT 1 FROM medicine_ingredients r
          WHERE r.medicine_id = :id
            AND r.ingredient_name_norm = x.ingredient_name_norm
            AND r.ingredient_strength_norm = x.ingredient_strength_norm))
LIMIT 1
"""

_priced_option_cache = {}


def _has_priced_same_composition(connection, medicine, ingredient_names, ingredient_count):
    cached = _priced_option_cache.get(medicine["id"])
    if cached is not None:
        return cached
    result = ingredient_count > 0 and connection.execute(_PRICED_OPTION_SQL, {
        "id": medicine["id"],
        "strength": medicine["strength"],
        "form": medicine["dosage_form"],
        "release": str(medicine["release_type"] or "").casefold(),
        "ingredient_count": ingredient_count,
    }).fetchone() is not None
    if len(_priced_option_cache) >= SUGGESTION_CACHE_LIMIT:
        _priced_option_cache.clear()
    _priced_option_cache[medicine["id"]] = result
    return result


def _price_is_current(connection, value):
    if not value:
        return False
    return connection.execute(
        "SELECT 1 WHERE datetime(?) >= datetime('now', ?) LIMIT 1",
        (value, f"-{PRICE_MAX_AGE_DAYS} days"),
    ).fetchone() is not None


def suggest_functional_medicines(keyword, limit=8):
    """Medicines whose page will actually show a priced same-composition comparison.

    Type-ahead suggestions must not promise comparisons the database cannot
    produce, so a medicine is only returned when another product with the same
    recorded ingredients, strength, form and release carries a price and a
    countable pack size. Restricted classes are never suggested.
    """
    ensure_catalog_schema()
    connection = get_connection()
    deadline = time.monotonic() + SUGGESTION_SECONDS
    try:
        text = (keyword or "").strip()
        if not text:
            return _starter_suggestions(connection, limit, deadline)
        key = normalize_medication_text(text)
        if not key:
            return []
        return _collect_suggestions(connection, key, limit, deadline)
    finally:
        connection.close()


def _starter_suggestions(connection, limit, deadline):
    """A short recommendation list shown while the box is still empty."""
    suggestions = []
    seen = set()
    for seed in SUGGESTION_SEEDS:
        for item in _collect_suggestions(connection, seed, 1, deadline):
            if item["id"] in seen:
                continue
            seen.add(item["id"])
            suggestions.append(item)
        if len(suggestions) >= limit:
            break
    return suggestions[:limit]


def _collect_suggestions(connection, key, limit, deadline):
    if not key:
        return []
    candidates = [dict(row) for row in connection.execute(
        "SELECT id, medicine_name, brand_name, strength, dosage_form, release_type, "
        "pack_size, price, price_checked_at "
        "FROM medicines INDEXED BY idx_search_norm "
        "WHERE search_norm GLOB ? AND composition_complete = 1 "
        "LIMIT ?",
        (key + "*", SUGGESTION_CANDIDATES),
    )]
    if not candidates:
        return []
    # The typed brand itself should surface before longer names that merely
    # start with the same letters ("Dolo" before "Dolobak").
    candidates.sort(key=lambda item: (
        0 if normalize_medication_text(item["brand_name"]) == key else (
            1 if normalize_medication_text(item["brand_name"]).startswith(key) else 2),
        len(item["medicine_name"] or ""),
        (item["medicine_name"] or "").casefold(),
    ))

    ingredients = {}
    placeholders = ",".join("?" * len(candidates))
    for row in connection.execute(
        "SELECT medicine_id, ingredient_name_norm FROM medicine_ingredients "
        f"WHERE medicine_id IN ({placeholders})",
        [candidate["id"] for candidate in candidates],
    ):
        ingredients.setdefault(row["medicine_id"], []).append(row["ingredient_name_norm"])

    suggestions = []
    seen = set()
    scanned = 0
    for medicine in candidates:
        if scanned >= SUGGESTION_SCAN_LIMIT or time.monotonic() > deadline:
            break
        scanned += 1
        names = ingredients.get(medicine["id"], [])
        blocked = restricted_reason(medicine, names) is not None
        if blocked or not _has_priced_same_composition(connection, medicine, names, len(names)):
            continue
        duplicate = _search_duplicate_key(medicine)
        if duplicate in seen:
            continue
        seen.add(duplicate)
        price = float(medicine["price"] or 0)
        current = price > 0 and _price_is_current(connection, medicine["price_checked_at"])
        quantity = pack_quantity(medicine["pack_size"])
        suggestions.append({
            "id": medicine["id"],
            "name": medicine["medicine_name"],
            "brand": medicine["brand_name"],
            "strength": medicine["strength"],
            "form": medicine["dosage_form"],
            "pack": medicine["pack_size"],
            "price": price if price > 0 else None,
            "price_per_unit": round(price / quantity[0], 4) if price and quantity else None,
            "unit": quantity[1] if quantity else None,
            "price_is_current": current,
            "price_label": (
                "Live listing" if current
                else ("Catalog price" if price > 0 else "No recorded price")
            ),
        })
        if len(suggestions) >= limit:
            break
    return suggestions


# ============================================================
# Search Medicines
# ============================================================

SEARCH_COLUMNS = """
        id,
        medicine_name,
        brand_name,
        strength,
        dosage_form,
        medicine_category,
        composition,
        price,
        price_source,
        price_checked_at,
        pack_size,
        release_type,
        composition_complete,
        rxnorm_rxcui,
        prescription_required,
        image_url,
        source,
        api_source
"""


def _generic_key(keyword):
    """The stored ingredient a generic query names ("Paracetamol 500"), or None."""
    return generic_ingredient(_search_ingredient_key(keyword))


def _search_local(keyword, limit):
    """Search local rows. Generic ingredient queries keep the ingredient path;
    everything else is ranked by how well the product name matches."""
    if _generic_key(keyword):
        return _search_local_generic(keyword, limit)
    return _search_local_names(keyword, limit)


def _search_local_names(keyword, limit, candidates=400):
    query = Query(keyword)
    conditions = ["medicine_name LIKE ?", "brand_name LIKE ?"]
    params = [f"%{keyword}%", f"%{keyword}%"]
    norm_keyword = normalize_medication_text(keyword)
    if norm_keyword:
        conditions.append("search_norm LIKE ?")
        params.append(f"%{norm_keyword}%")
    if query.spaced.casefold() != keyword.casefold():
        conditions.append("medicine_name LIKE ?")      # "pan40" -> "pan 40"
        params.append(f"%{query.spaced}%")
    text = query.text
    if text and text != keyword.casefold():
        # "Amlong 5" names "AMlong Tablet" (strength 5mg): match the name words
        # at a word start and let ranking check the strength.
        conditions.append(
            "(medicine_name LIKE ? OR medicine_name LIKE ? OR medicine_name LIKE ? OR brand_name LIKE ?)"
        )
        params += [f"{text}%", f"% {text}%", f"%-{text}%", f"{text}%"]
    rough_text = text or keyword
    sql = f"""
    SELECT {SEARCH_COLUMNS}
    FROM medicines
    WHERE {" OR ".join(conditions)}
    ORDER BY
        CASE WHEN ? <> '' AND medicine_name LIKE ? THEN 0 ELSE 1 END,
        CASE
            WHEN lower(trim(medicine_name)) = lower(?) THEN 0
            WHEN medicine_name LIKE ? OR medicine_name LIKE ? THEN 1
            WHEN medicine_name LIKE ? THEN 2
            WHEN brand_name LIKE ? THEN 3
            ELSE 4
        END,
        medicine_name
    LIMIT ?
    """
    # Rows naming both the words and the strength ("Himalaya Liv.52") must
    # survive the candidate limit, whatever their alphabetical place.
    params += [query.number or "", f"%{rough_text}%{query.number or ''}%"]
    params += [rough_text, f"{rough_text} %", f"{rough_text}-%", f"{rough_text}%",
               f"{rough_text}%", candidates]
    rows = execute_query(sql, tuple(params))
    return rank_records(query, rows)[:limit]


def _search_local_generic(keyword, limit):
    keyword_pattern = f"%{keyword}%"
    norm_keyword = normalize_medication_text(keyword)
    norm_pattern = f"%{norm_keyword}%" if norm_keyword else keyword_pattern

    # A generic ingredient is rarely part of the product name ("Voveran" is not
    # "Diclofenac"), so also match the structured ingredient list. Only
    # single-ingredient products are matched here; combination products are
    # dropped later for generic searches anyway and would crowd the page.
    ingredient_key = _generic_key(keyword)
    strength_pattern = _search_strength_pattern(keyword)
    strength_condition = " AND i.ingredient_strength_norm GLOB ?" if strength_pattern else ""
    ingredient_condition = f"""
        OR medicines.id IN (
            SELECT i.medicine_id
            FROM medicine_ingredients i
            WHERE i.ingredient_name_norm LIKE ?{strength_condition}
              AND (SELECT COUNT(DISTINCT j.ingredient_name_norm)
                   FROM medicine_ingredients j
                   WHERE j.medicine_id = i.medicine_id) = 1
        )"""
    ingredient_params = ((f"{ingredient_key}%", strength_pattern) if strength_pattern
                         else (f"{ingredient_key}%",))
    form_order = """,
        CASE lower(coalesce(dosage_form, ''))
            WHEN 'tablet' THEN 0
            WHEN 'capsule' THEN 1
            WHEN 'syrup' THEN 2
            WHEN 'suspension' THEN 3
            WHEN 'gel' THEN 4
            WHEN 'cream' THEN 5
            WHEN 'ointment' THEN 6
            WHEN 'patch' THEN 7
            WHEN 'injection' THEN 8
            ELSE 9
        END"""

    # For a generic ingredient query the name-prefix matches are incidental
    # ("Diclofenac Sodium Injection" for "Diclofenac"); let form ordering rank
    # them so tablets lead. Exact name/brand matches still come first.
    rank_sql = """
        CASE
            WHEN lower(trim(medicine_name)) = lower(trim(?)) THEN 0
            WHEN lower(trim(brand_name)) = lower(trim(?)) THEN 1
            ELSE 2
        END"""
    rank_params = (keyword, keyword)

    query = f"""
    SELECT {SEARCH_COLUMNS}
    FROM medicines
    WHERE
        medicine_name LIKE ?
        OR brand_name LIKE ?
        OR search_norm LIKE ?
        {ingredient_condition}
    ORDER BY
        {rank_sql},
        CASE
            WHEN source = 'local' THEN 1
            ELSE 2
        END{form_order},
        medicine_name
    LIMIT ?
    """
    return execute_query(
        query,
        (keyword_pattern, keyword_pattern, norm_pattern, *ingredient_params,
         *rank_params, limit),
    )


def _cache_result(cache_key, result_count):
    try:
        ensure_api_search_cache()
        record_api_search(cache_key, result_count)
    except Exception as e:
        print(f"Could not cache API search: {e}")


def _local_results(keyword, limit):
    return _dedupe_search_records(
        _filter_single_ingredient_search(keyword, _search_local(keyword, limit * 3))
    )[:limit]


def search_medicines_detailed(keyword, limit=20, enable_api_fallback=True, allow_correction=False):
    """Search like `search_medicines`, and say when a misspelling was corrected.

    Returns {"rows": [...], "corrected_query": str | None}. With
    `allow_correction`, a query that finds nothing (or only loose substring
    matches) is retried once with catalog words for its misspelled words; the
    corrected rows are used only if they match better, and the caller must
    show the user which query was actually searched.
    """
    keyword = (keyword or "").strip()
    ensure_catalog_schema()
    rows = _local_results(keyword, limit)
    if allow_correction and keyword:
        query = Query(keyword)
        tier = None if (not rows or _generic_key(keyword)) else best_tier(query, rows)
        if not rows or (tier is not None and tier >= 4):
            try:
                corrected = correct_query(query)
            except Exception as error:
                print(f"Spelling correction unavailable: {error}")
                corrected = None
            if corrected and corrected.casefold() != keyword.casefold():
                corrected_rows = _local_results(corrected, limit)
                corrected_tier = (0 if _generic_key(corrected)
                                  else best_tier(Query(corrected), corrected_rows))
                if corrected_rows and (not rows or corrected_tier < tier):
                    return {"rows": corrected_rows, "corrected_query": corrected}
    if rows or not enable_api_fallback:
        return {"rows": rows, "corrected_query": None}
    return {"rows": search_medicines(keyword, limit, enable_api_fallback), "corrected_query": None}


def search_medicines(keyword, limit=20, enable_api_fallback=True):
    """
    Search medicines by medicine name, brand name, or normalized text.

    Local data is searched first. On a miss, the India drug catalog (DrugDB)
    is queried and its structured records are saved locally, then RxNorm is
    used as a last resort. Search attempts are cached.

    Parameters
    ----------
    keyword : str
        Search term
    limit : int
        Maximum results to return
    enable_api_fallback : bool
        Whether to query external APIs if local results are insufficient

    Returns
    -------
    list
        Medicine records matching search
    """
    keyword = (keyword or "").strip()
    ensure_catalog_schema()

    # Over-fetch because combination products are filtered out for generic
    # ingredient searches after the query has been ranked.
    local_results = _dedupe_search_records(
        _filter_single_ingredient_search(keyword, _search_local(keyword, limit * 3))
    )

    # An existing local match is enough; don't call an external API just to fill the page.
    if local_results or not enable_api_fallback:
        return local_results[:limit]

    cache_key = _normalized_search_key(keyword)
    try:
        ensure_api_search_cache()
        if has_recent_api_search(cache_key):
            note_issue("cached_miss")
            return local_results
    except Exception as e:
        # The search still works if cache setup fails; it just won't suppress retries.
        print(f"API search cache unavailable: {e}")

    # Both catalog sources return structured composition; the pharmacy catalog
    # also carries a listing price, so its rows can compare prices immediately.
    print(f"No local matches for '{keyword}', querying the India drug catalog...")
    try:
        catalog_results = _filter_single_ingredient_search(
            keyword, search_catalog(keyword, limit=min(limit, 10))
        )
        source_name = "India drug catalog"
        if not catalog_results:
            print(f"No India catalog matches for '{keyword}', querying the live pharmacy catalog...")
            catalog_results = _filter_single_ingredient_search(
                keyword, search_pharmeasy_catalog(keyword, limit=min(limit, 10))
            )
            source_name = "live pharmacy catalog"
        saved_ids = []
        for medicine in catalog_results:
            medicine_id = save_catalog_medicine(medicine)
            if medicine_id and medicine_id not in saved_ids:
                saved_ids.append(medicine_id)
        if saved_ids:
            results = _dedupe_search_records(fetch_medicines(saved_ids))[:limit]
            print(f"Saved {len(results)} medicines from the {source_name}")
            _cache_result(cache_key, len(results))
            return results
    except Exception as e:
        print(f"Catalog fallback failed: {e}")
        note_issue("India drug catalog / PharmEasy")

    # RxNorm covers medicines the Indian catalog does not list; these records
    # carry no composition or price, so they cannot be matched for alternatives.
    try:
        print(f"No catalog matches for '{keyword}', querying RxNorm API...")

        api_results = _filter_single_ingredient_search(keyword, search_rxnorm(keyword, limit=10))

        saved_count = 0
        for medicine in api_results:
            if save_api_medicine(medicine) > 0:
                saved_count += 1

        if saved_count > 0:
            print(f"Saved {saved_count} medicines from RxNorm API")

        # Re-query SQLite so results and IDs come from the same persistent store.
        results = _dedupe_search_records(
            _filter_single_ingredient_search(keyword, _search_local(keyword, limit * 3))
        )[:limit]
        # Cache successful matches for 30 days; cache empty responses for one
        # day so transient API/network failures don't block retries for a month.
        _cache_result(cache_key, len(results))
        return results

    except Exception as e:
        print(f"API fallback failed: {e}")

    # Return whatever we found locally
    return local_results[:limit]


# ============================================================
# Get Medicine by ID
# ============================================================

def get_medicine(medicine_id):

    query = """
    SELECT *
    FROM medicines
    WHERE id = ?
    """

    result = execute_query(query, (medicine_id,))

    if result:
        return result[0]

    return None


# ============================================================
# Search by Brand
# ============================================================

def search_by_brand(brand):

    query = """
    SELECT *
    FROM medicines
    WHERE brand_name LIKE ?
    ORDER BY medicine_name
    """

    return execute_query(query, (f"%{brand}%",))


# ============================================================
# Search by Category
# ============================================================

def search_by_category(category):

    query = """
    SELECT *
    FROM medicines
    WHERE medicine_category = ?
    ORDER BY medicine_name
    """

    return execute_query(query, (category,))


# ============================================================
# Cheapest Medicines
# ============================================================

def cheapest_medicines(limit=20):

    query = """
    SELECT *
    FROM medicines
    WHERE price IS NOT NULL
    ORDER BY price ASC
    LIMIT ?
    """

    return execute_query(query, (limit,))


# ============================================================
# Search by Composition
# ============================================================

def search_by_composition(text):

    query = """
    SELECT *
    FROM medicines
    WHERE composition LIKE ?
    ORDER BY medicine_name
    """

    return execute_query(query, (f"%{text}%",))


# ============================================================
# Standalone Testing
# ============================================================

if __name__ == "__main__":

    print("=" * 60)
    print("MED FINDER - SERVICE TEST")
    print("=" * 60)

    medicines = search_medicines("Dolo")

    print(f"Results : {len(medicines)}")

    print()

    for medicine in medicines[:10]:

        print(
            medicine["medicine_name"],
            "| ₹",
            medicine["price"]
        )
