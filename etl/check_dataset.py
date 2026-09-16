"""
============================================================
MED FINDER
ETL MODULE : CHECK DATASET
Version : 1.0
Author : Naman

Purpose:
Checks dataset integrity before ETL starts.

Input:
Original medicine dataset

Output:
Validation report

============================================================
"""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent

if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from backend.config import ORIGINAL_DATASET


EXPECTED_COLUMNS = 9


def check_dataset():

    print("\n" + "=" * 70)
    print("MED FINDER - DATASET VALIDATION")
    print("=" * 70)

    total_rows = 0
    valid_rows = 0
    invalid_rows = 0

    invalid_line_numbers = []

    with open(
        ORIGINAL_DATASET,
        "r",
        encoding="cp1252",
        errors="replace"
    ) as file:

        for line_number, line in enumerate(file, start=1):

            total_rows += 1

            columns = line.rstrip("\n").split("\t")

            if len(columns) == EXPECTED_COLUMNS:

                valid_rows += 1

            else:

                invalid_rows += 1

                invalid_line_numbers.append(line_number)

    print()

    print(f"Total Lines      : {total_rows:,}")
    print(f"Valid Lines      : {valid_rows:,}")
    print(f"Invalid Lines    : {invalid_rows:,}")

    print()

    if invalid_rows == 0:

        print("Dataset Structure : PASSED ✅")

    else:

        print("Dataset Structure : FAILED ❌")

        print()

        print("Problematic Lines")

        for line in invalid_line_numbers[:20]:

            print(f"Line {line}")

        if len(invalid_line_numbers) > 20:

            print("...")

    print("\n" + "=" * 70)


if __name__ == "__main__":

    check_dataset()