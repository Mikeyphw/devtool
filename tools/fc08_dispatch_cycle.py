#!/usr/bin/env python3
"""FC08 R1R25: manually opted-in, bounded real GitHub dispatch-cycle evidence.

Only the dedicated, inert FC08 probe workflow may be dispatched. Never
promotes a run to release; never retries uncertain/uncorrelated POST outcomes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable

from devtool.actions_dispatch import dispatch_workflow
from devtool.actions_runs import _normalize_run
from devtool.github_actions import GitHubActionsClient, GitHubActionsError

REPO = 'Mikeyphw/devtool'
WORKFLOW = 'devtool-fc08-dispatch-probe.yml'
FAMILY = 'devtool.fc08.real-dispatch-cycle.v1'


def source_identity(env: dict[str, str], root: Path) -> dict[str, Any]:
    """Require one verified main-branch hosted GitHub run, not a local test."""
    if (env.get('GITHUB_ACTIONS') != 'true' or env.get('GITHUB_REPOSITORY') != REPO
            or env.get('GITHUB_REF') != 'refs/heads/main'
            or env.get('FC08_DISPATCH_AUTHORIZED') != 'true'):
        raise RuntimeError('FC08_DISPATCH_OPT_IN_OR_HOST_IDENTITY_MISSING')
    sha = env.get('GITHUB_SHA', '')
    run = env.get('GITHUB_RUN_ID', '')
    attempt = env.get('GITHUB_RUN_ATTEMPT', '')
    if (not re.fullmatch(r'[0-9a-f]{40}', sha) or not run.isdecimal()
            or not attempt.isdecimal() or not int(run) or not int(attempt)):
        raise RuntimeError('FC08_DISPATCH_INVALID_SOURCE_OR_RUN_ID')
    p = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'],
                       capture_output=True, text=True, timeout=20)
    if p.returncode or p.stdout.strip() != sha:
        raise RuntimeError('FC08_DISPATCH_SOURCE_MISMATCH')
    dirty = subprocess.run(['git', '-C', str(root), 'status', '--porcelain', '--untracked-files=all'],
                           capture_output=True, text=True, timeout=20)
    if dirty.returncode or dirty.stdout.strip():
        raise RuntimeError('FC08_DISPATCH_DIRTY_REVIEWED_SOURCE')
    return {'repository': REPO, 'sha': sha, 'run_id': int(run), 'run_attempt': int(attempt),
            'ref': 'main', 'correlation': f'fc08-{int(run)}-{int(attempt)}'}


class CountingClient(GitHubActionsClient):
    """Count actual non-idempotent dispatch method invocations (not GETs)."""
    def __init__(self, token: str, **kwargs: Any):
        super().__init__(token, **kwargs)
        self.dispatch_calls = 0

    def dispatch(self, slug: str, workflow_id: str, ref: str, inputs: dict[str, str]) -> dict[str, Any]:
        self.dispatch_calls += 1
        return super().dispatch(slug, workflow_id, ref, inputs)


def remote_proof(client: Any, identity: dict[str, Any], first: dict[str, Any],
                 scenario: str, *, sleep: Callable[[float], Any] = time.sleep,
                 polls: int = 90, interval: float = 4.0) -> dict[str, Any]:
    """Accept only correlated run IDs and exact source/attempt/completion."""
    run_id = first.get('run_id')
    workflow_id = first.get('workflow_id')
    if type(run_id) is not int or run_id <= 0 or type(workflow_id) is not int:
        raise RuntimeError('FC08_DISPATCH_UNCORRELATED_RUN_NO_INFERRED_ID')
    if not 1 <= polls <= 120 or not 1 <= interval <= 20:
        raise RuntimeError('FC08_DISPATCH_POLL_BOUNDS_INVALID')
    expected = 'success' if scenario == 'success' else 'failure'
    for i in range(polls):
        obj = _normalize_run(client.get_run(REPO, run_id), REPO, run_id)
        if (obj['event'] != 'workflow_dispatch' or obj['workflow_id'] != workflow_id
                or obj['head_sha'] != identity['sha'] or obj['head_branch'] != 'main'
                or obj['run_attempt'] != 1):
            raise RuntimeError('FC08_DISPATCH_REMOTE_IDENTITY_OR_ATTEMPT_MISMATCH')
        if obj['completed']:
            if obj['conclusion'] != expected:
                raise RuntimeError('FC08_DISPATCH_WRONG_REMOTE_CONCLUSION:'+scenario)
            return {'run_id': run_id, 'workflow_id': workflow_id,
                    'run_attempt': 1, 'conclusion': expected,
                    'status': 'completed', 'event': 'workflow_dispatch',
                    'source_sha': identity['sha'], 'polls': i + 1}
        if i + 1 < polls:
            sleep(interval)
    raise RuntimeError('FC08_DISPATCH_REMOTE_COMPLETION_UNPROVEN')


def execute_cycle(client: Any, identity: dict[str, Any], state_root: Path,
                  *, sleep: Callable[[float], Any] = time.sleep,
                  polls: int = 90, interval: float = 4.0) -> dict[str, Any]:
    """One POST per distinct input; repeated request must reuse journal."""
    if state_root.exists() and any(state_root.iterdir()):
        raise RuntimeError('FC08_DISPATCH_STATE_ROOT_NOT_EMPTY')
    state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    scenarios: dict[str, Any] = {}
    baseline_calls = client.dispatch_calls
    # Distinct input digest per scenario: safe repeated identical dispatch for
    # that scenario must be journal-reused, not posted a second time.
    for scenario in ('success', 'failure'):
        values = {'scenario': scenario, 'correlation': identity['correlation']}
        first = dispatch_workflow(client, REPO, WORKFLOW, 'main', values,
            execute=True, state_root=state_root, expected_source_sha=identity['sha'],
            resume_if_existing=True)
        if (first.get('dispatched') is not True or first.get('resume_existing') or
                first.get('pre_dispatch_sha') != identity['sha']):
            raise RuntimeError('FC08_DISPATCH_FIRST_POST_NOT_ATTESTED:'+scenario)
        calls_after_first = client.dispatch_calls
        second = dispatch_workflow(client, REPO, WORKFLOW, 'main', values,
            execute=True, state_root=state_root, expected_source_sha=identity['sha'],
            resume_if_existing=True)
        if (second.get('dispatched') is not False or second.get('resume_existing') is not True
                or second.get('request_id') != first.get('request_id')
                or second.get('run_id') != first.get('run_id')
                or client.dispatch_calls != calls_after_first):
            raise RuntimeError('FC08_DISPATCH_DUPLICATE_POST_OR_JOURNAL_MISMATCH:'+scenario)
        scenarios[scenario] = {'first_dispatched': True, 'repeat_dispatched': False,
            'same_request_id': True, 'post_count': 1,
            'journal_state': second.get('state'),
            'remote': remote_proof(client, identity, first, scenario,
                                   sleep=sleep, polls=polls, interval=interval)}
    if client.dispatch_calls - baseline_calls != 2:
        raise RuntimeError('FC08_DISPATCH_INCORRECT_TOTAL_POST_COUNT')
    return {'schema': FAMILY, 'identity': identity, 'scenarios': scenarios,
            'total_post_calls': 2, 'same_input_reuse_proven': True,
            'remote_success_and_failure_proven': True, 'api_failure_retry_proven': False,
            'release_qualified': False, 'fc08_overall_qualified': False,
            'device_qualified': False, 'consumer_qualified': False}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    out = Path(args.out)
    if out.is_symlink() or (out.exists() and not out.is_file()):
        print('BLOCKED_FC08_DISPATCH_UNSAFE_OUTPUT', file=sys.stderr)
        return 2
    try:
        identity = source_identity(dict(os.environ), Path(__file__).resolve().parents[1])
        token = os.environ.get('GITHUB_TOKEN', '')
        if not token or '\n' in token or '\r' in token:
            raise RuntimeError('FC08_DISPATCH_RESTRICTED_WORKFLOW_TOKEN_MISSING')
        client = CountingClient(token)
        with tempfile.TemporaryDirectory(prefix='fc08-dispatch-journal-') as d:
            payload = execute_cycle(client, identity, Path(d) / 'journal')
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, sort_keys=True, indent=2) + '\n')
        print('PASS_FC08_REAL_DISPATCH_CYCLE_NON_RELEASE')
        return 0
    except (OSError, ValueError, RuntimeError, GitHubActionsError) as exc:
        # Never print network bodies, request headers, tokens, or raw paths.
        safe = {'schema': FAMILY, 'status': 'BLOCKED_UNQUALIFIED',
                'error_type': type(exc).__name__,
                'error_category': str(exc).split(':', 1)[0] if str(exc).startswith('FC08_') else 'EXTERNAL_DISPATCH_UNVERIFIED',
                'release_qualified': False, 'fc08_overall_qualified': False}
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(safe, sort_keys=True, indent=2) + '\n')
        except OSError:
            pass
        print('BLOCKED_FC08_REAL_DISPATCH_CYCLE:'+safe['error_category'], file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
