"""
============================================================
MED FINDER
MEDICINE SERVICE
Version : 2.0

Author : Naman
============================================================
"""

import re

from backend.database import execute_query
from backend.services.api_cache import (
    ensure_api_search_cache, has_recent_api_search, record_api_search,
)
from backend.services.drug_catalog_service import (
    ensure_catalog_schema, fetch_medicines, save_catalog_medicine,
    search_catalog, search_pharmeasy_catalog,
)
from backend.services.external_api_service import search_rxnorm, save_api_medicine
from backend.medicine_data import normalize_medication_text, parse_composition


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


def _search_local(keyword, limit):
    query = f"""
    SELECT {SEARCH_COLUMNS}
    FROM medicines
    WHERE
        medicine_name LIKE ?
        OR brand_name LIKE ?
        OR search_norm LIKE ?
    ORDER BY
        CASE
            WHEN lower(trim(medicine_name)) = lower(trim(?)) THEN 0
            WHEN lower(trim(brand_name)) = lower(trim(?)) THEN 1
            WHEN lower(medicine_name) LIKE lower(?) THEN 2
            WHEN lower(brand_name) LIKE lower(?) THEN 3
            ELSE 4
        END,
        CASE
            WHEN source = 'local' THEN 1
            ELSE 2
        END,
        medicine_name
    LIMIT ?
    """
    keyword_pattern = f"%{keyword}%"
    norm_keyword = normalize_medication_text(keyword)
    norm_pattern = f"%{norm_keyword}%" if norm_keyword else keyword_pattern
    prefix_pattern = f"{keyword}%"
    return execute_query(
        query,
        (keyword_pattern, keyword_pattern, norm_pattern,
         keyword, keyword, prefix_pattern, prefix_pattern, limit),
    )


def _cache_result(cache_key, result_count):
    try:
        ensure_api_search_cache()
        record_api_search(cache_key, result_count)
    except Exception as e:
        print(f"Could not cache API search: {e}")


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

    local_results = _filter_single_ingredient_search(keyword, _search_local(keyword, limit))

    # An existing local match is enough; don't call an external API just to fill the page.
    if local_results or not enable_api_fallback:
        return local_results

    cache_key = _normalized_search_key(keyword)
    try:
        ensure_api_search_cache()
        if has_recent_api_search(cache_key):
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
            results = fetch_medicines(saved_ids)
            print(f"Saved {len(results)} medicines from the {source_name}")
            _cache_result(cache_key, len(results))
            return results
    except Exception as e:
        print(f"Catalog fallback failed: {e}")

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
        results = _filter_single_ingredient_search(keyword, _search_local(keyword, limit))
        # Cache successful matches for 30 days; cache empty responses for one
        # day so transient API/network failures don't block retries for a month.
        _cache_result(cache_key, len(results))
        return results

    except Exception as e:
        print(f"API fallback failed: {e}")

    # Return whatever we found locally
    return local_results


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
