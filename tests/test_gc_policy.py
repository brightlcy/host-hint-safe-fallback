import unittest

from simulator.config import SSDConfig
from simulator.gc_policy import GreedyGCPolicy, NoVictimBlockError
from simulator.metadata import FTLMetadata


class GreedyGCPolicyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = SSDConfig(
            page_size=4096,
            chips=1,
            blocks_per_chip=4,
            pages_per_block=4,
            gc_threshold_ratio=0.25,
            reserved_gc_blocks=1,
        )
        self.metadata = FTLMetadata(self.config)
        self.policy = GreedyGCPolicy()

    def _program_pages(self, lpns):
        ppns = []
        for lpn in lpns:
            ppn = self.metadata.allocate_free_page()
            self.metadata.register_valid_page(lpn=lpn, ppn=ppn)
            ppns.append(ppn)
        return ppns

    def _close_block(self, first_lpn):
        """Program four sequential LPNs into the next FIFO block."""
        return self._program_pages(range(first_lpn, first_lpn + 4))

    def test_no_invalid_block_raises(self) -> None:
        self._close_block(0)

        with self.assertRaisesRegex(NoVictimBlockError, "no eligible GC victim"):
            self.policy.select_victim_block(self.metadata)

    def test_partially_written_block_is_not_eligible(self) -> None:
        self._program_pages([0, 1])
        self.metadata.invalidate_lpn(0)
        self.metadata.invalidate_lpn(1)

        info = self.metadata.get_block_info(0)
        self.assertEqual(info.invalid_count, 2)
        self.assertEqual(info.free_count, 2)
        self.assertFalse(self.policy.is_eligible(self.metadata, 0))

        with self.assertRaises(NoVictimBlockError):
            self.policy.select_victim_block(self.metadata)

    def test_selects_closed_block_with_most_invalid_pages(self) -> None:
        self._close_block(0)       # block 0
        self._close_block(100)     # block 1

        for lpn in (0, 1):
            self.metadata.invalidate_lpn(lpn)
        for lpn in (100, 101, 102):
            self.metadata.invalidate_lpn(lpn)

        victim = self.policy.select_victim(self.metadata)

        self.assertEqual(victim.block_id, 1)
        self.assertEqual(victim.invalid_pages, 3)
        self.assertEqual(victim.valid_pages, 1)
        self.assertEqual(victim.migration_cost, 1)
        self.assertEqual(victim.reclaimable_pages, 3)
        self.assertAlmostEqual(victim.invalid_ratio, 0.75)

    def test_tie_breaks_by_lower_block_id(self) -> None:
        self._close_block(0)
        self._close_block(100)

        for lpn in (0, 1, 100, 101):
            self.metadata.invalidate_lpn(lpn)

        self.assertEqual(self.policy.select_victim_block(self.metadata), 0)

    def test_tie_breaks_by_lower_erase_count_before_block_id(self) -> None:
        self._close_block(0)
        self._close_block(100)

        for lpn in (0, 1, 100, 101):
            self.metadata.invalidate_lpn(lpn)

        # Artificially make block 0 more worn. The primary invalid-count score
        # stays equal, so block 1 should win the wear-aware tie break.
        self.metadata.get_block_info(0).erase_count = 3
        self.metadata.get_block_info(1).erase_count = 1

        self.assertEqual(self.policy.select_victim_block(self.metadata), 1)

    def test_excluded_block_is_not_selected(self) -> None:
        self._close_block(0)
        self._close_block(100)

        for lpn in (0, 1, 2):
            self.metadata.invalidate_lpn(lpn)
        for lpn in (100, 101):
            self.metadata.invalidate_lpn(lpn)

        victim = self.policy.select_victim_block(
            self.metadata,
            excluded_block_ids={0},
        )
        self.assertEqual(victim, 1)

    def test_reserved_page_makes_block_ineligible(self) -> None:
        self._program_pages([0, 1, 2])
        self.metadata.invalidate_lpn(0)
        reserved_ppn = self.metadata.allocate_free_page()

        info = self.metadata.get_block_info(0)
        self.assertEqual(info.free_count, 0)
        self.assertEqual(info.reserved_count, 1)
        self.assertFalse(self.policy.is_eligible(self.metadata, 0))

        self.metadata.release_reserved_page(reserved_ppn)
        self.metadata.assert_consistent()

    def test_candidates_are_stable_snapshots_ordered_by_block_id(self) -> None:
        self._close_block(0)
        self._close_block(100)
        self.metadata.invalidate_lpn(0)
        self.metadata.invalidate_lpn(100)

        candidates = self.policy.get_candidates(self.metadata)

        self.assertEqual([candidate.block_id for candidate in candidates], [0, 1])
        self.assertEqual([candidate.invalid_pages for candidate in candidates], [1, 1])
        self.assertEqual([candidate.free_pages for candidate in candidates], [0, 0])

    def test_optional_open_block_mode_is_explicit(self) -> None:
        policy = GreedyGCPolicy(require_closed_block=False)
        self._program_pages([0, 1])
        self.metadata.invalidate_lpn(0)

        self.assertTrue(policy.is_eligible(self.metadata, 0))
        self.assertEqual(policy.select_victim_block(self.metadata), 0)


if __name__ == "__main__":
    unittest.main()
