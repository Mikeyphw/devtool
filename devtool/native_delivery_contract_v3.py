"""Data-only, fail-closed native overlay delivery v3 contract (M1).

This module does not resolve a runtime, apply an overlay, mutate Git, contact
GitHub, authorize operations, or qualify CI. Those are later milestones and
remain owned by Devtool's existing native authorities.
"""
from __future__ import annotations

from pathlib import PurePosixPath
import re
from typing import Any, Mapping


class NativeDeliveryContractError(ValueError):
    """Manifest requested an unsupported or unsafe native delivery contract."""


_SHA1 = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SLUG = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
_GITHUB = re.compile(r"(?:git@github\.com:|ssh://git@github\.com/|https://github\.com/)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?\Z", re.I)
_BRANCH = re.compile(r"[A-Za-z0-9_./-]{1,180}\Z")
_LIMIT = 48
_VERIFY = frozenset({"commit", "tree", "launcher-module-blob", "cli-schema-blob"})
_BIND = frozenset({"artifact-digest", "transaction-id", "result-commit", "branch"})


def _refuse(reason: str) -> None:
    raise NativeDeliveryContractError("native delivery v3: " + reason)


def _fields(value: Any, expected: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        _refuse(label + " must contain exactly " + ", ".join(sorted(expected)) +
                " (unknown and missing capabilities are forbidden)")
    return value


def _is_int(value: Any, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def _sha1(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SHA1.fullmatch(value):
        _refuse(label + " requires exact lowercase Git SHA-1")
    return value


def _sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        _refuse(label + " requires exact lowercase SHA-256")
    return value


def _string(value: Any, label: str, limit: int = 160) -> str:
    if (not isinstance(value, str) or not value or len(value) > limit
            or value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        _refuse(label + " requires a bounded nonempty plain string")
    return value


def _branch(value: Any, label: str) -> str:
    value = _string(value, label, 180)
    if (not _BRANCH.fullmatch(value) or value.startswith(('.', '/'))
            or value.endswith(('.', '/')) or '..' in value or '//' in value
            or '/.' in value or value == 'HEAD' or value.endswith('.lock')):
        _refuse(label + " is not a safe named Git branch")
    return value


def _path(value: Any, label: str) -> str:
    value = _string(value, label, 220)
    pure = PurePosixPath(value)
    if (pure.is_absolute() or pure.as_posix() != value
            or '\\' in value or any(x in ('', '.', '..', '.git') for x in value.split('/'))
            or ':' in value):
        _refuse(label + " has traversal, aliasing or a reserved segment")
    return value


def _git_slug(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SLUG.fullmatch(value):
        _refuse(label + " must be an exact owner/repository slug")
    return value


def _github_origin(value: Any) -> str:
    if not isinstance(value, str):
        _refuse('repository.remote must be a canonical GitHub origin')
    match = _GITHUB.fullmatch(value)
    if match is None:
        _refuse('repository.remote must be a canonical GitHub SSH/HTTPS origin')
    return _git_slug(match.group(1), 'repository.remote')


def _required_executable_validation(manifest: Mapping[str, Any]) -> None:
    raw = manifest.get('validation')
    if not isinstance(raw, dict) or raw.get('required') is not True or raw.get('static_only') is True:
        _refuse('validation.required=true with real executable checks is mandatory')
    if raw.get('mode') == 'disabled':
        _refuse('validation mode cannot disable required execution')
    has_script = isinstance(raw.get('script'), dict) and bool(raw['script'].get('path'))
    has_tasks = isinstance(raw.get('tasks'), list) and any(isinstance(x, str) and x.strip() for x in raw['tasks'])
    has_refs = isinstance(raw.get('refs'), list) and bool(raw['refs'])
    has_tests = isinstance(raw.get('tests'), list) and any(
        isinstance(test, str) and bool(test) or
        isinstance(test, dict) and test.get('required', True) is True and bool(
            test.get('ref') or test.get('command') or test.get('task'))
        for test in raw['tests']
    )
    has_phases = any(raw.get(key) is True for key in ('restore', 'build', 'test'))
    if not (has_script or has_tasks or has_refs or has_tests or has_phases):
        _refuse('required-validation has no executable task, test, ref, script or phase')


def normalize_delivery_contract(manifest: Mapping[str, Any]) -> dict[str, Any] | None:
    """Validate v3 and return declarative *intent*, never execution evidence.

    v1/v2 without this extension retain their existing manifest contracts.
    The native verifier invokes this from artifact validation and inspection.
    """
    schema = manifest.get('schema_version', 1)
    extension = manifest.get('delivery_contract')
    if schema != 3:
        if extension is not None:
            _refuse('delivery_contract requires schema_version=3; legacy schemas cannot ignore it')
        return None
    if extension is None:
        _refuse('schema_version=3 requires delivery_contract; no implicit opt-in')
    if manifest.get('type') != 'overlay':
        _refuse('delivery v3 is supported only for individual overlay manifests')
    if any(k in manifest for k in ('delivery', 'delivery_v3')):
        _refuse('duplicate or competing delivery declaration')
    if manifest.get('base_contract_mode') != 'exact-tree':
        _refuse('base_contract_mode must be exact-tree')
    _sha1(manifest.get('base_commit'), 'base_commit')
    _sha1(manifest.get('base_tree'), 'base_tree')
    source = manifest.get('repository')
    if not isinstance(source, dict):
        _refuse('repository must be an object with enforced GitHub origin and branch')
    for required_key in ('remote', 'branch', 'enforce_remote', 'enforce_branch'):
        if required_key not in source:
            _refuse('repository.' + required_key + ' is required')
    origin = _github_origin(source['remote'])
    branch = _branch(source['branch'], 'repository.branch')
    if source['enforce_remote'] is not True or source['enforce_branch'] is not True:
        _refuse('repository requires enforced remote and named branch')
    inventory = _fields(manifest.get('inventory'), {'format', 'entries'}, 'inventory')
    if inventory['format'] != 1 or type(inventory['format']) is not int:
        _refuse('inventory.format must be integer 1')
    entries = inventory['entries']
    if not isinstance(entries, list) or not 1 <= len(entries) <= _LIMIT:
        _refuse('inventory must contain 1..48 reviewed changes')
    touched: list[str] = []
    before: dict[str, str] = {}
    after: dict[str, str | None] = {}
    for item in entries:
        if not isinstance(item, dict):
            _refuse('inventory entries must be objects')
        action = item.get('action')
        if action not in ('add', 'modify', 'delete'):
            _refuse('inventory action must be add, modify or delete')
        path = _path(item.get('path'), 'inventory.path')
        if path in touched:
            _refuse('duplicate inventory path: ' + path)
        touched.append(path)
        if action != 'add':
            before[path] = _sha256(item.get('source_sha256'), 'inventory.source_sha256')
        if action != 'delete':
            after[path] = _sha256(item.get('sha256'), 'inventory.sha256')
        else:
            after[path] = None
        if 'size' in item and not _is_int(item['size']):
            _refuse('inventory.size must be a nonnegative integer')
    if manifest.get('touched_paths') != sorted(touched):
        _refuse('touched_paths must match exact sorted inventory')
    if manifest.get('source_sha256') != {p: before.get(p) for p in touched}:
        _refuse('source_sha256 must match inventory preimages, including null additions')
    if manifest.get('expected_post_sha256') != after:
        _refuse('expected_post_sha256 must match inventory payload hashes')
    _required_executable_validation(manifest)
    apply = manifest.get('apply')
    if not isinstance(apply, dict) or apply.get('atomic') is not True or apply.get('requires_clean_worktree') is not True:
        _refuse('apply.atomic and apply.requires_clean_worktree must be true')
    commit = _fields(apply.get('commit'), {'enabled', 'strategy', 'message'}, 'apply.commit')
    if commit['enabled'] is not True or commit['strategy'] != 'single':
        _refuse('apply.commit requires enabled=true, strategy=single')
    _string(commit['message'], 'apply.commit.message', 240)
    if any(k in manifest for k in ('commit', 'git', 'commit_message')):
        _refuse('duplicate root commit policy conflicts with apply.commit')

    doc = _fields(extension, {'version', 'runtime', 'session', 'remote', 'ci', 'recovery'}, 'delivery_contract')
    if not _is_int(doc['version'], 1) or doc['version'] != 1:
        _refuse('unsupported delivery_contract.version (expected integer 1)')
    runtime = _fields(doc['runtime'], {'compatibility', 'resolution', 'verify'}, 'delivery_contract.runtime')
    compatibility = _fields(runtime['compatibility'], {'minimum', 'major'}, 'runtime.compatibility')
    version = compatibility['minimum']
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        _refuse('runtime.compatibility.minimum must be X.Y.Z')
    parsed = tuple(int(part) for part in version.split('.'))
    if parsed < (3, 2, 0) or compatibility['major'] != 3 or type(compatibility['major']) is not int or parsed[0] != 3:
        _refuse('native delivery requires compatible 3.x runtime >=3.2.0')
    if runtime['resolution'] != 'trusted-verified-compatible':
        _refuse('unsupported runtime resolver (M2 does not execute here)')
    if (not isinstance(runtime['verify'], list) or len(runtime['verify']) != len(_VERIFY)
            or not all(isinstance(item, str) for item in runtime['verify'])
            or set(runtime['verify']) != _VERIFY):
        _refuse('runtime.verify must attest commit/tree/launcher module/CLI schema exactly')
    session = _fields(doc['session'], {'after_commit', 'bind_to'}, 'delivery_contract.session')
    if (session['after_commit'] != 'adopt-or-resume' or not isinstance(session['bind_to'], list)
            or len(session['bind_to']) != len(_BIND)
            or not all(isinstance(item, str) for item in session['bind_to'])
            or set(session['bind_to']) != _BIND):
        _refuse('unsupported session adoption or commit identity bindings')
    remote = _fields(doc['remote'], {'base_repository', 'base_branch', 'base_sha', 'push', 'pull_request'}, 'delivery_contract.remote')
    upstream = _git_slug(remote['base_repository'], 'remote.base_repository')
    base_branch = _branch(remote['base_branch'], 'remote.base_branch')
    base_sha = _sha1(remote['base_sha'], 'remote.base_sha')
    if base_branch == branch and upstream.casefold() == origin.casefold():
        _refuse('delivery from its own PR base branch is forbidden')
    if remote['push'] != 'requires-explicit-approval':
        _refuse('remote.push cannot authorize implicit mutation')
    pr = _fields(remote['pull_request'], {'mode', 'draft', 'title'}, 'remote.pull_request')
    if pr['mode'] != 'requires-explicit-approval' or pr['draft'] is not True:
        _refuse('PR must be draft and explicitly approved')
    _string(pr['title'], 'remote.pull_request.title', 160)
    ci = _fields(doc['ci'], {'policy_path', 'observe', 'exact_pr_head', 'qualification'}, 'delivery_contract.ci')
    if (ci['policy_path'] != 'config/delivery-required-ci.json'
            or ci['observe'] != 'on-explicit-request'
            or ci['exact_pr_head'] is not True
            or ci['qualification'] != 'required-workflows-only'):
        _refuse('unsupported CI policy, implicit observer or non-exact PR-head qualification')
    recovery = _fields(doc['recovery'], {'after_commit', 'unknown_remote_outcome'}, 'delivery_contract.recovery')
    if recovery['after_commit'] != 'resume-only' or recovery['unknown_remote_outcome'] != 'read-only-reconcile':
        _refuse('recovery must be resume-only and read-only for uncertain remote outcomes')
    return {
        'schema': 'devtool.native-delivery-contract.v1',
        'status': 'declared-not-executed',
        'source': {'repository': origin, 'branch': branch,
                   'base_commit': manifest['base_commit'], 'base_tree': manifest['base_tree'],
                   'inventory_paths': sorted(touched)},
        'runtime': {'minimum': version, 'major': 3, 'verified': False},
        'session': {'policy': session['after_commit'], 'adopted': False},
        'remote': {'base_repository': upstream, 'base_branch': base_branch,
                   'base_sha': base_sha, 'approved': False},
        'ci': {'policy_path': ci['policy_path'], 'state': 'NOT_QUALIFIED', 'observed': False},
        'recovery': {'postcommit': 'resume-only', 'remote': 'read-only-reconcile'},
    }


def inspect_delivery_contract(manifest: Mapping[str, Any]) -> dict[str, Any] | None:
    """Safe, read-only inspect projection, including actionable refusal reason."""
    try:
        return normalize_delivery_contract(manifest)
    except NativeDeliveryContractError as exc:
        return {'schema': 'devtool.native-delivery-contract.v1', 'status': 'REFUSED',
                'reason': str(exc), 'runtime_verified': False, 'ci_state': 'NOT_QUALIFIED'}
