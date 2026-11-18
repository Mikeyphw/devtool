"""Explicit, committed required-CI policy for Devtool delivery sessions.

This policy qualifies *CI evidence only*. It does not publish releases, infer
branch-protection settings, or infer required workflows from observed runs.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import stat
import subprocess
from typing import Any, Mapping

from .delivery_session import DeliverySessionError

POLICY_PATH = 'config/delivery-required-ci.json'
_MAX_BYTES = 64 * 1024
_SLUG = re.compile(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z')
_NAME = re.compile(r'[^\r\n\t]{1,160}\Z')
_REF = re.compile(r'[A-Za-z0-9._/-]{1,200}\Z')


class DeliveryCIPolicyError(DeliverySessionError):
    """Policy is absent, mutable, ambiguous, or inconsistent with the session."""


@dataclass(frozen=True)
class RequiredWorkflow:
    name: str
    workflow_id: int


@dataclass(frozen=True)
class DeliveryCIPolicy:
    repository: str
    base_branch: str
    requirements: tuple[RequiredWorkflow, ...]
    sha256: str
    path: str = POLICY_PATH

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(row.name for row in self.requirements)

    @property
    def workflow_ids(self) -> dict[str, int]:
        return {row.name: row.workflow_id for row in self.requirements}

    def receipt(self) -> dict[str, Any]:
        return {'policy_path': self.path, 'policy_sha256': self.sha256,
                'required_workflows': [{'name': row.name, 'workflow_id': row.workflow_id}
                                       for row in self.requirements],
                'ci_qualification': 'required-workflows-only',
                'release_qualified': False}


def _git(root: Path, *args: str) -> bytes:
    try:
        p = subprocess.run(['git', '-C', str(root), *args], capture_output=True,
                           timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeliveryCIPolicyError('Cannot verify committed CI policy') from exc
    if p.returncode:
        raise DeliveryCIPolicyError('CI policy is not tracked in the current HEAD')
    return p.stdout


def _validate(raw: Any, *, expected_repository: str,
              expected_base: str, digest: str) -> DeliveryCIPolicy:
    if not isinstance(raw, dict) or set(raw) != {
            'schema_version', 'repository', 'base_branch', 'event',
            'required_workflows', 'release_qualification'}:
        raise DeliveryCIPolicyError('CI policy has missing or unsupported top-level fields')
    if type(raw['schema_version']) is not int or raw['schema_version'] != 1:
        raise DeliveryCIPolicyError('Unsupported CI policy schema version')
    slug = raw['repository']
    base = raw['base_branch']
    if not isinstance(slug, str) or not _SLUG.fullmatch(slug):
        raise DeliveryCIPolicyError('Policy repository must be a GitHub slug')
    if not isinstance(base, str) or not _REF.fullmatch(base) or '..' in base:
        raise DeliveryCIPolicyError('Invalid policy base branch')
    if slug.casefold() != expected_repository.casefold() or base != expected_base:
        raise DeliveryCIPolicyError('CI policy is for a different repository or base branch')
    if raw['event'] != 'pull_request':
        raise DeliveryCIPolicyError('CI policy must declare pull_request event')
    if raw['release_qualification'] != 'separate':
        raise DeliveryCIPolicyError('CI policy cannot qualify releases')
    items = raw['required_workflows']
    if not isinstance(items, list) or not 1 <= len(items) <= 25:
        raise DeliveryCIPolicyError('Required CI policy must declare 1..25 workflows')
    seen_names: set[str] = set()
    seen_ids: set[int] = set()
    requirements: list[RequiredWorkflow] = []
    for item in items:
        if not isinstance(item, dict) or set(item) != {'name', 'workflow_id'}:
            raise DeliveryCIPolicyError('Invalid required workflow entry')
        name, identity = item['name'], item['workflow_id']
        if (not isinstance(name, str) or not _NAME.fullmatch(name) or
                not name.strip() or name != name.strip()):
            raise DeliveryCIPolicyError('Required workflow has an invalid name')
        if type(identity) is not int or identity <= 0:
            raise DeliveryCIPolicyError('Required workflow must have a positive numeric GitHub workflow ID')
        if name in seen_names or identity in seen_ids:
            raise DeliveryCIPolicyError('Duplicate required workflow name or ID')
        seen_names.add(name)
        seen_ids.add(identity)
        requirements.append(RequiredWorkflow(name, identity))
    return DeliveryCIPolicy(slug, base, tuple(requirements), digest)


def load_policy(repo_root: Path, *, expected_repository: str,
                expected_base: str) -> DeliveryCIPolicy:
    """Read exact committed bytes. Untracked/symlink/modified policy is refused.

    HEAD policy bytes are compared with the working file; this permits concurrent
    source editing elsewhere, but never permits using a locally modified policy.
    """
    root = Path(repo_root).expanduser().resolve(strict=True)
    file = root / POLICY_PATH
    try:
        meta = file.lstat()
    except OSError as exc:
        raise DeliveryCIPolicyError('Committed required CI policy is missing: '+POLICY_PATH) from exc
    if not stat.S_ISREG(meta.st_mode) or meta.st_size > _MAX_BYTES:
        raise DeliveryCIPolicyError('CI policy must be a bounded regular file (no symlinks)')
    content = file.read_bytes()
    if len(content) > _MAX_BYTES:
        raise DeliveryCIPolicyError('CI policy exceeds size budget')
    # A staged deletion/replacement must not be accepted even when the on-disk
    # bytes match HEAD. HEAD and index both bind this policy.
    try:
        staged = subprocess.run(['git', '-C', str(root), 'diff', '--cached', '--quiet',
                                 'HEAD', '--', POLICY_PATH], timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeliveryCIPolicyError('Cannot inspect staged CI policy') from exc
    if staged.returncode != 0:
        raise DeliveryCIPolicyError('CI policy has staged changes')
    committed = _git(root, 'show', 'HEAD:' + POLICY_PATH)
    if committed != content:
        raise DeliveryCIPolicyError('CI policy differs from current Git HEAD')
    try:
        raw = json.loads(content.decode('utf-8'))
    except (ValueError, UnicodeDecodeError) as exc:
        raise DeliveryCIPolicyError('CI policy must be UTF-8 JSON') from exc
    return _validate(raw, expected_repository=expected_repository,
                     expected_base=expected_base,
                     digest=hashlib.sha256(content).hexdigest())


def check_requested(required: tuple[str, ...], policy: DeliveryCIPolicy) -> None:
    """Legacy --require-workflow flags may repeat the policy, never weaken it."""
    if required and (len(required) != len(set(required)) or set(required) != set(policy.names)):
        raise DeliveryCIPolicyError('Explicit --require-workflow list differs from committed CI policy')


def assert_observation(summary: Mapping[str, Any], policy: DeliveryCIPolicy) -> None:
    if summary.get('policy_sha256') != policy.sha256:
        raise DeliveryCIPolicyError('CI receipt is not bound to the current policy digest')
    if set(summary.get('required', ())) != set(policy.names):
        raise DeliveryCIPolicyError('CI receipt required set differs from committed policy')
    if summary.get('release_qualified') is not False:
        raise DeliveryCIPolicyError('CI receipt illegally asserted release qualification')
    if summary.get('state') == 'REQUIRED_CHECKS_PASSED':
        reg = summary.get('registration')
        if (not isinstance(reg, dict) or reg.get('event') != 'pull_request'
                or any(reg.get(k) for k in ('missing','ambiguous','indeterminate',
                                              'not_expected','dispatch_uncorrelated'))
                or type(reg.get('run_count')) is not int
                or reg['run_count'] < len(policy.requirements)
                or not reg.get('started_at') or not reg.get('deadline_at')):
            raise DeliveryCIPolicyError('CI pass lacks unambiguous event-bound registration evidence')
        protected = summary.get('branch_rules')
        if (not isinstance(protected, dict) or
                protected.get('protection_checked') is not True or
                protected.get('ruleset_checked') is not True or
                protected.get('required_status_contexts_verified') is not True or
                protected.get('release_qualified') is not False):
            raise DeliveryCIPolicyError('CI pass lacks verified branch/ruleset requirements')
        evidence = summary.get('exact_run_evidence')
        if (not isinstance(evidence, dict) or evidence.get('verified') is not True
                or evidence.get('release_qualified') is not False
                or evidence.get('evidence_scope') != 'per-run-GET-and-exact-attempt-jobs'):
            raise DeliveryCIPolicyError('CI pass lacks independently verified exact-run/attempt evidence')
        evidence_rows = {row['name']: row for row in evidence.get('runs', ())
                         if isinstance(row, dict) and 'name' in row}
        runs = {r['name']: r for r in summary.get('runs', ()) if isinstance(r, dict) and 'name' in r}
        if len(evidence_rows) != len(policy.requirements):
            raise DeliveryCIPolicyError('CI pass has incomplete exact-run evidence')
        for requirement in policy.requirements:
            row = runs.get(requirement.name)
            attested = evidence_rows.get(requirement.name)
            if (not row or row.get('workflow_id') != requirement.workflow_id or
                    row.get('status') != 'completed' or row.get('conclusion') != 'success'):
                raise DeliveryCIPolicyError('CI pass has missing or mismatched workflow ID/result')
            if (not attested or attested.get('run_id') != row.get('id') or
                    attested.get('attempt') != row.get('attempt') or
                    attested.get('workflow_id') != requirement.workflow_id or
                    attested.get('source_bound') is not True or
                    attested.get('jobs_scope') != 'exact-run-attempt' or
                    type(attested.get('jobs_success')) is not int or attested['jobs_success'] < 1):
                raise DeliveryCIPolicyError('CI pass has unbound exact-run/attempt job evidence')


def load_upstream_policy(client: Any, *, expected_repository: str,
                         expected_base: str, expected_base_sha: str | None = None
                         ) -> tuple[DeliveryCIPolicy, str]:
    """Read the upstream's committed policy on a *verified* base commit.

    A fork's own policy is not authoritative for upstream protected-branch CI.
    No insecure fallback to fork worktree bytes is permitted. The returned
    base SHA must be checked again after observing the required CI receipts.
    """
    from base64 import b64decode
    from binascii import Error as Base64Error
    from urllib.parse import quote

    if (not _SLUG.fullmatch(expected_repository) or
            not _REF.fullmatch(expected_base) or '..' in expected_base):
        raise DeliveryCIPolicyError('Invalid upstream repository or branch')
    if expected_base_sha is not None and not re.fullmatch(r'[0-9a-f]{40}',expected_base_sha):
        raise DeliveryCIPolicyError('Invalid pinned upstream SHA')
    try:
        branch = client._get(f'/repos/{expected_repository}/branches/{quote(expected_base,safe="")}')
        sha = (branch.get('commit') or {}).get('sha')
        if not isinstance(sha,str) or not re.fullmatch(r'[0-9a-f]{40}',sha):
            raise DeliveryCIPolicyError('Upstream branch SHA missing')
        if expected_base_sha is not None and sha != expected_base_sha:
            raise DeliveryCIPolicyError('Upstream base branch drifted from pin')
        obj = client._get(f'/repos/{expected_repository}/contents/{POLICY_PATH}?ref={sha}')
    except (OSError, ValueError, TypeError) as exc:
        raise DeliveryCIPolicyError('Unable to read upstream committed policy') from exc
    if (not isinstance(obj,dict) or obj.get('type') != 'file' or
            obj.get('path') != POLICY_PATH or obj.get('encoding') != 'base64' or
            not isinstance(obj.get('content'),str)):
        raise DeliveryCIPolicyError('Upstream policy file response invalid')
    try:
        raw = b64decode(''.join(obj['content'].split()).encode('ascii'),validate=True)
    except (UnicodeError, ValueError, Base64Error):
        raise DeliveryCIPolicyError('Upstream policy encoding invalid') from None
    if len(raw) > _MAX_BYTES:
        raise DeliveryCIPolicyError('Upstream policy exceeds content budget')
    blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
    if obj.get('sha') != blob:
        raise DeliveryCIPolicyError('Upstream policy content differs from Git blob identity')
    try:
        doc = json.loads(raw.decode('utf-8'))
    except (ValueError,UnicodeError):
        raise DeliveryCIPolicyError('Upstream policy has invalid JSON') from None
    result = _validate(doc, expected_repository=expected_repository,
                       expected_base=expected_base,
                       digest=hashlib.sha256(raw).hexdigest())
    return result,sha
