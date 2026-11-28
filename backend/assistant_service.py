"""
============================================================
MED FINDER
AI HEALTH ASSISTANT SERVICE
============================================================

Rule-based answers. Honesty rules (see CLAUDE.md):
- never call products identical, bioequivalent or interchangeable;
- never quote savings that are not computed from stored prices;
- symptom answers give self-care and red flags only, no drug names or doses;
- always point the user to a pharmacist or doctor.

Replies use a tiny markup the UI renders safely: **bold**, newlines, and
[label](/medicine/<id>) links to this app's own detail pages.
"""

import re

from backend.services.medicine_service import search_medicines

DISCLAIMER = (
    "\n\nNote: I give general information only. Please confirm with a doctor or "
    "pharmacist before starting, stopping or switching any medicine."
)

EMERGENCY_KEYWORDS = [
    "chest pain", "heart attack", "can't breathe", "cannot breathe",
    "severe bleeding", "unconscious", "stroke", "poisoning", "overdose",
]

# Words that never name a medicine; removed before the catalog lookup so that
# "Cheaper option for Dolo 650" searches "Dolo 650", not "option".
STOP_WORDS = {
    "a", "an", "the", "is", "in", "of", "on", "to", "for", "with", "and", "or",
    "what", "whats", "when", "where", "which", "who", "how", "why", "can", "could",
    "should", "would", "do", "does", "i", "me", "my", "it", "its", "there", "any",
    "take", "give", "dose", "dosage", "side", "effect", "effects", "reaction",
    "reactions", "price", "about", "tell", "explain", "generic", "generics",
    "alternative", "alternatives", "cheap", "cheaper", "cheapest", "option",
    "options", "substitute", "substitutes", "cost", "costs", "inside", "contain",
    "contains", "composition", "ingredient", "ingredients", "salt", "medicine",
    "medicines", "tablet", "tablets", "please", "info", "information", "than",
    "vs", "long", "term", "safe", "use", "used", "uses", "hello", "hi", "hey",
    "thanks", "thank", "you", "there", "need", "want", "know", "have", "has",
    # Symptom words: a symptom question gets self-care guidance, never a product
    # whose name happens to contain the symptom (e.g. "... Headache Relief").
    "fever", "temperature", "headache", "headaches", "migraine", "cold", "colds",
    "cough", "runny", "nose", "pain", "ache", "aches", "mild", "severe", "bad",
    "pregnancy", "pregnant", "during", "while", "after", "before", "kids", "child",
}


def _query_terms(user_message):
    tokens = re.findall(r"[A-Za-z0-9]+(?:[.\-][A-Za-z0-9]+)*", user_message or "")
    return [t for t in tokens if t.lower() not in STOP_WORDS]


def find_medicines(user_message):
    """Look up the medicine named in the message, local catalog only."""
    terms = _query_terms(user_message)
    if not terms:
        return []
    candidates = []
    phrase = " ".join(terms)
    if len(terms) > 1:
        candidates.append(phrase)
    candidates.extend(t for t in terms if len(t) >= 3 and not t.isdigit())
    for candidate in candidates[:4]:
        try:
            results = search_medicines(candidate, limit=3, enable_api_fallback=False)
        except Exception:
            continue
        relevant = [dict(r) for r in results if _mentions_term(dict(r), terms)]
        if relevant:
            return relevant
    return []


def _mentions_term(record, terms):
    """True when a query word appears as a whole word in the name or composition,
    so "hello" does not pick up "Hellox"."""
    text = " ".join(str(record.get(k) or "") for k in ("medicine_name", "composition")).lower()
    words = set(re.findall(r"[a-z0-9]+", text))
    return any(t.lower() in words for t in terms if len(t) >= 3 and not t.isdigit())


def _detail_link(m):
    name = m.get("medicine_name") or "this medicine"
    if m.get("id") is None:
        return f"**{name}**"
    return f"[{name}](/medicine/{int(m['id'])})"


def _generic_answer(found_meds):
    lines = [
        "**Same-composition options**",
        "A cheaper product with the same recorded active ingredients, strength and "
        "dosage form may exist. Same composition does not by itself mean two products "
        "can be swapped for you, so a pharmacist or doctor should confirm before you switch.",
    ]
    if found_meds:
        m = found_meds[0]
        comp = m.get("composition")
        lines.append("")
        if comp:
            lines.append(f"{_detail_link(m)} is recorded as containing **{comp}**.")
        else:
            lines.append(f"I found {_detail_link(m)}, but its composition is not recorded yet.")
        lines.append(
            "Open its detail page to see products with the same recorded composition "
            "and their catalog prices, with how current each price is."
        )
    else:
        lines.append("")
        lines.append(
            "Tell me the medicine name (for example *Dolo 650* or *Pan 40*) and I will "
            "link you to its detail page."
        )
    return "\n".join(lines) + DISCLAIMER


def _info_answer(m):
    rx = str(m.get("prescription_required", "")).lower() in ("true", "1", "yes")
    lines = [
        f"**{m.get('medicine_name')}**",
        f"• **Composition**: {m.get('composition') or 'Not recorded yet'}",
        f"• **Form / strength**: {m.get('dosage_form') or 'Not recorded'}"
        f" | {m.get('strength') or 'Not recorded'}",
    ]
    if m.get("brand_name"):
        lines.append(f"• **Brand**: {m.get('brand_name')}")
    if m.get("price"):
        lines.append(f"• **Catalog price**: Rs. {m.get('price')} (may not be current)")
    lines.append(f"• **Prescription**: {'Required' if rx else 'Not marked as required'}")
    lines.append("")
    lines.append(f"See {_detail_link(m)} for same-composition options and prices.")
    return "\n".join(lines) + DISCLAIMER


def process_assistant_query(user_message):
    """Answer a health or medicine question with conservative, rule-based text."""
    if not user_message or not user_message.strip():
        return ("Hello! I am your MED FINDER assistant. Ask me what a medicine contains "
                "or where to find same-composition options.")

    msg = user_message.strip().lower()

    if any(k in msg for k in EMERGENCY_KEYWORDS):
        return ("🚨 **EMERGENCY WARNING**: Based on what you described, please seek "
                "immediate emergency medical care or call your local emergency services "
                "(e.g. 112 / 108) right away. Do not delay professional hospital evaluation.")

    found_meds = find_medicines(user_message)

    # Optional Gemini answer grounded in catalog data (off unless
    # MEDFINDER_AI_ASSISTANT=1). Any failure returns None and the rules answer.
    if found_meds:
        from backend import ai_assistant
        if ai_assistant.ai_enabled():
            answer = ai_assistant.ai_answer(user_message.strip(), found_meds[0], DISCLAIMER)
            if answer:
                return answer

    if any(k in msg for k in ("generic", "alternative", "cheap", "substitute")):
        return _generic_answer(found_meds)

    if "fever" in msg or "temperature" in msg:
        return (
            "🌡️ **Fever: general self-care**\n"
            "• Drink plenty of fluids and rest.\n"
            "• Wear light clothing; a lukewarm sponge can help you feel more comfortable.\n"
            "• **See a doctor promptly** if the fever is above 103°F (39.4°C), lasts more "
            "than 3 days, or comes with a stiff neck, rash, confusion or breathing difficulty.\n"
            "• For a fever medicine and the right dose, ask a pharmacist or doctor, "
            "especially for children, pregnancy or long-term conditions."
            + DISCLAIMER
        )

    if "headache" in msg or "migraine" in msg:
        return (
            "🧠 **Headache: general self-care**\n"
            "• Rest in a quiet, dim room and drink water.\n"
            "• Regular sleep and meals help prevent many headaches.\n"
            "• **Get emergency care** for a sudden, extremely severe headache, or one with "
            "weakness, confusion, fever with a stiff neck, or after a head injury.\n"
            "• For a pain reliever that suits you, ask a pharmacist or doctor."
            + DISCLAIMER
        )

    if "cold" in msg or "cough" in msg or "runny nose" in msg:
        return (
            "🤧 **Cold and cough: general self-care**\n"
            "• Rest, warm fluids and steam inhalation can ease congestion.\n"
            "• Most colds are viral; antibiotics do not help them.\n"
            "• **See a doctor** if you have breathing difficulty, a high fever, chest pain, "
            "or a cough lasting more than 2 weeks.\n"
            "• For a medicine that suits you, ask a pharmacist or doctor."
            + DISCLAIMER
        )

    if "side effect" in msg or "reaction" in msg:
        lines = ["ℹ️ **Side effects**"]
        if found_meds:
            m = found_meds[0]
            lines.append(f"{_detail_link(m)} is recorded as containing "
                         f"**{m.get('composition') or 'an unrecorded composition'}**.")
        lines.append("I cannot list side effects reliably. The leaflet in the pack and your "
                     "pharmacist are the best sources.")
        lines.append("• Swelling of the face, a rash, or difficulty breathing can be an allergic "
                     "reaction: stop and get urgent medical help.")
        return "\n".join(lines) + DISCLAIMER

    if found_meds:
        return _info_answer(found_meds[0])

    return (
        "I can tell you what a medicine contains and link you to products with the same "
        "recorded composition. Try *What is in Pan 40?* or *Cheaper option for Dolo 650*."
        + DISCLAIMER
    )
