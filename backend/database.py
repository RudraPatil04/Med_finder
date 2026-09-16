"""
============================================================
MED FINDER
DATABASE CONNECTION
Version : 1.0

Author : Naman
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
# Database Connection
# ============================================================

def get_connection():
    """
    Returns a SQLite database connection.
    """

    connection = sqlite3.connect(MEDICINE_DATABASE)

    # Return rows as dictionaries
    connection.row_factory = sqlite3.Row

    return connection


# ============================================================
# Execute SELECT Query
# ============================================================

def execute_query(query, params=()):
    """
    Execute SELECT query.
    """

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute(query, params)

    rows = cursor.fetchall()

    conn.close()

    return rows


# ============================================================
# Execute INSERT / UPDATE / DELETE
# ============================================================

def execute_non_query(query, params=()):
    """
    Execute INSERT / UPDATE / DELETE.
    """

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute(query, params)

    conn.commit()

    affected = cursor.rowcount

    conn.close()

    return affected


# ============================================================
# Database Test
# ============================================================

if __name__ == "__main__":

    print("=" * 60)
    print("MED FINDER DATABASE TEST")
    print("=" * 60)

    medicines = execute_query(

        """
        SELECT
            medicine_name,
            brand_name,
            price
        FROM medicines
        LIMIT 10
        """

    )

    print(f"Records Retrieved : {len(medicines)}\n")

    for medicine in medicines:

        print(

            medicine["medicine_name"],
            "|",
            medicine["brand_name"],
            "| ₹",
            medicine["price"]

        )

    print("\nDatabase Connected Successfully.")