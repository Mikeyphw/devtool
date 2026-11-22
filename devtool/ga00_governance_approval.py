"""GA-00 separate owner-approved branch safeguard: preview -> confirm -> durable intent -> POST.

NEVER called by ACTIONS-09 release setup. Does NOT claim that GitHub required
status-check contexts match the committed six-workflow CI policy.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any
from urllib.parse import quote

from .actions_setup import _branch, _gh
from .github_actions import _slug


class GovernanceApprovalRefusal(RuntimeError):
    pass


def _object(path: str) -> Any:
    try:
        return json.loads(_gh(['api', '--method', 'GET', path]))
    except (ValueError, OSError, Exception):
        raise GovernanceApprovalRefusal('GA00_OWNER_LIVE_GET_FAILED') from None


def _policy(branch: str) -> dict:
    return {'name': 'Devtool release safeguard: ' + branch,
            'target': 'branch', 'enforcement': 'active',
            'conditions': {'ref_name': {'include': ['refs/heads/' + branch], 'exclude': []}},
            'rules': [{'type': 'deletion'}, {'type': 'non_fast_forward'}]}


def preview(repository: str, branch: str = 'main') -> dict[str, Any]:
    slug = _slug(repository)
    branch = _branch(branch)
    # This method is GET-only; it must not be invoked from release_setup.
    identity = _object('user')
    repo = _object('repos/' + slug)
    remote = _object('repos/' + slug + '/commits/' + quote(branch, safe=''))
    protected = _object('repos/' + slug + '/branches/' + quote(branch, safe=''))
    rulesets = _object('repos/' + slug + '/rulesets?includes_parents=false')
    if (not isinstance(identity, dict) or not isinstance(identity.get('login'), str)
            or not isinstance(repo, dict) or repo.get('full_name', '').casefold() != slug.casefold()
            or type((repo.get('permissions') or {}).get('admin')) is not bool
            or not isinstance(remote, dict) or not re.fullmatch('[a-f0-9]{40}', str(remote.get('sha', '')))
            or not isinstance(protected, dict) or type(protected.get('protected')) is not bool
            or not isinstance(rulesets, list) or len(rulesets) > 100
            or any(not isinstance(x, dict) or type(x.get('id')) is not int or x['id'] <= 0 or
                   not isinstance(x.get('updated_at'), str) for x in rulesets)):
        raise GovernanceApprovalRefusal('GA00_OWNER_LIVE_IDENTITY_UNVERIFIED')
    if not re.fullmatch('[A-Za-z0-9-]{1,39}', identity['login']):
        raise GovernanceApprovalRefusal('GA00_OWNER_LOGIN_INVALID')
    snap = sorted(({'id': x['id'], 'updated_at': x['updated_at']} for x in rulesets), key=lambda x: x['id'])
    draft = {'schema': 'devtool.ga00.owner-governance-preview.v1',
             'repository': slug, 'branch': branch,
             'source_sha': remote['sha'],
             'actor_login': identity['login'],
             'actor_admin': repo['permissions']['admin'],
             'already_protected': protected['protected'],
             'rulesets_snapshot': snap, 'proposed_additive_ruleset': _policy(branch)}
    digest = hashlib.sha256(json.dumps(draft, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {**draft, 'approval_digest': digest, 'effect': 'NO_MUTATION_PREVIEW',
            'approval_required': 'separate exact-digest owner action',
            'six_required_ci_workflows_enforced': False,
            'source_review_required': True}


def _state_path(root: Path, repository: str, branch: str) -> Path:
    if not root.is_absolute() or root.is_symlink():
        raise GovernanceApprovalRefusal('GA00_OWNER_STATE_ROOT_UNSAFE')
    path = root / (hashlib.sha256((repository.casefold() + '\n' + branch).encode()).hexdigest() + '.jsonl')
    if root.is_symlink() or (path.exists() and (path.is_symlink() or not path.is_file())):
        raise GovernanceApprovalRefusal('GA00_OWNER_STATE_PATH_UNSAFE')
    return path


def _append(path: Path, row: dict, *, create: bool) -> None:
    flags = os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    flags |= os.O_CREAT | os.O_EXCL if create else os.O_APPEND
    fd = os.open(path, flags, 0o600)
    try:
        data = (json.dumps(row, sort_keys=True) + '\n').encode()
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    if create:
        dfd = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(dfd)
        finally: os.close(dfd)


def apply_approved(repository: str, branch: str, *, yes: bool,
                   owner_login: str, approval_digest: str, state_dir: Path) -> dict:
    if not yes or not re.fullmatch('[0-9a-f]{64}', approval_digest):
        raise GovernanceApprovalRefusal('GA00_EXPLICIT_OWNER_DIGEST_AND_YES_REQUIRED')
    current = preview(repository, branch)
    if (not current['actor_admin'] or current['actor_login'].casefold() != owner_login.casefold()
            or current['approval_digest'] != approval_digest or current['already_protected']):
        raise GovernanceApprovalRefusal('GA00_OWNER_AUTHORITY_OR_LIVE_PLAN_DRIFT')
    root = state_dir.expanduser().absolute()
    if root.exists() and (root.is_symlink() or not root.is_dir() or root.stat().st_uid != os.getuid()):
        raise GovernanceApprovalRefusal('GA00_OWNER_STATE_UNSAFE')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    path = _state_path(root, current['repository'], current['branch'])
    fields = {'schema': 'devtool.ga00.owner-governance.v1', 'source_sha': current['source_sha'],
              'repository': current['repository'], 'branch': current['branch'],
              'approval_digest': current['approval_digest'], 'actor': current['actor_login'],
              'at': datetime.now(timezone.utc).isoformat()}
    try:
        _append(path, {**fields, 'state': 'INTENT_BEFORE_POST'}, create=True)
    except FileExistsError:
        raise GovernanceApprovalRefusal('GA00_OWNER_PREVIOUS_MUTATION_INTENT_EXISTS_RECONCILE_MANUALLY') from None
    try:
        # The only POST in this module, AFTER separately approved source-bound intent.
        reply = _gh(['api', '--method', 'POST', 'repos/' + current['repository'] + '/rulesets', '--input', '-'],
                    input_text=json.dumps(current['proposed_additive_ruleset']), timeout=45)
        raw = json.loads(reply)
        if not isinstance(raw, dict) or type(raw.get('id')) is not int or raw['id'] <= 0:
            raise GovernanceApprovalRefusal('GA00_OWNER_POST_ACK_MALFORMED')
        _append(path, {**fields, 'state': 'ACK_POST', 'ruleset_id': raw['id']}, create=False)
        branch_remote = _object('repos/' + current['repository'] + '/branches/' + quote(branch, safe=''))
        if not isinstance(branch_remote, dict) or branch_remote.get('protected') is not True:
            raise GovernanceApprovalRefusal('GA00_OWNER_POST_NOT_YET_VERIFIED')
        _append(path, {**fields, 'state': 'PROTECTION_OBSERVED', 'ruleset_id': raw['id']}, create=False)
        return {'state': 'PROTECTION_OBSERVED', 'repository': current['repository'],
                'branch': current['branch'], 'source_sha': current['source_sha'],
                'ruleset_id': raw['id'], 'qualified_release': False}
    except (Exception,):
        # May already have POSTed. Never replay, auto-delete or claim rollback.
        raise GovernanceApprovalRefusal('GA00_OWNER_MUTATION_OUTCOME_UNKNOWN_OR_UNVERIFIED; inspect durable intent and GitHub rulesets; NEVER repeat') from None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description='Separate owner branch-governance approval; never automatic')
    p.add_argument('command', choices=('preview', 'approve'))
    p.add_argument('--repository', required=True)
    p.add_argument('--branch', default='main')
    p.add_argument('--owner-login', default='')
    p.add_argument('--approval-digest', default='')
    p.add_argument('--yes', action='store_true')
    p.add_argument('--state-dir', type=Path, default=Path.home()/'.local/state/devtool/ga00-governance')
    args = p.parse_args(argv)
    try:
        result = (preview(args.repository, args.branch) if args.command == 'preview'
                  else apply_approved(args.repository, args.branch, yes=args.yes,
                                      owner_login=args.owner_login,
                                      approval_digest=args.approval_digest, state_dir=args.state_dir))
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0
    except GovernanceApprovalRefusal as exc:
        print('GA00_OWNER_APPROVAL_REFUSED=' + str(exc))
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
