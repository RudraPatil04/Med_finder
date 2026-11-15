"""Offline tests for home-search type-ahead suggestions."""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import database
from backend.medicine_data import make_product_key, normalize_medication_text
from backend.services import medicine_service


class SearchSuggestionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "suggest.db"
        connection = sqlite3.connect(self.db_path)
        try:
            connection.executescript(
                """CREATE TABLE medicines (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    medicine_name TEXT NOT NULL,
                    brand_name TEXT,
                    strength TEXT,
                    dosage_form TEXT,
                    medicine_category TEXT,
                    composition TEXT,
                    price REAL,
                    price_source TEXT,
                    price_checked_at TEXT,
                    release_type TEXT,
                    pack_size TEXT,
                    composition_complete INTEGER NOT NULL DEFAULT 0,
                    product_key TEXT,
                    search_norm TEXT
                );
                CREATE TABLE medicine_ingredients (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    medicine_id INTEGER NOT NULL,
                    ingredient_name TEXT NOT NULL,
                    ingredient_name_norm TEXT NOT NULL,
                    ingredient_strength TEXT NOT NULL,
                    ingredient_strength_norm TEXT NOT NULL
                );"""
            )
            connection.commit()
        finally:
            connection.close()
        self.db_patch = patch.object(database, "MEDICINE_DATABASE", self.db_path)
        self.db_patch.start()
        medicine_service._priced_option_cache.clear()

    def tearDown(self):
        medicine_service._priced_option_cache.clear()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _add(self, name, brand, strength, form, pack, price, ingredients):
        composition = " + ".join(f"{ingredient} ({dose})" for ingredient, dose in ingredients)
        connection = sqlite3.connect(self.db_path)
        try:
            medicine_id = connection.execute(
                "INSERT INTO medicines(medicine_name, brand_name, strength, dosage_form, composition, "
                "price, pack_size, composition_complete, product_key, search_norm) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (
                    name, brand, strength, form, composition, price, pack,
                    make_product_key(name, brand, strength, form, composition, None, pack),
                    normalize_medication_text(f"{name} {brand}"),
                ),
            ).lastrowid
            for ingredient, dose in ingredients:
                connection.execute(
                    "INSERT INTO medicine_ingredients(medicine_id, ingredient_name, ingredient_name_norm, "
                    "ingredient_strength, ingredient_strength_norm) VALUES (?, ?, ?, ?, ?)",
                    (medicine_id, ingredient, normalize_medication_text(ingredient),
                     dose, normalize_medication_text(dose)),
                )
            connection.commit()
        finally:
            connection.close()
        return medicine_id

    def _seed_naproxen_group(self):
        ingredients = [("Naproxen", "500mg"), ("Domperidone", "10mg")]
        self.naxdom = self._add(
            "Naxdom 500 Tablet", "Naxdom", "500mg/10mg", "Tablet", "10 tablets", 130, ingredients)
        self.pandex = self._add(
            "Pandex 500 Tablet", "Pandex", "500mg/10mg", "Tablet", "10 tablets", 69, ingredients)

    def test_suggests_medicines_that_have_a_priced_comparison(self):
        self._seed_naproxen_group()
        self._add("Metoz 2.5 Tablet", "Metoz", "2.5mg", "Tablet", "10 tablets", 40,
                  [("Metolazone", "2.5mg")])

        names = [item["name"] for item in medicine_service.suggest_functional_medicines("nax")]
        self.assertEqual(names, ["Naxdom 500 Tablet"])
        # The priced product in the same group is a suggestion too.
        names = [item["name"] for item in medicine_service.suggest_functional_medicines("pand")]
        self.assertEqual(names, ["Pandex 500 Tablet"])
        # A medicine with no same-composition product is not offered.
        self.assertEqual(medicine_service.suggest_functional_medicines("metoz"), [])

    def test_suggestion_reports_price_per_unit_and_provenance(self):
        self._seed_naproxen_group()
        [suggestion] = medicine_service.suggest_functional_medicines("naxdom")
        self.assertEqual(suggestion["price"], 130)
        self.assertEqual(suggestion["price_per_unit"], 13.0)
        self.assertEqual(suggestion["unit"], "tablet")
        self.assertFalse(suggestion["price_is_current"])
        self.assertEqual(suggestion["price_label"], "Catalog price")

    def test_price_without_a_pack_size_is_not_a_comparison(self):
        ingredients = [("Paracetamol", "650mg")]
        self._add("Nofemol 650mg Tablet", "Nofemol", "650mg", "Tablet", None, 12.41, ingredients)
        self._add("Dolo 650 Tablet", "Dolo", "650mg", "Tablet", None, 31.94, ingredients)
        self.assertEqual(medicine_service.suggest_functional_medicines("nofemol"), [])

    def test_restricted_products_are_never_suggested(self):
        ingredients = [("Warfarin", "1mg")]
        self._add("Cofarin 1mg Tablet", "Cofarin", "1mg", "Tablet", "10 tablets", 40, ingredients)
        self._add("Warf 1mg Tablet", "Warf", "1mg", "Tablet", "10 tablets", 30, ingredients)
        self.assertEqual(medicine_service.suggest_functional_medicines("cofarin"), [])

    def test_single_letter_prefix_matches_and_unknown_queries_return_nothing(self):
        self._seed_naproxen_group()
        names = [item["name"] for item in medicine_service.suggest_functional_medicines("n")]
        self.assertEqual(names, ["Naxdom 500 Tablet"])
        names = [item["name"] for item in medicine_service.suggest_functional_medicines("p")]
        self.assertEqual(names, ["Pandex 500 Tablet"])
        self.assertEqual(medicine_service.suggest_functional_medicines("zorblax"), [])

    def test_empty_query_returns_starter_recommendations(self):
        paracetamol = [("Paracetamol", "650mg")]
        self._add("Dolo 650 Tablet", "Dolo", "650mg", "Tablet", "15 tablets", 31.94, paracetamol)
        self._add("Calpol 650 Tablet", "Calpol", "650mg", "Tablet", "15 tablets", 25, paracetamol)

        # One recommendation per known seed brand, in seed order.
        starter = medicine_service.suggest_functional_medicines("")
        self.assertEqual([item["name"] for item in starter], ["Dolo 650 Tablet", "Calpol 650 Tablet"])
        self.assertEqual(medicine_service.suggest_functional_medicines("   "), starter)

    def test_starter_list_only_includes_known_seed_brands(self):
        self._seed_naproxen_group()
        self.assertEqual(medicine_service.suggest_functional_medicines(""), [])


if __name__ == "__main__":
    unittest.main()
