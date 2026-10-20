import unittest

from simulator.lifetime_tracker import LifetimeClassResolution
from simulator.lifetime_workload import LifetimeClass
from simulator.persistence_detector import (
    BAD,
    GOOD,
    INVALID,
    PersistenceAssociationDetector,
)


class PersistenceAssociationDetectorTest(unittest.TestCase):
    block_size = 20

    @staticmethod
    def resolution(
        observation: int,
        true_short: bool,
        hint_short: bool,
        *,
        host_write: int | None = None,
    ) -> LifetimeClassResolution:
        return LifetimeClassResolution(
            lpn=observation,
            birth_sequence=observation,
            resolved_sequence=host_write if host_write is not None else observation,
            lifetime_class=LifetimeClass.SHORT if true_short else LifetimeClass.LONG,
            host_hint=LifetimeClass.SHORT if hint_short else LifetimeClass.LONG,
            exact_lifetime_writes=None,
            resolution_reason="test",
        )

    @staticmethod
    def balanced_bad_pairs():
        # Five of every 2x2 cell: valid margins and phi = 0.
        return [(True, True), (True, False), (False, True), (False, False)] * 5

    @staticmethod
    def perfect_positive_pairs():
        return [(True, True)] * 10 + [(False, False)] * 10

    @staticmethod
    def perfect_reversed_pairs():
        return [(True, False)] * 10 + [(False, True)] * 10

    def observe_pairs(self, detector, pairs, *, first_observation=1, host_write=None):
        for offset, (true_short, hint_short) in enumerate(pairs):
            observation = first_observation + offset
            detector.observe_resolution(
                self.resolution(
                    observation,
                    true_short,
                    hint_short,
                    host_write=host_write,
                )
            )

    def detector(self, *, threshold=0.29980003993028376, persistence_k=2):
        return PersistenceAssociationDetector(
            failure_association_threshold=threshold,
            block_size=self.block_size,
            persistence_k=persistence_k,
            min_marginal_support=5,
        )

    def test_full_blocks_are_non_overlapping_and_partial_is_not_evidence(self) -> None:
        detector = self.detector()
        pairs = self.balanced_bad_pairs()
        self.observe_pairs(detector, pairs + pairs[:5])
        snapshot = detector.snapshot()
        self.assertEqual(snapshot.completed_block_count, 1)
        self.assertEqual(snapshot.partial_block_sample_count, 5)
        self.assertEqual(detector.completed_blocks[0].usable_sample_count, 20)

        self.observe_pairs(detector, pairs[5:], first_observation=26)
        blocks = detector.completed_blocks
        self.assertEqual(len(blocks), 2)
        self.assertEqual(
            (blocks[0].first_detector_observation_ordinal, blocks[0].last_detector_observation_ordinal),
            (1, 20),
        )
        self.assertEqual(
            (blocks[1].first_detector_observation_ordinal, blocks[1].last_detector_observation_ordinal),
            (21, 40),
        )

    def test_unknown_hint_is_not_a_usable_observation(self) -> None:
        detector = self.detector()
        detector.observe_resolution(
            LifetimeClassResolution(
                lpn=1,
                birth_sequence=1,
                resolved_sequence=1,
                lifetime_class=LifetimeClass.SHORT,
                host_hint=LifetimeClass.UNKNOWN,
                exact_lifetime_writes=None,
                resolution_reason="test",
            )
        )
        self.assertEqual(detector.snapshot().detector_observation_count, 0)
        self.observe_pairs(detector, self.balanced_bad_pairs())
        self.assertEqual(detector.completed_blocks[0].first_detector_observation_ordinal, 1)

    def test_perfect_positive_and_reversed_are_good(self) -> None:
        for pairs in (self.perfect_positive_pairs(), self.perfect_reversed_pairs()):
            detector = self.detector()
            self.observe_pairs(detector, pairs)
            block = detector.completed_blocks[0]
            self.assertEqual(block.block_state, GOOD)
            self.assertEqual(block.absolute_phi, 1.0)

    def test_phi_equality_is_good_and_lower_phi_is_bad(self) -> None:
        equality = self.detector(threshold=1.0)
        self.observe_pairs(equality, self.perfect_positive_pairs())
        self.assertEqual(equality.completed_blocks[0].block_state, GOOD)

        lower = self.detector()
        self.observe_pairs(lower, self.balanced_bad_pairs())
        self.assertEqual(lower.completed_blocks[0].block_state, BAD)
        self.assertLess(lower.completed_blocks[0].absolute_phi, 0.29980003993028376)

    def test_k_minus_one_bad_does_not_trigger_and_kth_does(self) -> None:
        detector = self.detector(persistence_k=2)
        self.observe_pairs(detector, self.balanced_bad_pairs())
        self.assertFalse(detector.triggered)
        self.observe_pairs(detector, self.balanced_bad_pairs(), first_observation=21)
        self.assertTrue(detector.triggered)
        snapshot = detector.snapshot()
        self.assertEqual(snapshot.trigger_block_index, 2)
        self.assertEqual(snapshot.trigger_detector_observation_ordinal, 40)
        self.assertEqual(snapshot.trigger_host_write_index, 40)
        self.assertEqual(snapshot.trigger_bad_streak, 2)
        self.assertEqual(snapshot.trigger_block_absolute_phi, 0.0)

    def test_good_and_invalid_blocks_reset_bad_streak(self) -> None:
        detector = self.detector(persistence_k=2)
        self.observe_pairs(detector, self.balanced_bad_pairs())
        self.observe_pairs(detector, self.perfect_positive_pairs(), first_observation=21)
        self.assertEqual(detector.completed_blocks[-1].block_state, GOOD)
        self.assertEqual(detector.completed_blocks[-1].bad_streak_after, 0)
        self.observe_pairs(detector, self.balanced_bad_pairs(), first_observation=41)
        self.assertFalse(detector.triggered)

        invalid = [(True, True)] * 20
        self.observe_pairs(detector, invalid, first_observation=61)
        self.assertEqual(detector.completed_blocks[-1].block_state, INVALID)
        self.assertEqual(detector.completed_blocks[-1].bad_streak_after, 0)
        self.observe_pairs(detector, self.balanced_bad_pairs(), first_observation=81)
        self.assertFalse(detector.triggered)
        self.observe_pairs(detector, self.balanced_bad_pairs(), first_observation=101)
        self.assertTrue(detector.triggered)

    def test_denominator_and_marginal_support_failures_are_invalid(self) -> None:
        denominator_zero = self.detector()
        self.observe_pairs(denominator_zero, [(True, True)] * 20)
        block = denominator_zero.completed_blocks[0]
        self.assertEqual(block.block_state, INVALID)
        self.assertIn("phi_denominator_zero", block.invalid_reason)

        true_support_low = self.detector()
        pairs = [(True, True), (True, False)] * 2 + [(False, True), (False, False)] * 8
        self.observe_pairs(true_support_low, pairs)
        block = true_support_low.completed_blocks[0]
        self.assertEqual(block.block_state, INVALID)
        self.assertIn("true_short_support_below_minimum", block.invalid_reason)

        hint_support_low = self.detector()
        pairs = [(True, True), (False, True)] * 2 + [(True, False), (False, False)] * 8
        self.observe_pairs(hint_support_low, pairs)
        block = hint_support_low.completed_blocks[0]
        self.assertEqual(block.block_state, INVALID)
        self.assertIn("hint_short_support_below_minimum", block.invalid_reason)

    def test_host_write_and_detector_observation_ordinals_are_distinct(self) -> None:
        detector = self.detector(persistence_k=1)
        self.observe_pairs(detector, self.balanced_bad_pairs(), host_write=77)
        block = detector.completed_blocks[0]
        snapshot = detector.snapshot()
        self.assertEqual(block.last_detector_observation_ordinal, 20)
        self.assertEqual(block.block_end_host_write_index, 77)
        self.assertEqual(snapshot.trigger_detector_observation_ordinal, 20)
        self.assertEqual(snapshot.trigger_host_write_index, 77)

    def test_sticky_trigger_survives_later_good_evidence(self) -> None:
        detector = self.detector(persistence_k=1)
        self.observe_pairs(detector, self.balanced_bad_pairs())
        trigger = detector.snapshot()
        self.observe_pairs(detector, self.perfect_positive_pairs(), first_observation=21)
        after = detector.snapshot()
        self.assertTrue(after.triggered)
        self.assertEqual(after.trigger_block_index, trigger.trigger_block_index)
        self.assertEqual(after.trigger_host_write_index, trigger.trigger_host_write_index)


if __name__ == "__main__":
    unittest.main()
