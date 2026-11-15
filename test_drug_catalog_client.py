"""Offline tests for the DrugDB /v1/search client.

The catalogue moved from a JSON `/search` endpoint returning NRCeS-style
records to a documented `/v1/search` API returning flat product rows. These
tests pin the new shape so a silent change is caught without a network call.
"""

import os
import unittest
from unittest.mock import Mock, patch

from backend.services import drug_catalog_service as catalog

DOLO_650 = {
    "composition": "Paracetamol (650mg)",
    "dosage_form": "Tablet",
    "image": "https://medicinedata.in/drg/DRS091818_1.jpg",
    "kind": "drug",
    "manufacturer": "Micro Labs Ltd",
    "mrp": 32.28,
    "name": "Dolo 650 Tablet",
    "packaging": "strip of 15 tablets",
    "product_id": "DRS091818",
    "source": "vendor",
}


class DrugDbMappingTests(unittest.TestCase):
    def test_flat_product_row_is_mapped_to_the_local_schema(self):
        medicine = catalog._map_record(DOLO_650)

        self.assertIsNotNone(medicine)
        self.assertEqual(medicine["medicine_name"], "Dolo 650mg Tablet")
        self.assertEqual(medicine["brand_name"], "Dolo")
        self.assertEqual(medicine["strength"], "650mg")
        self.assertEqual(medicine["dosage_form"], "Tablet")
        self.assertEqual(medicine["composition"], "Paracetamol (650mg)")
        self.assertEqual(medicine["pack_size"], "15 tablets")
        self.assertEqual(medicine["api_record_id"], "DRS091818")
        self.assertEqual(medicine["api_source"], catalog.DRUGDB_SOURCE)
        self.assertIsNone(medicine["price"])
        self.assertEqual(medicine["composition_complete"], 1)

    def test_combination_keeps_every_ingredient(self):
        medicine = catalog._map_record({
            "name": "Augmentin 625 Duo Tablet",
            "composition": "Amoxycillin (500mg) + Clavulanic Acid (125mg)",
            "dosage_form": "Tablet",
            "packaging": "strip of 10 tablets",
            "product_id": "DRS100",
        })

        self.assertEqual(medicine["composition"], "Amoxycillin (500mg) + Clavulanic Acid (125mg)")
        self.assertEqual(medicine["strength"], "500mg/125mg")
        self.assertEqual(medicine["medicine_name"], "Augmentin Tablet")
        self.assertEqual(medicine["pack_size"], "10 tablets")

    def test_record_without_a_usable_composition_is_rejected(self):
        self.assertIsNone(catalog._map_record({**DOLO_650, "composition": "(650mg)"}))
        self.assertIsNone(catalog._map_record({**DOLO_650, "composition": ""}))

    def test_record_without_a_dosage_form_is_rejected(self):
        record = {**DOLO_650, "name": "Dolo", "dosage_form": ""}
        self.assertIsNone(catalog._map_record(record))


class DrugDbRequestTests(unittest.TestCase):
    def test_request_uses_v1_search_and_an_optional_key(self):
        response = Mock()
        response.json.return_value = {"results": [DOLO_650]}
        with patch.object(catalog.requests, "get", return_value=response) as call, \
                patch.dict(os.environ, {"DRUGDB_API_KEY": "key-123"}):
            records = catalog._fetch_records("dolo 650", 5)

        self.assertEqual(len(records), 1)
        url = call.call_args.args[0]
        self.assertTrue(url.endswith("/v1/search"), url)
        self.assertEqual(call.call_args.kwargs["params"]["type"], "drug")
        self.assertEqual(call.call_args.kwargs["params"]["limit"], 5)
        self.assertEqual(call.call_args.kwargs["headers"]["X-Api-Key"], "key-123")

    def test_no_key_header_without_an_environment_key(self):
        response = Mock()
        response.json.return_value = {"results": []}
        with patch.object(catalog.requests, "get", return_value=response) as call, \
                patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DRUGDB_API_KEY", None)
            catalog._fetch_records("dolo", 5)

        self.assertNotIn("X-Api-Key", call.call_args.kwargs["headers"])

    def test_an_html_body_raises_a_readable_error(self):
        response = Mock()
        response.json.side_effect = ValueError("Expecting value: line 1 column 1 (char 0)")
        with patch.object(catalog.requests, "get", return_value=response):
            with self.assertRaises(ValueError) as caught:
                catalog._fetch_records("dolo", 5)

        self.assertIn("non-JSON", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
