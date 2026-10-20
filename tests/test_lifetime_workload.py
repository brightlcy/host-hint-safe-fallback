import unittest

from simulator.lifetime_workload import (
    LifetimeClass,
    SyntheticWorkloadConfig,
    annotate_exact_lifetimes,
    calibration_median_boundary,
    classify_lifetimes,
    generate_classified_synthetic_prefix,
    generate_synthetic_lpn_sequence,
    inject_nested_random_hint_errors,
)


class LifetimeWorkloadTest(unittest.TestCase):
    def test_exact_lifetime_is_next_overwrite_write_distance(self) -> None:
        events = annotate_exact_lifetimes([5, 7, 5, 5])
        self.assertEqual(events[0].exact_lifetime_writes, 2)
        self.assertIsNone(events[1].exact_lifetime_writes)
        self.assertEqual(events[2].exact_lifetime_writes, 1)
        self.assertIsNone(events[3].exact_lifetime_writes)

    def test_censored_class_is_long_only_when_lower_bound_crosses_boundary(self) -> None:
        raw = annotate_exact_lifetimes([1, 2, 3, 4, 5, 6])
        classified = classify_lifetimes(raw, boundary_writes=2)
        self.assertEqual(classified[0].true_class, LifetimeClass.LONG)
        self.assertEqual(classified[3].true_class, LifetimeClass.LONG)
        self.assertEqual(classified[4].true_class, LifetimeClass.UNKNOWN)
        self.assertEqual(classified[5].true_class, LifetimeClass.UNKNOWN)

    def test_random_hint_error_rate_is_exact_over_hintable_versions(self) -> None:
        raw = annotate_exact_lifetimes([0, 1] * 50)
        classified = classify_lifetimes(raw, boundary_writes=2)
        hinted = inject_nested_random_hint_errors(classified, error_rate=0.20, seed=9)
        hintable = [row for row in hinted if row.true_class is not LifetimeClass.UNKNOWN]
        mismatches = [row for row in hintable if row.true_class != row.host_hint]
        self.assertEqual(len(mismatches), round(0.20 * len(hintable)))

    def test_error_sets_are_nested_for_same_seed(self) -> None:
        raw = annotate_exact_lifetimes([0, 1, 2, 3] * 50)
        classified = classify_lifetimes(raw, boundary_writes=4)
        low = inject_nested_random_hint_errors(classified, error_rate=0.10, seed=3)
        high = inject_nested_random_hint_errors(classified, error_rate=0.40, seed=3)
        low_bad = {
            row.write_sequence
            for row in low
            if row.true_class is not LifetimeClass.UNKNOWN and row.true_class != row.host_hint
        }
        high_bad = {
            row.write_sequence
            for row in high
            if row.true_class is not LifetimeClass.UNKNOWN and row.true_class != row.host_hint
        }
        self.assertTrue(low_bad.issubset(high_bad))

    def test_synthetic_generator_is_reproducible_and_phase_change_safe(self) -> None:
        config = SyntheticWorkloadConfig(
            total_writes=1000,
            logical_pages=100,
            phase_change_at=500,
            seed=17,
        )
        self.assertEqual(
            generate_synthetic_lpn_sequence(config),
            generate_synthetic_lpn_sequence(config),
        )

    def test_guard_suffix_fully_labels_prefix_without_replaying_it(self) -> None:
        config = SyntheticWorkloadConfig(total_writes=100, logical_pages=32, seed=17)
        events = generate_classified_synthetic_prefix(config, boundary_writes=9)
        self.assertEqual(len(events), config.total_writes)
        self.assertEqual(events[-1].residual_horizon_writes, 9)
        self.assertNotIn(LifetimeClass.UNKNOWN, {event.true_class for event in events})

    def test_calibration_boundary_requires_enough_completed_lifetimes(self) -> None:
        with self.assertRaises(ValueError):
            calibration_median_boundary(annotate_exact_lifetimes([1, 2, 1]))


if __name__ == "__main__":
    unittest.main()
