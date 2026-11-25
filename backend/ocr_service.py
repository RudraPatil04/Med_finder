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
import time
import uuid
from pathlib import Path

import requests
from PIL import Image, ImageFilter, ImageOps, UnidentifiedImageError
from rapidfuzz import fuzz, process

from backend import database
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
# Gemini (Google AI Studio, free tier) reads handwriting far better than OCR.
# The key comes from GEMINI_API_KEY and is never stored in the repository.
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# Tried in order (after GEMINI_MODEL, if set). Google retires model names for new
# keys (gemini-2.5-flash answered 404 in Nov 2026) and the free tier often answers
# 503 "high demand", so a retired, overloaded or rate-limited model moves on to
# the next one instead of dropping straight to Tesseract.
GEMINI_DEFAULT_MODELS = ("gemini-flash-latest", "gemini-3.5-flash", "gemini-flash-lite-latest")
GEMINI_RETRY_NEXT_MODEL = {404, 429, 500, 503}
GEMINI_TIMEOUT_SECONDS = 45
GEMINI_PROMPT = (
    "This is a photo of a doctor's prescription, possibly handwritten. "
    "Transcribe every medicine line exactly as written, one medicine per line, in the form: "
    "<form> <medicine name> <strength> <dosage schedule> <duration>, for example "
    "'Tab Dolo 650 mg 1-0-1 x 5 days'. Keep the brand name as written; when a letter is "
    "unclear, write your best reading of the word. Do not add medicines that are not on the "
    "page, do not suggest substitutes, and skip the doctor, clinic, patient and address lines. "
    "Output plain text only, no numbering, no markdown. If no medicine is written, output NONE."
)
# Phone photos are usually larger than this; small scans are upscaled so thin
# handwriting strokes stay several pixels wide.
PREPROCESS_TARGET_SIDE = 2000
PREPROCESS_MIN_SIDE = 1600
# Paper crop: only applied when the bright sheet is clearly smaller than the
# photo but still most of it, so a plain white scan is never cut.
PAPER_CROP_MIN_KEEP = 0.30
PAPER_CROP_MAX_KEEP = 0.95
# Suggestions for lines that matched nothing literally (handwriting fallback).
SUGGEST_CUTOFF = 72
SUGGEST_LIMIT = 3
AUTO_ACCEPT_SCORE = 90
AUTO_ACCEPT_GAP = 5
VOCAB_TTL_SECONDS = 3600
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
    (re.compile(r"\b(?:tab|tabs|tablet|tablets)\b|(?:^|\s)T\.\s", re.I), "tablet"),
    (re.compile(r"\b(?:cap|caps|capsule|capsules)\b|(?:^|\s)C\.\s", re.I), "capsule"),
    (re.compile(r"\b(?:syr|syp|syrup|susp|suspension)\b", re.I), "syrup"),
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
    re.compile(r"\b\d+\s*(?:packets?|pkts?|sachets?|pouch(?:es)?|bottles?|strips?)\b", re.I),
)
# Generics that prescriptions write as an abbreviation, mapped to the name the
# catalog would record. A line naming one is always reported (matched when the
# catalog has the product, otherwise "not in the catalog"), never fuzzy-matched
# to a look-alike brand.
_KNOWN_ABBREVIATIONS = {
    "ors": "Oral rehydration salts",
}
_DOTTED_ORS = re.compile(r"\bO\.\s?R\.\s?S\b\.?", re.I)
# "12 MG Road" is an address, not a 12 mg dose; Indian streets make MG ambiguous.
_ADDRESS_UNIT = re.compile(
    r"\b\d+\s*(?:mg|mcg|g|ml)\s*(?:road|rd|street|lane|nagar|colony|sector|chowk)\b", re.I
)
_HEADER_WORDS = re.compile(
    r"\b(?:clinic|hospital|medical|doctor|dr|patient|address|phone|mobile|email|"
    r"registration|regn|regd|mci|council|signature|stamp|seal|diagnosis|advice|"
    r"investigation|investigations|follow[\s-]?up|consultation|date|complaint|vitals|"
    r"weight|height|pulse|temperature|allergy|allergies|"
    # Clinical-note words (complaints, findings, advice) are never medicine names.
    r"giddiness|restlessness|hypoglyc\w*|hyperglyc\w*|rbs|impression|imp|adv|intake|fluid|stat|"
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
    # Pack words ("ORS 2 packets") are quantities, never part of a medicine name.
    "sachets", "packet", "packets", "pkt", "pkts", "pouch", "pouches",
    "bottle", "bottles", "strip", "strips", "vial", "vials", "ampoule", "ampoules",
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
    # Directions and body sites ("as needed for pain", "drops to right ear").
    "pain", "fever", "cough", "cold", "ear", "ears", "eye", "eyes", "nose", "otic",
    "right", "left", "both", "sig", "cont", "wks", "hrs", "hourly", "stat",
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

    prepared = preprocess_prescription_image(image)
    return pytesseract.image_to_string(
        prepared, lang="eng", config="--oem 3 --psm 6", timeout=OCR_TIMEOUT_SECONDS,
    )


def _google_vision_key():
    return os.environ.get("GOOGLE_VISION_API_KEY", "").strip()


def _crop_to_paper(gray):
    """Crop a phone photo to the bright sheet of paper, or return it unchanged.

    Rows and columns where most pixels are bright belong to the paper; the
    dark table or hand around it is dropped. Uses a small copy so it is fast.
    """
    small = gray.copy()
    small.thumbnail((400, 400))
    small = small.filter(ImageFilter.MedianFilter(5))
    histogram = small.histogram()
    total = sum(histogram) or 1
    # Otsu threshold between "paper" and "background".
    weighted_total = sum(index * count for index, count in enumerate(histogram))
    weight_back = 0
    sum_back = 0
    best_threshold, best_variance = 128, -1.0
    for index, count in enumerate(histogram):
        weight_back += count
        if weight_back == 0 or weight_back == total:
            continue
        sum_back += index * count
        mean_back = sum_back / weight_back
        mean_fore = (weighted_total - sum_back) / (total - weight_back)
        variance = weight_back * (total - weight_back) * (mean_back - mean_fore) ** 2
        if variance > best_variance:
            best_threshold, best_variance = index, variance

    width, height = small.size
    pixels = small.load()

    def bright_share_row(y):
        return sum(1 for x in range(width) if pixels[x, y] > best_threshold) / width

    def bright_share_col(x):
        return sum(1 for y in range(height) if pixels[x, y] > best_threshold) / height

    rows = [y for y in range(height) if bright_share_row(y) > 0.5]
    cols = [x for x in range(width) if bright_share_col(x) > 0.5]
    if not rows or not cols:
        return gray
    top, bottom, left, right = rows[0], rows[-1] + 1, cols[0], cols[-1] + 1
    keep = ((bottom - top) * (right - left)) / float(width * height)
    if not (PAPER_CROP_MIN_KEEP <= keep <= PAPER_CROP_MAX_KEEP):
        return gray
    scale_x = gray.width / width
    scale_y = gray.height / height
    pad = 4
    box = (
        max(0, round((left - pad) * scale_x)),
        max(0, round((top - pad) * scale_y)),
        min(gray.width, round((right + pad) * scale_x)),
        min(gray.height, round((bottom + pad) * scale_y)),
    )
    return gray.crop(box)


def preprocess_prescription_image(image):
    """Prepare a prescription photo for OCR.

    Straightens by EXIF, crops to the paper, converts to grayscale, stretches
    contrast, resizes so the longest side is about 1600-2400 px and sharpens
    pen strokes. Every step is conservative so a clean scan is not harmed.
    """
    prepared = ImageOps.exif_transpose(image)
    if prepared.mode not in ("RGB", "L"):
        prepared = prepared.convert("RGB")
    gray = prepared.convert("L")
    gray = _crop_to_paper(gray)
    gray = ImageOps.autocontrast(gray, cutoff=1)

    longest = max(gray.size) or 1
    if longest < PREPROCESS_MIN_SIDE:
        scale = min(3.0, PREPROCESS_TARGET_SIDE / longest)
    elif longest > GOOGLE_VISION_MAX_SIDE:
        scale = GOOGLE_VISION_MAX_SIDE / longest
    else:
        scale = 1.0
    if scale != 1.0:
        gray = gray.resize(
            (max(1, round(gray.width * scale)), max(1, round(gray.height * scale))),
            Image.LANCZOS,
        )
    return gray.filter(ImageFilter.UnsharpMask(radius=2, percent=80, threshold=3))


def _vision_word_confidences(result, confidences):
    """Collect Vision's per-word confidence (0-1) keyed by lower-cased word."""
    for page in (result.get("fullTextAnnotation") or {}).get("pages", []):
        for block in page.get("blocks", []):
            for paragraph in block.get("paragraphs", []):
                for word in paragraph.get("words", []):
                    text = "".join(symbol.get("text", "") for symbol in word.get("symbols", []))
                    confidence = word.get("confidence")
                    if not text or confidence is None:
                        continue
                    # Store the raw reading and its cleaned form ("Dol0" -> "dolo"),
                    # keeping the lowest: a word read badly anywhere is suspect.
                    for key in {text.casefold(), normalize_ocr_line(text).casefold()}:
                        confidences[key] = min(confidences.get(key, 1.0), float(confidence))


def _google_vision_text(image, confidences=None):
    """Read one page with Google Cloud Vision (DOCUMENT_TEXT_DETECTION).

    When ``confidences`` is a dict it is filled with per-word confidence.
    """
    import base64

    key = _google_vision_key()
    if not key:
        raise RuntimeError("GOOGLE_VISION_API_KEY is not set.")

    prepared = preprocess_prescription_image(image)
    buffer = io.BytesIO()
    prepared.save(buffer, "JPEG", quality=92)

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
    if confidences is not None:
        _vision_word_confidences(result, confidences)
    return (result.get("fullTextAnnotation") or {}).get("text", "")


def _gemini_key():
    return os.environ.get("GEMINI_API_KEY", "").strip()


def _gemini_models():
    """GEMINI_MODEL first (if set), then the defaults, without repeats."""
    preferred = os.environ.get("GEMINI_MODEL", "").strip()
    models = [preferred] if preferred else []
    models += [model for model in GEMINI_DEFAULT_MODELS if model != preferred]
    return models


def _gemini_text(image):
    """Read one page with Gemini and return its line-by-line transcription.

    Each model in _gemini_models() is tried until one answers; a retired (404),
    rate-limited (429) or overloaded (5xx) model, or a timeout, moves on to the
    next. The error raised when all fail names every model and its reason.
    """
    import base64

    key = _gemini_key()
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set.")

    prepared = preprocess_prescription_image(image)
    buffer = io.BytesIO()
    prepared.save(buffer, "JPEG", quality=92)
    image_part = {"inline_data": {
        "mime_type": "image/jpeg",
        "data": base64.b64encode(buffer.getvalue()).decode("ascii"),
    }}

    failures = []
    data = None
    for model in _gemini_models():
        generation = {"temperature": 0}
        if "2.5-flash" in model:
            generation["thinkingConfig"] = {"thinkingBudget": 0}  # faster, plain transcription
        payload = {
            "contents": [{"parts": [image_part, {"text": GEMINI_PROMPT}]}],
            "generationConfig": generation,
        }
        try:
            response = requests.post(
                GEMINI_URL.format(model=model),
                headers={"x-goog-api-key": key},
                json=payload,
                timeout=GEMINI_TIMEOUT_SECONDS,
            )
        except requests.Timeout:
            failures.append(f"{model}: timed out")
            continue
        if response.status_code == 200:
            data = response.json()
            break
        detail = ""
        try:
            detail = response.json().get("error", {}).get("message", "")
        except ValueError:
            detail = response.text[:200]
        failures.append(f"{model}: {response.status_code} {detail[:120]}".strip())
        if response.status_code not in GEMINI_RETRY_NEXT_MODEL:
            break  # bad key or bad request: another model will not help
    if data is None:
        raise RuntimeError("Gemini returned no answer (" + "; ".join(failures) + ")")
    if failures:
        print("Gemini: skipped " + "; ".join(failures))

    candidates = data.get("candidates") or []
    if not candidates:
        reason = (data.get("promptFeedback") or {}).get("blockReason", "no answer")
        raise RuntimeError(f"Gemini returned no transcription ({reason}).")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "\n".join(part.get("text", "") for part in parts).strip()
    text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text)
    return "" if text.strip().upper() == "NONE" else text


def _image_text(image, confidences=None):
    """Read one page with Gemini, else Google Vision, else local Tesseract.

    A failed cloud call (no network, quota or rate limit) falls through to the
    next reader, so the scanner keeps working on the local OCR.
    """
    if _gemini_key():
        try:
            return _gemini_text(image), "gemini"
        except Exception as error:
            print(f"Gemini reading failed, falling back: {error}")
    else:
        print("Gemini skipped: GEMINI_API_KEY is not set in this server's environment.")
    if _google_vision_key():
        try:
            return _google_vision_text(image, confidences), "google-vision"
        except Exception as error:
            print(f"Google Vision OCR failed, falling back to Tesseract: {error}")
    return _ocr_image(image), "tesseract"


def _read_pdf(path, confidences=None):
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
            page_text, provider = _image_text(image, confidences)
            parts.append(page_text)
            providers.add(provider)
    return "\n".join(parts), "+".join(sorted(providers))


def _read_text(path, suffix, confidences=None):
    if suffix == PDF_SUFFIX:
        return _read_pdf(path, confidences)
    with Image.open(str(path)) as image:
        return _image_text(image, confidences)


# Characters OCR confuses inside numbers ("65O" -> 650, "l0" -> 10) and units
# ("rng", "mq", "m9" -> mg). Applied only next to digits so words stay intact.
_DIGIT_LOOKALIKES = {"O": "0", "o": "0", "l": "1", "I": "1", "|": "1"}
_LETTER_LOOKALIKES = {"0": "o", "1": "l", "5": "s", "8": "b", "3": "e", "4": "a", "6": "g", "7": "t"}
_UNIT_FIXES = (
    (re.compile(r"(?<=\d)\s*(?:rng|rnq|nng|rmg|mq|m9|mgs|my)\b", re.I), "mg"),
    (re.compile(r"(?<=\d)\s*(?:mcq|rncg|mcgs)\b", re.I), "mcg"),
    (re.compile(r"(?<=\d)\s*(?:rnl|mI|m1)\b"), "ml"),
)
_RX_MARKER = re.compile(r"^\s*(?:\u211e|r[x/]\s*[:.)-]?|\d{1,2}\s*[.)]|[-*\u2022])\s*", re.I)


def _fix_number_run(match):
    run = match.group(0)
    # Only rewrite runs that are clearly numbers: "65O", "5OO", "4O", "l0".
    # "B12" and "D3" never reach here because a letter precedes the digit.
    starts_numeric = run[0].isdigit() or (len(run) > 1 and run[0] in "lI|" and run[1].isdigit())
    if not starts_numeric:
        return run
    return "".join(_DIGIT_LOOKALIKES.get(char, char) for char in run)


def _fix_word(match):
    word = match.group(0)
    letters = sum(char.isalpha() for char in word)
    # "Parac0tamol" -> "paracotamol" is still closer to the brand than with a 0;
    # tokens that are mostly digits (strengths) are left to _fix_number_run.
    if letters < 3 or letters <= len(word) / 2:
        return word
    word = re.sub(
        r"(?<=[A-Za-z])[0-8](?=[A-Za-z])",
        lambda m: _LETTER_LOOKALIKES.get(m.group(0), m.group(0)),
        word,
    )
    # A single trailing 0 or 1 after a word is a misread o or l ("Dol0", "Calpo1");
    # two or more digits are a strength written without a space ("Telma40").
    if re.fullmatch(r"[A-Za-z]{3,}[01]", word):
        word = word[:-1] + _LETTER_LOOKALIKES[word[-1]]
    return word


def normalize_ocr_line(line):
    """Undo common OCR/handwriting slips without inventing content."""
    text = line.replace("\u2014", "-").replace("\u2013", "-")
    text = re.sub(r"(?<![A-Za-z0-9])[0-9OolI|]*\d[0-9OolI|]*", _fix_number_run, text)
    text = re.sub(r"[A-Za-z]+\d[A-Za-z0-9]*", _fix_word, text)
    for pattern, replacement in _UNIT_FIXES:
        text = pattern.sub(replacement, text)
    return text


def _clean_line(line):
    line = _DOTTED_ORS.sub("ORS", line)
    line = normalize_ocr_line(line)
    line = _RX_MARKER.sub("", line)
    return re.sub(r"\s+", " ", line.replace("|", "l")).strip()


_PER_5ML = re.compile(r"\(?\b(\d+(?:\.\d+)?)\s*/\s*5\s*(?:ml)?\s*\)?", re.I)


def _line_strength(line):
    per_5ml = _PER_5ML.search(line)
    if per_5ml and _form_hint(line) == "syrup":
        return f"{per_5ml.group(1)}mg"
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


# A handwritten "Syp" covers every oral liquid the catalog may record.
_FORM_SYNONYMS = {
    "syrup": ("syrup", "suspension", "solution", "elixir", "expectorant", "linctus"),
    "injection": ("injection", "infusion"),
}


def _form_ok(form_hint, dosage_form):
    if not form_hint:
        return True
    form = str(dosage_form or "").casefold()
    return any(word in form for word in _FORM_SYNONYMS.get(form_hint, (form_hint,)))


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
        if form_hint and not _form_ok(form_hint, row["dosage_form"]):
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
        form_ok = _form_ok(form_hint, row["dosage_form"])
        hint_rank = 0 if strength_number and strength_number in _name_numbers(row["medicine_name"]) else 1
        # "Ibugesic Plus" must beat the plain "Ibugesic" product, and among Plus
        # products the one adding the fewest words wins ("Plus" over "Plus NF").
        name_rank = 0 if hint_boundary and hint_boundary.search(str(row["medicine_name"] or "")) else 1
        extra_rank = 0
        if hint_boundary:
            printed = {token_key, name_hint.casefold()}
            extra_rank = len([word for word in name_words if word not in printed])
        # A printed form ("Syp", "Tab") outranks strength: Meftal-P (100/5) is the
        # suspension, not the 100 mg dispersible tablet.
        rank = (0 if form_ok else 1, strength_rank, 0 if exact else 1, name_rank, extra_rank, hint_rank)
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


_VOCAB_CACHE = {}


def _catalog_vocabulary():
    """Distinct brand words and ingredient names, cached per database file.

    Returns (labels, kinds): parallel lists where kind is "brand" or
    "ingredient". Built once from the catalog so a handwritten word can be
    compared with every real name, not only names sharing its first letters.
    """
    cache_key = str(database.MEDICINE_DATABASE)
    cached = _VOCAB_CACHE.get(cache_key)
    if cached and time.monotonic() - cached[0] < VOCAB_TTL_SECONDS:
        return cached[1], cached[2]

    vocabulary = {}
    try:
        for row in execute_query(
            "SELECT DISTINCT COALESCE(NULLIF(TRIM(brand_name), ''), medicine_name) AS label FROM medicines"
        ):
            words = _name_words(row["label"])
            if words and len(words[0]) >= FUZZY_MIN_TOKEN and words[0].isalpha():
                vocabulary.setdefault(words[0], "brand")
    except Exception as error:
        print(f"OCR vocabulary: brand list unavailable: {error}")
    try:
        for row in execute_query("SELECT DISTINCT ingredient_name_norm AS label FROM medicine_ingredients"):
            label = " ".join(_name_words(row["label"]))
            if len(label) >= FUZZY_MIN_TOKEN and not label[0].isdigit():
                vocabulary[label] = "ingredient"
    except Exception as error:
        print(f"OCR vocabulary: ingredient list unavailable: {error}")

    labels = list(vocabulary)
    kinds = [vocabulary[label] for label in labels]
    _VOCAB_CACHE[cache_key] = (time.monotonic(), labels, kinds)
    return labels, kinds


def _token_variants(tokens):
    """Single words plus joined neighbours, since handwriting splits names."""
    variants = []
    for index, token in enumerate(tokens):
        if len(token) >= FUZZY_MIN_TOKEN:
            variants.append(token.casefold())
        if index + 1 < len(tokens):
            joined = (token + tokens[index + 1]).casefold()
            if len(joined) <= 16:
                variants.append(joined)
    if len(tokens) >= 2:
        variants.append(" ".join(tokens[:2]).casefold())  # "clavulanic acid"
    return list(dict.fromkeys(variants))


def _suggest_matches(line, tokens, strength, form_hint, cache):
    """Rank catalog medicines that a garbled printed line probably names.

    Each word is compared with every catalog brand word and ingredient name
    (Levenshtein ratio). The best names are then resolved to a product with
    the same literal lookup used for clean text, preferring the printed
    strength. Scores are the computed name similarity, 0-100.
    """
    labels, kinds = _catalog_vocabulary()
    if not labels:
        return []

    hits = {}
    for variant in _token_variants(tokens):
        for label, score, index in process.extract(
            variant, labels, scorer=fuzz.ratio, score_cutoff=SUGGEST_CUTOFF, limit=5
        ):
            if score > hits.get(label, (0, None))[0]:
                hits[label] = (score, kinds[index], variant)

    target_strength = normalize_measure(strength) if strength else None
    strength_number = _strength_number(strength)
    suggestions = {}
    for label, (score, kind, variant) in sorted(hits.items(), key=lambda item: -item[1][0])[:5]:
        hint = strength_number
        if not hint:
            for token in tokens:
                if token.casefold() in variant:
                    hint = _adjacent_strength(line, token)
                    if hint:
                        break
        found = _match_token(label, strength, cache, form_hint, hint)
        if kind == "ingredient" and (strength_number or hint) and (
            not found or _strength_state(found[0], target_strength, strength_number or hint) is not True
        ):
            # "Paracetamol 650" should offer a 650 product by composition, not
            # the first product that merely has paracetamol in its name.
            found = _ingredient_product(label, target_strength, strength_number or hint, form_hint) or found
        if found:
            row = found[0]
            strength_ok = _strength_state(row, target_strength, strength_number or hint)
            form_ok = _form_ok(form_hint, row["dosage_form"])
            key = row["id"]
            entry = {
                "medicine_id": row["id"],
                "name": row["medicine_name"],
                "brand": row["brand_name"],
                "strength": row["strength"],
                "dosage_form": row["dosage_form"],
                "price": row["price"],
                "prescription_required": row["prescription_required"],
                "matched_text": label,
                "kind": kind,
                "score": round(float(score), 1),
                "strength_match": strength_ok,
                "form_match": form_ok,
            }
        elif kind == "ingredient":
            query = f"{label} {strength_number}" if strength_number else label
            key = "q:" + query
            entry = {
                "medicine_id": None,
                "name": label.title() + (f" {strength}" if strength else ""),
                "matched_text": label,
                "query": query,
                "kind": kind,
                "score": round(float(score), 1),
                "strength_match": None,
                "form_match": None,
            }
        else:
            continue
        if key not in suggestions or entry["score"] > suggestions[key]["score"]:
            suggestions[key] = entry

    def rank(entry):
        return (
            -entry["score"]
            - (6 if entry["strength_match"] is True else 0)
            + (10 if entry["strength_match"] is False else 0)
            + (4 if entry["form_match"] is False else 0)
        )

    return sorted(suggestions.values(), key=rank)[:SUGGEST_LIMIT]


def _ingredient_product(ingredient, target_strength, strength_number, form_hint):
    """Cheapest single-ingredient catalog product with the printed strength."""
    query = f"{ingredient} {strength_number}"
    try:
        rows = search_medicines(query, limit=15, enable_api_fallback=False)
    except Exception:
        return None
    ranked = []
    for row in rows:
        if _strength_state(row, target_strength, strength_number) is not True:
            continue
        form_ok = _form_ok(form_hint, row["dosage_form"])
        price = row["price"] if row["price"] else float("inf")
        ranked.append((0 if form_ok else 1, price, row))
    if not ranked:
        return None
    ranked.sort(key=lambda item: (item[0], item[1]))
    return (ranked[0][2], "partial", None)


def _auto_accept(suggestions, strength, form_hint):
    """Promote the top suggestion only when it is clearly the printed medicine.

    It must be a catalog product, very similar in spelling, agree with the
    printed strength and form, and beat the next product by a clear margin.
    It is still labelled "possible" so the user confirms it.
    """
    if not suggestions or not (strength or form_hint):
        return None
    top = suggestions[0]
    if not top.get("medicine_id") or top["score"] < AUTO_ACCEPT_SCORE:
        return None
    if strength and top["strength_match"] is not True:
        return None
    if top["form_match"] is False:
        return None
    rivals = [entry for entry in suggestions[1:] if entry.get("medicine_id")]
    if rivals and rivals[0]["score"] > top["score"] - AUTO_ACCEPT_GAP and rivals[0]["matched_text"] != top["matched_text"]:
        return None
    return top


def _line_confidence(line, confidences):
    """Mean Vision word confidence (0-100) for a line, or None without data."""
    if not confidences:
        return None
    values = [
        confidences[word.casefold()]
        for word in re.findall(r"\S+", line)
        if word.casefold() in confidences
    ]
    if not values:
        return None
    return round(100 * sum(values) / len(values), 1)


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


def _looks_garbled(line):
    """True for OCR noise such as 'Be soe Negere 7) 48. @) l'.

    Printed medicine lines are mostly real words; handwriting read by a printed-text
    engine comes out as symbols and short fragments. Used only for lines with no
    printed strength or dosage form, so '2 Tab Dolo 650 bd' is never affected.
    """
    words = line.split()
    visible = sum(len(word) for word in words)
    if not words or not visible:
        return True
    letters = sum(char.isalpha() for char in line)
    real_words = sum(1 for word in words if re.fullmatch(r"[A-Za-z]{3,}", word))
    return letters / visible < 0.6 or real_words / len(words) <= 0.5


def _handwriting_suspected(text):
    """True when most of the text is noise, so no match should be trusted."""
    lines = [_clean_line(raw) for raw in text.splitlines()]
    lines = [line for line in lines if len(line) >= 4]
    if len(lines) < 3:
        return False
    garbled = sum(
        1 for line in lines
        if not (_line_strength(line) or _form_hint(line)) and _looks_garbled(line)
    )
    return garbled / len(lines) >= 0.5


def _name_tokens(line):
    tokens = []
    for token in _NAME_TOKEN.findall(line):
        if token.casefold() in _STOPWORDS:
            continue
        if any(char.isdigit() for char in token):
            continue
        tokens.append(token)
    return tokens


def _known_abbreviation_entry(token, line, line_confidence):
    """A line naming a known abbreviation ("ORS"): the catalog product, or a note.

    A catalog product counts only when its name carries the abbreviation as a
    word or its name/composition carries the full generic name. Otherwise the
    line is reported as absent from the catalog, with no look-alike suggestions.
    """
    generic = _KNOWN_ABBREVIATIONS[token.casefold()]
    try:
        rows = execute_query(
            "SELECT id, medicine_name, brand_name, strength, dosage_form, price, prescription_required "
            "FROM medicines WHERE ' ' || medicine_name || ' ' LIKE ? OR medicine_name LIKE ? "
            "OR composition LIKE ? ORDER BY price IS NULL, price LIMIT 1",
            (f"% {token} %", f"%{generic}%", f"%{generic}%"),
        )
    except Exception:
        rows = []
    for row in rows:
        return {
            "medicine_id": row["id"],
            "name": row["medicine_name"],
            "matched_text": token,
            "brand": row["brand_name"],
            "strength": row["strength"],
            "dosage_form": row["dosage_form"],
            "price": row["price"],
            "prescription_required": row["prescription_required"],
            "match_type": "possible",
            "match_score": None,
            "strength_match": None,
            "ocr_confidence": line_confidence,
            "dosage_text": _dosage_text(line),
            "source_line": line[:160],
            "alternatives": [],
        }
    return {
        "text": line[:160],
        "strength": None,
        "query": generic,
        "ocr_confidence": line_confidence,
        "suggestions": [],
        "generic_name": generic,
        "not_in_catalog": True,
        "note": (f"{token.upper()} means {generic.lower()}. It was read from the prescription, but "
                 f"the catalog has no {generic.lower()} product, so there is no price to compare."),
    }


def _extract_medicines(text, confidences=None):
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
            # A brand is accepted only when this line also prints a strength (with
            # or without a unit: "Dolo 650") or a dosage form. A brand name that is
            # also an ordinary word ("Further", "Format") is otherwise matched
            # inside any sentence.
            if candidate and (has_context or hint):
                match = (token, candidate)
                matched_hint = hint
                break

        line_confidence = _line_confidence(line, confidences)
        if not match:
            abbreviation = next(
                (token for token in tokens if token.casefold() in _KNOWN_ABBREVIATIONS), None
            )
            if abbreviation:
                known = _known_abbreviation_entry(abbreviation, line, line_confidence)
                if known.get("medicine_id"):
                    if known["medicine_id"] not in seen_ids:
                        seen_ids.add(known["medicine_id"])
                        medicines.append(known)
                elif len(unmatched) < MAX_UNMATCHED:
                    unmatched.append(known)
                continue
            long_token = any(len(token) >= 4 for token in tokens)
            listable = strength or form_hint or (2 <= len(tokens) <= 8 and long_token)
            if not listable or len(unmatched) >= MAX_UNMATCHED:
                continue
            if not (strength or form_hint) and _looks_garbled(line):
                continue  # noise, not a printed name: offer no suggestions for it
            suggestions = _suggest_matches(line, tokens, strength, form_hint, cache) if long_token else []
            accepted = _auto_accept(suggestions, strength, form_hint)
            if accepted and accepted["medicine_id"] not in seen_ids:
                seen_ids.add(accepted["medicine_id"])
                medicines.append({
                    "medicine_id": accepted["medicine_id"],
                    "name": accepted["name"],
                    "matched_text": accepted["matched_text"],
                    "brand": accepted["brand"],
                    "strength": accepted["strength"],
                    "dosage_form": accepted["dosage_form"],
                    "price": accepted["price"],
                    "prescription_required": accepted["prescription_required"],
                    "match_type": "possible",
                    "match_score": accepted["score"],
                    "strength_match": accepted["strength_match"],
                    "ocr_confidence": line_confidence,
                    "dosage_text": _dosage_text(line),
                    "source_line": line[:160],
                    "alternatives": [entry for entry in suggestions if entry is not accepted],
                })
                continue
            unmatched.append({
                "text": line[:160],
                "strength": strength,
                "query": tokens[0] if tokens else line[:40],
                "ocr_confidence": line_confidence,
                "suggestions": suggestions,
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
        # A name with no printed strength at all is never "exact" either: the line
        # does not say which product was written.
        printed_strength = bool(strength or matched_hint)
        effective_match_type = (
            "possible"
            if match_type == "exact" and (strength_match is not True or not printed_strength)
            else match_type
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
            "ocr_confidence": line_confidence,
            "dosage_text": _dosage_text(line),
            "source_line": line[:160],
            "alternatives": [],
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
    if suffix == PDF_SUFFIX and not _tesseract_command() and not _google_vision_key() and not _gemini_key():
        return _failure(display_name, "No OCR engine is configured on the server.")

    stored_path = UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
    text = ""
    provider = ""
    confidences = {}
    try:
        file_storage.save(str(stored_path))
        text, provider = _read_text(stored_path, suffix, confidences)
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

    medicines, unmatched = _extract_medicines(text, confidences)
    warning = None
    if _handwriting_suspected(text):
        warning = (
            "This looks handwritten or too unclear to read reliably, so any names below "
            "are guesses. Ask your pharmacist or doctor to read it, or type the names yourself."
        )
        if provider == "tesseract":
            warning += (" A cloud handwriting reader (Google Vision) is not enabled on this "
                        "server; it handles handwriting better than the local reader.")
    if medicines:
        message = "Confirm each match against the paper prescription, or change it, before acting on it."
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
        "warning": warning,
    }
