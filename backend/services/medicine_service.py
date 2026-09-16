"""
============================================================
MED FINDER
MEDICINE SERVICE
Version : 1.0

Author : Naman
============================================================
"""

from backend.database import execute_query


# ============================================================
# Search Medicines
# ============================================================

def search_medicines(keyword, limit=20):
    """
    Search medicines by medicine name or brand name.
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
        image_url
    FROM medicines
    WHERE
        medicine_name LIKE ?
        OR brand_name LIKE ?
    ORDER BY medicine_name
    LIMIT ?
    """

    keyword = f"%{keyword}%"

    return execute_query(query, (keyword, keyword, limit))


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