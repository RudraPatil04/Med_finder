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
        # Dataset prices are not current listings, so no cheaper/saving claim is made.
        self.assertEqual(result["comparison_status"], "catalog_price_unverified")
        self.assertFalse(result["reference_price_is_current"])
        self.assertEqual(result["same_composition_alternatives"], [])
        option = result["same_composition_options"][0]
        self.assertEqual(option["medicine_name"], "Potential")
        self.assertEqual(option["price_per_unit_value"], 4.0)
        self.assertNotIn("savings_amount", option)
        self.assertNotIn("savings_percent", option)
        self.assertEqual([item["medicine_name"] for item in result["unverified_matches"]], ["Potential"])
        self.assertIn("No product-level equivalence evidence found", result["unverified_matches"][0]["verification_gaps"])
        self.assertNotIn("savings_amount", result["unverified_matches"][0])

    def test_cheaper_claim_requires_current_listing_prices_on_both_sides(self):
        now = datetime.now(timezone.utc).isoformat()
        connection = sqlite3.connect(self.db_path)
        try:
            ids = []
            for name, price, checked in [
                ("Target", 50, now),
                ("Current cheaper", 40, now),
                ("Stale cheaper", 30, None),
            ]:
                key = make_product_key(name, name, "500mg", "Capsule", "Amoxicillin (500mg)", "IR", "10 capsules")
                medicine_id = connection.execute("""INSERT INTO medicines(
                    medicine_name, brand_name, strength, dosage_form, composition, price,
                    release_type, pack_size, composition_complete, price_source,
                    price_checked_at, product_key
                ) VALUES (?, ?, '500mg', 'Capsule', 'Amoxicillin (500mg)', ?, 'IR',
                    '10 capsules', 1, 'fixture', ?, ?)""",
                    (name, name, price, checked, key)).lastrowid
                ids.append(medicine_id)
                connection.execute("""INSERT INTO medicine_ingredients(
                    medicine_id, ingredient_name, ingredient_name_norm, ingredient_strength,
                    ingredient_strength_norm
                ) VALUES (?, 'Amoxicillin', 'amoxicillin', '500mg', '500mg')""", (medicine_id,))
            connection.commit()
        finally:
            connection.close()

        result = get_substitutes(ids[0])
        self.assertTrue(result["reference_price_is_current"])
        self.assertEqual(result["comparison_status"], "cheaper_same_composition")
        cheaper = result["same_composition_alternatives"]
        self.assertEqual([item["medicine_name"] for item in cheaper], ["Current cheaper"])
        self.assertEqual(cheaper[0]["savings_amount"], 1.0)
        self.assertEqual(cheaper[0]["savings_basis"], "per unit")
        self.assertTrue(cheaper[0]["savings_reliable"])
        # The stale-priced product is still listed, but without a saving claim.
        self.assertEqual([item["medicine_name"] for item in result["same_composition_options"]], ["Stale cheaper"])

    def _add_reference_price(self, composition_key, observed_at, price=10.0, price_per_unit=1.0, unit="capsule"):
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute("""CREATE TABLE IF NOT EXISTS reference_prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                source_edition TEXT,
                source_url TEXT,
                drug_code TEXT,
                generic_name TEXT NOT NULL,
                composition TEXT,
                composition_key TEXT NOT NULL,
                pack_text TEXT,
                pack_quantity INTEGER,
                pack_unit TEXT,
                price_kind TEXT,
                price_value REAL,
                price_per_unit REAL,
                currency TEXT,
                release_type TEXT,
                observed_at TEXT,
                imported_at TEXT
            )""")
            connection.execute("""INSERT INTO reference_prices(
                source, source_edition, generic_name, composition, composition_key, pack_text,
                pack_quantity, pack_unit, price_kind, price_value, price_per_unit, currency, observed_at
            ) VALUES ('Jan Aushadhi (PMBJP)', '2025', 'Amoxicillin Capsules IP 500mg', 'Amoxicillin (500mg)',
                ?, '10''s', 10, ?, 'reference_generic', ?, ?, 'INR', ?)""",
                (composition_key, unit, price, price_per_unit, observed_at))
            connection.commit()
        finally:
            connection.close()

    def _add_amoxicillin_target(self, price=50, checked=None):
        key = make_product_key("Target", "Target", "500mg", "Capsule", "Amoxicillin (500mg)", "IR", "10 capsules")
        connection = sqlite3.connect(self.db_path)
        try:
            medicine_id = connection.execute("""INSERT INTO medicines(
                medicine_name, brand_name, strength, dosage_form, composition, price,
                release_type, pack_size, composition_complete, price_source,
                price_checked_at, product_key
            ) VALUES ('Target', 'Target', '500mg', 'Capsule', 'Amoxicillin (500mg)', ?, 'IR',
                '10 capsules', 1, ?, ?, ?)""",
                (price, "fixture" if checked else None, checked, key)).lastrowid
            connection.execute("""INSERT INTO medicine_ingredients(
                medicine_id, ingredient_name, ingredient_name_norm, ingredient_strength,
                ingredient_strength_norm
            ) VALUES (?, 'Amoxicillin', 'amoxicillin', '500mg', '500mg')""", (medicine_id,))
            connection.commit()
        finally:
            connection.close()
        return medicine_id

    def test_no_reference_options_without_the_reference_price_table(self):
        result = get_substitutes(self._add_amoxicillin_target())
        self.assertEqual(result["reference_price_options"], [])
        self.assertEqual(result["reference_price_option_count"], 0)

    def test_reference_price_joins_on_form_and_composition_without_a_saving_claim(self):
        self._add_reference_price("capsule|amoxicillin:500mg", "2025-04-01", price=10.0, price_per_unit=1.0)
        # A different dosage form must not be joined to a capsule product.
        self._add_reference_price("tablet|amoxicillin:500mg", "2025-04-01", price=12.0, price_per_unit=1.2, unit="tablet")
        medicine_id = self._add_amoxicillin_target(price=50, checked=datetime.now(timezone.utc).isoformat())

        result = get_substitutes(medicine_id)
        self.assertEqual(result["reference_price_option_count"], 1)
        option = result["reference_price_options"][0]
        self.assertEqual(option["product_name"], "Amoxicillin Capsules IP 500mg")
        self.assertEqual(option["pack"], "10's")
        self.assertEqual(option["price"], 10.0)
        self.assertEqual(option["price_per_unit"], 1.0)
        self.assertEqual(option["unit"], "capsule")
        self.assertEqual(option["reference_price_per_unit"], 5.0)
        self.assertEqual(option["difference_per_unit"], 4.0)
        self.assertEqual(option["observed_at"], "2025-04-01")
        # The edition is over a year old, so no saving is claimed.
        self.assertFalse(option["price_is_current"])
        self.assertNotIn("savings_percent", option)
        self.assertNotIn("savings_amount", option)

    def test_reference_price_saving_requires_a_current_listing_and_a_recent_edition(self):
        self._add_reference_price(
            "capsule|amoxicillin:500mg",
            datetime.now(timezone.utc).isoformat(),
            price=10.0, price_per_unit=1.0,
        )
        now = datetime.now(timezone.utc).isoformat()

        current = get_substitutes(self._add_amoxicillin_target(price=50, checked=now))
        option = current["reference_price_options"][0]
        self.assertTrue(option["price_is_current"])
        self.assertEqual(option["savings_amount"], 4.0)
        self.assertEqual(option["savings_percent"], 80.0)
        self.assertEqual(option["savings_basis"], "per unit")

        undated = get_substitutes(self._add_amoxicillin_target(price=50, checked=None))
        self.assertNotIn("savings_percent", undated["reference_price_options"][0])

    def _add_medicine(self, name, composition, price=50, form="Capsule", release=None,
                      checked=None, pack="10 capsules", strength="500mg"):
        key = make_product_key(name, name, strength, form, composition, release, pack)
        connection = sqlite3.connect(self.db_path)
        try:
            medicine_id = connection.execute("""INSERT INTO medicines(
                medicine_name, brand_name, strength, dosage_form, composition, price,
                release_type, pack_size, composition_complete, price_source,
                price_checked_at, product_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)""",
                (name, name, strength, form, composition, price, release, pack,
                 "fixture" if checked else None, checked, key)).lastrowid
            for ingredient in parse_composition(composition) or []:
                connection.execute("""INSERT INTO medicine_ingredients(
                    medicine_id, ingredient_name, ingredient_name_norm, ingredient_strength,
                    ingredient_strength_norm
                ) VALUES (?, ?, ?, ?, ?)""",
                    (medicine_id, ingredient["ingredient_name"], ingredient["ingredient_name_norm"],
                     ingredient["ingredient_strength"], ingredient["ingredient_strength_norm"]))
            connection.commit()
        finally:
            connection.close()
        return medicine_id

    def test_narrow_therapeutic_index_products_are_not_compared(self):
        now = datetime.now(timezone.utc).isoformat()
        target = self._add_medicine(
            "Warfarin 5mg Tablet", "Warfarin (500mg)", price=50, form="Tablet",
            checked=now, pack="10 tablets",
        )
        self._add_medicine(
            "Cheap Warfarin 5mg Tablet", "Warfarin (500mg)", price=10, form="Tablet",
            checked=now, pack="10 tablets",
        )

        result = get_substitutes(target)
        self.assertEqual(result["comparison_status"], "restricted_class")
        self.assertIn("narrow therapeutic index", result["restricted_class_reason"])
        self.assertEqual(result["same_composition_alternatives"], [])
        self.assertEqual(result["same_composition_options"], [])
        self.assertEqual(result["substitutes"], [])
        self.assertEqual(result["unverified_matches"], [])
        self.assertEqual(result["reference_price_options"], [])

    def test_prolonged_release_and_dsr_references_are_not_compared(self):
        for name in ("Pantoprazole Domperidone Capsule PR", "Pantoprazole Domperidone DSR Capsule"):
            target = self._add_medicine(name, "Domperidone (30mg) + Pantoprazole (40mg)", strength="30mg")
            self._add_medicine("Cheap " + name.replace(" PR", "").replace(" DSR", ""),
                               "Domperidone (30mg) + Pantoprazole (40mg)", price=10, strength="30mg")
            result = get_substitutes(target)
            self.assertEqual(result["comparison_status"], "restricted_class", name)
            self.assertIn("modified-release", result["restricted_class_reason"])

    def test_thyroxine_is_treated_as_narrow_therapeutic_index(self):
        target = self._add_medicine("Thyro 50mcg Tablet", "Thyroxine (50mcg)", form="Tablet",
                                    pack="100 tablets", strength="50mcg")
        self._add_medicine("Other Thyro 50mcg Tablet", "Thyroxine (50mcg)", price=10, form="Tablet",
                           pack="100 tablets", strength="50mcg")
        result = get_substitutes(target)
        self.assertEqual(result["comparison_status"], "restricted_class")
        self.assertEqual(result["same_composition_options"], [])

    def test_nebuliser_respules_are_not_compared(self):
        target = self._add_medicine("Budesonide 0.5mg Respules", "Budesonide (0.5mg)", form="Respules",
                                    pack="5 units", strength="0.5mg")
        result = get_substitutes(target)
        self.assertEqual(result["comparison_status"], "restricted_class")

    def test_dispersible_and_vaginal_tablets_do_not_match_plain_tablets(self):
        kwargs = {"form": "Tablet", "pack": "10 tablets", "strength": "100mg"}
        plain = self._add_medicine("Clotri 100mg Tablet", "Clotrimazole (100mg)", **kwargs)
        self._add_medicine("Clotri V 100mg Tablet VT", "Clotrimazole (100mg)", price=10, **kwargs)
        dispersible = self._add_medicine("Clotri 100mg Tablet DT", "Clotrimazole (100mg)", price=20, **kwargs)
        self._add_medicine("Other Clotri 100mg Tablet DT", "Clotrimazole (100mg)", price=15, **kwargs)
        self._add_medicine("Generic Clotri 100mg Tablet", "Clotrimazole (100mg)", price=30, **kwargs)

        plain_names = [item["medicine_name"] for item in get_substitutes(plain)["same_composition_options"]]
        self.assertEqual(plain_names, ["Generic Clotri 100mg Tablet"])
        dt_names = [item["medicine_name"] for item in get_substitutes(dispersible)["same_composition_options"]]
        self.assertEqual(dt_names, ["Other Clotri 100mg Tablet DT"])

    def test_combination_matches_when_strength_column_is_formatted_differently(self):
        kwargs = {"form": "Tablet", "pack": "10 tablets"}
        composition = "Ofloxacin (200mg) + Ornidazole (500mg)"
        target = self._add_medicine("Oflo OZ Tablet", composition, strength="200mg", **kwargs)
        self._add_medicine("Other OZ 200mg/500mg Tablet", composition, price=20, strength="200mg/500mg", **kwargs)
        # A strength column that contradicts the product's own composition is not trusted.
        self._add_medicine("Odd OZ Tablet", composition, price=15, strength="250mg", **kwargs)

        names = [item["medicine_name"] for item in get_substitutes(target)["same_composition_options"]]
        self.assertEqual(names, ["Other OZ 200mg/500mg Tablet"])

    def test_decimal_strengths_are_not_confused(self):
        kwargs = {"form": "Tablet", "pack": "10 tablets", "strength": "40mg"}
        target = self._add_medicine("Telmi H Tablet", "Hydrochlorothiazide (12.5mg) + Telmisartan (40mg)", **kwargs)
        # The stored strength key reads "125mg" for both 12.5mg and 125mg.
        self._add_medicine("Wrong Telmi H Tablet", "Hydrochlorothiazide (125mg) + Telmisartan (40mg)",
                           price=10, **kwargs)
        self._add_medicine("Right Telmi H Tablet", "Hydrochlorothiazide (12.5mg) + Telmisartan (40mg)",
                           price=20, **kwargs)

        names = [item["medicine_name"] for item in get_substitutes(target)["same_composition_options"]]
        self.assertEqual(names, ["Right Telmi H Tablet"])

    def test_injectable_reference_is_not_compared(self):
        target = self._add_medicine(
            "Amoxicillin 500mg Injection", "Amoxicillin (500mg)", form="Injection", pack="1 vial",
        )
        result = get_substitutes(target)
        self.assertEqual(result["comparison_status"], "restricted_class")
        self.assertIn("injectable", result["restricted_class_reason"])

    def test_modified_release_candidate_is_excluded_without_a_release_column(self):
        target = self._add_medicine("Plain Amoxicillin 500mg Capsule", "Amoxicillin (500mg)")
        # Name-marked modified-release rows with an empty release column must not
        # pass as plain immediate-release equivalents.
        self._add_medicine("Sustained Release Amoxicillin 500mg Capsule", "Amoxicillin (500mg)", price=10)
        self._add_medicine("Amoxicillin 500mg SR Capsule", "Amoxicillin (500mg)", price=5)
        self._add_medicine("Amoxicillin 500mg Capsule", "Amoxicillin (500mg)", price=30)

        result = get_substitutes(target)
        names = [item["medicine_name"] for item in result["same_composition_options"]]
        self.assertEqual(names, ["Amoxicillin 500mg Capsule"])
        potential = [item["medicine_name"] for item in result["unverified_matches"]]
        self.assertNotIn("Amoxicillin 500mg SR Capsule", potential)
        self.assertNotIn("Sustained Release Amoxicillin 500mg Capsule", potential)

    def test_catalog_options_exclude_products_without_a_pack_size(self):
        connection = sqlite3.connect(self.db_path)
        try:
            ids = []
            for name, price, pack in [
                ("Target", 50, None),
                ("Known pack", 30, "10 capsules"),
                ("Unknown pack", 5, None),
            ]:
                key = make_product_key(name, name, "500mg", "Capsule", "Amoxicillin (500mg)", "IR", pack)
                medicine_id = connection.execute("""INSERT INTO medicines(
                    medicine_name, brand_name, strength, dosage_form, composition, price,
                    release_type, pack_size, composition_complete, product_key
                ) VALUES (?, ?, '500mg', 'Capsule', 'Amoxicillin (500mg)', ?, 'IR', ?, 1, ?)""",
                    (name, name, price, pack, key)).lastrowid
                ids.append(medicine_id)
                connection.execute("""INSERT INTO medicine_ingredients(
                    medicine_id, ingredient_name, ingredient_name_norm, ingredient_strength,
                    ingredient_strength_norm
                ) VALUES (?, 'Amoxicillin', 'amoxicillin', '500mg', '500mg')""", (medicine_id,))
            connection.commit()
        finally:
            connection.close()

        result = get_substitutes(ids[0])
        self.assertEqual(
            [item["medicine_name"] for item in result["same_composition_options"]],
            ["Known pack"],
        )
        self.assertIn("Unknown pack", [item["medicine_name"] for item in result["unverified_matches"]])

    def test_generic_ingredient_search_finds_brand_named_products(self):
        records = [
            ("Voveran 50mg Tablet", "Voveran", "50mg", "Tablet", "Diclofenac Sodium (50mg)"),
            ("Voveran Gel", "Voveran", "1%", "Gel", "Diclofenac Diethylamine (1%)"),
            ("Diclofenac Plus Tablet", "Diclofenac Plus", "50mg", "Tablet",
             "Diclofenac Sodium (50mg) + Paracetamol (325mg)"),
            ("Crocin 500mg Tablet", "Crocin", "500mg", "Tablet", "Paracetamol (500mg)"),
        ]
        connection = sqlite3.connect(self.db_path)
        try:
            for name, brand, strength, form, composition in records:
                medicine_id = connection.execute(
                    """INSERT INTO medicines(
                        medicine_name, brand_name, strength, dosage_form, composition,
                        price, pack_size, composition_complete
                    ) VALUES (?, ?, ?, ?, ?, 20, '10 tablets', 1)""",
                    (name, brand, strength, form, composition),
                ).lastrowid
                for ingredient in parse_composition(composition):
                    connection.execute(
                        """INSERT INTO medicine_ingredients(
                            medicine_id, ingredient_name, ingredient_name_norm,
                            ingredient_strength, ingredient_strength_norm
                        ) VALUES (?, ?, ?, ?, ?)""",
                        (medicine_id, ingredient["ingredient_name"], ingredient["ingredient_name_norm"],
                         ingredient["ingredient_strength"], ingredient["ingredient_strength_norm"]),
                    )
            connection.commit()
        finally:
            connection.close()

        results = medicine_service.search_medicines("Diclofenac", limit=20, enable_api_fallback=False)
        names = [item["medicine_name"] for item in results]
        # Brand names that never mention the ingredient are found through the
        # structured ingredient table, tablets are ranked before gels.
        self.assertEqual(names[0], "Voveran 50mg Tablet")
        self.assertIn("Voveran Gel", names)
        # Combination products and unrelated ingredients stay out.
        self.assertNotIn("Diclofenac Plus Tablet", names)
        self.assertNotIn("Crocin 500mg Tablet", names)

    def test_generic_search_with_strength_excludes_other_strengths(self):
        records = [
            ("Glucomet 500mg Tablet", "Glucomet", "500mg", "Tablet", "Metformin (500mg)"),
            ("Glucomet 1000mg Tablet", "Glucomet", "1000mg", "Tablet", "Metformin (1000mg)"),
            ("Metformin 500mg Tablet", "Metformin", "500mg", "Tablet", "Metformin (500mg)"),
        ]
        connection = sqlite3.connect(self.db_path)
        try:
            for name, brand, strength, form, composition in records:
                medicine_id = connection.execute(
                    """INSERT INTO medicines(
                        medicine_name, brand_name, strength, dosage_form, composition,
                        price, pack_size, composition_complete
                    ) VALUES (?, ?, ?, ?, ?, 20, '10 tablets', 1)""",
                    (name, brand, strength, form, composition),
                ).lastrowid
                for ingredient in parse_composition(composition):
                    connection.execute(
                        """INSERT INTO medicine_ingredients(
                            medicine_id, ingredient_name, ingredient_name_norm,
                            ingredient_strength, ingredient_strength_norm
                        ) VALUES (?, ?, ?, ?, ?)""",
                        (medicine_id, ingredient["ingredient_name"], ingredient["ingredient_name_norm"],
                         ingredient["ingredient_strength"], ingredient["ingredient_strength_norm"]),
                    )
            connection.commit()
        finally:
            connection.close()

        results = medicine_service.search_medicines("Metformin 500", limit=20, enable_api_fallback=False)
        names = [item["medicine_name"] for item in results]
        self.assertIn("Metformin 500mg Tablet", names)
        self.assertIn("Glucomet 500mg Tablet", names)
        self.assertNotIn("Glucomet 1000mg Tablet", names)

    def test_duplicate_search_rows_are_collapsed_to_the_cheapest_copy(self):
        connection = sqlite3.connect(self.db_path)
        try:
            for price, pack in ((25, "10 tablets"), (20, "15 tablets")):
                connection.execute(
                    """INSERT INTO medicines(
                        medicine_name, brand_name, strength, dosage_form, composition, price,
                        pack_size, composition_complete
                    ) VALUES ('Duplicet 500mg Tablet', 'Duplicet', '500mg', 'Tablet',
                        'Paracetamol (500mg)', ?, ?, 1)""",
                    (price, pack),
                )
            connection.commit()
        finally:
            connection.close()

        results = medicine_service.search_medicines("Duplicet", limit=20, enable_api_fallback=False)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["price"], 20)

    def test_missing_pack_sizes_are_explained_instead_of_implying_a_comparison(self):
        connection = sqlite3.connect(self.db_path)
        try:
            ids = []
            for name, price in [("Reference Patch", 180), ("Patch A", 191), ("Patch B", 98)]:
                key = make_product_key(
                    name, name, "100mg", "Patch", "Diclofenac Diethylamine (100mg)", None, None
                )
                medicine_id = connection.execute(
                    """INSERT INTO medicines(
                        medicine_name, brand_name, strength, dosage_form, composition, price,
                        release_type, pack_size, composition_complete, product_key
                    ) VALUES (?, ?, '100mg', 'Patch', 'Diclofenac Diethylamine (100mg)', ?,
                        NULL, NULL, 1, ?)""",
                    (name, name, price, key),
                ).lastrowid
                ids.append(medicine_id)
                connection.execute(
                    """INSERT INTO medicine_ingredients(
                        medicine_id, ingredient_name, ingredient_name_norm,
                        ingredient_strength, ingredient_strength_norm
                    ) VALUES (?, 'Diclofenac Diethylamine', 'diclofenacdiethylamine', '100mg', '100mg')""",
                    (medicine_id,),
                )
            connection.commit()
        finally:
            connection.close()

        result = get_substitutes(ids[0])
        self.assertEqual(result["comparison_status"], "no_verified_alternatives")
        self.assertEqual(result["same_composition_options"], [])
        self.assertIn("none records a pack size", result["message"])

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
        def record(pid, name, composition, form="Tablet"):
            return {"product_id": pid, "name": name, "composition": composition, "dosage_form": form,
                    "manufacturer": "Example Pharma", "packaging": "strip of 10 tablets", "mrp": 50.0}
        payload = {"products": 5, "results": [
            record("p20", "Example 20 Tablet", "Atorvastatin (20mg)"),
            record("p40", "Example 40 Tablet", "Atorvastatin (40mg)"),
            record("combo", "Combo Tablet", "Atorvastatin (20mg) + Amlodipine (5mg)"),
            record("cap", "Example 20 Capsule", "Atorvastatin (20mg)", form="Capsule"),
            record("sr", "Example 20 SR Tablet", "Atorvastatin (20mg)"),
            record("self", "Reference 20 Tablet", "Atorvastatin (20mg)"),
        ]}
        response = Mock()
        response.json.return_value = payload
        with patch.object(india_medicine_api.requests, "get", return_value=response) as api_call:
            result = india_medicine_api.search_live_alternatives({
                "medicine_name": "Reference 20 Tablet",
                "composition": "Atorvastatin (20mg)", "dosage_form": "Tablet", "release_type": None,
            }, limit=5)

        api_call.assert_called_once()
        self.assertTrue(api_call.call_args.args[0].endswith("/v1/search"))
        self.assertEqual(api_call.call_args.kwargs["params"]["q"], "Atorvastatin 20mg")
        self.assertEqual([m["medicine_name"] for m in result["matches"]], ["Example 20 Tablet"])
        self.assertEqual(result["matches"][0]["pack_size"], "10 tablets")
        self.assertIsNone(result["matches"][0]["price"])
        self.assertIn("equivalence not confirmed", result["matches"][0]["classification"])

    def test_live_india_api_failure_message_hides_exception_names(self):
        response = Mock()
        response.json.side_effect = ValueError("Expecting value: line 1 column 1 (char 0)")
        with patch.object(india_medicine_api.requests, "get", return_value=response):
            result = india_medicine_api.search_live_alternatives({
                "composition": "Atorvastatin (20mg)", "dosage_form": "Tablet",
            })
        self.assertFalse(result["success"])
        self.assertNotIn("Error", result["message"])
        self.assertNotIn("(", result["message"])

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
