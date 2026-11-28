"""Medicine detail page extras: ingredient uses, 1mg / pharmacy links, alternatives panel."""
import pathlib
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlparse

from backend.ingredient_uses import USES, ALIASES, buy_links, ingredient_use, ingredient_uses

ROOT = pathlib.Path(__file__).resolve().parent


class IngredientUsesTests(unittest.TestCase):
    def test_single_ingredient_gets_its_use(self):
        uses = ingredient_uses("Dexamethasone (4mg)")
        self.assertEqual(len(uses), 1)
        self.assertIn("steroid", uses[0]["use"])

    def test_salt_and_spelling_variants_resolve(self):
        self.assertEqual(ingredient_use("Atorvastatin Calcium"), USES["atorvastatin"])
        self.assertEqual(ingredient_use("Metformin Hydrochloride"), USES["metformin"])
        self.assertEqual(ingredient_use("Amoxycillin"), USES["amoxicillin"])
        self.assertEqual(ingredient_use("Potassium Clavulanate"), USES["clavulanicacid"])

    def test_unknown_ingredient_gets_no_text(self):
        self.assertIsNone(ingredient_use("Unknownium"))
        self.assertEqual(ingredient_uses("Unknownium (5mg)"), [])
        self.assertEqual(ingredient_uses(""), [])
        self.assertEqual(ingredient_uses(None), [])

    def test_partly_known_combination_lists_the_gap(self):
        uses = ingredient_uses("Paracetamol (500mg) + Unknownium (2mg)")
        self.assertEqual([u["use"] is None for u in uses], [False, True])

    def test_aliases_point_at_real_entries(self):
        for alias, target in ALIASES.items():
            self.assertIn(target, USES, alias)

    def test_no_marketing_or_certainty_words(self):
        banned = ("cure", "guarantee", "best", "safe for", "100%", "miracle", "interchangeable")
        for key, text in USES.items():
            lowered = text.lower()
            for word in banned:
                self.assertNotIn(word, lowered, key)


class BuyLinkTests(unittest.TestCase):
    def test_onemg_search_uses_this_medicine_name(self):
        links = buy_links({"medicine_name": "Auradex 4mg Tablet", "strength": "4mg"})
        url = urlparse(links["onemg"]["url"])
        self.assertEqual(url.netloc, "www.1mg.com")
        self.assertEqual(parse_qs(url.query)["name"], ["Auradex 4mg Tablet"])
        self.assertFalse(links["onemg"]["exact"])

    def test_strength_added_when_name_lacks_it(self):
        links = buy_links({"medicine_name": "Dolo Tablet", "strength": "650mg"})
        self.assertEqual(links["onemg"]["query"], "Dolo Tablet 650mg")

    def test_pharmacy_map_link(self):
        links = buy_links({"medicine_name": "Dolo 650 Tablet"})
        self.assertEqual(links["pharmacy_map"]["url"], "https://www.google.com/maps/search/pharmacy+near+me")

    def test_us_only_rows_get_no_onemg_link(self):
        links = buy_links({"medicine_name": "Tylenol", "medicine_category": "External"})
        self.assertNotIn("onemg", links)


class DetailRouteTests(unittest.TestCase):
    MEDICINE = {
        "id": 180052, "medicine_name": "Auradex 4mg Tablet", "brand_name": "Auradex", "strength": "4mg",
        "dosage_form": "Tablet", "composition": "Dexamethasone (4mg)", "composition_complete": 1,
        "pack_size": "10 tablets", "mrp": 10.0, "medicine_category": "Oral", "price": 10.0,
    }

    def test_detail_api_carries_uses_and_links(self):
        from backend.app import app
        with mock.patch("backend.routes.medicine_routes.get_medicine", return_value=dict(self.MEDICINE)), \
                mock.patch("backend.routes.medicine_routes.request_enrichment"):
            data = app.test_client().get("/api/medicine/180052").get_json()["data"]
        self.assertEqual(data["medicine_name"], "Auradex 4mg Tablet")
        self.assertEqual(data["ingredient_uses"][0]["ingredient"], "Dexamethasone")
        self.assertIn("Ask your doctor or pharmacist", data["ingredient_uses_note"])
        self.assertIn("1mg.com/search/all?name=Auradex+4mg+Tablet", data["buy_links"]["onemg"]["url"])


class DetailPageScriptTests(unittest.TestCase):
    def setUp(self):
        self.js = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")

    def test_outbound_links_open_in_new_tab_without_opener(self):
        start = self.js.index("function buyActions")
        block = self.js[start:self.js.index("function usesCard")]
        self.assertEqual(block.count('target="_blank" rel="noopener noreferrer"'), 2)
        self.assertNotIn("in stock", block.lower())

    def test_filler_uses_and_benefits_are_gone(self):
        self.assertNotIn("Compare price and composition", self.js)
        self.assertNotIn('infoCard("Benefits"', self.js)

    def test_detail_page_reuses_search_alternatives(self):
        start = self.js.index("function setupMedicineDetail")
        block = self.js[start:self.js.index("function setupScanner")]
        self.assertIn("loadCheaperAlternatives(data.data", block)
        self.assertIn("Not proven interchangeable: ask your doctor or pharmacist", self.js)


if __name__ == "__main__":
    unittest.main()
