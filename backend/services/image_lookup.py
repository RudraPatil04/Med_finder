"""Find a real product photo for a medicine whose catalog row has none.

About 279k catalog rows carry only 1mg's shared "no image" placeholder. For
those, the PharmEasy listing of the *same product* usually has a photo. A
photo is accepted only when the listing passes the same checks used before a
listing price is attached (find_reference_listing): brand, product name with no
extra sub-brand words, ingredient signature with strengths, dosage form and
release type. Anything else keeps the dosage-form drawing in the UI.

Every attempt is cached (``image:<id>`` in api_search_cache) so a product is
looked up at most once a week, and the pharmacy site gets a cooldown after it
starts refusing requests.
"""

import re
import time
from concurrent.futures import ThreadPoolExecutor, wait

from backend.database import execute_non_query
from backend.services.api_cache import (
    ensure_api_search_cache, has_recent_api_search, record_api_search,
)
from backend.medicine_data import extract_release_type, normalize_medication_text
from backend.services.price_comparison_service import (
    _has_form, _item_signature, _matches_brand, _name_matches_product,
    _search_payload, _signature_from_composition,
)

SHARED_PLACEHOLDER_IMAGE = "hx2gxivwmeoxxxsc1hix"
ATTEMPT_KEY = "image:{medicine_id}"
HIT_TTL_DAYS = 365
MISS_TTL_DAYS = 7
DEFAULT_BUDGET_SECONDS = 5.0
MAX_IDS_PER_REQUEST = 24
COOLDOWN_SECONDS = 15 * 60
_PLACEHOLDER_WORDS = re.compile(r"placeholder|no[-_]?image|default[-_]?image|coming[-_]?soon", re.I)
_ALLOWED_HOSTS = ("https://cdn01.pharmeasy.in/", "https://onemg.gumlet.io/", "https://medicinedata.in/")

_cooldown_until = 0.0
_ERROR = object()


def has_real_image(url):
    """True for an https product photo that is not a shared placeholder."""
    url = str(url or "")
    return (
        url.startswith("https://")
        and SHARED_PLACEHOLDER_IMAGE not in url
        and not _PLACEHOLDER_WORDS.search(url)
    )


def _clean_listing_image(url):
    url = str(url or "").strip()
    if not url.startswith(_ALLOWED_HOSTS[0]) or not has_real_image(url):
        return None
    # Search results carry an 80px thumbnail; ask the CDN for a card-sized one.
    return re.sub(r"([?&])dim=\d+x\d+", r"\1dim=300x0", url)


def in_cooldown():
    return time.monotonic() < _cooldown_until


def start_cooldown():
    global _cooldown_until
    _cooldown_until = time.monotonic() + COOLDOWN_SECONDS


def _recently_attempted(medicine_id):
    try:
        return has_recent_api_search(ATTEMPT_KEY.format(medicine_id=medicine_id), HIT_TTL_DAYS, MISS_TTL_DAYS)
    except Exception:
        return False


def record_attempt(medicine_id, found):
    try:
        record_api_search(ATTEMPT_KEY.format(medicine_id=medicine_id), 1 if found else 0)
    except Exception:
        pass


def needs_image(medicine, check_cache=True):
    return bool(
        medicine
        and medicine.get("id")
        and not has_real_image(medicine.get("image_url"))
        and medicine.get("composition")
        and medicine.get("dosage_form")
        and len(str(medicine.get("brand_name") or medicine.get("medicine_name") or "").strip()) >= 3
        and (not check_cache or not _recently_attempted(medicine["id"]))
    )


def _same_brand(item, medicine):
    """The listing's umbrella brand ("CROCIN") or its title ("Crocin Quik 500Mg …") names this brand."""
    if _matches_brand(item, medicine):
        return True
    brand = normalize_medication_text(medicine.get("brand_name") or medicine.get("medicine_name"))
    title = normalize_medication_text(item.get("name"))
    return len(brand) >= 3 and title.startswith(brand)


def _pick_same_product(items, medicine, signature, expected_release):
    # _name_matches_product rejects titles with extra words ("Crocin Advance" for "Crocin").
    return next((
        item for item in items
        if _same_brand(item, medicine)
        and _name_matches_product(item, medicine)
        and _item_signature(item) == signature
        and _has_form(item, medicine.get("dosage_form"))
        and (extract_release_type(item.get("name")) or "").casefold() == expected_release
    ), None)


def image_from_items(items, medicine):
    """The photo of the listing that is this exact product, or None."""
    signature = _signature_from_composition(medicine.get("composition"))
    if not signature or not medicine.get("dosage_form"):
        return None
    expected_release = str(medicine.get("release_type") or "").casefold()
    item = _pick_same_product(items, medicine, signature, expected_release)
    if not item:
        return None
    url = _clean_listing_image(item.get("image"))
    if not url:
        return None
    # A picture reused by another listing in the same results is a stock image.
    base = url.split("?", 1)[0]
    reused = sum(
        1 for other in items
        if other is not item and str(other.get("image") or "").split("?", 1)[0] == base
    )
    return None if reused else url


def find_listing_image(medicine):
    medicine = dict(medicine)
    query = str(medicine.get("brand_name") or medicine.get("medicine_name") or "").strip()
    if len(query) < 3 or not _signature_from_composition(medicine.get("composition")):
        return None
    return image_from_items(_search_payload(query), medicine)


def store_image(medicine_id, url):
    execute_non_query("UPDATE medicines SET image_url = ? WHERE id = ?", (url, medicine_id))


def _lookup(medicine):
    try:
        return find_listing_image(medicine)
    except Exception:
        return _ERROR


def fill_images(medicines, budget_seconds=DEFAULT_BUDGET_SECONDS, workers=4):
    """Look up photos for the given rows in parallel; store matches. Returns {id: url}."""
    if in_cooldown():
        return {}
    targets = [dict(item) for item in medicines if needs_image(dict(item))][:MAX_IDS_PER_REQUEST]
    if not targets:
        return {}
    executor = ThreadPoolExecutor(max_workers=min(workers, len(targets)))
    try:
        futures = {executor.submit(_lookup, item): item for item in targets}
        done, not_done = wait(futures, timeout=budget_seconds)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    try:
        ensure_api_search_cache()
    except Exception:
        pass
    found = {}
    errors = len(not_done)
    for future in done:
        item = futures[future]
        url = future.result()
        if url is _ERROR:
            errors += 1
            continue
        if url:
            store_image(item["id"], url)
            found[item["id"]] = url
        record_attempt(item["id"], bool(url))
    if errors and errors * 2 >= len(targets):
        start_cooldown()
    return found
