"""
============================================================
MED FINDER
RUN COMPLETE ETL PIPELINE
Version : 1.0
Author : Naman
============================================================
"""

import time

from etl.load_dataset import load_dataset
from etl.clean_dataset import clean_dataset
from etl.extract_fields import extract_fields
from etl.build_database import build_database


def main():

    start = time.time()

    print("\n" + "=" * 70)
    print("MED FINDER - ETL PIPELINE")
    print("=" * 70)

    # --------------------------------------------------
    # Step 1
    # --------------------------------------------------

    print("\n[1/4] Loading Dataset...\n")

    df = load_dataset()

    if df is None:
        raise Exception("Dataset loading failed.")

    # --------------------------------------------------
    # Step 2
    # --------------------------------------------------

    print("\n[2/4] Cleaning Dataset...\n")

    df = clean_dataset(df)

    # --------------------------------------------------
    # Step 3
    # --------------------------------------------------

    print("\n[3/4] Extracting Fields...\n")

    df = extract_fields(df)

    # --------------------------------------------------
    # Step 4
    # --------------------------------------------------

    print("\n[4/4] Building SQLite Database...\n")

    build_database(df)

    end = time.time()

    print("\n" + "=" * 70)
    print("ETL PIPELINE COMPLETED SUCCESSFULLY")
    print("=" * 70)

    print(f"\nExecution Time : {end-start:.2f} seconds")


if __name__ == "__main__":

    main()