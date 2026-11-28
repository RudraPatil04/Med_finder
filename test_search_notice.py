"""Offline tests for the 'why is there no price' notices."""
import unittest

from backend.services.search_notice_service import build_notice

US_ONLY = {"source": "api", "price": None, "composition_complete": 0}


class SearchNoticeTests(unittest.TestCase):
    def test_not_found(self):
        self.assertEqual(build_notice("x", [], [])["kind"], "not_found")

    def test_cached_miss_explains_pause(self):
        self.assertEqual(build_notice("x", [], ["cached_miss"])["kind"], "recently_searched")

    def test_unreachable_catalog(self):
        notice = build_notice("x", [], ["RxNorm"])
        self.assertEqual(notice["kind"], "lookup_incomplete")
        self.assertIn("RxNorm", notice["message"])

    def test_us_only_result_warns_and_never_says_substitute(self):
        related = [{"id": 1}]
        notice = build_notice("x", [US_ONLY], [], "tazarotene", related)
        self.assertEqual(notice["kind"], "no_indian_price")
        self.assertIn("pharmacist", notice["message"])
        self.assertNotIn("substitute", notice["message"].lower())

    def test_priced_results_need_no_notice(self):
        self.assertIsNone(build_notice("x", [{"source": "local", "price": 10}], []))


if __name__ == "__main__":
    unittest.main()
