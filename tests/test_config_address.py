import unittest

from simulator.address import (
    block_page_range,
    decode_ppn,
    encode_ppn,
    ppn_to_block,
)
from simulator.config import SSDConfig


class SSDConfigTest(unittest.TestCase):
    def test_derived_geometry(self) -> None:
        config = SSDConfig(
            page_size=4096,
            chips=4,
            blocks_per_chip=128,
            pages_per_block=64,
            gc_threshold_ratio=0.05,
            reserved_gc_blocks=2,
        )
        self.assertEqual(config.total_blocks, 512)
        self.assertEqual(config.total_pages, 32768)
        self.assertEqual(config.gc_reserve_pages, 128)
        self.assertEqual(config.gc_threshold_pages, 1639)

    def test_auto_size_is_aligned_and_has_gc_reserve(self) -> None:
        config = SSDConfig.from_logical_pages(
            logical_pages=10_000,
            chips=4,
            pages_per_block=64,
            overprovision_ratio=0.20,
            reserved_gc_blocks=2,
        )
        self.assertGreaterEqual(config.total_pages, 12_500)
        self.assertGreaterEqual(config.total_pages - 10_000, 128)
        self.assertEqual(config.total_blocks % config.chips, 0)


class AddressTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = SSDConfig(
            page_size=4096,
            chips=4,
            blocks_per_chip=8,
            pages_per_block=64,
            gc_threshold_ratio=0.05,
            reserved_gc_blocks=1,
        )

    def test_round_trip(self) -> None:
        samples = [0, 63, 64, 511, 512, self.config.total_pages - 1]
        for ppn in samples:
            with self.subTest(ppn=ppn):
                chip, block, page = decode_ppn(ppn, self.config)
                self.assertEqual(encode_ppn(chip, block, page, self.config), ppn)

    def test_block_helpers(self) -> None:
        self.assertEqual(ppn_to_block(130, self.config), 2)
        self.assertEqual(list(block_page_range(2, self.config))[:2], [128, 129])
        self.assertEqual(list(block_page_range(2, self.config))[-1], 191)

    def test_invalid_ppn_raises(self) -> None:
        with self.assertRaises(ValueError):
            decode_ppn(self.config.total_pages, self.config)


if __name__ == "__main__":
    unittest.main()
