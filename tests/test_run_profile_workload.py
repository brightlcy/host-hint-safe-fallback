from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from simulator.run_profile_workload import main


class RunProfileWorkloadCLITest(unittest.TestCase):
    def test_cli_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            trace = root / "trace.csv"
            with trace.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["timestamp", "flow_id", "op", "lba", "size"])
                writer.writerow([0.0, "f0", "W", 0, 1])
                writer.writerow([0.1, "f0", "R", 1, 1])

            exit_code = main([
                "--input", str(trace),
                "--output-dir", str(root / "out"),
                "--window-seconds", "0.1",
            ])
            self.assertEqual(exit_code, 0)
            self.assertTrue((root / "out" / "workload_summary.csv").exists())


if __name__ == "__main__":
    unittest.main()
