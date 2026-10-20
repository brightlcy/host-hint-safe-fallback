import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from simulator.run_analyze_alibaba import main, parse_args


class RunAnalyzeAlibabaTest(unittest.TestCase):
    def test_parse_args_defaults(self):
        args = parse_args(["--input", "a.csv", "--output-dir", "out"])
        self.assertEqual(args.page_size, 4096)
        self.assertEqual(args.short_iat_us, 1000)
        self.assertIsNone(args.max_source_rows)

    def test_main_creates_summary_files(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            source = root / "io.csv"
            source.write_text(
                "device_id,opcode,offset,length,timestamp\n"
                "0,W,0,4096,0\n"
                "0,R,4096,4096,100\n",
                encoding="utf-8",
            )
            with patch("builtins.print") as mocked_print:
                code = main([
                    "--input", str(source),
                    "--output-dir", str(root / "out"),
                    "--minimum-candidate-requests", "1",
                ])
            self.assertEqual(code, 0)
            self.assertTrue((root / "out" / "alibaba_device_summary.csv").exists())
            rendered = "\n".join(
                " ".join(map(str, call.args)) for call in mocked_print.call_args_list
            )
            self.assertIn("Alibaba workload analysis completed", rendered)


if __name__ == "__main__":
    unittest.main()
