import csv
import json
import io
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from simulator.alibaba_trace import (
    AlibabaTraceFormatError,
    convert_alibaba_device_trace,
    iter_alibaba_io_rows,
    load_alibaba_device_capacities,
)


class AlibabaTraceTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def write(self, name, text):
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        return path


    def test_iter_accepts_decompressed_stdin(self):
        content = (
            "device_id,opcode,offset,length,timestamp\n"
            "9,W,0,4096,100\n"
        )
        with mock.patch("sys.stdin", io.StringIO(content)):
            rows = list(iter_alibaba_io_rows("-"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].device_id, 9)

    def test_iter_accepts_published_header(self):
        path = self.write(
            "io.csv",
            "device_id,opcode,offset,length,timestamp\n"
            "7,R,4096,8192,1000000\n",
        )
        rows = list(iter_alibaba_io_rows(path))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].device_id, 7)
        self.assertEqual(rows[0].opcode, "R")
        self.assertEqual(rows[0].offset_bytes, 4096)
        self.assertEqual(rows[0].length_bytes, 8192)
        self.assertEqual(rows[0].timestamp_us, 1000000)

    def test_iter_accepts_headerless_published_order(self):
        path = self.write("io.csv", "3,W,0,4096,1577808000000626\n")
        rows = list(iter_alibaba_io_rows(path))
        self.assertEqual(rows[0].row_number, 1)
        self.assertEqual(rows[0].device_id, 3)

    def test_conversion_filters_device_and_normalizes_time(self):
        path = self.write(
            "io.csv",
            "device_id,opcode,offset,length,timestamp\n"
            "1,R,0,4096,2000000\n"
            "2,W,4096,8192,3000000\n"
            "2,R,12288,4096,3500000\n",
        )
        output = self.root / "standard.csv"
        result = convert_alibaba_device_trace(path, output, device_id=2)

        with output.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0], {
            "timestamp": "0.000000",
            "flow_id": "alibaba_device_2",
            "op": "W",
            "lba": "1",
            "size": "2",
        })
        self.assertEqual(rows[1]["timestamp"], "0.500000")
        self.assertEqual(result.stats.skipped_other_devices, 1)
        self.assertEqual(result.stats.write_pages, 2)
        self.assertEqual(result.stats.read_pages, 1)

    def test_unaligned_byte_range_conservatively_covers_pages(self):
        path = self.write(
            "io.csv",
            "device_id,opcode,offset,length,timestamp\n"
            "0,W,4095,2,10\n",
        )
        output = self.root / "standard.csv"
        result = convert_alibaba_device_trace(path, output, device_id=0)
        with output.open(newline="", encoding="utf-8") as handle:
            row = next(csv.DictReader(handle))
        self.assertEqual(row["lba"], "0")
        self.assertEqual(row["size"], "2")
        self.assertEqual(result.stats.unaligned_requests, 1)

    def test_time_window_and_max_rows_are_streaming_filters(self):
        path = self.write(
            "io.csv",
            "device_id,opcode,offset,length,timestamp\n"
            "0,R,0,4096,10\n"
            "0,R,4096,4096,20\n"
            "0,W,8192,4096,30\n"
            "0,W,12288,4096,40\n",
        )
        output = self.root / "standard.csv"
        result = convert_alibaba_device_trace(
            path,
            output,
            device_id=0,
            start_timestamp_us=20,
            end_timestamp_us=50,
            max_rows=2,
        )
        self.assertEqual(result.stats.selected_rows, 2)
        self.assertEqual(result.stats.skipped_before_window, 1)
        with output.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual([row["lba"] for row in rows], ["1", "2"])

    def test_device_size_capacity_is_checked(self):
        io_path = self.write(
            "io.csv",
            "device_id,opcode,offset,length,timestamp\n"
            "0,W,4096,4096,10\n",
        )
        size_path = self.write(
            "device_size.csv",
            "device_id,capacity\n0,4096\n",
        )
        with self.assertRaisesRegex(AlibabaTraceFormatError, "exceeds device"):
            convert_alibaba_device_trace(
                io_path,
                self.root / "out.csv",
                device_id=0,
                device_size_path=size_path,
            )

    def test_device_size_loader_accepts_headerless_file(self):
        path = self.write("device_size.csv", "0,4096\n1,8192\n")
        self.assertEqual(load_alibaba_device_capacities(path), {0: 4096, 1: 8192})

    def test_invalid_opcode_reports_source_row(self):
        path = self.write(
            "io.csv",
            "device_id,opcode,offset,length,timestamp\n0,X,0,4096,10\n",
        )
        with self.assertRaisesRegex(AlibabaTraceFormatError, "row 2"):
            list(iter_alibaba_io_rows(path))

    def test_empty_selection_removes_partial_output(self):
        path = self.write(
            "io.csv",
            "device_id,opcode,offset,length,timestamp\n0,R,0,4096,10\n",
        )
        output = self.root / "standard.csv"
        with self.assertRaisesRegex(AlibabaTraceFormatError, "no rows selected"):
            convert_alibaba_device_trace(path, output, device_id=999)
        self.assertFalse(output.exists())

    def test_conversion_stats_json_is_reproducible_artifact(self):
        path = self.write(
            "io.csv",
            "device_id,opcode,offset,length,timestamp\n0,R,0,4096,10\n",
        )
        output = self.root / "standard.csv"
        result = convert_alibaba_device_trace(path, output, device_id=0)
        payload = json.loads(result.stats_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["source_format"], "alibaba-block-traces-2020")
        self.assertEqual(payload["conversion_stats"]["selected_rows"], 1)
        self.assertEqual(
            payload["standard_trace_contract"]["size"],
            "number of pages conservatively covering the byte range",
        )


    def test_gzip_csv_is_streamed(self):
        import gzip

        path = self.root / "io.csv.gz"
        with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
            handle.write(
                "device_id,opcode,offset,length,timestamp\n"
                "7,W,0,4096,10\n"
            )
        rows = list(iter_alibaba_io_rows(path))
        self.assertEqual(rows[0].device_id, 7)

    def test_official_tar_gz_member_is_streamed(self):
        import io
        import tarfile

        path = self.root / "alibaba_block_traces_2020.tar.gz"
        content = (
            "device_id,opcode,offset,length,timestamp\n"
            "8,R,0,4096,10\n"
        ).encode("utf-8")
        with tarfile.open(path, "w:gz") as archive:
            info = tarfile.TarInfo("bundle/io_traces.csv")
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
        rows = list(iter_alibaba_io_rows(path))
        self.assertEqual(rows[0].device_id, 8)

    def test_write_page_budget_keeps_final_request_and_reports_overshoot(self):
        path = self.write(
            "io.csv",
            "device_id,opcode,offset,length,timestamp\n"
            "0,R,0,4096,10\n"
            "0,W,4096,8192,20\n"
            "0,W,12288,8192,30\n"
            "0,R,20480,4096,40\n",
        )
        result = convert_alibaba_device_trace(
            path,
            self.root / "budget.standard.csv",
            device_id=0,
            max_write_pages=3,
        )
        self.assertEqual(result.stats.write_pages, 4)
        self.assertEqual(result.stats.write_page_budget_overshoot, 1)
        self.assertEqual(result.stats.stop_reason, "max_write_pages")
        self.assertEqual(result.stats.selected_rows, 3)

    def test_skip_selected_rows_extracts_later_window(self):
        path = self.write(
            "io.csv",
            "device_id,opcode,offset,length,timestamp\n"
            "0,R,0,4096,10\n"
            "0,W,4096,4096,20\n"
            "0,W,8192,4096,30\n",
        )
        output = self.root / "window.standard.csv"
        result = convert_alibaba_device_trace(
            path,
            output,
            device_id=0,
            skip_selected_rows=1,
            max_rows=1,
        )
        with output.open(newline="", encoding="utf-8") as handle:
            row = next(csv.DictReader(handle))
        self.assertEqual(row["lba"], "1")
        self.assertEqual(result.stats.skipped_selected_rows, 1)
        self.assertEqual(result.stats.stop_reason, "max_rows")


if __name__ == "__main__":
    unittest.main()
