"""Offline tests for the ingredient prefix repair."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.medicine_data import make_product_key, parse_composition
from etl import repair_ingredient_prefixes as repair


def _insert_medicine(connection, name, brand, strength, form, composition, pack="10 tablets"):
    key = make_product_key(name, brand, strength, form, composition, None, pack)
    cursor = connection.execute(
        "INSERT INTO medicines(medicine_name, brand_name, strength, dosage_form, composition, "
        "pack_size, product_key) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (name, brand, strength, form, composition, pack, key),
    )
    medicine_id = cursor.lastrowid
    for item in parse_composition(composition):
        connection.execute(
            "INSERT INTO medicine_ingredients(medicine_id, ingredient_name, ingredient_name_norm, "
            "ingredient_strength, ingredient_strength_norm) VALUES (?, ?, ?, ?, ?)",
            (medicine_id, item["ingredient_name"], item["ingredient_name_norm"],
             item["ingredient_strength"], item["ingredient_strength_norm"]),
        )
    connection.commit()
    return medicine_id


class IngredientPrefixRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "repair.db"
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(
            """CREATE TABLE medicines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                medicine_name TEXT NOT NULL,
                brand_name TEXT,
                strength TEXT,
                dosage_form TEXT,
                composition TEXT,
                release_type TEXT,
                pack_size TEXT,
                product_key TEXT
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

    def tearDown(self):
        self.connection.close()
        self.temp_dir.cleanup()

    def test_only_phrases_that_lead_several_ingredients_are_learned(self):
        prefixes = repair.learn_prefixes(
            {
                "Cure Healthcare Naproxen": 5,
                "Cure Healthcare Aspirin": 4,
                "Cure Healthcare Atorvastatin": 3,
                "One Off Naproxen": 7,
            },
            {"naproxen", "aspirin", "atorvastatin"},
        )
        self.assertEqual(
            sorted(prefixes["curehealthcare"]),
            ["aspirin", "atorvastatin", "naproxen"],
        )
        self.assertNotIn("oneoff", prefixes)

    def test_remainder_must_be_a_recorded_ingredient(self):
        prefixes = repair.learn_prefixes(
            {
                "Cure Healthcare Zorblax": 3,
                "Cure Healthcare Naproxen": 3,
                "Cure Healthcare Aspirin": 3,
            },
            {"naproxen", "aspirin"},
            min_ingredients=2,
        )
        self.assertEqual(sorted(prefixes["curehealthcare"]), ["aspirin", "naproxen"])
        self.assertIsNone(
            repair.repair_name("Cure Healthcare Zorblax", prefixes, {"naproxen", "aspirin"})
        )

    def test_real_multipart_drug_names_are_never_split(self):
        # `levocetirizine` is itself a catalogued ingredient, so the leading
        # word is part of the drug name, not a manufacturer phrase.
        known = {"levocetirizine", "methylprednisolone", "cetirizine", "prednisolone"}
        prefixes = repair.learn_prefixes(
            {
                "Levo Cetirizine": 60,
                "Methyl Prednisolone": 40,
                "Levocetirizine": 120,
            },
            known,
        )
        self.assertEqual(prefixes, {})
        self.assertIsNone(repair.repair_name("Levo Cetirizine", prefixes, known))

    def test_repair_component_keeps_the_strength_and_trailing_markers(self):
        renames = {"curehealthcarenaproxen": "Naproxen"}
        self.assertEqual(
            repair.repair_component("Cure Healthcare Naproxen (500mg)ADD", renames),
            "Naproxen (500mg)ADD",
        )
        self.assertEqual(
            repair.repair_component("Domperidone (10mg)", renames),
            "Domperidone (10mg)",
        )
        self.assertEqual(
            repair.repair_composition(
                "Cure Healthcare Naproxen (500mg) + Domperidone (10mg)", renames
            ),
            "Naproxen (500mg) + Domperidone (10mg)",
        )

    def _seed_known_ingredients(self, names):
        """Stock rows, so these names clear the 'real ingredient' threshold."""
        connection = self.connection
        medicine_id = connection.execute(
            "INSERT INTO medicines(medicine_name, brand_name) VALUES ('Stock', 'Stock')"
        ).lastrowid
        for name in names:
            for _ in range(repair.MIN_REMAINDER_ROWS):
                connection.execute(
                    "INSERT INTO medicine_ingredients(medicine_id, ingredient_name, "
                    "ingredient_name_norm, ingredient_strength, ingredient_strength_norm) "
                    "VALUES (?, ?, ?, '1mg', '1mg')",
                    (medicine_id, name.title(), name),
                )
        connection.commit()

    def _seed_prefixed_products(self):
        """Three manufactured-prefixed rows, enough to learn the phrase."""
        self._seed_known_ingredients(["naproxen", "aspirin", "atorvastatin", "domperidone"])
        return [
            _insert_medicine(
                self.connection, "Naxdom 500 Tablet", "Naxdom", "500mg/10mg", "Tablet",
                "Cure Healthcare Naproxen (500mg) + Domperidone (10mg)",
            ),
            _insert_medicine(
                self.connection, "Ecosprin 75 Tablet", "Ecosprin", "75mg", "Tablet",
                "Cure Healthcare Aspirin (75mg)",
            ),
            _insert_medicine(
                self.connection, "Atorva 10 Tablet", "Atorva", "10mg", "Tablet",
                "Cure Healthcare Atorvastatin (10mg)",
            ),
        ]

    def _learn(self):
        display_counts, norm_counts = repair.load_name_counts(self.connection)
        known = repair.known_ingredient_names(norm_counts)
        return repair.learn_prefixes(display_counts, known), known

    def test_repair_is_idempotent_and_recomputes_the_product_key(self):
        dirty_id, aspirin_id, atorva_id = self._seed_prefixed_products()

        prefixes, known = self._learn()
        self.assertIn("curehealthcare", prefixes)

        renames = repair.find_repairs(self.connection, prefixes, known)
        self.assertEqual(sorted(renames), sorted([dirty_id, aspirin_id, atorva_id]))
        plan = repair.plan_repairs(self.connection, renames)
        repair.apply_repairs(self.connection, plan)

        row = self.connection.execute(
            "SELECT composition, product_key FROM medicines WHERE id = ?", (dirty_id,)
        ).fetchone()
        self.assertEqual(row["composition"], "Naproxen (500mg) + Domperidone (10mg)")
        self.assertEqual(
            row["product_key"],
            make_product_key(
                "Naxdom 500 Tablet", "Naxdom", "500mg/10mg", "Tablet",
                "Naproxen (500mg) + Domperidone (10mg)", None, "10 tablets",
            ),
        )
        names = sorted(
            item["ingredient_name_norm"]
            for item in self.connection.execute(
                "SELECT ingredient_name_norm FROM medicine_ingredients WHERE medicine_id = ?",
                (dirty_id,),
            )
        )
        self.assertEqual(names, ["domperidone", "naproxen"])

        # A second pass has nothing left to do.
        prefixes, known = self._learn()
        self.assertEqual(repair.find_repairs(self.connection, prefixes, known), {})

    def test_planning_does_not_write(self):
        dirty_id, _, _ = self._seed_prefixed_products()
        prefixes, known = self._learn()
        plan = repair.plan_repairs(
            self.connection, repair.find_repairs(self.connection, prefixes, known)
        )
        self.assertEqual(len(plan), 3)
        stored = self.connection.execute(
            "SELECT composition FROM medicines WHERE id = ?", (dirty_id,)
        ).fetchone()["composition"]
        self.assertEqual(stored, "Cure Healthcare Naproxen (500mg) + Domperidone (10mg)")


if __name__ == "__main__":
    unittest.main()
