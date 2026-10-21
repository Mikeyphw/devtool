"""R08 current-source, fail-closed H1/H2 assessment; NOT formal security closure.

This is an evidence *inventory*. A local commit, pytest pass or self-declared
receipt cannot establish independent audit, Android, remote or release authority.
Read-only unless an operator explicitly requests a unique JSON output file.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

from devtool.rmp_final_hardening import (
    historical_probe_accounting, verify_prior, OBLIGATIONS,
)
from devtool.rmp_h1_acceptance import authority_check, ANCHOR

SOURCE_PATHS = (
    'devtool/rmp_r08_assessment.py', 'devtool/rmp_final_hardening.py',
    'devtool/rmp_h1_acceptance.py', 'devtool/validation_sessions.py',
    'devtool/presentation_surfaces.py', 'devtool/environment_composition.py',
    'devtool/test_runtime_optimization.py',
    'docs/RMP_H1_308_RETAINED_ANCHOR.csv',
    'docs/RMP_FINAL_HISTORICAL_PROBES.csv',
    'FINAL_REMEDIATION_COVERAGE.json',
)
LANES = (
    'tests/test_rmp_r08_assessment.py',
    'tests/test_rmp_final_hardening.py',
    'tests/test_rmp_h1_authority_anchor.py',
    'tests/test_rmp_h1_consolidated_boundaries.py',
    'tests/test_rmp_h1_phase3_session_and_selection.py',
    'tests/test_rmp_h1_phase4_cross_consumers.py',
    'tests/test_r04_durable_credentials.py',
    'tests/test_remed_r04a_presentation_evidence_truth.py',
    'tests/test_rmp_c02_cleanup_recovery.py',
    'tests/test_rmp_c03_ack_context.py',
)
# These statuses deliberately contain no path for a caller-controlled PASS -> CLOSED conversion.
FAMILY_REQUIREMENTS = {
    'N01': ('independently authenticated original 308-finding source',),
    'N02': ('independent persistence-reader and native redaction assessment',),
    'C04': ('independent TEST-G3 expected-case and TEST-A11 remote case authority',),
    'C05': ('real Android HUD end-to-end projection behavior',),
    'C06': ('independent downstream environment identity and credential-safety evidence',),
    'Q01': ('current native confined cleanup/rollback negative evidence',),
    'Q02': ('11 historical test contracts mapped and six original mismatches re-assessed',),
    'U01': ('six original real-substrate qualification authorities',),
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ['git', '-c', f'safe.directory={repo}', '-C', str(repo), *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        timeout=20,
    )
    if result.returncode:
        raise ValueError('Git identity/cleanliness check failed: ' + ' '.join(args))
    return result.stdout.decode('utf-8', errors='strict').strip()


def tracked_sources(repo: Path) -> dict[str, str]:
    """Pin current working bytes to HEAD's Git objects, refusing symlinks."""
    result: dict[str, str] = {}
    for rel in SOURCE_PATHS:
        path = repo / rel
        if path.is_symlink() or not path.is_file():
            raise ValueError('Missing or symlinked R08 evidence: ' + rel)
        current = path.read_bytes()
        committed = subprocess.run(
            ['git', '-c', f'safe.directory={repo}', '-C', str(repo), 'show', 'HEAD:' + rel],
            capture_output=True, timeout=20,
        )
        if committed.returncode or current != committed.stdout:
            raise ValueError('R08 evidence differs from committed source: ' + rel)
        result[rel] = sha(current)
    return result


def check_phase4(repo: Path, receipt: Path, source_commit: str) -> dict[str, Any]:
    """Check original historical Git objects, not current replacement contents.

    The receipt is locally integrity-correlated; it is NOT independently signed.
    """
    if not re.fullmatch('[0-9a-f]{40}', source_commit):
        raise ValueError('Phase4 commit must be a full SHA-1 Git object ID')
    if _git(repo, 'cat-file', '-t', source_commit) != 'commit':
        raise ValueError('Phase4 identity is not a Git commit')
    if subprocess.run(
        ['git', '-C', str(repo), 'merge-base', '--is-ancestor', source_commit, 'HEAD'],
        capture_output=True, timeout=20,
    ).returncode:
        raise ValueError('Phase4 source is not an ancestor of the current source')
    verified = verify_prior(repo, source_commit, receipt)
    return {
        'status': 'LOCAL_HISTORICAL_RECEIPT_CORRELATED',
        'source_commit': source_commit,
        'source_tree': verified['source_tree'],
        'receipt_sha256': sha(receipt.read_bytes()),
        'passed_tests_reported': verified['pytest_passed'],
        'independently_authenticated': False,
        'formal_h1_closed': False,
    }


def assess(repo: Path, *, phase4_receipt: Path | None = None,
           phase4_commit: str | None = None, run_tests: bool = False,
           timeout: int = 180) -> dict[str, Any]:
    repo = repo.expanduser().resolve(strict=True)
    record: dict[str, Any] = {
        'schema_version': 1, 'producer': 'devtool-r08-current-source-security-assessment',
        'overall': 'BLOCKED_R08_CURRENT_SOURCE',
        'formal_h1_closed': False, 'formal_h2_closed': False,
        'formal_u01_closed': False, 'release_qualified': False,
        'historical_campaigns_requalified': False,
        'remote_dispatched': False, 'device_tested': False,
        'source_integrity': 'UNVERIFIED',
        'external_gates': {name: {'status': 'BLOCKED_EXTERNAL',
                                  'independent_authority_verified': False}
                           for name in OBLIGATIONS},
        'families': {name: {'status': 'OPEN_FORMAL_AUTHORITY', 'requirements': list(requirements)}
                     for name, requirements in FAMILY_REQUIREMENTS.items()},
    }
    try:
        if _git(repo, 'status', '--porcelain=v1', '--untracked-files=all'):
            raise ValueError('A clean Git worktree is required for R08 source assessment')
        record['source_commit'] = _git(repo, 'rev-parse', 'HEAD')
        record['source_tree'] = _git(repo, 'rev-parse', 'HEAD^{tree}')
        record['sources'] = tracked_sources(repo)
        if (repo / ANCHOR).is_symlink() or (repo / 'FINAL_REMEDIATION_COVERAGE.json').is_symlink():
            raise ValueError('N01 anchor/coverage must not be symlinks')
        record['n01'] = authority_check(repo)
        if record['n01']['rows'] != 308 or record['n01']['formal_n01_closed'] is not False:
            raise ValueError('N01 anchor did not preserve provisional 308-row authority')
        record['q02'] = historical_probe_accounting(repo)
        if (phase4_receipt is None) != (phase4_commit is None):
            raise ValueError('Both --phase4-receipt and --phase4-commit must be provided')
        if phase4_receipt is not None and phase4_commit is not None:
            record['phase4'] = check_phase4(repo, phase4_receipt.expanduser(), phase4_commit)
        else:
            record['phase4'] = {'status': 'PENDING_EXACT_NATIVE_RECEIPT',
                                'independently_authenticated': False}
        if run_tests:
            tested = subprocess.run([sys.executable, '-m', 'pytest', '-q', '--disable-warnings',
                                     '--maxfail=1', *LANES], cwd=repo,
                                    capture_output=True, timeout=max(1, timeout), check=False)
            combined = tested.stdout + b'\n' + tested.stderr
            # Avoid writing raw subprocess output; it may include sensitive fixture content.
            matched = re.search(rb'(\d+) passed', tested.stdout)
            record['selected_tests'] = {
                'status': 'PASS' if tested.returncode == 0 and matched else 'FAILED',
                'exit_code': tested.returncode,
                'passed': int(matched.group(1)) if matched else 0,
                'output_sha256': sha(combined),
                'lane_count': len(LANES),
            }
            if record['selected_tests']['status'] != 'PASS':
                raise ValueError('Current-source selected security regressions failed')
        else:
            record['selected_tests'] = {'status': 'NOT_RUN', 'lane_count': len(LANES)}
        record['source_integrity'] = 'PASS_HEAD_TRACKED_AND_HISTORICAL_ACCOUNTING'
        record['overall'] = 'PASS_R08_SOURCE_INVENTORY_EXTERNAL_GATES_OPEN'
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError,
            subprocess.TimeoutExpired) as exc:
        record['reason'] = str(exc)
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path.cwd())
    parser.add_argument('--phase4-receipt', type=Path)
    parser.add_argument('--phase4-commit')
    parser.add_argument('--run-tests', action='store_true')
    parser.add_argument('--timeout', type=int, default=180)
    parser.add_argument('--output', type=Path, help='Explicit unique local JSON output; no implicit write')
    args = parser.parse_args(argv)
    result = assess(args.repo, phase4_receipt=args.phase4_receipt,
                    phase4_commit=args.phase4_commit,
                    run_tests=args.run_tests, timeout=args.timeout)
    if args.output is not None:
        output = args.output.expanduser()
        if output.is_symlink() or output.exists():
            parser.error('R08 output path must be new and non-symlinked')
        if output.parent.is_symlink():
            parser.error('R08 output parent must not be symlinked')
        output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, 'O_NOFOLLOW'):
            flags |= os.O_NOFOLLOW
        fd = os.open(output, flags, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as file:
            json.dump(result, file, indent=2, sort_keys=True)
            file.write('\n')
    print(json.dumps(result, sort_keys=True))
    return 0 if result['overall'] == 'PASS_R08_SOURCE_INVENTORY_EXTERNAL_GATES_OPEN' else 2


if __name__ == '__main__':
    raise SystemExit(main())
