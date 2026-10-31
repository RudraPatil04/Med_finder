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
        with patch.dict(os.environ, {"GOOGLE_VISION_API_KEY": "test-key"}), patch.object(
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
        self.assertTrue(payload["image"]["content"])

    def test_falls_back_to_tesseract_when_google_vision_fails(self):
        image = Image.new("RGB", (600, 200), "white")
        with patch.dict(os.environ, {"GOOGLE_VISION_API_KEY": "test-key"}), patch.object(
            ocr_service, "_google_vision_text", side_effect=RuntimeError("quota")
        ), patch.object(ocr_service, "_ocr_image", return_value="Local text"):
            text, provider = ocr_service._image_text(image)

        self.assertEqual(text, "Local text")
        self.assertEqual(provider, "tesseract")

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
