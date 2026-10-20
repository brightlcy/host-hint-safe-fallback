import csv
import hashlib
import inspect
import json
import tempfile
import unittest
from collections import Counter, defaultdict
from pathlib import Path
from unittest import mock

from simulator.persistence_protocol import (
    FROZEN_FAILURE_ASSOCIATION_THRESHOLD,
    FROZEN_LIFETIME_BOUNDARY_WRITES,
    FROZEN_MIN_MARGINAL_SUPPORT,
    FrozenV2Selection,
    PersistenceCandidate,
    save_frozen_v2_selection,
)
from simulator.run_persistence_final import (
    FINAL_HELD_OUT_SEEDS,
    FROZEN_SELECTION_TAG,
    FinalRunConfig,
    TEST_ONLY_SEEDS,
    _condition_outcome,
    build_parser,
    run_final,
    summarize_final_outcomes,
)


class PersistenceFinalRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        # Isolate unit tests from historical Git metadata that is kept locally.
        # Missing-tag failure tests still patch _git_required_value explicitly.
        def test_git_value(args):
            if args == ("rev-parse", "HEAD"):
                return "0" * 40
            if len(args) == 3 and args[:2] == ("rev-parse", "--verify"):
                return "1" * 40
            if args == ("branch", "--show-current"):
                return "unit-test-export"
            return "unavailable"

        patcher = mock.patch("simulator.run_persistence_final._git_value", side_effect=test_git_value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def frozen_selection(self, directory: Path, *, block_size: int = 512, persistence_k: int = 2) -> Path:
        path = directory / "frozen_v2_selection.json"
        save_frozen_v2_selection(
            FrozenV2Selection(
                candidate=PersistenceCandidate(block_size, persistence_k)
            ),
            path,
        )
        return path

    def tiny_config(self, selection_file: Path, **overrides) -> FinalRunConfig:
        values = {
            "selection_file": selection_file,
            "test_mode": True,
            "seeds": TEST_ONLY_SEEDS,
            "total_writes": 900,
            "logical_pages": 64,
            "error_rates": (0.0,),
        }
        values.update(overrides)
        return FinalRunConfig(**values)

    @staticmethod
    def outcome(seed: int, truth: str, *, timely: bool = True, recovered: float = 1.0,
                v1_false: bool = False, v2_false: bool = False) -> dict:
        return {
            "workload_seed": seed,
            "ground_truth": truth,
            "v2_timely_detection": timely,
            "v2_recovered_harm": recovered,
            "v1_false_fallback": v1_false,
            "v2_false_fallback": v2_false,
        }

    def test_final_runner_requires_frozen_selection_and_exact_candidate(self) -> None:
        with self.assertRaisesRegex(ValueError, "selection file"):
            FinalRunConfig(test_mode=True, seeds=TEST_ONLY_SEEDS).validate()
        with tempfile.TemporaryDirectory() as directory:
            selection = self.frozen_selection(Path(directory), persistence_k=3)
            with self.assertRaisesRegex(ValueError, "B512/K2"):
                self.tiny_config(selection).validate()
            selection = self.frozen_selection(Path(directory), block_size=256)
            with self.assertRaisesRegex(ValueError, "B512/K2"):
                self.tiny_config(selection).validate()
            selection = self.frozen_selection(Path(directory))
            payload = json.loads(selection.read_text())
            payload["min_marginal_support"] = 6
            selection.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "marginal support"):
                self.tiny_config(selection).validate()
            selection.write_text("not json", encoding="utf-8")
            with self.assertRaises(Exception):
                self.tiny_config(selection).validate()

    def test_seed_splits_reject_final_test_execution_and_development_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            selection = self.frozen_selection(Path(directory))
            with self.assertRaisesRegex(ValueError, "must never execute final"):
                self.tiny_config(selection, seeds=FINAL_HELD_OUT_SEEDS).validate()
            with self.assertRaisesRegex(ValueError, "development seeds"):
                self.tiny_config(selection, seeds=(201, 202, 203, 204, 205)).validate()
            with self.assertRaisesRegex(ValueError, "authorized seed split"):
                FinalRunConfig(selection_file=selection, seeds=TEST_ONLY_SEEDS).validate()

    def test_cli_requires_explicit_final_arguments(self) -> None:
        parser = build_parser()
        destinations = {action.dest: action for action in parser._actions}
        self.assertTrue(destinations["stage"].required)
        self.assertTrue(destinations["selection_file"].required)
        self.assertTrue(destinations["output_dir"].required)
        self.assertEqual(destinations["stage"].choices, ("final",))

    def test_tiny_runner_has_one_selected_candidate_and_paired_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selection = self.frozen_selection(root)
            output_dir = root / "final"
            with mock.patch(
                "simulator.run_persistence_final._git_worktree_clean", return_value=True
            ):
                summary = run_final(output_dir, config=self.tiny_config(selection))
            required = (
                "final_run_results.csv", "final_block_results.csv",
                "final_condition_outcomes.csv", "final_seed_summaries.csv",
                "final_summary.json", "final_manifest.json",
            )
            self.assertTrue(all((output_dir / name).is_file() for name in required))
            with (output_dir / "final_run_results.csv").open(newline="") as handle:
                rows = list(csv.DictReader(handle))
            counts = Counter(row["policy"] for row in rows)
            self.assertEqual(counts["baseline_zero_information"], 5)
            self.assertEqual(counts["device_only_lifetime"], 5)
            self.assertEqual(counts["static_host_guided"], 5)
            self.assertEqual(counts["safe_fallback_v1"], 5)
            self.assertEqual(counts["persistence_fallback_v2"], 5)
            v2_rows = [row for row in rows if row["policy"] == "persistence_fallback_v2"]
            self.assertEqual(
                {(int(row["block_size"]), int(row["persistence_k"])) for row in v2_rows},
                {(512, 2)},
            )
            ids = defaultdict(set)
            for row in rows:
                if row["policy"] in {"static_host_guided", "safe_fallback_v1", "persistence_fallback_v2"}:
                    ids[(row["workload_seed"], row["error_rate"])].add(row["hint_realization_id"])
            self.assertTrue(all(len(value) == 1 for value in ids.values()))

            with (output_dir / "final_condition_outcomes.csv").open(newline="") as handle:
                outcomes = list(csv.DictReader(handle))
            self.assertEqual(len(outcomes), 5)
            static_waf = {(r["workload_seed"], r["error_rate"]): float(r["waf"]) for r in rows if r["policy"] == "static_host_guided"}
            device_waf = {r["workload_seed"]: float(r["waf"]) for r in rows if r["policy"] == "device_only_lifetime"}
            for row in outcomes:
                self.assertAlmostEqual(float(row["delta_static_device"]), static_waf[(row["workload_seed"], row["error_rate"])] - device_waf[row["workload_seed"]])
            manifest = json.loads((output_dir / "final_manifest.json").read_text())
            self.assertEqual(manifest["frozen_selection_sha256"], hashlib.sha256(selection.read_bytes()).hexdigest())
            self.assertEqual(manifest["frozen_selection_tag"], FROZEN_SELECTION_TAG)
            self.assertEqual(len(manifest["hint_realizations"]), 5)
            self.assertEqual(summary["secondary_tradeoff_fields"], ["waf", "nand_write_pages", "valid_page_migrations", "gc_count", "wear_imbalance_cv"])

    def test_final_evaluation_gates_and_verdicts(self) -> None:
        rows = []
        for index, seed in enumerate(TEST_ONLY_SEEDS):
            rows.append(self.outcome(seed, "harmful", timely=index < 4, recovered=1.0 if index < 4 else 0.0))
            rows.append(self.outcome(seed, "beneficial", v1_false=True, v2_false=False))
        summaries, result = summarize_final_outcomes(rows, expected_seeds=TEST_ONLY_SEEDS)
        self.assertEqual(len(summaries), 5)
        self.assertEqual(result["gate1_harmful_timely_detection"]["result"], "PASS")
        self.assertEqual(result["gate2_harmful_recovery"]["result"], "PASS")
        self.assertEqual(result["safety_improvement"]["result"], "PASS")
        self.assertEqual(result["verdict"], "FINAL_SUPPORT")

        partial_rows = [self.outcome(seed, "harmful") for seed in TEST_ONLY_SEEDS]
        partial_rows.extend(self.outcome(seed, "beneficial", v1_false=False, v2_false=True) for seed in TEST_ONLY_SEEDS)
        _, partial = summarize_final_outcomes(partial_rows, expected_seeds=TEST_ONLY_SEEDS)
        self.assertEqual(partial["verdict"], "PARTIAL_SUPPORT")
        no_support_rows = [self.outcome(seed, "harmful", timely=seed == TEST_ONLY_SEEDS[0]) for seed in TEST_ONLY_SEEDS]
        _, no_support = summarize_final_outcomes(no_support_rows, expected_seeds=TEST_ONLY_SEEDS)
        self.assertEqual(no_support["verdict"], "NO_SUPPORT")
        self.assertEqual(no_support["safety_improvement"]["result"], "UNEVALUABLE")

    def test_equal_seed_weighting_and_harmful_edge_cases(self) -> None:
        rows = [self.outcome(TEST_ONLY_SEEDS[0], "beneficial", v1_false=True, v2_false=False)]
        rows.extend(self.outcome(TEST_ONLY_SEEDS[0], "beneficial") for _ in range(9))
        rows.append(self.outcome(TEST_ONLY_SEEDS[1], "beneficial", v1_false=True, v2_false=False))
        _, result = summarize_final_outcomes(rows, expected_seeds=TEST_ONLY_SEEDS)
        self.assertAlmostEqual(result["safety_improvement"]["v1_mean_seed_level_beneficial_false_fallback_rate"], 0.55)
        self.assertEqual(result["safety_improvement"]["result"], "PASS")
        # A seed with no harmful evidence does not count as an automatic pass.
        harmful = [self.outcome(seed, "harmful") for seed in TEST_ONLY_SEEDS[:3]]
        _, gate = summarize_final_outcomes(harmful, expected_seeds=TEST_ONLY_SEEDS)
        self.assertEqual(gate["gate1_harmful_timely_detection"]["result"], "FAIL")
        self.assertEqual(gate["gate2_harmful_recovery"]["result"], "FAIL")

    def test_condition_ground_truth_false_fallback_and_timely_boundary(self) -> None:
        static = {"waf": 1.2}
        device = {"waf": 1.0}
        v1 = {"waf": 1.1, "fallback_triggered": True, "fallback_trigger_write_index": 500}
        v2 = {"waf": 1.05, "fallback_triggered": True, "trigger_host_write_index": 500}
        harmful = _condition_outcome(
            seed=901, error_rate=0.1, static=static, device=device, v1=v1, v2=v2,
            hint_identifier="sha256:test", total_writes=1_000,
        )
        self.assertEqual(harmful["ground_truth"], "harmful")
        self.assertTrue(harmful["v1_timely_detection"])
        self.assertTrue(harmful["v2_timely_detection"])
        self.assertFalse(harmful["v1_false_fallback"])
        self.assertAlmostEqual(harmful["v2_recovered_harm"], 0.15)
        beneficial = _condition_outcome(
            seed=901, error_rate=0.2, static={"waf": 0.9}, device=device,
            v1=v1, v2=v2, hint_identifier="sha256:test2", total_writes=1_000,
        )
        self.assertEqual(beneficial["ground_truth"], "beneficial")
        self.assertTrue(beneficial["v1_false_fallback"])
        neutral = _condition_outcome(
            seed=901, error_rate=0.3, static=device, device=device, v1=v1, v2=v2,
            hint_identifier="sha256:test3", total_writes=1_000,
        )
        self.assertEqual(neutral["ground_truth"], "neutral")

    def test_production_provenance_guards_and_no_candidate_selector(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            selection = self.frozen_selection(Path(directory))
            with mock.patch(
                "simulator.run_persistence_final._git_worktree_clean", return_value=False
            ):
                with self.assertRaisesRegex(ValueError, "clean git worktree"):
                    run_final(Path(directory) / "out", config=FinalRunConfig(selection_file=selection))
            with mock.patch(
                "simulator.run_persistence_final._git_required_value",
                side_effect=("a" * 40, "b" * 40, RuntimeError("frozen selection tag missing")),
            ):
                with self.assertRaisesRegex(RuntimeError, "selection tag"):
                    run_final(Path(directory) / "out", config=FinalRunConfig(selection_file=selection))
        source = inspect.getsource(__import__("simulator.run_persistence_final", fromlist=["*"]))
        self.assertNotIn("select" + "_persistence_candidate", source)
        self.assertNotIn("candidate" + "_grid", source)

    def test_frozen_constants_are_not_runner_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            selection = self.frozen_selection(Path(directory))
            config = self.tiny_config(selection)
            loaded = config.validate()
            self.assertEqual(loaded.lifetime_boundary_writes, FROZEN_LIFETIME_BOUNDARY_WRITES)
            self.assertEqual(loaded.failure_association_threshold, FROZEN_FAILURE_ASSOCIATION_THRESHOLD)
            self.assertEqual(loaded.min_marginal_support, FROZEN_MIN_MARGINAL_SUPPORT)


if __name__ == "__main__":
    unittest.main()
