"""
============================================================
MED FINDER
ETL MODULE : LOAD DATASET
Version : 2.0
Author : Naman

Purpose:
Load the original medicine dataset.

Input:
datasets/India Medicines and Drug Info Dataset.csv

Output:
Pandas DataFrame

============================================================
"""

import sys
from pathlib import Path
import pandas as pd

# ------------------------------------------------------------
# Add Project Root
# ------------------------------------------------------------

ROOT_DIR = Path(__file__).resolve().parent.parent

if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from backend.config import ORIGINAL_DATASET


# ============================================================
# LOAD DATASET
# ============================================================

def load_dataset(limit=None):
    """
    Load medicine dataset.

    Parameters
    ----------
    limit : int | None
        Number of rows to load.
        None = Load complete dataset.

    Returns
    -------
    pandas.DataFrame
    """

    print("\n" + "=" * 70)
    print("MED FINDER - LOAD DATASET")
    print("=" * 70)

    try:

        df = pd.read_csv(
            ORIGINAL_DATASET,
            sep="\t",
            encoding="cp1252",
            engine="python",
            nrows=limit
        )

        print(f"Dataset Loaded Successfully")
        print(f"Rows    : {len(df):,}")
        print(f"Columns : {len(df.columns)}")

        return df

    except FileNotFoundError:

        print("Dataset not found.")

        return None

    except Exception as e:

        print("Error loading dataset")

        print(e)

        return None


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    df = load_dataset()

    if df is not None:

        print()

        print(df.head())