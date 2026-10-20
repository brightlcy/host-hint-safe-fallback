#!/usr/bin/env python3
"""Portable, standard-library validation of preserved FTL research exports.

All execution outputs go to a newly created artifacts/runs directory. Historical
results are read only. This runner does not bypass the frozen final-run guard.
"""
from __future__ import annotations

import argparse
import csv
import io
import hashlib
import json
import math
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest() if hasattr(hashlib, 'file_digest') else _digest_stream(stream)


def _digest_stream(stream):
    value = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b''):
        value.update(block)
    return value.hexdigest()


def load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path, payload):
    Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + '\n', encoding='utf-8')


def git_value(*args):
    try:
        result = subprocess.run(['git', '-C', str(ROOT), *args], text=True, capture_output=True)
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def safe_extract_member(archive, member, destination):
    """Extract one explicitly selected regular member into a new destination."""
    relative = PurePosixPath(member)
    if relative.is_absolute() or '..' in relative.parts or '\\' in member or ':' in member:
        raise ValueError('unsafe archive path: ' + member)
    info = archive.getinfo(member)
    mode = info.external_attr >> 16
    if info.is_dir() or stat.S_ISLNK(mode):
        raise ValueError('archive member must be a regular file: ' + member)
    base = Path(destination).resolve()
    target = base.joinpath(*relative.parts)
    if base not in target.resolve().parents:
        raise ValueError('archive destination escapes extraction root')
    target.parent.mkdir(parents=True, exist_ok=True)
    with archive.open(info) as source, target.open('xb') as output:
        shutil.copyfileobj(source, output)
    return target


def compare_metrics(expected, actual, prefix=''):
    """Compare every metric, reporting missing/extra keys and numeric drift."""
    failures = []
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [{'metric': prefix, 'reason': 'type mismatch'}]
        for key in sorted(set(expected) | set(actual)):
            name = prefix + '.' + key if prefix else key
            if key not in expected or key not in actual:
                failures.append({'metric': name, 'reason': 'missing/extra key'})
            else:
                failures.extend(compare_metrics(expected[key], actual[key], name))
    elif isinstance(expected, (int, float)) and not isinstance(expected, bool):
        if not isinstance(actual, (int, float)) or isinstance(actual, bool) or not math.isclose(expected, actual, rel_tol=1e-12, abs_tol=1e-12):
            failures.append({'metric': prefix, 'expected': expected, 'actual': actual})
    elif type(expected) is not type(actual) or expected != actual:
        failures.append({'metric': prefix, 'expected': expected, 'actual': actual})
    return failures


def verify_reference(reference):
    errors = []
    observed_sources = {str(p.relative_to(ROOT)): digest(p) for p in sorted((ROOT / 'simulator').glob('*.py'))}
    if observed_sources != reference['source_sha256']:
        errors.extend(compare_metrics(reference['source_sha256'], observed_sources, 'source_sha256'))
    archive_path = ROOT / 'results_archive.zip'
    observed_archive = digest(archive_path)
    if observed_archive != reference['archive_sha256']:
        errors.append({'check': 'archive_sha256', 'reason': 'checksum mismatch'})
    with zipfile.ZipFile(archive_path) as archive:
        bad_member = archive.testzip()
        if bad_member:
            errors.append({'check': 'archive_crc', 'member': bad_member})
        observed_members = {n: hashlib.sha256(archive.read(n)).hexdigest() for n in sorted(archive.namelist())}
        if len(archive.namelist()) != len(observed_members):
            errors.append({'check': 'archive_members', 'reason': 'duplicate paths'})
        errors.extend(compare_metrics(reference['archive_members_sha256'], observed_members, 'archive_members_sha256'))
        if reference['project'] == 'host_hint_safe_fallback':
            final = json.loads(archive.read('results/safe_fallback_v2_final_20260820/final_manifest.json'))
            selection = archive.read('results/safe_fallback_v2_development_20260820/frozen_v2_selection.json')
            if hashlib.sha256(selection).hexdigest() != final['frozen_selection_sha256']:
                errors.append({'check': 'frozen_selection', 'reason': 'final reference mismatch'})
        else:
            for device in (4, 49, 740):
                manifest = json.loads(archive.read(f'results/alibaba_compare_device_{device}_t8/baseline/experiment_manifest.json'))
                member = f'results/alibaba_candidate_100k/device_{device}.standard.csv'
                if hashlib.sha256(archive.read(member)).hexdigest() != manifest['input_trace']['sha256']:
                    errors.append({'check': 'historical_trace_checksum', 'device_id': device})
    frozen_path = ROOT / 'FROZEN_SOURCE_SHA256.json'
    frozen_count = 0
    if frozen_path.exists():
        for tag, frozen in load_json(frozen_path).items():
            for name, expected in frozen['files_sha256'].items():
                frozen_count += 1
                if digest(ROOT / name) != expected:
                    errors.append({'check': 'frozen_source', 'tag': tag, 'file': name})
    return {'status': 'PASS' if not errors else 'FAIL', 'errors': errors,
            'source_file_count': len(observed_sources), 'archive_member_count': len(observed_members),
            'frozen_tag_file_checks': frozen_count, 'archive_sha256': observed_archive,
            'reference_commit': reference['reference_commit'],
            'scope': 'Export byte preservation and archive/manifest consistency; no experiment replay in verify.'}


def run_command(command, output_dir, record, name, cwd=ROOT):
    log = output_dir / (name + '.log')
    item = {'argv': command, 'cwd': str(cwd), 'started_at_utc': utc_now(), 'log': log.name}
    record['commands'].append(item)
    write_json(output_dir / 'run_manifest.json', record)
    with log.open('x', encoding='utf-8') as stream:
        result = subprocess.run(command, cwd=cwd, text=True, stdout=stream, stderr=subprocess.STDOUT)
    item.update({'returncode': result.returncode, 'finished_at_utc': utc_now(), 'log_sha256': digest(log)})
    write_json(output_dir / 'run_manifest.json', record)
    print(f'{name}: {"PASS" if result.returncode == 0 else "FAIL"} ({log})', flush=True)
    if result.returncode:
        raise RuntimeError(f'{name} failed with exit code {result.returncode}; see {log}')


def cli_args(parameters):
    values = []
    for name, value in parameters.items():
        values.extend(['--' + name.replace('_', '-'), ','.join(map(str, value)) if isinstance(value, list) else str(value)])
    return values


def validate_host_smoke_seeds(config):
    calibration = config['calibration_seeds']
    measurement = config['measurement_seeds']
    if not calibration or not measurement:
        raise ValueError('smoke calibration and measurement seeds must be nonempty')
    if set(calibration) & set(measurement):
        raise ValueError('smoke calibration and measurement seeds must be disjoint')
    if any(seed < 901 for seed in calibration + measurement):
        raise ValueError('smoke seeds must be separate from historical splits (use 901+)')


def validate_historical_final_config(config, manifest):
    fields = {'total_writes': 'total_writes', 'logical_pages': 'logical_pages',
              'workload_seeds': 'workload_seeds', 'block_size': 'selected_block_size',
              'persistence_k': 'selected_persistence_k', 'expected_head': 'git_commit_hash',
              'selection_sha256': 'frozen_selection_sha256'}
    for configured, frozen in fields.items():
        if config[configured] != manifest[frozen]:
            raise ValueError('historical configuration differs from frozen manifest: ' + configured)
    expected_tags = {manifest['frozen_protocol_tag']: manifest['frozen_protocol_commit'],
                     manifest['frozen_selection_tag']: manifest['frozen_selection_commit']}
    if config['expected_tags'] != expected_tags:
        raise ValueError('historical configuration differs from frozen tags')


def execute_smoke(reference, out, record):
    config = load_json(ROOT / 'configs/smoke.json')
    record['experiment_config'] = config
    record['evidence_class'] = 'NEW_SMOKE'
    if reference['project'] == 'online_hotcold_ftl':
        parameters = dict(config['parameters'])
        parameters.update({'input': str(ROOT / config['input']), 'output_dir': str(out / 'output')})
        record['dataset_sha256'] = digest(ROOT / config['input'])
        module = 'simulator.run_compare_hot_cold'
    else:
        parameters = {k: v for k, v in config.items() if k in ('stage', 'profile', 'calibration_seeds', 'measurement_seeds', 'total_writes', 'logical_pages', 'hint_seed', 'calibration_error_rates', 'measurement_error_rates', 'detector_window')}
        validate_host_smoke_seeds(parameters)
        parameters['output_dir'] = str(out / 'output')
        module = 'simulator.run_lifetime_experiment'
    run_command([sys.executable, '-m', module, *cli_args(parameters)], out, record, 'smoke')
    record['output_sha256'] = {str(p.relative_to(out)): digest(p) for p in sorted((out / 'output').rglob('*')) if p.is_file()}


def inspect_latency_log(path):
    violations = {'negative_queue_delay': 0, 'negative_service_time': 0, 'negative_latency': 0,
                  'latency_below_service': 0, 'completion_before_arrival': 0}
    minimum_gap = 0.0
    count = 0
    with Path(path).open(encoding='utf-8', newline='') as stream:
        for row in csv.DictReader(stream):
            count += 1
            queue, service, latency, arrival, completion = (float(row[k]) for k in ('queue_delay_us', 'service_time_us', 'latency_us', 'arrival_time_us', 'completion_time_us'))
            violations['negative_queue_delay'] += queue < 0
            violations['negative_service_time'] += service < 0
            violations['negative_latency'] += latency < 0
            violations['latency_below_service'] += latency < service
            violations['completion_before_arrival'] += completion < arrival
            minimum_gap = min(minimum_gap, latency - service)
    return {'request_count': count, 'violations': violations,
            'minimum_latency_minus_service_us': minimum_gap, 'request_log_sha256': digest(path)}


def compare_hotcold_output(archive, device_out, device):
    original = f'results/alibaba_compare_device_{device}_t8/comparison_manifest.json'
    expected = json.loads(archive.read(original))
    observed = load_json(device_out / 'comparison_manifest.json')
    metric_sets = ('baseline', 'hot_cold', 'hot_cold_policy')
    differences = compare_metrics({k: expected[k] for k in metric_sets}, {k: observed[k] for k in metric_sets})
    result = {'device_id': device, 'status': 'PASS', 'original_manifest_member': original,
              'original_manifest_sha256': hashlib.sha256(archive.read(original)).hexdigest(),
              'replay_manifest_sha256': digest(device_out / 'comparison_manifest.json'),
              'metric_count': len(expected['baseline']) + len(expected['hot_cold']),
              'differences': differences, 'metrics': {k: observed[k] for k in metric_sets},
              'phase_configuration_checks': {}}
    for policy in ('baseline', 'hot_cold'):
        member = f'results/alibaba_compare_device_{device}_t8/{policy}/experiment_manifest.json'
        frozen_run = json.loads(archive.read(member))
        replay_run = load_json(device_out / policy / 'experiment_manifest.json')
        fields = ('ssd_config', 'parameters', 'phase_plan', 'phase_results', 'trace_stats', 'latency_model')
        errors = compare_metrics({k: frozen_run[k] for k in fields}, {k: replay_run[k] for k in fields}, policy)
        if replay_run['input_trace']['sha256'] != frozen_run['input_trace']['sha256']:
            errors.append({'metric': policy + '.input_trace.sha256', 'reason': 'trace checksum mismatch'})
        result['phase_configuration_checks'][policy] = {'status': 'FAIL' if errors else 'PASS', 'compared_fields': list(fields) + ['input_trace.sha256'], 'differences': errors}
        differences.extend(errors)
    result['latency_diagnostics'] = {policy: inspect_latency_log(device_out / policy / 'request_latency.csv') for policy in ('baseline', 'hot_cold')}
    result['status'] = 'FAIL' if differences else 'PASS'
    return result


def execute_replay(device_ids, out, record):
    config = load_json(ROOT / 'configs/alibaba_t8.json')
    record['evidence_class'] = 'HISTORICAL_REPLAY'
    record['experiment_config'] = config
    record['devices'] = []
    with tempfile.TemporaryDirectory(prefix='hotcold-replay-') as temporary:
        with zipfile.ZipFile(ROOT / 'results_archive.zip') as archive:
            for device in device_ids:
                member = f'results/alibaba_candidate_100k/device_{device}.standard.csv'
                trace = safe_extract_member(archive, member, temporary)
                parameters = {k: v for k, v in config.items() if k in ('page_size', 'chips', 'pages_per_block', 'overprovision_ratio', 'gc_threshold_ratio', 'reserved_gc_blocks', 'capacity_basis', 'warmup_rounds', 'measurement_rounds', 'hot_threshold', 'hot_window_writes')}
                device_out = out / f'device_{device}'
                parameters.update({'input': str(trace), 'output_dir': str(device_out)})
                run_command([sys.executable, '-m', 'simulator.run_compare_hot_cold', *cli_args(parameters)], out, record, f'replay-device-{device}')
                result = compare_hotcold_output(archive, device_out, device)
                result.update({'trace_sha256': digest(trace), 'trace_archive_member': member})
                record['devices'].append(result)
                write_json(out / 'replay_comparison.json', record['devices'])
                if result['status'] != 'PASS':
                    raise RuntimeError(f'Historical numeric/configuration mismatch for device {device}')
    record['blocked'] = [{'dataset': 'MSR prxy_1 100k', 'status': 'BLOCKED', 'reason': 'Input trace is absent from results_archive.zip. Summary/manifest evidence is preserved; no replay claimed.'}]


def finalize_existing_run(run_dir, out, record, report_path=None):
    """Validate completed outputs only. This function never launches a simulator."""
    run_dir = Path(run_dir).expanduser().resolve()
    prior_path = run_dir / 'run_manifest.json'
    prior = load_json(prior_path)
    if prior.get('project') != 'online_hotcold_ftl' or prior.get('action') != 'replay':
        raise ValueError('existing run must be a Hot/Cold historical replay')
    if prior.get('status') != 'PASS':
        raise ValueError('existing replay has not finished successfully; keep the live process running and inspect its manifest first')
    if prior['source_sha256'] != record['actual_source_sha256']:
        raise ValueError('existing replay source differs from the preserved current simulator')
    historical_actual = prior.get('actual_source_sha256')
    if historical_actual is not None and historical_actual != record['actual_source_sha256']:
        raise ValueError('existing replay executed source differs from the preserved current simulator')
    historical_verification = prior.get('verification', {})
    if historical_verification.get('status') != 'PASS' or historical_verification.get('errors'):
        raise ValueError('existing replay lacks a successful preserved-source verification')
    source_evidence = {
        'expected_source_matches_current': True,
        'separate_executed_digest_recorded': historical_actual is not None,
        'historical_preservation_check': 'PASS',
        'interpretation': ('Historical expected and separately recorded executed digests both match current source.'
                           if historical_actual is not None else
                           'Legacy worker records expected source and a passing preservation check, not a separate executed digest.'),
    }
    record['historical_source_evidence'] = source_evidence
    devices = [row['device_id'] for row in prior['devices']]
    if not devices or len(devices) != len(set(devices)) or set(devices) - {4, 49, 740}:
        raise ValueError('existing replay has an invalid device list')
    record.update({'evidence_class': 'HISTORICAL_REPLAY_VALIDATION', 'simulation_executed': False,
                   'source_run_manifest_sha256': digest(prior_path), 'existing_run_dir': str(run_dir), 'devices': []})
    with zipfile.ZipFile(ROOT / 'results_archive.zip') as archive:
        for device in devices:
            record['devices'].append(compare_hotcold_output(archive, run_dir / f'device_{device}', device))
    if any(row['status'] != 'PASS' for row in record['devices']):
        raise RuntimeError('existing replay validation found a metric/configuration mismatch')
    result = {'schema_version': 1, 'project': 'online_hotcold_ftl', 'status': 'PASS',
              'evidence_class': record['evidence_class'], 'simulation_executed': False,
              'validated_at_utc': utc_now(), 'source_run_manifest_sha256': digest(prior_path),
              'existing_run_dir': str(run_dir), 'runner_sha256': digest(Path(__file__)),
              'archive_sha256': record['verification']['archive_sha256'],
              'source_sha256': record['actual_source_sha256'], 'historical_source_evidence': source_evidence, 'devices': record['devices'],
              'blocked': [{'dataset': 'MSR prxy_1 100k', 'status': 'BLOCKED', 'reason': 'Input extract absent.'}]}
    write_json(out / 'existing_run_validation.json', result)
    if report_path is not None:
        target = (ROOT / report_path).resolve()
        allowed = (ROOT / 'results/manifests').resolve()
        if target.parent != allowed or not target.name.startswith('rebuild_') or target.suffix != '.json':
            raise ValueError('--report must be a new results/manifests/rebuild_*.json file')
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('x', encoding='utf-8') as stream:
            stream.write(json.dumps(result, indent=2, sort_keys=True) + '\n')
        record['report'] = str(target)


def compare_csv_bytes(expected, actual):
    expected_rows = list(csv.DictReader(io.StringIO(expected.decode('utf-8'))))
    actual_rows = list(csv.DictReader(io.StringIO(actual.decode('utf-8'))))
    errors = []
    if len(expected_rows) != len(actual_rows):
        errors.append({'reason': 'row_count', 'expected': len(expected_rows), 'actual': len(actual_rows)})
    for index, (before, after) in enumerate(zip(expected_rows, actual_rows)):
        for key in sorted(set(before) | set(after)):
            old, new = before.get(key), after.get(key)
            if old == new:
                continue
            try:
                equal = math.isclose(float(old), float(new), rel_tol=1e-12, abs_tol=1e-12)
            except (ValueError, TypeError):
                equal = False
            if not equal:
                errors.append({'row': index + 2, 'column': key, 'expected': old, 'actual': new})
    return {'row_count': len(actual_rows), 'difference_count': len(errors), 'differences': errors[:20]}


def execute_historical_final(checkout, out, record):
    """Run the unmodified original production runner in its clean Git history."""
    checkout = Path(checkout).expanduser().resolve()
    config = load_json(ROOT / 'configs/historical_final.json')
    with zipfile.ZipFile(ROOT / 'results_archive.zip') as archive:
        frozen_manifest = json.loads(archive.read('results/safe_fallback_v2_final_20260820/final_manifest.json'))
    validate_historical_final_config(config, frozen_manifest)

    def original_git(*args):
        return subprocess.check_output(['git', '-C', str(checkout), *args], text=True, stderr=subprocess.DEVNULL).strip()

    if original_git('rev-parse', 'HEAD') != config['expected_head']:
        raise ValueError('historical checkout must use the original frozen final HEAD')
    if original_git('status', '--porcelain'):
        raise ValueError('historical checkout must be clean before production replay')
    for tag, expected in config['expected_tags'].items():
        if original_git('rev-parse', tag + '^{commit}') != expected:
            raise ValueError('historical frozen tag mismatch: ' + tag)
    selection = checkout / config['selection_file']
    if original_git('ls-files', config['selection_file']) != config['selection_file']:
        raise ValueError('historical frozen selection must be tracked in Git')
    if digest(selection) != config['selection_sha256']:
        raise ValueError('historical frozen selection checksum mismatch')
    original_sources = {str(p.relative_to(checkout)): digest(p) for p in sorted((checkout / 'simulator').glob('*.py'))}
    if original_sources != record['actual_source_sha256']:
        raise ValueError('historical simulator differs from the preserved export')
    record.update({'evidence_class': 'HISTORICAL_REPLAY', 'experiment_config': config,
                   'historical_holdout_reused': True, 'new_generalization_evidence': False,
                   'historical_git_commit_sha': config['expected_head'],
                   'historical_source_sha256': original_sources,
                   'guard': 'Unmodified production runner, clean original Git history and tags; no bypass or test mode.'})
    command = [sys.executable, '-m', 'simulator.run_persistence_final', '--stage', 'final', '--selection-file', config['selection_file'], '--output-dir', str(out / 'output')]
    run_command(command, out, record, 'historical-final', cwd=checkout)
    results = []
    with zipfile.ZipFile(ROOT / 'results_archive.zip') as archive:
        prefix = 'results/safe_fallback_v2_final_20260820/'
        for name in ('final_run_results.csv', 'final_block_results.csv', 'final_condition_outcomes.csv', 'final_seed_summaries.csv', 'final_summary.json'):
            original = archive.read(prefix + name)
            actual = (out / 'output' / name).read_bytes()
            result = {'file': name, 'historical_sha256': hashlib.sha256(original).hexdigest(),
                      'replay_sha256': hashlib.sha256(actual).hexdigest(), 'bytes_equal': original == actual}
            if name.endswith('.csv'):
                result.update(compare_csv_bytes(original, actual))
            else:
                differences = compare_metrics(json.loads(original), json.loads(actual))
                result.update({'difference_count': len(differences), 'differences': differences[:20]})
            results.append(result)
        old_manifest = json.loads(archive.read(prefix + 'final_manifest.json'))
        new_manifest = load_json(out / 'output/final_manifest.json')
        validate_historical_final_config(config, new_manifest)
        metadata_differences = {k: {'historical': old_manifest.get(k), 'replay': new_manifest.get(k)} for k in sorted(set(old_manifest) | set(new_manifest)) if old_manifest.get(k) != new_manifest.get(k)}
        record.update({'output_comparisons': results, 'historical_manifest_differences': metadata_differences})
        if set(metadata_differences) - {'git_branch'}:
            raise RuntimeError('historical final protocol/manifest mismatch')
    if any(result['difference_count'] for result in results):
        raise RuntimeError('historical final result mismatch')
    record['historical_git_worktree_clean_after'] = not original_git('status', '--porcelain')
    if not record['historical_git_worktree_clean_after']:
        raise RuntimeError('historical checkout became dirty')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('tests', 'verify', 'smoke', 'replay', 'finalize'))
    parser.add_argument('--device', choices=('all', '4', '49', '740'), default='all', help='Hot/Cold replay only')
    parser.add_argument('--history-checkout', type=Path, help='Host-Hint historical replay: clean original Git checkout with frozen tags')
    parser.add_argument('--run-dir', type=Path, help='finalize only: completed existing Hot/Cold replay directory')
    parser.add_argument('--report', type=Path, help='finalize only: create a new results/manifests/rebuild_*.json snapshot')
    args = parser.parse_args(argv)
    reference = load_json(ROOT / 'configs/evidence_snapshot.json')
    if args.action == 'finalize' and (reference['project'] != 'online_hotcold_ftl' or not args.run_dir):
        parser.error('finalize requires a Hot/Cold project and --run-dir; it never launches a simulation.')
    if args.action == 'replay' and reference['project'] == 'host_hint_safe_fallback' and not args.history_checkout:
        parser.error('Host-Hint replay requires --history-checkout with separately restored original Git history; see docs/REPRODUCIBILITY.md. Production guard remains unchanged.')
    out = ROOT / 'artifacts/runs' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + args.action + '-' + uuid.uuid4().hex[:8])
    out.mkdir(parents=True, exist_ok=False)
    record = {'schema_version': 1, 'experiment_id': out.name, 'project': reference['project'], 'action': args.action, 'status': 'RUNNING', 'started_at_utc': utc_now(), 'python_version': platform.python_version(), 'python_executable': sys.executable, 'platform': platform.platform(), 'git_commit_sha': git_value('rev-parse', 'HEAD'), 'git_branch': git_value('branch', '--show-current'), 'git_worktree_status': git_value('status', '--porcelain'), 'commands': [], 'source_sha256': reference['source_sha256'], 'actual_source_sha256': {str(p.relative_to(ROOT)): digest(p) for p in sorted((ROOT / 'simulator').glob('*.py'))}, 'source_hash_semantics': 'source_sha256 is the expected preserved export; actual_source_sha256 records executed bytes.', 'config_sha256': {str(p.relative_to(ROOT)): digest(p) for p in sorted((ROOT / 'configs').glob('*.json'))}, 'runner_sha256': digest(Path(__file__)), 'reference_manifest_sha256': digest(ROOT / 'configs/evidence_snapshot.json')}
    manifest = out / 'run_manifest.json'
    write_json(manifest, record)
    try:
        record['verification'] = verify_reference(reference)
        if record['verification']['status'] != 'PASS':
            raise RuntimeError('Preserved source/archive verification failed')
        if args.action == 'tests':
            run_command([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'], out, record, 'tests')
            modules = ['simulator.run_baseline', 'simulator.run_compare_hot_cold']
            if reference['project'] == 'host_hint_safe_fallback':
                modules += ['simulator.run_lifetime_experiment', 'simulator.run_persistence_development', 'simulator.run_persistence_final']
            for module in modules:
                run_command([sys.executable, '-m', module, '--help'], out, record, 'help-' + module.rsplit('.', 1)[-1])
        elif args.action == 'smoke':
            execute_smoke(reference, out, record)
        elif args.action == 'finalize':
            finalize_existing_run(args.run_dir, out, record, args.report)
        elif args.action == 'replay':
            if reference['project'] == 'host_hint_safe_fallback':
                execute_historical_final(args.history_checkout, out, record)
            else:
                execute_replay([4, 49, 740] if args.device == 'all' else [int(args.device)], out, record)
        record['status'] = 'PASS'
    except Exception as error:
        record['status'] = 'FAIL'
        record['error'] = f'{type(error).__name__}: {error}'
        print(record['error'], file=sys.stderr)
    finally:
        record['finished_at_utc'] = utc_now()
        write_json(manifest, record)
        print(f'{record["status"]}: {manifest}', flush=True)
    return 0 if record['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
