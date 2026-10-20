import csv
import json
import tempfile
import unittest
from pathlib import Path

from simulator.alibaba_streaming import (
    extract_alibaba_devices,
    load_candidate_device_ids,
)


class AlibabaStreamingExtractTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def make_trace(self):
        path = self.root / "io.csv"
        path.write_text(
            "device_id,opcode,offset,length,timestamp\n"
            "1,W,0,4096,0\n"
            "2,R,4096,4096,100\n"
            "1,W,8192,8192,200\n"
            "3,W,0,4096,300\n"
            "2,W,12288,4096,400\n",
            encoding="utf-8",
        )
        return path

    def test_extracts_multiple_devices_in_one_pass(self):
        result = extract_alibaba_devices(
            self.make_trace(),
            self.root / "out",
            device_ids=[1, 2],
        )
        self.assertEqual(result.source_rows, 5)
        self.assertEqual(len(result.devices), 2)
        with (self.root / "out/device_1.standard.csv").open(
            newline="", encoding="utf-8"
        ) as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["timestamp"], "0.000000")
        self.assertEqual(rows[1]["lba"], "2")
        self.assertEqual(rows[1]["size"], "2")

        payload = json.loads(result.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["devices"][0]["device_id"], 1)
        self.assertIn("independent logical-page namespace", payload["methodological_note"])

    def test_per_device_row_budget_stops_when_all_devices_finish(self):
        result = extract_alibaba_devices(
            self.make_trace(),
            self.root / "budget",
            device_ids=[1, 2],
            max_rows_per_device=1,
        )
        self.assertEqual(result.stop_reason, "all_devices_reached_budget")
        self.assertEqual([item.selected_rows for item in result.devices], [1, 1])

    def test_candidate_csv_loader_is_deduplicated(self):
        path = self.root / "candidates.csv"
        path.write_text(
            "candidate_role,device_id\nwrite_intensive,7\nmixed,8\nhigh_burst,7\n",
            encoding="utf-8",
        )
        self.assertEqual(load_candidate_device_ids(path), (7, 8))


if __name__ == "__main__":
    unittest.main()
