"""
============================================================
MED FINDER
PRICE SCRAPER SERVICE - 1mg Integration
Version : 1.0

Purpose:
Scrape medicine prices from 1mg.com for API-sourced medicines.

Author : Naman (with AI assistance)
============================================================
"""

import requests
from bs4 import BeautifulSoup
import re
import time
import sys
from pathlib import Path

# ------------------------------------------------------------
# Add Project Root
# ------------------------------------------------------------

ROOT_DIR = Path(__file__).resolve().parent.parent.parent

if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from backend.config import PRICE_SCRAPER_TIMEOUT, PRICE_SCRAPER_USER_AGENT


# ============================================================
# Scrape Price from 1mg
# ============================================================

def scrape_1mg_price(medicine_name, max_retries=2):
    """
    Scrape price from 1mg.com for a given medicine.

    Parameters
    ----------
    medicine_name : str
        Medicine name to search
    max_retries : int
        Maximum retry attempts

    Returns
    -------
    dict or None
        Price information or None if scraping fails
    """

    if not medicine_name or len(medicine_name.strip()) < 3:
        return None

    # Clean medicine name for URL
    search_query = medicine_name.strip().replace(' ', '+')

    url = f"https://www.1mg.com/search/all?name={search_query}"

    headers = {
        'User-Agent': PRICE_SCRAPER_USER_AGENT,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate',
        'Connection': 'keep-alive',
    }

    for attempt in range(max_retries):
        try:
            response = requests.get(
                url,
                headers=headers,
                timeout=PRICE_SCRAPER_TIMEOUT
            )

            if response.status_code != 200:
                print(f"1mg returned status {response.status_code}")
                if attempt < max_retries - 1:
                    time.sleep(1)
                    continue
                return None

            soup = BeautifulSoup(response.content, 'html.parser')

            # Find first medicine result
            medicine_card = soup.find('div', class_=re.compile('style__product-card'))

            if not medicine_card:
                # Try alternative selectors
                medicine_card = soup.find('div', class_=re.compile('Medicine_medicine'))

            if not medicine_card:
                print(f"No medicine card found for: {medicine_name}")
                return None

            # Extract price
            price_element = medicine_card.find('div', class_=re.compile('style__price-tag'))

            if not price_element:
                price_element = medicine_card.find('span', class_=re.compile('price'))

            if price_element:
                price_text = price_element.get_text(strip=True)
                # Extract numeric price
                price_match = re.search(r'[\d,]+\.?\d*', price_text.replace(',', ''))
                if price_match:
                    price = float(price_match.group())

                    # Extract MRP if available
                    mrp_element = medicine_card.find('span', class_=re.compile('mrp'))
                    mrp = None
                    if mrp_element:
                        mrp_text = mrp_element.get_text(strip=True)
                        mrp_match = re.search(r'[\d,]+\.?\d*', mrp_text.replace(',', ''))
                        if mrp_match:
                            mrp = float(mrp_match.group())

                    return {
                        'price': price,
                        'mrp': mrp if mrp else price * 1.15,  # Estimate MRP if not found
                        'source_url': url,
                        'scraped_at': time.strftime('%Y-%m-%d %H:%M:%S')
                    }

            print(f"Price element not found for: {medicine_name}")
            return None

        except requests.Timeout:
            print(f"1mg scraper timeout (attempt {attempt + 1}/{max_retries})")
            if attempt < max_retries - 1:
                time.sleep(1)
            continue

        except Exception as e:
            print(f"Error scraping 1mg: {e}")
            if attempt < max_retries - 1:
                time.sleep(1)
            continue

    return None


# ============================================================
# Fallback: Try Alternative Selectors
# ============================================================

def scrape_1mg_price_alternative(medicine_name):
    """
    Alternative scraping method using different selectors.

    This is a backup if the primary method fails.
    """

    try:
        search_query = medicine_name.strip().replace(' ', '+')
        url = f"https://www.1mg.com/drugs/{search_query.lower().replace('+', '-')}"

        headers = {
            'User-Agent': PRICE_SCRAPER_USER_AGENT
        }

        response = requests.get(url, headers=headers, timeout=PRICE_SCRAPER_TIMEOUT)

        if response.status_code == 200:
            soup = BeautifulSoup(response.content, 'html.parser')

            # Look for price in product detail page
            price_container = soup.find('div', class_=re.compile('DrugPriceBox'))

            if price_container:
                price_text = price_container.get_text(strip=True)
                price_match = re.search(r'[\d,]+\.?\d*', price_text.replace(',', ''))
                if price_match:
                    price = float(price_match.group())
                    return {
                        'price': price,
                        'mrp': price * 1.15,
                        'source_url': url,
                        'scraped_at': time.strftime('%Y-%m-%d %H:%M:%S')
                    }

    except Exception as e:
        print(f"Alternative scraping method failed: {e}")

    return None


# ============================================================
# Get Price with Fallback
# ============================================================

def get_medicine_price(medicine_name):
    """
    Get medicine price with fallback strategies.

    Parameters
    ----------
    medicine_name : str
        Medicine name to search

    Returns
    -------
    dict or None
        Price information or None if all methods fail
    """

    # Try primary scraping method
    price_data = scrape_1mg_price(medicine_name)

    if price_data:
        return price_data

    # Try alternative method
    price_data = scrape_1mg_price_alternative(medicine_name)

    return price_data


# ============================================================
# Standalone Testing
# ============================================================

if __name__ == "__main__":

    print("=" * 60)
    print("1MG PRICE SCRAPER TEST")
    print("=" * 60)

    test_medicines = [
        "Paracetamol 500mg Tablet",
        "Dolo 650 Tablet",
        "Aspirin 75mg"
    ]

    for medicine in test_medicines:
        print(f"\nSearching for: {medicine}")
        price_info = get_medicine_price(medicine)

        if price_info:
            print(f"  Price: Rs. {price_info['price']}")
            print(f"  MRP: Rs. {price_info['mrp']}")
            print(f"  Source: {price_info['source_url']}")
        else:
            print("  [FAILED] Price not found")

        time.sleep(2)  # Be polite to the server
