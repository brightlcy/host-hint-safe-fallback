import unittest

from simulator.address import ppn_to_block
from simulator.config import SSDConfig
from simulator.hot_cold_ftl import DataTemperature, HotColdFTL, OnlineHotnessTracker


class OnlineHotnessTrackerTest(unittest.TestCase):
    def test_first_write_is_cold_and_second_recent_write_is_hot(self) -> None:
        tracker = OnlineHotnessTracker(hot_threshold=2, window_writes=4)
        self.assertEqual(tracker.observe_write(10), DataTemperature.COLD)
        self.assertEqual(tracker.observe_write(10), DataTemperature.HOT)
        self.assertEqual(tracker.promotions, 1)

    def test_lpn_demotes_when_old_events_leave_window(self) -> None:
        tracker = OnlineHotnessTracker(hot_threshold=2, window_writes=3)
        tracker.observe_write(7)
        tracker.observe_write(7)
        self.assertEqual(tracker.classify(7), DataTemperature.HOT)
        tracker.observe_write(8)
        tracker.observe_write(9)
        self.assertEqual(tracker.classify(7), DataTemperature.COLD)
        self.assertEqual(tracker.demotions, 1)

    def test_invalid_parameters_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "hot_threshold"):
            OnlineHotnessTracker(hot_threshold=1, window_writes=4)
        with self.assertRaisesRegex(ValueError, "window_writes"):
            OnlineHotnessTracker(hot_threshold=4, window_writes=3)


class HotColdFTLTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = SSDConfig(
            page_size=4096,
            chips=1,
            blocks_per_chip=8,
            pages_per_block=4,
            gc_threshold_ratio=0.25,
            reserved_gc_blocks=1,
            logical_pages=12,
            capacity_basis="write-working-set",
        )

    def test_cold_first_write_and_hot_rewrite_use_different_blocks(self) -> None:
        ftl = HotColdFTL(
            self.config,
            hot_threshold=2,
            hot_window_writes=16,
            debug_check=True,
        )
        cold_ppn = ftl.write_lpn(3)
        hot_ppn = ftl.write_lpn(3)

        self.assertNotEqual(
            ppn_to_block(cold_ppn, self.config),
            ppn_to_block(hot_ppn, self.config),
        )
        self.assertEqual(ftl.cold_host_write_pages, 1)
        self.assertEqual(ftl.hot_host_write_pages, 1)
        self.assertEqual(ftl.placement_fallback_pages, 0)
        ftl.assert_consistent()

    def test_phase_summary_accounts_every_host_write_temperature(self) -> None:
        ftl = HotColdFTL(self.config, hot_threshold=2, hot_window_writes=16)
        snapshot = ftl.capture_counter_snapshot()
        ftl.process_write_request(0, 4)
        ftl.process_write_request(0, 4)
        summary = ftl.get_summary_since(snapshot)

        self.assertEqual(summary["host_write_pages"], 8)
        self.assertEqual(summary["cold_host_write_pages"], 4)
        self.assertEqual(summary["hot_host_write_pages"], 4)
        self.assertEqual(
            summary["hot_host_write_pages"] + summary["cold_host_write_pages"],
            summary["host_write_pages"],
        )

    def test_no_future_information_is_needed_for_classification(self) -> None:
        ftl = HotColdFTL(self.config, hot_threshold=3, hot_window_writes=16)
        ftl.write_lpn(1)
        ftl.write_lpn(1)
        self.assertEqual(ftl.hot_host_write_pages, 0)
        self.assertEqual(ftl.cold_host_write_pages, 2)
        ftl.write_lpn(1)
        self.assertEqual(ftl.hot_host_write_pages, 1)


if __name__ == "__main__":
    unittest.main()
