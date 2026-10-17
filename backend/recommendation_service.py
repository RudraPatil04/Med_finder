"""
============================================================
MED FINDER
GENERIC & SUBSTITUTE RECOMMENDATION SERVICE
Version : 1.0
============================================================
"""

from backend.database import execute_query


def get_substitutes(medicine_id, limit=5):
    """
    Find cheaper generic or alternative medicines sharing
    the same composition or category.

    Parameters
    ----------
    medicine_id : int
        ID of target medicine
    limit : int
        Max substitutes to return

    Returns
    -------
    dict
        Substitutes list and potential savings percentage
    """
    # 1. Fetch current medicine details
    target_query = "SELECT * FROM medicines WHERE id = ? LIMIT 1"
    target = execute_query(target_query, (medicine_id,))

    if not target:
        return {"success": False, "message": "Medicine not found", "data": []}

    med = dict(target[0])
    composition = med.get("composition") or ""
    current_price = float(med.get("price") or 0.0)

    if not composition:
        # Fallback to category matching if composition is missing
        cat_query = """
        SELECT * FROM medicines
        WHERE medicine_category = ? AND id != ? AND price IS NOT NULL
        ORDER BY price ASC
        LIMIT ?
        """
        results = execute_query(cat_query, (med.get("medicine_category"), medicine_id, limit))
    else:
        # Match by composition
        comp_query = """
        SELECT * FROM medicines
        WHERE composition LIKE ? AND id != ? AND price IS NOT NULL
        ORDER BY price ASC
        LIMIT ?
        """
        results = execute_query(comp_query, (f"%{composition.split('+')[0].strip()}%", medicine_id, limit))

    # Calculate savings
    substitutes = []
    max_savings_percent = 0
    max_savings_amount = 0.0

    for raw_item in results:
        item = dict(raw_item)
        sub_price = float(item.get("price") or 0.0)
        savings_amount = max(0.0, current_price - sub_price) if current_price > 0 else 0.0
        savings_pct = round((savings_amount / current_price * 100), 1) if current_price > 0 else 0.0

        if savings_pct > max_savings_percent:
            max_savings_percent = savings_pct
            max_savings_amount = savings_amount

        substitutes.append({
            **item,
            "savings_amount": round(savings_amount, 2),
            "savings_percent": savings_pct
        })

    return {
        "success": True,
        "original_medicine": dict(med),
        "count": len(substitutes),
        "max_savings_percent": max_savings_percent,
        "max_savings_amount": max_savings_amount,
        "substitutes": substitutes
    }
