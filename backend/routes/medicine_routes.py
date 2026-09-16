"""
============================================================
MED FINDER
MEDICINE ROUTES
Version : 1.0

Author : Naman
============================================================
"""

from flask import Blueprint, jsonify, request

from backend.services.medicine_service import (
    search_medicines,
    get_medicine,
    search_by_brand,
    search_by_category,
    search_by_composition,
    cheapest_medicines
)

medicine_bp = Blueprint("medicine", __name__)


# ==========================================================
# Search Medicines
# ==========================================================

@medicine_bp.route("/search", methods=["GET"])
def search():

    keyword = request.args.get("q", "").strip()

    if keyword == "":
        return jsonify({
            "success": False,
            "message": "Search keyword is required."
        }), 400

    medicines = search_medicines(keyword)

    return jsonify({
        "success": True,
        "count": len(medicines),
        "data": [dict(row) for row in medicines]
    })


# ==========================================================
# Medicine Details
# ==========================================================

@medicine_bp.route("/medicine/<int:medicine_id>", methods=["GET"])
def medicine_details(medicine_id):

    medicine = get_medicine(medicine_id)

    if medicine is None:

        return jsonify({
            "success": False,
            "message": "Medicine not found."
        }), 404

    return jsonify({
        "success": True,
        "data": dict(medicine)
    })


# ==========================================================
# Search by Brand
# ==========================================================

@medicine_bp.route("/brand/<brand>", methods=["GET"])
def brand_search(brand):

    medicines = search_by_brand(brand)

    return jsonify({
        "success": True,
        "count": len(medicines),
        "data": [dict(row) for row in medicines]
    })


# ==========================================================
# Search by Category
# ==========================================================

@medicine_bp.route("/category/<category>", methods=["GET"])
def category_search(category):

    medicines = search_by_category(category)

    return jsonify({
        "success": True,
        "count": len(medicines),
        "data": [dict(row) for row in medicines]
    })


# ==========================================================
# Search by Composition
# ==========================================================

@medicine_bp.route("/composition/<composition>", methods=["GET"])
def composition_search(composition):

    medicines = search_by_composition(composition)

    return jsonify({
        "success": True,
        "count": len(medicines),
        "data": [dict(row) for row in medicines]
    })


# ==========================================================
# Cheapest Medicines
# ==========================================================

@medicine_bp.route("/cheapest", methods=["GET"])
def cheapest():

    medicines = cheapest_medicines()

    return jsonify({
        "success": True,
        "count": len(medicines),
        "data": [dict(row) for row in medicines]
    })