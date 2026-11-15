import os
import tempfile
import unittest

from etl import import_jan_aushadhi as importer


CSV_TEXT = (
    '﻿"Sr No","Drug Code","Generic Name","Unit Size","MRP","Group Name"\n'
    '"1","1","Aceclofenac Tablets IP 100 mg","10\'s","8.25","Analgesic"\n'
    '"2","9071","Sanitary Napkins Regular size","Pack of 10 Sanitary Napkins","0","Consumables"\n'
    '\n'
)


class JanAushadhiCsvTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".csv")
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as out:
            out.write(CSV_TEXT)

    def tearDown(self):
        os.remove(self.path)

    def test_csv_rows_skip_header_and_blank_lines(self):
        rows = list(importer.iter_table_rows(self.path))
        self.assertEqual([page for page, _ in rows], [1, 1])
        self.assertEqual(rows[0][1][2], "Aceclofenac Tablets IP 100 mg")

    def test_csv_row_maps_like_a_pdf_row(self):
        _, row = next(importer.iter_table_rows(self.path))
        record, reason = importer.row_to_record(row)
        self.assertIsNone(reason)
        self.assertEqual(record["drug_code"], "1")
        self.assertEqual(record["price_value"], 8.25)
        self.assertEqual(record["pack_quantity"], 10)

    def test_zero_mrp_row_is_skipped(self):
        rows = list(importer.iter_table_rows(self.path))
        record, reason = importer.row_to_record(rows[1][1])
        self.assertIsNone(record)

    def test_cursor_past_page_one_reads_nothing(self):
        self.assertEqual(list(importer.iter_table_rows(self.path, start_page=2)), [])

    def test_reset_without_file_exits_cleanly(self):
        original = importer.reset_cursor
        importer.reset_cursor = lambda: None
        try:
            self.assertEqual(importer.main(["--reset"]), 0)
        finally:
            importer.reset_cursor = original


if __name__ == "__main__":
    unittest.main()
