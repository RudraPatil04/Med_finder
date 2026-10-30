"""
============================================================
MED FINDER
ETL MODULE : BUILD DATABASE
Version : 1.0
Author : Naman

Purpose:
Create SQLite database from processed medicine dataset.
============================================================
"""

import sqlite3
import sys
from pathlib import Path

# ------------------------------------------------------------
# Add Project Root
# ------------------------------------------------------------

ROOT_DIR = Path(__file__).resolve().parent.parent

if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from backend.config import MEDICINE_DATABASE
from backend.medicine_data import (
    canonical_composition, extract_pack_size, extract_release_type,
    make_product_key, normalize_medication_text, parse_composition,
)


# ============================================================
# BUILD DATABASE
# ============================================================

def build_database(df):

    print("\n" + "=" * 70)
    print("MED FINDER - BUILD SQLITE DATABASE")
    print("=" * 70)

    # --------------------------------------------------------
    # Connect
    # --------------------------------------------------------

    conn = sqlite3.connect(MEDICINE_DATABASE)

    cursor = conn.cursor()

    cursor.execute("DROP TABLE IF EXISTS medicine_ingredients")
    cursor.execute("DROP TABLE IF EXISTS api_search_cache")

    # --------------------------------------------------------
    # Drop Existing Table
    # --------------------------------------------------------

    cursor.execute("""
        DROP TABLE IF EXISTS medicines;
    """)

    conn.commit()

    # --------------------------------------------------------
    # Create Table
    # --------------------------------------------------------

    cursor.execute("""

        CREATE TABLE medicines(

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            medicine_name TEXT NOT NULL,

            brand_name TEXT,

            strength TEXT,

            dosage_form TEXT,

            medicine_category TEXT,

            composition TEXT,

            price REAL,

            prescription_required TEXT,

            image_url TEXT,

            release_type TEXT,

            pack_size TEXT,

            composition_complete INTEGER NOT NULL DEFAULT 0,

            price_source TEXT,

            price_checked_at TEXT,

            rxnorm_rxcui TEXT,

            product_key TEXT,

            source TEXT DEFAULT 'local',

            api_source TEXT,

            api_record_id TEXT,

            api_fetched_at TEXT,

            search_norm TEXT

        );

    """)

    conn.commit()

    print("✓ Table Created")

    # --------------------------------------------------------
    # Prepare Data
    # --------------------------------------------------------

    records = []

    for _, row in df.iterrows():

        raw_composition = row.get("Composition")
        ingredients = parse_composition(raw_composition)
        composition = canonical_composition(raw_composition)
        raw_price = row.get("Price")
        price = None if str(raw_price).strip().lower() in {"", "nan", "<na>", "none"} else raw_price
        name = row.get("Medicine_Name")
        type_of_medicine = row.get("Type_of_Medicine")
        product_name = row.get("Product_Name")
        release_type = extract_release_type(name)
        pack_size = extract_pack_size(type_of_medicine, product_name, name)
        product_key = make_product_key(
            name, row.get("Brand_Name"), row.get("Strength"), row.get("Dosage_Form"),
            composition, release_type, pack_size,
        )
        records.append(

            (

                row.get("Medicine_Name"),

                row.get("Brand_Name"),

                row.get("Strength"),

                row.get("Dosage_Form"),

                row.get("Medicine_Category"),

                composition,

                price,

                row.get("Is_Prescription_Required"),

                row.get("Image_URL"),

                release_type,

                pack_size,

                int(bool(ingredients)),

                "Source dataset" if price is not None else None,

                None,

                None,

                product_key,

                'local',

                None,

                None,

                None,

                normalize_medication_text(
                    f"{row.get('Medicine_Name') or ''} {row.get('Brand_Name') or ''}"
                )

            )

        )

    # --------------------------------------------------------
    # Insert Records
    # --------------------------------------------------------

    cursor.executemany(

        """

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

            release_type,

            pack_size,

            composition_complete,

            price_source,

            price_checked_at,

            rxnorm_rxcui,

            product_key,

            source,

            api_source,

            api_record_id,

            api_fetched_at,

            search_norm

        )

        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)

        """,

        records

    )

    conn.commit()

    # Store ingredient names and strengths as rows so matching never relies on
    # partial composition text or dose-only fragments.
    ingredient_rows = []
    for medicine_id, composition in cursor.execute("SELECT id, composition FROM medicines"):
        for ingredient in parse_composition(composition):
            ingredient_rows.append((
                medicine_id,
                ingredient["ingredient_name"],
                ingredient["ingredient_name_norm"],
                ingredient["ingredient_strength"],
                ingredient["ingredient_strength_norm"],
            ))
    cursor.execute("""
        CREATE TABLE medicine_ingredients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            medicine_id INTEGER NOT NULL REFERENCES medicines(id) ON DELETE CASCADE,
            ingredient_name TEXT NOT NULL,
            ingredient_name_norm TEXT NOT NULL,
            ingredient_strength TEXT NOT NULL,
            ingredient_strength_norm TEXT NOT NULL
        )
    """)
    cursor.executemany("""
        INSERT INTO medicine_ingredients(
            medicine_id, ingredient_name, ingredient_name_norm,
            ingredient_strength, ingredient_strength_norm
        ) VALUES (?,?,?,?,?)
    """, ingredient_rows)
    cursor.execute("CREATE INDEX idx_medicine_ingredients_med ON medicine_ingredients(medicine_id)")
    cursor.execute("CREATE INDEX idx_medicine_ingredients_name ON medicine_ingredients(ingredient_name_norm)")
    cursor.execute("""CREATE TABLE IF NOT EXISTS medicine_equivalence_evidence (
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
    cursor.execute("""CREATE INDEX IF NOT EXISTS idx_equivalence_lookup
        ON medicine_equivalence_evidence(reference_product_key, candidate_product_key, jurisdiction, status)""")
    conn.commit()
    print(f"✓ Structured ingredients stored: {len(ingredient_rows):,}")

    print(f"✓ Records Inserted : {len(records):,}")

    # --------------------------------------------------------
    # Indexes
    # --------------------------------------------------------

    indexes = [

        (
            "idx_medicine_name",
            "medicine_name"
        ),

        (
            "idx_brand_name",
            "brand_name"
        ),

        (
            "idx_strength",
            "strength"
        ),

        (
            "idx_price",
            "price"
        ),

        (
            "idx_category",
            "medicine_category"
        )

    ]

    for index_name, column_name in indexes:

        cursor.execute(

            f"""

            CREATE INDEX IF NOT EXISTS

            {index_name}

            ON medicines({column_name});

            """

        )

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_medicine_match
        ON medicines(composition_complete, strength, dosage_form, release_type, pack_size, price)
    """)

    conn.commit()

    print("✓ Indexes Created")

    # --------------------------------------------------------
    # Statistics
    # --------------------------------------------------------

    total = cursor.execute(

        "SELECT COUNT(*) FROM medicines"

    ).fetchone()[0]

    print()

    print("=" * 70)

    print("DATABASE SUMMARY")

    print("=" * 70)

    print(f"Database File : {MEDICINE_DATABASE}")

    print(f"Total Records : {total:,}")

    print()

    print("Sample Records")

    print("-" * 70)

    sample = cursor.execute(

        """

        SELECT

            medicine_name,

            brand_name,

            strength,

            dosage_form,

            price

        FROM medicines

        LIMIT 10

        """

    ).fetchall()

    for row in sample:

        print(row)

    conn.close()

    print()

    print("=" * 70)

    print("SQLITE DATABASE CREATED SUCCESSFULLY")

    print("=" * 70)


# ============================================================
# Standalone Test
# ============================================================

if __name__ == "__main__":

    from etl.load_dataset import load_dataset
    from etl.clean_dataset import clean_dataset
    from etl.extract_fields import extract_fields

    df = load_dataset()

    if df is None:
        raise Exception("Dataset could not be loaded.")

    df = clean_dataset(df)

    df = extract_fields(df)

    build_database(df)
