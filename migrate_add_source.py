"""
============================================================
MED FINDER
DATABASE MIGRATION - ADD SOURCE COLUMN
Version : 1.0

Purpose:
Add 'source' column to existing medicines table for tracking
data origin (local vs API).

Author : Naman (with AI assistance)
============================================================
"""

import sqlite3
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent

if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from backend.config import MEDICINE_DATABASE


def migrate_database():
    """
    Add source column to medicines table if it doesn't exist.
    """

    print("=" * 70)
    print("DATABASE MIGRATION - ADD SOURCE COLUMN")
    print("=" * 70)

    conn = sqlite3.connect(MEDICINE_DATABASE)
    cursor = conn.cursor()

    try:
        # Check if source column already exists
        cursor.execute("PRAGMA table_info(medicines)")
        columns = [col[1] for col in cursor.fetchall()]

        if "source" in columns:
            print("\n[OK] Source column already exists. No migration needed.")
            return

        print("\n[*] Adding 'source' column to medicines table...")

        # Add source column with default value 'local'
        cursor.execute("""
            ALTER TABLE medicines
            ADD COLUMN source TEXT DEFAULT 'local'
        """)

        # Update all existing records to have source='local'
        cursor.execute("""
            UPDATE medicines
            SET source = 'local'
            WHERE source IS NULL
        """)

        conn.commit()

        # Verify
        cursor.execute("SELECT COUNT(*) FROM medicines WHERE source='local'")
        count = cursor.fetchone()[0]

        print(f"[OK] Source column added successfully")
        print(f"[OK] Updated {count:,} existing records with source='local'")

        print("\n" + "=" * 70)
        print("MIGRATION COMPLETED SUCCESSFULLY")
        print("=" * 70)

    except Exception as e:
        conn.rollback()
        print(f"\n[ERROR] Migration failed: {e}")
        raise

    finally:
        conn.close()


if __name__ == "__main__":
    migrate_database()
