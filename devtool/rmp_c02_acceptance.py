"""RMP-C02 focused local acceptance, using disposable test-owned files only.

This gate does not modify a user's archives or change formal release qualification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# This is deliberately a fixed source-controlled inventory: never execute
# user-supplied test paths through this security evidence producer.
LANES = (
    'tests/test_rmp_c02_acceptance.py',
    'tests/test_rmp_c02_worker_exit_recovery.py',
    'tests/test_rmp_c02_cleanup_recovery.py',
    'tests/test_rmp_c02_consumed_source_authority.py',
    'tests/test_artifact_matching.py',
    'tests/test_apply_terminal_cleanup_hardening.py',
    'tests/test_remed_r04b_cleanup_fresh_process_cli.py',
    'tests/test_remed_r04b_artifact_cleanup_active_session.py',
    'tests/test_remed_r04b_artifact_cleanup_worker.py',
    'tests/test_remed_r03b_retention_recovery_cleanup.py',
    'tests/test_r01_cleanup_repository_boundary.py',
)
_ENV_REMOVE = (
    'DEVTOOL_RUN_ID', 'DEVTOOL_TRANSACTION_REPO_ROOT',
    'DEVTOOL_TRANSACTION_PRIMARY_REPO_ROOT', 'DEVTOOL_TRANSACTION_ISOLATION',
    'DEVTOOL_VALIDATION_STATE_REPO_ROOT',
)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(repo: Path, *, output: Path | None = None, timeout: int = 180) -> dict[str, object]:
    repo = repo.expanduser().resolve(strict=True)
    if not (repo / 'devtool' / '__init__.py').is_file() or not all((repo / p).is_file() for p in LANES):
        raise RuntimeError('C02 acceptance requires the complete current Devtool source and all pinned test lanes')
    command = [sys.executable, '-m', 'pytest', '-q', '--disable-warnings', *LANES]
    env = os.environ.copy()
    for key in _ENV_REMOVE:
        env.pop(key, None)
    # A caller-controlled PYTHONPATH must not silently select a different Devtool.
    env['PYTHONPATH'] = str(repo)
    start = datetime.now(timezone.utc)
    timeout_hit = False
    try:
        result = subprocess.run(command, cwd=repo, env=env, capture_output=True, text=True,
                                timeout=timeout, check=False)
        code, stdout = result.returncode, result.stdout + '\n' + result.stderr
    except subprocess.TimeoutExpired as exc:
        timeout_hit = True
        code = 124
        stdout = ((exc.stdout or b'').decode(errors='replace') if isinstance(exc.stdout, bytes) else (exc.stdout or ''))
        stdout += '\nacceptance test timeout; NOT QUALIFIED'
    # Require a meaningful pytest terminal summary, not just process exit=0.
    matches = re.findall(r'(?<!\d)(\d+) passed(?:, (\d+) (?:skipped|deselected))?', stdout)
    passed = int(matches[-1][0]) if matches else 0
    success = code == 0 and passed > 0 and not timeout_hit and not re.search(r'\b(?:failed|error|errors)\b', stdout.lower())
    receipt: dict[str, object] = {
        'schema_version': 1,
        'producer': 'devtool-rmp-c02-local-acceptance',
        'scope': 'RMP-C02-disposable-caller-recovery-local-only',
        'overall': 'PASS_C02_LOCAL_ACCEPTANCE' if success else 'FAILED_OR_BLOCKED_C02_LOCAL_ACCEPTANCE',
        'release_qualified': False,
        'formal_c02_closed': False,
        'source': {
            'artifact_cleanup_worker_sha256': _digest(repo / 'devtool' / 'artifact_cleanup_worker.py'),
            'cli_sha256': _digest(repo / 'devtool' / 'cli.py'),
        },
        'lanes': {lane: _digest(repo / lane) for lane in LANES},
        'pytest_passed': passed,
        'pytest_returncode': code,
        'timed_out': timeout_hit,
        'output_sha256': hashlib.sha256(stdout.encode()).hexdigest(),
        'started_at_utc': start.isoformat(),
        'finished_at_utc': datetime.now(timezone.utc).isoformat(),
        'guardrails': {
            'test_owned_temporary_artifacts_only': True,
            'automatic_requalification_of_historical_campaigns': False,
            'automatic_push': False,
            'automatic_release': False,
        },
    }
    if output is not None:
        output = output.expanduser().absolute()
        if not output.parent.is_dir() or output.is_symlink():
            raise RuntimeError('Receipt destination must be in an existing directory and cannot be a symlink')
        if output.exists():
            raise RuntimeError('Refusing to overwrite an existing C02 receipt')
        # Exclusive creation preserves old evidence; do not mutate previous receipts.
        with output.open('x', encoding='utf-8') as handle:
            json.dump(receipt, handle, sort_keys=True, indent=2)
            handle.write('\n')
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path.cwd())
    parser.add_argument('--output', type=Path, help='Exclusive-create optional JSON receipt path')
    parser.add_argument('--timeout', type=int, default=180)
    args = parser.parse_args(argv)
    if not 30 <= args.timeout <= 600:
        parser.error('timeout must be 30–600 seconds')
    try:
        payload = run(args.repo, output=args.output, timeout=args.timeout)
    except (RuntimeError, OSError, ValueError) as exc:
        print(json.dumps({'overall':'BLOCKED_C02_LOCAL_ACCEPTANCE','reason':str(exc),'release_qualified':False}))
        return 2
    print(json.dumps(payload, sort_keys=True))
    return 0 if payload['overall'] == 'PASS_C02_LOCAL_ACCEPTANCE' else 1


if __name__ == '__main__':
    raise SystemExit(main())
