"""
============================================================
MED FINDER
DISEASE & SYMPTOM GUIDANCE SERVICE
Version : 1.0
============================================================
"""

DISEASES_DB = [
    {
        "id": "diabetes",
        "name": "Type 2 Diabetes",
        "category": "Endocrine",
        "severity": "Chronic",
        "emergency": "Seek immediate medical care if blood sugar > 300 mg/dL, confusion, fruity breath, or loss of consciousness occurs.",
        "symptoms": ["Frequent urination (polyuria)", "Increased thirst (polydipsia)", "Unexplained weight loss", "Fatigue and weakness", "Blurred vision"],
        "causes": ["Insulin resistance", "Genetics and family history", "Obesity or high BMI", "Sedentary lifestyle and poor diet"],
        "lifestyle": ["30+ minutes of moderate exercise daily", "Regular blood glucose monitoring (HbA1c test)", "Weight management", "Stress reduction and adequate sleep"],
        "diet": ["High-fiber whole grains (oats, brown rice)", "Leafy green vegetables and legumes", "Lean proteins (tofu, fish, chicken)", "Avoid refined sugar, sweets, and sweetened beverages"]
    },
    {
        "id": "hypertension",
        "name": "Hypertension (High Blood Pressure)",
        "category": "Cardiovascular",
        "severity": "Chronic",
        "emergency": "Blood pressure above 180/120 mmHg accompanied by chest pain, shortness of breath, or numbness requires emergency ER care.",
        "symptoms": ["Severe headaches", "Dizziness or lightheadedness", "Chest tightness or palpitations", "Nosebleeds (in extreme cases)", "Often symptomless ('Silent Killer')"],
        "causes": ["High dietary sodium intake", "Chronic stress and anxiety", "Obesity and physical inactivity", "Age and genetic predisposition", "Smoking and heavy alcohol consumption"],
        "lifestyle": ["Adopt DASH (Dietary Approaches to Stop Hypertension)", "Reduce daily sodium to < 1,500 - 2,000 mg", "Engage in regular aerobic exercise (brisk walking)", "Limit alcohol and quit smoking", "7-8 hours of quality sleep"],
        "diet": ["Potassium-rich foods (bananas, spinach, avocados)", "Berries, beets, and garlic", "Low-fat dairy products", "Avoid salted snacks, pickles, and processed meats"]
    },
    {
        "id": "asthma",
        "name": "Asthma",
        "category": "Respiratory",
        "severity": "Chronic",
        "emergency": "Severe breathlessness, inability to speak full sentences, blue lips or fingernails, and unresponsive to rescue inhaler requires immediate ER care.",
        "symptoms": ["Wheezing sound while exhaling", "Shortness of breath and breathlessness", "Chest tightness or pressure", "Coughing bouts, especially at night or early morning"],
        "causes": ["Airborne allergens (pollen, dust mites, pet dander)", "Respiratory infections (cold, flu)", "Cold air and sudden weather changes", "Air pollution, smoke, and strong chemical fumes"],
        "lifestyle": ["Identify and avoid specific allergy triggers", "Use spacer with metered-dose inhalers", "Keep rescue inhaler readily accessible", "Annual influenza and pneumonia vaccination"],
        "diet": ["Foods rich in Vitamin D (fortified foods, sunshine)", "Omega-3 fatty acids (flaxseeds, walnuts, fatty fish)", "Vitamin C and antioxidant-rich fruits", "Avoid foods with artificial sulfites and preservatives"]
    },
    {
        "id": "gerd",
        "name": "Acid Reflux / GERD",
        "category": "Gastroenterology",
        "severity": "Moderate",
        "emergency": "Severe retrosternal chest pain radiating to the left arm or jaw may mimic myocardial infarction; seek urgent medical evaluation.",
        "symptoms": ["Heartburn (burning chest pain behind breastbone)", "Acid regurgitation / sour taste in mouth", "Difficulty or pain while swallowing (dysphagia)", "Chronic dry cough or sore throat"],
        "causes": ["Weak lower esophageal sphincter (LES)", "Hiatal hernia", "Overeating or lying down immediately after meals", "Consumption of spicy, fatty, or caffeinated foods"],
        "lifestyle": ["Elevate head of bed by 6 inches", "Avoid lying down for 2-3 hours after eating", "Eat smaller, more frequent meals", "Maintain a healthy body weight"],
        "diet": ["Non-citrus fruits (bananas, melons, apples)", "Oatmeal, ginger, and green vegetables", "Almond milk and lean meats", "Avoid deep-fried food, tomatoes, chocolate, citrus, and mint"]
    },
    {
        "id": "migraine",
        "name": "Migraine Headache",
        "category": "Neurological",
        "severity": "Episodic / Moderate to Severe",
        "emergency": "Sudden 'thunderclap' headache reaching peak intensity within seconds or headache with fever and neck stiffness requires immediate evaluation.",
        "symptoms": ["Throbbing, pulsating pain usually on one side of head", "Sensitivity to light (photophobia) and sound (phonophobia)", "Nausea, vomiting, and dizziness", "Visual aura (flashing lights, blind spots)"],
        "causes": ["Neurochemical fluctuations (serotonin imbalance)", "Hormonal changes", "Bright lights, loud noises, and strong odors", "Sleep deprivation or irregular meal timing"],
        "lifestyle": ["Maintain consistent sleep and meal schedules", "Rest in a quiet, dark room during attacks", "Practice mindfulness, yoga, or biofeedback", "Keep a migraine trigger diary"],
        "diet": ["Magnesium-rich foods (spinach, pumpkin seeds, almonds)", "Stay well hydrated throughout the day", "Avoid aged cheeses, cured meats (nitrates), and artificial sweeteners"]
    },
    {
        "id": "viral_fever",
        "name": "Viral Fever & Upper Respiratory Infection",
        "category": "Infectious Disease",
        "severity": "Acute",
        "emergency": "High fever (>103°F / 39.4°C) lasting > 3 days, severe breathlessness, confusion, or stiff neck requires urgent hospital visit.",
        "symptoms": ["Elevated body temperature / fever", "Body ache, joint pain, and muscle weakness", "Sore throat, runny nose, and sneezing", "Chills and sweating", "Headache and loss of appetite"],
        "causes": ["Viral transmission via airborne droplets", "Influenza, Rhinovirus, Adenovirus, or RSV", "Close contact with infected individuals"],
        "lifestyle": ["Strict bed rest and recovery time", "Use cold sponging to reduce high fever", "Avoid exertion until body temperature normalizes", "Wear a mask to prevent household transmission"],
        "diet": ["Warm soups, broths, and herbal teas", "Electrolyte solutions and coconut water for rehydration", "Easily digestible meals (khichdi, porridge)", "Citrus fruits rich in Vitamin C"]
    }
]


def get_all_diseases():
    """Return all guided diseases."""
    return DISEASES_DB


def get_disease_by_id_or_name(query):
    """
    Search disease guidance by ID, condition name, or symptom keyword.
    """
    if not query:
        return DISEASES_DB[0]

    q_clean = query.strip().lower()

    # Exact ID match
    for d in DISEASES_DB:
        if d["id"].lower() == q_clean or d["name"].lower() == q_clean:
            return d

    # Substring in name or category
    for d in DISEASES_DB:
        if q_clean in d["name"].lower() or q_clean in d["category"].lower():
            return d

    # Symptom keyword match
    for d in DISEASES_DB:
        for sym in d["symptoms"]:
            if q_clean in sym.lower():
                return d

    # Default fallback to first
    return DISEASES_DB[0]
