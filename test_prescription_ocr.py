"""Focused tests for prescription extraction without a live OCR engine."""

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from backend import database
from backend import ocr_service
from backend.medicine_data import parse_composition


class PrescriptionOcrTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_medfinder.db"
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute("""CREATE TABLE medicines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                medicine_name TEXT NOT NULL,
                brand_name TEXT,
                strength TEXT,
                dosage_form TEXT,
                medicine_category TEXT,
                composition TEXT,
                price REAL,
                price_source TEXT,
                price_checked_at TEXT,
                prescription_required TEXT,
                image_url TEXT,
                release_type TEXT,
                pack_size TEXT,
                composition_complete INTEGER NOT NULL DEFAULT 0,
                rxnorm_rxcui TEXT,
                product_key TEXT,
                source TEXT DEFAULT 'local',
                search_norm TEXT,
                api_source TEXT
            )""")
            connection.execute("""CREATE TABLE medicine_ingredients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                medicine_id INTEGER NOT NULL,
                ingredient_name TEXT NOT NULL,
                ingredient_name_norm TEXT NOT NULL,
                ingredient_strength TEXT,
                ingredient_strength_norm TEXT
            )""")
            rows = [
                ("Dolo 650 Tablet", "Dolo", "650mg", "Tablet", "Paracetamol (650mg)", 32.0),
                ("Augmentin 625 Duo Tablet", "Augmentin", "625mg", "Tablet",
                 "Amoxicillin (500mg) + Clavulanic Acid (125mg)", 120.0),
                ("Pantoprazole 40 Tablet", "Pantoprazole", "40mg", "Tablet",
                 "Pantoprazole (40mg)", 18.0),
                ("Paracetamol 500mg Tablet", "Paracetamol", "500mg", "Tablet",
                 "Paracetamol (500mg)", 12.0),
            ]
            for name, brand, strength, form, composition, price in rows:
                medicine_id = connection.execute(
                    """INSERT INTO medicines(
                        medicine_name, brand_name, strength, dosage_form, composition,
                        price, composition_complete, source
                    ) VALUES (?,?,?,?,?,?,1,'local')""",
                    (name, brand, strength, form, composition, price),
                ).lastrowid
                for ingredient in parse_composition(composition):
                    connection.execute(
                        """INSERT INTO medicine_ingredients(
                            medicine_id, ingredient_name, ingredient_name_norm,
                            ingredient_strength, ingredient_strength_norm
                        ) VALUES (?,?,?,?,?)""",
                        (medicine_id, ingredient["ingredient_name"], ingredient["ingredient_name_norm"],
                         ingredient["ingredient_strength"], ingredient["ingredient_strength_norm"]),
                    )
            connection.commit()
        finally:
            connection.close()

        self.db_patch = patch.object(database, "MEDICINE_DATABASE", self.db_path)
        self.db_patch.start()
        self.upload_dir = Path(self.temp_dir.name) / "uploads"
        self.upload_dir.mkdir()
        self.upload_patch = patch.object(ocr_service, "UPLOAD_DIR", self.upload_dir)
        self.upload_patch.start()

    def tearDown(self):
        self.upload_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _dummy_upload(self, filename="rx.png"):
        return type("Upload", (), {
            "filename": filename,
            "save": lambda self, path: Path(path).write_bytes(b"fake"),
        })()

    def test_printed_line_matches_catalog_with_strength_and_dosage(self):
        medicines, unmatched = ocr_service._extract_medicines(
            "Dr. A Sharma Clinic\n"
            "Tab Dolo 650 mg 1-0-1 after meals for 5 days\n"
            "Cap Augmentin 625 mg 1-0-1 for 7 days\n"
        )

        by_name = {item["name"]: item for item in medicines}
        self.assertIn("Dolo 650 Tablet", by_name)
        self.assertEqual(by_name["Dolo 650 Tablet"]["match_type"], "exact")
        self.assertTrue(by_name["Dolo 650 Tablet"]["strength_match"])
        self.assertIn("1-0-1", by_name["Dolo 650 Tablet"]["dosage_text"])
        self.assertIn("after meals", by_name["Dolo 650 Tablet"]["dosage_text"])
        self.assertIn("Augmentin 625 Duo Tablet", by_name)
        self.assertEqual([line["text"] for line in unmatched], [])

    def test_unitless_brand_strength_picks_matching_product(self):
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """INSERT INTO medicines(
                    medicine_name, brand_name, strength, dosage_form, composition,
                    price, composition_complete, source
                ) VALUES ('Dolo 1000mg Tablet','Dolo','1000mg','Tablet',
                          'Paracetamol (1000mg)',40.0,1,'local')"""
            )
            connection.commit()
        finally:
            connection.close()

        medicines, _ = ocr_service._extract_medicines(
            "Dolo 650 Tablet - 1 Tablet twice daily x5 days\n"
        )

        self.assertEqual([item["name"] for item in medicines], ["Dolo 650 Tablet"])
        self.assertTrue(medicines[0]["strength_match"])

    def test_conflicting_printed_strength_downgrades_an_exact_name_match(self):
        medicines, _ = ocr_service._extract_medicines(
            "Tab Paracetamol 650 mg 1-0-1 after meals\n"
        )

        self.assertEqual([item["name"] for item in medicines], ["Paracetamol 500mg Tablet"])
        self.assertEqual(medicines[0]["match_type"], "possible")
        self.assertIs(medicines[0]["strength_match"], False)

    def test_letterhead_lines_are_not_candidates(self):
        self.assertTrue(ocr_service._looks_like_header("Dr. A Sharma Clinic, Delhi"))
        self.assertTrue(ocr_service._looks_like_header("Patient Name: Ramesh Verma"))
        self.assertTrue(ocr_service._looks_like_header("Address: 12 MG Road, Sector 4"))
        self.assertFalse(ocr_service._looks_like_header("Tab Dolo 650 mg 1-0-1"))
        self.assertFalse(ocr_service._looks_like_header("Vomikind MD Tablet 1-0-1"))

    def test_garbled_brand_is_returned_as_a_possible_match(self):
        medicines, unmatched = ocr_service._extract_medicines(
            "Tab Augmentn 625 mg 1-0-1 for 5 days\n"
        )

        self.assertEqual([item["name"] for item in medicines], ["Augmentin 625 Duo Tablet"])
        self.assertEqual(medicines[0]["match_type"], "possible")
        self.assertIsInstance(medicines[0]["match_score"], float)
        self.assertEqual(unmatched, [])

    def test_fuzzy_lookup_requires_printed_strength_or_form(self):
        self.assertIsNone(ocr_service._fuzzy_lookup("augmentn", None, None, None))
        self.assertIsNotNone(ocr_service._fuzzy_lookup("augmentn", None, "625", "tablet"))

    def test_fuzzy_lookup_rejects_a_tie_between_two_brands(self):
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """INSERT INTO medicines(
                    medicine_name, brand_name, strength, dosage_form, composition,
                    price, composition_complete, source
                ) VALUES ('Augmentix 625mg Tablet','Augmentix','625mg','Tablet',
                          'Amoxicillin (500mg) + Clavulanic Acid (125mg)',110.0,1,'local')"""
            )
            connection.commit()
        finally:
            connection.close()

        self.assertIsNone(
            ocr_service._fuzzy_lookup("augmentim", "625mg", "625", "tablet")
        )

    def _insert(self, name, brand, strength, form, composition):
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """INSERT INTO medicines(
                    medicine_name, brand_name, strength, dosage_form, composition,
                    price, composition_complete, source
                ) VALUES (?,?,?,?,?,25.0,1,'local')""",
                (name, brand, strength, form, composition),
            )
            connection.commit()
        finally:
            connection.close()

    def test_phone_and_clinic_hours_are_not_matched(self):
        self._insert("Tel 40mg Tablet", "Tel", "40mg", "Tablet", "Telmisartan (40mg)")
        self._insert("Mon 10mg Tablet", "Mon", "10mg", "Tablet", "Montelukast (10mg)")

        medicines, unmatched = ocr_service._extract_medicines(
            "Tel.: 2261594698\n"
            "Mon to Sat 10-1, 5-8\n"
            "WT 35.8 KG  HT 145 CM\n"
        )

        self.assertEqual(medicines, [])
        self.assertEqual(unmatched, [])

    def test_bare_brand_line_without_strength_or_form_is_matched(self):
        self._insert("Ibugesic Plus Tablet", "Ibugesic", "400mg", "Tablet",
                     "Ibuprofen (400mg) + Paracetamol (325mg)")

        medicines, _ = ocr_service._extract_medicines("Ibugesic Plus\n")

        self.assertEqual([item["name"] for item in medicines], ["Ibugesic Plus Tablet"])

    def test_doctor_name_is_not_matched_to_a_medicine_that_contains_it(self):
        self._insert("Pramodep 50mg Tablet", "Pramodep", "50mg", "Tablet", "Pramipexole (50mg)")

        medicines, _ = ocr_service._extract_medicines(
            "Dr. Pramod Balakrishnan\n"
            "Pramod\n"
        )

        self.assertEqual(medicines, [])

    def test_abbreviation_is_not_matched_inside_a_longer_brand(self):
        self._insert("Nabiclav Forte Dry Syrup", "Nabiclav", "228.5mg", "Syrup",
                     "Amoxicillin (200mg) + Clavulanic Acid (28.5mg)")

        medicines, unmatched = ocr_service._extract_medicines(
            "Cap Biclav DS 1-0-1 x 5 days\n"
        )

        self.assertEqual(medicines, [])
        self.assertEqual([line["query"] for line in unmatched], ["Biclav"])

    def test_second_printed_name_word_prefers_the_matching_product(self):
        self._insert("IBUgesic 200 Tablet", "IBUgesic", "200mg", "Tablet", "Ibuprofen (200mg)")
        self._insert("Ibugesic Plus Tablet", "Ibugesic", "400mg", "Tablet",
                     "Ibuprofen (400mg) + Paracetamol (325mg)")

        medicines, _ = ocr_service._extract_medicines(
            "Tab Ibugesic Plus 1-0-1 x 5 days\n"
        )

        self.assertEqual([item["name"] for item in medicines], ["Ibugesic Plus Tablet"])

    def test_three_letter_brand_is_read(self):
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """INSERT INTO medicines(
                    medicine_name, brand_name, strength, dosage_form, composition,
                    price, composition_complete, source
                ) VALUES ('PAN 40 Tablet','PAN','40mg','Tablet',
                          'Pantoprazole (40mg)',18.0,1,'local')"""
            )
            connection.commit()
        finally:
            connection.close()

        medicines, _ = ocr_service._extract_medicines(
            "Tab PAN 40 mg 1-0-0 before breakfast\n"
        )

        self.assertEqual([item["name"] for item in medicines], ["PAN 40 Tablet"])
        self.assertEqual(medicines[0]["match_type"], "exact")
        self.assertTrue(medicines[0]["strength_match"])

    def test_unknown_medicine_is_listed_unmatched_not_invented(self):
        medicines, unmatched = ocr_service._extract_medicines(
            "Tab Zorblax 20 mg 1-0-1 after food\n"
        )

        self.assertEqual(medicines, [])
        self.assertEqual(len(unmatched), 1)
        self.assertIn("Zorblax", unmatched[0]["text"])
        self.assertEqual(unmatched[0]["query"], "Zorblax")

    def test_unreadable_text_returns_explicit_failure(self):
        with patch.object(ocr_service, "_read_text", return_value=("   \n  ", "tesseract")):
            result = ocr_service.parse_prescription_medicines(self._dummy_upload())

        self.assertFalse(result["success"])
        self.assertEqual(result["data"], [])
        self.assertIn("No readable text", result["message"])
        self.assertEqual(result["provider"], "tesseract")

    def test_google_vision_reads_when_key_is_set(self):
        image = Image.new("RGB", (600, 200), "white")
        with patch.dict(os.environ, {"GOOGLE_VISION_API_KEY": "test-key", "GEMINI_API_KEY": ""}), patch.object(
            ocr_service, "_google_vision_text", return_value="Dolo 650 Tablet 1-0-1"
        ) as vision:
            text, provider = ocr_service._image_text(image)

        vision.assert_called_once()
        self.assertEqual(text, "Dolo 650 Tablet 1-0-1")
        self.assertEqual(provider, "google-vision")

    def test_google_vision_request_uses_key_header_and_document_text_detection(self):
        image = Image.new("RGB", (600, 200), "white")
        response = type("Response", (), {
            "status_code": 200,
            "text": "",
            "json": lambda self: {"responses": [{"fullTextAnnotation": {"text": "Dolo 650"}}]},
        })()
        with patch.dict(os.environ, {"GOOGLE_VISION_API_KEY": "secret-key"}), patch.object(
            ocr_service.requests, "post", return_value=response
        ) as post:
            text = ocr_service._google_vision_text(image)

        self.assertEqual(text, "Dolo 650")
        args, kwargs = post.call_args
        self.assertEqual(args[0], ocr_service.GOOGLE_VISION_URL)
        self.assertEqual(kwargs["headers"], {"X-Goog-Api-Key": "secret-key"})
        payload = kwargs["json"]["requests"][0]
        self.assertEqual(payload["features"][0]["type"], "DOCUMENT_TEXT_DETECTION")
        self.assertEqual(payload["imageContext"]["languageHints"], ["en"])
        self.assertTrue(payload["image"]["content"])

    def test_falls_back_to_tesseract_when_google_vision_fails(self):
        image = Image.new("RGB", (600, 200), "white")
        with patch.dict(os.environ, {"GOOGLE_VISION_API_KEY": "test-key", "GEMINI_API_KEY": ""}), patch.object(
            ocr_service, "_google_vision_text", side_effect=RuntimeError("quota")
        ), patch.object(ocr_service, "_ocr_image", return_value="Local text"):
            text, provider = ocr_service._image_text(image)

        self.assertEqual(text, "Local text")
        self.assertEqual(provider, "tesseract")

    def test_ocr_slips_in_numbers_and_units_are_corrected(self):
        self.assertEqual(ocr_service._clean_line("Rx Dolo 5OO rng"), "Dolo 500mg")
        self.assertEqual(ocr_service._clean_line("1) Pan 4O mg OD"), "Pan 40 mg OD")
        self.assertEqual(ocr_service._clean_line("Tab Dol0 650"), "Tab Dolo 650")
        # Vitamin names and strengths written without a space stay as written.
        self.assertEqual(ocr_service._clean_line("Inj B12 1ml"), "Inj B12 1ml")
        self.assertEqual(ocr_service._clean_line("Vit D3 60000 IU"), "Vit D3 60000 IU")
        self.assertEqual(ocr_service._clean_line("Tab Telma40 OD"), "Tab Telma40 OD")

    def test_misread_letter_in_brand_still_matches_exactly(self):
        medicines, _ = ocr_service._extract_medicines("Tab Dol0 650 1-0-1\n")

        self.assertEqual([item["name"] for item in medicines], ["Dolo 650 Tablet"])
        self.assertEqual(medicines[0]["match_type"], "exact")

    def test_misspelled_ingredient_offers_product_with_printed_strength(self):
        medicines, _ = ocr_service._extract_medicines("Tab Paracetmol 65O mg 1-0-1\n")

        self.assertEqual([item["name"] for item in medicines], ["Dolo 650 Tablet"])
        self.assertEqual(medicines[0]["match_type"], "possible")
        self.assertTrue(medicines[0]["strength_match"])

    def test_handwriting_typo_in_first_letters_is_matched_as_possible(self):
        medicines, _ = ocr_service._extract_medicines("T. Pantoprazol 4O mg OD\n")

        self.assertEqual([item["name"] for item in medicines], ["Pantoprazole 40 Tablet"])
        self.assertEqual(medicines[0]["match_type"], "possible")

    def test_unclear_line_lists_suggestions_instead_of_asserting(self):
        self._insert("Montair LC Tablet", "Montair", "10mg", "Tablet",
                     "Montelukast (10mg) + Levocetirizine (5mg)")
        self._insert("Montek LC Tablet", "Montek", "10mg", "Tablet",
                     "Montelukast (10mg) + Levocetirizine (5mg)")

        medicines, unmatched = ocr_service._extract_medicines("Tab Montak LC 0-0-1\n")

        self.assertEqual(medicines, [])
        self.assertEqual(len(unmatched), 1)
        names = [entry["name"] for entry in unmatched[0]["suggestions"]]
        self.assertIn("Montek LC Tablet", names)
        self.assertIn("Montair LC Tablet", names)
        self.assertTrue(all(isinstance(entry["score"], float) for entry in unmatched[0]["suggestions"]))

    def test_suggestions_degrade_when_catalog_tables_are_missing(self):
        empty = Path(self.temp_dir.name) / "empty.db"
        sqlite3.connect(empty).close()
        with patch.object(database, "MEDICINE_DATABASE", empty):
            self.assertEqual(ocr_service._catalog_vocabulary(), ([], []))

    def test_vision_word_confidence_is_attached_to_the_line(self):
        confidences = {}
        ocr_service._vision_word_confidences({"fullTextAnnotation": {"pages": [{"blocks": [{"paragraphs": [{
            "words": [
                {"confidence": 0.5, "symbols": [{"text": "D"}, {"text": "o"}, {"text": "l"}, {"text": "0"}]},
                {"confidence": 0.9, "symbols": [{"text": "6"}, {"text": "5"}, {"text": "0"}]},
            ]
        }]}]}]}}, confidences)

        self.assertEqual(confidences["dolo"], 0.5)
        medicines, _ = ocr_service._extract_medicines("Tab Dol0 650\n", confidences)
        self.assertEqual(medicines[0]["ocr_confidence"], 70.0)

    def test_preprocessing_crops_dark_background_and_upscales(self):
        photo = Image.new("RGB", (1000, 800), (40, 40, 40))
        photo.paste(Image.new("RGB", (600, 500), (245, 245, 240)), (200, 160))

        prepared = ocr_service.preprocess_prescription_image(photo)

        self.assertEqual(prepared.mode, "L")
        self.assertGreaterEqual(max(prepared.size), ocr_service.PREPROCESS_MIN_SIDE)
        ratio = prepared.width / prepared.height
        self.assertAlmostEqual(ratio, 600 / 500, delta=0.08)

    def test_preprocessing_keeps_a_plain_scan_whole(self):
        scan = Image.new("RGB", (1800, 1200), "white")
        prepared = ocr_service.preprocess_prescription_image(scan)
        self.assertEqual(prepared.size, (1800, 1200))

    def _gemini_response(self, status, body):
        return type("Response", (), {
            "status_code": status,
            "text": "",
            "json": lambda self: body,
        })()

    def test_gemini_reads_first_and_sends_key_in_header(self):
        image = Image.new("RGB", (600, 200), "white")
        body = {"candidates": [{"content": {"parts": [{"text": "Tab Dolo 650 mg 1-0-1"}]}}]}
        with patch.dict(os.environ, {"GEMINI_API_KEY": "gem-key", "GOOGLE_VISION_API_KEY": "v-key"}), \
                patch.object(ocr_service.requests, "post", return_value=self._gemini_response(200, body)) as post, \
                patch.object(ocr_service, "_google_vision_text") as vision:
            text, provider = ocr_service._image_text(image)

        self.assertEqual((text, provider), ("Tab Dolo 650 mg 1-0-1", "gemini"))
        vision.assert_not_called()
        args, kwargs = post.call_args
        self.assertIn("generativelanguage.googleapis.com", args[0])
        self.assertNotIn("gem-key", args[0])
        self.assertEqual(kwargs["headers"], {"x-goog-api-key": "gem-key"})
        parts = kwargs["json"]["contents"][0]["parts"]
        self.assertEqual(parts[0]["inline_data"]["mime_type"], "image/jpeg")
        self.assertTrue(parts[0]["inline_data"]["data"])

    def test_gemini_rate_limit_falls_back_to_local_ocr(self):
        image = Image.new("RGB", (600, 200), "white")
        limited = self._gemini_response(429, {"error": {"message": "Resource exhausted"}})
        with patch.dict(os.environ, {"GEMINI_API_KEY": "gem-key"}), \
                patch.dict(os.environ, {"GOOGLE_VISION_API_KEY": ""}), \
                patch.object(ocr_service.requests, "post", return_value=limited), \
                patch.object(ocr_service, "_ocr_image", return_value="Local text"):
            text, provider = ocr_service._image_text(image)

        self.assertEqual((text, provider), ("Local text", "tesseract"))

    def test_gemini_none_answer_means_no_text(self):
        image = Image.new("RGB", (600, 200), "white")
        body = {"candidates": [{"content": {"parts": [{"text": "NONE"}]}}]}
        with patch.dict(os.environ, {"GEMINI_API_KEY": "gem-key"}), \
                patch.object(ocr_service.requests, "post", return_value=self._gemini_response(200, body)):
            self.assertEqual(ocr_service._gemini_text(image), "")

    def test_unsupported_file_type_is_rejected(self):
        result = ocr_service.parse_prescription_medicines(self._dummy_upload("notes.txt"))

        self.assertFalse(result["success"])
        self.assertIn("Unsupported file type", result["message"])

    def test_uploaded_file_is_deleted_after_processing(self):
        text = "Tab Dolo 650 mg once daily for 3 days\n"
        with patch.object(ocr_service, "_read_text", return_value=(text, "google-vision")):
            result = ocr_service.parse_prescription_medicines(self._dummy_upload())

        self.assertTrue(result["success"])
        self.assertEqual(result["provider"], "google-vision")
        self.assertEqual(list(self.upload_dir.iterdir()), [])

    def test_no_sample_medicines_are_returned_when_ocr_is_empty(self):
        with patch.object(ocr_service, "_read_text", return_value=("", "tesseract")):
            result = ocr_service.parse_prescription_medicines(self._dummy_upload())

        self.assertFalse(result["success"])
        self.assertEqual(result["count"], 0)
        self.assertNotIn("Paracetamol 650mg", str(result))


if __name__ == "__main__":
    unittest.main()
