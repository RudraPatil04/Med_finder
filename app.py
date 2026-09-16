"""
============================================================
MED FINDER
MAIN APPLICATION
============================================================
"""

from flask import Flask, render_template
from backend.routes.medicine_routes import medicine_bp

app = Flask(__name__)

# -------------------------------------------------------
# Register API Routes
# -------------------------------------------------------

app.register_blueprint(
    medicine_bp,
    url_prefix="/api"
)

# -------------------------------------------------------
# WEB ROUTES
# -------------------------------------------------------

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/search")
@app.route("/results")
def results():
    return render_template("results.html")


@app.route("/scanner")
@app.route("/scan")
def scan():
    return render_template("scan_medicine.html")


@app.route("/disease")
def disease():
    return render_template("disease_guidance.html")


@app.route("/assistant")
def assistant():
    return render_template("assistant.html")


@app.route("/medicine/<int:medicine_id>")
def medicine(medicine_id):
    return render_template("medicine_detail.html", medicine_id=medicine_id)


@app.route("/about")
def about():
    return render_template("index.html")


@app.route("/health")
def health():
    return {
        "status": "Healthy"
    }


# -------------------------------------------------------
# START APPLICATION
# -------------------------------------------------------

if __name__ == "__main__":

    app.run(
        debug=True
    )
