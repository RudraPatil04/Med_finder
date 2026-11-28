"""
Optional Gemini answers for the AI Assistant, grounded in MED FINDER's own catalog.

Off by default. Turn it on with MEDFINDER_AI_ASSISTANT=1 (and GEMINI_API_KEY set,
the same free AI Studio key the prescription scanner uses). The rule-based answer
in assistant_service is used whenever this is off, no medicine is named, the key
is missing, the quota is used up, Gemini is slow (AI_TIMEOUT_SECONDS in total) or
anything else goes wrong.

Gemini only sees the catalog facts collected here for the medicine named in the
question, and is told to answer only from them. Answers are cached per question
and medicine, so a repeated demo question is instant and uses no quota.
"""

import json
import os
import re
import threading
import time
from collections import OrderedDict

import requests

from backend import ocr_service

AI_FLAG = "MEDFINDER_AI_ASSISTANT"
AI_TIMEOUT_SECONDS = 6.0
CACHE_SIZE = 256
MAX_OPTIONS = 3
# The light model answers a short grounded question in about a second on the
# free tier; the larger ones often take longer than the timeout. Override with
# GEMINI_ASSISTANT_MODEL.
ASSISTANT_MODELS = ("gemini-flash-lite-latest", "gemini-flash-latest")

SYSTEM_PROMPT = """You are the MED FINDER assistant, a medicine information helper for an Indian medicine catalog.
Answer the user's question using ONLY the JSON catalog data provided. If the data does not answer it, say you do not have that information and suggest asking a pharmacist.
Rules you must always follow:
- Never say two products are identical, bioequivalent, interchangeable or the same medicine. Products with the same recorded composition still need a doctor or pharmacist to confirm a switch.
- Never give doses, dosing schedules, diagnoses, pregnancy or breastfeeding advice, or drug-interaction advice. Say a doctor or pharmacist should advise on those.
- Never invent prices, savings, ratings or facts. Quote prices exactly as given, in Rs., and say when a price is marked as not current. Only mention a saving if the data gives one, and call an estimated saving an estimate.
- End by telling the user to confirm with a doctor or pharmacist.
- Keep it under 120 words. Plain text only. You may use **bold** and line breaks. To link a product, use exactly the markdown link given in its "link" field, for example [Dolo 650 Tablet](/medicine/7087). Use no other links."""

# Phrases an answer must never contain; such an answer is discarded for the rules.
FORBIDDEN = re.compile(r"\b(identical|bioequivalen\w*|same medicine|exactly the same|"
                       r"guaranteed|100% safe|twice daily|once daily|thrice daily|"
                       r"\d+\s*times? (?:a|per) day|every \d+\s*hours)\b", re.I)
INTERCHANGEABLE = re.compile(r"\binterchangeabl\w*|\bsubstitutable\b", re.I)

_cache = OrderedDict()
_cache_lock = threading.Lock()


def ai_enabled():
    """True when the switch is on and a Gemini key is set in this environment."""
    if os.environ.get(AI_FLAG, "").strip().lower() not in ("1", "true", "yes", "on"):
        return False
    return bool(ocr_service._gemini_keys())


def _cache_key(question, medicine_id):
    words = re.findall(r"[a-z0-9]+", (question or "").lower())
    return (" ".join(words), medicine_id)


def cache_get(question, medicine_id):
    with _cache_lock:
        key = _cache_key(question, medicine_id)
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    return None


def cache_put(question, medicine_id, answer):
    with _cache_lock:
        _cache[_cache_key(question, medicine_id)] = answer
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)


def clear_cache():
    with _cache_lock:
        _cache.clear()


def _link(name, medicine_id):
    return f"[{name}](/medicine/{int(medicine_id)})" if medicine_id is not None else name


def build_context(medicine):
    """Catalog facts for one medicine: composition, price, same-composition options
    and Jan Aushadhi list prices. Reads the local database only; the alternatives
    lookup runs only for rows that will not trigger a live enrichment call."""
    context = {
        "medicine": {
            "name": medicine.get("medicine_name"),
            "link": _link(medicine.get("medicine_name"), medicine.get("id")),
            "composition": medicine.get("composition") or "not recorded",
            "strength": medicine.get("strength"),
            "dosage_form": medicine.get("dosage_form"),
            "release_type": medicine.get("release_type"),
            "pack_size": medicine.get("pack_size"),
            "catalog_price_rs": medicine.get("price"),
            "price_is_current": False,
            "prescription_required": str(medicine.get("prescription_required", "")).lower()
            in ("true", "1", "yes"),
        },
    }
    complete = bool(medicine.get("composition_complete"))
    priced = float(medicine.get("price") or 0) > 0
    if not (complete and priced and medicine.get("brand_name") and medicine.get("id") is not None):
        context["alternatives_note"] = "Same-composition options are not available for this record."
        return context

    from backend.recommendation_service import _fresh_price, get_substitutes

    context["medicine"]["price_is_current"] = _fresh_price(medicine.get("price_checked_at"))
    result = get_substitutes(medicine["id"], limit=MAX_OPTIONS)
    context["comparison_status"] = result.get("comparison_status")
    context["comparison_message"] = result.get("message")

    options = []
    for item in (result.get("same_composition_alternatives") or []) + (result.get("same_composition_options") or []):
        if len(options) >= MAX_OPTIONS:
            break
        option = {
            "name": item.get("medicine_name"),
            "link": _link(item.get("medicine_name"), item.get("id")),
            "composition": item.get("composition"),
            "pack_size": item.get("pack_size"),
            "catalog_price_rs": item.get("price"),
            "price_is_current": bool(item.get("price_is_current")),
        }
        if item.get("savings_percent") is not None:
            option["verified_saving_percent"] = item["savings_percent"]
        elif item.get("estimated_savings_percent") is not None:
            option["estimated_saving_percent_from_catalog_prices"] = item["estimated_savings_percent"]
        options.append(option)
    context["same_composition_options"] = options

    jan = []
    for row in (result.get("reference_price_options") or [])[:2]:
        jan.append({
            "source": "Jan Aushadhi (PMBJP) list price",
            "product": row.get("product_name"),
            "pack": row.get("pack"),
            "price_rs": row.get("price"),
            "price_is_current": bool(row.get("price_is_current")),
        })
    if jan:
        context["jan_aushadhi_prices"] = jan
    return context


def _assistant_models():
    preferred = os.environ.get("GEMINI_ASSISTANT_MODEL", "").strip()
    models = [preferred] if preferred else []
    return models + [m for m in ASSISTANT_MODELS if m != preferred]


def _gemini_generate(question, context, deadline):
    """One answer from Gemini, trying each key and model until the deadline."""
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text":
            "Catalog data:\n" + json.dumps(context, ensure_ascii=False, default=str)
            + "\n\nUser question: " + question}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 400},
    }
    failures = []
    for name, key in ocr_service._gemini_keys():
        quota_hit = False
        for model in _assistant_models():
            remaining = deadline - time.monotonic()
            if remaining <= 0.3:
                raise TimeoutError("Gemini took too long (" + "; ".join(failures or ["no answer"]) + ")")
            try:
                response = requests.post(
                    ocr_service.GEMINI_URL.format(model=model),
                    headers={"x-goog-api-key": key},
                    json=payload,
                    timeout=remaining,
                )
            except requests.RequestException as error:
                failures.append(f"{model}: {type(error).__name__}")
                continue
            if response.status_code == 200:
                data = response.json()
                parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
                text = "\n".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
                if text:
                    ocr_service._gemini_rest_until.pop(name, None)
                    return text
                failures.append(f"{model}: empty answer")
                continue
            failures.append(f"{model}: {response.status_code}")
            if response.status_code == 429:
                quota_hit = True
            if response.status_code not in ocr_service.GEMINI_RETRY_NEXT_MODEL:
                break  # this key is rejected or the request is bad
        if quota_hit:
            ocr_service._gemini_rest_until[name] = time.monotonic() + ocr_service.GEMINI_KEY_COOLDOWN_SECONDS
    raise RuntimeError("Gemini returned no answer (" + "; ".join(failures or ["no key"]) + ")")


def _acceptable(text):
    if FORBIDDEN.search(text):
        return False
    for match in INTERCHANGEABLE.finditer(text):
        before = text[max(0, match.start() - 30):match.start()].lower()
        if not re.search(r"\b(not|never|n't|cannot)\b", before):
            return False
    return True


def ai_answer(question, medicine, disclaimer):
    """Gemini's answer about `medicine`, or None so the caller uses the rules."""
    medicine_id = medicine.get("id")
    cached = cache_get(question, medicine_id)
    if cached:
        return cached
    deadline = time.monotonic() + AI_TIMEOUT_SECONDS
    try:
        context = build_context(medicine)
        text = _gemini_generate(question, context, deadline)
    except Exception as error:
        print(f"AI assistant: using rule-based answer ({error})")
        return None
    text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text).strip()
    if not _acceptable(text):
        print("AI assistant: answer broke a safety rule; using rule-based answer.")
        return None
    answer = text + "\n\n*Written by Gemini AI from MED FINDER catalog data.*" + disclaimer
    cache_put(question, medicine_id, answer)
    return answer
