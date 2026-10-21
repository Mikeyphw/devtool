"""R09 current-source cross-campaign acceptance inventory.

Read-only by default. Locally hashed receipts are observations, NOT independent
hosted/Android/security/release authority. Neither JSON claims nor a successful
local test run can promote an external gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any

from devtool.rmp_r08_assessment import assess as assess_security
from devtool.termux_toolchains import detect_runtime_identity

CONTRACT = 'docs/actions/DEVTOOL_R09_ACCEPTANCE_CONTRACT_20261021.json'
MAX_EVIDENCE_BYTES = 1024 * 1024
MAX_EVIDENCE_FILES = 64
LANES = (
    'tests/test_devtool_r01_foundation.py',
    'tests/test_devtool_r03_scoped_closure.py',
    'tests/test_actions_r03_android_roles.py',
    'tests/test_actions_r03_android_pm_presence.py',
    'tests/test_actions_r04_lineage.py',
    'tests/test_actions_r04b_lineage_authority.py',
    'tests/test_actions_r05_operator.py',
    'tests/test_workflow_r06_plan_views.py',
    'tests/test_r06b_adoption_overlay_plan.py',
    'tests/test_federation_r06c_dag_contract.py',
    'tests/test_overlay_r06d_recovery_plan.py',
    'tests/test_ssh_r07a_qualification_returns.py',
    'tests/test_ssh_r07_consolidated_reliability.py',
    'tests/test_rmp_r08_assessment.py',
    'tests/test_actions_a09_ci_host_contract.py',
    'tests/test_core_x04_generated_surfaces.py',
    'tests/test_rmp_r09_campaign.py',
)
EXTERNAL = {
    'hosted': (
        'CI24_24_SHARDS_CURRENT_SOURCE', 'G3R_NATIVE_13_LANES',
        'FS03_CURRENT_SOURCE', 'A09_SECOND_CONSUMER_SKYBIND_GO',
        'ACTIONS_WORKFLOW_RUN_ATTEMPT_ARTIFACT',
    ),
    'consumer': (
        'SKYBIND', 'CINDERBOX', 'BKX', 'CHROOTCTL', 'OTHER_CONFIGURED_CONSUMERS',
    ),
    'device': (
        'REAL_ANDROID_INSTRUMENTATION', 'INSTALLED_PAIR_PROVENANCE',
        'TRUSTED_ROOT_BROKER_IDENTITY', 'INDEPENDENT_SSH_WORKER',
        'ANDROID_NOTIFICATION_DELIVERY',
    ),
    'security': (
        'N01', 'N02', 'C04', 'C05', 'C06', 'Q01', 'Q02', 'U01',
        'ENV-A06', 'ANDROID-A16', 'DIST-A01', 'DIST-A12', 'DIST-A13', 'TEST-A11',
    ),
    'release': ('INDEPENDENT_RELEASE_OWNER_APPROVAL',),
}
SHA40 = re.compile(r'[0-9a-f]{40}\Z')
SHA64 = re.compile(r'[0-9a-f]{64}\Z')


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ['git', '-c', f'safe.directory={repo}', '-C', str(repo), *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False,
    )
    if completed.returncode:
        raise ValueError('Git source check failed: ' + ' '.join(args))
    return completed.stdout.decode('utf-8', errors='strict').strip()


def _safe_file(path: Path, *, limit: int = MAX_EVIDENCE_BYTES) -> bytes:
    """Never follow a symlink in the supplied file or any existing parent."""
    path = path.expanduser().absolute()
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError('Symlinked evidence path refused')
    if not path.is_file():
        raise ValueError('Evidence must be a regular file')
    if path.stat().st_size > limit:
        raise ValueError('Evidence exceeds size limit')
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(path, flags)
    try:
        with os.fdopen(descriptor, 'rb') as stream:
            data = stream.read(limit + 1)
    except BaseException:
        # fdopen closes the descriptor even when reading fails.
        raise
    if len(data) > limit:
        raise ValueError('Evidence exceeds size limit')
    return data


def _evidence_record(path: Path, expected_tree: str, expected_commit: str) -> dict[str, Any]:
    """Hash bytes and assess shape. No receipt controls any qualification flag."""
    label = path.name[:120]
    try:
        data = _safe_file(path)
        decoded = json.loads(data)
        if not isinstance(decoded, dict):
            raise ValueError('JSON receipt must be an object')
        claimed_tree = decoded.get('source_tree')
        claimed_commit = decoded.get('source_commit')
        if claimed_tree is None or claimed_commit is None:
            identity = 'MISSING_EXACT_SOURCE_IDENTITY'
        elif not isinstance(claimed_tree, str) or not isinstance(claimed_commit, str) or not SHA40.fullmatch(claimed_tree) or not SHA40.fullmatch(claimed_commit):
            identity = 'INVALID_SOURCE_IDENTITY'
        elif claimed_tree == expected_tree and claimed_commit == expected_commit:
            identity = 'MATCH_CURRENT_SOURCE'
        else:
            identity = 'FOREIGN_SOURCE'
        producer = decoded.get('producer')
        return {
            'file_name': label, 'sha256': _hash(data), 'bytes': len(data),
            'format': 'VALID_JSON_OBJECT', 'source_identity': identity,
            'producer': producer if isinstance(producer, str) and re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', producer) else 'UNDECLARED',
            'authority': 'OPERATOR_SUPPLIED_UNTRUSTED', 'qualified': False,
        }
    except (OSError, ValueError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
        return {'file_name': label, 'format': 'REFUSED',
                'reason_code': type(exc).__name__, 'authority': 'NONE', 'qualified': False}


def _evidence_paths(paths: tuple[Path, ...], directories: tuple[Path, ...]) -> list[Path]:
    found = list(paths)
    for directory in directories:
        directory = directory.expanduser().absolute()
        if any(part.is_symlink() for part in (directory, *directory.parents)):
            raise ValueError('Symlinked evidence directory refused')
        if not directory.is_dir():
            raise ValueError('Evidence directory is missing or not a directory')
        # No recursive walk; bounded by directory entries, not just number of JSON files.
        entries = list(directory.iterdir())
        if len(entries) > MAX_EVIDENCE_FILES:
            raise ValueError('Evidence directory has too many entries')
        for entry in sorted(entries):
            if entry.is_symlink():
                raise ValueError('Symlinked evidence entry refused')
            if entry.suffix == '.json':
                found.append(entry)
    if len(found) > MAX_EVIDENCE_FILES:
        raise ValueError('Too many evidence files')
    return found


def _selected_tests(repo: Path, *, full: bool, timeout: int) -> dict[str, Any]:
    argv = [sys.executable, '-m', 'pytest', '-p', 'no:cacheprovider', '-q',
            '--disable-warnings', '--maxfail=1', *(('tests',) if full else LANES)]
    try:
        result = subprocess.run(argv, cwd=repo, capture_output=True, timeout=max(timeout, 1),
                                check=False, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1',
                                                  'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'})
        match = re.search(rb'(\d+) passed', result.stdout)
        fail = re.search(rb'(\d+) failed', result.stdout)
        skip = re.search(rb'(\d+) skipped', result.stdout)
        return {'status': 'PASS' if result.returncode == 0 and match else 'FAILED',
                'passed': int(match.group(1)) if match else 0,
                'failed': int(fail.group(1)) if fail else (1 if result.returncode else 0),
                'skipped': int(skip.group(1)) if skip else 0,
                'exit_code': result.returncode, 'output_sha256': _hash(result.stdout + result.stderr),
                'suite': 'FULL_PYTEST' if full else 'CROSS_CAMPAIGN_FOCUSED',
                'selected_lane_count': 0 if full else len(LANES)}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {'status': 'FAILED', 'reason_code': type(exc).__name__,
                'suite': 'FULL_PYTEST' if full else 'CROSS_CAMPAIGN_FOCUSED',
                'selected_lane_count': 0 if full else len(LANES)}


def _native_smoke() -> dict[str, Any]:
    runtime = detect_runtime_identity()
    native = runtime.platform == 'android' and runtime.environment == 'termux'
    outcome: dict[str, Any] = {'substrate': 'NATIVE_TERMUX' if native else 'NON_NATIVE_OR_UNKNOWN',
                                'runtime': runtime.environment, 'native_confirmed': native,
                                'installed_cli': 'NOT_CHECKED'}
    if native:
        cli = shutil.which('devtool')
        if cli:
            try:
                result = subprocess.run([cli, '--help'], capture_output=True, timeout=90, check=False)
                outcome['installed_cli'] = 'HELP_PASS' if result.returncode == 0 else 'HELP_FAILED'
                outcome['help_sha256'] = _hash(result.stdout + result.stderr)
            except (OSError, subprocess.TimeoutExpired):
                outcome['installed_cli'] = 'HELP_FAILED'
        else:
            outcome['installed_cli'] = 'CLI_MISSING'
    return outcome


def assess(repo: Path, *, run_tests: bool = False, full_tests: bool = False,
           timeout: int = 1200, evidence: tuple[Path, ...] = (),
           evidence_dirs: tuple[Path, ...] = ()) -> dict[str, Any]:
    """Assess only locally observable facts. External authority is *always* pending."""
    record: dict[str, Any] = {
        'schema_version': 1, 'producer': 'devtool-r09-cross-campaign-acceptance',
        'overall': 'BLOCKED_R09_SOURCE',
        'source_pass': False, 'native_pass': False, 'hosted_pass': False,
        'consumer_pass': False, 'device_pass': False, 'security_formal_pass': False,
        'formal_release_qualified': False, 'release_qualified': False,
        'external_gates': {group: {item: {'status': 'PENDING_INDEPENDENT_AUTHORITY'}
                                   for item in items} for group, items in EXTERNAL.items()},
        'evidence': [], 'source_regressions': {'status': 'NOT_RUN'},
        'qualification_scope': 'CURRENT_SOURCE_INVENTORY_NOT_RELEASE_AUTHORITY',
    }
    try:
        repo = repo.expanduser().resolve(strict=True)
        if _git(repo, 'status', '--porcelain=v1', '--untracked-files=all'):
            raise ValueError('A clean current-source Git worktree is required')
        commit = _git(repo, 'rev-parse', 'HEAD')
        tree = _git(repo, 'rev-parse', 'HEAD^{tree}')
        record['source_commit'] = commit
        record['source_tree'] = tree
        expected = _safe_file(repo / CONTRACT)
        git_object = subprocess.run(['git', '-C', str(repo), 'show', 'HEAD:' + CONTRACT],
                                    capture_output=True, check=False, timeout=30)
        if git_object.returncode or expected != git_object.stdout:
            raise ValueError('R09 contract must match exact HEAD Git object')
        contract = json.loads(expected)
        if contract.get('schema_version') != 1 or contract.get('producer') != 'devtool-r09-contract':
            raise ValueError('Unexpected R09 contract')
        record['contract_sha256'] = _hash(expected)
        # Reuse R08's actual current-source source pin, 308-row crosswalk and probe authority.
        security = assess_security(repo, run_tests=False)
        record['security_inventory'] = {
            'status': security['overall'],
            'n01_rows': security.get('n01', {}).get('rows'),
            'q02_historical_probes': security.get('q02', {}).get('original_probes'),
            'q02_historical_mismatches': security.get('q02', {}).get('historical_mismatches'),
            'formal_h1_closed': False, 'formal_h2_closed': False,
        }
        if security['overall'] != 'PASS_R08_SOURCE_INVENTORY_EXTERNAL_GATES_OPEN':
            raise ValueError('R08 current-source security inventory failed')
        paths = _evidence_paths(evidence, evidence_dirs)
        record['evidence'] = [_evidence_record(path, tree, commit) for path in paths]
        # source tests from the current repository; no historical greens promoted.
        if full_tests or run_tests:
            record['source_regressions'] = _selected_tests(repo, full=full_tests, timeout=timeout)
        if record['source_regressions']['status'] == 'FAILED':
            raise ValueError('R09 current-source regressions failed')
        if record['source_regressions']['status'] == 'PASS' and record['source_regressions'].get('skipped', 0):
            raise ValueError('R09 current-source regression selection contains unexecuted tests')
        source_verified = record['source_regressions']['status'] == 'PASS'
        record['source_pass'] = source_verified
        record['native_observation'] = _native_smoke() if source_verified else {
            'status': 'NOT_RUN', 'native_confirmed': False}
        record['native_pass'] = bool(source_verified and
                                     record['native_observation'].get('native_confirmed') and
                                     record['native_observation'].get('installed_cli') == 'HELP_PASS')
        record['overall'] = ('PASS_R09_LOCAL_SOURCE_NATIVE_EXTERNAL_OPEN' if record['native_pass']
                             else 'PASS_R09_LOCAL_SOURCE_EXTERNAL_OPEN' if source_verified
                             else 'PASS_R09_INVENTORY_TESTS_NOT_RUN_EXTERNAL_OPEN')
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError,
            subprocess.TimeoutExpired) as exc:
        record['reason_code'] = type(exc).__name__
        record['reason'] = str(exc)
    # One canonical, truthful digest over content (never includes its own digest).
    record['matrix_sha256'] = _hash(json.dumps(record, sort_keys=True, separators=(',', ':')).encode())
    return record


def _write_new_json(path: Path, result: dict[str, Any]) -> None:
    path = path.expanduser().absolute()
    if path.is_symlink() or path.exists() or path.parent.is_symlink():
        raise ValueError('Output must be new and not symlinked')
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
        stream.write('\n')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path.cwd())
    parser.add_argument('--run-tests', action='store_true', help='Run cross-campaign focused tests')
    parser.add_argument('--full-tests', action='store_true', help='Run full current-source pytest suite')
    parser.add_argument('--timeout', type=int, default=1200)
    parser.add_argument('--evidence', type=Path, action='append', default=[])
    parser.add_argument('--evidence-dir', type=Path, action='append', default=[])
    parser.add_argument('--output', type=Path, help='Explicit new private JSON path; no default write')
    parser.add_argument('--format', choices=['json', 'text'], default='json')
    parser.add_argument('--require-full', action='store_true',
                        help='Fail if independently qualified hosted/device/security/release gates remain open')
    args = parser.parse_args(argv)
    result = assess(args.repo, run_tests=args.run_tests, full_tests=args.full_tests,
                    timeout=args.timeout, evidence=tuple(args.evidence),
                    evidence_dirs=tuple(args.evidence_dir))
    if args.output:
        try:
            _write_new_json(args.output, result)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
    if args.format == 'text':
        print('R09 cross-campaign acceptance — ' + result['overall'])
        for key in ('source_pass', 'native_pass', 'hosted_pass', 'consumer_pass',
                    'device_pass', 'security_formal_pass', 'formal_release_qualified'):
            print(f'{key}: {result[key]}')
        print('source_commit: ' + result.get('source_commit', 'UNAVAILABLE'))
        print('evidence_records: ' + str(len(result['evidence'])))
        if result.get('reason'):
            print('reason: ' + result['reason'])
    else:
        print(json.dumps(result, sort_keys=True))
    if result['overall'].startswith('BLOCKED'):
        return 2
    if args.require_full and not result['formal_release_qualified']:
        return 3
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
