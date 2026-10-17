"""
============================================================
MED FINDER
AI HEALTH ASSISTANT SERVICE
Version : 1.0
============================================================
"""

import re
from backend.services.medicine_service import search_medicines

DISCLAIMER = "\n\n⚠️ Disclaimer: I am an AI assistant for informational purposes only. Always consult a qualified healthcare professional or doctor for medical diagnosis, prescriptions, or personalized treatment."

def process_assistant_query(user_message):
    """
    Process user health and medicine questions with intelligent responses,
    database lookups, and clinical safety guardrails.
    """
    if not user_message or not user_message.strip():
        return "Hello! I am your MED FINDER health assistant. How can I help you with medicine information or guidance today?"

    msg = user_message.strip().lower()

    # Emergency Detection
    emergency_keywords = ["chest pain", "heart attack", "can't breathe", "cannot breathe", "severe bleeding", "unconscious", "stroke", "poisoning", "overdose"]
    if any(k in msg for k in emergency_keywords):
        return "🚨 **EMERGENCY WARNING**: Based on what you described, please seek immediate emergency medical care or call your local emergency services (e.g. 112 / 911 / 108) right away. Do not delay professional hospital evaluation."

    # Medicine lookup in database
    # Extract likely medicine names from the message
    words = [w for w in re.findall(r'\b[a-zA-Z]{3,}\b', user_message) if w.lower() not in ["what", "when", "where", "which", "how", "the", "and", "for", "with", "take", "give", "dose", "side", "effect", "price", "about", "tell", "explain", "generic", "alternative", "cost"]]

    found_meds = []
    for w in words[:3]:
        try:
            results = search_medicines(w, limit=3, enable_api_fallback=False)
            if results:
                found_meds.extend(results)
                break
        except Exception:
            pass

    # Specific common question patterns
    if "fever" in msg or "temperature" in msg:
        return (
            "🌡️ **Fever Guidance**:\n"
            "• **Common OTC Options**: Paracetamol / Acetaminophen (e.g., 500mg or 650mg as advised per body weight).\n"
            "• **Key Actions**: Stay hydrated with plenty of water and oral electrolytes. Get adequate bed rest and use lukewarm sponge baths if temperature is high.\n"
            "• **When to see a doctor**: If fever exceeds 103°F (39.4°C), lasts more than 3 days, or is accompanied by stiff neck, rash, or breathing difficulty."
            + DISCLAIMER
        )

    if "headache" in msg or "migraine" in msg:
        return (
            "🧠 **Headache / Migraine Management**:\n"
            "• **Common Relievers**: Paracetamol, Ibuprofen, or Naproxen (take with food).\n"
            "• **Supportive Care**: Rest in a quiet, dimly lit room. Stay hydrated, avoid caffeine withdrawal, and apply a cool compress to your forehead.\n"
            "• **Warning Signs**: A sudden, extremely severe headache ('thunderclap') or headache with neurological weakness requires immediate emergency attention."
            + DISCLAIMER
        )

    if "cold" in msg or "cough" in msg or "runny nose" in msg:
        return (
            "🤧 **Cold & Cough Support**:\n"
            "• **Allergy / Runny Nose**: Antihistamines like Cetirizine (10mg) or Levocetirizine.\n"
            "• **Congestion**: Steam inhalation, saline nasal spray, and warm fluids.\n"
            "• **Cough**: Honey with warm water (for dry cough), expectorants for productive chest cough.\n"
            "• **Note**: Antibiotics are ineffective against viral colds unless a secondary bacterial infection is diagnosed by a doctor."
            + DISCLAIMER
        )

    if "generic" in msg or "alternative" in msg or "cheap" in msg or "substitute" in msg:
        med_context = ""
        if found_meds:
            m = found_meds[0]
            med_context = f"\n\nFor **{m.get('medicine_name')}**, the active composition is *{m.get('composition') or m.get('brand_name')}*. Ask your pharmacist for generic equivalents sharing this exact active salt to save up to 50–80%."
        return (
            "💊 **Generic Substitutes**:\n"
            "Generic medicines contain the identical active chemical ingredient, strength, dosage form, and bioequivalence as branded medications, but typically cost 50% to 80% less because they don't carry marketing overhead."
            + med_context
            + "\n• Always confirm the active salt and dosage strength with your pharmacist before switching."
            + DISCLAIMER
        )

    if "side effect" in msg or "reactions" in msg:
        if found_meds:
            m = found_meds[0]
            return (
                f"ℹ️ **Side Effects for {m.get('medicine_name')}**:\n"
                f"• **Category**: {m.get('medicine_category', 'General Medication')}\n"
                f"• **Composition**: {m.get('composition', 'N/A')}\n"
                "• **Common Precautions**: Take with or after meals to avoid gastrointestinal upset unless directed otherwise. Avoid alcohol when on medications.\n"
                "• If you experience rashes, facial swelling, severe dizziness, or breathing difficulty, discontinue and contact emergency services immediately."
                + DISCLAIMER
            )
        return (
            "ℹ️ **Medicine Side Effects**:\n"
            "Most medicines have mild, self-limiting side effects (like mild stomach upset or drowsiness). However, unexpected swelling, itching, rashes, or difficulty breathing can indicate an allergic reaction. Please specify the exact medicine name to get focused information."
            + DISCLAIMER
        )

    # If a medicine was matched in the database
    if found_meds:
        m = found_meds[0]
        price_str = f"Rs. {m.get('price')}" if m.get('price') else "Price varies"
        rx_str = "Yes (Doctor's prescription required)" if str(m.get('prescription_required', '')).lower() in ['true', '1', 'yes'] else "OTC / As advised"
        return (
            f"🔍 **Information for {m.get('medicine_name')}**:\n"
            f"• **Brand / Manufacturer**: {m.get('brand_name', 'N/A')}\n"
            f"• **Category**: {m.get('medicine_category', 'General')}\n"
            f"• **Dosage Form & Strength**: {m.get('dosage_form', 'Form')} | {m.get('strength', 'Standard')}\n"
            f"• **Active Composition**: {m.get('composition', 'Standard Composition')}\n"
            f"• **Price**: {price_str}\n"
            f"• **Prescription Required**: {rx_str}\n\n"
            f"You can view full details and find cheaper alternatives by searching for this medicine on MED FINDER."
            + DISCLAIMER
        )

    # General fallback response
    return (
        f"Thank you for your question: *'{user_message}'*.\n\n"
        "MED FINDER can help you look up medicine compositions, find generic substitutes, compare prices, and explore disease guidance. "
        "Feel free to ask about specific medicine names (e.g. *Dolo 650*, *Metformin*, *Azithromycin*), symptoms, or medication safety precautions."
        + DISCLAIMER
    )
