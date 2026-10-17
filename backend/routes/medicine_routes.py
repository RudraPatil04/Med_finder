"""
============================================================
MED FINDER
MEDICINE & PLATFORM ROUTES
Version : 2.0
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
from backend.disease_service import get_disease_by_id_or_name, get_all_diseases
from backend.assistant_service import process_assistant_query
from backend.ocr_service import parse_prescription_medicines
from backend.recommendation_service import get_substitutes

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
# Generic / Cheaper Substitutes
# ==========================================================

@medicine_bp.route("/substitutes/<int:medicine_id>", methods=["GET"])
def medicine_substitutes(medicine_id):

    limit = request.args.get("limit", 5, type=int)
    result = get_substitutes(medicine_id, limit=limit)

    return jsonify(result)


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


# ==========================================================
# Disease Guidance API
# ==========================================================

@medicine_bp.route("/disease", methods=["GET"])
def disease_guidance_api():

    query = request.args.get("q", "").strip()

    if not query:
        return jsonify({
            "success": True,
            "data": get_disease_by_id_or_name(None),
            "all": get_all_diseases()
        })

    disease = get_disease_by_id_or_name(query)

    return jsonify({
        "success": True,
        "data": disease
    })


# ==========================================================
# AI Health Assistant API
# ==========================================================

@medicine_bp.route("/assistant", methods=["POST"])
def assistant_chat_api():

    data = request.get_json(silent=True) or {}
    user_message = data.get("message", "").strip()

    if not user_message:
        return jsonify({
            "success": False,
            "message": "Message is required."
        }), 400

    reply = process_assistant_query(user_message)

    return jsonify({
        "success": True,
        "reply": reply
    })


# ==========================================================
# Prescription OCR API
# ==========================================================

@medicine_bp.route("/ocr", methods=["POST"])
def prescription_ocr_api():

    if "file" not in request.files:
        return jsonify({
            "success": False,
            "message": "No prescription file uploaded."
        }), 400

    file = request.files["file"]

    if file.filename == "":
        return jsonify({
            "success": False,
            "message": "Empty file."
        }), 400

    result = parse_prescription_medicines(file)

    return jsonify(result)
