"""
Catalog products for a condition's "Related medicines" section.

Only a small curated list of active ingredients commonly used for symptom relief
is used. Products are read from the local catalog (no live lookups): a product is
shown only when its recorded ingredient set is exactly the curated one, in the
curated form and strength, with a complete composition and a recorded price, and
it is not a restricted class. Cheapest per unit first. Nothing here is a
recommendation for a person, and no dose is ever given.
"""

import threading

from backend.database import execute_query
from backend.medicine_data import restricted_reason
from backend.recommendation_service import _fresh_price, _price_per_unit

RELIEF_NOTE = ("Commonly used for symptom relief. Not a recommendation for you. "
               "Ask your doctor or pharmacist before taking any medicine.")
PHARMACIST_LINE = ("A pharmacist can tell you whether one of these suits you, "
                   "and how to use it safely.")
PRODUCTS_PER_GROUP = 3

# key -> label, ingredient set {normalized name: allowed strengths or None}, forms
RELIEF_PRODUCTS = {
    "paracetamol": {"label": "Paracetamol (fever and pain)",
                    "ingredients": {"paracetamol": ("500mg", "650mg")}, "forms": ("Tablet",)},
    "ibuprofen": {"label": "Ibuprofen (pain and inflammation)",
                  "ingredients": {"ibuprofen": ("400mg",)}, "forms": ("Tablet",)},
    "cold_combo": {"label": "Paracetamol + phenylephrine + chlorpheniramine (cold combination)",
                   "ingredients": {"paracetamol": None, "phenylephrine": None,
                                   "chlorpheniraminemaleate": None},
                   "forms": ("Tablet",)},
    "cetirizine": {"label": "Cetirizine (allergy and runny nose)",
                   "ingredients": {"cetirizine": ("10mg",)}, "forms": ("Tablet",)},
    "levocetirizine": {"label": "Levocetirizine (allergy and runny nose)",
                       "ingredients": {"levocetirizine": ("5mg",)}, "forms": ("Tablet",)},
    "loratadine": {"label": "Loratadine (allergy)",
                   "ingredients": {"loratadine": ("10mg",)}, "forms": ("Tablet",)},
    "fexofenadine": {"label": "Fexofenadine (allergy)",
                     "ingredients": {"fexofenadine": ("120mg", "180mg")}, "forms": ("Tablet",)},
    "ors": {"label": "Oral rehydration salts (ORS)",
            "ingredients": {"anhydrousdextrose": None, "potassiumchloride": None,
                            "sodiumchloride": None, "sodiumcitrate": None},
            "forms": ("Sachet", "Powder")},
    "famotidine": {"label": "Famotidine (acidity)",
                   "ingredients": {"famotidine": ("20mg", "40mg")}, "forms": ("Tablet",)},
    "pantoprazole": {"label": "Pantoprazole (acidity)",
                     "ingredients": {"pantoprazole": ("40mg",)}, "forms": ("Tablet",)},
    "omeprazole": {"label": "Omeprazole (acidity)",
                   "ingredients": {"omeprazole": ("20mg",)}, "forms": ("Capsule",)},
    "diclofenac_gel": {"label": "Diclofenac gel (applied on the skin for muscle and joint pain)",
                       "ingredients": {"diclofenac": None}, "forms": ("Gel",)},
    "clotrimazole_topical": {"label": "Clotrimazole (applied on the skin for fungal infection)",
                             "ingredients": {"clotrimazole": None},
                             "forms": ("Cream", "Powder", "Dusting Powder", "Lotion")},
}

# The only keys an AI-written guide may pick: low-risk symptom relief. Anti-
# inflammatories and acid reducers are left out, because whether they are safe
# depends on the condition (for example dengue) and the person.
AI_RELIEF_KEYS = ("paracetamol", "cetirizine", "levocetirizine", "loratadine",
                  "fexofenadine", "ors", "clotrimazole_topical")

_cache = {}
_cache_lock = threading.Lock()


def _products_for(spec):
    names = list(spec["ingredients"])
    first = names[0]
    form_marks = ",".join("?" * len(spec["forms"]))
    rows = execute_query(
        f"""
        SELECT m.* FROM medicine_ingredients i
        JOIN medicines m ON m.id = i.medicine_id
        WHERE i.ingredient_name_norm = ?
          AND m.composition_complete = 1
          AND m.price > 0
          AND m.dosage_form COLLATE NOCASE IN ({form_marks})
          AND (SELECT COUNT(*) FROM medicine_ingredients j WHERE j.medicine_id = m.id) = ?
        """,
        (first, *spec["forms"], len(names)),
    )
    candidates = {row["id"]: dict(row) for row in rows}
    if not candidates:
        return []
    ids = list(candidates)
    ingredient_rows = []
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        ingredient_rows += execute_query(
            "SELECT medicine_id, ingredient_name_norm, ingredient_strength_norm "
            f"FROM medicine_ingredients WHERE medicine_id IN ({','.join('?' * len(chunk))})",
            tuple(chunk),
        )
    by_medicine = {}
    for row in ingredient_rows:
        by_medicine.setdefault(row["medicine_id"], {})[row["ingredient_name_norm"]] = row["ingredient_strength_norm"]

    products = []
    for medicine_id, medicine in candidates.items():
        recorded = by_medicine.get(medicine_id, {})
        if set(recorded) != set(names):
            continue
        if any(allowed and recorded[name] not in allowed for name, allowed in spec["ingredients"].items()):
            continue
        if restricted_reason(medicine, names):
            continue
        unit = _price_per_unit(medicine.get("price"), medicine.get("pack_size"))
        medicine["_sort"] = (0, unit[0]) if unit else (1, float(medicine["price"]))
        products.append(medicine)

    # One card per product name (the catalog repeats some listings).
    products.sort(key=lambda m: (m["_sort"], (m.get("medicine_name") or "").casefold()))
    seen, unique = set(), []
    for medicine in products:
        key = (medicine.get("medicine_name") or "").strip().casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(medicine)
        if len(unique) >= PRODUCTS_PER_GROUP:
            break
    return [_card(m) for m in unique]


def _card(medicine):
    return {
        "id": medicine["id"],
        "medicine_name": medicine.get("medicine_name"),
        "brand_name": medicine.get("brand_name"),
        "composition": medicine.get("composition"),
        "dosage_form": medicine.get("dosage_form"),
        "strength": medicine.get("strength"),
        "pack_size": medicine.get("pack_size"),
        "price": medicine.get("price"),
        "price_source": medicine.get("price_source"),
        "price_checked_at": medicine.get("price_checked_at"),
        "price_is_current": _fresh_price(medicine.get("price_checked_at")),
        "image_url": medicine.get("image_url"),
        "prescription_required": str(medicine.get("prescription_required") or "").strip().lower()
        in ("true", "1", "yes", "required"),
    }


def relief_groups(keys):
    """[{key, label, products}] for curated relief keys; unknown keys are ignored."""
    groups = []
    for key in keys or []:
        spec = RELIEF_PRODUCTS.get(key)
        if not spec:
            continue
        with _cache_lock:
            cached = _cache.get(key)
        if cached is None:
            cached = _products_for(spec)
            with _cache_lock:
                _cache[key] = cached
        if cached:
            groups.append({"key": key, "label": spec["label"], "products": cached})
    return groups


def related_medicines(disease):
    """The Related medicines section for a condition dict."""
    groups = relief_groups(disease.get("relief") or [])
    note = disease.get("medicine_note")
    if not groups and not note:
        note = "No suitable product is listed for this condition. Please ask a doctor or pharmacist."
    return {
        "groups": groups,
        "note": note,
        "relief_note": RELIEF_NOTE if groups else None,
        "pharmacist_line": PHARMACIST_LINE,
    }


def clear_cache():
    with _cache_lock:
        _cache.clear()
