"""Query parsing, ranking and spelling correction for medicine search.

The search service gathers candidate rows with SQL; this module decides their
order. It never invents data: ranking only reads the stored product name,
brand, strength, form and composition flags.
"""

import math
import re
import threading
from collections import Counter

from backend import database
from backend.medicine_data import normalize_medication_text

FORM_WORDS = frozenset("""
tablet tablets tab tabs capsule capsules cap caps syrup suspension injection
infusion cream ointment gel drops drop spray inhaler solution oral lotion
powder sachet granules strip bottle vial ampoule kit
""".split())
UNIT_WORDS = frozenset("mg mcg ug g gm kg ml l iu unit units w v ww wv".split())

# Salt and hydrate words that follow an active ingredient in stored
# compositions ("Diclofenac Sodium" -> "diclofenacsodium"). A query for the
# base ingredient is still a generic query.
SALT_SUFFIXES = (
    "hydrochloride", "hcl", "sodium", "potassium", "calcium", "magnesium",
    "diethylamine", "maleate", "besylate", "besilate", "succinate", "tartrate",
    "citrate", "sulphate", "sulfate", "phosphate", "mesylate", "fumarate",
    "acetate", "trihydrate", "dihydrate", "monohydrate", "hydrobromide",
    "bromide", "dipropionate", "propionate", "valerate", "furoate", "lactate",
    "gluconate", "carbonate", "oxide", "hydroxide", "chloride", "nitrate",
)

# Spellings the catalog stores differently from common usage.
INGREDIENT_SYNONYMS = {
    "amoxicillin": "amoxycillin",
    "acetaminophen": "paracetamol",
    "levothyroxine": "thyroxine",
    "cephalexin": "cefalexin",
}

_FORM_ORDER = {
    "tablet": 0, "capsule": 1, "syrup": 2, "suspension": 3, "gel": 4,
    "cream": 5, "ointment": 6, "patch": 7, "injection": 8,
}


def split_letters_digits(text):
    """'pan40' -> 'pan 40', 'Telma-40' -> 'Telma 40'; 'B12' and 'D3' stay glued."""
    text = (text or "").strip()
    text = re.sub(r"(?<=[A-Za-z]{2})[-_]?(?=\d)", " ", text)
    text = re.sub(r"(?<=\d)-(?=[A-Za-z])", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _words(text):
    """Lowercase words with letter/digit runs split ('650mg' -> '650', 'mg')."""
    return re.findall(r"[a-z]+|\d+(?:\.\d+)?", (text or "").casefold())


class Query:
    """A parsed search query: name words and an optional strength number."""

    def __init__(self, keyword):
        self.raw = (keyword or "").strip()
        self.spaced = split_letters_digits(self.raw)
        words = _words(self.spaced)
        self.numbers = [w for w in words if w[0].isdigit()]
        self.alpha = [w for w in words if not w[0].isdigit()
                      and w not in FORM_WORDS and w not in UNIT_WORDS]
        # Only one strength is read from a query; combination queries
        # ("amlodipine 5 / atorvastatin 10") keep the plain substring search.
        self.number = self.numbers[0] if len(self.numbers) == 1 else None
        self.text = " ".join(self.alpha)


def _number_set(text):
    return {w.lstrip("0") or "0" for w in re.findall(r"\d+(?:\.\d+)?", text or "")}


def _row(record, field):
    try:
        return record[field]
    except (IndexError, KeyError):
        return None


def score_record(query, record, family_counts=None):
    """Return a sort key (lower is better) for one candidate row."""
    name = str(_row(record, "medicine_name") or "")
    brand = str(_row(record, "brand_name") or "")
    name_words = _words(split_letters_digits(name))
    name_alpha = [w for w in name_words if not w[0].isdigit()
                  and w not in FORM_WORDS and w not in UNIT_WORDS]
    q_alpha = query.alpha

    strength_ok = True
    if query.number:
        wanted = query.number.lstrip("0") or "0"
        strength_ok = (wanted in _number_set(" ".join(w for w in name_words if w[0].isdigit()))
                       or wanted in _number_set(str(_row(record, "strength") or "")))

    n = len(q_alpha)
    if not q_alpha:
        tier = 4
    elif name_alpha == q_alpha:
        tier = 0                       # "Telma 40 Tablet" for "telma 40"
    elif name_alpha[:n] == q_alpha:
        tier = 1                       # "Meftal Spas DS" for "meftal spas"
    elif normalize_medication_text(brand) == normalize_medication_text(query.text):
        tier = 1
    elif name_alpha and name_alpha[0].startswith(q_alpha[0]) and all(
            any(w.startswith(q) for w in name_alpha) for q in q_alpha[1:]):
        tier = 2                       # "Combiflam" for "combi"
    elif any(name_alpha[i:i + n] == q_alpha for i in range(1, len(name_alpha))):
        tier = 3                       # "S-Amlong 5" for "amlong 5"
    else:
        tier = 4                       # any other substring match
    if not strength_ok:
        tier += 5

    # Among partial matches, a brand family the catalog lists many products
    # for ("Augmentin") comes before a rare look-alike ("Augmarc").
    family = 0
    if family_counts and tier % 5 >= 2 and name_alpha:
        family = family_counts.get(name_alpha[0], 0)

    form = str(_row(record, "dosage_form") or "").casefold()
    return (
        tier,
        -family,
        max(0, len(name_alpha) - len(q_alpha)),         # fewer extra words first
        0 if _row(record, "composition_complete") else 1,  # usable rows first
        _FORM_ORDER.get(form, 9),
        name.casefold(),
    )


def rank_records(query, records):
    try:
        family_counts, _ = _vocabulary()
    except Exception:
        family_counts = None
    return sorted(records, key=lambda record: score_record(query, record, family_counts))


def best_tier(query, records):
    return min((score_record(query, r)[0] for r in records), default=None)


# ------------------------------------------------------------------
# Catalog vocabulary (cached per database file and catalog size)
# ------------------------------------------------------------------

_lock = threading.Lock()
_cache = {}


def _signature(connection):
    medicines = connection.execute("SELECT coalesce(max(id), 0), count(*) FROM medicines").fetchone()
    ingredients = connection.execute("SELECT coalesce(max(rowid), 0) FROM medicine_ingredients").fetchone()
    return (str(database.MEDICINE_DATABASE), tuple(medicines), tuple(ingredients))


def _strip_salts(norm):
    changed = True
    while changed:
        changed = False
        for suffix in SALT_SUFFIXES:
            if norm.endswith(suffix) and len(norm) - len(suffix) >= 4:
                norm = norm[: -len(suffix)]
                changed = True
    return norm


def _vocabulary():
    """Return (word counts from product names, base ingredient -> product count)."""
    connection = database.get_connection()
    try:
        signature = _signature(connection)
        with _lock:
            cached = _cache.get("vocab")
            if cached and cached[0] == signature:
                return cached[1], cached[2]
        words = Counter()
        for (name,) in connection.execute("SELECT medicine_name FROM medicines"):
            for word in set(re.findall(r"[a-z]+", str(name or "").casefold())):
                if len(word) >= 3 and word not in FORM_WORDS:
                    words[word] += 1
        ingredients = Counter()
        for norm, count in connection.execute(
            "SELECT ingredient_name_norm, count(DISTINCT medicine_id) FROM medicine_ingredients "
            "WHERE ingredient_name_norm <> '' GROUP BY ingredient_name_norm"
        ):
            ingredients[norm] += count
            base = _strip_salts(norm)
            if base != norm:
                ingredients[base] += count
    finally:
        connection.close()
    with _lock:
        _cache["vocab"] = (signature, words, ingredients)
    return words, ingredients


def warm_up():
    """Build the vocabulary ahead of the first search (safe to call in a thread)."""
    try:
        _vocabulary()
    except Exception as error:  # an empty or missing catalog must not stop the app
        print(f"Search vocabulary not built: {error}")


def generic_ingredient(key):
    """Return the stored ingredient name a query names, or None.

    `key` is the normalized query text. Exact base names count ("diclofenac"
    for "Diclofenac Sodium"); brand prefixes ("telma", "atorva") do not.
    """
    if not key:
        return None
    key = INGREDIENT_SYNONYMS.get(key, key)
    try:
        _, ingredients = _vocabulary()
    except Exception:
        return None
    if key in ingredients:
        return key
    # A query that is most of one ingredient name ("cefixim", "domperidon")
    # is that ingredient; a short brand stem ("atorva", "telma") is not.
    prefixed = [(count, norm) for norm, count in ingredients.items()
                if norm.startswith(key) and len(key) >= 0.8 * len(norm)]
    return max(prefixed)[1] if prefixed else None


def correct_query(query):
    """Return a corrected query string for a misspelled one, or None.

    Each name word of four letters or more that the catalog never uses is
    replaced by the closest catalog word with the same first letter, weighing
    similarity against how many products use the word. A word is left alone
    when nothing is close enough.
    """
    from rapidfuzz import fuzz, process

    words, ingredients = _vocabulary()
    candidates = Counter(words)
    for norm, count in ingredients.items():
        candidates[norm] = max(candidates[norm], count)
    choices = list(candidates)

    changed = False
    corrected = []
    for word in query.alpha:
        if len(word) < 4 or word in candidates:
            corrected.append(word)
            continue
        matches = process.extract(word, choices, scorer=fuzz.ratio, score_cutoff=80, limit=25)
        best = None
        for choice, similarity, _ in matches:
            if choice[0] != word[0] or abs(len(choice) - len(word)) > 3:
                continue
            weight = similarity + 4 * math.log10(candidates[choice] + 1)
            if best is None or weight > best[0]:
                best = (weight, choice)
        if best and best[0] >= 90:
            corrected.append(best[1])
            changed = True
        else:
            corrected.append(word)
    if not changed:
        return None
    text = " ".join(corrected)
    return f"{text} {query.number}" if query.number else text
