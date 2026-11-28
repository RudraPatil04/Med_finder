"""Plain-language uses for common ingredients, and outbound shopping links.

USES holds short, conservative descriptions of what each ingredient is
commonly prescribed for, worded from its standard labelled indications (the
kind of line a drug reference or the pack insert gives). They describe the
ingredient, never this product, and never say a product is right for anyone.
An ingredient missing from the table gets no text: the detail page then hides
the Uses box instead of showing filler.

Buy links only leave the app. Nothing here places an order or claims stock.
"""

import re
from urllib.parse import quote_plus

from backend.medicine_data import normalize_medication_text, parse_composition

USES_NOTE = (
    "General information about the ingredient, not advice for you. "
    "Ask your doctor or pharmacist whether and how to take this medicine."
)

# Keyed by normalize_medication_text(ingredient name). Spelling variants
# share one entry through ALIASES below.
USES = {
    # Pain, fever, inflammation
    "paracetamol": "Relieves mild to moderate pain and lowers fever.",
    "ibuprofen": "Relieves pain, swelling and fever (an NSAID painkiller).",
    "diclofenac": "Relieves pain and swelling, for example from joint or muscle problems (an NSAID painkiller).",
    "aceclofenac": "Relieves pain and swelling in arthritis and similar joint conditions (an NSAID painkiller).",
    "naproxen": "Relieves pain and swelling, for example in arthritis or period pain (an NSAID painkiller).",
    "etoricoxib": "Relieves pain and swelling in arthritis and gout attacks (an NSAID painkiller).",
    "mefenamicacid": "Relieves mild to moderate pain, including period pain (an NSAID painkiller).",
    "aspirin": "At low doses, helps prevent blood clots in people at risk of heart attack or stroke; at higher doses, relieves pain and fever.",
    "tramadol": "Relieves moderate to severe pain (an opioid painkiller).",
    "thiocolchicoside": "Relaxes painful muscle spasm.",
    "serratiopeptidase": "An enzyme often added to painkillers to help reduce swelling.",
    "drotaverine": "Relieves cramping pain of the stomach, gut or urinary tract.",
    "dicyclomine": "Relieves stomach and gut cramps.",
    # Allergy, cough and cold
    "cetirizine": "Relieves allergy symptoms such as sneezing, runny nose, itchy eyes and hives (an antihistamine).",
    "levocetirizine": "Relieves allergy symptoms such as sneezing, runny nose, itchy eyes and hives (an antihistamine).",
    "fexofenadine": "Relieves hay fever and hives (a non-drowsy antihistamine).",
    "loratadine": "Relieves hay fever and hives (an antihistamine).",
    "desloratadine": "Relieves hay fever and hives (an antihistamine).",
    "chlorpheniramine": "Relieves allergy and cold symptoms such as sneezing and runny nose (an antihistamine).",
    "montelukast": "Helps prevent asthma symptoms and relieves seasonal allergy symptoms.",
    "phenylephrine": "Relieves a blocked nose (a decongestant).",
    "ambroxol": "Loosens thick mucus to make coughing it up easier.",
    "guaifenesin": "Loosens chest mucus to make coughing it up easier.",
    "dextromethorphan": "Relieves a dry, tickly cough.",
    "salbutamol": "Opens the airways to relieve wheeze and breathlessness in asthma and COPD.",
    "budesonide": "A steroid that reduces airway or nasal inflammation, for example in asthma.",
    # Antibiotics, antifungals, antivirals, antiparasitics
    "amoxicillin": "Treats bacterial infections such as ear, chest, throat and urinary infections (an antibiotic).",
    "clavulanicacid": "Added to amoxicillin so it works against more bacteria; has no antibiotic effect on its own.",
    "azithromycin": "Treats bacterial infections of the chest, throat, ear and skin (an antibiotic).",
    "clarithromycin": "Treats bacterial infections of the chest, throat and skin (an antibiotic).",
    "cefixime": "Treats bacterial infections such as urinary, chest and throat infections and typhoid (an antibiotic).",
    "cefuroxime": "Treats bacterial infections of the chest, ear, throat, skin and urinary tract (an antibiotic).",
    "cefpodoxime": "Treats bacterial infections of the chest, throat and urinary tract (an antibiotic).",
    "cephalexin": "Treats bacterial infections of the skin, chest and urinary tract (an antibiotic).",
    "ciprofloxacin": "Treats bacterial infections such as urinary, gut and some chest infections (an antibiotic).",
    "ofloxacin": "Treats bacterial infections such as urinary and some chest and gut infections (an antibiotic).",
    "levofloxacin": "Treats bacterial infections such as pneumonia, sinus and urinary infections (an antibiotic).",
    "doxycycline": "Treats bacterial infections such as chest infections and acne (an antibiotic).",
    "metronidazole": "Treats certain bacterial and parasitic infections, for example of the gut or vagina.",
    "ornidazole": "Treats certain bacterial and parasitic infections, for example amoebic dysentery.",
    "nitrofurantoin": "Treats and helps prevent bladder (urinary tract) infections (an antibiotic).",
    "linezolid": "Treats serious bacterial infections (an antibiotic).",
    "fluconazole": "Treats fungal infections such as thrush (an antifungal).",
    "itraconazole": "Treats fungal infections of the skin, nails and other areas (an antifungal).",
    "terbinafine": "Treats fungal infections of the skin and nails (an antifungal).",
    "clotrimazole": "Treats fungal skin and vaginal infections (an antifungal).",
    "ketoconazole": "Treats fungal skin and scalp infections, including dandruff (an antifungal).",
    "aciclovir": "Treats herpes virus infections such as cold sores, genital herpes, shingles and chickenpox (an antiviral).",
    "valaciclovir": "Treats herpes virus infections such as cold sores, genital herpes and shingles (an antiviral).",
    "oseltamivir": "Treats and helps prevent influenza (flu) (an antiviral).",
    "albendazole": "Treats worm infections of the gut (a deworming medicine).",
    "ivermectin": "Treats certain worm and parasite infections, and scabies.",
    "hydroxychloroquine": "Treats rheumatoid arthritis and lupus, and is used for malaria.",
    "mupirocin": "Treats bacterial skin infections such as impetigo (an antibiotic cream or ointment).",
    # Stomach and gut
    "pantoprazole": "Reduces stomach acid to treat acidity, heartburn, acid reflux and stomach ulcers.",
    "omeprazole": "Reduces stomach acid to treat acidity, heartburn, acid reflux and stomach ulcers.",
    "esomeprazole": "Reduces stomach acid to treat acidity, heartburn, acid reflux and stomach ulcers.",
    "rabeprazole": "Reduces stomach acid to treat acidity, heartburn, acid reflux and stomach ulcers.",
    "lansoprazole": "Reduces stomach acid to treat acidity, heartburn, acid reflux and stomach ulcers.",
    "famotidine": "Reduces stomach acid to treat acidity, heartburn and stomach ulcers.",
    "ranitidine": "Reduces stomach acid to treat acidity, heartburn and stomach ulcers.",
    "sucralfate": "Coats and protects stomach and duodenal ulcers while they heal.",
    "domperidone": "Relieves nausea and vomiting, and bloating or fullness after meals.",
    "ondansetron": "Prevents and treats nausea and vomiting.",
    "itopride": "Relieves bloating, fullness and nausea from slow stomach emptying.",
    "levosulpiride": "Relieves bloating, nausea and indigestion.",
    "loperamide": "Relieves diarrhoea.",
    "lactulose": "Relieves constipation (a laxative).",
    "bisacodyl": "Relieves constipation (a laxative).",
    "rifaximin": "Treats travellers' diarrhoea and some other gut conditions (an antibiotic that stays in the gut).",
    "ursodeoxycholicacid": "Dissolves some types of gallstones and treats certain liver conditions.",
    # Diabetes
    "metformin": "Lowers blood sugar in type 2 diabetes.",
    "glimepiride": "Lowers blood sugar in type 2 diabetes.",
    "gliclazide": "Lowers blood sugar in type 2 diabetes.",
    "glipizide": "Lowers blood sugar in type 2 diabetes.",
    "sitagliptin": "Lowers blood sugar in type 2 diabetes.",
    "vildagliptin": "Lowers blood sugar in type 2 diabetes.",
    "teneligliptin": "Lowers blood sugar in type 2 diabetes.",
    "linagliptin": "Lowers blood sugar in type 2 diabetes.",
    "dapagliflozin": "Lowers blood sugar in type 2 diabetes; also used in some heart failure and kidney conditions.",
    "empagliflozin": "Lowers blood sugar in type 2 diabetes; also used in some heart failure conditions.",
    "voglibose": "Lowers the rise in blood sugar after meals in diabetes.",
    "pioglitazone": "Lowers blood sugar in type 2 diabetes.",
    # Heart, blood pressure, cholesterol
    "atorvastatin": "Lowers cholesterol and helps reduce the risk of heart attack and stroke.",
    "rosuvastatin": "Lowers cholesterol and helps reduce the risk of heart attack and stroke.",
    "amlodipine": "Lowers high blood pressure and helps prevent chest pain (angina).",
    "cilnidipine": "Lowers high blood pressure.",
    "nifedipine": "Lowers high blood pressure and helps prevent chest pain (angina).",
    "telmisartan": "Lowers high blood pressure.",
    "losartan": "Lowers high blood pressure and helps protect the kidneys in some people with diabetes.",
    "olmesartan": "Lowers high blood pressure.",
    "valsartan": "Lowers high blood pressure and treats heart failure.",
    "ramipril": "Lowers high blood pressure and treats heart failure.",
    "enalapril": "Lowers high blood pressure and treats heart failure.",
    "metoprolol": "Lowers high blood pressure, helps prevent chest pain and treats some heart rhythm problems (a beta blocker).",
    "atenolol": "Lowers high blood pressure and helps prevent chest pain (a beta blocker).",
    "bisoprolol": "Lowers high blood pressure and treats heart failure (a beta blocker).",
    "nebivolol": "Lowers high blood pressure (a beta blocker).",
    "propranolol": "Lowers high blood pressure, and is used to prevent migraine and control some tremors (a beta blocker).",
    "hydrochlorothiazide": "Lowers high blood pressure by helping the body remove extra water and salt (a diuretic).",
    "chlorthalidone": "Lowers high blood pressure by helping the body remove extra water and salt (a diuretic).",
    "furosemide": "Removes extra fluid from the body, for example swelling from heart, liver or kidney problems (a diuretic).",
    "torasemide": "Removes extra fluid from the body and lowers blood pressure (a diuretic).",
    "spironolactone": "Treats fluid build-up and heart failure, and lowers blood pressure (a diuretic).",
    "clopidogrel": "Helps prevent blood clots in people who have had a heart attack, stroke or stent.",
    "warfarin": "Helps prevent and treat harmful blood clots (an anticoagulant that needs regular blood tests).",
    # Hormones, steroids
    "levothyroxine": "Replaces thyroid hormone when the thyroid is underactive (hypothyroidism).",
    "prednisolone": "A steroid that reduces inflammation and calms the immune system, used in many conditions such as asthma, allergies and arthritis.",
    "methylprednisolone": "A steroid that reduces inflammation and calms the immune system, used in many inflammatory conditions.",
    "dexamethasone": "A steroid that reduces inflammation and calms the immune system, used in many inflammatory and allergic conditions.",
    "deflazacort": "A steroid that reduces inflammation and calms the immune system, used in many inflammatory conditions.",
    # Brain, nerves, mood, sleep
    "pregabalin": "Relieves nerve pain, and is used for some types of seizures and anxiety.",
    "gabapentin": "Relieves nerve pain and helps control some types of seizures.",
    "escitalopram": "Treats depression and anxiety (an SSRI antidepressant).",
    "sertraline": "Treats depression, anxiety and some related conditions (an SSRI antidepressant).",
    "fluoxetine": "Treats depression and some related conditions (an SSRI antidepressant).",
    "amitriptyline": "Treats depression, and at low doses helps prevent nerve pain and migraine.",
    "alprazolam": "Short-term relief of anxiety (a sedative that can cause dependence).",
    "clonazepam": "Treats some seizures and panic disorder (a sedative that can cause dependence).",
    "betahistine": "Reduces dizziness (vertigo) attacks in Ménière's disease.",
    "cinnarizine": "Relieves dizziness, vertigo and travel sickness.",
    "meclozine": "Relieves nausea, dizziness and travel sickness.",
    "doxylamine": "Short-term relief of sleeplessness; also combined with vitamin B6 for morning sickness.",
    # Urology
    "tamsulosin": "Eases urine flow in men with an enlarged prostate.",
    "finasteride": "Shrinks an enlarged prostate; at a lower dose, treats male-pattern hair loss.",
    "sildenafil": "Treats erectile dysfunction; also used for high blood pressure in the lungs.",
    "tadalafil": "Treats erectile dysfunction and symptoms of an enlarged prostate.",
    # Gout
    "allopurinol": "Lowers uric acid to help prevent gout attacks and some kidney stones.",
    "febuxostat": "Lowers uric acid to help prevent gout attacks.",
    "colchicine": "Treats and helps prevent gout attacks.",
    # Vitamins and minerals
    "cholecalciferol": "Vitamin D3: prevents and treats vitamin D deficiency and supports bone health.",
    "folicacid": "Treats and prevents folic acid deficiency and some anaemias; recommended before and in early pregnancy.",
    "methylcobalamin": "A form of vitamin B12, used for B12 deficiency and nerve health.",
    "cyanocobalamin": "Vitamin B12, used for B12 deficiency.",
    "pyridoxine": "Vitamin B6, used for B6 deficiency.",
    "calciumcarbonate": "Calcium supplement for bone health; also neutralises stomach acid.",
    "ferrousfumarate": "Iron supplement to prevent and treat iron-deficiency anaemia.",
    "ferrousascorbate": "Iron supplement to prevent and treat iron-deficiency anaemia.",
    "zincsulphate": "Zinc supplement; also given with ORS for diarrhoea in children.",
}

ALIASES = {
    "acetaminophen": "paracetamol",
    "amoxycillin": "amoxicillin",
    "potassiumclavulanate": "clavulanicacid",
    "clavulanate": "clavulanicacid",
    "albuterol": "salbutamol",
    "levosalbutamol": "salbutamol",
    "chlorphenamine": "chlorpheniramine",
    "cefalexin": "cephalexin",
    "acyclovir": "aciclovir",
    "valacyclovir": "valaciclovir",
    "ursodiol": "ursodeoxycholicacid",
    "torsemide": "torasemide",
    "meclizine": "meclozine",
    "thyroxine": "levothyroxine",
    "vitamind3": "cholecalciferol",
    "mecobalamin": "methylcobalamin",
    "vitaminb12": "cyanocobalamin",
    "vitaminb6": "pyridoxine",
    "zincsulfate": "zincsulphate",
    "dicycloverine": "dicyclomine",
}

# Salt and hydrate words that do not change what the ingredient is used for.
_SALT_WORDS = {
    "hydrochloride", "hcl", "sodium", "potassium", "sulphate", "sulfate", "maleate",
    "besylate", "besilate", "succinate", "tartrate", "citrate", "fumarate", "mesylate",
    "mesilate", "acetate", "phosphate", "monohydrate", "dihydrate", "trihydrate",
    "hemihydrate", "sesquihydrate", "hydrobromide", "bromide", "disodium", "dipropionate",
    "propionate", "magnesium", "calcium", "trometamol", "medoxomil", "cilexetil",
    "hyclate", "anhydrous", "free", "base", "ip", "bp", "usp",
}


def _lookup(key):
    if key in ALIASES:
        key = ALIASES[key]
    return USES.get(key) if key else None


def ingredient_use(name):
    """Plain-language use of one ingredient name, or None when we have no reliable text."""
    text = _lookup(normalize_medication_text(name))
    if text:
        return text
    words = [w for w in re.findall(r"[a-z0-9]+", str(name or "").casefold())]
    stripped = [w for w in words if w not in _SALT_WORDS]
    if stripped and stripped != words:
        return _lookup("".join(stripped))
    return None


def ingredient_uses(composition):
    """[{ingredient, use}] for each recorded ingredient; ``use`` is None when we have no reliable text.

    Text is never guessed. When no ingredient has text the page shows no Uses box.
    """
    result = []
    seen = set()
    for part in parse_composition(composition):
        key = part["ingredient_name_norm"]
        if key in seen:
            continue
        seen.add(key)
        result.append({
            "ingredient": part["ingredient_name"].strip().title(),
            "use": ingredient_use(part["ingredient_name"]),
        })
    return result if any(item["use"] for item in result) else []


ONEMG_SEARCH_URL = "https://www.1mg.com/search/all?name={query}"
PHARMACY_MAP_URL = "https://www.google.com/maps/search/pharmacy+near+me"


def onemg_search_query(medicine):
    """Name plus strength (when the name lacks it), so 1mg lists this exact product first."""
    name = str(medicine.get("medicine_name") or "").strip()
    strength = str(medicine.get("strength") or "").strip()
    if strength and normalize_medication_text(strength) not in normalize_medication_text(name):
        name = f"{name} {strength}".strip()
    return name


def buy_links(medicine):
    """Outbound links for a medicine row (dict-like).

    The catalog holds no 1mg product id or page URL (photos are 1mg image-CDN
    hashes, which do not identify a product page), so the 1mg link is a search
    for this medicine's own name and strength, marked ``exact: False``.
    US-only reference rows (category "External") get no Indian pharmacy link.
    """
    links = {"pharmacy_map": {"url": PHARMACY_MAP_URL}}
    category = str(medicine.get("medicine_category") or "").casefold()
    query = onemg_search_query(medicine)
    if query and category != "external":
        links["onemg"] = {
            "url": ONEMG_SEARCH_URL.format(query=quote_plus(query)),
            "exact": False,
            "query": query,
        }
    return links
