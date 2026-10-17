"""
============================================================
MED FINDER
FLASK APPLICATION - Web & API Server
Version : 2.0
Author : Naman (with AI assistance)
============================================================
"""

import sys
from pathlib import Path
from flask import Flask, jsonify, render_template, request

# Add root project path to sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from backend.routes.medicine_routes import medicine_bp


# ============================================================
# Create Flask App with Templates & Static Assets
# ============================================================

app = Flask(
    __name__,
    template_folder=str(ROOT_DIR / "templates"),
    static_folder=str(ROOT_DIR / "static")
)

app.config["JSON_SORT_KEYS"] = False
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB max upload


# ============================================================
# Register Blueprints
# ============================================================

app.register_blueprint(
    medicine_bp,
    url_prefix="/api"
)


# ============================================================
# Frontend Web Routes (HTML Views)
# ============================================================

@app.route("/")
def home_page():
    """Render home landing page."""
    return render_template("index.html")


@app.route("/search")
def search_page():
    """Render medicine search and results page."""
    query = request.args.get("q", "")
    return render_template("results.html", query=query)


@app.route("/medicine/<int:medicine_id>")
def medicine_detail_page(medicine_id):
    """Render single medicine detail page."""
    return render_template("medicine_detail.html", medicine_id=medicine_id)


@app.route("/scanner")
@app.route("/scan")
def scanner_page():
    """Render prescription and medicine label OCR scanner."""
    return render_template("scan_medicine.html")


@app.route("/disease")
@app.route("/disease-guidance")
def disease_page():
    """Render disease and symptom guidance page."""
    return render_template("disease_guidance.html")


@app.route("/assistant")
def assistant_page():
    """Render AI health assistant chat interface."""
    return render_template("assistant.html")


# ============================================================
# Health & Status Routes
# ============================================================

@app.route("/health")
@app.route("/api/health")
def health():
    return jsonify({
        "status": "Healthy",
        "service": "MED_FINDER",
        "version": "2.0"
    })


@app.route("/api/info")
def info():
    return jsonify({
        "application": "MED FINDER",
        "version": "2.0",
        "status": "Running",
        "database": "SQLite",
        "features": [
            "Medicine Search & Autocomplete",
            "RxNorm Global API Fallback",
            "PharmEasy Real-time Price Scraping",
            "Prescription OCR Scanner",
            "Disease & Symptom Guidance",
            "AI Health Assistant"
        ]
    })


# ============================================================
# Run Server
# ============================================================

if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True
    )
