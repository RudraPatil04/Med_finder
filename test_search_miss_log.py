"""Offline tests for the zero-result search log and the coverage checker."""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import database
from backend.services import search_miss_log
from etl import check_coverage


class SearchMissLogTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "miss.db"
        sqlite3.connect(self.db_path).close()
        self.patch = patch.object(database, "MEDICINE_DATABASE", self.db_path)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temp_dir.cleanup()

    def test_counts_repeated_misses_once_per_normalized_query(self):
        search_miss_log.record_miss("Foo-Bar 500")
        search_miss_log.record_miss("foobar500")
        rows = search_miss_log.list_misses()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["miss_count"], 2)

    def test_ignores_tiny_queries_and_forgets(self):
        self.assertFalse(search_miss_log.record_miss("a"))
        search_miss_log.record_miss("Zincovit")
        search_miss_log.forget_miss("zincovit")
        self.assertEqual(search_miss_log.list_misses(), [])

    def test_logging_never_raises(self):
        with patch.object(database, "MEDICINE_DATABASE", Path(self.temp_dir.name) / "no" / "dir.db"):
            self.assertFalse(search_miss_log.record_miss("Zincovit"))

    def test_read_names_skips_comments_and_duplicates(self):
        path = Path(self.temp_dir.name) / "names.txt"
        path.write_text("# header\nDolo 650\ndolo 650\nCalpol  # brand\n\n", encoding="utf-8")
        self.assertEqual(check_coverage.read_names(path), ["Dolo 650", "Calpol"])

    def test_check_splits_found_and_missing(self):
        with patch.object(check_coverage, "is_found", side_effect=lambda n: n == "Dolo"):
            self.assertEqual(check_coverage.check(["Dolo", "Nope"]), (["Dolo"], ["Nope"]))


if __name__ == "__main__":
    unittest.main()
