"""Assistant answers, disease lookup, and the assistant/disease page templates."""
import json
import pathlib
import re
import shutil
import subprocess
import unittest
from unittest import mock

from backend import assistant_service
from backend.assistant_service import find_medicines, process_assistant_query
from backend.disease_service import get_all_diseases, get_disease_by_id_or_name

ROOT = pathlib.Path(__file__).resolve().parent

DOLO = {"id": 7087, "medicine_name": "Dolo 650 Tablet", "composition": "Paracetamol (650mg)",
        "brand_name": "Dolo", "dosage_form": "Tablet", "strength": "650mg", "price": 33.0,
        "prescription_required": 0}
PAN = {"id": 109535, "medicine_name": "PAN 40 Tablet", "composition": "Pantoprazole (40mg)",
       "brand_name": "PAN", "dosage_form": "Tablet", "strength": "40mg", "price": 146.5,
       "prescription_required": 1}

BANNED = ("identical", "bioequivalen", "50%", "80%", "interchangeable")
DRUG_WORDS = ("paracetamol", "ibuprofen", "naproxen", "cetirizine", "levocetirizine",
              "acetaminophen", "mg")


def fake_search(rows_by_query):
    def search(keyword, limit=3, enable_api_fallback=False):
        return rows_by_query.get(keyword.lower(), [])
    return search


class AssistantTests(unittest.TestCase):
    def ask(self, message, rows_by_query=None):
        with mock.patch.object(assistant_service, "search_medicines",
                               side_effect=fake_search(rows_by_query or {})):
            return process_assistant_query(message)

    def test_lookup_ignores_question_words(self):
        calls = []

        def search(keyword, limit=3, enable_api_fallback=False):
            calls.append(keyword)
            return [DOLO] if keyword.lower() == "dolo 650" else []

        with mock.patch.object(assistant_service, "search_medicines", side_effect=search):
            found = find_medicines("Cheaper option for Dolo 650")
        self.assertEqual(calls[0], "Dolo 650")
        self.assertNotIn("option", calls)
        self.assertEqual(found[0]["id"], 7087)

    def test_symptom_words_never_look_up_products(self):
        with mock.patch.object(assistant_service, "search_medicines") as search:
            self.assertEqual(find_medicines("What can I take for a mild headache?"), [])
            self.assertEqual(find_medicines("I have fever and cold"), [])
        search.assert_not_called()

    def test_lookup_rejects_partial_word_hits(self):
        hellox = {"id": 1, "medicine_name": "Hellox 500mg Tablet", "composition": "Ofloxacin (500mg)"}
        with mock.patch.object(assistant_service, "search_medicines", return_value=[hellox]):
            self.assertEqual(find_medicines("hello there"), [])

    def test_generic_answer_is_honest_and_links_detail_page(self):
        reply = self.ask("Cheaper option for Dolo 650", {"dolo 650": [DOLO]})
        self.assertIn("[Dolo 650 Tablet](/medicine/7087)", reply)
        self.assertIn("pharmacist", reply.lower())
        for word in BANNED:
            self.assertNotIn(word, reply.lower())

    def test_generic_answer_without_medicine_asks_for_name(self):
        reply = self.ask("Are generic medicines cheaper?")
        self.assertIn("medicine name", reply)
        for word in BANNED:
            self.assertNotIn(word, reply.lower())

    def test_composition_question(self):
        reply = self.ask("What is in Pan 40?", {"pan 40": [PAN]})
        self.assertIn("Pantoprazole (40mg)", reply)
        self.assertIn("/medicine/109535", reply)
        self.assertIn("may not be current", reply)

    def test_symptom_answers_name_no_drugs_or_doses(self):
        for message in ("I have a fever", "What can I take for a mild headache?", "cold and cough"):
            reply = self.ask(message).lower()
            self.assertIn("pharmacist", reply, message)
            for word in DRUG_WORDS:
                self.assertNotIn(word, reply, f"{message!r} mentions {word!r}")

    def test_symptom_answers_keep_red_flags(self):
        self.assertIn("see a doctor", self.ask("I have a fever").lower())
        self.assertIn("emergency", self.ask("bad headache").lower())

    def test_emergency_warning_comes_first(self):
        reply = self.ask("chest pain and fever", {"chest": [DOLO]})
        self.assertIn("EMERGENCY", reply)

    def test_fallback_suggests_supported_questions(self):
        reply = self.ask("hello")
        self.assertIn("Pan 40", reply)


class DiseaseServiceTests(unittest.TestCase):
    def test_thirty_conditions(self):
        ids = [d["id"] for d in get_all_diseases()]
        self.assertEqual(len(ids), 30)
        self.assertEqual(len(set(ids)), 30)

    def test_symptom_queries_find_a_condition(self):
        self.assertEqual(get_disease_by_id_or_name("headache")["id"], "headache")
        self.assertEqual(get_disease_by_id_or_name("wheezing")["id"], "asthma")
        self.assertEqual(get_disease_by_id_or_name("heartburn")["id"], "gerd")

    def test_unknown_query_is_no_match(self):
        self.assertIsNone(get_disease_by_id_or_name("zzzunknown"))

    def test_empty_query_returns_default(self):
        self.assertEqual(get_disease_by_id_or_name("")["id"], "common_cold")


class DiseaseRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from backend.app import app
        cls.client = app.test_client()

    def test_list_returns_all(self):
        data = self.client.get("/api/disease").get_json()
        self.assertEqual(len(data["all"]), 30)

    def test_unknown_returns_no_match(self):
        response = self.client.get("/api/disease?q=zzzunknown")
        self.assertEqual(response.status_code, 404)
        data = response.get_json()
        self.assertFalse(data["success"])
        self.assertIsNone(data["data"])

    def test_symptom_query(self):
        data = self.client.get("/api/disease?q=wheezing").get_json()
        self.assertEqual(data["data"]["id"], "asthma")


class TemplateTests(unittest.TestCase):
    def test_assistant_has_no_fake_history_or_status(self):
        html = (ROOT / "templates" / "assistant.html").read_text(encoding="utf-8")
        self.assertNotIn("history-item", html)
        self.assertNotIn("Online", html)
        self.assertNotIn("pregnancy", html)
        self.assertNotIn("mild headache", html)
        self.assertIn("Cheaper option for Dolo 650", html)
        self.assertIn("What is in Pan 40?", html)

    def test_disease_page_has_live_region(self):
        html = (ROOT / "templates" / "disease_guidance.html").read_text(encoding="utf-8")
        self.assertIn('aria-live="polite"', html)

    def test_disease_list_not_hardcoded(self):
        js = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
        self.assertIn('fetchJson("/api/disease")', js)
        self.assertNotIn('emergency: "Blue lips', js)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class AssistantFormattingTests(unittest.TestCase):
    def render(self, text):
        js = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
        functions = []
        for name in ("escapeHtml", "formatAssistantText"):
            match = re.search(r"    function " + name + r"\(.*?\n    \}\n", js, re.S)
            self.assertIsNotNone(match, name)
            functions.append(match.group(0))
        script = "\n".join(functions) + "\nprocess.stdout.write(formatAssistantText(" + json.dumps(text) + "));"
        return subprocess.run(["node", "-e", script], capture_output=True, text=True,
                              encoding="utf-8", check=True).stdout

    def test_bold_and_line_breaks(self):
        self.assertEqual(self.render("**Hi**\nthere"), "<strong>Hi</strong><br>there")

    def test_html_is_escaped(self):
        out = self.render("<img src=x onerror=alert(1)> **<b>x</b>**")
        self.assertNotIn("<img", out)
        self.assertNotIn("<b>", out)
        self.assertIn("&lt;img", out)

    def test_only_internal_medicine_links(self):
        self.assertEqual(self.render("[Dolo](/medicine/7087)"), '<a href="/medicine/7087">Dolo</a>')
        out = self.render("[x](javascript:alert(1)) [y](https://evil.example/medicine/1)")
        self.assertNotIn("<a", out)


if __name__ == "__main__":
    unittest.main()
