"""Offline tests for the resumable composition backfill.

The catalog lookups are replaced, so no network is used. The database is a
temporary file with only the columns the script reads.
"""

import io
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from backend import database
from etl import backfill_compositions as backfill


class BackfillCompositionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_medfinder.db"
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """CREATE TABLE medicines (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    medicine_name TEXT NOT NULL,
                    brand_name TEXT,
                    strength TEXT,
                    dosage_form TEXT,
                    composition TEXT,
                    composition_complete INTEGER NOT NULL DEFAULT 0
                )"""
            )
            connection.executemany(
                "INSERT INTO medicines(medicine_name, brand_name, composition_complete) VALUES (?,?,?)",
                [
                    ("Alpha 10mg Tablet", "Alpha", 0),
                    ("Beta 20mg Tablet", "Beta", 0),
                    ("Gamma 30mg Tablet", "Gamma", 0),
                    ("Delta 40mg Tablet", "Delta", 0),
                    ("NoBrand 50mg Tablet", "", 0),
                    ("Full 60mg Tablet", "Full", 1),
                ],
            )
            connection.commit()
        finally:
            connection.close()
        self.db_patch = patch.object(database, "MEDICINE_DATABASE", self.db_path)
        self.db_patch.start()
        self.sleep_patch = patch.object(backfill.time, "sleep", return_value=None)
        self.sleep_patch.start()

    def tearDown(self):
        self.sleep_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def run_main(self, argv):
        output = io.StringIO()
        with redirect_stdout(output):
            code = backfill.main(argv)
        return code, output.getvalue()

    def seed_cursor(self, value):
        backfill.ensure_state_table()
        backfill.write_cursor(value)

    def test_pool_excludes_rows_without_a_brand(self):
        self.assertEqual(backfill.count_pool(), 4)
        self.assertEqual(backfill.count_without_brand(), 1)
        self.assertEqual(backfill.count_remaining(0), 4)

    def test_fills_and_resumes_where_it_stopped(self):
        filled_ids = []

        def fake_enrich(row, use_cache=True, raise_errors=False):
            filled_ids.append(row["id"])
            return row["id"] in (2, 3)

        with patch.object(backfill, "enrich_medicine_composition", side_effect=fake_enrich):
            code, output = self.run_main(["--limit", "2", "--delay", "0.5"])

        self.assertEqual(code, 0)
        self.assertEqual(filled_ids, [1, 2])
        self.assertEqual(backfill.read_cursor(), 2)
        self.assertIn("filled : 1", output)
        self.assertIn("empty  : 1", output)

        with patch.object(backfill, "enrich_medicine_composition", side_effect=fake_enrich):
            self.run_main(["--delay", "0.5"])

        self.assertEqual(filled_ids, [1, 2, 3, 4])
        self.assertEqual(backfill.read_cursor(), 4)
        self.assertEqual(backfill.count_remaining(4), 0)

    def test_dry_run_writes_nothing_and_keeps_the_cursor(self):
        seen = []

        def fake_find(row):
            seen.append(row["id"])
            return {"composition": "Alpha (10mg)"}

        with patch.object(backfill, "find_composition_record", side_effect=fake_find), \
                patch.object(backfill, "enrich_medicine_composition") as writer:
            code, output = self.run_main(["--sample", "3", "--dry-run", "--delay", "0.5"])

        writer.assert_not_called()
        self.assertEqual(code, 0)
        self.assertEqual(len(seen), 3)
        self.assertEqual(backfill.read_cursor(), 0)
        self.assertIn("filled : 3", output)

    def test_sample_is_reproducible_and_skips_completed_rows(self):
        first = backfill.sample_ids(3, seed=7)
        second = backfill.sample_ids(3, seed=7)
        self.assertEqual(first, second)
        self.assertEqual(len(set(first)), 3)
        self.assertNotIn(5, first)
        self.assertNotIn(6, first)

    def test_status_reports_pool_and_cursor(self):
        self.seed_cursor(2)
        code, output = self.run_main(["--status"])
        self.assertEqual(code, 0)
        self.assertIn("with a brand to look up    : 4", output)
        self.assertIn("without a brand (skipped)  : 1", output)
        self.assertIn("Remaining after the cursor      : 2", output)

    def test_reset_clears_the_cursor(self):
        self.seed_cursor(4)
        with patch.object(backfill, "enrich_medicine_composition", return_value=False):
            _, output = self.run_main(["--reset", "--limit", "1", "--delay", "0.5"])

        self.assertIn("Cursor cleared.", output)
        self.assertEqual(backfill.read_cursor(), 1)

    def test_a_run_of_errors_stops_and_does_not_advance(self):
        def boom(row, use_cache=True, raise_errors=False):
            raise ConnectionError("catalog unreachable")

        with patch.object(backfill, "enrich_medicine_composition", side_effect=boom), \
                patch.object(backfill, "MAX_CONSECUTIVE_ERRORS", 2):
            code, output = self.run_main(["--delay", "0.5"])

        self.assertEqual(code, 1)
        self.assertIn("consecutive lookups failed", output)
        self.assertEqual(backfill.read_cursor(), 0)


if __name__ == "__main__":
    unittest.main()
