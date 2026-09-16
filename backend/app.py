"""
============================================================
MED FINDER
FLASK APPLICATION

Version : 1.0
Author : Naman
============================================================
"""

from flask import Flask, jsonify

from backend.routes.medicine_routes import medicine_bp


# ============================================================
# Create Flask App
# ============================================================

app = Flask(__name__)

app.config["JSON_SORT_KEYS"] = False


# ============================================================
# Register Blueprints
# ============================================================

app.register_blueprint(
    medicine_bp,
    url_prefix="/api"
)


# ============================================================
# Home Route
# ============================================================

@app.route("/")
def home():

    return jsonify({

        "application": "MED FINDER",

        "version": "1.0",

        "status": "Running",

        "database": "SQLite",

        "records": "348,211 Medicines"

    })


# ============================================================
# Health Check
# ============================================================

@app.route("/health")
def health():

    return jsonify({

        "status": "Healthy"

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