"""Offline tests for search ranking, strength parsing and spelling correction."""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import database
from backend.medicine_data import parse_composition
from backend.services import medicine_service
from backend.services.search_ranking import Query, split_letters_digits

ROWS = [
    # name, brand, strength, form, composition (None = not recorded)
    ("Telma 40 Tablet", "Telma", "40mg", "Tablet", "Telmisartan (40mg)"),
    ("Detelma 40 Tablet", "Detelma", "40mg", "Tablet", "Telmisartan (40mg)"),
    ("Telma 20 Tablet", "Telma", "20mg", "Tablet", "Telmisartan (20mg)"),
    ("PAN 40 Tablet", "PAN", "40mg", "Tablet", "Pantoprazole (40mg)"),
    ("Abipan 40mg Tablet", "Abipan", "40mg", "Tablet", "Pantoprazole (40mg)"),
    ("AMlong Tablet", "AMlong", "5mg", "Tablet", "Amlodipine (5mg)"),
    ("S-Amlong 5 Tablet", "S-Amlong", "5mg", "Tablet", "Amlodipine (5mg)"),
    ("AMlong 10 Tablet", "AMlong", "10mg", "Tablet", "Amlodipine (10mg)"),
    ("Atorva Tablet", "Atorva", "10mg", "Tablet", "Atorvastatin (10mg)"),
    ("A Vin 10mg Tablet", "A Vin", "10mg", "Tablet", "Atorvastatin (10mg)"),
    ("Ondem 4 Tablet", "Ondem", "4mg", "Tablet", "Ondansetron (4mg)"),
    ("Ondem 8mg Injection", "Ondem", "8mg", "Injection", None),
    ("Augmentin 625 Duo Tablet", "Augmentin", "500mg", "Tablet",
     "Amoxycillin (500mg) + Clavulanic Acid (125mg)"),
    ("Augmentin 375 Tablet", "Augmentin", "250mg", "Tablet",
     "Amoxycillin (250mg) + Clavulanic Acid (125mg)"),
    ("Augmarc 625 Tablet", "Augmarc", "500mg", "Tablet",
     "Amoxycillin (500mg) + Clavulanic Acid (125mg)"),
    ("Paracetamol 500mg Tablet", "Paracetamol", "500mg", "Tablet", "Paracetamol (500mg)"),
    ("Calpol 500mg Tablet", "Calpol", "500mg", "Tablet", "Paracetamol (500mg)"),
    ("Electril Plus Tablet", "Electril Plus", "100mg/2mg", "Tablet",
     "Something (100mg) + Trihexyphenidyl (2mg)"),
]


class SearchRankingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "ranking.db"
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """CREATE TABLE medicines (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    medicine_name TEXT NOT NULL, brand_name TEXT, strength TEXT,
                    dosage_form TEXT, medicine_category TEXT, composition TEXT,
                    price REAL, prescription_required TEXT, image_url TEXT,
                    release_type TEXT, pack_size TEXT,
                    composition_complete INTEGER NOT NULL DEFAULT 0,
                    price_source TEXT, price_checked_at TEXT, rxnorm_rxcui TEXT,
                    product_key TEXT, source TEXT DEFAULT 'local'
                )""")
            connection.execute("""CREATE TABLE medicine_ingredients (
                id INTEGER PRIMARY KEY AUTOINCREMENT, medicine_id INTEGER NOT NULL,
                ingredient_name TEXT NOT NULL, ingredient_name_norm TEXT NOT NULL,
                ingredient_strength TEXT NOT NULL, ingredient_strength_norm TEXT NOT NULL
            )""")
            for name, brand, strength, form, composition in ROWS:
                parsed = parse_composition(composition)
                medicine_id = connection.execute(
                    """INSERT INTO medicines(medicine_name, brand_name, strength, dosage_form,
                        composition, price, pack_size, composition_complete)
                       VALUES (?, ?, ?, ?, ?, 20, '10 tablets', ?)""",
                    (name, brand, strength, form, composition, int(bool(parsed))),
                ).lastrowid
                for item in parsed:
                    connection.execute(
                        """INSERT INTO medicine_ingredients(medicine_id, ingredient_name,
                            ingredient_name_norm, ingredient_strength, ingredient_strength_norm)
                           VALUES (?, ?, ?, ?, ?)""",
                        (medicine_id, item["ingredient_name"], item["ingredient_name_norm"],
                         item["ingredient_strength"], item["ingredient_strength_norm"]),
                    )
            connection.commit()
        finally:
            connection.close()
        self.patches = [
            patch.object(database, "MEDICINE_DATABASE", self.db_path),
            patch.object(medicine_service, "search_catalog", return_value=[]),
            patch.object(medicine_service, "search_pharmeasy_catalog", return_value=[]),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.temp_dir.cleanup()

    def names(self, query, **kwargs):
        rows = medicine_service.search_medicines(query, enable_api_fallback=False, **kwargs)
        return [row["medicine_name"] for row in rows]

    def detailed(self, query):
        return medicine_service.search_medicines_detailed(
            query, enable_api_fallback=False, allow_correction=True)

    def test_query_splits_glued_strengths_but_not_vitamin_names(self):
        self.assertEqual(split_letters_digits("pan40"), "pan 40")
        self.assertEqual(split_letters_digits("Telma-40"), "Telma 40")
        self.assertEqual(split_letters_digits("Vitamin B12"), "Vitamin B12")
        query = Query("Dolo650")
        self.assertEqual((query.alpha, query.number), (["dolo"], "650"))

    def test_exact_brand_and_strength_outrank_lookalikes(self):
        self.assertEqual(self.names("Telma 40")[0], "Telma 40 Tablet")
        self.assertNotIn("Telma 20 Tablet", self.names("Telma 40")[:2])
        self.assertEqual(self.names("pan40")[0], "PAN 40 Tablet")
        self.assertEqual(self.names("Telma-40")[0], "Telma 40 Tablet")

    def test_strength_is_read_from_the_strength_field(self):
        # "AMlong Tablet" records 5mg only in its strength column.
        self.assertEqual(self.names("Amlong 5")[0], "AMlong Tablet")
        self.assertEqual(self.names("Atorva 10")[0], "Atorva Tablet")

    def test_brand_stem_is_not_treated_as_an_ingredient(self):
        self.assertIsNone(medicine_service._generic_key("Atorva 10"))
        self.assertEqual(medicine_service._generic_key("Paracetamol 500"), "paracetamol")
        self.assertEqual(medicine_service._generic_key("Amoxicillin"), "amoxycillin")

    def test_brand_rows_without_composition_are_not_dropped(self):
        names = self.names("Ondem")
        self.assertIn("Ondem 4 Tablet", names)
        self.assertIn("Ondem 8mg Injection", names)
        self.assertEqual(medicine_service._record_ingredient_keys(
            {"medicine_name": "Ondem 8mg Injection", "composition": None}), [])
        self.assertEqual(medicine_service._record_ingredient_keys(
            {"medicine_name": "atorvastatin 10 MG Oral Tablet", "composition": None}), ["atorvastatin"])

    def test_common_brand_family_leads_a_partial_query(self):
        self.assertTrue(self.names("Augm")[0].startswith("Augmentin"))

    def test_misspelling_is_corrected_and_reported(self):
        result = self.detailed("Paracetmol")
        self.assertEqual(result["corrected_query"], "paracetamol")
        self.assertTrue(result["rows"])
        result = self.detailed("Augmentine")
        self.assertEqual(result["corrected_query"], "augmentin")

    def test_correction_is_off_by_default_and_skipped_for_real_names(self):
        self.assertEqual(self.names("Paracetmol"), [])
        result = self.detailed("Calpol")
        self.assertIsNone(result["corrected_query"])

    def test_rare_lookalike_is_not_offered_for_an_unknown_brand(self):
        # "Electral" (ORS) must not silently become "Electril Plus".
        result = self.detailed("Electral")
        self.assertIsNone(result["corrected_query"])
        self.assertEqual(result["rows"], [])


if __name__ == "__main__":
    unittest.main()
