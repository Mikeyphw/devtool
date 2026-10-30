#!/usr/bin/env python3
"""R1R26: optional real GitHub definitive-rejection -> safe retry proof.

A deliberately nonexistent ref is substituted for ONE raw GitHub POST after
Devtool's reviewed source preflight. An HTTP 422 refusal is required before any
valid retry. Timeouts/5xx remain unresolved and are NEVER tested as real
retryable results. No release authority is conveyed.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Callable

from devtool.actions_dispatch import dispatch_workflow
from devtool.github_actions import GitHubActionsError, GitHubDispatchOutcomeUnknown

_SOURCE = Path(__file__).resolve().with_name('fc08_dispatch_cycle.py')
_SPEC = importlib.util.spec_from_file_location('devtool_fc08_dispatch_reviewed_r1r26', _SOURCE)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError('FC08_R1R26_REVIEWED_DISPATCH_SOURCE_UNAVAILABLE')
_cycle = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_cycle)

FAMILY = 'devtool.fc08.real-api-rejection-retry.v1'
INVALID_REF_PREFIX = 'fc08-r1r26-intentionally-missing-'
# Error categories are an allowlist, never untrusted response text. Even a
# forged exception prefixed with FC08_ must not echo a credential or path.
SAFE_FAILURE_CATEGORIES = frozenset({
    'FC08_R1R26_SOURCE_IDENTITY_INVALID',
    'FC08_R1R26_WORKFLOW_TOKEN_MISSING',
    'FC08_R1R26_POLL_BOUNDS_INVALID',
    'FC08_R1R26_UNSAFE_OR_NONEMPTY_STATE_ROOT',
    'FC08_R1R26_DEFN_REJECTION_UNPROVEN',
    'FC08_R1R26_INVALID_REF_NOT_REJECTED',
    'FC08_R1R26_DEFN_REJECTION_NOT_JOURNALED',
    'FC08_R1R26_RETRY_NOT_CORRELATED',
    'FC08_R1R26_RESUME_DUPLICATE_POST_OR_ID_MISMATCH',
    'FC08_R1R26_JOURNAL_STATE_MISMATCH',
    'FC08_R1R26_UNSAFE_JOURNAL_FILE',
    'FC08_R1R26_INVALID_JOURNAL_RECORD',
    'FC08_R1R26_SOURCE_SHA_INVALID',
    'FC08_R1R26_UNREVIEWED_CLIENT',
    'FC08_DISPATCH_OPT_IN_OR_HOST_IDENTITY_MISSING',
    'FC08_DISPATCH_INVALID_SOURCE_OR_RUN_ID',
    'FC08_DISPATCH_SOURCE_MISMATCH',
    'FC08_DISPATCH_DIRTY_REVIEWED_SOURCE',
})


class RejectOnceClient(_cycle.CountingClient):
    """Change only first raw POST's ref; never change a reviewed workflow/input.

    An unexpected 200/204 response becomes outcome-unknown, not a rejection.
    """
    def __init__(self, token: str, *, source_sha: str, **kw: Any):
        super().__init__(token, **kw)
        if not re.fullmatch('[a-f0-9]{40}', source_sha):
            raise RuntimeError('FC08_R1R26_SOURCE_SHA_INVALID')
        self.invalid_ref = INVALID_REF_PREFIX + source_sha[:12]
        self.attempted_rejection = False
        self.verified_422 = False

    def dispatch(self, slug: str, workflow_id: str, ref: str,
                 inputs: dict[str, str]) -> dict[str, Any]:
        if not self.attempted_rejection:
            self.attempted_rejection = True
            if slug != _cycle.REPO or ref != 'main' or inputs.get('scenario') != 'success':
                raise GitHubDispatchOutcomeUnknown('FC08_R1R26_WRONG_FIRST_DISPATCH')
            try:
                result = super().dispatch(slug, workflow_id, self.invalid_ref, inputs)
            except GitHubDispatchOutcomeUnknown:
                raise
            except GitHubActionsError as exc:
                # This exact text is produced by GitHubActionsClient only for
                # the real HTTP 422 branch, not arbitrary HTTP/transport errors.
                if type(exc) is not GitHubActionsError or str(exc) != 'GitHub rejected workflow ref or input validation':
                    raise GitHubDispatchOutcomeUnknown('FC08_R1R26_REJECTION_STATUS_UNPROVEN') from None
                self.verified_422 = True
                raise
            raise GitHubDispatchOutcomeUnknown('FC08_R1R26_INVALID_REF_WAS_ACCEPTED')
        return super().dispatch(slug, workflow_id, ref, inputs)


def _journal(folder: Path) -> list[dict[str, Any]]:
    rows = []
    for file in sorted(folder.glob('*.json')):
        if file.is_symlink() or not re.fullmatch(r'[0-9a-f]{32}\.json', file.name):
            raise RuntimeError('FC08_R1R26_UNSAFE_JOURNAL_FILE')
        obj = json.loads(file.read_bytes())
        if not isinstance(obj, dict) or obj.get('request_id') != file.stem:
            raise RuntimeError('FC08_R1R26_INVALID_JOURNAL_RECORD')
        rows.append(obj)
    return rows


def execute(client: Any, identity: dict[str, Any], state_root: Path,
            *, sleep: Callable[[float], Any] = _cycle.time.sleep,
            polls: int = 90, interval: float = 4.0) -> dict[str, Any]:
    """Prove real 422, one authorized retry and zero extra POST on resume."""
    if not 1 <= polls <= 120 or not 1 <= interval <= 20:
        raise RuntimeError('FC08_R1R26_POLL_BOUNDS_INVALID')
    if (state_root.is_symlink() or
        (state_root.exists() and (not state_root.is_dir() or any(state_root.iterdir())))):
        raise RuntimeError('FC08_R1R26_UNSAFE_OR_NONEMPTY_STATE_ROOT')
    state_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if (identity.get('repository') != _cycle.REPO or identity.get('ref') != 'main'
        or not re.fullmatch('[a-f0-9]{40}', str(identity.get('sha','')))
        or not re.fullmatch('fc08-[0-9]+-[1-9][0-9]*', str(identity.get('correlation','')))):
        raise RuntimeError('FC08_R1R26_SOURCE_IDENTITY_INVALID')
    if not isinstance(client, RejectOnceClient) and not getattr(client, 'r1r26_offline_contract_client', False):
        raise RuntimeError('FC08_R1R26_UNREVIEWED_CLIENT')
    inputs = {'scenario': 'success', 'correlation': identity['correlation']}
    kwargs = dict(execute=True, state_root=state_root,
                  expected_source_sha=identity['sha'], resume_if_existing=True)
    try:
        dispatch_workflow(client, _cycle.REPO, _cycle.WORKFLOW, 'main', inputs, **kwargs)
    except GitHubActionsError as exc:
        if (not client.verified_422 or type(exc) is not GitHubActionsError
                or str(exc) != 'GitHub rejected workflow ref or input validation'):
            raise RuntimeError('FC08_R1R26_DEFN_REJECTION_UNPROVEN') from None
    else:
        raise RuntimeError('FC08_R1R26_INVALID_REF_NOT_REJECTED')
    rejected = _journal(state_root)
    if (client.dispatch_calls != 1 or len(rejected) != 1
        or rejected[0].get('state') != 'rejected-by-api'
        or rejected[0].get('run_id') is not None
        or rejected[0].get('pre_dispatch_sha') != identity['sha']):
        raise RuntimeError('FC08_R1R26_DEFN_REJECTION_NOT_JOURNALED')
    retry = dispatch_workflow(client, _cycle.REPO, _cycle.WORKFLOW, 'main', inputs, **kwargs)
    if (client.dispatch_calls != 2 or retry.get('dispatched') is not True
        or retry.get('resume_existing') or retry.get('pre_dispatch_sha') != identity['sha']
        or retry.get('state') != 'accepted-correlated'
        or type(retry.get('run_id')) is not int or retry['run_id'] <= 0
        or retry.get('request_id') == rejected[0]['request_id']):
        raise RuntimeError('FC08_R1R26_RETRY_NOT_CORRELATED')
    before_resume = client.dispatch_calls
    reused = dispatch_workflow(client, _cycle.REPO, _cycle.WORKFLOW, 'main', inputs, **kwargs)
    if (client.dispatch_calls != before_resume or reused.get('dispatched') is not False
        or reused.get('resume_existing') is not True
        or reused.get('request_id') != retry['request_id']
        or reused.get('run_id') != retry['run_id']):
        raise RuntimeError('FC08_R1R26_RESUME_DUPLICATE_POST_OR_ID_MISMATCH')
    records = _journal(state_root)
    if (len(records) != 2 or {row['state'] for row in records}
            != {'rejected-by-api', 'accepted-correlated'}):
        raise RuntimeError('FC08_R1R26_JOURNAL_STATE_MISMATCH')
    remote = _cycle.remote_proof(client, identity, retry, 'success',
                                  sleep=sleep, polls=polls, interval=interval)
    return {'schema': FAMILY, 'identity': identity, 'source_bound': True,
            'real_http_422_rejection_proven': True,
            'rejection': {'classification': 'HTTP_422_DEFINITIVE',
                          'request_id': rejected[0]['request_id'],
                          'journal_state': 'rejected-by-api', 'dispatch_calls': 1,
                          'run_id': None},
            'authorized_retry': {'request_id': retry['request_id'],
                                 'journal_state': 'accepted-correlated',
                                 'dispatch_calls': 1, 'remote': remote},
            'resume_without_post_proven': True,
            'total_post_calls': 2, 'unknown_transport_retry_proven': False,
            'real_5xx_or_timeout_proven': False,
            'fc08_overall_qualified': False, 'release_qualified': False,
            'device_qualified': False, 'consumer_qualified': False}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    out = Path(args.out)
    if out.is_symlink() or (out.exists() and not out.is_file()):
        print('BLOCKED_FC08_R1R26_UNSAFE_OUTPUT', file=sys.stderr)
        return 2
    try:
        identity = _cycle.source_identity(dict(os.environ), Path(__file__).resolve().parents[1])
        token = os.environ.get('GITHUB_TOKEN', '')
        if not token or '\n' in token or '\r' in token:
            raise RuntimeError('FC08_R1R26_WORKFLOW_TOKEN_MISSING')
        client = RejectOnceClient(token, source_sha=identity['sha'])
        with tempfile.TemporaryDirectory(prefix='fc08-r1r26-journal-') as tmp:
            payload = execute(client, identity, Path(tmp) / 'journal')
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, sort_keys=True, indent=2) + '\n')
        print('PASS_FC08_R1R26_REAL_HTTP_422_SAFE_RETRY_NON_RELEASE')
        return 0
    except (OSError, ValueError, RuntimeError, GitHubActionsError) as exc:
        category = str(exc).split(':', 1)[0]
        if category not in SAFE_FAILURE_CATEGORIES:
            category = 'EXTERNAL_DISPATCH_UNVERIFIED'
        safe = {'schema': FAMILY, 'status': 'BLOCKED_UNQUALIFIED',
                'error_type': type(exc).__name__, 'error_category': category,
                'release_qualified': False, 'fc08_overall_qualified': False}
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(safe, sort_keys=True, indent=2) + '\n')
        except OSError:
            pass
        print('BLOCKED_FC08_R1R26:'+category, file=sys.stderr)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
