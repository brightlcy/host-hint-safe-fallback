from __future__ import annotations

import csv
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from simulator.alibaba_ebs_2026 import (
    AlibabaTraceFormatError,
    convert_alibaba_ebs_trace,
    infer_ebs_trace_identity,
    iter_alibaba_ebs_rows,
)


class AlibabaEBS2026Test(unittest.TestCase):
    def _write_trace(self, path: Path, rows: list[list[object]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["op", "dev_offset", "length", "start_time", "end_time"])
            writer.writerows(rows)

    def test_identity_is_inferred_from_parent_and_filename(self) -> None:
        identity = infer_ebs_trace_identity(
            "/tmp/mysql/cluster2-dev1451.csv"
        )
        self.assertEqual(identity.workload_type, "mysql")
        self.assertEqual(identity.cluster_id, 2)
        self.assertEqual(identity.volume_id, 1451)

    def test_parser_converts_operation_and_observed_latency(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "mysql" / "cluster0-dev1.csv"
            self._write_trace(path, [[1, 0, 4096, 10, 90], [2, 4096, 8192, 100, 520]])
            rows = list(iter_alibaba_ebs_rows(path))
            self.assertEqual([row.opcode for row in rows], ["R", "W"])
            self.assertEqual(rows[0].observed_latency_us, 80)
            self.assertEqual(rows[1].observed_latency_us, 420)

    def test_invalid_operation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "mysql" / "cluster0-dev1.csv"
            self._write_trace(path, [[3, 0, 4096, 10, 20]])
            with self.assertRaises(AlibabaTraceFormatError):
                list(iter_alibaba_ebs_rows(path))

    def test_end_time_before_start_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "mysql" / "cluster0-dev1.csv"
            self._write_trace(path, [[1, 0, 4096, 20, 10]])
            with self.assertRaises(AlibabaTraceFormatError):
                list(iter_alibaba_ebs_rows(path))

    def test_decreasing_start_time_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "mysql" / "cluster0-dev1.csv"
            self._write_trace(path, [[1, 0, 4096, 20, 30], [1, 4096, 4096, 19, 40]])
            with self.assertRaises(AlibabaTraceFormatError):
                list(iter_alibaba_ebs_rows(path))

    def test_conversion_writes_standard_trace_and_stats(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "mq" / "cluster3-dev451.csv"
            output = root / "converted.standard.csv"
            self._write_trace(
                source,
                [
                    [1, 0, 4096, 1_000_000, 1_000_080],
                    [2, 4096, 8192, 1_000_200, 1_000_620],
                    [1, 12288, 2048, 1_001_000, 1_001_100],
                ],
            )
            result = convert_alibaba_ebs_trace(source, output)
            with output.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[0]["timestamp"], "0.000000")
            self.assertEqual(rows[1]["op"], "W")
            self.assertEqual(rows[1]["lba"], "1")
            self.assertEqual(rows[1]["size"], "2")
            self.assertEqual(result.stats.read_requests, 2)
            self.assertEqual(result.stats.write_requests, 1)
            self.assertEqual(result.stats.unaligned_requests, 1)
            self.assertAlmostEqual(result.stats.observed_latency_avg_us, 200.0)
            with result.stats_path.open("r", encoding="utf-8") as handle:
                manifest = json.load(handle)
            self.assertEqual(manifest["source_format"], "alibaba-cloud-ebs-io-traces-2026")
            self.assertEqual(manifest["trace_identity"]["volume_id"], 451)

    def test_max_rows_and_skip_rows_are_respected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "k8s" / "cluster0-dev2479.csv"
            output = root / "converted.standard.csv"
            self._write_trace(
                source,
                [
                    [1, 0, 4096, 10, 20],
                    [1, 4096, 4096, 20, 30],
                    [2, 8192, 4096, 30, 40],
                    [2, 12288, 4096, 40, 50],
                ],
            )
            result = convert_alibaba_ebs_trace(
                source, output, skip_rows=1, max_rows=2
            )
            self.assertEqual(result.stats.selected_rows, 2)
            self.assertEqual(result.stats.skipped_rows, 1)
            self.assertEqual(result.stats.stop_reason, "max_rows")

    def test_zip_member_can_be_converted_without_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            csv_path = root / "cluster0-dev22.csv"
            self._write_trace(csv_path, [[2, 0, 4096, 10, 230]])
            archive_path = root / "io_record_part0.zip"
            member = "mysql/cluster0-dev22.csv"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.write(csv_path, arcname=member)
            output = root / "converted.standard.csv"
            result = convert_alibaba_ebs_trace(
                archive_path,
                output,
                member_name=member,
            )
            self.assertEqual(result.stats.workload_type, "mysql")
            self.assertEqual(result.stats.volume_id, 22)
            self.assertEqual(result.stats.write_pages, 1)


if __name__ == "__main__":
    unittest.main()
