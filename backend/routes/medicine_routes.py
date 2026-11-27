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
    search_medicines_detailed,
    suggest_functional_medicines,
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
from backend.services.india_medicine_api import search_live_alternatives
from backend.medicine_data import parse_composition, restricted_reason
from backend.services.price_comparison_service import compare_current_prices
from backend.services.price_enrichment_service import persist_reference_price
from backend.services.enrichment_worker import request_enrichment
from backend.services.live_price_refresh import substitutes_with_live_prices, live_prices_enabled
from backend.services.image_lookup import MAX_IDS_PER_REQUEST, fill_images

from backend.services.external_api_service import pop_issues
from backend.services.search_notice_service import related_by_ingredient, build_notice

medicine_bp = Blueprint("medicine", __name__)


@medicine_bp.after_request
def disable_api_cache(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


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

    # exact=1 searches the words as typed, without spelling correction.
    exact = request.args.get("exact", "").strip() == "1"

    pop_issues()
    result = search_medicines_detailed(keyword, allow_correction=not exact)
    rows = [dict(row) for row in result["rows"]]
    corrected = result.get("corrected_query")
    ingredient, related = related_by_ingredient(rows)
    notice = build_notice(corrected or keyword, rows, pop_issues(), ingredient, related)

    return jsonify({
        "success": True,
        "count": len(rows),
        "data": rows,
        "notice": notice,
        "related": related,
        # Set when nothing matched the typed words and a corrected spelling was
        # searched instead; the page must say so.
        "corrected_query": corrected,
        "original_query": keyword,
    })


# ==========================================================
# Search Suggestions
# ==========================================================

@medicine_bp.route("/search-suggestions", methods=["GET"])
def search_suggestions():

    keyword = request.args.get("q", "").strip()

    suggestions = suggest_functional_medicines(keyword)

    return jsonify({
        "success": True,
        "count": len(suggestions),
        "data": suggestions
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

    try:
        needs_enrichment = (
            not medicine["composition_complete"]
            or not medicine["pack_size"]
            or medicine["mrp"] is None
        )
    except IndexError:
        needs_enrichment = True
    if needs_enrichment:
        request_enrichment(medicine_id)

    return jsonify({
        "success": True,
        "data": dict(medicine)
    })


@medicine_bp.route("/medicine-images", methods=["GET"])
def medicine_images():
    """Verified product photos for rows that have none (looked up once, then cached in the DB)."""
    ids = []
    for part in (request.args.get("ids") or "").split(","):
        if part.strip().isdigit() and int(part) not in ids:
            ids.append(int(part))
    ids = ids[:MAX_IDS_PER_REQUEST]
    if not ids or not live_prices_enabled():
        return jsonify({"success": True, "images": {}})
    try:
        rows = [dict(row) for row in (get_medicine(i) for i in ids) if row is not None]
        found = fill_images(rows)
    except Exception:
        found = {}
    return jsonify({"success": True, "images": {str(k): v for k, v in found.items()}})


# ==========================================================
# Generic / Cheaper Substitutes
# ==========================================================

@medicine_bp.route("/substitutes/<int:medicine_id>", methods=["GET"])
def medicine_substitutes(medicine_id):

    limit = request.args.get("limit", 5, type=int)
    # live=0 skips the price lookups and answers from stored prices only.
    if request.args.get("live", "1") == "0":
        result = get_substitutes(medicine_id, limit=limit)
    else:
        result = substitutes_with_live_prices(medicine_id, limit=limit)

    return jsonify(result)


def _restricted_live_response(medicine):
    """Restricted classes get no live listings either, same as /api/substitutes."""
    medicine = dict(medicine)
    names = [row["ingredient_name"] for row in parse_composition(medicine.get("composition"))]
    reason = restricted_reason(medicine, names)
    if not reason:
        return None
    return {"success": True, "restricted": True, "comparison_available": False, "reference": None,
            "count": 0, "matches": [],
            "message": f"Other products are not listed because {reason}. Ask your doctor or pharmacist."}


@medicine_bp.route("/live-alternatives/<int:medicine_id>", methods=["GET"])
def live_medicine_alternatives(medicine_id):
    """Query the India drug information API at request time, without caching results."""
    medicine = get_medicine(medicine_id)
    if medicine is None:
        return jsonify({"success": False, "message": "Medicine not found.", "matches": []}), 404
    blocked = _restricted_live_response(medicine)
    if blocked:
        return jsonify(blocked)
    limit = request.args.get("limit", 10, type=int)
    return jsonify(search_live_alternatives(medicine, limit=limit))


@medicine_bp.route("/price-alternatives/<int:medicine_id>", methods=["GET"])
def live_price_alternatives(medicine_id):
    """Fetch current same-composition pharmacy listings and compare matching packs."""
    medicine = get_medicine(medicine_id)
    if medicine is None:
        return jsonify({"success": False, "message": "Medicine not found.", "matches": []}), 404
    blocked = _restricted_live_response(medicine)
    if blocked:
        return jsonify(blocked)
    limit = request.args.get("limit", 10, type=int)
    result = compare_current_prices(medicine, limit=limit)
    if result.get("reference") and float(medicine["price"] or 0) <= 0:
        persist_reference_price(medicine_id, result["reference"], result.get("reference_pack"))
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
