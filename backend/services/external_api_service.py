"""
============================================================
MED FINDER
EXTERNAL API SERVICE - RxNorm Integration
Version : 1.0

Purpose:
Query external medicine APIs when local database has no results.

Author : Naman (with AI assistance)
============================================================
"""

import requests
import sys
from pathlib import Path

# ------------------------------------------------------------
# Add Project Root
# ------------------------------------------------------------

ROOT_DIR = Path(__file__).resolve().parent.parent.parent

if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from backend.config import RXNORM_API_BASE_URL, RXNORM_API_TIMEOUT
from backend.database import execute_non_query
from backend.services.pharmeasy_service import get_pharmeasy_price


# ============================================================
# Search RxNorm API
# ============================================================

def search_rxnorm(query, limit=10, fetch_prices=True):
    """
    Search RxNorm API for medicine information and optionally fetch prices.

    Parameters
    ----------
    query : str
        Medicine name to search
    limit : int
        Maximum results to return
    fetch_prices : bool
        Whether to scrape prices from 1mg

    Returns
    -------
    list
        List of medicine dictionaries matching our schema
    """

    if not query or len(query.strip()) < 2:
        return []

    try:
        # RxNorm API endpoint for drug search
        url = f"{RXNORM_API_BASE_URL}/drugs.json"
        params = {"name": query.strip()}

        response = requests.get(
            url,
            params=params,
            timeout=RXNORM_API_TIMEOUT
        )

        if response.status_code != 200:
            print(f"RxNorm API returned status {response.status_code}")
            return []

        data = response.json()

        # Parse RxNorm response
        medicines = []
        drug_group = data.get("drugGroup", {})
        concept_group = drug_group.get("conceptGroup", [])

        for group in concept_group:
            if "conceptProperties" not in group:
                continue

            for concept in group["conceptProperties"][:limit]:
                medicine = parse_rxnorm_concept(concept)
                if medicine:
                    # Try to fetch price if enabled
                    if fetch_prices and medicine['price'] is None:
                        print(f"Attempting to fetch price for: {medicine['medicine_name']}")
                        price_data = get_pharmeasy_price(medicine['medicine_name'])

                        if price_data:
                            medicine['price'] = price_data['price']
                            print(f"  -> Price found: Rs. {price_data['price']}")
                        else:
                            print(f"  -> Price not found")

                    medicines.append(medicine)

        return medicines[:limit]

    except requests.Timeout:
        print(f"RxNorm API timeout after {RXNORM_API_TIMEOUT} seconds")
        return []

    except Exception as e:
        print(f"Error querying RxNorm API: {e}")
        return []


# ============================================================
# Parse RxNorm Concept to Our Schema
# ============================================================

def parse_rxnorm_concept(concept):
    """
    Convert RxNorm concept to our medicine schema.

    Parameters
    ----------
    concept : dict
        RxNorm concept data

    Returns
    -------
    dict
        Medicine in our database format
    """

    try:
        name = concept.get("name", "").strip()
        synonym = concept.get("synonym", "").strip()

        if not name:
            return None

        # Extract strength if present in name
        strength = extract_strength(name)

        return {
            "medicine_name": name,
            "brand_name": synonym if synonym else name.split()[0],
            "strength": strength,
            "dosage_form": extract_dosage_form(name),
            "medicine_category": "External",
            "composition": name,  # RxNorm doesn't provide detailed composition
            "price": None,
            "prescription_required": "Unknown",
            "image_url": None,
            "source": "api"
        }

    except Exception as e:
        print(f"Error parsing RxNorm concept: {e}")
        return None


# ============================================================
# Helper: Extract Strength
# ============================================================

def extract_strength(name):
    """Extract dosage strength from medicine name."""
    import re

    # Look for patterns like "500mg", "10mg/ml", "5%"
    match = re.search(r'\d+(\.\d+)?\s*(mg|mcg|g|ml|%|IU)', name, re.IGNORECASE)

    if match:
        return match.group(0)

    return None


# ============================================================
# Helper: Extract Dosage Form
# ============================================================

def extract_dosage_form(name):
    """Extract dosage form from medicine name."""

    name_lower = name.lower()

    forms = [
        "tablet", "capsule", "syrup", "injection",
        "cream", "gel", "ointment", "drops",
        "spray", "inhaler", "powder", "solution"
    ]

    for form in forms:
        if form in name_lower:
            return form.capitalize()

    return "Medicine"


# ============================================================
# Save API Result to Database
# ============================================================

def save_api_medicine(medicine):
    """
    Save medicine from API to local database.

    Parameters
    ----------
    medicine : dict
        Medicine data to save

    Returns
    -------
    int
        Number of rows affected
    """

    query = """
    INSERT INTO medicines(
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
    )
    VALUES (?,?,?,?,?,?,?,?,?,?)
    """

    params = (
        medicine.get("medicine_name"),
        medicine.get("brand_name"),
        medicine.get("strength"),
        medicine.get("dosage_form"),
        medicine.get("medicine_category"),
        medicine.get("composition"),
        medicine.get("price"),
        medicine.get("prescription_required"),
        medicine.get("image_url"),
        medicine.get("source", "api")
    )

    try:
        return execute_non_query(query, params)
    except Exception as e:
        print(f"Error saving API medicine to database: {e}")
        return 0


# ============================================================
# Standalone Testing
# ============================================================

if __name__ == "__main__":

    print("=" * 60)
    print("RXNORM API TEST")
    print("=" * 60)

    test_query = "aspirin"

    print(f"\nSearching RxNorm for: {test_query}")

    results = search_rxnorm(test_query, limit=5)

    print(f"\nFound {len(results)} results:\n")

    for med in results:
        print(f"- {med['medicine_name']}")
        print(f"  Brand: {med['brand_name']}")
        print(f"  Strength: {med['strength']}")
        print(f"  Form: {med['dosage_form']}")
        print()
