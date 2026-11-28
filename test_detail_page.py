"""Medicine detail page extras: ingredient uses, 1mg / pharmacy links, alternatives panel."""
import pathlib
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlparse
import json
import shutil
import subprocess

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


def _summary_block(js):
    start = js.index("// --- alternatives-summary:start")
    return js[start:js.index("// --- alternatives-summary:end")]


NODE = shutil.which("node")


@unittest.skipUnless(NODE, "node is not installed; the summary logic is still covered by the text checks")
class AlternativesSummaryTests(unittest.TestCase):
    """Runs the pure summary/badge/sort functions from app.js in node."""

    def run_js(self, expression, data):
        js = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
        script = _summary_block(js) + "\nconst data = " + json.dumps(data) + ";\n" + \
            "process.stdout.write(JSON.stringify(" + expression + "));"
        out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True, timeout=30)
        return json.loads(out.stdout)

    def summary(self, data):
        return self.run_js("alternativesSummary(data)", data)

    OPTIONS = [
        {"id": 2, "medicine_name": "Brand B", "price": 30, "pack_size": "10 tablets", "price_per_unit_value": 3.0,
         "estimated_savings_percent": 40.0},
        {"id": 1, "medicine_name": "Aclonac", "price": 60, "pack_size": "30 tablets", "price_per_unit_value": 2.0,
         "estimated_savings_percent": 60.0},
        {"id": 3, "medicine_name": "Brand C", "price": 45, "pack_size": "10 tablets", "price_per_unit_value": 4.5},
    ]

    def test_estimated_saving_is_labelled_as_estimate(self):
        result = self.summary({"success": True, "comparison_status": "catalog_price_unverified",
                               "reference_price_available": True,
                               "same_composition_options": self.OPTIONS, "same_composition_option_count": 7})
        text = result["text"]
        self.assertEqual(result["tone"], "estimated")
        self.assertTrue(text.startswith("7 same-composition options (the 3 lowest per unit are listed) at catalog prices."))
        self.assertIn("Lowest per unit is Aclonac at Rs. 60 for 30 tablets (Rs. 2 per unit)", text)
        self.assertIn("an estimated 60% less per unit", text)
        self.assertIn("estimated saving vs catalog price", text)
        self.assertNotIn("verified saving", text)
        self.assertIn("does not make them interchangeable", text)
        self.assertIn("ask your doctor or pharmacist", text)

    def test_verified_saving_needs_the_checked_list(self):
        cheaper = [{"id": 5, "medicine_name": "Cheapo", "price": 20, "pack_size": "10 tablets",
                    "price_per_unit_value": 2.0, "savings_percent": 47.2, "savings_basis": "per unit",
                    "savings_reliable": True}]
        result = self.summary({"success": True, "same_composition_alternatives": cheaper,
                               "same_composition_count": 1, "same_composition_options": self.OPTIONS})
        self.assertEqual(result["tone"], "verified")
        self.assertTrue(result["text"].startswith("1 cheaper same-composition option with recently checked prices."))
        self.assertIn("a verified saving of 47% per unit (both prices checked in the last 30 days)", result["text"])

    def test_no_estimate_when_this_medicine_has_no_price(self):
        options = [dict(self.OPTIONS[2])]
        result = self.summary({"success": True, "reference_price_available": False,
                               "same_composition_options": options})
        self.assertIn("1 same-composition option at catalog prices", result["text"])
        self.assertIn("no recorded price, so no saving is estimated", result["text"])
        self.assertNotIn("%", result["text"])

    def test_no_alternatives_said_plainly(self):
        result = self.summary({"success": True, "same_composition_options": [], "same_composition_alternatives": []})
        self.assertIn("No other product with the same recorded ingredients", result["text"])

    def test_restricted_class_offers_nothing(self):
        result = self.summary({"success": True, "comparison_status": "restricted_class",
                               "restricted_class_reason": "it has a narrow therapeutic index",
                               "same_composition_options": self.OPTIONS})
        self.assertEqual(result["tone"], "warning")
        self.assertIn("No alternatives are shown for this medicine because it has a narrow therapeutic index", result["text"])
        self.assertNotIn("Aclonac", result["text"])

    def test_failed_request(self):
        self.assertEqual(self.summary(None)["tone"], "muted")
        self.assertEqual(self.summary({"success": False})["tone"], "muted")

    def test_badges_mark_cheapest_pack_and_lowest_per_unit(self):
        badges = self.run_js("alternativeBadges(data, false)", self.OPTIONS)
        self.assertEqual(badges, {"2": ["Cheapest pack"], "1": ["Lowest per unit"]})

    def test_verified_badge_only_on_checked_savings(self):
        items = [{"id": 1, "price": 10, "price_per_unit_value": 1, "savings_percent": 20, "savings_reliable": True}]
        self.assertEqual(self.run_js("alternativeBadges(data, true)", items), {"1": ["Verified saving"]})
        self.assertEqual(self.run_js("alternativeBadges(data, false)", items), {})

    def test_single_option_gets_no_ranking_badge(self):
        self.assertEqual(self.run_js("alternativeBadges(data, false)", self.OPTIONS[:1]), {})

    def test_sort_by_unit_and_pack(self):
        self.assertEqual(self.run_js("sortAlternatives(data, 'unit').map(i => i.id)", self.OPTIONS), [1, 2, 3])
        self.assertEqual(self.run_js("sortAlternatives(data, 'pack').map(i => i.id)", self.OPTIONS), [2, 3, 1])
        self.assertEqual(self.run_js("data.map(i => i.id)", self.OPTIONS), [2, 1, 3])


class DetailSummaryScriptTests(unittest.TestCase):
    def setUp(self):
        self.js = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")

    def test_generic_ai_recommendation_is_gone(self):
        self.assertNotIn("AI Recommendation", self.js)
        self.assertIn('id="detailSummary" aria-live="polite"', self.js)

    def test_summary_uses_the_existing_substitutes_answer(self):
        start = self.js.index("async function loadCheaperAlternatives")
        block = self.js[start:self.js.index("function sortToggle")]
        self.assertEqual(block.count("fetchJson("), 1)
        self.assertIn("renderDetailSummary(data)", block)
        summary = _summary_block(self.js)
        self.assertNotIn("fetch", summary)
        self.assertNotIn("document", summary)

    def test_summary_never_claims_interchangeability(self):
        summary = _summary_block(self.js).lower()
        self.assertNotIn("is interchangeable", summary)
        self.assertNotIn("can be swapped", summary)
        self.assertIn("does not make them interchangeable", summary)

    def test_pharmacist_questions_are_on_the_detail_page(self):
        start = self.js.index("function renderMedicineDetail")
        block = self.js[start:self.js.index("function pharmacistQuestions")]
        self.assertIn("pharmacistQuestions()", block)
        self.assertIn("Questions to ask your pharmacist", self.js)


if __name__ == "__main__":
    unittest.main()
