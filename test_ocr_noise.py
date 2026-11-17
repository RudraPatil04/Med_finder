"""Offline tests for the unreadable-handwriting guard."""
import unittest

from backend import ocr_service as ocr

NOISE = "be) Hospit h Centre an\nBe soe Negere 7) 48. @) l\nDBD ARNG 5. NOL US uersion\nUe pee Veobloss nos\nole fp - viol\n"


class OcrNoiseTests(unittest.TestCase):
    def test_symbol_noise_is_garbled(self):
        self.assertTrue(ocr._looks_garbled("Be soe Negere 7) 48. @) l"))

    def test_noisy_page_is_flagged_as_handwriting(self):
        self.assertTrue(ocr._handwriting_suspected(NOISE))

    def test_clean_printed_page_is_not_flagged(self):
        self.assertFalse(ocr._handwriting_suspected(
            "Tab Dolo 650 mg bd\nCap Amoxicillin 500mg tds\nSyp Cough 5 ml"))

    def test_clinical_note_words_are_not_medicines(self):
        self.assertTrue(ocr._looks_like_header("Clo giddiness, restlessness"))


if __name__ == "__main__":
    unittest.main()
