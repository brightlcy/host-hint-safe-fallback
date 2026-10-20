import csv
import json
import tempfile
import unittest
from pathlib import Path

from simulator.alibaba_workload import (
    analyze_alibaba_workloads,
    select_representative_candidates,
)


class AlibabaWorkloadAnalysisTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def make_trace(self) -> Path:
        path = self.root / "io_traces.csv"
        rows = ["device_id,opcode,offset,length,timestamp"]
        # Device 0: write intensive, short inter-arrivals, mostly random.
        rows.extend([
            "0,W,0,4096,0",
            "0,W,8192,4096,100",
            "0,W,16384,4096,200",
            "0,R,24576,4096,300",
        ])
        # Device 1: read intensive and sequential.
        rows.extend([
            "1,R,0,4096,0",
            "1,R,4096,4096,5000",
            "1,R,8192,4096,10000",
            "1,W,12288,4096,15000",
        ])
        # Device 2: mixed.
        rows.extend([
            "2,R,0,8192,0",
            "2,W,8192,8192,2000",
            "2,R,16384,8192,4000",
            "2,W,24576,8192,6000",
        ])
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")
        return path

    def test_analysis_writes_all_reproducibility_artifacts(self):
        result = analyze_alibaba_workloads(
            self.make_trace(),
            self.root / "analysis",
            minimum_candidate_requests=1,
        )
        self.assertEqual(result.source_rows, 12)
        self.assertEqual(result.analyzed_devices, 3)
        self.assertTrue(result.device_summary_path.exists())
        self.assertTrue(result.candidate_path.exists())
        self.assertTrue(result.dataset_summary_path.exists())

        with result.device_summary_path.open(newline="", encoding="utf-8") as handle:
            rows = {int(row["device_id"]): row for row in csv.DictReader(handle)}
        self.assertEqual(rows[0]["write_pattern"], "write_intensive")
        self.assertEqual(rows[1]["write_pattern"], "read_intensive")
        self.assertEqual(rows[2]["write_pattern"], "mixed")
        self.assertGreater(float(rows[0]["short_interarrival_ratio"]), 0.9)
        self.assertGreater(float(rows[1]["sequential_request_ratio"]), 0.9)

        payload = json.loads(result.dataset_summary_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["totals"]["requests"], 12)
        self.assertIn("bounded per-device slices", payload["methodological_note"])

    def test_candidate_roles_are_deterministic_and_unique_when_possible(self):
        result = analyze_alibaba_workloads(
            self.make_trace(),
            self.root / "analysis",
            minimum_candidate_requests=1,
        )
        ids = [item.device_id for item in result.candidates]
        self.assertEqual(len(ids), len(set(ids)))
        roles = [item.candidate_role for item in result.candidates]
        self.assertEqual(roles[:3], ["write_intensive", "read_intensive", "mixed"])


    def test_max_duration_seconds_stops_timestamp_ordered_stream(self):
        path = self.root / "duration.csv"
        path.write_text(
            "device_id,opcode,offset,length,timestamp\n"
            "0,W,0,4096,0\n"
            "1,R,0,4096,500000\n"
            "0,W,4096,4096,1000000\n",
            encoding="utf-8",
        )
        result = analyze_alibaba_workloads(
            path,
            self.root / "duration_analysis",
            max_duration_seconds=1.0,
            minimum_candidate_requests=1,
        )
        self.assertEqual(result.source_rows, 2)
        self.assertEqual(result.stop_reason, "max_duration_seconds")
        self.assertAlmostEqual(result.analyzed_duration_s, 0.5)

    def test_max_source_rows_supports_fast_smoke_scan(self):
        result = analyze_alibaba_workloads(
            self.make_trace(),
            self.root / "analysis",
            max_source_rows=2,
            minimum_candidate_requests=1,
        )
        self.assertEqual(result.source_rows, 2)
        self.assertEqual(result.analyzed_devices, 1)


if __name__ == "__main__":
    unittest.main()
