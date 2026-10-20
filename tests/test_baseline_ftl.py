import unittest
from pathlib import Path

from simulator.baseline_ftl import BaselineFTL
from simulator.config import SSDConfig
from simulator.gc_policy import NoVictimBlockError
from simulator.metadata import PageState


class BaselineFTLTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = SSDConfig(
            page_size=4096,
            chips=1,
            blocks_per_chip=4,
            pages_per_block=4,
            gc_threshold_ratio=0.25,
            reserved_gc_blocks=1,
        )
        self.ftl = BaselineFTL(self.config, debug_check=True)

    def test_initial_summary_is_zero(self) -> None:
        summary = self.ftl.get_summary()
        self.assertEqual(summary["host_write_pages"], 0)
        self.assertEqual(summary["nand_write_pages"], 0)
        self.assertEqual(summary["gc_count"], 0)
        self.assertEqual(summary["write_amplification_factor"], 0.0)
        self.assertEqual(summary["free_pages"], 16)

    def test_write_lpn_creates_mapping_and_counts_nand_write(self) -> None:
        ppn = self.ftl.write_lpn(7)

        self.assertEqual(ppn, 0)
        self.assertEqual(self.ftl.metadata.mapping_table[7], 0)
        self.assertEqual(self.ftl.metadata.get_page_state(0), PageState.VALID)
        self.assertEqual(self.ftl.host_write_pages, 1)
        self.assertEqual(self.ftl.nand_write_pages, 1)
        self.assertEqual(self.ftl.write_amplification_factor, 1.0)

    def test_overwrite_invalidates_old_page_without_gc_when_space_is_high(self) -> None:
        old_ppn = self.ftl.write_lpn(5)
        new_ppn = self.ftl.write_lpn(5)

        self.assertEqual(old_ppn, 0)
        self.assertEqual(new_ppn, 1)
        self.assertEqual(self.ftl.metadata.get_page_state(old_ppn), PageState.INVALID)
        self.assertEqual(self.ftl.metadata.get_page_state(new_ppn), PageState.VALID)
        self.assertEqual(self.ftl.host_write_pages, 2)
        self.assertEqual(self.ftl.nand_write_pages, 2)
        self.assertEqual(self.ftl.gc_count, 0)

    def test_process_write_request_splits_pages_and_counts_one_request(self) -> None:
        self.ftl.process_write_request(lba=10, size=3)

        self.assertEqual(self.ftl.host_write_requests, 1)
        self.assertEqual(self.ftl.host_write_pages, 3)
        self.assertEqual(set(self.ftl.metadata.mapping_table), {10, 11, 12})

    def test_read_request_is_counted_without_nand_write(self) -> None:
        self.ftl.process_trace_row(
            {"timestamp": 0.0, "flow_id": "vm0", "op": "R", "lba": 2, "size": 3}
        )

        self.assertEqual(self.ftl.host_read_requests, 1)
        self.assertEqual(self.ftl.host_read_pages, 3)
        self.assertEqual(self.ftl.nand_write_pages, 0)
        self.assertEqual(len(self.ftl.metadata.mapping_table), 0)

    def test_sparse_lpn_ids_are_allowed_with_count_based_capacity(self) -> None:
        config = SSDConfig(
            page_size=4096,
            chips=1,
            blocks_per_chip=4,
            pages_per_block=4,
            gc_threshold_ratio=0.25,
            reserved_gc_blocks=1,
            logical_pages=2,
            capacity_basis="write-working-set",
        )
        ftl = BaselineFTL(config)

        ftl.process_write_request(lba=1_000_000, size=2)

        self.assertEqual(
            set(ftl.metadata.mapping_table),
            {1_000_000, 1_000_001},
        )
        self.assertEqual(ftl.get_summary()["logical_capacity_utilization"], 1.0)

    def test_mapping_count_cannot_exceed_logical_capacity(self) -> None:
        config = SSDConfig(
            page_size=4096,
            chips=1,
            blocks_per_chip=4,
            pages_per_block=4,
            gc_threshold_ratio=0.25,
            reserved_gc_blocks=1,
            logical_pages=2,
            capacity_basis="write-working-set",
        )
        ftl = BaselineFTL(config)
        ftl.write_lpn(100)
        ftl.write_lpn(200)

        with self.assertRaisesRegex(ValueError, "mapping capacity"):
            ftl.write_lpn(300)

        self.assertEqual(ftl.host_write_pages, 2)

    def test_threshold_without_invalid_victim_does_not_block_initial_fill(self) -> None:
        for lpn in range(13):
            self.ftl.write_lpn(lpn)

        self.assertEqual(self.ftl.gc_count, 0)
        self.assertEqual(self.ftl.metadata.free_page_count, 3)
        self.assertEqual(self.ftl.host_write_pages, 13)

    def test_overwrite_at_threshold_runs_gc_and_accounts_migration(self) -> None:
        # Fill three closed blocks. Four pages remain, exactly the threshold.
        for lpn in range(12):
            self.ftl.write_lpn(lpn)

        new_ppn = self.ftl.write_lpn(0)
        summary = self.ftl.get_summary()
        log = self.ftl.gc_log[0]

        self.assertEqual(new_ppn, 15)
        self.assertEqual(summary["host_write_pages"], 13)
        self.assertEqual(summary["valid_page_migrations"], 3)
        self.assertEqual(summary["nand_write_pages"], 16)
        self.assertEqual(summary["gc_count"], 1)
        self.assertEqual(summary["erase_count"], 1)
        self.assertAlmostEqual(summary["write_amplification_factor"], 16 / 13)
        self.assertEqual(summary["mapped_logical_pages"], 12)
        self.assertEqual(summary["free_pages"], 4)

        self.assertEqual(log.victim_block, 0)
        self.assertEqual(log.valid_pages_before, 3)
        self.assertEqual(log.invalid_pages_before, 1)
        self.assertEqual(log.migrated_pages, 3)
        self.assertEqual(log.free_pages_before, 4)
        self.assertEqual(log.free_pages_after, 5)
        self.assertEqual(log.net_reclaimed_pages, 1)
        self.assertEqual(log.victim_erase_count, 1)
        self.ftl.assert_consistent()

    def test_explicit_gc_without_invalid_page_raises(self) -> None:
        for lpn in range(4):
            self.ftl.write_lpn(lpn)

        with self.assertRaises(NoVictimBlockError):
            self.ftl.run_gc()

    def test_run_sample_trace_returns_expected_summary(self) -> None:
        sample = Path(__file__).resolve().parents[1] / "samples" / "sample.standard.csv"
        config = SSDConfig.from_logical_pages(
            logical_pages=1008,
            chips=4,
            pages_per_block=64,
            overprovision_ratio=0.20,
            gc_threshold_ratio=0.05,
            reserved_gc_blocks=2,
        )
        ftl = BaselineFTL(config, debug_check=True)

        summary = ftl.run_trace(sample)

        self.assertEqual(summary["host_write_requests"], 2)
        self.assertEqual(summary["host_write_pages"], 12)
        self.assertEqual(summary["host_read_requests"], 1)
        self.assertEqual(summary["host_read_pages"], 2)
        self.assertEqual(summary["nand_write_pages"], 12)
        self.assertEqual(summary["gc_count"], 0)
        self.assertEqual(summary["mapped_logical_pages"], 12)
        self.assertEqual(summary["write_amplification_factor"], 1.0)

    def test_phase_summary_uses_counter_deltas_without_resetting_state(self) -> None:
        self.ftl.process_write_request(0, 4)
        snapshot = self.ftl.capture_counter_snapshot()

        self.ftl.process_write_request(0, 4)
        phase = self.ftl.get_summary_since(snapshot)
        cumulative = self.ftl.get_summary()

        self.assertEqual(phase["host_write_requests"], 1)
        self.assertEqual(phase["host_write_pages"], 4)
        self.assertEqual(phase["nand_write_pages"], 4)
        self.assertEqual(phase["mapped_logical_pages"], 4)
        self.assertEqual(cumulative["host_write_pages"], 8)
        self.assertEqual(cumulative["mapped_logical_pages"], 4)
        self.ftl.assert_consistent()

    def test_gc_log_since_returns_only_new_phase_entries(self) -> None:
        for lpn in range(12):
            self.ftl.write_lpn(lpn)
        snapshot = self.ftl.capture_counter_snapshot()

        self.ftl.write_lpn(0)
        rows = self.ftl.get_gc_log_since(snapshot)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["victim_block"], 0)
        self.assertEqual(rows[0]["gc_id"], 1)

    def test_run_trace_rounds_replays_without_reset(self) -> None:
        sample = Path(__file__).resolve().parents[1] / "samples" / "sample.standard.csv"
        config = SSDConfig.from_logical_pages(
            logical_pages=12,
            chips=4,
            pages_per_block=64,
            overprovision_ratio=0.20,
            gc_threshold_ratio=0.05,
            reserved_gc_blocks=2,
        )
        ftl = BaselineFTL(config, debug_check=True)

        ftl.run_trace_rounds(sample, 2)
        summary = ftl.get_summary()

        self.assertEqual(summary["host_write_requests"], 4)
        self.assertEqual(summary["host_write_pages"], 24)
        self.assertEqual(summary["mapped_logical_pages"], 12)

    def test_run_trace_rounds_rejects_negative_rounds(self) -> None:
        sample = Path(__file__).resolve().parents[1] / "samples" / "sample.standard.csv"
        with self.assertRaisesRegex(ValueError, "rounds"):
            self.ftl.run_trace_rounds(sample, -1)


if __name__ == "__main__":
    unittest.main()
