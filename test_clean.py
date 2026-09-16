from etl.load_dataset import load_dataset
from etl.clean_dataset import clean_dataset
from etl.extract_fields import extract_fields

import pandas as pd

pd.set_option("display.max_columns", None)
pd.set_option("display.width", None)

print("Loading Dataset...")

df = load_dataset()

if df is None:
    raise Exception("Dataset could not be loaded.")

print("Cleaning Dataset...")

df = clean_dataset(df)

print("Extracting Fields...")

df = extract_fields(df)

print()

print(
    df[
        [
            "Medicine_Name",
            "Brand_Name",
            "Strength",
            "Dosage_Form",
            "Medicine_Category",
            "Price"
        ]
    ].head(30)
)