import csv
import tempfile
import unittest
from pathlib import Path

from simulator.trace_io import GC_LOG_FIELDS, save_gc_log, save_summary


class ResultIOTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_save_summary_writes_single_row_with_stable_columns(self) -> None:
        summary = {
            "host_write_pages": 13,
            "nand_write_pages": 16,
            "write_amplification_factor": 16 / 13,
        }

        path = save_summary(self.root / "result", summary)

        with path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(path.name, "baseline_summary.csv")
        self.assertEqual(len(rows), 1)
        self.assertEqual(list(rows[0]), list(summary))
        self.assertEqual(rows[0]["host_write_pages"], "13")

    def test_save_summary_rejects_empty_mapping(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            save_summary(self.root, {})

    def test_save_gc_log_creates_header_only_file_when_no_gc_occurs(self) -> None:
        path = save_gc_log(self.root, [])

        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
            fieldnames = reader.fieldnames
        self.assertEqual(rows, [])
        self.assertEqual(tuple(fieldnames or ()), GC_LOG_FIELDS)

    def test_save_gc_log_writes_completed_cycle(self) -> None:
        row = {
            "gc_id": 1,
            "victim_block": 3,
            "valid_pages_before": 2,
            "invalid_pages_before": 2,
            "free_pages_before": 4,
            "migrated_pages": 2,
            "free_pages_after": 6,
            "victim_erase_count": 1,
            "net_reclaimed_pages": 2,
        }

        path = save_gc_log(self.root, [row])

        with path.open("r", encoding="utf-8", newline="") as handle:
            saved = list(csv.DictReader(handle))
        self.assertEqual(saved[0]["victim_block"], "3")
        self.assertEqual(saved[0]["net_reclaimed_pages"], "2")

    def test_save_gc_log_rejects_schema_mismatch(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid GC log row"):
            save_gc_log(self.root, [{"gc_id": 1}])


if __name__ == "__main__":
    unittest.main()
