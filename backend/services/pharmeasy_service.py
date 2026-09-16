"""
============================================================
MED FINDER
PHARMEASY PRICE SCRAPER SERVICE
Version : 1.0

Purpose:
Get medicine prices from PharmEasy's search page.
Extracts structured JSON from Next.js __NEXT_DATA__ script.

Author : Naman (with AI assistance)
============================================================
"""

import requests
import json
import sys
from pathlib import Path
from bs4 import BeautifulSoup

ROOT_DIR = Path(__file__).resolve().parent.parent.parent

if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from backend.config import PRICE_SCRAPER_TIMEOUT, PRICE_SCRAPER_USER_AGENT


def get_pharmeasy_price(medicine_name):
    """
    Get medicine price from PharmEasy search page.

    Parameters
    ----------
    medicine_name : str
        Medicine name to search

    Returns
    -------
    dict or None
        Price information
    """

    if not medicine_name or len(medicine_name.strip()) < 3:
        return None

    # Clean query
    query = medicine_name.strip()

    # PharmEasy search page URL
    url = f"https://pharmeasy.in/search/all?name={query.replace(' ', '%20')}"

    headers = {
        "User-Agent": PRICE_SCRAPER_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }

    try:
        response = requests.get(url, headers=headers, timeout=PRICE_SCRAPER_TIMEOUT)

        if response.status_code != 200:
            return None

        # Parse Next.js __NEXT_DATA__ JSON from HTML
        from bs4 import BeautifulSoup
        import json

        soup = BeautifulSoup(response.text, 'html.parser')
        next_data_script = soup.find('script', id='__NEXT_DATA__')

        if not next_data_script:
            return None

        data = json.loads(next_data_script.string)
        page_props = data.get('props', {}).get('pageProps', {})

        # Get products from both generics and regular product lists
        generics = page_props.get('genericsProductList') or []
        products = page_props.get('productList') or []
        all_items = generics + products

        if not all_items:
            return None

        # Get first matching product
        first_product = all_items[0]

        # Extract price
        sale_price = first_product.get("salePriceDecimal")
        mrp = first_product.get("mrpDecimal")

        if sale_price:
            return {
                "price": float(sale_price),
                "mrp": float(mrp) if mrp else float(sale_price) * 1.15,
                "product_name": first_product.get("name"),
                "source": "pharmeasy"
            }

    except Exception as e:
        print(f"PharmEasy scraper error: {e}")

    return None


if __name__ == "__main__":
    print("=" * 60)
    print("PHARMEASY API PRICE TEST")
    print("=" * 60)

    test_medicines = [
        "Paracetamol",
        "Dolo 650",
        "Aspirin",
        "Relugolix"
    ]

    for med in test_medicines:
        print(f"\nSearching PharmEasy for: {med}")
        result = get_pharmeasy_price(med)
        if result:
            print(f"  [FOUND] {result['product_name']}")
            print(f"  Price: Rs. {result['price']} (MRP: Rs. {result['mrp']})")
        else:
            print("  [NOT FOUND]")
