from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from simulator.run_convert_alibaba_ebs import main as convert_main
from simulator.run_select_alibaba_ebs import main as select_main


class AlibabaEBSCLITest(unittest.TestCase):
    def test_select_cli_creates_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            metadata = root / "metadata.csv"
            with metadata.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["volume_id", "storage_cluster_name", "ecstag_list", "size(MB)"])
                writer.writerows(
                    [
                        [1, 0, "mysql", 100], [2, 1, "mysql", 110],
                        [3, 0, "mq", 100], [4, 1, "mq", 110],
                        [5, 0, "k8s", 100], [6, 1, "k8s", 110],
                    ]
                )
            code = select_main([
                "--metadata", str(metadata),
                "--output-dir", str(root / "out"),
            ])
            self.assertEqual(code, 0)
            self.assertTrue((root / "out" / "pilot_candidates.csv").exists())

    def test_convert_cli_creates_standard_trace(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "mysql" / "cluster0-dev1.csv"
            source.parent.mkdir(parents=True)
            with source.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["op", "dev_offset", "length", "start_time", "end_time"])
                writer.writerow([2, 0, 4096, 10, 230])
            output = root / "out.standard.csv"
            code = convert_main([
                "--input", str(source),
                "--output", str(output),
            ])
            self.assertEqual(code, 0)
            self.assertTrue(output.exists())


if __name__ == "__main__":
    unittest.main()
