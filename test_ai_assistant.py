"""Optional Gemini assistant: switch, grounding, cache, safety filter and fallbacks.
Gemini is always mocked; these tests make no network calls."""
import os
import unittest
from unittest import mock

import requests

from backend import ai_assistant, assistant_service, ocr_service
from backend.assistant_service import DISCLAIMER, process_assistant_query

DOLO = {"id": 7087, "medicine_name": "Dolo 650 Tablet", "composition": "Paracetamol (650mg)",
        "brand_name": "Dolo", "dosage_form": "Tablet", "strength": "650mg", "price": 33.0,
        "pack_size": "15 tablets", "composition_complete": 1, "prescription_required": 0}

SUBSTITUTES = {
    "comparison_status": "catalog_price_unverified",
    "message": "The recorded price for this product is not a current listing.",
    "same_composition_alternatives": [],
    "same_composition_options": [
        {"id": 205479, "medicine_name": "Dolopar 650 Tablet", "composition": "Paracetamol (650mg)",
         "pack_size": "15 tablets", "price": 20.0, "price_is_current": False,
         "estimated_savings_percent": 39.4},
    ],
    "reference_price_options": [
        {"product_name": "Paracetamol Tablets IP 650 mg", "pack": "10's", "price": 9.5,
         "price_is_current": False},
    ],
}

ON = {"MEDFINDER_AI_ASSISTANT": "1", "GEMINI_API_KEY": "test-key"}


def gemini_response(text, status=200):
    response = mock.Mock(status_code=status)
    response.json.return_value = {"candidates": [{"content": {"parts": [{"text": text}]}}]}
    return response


class AiAssistantTests(unittest.TestCase):
    def setUp(self):
        ai_assistant.clear_cache()
        ocr_service._gemini_rest_until.clear()
        patches = [
            mock.patch.object(assistant_service, "search_medicines",
                              side_effect=lambda q, **kw: [DOLO] if "dolo" in q.lower() else []),
            mock.patch("backend.recommendation_service.get_substitutes", return_value=SUBSTITUTES),
            mock.patch.dict(os.environ, {"GEMINI_ASSISTANT_MODEL": ""}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        for name in ocr_service.GEMINI_KEY_VARIABLES + (ai_assistant.AI_FLAG,):
            p = mock.patch.dict(os.environ, {name: ""})
            p.start()
            self.addCleanup(p.stop)

    def ask(self, message, env=ON, post=None):
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(ai_assistant.requests, "post", post or mock.Mock()) as posted:
            return process_assistant_query(message), posted

    def test_off_by_default_uses_rules_and_never_calls_gemini(self):
        reply, posted = self.ask("Cheaper option for Dolo 650", env={"GEMINI_API_KEY": "test-key"})
        posted.assert_not_called()
        self.assertIn("Same-composition options", reply)

    def test_missing_key_falls_back(self):
        reply, posted = self.ask("Cheaper option for Dolo 650", env={"MEDFINDER_AI_ASSISTANT": "1"})
        posted.assert_not_called()
        self.assertIn("Same-composition options", reply)

    def test_answer_is_grounded_and_labelled(self):
        post = mock.Mock(return_value=gemini_response(
            "[Dolopar 650 Tablet](/medicine/205479) has the same recorded composition. "
            "Please confirm with a pharmacist."))
        reply, _ = self.ask("Cheaper option for Dolo 650", post=post)
        self.assertIn("Dolopar", reply)
        self.assertIn("Written by Gemini AI", reply)
        self.assertTrue(reply.endswith(DISCLAIMER))
        body = post.call_args.kwargs["json"]
        prompt = body["contents"][0]["parts"][0]["text"]
        self.assertIn("Paracetamol (650mg)", prompt)
        self.assertIn("/medicine/205479", prompt)
        self.assertIn("Jan Aushadhi", prompt)
        self.assertIn("catalog_price_unverified", prompt)
        self.assertIn("gemini-flash-lite-latest", post.call_args.args[0])
        self.assertIn("interchangeable", body["systemInstruction"]["parts"][0]["text"])
        self.assertLessEqual(post.call_args.kwargs["timeout"], ai_assistant.AI_TIMEOUT_SECONDS)
        self.assertEqual(post.call_args.kwargs["headers"], {"x-goog-api-key": "test-key"})

    def test_repeat_question_is_cached(self):
        post = mock.Mock(return_value=gemini_response("Answer. Confirm with a pharmacist."))
        first, _ = self.ask("Cheaper option for Dolo 650?", post=post)
        second, _ = self.ask("cheaper option for dolo 650", post=post)
        self.assertEqual(first, second)
        self.assertEqual(post.call_count, 1)

    def test_quota_exhaustion_falls_back(self):
        post = mock.Mock(return_value=mock.Mock(status_code=429, json=mock.Mock(return_value={})))
        reply, _ = self.ask("Cheaper option for Dolo 650", post=post)
        self.assertIn("Same-composition options", reply)
        self.assertNotIn("Gemini", reply)

    def test_timeout_falls_back(self):
        post = mock.Mock(side_effect=requests.Timeout())
        reply, _ = self.ask("What is in Dolo 650?", post=post)
        self.assertIn("Paracetamol (650mg)", reply)
        self.assertNotIn("Gemini", reply)

    def test_deadline_stops_trying_models(self):
        post = mock.Mock(side_effect=requests.ConnectionError())
        with mock.patch.object(ai_assistant, "AI_TIMEOUT_SECONDS", 0):
            reply, _ = self.ask("What is in Dolo 650?", post=post)
        self.assertEqual(post.call_count, 0)
        self.assertNotIn("Gemini", reply)

    def test_unsafe_answers_are_discarded(self):
        for text in ("Dolopar is identical to Dolo.",
                     "They are bioequivalent.",
                     "These products are interchangeable.",
                     "Take one tablet 3 times a day."):
            ai_assistant.clear_cache()
            reply, _ = self.ask("Cheaper option for Dolo 650", post=mock.Mock(return_value=gemini_response(text)))
            self.assertNotIn("Gemini", reply, text)

    def test_negated_interchangeable_is_allowed(self):
        post = mock.Mock(return_value=gemini_response(
            "They are not confirmed as interchangeable; ask a pharmacist."))
        reply, _ = self.ask("Cheaper option for Dolo 650", post=post)
        self.assertIn("Written by Gemini AI", reply)

    def test_emergency_never_reaches_gemini(self):
        reply, posted = self.ask("chest pain after Dolo 650")
        posted.assert_not_called()
        self.assertIn("EMERGENCY", reply)

    def test_symptom_question_without_medicine_uses_rules(self):
        reply, posted = self.ask("I have a fever")
        posted.assert_not_called()
        self.assertIn("Fever", reply)

    def test_incomplete_record_skips_alternatives_lookup(self):
        sparse = dict(DOLO, composition_complete=0)
        with mock.patch("backend.recommendation_service.get_substitutes") as subs:
            context = ai_assistant.build_context(sparse)
        subs.assert_not_called()
        self.assertIn("alternatives_note", context)


if __name__ == "__main__":
    unittest.main()
