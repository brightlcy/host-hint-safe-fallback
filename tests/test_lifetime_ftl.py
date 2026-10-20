import unittest

from simulator.config import SSDConfig
from simulator.lifetime_ftl import (
    DeviceOnlyLifetimeFTL,
    OracleLifetimeFTL,
    PersistenceFallbackFTL,
    StaticHostGuidedFTL,
    TrustFallbackFTL,
    run_page_write_events,
)
from simulator.lifetime_workload import LifetimeClass


class LifetimeFTLTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = SSDConfig.from_logical_pages(
            logical_pages=16,
            chips=1,
            pages_per_block=4,
            overprovision_ratio=0.25,
            gc_threshold_ratio=0.10,
            reserved_gc_blocks=1,
        )

    def test_static_host_uses_different_frontiers(self) -> None:
        ftl = StaticHostGuidedFTL(self.config, boundary_writes=2)
        ppn_short = ftl.write_lpn_with_hint(0, LifetimeClass.SHORT)
        ppn_long = ftl.write_lpn_with_hint(1, LifetimeClass.LONG)
        self.assertNotEqual(ppn_short // 4, ppn_long // 4)

    def test_oracle_unknown_has_explicit_long_default(self) -> None:
        ftl = OracleLifetimeFTL(self.config)
        ftl.write_lpn_with_true_class(0, LifetimeClass.UNKNOWN)
        self.assertEqual(ftl.current_class_by_lpn[0], LifetimeClass.LONG)

    def test_device_only_learns_short_after_first_overwrite(self) -> None:
        ftl = DeviceOnlyLifetimeFTL(self.config, boundary_writes=2)
        ftl.write_lpn(0)  # no history => long
        self.assertEqual(ftl.current_class_by_lpn[0], LifetimeClass.LONG)
        ftl.write_lpn(0)  # previous lifetime=1; new version predicts short
        self.assertEqual(ftl.current_class_by_lpn[0], LifetimeClass.SHORT)

    def test_trust_fallback_can_trigger_without_relocating_old_data(self) -> None:
        ftl = TrustFallbackFTL(
            self.config,
            boundary_writes=1,
            failure_association_threshold=None,
            detector_window=8,
            detector_min_samples=4,
        )
        ftl.write_lpn_with_hint(0, LifetimeClass.SHORT)
        old_ppn = ftl.metadata.mapping_table[0]
        # Detector behavior is unit-tested separately. Here we verify the FTL's
        # one-way fallback action does not relocate already-valid data.
        ftl.detector.triggered = True
        ftl.detector.trigger_sequence = ftl.lifetime_tracker.sequence
        ftl.write_lpn_with_hint(1, LifetimeClass.SHORT)
        self.assertEqual(ftl.metadata.mapping_table[0], old_ppn)
        self.assertEqual(ftl.device_fallback_write_pages, 1)

    def test_trigger_write_index_is_the_host_write_ordinal(self) -> None:
        ftl = TrustFallbackFTL(
            self.config,
            boundary_writes=1,
            failure_association_threshold=0.99,
            detector_window=4,
            detector_min_samples=4,
        )
        writes = (
            (0, LifetimeClass.SHORT),
            (0, LifetimeClass.LONG),
            (1, LifetimeClass.LONG),
            (1, LifetimeClass.SHORT),
            (2, LifetimeClass.SHORT),
            (2, LifetimeClass.LONG),
        )

        trigger_ordinal = None
        for ordinal, (lpn, hint) in enumerate(writes, start=1):
            ftl.write_lpn_with_hint(lpn, hint)
            if ftl.detector.triggered:
                trigger_ordinal = ordinal
                break

        self.assertEqual(trigger_ordinal, 6)
        self.assertEqual(ftl.lifetime_tracker.sequence, trigger_ordinal)
        self.assertEqual(ftl.host_write_pages, trigger_ordinal)
        summary = ftl.get_summary()
        self.assertEqual(summary["fallback_trigger_sequence"], trigger_ordinal)
        self.assertEqual(summary["fallback_trigger_write_index"], trigger_ordinal)

    def test_gc_preserves_current_version_class(self) -> None:
        ftl = StaticHostGuidedFTL(self.config, boundary_writes=2, debug_check=True)
        # Force repeated updates and GC while alternating classes.
        for i in range(100):
            lpn = i % 8
            hint = LifetimeClass.SHORT if lpn < 4 else LifetimeClass.LONG
            ftl.write_lpn_with_hint(lpn, hint)
        ftl.assert_consistent()
        self.assertGreater(ftl.gc_count, 0)
        for lpn in ftl.metadata.mapping_table:
            self.assertIn(ftl.current_class_by_lpn[lpn], {LifetimeClass.SHORT, LifetimeClass.LONG})

    def test_gc_migration_does_not_create_lifetime_learning_events(self) -> None:
        ftl = StaticHostGuidedFTL(self.config, boundary_writes=2, debug_check=True)
        for i in range(100):
            lpn = i % 8
            hint = LifetimeClass.SHORT if lpn < 4 else LifetimeClass.LONG
            ftl.write_lpn_with_hint(lpn, hint)
        self.assertGreater(ftl.gc_count, 0)
        tracker = ftl.lifetime_tracker
        before = (
            tracker.sequence,
            tracker.resolution_count,
            tracker.completion_count,
        )
        classes_before = dict(ftl.current_class_by_lpn)

        ftl.run_gc()

        self.assertEqual(
            (tracker.sequence, tracker.resolution_count, tracker.completion_count), before
        )
        for lpn in ftl.metadata.mapping_table:
            self.assertEqual(ftl.current_class_by_lpn[lpn], classes_before[lpn])
        ftl.assert_consistent()

    def test_host_guided_paths_do_not_read_offline_truth_fields(self) -> None:
        class HostOnlyEvent:
            lpn = 1
            host_hint = LifetimeClass.SHORT

            @property
            def true_class(self):
                raise AssertionError("Host-guided path read true_class")

            @property
            def exact_lifetime_writes(self):
                raise AssertionError("Host-guided path read exact_lifetime_writes")

        events = [HostOnlyEvent()]
        static = StaticHostGuidedFTL(self.config, boundary_writes=1)
        fallback = TrustFallbackFTL(
            self.config,
            boundary_writes=1,
            failure_association_threshold=None,
            detector_window=4,
            detector_min_samples=4,
        )
        run_page_write_events(static, events)
        run_page_write_events(fallback, events)

    def test_persistence_fallback_applies_on_the_triggering_host_write(self) -> None:
        class TriggerOnFirstResolution:
            triggered = False

            def observe_resolution(self, resolution):
                self.triggered = True
                return True

        ftl = PersistenceFallbackFTL(
            self.config,
            boundary_writes=1,
            failure_association_threshold=0.29980003993028376,
            block_size=20,
            persistence_k=2,
            min_marginal_support=5,
        )
        ftl.detector = TriggerOnFirstResolution()
        ftl.write_lpn_with_hint(0, LifetimeClass.SHORT)
        # This overwrite resolves the first version as SHORT.  The stub models
        # the detector triggering during resolution; the same current write
        # must use the pre-existing device-only predictor rather than LONG hint.
        ftl.write_lpn_with_hint(0, LifetimeClass.LONG)
        self.assertTrue(ftl.detector.triggered)
        self.assertEqual(ftl.current_class_by_lpn[0], LifetimeClass.SHORT)
        self.assertEqual(ftl.device_fallback_write_pages, 1)
        self.assertEqual(ftl.host_guided_write_pages, 1)

    def test_persistence_fallback_is_sticky_after_later_evidence(self) -> None:
        class StickyDetector:
            triggered = False

            def observe_resolution(self, resolution):
                self.triggered = True
                return True

        ftl = PersistenceFallbackFTL(
            self.config,
            boundary_writes=1,
            failure_association_threshold=0.29980003993028376,
            block_size=20,
            persistence_k=2,
            min_marginal_support=5,
        )
        ftl.detector = StickyDetector()
        ftl.write_lpn_with_hint(0, LifetimeClass.SHORT)
        ftl.write_lpn_with_hint(0, LifetimeClass.LONG)
        ftl.write_lpn_with_hint(0, LifetimeClass.LONG)
        self.assertTrue(ftl.detector.triggered)
        self.assertEqual(ftl.device_fallback_write_pages, 2)
        self.assertEqual(ftl.host_guided_write_pages, 1)


if __name__ == "__main__":
    unittest.main()
