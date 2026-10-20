import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from simulator.run_convert_alibaba import main, parse_args


class RunConvertAlibabaTest(unittest.TestCase):
    def test_parse_args_defaults(self):
        args = parse_args(["--input", "a.csv", "--output", "b.csv", "--device-id", "3"])
        self.assertEqual(args.device_id, 3)
        self.assertEqual(args.page_size, 4096)
        self.assertIsNone(args.max_rows)
        self.assertFalse(args.keep_absolute_time)

    def test_main_creates_standard_trace_and_prints_paths(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            source = root / "io.csv"
            output = root / "standard.csv"
            source.write_text(
                "device_id,opcode,offset,length,timestamp\n"
                "5,W,0,4096,1000000\n",
                encoding="utf-8",
            )
            with patch("builtins.print") as mocked_print:
                code = main([
                    "--input", str(source),
                    "--output", str(output),
                    "--device-id", "5",
                ])
            self.assertEqual(code, 0)
            self.assertTrue(output.exists())
            with output.open(newline="", encoding="utf-8") as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(row["flow_id"], "alibaba_device_5")
            rendered = "\n".join(" ".join(map(str, call.args)) for call in mocked_print.call_args_list)
            self.assertIn("Alibaba trace conversion completed", rendered)
            self.assertIn(str(output), rendered)


if __name__ == "__main__":
    unittest.main()
