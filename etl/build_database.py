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

from backend.config import MEDICINE_DATABASE


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

            source TEXT DEFAULT 'local'

        );

    """)

    conn.commit()

    print("✓ Table Created")

    # --------------------------------------------------------
    # Prepare Data
    # --------------------------------------------------------

    records = []

    for _, row in df.iterrows():

        records.append(

            (

                row.get("Medicine_Name"),

                row.get("Brand_Name"),

                row.get("Strength"),

                row.get("Dosage_Form"),

                row.get("Medicine_Category"),

                row.get("Composition"),

                None if str(row.get("Price")) == "nan" else row.get("Price"),

                row.get("Is_Prescription_Required"),

                row.get("Image_URL"),

                'local'

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

            source

        )

        VALUES (?,?,?,?,?,?,?,?,?,?)

        """,

        records

    )

    conn.commit()

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