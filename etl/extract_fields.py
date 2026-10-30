# extract_fields.py
import re
import sys
from collections import Counter
from pathlib import Path
import pandas as pd

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from backend.medicine_data import (
    extract_pack_size,
    extract_release_type,
    normalize_medication_text,
    parse_composition,
)

DOSAGE_FORMS=sorted([
"Mouth Dissolving Tablet","Dry Syrup","Eye Drop","Eye Drops","Ear Drop","Ear Drops",
"Nasal Drop","Nasal Drops","Nasal Spray","Face Wash","Tablet","Capsule","Syrup",
"Injection","Cream","Gel","Ointment","Lotion","Drops","Suspension","Powder","Spray",
"Soap","Shampoo","Mouthwash","Solution","Respules","Inhaler","Patch","Granules",
"Sachet","Infusion","Foam","Oil"], key=len, reverse=True)

def extract_brand_name(name):
    if pd.isna(name): return None
    t=str(name).strip()
    for f in DOSAGE_FORMS:
        t=re.sub(r"\s+"+re.escape(f)+r"$","",t,flags=re.I).strip()
    t=re.sub(r"\s+\d+(\.\d+)?([A-Za-z/%]+)?(/\d+(\.\d+)?([A-Za-z/%]+)?)*$","",t).strip()
    return t

def extract_strength_from_name(name):
    if pd.isna(name): return None
    m=re.search(r"\d+(?:\.\d+)?(?:mg|mcg|g|ml|IU|%)(?:/\d+(?:\.\d+)?(?:mg|mcg|g|ml|IU|%))*",str(name),re.I)
    return m.group() if m else None

def extract_strength_from_composition(comp):
    if pd.isna(comp): return None
    m=re.findall(r"\((.*?)\)",str(comp))
    if not m: return None
    v=m[0].strip()
    return None if v.upper() in {"NA","N/A","NONE","NULL","NOT AVAILABLE",""} else v

def extract_dosage_form(name):
    if pd.isna(name): return None
    t=str(name).lower()
    if "eye drop" in t: return "Eye Drop"
    if "ear drop" in t: return "Ear Drop"
    if "nasal drop" in t: return "Nasal Drop"
    for f in DOSAGE_FORMS:
        if f.lower() in t: return f
    return None

def extract_category(form):
    if pd.isna(form): return None
    f=str(form).lower()
    if f in {"tablet","capsule","granules","powder","sachet"}: return "Oral"
    if f in {"syrup","dry syrup","suspension","solution"}: return "Oral Liquid"
    if f in {"cream","gel","ointment","lotion","foam","oil"}: return "Topical"
    if f in {"eye drop","eye drops","ear drop","ear drops","nasal drop","nasal drops","drops"}: return "Drops"
    if f in {"injection","infusion"}: return "Injectable"
    if f in {"spray","nasal spray","inhaler","respules"}: return "Respiratory"
    return "Other"

# The source scrape merged the manufacturer into the first ingredient, and for
# most rows dropped the ingredient name entirely ("(650mg)").  Recover it from
# the full composition embedded in Product Name, falling back to Type of Medicine.
COMPANY_WORDS = {
    "pvt", "private", "ltd", "limited", "inc", "incorporated", "llp", "llc",
    "co", "company", "corp", "corporation", "pharmaceuticals", "pharmaceutical",
    "pharma", "labs", "laboratory", "laboratories", "biotech", "healthcare",
    "health", "remedies", "lifesciences", "sciences", "biosciences", "formulations",
    "drugs", "medicaments", "medicament", "india", "indian", "international",
    "enterprises", "industries", "agency", "agencies", "traders", "trading",
    "marketing", "distributors", "chemicals", "chemical", "generics", "bioscience",
    "research", "care", "therapeutics", "medicare", "biogenic", "life",
}

STRENGTH_TOKEN = (
    r"\d+(?:\.\d+)?\s*(?:mcg|μg|µg|mg|g|ml|l|iu|units?|%)"
    r"(?:\s*/\s*(?:\d+(?:\.\d+)?\s*(?:mcg|μg|µg|mg|g|ml|l|iu|units?|%)|w/w|w/v))*"
)
COMPOSITION_COMPONENT = r"[A-Za-z][A-Za-z0-9\s\-./&]*?\(\s*" + STRENGTH_TOKEN + r"\s*\)"
COMPOSITION_CHAIN = re.compile(
    COMPOSITION_COMPONENT + r"(?:\s*\+\s*" + COMPOSITION_COMPONENT + r")*"
)
LEADING_STRENGTH = re.compile(r"\s*(\([^()]*\))\s*(.*)$", re.S)


def _text_value(value):
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _component_name(part):
    text = re.sub(r"\([^)]*\)", " ", _text_value(part))
    text = re.sub(r"\b(?:ADD|equivalent|eq\.?|each|contains|not available)\b", " ", text, flags=re.I)
    text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)
    tokens = re.findall(r"[A-Za-z]+", text)
    display = [token for token in tokens if token.casefold() not in COMPANY_WORDS]
    return " ".join(display).strip(), sum(token.casefold() in COMPANY_WORDS for token in tokens)


def _ingredient_lexicon(compositions):
    counts = Counter()
    displays = {}
    for composition in compositions:
        for index, part in enumerate(re.split(r"\s*\+\s*", _text_value(composition))):
            display, company_words = _component_name(part)
            key = normalize_medication_text(display)
            if len(key) < 4 or (index == 0 and company_words):
                continue
            counts[key] += 1
            displays.setdefault(key, Counter())[display] += 1
    return (
        counts,
        {key: values.most_common(1)[0][0] for key, values in displays.items()},
        max((len(key) for key in counts), default=0),
    )


def _suffix_match(source, counts, display_names, max_length):
    text = re.sub(r"[^a-z0-9]+", "", str(source or "").casefold())
    hits = [
        text[-length:]
        for length in range(min(max_length, len(text)), 3, -1)
        if text[-length:] in counts
    ]
    if not hits:
        return None
    frequent = [key for key in hits if counts[key] >= 5]
    pool = frequent or [key for key in hits if counts[key] >= 2] or hits
    return display_names[max(pool, key=lambda key: (len(key), counts[key]))]


def _embedded_composition(product_name):
    match = COMPOSITION_CHAIN.search(_text_value(product_name))
    return match.group() if match else None


def _repair_chain(chain, counts, display_names, max_length):
    parts = [part.strip() for part in re.split(r"\s*\+\s*", chain) if part.strip()]
    if not parts:
        return None
    match = re.match(r"(.*)\((.*)\)\s*$", parts[0], re.S)
    if not match:
        return None
    ingredient = _suffix_match(match.group(1), counts, display_names, max_length)
    if not ingredient:
        return None
    parts[0] = f"{ingredient} ({match.group(2).strip()})"
    repaired = " + ".join(parts)
    return repaired if parse_composition(repaired) else None


def repair_compositions(df):
    """Rebuild the missing first ingredient so composition matching can work."""
    counts, display_names, max_length = _ingredient_lexicon(df["Composition"])

    def repair_row(row):
        composition = row.get("Composition")
        if parse_composition(composition):
            return composition

        chain = _embedded_composition(row.get("Product_Name"))
        repaired_chain = _repair_chain(chain, counts, display_names, max_length) if chain else None
        if repaired_chain:
            return repaired_chain

        match = LEADING_STRENGTH.match(_text_value(composition))
        if not match:
            return composition
        source = chain or row.get("Type_of_Medicine") or row.get("Product_Name")
        ingredient = _suffix_match(source, counts, display_names, max_length)
        if not ingredient:
            return composition
        repaired = f"{ingredient} {match.group(1)}{match.group(2)}".strip()
        return repaired if parse_composition(repaired) else composition

    repaired_count = 0
    result = []
    for _, row in df.iterrows():
        before = row.get("Composition")
        after = repair_row(row)
        if after != before and parse_composition(after):
            repaired_count += 1
        result.append(after)

    print(f"✓ Compositions repaired : {repaired_count:,}")
    return pd.Series(result, index=df.index)


def extract_fields(df):
    df["Composition"] = repair_compositions(df)
    df["Brand_Name"]=df["Medicine_Name"].apply(extract_brand_name)
    df["Strength"]=df["Medicine_Name"].apply(extract_strength_from_name)
    m=df["Strength"].isna()
    df.loc[m,"Strength"]=df.loc[m,"Composition"].apply(extract_strength_from_composition)
    df["Dosage_Form"]=df["Medicine_Name"].apply(extract_dosage_form)
    df["Medicine_Category"]=df["Dosage_Form"].apply(extract_category)
    df["Release_Type"]=df["Medicine_Name"].apply(extract_release_type)
    type_column = df["Type_of_Medicine"] if "Type_of_Medicine" in df else pd.Series([None] * len(df), index=df.index)
    product_column = df["Product_Name"] if "Product_Name" in df else pd.Series([None] * len(df), index=df.index)
    df["Pack_Size"] = [
        extract_pack_size(type_value, product_value, name)
        for type_value, product_value, name in zip(type_column, product_column, df["Medicine_Name"])
    ]
    df["Composition_Complete"] = df["Composition"].apply(lambda value: bool(parse_composition(value)))
    return df
