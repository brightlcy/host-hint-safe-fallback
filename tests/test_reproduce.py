"""Safety and evidence checks for the research reproduction entry point."""
import importlib.util
import io
import json
from pathlib import Path
import stat
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('portfolio_reproduce', ROOT / 'reproduce.py')
reproduce = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reproduce)


class ReproductionSafetyTest(unittest.TestCase):
    def test_selective_extraction_cannot_overwrite_existing_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'results').mkdir()
            target = root / 'results/trace.csv'
            target.write_bytes(b'preserved original')
            payload = io.BytesIO()
            with zipfile.ZipFile(payload, 'w') as archive:
                archive.writestr('results/trace.csv', b'replacement')
            with zipfile.ZipFile(payload) as archive:
                with self.assertRaises(FileExistsError):
                    reproduce.safe_extract_member(archive, 'results/trace.csv', root)
            self.assertEqual(target.read_bytes(), b'preserved original')

    def test_archive_traversal_and_symlinks_cannot_write_outside_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = io.BytesIO()
            with zipfile.ZipFile(payload, 'w') as archive:
                for name in ('../outside.csv', '/absolute.csv', 'folder\\outside.csv'):
                    archive.writestr(name, b'bad')
                link = zipfile.ZipInfo('results/link')
                link.create_system = 3
                link.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(link, '../../outside.csv')
            with zipfile.ZipFile(payload) as archive:
                for member in archive.namelist():
                    with self.subTest(member=member), self.assertRaises(ValueError):
                        reproduce.safe_extract_member(archive, member, root)
            self.assertEqual(list(root.iterdir()), [])

    def test_extraction_is_allowlisted_by_caller(self):
        with tempfile.TemporaryDirectory() as temporary:
            payload = io.BytesIO()
            with zipfile.ZipFile(payload, 'w') as archive:
                archive.writestr('selected.csv', b'needed trace')
                archive.writestr('unselected.csv', b'unrequested payload')
            with zipfile.ZipFile(payload) as archive:
                selected = reproduce.safe_extract_member(archive, 'selected.csv', temporary)
            self.assertEqual(selected.read_bytes(), b'needed trace')
            self.assertFalse((Path(temporary) / 'unselected.csv').exists())

    def test_comparison_rejects_missing_counter_and_numerical_drift(self):
        expected = {'baseline': {'waf': 2.25, 'migrations': 5}, 'policy': {'future_information_used': False}}
        self.assertEqual(reproduce.compare_metrics(expected, expected), [])
        changed = json.loads(json.dumps(expected))
        changed['baseline']['waf'] = 2.35
        del changed['baseline']['migrations']
        changed['policy']['future_information_used'] = True
        self.assertEqual(len(reproduce.compare_metrics(expected, changed)), 3)
        self.assertTrue(reproduce.compare_metrics({'future_information_used': False}, {'future_information_used': 0}))
        self.assertEqual(reproduce.compare_csv_bytes(b'value\n1.0\n', b'value\n1\n')['difference_count'], 0)
        self.assertGreater(reproduce.compare_csv_bytes(b'value\n1\n2\n', b'value\n1\n')['difference_count'], 0)

    def test_smoke_is_new_evidence_with_distinct_seed_split(self):
        config = reproduce.load_json(ROOT / 'configs/smoke.json')
        self.assertEqual(config['evidence_class'], 'NEW_SMOKE')
        if 'calibration_seeds' in config:
            self.assertTrue(all(seed >= 901 for seed in config['calibration_seeds'] + config['measurement_seeds']))
            self.assertFalse(set(config['calibration_seeds']) & set(config['measurement_seeds']))


    def test_host_smoke_rejects_overlap_empty_and_historical_seeds(self):
        reproduce.validate_host_smoke_seeds({'calibration_seeds': [901], 'measurement_seeds': [902]})
        invalid = [([901], [901]), ([], [902]), ([901], []), ([1], [902]), ([901], [301])]
        for calibration, measurement in invalid:
            with self.subTest(calibration=calibration, measurement=measurement), self.assertRaises(ValueError):
                reproduce.validate_host_smoke_seeds({'calibration_seeds': calibration, 'measurement_seeds': measurement})

    def test_historical_config_cannot_mislabel_frozen_dimensions(self):
        frozen = {'total_writes': 50000, 'logical_pages': 2048, 'workload_seeds': [301, 302, 303, 304, 305],
                  'selected_block_size': 512, 'selected_persistence_k': 2, 'git_commit_hash': 'original-head',
                  'frozen_selection_sha256': 'selection-checksum', 'frozen_protocol_tag': 'protocol-tag',
                  'frozen_protocol_commit': 'protocol-commit', 'frozen_selection_tag': 'selection-tag',
                  'frozen_selection_commit': 'selection-commit'}
        config = {'total_writes': 50000, 'logical_pages': 2048, 'workload_seeds': [301, 302, 303, 304, 305],
                  'block_size': 512, 'persistence_k': 2, 'expected_head': 'original-head',
                  'selection_sha256': 'selection-checksum',
                  'expected_tags': {'protocol-tag': 'protocol-commit', 'selection-tag': 'selection-commit'}}
        reproduce.validate_historical_final_config(config, frozen)
        for field, invalid in [('total_writes', 1), ('logical_pages', 256), ('workload_seeds', [901]), ('block_size', 128), ('persistence_k', 3)]:
            changed = {**config, field: invalid}
            with self.subTest(field=field), self.assertRaises(ValueError):
                reproduce.validate_historical_final_config(changed, frozen)

if __name__ == '__main__':
    unittest.main()
