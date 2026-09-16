"""
============================================================
MED FINDER
MEDICINE SERVICE
Version : 2.0

Author : Naman
============================================================
"""

from backend.database import execute_query
from backend.services.external_api_service import search_rxnorm, save_api_medicine


# ============================================================
# Search Medicines
# ============================================================

def search_medicines(keyword, limit=20, enable_api_fallback=True):
    """
    Search medicines by medicine name or brand name.

    Searches local database first. If results are insufficient
    and API fallback is enabled, queries RxNorm API and saves
    results to database.

    Parameters
    ----------
    keyword : str
        Search term
    limit : int
        Maximum results to return
    enable_api_fallback : bool
        Whether to query external API if local results are insufficient

    Returns
    -------
    list
        Medicine records matching search
    """

    query = """
    SELECT
        id,
        medicine_name,
        brand_name,
        strength,
        dosage_form,
        medicine_category,
        composition,
        price,
        prescription_required,
        image_url,
        source
    FROM medicines
    WHERE
        medicine_name LIKE ?
        OR brand_name LIKE ?
    ORDER BY
        CASE
            WHEN source = 'local' THEN 1
            ELSE 2
        END,
        medicine_name
    LIMIT ?
    """

    keyword_pattern = f"%{keyword}%"

    local_results = execute_query(query, (keyword_pattern, keyword_pattern, limit))

    # If we have enough results or API fallback is disabled, return
    if len(local_results) >= 5 or not enable_api_fallback:
        return local_results

    # Try external API
    try:
        print(f"Local results insufficient ({len(local_results)}), querying RxNorm API...")

        api_results = search_rxnorm(keyword, limit=10)

        # Save API results to database
        saved_count = 0
        for medicine in api_results:
            if save_api_medicine(medicine) > 0:
                saved_count += 1

        if saved_count > 0:
            print(f"Saved {saved_count} medicines from RxNorm API")

            # Re-query database to get combined results
            all_results = execute_query(query, (keyword_pattern, keyword_pattern, limit))
            return all_results

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