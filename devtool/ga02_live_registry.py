"""GA-02 live registry and permission qualification, read-only by design.

All GitHub credential issuance and API transports remain in M4/GA-01/M7.
Cached/discovered nodes, local checkouts and relationship edges never grant access.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
import os
import re
import subprocess

from .github_app import AppInstallationProvider, GitHubAppError, _repository
from .github_installations import (InstallationIdentityRefused, RepositoryObservation,
                                   observe_installed, observe_public_upstream)
from .github_relationships import (RelationshipRegistry, RelationshipRefused,
                                   assert_observation)
from .ga01_scoped_auth import (_OPERATION_CAPABILITIES, AppOperationRefused,
                               issue_for_operation)

_SHA1 = re.compile(r'[a-f0-9]{40}\Z')
_ALIAS = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z')


class LiveRegistryRefused(RuntimeError):
    """Stable non-secret refusal reason; no token, private path or response body."""


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _validate_policy(registry: RelationshipRegistry, source_sha: str) -> None:
    if not isinstance(source_sha, str) or not _SHA1.fullmatch(source_sha):
        raise LiveRegistryRefused('GA02_LIVE_SOURCE_SHA_REQUIRED')
    if registry.source_sha != source_sha:
        raise LiveRegistryRefused('GA02_LIVE_POLICY_SOURCE_DRIFT')


def _role_env(role: str, env: Mapping[str, str] | None) -> Mapping[str, str]:
    if role == 'head':
        return os.environ if env is None else env
    if role != 'base':
        raise LiveRegistryRefused('GA02_LIVE_ROLE_NOT_APP')
    if env is None:
        from .federation_app_auth import installed_app_environment
        return installed_app_environment()
    return env


def qualify_operation(registry: RelationshipRegistry, repository: str,
                      operation: str, *, source_sha: str,
                      environ: Mapping[str, str] | None = None,
                      observer: Callable[..., RepositoryObservation] = observe_installed,
                      doctor: Callable[..., dict[str, Any]] | None = None,
                      provider_factory: Callable[..., Any] | None = None) -> dict[str, Any]:
    """Actual scoped App issuance + M7 live GET proof; no remote writes.

    The test seams inject existing producer interfaces, never an alternate
    credential issuer in production. No write-authorized claim is emitted.
    """
    _validate_policy(registry, source_sha)
    slug = _repository(repository)
    node = registry.node(slug)
    if node.role not in ('base', 'head'):
        raise LiveRegistryRefused('GA02_LIVE_PUBLIC_REPOSITORY_HAS_NO_APP_GRANT')
    capability = _OPERATION_CAPABILITIES.get(operation)
    if not capability:
        raise LiveRegistryRefused('GA02_LIVE_OPERATION_UNSUPPORTED')
    try:
        env = _role_env(node.role, environ)
        observed = observer(slug, role=node.role, environ=env)
        assert_observation(registry, observed)
        if observed.evidence != 'SCOPED_APP_READ_VERIFIED' or observed.archived or observed.suspended:
            raise LiveRegistryRefused('GA02_LIVE_OBSERVATION_NOT_ACTIVE')
        if doctor is None:
            from .m7_credentials import inspect_app
            doctor = inspect_app
        health = doctor(slug, env=env, role=node.role, probe=True, key_fingerprint=False)
        if not isinstance(health, dict) or not (
            health.get('identity_verified') is True and health.get('effective_read_verified') is True
            and health.get('repository_id') == observed.repository_id
            and health.get('installation_id') == observed.installation_id
            and health.get('account_id') == observed.owner_id
        ):
            raise LiveRegistryRefused('GA02_LIVE_M7_READ_OR_IDENTITY_UNVERIFIED')
        if provider_factory is None:
            provider_factory = AppInstallationProvider.from_environment
        provider = provider_factory(env, role=node.role)
        access = issue_for_operation(slug, operation, expected_repository_id=node.repository_id,
                                     provider=provider)
        if (access.repository_id != observed.repository_id
                or access.installation_id != observed.installation_id
                or access.app_id != observed.app_id):
            raise LiveRegistryRefused('GA02_LIVE_SCOPED_TOKEN_IDENTITY_DRIFT')
        return {'schema': 'devtool.ga02.live-permission.v1',
                'status': 'EFFECTIVE_SCOPE_VERIFIED', 'observed_at': _utc(),
                'policy_id': registry.policy_id, 'policy_sha256': registry.sha256,
                'source_sha': source_sha, 'repository': slug,
                'repository_id': observed.repository_id, 'owner_id': observed.owner_id,
                'installation_id': observed.installation_id, 'app_id': observed.app_id,
                'role': node.role, 'operation': operation, 'capability': capability,
                'repository_read_verified': True, 'm7_identity_verified': True,
                'scoped_token_issued': True, 'scoped_token_expires_at_unix': access.expires_at,
                'write_authorized': False, 'write_executed': False,
                'write_execution_verified': False, 'remote_mutation': False,
                'token_persisted': False, 'release_qualified': False}
    except LiveRegistryRefused:
        raise
    except (RelationshipRefused, InstallationIdentityRefused, AppOperationRefused, GitHubAppError):
        raise LiveRegistryRefused('GA02_LIVE_INSTALLATION_OR_PERMISSION_UNVERIFIED') from None
    except Exception:
        raise LiveRegistryRefused('GA02_LIVE_PROBE_UNAVAILABLE') from None


def account_visible_repositories(*, mode: str, limit: int = 100,
                                 auth_resolver: Callable[..., Any] | None = None,
                                 client_factory: Callable[..., Any] | None = None) -> dict[str, Any]:
    """Opt-in account/organization visibility through existing Actions GET transport."""
    if mode not in ('env', 'gh') or type(limit) is not int or not 1 <= limit <= 300:
        raise LiveRegistryRefused('GA02_ACCOUNT_DISCOVERY_OPTIONS_INVALID')
    try:
        if auth_resolver is None or client_factory is None:
            from .github_actions import GitHubActionsClient, resolve_token
            auth_resolver = resolve_token if auth_resolver is None else auth_resolver
            client_factory = GitHubActionsClient if client_factory is None else client_factory
        token, origin = auth_resolver(mode)
        if not isinstance(token, str) or not token:
            raise LiveRegistryRefused('GA02_ACCOUNT_AUTHENTICATION_REQUIRED')
        client = client_factory(token)
        viewer = client.viewer()
        if type(viewer.get('id')) is not int or viewer['id'] < 1:
            raise LiveRegistryRefused('GA02_ACCOUNT_NUMERIC_ID_MISSING')
        rows = client.user_repositories(max_results=limit)
        if not isinstance(rows, list) or len(rows) > limit:
            raise LiveRegistryRefused('GA02_ACCOUNT_LIST_UNBOUNDED')
        by_id: dict[int, dict[str, Any]] = {}
        by_slug: dict[str, int] = {}
        for item in rows:
            if not isinstance(item, dict) or type(item.get('id')) is not int or item['id'] < 1:
                raise LiveRegistryRefused('GA02_ACCOUNT_REPOSITORY_ID_INVALID')
            slug = _repository(item.get('full_name'))
            owner = item.get('owner')
            if not isinstance(owner, dict) or type(owner.get('id')) is not int or owner['id'] < 1:
                raise LiveRegistryRefused('GA02_ACCOUNT_OWNER_ID_INVALID')
            if str(owner.get('login', '')).casefold() != slug.split('/')[0].casefold():
                raise LiveRegistryRefused('GA02_ACCOUNT_OWNER_DRIFT')
            key = slug.casefold()
            previous = by_id.get(item['id'])
            if (previous is not None and previous['repository'].casefold() != key
                    or key in by_slug and by_slug[key] != item['id']):
                raise LiveRegistryRefused('GA02_ACCOUNT_ID_OR_SLUG_COLLISION')
            if type(item.get('private')) is not bool or type(item.get('fork')) is not bool:
                raise LiveRegistryRefused('GA02_ACCOUNT_PRIVACY_OR_FORK_UNVERIFIED')
            by_id[item['id']] = {'repository': slug, 'repository_id': item['id'],
                                 'owner_id': owner['id'], 'private': item['private'],
                                 'fork': item['fork'], 'authority': 'USER_VISIBLE_ONLY',
                                 'write_authorized': False}
            by_slug[key] = item['id']
        return {'schema': 'devtool.ga02.account-visibility.v1',
                'viewer_login': viewer['login'], 'viewer_id': viewer['id'],
                'credential_mode': origin, 'repositories': list(sorted(by_id.values(), key=lambda r:r['repository'].casefold())),
                'discovery_only': True, 'write_authorized': False}
    except LiveRegistryRefused:
        raise
    except (OSError, ValueError, TypeError, KeyError):
        raise LiveRegistryRefused('GA02_ACCOUNT_DISCOVERY_UNAVAILABLE') from None
    except Exception:
        raise LiveRegistryRefused('GA02_ACCOUNT_API_UNAVAILABLE_OR_DENIED') from None


def local_checkouts(checkouts: list[str | Path], *, host: str,
                    runner: Callable[..., Any] = subprocess.run) -> list[dict[str, Any]]:
    """Enumerate ONLY explicit paths. Local origin/HEAD is navigation, not a grant."""
    if not isinstance(host, str) or not _ALIAS.fullmatch(host) or len(checkouts) > 64:
        raise LiveRegistryRefused('GA02_LOCAL_SCOPE_UNSAFE')
    out = []
    seen_paths: set[str] = set()
    for raw in checkouts:
        p = Path(raw).expanduser().absolute()
        if any(part.is_symlink() for part in (p,*p.parents)) or not p.is_dir() or str(p) in seen_paths:
            raise LiveRegistryRefused('GA02_LOCAL_PATH_UNSAFE_OR_DUPLICATE')
        seen_paths.add(str(p))
        def git(*args: str) -> str:
            try:
                r = runner(['git', '-C', str(p), *args], capture_output=True, text=True,
                           timeout=15, check=False)
            except (OSError, subprocess.TimeoutExpired):
                raise LiveRegistryRefused('GA02_LOCAL_GIT_QUERY_UNAVAILABLE') from None
            if r.returncode != 0:
                raise LiveRegistryRefused('GA02_LOCAL_GIT_QUERY_FAILED')
            return r.stdout.strip()
        if git('rev-parse', '--show-toplevel') != str(p):
            raise LiveRegistryRefused('GA02_LOCAL_CHECKOUT_ROOT_MISMATCH')
        sha = git('rev-parse', 'HEAD')
        if not _SHA1.fullmatch(sha):
            raise LiveRegistryRefused('GA02_LOCAL_HEAD_INVALID')
        # A checkout without origin remains navigable, but never joins a
        # GitHub identity or becomes eligible for App/native authorization.
        try:
            origin_process = runner(['git', '-C', str(p), 'remote', 'get-url', 'origin'],
                                    capture_output=True, text=True, timeout=15, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise LiveRegistryRefused('GA02_LOCAL_GIT_QUERY_UNAVAILABLE') from None
        slug = None
        if origin_process.returncode == 0:
            from .github_actions import parse_github_remote
            try:
                slug = parse_github_remote(origin_process.stdout.strip())
            except Exception:
                raise LiveRegistryRefused('GA02_LOCAL_ORIGIN_NOT_VERIFIED_GITHUB') from None
        out.append({'host': host, 'checkout': str(p), 'repository': slug,
                    'head_sha': sha, 'authority': 'LOCAL_NAVIGATION_ONLY',
                    'identity': 'GITHUB_ORIGIN_UNVERIFIED_LOCAL_ONLY' if slug is None else 'GITHUB_REMOTE_SLUG_ONLY',
                    'repository_id': None, 'write_authorized': False})
    return out


def catalog(registry: RelationshipRegistry, *, source_sha: str,
            account: dict[str, Any] | None = None,
            checkouts: list[dict[str, Any]] | None = None,
            aliases: list[str] | None = None,
            observer: Callable[..., RepositoryObservation] = observe_installed,
            public_observer: Callable[..., RepositoryObservation] = observe_public_upstream,
            environ: Mapping[str, str] | None = None,
            strict: bool = True) -> dict[str, Any]:
    """Never unify user visibility, policy enrollment and local checkouts into an auth grant."""
    _validate_policy(registry, source_sha)
    nodes = []
    seen_id: dict[int, str] = {}
    for node in registry.nodes:
        try:
            if node.role == 'public':
                obs = public_observer(node.repository)
            else:
                obs = observer(node.repository, role=node.role,
                               environ=_role_env(node.role, environ))
            assert_observation(registry, obs)
            if obs.repository_id in seen_id and seen_id[obs.repository_id].casefold()!=obs.repository.casefold():
                raise LiveRegistryRefused('GA02_CATALOG_REPOSITORY_ID_COLLISION')
            seen_id[obs.repository_id] = obs.repository
            nodes.append({'repository': obs.repository, 'repository_id': obs.repository_id,
                          'owner_id': obs.owner_id, 'installation_id': obs.installation_id,
                          'role': node.role, 'status': 'LIVE_OBSERVATION_VERIFIED',
                          'visibility': obs.visibility, 'fork': obs.fork,
                          'evidence': obs.evidence, 'write_authorized': False})
        except Exception:
            if strict:
                raise LiveRegistryRefused('GA02_CATALOG_LIVE_OBSERVATION_BLOCKED') from None
            nodes.append({'repository': node.repository, 'repository_id': node.repository_id,
                          'role': node.role, 'status': 'LIVE_OBSERVATION_BLOCKED',
                          'write_authorized': False})
    links = []
    for e in registry.edges:
        links.append({'kind': e.kind, 'source': e.source, 'target': e.target,
                      'policy_ref': e.policy_ref, 'authority': 'DECLARED_ONLY'})
    local = list(checkouts or [])
    if len(local) > 64:
        raise LiveRegistryRefused('GA02_LOCAL_SCOPE_UNSAFE')
    for entry in local:
        match = next((n for n in nodes if entry.get('repository') and n['repository'].casefold()==entry['repository'].casefold()), None)
        entry['linked_live_repository_id'] = match.get('repository_id') if match and match['status']=='LIVE_OBSERVATION_VERIFIED' else None
        entry['write_authorized'] = False
    alias_rows=[]
    known_aliases: set[str] = set()
    for value in aliases or []:
        if not isinstance(value,str) or value.count('=')!=1:
            raise LiveRegistryRefused('GA02_ALIAS_INVALID')
        alias,repository=value.split('=',1)
        if not _ALIAS.fullmatch(alias) or alias.casefold() in known_aliases:
            raise LiveRegistryRefused('GA02_ALIAS_COLLISION_OR_UNSAFE')
        try:repository=_repository(repository)
        except Exception:raise LiveRegistryRefused('GA02_ALIAS_REPOSITORY_INVALID') from None
        known_aliases.add(alias.casefold())
        alias_rows.append({'alias':alias,'repository':repository,
                           'authority':'NAVIGATION_ONLY','write_authorized':False})
    if len(alias_rows)>64:raise LiveRegistryRefused('GA02_ALIAS_UNBOUNDED')
    visible = [] if account is None else list(account.get('repositories') or [])
    if len(visible)>300:
        raise LiveRegistryRefused('GA02_ACCOUNT_LIST_UNBOUNDED')
    return {'schema':'devtool.ga02.live-catalog.v1', 'observed_at':_utc(),
            'policy_id':registry.policy_id,'policy_sha256':registry.sha256,
            'source_sha':source_sha, 'account_visible':visible, 'app_enrolled':nodes,
            'declared_relationships':links, 'local_checkouts':local, 'aliases':alias_rows,
            'account_enumerated':account is not None,
            'write_authorized':False,'remote_mutation':False,
            'permission_qualification_separate':True}
