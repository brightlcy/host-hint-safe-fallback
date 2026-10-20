import csv
import json
import tempfile
import unittest
from collections import Counter, defaultdict
from pathlib import Path
from unittest import mock

from simulator.persistence_protocol import (
    DEVELOPMENT_SEEDS,
    FROZEN_FAILURE_ASSOCIATION_THRESHOLD,
    FROZEN_LIFETIME_BOUNDARY_WRITES,
    FROZEN_MIN_MARGINAL_SUPPORT,
    select_persistence_candidate,
)
from simulator.run_persistence_development import (
    DevelopmentRunConfig,
    FROZEN_PROTOCOL_TAG,
    _git_provenance,
    build_parser,
    candidate_grid,
    run_development,
)


class PersistenceDevelopmentRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        # Historical Git tags are not part of this source-only export. These
        # stand-ins are test fixtures; production provenance guards stay intact.
        def test_git_value(args):
            if args == ("rev-parse", "HEAD"):
                return "0" * 40
            if args == ("rev-parse", "--verify", f"{FROZEN_PROTOCOL_TAG}^{{commit}}"):
                return "1" * 40
            if args == ("branch", "--show-current"):
                return "unit-test-export"
            return "unavailable"

        patcher = mock.patch("simulator.run_persistence_development._git_value", side_effect=test_git_value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tiny_config(self, **overrides):
        values = {
            "test_mode": True,
            "total_writes": 900,
            "logical_pages": 64,
            "error_rates": (0.0,),
        }
        values.update(overrides)
        return DevelopmentRunConfig(**values)

    def test_tiny_runner_executes_frozen_grid_with_fair_cached_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "development"
            with mock.patch(
                "simulator.run_persistence_development.select_persistence_candidate",
                wraps=select_persistence_candidate,
            ) as selection_helper, mock.patch(
                "simulator.run_persistence_development._git_worktree_clean",
                return_value=True,
            ):
                summary = run_development(output_dir, config=self.tiny_config())
            self.assertEqual(selection_helper.call_count, 1)

            required = (
                "development_run_results.csv",
                "development_block_results.csv",
                "development_condition_outcomes.csv",
                "development_seed_summaries.csv",
                "development_candidate_summary.json",
                "development_manifest.json",
            )
            self.assertTrue(all((output_dir / name).is_file() for name in required))
            self.assertFalse((output_dir / "frozen_v2_selection.json").exists())
            self.assertFalse((output_dir / "selected_config.json").exists())

            with (output_dir / "development_run_results.csv").open(newline="") as handle:
                run_rows = list(csv.DictReader(handle))
            policy_counts = Counter(row["policy"] for row in run_rows)
            self.assertEqual(policy_counts["baseline_zero_information"], 5)
            self.assertEqual(policy_counts["device_only_lifetime"], 5)
            self.assertEqual(policy_counts["static_host_guided"], 5)
            self.assertEqual(policy_counts["safe_fallback"], 5)
            self.assertEqual(policy_counts["persistence_fallback_v2"], 30)

            v2_rows = [row for row in run_rows if row["policy"] == "persistence_fallback_v2"]
            self.assertEqual(
                {(int(row["block_size"]), int(row["persistence_k"])) for row in v2_rows},
                {(item.block_size, item.persistence_k) for item in candidate_grid()},
            )
            # A reused FTL would accumulate host-write counters across candidates.
            self.assertEqual({int(row["host_write_pages"]) for row in v2_rows}, {900})

            by_condition = defaultdict(set)
            for row in v2_rows:
                by_condition[(row["workload_seed"], row["error_rate"])].add(
                    row["hint_realization_id"]
                )
            self.assertTrue(all(len(ids) == 1 for ids in by_condition.values()))
            self.assertEqual(set(by_condition), {(str(seed), "0.0") for seed in DEVELOPMENT_SEEDS})
            static_ids = {
                (row["workload_seed"], row["error_rate"]): row["hint_realization_id"]
                for row in run_rows
                if row["policy"] == "static_host_guided"
            }
            self.assertEqual(
                {key: next(iter(value)) for key, value in by_condition.items()}, static_ids
            )

            with (output_dir / "development_condition_outcomes.csv").open(newline="") as handle:
                outcomes = list(csv.DictReader(handle))
            self.assertEqual(len(outcomes), 30)
            static_waf = {
                (row["workload_seed"], row["error_rate"]): float(row["waf"])
                for row in run_rows
                if row["policy"] == "static_host_guided"
            }
            device_waf = {
                row["workload_seed"]: float(row["waf"])
                for row in run_rows
                if row["policy"] == "device_only_lifetime"
            }
            for outcome in outcomes:
                key = (outcome["workload_seed"], outcome["error_rate"])
                self.assertAlmostEqual(
                    float(outcome["delta_static_device"]),
                    static_waf[key] - device_waf[outcome["workload_seed"]],
                )

            with (output_dir / "development_block_results.csv").open(newline="") as handle:
                block_rows = list(csv.DictReader(handle))
            self.assertTrue(block_rows)
            self.assertTrue(all(row["hint_realization_id"] for row in block_rows))
            with (output_dir / "development_seed_summaries.csv").open(newline="") as handle:
                seed_summaries = list(csv.DictReader(handle))
            self.assertEqual(len(seed_summaries), 30)
            self.assertIn(summary["selection_status"], {"NO_GO", "SELECTED"})

            candidate_payload = json.loads(
                (output_dir / "development_candidate_summary.json").read_text()
            )
            self.assertEqual(candidate_payload["candidate_grid"], [
                {"block_size": item.block_size, "persistence_k": item.persistence_k}
                for item in candidate_grid()
            ])
            self.assertEqual(candidate_payload["development_seeds"], list(DEVELOPMENT_SEEDS))
            manifest = json.loads((output_dir / "development_manifest.json").read_text())
            self.assertEqual(len(manifest["hint_realizations"]), 5)
            self.assertEqual(manifest["total_writes"], 900)
            self.assertTrue(manifest["git_worktree_clean"])
            self.assertTrue(manifest["git_commit_hash"])
            self.assertEqual(manifest["frozen_protocol_tag"], FROZEN_PROTOCOL_TAG)
            self.assertTrue(manifest["frozen_protocol_commit"])
            self.assertTrue(
                all(
                    set(realization)
                    == {
                        "workload_seed",
                        "error_rate",
                        "hint_seed",
                        "hint_realization_id",
                    }
                    for realization in manifest["hint_realizations"]
                )
            )

    def test_development_profile_rejects_nondevelopment_and_final_seeds(self) -> None:
        with self.assertRaisesRegex(ValueError, "development seeds"):
            self.tiny_config(seeds=(201, 202, 203, 204, 301)).validate()
        with self.assertRaisesRegex(ValueError, "development seeds"):
            self.tiny_config(seeds=(301, 302, 303, 304, 305)).validate()
        with self.assertRaisesRegex(ValueError, "development stage"):
            self.tiny_config(stage="final").validate()

    def test_frozen_constants_and_production_grid_cannot_be_changed(self) -> None:
        with self.assertRaisesRegex(ValueError, "lifetime boundary"):
            self.tiny_config(
                lifetime_boundary_writes=FROZEN_LIFETIME_BOUNDARY_WRITES + 1
            ).validate()
        with self.assertRaisesRegex(ValueError, "association threshold"):
            self.tiny_config(
                failure_association_threshold=FROZEN_FAILURE_ASSOCIATION_THRESHOLD + 0.1
            ).validate()
        with self.assertRaisesRegex(ValueError, "marginal support"):
            self.tiny_config(min_marginal_support=FROZEN_MIN_MARGINAL_SUPPORT + 1).validate()
        with self.assertRaisesRegex(ValueError, "measurement grid"):
            DevelopmentRunConfig(error_rates=(0.0,)).validate()
        stage_action = next(
            action
            for action in build_parser()._actions
            if action.dest == "stage"
        )
        self.assertTrue(stage_action.required)
        self.assertEqual(stage_action.choices, ("development",))

    def test_production_rejects_dirty_worktree_before_experiment(self) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "simulator.run_persistence_development._git_worktree_clean",
            return_value=False,
        ):
            with self.assertRaisesRegex(
                ValueError, "development experiment requires a clean git worktree"
            ):
                run_development(Path(directory) / "production", config=DevelopmentRunConfig())

    def test_test_mode_bypasses_dirty_worktree_guard(self) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "simulator.run_persistence_development._git_worktree_clean",
            return_value=False,
        ):
            summary = run_development(
                Path(directory) / "test-mode", config=self.tiny_config()
            )
        self.assertIn(summary["selection_status"], {"NO_GO", "SELECTED"})

    def test_production_rejects_missing_frozen_protocol_tag(self) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "simulator.run_persistence_development._git_required_value",
            side_effect=(
                "a" * 40,
                RuntimeError(
                    "development experiment requires frozen protocol tag "
                    f"{FROZEN_PROTOCOL_TAG}"
                ),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "frozen protocol tag"):
                run_development(Path(directory) / "production", config=DevelopmentRunConfig())

    def test_test_mode_allows_dirty_git_provenance_collection(self) -> None:
        with mock.patch(
            "simulator.run_persistence_development._git_worktree_clean",
            return_value=False,
        ):
            provenance = _git_provenance(production=False)
        self.assertFalse(provenance["git_worktree_clean"])


if __name__ == "__main__":
    unittest.main()
