"""Safely add structured matching and price metadata to an existing database."""

import csv
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from backend.config import MEDICINE_DATABASE, ORIGINAL_DATASET
from backend.medicine_data import (
    canonical_composition, extract_pack_size, extract_release_type,
    make_product_key, parse_composition,
)


def _price_key(value):
    try:
        number = re.sub(r"[^0-9.]", "", str(value or ""))
        return round(float(number), 2) if number else None
    except ValueError:
        return None


def _source_pack_lookup():
    """Use source metadata to recover package quantity when the match is unique."""
    packs = defaultdict(set)
    if not ORIGINAL_DATASET.exists():
        return {}
    with ORIGINAL_DATASET.open("r", encoding="cp1252", newline="") as source:
        for row in csv.DictReader(source, delimiter="\t"):
            key = (
                (row.get("Medicine Name") or "").strip().casefold(),
                (row.get("Composition") or "").strip().casefold(),
                _price_key(row.get("Price")),
            )
            pack = extract_pack_size(
                row.get("Type of Medicine"), row.get("Product Name"), row.get("Medicine Name")
            )
            if key[2] is not None and pack:
                packs[key].add(pack)
    return {key: next(iter(values)) for key, values in packs.items() if len(values) == 1}


def migrate_database(database_path=MEDICINE_DATABASE):
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(medicines)")}
        if not columns:
            raise RuntimeError(f"No medicines table found in {database_path}")

        new_columns = {
            "release_type": "TEXT",
            "pack_size": "TEXT",
            "composition_complete": "INTEGER NOT NULL DEFAULT 0",
            "price_source": "TEXT",
            "price_checked_at": "TEXT",
            "rxnorm_rxcui": "TEXT",
            "product_key": "TEXT",
        }
        for name, definition in new_columns.items():
            if name not in columns:
                connection.execute(f"ALTER TABLE medicines ADD COLUMN {name} {definition}")
        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_medicine_match
            ON medicines(composition_complete, strength, dosage_form, release_type, pack_size, price)
        """)

        connection.execute("""CREATE TABLE IF NOT EXISTS medicine_ingredients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            medicine_id INTEGER NOT NULL REFERENCES medicines(id) ON DELETE CASCADE,
            ingredient_name TEXT NOT NULL,
            ingredient_name_norm TEXT NOT NULL,
            ingredient_strength TEXT NOT NULL,
            ingredient_strength_norm TEXT NOT NULL
        )""")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_medicine_ingredients_med ON medicine_ingredients(medicine_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_medicine_ingredients_name ON medicine_ingredients(ingredient_name_norm)")
        connection.execute("""CREATE TABLE IF NOT EXISTS api_search_cache (
            query_key TEXT PRIMARY KEY,
            searched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            result_count INTEGER NOT NULL DEFAULT 0
        )""")
        cache_columns = {row["name"] for row in connection.execute("PRAGMA table_info(api_search_cache)")}
        if "result_count" not in cache_columns:
            connection.execute("ALTER TABLE api_search_cache ADD COLUMN result_count INTEGER NOT NULL DEFAULT 0")

        connection.execute("""CREATE TABLE IF NOT EXISTS medicine_equivalence_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reference_product_key TEXT NOT NULL,
            candidate_product_key TEXT NOT NULL,
            jurisdiction TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pending', 'confirmed', 'rejected')),
            evidence_type TEXT,
            reference_product TEXT,
            source_url TEXT NOT NULL,
            verified_at TEXT,
            notes TEXT,
            UNIQUE(reference_product_key, candidate_product_key, jurisdiction)
        )""")
        connection.execute("""CREATE INDEX IF NOT EXISTS idx_equivalence_lookup
            ON medicine_equivalence_evidence(reference_product_key, candidate_product_key, jurisdiction, status)""")

        packs = _source_pack_lookup()
        records = connection.execute(
            "SELECT id, medicine_name, brand_name, strength, dosage_form, composition, pack_size, price, source FROM medicines"
        ).fetchall()
        connection.execute("DELETE FROM medicine_ingredients")
        ingredient_rows = []
        for row in records:
            ingredients = parse_composition(row["composition"])
            pack = packs.get((
                (row["medicine_name"] or "").strip().casefold(),
                (row["composition"] or "").strip().casefold(),
                _price_key(row["price"]),
            ))
            connection.execute(
                "UPDATE medicines SET release_type = ?, pack_size = COALESCE(?, pack_size), "
                "composition = ?, composition_complete = ?, product_key = ?, "
                "price_source = COALESCE(price_source, CASE WHEN price IS NULL THEN NULL "
                "WHEN source = 'local' THEN 'Source dataset' ELSE 'Imported API result' END) "
                "WHERE id = ?",
                (
                    extract_release_type(row["medicine_name"]), pack,
                    canonical_composition(row["composition"]), int(bool(ingredients)),
                    make_product_key(
                        row["medicine_name"], row["brand_name"], row["strength"], row["dosage_form"],
                        canonical_composition(row["composition"]),
                        extract_release_type(row["medicine_name"]), pack or row["pack_size"],
                    ),
                    row["id"],
                ),
            )
            ingredient_rows.extend((
                row["id"], item["ingredient_name"], item["ingredient_name_norm"],
                item["ingredient_strength"], item["ingredient_strength_norm"],
            ) for item in ingredients)

        connection.executemany("""INSERT INTO medicine_ingredients(
            medicine_id, ingredient_name, ingredient_name_norm,
            ingredient_strength, ingredient_strength_norm
        ) VALUES (?,?,?,?,?)""", ingredient_rows)
        connection.commit()
        complete = connection.execute(
            "SELECT COUNT(*) FROM medicines WHERE composition_complete = 1"
        ).fetchone()[0]
        packaged = connection.execute(
            "SELECT COUNT(*) FROM medicines WHERE pack_size IS NOT NULL"
        ).fetchone()[0]
        print(f"Migration complete: {len(records):,} medicines; {complete:,} with complete ingredients; {packaged:,} with package size.")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    migrate_database()
