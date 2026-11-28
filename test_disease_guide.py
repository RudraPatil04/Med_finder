"""Disease guide: 30 curated conditions, search words, related catalog medicines,
and the optional Gemini guide for other searches (Gemini always mocked)."""
import os
import re
import sqlite3
import tempfile
import unittest
from unittest import mock

from backend import ai_assistant, database, disease_medicines, ocr_service
from backend.disease_medicines import RELIEF_PRODUCTS, related_medicines, relief_groups
from backend.disease_service import SEE_DOCTOR, get_all_diseases, get_disease_by_id_or_name, search_diseases

PRESCRIPTION_ONLY = ["diabetes", "hypertension", "high_cholesterol", "thyroid", "anemia", "asthma",
                     "dengue", "malaria", "typhoid", "uti", "conjunctivitis"]
DOSE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|ml|iu)\b|\bdose\b|\bdosage\b|\btablets?\b|\bcapsules?\b", re.I)


class CuratedConditionTests(unittest.TestCase):
    def test_every_condition_is_complete(self):
        for d in get_all_diseases():
            for field in ("id", "name", "category", "severity", "emergency"):
                self.assertTrue(d[field], f"{d['id']} {field}")
            for field in ("symptoms", "causes", "lifestyle", "diet", "keywords"):
                self.assertTrue(d[field], f"{d['id']} {field}")
            for key in d["relief"]:
                self.assertIn(key, RELIEF_PRODUCTS, d["id"])
            self.assertTrue(d["relief"] or d["medicine_note"], d["id"])

    def test_no_doses_in_guidance_text(self):
        for d in get_all_diseases():
            text = " ".join([d["emergency"]] + d["symptoms"] + d["causes"] + d["lifestyle"] + d["diet"])
            self.assertIsNone(DOSE.search(text), f"{d['id']}: {DOSE.search(text)}")

    def test_prescription_conditions_list_no_medicines(self):
        by_id = {d["id"]: d for d in get_all_diseases()}
        for cid in PRESCRIPTION_ONLY:
            self.assertEqual(by_id[cid]["relief"], [], cid)
            self.assertEqual(by_id[cid]["medicine_note"], SEE_DOCTOR, cid)

    def test_everyday_words_find_the_right_condition(self):
        expected = {
            "cold": "common_cold", "flu": "flu", "fever": "viral_fever", "cough": "cough",
            "sore throat": "sore_throat", "headache": "headache", "migraine": "migraine",
            "loose motions": "diarrhea", "acidity": "gerd", "sugar": "diabetes", "bp": "hypertension",
            "eye flu": "conjunctivitis", "pimples": "acne", "ringworm": "fungal_skin",
            "wheezing": "asthma", "Common Cold": "common_cold", "insomnia": "anxiety_insomnia",
        }
        for query, cid in expected.items():
            self.assertEqual(get_disease_by_id_or_name(query)["id"], cid, query)

    def test_unknown_word_matches_nothing(self):
        self.assertIsNone(get_disease_by_id_or_name("xyzzy"))
        self.assertEqual(search_diseases("xyzzy"), [])

    def test_substring_inside_a_word_does_not_match(self):
        # "flu" must not find GERD through "reflux".
        self.assertNotIn("gerd", [d["id"] for d in search_diseases("flu")])


class RelatedMedicineTests(unittest.TestCase):
    """Runs against a small temporary catalog."""

    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        conn = sqlite3.connect(self.db_path)
        conn.executescript("""
            CREATE TABLE medicines (id INTEGER PRIMARY KEY, medicine_name TEXT, brand_name TEXT,
                composition TEXT, composition_complete INTEGER, dosage_form TEXT, strength TEXT,
                release_type TEXT, pack_size TEXT, price REAL, price_source TEXT, price_checked_at TEXT,
                image_url TEXT, prescription_required TEXT);
            CREATE TABLE medicine_ingredients (id INTEGER PRIMARY KEY, medicine_id INTEGER,
                ingredient_name TEXT, ingredient_name_norm TEXT, ingredient_strength TEXT,
                ingredient_strength_norm TEXT);
        """)
        rows = [
            # id, name, form, pack, price, rx, ingredients
            (1, "Cheap 500 Tablet", "Tablet", "10 tablets", 10.0, "FALSE", [("paracetamol", "500mg")]),
            (2, "Pricey 650 Tablet", "Tablet", "10 tablets", 30.0, "TRUE", [("paracetamol", "650mg")]),
            (3, "Bulk 500 Tablet", "Tablet", "100 tablets", 50.0, "FALSE", [("paracetamol", "500mg")]),
            (4, "Kids 125 Tablet", "Tablet", "10 tablets", 1.0, "FALSE", [("paracetamol", "125mg")]),
            (5, "Para Syrup", "Syrup", "60 ml", 1.0, "FALSE", [("paracetamol", "500mg")]),
            (6, "Combo Tablet", "Tablet", "10 tablets", 1.0, "FALSE",
             [("paracetamol", "500mg"), ("caffeine", "30mg")]),
            (7, "Cheap 500 Tablet", "Tablet", "10 tablets", 12.0, "FALSE", [("paracetamol", "500mg")]),
            (8, "No Price Tablet", "Tablet", "10 tablets", 0, "FALSE", [("paracetamol", "500mg")]),
        ]
        for mid, name, form, pack, price, rx, ingredients in rows:
            conn.execute(
                "INSERT INTO medicines (id, medicine_name, brand_name, composition, composition_complete, "
                "dosage_form, strength, pack_size, price, prescription_required) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (mid, name, name.split()[0], " + ".join(f"{n} ({s})" for n, s in ingredients), 1,
                 form, ingredients[0][1], pack, price, rx))
            for n, s in ingredients:
                conn.execute("INSERT INTO medicine_ingredients (medicine_id, ingredient_name, "
                             "ingredient_name_norm, ingredient_strength, ingredient_strength_norm) "
                             "VALUES (?,?,?,?,?)", (mid, n, n, s, s))
        conn.commit()
        conn.close()
        patcher = mock.patch.object(database, "MEDICINE_DATABASE", self.db_path)
        patcher.start()
        self.addCleanup(patcher.stop)
        disease_medicines.clear_cache()
        self.addCleanup(disease_medicines.clear_cache)

    def tearDown(self):
        try:
            os.remove(self.db_path)
        except OSError:
            pass

    def test_exact_ingredient_strength_and_form_cheapest_per_unit_first(self):
        groups = relief_groups(["paracetamol"])
        names = [p["medicine_name"] for p in groups[0]["products"]]
        # Bulk 500 is Rs 0.50 a tablet, Cheap 500 Rs 1.00, Pricey 650 Rs 3.00.
        self.assertEqual(names, ["Bulk 500 Tablet", "Cheap 500 Tablet", "Pricey 650 Tablet"])
        products = {p["medicine_name"]: p for p in groups[0]["products"]}
        self.assertEqual(products["Cheap 500 Tablet"]["id"], 1)  # duplicate name: cheaper listing kept
        self.assertTrue(products["Pricey 650 Tablet"]["prescription_required"])
        self.assertFalse(products["Bulk 500 Tablet"]["price_is_current"])

    def test_condition_section_has_safety_wording(self):
        cold = get_disease_by_id_or_name("fever")
        section = related_medicines(cold)
        self.assertIn("Not a recommendation for you", section["relief_note"])
        self.assertIn("pharmacist", section["pharmacist_line"])

    def test_prescription_condition_has_note_and_no_products(self):
        section = related_medicines(get_disease_by_id_or_name("diabetes"))
        self.assertEqual(section["groups"], [])
        self.assertEqual(section["note"], SEE_DOCTOR)

    def test_unknown_relief_key_is_ignored(self):
        self.assertEqual(relief_groups(["warfarin", "paracetamol; DROP TABLE"]), [])

    def test_route(self):
        from backend.app import app
        client = app.test_client()
        data = client.get("/api/disease/medicines?id=viral_fever").get_json()["data"]
        self.assertEqual(data["groups"][0]["key"], "paracetamol")
        self.assertEqual(client.get("/api/disease/medicines?id=nope").status_code, 404)
        data = client.get("/api/disease/medicines?keys=paracetamol,ibuprofen,bogus").get_json()["data"]
        self.assertEqual([g["key"] for g in data["groups"]], ["paracetamol"])


def gemini_json(text):
    response = mock.Mock(status_code=200)
    response.json.return_value = {"candidates": [{"content": {"parts": [{"text": text}]}}]}
    return response


GOOD_GUIDE = """{"is_health_condition": true, "name": "Chickenpox", "category": "Infectious Disease",
 "summary": "A contagious viral infection that causes an itchy, blistering rash.",
 "symptoms": ["Itchy rash of fluid-filled blisters", "Fever", "Tiredness"],
 "causes": ["Varicella-zoster virus"], "lifestyle": ["Rest", "Avoid scratching"],
 "diet": ["Plenty of fluids"], "emergency": "Seek care for breathing difficulty or confusion.",
 "relief": ["paracetamol", "ibuprofen", "warfarin"]}"""


class AiDiseaseGuideTests(unittest.TestCase):
    def setUp(self):
        ai_assistant.clear_cache()
        ocr_service._gemini_rest_until.clear()
        env = {name: "" for name in ocr_service.GEMINI_KEY_VARIABLES}
        env.update({ai_assistant.AI_FLAG: "1", "GEMINI_API_KEY": "test-key", "GEMINI_ASSISTANT_MODEL": ""})
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def guide(self, query, post):
        with mock.patch.object(ai_assistant.requests, "post", post):
            return ai_assistant.ai_disease_guide(query)

    def test_guide_is_labelled_and_relief_is_allowlisted(self):
        post = mock.Mock(return_value=gemini_json(GOOD_GUIDE))
        guide = self.guide("chickenpox", post)
        self.assertTrue(guide["ai_generated"])
        self.assertEqual(guide["severity"], ai_assistant.AI_LABEL)
        self.assertEqual(guide["relief"], ["paracetamol"])
        self.assertIsNone(guide["medicine_note"])
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["generationConfig"]["responseMimeType"], "application/json")
        self.assertIn("Do NOT name any medicine", body["systemInstruction"]["parts"][0]["text"])

    def test_cached(self):
        post = mock.Mock(return_value=gemini_json(GOOD_GUIDE))
        self.guide("Chickenpox", post)
        self.guide("chickenpox ", post)
        self.assertEqual(post.call_count, 1)

    def test_not_a_condition(self):
        post = mock.Mock(return_value=gemini_json('{"is_health_condition": false}'))
        self.assertIsNone(self.guide("pizza", post))

    def test_dose_or_drug_text_is_rejected(self):
        bad = GOOD_GUIDE.replace('"Rest"', '"Take 500 mg every day"')
        self.assertIsNone(self.guide("chickenpox", mock.Mock(return_value=gemini_json(bad))))

    def test_failures_return_none(self):
        import requests
        self.assertIsNone(self.guide("chickenpox", mock.Mock(side_effect=requests.Timeout())))
        self.assertIsNone(self.guide("measles", mock.Mock(return_value=gemini_json("not json"))))

    def test_switch_off_makes_no_call(self):
        post = mock.Mock()
        with mock.patch.dict(os.environ, {ai_assistant.AI_FLAG: ""}):
            self.assertIsNone(self.guide("chickenpox", post))
        post.assert_not_called()

    def test_route_falls_back_to_no_guide(self):
        from backend.app import app
        client = app.test_client()
        with mock.patch.dict(os.environ, {ai_assistant.AI_FLAG: ""}):
            response = client.get("/api/disease?q=xyzzy")
        self.assertEqual(response.status_code, 404)
        data = response.get_json()
        self.assertIn("don't have a guide", data["message"])
        self.assertEqual(len(data["available"]), 30)

    def test_route_uses_ai_guide(self):
        from backend.app import app
        client = app.test_client()
        with mock.patch.object(ai_assistant.requests, "post", mock.Mock(return_value=gemini_json(GOOD_GUIDE))):
            data = client.get("/api/disease?q=chickenpox").get_json()
        self.assertTrue(data["data"]["ai_generated"])

    def test_curated_conditions_never_call_gemini(self):
        post = mock.Mock()
        from backend.app import app
        with mock.patch.object(ai_assistant.requests, "post", post):
            app.test_client().get("/api/disease?q=cold")
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
