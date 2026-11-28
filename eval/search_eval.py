r"""Search accuracy eval over the local catalog (no API fallback, nothing is written).

    venv\Scripts\python.exe -m eval.search_eval             # original 72-query set
    venv\Scripts\python.exe -m eval.search_eval heldout     # held-out set
    venv\Scripts\python.exe -m eval.search_eval all out.json

A hit is judged on the returned rows' names and compositions, never on ids, so
the check survives a rebuild. Brand checks need the brand at a word start in
the product name (plus the strength, if one is asked); generic checks need a
single-ingredient product of that ingredient (plus the strength).
"""
import json
import re
import sys
import time

from backend.services import medicine_service

# (query, category, check)  check: ("brand", [prefixes], strength_or_None) | ("ing", ingredient, strength_or_None)
# Original set, written 2026-11-26 for the "Measure search and price accuracy" thread.
CASES = [
    # --- exact brand ---
    ("Crocin", "brand", ("brand", ["crocin"], None)),
    ("Calpol", "brand", ("brand", ["calpol"], None)),
    ("Combiflam", "brand", ("brand", ["combiflam"], None)),
    ("Allegra", "brand", ("brand", ["allegra"], None)),
    ("Montair LC", "brand", ("brand", ["montair lc"], None)),
    ("Shelcal", "brand", ("brand", ["shelcal"], None)),
    ("Becosules", "brand", ("brand", ["becosules"], None)),
    ("Zincovit", "brand", ("brand", ["zincovit"], None)),
    ("Glycomet", "brand", ("brand", ["glycomet"], None)),
    ("Ecosprin", "brand", ("brand", ["ecosprin"], None)),
    ("Voveran", "brand", ("brand", ["voveran"], None)),
    ("Digene", "brand", ("brand", ["digene"], None)),
    ("Cyclopam", "brand", ("brand", ["cyclopam"], None)),
    ("Ondem", "brand", ("brand", ["ondem"], None)),
    ("Sinarest", "brand", ("brand", ["sinarest"], None)),
    ("Ascoril", "brand", ("brand", ["ascoril"], None)),
    ("Liv 52", "brand", ("brand", ["liv 52", "liv.52", "liv-52", "liv52"], None)),
    ("Neurobion Forte", "brand", ("brand", ["neurobion forte"], None)),
    ("Metrogyl", "brand", ("brand", ["metrogyl"], None)),
    ("Electral", "brand", ("brand", ["electral"], None)),
    ("Saridon", "brand", ("brand", ["saridon"], None)),
    ("Meftal Spas", "brand", ("brand", ["meftal-spas", "meftal spas"], None)),
    ("Zerodol", "brand", ("brand", ["zerodol"], None)),
    ("Rablet", "brand", ("brand", ["rablet"], None)),
    ("Thyronorm", "brand", ("brand", ["thyronorm"], None)),
    # --- brand + strength ---
    ("Dolo 650", "brand+strength", ("brand", ["dolo"], "650")),
    ("Augmentin 625", "brand+strength", ("brand", ["augmentin"], "625")),
    ("Azithral 500", "brand+strength", ("brand", ["azithral"], "500")),
    ("Pan 40", "brand+strength", ("brand", ["pan "], "40")),
    ("Telma 40", "brand+strength", ("brand", ["telma"], "40")),
    ("Amlong 5", "brand+strength", ("brand", ["amlong"], "5")),
    ("Atorva 10", "brand+strength", ("brand", ["atorva"], "10")),
    ("Glycomet 500", "brand+strength", ("brand", ["glycomet"], "500")),
    ("Allegra 120", "brand+strength", ("brand", ["allegra"], "120")),
    ("Dolo650", "brand+strength (no space)", ("brand", ["dolo"], "650")),
    ("pan40", "brand+strength (no space)", ("brand", ["pan "], "40")),
    ("Telma-40", "brand+strength (hyphen)", ("brand", ["telma"], "40")),
    # --- generic / salt ---
    ("Paracetamol", "generic", ("ing", "paracetamol", None)),
    ("Metformin", "generic", ("ing", "metformin", None)),
    ("Amoxicillin", "generic", ("ing", "amoxicillin", None)),
    ("Azithromycin", "generic", ("ing", "azithromycin", None)),
    ("Pantoprazole", "generic", ("ing", "pantoprazole", None)),
    ("Cetirizine", "generic", ("ing", "cetirizine", None)),
    ("Atorvastatin", "generic", ("ing", "atorvastatin", None)),
    ("Amlodipine", "generic", ("ing", "amlodipine", None)),
    ("Ibuprofen", "generic", ("ing", "ibuprofen", None)),
    ("Ondansetron", "generic", ("ing", "ondansetron", None)),
    ("Metronidazole", "generic", ("ing", "metronidazole", None)),
    ("Omeprazole", "generic", ("ing", "omeprazole", None)),
    # --- generic + strength ---
    ("Paracetamol 500", "generic+strength", ("ing", "paracetamol", "500")),
    ("Metformin 500mg", "generic+strength", ("ing", "metformin", "500")),
    ("Amlodipine 5mg", "generic+strength", ("ing", "amlodipine", "5")),
    ("Atorvastatin 10", "generic+strength", ("ing", "atorvastatin", "10")),
    ("Azithromycin 500", "generic+strength", ("ing", "azithromycin", "500")),
    # --- misspellings ---
    ("Paracetmol", "misspelling", ("ing", "paracetamol", None)),
    ("Dollo 650", "misspelling", ("brand", ["dolo"], "650")),
    ("Augmentine", "misspelling", ("brand", ["augmentin"], None)),
    ("Azithromicin", "misspelling", ("ing", "azithromycin", None)),
    ("Pantaprazole", "misspelling", ("ing", "pantoprazole", None)),
    ("Cetrizine", "misspelling", ("ing", "cetirizine", None)),
    ("Crocine", "misspelling", ("brand", ["crocin"], None)),
    ("Combiflem", "misspelling", ("brand", ["combiflam"], None)),
    ("Metformine", "misspelling", ("ing", "metformin", None)),
    ("Amoxycillin", "spelling variant", ("ing", "amoxicillin", None)),
    # --- partial ---
    ("Dol", "partial", ("brand", ["dolo"], None)),
    ("Augm", "partial", ("brand", ["augmentin"], None)),
    ("Azith", "partial", ("brand", ["azith"], None)),
    ("Combi", "partial", ("brand", ["combiflam"], None)),
    ("Montai", "partial", ("brand", ["montair"], None)),
    ("Shelc", "partial", ("brand", ["shelcal"], None)),
    # --- case ---
    ("DOLO 650", "uppercase", ("brand", ["dolo"], "650")),
    ("crocin advance", "lowercase multiword", ("brand", ["crocin advance"], None)),
]


# Held-out set, written 2026-11-27 before any search change was made, from
# common Indian brands and generics that are not in CASES. Do not tune on it.
HELDOUT = [
    ("Pantocid", "brand", ("brand", ["pantocid"], None)),
    ("Omez", "brand", ("brand", ["omez"], None)),
    ("Razo", "brand", ("brand", ["razo"], None)),
    ("Ciplox", "brand", ("brand", ["ciplox"], None)),
    ("Zifi", "brand", ("brand", ["zifi"], None)),
    ("Levocet", "brand", ("brand", ["levocet"], None)),
    ("Avil", "brand", ("brand", ["avil"], None)),
    ("Brufen", "brand", ("brand", ["brufen"], None)),
    ("Hifenac", "brand", ("brand", ["hifenac"], None)),
    ("Lasix", "brand", ("brand", ["lasix"], None)),
    ("Storvas", "brand", ("brand", ["storvas"], None)),
    ("Amaryl", "brand", ("brand", ["amaryl"], None)),
    ("Folvite", "brand", ("brand", ["folvite"], None)),
    ("Domstal", "brand", ("brand", ["domstal"], None)),
    ("Pantocid 40", "brand+strength", ("brand", ["pantocid"], "40")),
    ("Omez 20", "brand+strength", ("brand", ["omez"], "20")),
    ("Zifi 200", "brand+strength", ("brand", ["zifi"], "200")),
    ("Brufen 400", "brand+strength", ("brand", ["brufen"], "400")),
    ("Lasix 40", "brand+strength", ("brand", ["lasix"], "40")),
    ("Storvas 10", "brand+strength", ("brand", ["storvas"], "10")),
    ("Amaryl 1", "brand+strength", ("brand", ["amaryl"], "1")),
    ("Stamlo 5", "brand+strength", ("brand", ["stamlo"], "5")),
    ("Omez20", "brand+strength (no space)", ("brand", ["omez"], "20")),
    ("Ciplox500", "brand+strength (no space)", ("brand", ["ciplox"], "500")),
    ("Zifi-200", "brand+strength (hyphen)", ("brand", ["zifi"], "200")),
    ("Levocetirizine", "generic", ("ing", "levocetirizine", None)),
    ("Ciprofloxacin", "generic", ("ing", "ciprofloxacin", None)),
    ("Cefixime", "generic", ("ing", "cefixime", None)),
    ("Furosemide", "generic", ("ing", "furosemide", None)),
    ("Glimepiride", "generic", ("ing", "glimepiride", None)),
    ("Rosuvastatin", "generic", ("ing", "rosuvastatin", None)),
    ("Domperidone", "generic", ("ing", "domperidone", None)),
    ("Diclofenac", "generic", ("ing", "diclofenac", None)),
    ("Cefixime 200", "generic+strength", ("ing", "cefixime", "200")),
    ("Rosuvastatin 10mg", "generic+strength", ("ing", "rosuvastatin", "10")),
    ("Glimepiride 2", "generic+strength", ("ing", "glimepiride", "2")),
    ("Ciprofloxacin 500 mg", "generic+strength", ("ing", "ciprofloxacin", "500")),
    ("Levocetrizine", "misspelling", ("ing", "levocetirizine", None)),
    ("Ciprofloxacine", "misspelling", ("ing", "ciprofloxacin", None)),
    ("Cefixim", "misspelling", ("ing", "cefixime", None)),
    ("Pantocyd", "misspelling", ("brand", ["pantocid"], None)),
    ("Omezz", "misspelling", ("brand", ["omez"], None)),
    ("Domperidon", "misspelling", ("ing", "domperidone", None)),
    ("Rosuvastatine", "misspelling", ("ing", "rosuvastatin", None)),
    ("Brufin", "misspelling", ("brand", ["brufen"], None)),
    ("Glimiperide", "misspelling", ("ing", "glimepiride", None)),
    ("Pantoc", "partial", ("brand", ["pantocid"], None)),
    ("Storv", "partial", ("brand", ["storvas"], None)),
    ("Hifen", "partial", ("brand", ["hifenac"], None)),
    ("LASIX", "uppercase", ("brand", ["lasix"], None)),
    ("omez d", "lowercase multiword", ("brand", ["omez d", "omez-d"], None)),
]


def is_hit(r, check):
    name = (r.get("medicine_name") or "").lower()
    strength = (r.get("strength") or "").lower()
    if check[0] == "brand":
        name = name.replace("-", " ")  # "Montair-LC" is "Montair LC" (rule added 2026-11-27)
        _, prefixes, st = check
        if not any(re.search(r"(?:^|\s)" + re.escape(p), name) for p in prefixes):
            return False
        if st and not re.search(rf"(?<!\d){st}(?!\d)", name + " " + strength):
            return False
        return True
    _, ing, st = check
    comp = (r.get("composition") or "").lower()
    text = comp or name
    syn = {"paracetamol": ["acetaminophen"], "amoxicillin": ["amoxycillin"]}.get(ing, [])
    if ing not in text and not any(x in text for x in syn):
        return False
    if "+" in comp:  # must be the single ingredient the user asked for
        return False
    if st and not re.search(rf"(?<!\d){st}(?!\d)", strength + " " + comp + " " + name):
        return False
    return True


def run(cases, label):
    detailed = getattr(medicine_service, "search_medicines_detailed", None)
    out = []
    for q, cat, check in cases:
        started = time.time()
        corrected = None
        if detailed:
            result = detailed(q, enable_api_fallback=False, allow_correction=True)
            rows, corrected = result["rows"], result.get("corrected_query")
        else:
            rows = medicine_service.search_medicines(q, enable_api_fallback=False)
        rows = [dict(r) for r in rows]
        seconds = time.time() - started
        hits = [is_hit(r, check) for r in rows[:3]]
        out.append({
            "set": label, "q": q, "cat": cat.split(" (")[0], "n": len(rows),
            "top1": bool(hits[:1] and hits[0]), "top3": any(hits), "secs": round(seconds, 2),
            "corrected": corrected,
            "top": [f"{r['medicine_name']} | {r.get('strength')} | {(r.get('composition') or '')[:50]}"
                    for r in rows[:3]],
        })
        o = out[-1]
        flag = "T1" if o["top1"] else ("T3" if o["top3"] else "--")
        fix = f" (as {corrected!r})" if corrected else ""
        print(f"{flag} {seconds:5.2f}s {q!r:24} n={len(rows):2}{fix} -> {o['top'][:1]}", flush=True)
    n = len(out)
    t1, t3 = sum(o["top1"] for o in out), sum(o["top3"] for o in out)
    print(f"\n[{label}] N={n} top1={t1} ({100 * t1 / n:.1f}%) top3={t3} ({100 * t3 / n:.1f}%)")
    cats = {}
    for o in out:
        c = cats.setdefault(o["cat"], [0, 0, 0])
        c[0] += 1
        c[1] += o["top1"]
        c[2] += o["top3"]
    for k, v in cats.items():
        print(f"  {k:22} n={v[0]:2} top1={v[1]} top3={v[2]}")
    return out


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "original"
    results = []
    if which in ("original", "all"):
        results += run(CASES, "original")
    if which in ("heldout", "all"):
        results += run(HELDOUT, "heldout")
    if len(sys.argv) > 2:
        with open(sys.argv[2], "w", encoding="utf-8") as handle:
            json.dump(results, handle, indent=1)
