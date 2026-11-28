"""
============================================================
MED FINDER
DISEASE & SYMPTOM GUIDANCE SERVICE
============================================================

General, conservative information for about 30 common conditions. No doses,
no diagnoses. Each condition lists:
- keywords: everyday words people search with ("cold", "flu", "loose motions");
- relief: keys into disease_medicines.RELIEF_PRODUCTS for conditions where
  over-the-counter style symptom relief is reasonable; empty otherwise;
- medicine_note: shown in place of products (always shown when relief is empty).
"""

import re

SEE_DOCTOR = ("Treatment for this condition needs a doctor's assessment and usually a "
              "prescription, so MED FINDER does not list medicines for it.")


def _c(id, name, category, severity, emergency, symptoms, causes, lifestyle, diet,
       keywords=(), relief=(), medicine_note=None):
    return {
        "id": id, "name": name, "category": category, "severity": severity,
        "emergency": emergency, "symptoms": list(symptoms), "causes": list(causes),
        "lifestyle": list(lifestyle), "diet": list(diet), "keywords": list(keywords),
        "relief": list(relief),
        "medicine_note": medicine_note or (None if relief else SEE_DOCTOR),
    }


DISEASES_DB = [
    # ---------------- Infections and fever ----------------
    _c("common_cold", "Common Cold", "Respiratory", "Acute, usually mild",
       "Get medical help for breathing difficulty, chest pain, a high fever lasting more than 3 days, "
       "or symptoms that keep getting worse after a week.",
       ["Runny or blocked nose", "Sneezing", "Sore or scratchy throat", "Mild cough",
        "Mild body ache or low fever", "Watery eyes"],
       ["Viruses (most often rhinovirus) spread by droplets and touch",
        "Close contact with someone who has a cold", "Touching the face with unwashed hands"],
       ["Rest and sleep", "Steam inhalation or a warm shower to ease a blocked nose",
        "Wash hands often and cover coughs and sneezes", "Most colds settle in 7 to 10 days"],
       ["Warm fluids: soups, herbal tea, warm water", "Honey in warm water can soothe a sore throat (not for babies under 1 year)",
        "Fruits rich in vitamin C", "Plenty of water"],
       keywords=["cold", "common cold", "runny nose", "blocked nose", "sneezing", "nasal congestion", "zukam"],
       relief=["cold_combo", "paracetamol", "cetirizine", "levocetirizine"]),
    _c("flu", "Influenza (Flu)", "Respiratory", "Acute",
       "Seek urgent care for breathing difficulty, chest pain, confusion, bluish lips, severe weakness, "
       "or if you are elderly, pregnant or have a long-term illness and symptoms are severe.",
       ["Sudden fever and chills", "Body ache and muscle pain", "Tiredness and weakness",
        "Headache", "Dry cough", "Sore throat"],
       ["Influenza viruses spread by droplets", "Seasonal outbreaks", "Close contact with infected people"],
       ["Rest at home until the fever settles", "Yearly flu vaccination", "Wear a mask and wash hands to protect others"],
       ["Plenty of fluids", "Light, easily digestible meals", "Warm soups and broths"],
       keywords=["flu", "influenza", "seasonal flu", "body ache"],
       relief=["paracetamol", "ors"]),
    _c("viral_fever", "Viral Fever", "Infectious Disease", "Acute",
       "Seek urgent care for fever above 103°F (39.4°C), fever lasting more than 3 days, a stiff neck, "
       "rash, confusion, breathing difficulty, or fits.",
       ["Raised body temperature", "Body ache and joint pain", "Chills and sweating",
        "Headache", "Tiredness and loss of appetite"],
       ["Viral infections spread by air, water or contact", "Seasonal changes", "Close contact with infected people"],
       ["Rest", "Lukewarm sponging can help you feel comfortable", "Wear light clothing",
        "Get a blood test if a doctor advises it (dengue and malaria can look similar)"],
       ["Plenty of fluids, ORS or coconut water", "Light meals such as khichdi or porridge", "Fruits"],
       keywords=["fever", "viral fever", "temperature", "bukhar", "high temperature"],
       relief=["paracetamol", "ors"]),
    _c("cough", "Cough", "Respiratory", "Acute or long-lasting",
       "Seek urgent care if you cough up blood, have breathing difficulty or chest pain. See a doctor for a "
       "cough lasting more than 2 to 3 weeks, or with weight loss or night sweats.",
       ["Dry, tickly cough", "Cough with phlegm", "Sore throat from coughing", "Disturbed sleep"],
       ["Viral infections such as colds", "Allergies and dust", "Smoke and pollution", "Acid reflux", "Asthma"],
       ["Steam inhalation", "Avoid smoke and dust", "Sleep with the head slightly raised", "Stop smoking"],
       ["Warm fluids", "Honey in warm water (not for babies under 1 year)", "Avoid very cold drinks if they trigger coughing"],
       keywords=["cough", "dry cough", "wet cough", "khansi", "phlegm"],
       medicine_note="The right cough product depends on the type of cough and other conditions, "
                     "so MED FINDER does not list one. Ask a pharmacist or doctor."),
    _c("sore_throat", "Sore Throat (Pharyngitis)", "ENT", "Acute, usually mild",
       "Seek urgent care for difficulty breathing or swallowing saliva, drooling, or a muffled voice. "
       "See a doctor if it lasts more than a week or comes with a high fever.",
       ["Pain or scratchiness in the throat", "Pain on swallowing", "Swollen glands in the neck", "Hoarse voice"],
       ["Viral infections", "Bacterial infections such as strep throat", "Dry air, smoke, shouting"],
       ["Gargle with warm salt water", "Rest your voice", "Avoid smoking"],
       ["Warm fluids and soups", "Honey in warm water (not for babies under 1 year)", "Soft foods"],
       keywords=["sore throat", "throat pain", "throat infection", "pharyngitis", "tonsillitis"],
       relief=["paracetamol"]),
    _c("dengue", "Dengue", "Infectious Disease", "Acute, can become serious",
       "Go to hospital at once for severe stomach pain, repeated vomiting, bleeding from gums or nose, "
       "blood in vomit or stool, extreme tiredness or restlessness, especially as the fever falls.",
       ["High fever", "Severe headache and pain behind the eyes", "Muscle and joint pain",
        "Rash", "Nausea and vomiting"],
       ["Dengue virus spread by the bite of Aedes mosquitoes (they bite in the daytime)"],
       ["See a doctor for a blood test", "Rest", "Prevent mosquito bites: nets, repellents, full sleeves",
        "Empty standing water around the home"],
       ["Plenty of fluids, ORS, coconut water", "Light meals"],
       keywords=["dengue", "dengue fever", "platelets"]),
    _c("malaria", "Malaria", "Infectious Disease", "Acute, can become serious",
       "Seek urgent care for confusion, fits, breathing difficulty, yellow eyes, dark urine or inability to keep fluids down.",
       ["Fever with chills and shivering, often in cycles", "Sweating", "Headache", "Body ache", "Nausea and vomiting"],
       ["Malaria parasites spread by the bite of Anopheles mosquitoes"],
       ["See a doctor for a blood test; malaria needs prescribed treatment",
        "Use mosquito nets and repellents", "Remove standing water"],
       ["Plenty of fluids", "Light, nourishing meals"],
       keywords=["malaria", "chills fever"]),
    _c("typhoid", "Typhoid", "Infectious Disease", "Acute, can become serious",
       "Seek urgent care for severe stomach pain, vomiting, blood in stool, or confusion.",
       ["Fever that rises day by day", "Weakness and tiredness", "Stomach pain",
        "Headache", "Loss of appetite", "Constipation or diarrhea"],
       ["Salmonella Typhi bacteria in contaminated food or water"],
       ["See a doctor for tests; typhoid needs prescribed treatment",
        "Drink safe, boiled or filtered water", "Wash hands before eating", "Typhoid vaccination"],
       ["Soft, well-cooked food", "Plenty of safe fluids", "Avoid street food and raw salads"],
       keywords=["typhoid", "enteric fever"]),
    _c("uti", "Urinary Tract Infection (UTI)", "Urology", "Acute",
       "Seek urgent care for fever with back or side pain, vomiting, or blood in the urine, or if you are pregnant.",
       ["Burning when passing urine", "Passing urine often and urgently", "Cloudy or strong-smelling urine",
        "Lower belly pain"],
       ["Bacteria entering the urinary tract", "Not drinking enough water", "Holding urine for long"],
       ["See a doctor; a UTI usually needs a urine test and prescribed treatment",
        "Do not hold urine for long", "Good personal hygiene"],
       ["Drink plenty of water"],
       keywords=["uti", "urine infection", "burning urine", "urinary infection", "cystitis"]),
    _c("conjunctivitis", "Conjunctivitis (Eye Flu / Pink Eye)", "Eye", "Acute",
       "See a doctor urgently for eye pain, blurred vision, sensitivity to light, or after an injury.",
       ["Red eye", "Watery or sticky discharge", "Itchy or gritty eyes", "Swollen eyelids"],
       ["Viruses or bacteria", "Allergies", "Irritants such as smoke or chlorine"],
       ["See a doctor before using any eye drops", "Do not rub your eyes", "Wash hands often",
        "Do not share towels; avoid contact lenses until it clears"],
       ["A balanced diet; no specific food treats conjunctivitis"],
       keywords=["conjunctivitis", "eye flu", "pink eye", "red eye", "eye infection"]),

    # ---------------- Pain ----------------
    _c("headache", "Tension Headache", "Neurological", "Episodic, usually mild",
       "Get emergency care for a sudden, extremely severe headache, or one with weakness, confusion, "
       "fever with a stiff neck, loss of vision, or after a head injury.",
       ["Dull, pressing pain on both sides of the head", "Tight band feeling around the head",
        "Neck and shoulder tightness"],
       ["Stress", "Poor sleep", "Long screen time and poor posture", "Skipped meals and dehydration"],
       ["Regular sleep", "Screen breaks and good posture", "Relaxation and gentle exercise",
        "See a doctor if headaches are frequent"],
       ["Drink enough water", "Regular meals", "Limit caffeine"],
       keywords=["headache", "head ache", "head pain", "sir dard", "tension headache"],
       relief=["paracetamol", "ibuprofen"]),
    _c("migraine", "Migraine", "Neurological", "Episodic, moderate to severe",
       "Get emergency care for a sudden 'thunderclap' headache, or headache with fever and stiff neck, "
       "weakness, confusion or trouble speaking.",
       ["Throbbing pain, often on one side", "Sensitivity to light and sound", "Nausea or vomiting",
        "Visual aura (flashing lights, blind spots)"],
       ["Genetic tendency", "Hormonal changes", "Bright lights, loud noise, strong smells",
        "Poor sleep or skipped meals"],
       ["Rest in a quiet, dark room during an attack", "Keep a trigger diary",
        "Regular sleep and meals", "See a doctor if attacks are frequent"],
       ["Stay hydrated", "Note foods that trigger attacks (for example aged cheese, processed meats)"],
       keywords=["migraine", "one sided headache", "aura"],
       relief=["paracetamol", "ibuprofen"]),
    _c("back_pain", "Back Pain", "Musculoskeletal", "Acute or chronic",
       "Seek urgent care for back pain with numbness around the groin, loss of bladder or bowel control, "
       "leg weakness, fever, or after a fall or injury.",
       ["Ache or stiffness in the lower or upper back", "Pain on bending or lifting", "Muscle spasm"],
       ["Muscle strain", "Poor posture", "Lifting heavy objects wrongly", "Lack of exercise"],
       ["Keep gently active; long bed rest slows recovery", "Good posture while sitting",
        "Lift with your knees, not your back", "Warm compress"],
       ["Balanced diet with calcium and vitamin D", "Maintain a healthy weight"],
       keywords=["back pain", "backache", "lower back pain", "kamar dard", "muscle pain"],
       relief=["diclofenac_gel", "paracetamol"]),
    _c("arthritis", "Arthritis (Joint Pain)", "Musculoskeletal", "Chronic",
       "See a doctor promptly for a hot, red, very swollen joint, especially with fever.",
       ["Joint pain and stiffness", "Swelling", "Reduced movement", "Morning stiffness"],
       ["Wear and tear (osteoarthritis)", "Immune conditions (rheumatoid arthritis)", "Age", "Excess weight", "Old injuries"],
       ["See a doctor to find the type of arthritis", "Gentle regular exercise",
        "Weight management", "Warm compress for stiffness"],
       ["Balanced diet", "Omega-3 rich foods (walnuts, flaxseed, fish)", "Maintain a healthy weight"],
       keywords=["arthritis", "joint pain", "knee pain", "osteoarthritis", "gathiya"],
       relief=["diclofenac_gel"],
       medicine_note="Long-lasting joint pain needs a doctor's assessment. Only a topical product is listed here."),

    # ---------------- Allergy, skin ----------------
    _c("allergy", "Allergic Rhinitis (Allergy)", "Allergy", "Recurring",
       "Get emergency care for swelling of the face, lips or tongue, difficulty breathing, or fainting.",
       ["Sneezing", "Runny or blocked nose", "Itchy nose, eyes or throat", "Watery eyes"],
       ["Pollen", "Dust mites", "Pet dander", "Mould", "Smoke and strong smells"],
       ["Identify and avoid triggers", "Keep windows closed in high-pollen seasons",
        "Wash bedding in hot water", "Use a mask in dusty places"],
       ["A balanced diet; no food cures allergy"],
       keywords=["allergy", "allergic", "hay fever", "dust allergy", "sneezing", "itchy eyes"],
       relief=["cetirizine", "levocetirizine", "loratadine", "fexofenadine"]),
    _c("acne", "Acne", "Dermatology", "Chronic, mild to moderate",
       "See a doctor for painful deep lumps, acne that leaves scars, or acne affecting your mood.",
       ["Pimples and whiteheads", "Blackheads", "Oily skin", "Red, tender bumps"],
       ["Blocked skin pores", "Hormonal changes", "Oily skin products", "Genetics"],
       ["Wash the face twice a day with a mild cleanser", "Do not squeeze pimples",
        "Use oil-free products", "A doctor or pharmacist can advise on creams"],
       ["A balanced diet", "Notice whether certain foods seem to worsen it"],
       keywords=["acne", "pimples", "pimple", "breakouts"],
       medicine_note="Acne treatments vary by skin type and severity, so MED FINDER does not list one. "
                     "Ask a pharmacist or skin doctor."),
    _c("eczema", "Eczema (Dermatitis)", "Dermatology", "Chronic, recurring",
       "See a doctor for weeping, crusted or painful skin, or fever with a rash.",
       ["Dry, itchy skin", "Red or dark patches", "Cracked or scaly skin"],
       ["Genetic tendency", "Soaps and detergents", "Allergens", "Stress", "Dry weather"],
       ["Moisturise often", "Use mild, fragrance-free soap", "Wear soft cotton clothes", "Avoid scratching"],
       ["Note any foods that seem to trigger flare-ups and discuss them with a doctor"],
       keywords=["eczema", "dermatitis", "itchy skin", "dry skin", "skin rash"],
       medicine_note="Eczema treatment depends on severity. Ask a skin doctor or pharmacist."),
    _c("fungal_skin", "Fungal Skin Infection (Ringworm)", "Dermatology", "Acute or recurring",
       "See a doctor if it spreads widely, affects the scalp or nails, or does not improve in 2 weeks.",
       ["Round, red, itchy patches with a clear centre", "Itching in skin folds", "Scaly or cracked skin"],
       ["Fungi that grow in warm, moist skin", "Sweating", "Shared towels or clothes", "Tight clothing"],
       ["Keep skin clean and dry", "Do not share towels", "Wear loose cotton clothes",
        "Continue treatment for as long as advised, even after it looks better"],
       ["A balanced diet"],
       keywords=["fungal infection", "ringworm", "daad", "tinea", "jock itch", "athlete's foot"],
       relief=["clotrimazole_topical"]),

    # ---------------- Digestive ----------------
    _c("gerd", "Acidity / Acid Reflux (GERD)", "Gastroenterology", "Recurring",
       "Chest pain spreading to the arm or jaw can be a heart attack: get emergency care. "
       "See a doctor for trouble swallowing, vomiting blood, black stools or weight loss.",
       ["Heartburn (burning behind the breastbone)", "Sour taste or acid in the mouth",
        "Burping and bloating", "Discomfort after meals"],
       ["Large or late meals", "Spicy, fried or fatty food", "Lying down soon after eating",
        "Excess weight", "Smoking and alcohol"],
       ["Eat smaller meals", "Do not lie down for 2 to 3 hours after eating",
        "Raise the head of the bed", "Quit smoking"],
       ["Avoid spicy, fried and very fatty food", "Limit tea, coffee and fizzy drinks", "Bananas, oats, vegetables"],
       keywords=["acidity", "acid reflux", "heartburn", "gerd", "gas", "indigestion"],
       relief=["famotidine", "pantoprazole", "omeprazole"]),
    _c("gastritis", "Gastritis", "Gastroenterology", "Acute or chronic",
       "See a doctor urgently for vomiting blood, black stools, or severe stomach pain.",
       ["Burning or gnawing upper stomach pain", "Nausea", "Feeling full quickly", "Bloating"],
       ["H. pylori infection", "Frequent use of pain relievers", "Alcohol", "Stress", "Irregular meals"],
       ["Regular, smaller meals", "Avoid alcohol and smoking",
        "Ask a doctor before regular use of pain relievers", "See a doctor if it keeps coming back"],
       ["Bland, easily digestible food", "Avoid spicy and fried food"],
       keywords=["gastritis", "stomach pain", "stomach burning", "upper abdominal pain"],
       relief=["famotidine", "pantoprazole"]),
    _c("diarrhea", "Diarrhea (Loose Motions)", "Gastroenterology", "Acute",
       "Seek urgent care for signs of dehydration (very little urine, dizziness, dry mouth), blood in stool, "
       "high fever, or diarrhea lasting more than 2 days. Babies and elderly people need care sooner.",
       ["Frequent loose or watery stools", "Stomach cramps", "Nausea", "Weakness"],
       ["Contaminated food or water", "Viral or bacterial infections", "Food intolerance"],
       ["Replace lost fluids", "Wash hands before eating and after the toilet", "Drink safe water"],
       ["ORS, coconut water, rice water, buttermilk", "Banana, rice, curd, toast",
        "Avoid oily and spicy food and milk until better"],
       keywords=["diarrhea", "diarrhoea", "loose motions", "loose motion", "dysentery", "dehydration"],
       relief=["ors"]),
    _c("constipation", "Constipation", "Gastroenterology", "Acute or chronic",
       "See a doctor for blood in stool, severe stomach pain, vomiting, or a lasting change in bowel habit, "
       "especially with weight loss.",
       ["Infrequent stools", "Hard or dry stools", "Straining", "Bloating"],
       ["Low-fibre diet", "Not drinking enough water", "Lack of activity", "Some medicines"],
       ["Regular physical activity", "Do not ignore the urge to go", "A regular toilet routine"],
       ["Fibre-rich foods: fruits, vegetables, whole grains", "Plenty of water", "Soaked raisins or figs, papaya"],
       keywords=["constipation", "kabz", "hard stool"],
       medicine_note="Diet, water and activity usually help. For a product, ask a pharmacist."),
    _c("nausea_vomiting", "Nausea and Vomiting", "Gastroenterology", "Acute",
       "Seek urgent care for vomiting blood, severe stomach pain, a stiff neck, confusion, signs of "
       "dehydration, or vomiting that lasts more than a day.",
       ["Feeling sick", "Vomiting", "Loss of appetite", "Weakness"],
       ["Stomach infections", "Food poisoning", "Motion sickness", "Pregnancy", "Migraine"],
       ["Small sips of fluid often", "Rest", "Fresh air"],
       ["ORS in small sips", "Plain, dry foods once vomiting settles (toast, crackers, rice)", "Ginger"],
       keywords=["nausea", "vomiting", "vomit", "ulti", "food poisoning", "motion sickness"],
       relief=["ors"]),

    # ---------------- Long-term conditions (prescription only) ----------------
    _c("diabetes", "Type 2 Diabetes", "Endocrine", "Chronic",
       "Seek urgent care for very high blood sugar with confusion, vomiting, fruity breath or drowsiness, "
       "or very low sugar with sweating, shaking or confusion.",
       ["Frequent urination", "Increased thirst", "Unexplained weight loss", "Tiredness", "Blurred vision",
        "Slow-healing wounds"],
       ["Insulin resistance", "Family history", "Excess weight", "Inactive lifestyle"],
       ["30 minutes of activity most days", "Regular blood sugar checks (HbA1c)", "Weight management",
        "Regular eye, foot and kidney checks"],
       ["Whole grains, vegetables, pulses", "Lean protein", "Avoid sugary drinks and sweets"],
       keywords=["diabetes", "sugar", "blood sugar", "madhumeh", "high sugar"]),
    _c("hypertension", "Hypertension (High Blood Pressure)", "Cardiovascular", "Chronic",
       "Blood pressure above 180/120 with chest pain, breathlessness, weakness, or confusion needs emergency care.",
       ["Often no symptoms", "Headache", "Dizziness", "Nosebleeds (in severe cases)"],
       ["High salt intake", "Excess weight", "Inactivity", "Stress", "Family history", "Smoking and alcohol"],
       ["Check blood pressure regularly", "Reduce salt", "Regular exercise", "Quit smoking, limit alcohol"],
       ["Fruits and vegetables", "Low-fat dairy", "Avoid pickles, papad and salty snacks"],
       keywords=["hypertension", "high blood pressure", "bp", "high bp", "blood pressure"]),
    _c("high_cholesterol", "High Cholesterol", "Cardiovascular", "Chronic",
       "Chest pain, breathlessness, or sudden weakness on one side needs emergency care.",
       ["Usually no symptoms; found on a blood test (lipid profile)"],
       ["Diet high in saturated fat", "Inactivity", "Excess weight", "Family history", "Smoking"],
       ["Regular exercise", "Weight management", "Quit smoking", "Regular lipid tests"],
       ["Oats, pulses, vegetables, nuts", "Limit fried food, ghee, butter and red meat"],
       keywords=["cholesterol", "high cholesterol", "lipids", "triglycerides"]),
    _c("thyroid", "Thyroid Disorders (Hypo/Hyperthyroidism)", "Endocrine", "Chronic",
       "Seek urgent care for a racing heartbeat with fever and confusion, or extreme drowsiness and cold intolerance.",
       ["Underactive: tiredness, weight gain, feeling cold, dry skin, constipation",
        "Overactive: weight loss, fast heartbeat, sweating, anxiety, tremor"],
       ["Autoimmune conditions", "Iodine imbalance", "Family history"],
       ["See a doctor for a thyroid blood test (TSH)", "Take prescribed treatment regularly",
        "Regular follow-up tests"],
       ["Use iodised salt", "A balanced diet"],
       keywords=["thyroid", "hypothyroidism", "hyperthyroidism", "tsh", "goitre"]),
    _c("anemia", "Anemia", "Hematology", "Chronic",
       "Seek urgent care for chest pain, breathlessness at rest, fainting, or heavy bleeding.",
       ["Tiredness and weakness", "Pale skin", "Breathlessness on exertion", "Dizziness", "Cold hands and feet"],
       ["Iron deficiency", "Vitamin B12 or folate deficiency", "Blood loss", "Long-term illness"],
       ["See a doctor for a blood test to find the cause before taking supplements", "Regular follow-up"],
       ["Iron-rich foods: green leafy vegetables, pulses, jaggery, dates", "Vitamin C with meals helps iron absorption",
        "Avoid tea or coffee right after meals"],
       keywords=["anemia", "anaemia", "low hemoglobin", "low haemoglobin", "iron deficiency"]),
    _c("asthma", "Asthma", "Respiratory", "Chronic",
       "Get emergency care for severe breathlessness, inability to speak in full sentences, bluish lips, "
       "or no relief from a reliever inhaler.",
       ["Wheezing", "Shortness of breath", "Chest tightness", "Cough, often at night or early morning"],
       ["Allergens (dust, pollen, pets)", "Respiratory infections", "Cold air", "Smoke and pollution", "Exercise"],
       ["Follow your doctor's asthma plan", "Know and avoid your triggers",
        "Learn correct inhaler technique", "Flu vaccination"],
       ["A balanced diet", "Fruits and vegetables"],
       keywords=["asthma", "wheezing", "breathlessness", "dama", "breathing problem"]),
    _c("anxiety_insomnia", "Anxiety and Sleep Problems", "Mental Health", "Varies",
       "If you have thoughts of harming yourself, get help now: call 112 or Tele-MANAS 14416 (India).",
       ["Constant worry or nervousness", "Restlessness", "Racing heartbeat", "Trouble falling or staying asleep",
        "Daytime tiredness"],
       ["Stress", "Life changes", "Caffeine and screen use at night", "Medical conditions"],
       ["Regular sleep and wake times", "No screens an hour before bed", "Regular exercise",
        "Breathing exercises, yoga or meditation", "Talk to a doctor or counsellor"],
       ["Limit tea, coffee and energy drinks, especially after noon", "Avoid heavy meals late at night"],
       keywords=["anxiety", "insomnia", "sleep problem", "can't sleep", "stress", "panic"]),
]


def get_all_diseases():
    """Return all guided conditions."""
    return DISEASES_DB


def _words(text):
    return set(re.findall(r"[a-z0-9']+", (text or "").lower()))


def match_tier(disease, q_clean):
    """How well a query matches a condition (lower is better), or None."""
    if not q_clean:
        return 9
    if disease["id"] == q_clean or disease["name"].lower() == q_clean or q_clean in disease["keywords"]:
        return 0
    name = disease["name"].lower()
    if _words(q_clean) <= _words(name) or any(k.startswith(q_clean) for k in disease["keywords"]):
        return 1
    starts_word = re.compile(r"\b" + re.escape(q_clean))
    if (starts_word.search(name) or starts_word.search(disease["category"].lower())
            or any(starts_word.search(k) for k in disease["keywords"])):
        return 2
    if any(starts_word.search(s.lower()) for s in disease["symptoms"]):
        return 3
    return None


def search_diseases(query):
    """Conditions matching the query, best first (all of them for an empty query)."""
    q_clean = " ".join((query or "").lower().split())
    scored = [(match_tier(d, q_clean), i, d) for i, d in enumerate(DISEASES_DB)]
    return [d for tier, _i, d in sorted((s for s in scored if s[0] is not None), key=lambda s: s[:2])]


def get_disease_by_id_or_name(query):
    """
    Find one condition by ID, name, keyword, category or symptom.

    An empty query returns the first condition (the page's default view).
    A query that matches nothing returns None rather than an unrelated condition.
    """
    if not query or not query.strip():
        return DISEASES_DB[0]
    matches = search_diseases(query)
    return matches[0] if matches else None
