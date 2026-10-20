import unittest

from simulator.lifetime_tracker import (
    OnlineLifetimeTracker,
    RollingHintAssociationDetector,
    RollingMismatchDetector,
)
from simulator.lifetime_workload import LifetimeClass


class OnlineLifetimeTrackerTest(unittest.TestCase):
    def test_short_resolves_on_overwrite_at_boundary(self) -> None:
        tracker = OnlineLifetimeTracker(boundary_writes=2)
        tracker.observe_write(1, host_hint=LifetimeClass.SHORT)  # seq 1
        tracker.observe_write(2)  # seq 2
        resolutions, completion = tracker.observe_write(1)  # seq 3, lifetime 2
        self.assertIsNotNone(completion)
        matched = [row for row in resolutions if row.lpn == 1]
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0].lifetime_class, LifetimeClass.SHORT)
        self.assertFalse(matched[0].hint_mismatch)

    def test_long_resolves_as_soon_as_version_survives_boundary(self) -> None:
        tracker = OnlineLifetimeTracker(boundary_writes=2)
        tracker.observe_write(1, host_hint=LifetimeClass.SHORT)  # seq 1
        tracker.observe_write(2)  # seq 2
        tracker.observe_write(3)  # seq 3
        resolutions, _ = tracker.observe_write(4)  # seq 4 => LPN1 age 3 > 2
        long_rows = [row for row in resolutions if row.lpn == 1]
        self.assertEqual(len(long_rows), 1)
        self.assertEqual(long_rows[0].lifetime_class, LifetimeClass.LONG)
        self.assertTrue(long_rows[0].hint_mismatch)
        self.assertEqual(long_rows[0].resolution_reason, "survived_boundary")

    def test_device_prediction_reuses_last_resolved_class(self) -> None:
        tracker = OnlineLifetimeTracker(boundary_writes=3)
        tracker.observe_write(5)
        tracker.observe_write(5)  # lifetime 1 => short
        self.assertEqual(tracker.predict_next_class(5), LifetimeClass.SHORT)
        self.assertEqual(tracker.predict_next_class(999), LifetimeClass.LONG)

    def test_long_completion_does_not_emit_duplicate_resolution(self) -> None:
        tracker = OnlineLifetimeTracker(boundary_writes=1)
        tracker.observe_write(1)
        tracker.observe_write(2)
        resolutions, _ = tracker.observe_write(3)
        self.assertEqual(len([row for row in resolutions if row.lpn == 1]), 1)
        resolutions, completion = tracker.observe_write(1)
        self.assertIsNotNone(completion)
        self.assertEqual(len([row for row in resolutions if row.lpn == 1]), 0)


class RollingMismatchDetectorTest(unittest.TestCase):
    def _resolution(self, seq: int, mismatch: bool):
        from simulator.lifetime_tracker import LifetimeClassResolution

        true_class = LifetimeClass.LONG
        hint = LifetimeClass.SHORT if mismatch else LifetimeClass.LONG
        return LifetimeClassResolution(
            lpn=seq,
            birth_sequence=seq,
            resolved_sequence=seq,
            lifetime_class=true_class,
            host_hint=hint,
            exact_lifetime_writes=None,
            resolution_reason="test",
        )

    def test_clean_hints_do_not_trigger(self) -> None:
        detector = RollingMismatchDetector(
            failure_boundary=0.20, window_size=64, min_samples=30
        )
        for seq in range(1, 65):
            detector.observe_resolution(self._resolution(seq, False))
        self.assertFalse(detector.triggered)

    def test_heavy_mismatch_triggers_conservatively(self) -> None:
        detector = RollingMismatchDetector(
            failure_boundary=0.20, window_size=64, min_samples=30
        )
        for seq in range(1, 65):
            detector.observe_resolution(self._resolution(seq, True))
        self.assertTrue(detector.triggered)
        self.assertIsNotNone(detector.trigger_sequence)
        self.assertGreater(detector.wilson_lower_bound(), 0.20)

    def test_unknown_hints_do_not_enter_denominator(self) -> None:
        from simulator.lifetime_tracker import LifetimeClassResolution

        detector = RollingMismatchDetector(
            failure_boundary=0.20, window_size=64, min_samples=30
        )
        detector.observe_resolution(
            LifetimeClassResolution(
                lpn=1,
                birth_sequence=1,
                resolved_sequence=2,
                lifetime_class=LifetimeClass.LONG,
                host_hint=LifetimeClass.UNKNOWN,
                exact_lifetime_writes=None,
                resolution_reason="test",
            )
        )
        self.assertEqual(detector.snapshot().sample_count, 0)


if __name__ == "__main__":
    unittest.main()

class RollingHintAssociationDetectorTest(unittest.TestCase):
    def _resolution(self, seq: int, true_short: bool, hint_short: bool):
        from simulator.lifetime_tracker import LifetimeClassResolution
        return LifetimeClassResolution(
            lpn=seq,
            birth_sequence=seq,
            resolved_sequence=seq,
            lifetime_class=LifetimeClass.SHORT if true_short else LifetimeClass.LONG,
            host_hint=LifetimeClass.SHORT if hint_short else LifetimeClass.LONG,
            exact_lifetime_writes=None,
            resolution_reason="test",
        )

    def test_perfect_and_perfectly_inverted_hints_both_have_full_separation(self) -> None:
        positive = RollingHintAssociationDetector(
            failure_association_threshold=0.2, window_size=128, min_samples=30
        )
        inverted = RollingHintAssociationDetector(
            failure_association_threshold=0.2, window_size=128, min_samples=30
        )
        for seq in range(1, 65):
            true_short = seq % 2 == 0
            positive.observe_resolution(self._resolution(seq, true_short, true_short))
            inverted.observe_resolution(self._resolution(seq, true_short, not true_short))
        self.assertAlmostEqual(abs(positive.phi_correlation()), 1.0)
        self.assertAlmostEqual(abs(inverted.phi_correlation()), 1.0)
        self.assertFalse(positive.triggered)
        self.assertFalse(inverted.triggered)

    def test_unassociated_hints_can_trigger_when_ci_is_below_boundary(self) -> None:
        detector = RollingHintAssociationDetector(
            failure_association_threshold=0.30, window_size=128, min_samples=128
        )
        # Balanced 2x2 table => phi = 0.
        seq = 0
        for true_short, hint_short in [(True, True), (True, False), (False, True), (False, False)] * 32:
            seq += 1
            detector.observe_resolution(self._resolution(seq, true_short, hint_short))
        self.assertAlmostEqual(detector.phi_correlation(), 0.0)
        self.assertLess(detector.fisher_abs_upper_bound(), 0.30)
        self.assertTrue(detector.triggered)
        self.assertEqual(detector.snapshot().trigger_sample_count, 128)

    def test_trigger_sample_count_is_frozen_after_trigger(self) -> None:
        detector = RollingHintAssociationDetector(
            failure_association_threshold=0.80, window_size=128, min_samples=4
        )
        pairs = [(True, True), (True, False), (False, True), (False, False)]
        sequence = 0
        for true_short, hint_short in pairs * 8:
            sequence += 1
            detector.observe_resolution(
                self._resolution(sequence, true_short, hint_short)
            )
            if detector.triggered:
                break
        self.assertTrue(detector.triggered)
        trigger_snapshot = detector.snapshot()
        self.assertIsNotNone(trigger_snapshot.trigger_sample_count)
        self.assertEqual(trigger_snapshot.trigger_sequence, sequence)

        for true_short, hint_short in pairs * 8:
            sequence += 1
            detector.observe_resolution(
                self._resolution(sequence, true_short, hint_short)
            )
        end_snapshot = detector.snapshot()
        self.assertEqual(
            end_snapshot.trigger_sample_count,
            trigger_snapshot.trigger_sample_count,
        )
        self.assertGreater(end_snapshot.sample_count, trigger_snapshot.trigger_sample_count)

    def test_nontriggered_detector_has_no_trigger_sample_count(self) -> None:
        detector = RollingHintAssociationDetector(
            failure_association_threshold=0.20, window_size=128, min_samples=30
        )
        for sequence in range(1, 65):
            true_short = sequence % 2 == 0
            detector.observe_resolution(self._resolution(sequence, true_short, true_short))
        self.assertFalse(detector.triggered)
        self.assertIsNone(detector.snapshot().trigger_sample_count)

    def test_threshold_is_rejected_when_window_cannot_detect_it(self) -> None:
        with self.assertRaisesRegex(ValueError, "not detectable"):
            RollingHintAssociationDetector(
                failure_association_threshold=0.20,
                window_size=64,
                min_samples=30,
            )

    def test_threshold_is_detectable_with_larger_window(self) -> None:
        minimum = RollingHintAssociationDetector.minimum_fisher_abs_upper_bound(
            window_size=128
        )
        self.assertLess(minimum, 0.20)
        RollingHintAssociationDetector(
            failure_association_threshold=0.20,
            window_size=128,
            min_samples=30,
        )
