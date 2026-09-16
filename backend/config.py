"""
============================================================
MED FINDER
CONFIGURATION
Version : 1.0
============================================================
"""

from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent

DATASET_DIR = ROOT_DIR / "datasets"

DATABASE_DIR = ROOT_DIR / "database"

REPORT_DIR = ROOT_DIR / "reports"

UPLOAD_DIR = ROOT_DIR / "uploads"

MODEL_DIR = ROOT_DIR / "models"

# -------------------------------------------------------
# DATASETS
# -------------------------------------------------------

ORIGINAL_DATASET = DATASET_DIR / "India Medicines and Drug Info Dataset.csv"

DISEASE_DATASET = DATASET_DIR / "disease_knowledge_base.xlsx"

DISEASE_PREDICTION = DATASET_DIR / "disease_prediction.csv"

CLEAN_DATASET = DATASET_DIR / "clean_dataset.csv"

MEDICINES_DATASET = DATASET_DIR / "medicines_clean.csv"

# -------------------------------------------------------
# DATABASE
# -------------------------------------------------------

MEDICINE_DATABASE = DATABASE_DIR / "medfinder.db"

# -------------------------------------------------------
# CREATE FOLDERS
# -------------------------------------------------------

DATABASE_DIR.mkdir(exist_ok=True)

REPORT_DIR.mkdir(exist_ok=True)

UPLOAD_DIR.mkdir(exist_ok=True)

MODEL_DIR.mkdir(exist_ok=True)