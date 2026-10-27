"""RMP H1 Phase 4 native cross-consumer acceptance; no formal H1 closure.

Reuses Phase 3's source-pinned receipt and 308-finding provisional anchor;
runs only H1/PRES/ENV consumer tests on the committed Phase 4 source.
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

SUBJECT = 'fix(security): harden H1 dashboard truth and direnv identity propagation'
PREVIOUS = 'fix(security): harden H1 session loading and bind selected test cases'
LANES = (
    'tests/test_rmp_h1_phase4_cross_consumers.py',
    'tests/test_rmp_h1_phase3_session_and_selection.py',
    'tests/test_rmp_h1_phase2_resume_and_membership.py',
    'tests/test_rmp_h1_consolidated_boundaries.py',
    'tests/test_r09_environment_identity.py',
    'tests/test_env_x02_language_environment_convergence.py',
    'tests/test_r06_exo_status_truth.py',
    'tests/test_pres_x01_presentation_surfaces.py',
    'tests/test_pres_x02_presentation_layouts.py',
)
SOURCES = (
    'devtool/environment_composition.py',
    'devtool/presentation_surfaces.py',
    'devtool/rmp_h1_phase4_acceptance.py',
    'devtool/validation_sessions.py',
    'devtool/test_g3_gate.py',
    'devtool/test_runtime_optimization.py',
    'docs/RMP_H1_308_RETAINED_ANCHOR.csv',
)
HEX64 = re.compile(r'[0-9a-f]{64}\Z')


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(root: Path, *args: str) -> bytes:
    proc = subprocess.run(['git', '-c', f'safe.directory={root}', '-C', str(root), *args], capture_output=True)
    if proc.returncode:
        raise ValueError('git '+ ' '.join(args) + ': ' + proc.stderr.decode(errors='replace')[:300])
    return proc.stdout


def native_termux() -> bool:
    base = '/data/data/com.termux/files/usr'
    try:
        paths = (sys.executable, sys.base_prefix, os.readlink('/proc/self/exe'))
    except OSError:
        return False
    return sys.platform in ('linux', 'android') and all(p == base or p.startswith(base+'/') for p in paths)


def read_prior(path: Path, repo: Path, parent: str) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ValueError('Phase 3 receipt missing or symlinked')
    data = json.loads(path.read_bytes())
    if (
        data.get('schema_version') != 1
        or data.get('producer') != 'devtool-rmp-h1-phase3-native-consumer-acceptance'
        or data.get('overall') != 'PASS_H1_PHASE3_CONSUMER_ACCEPTANCE'
        or type(data.get('pytest_passed')) is not int or data['pytest_passed'] < 130
        or data.get('formal_h1_closed') is not False
        or data.get('historical_campaigns_requalified') is not False
        or data.get('release_qualified') is not False
        or data.get('source_commit') != parent
        or data.get('source_tree') != git(repo, 'rev-parse', parent+'^{tree}').decode().strip()
        or not isinstance(data.get('authority'), dict)
        or data['authority'].get('rows') != 308
        or data['authority'].get('formal_n01_closed') is not False
        or data['authority'].get('authoritative_signature_verified') is not False
    ):
        raise ValueError('Phase 3 acceptance source, status or authority scope mismatch')
    for section in ('sources', 'lanes'):
        entries = data.get(section)
        if not isinstance(entries, dict) or not entries:
            raise ValueError('Phase 3 receipt missing '+section)
        for name, expected in entries.items():
            if not isinstance(name, str) or name.startswith('/') or '..' in Path(name).parts or not isinstance(expected, str) or not HEX64.fullmatch(expected):
                raise ValueError('Invalid Phase 3 source/lane digest')
            if sha(git(repo, 'show', parent+':'+name)) != expected:
                raise ValueError('Phase 3 receipt file does not match original commit: '+name)
    return data


def main(argv=None) -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--repo', type=Path, default=Path.cwd())
    cli.add_argument('--prior-acceptance', type=Path)
    cli.add_argument('--output', type=Path)
    cli.add_argument('--timeout', type=int, default=180)
    a = cli.parse_args(argv)
    repo = a.repo.expanduser().resolve()
    record = {
        'schema_version': 1,
        'producer': 'devtool-rmp-h1-phase4-native-cross-consumer-acceptance',
        'overall': 'BLOCKED_H1_PHASE4_ACCEPTANCE',
        'formal_h1_closed': False,
        'family_closure': {k: False for k in ('N01', 'N02', 'C04', 'C05', 'C06')},
        'historical_campaigns_requalified': False,
        'release_qualified': False,
        'automatic_push': False,
        'automatic_release': False,
        'started_at_utc': datetime.now(timezone.utc).isoformat(),
    }
    code = 2
    try:
        if not native_termux():
            raise ValueError('Native Termux Python runtime required')
        if git(repo, 'status', '--porcelain=v1', '--untracked-files=all').strip():
            raise ValueError('Clean worktree required')
        head = git(repo, 'rev-parse', 'HEAD').decode().strip()
        parent = git(repo, 'rev-parse', 'HEAD^').decode().strip()
        if git(repo, 'log', '-1', '--format=%s').decode().strip() != SUBJECT:
            raise ValueError('Expected Phase 4 commit not HEAD')
        if git(repo, 'log', '-1', '--format=%s', parent).decode().strip() != PREVIOUS:
            raise ValueError('Phase 3 commit not the direct parent')
        if a.prior_acceptance:
            prior = a.prior_acceptance.expanduser()
        else:
            paths = sorted((Path.home()/'.local/state/devtool/rmp-h1').glob('phase3-*.json'))
            if not paths:
                raise ValueError('No Phase 3 receipt found')
            prior = paths[-1]
        old = read_prior(prior, repo, parent)
        record.update({
            'source_commit': head,
            'source_tree': git(repo, 'rev-parse', 'HEAD^{tree}').decode().strip(),
            'phase3_source_commit': parent,
            'phase3_receipt_sha256': sha(prior.read_bytes()),
            'phase3_receipt_path': str(prior),
            'phase3_test_count': old['pytest_passed'],
        })
        from devtool.rmp_h1_acceptance import authority_check
        record['authority'] = authority_check(repo)
        if (record['authority'].get('rows') != 308
            or record['authority'].get('formal_n01_closed') is not False
            or record['authority'].get('authoritative_signature_verified') is not False):
            raise ValueError('Retained N01 inventory must remain provisional')
        for section, paths in (('sources', SOURCES), ('lanes', LANES)):
            record[section] = {}
            for name in paths:
                path = repo/name
                if path.is_symlink() or not path.is_file() or sha(path.read_bytes()) != sha(git(repo, 'show', 'HEAD:'+name)):
                    raise ValueError('Current source/lane not clean: '+name)
                record[section][name] = sha(path.read_bytes())
        proc = subprocess.run([sys.executable, '-m', 'pytest', '-q', *LANES], cwd=repo, text=True, capture_output=True, timeout=max(1, a.timeout))
        output = proc.stdout+'\n'+proc.stderr
        record['pytest_output_sha256'] = sha(output.encode())
        record['pytest_returncode'] = proc.returncode
        record['pytest_summary_tail'] = output.splitlines()[-12:]
        m = re.search(r'(\d+) passed', proc.stdout)
        record['pytest_passed'] = int(m.group(1)) if m else 0
        if proc.returncode != 0 or record['pytest_passed'] < 200:
            raise ValueError('Phase 4 cross-consumer regressions incomplete or failed')
        record['overall'] = 'PASS_H1_PHASE4_CROSS_CONSUMER_ACCEPTANCE'
        code = 0
    except (ValueError, OSError, RuntimeError, KeyError, TypeError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        record['reason'] = str(exc)
    finally:
        record['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
        out = a.output or Path.home()/'.local/state/devtool/rmp-h1'/('phase4-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open('x', encoding='utf-8') as stream:
            json.dump(record, stream, sort_keys=True, indent=2)
            stream.write('\n')
        print(json.dumps({'overall': record['overall'], 'pytest_passed': record.get('pytest_passed', 0), 'formal_h1_closed': False, 'receipt': str(out), 'reason': record.get('reason')}, sort_keys=True))
    return code

if __name__ == '__main__':
    raise SystemExit(main())
