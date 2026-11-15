"""
============================================================
MED FINDER
PRESCRIPTION OCR SERVICE
Version : 2.0

Reads printed text from an uploaded prescription image or PDF,
matches conservative medicine candidates against the local
catalog, and never invents results when OCR fails.
============================================================
"""

import io
import os
import re
import shutil
import uuid
from pathlib import Path

import requests
from PIL import Image, ImageOps, UnidentifiedImageError
from rapidfuzz import fuzz, process

from backend.database import execute_query
from backend.medicine_data import normalize_measure
from backend.services.medicine_service import search_medicines

UPLOAD_DIR = Path(__file__).resolve().parent.parent / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
PDF_SUFFIX = ".pdf"
MAX_PAGES = 3
MAX_TEXT_CHARS = 8000
MIN_READABLE_CHARS = 12
OCR_TIMEOUT_SECONDS = 40
GOOGLE_VISION_URL = "https://vision.googleapis.com/v1/images:annotate"
GOOGLE_VISION_TIMEOUT_SECONDS = 20
GOOGLE_VISION_MAX_SIDE = 2400
MAX_NAME_TOKENS = 30
MAX_LINES = 40
MAX_UNMATCHED = 8
# Fuzzy fallback: only near-identical readings of a real brand are accepted.
# 88 scored 43% wrong in calibration, 92 scored 1/17; a candidate also needs a
# printed strength or dosage form and must beat every rival by FUZZY_MIN_GAP.
FUZZY_CUTOFF = 92
FUZZY_CANDIDATES = 400
FUZZY_MATCHES = 8
FUZZY_MIN_TOKEN = 4
FUZZY_MIN_GAP = 3

TESSERACT_CANDIDATES = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
)

_STRENGTH = re.compile(r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|µg|ug|g|ml|iu|units?|%)(?![a-z])", re.I)
_NAME_TOKEN = re.compile(r"\b[A-Za-z][A-Za-z0-9-]{2,}\b")
_FORM_HINTS = (
    (re.compile(r"\b(?:tab|tabs|tablet|tablets)\b", re.I), "tablet"),
    (re.compile(r"\b(?:cap|caps|capsule|capsules)\b", re.I), "capsule"),
    (re.compile(r"\b(?:syr|syrup|susp|suspension)\b", re.I), "syrup"),
    (re.compile(r"\b(?:inj|injection|infusion)\b", re.I), "injection"),
    (re.compile(r"\b(?:drop|drops)\b", re.I), "drops"),
)
_DOSAGE_PATTERNS = (
    re.compile(r"\b\d+\s*[-–—x×]\s*\d+\s*[-–—x×]\s*\d+\b"),
    re.compile(r"\b\d+\s*[-–—x×]\s*\d+\b"),
    re.compile(r"\b(?:od|bd|bid|tds|tid|qid|qds|hs|sos|prn)\b", re.I),
    re.compile(r"\b(?:once|twice|thrice|four\s+times)\s+(?:a\s+)?(?:daily|day)\b", re.I),
    re.compile(r"\b\d+\s*(?:times?|x)\s*(?:a\s*)?(?:day|daily)\b", re.I),
    re.compile(r"\b(?:after|before)\s+(?:meals?|food|breakfast|lunch|dinner)\b", re.I),
    re.compile(r"\bfor\s+\d+\s+(?:days?|weeks?|months?)\b", re.I),
    re.compile(r"\b[x×]\s*\d+\s*(?:days?|weeks?|months?)\b", re.I),
    re.compile(r"\b\d+\s*(?:days?|weeks?|months?)\b", re.I),
    re.compile(r"\b(?:take\s+)?(?:\d{1,2}|half|one|two)\s*(?:tablet|capsule|cap|tab|tsp|ml)s?\b", re.I),
    re.compile(r"\b(?:morning|afternoon|evening|night|bedtime)\b", re.I),
)
# "12 MG Road" is an address, not a 12 mg dose; Indian streets make MG ambiguous.
_ADDRESS_UNIT = re.compile(
    r"\b\d+\s*(?:mg|mcg|g|ml)\s*(?:road|rd|street|lane|nagar|colony|sector|chowk)\b", re.I
)
_HEADER_WORDS = re.compile(
    r"\b(?:clinic|hospital|medical|doctor|dr|patient|address|phone|mobile|email|"
    r"registration|regn|regd|mci|council|signature|stamp|seal|diagnosis|advice|"
    r"investigation|investigations|follow[\s-]?up|consultation|date|complaint|vitals|"
    r"weight|height|pulse|temperature|allergy|allergies|"
    r"tower|road|street|lane|nagar|colony|sector|village|district|city|pincode|pin|"
    r"building|complex|plaza|market|chowk|apartment|plot|opposite|opp|near|"
    r"landline|telephone|fax|website|www|"
    r"mbbs|bds|mds|mch|dnb|dch|dgo|bams|bhms|rmp|frcs|mrcp|mph|phd|"
    r"pharmacy|chemist|stores)\b",
    re.I,
)
_STOPWORDS = {
    "tablet", "tablets", "tab", "tabs", "capsule", "capsules", "cap", "caps",
    "syrup", "suspension", "injection", "inj", "cream", "ointment", "gel", "drops",
    "spray", "inhaler", "solution", "lotion", "sachet", "powder",
    "take", "taking", "use", "apply", "daily", "once", "twice", "thrice",
    "after", "before", "with", "without", "and", "the", "for", "from", "each",
    "every", "times", "time", "hours", "hour", "days", "day", "weeks", "week",
    "months", "month", "meals", "meal", "food", "breakfast", "lunch", "dinner",
    "morning", "afternoon", "evening", "night", "bedtime", "needed", "required",
    "patient", "name", "age", "sex", "date", "doctor", "clinic", "hospital",
    "prescription", "diagnosis", "advice", "follow", "visit", "review",
    "signature", "address", "phone", "mobile", "email", "registration", "regd",
    "dose", "dosage", "total", "qty", "quantity", "continue", "stop", "start",
    "medicine", "medicines", "drug", "drugs", "rx", "mg", "mcg", "ml", "gm",
    "od", "bd", "bid", "tds", "tid", "qid", "qds", "hs", "sos", "prn",
    "syp", "syr", "inj", "susp", "soln", "bp", "hr", "kg", "cm",
}


def _failure(filename, message, text="", provider=""):
    return {
        "success": False,
        "filename": filename,
        "message": message,
        "text": text[:1000],
        "count": 0,
        "data": [],
        "unmatched_lines": [],
        "provider": provider,
    }


def _tesseract_command():
    configured = os.environ.get("TESSERACT_CMD")
    if configured and Path(configured).exists():
        return configured
    found = shutil.which("tesseract")
    if found:
        return found
    for candidate in TESSERACT_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def _ocr_image(image):
    import pytesseract

    command = _tesseract_command()
    if not command:
        raise RuntimeError("Tesseract OCR is not installed on the server.")
    pytesseract.pytesseract.tesseract_cmd = command

    prepared = ImageOps.exif_transpose(image).convert("L")
    if prepared.width < 1200:
        scale = min(3.0, 1600 / max(prepared.width, 1))
        prepared = prepared.resize(
            (round(prepared.width * scale), round(prepared.height * scale)),
            Image.LANCZOS,
        )
    prepared = ImageOps.autocontrast(prepared)
    return pytesseract.image_to_string(
        prepared, lang="eng", config="--oem 3 --psm 6", timeout=OCR_TIMEOUT_SECONDS,
    )


def _google_vision_key():
    return os.environ.get("GOOGLE_VISION_API_KEY", "").strip()


def _google_vision_text(image):
    """Read one page with Google Cloud Vision (DOCUMENT_TEXT_DETECTION)."""
    import base64

    key = _google_vision_key()
    if not key:
        raise RuntimeError("GOOGLE_VISION_API_KEY is not set.")

    prepared = ImageOps.exif_transpose(image).convert("RGB")
    longest = max(prepared.size)
    if longest > GOOGLE_VISION_MAX_SIDE:
        scale = GOOGLE_VISION_MAX_SIDE / longest
        prepared = prepared.resize(
            (round(prepared.width * scale), round(prepared.height * scale)),
            Image.LANCZOS,
        )
    buffer = io.BytesIO()
    prepared.save(buffer, "PNG")

    payload = {
        "requests": [{
            "image": {"content": base64.b64encode(buffer.getvalue()).decode("ascii")},
            "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
            "imageContext": {"languageHints": ["en"]},
        }]
    }
    response = requests.post(
        GOOGLE_VISION_URL,
        headers={"X-Goog-Api-Key": key},
        json=payload,
        timeout=GOOGLE_VISION_TIMEOUT_SECONDS,
    )
    if response.status_code != 200:
        detail = ""
        try:
            detail = response.json().get("error", {}).get("message", "")
        except ValueError:
            detail = response.text[:200]
        raise RuntimeError(f"Google Vision returned {response.status_code}: {detail}".strip())

    data = response.json()
    result = (data.get("responses") or [{}])[0]
    if result.get("error"):
        raise RuntimeError("Google Vision error: " + result["error"].get("message", "unknown error"))
    return (result.get("fullTextAnnotation") or {}).get("text", "")


def _image_text(image):
    """Read one page with Google Vision when configured, else local Tesseract."""
    if _google_vision_key():
        try:
            return _google_vision_text(image), "google-vision"
        except Exception as error:
            print(f"Google Vision OCR failed, falling back to Tesseract: {error}")
    return _ocr_image(image), "tesseract"


def _read_pdf(path):
    import fitz

    parts = []
    providers = set()
    with fitz.open(str(path)) as document:
        for page in list(document)[:MAX_PAGES]:
            text = page.get_text("text").strip()
            if len(text) >= MIN_READABLE_CHARS:
                parts.append(text)
                providers.add("pdf-text-layer")
                continue
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
            image = Image.open(io.BytesIO(pixmap.tobytes("png")))
            page_text, provider = _image_text(image)
            parts.append(page_text)
            providers.add(provider)
    return "\n".join(parts), "+".join(sorted(providers))


def _read_text(path, suffix):
    if suffix == PDF_SUFFIX:
        return _read_pdf(path)
    with Image.open(str(path)) as image:
        return _image_text(image)


def _clean_line(line):
    return re.sub(r"\s+", " ", line.replace("|", "l")).strip()


def _line_strength(line):
    corrected = re.sub(r"(?<=\d)[Oo](?=\d|[a-z])", "0", line)  # 4omg -> 40mg
    corrected = re.sub(r"(?<=\d)\s*wg\b", "mg", corrected, flags=re.I)  # 40wg -> 40mg
    match = _STRENGTH.search(corrected)
    return match.group(0).strip() if match else None


def _name_words(value):
    return re.findall(r"[a-z0-9]+", str(value or "").casefold())


def _strength_number(strength):
    match = re.match(r"(\d+(?:\.\d+)?)", strength or "")
    return match.group(1) if match else None


def _adjacent_strength(line, token):
    """Pick up unit-less brand strengths such as 'Dolo 650' or 'Augmentin 625 Duo'."""
    match = re.search(rf"\b{re.escape(token)}\s*[- ]?\s*(\d{{2,4}}(?:\.\d+)?)\b", line, re.I)
    return match.group(1) if match else None


def _name_numbers(value):
    return re.findall(r"\d+(?:\.\d+)?", str(value or ""))


def _strength_state(row, target_strength, strength_number):
    """True when the printed strength matches, False when it conflicts, None if unknown."""
    row_strength = normalize_measure(row["strength"]) if row["strength"] else None
    if target_strength and row_strength and row_strength == target_strength:
        return True
    name_numbers = _name_numbers(row["medicine_name"])
    if strength_number and strength_number in name_numbers:
        return True
    if target_strength and row_strength:
        return False
    if strength_number and name_numbers and row_strength:
        return False
    return None


def _form_hint(line):
    for pattern, hint in _FORM_HINTS:
        if pattern.search(line):
            return hint
    return None


def _fuzzy_lookup(token, target_strength, strength_number, form_hint):
    """Find one near-identical brand for a garbled reading, or None.

    Only brands sharing the first three letters are considered, the line must
    carry a strength or dosage form, a candidate must still match both, and the
    best candidate must beat every other brand by FUZZY_MIN_GAP. A typo therefore
    cannot turn into an unrelated or ambiguous medicine.
    """
    key = token.casefold()
    if len(key) < FUZZY_MIN_TOKEN:
        return None
    # A typo alone is not enough context; the same line must print a strength or
    # a dosage form, which is what separates a medicine line from letterhead.
    if not (target_strength or strength_number or form_hint):
        return None
    prefix = key[:3] + "%"
    rows = execute_query(
        "SELECT id, medicine_name, brand_name, strength, dosage_form, price, prescription_required "
        "FROM medicines WHERE brand_name LIKE ? OR medicine_name LIKE ? LIMIT ?",
        (prefix, prefix, FUZZY_CANDIDATES),
    )
    if not rows:
        return None

    labels = []
    owners = []
    for row in rows:
        brand = " ".join(str(row["brand_name"] or "").split())
        for label in (brand, brand.split(" ")[0] if brand else ""):
            if label:
                labels.append(label.casefold())
                owners.append(row)
    if not labels:
        return None

    best = {}
    for _label, score, index in process.extract(
        key, labels, scorer=fuzz.WRatio, score_cutoff=FUZZY_CUTOFF, limit=FUZZY_MATCHES
    ):
        row = owners[index]
        if form_hint and form_hint not in str(row["dosage_form"] or "").casefold():
            continue
        if (target_strength or strength_number) and _strength_state(
            row, target_strength, strength_number
        ) is not True:
            continue
        product = (
            " ".join(str(row["brand_name"] or "").split()).casefold(),
            str(row["strength"] or "").casefold(),
            str(row["dosage_form"] or "").casefold(),
        )
        if product not in best or score > best[product][1]:
            best[product] = (row, score)

    ranked = sorted(best.values(), key=lambda item: item[1], reverse=True)
    if not ranked:
        return None
    # Two plausible brands this close means the reading cannot tell them apart.
    if len(ranked) > 1 and ranked[1][1] >= ranked[0][1] - FUZZY_MIN_GAP:
        return None
    return ranked[0]


def _match_token(token, line_strength, cache, form_hint=None, strength_hint=None, name_hint=None):
    """Return (row, match_type, score) for a printed token, or None.

    Literal catalog hits win. When the token matches nothing, a fuzzy pass may
    return a near-identical brand, labelled "possible" so the UI can ask for
    confirmation instead of asserting it.
    """
    key = (
        f"{token.casefold()}|{line_strength or ''}|{strength_hint or ''}|"
        f"{form_hint or ''}|{name_hint or ''}"
    )
    if key in cache:
        return cache[key]

    strength_number = _strength_number(line_strength) or strength_hint
    queries = []
    if strength_number:
        queries.append(f"{token} {strength_number}")
    queries.append(token)

    rows = []
    seen_ids = set()
    for query in queries:
        try:
            found = search_medicines(query, limit=15, enable_api_fallback=False)
        except Exception:
            found = []
        for row in found:
            if row["id"] not in seen_ids:
                seen_ids.add(row["id"])
                rows.append(row)

    # A printed name must appear as a whole word: "Pramod" is not "Pramodep" and
    # "Biclav" is not "Abiclav". Ingredient-only hits have no word to anchor to,
    # so those lines are reported as unmatched instead of guessing a brand.
    boundary = re.compile(rf"\b{re.escape(token)}\b", re.I)
    rows = [
        row for row in rows
        if boundary.search(str(row["medicine_name"] or ""))
        or boundary.search(str(row["brand_name"] or ""))
    ]

    target_strength = normalize_measure(line_strength) if line_strength else None
    token_key = token.casefold()
    hint_boundary = None
    if name_hint and len(name_hint) >= 3:
        hint_boundary = re.compile(rf"\b{re.escape(name_hint)}\b", re.I)
    best = None
    best_rank = None
    for row in rows:
        name_words = _name_words(row["medicine_name"])
        brand_words = _name_words(row["brand_name"])
        exact = bool(name_words and name_words[0] == token_key) or bool(
            brand_words and brand_words[0] == token_key
        )
        strength = _strength_state(row, target_strength, strength_number)
        strength_rank = 0 if strength is True else 1 if strength is None else 2
        form_ok = not form_hint or form_hint in str(row["dosage_form"] or "").casefold()
        hint_rank = 0 if strength_number and strength_number in _name_numbers(row["medicine_name"]) else 1
        # "Ibugesic Plus" must beat the plain "Ibugesic" product, and among Plus
        # products the one adding the fewest words wins ("Plus" over "Plus NF").
        name_rank = 0 if hint_boundary and hint_boundary.search(str(row["medicine_name"] or "")) else 1
        extra_rank = 0
        if hint_boundary:
            printed = {token_key, name_hint.casefold()}
            extra_rank = len([word for word in name_words if word not in printed])
        rank = (strength_rank, 0 if exact else 1, name_rank, extra_rank, 0 if form_ok else 1, hint_rank)
        if best_rank is None or rank < best_rank:
            best, best_rank = (row, exact), rank

    if best is not None:
        row, exact = best
        result = (row, "exact" if exact else "partial", None)
    else:
        fuzzy = _fuzzy_lookup(token, target_strength, strength_number, form_hint)
        result = (fuzzy[0], "possible", round(fuzzy[1], 1)) if fuzzy else None

    cache[key] = result
    return result


def _dosage_text(line):
    found = []
    spans = []
    for pattern in _DOSAGE_PATTERNS:
        for match in pattern.finditer(line):
            start, end = match.span()
            if any(start < used_end and used_start < end for used_start, used_end in spans):
                continue
            spans.append((start, end))
            found.append(match.group(0).strip())
    return "; ".join(found)


def _looks_like_header(line):
    if not _HEADER_WORDS.search(line):
        return False
    if _ADDRESS_UNIT.search(line):
        return True
    return not _STRENGTH.search(line)


def _candidate_lines(text):
    """Split OCR text into lines that may describe a medicine."""
    rows = []
    for raw in text.splitlines()[:MAX_LINES]:
        line = _clean_line(raw)
        if len(line) < 4 or _looks_like_header(line):
            continue
        if not re.search(r"[A-Za-z]{3,}", line):
            continue
        rows.append(line)
    return rows


def _name_tokens(line):
    tokens = []
    for token in _NAME_TOKEN.findall(line):
        if token.casefold() in _STOPWORDS:
            continue
        if any(char.isdigit() for char in token):
            continue
        tokens.append(token)
    return tokens


def _extract_medicines(text):
    lines = _candidate_lines(text)
    cache = {}
    medicines = []
    unmatched = []
    seen_ids = set()
    tokens_checked = 0

    for line in lines:
        strength = _line_strength(line)
        form_hint = _form_hint(line)
        match = None
        matched_hint = None
        tokens = _name_tokens(line)
        # A strength or dosage form is what separates a medicine line from a phone
        # number or clinic hours ("Tel", "Mon to Sat"). Without one, only a long
        # whole-word brand name is trusted.
        has_context = bool(strength or form_hint)
        for index, token in enumerate(tokens):
            if tokens_checked >= MAX_NAME_TOKENS:
                break
            tokens_checked += 1
            hint = _adjacent_strength(line, token)
            name_hint = tokens[index + 1] if index + 1 < len(tokens) else None
            candidate = _match_token(token, strength, cache, form_hint, hint, name_hint)
            if candidate and (has_context or (len(token) >= 5 and candidate[1] == "exact")):
                match = (token, candidate)
                matched_hint = hint
                break

        if not match:
            long_token = any(len(token) >= 4 for token in tokens)
            listable = strength or form_hint or (2 <= len(tokens) <= 8 and long_token)
            if len(unmatched) < MAX_UNMATCHED and listable:
                unmatched.append({
                    "text": line[:160],
                    "strength": strength,
                    "query": tokens[0] if tokens else line[:40],
                })
            continue

        token, (row, match_type, match_score) = match
        if row["id"] in seen_ids:
            continue
        seen_ids.add(row["id"])

        target_strength = normalize_measure(strength) if strength else None
        strength_match = _strength_state(
            row,
            target_strength,
            _strength_number(strength) or matched_hint,
        )

        # A printed strength that contradicts the row means the name alone is not
        # enough to confirm the product, even when the name matches exactly.
        effective_match_type = (
            "possible" if match_type == "exact" and strength_match is False else match_type
        )

        medicines.append({
            "medicine_id": row["id"],
            "name": row["medicine_name"],
            "matched_text": token,
            "brand": row["brand_name"],
            "strength": row["strength"],
            "dosage_form": row["dosage_form"],
            "price": row["price"],
            "prescription_required": row["prescription_required"],
            "match_type": effective_match_type,
            "match_score": match_score,
            "strength_match": strength_match,
            "dosage_text": _dosage_text(line),
            "source_line": line[:160],
        })

    return medicines, unmatched


def parse_prescription_medicines(file_storage):
    """Read a prescription image/PDF and return matched catalog medicines.

    The uploaded file is deleted after processing. Unreadable input returns an
    explicit failure; no sample medicines are ever substituted for real output.
    """
    filename = getattr(file_storage, "filename", "") or "prescription"
    suffix = Path(filename).suffix.casefold()
    display_name = re.sub(r"[^A-Za-z0-9_.-]", "_", Path(filename).name)

    if suffix not in IMAGE_SUFFIXES and suffix != PDF_SUFFIX:
        return _failure(display_name, "Unsupported file type. Upload a PNG, JPG, or PDF prescription.")
    if suffix == PDF_SUFFIX and not _tesseract_command() and not _google_vision_key():
        return _failure(display_name, "No OCR engine is configured on the server.")

    stored_path = UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
    text = ""
    provider = ""
    try:
        file_storage.save(str(stored_path))
        text, provider = _read_text(stored_path, suffix)
    except UnidentifiedImageError:
        return _failure(display_name, "The image could not be opened. Try a PNG or JPG photo.")
    except RuntimeError as error:
        return _failure(display_name, str(error))
    except Exception as error:
        print(f"Prescription OCR failed: {error}")
        return _failure(
            display_name,
            "The prescription could not be read. Try a brighter, flatter photo or a text PDF.",
        )
    finally:
        try:
            stored_path.unlink()
        except OSError:
            pass

    text = text.strip()
    if len(re.sub(r"\s+", "", text)) < MIN_READABLE_CHARS:
        return _failure(
            display_name,
            "No readable text was found. Use a brighter, sharper photo with the whole prescription in frame.",
            text,
            provider,
        )

    medicines, unmatched = _extract_medicines(text)
    if medicines:
        message = "Review each match against the printed prescription before acting on it."
    else:
        message = (
            "Text was read, but no medicine matched the local catalog. "
            "Check the printed names below and search manually."
        )

    return {
        "success": True,
        "filename": display_name,
        "message": message,
        "text": text[:MAX_TEXT_CHARS],
        "count": len(medicines),
        "data": medicines,
        "unmatched_lines": unmatched,
        "provider": provider,
    }
