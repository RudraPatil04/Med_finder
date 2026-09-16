# extract_fields.py
import re
import pandas as pd

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

def extract_fields(df):
    df["Brand_Name"]=df["Medicine_Name"].apply(extract_brand_name)
    df["Strength"]=df["Medicine_Name"].apply(extract_strength_from_name)
    m=df["Strength"].isna()
    df.loc[m,"Strength"]=df.loc[m,"Composition"].apply(extract_strength_from_composition)
    df["Dosage_Form"]=df["Medicine_Name"].apply(extract_dosage_form)
    df["Medicine_Category"]=df["Dosage_Form"].apply(extract_category)
    return df
