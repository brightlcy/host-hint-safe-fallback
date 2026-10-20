import csv
import json
import tempfile
import unittest
from pathlib import Path

from simulator.experiment_runner import run_phased_baseline_experiment


class ExperimentRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.sample = (
            Path(__file__).resolve().parents[1]
            / "samples"
            / "sample.standard.csv"
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_warmup_and_measurement_share_state_but_not_counters(self) -> None:
        result = run_phased_baseline_experiment(
            self.sample,
            self.root / "run",
            warmup_rounds=1,
            measurement_rounds=2,
            debug_check=True,
            experiment_name="phase-test",
        )

        self.assertEqual(result.warmup_summary["host_write_pages"], 12)
        self.assertEqual(result.summary["host_write_pages"], 24)
        self.assertEqual(result.cumulative_summary["host_write_pages"], 36)
        self.assertEqual(result.summary["mapped_logical_pages"], 12)
        self.assertEqual(result.cumulative_summary["mapped_logical_pages"], 12)

    def test_all_reproducibility_artifacts_are_created(self) -> None:
        result = run_phased_baseline_experiment(
            self.sample,
            self.root / "run",
            warmup_rounds=1,
            measurement_rounds=1,
        )

        paths = (
            result.warmup_summary_path,
            result.summary_path,
            result.cumulative_summary_path,
            result.warmup_gc_log_path,
            result.gc_log_path,
            result.request_latency_path,
            result.latency_summary_path,
            result.latency_attribution_path,
            result.latency_cause_summary_path,
            result.tail_attribution_summary_path,
            result.config_path,
            result.manifest_path,
        )
        self.assertTrue(all(path.exists() for path in paths))

    def test_manifest_records_hash_phase_plan_and_sanity_checks(self) -> None:
        result = run_phased_baseline_experiment(
            self.sample,
            self.root / "run",
            warmup_rounds=1,
            measurement_rounds=2,
            experiment_name="manifest-test",
        )
        payload = json.loads(result.manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(payload["experiment_name"], "manifest-test")
        self.assertEqual(payload["phase_plan"]["warmup_rounds"], 1)
        self.assertEqual(payload["phase_plan"]["measurement_rounds"], 2)
        self.assertEqual(len(payload["input_trace"]["sha256"]), 64)
        self.assertTrue(
            all(payload["sanity_checks"]["measurement"].values())
        )
        self.assertTrue(all(payload["sanity_checks"]["latency"].values()))
        self.assertTrue(
            all(payload["sanity_checks"]["latency_attribution"].values())
        )
        self.assertEqual(
            payload["latency_model"]["model_name"],
            "single-resource-event-cost-v1",
        )
        self.assertEqual(
            payload["latency_results"]["all"]["request_count"],
            6,
        )
        self.assertEqual(
            sum(
                row["request_count"]
                for row in payload["latency_cause_results"].values()
            ),
            6,
        )
        self.assertIn("p99", payload["tail_attribution_results"])

    def test_saved_measurement_summary_excludes_warmup_counters(self) -> None:
        result = run_phased_baseline_experiment(
            self.sample,
            self.root / "run",
            warmup_rounds=2,
            measurement_rounds=1,
        )

        with result.summary_path.open("r", encoding="utf-8", newline="") as handle:
            row = next(csv.DictReader(handle))
        with result.cumulative_summary_path.open(
            "r", encoding="utf-8", newline=""
        ) as handle:
            cumulative = next(csv.DictReader(handle))

        self.assertEqual(row["host_write_pages"], "12")
        self.assertEqual(cumulative["host_write_pages"], "36")

    def test_request_latency_log_contains_only_measurement_requests(self) -> None:
        result = run_phased_baseline_experiment(
            self.sample,
            self.root / "latency",
            warmup_rounds=2,
            measurement_rounds=2,
        )

        with result.request_latency_path.open(
            "r", encoding="utf-8", newline=""
        ) as handle:
            rows = list(csv.DictReader(handle))
        with result.latency_summary_path.open(
            "r", encoding="utf-8", newline=""
        ) as handle:
            summary_rows = list(csv.DictReader(handle))
        with result.latency_attribution_path.open(
            "r", encoding="utf-8", newline=""
        ) as handle:
            attribution_rows = list(csv.DictReader(handle))
        with result.latency_cause_summary_path.open(
            "r", encoding="utf-8", newline=""
        ) as handle:
            cause_rows = list(csv.DictReader(handle))
        with result.tail_attribution_summary_path.open(
            "r", encoding="utf-8", newline=""
        ) as handle:
            tail_rows = list(csv.DictReader(handle))

        self.assertEqual(len(rows), 6)
        self.assertEqual({row["round_index"] for row in rows}, {"1", "2"})
        self.assertEqual(summary_rows[0]["scope"], "all")
        self.assertEqual(summary_rows[0]["request_count"], "6")
        self.assertEqual(len(attribution_rows), 6)
        self.assertEqual(len(cause_rows), 4)
        self.assertEqual(len(tail_rows), 4)


    def test_invalid_phase_rounds_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "warmup_rounds"):
            run_phased_baseline_experiment(
                self.sample,
                self.root / "bad1",
                warmup_rounds=-1,
            )
        with self.assertRaisesRegex(ValueError, "measurement_rounds"):
            run_phased_baseline_experiment(
                self.sample,
                self.root / "bad2",
                measurement_rounds=0,
            )


if __name__ == "__main__":
    unittest.main()
