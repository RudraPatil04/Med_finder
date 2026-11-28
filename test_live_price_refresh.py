"""Offline tests for fetching current prices while showing alternatives."""

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from backend import database
from backend.medicine_data import make_product_key
from backend.services import live_price_refresh


SCHEMA = [
    """CREATE TABLE medicines (
        id INTEGER PRIMARY KEY AUTOINCREMENT, medicine_name TEXT NOT NULL, brand_name TEXT,
        strength TEXT, dosage_form TEXT, medicine_category TEXT, composition TEXT, price REAL,
        prescription_required TEXT, image_url TEXT, release_type TEXT, pack_size TEXT,
        composition_complete INTEGER NOT NULL DEFAULT 0, price_source TEXT, price_checked_at TEXT,
        rxnorm_rxcui TEXT, product_key TEXT, source TEXT DEFAULT 'local')""",
    """CREATE TABLE medicine_ingredients (
        id INTEGER PRIMARY KEY AUTOINCREMENT, medicine_id INTEGER NOT NULL,
        ingredient_name TEXT NOT NULL, ingredient_name_norm TEXT NOT NULL,
        ingredient_strength TEXT NOT NULL, ingredient_strength_norm TEXT NOT NULL)""",
    """CREATE TABLE medicine_equivalence_evidence (
        id INTEGER PRIMARY KEY AUTOINCREMENT, reference_product_key TEXT NOT NULL,
        candidate_product_key TEXT NOT NULL, jurisdiction TEXT NOT NULL, status TEXT NOT NULL,
        evidence_type TEXT, reference_product TEXT, source_url TEXT NOT NULL,
        verified_at TEXT, notes TEXT)""",
]


def listing(price, pack="10 tablets"):
    return {"listing": {
        "price": price, "source": "PharmEasy listing price",
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pack_norm": pack,
    }}


class LivePriceRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "live.db"
        connection = sqlite3.connect(self.db_path)
        try:
            for statement in SCHEMA:
                connection.execute(statement)
            connection.commit()
        finally:
            connection.close()
        self.db_patch = patch.object(database, "MEDICINE_DATABASE", self.db_path)
        self.db_patch.start()
        live_price_refresh._cooldown_until = 0.0
        self.brand = self._add("Brandol", 100.0)
        self.generic = self._add("Genol", 40.0)

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _add(self, name, price, checked=None, composition="Paracetamol (650mg)"):
        key = make_product_key(name, name, "650mg", "Tablet", composition, "IR", "10 tablets")
        connection = sqlite3.connect(self.db_path)
        try:
            medicine_id = connection.execute(
                """INSERT INTO medicines(medicine_name, brand_name, strength, dosage_form, composition,
                   price, release_type, pack_size, composition_complete, price_source,
                   price_checked_at, product_key)
                   VALUES (?, ?, '650mg', 'Tablet', ?, ?, 'IR', '10 tablets', 1, 'Source dataset', ?, ?)""",
                (name + " 650 Tablet", name, composition, price, checked, key),
            ).lastrowid
            connection.execute(
                """INSERT INTO medicine_ingredients(medicine_id, ingredient_name, ingredient_name_norm,
                   ingredient_strength, ingredient_strength_norm)
                   VALUES (?, 'Paracetamol', 'paracetamol', '650mg', '650mg')""",
                (medicine_id,),
            )
            connection.commit()
        finally:
            connection.close()
        return medicine_id

    def _row(self, medicine_id):
        connection = sqlite3.connect(self.db_path)
        try:
            return connection.execute(
                "SELECT price, price_checked_at FROM medicines WHERE id = ?", (medicine_id,)
            ).fetchone()
        finally:
            connection.close()

    def test_live_prices_on_both_sides_produce_a_saving(self):
        prices = {"Brandol": listing(30.0), "Genol": listing(12.0)}
        with patch.object(live_price_refresh, "find_reference_listing",
                          side_effect=lambda m: prices.get(m["brand_name"])):
            result = live_price_refresh.substitutes_with_live_prices(self.brand)
        self.assertEqual(result["comparison_status"], "cheaper_same_composition")
        self.assertEqual(result["live_prices_checked"], 2)
        option = result["same_composition_alternatives"][0]
        self.assertEqual(option["medicine_name"], "Genol 650 Tablet")
        self.assertEqual(option["savings_percent"], 60.0)
        self.assertEqual(self._row(self.brand)[0], 30.0)

    def test_failed_lookup_keeps_old_price_and_claims_nothing(self):
        def boom(medicine):
            raise RuntimeError("network down")
        with patch.object(live_price_refresh, "find_reference_listing", side_effect=boom):
            result = live_price_refresh.substitutes_with_live_prices(self.brand)
        self.assertEqual(result["comparison_status"], "catalog_price_unverified")
        self.assertEqual(result["same_composition_alternatives"], [])
        self.assertEqual(self._row(self.brand), (100.0, None))
        # Old catalog prices give only an estimate, under keys of its own.
        option = result["same_composition_options"][0]
        self.assertEqual(option["estimated_savings_percent"], 60.0)
        self.assertNotIn("savings_percent", option)
        self.assertNotIn("savings_amount", option)
        self.assertEqual(result["max_savings_percent"], 0)
        self.assertEqual(result["max_estimated_savings_percent"], 60.0)

    def test_no_estimate_when_the_option_is_not_cheaper(self):
        pricey = self._add("Pricol", 150.0)
        with patch.dict("os.environ", {"MEDFINDER_LIVE_PRICES": "0"}):
            result = live_price_refresh.substitutes_with_live_prices(self.generic)
        by_id = {item["id"]: item for item in result["same_composition_options"]}
        self.assertNotIn("estimated_savings_percent", by_id[pricey])
        self.assertNotIn("estimated_savings_percent", by_id[self.brand])

    def test_blocked_site_starts_a_cooldown_and_is_not_cached_as_a_miss(self):
        with patch.object(live_price_refresh, "find_reference_listing",
                          side_effect=RuntimeError("405")) as lookup:
            live_price_refresh.substitutes_with_live_prices(self.brand)
            self.assertEqual(lookup.call_count, 2)
            live_price_refresh.substitutes_with_live_prices(self.brand)
            self.assertEqual(lookup.call_count, 2)
        # Once the cooldown ends the same products are tried again.
        live_price_refresh._cooldown_until = 0.0
        with patch.object(live_price_refresh, "find_reference_listing",
                          return_value=None) as lookup:
            live_price_refresh.substitutes_with_live_prices(self.brand)
        self.assertEqual(lookup.call_count, 2)

    def test_lookups_over_budget_start_a_cooldown(self):
        import time as real_time

        def slow(medicine):
            real_time.sleep(0.5)
            return None
        with patch.object(live_price_refresh, "find_reference_listing", side_effect=slow):
            live_price_refresh.refresh_prices(
                [{"id": self.brand, "composition": "x", "dosage_form": "Tablet"}],
                budget_seconds=0.05,
            )
        self.assertTrue(live_price_refresh._in_cooldown())

    def test_only_one_side_found_claims_nothing(self):
        prices = {"Brandol": listing(30.0)}
        with patch.object(live_price_refresh, "find_reference_listing",
                          side_effect=lambda m: prices.get(m["brand_name"])):
            result = live_price_refresh.substitutes_with_live_prices(self.brand)
        self.assertEqual(result["same_composition_alternatives"], [])
        self.assertEqual(result["max_savings_percent"], 0)

    def test_a_miss_is_not_retried_on_the_next_request(self):
        with patch.object(live_price_refresh, "find_reference_listing", return_value=None) as lookup:
            live_price_refresh.substitutes_with_live_prices(self.brand)
            first_calls = lookup.call_count
            live_price_refresh.substitutes_with_live_prices(self.brand)
        self.assertEqual(first_calls, 2)
        self.assertEqual(lookup.call_count, 2)

    def test_fresh_prices_are_not_looked_up_again(self):
        now = datetime.now(timezone.utc).isoformat()
        brand = self._add("Freshol", 50.0, checked=now)
        with patch.object(live_price_refresh, "find_reference_listing", return_value=None) as lookup:
            live_price_refresh.refresh_prices([{"id": brand, "composition": "x", "dosage_form": "Tablet",
                                                "price_checked_at": now}])
        lookup.assert_not_called()

    def test_disabled_flag_skips_lookups(self):
        with patch.dict("os.environ", {"MEDFINDER_LIVE_PRICES": "0"}), \
                patch.object(live_price_refresh, "find_reference_listing") as lookup:
            result = live_price_refresh.substitutes_with_live_prices(self.brand)
        lookup.assert_not_called()
        self.assertEqual(result["comparison_status"], "catalog_price_unverified")

    def test_restricted_medicine_is_never_looked_up(self):
        warfarin = self._add("Warf", 80.0, composition="Warfarin (5mg)")
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "UPDATE medicine_ingredients SET ingredient_name = 'Warfarin', "
                "ingredient_name_norm = 'warfarin', ingredient_strength = '5mg', "
                "ingredient_strength_norm = '5mg' WHERE medicine_id = ?", (warfarin,))
            connection.commit()
        finally:
            connection.close()
        with patch.object(live_price_refresh, "find_reference_listing") as lookup:
            result = live_price_refresh.substitutes_with_live_prices(warfarin)
        lookup.assert_not_called()
        self.assertEqual(result["comparison_status"], "restricted_class")

    def test_stale_old_price_is_refreshed(self):
        old = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        brand = self._add("Oldol", 70.0, checked=old)
        with patch.object(live_price_refresh, "find_reference_listing", return_value=listing(55.0)):
            live_price_refresh.refresh_prices([{"id": brand, "composition": "x", "dosage_form": "Tablet",
                                                "price_checked_at": old}])
        self.assertEqual(self._row(brand)[0], 55.0)


if __name__ == "__main__":
    unittest.main()
