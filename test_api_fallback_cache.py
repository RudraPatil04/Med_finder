"""Focused offline tests for RxNorm fallback persistence and caching."""

import sqlite3
import importlib.util
from datetime import datetime, timezone
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

# These tests replace RxNorm itself, so optional network/HTML packages are not
# exercised. Keep the focused persistence tests runnable in minimal environments.
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
from backend.medicine_data import canonical_composition, extract_pack_size, extract_release_type, make_product_key, parse_composition
from backend.services import medicine_service
from backend.recommendation_service import get_substitutes
from backend.services import india_medicine_api, price_comparison_service


class RxNormFallbackCacheTests(unittest.TestCase):
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
                )""")
            connection.execute("""CREATE TABLE medicine_ingredients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                medicine_id INTEGER NOT NULL,
                ingredient_name TEXT NOT NULL,
                ingredient_name_norm TEXT NOT NULL,
                ingredient_strength TEXT NOT NULL,
                ingredient_strength_norm TEXT NOT NULL
            )""")
            connection.execute("""CREATE TABLE medicine_equivalence_evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reference_product_key TEXT NOT NULL,
                candidate_product_key TEXT NOT NULL,
                jurisdiction TEXT NOT NULL,
                status TEXT NOT NULL,
                evidence_type TEXT,
                reference_product TEXT,
                source_url TEXT NOT NULL,
                verified_at TEXT,
                notes TEXT
            )""")
            connection.commit()
        finally:
            connection.close()
        self.db_patch = patch.object(database, "MEDICINE_DATABASE", self.db_path)
        self.db_patch.start()
        # Keep the suite offline: catalog fallbacks are network providers.
        self.catalog_patches = [
            patch.object(medicine_service, "search_catalog", return_value=[]),
            patch.object(medicine_service, "search_pharmeasy_catalog", return_value=[]),
        ]
        for catalog_patch in self.catalog_patches:
            catalog_patch.start()

    def tearDown(self):
        for catalog_patch in self.catalog_patches:
            catalog_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_composition_and_formulation_fields_are_parsed_conservatively(self):
        parsed = parse_composition("Sitagliptin (50mg) + Metformin (500mg)ADD")
        self.assertEqual(
            [(row["ingredient_name_norm"], row["ingredient_strength_norm"]) for row in parsed],
            [("metformin", "500mg"), ("sitagliptin", "50mg")],
        )
        self.assertEqual(parse_composition("(100mg) + Metformin (1000mg)"), [])
        self.assertEqual(parse_composition(". LtdAtorvastatin (20mg)")[0]["ingredient_name_norm"], "atorvastatin")
        self.assertEqual(canonical_composition(". LtdAtorvastatin (20mg)"), "Atorvastatin (20mg)")
        self.assertEqual(extract_release_type("Janumet XR CP Tablet"), "XR")
        self.assertEqual(extract_pack_size("strip of 10 tabletsAlpic Biotech"), "10 tablets")

    def test_single_ingredient_search_excludes_multi_ingredient_products(self):
        records = [
            {"medicine_name": "atorvastatin 10 MG Oral Tablet", "composition": None},
            {"medicine_name": "amlodipine 10 MG / atorvastatin 10 MG Oral Tablet", "composition": None},
            {"medicine_name": "amlodipine 2.5 MG / atorvastatin 10 MG Oral Tablet", "composition": None},
        ]
        filtered = medicine_service._filter_single_ingredient_search("Atorvastatin 10 mg", records)
        self.assertEqual([item["medicine_name"] for item in filtered], ["atorvastatin 10 MG Oral Tablet"])

    def test_explicit_combination_search_keeps_combination_results(self):
        records = [
            {"medicine_name": "atorvastatin 10 MG Oral Tablet", "composition": None},
            {"medicine_name": "amlodipine 5 MG / atorvastatin 10 MG Oral Tablet", "composition": None},
        ]
        filtered = medicine_service._filter_single_ingredient_search("Amlodipine 5 mg / Atorvastatin 10 mg", records)
        self.assertEqual(len(filtered), 2)

    def test_api_result_is_persisted_and_reused_without_second_api_call(self):
        api_result = {
            "medicine_name": "ExampleDrug 10mg Tablet",
            "brand_name": "ExampleDrug",
            "strength": "10mg",
            "dosage_form": "Tablet",
            "medicine_category": "External",
            "composition": "ExampleDrug 10mg Tablet",
            "price": None,
            "price_source": None,
            "price_checked_at": None,
            "release_type": None,
            "pack_size": None,
            "composition_complete": 0,
            "rxnorm_rxcui": "123456",
            "prescription_required": "Unknown",
            "image_url": None,
            "source": "api",
        }
        with patch.object(medicine_service, "search_rxnorm", return_value=[api_result]) as rx_search:
            first = medicine_service.search_medicines("ExampleDrug")
            second = medicine_service.search_medicines("ExampleDrug")

        self.assertEqual(rx_search.call_count, 1)
        self.assertTrue(first)
        self.assertTrue(second)
        self.assertEqual(first[0]["source"], "api")
        self.assertEqual(second[0]["medicine_name"], api_result["medicine_name"])
        connection = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM medicines").fetchone()[0], 1)
        finally:
            connection.close()

    def test_substitutes_require_product_evidence_and_fresh_comparable_prices(self):
        now = datetime.now(timezone.utc).isoformat()
        records = [
            ("Target 500mg Capsule", "Amoxicillin (500mg)", 50, "10 capsules", "IR", "fixture", now),
            ("Equivalent 500mg Capsule", "Amoxicillin (500mg)", 40, "10 capsules", "IR", "fixture", now),
            ("Wrong ingredient", "Azithromycin (500mg)", 20, "10 capsules", "IR", "fixture", now),
            ("Wrong pack", "Amoxicillin (500mg)", 10, "20 capsules", "IR", "fixture", now),
            ("Wrong release", "Amoxicillin (500mg)", 15, "10 capsules", "XR", "fixture", now),
        ]
        connection = sqlite3.connect(self.db_path)
        try:
            ids = {}
            for name, composition, price, pack, release, source, checked in records:
                key = make_product_key(name, name, "500mg", "Capsule", composition, release, pack)
                cursor = connection.execute(
                    """INSERT INTO medicines(
                        medicine_name, brand_name, strength, dosage_form, composition,
                        price, release_type, pack_size, composition_complete, price_source,
                        price_checked_at, product_key, source
                    ) VALUES (?,?,?,?,?,?,?,?,1,?,?,?,'local')""",
                    (name, name, "500mg", "Capsule", composition, price, release, pack,
                     source, checked, key),
                )
                medicine_id = cursor.lastrowid
                ids[name] = (medicine_id, key)
                ingredient = "amoxicillin" if "Amoxicillin" in composition else "azithromycin"
                connection.execute(
                    """INSERT INTO medicine_ingredients(
                        medicine_id, ingredient_name, ingredient_name_norm,
                        ingredient_strength, ingredient_strength_norm
                    ) VALUES (?,?,?,?,?)""",
                    (medicine_id, ingredient.title(), ingredient, "500mg", "500mg"),
                )
            connection.execute("""INSERT INTO medicine_equivalence_evidence(
                reference_product_key, candidate_product_key, jurisdiction, status, source_url
            ) VALUES (?, ?, 'IN', 'confirmed', 'https://regulator.example/evidence')""",
                (ids["Target 500mg Capsule"][1], ids["Equivalent 500mg Capsule"][1]))
            connection.commit()
            target_id = ids["Target 500mg Capsule"][0]
        finally:
            connection.close()

        result = get_substitutes(target_id)
        self.assertEqual([item["medicine_name"] for item in result["substitutes"]], ["Equivalent 500mg Capsule"])
        self.assertEqual(result["substitutes"][0]["savings_amount"], 10)

    def test_same_ingredient_matches_stay_out_of_verified_substitutes(self):
        connection = sqlite3.connect(self.db_path)
        try:
            ids = []
            for name, price in [("Target", 50), ("Potential", 40)]:
                key = make_product_key(name, name, "500mg", "Capsule", "Amoxicillin (500mg)", "IR", "10 capsules")
                medicine_id = connection.execute("""INSERT INTO medicines(
                    medicine_name, brand_name, strength, dosage_form, composition, price,
                    release_type, pack_size, composition_complete, product_key
                ) VALUES (?, ?, '500mg', 'Capsule', 'Amoxicillin (500mg)', ?, 'IR', '10 capsules', 1, ?)""",
                    (name, name, price, key)).lastrowid
                ids.append(medicine_id)
                connection.execute("""INSERT INTO medicine_ingredients(
                    medicine_id, ingredient_name, ingredient_name_norm, ingredient_strength,
                    ingredient_strength_norm
                ) VALUES (?, 'Amoxicillin', 'amoxicillin', '500mg', '500mg')""", (medicine_id,))
            connection.commit()
        finally:
            connection.close()

        result = get_substitutes(ids[0])
        self.assertEqual(result["substitutes"], [])
        self.assertEqual(result["potential_match_count"], 1)
        self.assertEqual(result["comparison_status"], "cheaper_same_composition")
        cheaper = result["same_composition_alternatives"][0]
        self.assertEqual(cheaper["medicine_name"], "Potential")
        self.assertEqual(cheaper["savings_amount"], 1.0)
        self.assertEqual(cheaper["savings_basis"], "per unit")
        self.assertFalse(cheaper["savings_reliable"])
        self.assertEqual([item["medicine_name"] for item in result["unverified_matches"]], ["Potential"])
        self.assertIn("No product-level equivalence evidence found", result["unverified_matches"][0]["verification_gaps"])
        self.assertNotIn("savings_amount", result["unverified_matches"][0])

    def test_incomplete_janumet_composition_never_matches_aciflam_by_dose_fragment(self):
        connection = sqlite3.connect(self.db_path)
        try:
            target_id = connection.execute(
                """INSERT INTO medicines(
                    medicine_name, strength, dosage_form, composition, price,
                    pack_size, composition_complete, source
                ) VALUES ('Janumet XR 100mg Tablet','100mg','Tablet',
                    '(100mg) + Metformin (1000mg)',290,'10 tablets',0,'local')"""
            ).lastrowid
            connection.execute(
                """INSERT INTO medicines(
                    medicine_name, strength, dosage_form, composition, price,
                    pack_size, composition_complete, source
                ) VALUES ('Aciflam Kid 100mg/125mg Tablet','100mg','Tablet',
                    '(100mg) + Paracetamol (125mg)',1,'10 tablets',0,'local')"""
            ).lastrowid
            connection.commit()
        finally:
            connection.close()

        result = get_substitutes(target_id)
        self.assertEqual(result["substitutes"], [])

    def test_live_india_api_returns_only_structured_same_strength_form_matches(self):
        payload = {"total": 2, "results": [
            {
                "medicineSctid": "prod-20", "medicineName": "Example Atorvastatin 20 mg tablet",
                "brand.brandName": "Example", "manufacturer.manufacturerName": "Example Pharma",
                "generic.sctid": "generic-20", "licenseStatus": "UNKNOWN",
                "genericDetail": {
                    "name": "Atorvastatin (as atorvastatin calcium) 20 mg oral tablet",
                    "doseForm": "Oral tablet", "routeOfAdministration": "Oral route",
                    "lastUpdatedOn": "2026-03-10", "synonyms": ["conventional release oral tablet"],
                    "composition": [{"substanceName": "Atorvastatin calcium", "strength": "20/1", "unit": "milligram/Tablet"}],
                },
            },
            {
                "medicineSctid": "prod-40", "medicineName": "Example Atorvastatin 40 mg tablet",
                "generic.sctid": "generic-40", "genericDetail": {
                    "name": "Atorvastatin 40 mg oral tablet", "doseForm": "Oral tablet",
                    "composition": [{"substanceName": "Atorvastatin", "strength": "40/1", "unit": "milligram/Tablet"}],
                },
            },
            {
                "medicineSctid": "prod-combo", "medicineName": "Combination tablet",
                "genericDetail": {
                    "name": "Atorvastatin 20 mg and Amlodipine 5 mg oral tablet",
                    "doseForm": "Oral tablet", "composition": [
                        {"substanceName": "Atorvastatin calcium", "strength": "20/1", "unit": "milligram/Tablet"},
                        {"substanceName": "Amlodipine besylate", "strength": "5/1", "unit": "milligram/Tablet"},
                    ],
                },
            },
        ]}
        response = Mock()
        response.json.return_value = payload
        with patch.object(india_medicine_api.requests, "get", return_value=response) as api_call:
            result = india_medicine_api.search_live_alternatives({
                "composition": "Atorvastatin (20mg)", "dosage_form": "Tablet", "release_type": None,
            }, limit=5)

        api_call.assert_called_once()
        self.assertEqual(api_call.call_args.kwargs["params"]["q"], "Atorvastatin 20 mg oral tablet")
        self.assertEqual([m["medicine_name"] for m in result["matches"]], ["Example Atorvastatin 20 mg tablet"])
        self.assertEqual(result["matches"][0]["release_characteristics"], "Conventional release")
        self.assertIsNone(result["matches"][0]["price"])
        self.assertIn("equivalence not confirmed", result["matches"][0]["classification"])

    def test_price_comparison_requires_exact_ingredients_form_and_pack(self):
        def product(product_id, name, strength, pack, price, brand, ingredients=None):
            return {
                "productId": product_id, "name": name, "slug": f"product-{product_id}",
                "consumerBrandName": brand, "manufacturer": "Example Pharma",
                "salePriceDecimal": str(price), "mrpDecimal": str(price + 10),
                "packQuantityValue": pack, "measurementUnit": f"{pack} Tablet(s) in Strip",
                "subtitleText": f"{pack} Tablet(s) in Strip",
                "productAvailabilityFlags": {"isAvailable": True},
                "compositions": ingredients or [{"name": f"Atorvastatin({strength} Mg)"}],
            }
        reference = product("target", "Reference 10mg Strip Of 10 Tablets", 10, 10, 100, "REFERENCE")
        cheap = product("cheap", "Lower 10mg Strip Of 10 Tablets", 10, 10, 30, "LOWER")
        wrong_strength = product("wrong-strength", "Lower 20mg Strip Of 10 Tablets", 20, 10, 20, "LOWER20")
        wrong_pack = product("wrong-pack", "Lower 10mg Strip Of 15 Tablets", 10, 15, 20, "LOWER15")
        combo = product("combo", "Combo 10mg Tablet", 10, 10, 10, "COMBO", [
            {"name": "Atorvastatin(10 Mg)+Amlodipine(5 Mg)"}
        ])
        with patch.object(price_comparison_service, "_search_payload", side_effect=[[cheap, wrong_strength, wrong_pack, combo], [reference]]):
            result = price_comparison_service.compare_current_prices({
                "medicine_name": "Reference 10mg Tablet", "brand_name": "Reference",
                "composition": "Atorvastatin (10mg)", "dosage_form": "Tablet",
                "pack_size": "10 tablets", "release_type": None,
            })

        self.assertTrue(result["comparison_available"])
        self.assertEqual(result["reference"]["price"], 100)
        self.assertEqual([item["medicine_name"] for item in result["matches"]], ["Lower 10mg Strip Of 10 Tablets"])
        self.assertEqual(result["matches"][0]["savings_amount"], 70)

    def test_price_listings_are_not_called_cheaper_when_target_price_is_missing(self):
        listing = {
            "productId": "candidate", "name": "Generic 10 mg Strip Of 10 Tablets", "slug": "generic-10",
            "consumerBrandName": "GENERIC", "salePriceDecimal": "25", "mrpDecimal": "30",
            "packQuantityValue": 10, "measurementUnit": "10 Tablet(s) in Strip",
            "productAvailabilityFlags": {"isAvailable": True},
            "compositions": [{"name": "Atorvastatin(10 Mg)"}],
        }
        with patch.object(price_comparison_service, "_search_payload", side_effect=[[listing], []]):
            result = price_comparison_service.compare_current_prices({
                "medicine_name": "Reference 10mg Tablet", "brand_name": "Reference",
                "composition": "Atorvastatin (10mg)", "dosage_form": "Tablet",
                "pack_size": "10 tablets", "release_type": None,
            })
        self.assertFalse(result["comparison_available"])
        self.assertEqual(result["matches"][0]["price"], 25)
        self.assertNotIn("savings_amount", result["matches"][0])

    def test_empty_api_result_is_cached_and_not_retried(self):
        with patch.object(medicine_service, "search_rxnorm", return_value=[]) as rx_search:
            self.assertEqual(medicine_service.search_medicines("NoSuchDrug"), [])
            self.assertEqual(medicine_service.search_medicines("NoSuchDrug"), [])

        self.assertEqual(rx_search.call_count, 1)


if __name__ == "__main__":
    unittest.main()
