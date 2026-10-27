"""R02: source-bound, local-only A09 adoption acceptance on the current Devtool commit.

Does not fetch GitHub, create workflows, dispatch jobs, access private tokens, or
qualify hosted execution. The optional consumer review is read-only.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

# Exact post-R01 tree (on both the disposable and accepted native R01 commits).
R01_TREE = 'ea93004437c1f5d7a1c8353aef834b1844ddcc38'
TESTS = (
    'tests/test_actions_r02_host_review.py',
    'tests/test_actions_a09_ci_host_contract.py',
    'tests/test_actions_a03_adoption.py',
    'tests/test_actions_a06_wheel_distribution.py',
)
SOURCE_PATHS = (
    'devtool/actions_adoption.py',
    'devtool/actions_r02_acceptance.py',
    'tests/test_actions_r02_host_review.py',
    'docs/actions/ACTIONS_R02_CONSUMER_REVIEW.md',
    'docs/DEVTOOL_IMPROVEMENTS.md',
    'docs/DEVTOOL_IMPROVEMENT_INDEX.json',
)


def _git(root: Path, *args: str) -> str:
    p = subprocess.run(['git', '-C', str(root), *args], text=True, capture_output=True, timeout=25)
    if p.returncode:
        raise ValueError('Git identity unavailable: '+str(args))
    return p.stdout.strip()


def acceptance(root: Path, *, consumer: Path | None = None, record: bool = False,
               state_root: Path | None = None) -> dict:
    root = root.resolve()
    if _git(root, 'rev-parse', 'HEAD^{tree}') == R01_TREE:
        raise ValueError('R02 is not installed; HEAD is still the R01 source tree')
    if _git(root, 'rev-parse', 'HEAD^^{tree}') != R01_TREE:
        raise ValueError('R02 must be installed immediately after the exact accepted R01 source tree')
    if _git(root, 'status', '--porcelain', '--untracked-files=normal'):
        raise ValueError('R02 qualification requires a clean Git worktree')
    source_sha = _git(root, 'rev-parse', 'HEAD')
    hashes: dict[str, str] = {}
    for rel in SOURCE_PATHS:
        f = root / rel
        if f.is_symlink() or not f.is_file():
            raise ValueError('Missing/symlinked R02 source file: '+rel)
        raw = f.read_bytes()
        content = subprocess.run(['git', '-C', str(root), 'show', 'HEAD:'+rel], capture_output=True, timeout=25)
        if content.returncode or raw != content.stdout:
            raise ValueError('R02 source differs from Git HEAD: '+rel)
        hashes[rel] = hashlib.sha256(raw).hexdigest()
    p = subprocess.run([sys.executable, '-m', 'pytest', '-p', 'no:cacheprovider', '-q',
                        '--disable-warnings', *TESTS], cwd=root, text=True,
                       capture_output=True, timeout=300,
                       env={**os.environ, 'PYTHONDONTWRITEBYTECODE':'1', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1'})
    if p.returncode:
        raise ValueError('R02 tests failed: '+(p.stdout+p.stderr)[-1000:])
    m = re.search(r'(\d+) passed(?:, (\d+) skipped)?', p.stdout)
    if not m or int(m.group(1)) < 30 or int(m.group(2) or 0):
        raise ValueError('R02 test result incomplete or unexpectedly skipped')
    receipt = {
        'schema_version': 1,
        'producer': 'devtool-actions-r02-local-acceptance',
        'overall': 'PASS_R02_LOCAL_SOURCE_ONLY',
        'devtool_commit': source_sha,
        'devtool_tree': _git(root, 'rev-parse', 'HEAD^{tree}'),
        'r01_parent_tree': R01_TREE,
        'source_sha256': hashes,
        'tests_passed': int(m.group(1)),
        'tests_skipped': 0,
        'pytest_output_sha256': hashlib.sha256((p.stdout+p.stderr).encode()).hexdigest(),
        'remote_dispatch': False,
        'hosted_qualified': False,
        'source_attested': False,
        'device_qualified': False,
        'release_qualified': False,
    }
    if consumer is not None:
        from .actions_adoption import adopt
        # The accepted source SHA need not be remotely published yet; no hosted
        # conclusion is derived from an otherwise successful local preview.
        review = adopt(consumer, target='skybind', workflow='native', devtool_sha=source_sha)
        if review['ci_host_profile'] != 'go' or not review['consumer_worktree_clean']:
            raise ValueError('Skybind Go consumer must be clean and explicitly Go-provisioned')
        receipt['consumer_review'] = {
            'commit': review['consumer_commit'],
            'tree': review['consumer_tree'],
            'review_sha256': review['review_sha256'],
            'selected_jobs': review['selected_jobs'],
            'host_profile': review['ci_host_profile'],
            'review_only': True,
        }
    if record:
        outdir = state_root or (Path.home() / '.local/state/devtool/actions/r02')
        if outdir.is_symlink():
            raise ValueError('Refusing symlink receipt directory')
        outdir.mkdir(parents=True, exist_ok=True, mode=0o700)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        target = outdir / ('acceptance-'+stamp+'-'+source_sha[:12]+'.json')
        fd = os.open(target, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(receipt, f, indent=2, sort_keys=True)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        receipt['receipt_path'] = str(target)
    return receipt


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', type=Path, default=Path.cwd())
    p.add_argument('--consumer', type=Path, help='Optional read-only clean Skybind source preview')
    p.add_argument('--record', action='store_true', help='Create one private, immutable local receipt')
    args = p.parse_args(argv)
    try:
        result = acceptance(args.repo, consumer=args.consumer, record=args.record)
    except (ValueError, OSError, subprocess.TimeoutExpired) as e:
        print(json.dumps({'overall':'BLOCKED_R02_LOCAL', 'reason':str(e)}, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
