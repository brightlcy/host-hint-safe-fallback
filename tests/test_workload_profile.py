from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from simulator.workload_profile import WorkloadProfileConfig, profile_standard_trace


class WorkloadProfileTest(unittest.TestCase):
    def _write_trace(self, root: Path) -> Path:
        path = root / "trace.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["timestamp", "flow_id", "op", "lba", "size"])
            writer.writerow([0.0, "f0", "W", 0, 2])
            writer.writerow([0.1, "f0", "W", 2, 2])
            writer.writerow([0.2, "f0", "R", 4, 1])
            writer.writerow([1.1, "f0", "W", 0, 1])
            writer.writerow([1.2, "f0", "W", 10, 1])
        return path

    def test_config_validation(self) -> None:
        with self.assertRaises(ValueError):
            WorkloadProfileConfig(window_seconds=0)
        with self.assertRaises(ValueError):
            WorkloadProfileConfig(burst_percentile=0)
        with self.assertRaises(ValueError):
            WorkloadProfileConfig(max_tracked_write_pages=0)

    def test_profile_outputs_and_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            trace = self._write_trace(root)
            result = profile_standard_trace(
                trace,
                root / "out",
                window_seconds=1.0,
                burst_percentile=95.0,
                top_hot_lpn_count=2,
            )

            self.assertEqual(result.summary["total_requests"], 5)
            self.assertEqual(result.summary["write_requests"], 4)
            self.assertEqual(result.summary["write_pages"], 6)
            self.assertEqual(result.summary["repeated_write_pages"], 1)
            self.assertAlmostEqual(result.summary["overwrite_page_ratio"], 1 / 6)
            self.assertEqual(result.summary["burst_request_threshold_per_window"], 3.0)
            self.assertEqual(result.summary["burst_window_count"], 1)
            self.assertAlmostEqual(result.summary["sequential_transition_ratio"], 1 / 4)
            self.assertEqual(result.hot_lpns[0].lpn, 0)
            self.assertEqual(result.hot_lpns[0].write_count, 2)

            for path in (
                result.summary_csv_path,
                result.summary_json_path,
                result.window_stats_path,
                result.burst_intervals_path,
                result.hot_lpn_path,
                result.manifest_path,
            ):
                self.assertTrue(path.exists(), path)

            with result.summary_json_path.open("r", encoding="utf-8") as handle:
                saved = json.load(handle)
            self.assertEqual(saved["total_requests"], 5)

    def test_hotness_budget_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            trace = self._write_trace(root)
            result = profile_standard_trace(
                trace,
                root / "out",
                max_tracked_write_pages=1,
            )
            self.assertFalse(result.summary["hotness_tracking_complete"])
            self.assertEqual(result.summary["overwrite_page_ratio"], -1.0)
            self.assertEqual(result.hot_lpns, ())


if __name__ == "__main__":
    unittest.main()
