import csv
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from simulator.run_baseline import main, parse_args, run_baseline_experiment


class RunBaselineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.sample = Path(__file__).resolve().parents[1] / "samples" / "sample.standard.csv"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_run_experiment_creates_all_artifacts(self) -> None:
        result = run_baseline_experiment(
            self.sample,
            self.root / "run",
            debug_check=True,
        )

        self.assertTrue(result.summary_path.exists())
        self.assertTrue(result.gc_log_path.exists())
        self.assertTrue(result.config_path.exists())
        self.assertTrue(result.warmup_summary_path.exists())
        self.assertTrue(result.cumulative_summary_path.exists())
        self.assertTrue(result.warmup_gc_log_path.exists())
        self.assertTrue(result.manifest_path.exists())
        self.assertTrue(result.request_latency_path.exists())
        self.assertTrue(result.latency_summary_path.exists())
        self.assertTrue(result.latency_attribution_path.exists())
        self.assertTrue(result.latency_cause_summary_path.exists())
        self.assertTrue(result.tail_attribution_summary_path.exists())
        self.assertEqual(result.summary["host_write_pages"], 12)
        self.assertEqual(result.summary["write_amplification_factor"], 1.0)

    def test_saved_summary_and_config_match_run(self) -> None:
        result = run_baseline_experiment(self.sample, self.root / "run")

        with result.summary_path.open("r", encoding="utf-8", newline="") as handle:
            summary_rows = list(csv.DictReader(handle))
        config_payload = json.loads(result.config_path.read_text(encoding="utf-8"))

        self.assertEqual(summary_rows[0]["host_read_pages"], "2")
        self.assertEqual(config_payload["trace_stats"]["write_pages"], 12)
        self.assertEqual(config_payload["ssd_config"]["logical_pages"], 12)
        self.assertEqual(config_payload["ssd_config"]["total_pages"], 256)
        self.assertEqual(
            config_payload["ssd_config"]["capacity_basis"],
            "write-working-set",
        )

    def test_sample_gc_log_is_header_only(self) -> None:
        result = run_baseline_experiment(self.sample, self.root / "run")

        with result.gc_log_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)

        self.assertEqual(rows, [])
        self.assertIn("victim_block", reader.fieldnames or [])

    def test_parse_args_uses_documented_defaults(self) -> None:
        args = parse_args(
            ["--input", "trace.csv", "--output-dir", "results/test"]
        )

        self.assertEqual(args.page_size, 4096)
        self.assertEqual(args.chips, 4)
        self.assertEqual(args.pages_per_block, 64)
        self.assertEqual(args.overprovision_ratio, 0.20)
        self.assertFalse(args.debug_check)
        self.assertEqual(args.capacity_basis, "write-working-set")
        self.assertEqual(args.warmup_rounds, 0)
        self.assertEqual(args.measurement_rounds, 1)
        self.assertEqual(args.command_overhead_us, 20.0)
        self.assertEqual(args.read_page_us, 80.0)
        self.assertEqual(args.program_page_us, 200.0)
        self.assertEqual(args.erase_block_us, 2000.0)

    def test_main_returns_zero_and_prints_artifact_paths(self) -> None:
        output_dir = self.root / "cli"
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            status = main(
                [
                    "--input",
                    str(self.sample),
                    "--output-dir",
                    str(output_dir),
                    "--debug-check",
                ]
            )

        self.assertEqual(status, 0)
        self.assertIn("Baseline FTL run completed", stdout.getvalue())
        self.assertIn("modeled_p99_latency_us", stdout.getvalue())
        self.assertTrue((output_dir / "baseline_summary.csv").exists())
        self.assertTrue((output_dir / "request_latency.csv").exists())
        self.assertTrue((output_dir / "latency_summary.csv").exists())
        self.assertTrue((output_dir / "latency_attribution.csv").exists())
        self.assertTrue((output_dir / "latency_cause_summary.csv").exists())
        self.assertTrue((output_dir / "tail_attribution_summary.csv").exists())


if __name__ == "__main__":
    unittest.main()
