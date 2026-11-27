r"""Accuracy eval for the "cheaper same-composition alternatives" list (offline, read-only).

    venv\Scripts\python.exe -m eval.alternatives_eval            # summary table
    venv\Scripts\python.exe -m eval.alternatives_eval out.json   # plus per-case detail
    venv\Scripts\python.exe -m eval.alternatives_eval sample 40  # held-out: 40 hash-picked products

What is scored is what the medicine page shows: `/api/substitutes/<id>?limit=10` with live
lookups off (`same_composition_alternatives` + `same_composition_options` +
`unverified_matches`). Enrichment calls are stubbed, so nothing is fetched or written.

The checker does not reuse the app's matching. It re-parses each product's raw `composition`
text ("Name (strength) + ..."), converts units (g/mg/mcg), and reads form variants and
release markers from the product name:

- correct: same ingredient names and strengths, same dosage form and name-level form variant
  (dispersible, mouth-dissolving, vaginal, chewable, effervescent), no modified-release marker.
- salt label only: names differ only by a salt word ("Chlorpheniramine" vs "... Maleate").
  Counted as correct, reported separately.
- wrong: anything else (different ingredient, strength, form, variant or release).

Recall is measured against the catalog itself: every product whose raw composition text,
dosage form and release agree by the checker's rules, and which carries a price and a
parseable pack size (the app cannot compare a product without them). Known brand checks list
well-known same-composition brands by hand; one counts only when the catalog holds it with a
matching recorded composition.
"""
import json
import re
import sys
from collections import Counter

from backend import recommendation_service as rs
from backend.database import execute_query
from backend.medicine_data import is_modified_release, pack_quantity

# (exact medicine_name, category, expected, known same-composition brand prefixes)
# expected: "compare" = should list alternatives, "restricted" = must list none,
# "no_data" = composition missing in the catalog, nothing can be matched.
CASES = [
    # --- common single-ingredient tablets / capsules ---
    ("Dolo 650 Tablet", "single", "compare", ["crocin 650", "calpol 650", "pacimol 650"]),
    ("CROcin Advance 500mg Tablet", "single", "compare", ["calpol 500", "paracip 500", "pacimol 500"]),
    ("Calpol 650mg Tablet", "single", "compare", ["dolo 650", "crocin 650"]),
    ("PAN 40 Tablet", "single", "compare", ["pantocid 40", "pantop 40", "pantodac 40"]),
    ("Telma 40 Tablet", "single", "compare", ["telmikind 40", "telsartan 40", "telvas 40"]),
    ("Azithral 500 Tablet", "single", "compare", ["azee 500", "zithrox 500", "azax 500"]),
    ("Allegra 120mg Tablet", "single", "compare", ["fexova 120", "altiva 120"]),
    ("Ecosprin 75 Tablet", "single", "compare", ["loprin 75", "delisprin 75"]),
    ("Zerodol Tablet", "single", "compare", ["hifenac", "aceclo 100", "acebid"]),
    ("rABlet 20 Tablet", "single", "compare", ["razo 20", "rabicip 20", "happi 20"]),
    ("Ondem 4 Tablet", "single", "compare", ["emeset 4 tablet", "vomikind 4"]),
    ("Cetzine Tablet", "single", "compare", ["alerid", "okacet", "cetcip"]),
    ("Omez Capsule", "single", "compare", ["ocid 20", "omee"]),
    ("Mox 500mg Capsule", "single", "compare", ["novamox 500"]),
    ("Mox 250mg Capsule", "single", "compare", ["novamox 250"]),
    ("MEtrogyl 400 Tablet", "single", "compare", ["flagyl 400", "metron 400"]),
    ("Brufen 400 Tablet", "single", "compare", ["ibugesic 400"]),
    ("Montair 10 Tablet", "single", "compare", ["montek 10", "romilast 10"]),
    ("Taxim-O 200 Tablet", "single", "compare", ["zifi 200", "mahacef 200"]),
    ("LEVoflox 500 Tablet", "single", "compare", ["glevo 500", "levomac 500"]),
    ("Losar 50 Tablet", "single", "compare", ["repace 50", "losacar 50"]),
    ("Aciloc 150 Tablet", "single", "compare", ["rantac 150", "zinetac 150"]),
    ("Atorva 20 Tablet", "single", "compare", ["storvas 20", "lipicure 20", "atorlip 20"]),
    ("cipLOX 500 Tablet", "single", "compare", ["cifran 500"]),
    # --- combinations ---
    ("Augmentin 625 Duo Tablet", "combination", "compare", ["clavam 625", "moxikind-cv 625"]),
    ("Combiflam Tablet", "combination", "compare", ["ibugesic plus"]),
    ("Telma H Tablet", "combination", "compare", ["telmikind-h", "telsartan h"]),
    ("Montek LC Tablet", "combination", "compare", ["montair lc tablet", "l-montus"]),
    ("O2 Tablet", "combination", "compare", ["oflox oz", "zenflox oz"]),
    ("Sinarest AF Tablet", "combination", "compare", []),
    ("Telma-AM H 40 Tablet", "combination", "compare", []),
    ("Becosules Capsule", "combination", "compare", []),
    # --- liquids, drops, other forms ---
    ("Calpol 120mg Suspension Strawberry", "liquid", "compare", []),
    ("Calpol 250mg Paediatric Oral Suspension Strawberry", "liquid", "compare", []),
    ("Combiflam Suspension", "liquid", "compare", ["ibugesic plus suspension"]),
    ("Meftal-Spas Suspension", "liquid", "compare", ["cyclopam suspension"]),
    ("Ascoril LS Syrup", "liquid", "compare", []),
    ("Benadryl Syrup", "liquid", "compare", []),
    ("Febrex Plus Syrup", "liquid", "compare", []),
    ("Amoxyclav Dry Syrup", "liquid", "compare", []),
    ("Azithral 200 Liquid", "liquid", "compare", ["azee 200"]),
    ("Cetzine Oral Drops", "liquid", "compare", []),
    ("Electral Sachet", "other form", "compare", []),
    # --- form variants (dispersible, mouth-dissolving, vaginal) ---
    ("Montair LC Kid Tablet DT", "form variant", "compare", []),
    ("Emeset 4 ODT Tablet", "form variant", "compare", []),
    ("CANDID-V 100 MG TABLET", "form variant", "compare", []),
    # --- must be blocked: injection, inhaler, modified release, NTI ---
    ("Monocef 1 g Injection", "restricted", "restricted", []),
    ("ASTHAlin 100mcg Inhaler", "restricted", "restricted", []),
    ("Glycomet 500 SR Tablet", "restricted", "restricted", []),
    ("Pan-D Capsule PR", "restricted", "restricted", []),
    ("Glycomet-GP 1 Tablet PR", "restricted", "restricted", []),
    ("Amlong MT 25 Tablet PR", "restricted", "restricted", []),
    ("THYronorm 50mcg Tablet", "restricted", "restricted", []),
    ("Deriphyllin Retard 150  Tablet PR", "restricted", "restricted", []),
    # --- composition missing (incl. the four brands added 2026-11-27 without ingredients) ---
    ("Zincovit Tablet", "missing composition", "no_data", []),
    ("Digene Mint Flavour Tablet", "missing composition", "no_data", []),
    ("Neurobion Forte Tablet", "missing composition", "no_data", []),
    ("BEtnovate Cream", "missing composition", "no_data", []),
    ("Moxicip Eye Drop", "missing composition", "no_data", []),
]

SALT_WORDS = {
    "hydrochloride", "hcl", "maleate", "sodium", "potassium", "calcium", "magnesium",
    "succinate", "tartrate", "besylate", "besilate", "mesylate", "citrate", "sulphate",
    "sulfate", "phosphate", "acetate", "fumarate", "bromide", "dihydrate", "trihydrate",
    "monohydrate", "anhydrous", "hyclate", "disodium", "dipropionate", "valerate",
}
SPELLING = {"amoxycillin": "amoxicillin", "cephalexin": "cefalexin", "sulphate": "sulfate"}
FORM_VARIANTS = (
    (re.compile(r"\b(?:dt|dispersible)\b", re.I), "dispersible"),
    (re.compile(r"\b(?:md|odt|mouth\s+dissolving|orally\s+disintegrating)\b", re.I), "orodispersible"),
    (re.compile(r"\b(?:vt|vaginal|pessary)\b", re.I), "vaginal"),
    (re.compile(r"\bchewable\b", re.I), "chewable"),
    (re.compile(r"\beffervescent\b", re.I), "effervescent"),
    (re.compile(r"\bsublingual\b", re.I), "sublingual"),
)
RELEASE_MARK = re.compile(r"\b(?:sr|er|xr|cr|mr|xl|pr|dr|ec|retard)\b", re.I)
PART = re.compile(r"^\s*(.+?)\s*\(([^)]*)\)\s*$")
AMOUNT = re.compile(r"([\d.]+)\s*(mg|mcg|µg|g|iu|ml|%|% ?w/w|% ?w/v)?", re.I)


def _name(text, strip_salts=False):
    words = re.sub(r"[^a-z0-9]+", " ", str(text or "").casefold()).split()
    words = [SPELLING.get(w, w) for w in words]
    if strip_salts:
        words = [w for w in words if w not in SALT_WORDS] or words
    return " ".join(words)


def _strength(text):
    """'650mg' -> '650mg', '0.65 g' -> '650mg', '120mg/5ml' -> '120mg/5ml'."""
    out = []
    for piece in str(text or "").casefold().replace(" ", "").split("/"):
        m = AMOUNT.fullmatch(piece) or AMOUNT.match(piece)
        if not m:
            out.append(piece)
            continue
        value, unit = float(m.group(1)), (m.group(2) or "").replace("µg", "mcg")
        if unit == "g":
            value, unit = value * 1000, "mg"
        elif unit == "mcg":
            value, unit = value / 1000, "mg"
        out.append(("%g" % round(value, 6)) + unit)
    return "/".join(out)


def parse_composition(text, strip_salts=False):
    if not text:
        return None
    parts = []
    for raw in re.split(r"\s\+\s", str(text)):
        m = PART.match(raw)
        if not m:
            return None
        parts.append((_name(m.group(1), strip_salts), _strength(m.group(2))))
    return tuple(sorted(parts)) or None


def form_variant(name):
    for pattern, label in FORM_VARIANTS:
        if pattern.search(str(name or "")):
            return label
    return None


def judge(ref, cand):
    """Return (verdict, reason) for one shown candidate against the reference product."""
    strict_ref, strict_cand = parse_composition(ref["composition"]), parse_composition(cand.get("composition"))
    if not strict_cand:
        return "wrong", "candidate composition missing/unparseable"
    if (ref.get("dosage_form") or "").casefold() != (cand.get("dosage_form") or "").casefold():
        return "wrong", "dosage form differs"
    if form_variant(ref["medicine_name"]) != form_variant(cand.get("medicine_name")):
        return "wrong", "form variant differs (%s vs %s)" % (
            form_variant(ref["medicine_name"]), form_variant(cand.get("medicine_name")))
    if bool(RELEASE_MARK.search(ref["medicine_name"] or "")) != bool(RELEASE_MARK.search(cand.get("medicine_name") or "")) \
            or (ref.get("release_type") or "") != (cand.get("release_type") or ""):
        return "wrong", "release type differs"
    if strict_ref == strict_cand:
        return "correct", ""
    loose_ref, loose_cand = parse_composition(ref["composition"], True), parse_composition(cand.get("composition"), True)
    if loose_ref == loose_cand:
        return "salt_label", "names differ only by a salt word"
    ref_names = {n for n, _ in loose_ref or ()}
    cand_names = {n for n, _ in loose_cand or ()}
    if ref_names == cand_names:
        return "wrong", "strength differs"
    return "wrong", "ingredient differs"


def comparable(row):
    return float(row.get("price") or 0) > 0 and pack_quantity(row.get("pack_size")) is not None


def catalog_truth(ref):
    """All catalog products the checker accepts as same composition/form/release."""
    comp = parse_composition(ref["composition"])
    if not comp:
        return []
    first = re.split(r"\s\+\s", ref["composition"])[0]
    first_name = PART.match(first).group(1).split()[0]
    rows = execute_query(
        "SELECT * FROM medicines WHERE id != ? AND dosage_form = ? COLLATE NOCASE "
        "AND composition LIKE ?",
        (ref["id"], ref["dosage_form"] or "", "%" + first_name + "%"),
    )
    truth = []
    for row in rows:
        row = dict(row)
        if parse_composition(row["composition"]) != comp:
            continue
        if form_variant(row["medicine_name"]) != form_variant(ref["medicine_name"]):
            continue
        if is_modified_release(row.get("release_type"), row.get("medicine_name"), row.get("brand_name")):
            continue
        if RELEASE_MARK.search(row["medicine_name"] or ""):
            continue
        if (row.get("release_type") or "") != (ref.get("release_type") or ""):
            continue
        truth.append(row)
    return truth


def system_full_ids(ref):
    """Every product the app would place in alternatives/options, before the display cap."""
    medicine = dict(ref)
    signature = rs._ingredient_signature(ref["id"])
    if rs.restricted_reason(medicine, [n for n, _ in signature]):
        return set()
    if not (medicine.get("composition_complete") and signature and medicine.get("strength")
            and medicine.get("dosage_form") and medicine.get("product_key")):
        return set()
    ids = set()
    release = str(medicine.get("release_type") or "").casefold()
    for item in rs._candidate_rows(medicine, signature, ref["id"], require_pack_match=False):
        if float(item.get("price") or 0) <= 0:
            continue
        if str(item.get("release_type") or "").casefold() != release:
            continue
        if not rs._price_per_unit(item.get("price"), item.get("pack_size")):
            continue
        ids.add(item["id"])
    return ids


def miss_cause(ref, row, ref_signature):
    if not row.get("composition_complete"):
        return "candidate marked composition incomplete"
    sig = rs._ingredient_signature(row["id"])
    if sig != ref_signature:
        return "ingredient rows differ (normalization)"
    if (row.get("strength") or "").casefold() != (ref.get("strength") or "").casefold():
        return "strength column formatted differently"
    return "other"


def find_reference(name):
    rows = execute_query(
        "SELECT * FROM medicines WHERE medicine_name = ? "
        "ORDER BY composition_complete DESC, (price > 0) DESC, (pack_size IS NOT NULL) DESC, id LIMIT 1",
        (name,),
    )
    return dict(rows[0]) if rows else None


def known_brand_rows(ref, prefixes, truth_ids):
    out = []
    for prefix in prefixes:
        rows = [dict(r) for r in execute_query(
            "SELECT * FROM medicines WHERE lower(medicine_name) LIKE ? AND dosage_form = ? COLLATE NOCASE",
            (prefix.casefold() + "%", ref["dosage_form"] or ""),
        )]
        in_truth = [r for r in rows if r["id"] in truth_ids and comparable(r)]
        if in_truth:
            out.append((prefix, "present", [r["id"] for r in in_truth]))
        elif rows:
            same = [r for r in rows if parse_composition(r["composition"]) == parse_composition(ref["composition"])]
            out.append((prefix, "unpriced" if same else "recorded_composition_differs", [r["id"] for r in rows[:3]]))
        else:
            out.append((prefix, "not_in_catalog", []))
    return out


def run_case(name, category, expected, known):
    ref = find_reference(name)
    if ref is None:
        return {"name": name, "category": category, "expected": expected, "error": "reference not found"}
    result = rs.get_substitutes(ref["id"], limit=10)
    shown = {}
    for key in ("same_composition_alternatives", "same_composition_options", "unverified_matches"):
        for item in result.get(key) or []:
            shown.setdefault(item["id"], item)
    judged = []
    for item in shown.values():
        verdict, reason = judge(ref, item)
        judged.append({"id": item["id"], "name": item.get("medicine_name"),
                       "composition": item.get("composition"), "verdict": verdict, "reason": reason})

    truth = [r for r in catalog_truth(ref) if comparable(r)] if expected == "compare" else []
    truth_ids = {r["id"] for r in truth}
    full = system_full_ids(ref)
    found = truth_ids & full
    ref_sig = rs._ingredient_signature(ref["id"])
    misses = Counter(miss_cause(ref, r, ref_sig) for r in truth if r["id"] not in full)
    extra_full = full - truth_ids
    known_rows = known_brand_rows(ref, known, truth_ids) if expected == "compare" else []
    known_present = [k for k in known_rows if k[1] == "present"]
    known_found = [k for k in known_present if set(k[2]) & full]

    return {
        "name": name, "id": ref["id"], "category": category, "expected": expected,
        "composition": ref.get("composition"), "status": result.get("comparison_status"),
        "shown": judged, "shown_count": len(judged),
        "correct": sum(1 for j in judged if j["verdict"] in ("correct", "salt_label")),
        "salt_label": sum(1 for j in judged if j["verdict"] == "salt_label"),
        "wrong": [j for j in judged if j["verdict"] == "wrong"],
        "truth_count": len(truth_ids), "found_count": len(found),
        "full_count": len(full), "full_not_in_truth": len(extra_full),
        "miss_causes": dict(misses),
        "known": known_rows, "known_present": len(known_present), "known_found": len(known_found),
        "reference_price_options": [
            {"composition": o.get("composition"), "product": o.get("product_name")}
            for o in result.get("reference_price_options") or []
        ],
    }


def sample_cases(count):
    """Held-out set: catalog products picked by a fixed hash of the id, not by hand."""
    rows = execute_query(
        "SELECT medicine_name FROM medicines WHERE composition_complete = 1 AND price > 0 "
        "AND brand_name IS NOT NULL ORDER BY (id * 2654435761) % 1000003 LIMIT ?",
        (count,),
    )
    return [(row["medicine_name"], "sample", "compare", []) for row in rows]


def main(argv):
    rs.enrich_medicine_composition = lambda medicine: False
    rs.enrich_medicine_price = lambda medicine: False
    cases = CASES
    if len(argv) > 2 and argv[1] == "sample":
        cases, argv = sample_cases(int(argv[2])), argv[2:]
    results = [run_case(*case) for case in cases]

    compare = [r for r in results if r.get("expected") == "compare" and "error" not in r]
    shown = sum(r["shown_count"] for r in results if "error" not in r)
    correct = sum(r["correct"] for r in results if "error" not in r)
    wrong = [w | {"ref": r["name"]} for r in results if "error" not in r for w in r["wrong"]]
    truth = sum(r["truth_count"] for r in compare)
    found = sum(r["found_count"] for r in compare)
    with_truth = [r for r in compare if r["truth_count"]]
    covered = [r for r in with_truth if r["shown_count"]]
    known_present = sum(r["known_present"] for r in compare)
    known_found = sum(r["known_found"] for r in compare)
    restricted = [r for r in results if r.get("expected") == "restricted" and "error" not in r]
    blocked = [r for r in restricted if r["shown_count"] == 0]
    no_data = [r for r in results if r.get("expected") == "no_data" and "error" not in r]
    misses = Counter()
    for r in compare:
        misses.update(r["miss_causes"])

    print("cases: %d (%d compare, %d restricted, %d missing composition)"
          % (len(results), len(compare), len(restricted), len(no_data)))
    for r in results:
        if "error" in r:
            print("  !! %s: %s" % (r["name"], r["error"]))
    pct = lambda a, b: "%d/%d (%.1f%%)" % (a, b, 100.0 * a / b) if b else "%d/%d" % (a, b)
    print("precision (shown items correct):     ", pct(correct, shown))
    print("recall vs catalog (comparable rows): ", pct(found, truth))
    print("known brands found (in catalog):     ", pct(known_found, known_present))
    print("cases with >=1 alternative shown:    ", pct(len(covered), len(with_truth)),
          "(of compare cases that have a true alternative)")
    print("restricted correctly blocked:        ", pct(len(blocked), len(restricted)))
    print("missing-composition cases w/ results:", pct(sum(1 for r in no_data if r["shown_count"]), len(no_data)))
    print("miss causes:", dict(misses))
    print("wrong items:")
    for w in wrong:
        print("  - %s -> %s [%s] %s" % (w["ref"], w["name"], w["reason"], w["composition"]))
    print("restricted not blocked:", [r["name"] for r in restricted if r["shown_count"]])
    print("per case: name | status | shown/wrong | found/truth | known found/present")
    for r in results:
        if "error" in r:
            continue
        print("  %-48s %-26s %2d/%-2d %4d/%-4d %d/%d" % (
            r["name"][:48], r["status"], r["shown_count"], len(r["wrong"]),
            r["found_count"], r["truth_count"], r["known_found"], r["known_present"]))
    if len(argv) > 1:
        with open(argv[1], "w", encoding="utf-8") as handle:
            json.dump(results, handle, indent=1, default=str)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(sys.argv)
