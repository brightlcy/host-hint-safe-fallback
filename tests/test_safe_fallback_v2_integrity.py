import hashlib
import json
import unittest
from pathlib import Path


class SafeFallbackV2IntegrityTest(unittest.TestCase):
    def _assert_frozen_files(self, tag: str, files: tuple[str, ...]) -> None:
        """Check the original tag's bytes without requiring historical tags in this export."""
        root = Path(__file__).resolve().parents[1]
        manifest = json.loads((root / "FROZEN_SOURCE_SHA256.json").read_text())
        reference = manifest[tag]
        self.assertEqual(len(reference["original_commit"]), 40)
        for filename in files:
            with self.subTest(tag=tag, filename=filename):
                actual = hashlib.sha256((root / filename).read_bytes()).hexdigest()
                self.assertEqual(actual, reference["files_sha256"][filename])

    def test_frozen_v1_tag_and_ftl_core_files_are_preserved(self) -> None:
        core_files = (
            "simulator/baseline_ftl.py",
            "simulator/hot_cold_ftl.py",
            "simulator/metadata.py",
            "simulator/gc_policy.py",
        )
        self._assert_frozen_files("safe-fallback-v1-core-20260820", core_files)

    def test_frozen_v2_detector_and_protocol_are_preserved(self) -> None:
        tag = "safe-fallback-v2-protocol-frozen-20260820"
        self._assert_frozen_files(tag, (
                "simulator/lifetime_ftl.py",
                "simulator/persistence_detector.py",
                "simulator/persistence_protocol.py",
        ))

    def test_v1_lifetime_tracker_is_preserved(self) -> None:
        self._assert_frozen_files("safe-fallback-v1-core-20260820", ("simulator/lifetime_tracker.py",))


if __name__ == "__main__":
    unittest.main()
