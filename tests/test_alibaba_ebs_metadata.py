from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from simulator.alibaba_ebs_metadata import (
    load_alibaba_ebs_metadata,
    select_pilot_candidates,
    write_pilot_plan,
)


class AlibabaEBSMetadataTest(unittest.TestCase):
    def _write_metadata(self, path: Path) -> None:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                ["volume_id", "storage_cluster_name", "ecstag_list", "size(MB)"]
            )
            writer.writerows(
                [
                    [10, 0, "mysql", 100],
                    [11, 1, "mysql", 112],
                    [12, 0, "mysql", 101],
                    [20, 0, "mq", 98],
                    [21, 1, "mq", 101],
                    [22, 2, "mq", 106],
                    [30, 0, "k8s", 100],
                    [31, 1, "k8s", 99],
                    [32, 3, "k8s", 105],
                ]
            )

    def test_load_metadata_and_expected_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "metedata.csv"
            self._write_metadata(path)
            rows = load_alibaba_ebs_metadata(path)
            self.assertEqual(len(rows), 9)
            self.assertEqual(rows[0].expected_relative_path, "mysql/cluster0-dev10.csv")

    def test_selection_prefers_target_size_and_cluster_diversity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "metedata.csv"
            self._write_metadata(path)
            rows = load_alibaba_ebs_metadata(path)
            selected = select_pilot_candidates(rows)
            self.assertEqual(len(selected), 6)
            mysql = [item for item in selected if item.workload_type == "mysql"]
            self.assertEqual([item.volume_id for item in mysql], [10, 11])
            self.assertEqual(len({item.cluster_id for item in mysql}), 2)
            self.assertTrue(
                all(
                    item.selection_purpose == "acquisition_pilot_not_final_representative"
                    for item in selected
                )
            )

    def test_write_plan_creates_summary_candidates_and_guardrail(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "metedata.csv"
            self._write_metadata(path)
            result = write_pilot_plan(path, root / "out")
            self.assertTrue(result.candidate_path.exists())
            self.assertTrue(result.summary_path.exists())
            self.assertTrue(result.manifest_path.exists())
            with result.manifest_path.open("r", encoding="utf-8") as handle:
                manifest = json.load(handle)
            self.assertEqual(manifest["metadata_row_count"], 9)
            self.assertIn("must not be called representative", manifest["methodological_guardrail"])

    def test_missing_workload_range_fails_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "metedata.csv"
            self._write_metadata(path)
            rows = load_alibaba_ebs_metadata(path)
            with self.assertRaises(ValueError):
                select_pilot_candidates(rows, workloads=["redis"], per_workload=2)


if __name__ == "__main__":
    unittest.main()
