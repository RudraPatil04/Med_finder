"""Live India drug catalog (DrugDB / NRCeS) lookups that persist records locally.

Records added here are catalog facts only: name, brand, structured generic
composition, strength and dosage form. No price, pack size or equivalence claim
is invented by this module.
"""

import re
from datetime import datetime, timezone

import requests

from backend import database
from backend.database import execute_non_query, execute_query, get_connection
from backend.medicine_data import (
    canonical_composition, extract_pack_size, extract_release_type,
    first_alias_composition, make_product_key, normalize_measure,
    normalize_medication_text, parse_composition,
)
from backend.services.api_cache import has_recent_api_search, record_api_search
from backend.services.price_comparison_service import _search_payload

DRUGDB_BASE_URL = "https://drugdb.in"
DRUGDB_TIMEOUT_SECONDS = 8
DRUGDB_SOURCE = "DrugDB · NRCeS India drug catalog"
PHARMEASY_SOURCE = "PharmEasy live catalog"
ENRICHMENT_ATTEMPT_KEY = "enrich:{medicine_id}"

_UNIT_ALIASES = {
    "milligram": "mg", "milligrams": "mg", "microgram": "mcg", "micrograms": "mcg",
    "gram": "g", "grams": "g", "milliliter": "ml", "millilitre": "ml",
    "milliliters": "ml", "millilitres": "ml", "international unit": "IU",
    "international units": "IU",
}
_SOLID_DENOMINATORS = {"tablet", "each", "capsule", "vial", "piece", "unit", "sachet", "dose", "actuation"}
# Ordered: the first marker found in the dose-form text wins.
_FORM_MARKERS = (
    ("eye drop", "Eye Drop"), ("ophthalmic", "Eye Drop"),
    ("ear drop", "Ear Drop"), ("otic", "Ear Drop"),
    ("nasal spray", "Nasal Spray"), ("nasal", "Nasal Drop"),
    ("respule", "Respules"), ("inhaler", "Inhaler"), ("inhalation", "Inhaler"),
    ("suppositor", "Suppository"),
    ("tablet", "Tablet"), ("capsule", "Capsule"),
    ("syrup", "Syrup"), ("suspension", "Suspension"), ("emulsion", "Emulsion"),
    ("injection", "Injection"), ("infusion", "Infusion"),
    ("cream", "Cream"), ("ointment", "Ointment"), ("gel", "Gel"), ("lotion", "Lotion"),
    ("shampoo", "Shampoo"), ("soap", "Soap"), ("sachet", "Sachet"),
    ("granule", "Powder"), ("powder", "Powder"), ("spray", "Spray"),
    ("drop", "Drops"), ("solution", "Solution"),
)

_SCHEMA_READY_FOR = None


# ============================================================
# Schema
# ============================================================

def ensure_catalog_schema():
    """Add catalog provenance and normalized-search columns once per database file."""
    global _SCHEMA_READY_FOR
    database_path = database.MEDICINE_DATABASE
    if _SCHEMA_READY_FOR == database_path:
        return
    columns = {row["name"] for row in execute_query("PRAGMA table_info(medicines)")}
    if not columns:
        return
    for name, ddl in (
        ("api_source", "TEXT"),
        ("api_record_id", "TEXT"),
        ("api_fetched_at", "TEXT"),
        ("search_norm", "TEXT"),
    ):
        if name not in columns:
            execute_non_query(f"ALTER TABLE medicines ADD COLUMN {name} {ddl}")
    if execute_query("SELECT 1 FROM medicines WHERE search_norm IS NULL LIMIT 1"):
        _backfill_search_norm()
    _SCHEMA_READY_FOR = database_path


def _backfill_search_norm():
    last_id = 0
    while True:
        rows = execute_query(
            "SELECT id, medicine_name, brand_name FROM medicines "
            "WHERE id > ? AND search_norm IS NULL ORDER BY id LIMIT 5000",
            (last_id,),
        )
        if not rows:
            return
        updates = [
            (normalize_medication_text(f"{row['medicine_name'] or ''} {row['brand_name'] or ''}"), row["id"])
            for row in rows
        ]
        conn = get_connection()
        try:
            conn.executemany("UPDATE medicines SET search_norm = ? WHERE id = ?", updates)
            conn.commit()
        finally:
            conn.close()
        last_id = rows[-1]["id"]


def search_text(medicine_name, brand_name):
    return normalize_medication_text(f"{medicine_name or ''} {brand_name or ''}")


# ============================================================
# Record mapping
# ============================================================

def _clean(value):
    return " ".join(str(value or "").strip().split())


def _component_strength(component):
    numbers = [part.strip() for part in _clean(component.get("strength")).split("/") if part.strip()]
    unit_parts = [part.strip() for part in _clean(component.get("unit")).split("/") if part.strip()]
    if not numbers or not unit_parts:
        return None
    unit = _UNIT_ALIASES.get(unit_parts[0].casefold(), unit_parts[0].casefold())
    denominator_unit = ""
    if len(unit_parts) > 1 and unit_parts[1].casefold() not in _SOLID_DENOMINATORS:
        denominator_unit = _UNIT_ALIASES.get(unit_parts[1].casefold(), unit_parts[1].casefold())
    if len(numbers) > 1 and denominator_unit:
        return f"{numbers[0]}{unit}/{numbers[1]}{denominator_unit}"
    return f"{numbers[0]}{unit}"


def _dosage_form(text):
    haystack = _clean(text).casefold()
    for marker, form in _FORM_MARKERS:
        if marker in haystack:
            return form
    return None


def _category(detail):
    for entry in detail.get("drugTypes") or []:
        name = _clean(entry.get("typeName") if isinstance(entry, dict) else None)
        if name:
            return name
    return "Unclassified"


def _map_record(record):
    detail = record.get("genericDetail") or {}
    components = detail.get("composition") or []
    parsed = []
    for component in components:
        if not isinstance(component, dict):
            continue
        name = _clean(component.get("substanceName"))
        strength = _component_strength(component)
        if name and strength:
            parsed.append((name, strength))
    if not parsed:
        return None

    composition = canonical_composition(" + ".join(f"{name} ({strength})" for name, strength in parsed))
    if not parse_composition(composition):
        return None
    form = _dosage_form(detail.get("doseForm") or detail.get("name"))
    if not form:
        return None

    medicine_name = _clean(record.get("medicineName"))
    # The product title is more reliable than the brand field: some catalog
    # records carry another brand's name while the title names the product.
    title_brand = re.split(r"\s+\(", medicine_name)[0].strip() if "(" in medicine_name else ""
    brand = title_brand or _clean(record.get("brand.brandName"))
    strength = "/".join(strength for _, strength in parsed)
    display_name = f"{brand} {strength} {form}" if len(parsed) == 1 else f"{brand} {form}"
    return {
        "medicine_name": display_name,
        "brand_name": brand or None,
        "strength": strength,
        "dosage_form": form,
        "medicine_category": _category(detail),
        "composition": composition,
        "price": None,
        "price_source": None,
        "price_checked_at": None,
        "prescription_required": "Unknown",
        "image_url": None,
        "release_type": extract_release_type(medicine_name),
        "pack_size": extract_pack_size(medicine_name),
        "composition_complete": 1,
        "rxnorm_rxcui": None,
        "source": "api",
        "api_source": DRUGDB_SOURCE,
        "api_record_id": _clean(record.get("medicineSctid")) or None,
        "api_fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# ============================================================
# Live lookup
# ============================================================

def _fetch_records(query, limit):
    response = requests.get(
        f"{DRUGDB_BASE_URL}/search",
        params={"q": query, "type": "medicine", "detail": "full", "limit": max(1, min(limit, 50))},
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
        timeout=DRUGDB_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    results = payload.get("results", []) if isinstance(payload, dict) else []
    medicines = []
    for record in results:
        if not isinstance(record, dict):
            continue
        medicine = _map_record(record)
        if medicine:
            medicines.append(medicine)
    return medicines


def _matches_query(medicine, query):
    """Keep only catalog records whose own text contains every query token."""
    tokens = [token for token in re.split(r"[^a-z0-9]+", query.casefold()) if token]
    if not tokens:
        return True
    haystack = " ".join(
        str(medicine.get(key) or "") for key in ("brand_name", "medicine_name", "composition")
    ).casefold()
    return all(token in haystack for token in tokens)


def _query_variants(query):
    """Progressively looser queries for brands the catalog indexes without strength."""
    variants = [query]
    stripped = _clean(re.sub(
        r"\b\d+(?:\.\d+)?\s*(?:mcg|mg|g|ml|iu|units?|%)?\b", " ", query, flags=re.I,
    ))
    if stripped and stripped.casefold() != query.casefold() and len(stripped) >= 3:
        variants.append(stripped)
    seen = {variant.casefold() for variant in variants}
    for token in re.split(r"[^A-Za-z0-9]+", query):
        if len(token) >= 4 and not token.isdigit() and token.casefold() not in seen:
            variants.append(token)
            break
    return variants


def search_catalog(query, limit=10):
    """Return catalog medicines for a free-text query, or an empty list on failure."""
    query = _clean(query)
    if len(query) < 3:
        return []
    unique = {}
    for variant in _query_variants(query):
        if len(unique) >= limit:
            break
        try:
            medicines = _fetch_records(variant, limit * 2)
        except (requests.RequestException, ValueError) as exc:
            print(f"India drug catalog lookup failed ({type(exc).__name__})")
            continue
        for medicine in medicines:
            if not _matches_query(medicine, query):
                continue
            key = (
                normalize_medication_text(medicine.get("brand_name")),
                normalize_measure(medicine.get("strength")),
                normalize_medication_text(medicine.get("dosage_form")),
                normalize_medication_text(medicine.get("composition")),
            )
            unique.setdefault(key, medicine)
    return list(unique.values())[:limit]


# ============================================================
# PharmEasy catalog records
# ============================================================

def _title_brand(value):
    text = _clean(value)
    return text.title() if text.isupper() else text


_BRAND_STOP_WORDS = {
    "tablet", "tablets", "tab", "capsule", "capsules", "cap", "caps", "softgel",
    "softgels", "syrup", "suspension", "injection", "cream", "ointment", "gel",
    "drops", "drop", "spray", "inhaler", "solution", "sachet", "powder", "lotion",
    "shampoo", "soap", "strip", "bottle", "tube", "pack", "respules", "emulsion",
    "infusion", "granules",
}


def _title_brand_phrase(title, fallback):
    """Keep the product title's leading words, including sub-brands.

    `Becosules Z Strip Of 20 Capsules` must not collapse into the plain
    `Becosules` row; the title is the only place that keeps the `Z`.
    """
    words = []
    for word in _clean(title).split():
        if re.search(r"\d", word) or word.casefold().strip("()-") in _BRAND_STOP_WORDS:
            break
        words.append(word)
    phrase = " ".join(words).strip(" -")
    return _title_brand(phrase) if phrase else _title_brand(fallback)


def _pack_size_from_item(item):
    label = " ".join(
        str(item.get(key) or "") for key in ("measurementUnit", "subtitleText", "name")
    )
    match = re.search(r"(\d+)\s*(tablet|capsule|softgel|ml|sachet)", label, re.I)
    if not match:
        return None
    quantity = int(match.group(1))
    unit = match.group(2).casefold()
    return f"{quantity} {unit}" if unit == "ml" else f"{quantity} {unit}s"


def _map_pharmeasy_item(item):
    entries = [
        entry.get("name") for entry in (item.get("compositions") or [])
        if isinstance(entry, dict) and entry.get("name")
    ]
    if not entries:
        return None
    composition = first_alias_composition(" + ".join(entries))
    parsed = parse_composition(composition)
    title = _clean(item.get("name"))
    form = _dosage_form(title)
    brand = _title_brand_phrase(title, item.get("consumerBrandName"))
    if not parsed or not form or not brand:
        return None

    strength = "/".join(part["ingredient_strength"] for part in parsed)
    display_name = f"{brand} {strength} {form}" if len(parsed) == 1 else f"{brand} {form}"
    price = float(item.get("salePriceDecimal") or 0) or None
    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds") if price else None
    rx_required = item.get("isRxRequired")
    image = item.get("image")
    return {
        "medicine_name": display_name,
        "brand_name": brand,
        "strength": strength,
        "dosage_form": form,
        "medicine_category": "Unclassified",
        "composition": composition,
        "price": price,
        "price_source": "PharmEasy listing price" if price else None,
        "price_checked_at": checked_at,
        "prescription_required": "Yes" if rx_required is True else "No" if rx_required is False else "Unknown",
        "image_url": image if isinstance(image, str) and image.startswith("http") else None,
        "release_type": extract_release_type(title),
        "pack_size": _pack_size_from_item(item),
        "composition_complete": 1,
        "rxnorm_rxcui": None,
        "source": "api",
        "api_source": PHARMEASY_SOURCE,
        "api_record_id": str(item.get("productId") or "") or None,
        "api_fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def search_pharmeasy_catalog(query, limit=10):
    """Map live pharmacy products (composition + price) for a free-text query."""
    query = _clean(query)
    if len(query) < 3:
        return []
    try:
        items = _search_payload(query)
    except (requests.RequestException, ValueError, TypeError) as exc:
        print(f"PharmEasy catalog lookup failed ({type(exc).__name__})")
        return []
    unique = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        medicine = _map_pharmeasy_item(item)
        if not medicine or not _matches_query(medicine, query):
            continue
        key = medicine.get("api_record_id") or (
            normalize_medication_text(medicine.get("brand_name")),
            normalize_measure(medicine.get("strength")),
            normalize_medication_text(medicine.get("dosage_form")),
            normalize_medication_text(medicine.get("composition")),
        )
        unique.setdefault(key, medicine)
        if len(unique) >= limit:
            break
    return list(unique.values())[:limit]


# ============================================================
# Persistence
# ============================================================

_INSERT_SQL = """
INSERT INTO medicines(
    medicine_name, brand_name, strength, dosage_form, medicine_category,
    composition, price, price_source, price_checked_at, prescription_required,
    image_url, release_type, pack_size, composition_complete, rxnorm_rxcui,
    product_key, source, api_source, api_record_id, api_fetched_at, search_norm
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""


def _ingredient_rows(medicine_id, composition):
    return [
        (
            medicine_id, item["ingredient_name"], item["ingredient_name_norm"],
            item["ingredient_strength"], item["ingredient_strength_norm"],
        )
        for item in parse_composition(composition)
    ]


def _store_price_if_missing(medicine_id, medicine):
    """A later provider may know a price for a row saved without one."""
    price = float(medicine.get("price") or 0)
    if price <= 0:
        return
    execute_non_query(
        """UPDATE medicines SET price = ?, price_source = ?, price_checked_at = ?,
             pack_size = coalesce(pack_size, ?)
           WHERE id = ? AND (price IS NULL OR price <= 0)""",
        (
            price, medicine.get("price_source"), medicine.get("price_checked_at"),
            medicine.get("pack_size"), medicine_id,
        ),
    )


def save_catalog_medicine(medicine):
    """Insert a catalog record once; return the local row id (existing or new)."""
    ensure_catalog_schema()
    record_id = medicine.get("api_record_id")
    if record_id:
        existing = execute_query(
            "SELECT id FROM medicines WHERE api_source = ? AND api_record_id = ? LIMIT 1",
            (medicine.get("api_source") or DRUGDB_SOURCE, record_id),
        )
        if existing:
            _store_price_if_missing(existing[0]["id"], medicine)
            return existing[0]["id"]

    # A catalog product may already exist as an imported brand row.
    duplicate = execute_query(
        """SELECT id FROM medicines
           WHERE composition_complete = 1
             AND lower(coalesce(strength, '')) = lower(?)
             AND lower(coalesce(dosage_form, '')) = lower(?)
             AND lower(coalesce(brand_name, '')) = lower(?)
             AND replace(lower(coalesce(composition, '')), ' ', '') = replace(lower(?), ' ', '')
           LIMIT 1""",
        (
            medicine.get("strength"), medicine.get("dosage_form"),
            medicine.get("brand_name"), medicine.get("composition"),
        ),
    )
    if duplicate:
        _store_price_if_missing(duplicate[0]["id"], medicine)
        return duplicate[0]["id"]

    medicine = dict(medicine)
    medicine["product_key"] = make_product_key(
        medicine.get("medicine_name"), medicine.get("brand_name"),
        medicine.get("strength"), medicine.get("dosage_form"),
        medicine.get("composition"), medicine.get("release_type"), medicine.get("pack_size"),
    )
    medicine["search_norm"] = search_text(medicine.get("medicine_name"), medicine.get("brand_name"))

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(_INSERT_SQL, (
            medicine.get("medicine_name"), medicine.get("brand_name"),
            medicine.get("strength"), medicine.get("dosage_form"),
            medicine.get("medicine_category"), medicine.get("composition"),
            medicine.get("price"), medicine.get("price_source"), medicine.get("price_checked_at"),
            medicine.get("prescription_required"), medicine.get("image_url"),
            medicine.get("release_type"), medicine.get("pack_size"),
            int(bool(medicine.get("composition_complete"))), medicine.get("rxnorm_rxcui"),
            medicine["product_key"], medicine.get("source", "api"),
            medicine.get("api_source"), medicine.get("api_record_id"),
            medicine.get("api_fetched_at"), medicine["search_norm"],
        ))
        medicine_id = cursor.lastrowid
        rows = _ingredient_rows(medicine_id, medicine.get("composition"))
        cursor.executemany(
            "INSERT INTO medicine_ingredients("
            "medicine_id, ingredient_name, ingredient_name_norm, ingredient_strength, ingredient_strength_norm"
            ") VALUES (?,?,?,?,?)",
            rows,
        )
        conn.commit()
        return medicine_id
    except Exception as exc:
        conn.rollback()
        print(f"Could not save catalog medicine '{medicine.get('medicine_name')}': {exc}")
        return None
    finally:
        conn.close()


def fetch_medicines(ids):
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    rows = execute_query(f"SELECT * FROM medicines WHERE id IN ({placeholders})", tuple(ids))
    by_id = {row["id"]: dict(row) for row in rows}
    return [by_id[medicine_id] for medicine_id in ids if medicine_id in by_id]


# ============================================================
# On-demand composition enrichment
# ============================================================

def _strength_tokens(value):
    return sorted(
        token.replace(" ", "").casefold()
        for token in re.findall(r"\d+(?:\.\d+)?\s*(?:mcg|μg|µg|mg|g|ml|iu|units?|%)", str(value or ""), re.I)
    )


def _unique_records(records):
    unique = {}
    for record in records:
        key = record.get("api_record_id") or normalize_medication_text(record.get("medicine_name"))
        unique.setdefault(key, record)
    return list(unique.values())


def _pick_record(records, medicine):
    brand_key = normalize_medication_text(medicine.get("brand_name"))
    if not brand_key:
        return None
    form = normalize_medication_text(medicine.get("dosage_form"))
    strength = medicine.get("strength")
    wanted = _strength_tokens(strength) if strength else None

    def prepare(candidates):
        if form:
            candidates = [record for record in candidates
                          if normalize_medication_text(record.get("dosage_form")) == form]
        if wanted is not None:
            candidates = [record for record in candidates
                          if _strength_tokens(record.get("strength")) == wanted]
        return _unique_records(candidates)

    exact = prepare([
        record for record in records
        if normalize_medication_text(record.get("brand_name")) == brand_key
    ])
    if len(exact) == 1:
        return exact[0]
    if exact:
        return None

    # The dataset brand sometimes drops a strength suffix ("Metoz" vs
    # "Metoz 2.5"). Accept a single prefix-compatible product only.
    lenient = prepare([
        record for record in records
        if normalize_medication_text(record.get("brand_name")).startswith(brand_key)
        and len(normalize_medication_text(record.get("brand_name"))) > len(brand_key)
    ])
    return lenient[0] if len(lenient) == 1 else None


def enrich_medicine_composition(medicine):
    """Fill an incomplete composition from the catalog; never overwrites complete data."""
    ensure_catalog_schema()
    medicine_id = medicine.get("id")
    brand = _clean(medicine.get("brand_name"))
    if not medicine_id or medicine.get("composition_complete") or not brand:
        return False

    attempt_key = ENRICHMENT_ATTEMPT_KEY.format(medicine_id=medicine_id)
    try:
        if has_recent_api_search(attempt_key):
            return False
    except Exception:
        pass

    try:
        records = _fetch_records(brand, 20)
    except (requests.RequestException, ValueError) as exc:
        print(f"Composition lookup failed for {brand} ({type(exc).__name__})")
        return False

    record = _pick_record(records, medicine)
    if not record or not parse_composition(record.get("composition")):
        try:
            record_api_search(attempt_key, 0)
        except Exception:
            pass
        return False

    product_key = make_product_key(
        medicine.get("medicine_name"), medicine.get("brand_name"),
        medicine.get("strength") or record.get("strength"),
        medicine.get("dosage_form") or record.get("dosage_form"),
        record.get("composition"), medicine.get("release_type"), medicine.get("pack_size"),
    )
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """UPDATE medicines SET
                 composition = ?, composition_complete = 1, product_key = ?,
                 release_type = coalesce(release_type, ?),
                 api_source = coalesce(api_source, ?), api_record_id = coalesce(api_record_id, ?),
                 api_fetched_at = ?
               WHERE id = ?""",
            (
                record.get("composition"), product_key, record.get("release_type"),
                record.get("api_source"), record.get("api_record_id"),
                record.get("api_fetched_at"), medicine_id,
            ),
        )
        cursor.execute("DELETE FROM medicine_ingredients WHERE medicine_id = ?", (medicine_id,))
        cursor.executemany(
            "INSERT INTO medicine_ingredients("
            "medicine_id, ingredient_name, ingredient_name_norm, ingredient_strength, ingredient_strength_norm"
            ") VALUES (?,?,?,?,?)",
            _ingredient_rows(medicine_id, record.get("composition")),
        )
        conn.commit()
    except Exception as exc:
        conn.rollback()
        print(f"Could not enrich composition for medicine {medicine_id}: {exc}")
        return False
    finally:
        conn.close()

    try:
        record_api_search(attempt_key, 1)
    except Exception:
        pass
    return True


if __name__ == "__main__":
    for result in search_catalog("montek lc", limit=3):
        print(result["medicine_name"], "|", result["strength"], "|", result["composition"])
