"""P16: prove selected-repository removal/restoration with the real App issuer.

The owner performs the selection edits separately in GitHub. This observer
never edits installation selection, repository settings, source, PRs or secrets.
Only fresh minimum-scope read credentials are minted, and never persisted.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from typing import Any

from .delivery_session import _locked, _write
from .ga00_governance_approval import _object
from .ga01_scoped_auth import AppOperationRefused, issue_for_operation
from .github_app import AppInstallationProvider, GitHubAppError, _jwt, _repository
from .github_installations import _repository_metadata
from .xdg import state_roots

_SCHEMA = 'devtool.ga00.p16-selection.v1'
_PHASES = ('baseline', 'denied', 'restored')
_UNAVAILABLE = 'APP_INSTALLATION_OR_REPOSITORY_UNAVAILABLE'


class SelectionRefusal(RuntimeError):
    """Only fixed diagnostic codes leave the security boundary."""


def _require(condition: Any, reason: str) -> None:
    if not condition:
        raise SelectionRefusal(reason)


def _sha(value: str) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r'[0-9a-f]{40}', value))


def _digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


@dataclass(frozen=True)
class SelectionPins:
    repository: str
    repository_id: int
    sandbox: str
    sandbox_id: int
    owner_id: int
    app_id: int
    installation_id: int
    source_sha: str
    source_tree: str

    def __post_init__(self) -> None:
        _repository(self.repository)
        _repository(self.sandbox)
        _require(self.repository.casefold() != self.sandbox.casefold() and
                 self.repository.split('/')[0].casefold() == self.sandbox.split('/')[0].casefold() and
                 self.repository_id != self.sandbox_id, 'P16_DISTINCT_SAME_OWNER_SANDBOX_REQUIRED')
        _require(all(type(v) is int and v > 0 for v in (self.repository_id, self.sandbox_id,
                 self.owner_id, self.app_id, self.installation_id)), 'P16_NUMERIC_PINS_INVALID')
        _require(_sha(self.source_sha) and _sha(self.source_tree), 'P16_SOURCE_PINS_INVALID')

    @property
    def identity(self) -> str:
        return _digest(asdict(self))


def _git(root: Path, *args: str) -> str:
    try:
        p = subprocess.run(['git', '-C', str(root), *args], text=True, capture_output=True, timeout=20,
                           env={**os.environ, 'GIT_NO_REPLACE_OBJECTS':'1', 'GIT_OPTIONAL_LOCKS':'0',
                                'GIT_TERMINAL_PROMPT':'0'})
    except (OSError, subprocess.SubprocessError):
        raise SelectionRefusal('P16_LOCAL_GIT_UNAVAILABLE') from None
    _require(p.returncode == 0, 'P16_LOCAL_GIT_FAILED')
    return p.stdout.strip()


def _source(root: Path, pins: SelectionPins) -> None:
    _require(root.resolve() == Path(_git(root, 'rev-parse', '--show-toplevel')).resolve() and
             _git(root, 'rev-parse', 'HEAD') == pins.source_sha and
             _git(root, 'rev-parse', 'HEAD^{tree}') == pins.source_tree and
             _git(root, 'symbolic-ref', '--short', 'HEAD') == 'main' and
             not _git(root, 'status', '--porcelain=v1', '--untracked-files=all'), 'P16_LOCAL_SOURCE_DRIFT')
    origin = _git(root, 'remote', 'get-url', 'origin')
    slug = pins.repository.casefold()
    _require(origin.casefold() in {f'https://github.com/{slug}', f'https://github.com/{slug}.git',
             f'git@github.com:{slug}', f'git@github.com:{slug}.git',
             f'ssh://git@github.com/{slug}', f'ssh://git@github.com/{slug}.git'}, 'P16_ORIGIN_DRIFT')
    remote = _object('repos/' + pins.repository + '/branches/main')
    _require(isinstance(remote, dict) and isinstance(remote.get('commit'), dict) and
             remote['commit'].get('sha') == pins.source_sha, 'P16_REMOTE_SOURCE_DRIFT')


def _owner(pins: SelectionPins) -> None:
    actor = _object('user')
    owner = pins.repository.split('/')[0].casefold()
    _require(isinstance(actor, dict) and actor.get('id') == pins.owner_id and
             str(actor.get('login', '')).casefold() == owner and actor.get('type') == 'User',
             'P16_OWNER_AUTHORITY_UNVERIFIED')
    for slug, rid in ((pins.repository, pins.repository_id), (pins.sandbox, pins.sandbox_id)):
        raw = _object('repos/' + slug)
        repo = _repository_metadata(slug, raw, allow_private=True)
        _require(repo['repository_id'] == rid and repo['owner_id'] == pins.owner_id and
                 not repo['archived'] and isinstance(raw.get('permissions'), dict) and
                 raw['permissions'].get('admin') is True, 'P16_OWNER_REPOSITORY_PIN_DRIFT')


class _Trace:
    """Capture only allowlisted endpoint/method facts, never request data."""
    def __init__(self, http: Any):
        self.http = http
        self.posts = 0
        self.last_failure: tuple[str, str, str] | None = None

    def request(self, method: str, path: str, credential: str, payload: dict | None = None) -> dict:
        if method == 'POST':
            self.posts += 1
        try:
            return self.http.request(method, path, credential, payload)
        except GitHubAppError as exc:
            self.last_failure = (method, path, str(exc))
            raise


def _installation(provider: AppInstallationProvider, pins: SelectionPins) -> None:
    c = provider.config
    _require((c.app_id, c.installation_id, c.account.casefold()) ==
             (pins.app_id, pins.installation_id, pins.repository.split('/')[0].casefold()), 'P16_APP_PIN_DRIFT')
    row = provider.http.request('GET', f'/app/installations/{pins.installation_id}', _jwt(c, int(provider.clock())))
    account = row.get('account')
    _require(row.get('id') == pins.installation_id and row.get('app_id') == pins.app_id and
             row.get('repository_selection') == 'selected' and row.get('suspended_at') is None and
             isinstance(account, dict) and account.get('id') == pins.owner_id and
             str(account.get('login', '')).casefold() == c.account.casefold() and account.get('type') == 'User',
             'P16_SELECTED_INSTALLATION_IDENTITY_REQUIRED')


def _probe(pins: SelectionPins, repository: str, rid: int, *, denied: bool) -> dict:
    # A new provider for each read deliberately excludes M4's token cache.
    provider = AppInstallationProvider.from_environment()
    _installation(provider, pins)
    trace = _Trace(provider.http)
    provider.http = trace
    try:
        access = issue_for_operation(repository, 'federation.source.fetch',
                                     expected_repository_id=rid, provider=provider)
    except AppOperationRefused as exc:
        _require(denied and str(exc) == _UNAVAILABLE and trace.posts == 0 and
                 trace.last_failure == ('GET', '/repos/' + repository + '/installation', _UNAVAILABLE),
                 'P16_EXPECTED_REPOSITORY_SELECTION_DENIAL_NOT_PROVEN')
        return {'repository': repository, 'repository_id': rid, 'result': 'SELECTION_DENIED',
                'endpoint': 'repository-installation', 'http_status': 404, 'token_mint_attempted': False}
    _require(not denied, 'P16_SANDBOX_STILL_SELECTED')
    _require((access.app_id, access.installation_id, access.repository_id) ==
             (pins.app_id, pins.installation_id, rid) and trace.posts == 1, 'P16_SCOPED_CREDENTIAL_DRIFT')
    # Do not use public repo metadata as evidence of selected-repository access.
    # Independently enumerate the actual fresh installation token's scope.
    scope = provider.http.request('GET', '/installation/repositories', access.token)
    repos = scope.get('repositories')
    _require(scope.get('total_count') == 1 and isinstance(repos, list) and len(repos) == 1 and
             isinstance(repos[0], dict) and repos[0].get('id') == rid and
             str(repos[0].get('full_name', '')).casefold() == repository.casefold(), 'P16_TOKEN_SCOPE_DRIFT')
    return {'repository': repository, 'repository_id': rid, 'result': 'SCOPED_READ_VERIFIED',
            'token_repository_count': 1, 'token_persisted': False, 'capability': access.capability}


def observe(root: Path, pins: SelectionPins, phase: str) -> dict:
    _require(phase in _PHASES, 'P16_PHASE_INVALID')
    _source(root, pins)
    _owner(pins)
    observations = []
    # Two independent observations bracket the sandbox with production controls.
    # A transient outage, 401, 403, unrelated 404, or suspended installation is
    # not revocation proof. No retry of repository mutation is possible here.
    for _ in range(2):
        control = _probe(pins, pins.repository, pins.repository_id, denied=False)
        sandbox = _probe(pins, pins.sandbox, pins.sandbox_id, denied=phase == 'denied')
        observations.append({'production': control, 'sandbox': sandbox})
    _owner(pins)
    _source(root, pins)
    return {'phase': phase, 'observed_at': datetime.now(timezone.utc).isoformat(),
            'observations': observations, 'remote_configuration_mutated': False,
            'secret_values_persisted': False}


def _read_record(path: Path, pins: SelectionPins) -> dict:
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as f:
        info = os.fstat(f.fileno())
        _require(stat.S_ISREG(info.st_mode) and info.st_size <= 65536 and
                 (os.name != 'posix' or info.st_uid == os.getuid() and not info.st_mode & 0o077),
                 'P16_RECEIPT_UNSAFE')
        record = json.loads(f.read(65537))
    _require(isinstance(record, dict), 'P16_RECEIPT_INVALID')
    checksum = record.pop('integrity_sha256', None)
    _require(checksum == _digest(record) and record.get('schema') == _SCHEMA and
             record.get('pins') == asdict(pins) and record.get('campaign_id') == pins.identity,
             'P16_RECEIPT_IDENTITY_OR_INTEGRITY_DRIFT')
    stages = record.get('stages')
    _require(isinstance(stages, list) and 1 <= len(stages) <= 3 and
             [x.get('phase') for x in stages if isinstance(x, dict)] == list(_PHASES[:len(stages)]) and
             record.get('selection_revocation_qualified') is (len(stages) == 3) and
             record.get('full_p16_qualified') is False and record.get('full_w3_qualified') is False,
             'P16_RECEIPT_SEQUENCE_INVALID')
    for stage in stages:
        observations = stage.get('observations')
        _require(isinstance(observations, list) and len(observations) == 2 and
                 stage.get('remote_configuration_mutated') is False and stage.get('secret_values_persisted') is False,
                 'P16_RECEIPT_OBSERVATION_INVALID')
        for observation in observations:
            production = observation.get('production', {})
            sandbox = observation.get('sandbox', {})
            _require(production == {'repository':pins.repository,'repository_id':pins.repository_id,
                         'result':'SCOPED_READ_VERIFIED','token_repository_count':1,
                         'token_persisted':False,'capability':'repository_read'}, 'P16_RECEIPT_CONTROL_INVALID')
            expected = ({'repository':pins.sandbox,'repository_id':pins.sandbox_id,
                         'result':'SELECTION_DENIED','endpoint':'repository-installation','http_status':404,
                         'token_mint_attempted':False} if stage['phase']=='denied' else
                        {'repository':pins.sandbox,'repository_id':pins.sandbox_id,
                         'result':'SCOPED_READ_VERIFIED','token_repository_count':1,
                         'token_persisted':False,'capability':'repository_read'})
            _require(sandbox == expected, 'P16_RECEIPT_SANDBOX_INVALID')
    return record


def campaign(root: Path, pins: SelectionPins, phase: str, *, state: Path | None = None) -> dict:
    _require(phase in (*_PHASES, 'status'), 'P16_PHASE_INVALID')
    directories = (state,) if state is not None else tuple(p/'governance'/'p16-selection-v1' for p in state_roots())
    filename = pins.identity + '.json'
    found = [p for p in directories if (p/filename).exists() or (p/filename).is_symlink()]
    _require(len(found) <= 1, 'P16_CONFLICTING_RECEIPT_ROOTS')
    folder = found[0] if found else directories[0]
    path = folder / filename
    if phase == 'status':
        _require(bool(found), 'P16_RECEIPT_MISSING')
        _require(not any(x.is_symlink() for x in (folder, folder.parent, folder.parent.parent)), 'P16_RECEIPT_UNSAFE')
        return {**_read_record(path, pins), 'receipt': str(path), 'live_observation': False}
    if folder.exists():
        _require(folder.is_dir() and not folder.is_symlink() and
                 (os.name != 'posix' or folder.stat().st_uid == os.getuid() and not folder.stat().st_mode & 0o077),
                 'P16_RECEIPT_DIRECTORY_UNSAFE')
    with _locked(folder):
        record = _read_record(path, pins) if path.exists() or path.is_symlink() else {
            'schema': _SCHEMA, 'campaign_id': pins.identity, 'pins': asdict(pins), 'stages': [],
            'selection_revocation_qualified': False, 'full_p16_qualified': False,
            'full_w3_qualified': False}
        n = len(record['stages'])
        _require(n < 3 and _PHASES[n] == phase, 'P16_EXACT_NEXT_PHASE_REQUIRED')
        record['stages'].append(observe(root, pins, phase))
        record['selection_revocation_qualified'] = len(record['stages']) == 3
        _write(path, record)
        observed = _read_record(path, pins)
        _require(observed == record, 'P16_RECEIPT_READBACK_DRIFT')
        return {**observed, 'receipt': str(path), 'live_observation': True}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('phase', choices=(*_PHASES, 'status'))
    p.add_argument('--repo', type=Path, default=Path.cwd())
    p.add_argument('--repository', required=True)
    p.add_argument('--sandbox', required=True)
    for field in ('repository-id', 'sandbox-id', 'owner-id', 'app-id', 'installation-id'):
        p.add_argument('--' + field, required=True, type=int)
    p.add_argument('--source-sha', required=True)
    p.add_argument('--source-tree', required=True)
    p.add_argument('--yes', action='store_true', help='authorize fresh scoped read-token issuance; never changes GitHub selection')
    args = p.parse_args()
    try:
        _require(args.phase == 'status' or args.yes, 'P16_READ_TOKEN_ISSUANCE_APPROVAL_REQUIRED')
        pins = SelectionPins(**{k: getattr(args, k) for k in SelectionPins.__dataclass_fields__})
        if args.phase != 'status':
            _require(Path(__file__).resolve() == args.repo.resolve() / 'devtool' / 'ga00_p16_selection.py',
                     'P16_EXECUTION_SOURCE_MISMATCH')
        result = campaign(args.repo.resolve(), pins, args.phase)
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0
    except Exception as exc:
        reason = str(exc)
        if not isinstance(exc, (SelectionRefusal, GitHubAppError, AppOperationRefused)) or not re.fullmatch(r'[A-Z0-9_]{1,100}', reason):
            reason = 'P16_OBSERVATION_UNVERIFIED'
        print(json.dumps({'status': 'BLOCKED', 'reason': reason, 'phase': args.phase,
                          'remote_configuration_mutated': False,
                          'next_action': 'If sandbox selection was removed, restore only that sandbox using the owner installation settings; do not alter production access.'}, sort_keys=True))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
