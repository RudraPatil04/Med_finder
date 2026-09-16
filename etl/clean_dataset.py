"""
============================================================
MED FINDER
ETL MODULE : CLEAN DATASET
Version : 2.0
Author : Naman

Purpose:
Perform generic cleaning on the medicine dataset.

Input:
Pandas DataFrame

Output:
Cleaned Pandas DataFrame

============================================================
"""

import pandas as pd


def clean_dataset(df):
    """
    Clean the medicine dataset.

    Parameters
    ----------
    df : pandas.DataFrame

    Returns
    -------
    pandas.DataFrame
    """

    print("\n" + "=" * 70)
    print("MED FINDER - CLEAN DATASET")
    print("=" * 70)

    print("\nCleaning dataset...\n")

    # ----------------------------------------------------
    # Column Names
    # ----------------------------------------------------

    df.columns = (
        df.columns
        .str.strip()
        .str.replace(" ", "_", regex=False)
        .str.replace("?", "", regex=False)
    )

    print("✓ Column names standardized")

    # ----------------------------------------------------
    # Remove Extra Spaces
    # ----------------------------------------------------

    object_columns = df.select_dtypes(include=["object"]).columns

    for column in object_columns:

        df[column] = (

            df[column]

            .fillna("")

            .astype(str)

            .str.strip()

        )

    print("✓ Text columns cleaned")

    # ----------------------------------------------------
    # Price
    # ----------------------------------------------------

    if "Price" in df.columns:

        df["Price"] = (

            df["Price"]

            .astype(str)

            .str.replace("₹", "", regex=False)

            .str.replace("?", "", regex=False)

            .str.replace(",", "", regex=False)

            .str.strip()

        )

        df["Price"] = pd.to_numeric(

            df["Price"],

            errors="coerce"

        )

        print("✓ Price converted to numeric")

    # ----------------------------------------------------
    # Prescription Column
    # ----------------------------------------------------

    if "Is_Prescription_Required" in df.columns:

        df["Is_Prescription_Required"] = (

            df["Is_Prescription_Required"]

            .astype(str)

            .str.upper()

            .str.strip()

        )

        print("✓ Prescription column standardized")

    # ----------------------------------------------------
    # Empty Values
    # ----------------------------------------------------

    df.replace(

        {

            "": pd.NA,

            "None": pd.NA,

            "nan": pd.NA,

            "NULL": pd.NA,

            "not available": pd.NA

        },

        inplace=True

    )

    print("✓ Missing values normalized")

    # ----------------------------------------------------
    # Duplicate Rows
    # ----------------------------------------------------

    before = len(df)

    df.drop_duplicates(inplace=True)

    after = len(df)

    print(f"✓ Duplicate rows removed : {before-after}")

    print("\nCleaning Completed Successfully")

    print("=" * 70)

    return df