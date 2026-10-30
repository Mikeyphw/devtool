#!/usr/bin/env python3
"""R1R27: one *real* inert GitHub probe POST with *injected* response loss.

A valid 200 response is deliberately discarded AFTER the real POST returns.
This is controlled fault injection, NOT evidence of an actual 5xx/timeout.
Never retry a journaled unknown outcome or infer a run ID into that journal.
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
_SPEC = importlib.util.spec_from_file_location('devtool_fc08_dispatch_reviewed_r1r27', _SOURCE)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError('FC08_R1R27_REVIEWED_SOURCE_MISSING')
_cycle = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_cycle)

FAMILY = 'devtool.fc08.injected-lost-response-real-post.v1'
SAFE_FAILURE_CATEGORIES = frozenset({
    'FC08_R1R27_OPT_IN_OR_TOKEN_MISSING',
    'FC08_R1R27_SOURCE_IDENTITY_INVALID',
    'FC08_R1R27_POLL_BOUNDS_INVALID',
    'FC08_R1R27_JOURNAL_UNSAFE',
    'FC08_R1R27_UNREVIEWED_CLIENT',
    'FC08_R1R27_UNKNOWN_OUTCOME_NOT_ATTESTED',
    'FC08_R1R27_JOURNAL_UNKNOWN_NOT_DURABLE',
    'FC08_R1R27_RESUME_REPOST_OR_CORRELATED_UNKNOWN',
    'FC08_R1R27_REAL_RESPONSE_NOT_OBSERVED',
    'FC08_R1R27_REAL_POST_NOT_CORRELATED',
    'FC08_R1R27_PROBE_NOT_COMPLETED',
    'FC08_R1R27_JOURNAL_MUTATED_BY_REMOTE_OBSERVATION',
    'FC08_R1R27_POST_BUDGET_EXCEEDED',
})

class LostResponseClient(_cycle.CountingClient):
    """Perform *one* real POST, then simulate losing its correlated response.

    Guard against an actual duplicate POST even if dispatcher regresses.
    The one remote ID retained here is an *independent injector observation*;
    it is NEVER passed back to the dispatch journal or resume operation.
    """
    def __init__(self, token: str, **kwargs: Any):
        super().__init__(token, **kwargs)
        self.observed_accepted_response: dict[str, Any] | None = None
        self.post_budget_exceeded = False

    def dispatch(self, slug: str, workflow_id: str, ref: str,
                 inputs: dict[str, str]) -> dict[str, Any]:
        if self.dispatch_calls:
            self.post_budget_exceeded = True
            raise GitHubDispatchOutcomeUnknown('FC08_R1R27_POST_BUDGET_EXCEEDED')
        if slug != _cycle.REPO or ref != 'main' or inputs.get('scenario') != 'success':
            raise GitHubDispatchOutcomeUnknown('FC08_R1R27_UNEXPECTED_PROBE_INPUT')
        response = super().dispatch(slug, workflow_id, ref, inputs)
        if (response.get('state') == 'accepted-correlated'
                and type(response.get('run_id')) is int and response['run_id'] > 0):
            self.observed_accepted_response = dict(response)
        # Deliberately lose the response whether 200 or 204. For non-200/
        # transport failures the underlying client already threw the real error.
        raise GitHubDispatchOutcomeUnknown('FC08_R1R27_INJECTED_RESPONSE_LOSS')


def _unknown_journal(root: Path, identity: dict[str, Any]) -> dict[str, Any]:
    files = sorted(root.iterdir())
    if (len(files) != 2 or not (root / '.rm16-dispatch.lock').is_file()
            or any(f.is_symlink() for f in files)):
        raise RuntimeError('FC08_R1R27_JOURNAL_UNKNOWN_NOT_DURABLE')
    matches = [f for f in files if re.fullmatch(r'[a-f0-9]{32}\.json', f.name)]
    if len(matches) != 1 or matches[0].stat().st_size > 16384:
        raise RuntimeError('FC08_R1R27_JOURNAL_UNKNOWN_NOT_DURABLE')
    try:
        data = json.loads(matches[0].read_bytes())
    except (OSError, ValueError):
        raise RuntimeError('FC08_R1R27_JOURNAL_UNKNOWN_NOT_DURABLE') from None
    if (not isinstance(data, dict) or data.get('request_id') != matches[0].stem
            or data.get('state') != 'dispatch-outcome-unknown'
            or data.get('run_id') is not None or data.get('run_url') is not None
            or data.get('pre_dispatch_sha') != identity['sha']
            or data.get('ref') != 'main' or data.get('repository') != _cycle.REPO
            or not re.fullmatch(r'[a-f0-9]{64}', str(data.get('inputs_sha256', '')))):
        raise RuntimeError('FC08_R1R27_JOURNAL_UNKNOWN_NOT_DURABLE')
    return data


def execute(client: Any, identity: dict[str, Any], state_root: Path, *,
            sleep: Callable[[float], Any] = _cycle.time.sleep,
            polls: int = 90, interval: float = 4.0) -> dict[str, Any]:
    """One real POST, injected loss, two journal resumes, separate remote GET."""
    if not 1 <= polls <= 120 or not 1 <= interval <= 20:
        raise RuntimeError('FC08_R1R27_POLL_BOUNDS_INVALID')
    if (not isinstance(identity, dict) or identity.get('repository') != _cycle.REPO
            or identity.get('ref') != 'main'
            or not re.fullmatch(r'[a-f0-9]{40}', str(identity.get('sha', '')))
            or not re.fullmatch(r'fc08-[0-9]+-[1-9][0-9]*', str(identity.get('correlation', '')))):
        raise RuntimeError('FC08_R1R27_SOURCE_IDENTITY_INVALID')
    if not isinstance(client, LostResponseClient):
        raise RuntimeError('FC08_R1R27_UNREVIEWED_CLIENT')
    if (state_root.is_symlink() or
            (state_root.exists() and (not state_root.is_dir() or any(state_root.iterdir())))):
        raise RuntimeError('FC08_R1R27_JOURNAL_UNSAFE')
    state_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    values = {'scenario': 'success', 'correlation': identity['correlation']}
    kwargs = dict(execute=True, state_root=state_root,
                  expected_source_sha=identity['sha'], resume_if_existing=True)
    try:
        dispatch_workflow(client, _cycle.REPO, _cycle.WORKFLOW, 'main', values, **kwargs)
    except GitHubActionsError as exc:
        # Never accept a genuine refusal or an unproven/fabricated exception.
        if (client.dispatch_calls != 1 or type(exc) is not GitHubActionsError
                or 'Dispatch outcome uncertain; do not retry blindly (request ' not in str(exc)):
            raise RuntimeError('FC08_R1R27_UNKNOWN_OUTCOME_NOT_ATTESTED') from None
    else:
        raise RuntimeError('FC08_R1R27_UNKNOWN_OUTCOME_NOT_ATTESTED')
    original = _unknown_journal(state_root, identity)
    request_id = original['request_id']

    def assert_no_new_post_or_correlation() -> None:
        resumed = dispatch_workflow(client, _cycle.REPO, _cycle.WORKFLOW, 'main', values, **kwargs)
        if (client.dispatch_calls != 1 or client.post_budget_exceeded
                or resumed.get('dispatched') is not False
                or resumed.get('resume_existing') is not True
                or resumed.get('safe_to_redispatch') is not False
                or resumed.get('request_id') != request_id
                or resumed.get('state') != 'dispatch-outcome-unknown'
                or resumed.get('run_id') is not None):
            raise RuntimeError('FC08_R1R27_RESUME_REPOST_OR_CORRELATED_UNKNOWN')
        if _unknown_journal(state_root, identity) != original:
            raise RuntimeError('FC08_R1R27_JOURNAL_MUTATED_BY_REMOTE_OBSERVATION')

    assert_no_new_post_or_correlation()
    observed = client.observed_accepted_response
    if observed is None:
        # 204 cannot provide an independently verified remote ID; NEVER infer.
        raise RuntimeError('FC08_R1R27_REAL_RESPONSE_NOT_OBSERVED')
    # This ID was obtained by the fault injector from a *real* 200 response
    # before it was deliberately discarded, NOT from the unknown journal or
    # a candidate run search. It is used for a separate GET-only observation.
    if (observed.get('state') != 'accepted-correlated'
            or type(observed.get('run_id')) is not int
            or observed['run_id'] <= 0):
        raise RuntimeError('FC08_R1R27_REAL_POST_NOT_CORRELATED')
    workflow = original.get('workflow_id')
    if type(workflow) is not int or workflow <= 0:
        raise RuntimeError('FC08_R1R27_REAL_POST_NOT_CORRELATED')
    remote = _cycle.remote_proof(client, identity,
        {'run_id': observed['run_id'], 'workflow_id': workflow},
        'success', sleep=sleep, polls=polls, interval=interval)
    if remote.get('conclusion') != 'success':
        raise RuntimeError('FC08_R1R27_PROBE_NOT_COMPLETED')
    assert_no_new_post_or_correlation()
    return {'schema': FAMILY, 'identity': identity,
            'fault_type': 'INJECTED_RESPONSE_LOSS_AFTER_REAL_ACCEPTED_200',
            'real_github_post_count': 1,
            'injected_loss_safety_proven': True,
            'unknown_journal_retained': True,
            'unknown_journal_request_id': request_id,
            'unknown_journal_state': 'dispatch-outcome-unknown',
            'unknown_journal_run_id': None,
            'resume_attempts': 2, 'extra_post_count': 0,
            'safe_to_redispatch': False,
            'separate_fault_injector_observed_remote': remote,
            'real_network_timeout_or_5xx_proven': False,
            'real_unknown_outcome_reconciled': False,
            'unknown_outcome_retry_authorized': False,
            'fc08_overall_qualified': False, 'release_qualified': False,
            'device_qualified': False, 'consumer_qualified': False}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    out = Path(args.out)
    if out.is_symlink() or (out.exists() and not out.is_file()):
        print('BLOCKED_FC08_R1R27_UNSAFE_OUTPUT', file=sys.stderr)
        return 2
    try:
        identity = _cycle.source_identity(dict(os.environ), Path(__file__).resolve().parents[1])
        token = os.environ.get('GITHUB_TOKEN', '')
        if not token or '\r' in token or '\n' in token:
            raise RuntimeError('FC08_R1R27_OPT_IN_OR_TOKEN_MISSING')
        client = LostResponseClient(token)
        with tempfile.TemporaryDirectory(prefix='fc08-r1r27-unknown-') as tmp:
            payload = execute(client, identity, Path(tmp) / 'journal')
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, sort_keys=True, indent=2) + '\n')
        print('PASS_FC08_R1R27_REAL_POST_INJECTED_RESPONSE_LOSS_NON_RELEASE')
        return 0
    except (OSError, ValueError, RuntimeError, GitHubActionsError) as exc:
        category = str(exc).split(':', 1)[0]
        if category not in SAFE_FAILURE_CATEGORIES:
            category = 'EXTERNAL_DISPATCH_UNVERIFIED'
        safe = {'schema': FAMILY, 'status': 'BLOCKED_UNQUALIFIED',
                'error_type': type(exc).__name__, 'error_category': category,
                'real_network_timeout_or_5xx_proven': False,
                'fc08_overall_qualified': False, 'release_qualified': False}
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(safe, sort_keys=True, indent=2) + '\n')
        except OSError:
            pass
        print('BLOCKED_FC08_R1R27:' + category, file=sys.stderr)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
