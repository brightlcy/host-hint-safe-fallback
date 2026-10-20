import unittest

from simulator.config import SSDConfig
from simulator.metadata import (
    FTLMetadata,
    MetadataStateError,
    NoFreePageError,
    PageState,
)


class FTLMetadataTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = SSDConfig(
            page_size=4096,
            chips=1,
            blocks_per_chip=2,
            pages_per_block=4,
            gc_threshold_ratio=0.25,
            reserved_gc_blocks=1,
        )
        self.metadata = FTLMetadata(self.config)

    def test_initial_state_all_pages_are_free(self) -> None:
        self.assertEqual(self.metadata.free_page_count, 8)
        self.assertEqual(self.metadata.valid_page_count, 0)
        self.assertEqual(self.metadata.invalid_page_count, 0)
        self.assertEqual(self.metadata.reserved_page_count, 0)

        for ppn in range(self.config.total_pages):
            self.assertEqual(self.metadata.get_page_state(ppn), PageState.FREE)

        for info in self.metadata.block_info:
            self.assertEqual(info.free_count, 4)
            self.assertEqual(info.valid_count, 0)
            self.assertEqual(info.invalid_count, 0)
            self.assertEqual(info.reserved_count, 0)

        self.metadata.assert_consistent()

    def test_allocate_reserves_fifo_page_and_updates_block(self) -> None:
        ppn = self.metadata.allocate_free_page()

        self.assertEqual(ppn, 0)
        self.assertEqual(self.metadata.get_page_state(ppn), PageState.RESERVED)
        self.assertEqual(self.metadata.free_page_count, 7)
        self.assertEqual(self.metadata.reserved_page_count, 1)
        self.assertEqual(self.metadata.block_info[0].free_count, 3)
        self.assertEqual(self.metadata.block_info[0].reserved_count, 1)
        self.metadata.assert_consistent()

    def test_register_valid_page_creates_bidirectional_mapping(self) -> None:
        ppn = self.metadata.allocate_free_page()
        self.metadata.register_valid_page(lpn=10, ppn=ppn)

        self.assertEqual(self.metadata.mapping_table[10], ppn)
        self.assertEqual(self.metadata.page_owner[ppn], 10)
        self.assertEqual(self.metadata.get_page_state(ppn), PageState.VALID)
        self.assertEqual(self.metadata.block_info[0].valid_count, 1)
        self.assertEqual(self.metadata.block_info[0].reserved_count, 0)
        self.metadata.assert_consistent()

    def test_overwrite_transition_makes_old_page_invalid(self) -> None:
        old_ppn = self.metadata.allocate_free_page()
        self.metadata.register_valid_page(lpn=7, ppn=old_ppn)

        returned_lpn = self.metadata.invalidate_old_page(old_ppn)
        new_ppn = self.metadata.allocate_free_page()
        self.metadata.register_valid_page(lpn=7, ppn=new_ppn)

        self.assertEqual(returned_lpn, 7)
        self.assertEqual(self.metadata.get_page_state(old_ppn), PageState.INVALID)
        self.assertEqual(self.metadata.get_page_state(new_ppn), PageState.VALID)
        self.assertEqual(self.metadata.mapping_table[7], new_ppn)
        self.assertEqual(self.metadata.block_info[0].valid_count, 1)
        self.assertEqual(self.metadata.block_info[0].invalid_count, 1)
        self.assertEqual(self.metadata.block_info[0].free_count, 2)
        self.metadata.assert_consistent()

    def test_register_requires_explicit_old_page_invalidation(self) -> None:
        first_ppn = self.metadata.allocate_free_page()
        self.metadata.register_valid_page(lpn=3, ppn=first_ppn)
        second_ppn = self.metadata.allocate_free_page()

        with self.assertRaisesRegex(MetadataStateError, "already mapped"):
            self.metadata.register_valid_page(lpn=3, ppn=second_ppn)

        # Roll back the failed reservation so invariants remain clean.
        self.metadata.release_reserved_page(second_ppn)
        self.metadata.assert_consistent()

    def test_get_valid_pages_in_block(self) -> None:
        for lpn in (100, 101, 102):
            ppn = self.metadata.allocate_free_page()
            self.metadata.register_valid_page(lpn, ppn)

        self.assertEqual(
            self.metadata.get_valid_pages_in_block(0),
            [(0, 100), (1, 101), (2, 102)],
        )
        self.assertEqual(self.metadata.get_valid_pages_in_block(1), [])

    def test_erase_rejects_block_with_valid_page(self) -> None:
        ppn = self.metadata.allocate_free_page()
        self.metadata.register_valid_page(lpn=1, ppn=ppn)

        with self.assertRaisesRegex(MetadataStateError, "valid page"):
            self.metadata.erase_block(0)

    def test_erase_returns_invalid_and_free_pages_without_duplicates(self) -> None:
        ppn0 = self.metadata.allocate_free_page()
        self.metadata.register_valid_page(lpn=10, ppn=ppn0)
        ppn1 = self.metadata.allocate_free_page()
        self.metadata.register_valid_page(lpn=11, ppn=ppn1)
        self.metadata.invalidate_lpn(10)
        self.metadata.invalidate_lpn(11)

        self.assertEqual(self.metadata.block_info[0].invalid_count, 2)
        self.metadata.erase_block(0)

        info = self.metadata.block_info[0]
        self.assertEqual(info.valid_count, 0)
        self.assertEqual(info.invalid_count, 0)
        self.assertEqual(info.free_count, 4)
        self.assertEqual(info.erase_count, 1)
        for ppn in range(4):
            self.assertEqual(self.metadata.get_page_state(ppn), PageState.FREE)

        self.assertEqual(len(self.metadata.free_page_queue), 8)
        self.assertEqual(len(set(self.metadata.free_page_queue)), 8)
        self.metadata.assert_consistent()

    def test_release_reserved_page_restores_free_pool(self) -> None:
        ppn = self.metadata.allocate_free_page()
        self.metadata.release_reserved_page(ppn)

        self.assertEqual(self.metadata.get_page_state(ppn), PageState.FREE)
        self.assertEqual(self.metadata.free_page_count, 8)
        self.assertEqual(self.metadata.reserved_page_count, 0)
        self.metadata.assert_consistent()

    def test_no_free_page_raises(self) -> None:
        allocated = [
            self.metadata.allocate_free_page()
            for _ in range(self.config.total_pages)
        ]
        self.assertEqual(len(allocated), 8)

        with self.assertRaises(NoFreePageError):
            self.metadata.allocate_free_page()

        for ppn in allocated:
            self.metadata.release_reserved_page(ppn)
        self.metadata.assert_consistent()


if __name__ == "__main__":
    unittest.main()
