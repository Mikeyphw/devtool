"""Native M3/M4 base-pin recovery for an existing committed delivery session.

One journal authority (delivery_session), one GitHub GET transport (M4 App),
no second token issuer, no PR creation, and no implicit source replay.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping

from . import delivery_session as ds

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_SESSION = re.compile(r"[0-9a-f]{32}\Z")


class BasePinRefused(ds.DeliverySessionError):
    """Pinned, non-secret explanation; no provider body or credential material."""


def _sha(value: str, code: str) -> str:
    if not isinstance(value, str) or _SHA.fullmatch(value) is None:
        raise BasePinRefused(code)
    return value


def _git(root: Path, *args: str, allow_rc1: bool = False) -> bool:
    """Read-only Git graph proof, with replace objects disabled."""
    try:
        p = subprocess.run(
            ['git', '-C', str(root), *args], capture_output=True,
            text=True, check=False, timeout=20,
            env={**os.environ, 'GIT_NO_REPLACE_OBJECTS': '1',
                 'GIT_OPTIONAL_LOCKS': '0', 'GIT_TERMINAL_PROMPT': '0'},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BasePinRefused('BASE_PIN_GIT_GRAPH_UNAVAILABLE') from None
    if p.returncode == 0:
        return True
    if allow_rc1 and p.returncode == 1:
        return False
    raise BasePinRefused('BASE_PIN_GIT_GRAPH_UNAVAILABLE')


def _assert_graph(root: Path, old: str, new: str, source: str) -> None:
    for sha in (old, new, source):
        if not _git(root, 'cat-file', '-e', sha + '^{commit}', allow_rc1=True):
            raise BasePinRefused('BASE_PIN_COMMIT_OBJECT_NOT_AVAILABLE')
    if not _git(root, 'merge-base', '--is-ancestor', old, new, allow_rc1=True):
        raise BasePinRefused('BASE_PIN_UPSTREAM_NOT_FAST_FORWARD')
    if not _git(root, 'merge-base', '--is-ancestor', new, source, allow_rc1=True):
        raise BasePinRefused('BASE_PIN_NEW_MAIN_NOT_IN_SOURCE')


def _live(client: Any, repo: str, branch: str, pr_number: int,
          head_repo: str, head_branch: str, source_sha: str, base_sha: str) -> dict[str, Any]:
    """Observe both branch and PR using the same operation-scoped App client."""
    from .delivery_github import _remote_head
    branch_tip = _remote_head(client, repo, branch)
    if branch_tip != base_sha:
        raise BasePinRefused('BASE_PIN_REMOTE_BRANCH_CHANGED')
    # Exact PR resource GET, not a POST, list alias or mutable cached source.
    try:
        pr = client._get(f'/repos/{repo}/pulls/{pr_number}')
    except Exception:
        raise BasePinRefused('BASE_PIN_PR_READ_UNAVAILABLE') from None
    if not isinstance(pr, dict):
        raise BasePinRefused('BASE_PIN_PR_IDENTITY_UNAVAILABLE')
    h, b = pr.get('head'), pr.get('base')
    if not isinstance(h, dict) or not isinstance(b, dict):
        raise BasePinRefused('BASE_PIN_PR_IDENTITY_UNAVAILABLE')
    if (pr.get('number') != pr_number or pr.get('state') != 'open'
            or h.get('ref') != head_branch or h.get('sha') != source_sha
            or (h.get('repo') or {}).get('full_name', '').casefold() != head_repo.casefold()
            or b.get('ref') != branch or b.get('sha') != base_sha
            or (b.get('repo') or {}).get('full_name', '').casefold() != repo.casefold()):
        raise BasePinRefused('BASE_PIN_PR_SOURCE_OR_BASE_MISMATCH')
    return {'number': pr_number, 'head_sha': source_sha, 'base_sha': base_sha,
            'base': branch, 'head': head_branch,
            'url': f'https://github.com/{repo}/pull/{pr_number}',
            'head_repository': head_repo, 'base_repository': repo}


def advance_base_pin(session_id: str, *, expected_old_sha: str,
                     verified_new_sha: str, existing_pr_number: int,
                     approved: bool, auth: str = 'app',
                     state_root: Path | None = None,
                     client: Any = None) -> dict[str, Any]:
    """Advance an existing session pin after independent live and graph checks.

    All writes go through delivery_session._locked/_write; _write reseals the
    canonical journal and fsyncs the replacement.  Previous events, push
    intents, native history references and source identity are retained.
    Repeated identical reconciliation returns ALREADY_ADVANCED, not a write.
    """
    if approved is not True:
        raise BasePinRefused('BASE_PIN_EXPLICIT_APPROVAL_REQUIRED')
    if not isinstance(session_id, str) or not _SESSION.fullmatch(session_id):
        raise BasePinRefused('BASE_PIN_SESSION_ID_INVALID')
    old = _sha(expected_old_sha, 'BASE_PIN_OLD_SHA_INVALID')
    new = _sha(verified_new_sha, 'BASE_PIN_NEW_SHA_INVALID')
    if type(existing_pr_number) is not int or existing_pr_number < 1:
        raise BasePinRefused('BASE_PIN_EXISTING_PR_NUMBER_REQUIRED')
    if auth != 'app':
        raise BasePinRefused('BASE_PIN_SCOPED_APP_AUTH_REQUIRED')
    if old == new:
        raise BasePinRefused('BASE_PIN_NO_ADVANCEMENT')

    folder = ds._state_root(state_root, identity=session_id)
    ds._directory(folder, create=False)
    # Lock spans both provider reads and journal reseal: a second writer cannot
    # race an old source pin into the same canonical delivery session.
    with ds._locked(folder):
        path = ds._path(folder, session_id)
        record = ds._read(path)  # Schema, mode, SHA and journal digest verified.
        if record.get('id') != session_id:
            raise BasePinRefused('BASE_PIN_SESSION_ID_DRIFT')
        current = record.get('base_sha')
        if current not in (old, new):
            raise BasePinRefused('BASE_PIN_UNEXPECTED_OLD_SHA')
        if record.get('remote_verified') is not True:
            raise BasePinRefused('BASE_PIN_PUSH_NOT_VERIFIED')
        if (record.get('native_apply') or {}).get('state') != 'COMMITTED':
            raise BasePinRefused('BASE_PIN_NATIVE_COMMIT_UNVERIFIED')
        intents = record.get('intents')
        if not isinstance(intents, list) or any(i.get('state') != 'confirmed' for i in intents):
            raise BasePinRefused('BASE_PIN_UNRESOLVED_INTENTS')
        if not any(i.get('operation') == 'push' for i in intents):
            raise BasePinRefused('BASE_PIN_VERIFIED_PUSH_INTENT_MISSING')
        bound = record.get('pr') or {}
        if bound and (bound.get('number') != existing_pr_number or
                      bound.get('head_sha') != record.get('head_sha')):
            raise BasePinRefused('BASE_PIN_EXISTING_JOURNALED_PR_CONFLICT')
        source = _sha(record.get('head_sha'), 'BASE_PIN_SOURCE_SHA_INVALID')
        root = Path(record['repo_root'])
        if root.is_symlink() or not root.is_dir():
            raise BasePinRefused('BASE_PIN_SOURCE_ROOT_UNAVAILABLE')
        identity = ds._identity(root, head=record['head'], base=record['base'])
        if (identity['head_sha'] != source or identity['git_dir'] != record['git_dir']
                or identity['repository'].casefold() != record['repository'].casefold()):
            raise BasePinRefused('BASE_PIN_SOURCE_IDENTITY_DRIFT')
        if record.get('base_repository', record['repository']).casefold() != record['repository'].casefold():
            raise BasePinRefused('BASE_PIN_CROSS_REPOSITORY_REQUIRES_SEPARATE_POLICY')
        if record.get('head_repository', record['repository']).casefold() != record['repository'].casefold():
            raise BasePinRefused('BASE_PIN_CROSS_OWNER_HEAD_REQUIRES_SEPARATE_POLICY')
        _assert_graph(root, old, new, source)
        if client is None:
            from .github_app import make_app_client
            client = make_app_client(record['repository'], 'pr_read')
        repo = record['repository']
        args = (client, repo, record['base'], existing_pr_number,
                record.get('head_repository') or repo, record['head'], source, new)
        first = _live(*args)
        second = _live(*args)
        if first != second:
            raise BasePinRefused('BASE_PIN_REMOTE_READ_CHANGED')
        changed = current != new
        if changed:
            at = ds._utc()
            record['base_sha'] = new
            record['updated_at'] = at
            record['events'].append({
                'kind': 'upstream_base_pin_advanced', 'at': at,
                'from_sha': old, 'to_sha': new, 'source_sha': source,
                'base_repository': repo, 'base_branch': record['base'],
                'existing_pr_number': existing_pr_number,
                'verification': 'TWO_LIVE_READS_AND_LOCAL_ANCESTRY',
                'remote_mutation': False,
            })
            ds._write(path, record)
    # Native existing-PR writer validates canonical URL, source and base.
    # This requires no PR-create credential and emits no GitHub write. A crash
    # between journal updates is recoverable by a subsequent re-attested call.
    observed = ds.record_pr_observation(session_id, first, state_root=state_root)
    if observed.get('number') != existing_pr_number:
        raise BasePinRefused('BASE_PIN_EXISTING_PR_NOT_RECORDED')
    return {'schema': 'devtool.delivery-base-pin.v1',
            'state': 'BASE_PIN_ADVANCED' if changed else 'ALREADY_ADVANCED',
            'session_id': session_id, 'source_sha': source,
            'old_base_sha': old, 'new_base_sha': new,
            'existing_pr': existing_pr_number, 'journal_changed': changed,
            'remote_mutation': False, 'pr_recorded': True,
            'pr_state': 'PR_VERIFIED'}



def _show_status(session_id: str, *, plain: bool = False) -> None:
    from .operator_status import project_delivery_status, render_status_text
    st = ds.status(session_id)
    projection = project_delivery_status(st)
    if not plain and os.isatty(1):
        try:
            from .operator_rich import render_operator
            # Existing Rich/TTY owner, no new Live or input processing.
            args = argparse.Namespace(ui='rich', no_color=False, plain=False)
            if render_operator('operator_status', projection, args=args):
                return
        except (ImportError, ValueError, TypeError):
            pass
    print(render_status_text(projection))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog='devtool.delivery_base_reconcile')
    p.add_argument('action', choices=('inspect', 'advance'))
    p.add_argument('--id', required=True)
    p.add_argument('--old-base-sha')
    p.add_argument('--new-base-sha')
    p.add_argument('--pr-number', type=int)
    p.add_argument('--auth', choices=('app',), default='app')
    p.add_argument('--yes', action='store_true')
    p.add_argument('--plain', action='store_true')
    p.add_argument('--json', action='store_true')
    args = p.parse_args(argv)
    try:
        if args.action == 'inspect':
            if args.yes:
                raise BasePinRefused('BASE_PIN_INSPECT_IS_READ_ONLY')
            st = ds.status(args.id)
            result = {'schema': 'devtool.delivery-base-pin.inspect.v1',
                      'session_id': st['id'], 'source_sha': st['head_sha'],
                      'base_sha': st.get('base_sha'), 'resumable': st['resumable'],
                      'source_binding': st.get('source_binding'),
                      'unresolved_intents': [i['operation'] for i in st['intents']
                         if i['state'] != 'confirmed'],
                      'pr_number': (st.get('pr') or {}).get('number'),
                      'remote_mutation': False}
        else:
            result = advance_base_pin(args.id,
                 expected_old_sha=args.old_base_sha,
                 verified_new_sha=args.new_base_sha,
                 existing_pr_number=args.pr_number, approved=args.yes,
                 auth=args.auth)
            # Native PR observation was already committed from read-only evidence.
        if args.json:
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        elif args.action == 'inspect':
            _show_status(args.id, plain=args.plain)
        else:
            print('BASE_PIN_RECONCILE='+result['state'])
            print('PR_OBSERVATION='+result.get('pr_state', 'PENDING'))
            _show_status(args.id, plain=args.plain)
        return 0
    except (BasePinRefused, ds.DeliverySessionError, OSError, ValueError) as exc:
        print('BASE_PIN_REFUSED '+str(exc), file=__import__('sys').stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
