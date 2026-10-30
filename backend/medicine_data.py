"""Conservative parsing helpers for matching medicine records."""

import hashlib
import json
import re


_AMOUNT = re.compile(
    r"\d+(?:\.\d+)?\s*(?:mcg|μg|µg|mg|g|kg|ml|l|iu|units?|%)",
    re.IGNORECASE,
)
_PACK = re.compile(
    r"\b(?i:strip|pack|bottle|box|blister|sachet)\s*(?:of\s*)?"
    r"(\d+)\s*((?i:tablets?|capsules?|softgels?|ml|g|sachets?)"
    r"(?=$|\s|[^\w]|[A-Z]))?",
)


def _text(value):
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.casefold() in {"", "nan", "<na>", "none", "null", "n/a", "not available"} else text


def normalize_medication_text(value):
    return "".join(re.findall(r"[a-z0-9]+", _text(value).casefold()))


def normalize_measure(value):
    value = _text(value).casefold().replace("μ", "u").replace("µ", "u")
    return normalize_medication_text(value)


def parse_composition(composition):
    """Parse a complete `Ingredient (strength) + ...` composition.

    Return normalized ingredient records, or an empty list if any component is
    unnamed or has no measurable strength. Incomplete data is never guessed.
    """
    composition = _text(composition)
    if not composition:
        return []

    result = []
    for component in re.split(r"\s*\+\s*", str(composition).strip()):
        amounts = _AMOUNT.findall(component)
        name = re.sub(r"\([^)]*\)", " ", component)
        name = re.sub(r"\b(?:ADD|equivalent|eq\.?|each|contains)\b", " ", name, flags=re.I)
        # Some scraped rows concatenate the tail of the manufacturer field with
        # the ingredient (e.g. '. LtdAtorvastatin'). Split CamelCase and remove
        # common legal company suffixes before normalizing the ingredient.
        name = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)
        name = re.sub(r"\b(?:pvt|private|ltd|limited|incorporated|inc|llp|llc)\b", " ", name, flags=re.I)
        name = re.sub(r"[^A-Za-z0-9]+", " ", name).strip()
        ingredient_norm = normalize_medication_text(name)
        if not ingredient_norm or not amounts:
            return []
        strength = "/".join(re.sub(r"\s+", "", amount).casefold() for amount in amounts)
        result.append({
            "ingredient_name": name,
            "ingredient_name_norm": ingredient_norm,
            "ingredient_strength": strength,
            "ingredient_strength_norm": normalize_measure(strength),
        })

    return sorted(result, key=lambda item: (item["ingredient_name_norm"], item["ingredient_strength_norm"]))


def canonical_composition(composition):
    ingredients = parse_composition(composition)
    if not ingredients:
        return composition
    return " + ".join(
        f"{item['ingredient_name']} ({item['ingredient_strength']})"
        for item in ingredients
    )


_VITAMIN_ALIAS = re.compile(r"vitamin\s*[a-z0-9]*", re.I)


def _pick_alias(name):
    """Pick one name from `A / B / C` aliases, preferring the generic name."""
    aliases = [alias.strip() for alias in re.split(r"\s*/\s*", str(name or "")) if alias.strip()]
    for alias in aliases:
        if not _VITAMIN_ALIAS.fullmatch(alias):
            return alias
    return aliases[0] if aliases else str(name or "")


def _normalized_strength(amount):
    match = re.match(r"(\d+(?:\.\d+)?)\s*([A-Za-z%].*)$", str(amount or "").strip())
    if not match:
        return re.sub(r"\s+", "", str(amount or "")).casefold()
    return f"{float(match.group(1)):g}{re.sub(r'\s+', '', match.group(2)).casefold()}"


def first_alias_composition(composition):
    """Collapse slash-separated ingredient aliases and drop trailing zeros.

    Live pharmacy catalogs spell ingredients as `Paracetamol / Acetaminophen`
    and strengths as `650.0 Mg`; the local dataset uses `Paracetamol (650mg)`.
    Both sides must normalize to the same composition before they can match.
    """
    text = _text(composition)
    if not text:
        return text
    components = []
    for component in re.split(r"\s*\+\s*", text):
        amounts = _AMOUNT.findall(component)
        name = _pick_alias(re.sub(r"\([^)]*\)", " ", component))
        name = re.sub(r"[^A-Za-z0-9]+", " ", name).strip()
        if name and amounts:
            components.append(f"{name} ({'/'.join(_normalized_strength(amount) for amount in amounts)})")
    return canonical_composition(" + ".join(components)) if components else canonical_composition(text)


def make_product_key(medicine_name, brand_name, strength, dosage_form, composition,
                     release_type, pack_size):
    """Stable SKU-like key that survives a database rebuild and row reordering."""
    identity = [
        normalize_medication_text(value)
        for value in (
            medicine_name, brand_name, strength, dosage_form,
            canonical_composition(composition), release_type, pack_size,
        )
    ]
    payload = json.dumps(identity, ensure_ascii=True, separators=(",", ":"))
    return "v1:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def extract_release_type(name):
    """Extract explicit modified-release markers; do not infer them."""
    match = re.search(r"\b(XR|ER|SR|CR|MR|XL)\b", _text(name), re.I)
    return match.group(1).upper() if match else None


def extract_pack_size(*values):
    """Extract a stated package quantity, such as `strip of 10 tablets`."""
    for value in values:
        match = _PACK.search(_text(value))
        if match:
            unit = (match.group(2) or "units").casefold()
            return f"{int(match.group(1))} {unit}"
    return None
