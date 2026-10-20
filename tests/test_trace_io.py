import csv
import json
import tempfile
import unittest
from pathlib import Path

from simulator.trace_io import (
    TraceFormatError,
    iter_standard_trace,
    resolve_ssd_config,
    save_resolved_config,
    scan_trace_stats,
)


class TraceIOTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_csv(self, name, header, rows):
        path = self.root / name
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            writer.writerows(rows)
        return path

    def test_iter_standard_trace_converts_types(self) -> None:
        path = self.write_csv(
            "valid.csv",
            ["timestamp", "flow_id", "op", "lba", "size"],
            [
                ["0.25", "vm-01", "w", "100", "3"],
                ["1.50", "vm-02", "R", "7", "1"],
            ],
        )

        rows = list(iter_standard_trace(path))

        self.assertEqual(
            rows[0],
            {
                "timestamp": 0.25,
                "flow_id": "vm-01",
                "op": "W",
                "lba": 100,
                "size": 3,
            },
        )
        self.assertEqual(rows[1]["op"], "R")
        self.assertIsInstance(rows[1]["lba"], int)

    def test_scan_trace_stats_uses_end_lpn_exclusive(self) -> None:
        path = self.write_csv(
            "stats.csv",
            ["timestamp", "flow_id", "op", "lba", "size"],
            [
                [0.0, "a", "W", 100, 4],   # LPN 100..103
                [2.0, "b", "R", 5, 2],     # LPN 5..6
                [3.5, "a", "W", 200, 3],   # LPN 200..202
            ],
        )

        stats = scan_trace_stats(path)

        self.assertEqual(stats.total_requests, 3)
        self.assertEqual(stats.write_requests, 2)
        self.assertEqual(stats.read_requests, 1)
        self.assertEqual(stats.write_pages, 7)
        self.assertEqual(stats.read_pages, 2)
        self.assertEqual(stats.min_lpn, 5)
        self.assertEqual(stats.max_lpn, 202)
        self.assertEqual(stats.logical_pages, 203)
        self.assertEqual(stats.write_working_set_pages, 7)
        self.assertEqual(stats.touched_working_set_pages, 9)
        self.assertEqual(stats.flow_ids, ("a", "b"))
        self.assertEqual(stats.duration, 3.5)

    def test_missing_header_column_raises(self) -> None:
        path = self.write_csv(
            "bad_header.csv",
            ["timestamp", "flow_id", "op", "lba"],
            [[0.0, "a", "W", 0]],
        )

        with self.assertRaisesRegex(TraceFormatError, "missing required columns"):
            list(iter_standard_trace(path))

    def test_invalid_row_reports_csv_row_number(self) -> None:
        path = self.write_csv(
            "bad_row.csv",
            ["timestamp", "flow_id", "op", "lba", "size"],
            [[0.0, "a", "W", 10, 0]],
        )

        with self.assertRaisesRegex(TraceFormatError, "row 2"):
            list(iter_standard_trace(path))

    def test_empty_trace_raises(self) -> None:
        path = self.write_csv(
            "empty.csv",
            ["timestamp", "flow_id", "op", "lba", "size"],
            [],
        )

        with self.assertRaisesRegex(TraceFormatError, "no data rows"):
            scan_trace_stats(path)

    def test_resolve_and_save_config(self) -> None:
        path = self.write_csv(
            "sizing.csv",
            ["timestamp", "flow_id", "op", "lba", "size"],
            [[0.0, "a", "W", 9999, 1]],
        )

        stats, config = resolve_ssd_config(
            path,
            chips=4,
            pages_per_block=64,
            overprovision_ratio=0.20,
            reserved_gc_blocks=2,
        )
        output_path = save_resolved_config(
            self.root / "result",
            config,
            trace_stats=stats,
            source_trace=path,
        )

        self.assertEqual(stats.logical_pages, 10000)
        self.assertEqual(stats.write_working_set_pages, 1)
        self.assertEqual(config.logical_pages, 1)
        self.assertEqual(config.capacity_basis, "write-working-set")
        self.assertGreaterEqual(config.total_pages, 129)
        self.assertTrue(output_path.exists())

        payload = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["trace_stats"]["write_pages"], 1)
        self.assertEqual(payload["trace_stats"]["write_working_set_pages"], 1)
        self.assertEqual(payload["ssd_config"]["logical_pages"], 1)
        self.assertEqual(
            payload["ssd_config"]["capacity_basis"], "write-working-set"
        )
        self.assertEqual(payload["source_trace"], str(path.resolve()))

    def test_working_set_counts_overlapping_intervals_once(self) -> None:
        path = self.write_csv(
            "overlap.csv",
            ["timestamp", "flow_id", "op", "lba", "size"],
            [
                [0.0, "a", "W", 1000, 4],  # 1000..1003
                [1.0, "a", "W", 1002, 4],  # 1002..1005
                [2.0, "a", "R", 1005, 3],  # 1005..1007
            ],
        )

        stats = scan_trace_stats(path)

        self.assertEqual(stats.write_pages, 8)
        self.assertEqual(stats.write_working_set_pages, 6)
        self.assertEqual(stats.touched_working_set_pages, 8)
        self.assertEqual(stats.logical_pages, 1008)

    def test_address_span_sizing_remains_available(self) -> None:
        path = self.write_csv(
            "sparse.csv",
            ["timestamp", "flow_id", "op", "lba", "size"],
            [[0.0, "a", "W", 9999, 1]],
        )

        _, config = resolve_ssd_config(path, capacity_basis="address-span")

        self.assertEqual(config.logical_pages, 10000)
        self.assertEqual(config.capacity_basis, "address-span")


if __name__ == "__main__":
    unittest.main()
