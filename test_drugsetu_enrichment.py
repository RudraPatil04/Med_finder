"""Offline tests for strict DrugSetu matching and field enrichment."""

import os
import sqlite3
import sys
import tempfile
import types
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

if importlib.util.find_spec("requests") is None:
    requests_stub = types.ModuleType("requests")
    requests_stub.Timeout = TimeoutError
    requests_stub.get = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("network disabled in test"))
    sys.modules["requests"] = requests_stub
if importlib.util.find_spec("bs4") is None:
    bs4_stub = types.ModuleType("bs4")
    bs4_stub.BeautifulSoup = object
    sys.modules["bs4"] = bs4_stub

from backend import database
from backend.services import drug_catalog_service, drugsetu_service


class DrugSetuEnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_medfinder.db"
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """CREATE TABLE medicines (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    medicine_name TEXT NOT NULL,
                    brand_name TEXT,
                    strength TEXT,
                    dosage_form TEXT,
                    medicine_category TEXT,
                    composition TEXT,
                    price REAL,
                    prescription_required TEXT,
                    image_url TEXT,
                    release_type TEXT,
                    pack_size TEXT,
                    composition_complete INTEGER NOT NULL DEFAULT 0,
                    price_source TEXT,
                    price_checked_at TEXT,
                    rxnorm_rxcui TEXT,
                    product_key TEXT,
                    source TEXT DEFAULT 'local'
                )"""
            )
            connection.execute(
                """CREATE TABLE medicine_ingredients (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    medicine_id INTEGER NOT NULL,
                    ingredient_name TEXT NOT NULL,
                    ingredient_name_norm TEXT NOT NULL,
                    ingredient_strength TEXT NOT NULL,
                    ingredient_strength_norm TEXT NOT NULL
                )"""
            )
            connection.commit()
        finally:
            connection.close()
        self.db_patch = patch.object(database, "MEDICINE_DATABASE", self.db_path)
        self.db_patch.start()
        drug_catalog_service._SCHEMA_READY_FOR = None
        drugsetu_service.ensure_catalog_schema()
        self.env_patch = patch.dict(os.environ, {"DRUGSETU_API_KEY": "test-key"})
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _insert(self, **values):
        columns = {
            "medicine_name", "brand_name", "strength", "dosage_form", "composition",
            "price", "pack_size", "composition_complete", "release_type",
        }
        values = {key: value for key, value in values.items() if key in columns}
        sql = "INSERT INTO medicines({}) VALUES ({})".format(
            ", ".join(values), ", ".join("?" * len(values))
        )
        connection = sqlite3.connect(self.db_path)
        try:
            cursor = connection.execute(sql, tuple(values.values()))
            connection.commit()
            return cursor.lastrowid
        finally:
            connection.close()

    def _row(self, medicine_id):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            return dict(connection.execute(
                "SELECT * FROM medicines WHERE id = ?", (medicine_id,)
            ).fetchone())
        finally:
            connection.close()

    # ---- matching ---------------------------------------------------

    def test_exact_product_matches(self):
        medicine = {"brand_name": "Dolo", "medicine_name": "Dolo 650 Tablet",
                    "strength": "650mg", "dosage_form": "Tablet"}
        candidates = [{
            "product_id": "prod_1", "product_name": "Dolo 650 Tablet",
            "composition": "Paracetamol (650mg)",
        }]
        self.assertEqual(drugsetu_service.match_product(medicine, candidates)["product_id"], "prod_1")

    def test_wrong_dosage_form_is_rejected(self):
        medicine = {"brand_name": "Rtsun", "medicine_name": "Rtsun 60 Tablet",
                    "strength": "60mg", "dosage_form": "Tablet"}
        candidates = [{
            "product_id": "prod_2", "product_name": "Rtsun 60mg injection",
            "composition": "Artesunate (60mg)",
        }]
        self.assertIsNone(drugsetu_service.match_product(medicine, candidates))

    def test_wrong_strength_is_rejected(self):
        medicine = {"brand_name": "Genothral", "medicine_name": "Genothral 250",
                    "strength": "250mg", "dosage_form": "Tablet"}
        candidates = [{
            "product_id": "prod_3", "product_name": "Genothral 500mg Tablet",
            "composition": "Azithromycin (500mg)",
        }]
        self.assertIsNone(drugsetu_service.match_product(medicine, candidates))

    def test_ambiguous_same_brand_strength_is_rejected(self):
        medicine = {"brand_name": "Dolo", "medicine_name": "Dolo 650 Tablet",
                    "strength": "650mg", "dosage_form": "Tablet"}
        candidates = [
            {"product_id": "a", "product_name": "Dolo 650 Tablet", "composition": "Paracetamol (650mg)"},
            {"product_id": "b", "product_name": "Dolo 650 Tablet", "composition": "Acetaminophen (650mg)"},
        ]
        self.assertIsNone(drugsetu_service.match_product(medicine, candidates))

    # ---- pack parsing -----------------------------------------------

    def test_pack_sizes_are_normalized(self):
        cases = {
            "strip of 10 tablets": "10 tablets",
            "vial of 1 Injection": "1 vial",
            "tube of 10 gm Cream": "10 g",
            "bottle of 30 ml Syrup": "30 ml",
            "strip of 1 Kit": "1 kit",
            "bottle of 100 ml Lotion": "100 ml",
        }
        for text, expected in cases.items():
            self.assertEqual(drugsetu_service._pack_from_text(text), expected, text)

    # ---- writes -----------------------------------------------------

    def test_enrichment_fills_missing_fields_and_keeps_price_null(self):
        medicine_id = self._insert(
            medicine_name="Zuglimin 500mg Tablet", brand_name="Zuglimin",
            strength="500mg", dosage_form="Tablet", composition=None,
            composition_complete=0,
        )
        with patch.object(drugsetu_service, "search_products", return_value=[{
            "product_id": "prod_9", "product_name": "Zuglimin 500mg Tablet",
            "composition": "Imeglimin (500mg)",
        }]), patch.object(drugsetu_service, "fetch_product", return_value={
            "product_id": "prod_9", "product_name": "Zuglimin 500mg Tablet",
            "composition": "Imeglimin (500mg)", "product_form": "Tablet",
            "packaging": "strip of 10 tablets", "mrp": 75,
        }):
            result = drugsetu_service.enrich_medicine(medicine_id)

        self.assertEqual(result["status"], "enriched")
        row = self._row(medicine_id)
        self.assertEqual(row["composition"], "Imeglimin (500mg)")
        self.assertEqual(row["composition_complete"], 1)
        self.assertEqual(row["composition_source"], drugsetu_service.DRUGSETU_SOURCE)
        self.assertEqual(row["pack_size"], "10 tablets")
        self.assertEqual(row["mrp"], 75.0)
        self.assertEqual(row["price_type"], "mrp")
        self.assertIsNone(row["price"])
        self.assertTrue(row["product_key"])
        ingredients = self._ingredients(medicine_id)
        self.assertEqual(ingredients, [("imeglimin", "500mg")])

    def test_complete_composition_is_not_overwritten(self):
        medicine_id = self._insert(
            medicine_name="Dolo 650 Tablet", brand_name="Dolo", strength="650mg",
            dosage_form="Tablet", composition="Paracetamol (650mg)",
            composition_complete=1, price=32.0,
        )
        with patch.object(drugsetu_service, "search_products", return_value=[{
            "product_id": "prod_1", "product_name": "Dolo 650 Tablet",
            "composition": "Acetaminophen (650mg)",
        }]), patch.object(drugsetu_service, "fetch_product", return_value={
            "product_id": "prod_1", "product_name": "Dolo 650 Tablet",
            "composition": "Acetaminophen (650mg)", "packaging": "strip of 10 tablets", "mrp": 22.84,
        }):
            result = drugsetu_service.enrich_medicine(medicine_id)

        self.assertEqual(result["status"], "enriched")
        row = self._row(medicine_id)
        self.assertEqual(row["composition"], "Paracetamol (650mg)")
        self.assertEqual(row["price"], 32.0)
        self.assertEqual(row["mrp"], 22.84)
        self.assertEqual(row["pack_size"], "10 tablets")

    def test_existing_price_and_mrp_are_untouched(self):
        medicine_id = self._insert(
            medicine_name="Dolo 650 Tablet", brand_name="Dolo", strength="650mg",
            dosage_form="Tablet", composition="Paracetamol (650mg)",
            composition_complete=1, price=99.0, pack_size="15 tablets",
        )
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute("UPDATE medicines SET mrp = 50.0 WHERE id = ?", (medicine_id,))
            connection.commit()
        finally:
            connection.close()

        with patch.object(drugsetu_service, "search_products") as search:
            result = drugsetu_service.enrich_medicine(medicine_id)

        self.assertEqual(result["status"], "complete")
        search.assert_not_called()
        row = self._row(medicine_id)
        self.assertEqual(row["price"], 99.0)
        self.assertEqual(row["mrp"], 50.0)

    # ---- caching ----------------------------------------------------

    def test_no_match_is_cached(self):
        medicine_id = self._insert(
            medicine_name="Crocin 500mg Tablet", brand_name="Crocin",
            strength="500mg", dosage_form="Tablet", composition_complete=0,
        )
        with patch.object(drugsetu_service, "search_products", return_value=[]) as search:
            first = drugsetu_service.enrich_medicine(medicine_id)
            second = drugsetu_service.enrich_medicine(medicine_id)

        self.assertEqual(first["status"], "no_match")
        self.assertEqual(second["status"], "cached")
        self.assertEqual(search.call_count, 1)

    def test_quota_exhaustion_is_not_cached(self):
        medicine_id = self._insert(
            medicine_name="Zuglimin 500mg Tablet", brand_name="Zuglimin",
            strength="500mg", dosage_form="Tablet", composition_complete=0,
        )
        failure = drugsetu_service.DrugSetuQuotaExceeded("Trial Limit Reached")
        with patch.object(drugsetu_service, "search_products", side_effect=failure) as search:
            first = drugsetu_service.enrich_medicine(medicine_id)
            second = drugsetu_service.enrich_medicine(medicine_id)

        self.assertEqual(first["status"], "quota_exceeded")
        self.assertEqual(second["status"], "quota_exceeded")
        self.assertEqual(search.call_count, 2)

    def test_missing_key_is_reported_without_calling_network(self):
        medicine_id = self._insert(
            medicine_name="Zuglimin 500mg Tablet", brand_name="Zuglimin",
            strength="500mg", dosage_form="Tablet", composition_complete=0,
        )
        with patch.dict(os.environ, {"DRUGSETU_API_KEY": ""}):
            result = drugsetu_service.enrich_medicine(medicine_id)
        self.assertEqual(result["status"], "not_configured")

    def _ingredients(self, medicine_id):
        connection = sqlite3.connect(self.db_path)
        try:
            return list(connection.execute(
                "SELECT ingredient_name_norm, ingredient_strength_norm "
                "FROM medicine_ingredients WHERE medicine_id = ?", (medicine_id,)
            ))
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
