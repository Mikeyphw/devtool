"""RMP B01/C01 bounded source-bound consumer acceptance, not formal closure."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

LANES = (
    'tests/test_r02_android_result_completeness.py',
    'tests/test_aops04_android_release_evidence_final_seal.py',
    'tests/test_rmp_b01_g3r_lane_provenance.py',
    'tests/test_g3r_native_termux_detection.py',
)


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(['git', '-c', f'safe.directory={root}', *args],
                               cwd=root, capture_output=True, text=True, timeout=30)
    if completed.returncode:
        raise RuntimeError('Git identity unavailable: ' + completed.stderr.strip())
    return completed.stdout.strip()


def run(repo: Path, output: Path) -> dict:
    repo = repo.resolve(strict=True)
    head = _git(repo, 'rev-parse', 'HEAD')
    tree = _git(repo, 'rev-parse', 'HEAD^{tree}')
    if _git(repo, 'status', '--porcelain=v1', '--untracked-files=all'):
        raise RuntimeError('B01/C01 acceptance requires a clean source tree')
    source_paths = ('devtool/android/release.py', 'devtool/g3r_independent_verify.py')
    source = {p: hashlib.sha256((repo/p).read_bytes()).hexdigest() for p in source_paths}
    completed = subprocess.run([sys.executable, '-m', 'pytest', '-q', '--disable-warnings', '--tb=short', *LANES],
                               cwd=repo, capture_output=True, text=True, timeout=180, env={**os.environ, 'PYTHONPATH': str(repo)})
    if _git(repo, 'rev-parse', 'HEAD') != head or _git(repo, 'status', '--porcelain=v1', '--untracked-files=all'):
        raise RuntimeError('Source changed during H0 acceptance')
    passed = completed.returncode == 0
    now = datetime.now(timezone.utc).isoformat()
    receipt = {
        'schema_version': 1, 'producer': 'devtool-rmp-b01-c01-local-consumer-acceptance',
        'overall': 'PASS_H0_B01_C01_LOCAL_ACCEPTANCE' if passed else 'FAILED_H0_B01_C01_LOCAL_ACCEPTANCE',
        'source_commit': head, 'source_tree': tree, 'source_sha256': source,
        'lanes': {p: hashlib.sha256((repo/p).read_bytes()).hexdigest() for p in LANES},
        'pytest_returncode': completed.returncode,
        'pytest_output_sha256': hashlib.sha256((completed.stdout+completed.stderr).encode()).hexdigest(),
        'pytest_summary_tail': (completed.stdout+completed.stderr).splitlines()[-8:],
        'runtime': {'platform': sys.platform, 'python': sys.executable},
        'formal_b01_closed': False, 'formal_c01_closed': False,
        'historical_campaigns_requalified': False, 'release_qualified': False,
        'automatic_push': False, 'timestamp_utc': now,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, indent=2, sort_keys=True)+'\n', encoding='utf-8')
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path.cwd())
    parser.add_argument('--output', type=Path, default=Path.home()/'.local/state/devtool/rmp-h0/acceptance.json')
    args = parser.parse_args(argv)
    try:
        receipt = run(args.repo, args.output)
        print(json.dumps(receipt, sort_keys=True))
        print('RMP_H0_ACCEPTANCE_RECEIPT:', args.output, flush=True)
        return 0 if receipt['overall'].startswith('PASS_') else 1
    except (OSError, RuntimeError, subprocess.TimeoutExpired, ValueError) as exc:
        print('BLOCKED_H0_ACCEPTANCE:', str(exc), file=sys.stderr)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
