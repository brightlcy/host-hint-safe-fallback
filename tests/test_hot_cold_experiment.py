import csv
import json
import tempfile
import unittest
from pathlib import Path

from simulator.hot_cold_experiment import run_phased_hot_cold_experiment
from simulator.run_compare_hot_cold import main as compare_main


class HotColdExperimentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.sample = Path(__file__).resolve().parents[1] / "samples" / "sample.standard.csv"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_hot_cold_experiment_writes_policy_artifacts(self) -> None:
        result = run_phased_hot_cold_experiment(
            self.sample,
            self.root / "run",
            warmup_rounds=1,
            measurement_rounds=1,
            hot_threshold=2,
            hot_window_writes=64,
            debug_check=True,
        )
        self.assertTrue(result.summary_path.exists())
        self.assertEqual(result.summary_path.name, "hot_cold_summary.csv")
        self.assertEqual(
            result.summary["hot_host_write_pages"]
            + result.summary["cold_host_write_pages"],
            result.summary["host_write_pages"],
        )
        payload = json.loads(result.manifest_path.read_text(encoding="utf-8"))
        self.assertFalse(payload["parameters"]["future_information_used"])
        self.assertTrue(
            all(payload["sanity_checks"]["placement_accounting"].values())
        )

    def test_compare_cli_creates_equal_condition_comparison(self) -> None:
        output = self.root / "comparison"
        status = compare_main(
            [
                "--input",
                str(self.sample),
                "--output-dir",
                str(output),
                "--warmup-rounds",
                "1",
                "--measurement-rounds",
                "1",
                "--hot-window-writes",
                "64",
            ]
        )
        self.assertEqual(status, 0)
        comparison = output / "policy_comparison.csv"
        self.assertTrue(comparison.exists())
        with comparison.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertIn("write_amplification_factor", {row["metric"] for row in rows})
        self.assertTrue((output / "baseline" / "baseline_summary.csv").exists())
        self.assertTrue((output / "hot_cold" / "hot_cold_summary.csv").exists())


if __name__ == "__main__":
    unittest.main()
