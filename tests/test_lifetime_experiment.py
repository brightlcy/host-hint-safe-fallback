import tempfile
import unittest
from collections import Counter
from pathlib import Path

from simulator.lifetime_experiment import (
    absolute_hint_phi,
    calibrate_synthetic_failure_boundary,
    derive_hint_seed,
    persistence_v2_block_rows,
    persistence_v2_run_row,
    run_synthetic_measurement,
    save_calibration_audit_rows,
    save_experiment_rows,
    select_stable_failure_boundary,
)
from simulator.config import SSDConfig
from simulator.lifetime_ftl import PersistenceFallbackFTL
from simulator.lifetime_tracker import LifetimeClassResolution
from simulator.lifetime_workload import (
    LifetimeClass,
    PageWriteEvent,
)


class LifetimeExperimentTest(unittest.TestCase):
    def test_absolute_phi_is_label_invariant(self) -> None:
        rows = [
            PageWriteEvent(1, 0.0, 1, 1, 3, LifetimeClass.SHORT, LifetimeClass.SHORT),
            PageWriteEvent(2, 0.1, 2, 4, 2, LifetimeClass.LONG, LifetimeClass.LONG),
        ]
        inverted = [
            PageWriteEvent(1, 0.0, 1, 1, 3, LifetimeClass.SHORT, LifetimeClass.LONG),
            PageWriteEvent(2, 0.1, 2, 4, 2, LifetimeClass.LONG, LifetimeClass.SHORT),
        ]
        self.assertAlmostEqual(absolute_hint_phi(rows), 1.0)
        self.assertAlmostEqual(absolute_hint_phi(inverted), 1.0)

    def test_small_calibration_and_measurement_pipeline(self) -> None:
        calibration = calibrate_synthetic_failure_boundary(
            calibration_seeds=(1, 2),
            total_writes=1000,
            logical_pages=64,
            error_rates=(0.0, 0.25, 0.50),
        )
        self.assertGreaterEqual(calibration.lifetime_boundary_writes, 1)
        self.assertEqual(
            [point["error_rate"] for point in calibration.calibration_points],
            [0.0, 0.25, 0.5],
        )
        rows = run_synthetic_measurement(
            calibration,
            measurement_seeds=(101,),
            total_writes=1000,
            logical_pages=64,
            error_rates=(0.0, 0.50, 1.0),
            detector_window=128,
        )
        self.assertTrue(any(row["policy"] == "static_host_guided" for row in rows))
        self.assertTrue(any(row["policy"] == "baseline_zero_information" for row in rows))
        self.assertTrue(
            all("fallback_trigger_write_index" in row for row in rows)
        )
        self.assertTrue(
            all("fallback_trigger_sample_count" in row for row in rows)
        )
        static = next(row for row in rows if row["policy"] == "static_host_guided")
        self.assertIsNotNone(static["short_host_write_pages"])
        self.assertIsNotNone(static["tracker_resolution_count"])
        self.assertEqual(static["host_guided_write_pages"], static["host_write_pages"])
        with tempfile.TemporaryDirectory() as tmp:
            path = save_experiment_rows(rows, Path(tmp) / "results.csv")
            self.assertTrue(path.exists())
            audit_path = save_calibration_audit_rows(
                calibration, Path(tmp) / "calibration_boundary_audit.csv"
            )
            self.assertTrue(audit_path.exists())

    def test_stable_boundary_requires_nonnegative_tail(self) -> None:
        points = [
            {"error_rate": 0.0, "median_delta_waf": -0.1, "median_absolute_phi": 1.0},
            {"error_rate": 0.1, "median_delta_waf": 0.1, "median_absolute_phi": 0.8},
            {"error_rate": 0.2, "median_delta_waf": -0.1, "median_absolute_phi": 0.6},
            {"error_rate": 0.3, "median_delta_waf": 0.1, "median_absolute_phi": 0.4},
            {"error_rate": 0.4, "median_delta_waf": 0.2, "median_absolute_phi": 0.2},
        ]
        self.assertEqual(select_stable_failure_boundary(points), (0.3, 0.4))
        self.assertEqual(
            select_stable_failure_boundary(points[:3]), (None, None)
        )

    def test_measurement_replays_error_independent_policies_once_per_seed(self) -> None:
        calibration = calibrate_synthetic_failure_boundary(
            calibration_seeds=(1, 2),
            total_writes=1000,
            logical_pages=64,
            error_rates=(0.0, 0.25, 0.50),
        )
        rows = run_synthetic_measurement(
            calibration,
            measurement_seeds=(101, 102),
            total_writes=1000,
            logical_pages=64,
            error_rates=(0.0, 0.50),
            detector_window=128,
        )
        counts = Counter(row["policy"] for row in rows)
        self.assertEqual(counts["baseline_zero_information"], 2)
        self.assertEqual(counts["device_only_lifetime"], 2)
        self.assertEqual(counts["oracle_observed_lifetime"], 2)
        self.assertEqual(counts["static_host_guided"], 4)
        self.assertEqual(
            {row["error_rate"] for row in rows if row["policy"] == "device_only_lifetime"},
            {None},
        )

    def test_derived_hint_seeds_are_deterministic_and_workload_specific(self) -> None:
        self.assertEqual(derive_hint_seed(42, 101), derive_hint_seed(42, 101))
        self.assertNotEqual(derive_hint_seed(42, 101), derive_hint_seed(42, 102))

    def test_persistence_v2_artifacts_include_required_block_and_run_fields(self) -> None:
        config = SSDConfig.from_logical_pages(
            logical_pages=32,
            chips=1,
            pages_per_block=4,
            overprovision_ratio=0.25,
            gc_threshold_ratio=0.10,
            reserved_gc_blocks=1,
        )
        ftl = PersistenceFallbackFTL(
            config,
            boundary_writes=451,
            failure_association_threshold=0.29980003993028376,
            block_size=20,
            persistence_k=2,
            min_marginal_support=5,
        )
        # Direct detector input keeps this as artifact-schema coverage, not an
        # experiment run. Five of every 2x2 cell form one valid BAD block.
        for ordinal, (true_short, hint_short) in enumerate(
            [(True, True), (True, False), (False, True), (False, False)] * 5,
            start=1,
        ):
            ftl.detector.observe_resolution(
                LifetimeClassResolution(
                    lpn=ordinal,
                    birth_sequence=ordinal,
                    resolved_sequence=ordinal + 100,
                    lifetime_class=(
                        LifetimeClass.SHORT if true_short else LifetimeClass.LONG
                    ),
                    host_hint=(
                        LifetimeClass.SHORT if hint_short else LifetimeClass.LONG
                    ),
                    exact_lifetime_writes=None,
                    resolution_reason="test",
                )
            )
        block_rows = persistence_v2_block_rows(
            ftl=ftl, workload_seed=7, error_rate=0.4
        )
        self.assertEqual(len(block_rows), 1)
        self.assertTrue(
            {
                "detector_version",
                "block_index",
                "first_detector_observation_ordinal",
                "last_detector_observation_ordinal",
                "block_end_host_write_index",
                "ss_count",
                "sl_count",
                "ls_count",
                "ll_count",
                "absolute_phi",
                "block_state",
                "bad_streak_before",
                "bad_streak_after",
            }.issubset(block_rows[0])
        )
        run_row = persistence_v2_run_row(
            ftl=ftl,
            seed=7,
            error_rate=0.4,
            boundary_writes=451,
            failure_association_threshold=0.29980003993028376,
        )
        self.assertTrue(
            {
                "completed_block_count",
                "good_block_count",
                "bad_block_count",
                "invalid_block_count",
                "partial_block_sample_count",
                "trigger_block_index",
                "trigger_detector_observation_ordinal",
                "trigger_host_write_index",
                "host_guided_write_pages",
                "device_fallback_write_pages",
                "waf",
                "nand_write_pages",
                "gc_count",
                "wear_imbalance_cv",
            }.issubset(run_row)
        )


if __name__ == "__main__":
    unittest.main()
