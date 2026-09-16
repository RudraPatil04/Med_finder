"""
============================================================
MED FINDER
TEST FALLBACK API SEARCH
Version : 1.0

Purpose:
Test the API fallback search functionality.

Author : Naman (with AI assistance)
============================================================
"""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent

if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from backend.services.medicine_service import search_medicines


def test_api_fallback():
    """
    Test search with API fallback enabled.
    """

    print("=" * 70)
    print("TESTING API FALLBACK SEARCH")
    print("=" * 70)

    # Test 1: Search for a common medicine (should find in local DB)
    print("\n[TEST 1] Searching for 'Paracetamol' (should find locally)...")
    results = search_medicines("Paracetamol", limit=5)
    print(f"Found {len(results)} results")
    if results:
        for r in results[:3]:
            print(f"  - {r['medicine_name']} (source: {r['source']})")

    # Test 2: Search for an uncommon medicine (should trigger API)
    print("\n[TEST 2] Searching for 'Atorvastatin' (may trigger API if not found)...")
    results = search_medicines("Atorvastatin", limit=5)
    print(f"Found {len(results)} results")
    if results:
        for r in results[:3]:
            print(f"  - {r['medicine_name']} (source: {r['source']})")

    # Test 3: Search with API fallback disabled
    print("\n[TEST 3] Searching for 'Lisinopril' (API disabled)...")
    results = search_medicines("Lisinopril", limit=5, enable_api_fallback=False)
    print(f"Found {len(results)} results (local only)")

    # Test 4: Search with API fallback enabled
    print("\n[TEST 4] Searching for 'Lisinopril' (API enabled)...")
    results = search_medicines("Lisinopril", limit=5, enable_api_fallback=True)
    print(f"Found {len(results)} results")
    if results:
        for r in results[:3]:
            print(f"  - {r['medicine_name']} (source: {r['source']})")

    print("\n" + "=" * 70)
    print("TESTING COMPLETED")
    print("=" * 70)


if __name__ == "__main__":
    test_api_fallback()
