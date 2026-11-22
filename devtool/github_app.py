"""M4: fail-closed GitHub App installation credentials for native delivery.

Only issues short-lived repository/capability-scoped installation credentials.
No delivery approvals, Git mutation journals or CI verdicts live here.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import base64
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Iterator, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

_API = 'https://api.github.com'
_VERSION = '2022-11-28'
_SLUG = re.compile(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}\Z')
_BRANCH = re.compile(r'[A-Za-z0-9._/-]{1,200}\Z')
_MAX_RESPONSE = 1024 * 1024
_PERMISSIONS = {
    'repository_read': {'contents': 'read'},
    'actions_read': {'contents': 'read', 'actions': 'read'},
    'actions_control': {'contents': 'read', 'actions': 'write'},
    'runners_read': {'contents': 'read', 'administration': 'read'},
    'release_read': {'contents': 'read'},
    'release_write': {'contents': 'write'},
    'pr_read': {'pull_requests': 'read', 'contents': 'read'},
    'pr_create': {'pull_requests': 'write', 'contents': 'read'},
    'pr_maintain': {'pull_requests': 'write', 'contents': 'read'},
    'status_write': {'statuses': 'write', 'contents': 'read'},
    'branch_write': {'contents': 'write'},
    'checks_read': {'actions': 'read', 'checks': 'read', 'contents': 'read'},
    'actions_dispatch': {'actions': 'write', 'contents': 'read'},
}


class GitHubAppError(RuntimeError):
    """Sanitized, actionable credential error. Never append provider response bodies."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _repository(slug: str) -> str:
    if not isinstance(slug, str) or not _SLUG.fullmatch(slug) or '..' in slug.split('/'):
        raise GitHubAppError('APP_REPOSITORY_INVALID')
    return slug


def _app_int(value: str, name: str) -> int:
    if not re.fullmatch(r'[1-9][0-9]{0,14}', str(value)):
        raise GitHubAppError('APP_' + name + '_INVALID')
    return int(value)


@dataclass(frozen=True)
class AppConfig:
    app_id: int
    installation_id: int
    account: str
    private_key_path: Path = field(repr=False)

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None, *, role: str = 'base') -> 'AppConfig':
        env = os.environ if environ is None else environ
        if role not in ('base','head'):
            raise GitHubAppError('APP_ROLE_INVALID')
        prefix = 'DEVTOOL_GITHUB_APP_HEAD_' if role == 'head' else 'DEVTOOL_GITHUB_APP_'
        app_id = _app_int(env.get(prefix+'ID', ''), 'ID')
        installation_id = _app_int(env.get(prefix+'INSTALLATION_ID', ''), 'INSTALLATION_ID')
        account = str(env.get(prefix+'ACCOUNT', '')).strip()
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}', account):
            raise GitHubAppError('APP_ACCOUNT_INVALID')
        raw_path = str(env.get(prefix+'PRIVATE_KEY_PATH', '')).strip()
        if not raw_path or '\x00' in raw_path or '\n' in raw_path:
            raise GitHubAppError('APP_PRIVATE_KEY_PATH_INVALID')
        return cls(app_id, installation_id, account, Path(raw_path).expanduser().absolute())


def _private_key_bytes(path: Path) -> bytes:
    """Read only a private, user-owned regular PEM. Never follow leaf symlinks."""
    if path.is_symlink():
        raise GitHubAppError('APP_PRIVATE_KEY_SYMLINK')
    parent = path.parent
    parent_stat = parent.stat()
    if not stat.S_ISDIR(parent_stat.st_mode) or parent_stat.st_mode & 0o022:
        raise GitHubAppError('APP_PRIVATE_KEY_PARENT_UNSAFE')
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        raise GitHubAppError('APP_PRIVATE_KEY_UNAVAILABLE') from None
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or not 512 <= info.st_size <= 32768):
            raise GitHubAppError('APP_PRIVATE_KEY_UNSAFE')
        data = os.read(fd, 32769)
        if len(data) != info.st_size:
            raise GitHubAppError('APP_PRIVATE_KEY_CHANGED')
        return data
    finally:
        os.close(fd)


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')


def _jwt(config: AppConfig, now: int) -> str:
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
    except ImportError:
        raise GitHubAppError('APP_CRYPTOGRAPHY_REQUIRED') from None
    try:
        key = serialization.load_pem_private_key(
            _private_key_bytes(config.private_key_path), password=None)
    except (ValueError, TypeError):
        raise GitHubAppError('APP_PRIVATE_KEY_PARSE_REFUSED') from None
    if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
        raise GitHubAppError('APP_PRIVATE_KEY_MUST_BE_RSA2048_PLUS')
    header = _b64(b'{"alg":"RS256","typ":"JWT"}')
    payload = _b64(json.dumps({'iat': now-60, 'exp': now+540, 'iss': str(config.app_id)},
                              separators=(',', ':'), sort_keys=True).encode('ascii'))
    signing_input = (header + '.' + payload).encode('ascii')
    signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return signing_input.decode('ascii') + '.' + _b64(signature)


def _permission_ok(actual: Mapping[str, Any], need: Mapping[str, str]) -> bool:
    rank = {'none': 0, 'read': 1, 'write': 2}
    return all(rank.get(str(actual.get(k, '')).lower(), 0) >= rank[v] for k,v in need.items())


class _ProviderHTTP:
    """Bounded GitHub API transport, no redirects or credential-bearing error details."""
    def __init__(self, opener: Any = None, timeout: float = 15.0):
        self.opener = opener if opener is not None else build_opener(_NoRedirect())
        self.timeout = timeout

    def request(self, method: str, path: str, credential: str,
                payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if (method not in ('GET','POST') or not path.startswith('/') or '//' in path or
                '..' in path or '?' in path or '#' in path or not credential):
            raise GitHubAppError('APP_HTTP_REQUEST_INVALID')
        url = _API + path
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.netloc != 'api.github.com':
            raise GitHubAppError('APP_HTTP_ORIGIN_INVALID')
        raw = None if payload is None else json.dumps(payload, separators=(',', ':')).encode('utf-8')
        if (method == 'POST') != (raw is not None) or (raw and len(raw)>8192):
            raise GitHubAppError('APP_HTTP_PAYLOAD_INVALID')
        headers = {'Authorization': 'Bearer ' + credential,
                   'Accept': 'application/vnd.github+json',
                   'X-GitHub-Api-Version': _VERSION,
                   'User-Agent': 'devtool-app-auth/1'}
        if raw is not None:
            headers['Content-Type'] = 'application/json'
        request = Request(url, headers=headers, data=raw, method=method)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                if int(response.status) != (201 if method=='POST' else 200):
                    raise GitHubAppError('APP_HTTP_STATUS_UNEXPECTED')
                data = response.read(_MAX_RESPONSE+1)
        except HTTPError as exc:
            code = int(exc.code)
            if code in (301,302,303,307,308):
                reason = 'APP_HTTP_REDIRECT_REFUSED'
            elif code == 401:
                reason = 'APP_AUTHENTICATION_FAILED'
            elif code == 403:
                reason = 'APP_PERMISSION_DENIED_OR_RATE_LIMIT'
            elif code == 404:
                reason = 'APP_INSTALLATION_OR_REPOSITORY_UNAVAILABLE'
            else:
                reason = 'APP_HTTP_' + str(code)
            raise GitHubAppError(reason) from None
        except (URLError, TimeoutError, OSError):
            raise GitHubAppError('APP_PROVIDER_UNAVAILABLE') from None
        if len(data)>_MAX_RESPONSE:
            raise GitHubAppError('APP_HTTP_RESPONSE_OVERSIZE')
        try:
            item = json.loads(data)
        except (ValueError, UnicodeError):
            raise GitHubAppError('APP_HTTP_RESPONSE_INVALID') from None
        if not isinstance(item, dict):
            raise GitHubAppError('APP_HTTP_RESPONSE_SHAPE')
        return item


@dataclass(frozen=True)
class InstallationAccess:
    repository: str
    repository_id: int
    app_id: int
    installation_id: int
    account: str
    capability: str
    permissions: dict[str, str]
    expires_at: int
    token: str = field(repr=False)

    def public_receipt(self) -> dict[str, Any]:
        return {'mode': 'app','repository':self.repository,'repository_id':self.repository_id,
                'app_id':self.app_id,'installation_id':self.installation_id,
                'account':self.account,'capability':self.capability,
                'granted_permissions':dict(self.permissions),
                'expires_at':datetime.fromtimestamp(self.expires_at,timezone.utc).isoformat()}


class AppInstallationProvider:
    def __init__(self, config: AppConfig, *, http: _ProviderHTTP | None = None,
                 clock: Callable[[], float] = time.time):
        self.config = config
        self.http = http if http is not None else _ProviderHTTP()
        self.clock = clock
        self._cache: dict[tuple[str,str], InstallationAccess] = {}

    @classmethod
    def from_environment(cls, environ: Mapping[str,str] | None = None, *, role: str = 'base') -> 'AppInstallationProvider':
        if environ is None and role == 'base':
            # M7's private literal-only config is canonical for local Termux
            # and Linux operators. No shell sourcing and no PAT fallback.
            from .federation_app_auth import installed_app_environment
            environ = installed_app_environment()
        return cls(AppConfig.from_environment(environ, role=role))

    def issue(self, repository: str, capability: str) -> InstallationAccess:
        repository = _repository(repository)
        if capability not in _PERMISSIONS:
            raise GitHubAppError('APP_CAPABILITY_UNSUPPORTED')
        if repository.split('/',1)[0].casefold()!=self.config.account.casefold():
            raise GitHubAppError('APP_ACCOUNT_REPOSITORY_MISMATCH')
        key = (repository.casefold(), capability)
        now = int(self.clock())
        prior = self._cache.get(key)
        if prior is not None and prior.expires_at - now > 300:
            return prior
        signed = _jwt(self.config, now)
        installation = self.http.request('GET', f'/app/installations/{self.config.installation_id}', signed)
        linked = self.http.request('GET', f'/repos/{repository}/installation', signed)
        for item in (installation, linked):
            if (item.get('id') != self.config.installation_id or
                (item.get('app_id') is not None and item['app_id'] != self.config.app_id) or
                not isinstance(item.get('account'),dict) or
                str(item['account'].get('login','')).casefold()!=self.config.account.casefold() or
                item.get('suspended_at')):
                raise GitHubAppError('APP_INSTALLATION_IDENTITY_UNVERIFIED')
        need = _PERMISSIONS[capability]
        if not _permission_ok(installation.get('permissions') or {},need):
            raise GitHubAppError('APP_INSTALLATION_PERMISSION_MISSING')
        issued = self.http.request('POST',
             f'/app/installations/{self.config.installation_id}/access_tokens',signed,
             {'repositories':[repository.split('/',1)[1]],'permissions':need})
        permissions = issued.get('permissions')
        repositories = issued.get('repositories')
        if (not isinstance(permissions,dict) or not _permission_ok(permissions,need)
            or not isinstance(repositories,list) or len(repositories)!=1
            or not isinstance(repositories[0],dict)
            or str(repositories[0].get('full_name','')).casefold()!=repository.casefold()
            or type(repositories[0].get('id')) is not int or repositories[0]['id'] < 1):
            raise GitHubAppError('APP_TOKEN_REPOSITORY_OR_PERMISSION_UNVERIFIED')
        token = issued.get('token')
        expires_text = issued.get('expires_at')
        try:
            expiry = int(datetime.fromisoformat(str(expires_text).replace('Z','+00:00')).timestamp())
        except (TypeError,ValueError,OverflowError):
            raise GitHubAppError('APP_TOKEN_EXPIRY_INVALID') from None
        if (not isinstance(token,str) or not 12<=len(token)<=4096 or '\n' in token
                or '\r' in token or expiry - now < 300 or expiry - now > 7200):
            raise GitHubAppError('APP_TOKEN_INVALID_OR_EXPIRED')
        # Prove independent API access and repository identity using the scoped token.
        actual = self.http.request('GET',f'/repos/{repository}',token)
        if (str(actual.get('full_name','')).casefold()!=repository.casefold()
                or actual.get('id')!=repositories[0]['id']):
            raise GitHubAppError('APP_TOKEN_REPOSITORY_ACCESS_UNVERIFIED')
        credential = InstallationAccess(repository, repositories[0]['id'],
            self.config.app_id,self.config.installation_id,self.config.account,
            capability,dict(permissions),expiry,token)
        self._cache[key] = credential
        return credential


# An App token may only reach the exact, pinned github.com repository via an
# isolated one-operation AskPass.  Every Git invocation here uses the same
# sanitized configuration universe (including the *preflight* Git calls).
_ASKPASS = r'''#!/usr/bin/env python3
import os, re, sys
from urllib.parse import urlsplit
prompt = sys.argv[1] if len(sys.argv) > 1 else ''
match = re.fullmatch(r"(Username|Password) for '([^']+)': ?", prompt)
if not match:
    sys.exit(2)
kind, target = match.groups()
try:
    parsed = urlsplit(target)
    repo = os.environ['DEVTOOL_APP_AUTH_REPOSITORY']
    allowed_paths = ('', '/', '/' + repo, '/' + repo + '.git',
                     '/' + repo + '/', '/' + repo + '.git/')
    if (parsed.scheme != 'https' or parsed.hostname != 'github.com'
            or parsed.port is not None or parsed.password is not None
            or parsed.query or parsed.fragment or parsed.path not in allowed_paths):
        sys.exit(2)
    if kind == 'Username':
        if parsed.username not in (None, 'x-access-token'):
            sys.exit(2)
        os.write(1, b'x-access-token\n')
    elif kind == 'Password' and parsed.username in (None, 'x-access-token'):
        fd = int(os.environ['DEVTOOL_APP_AUTH_FD'])
        raw = os.read(fd, 4096)
        if not raw or b'\n' in raw or b'\r' in raw:
            sys.exit(2)
        os.write(1, raw + b'\n')
    else:
        sys.exit(2)
except (ValueError, KeyError, OSError):
    sys.exit(2)
'''


def _git_isolated_environment() -> dict[str, str]:
    """Ignore untrusted GIT_* injections and user/system rewrite rules."""
    env = {key: val for key, val in os.environ.items() if not key.startswith('GIT_')
           and key not in ('SSH_ASKPASS', 'GCM_INTERACTIVE')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_CONFIG_SYSTEM=os.devnull, GIT_TERMINAL_PROMPT='0',
               GCM_INTERACTIVE='never', GIT_TRACE='0', GIT_CURL_VERBOSE='0',
               GIT_ALLOW_PROTOCOL='https')
    return env


def _config_git(repo: Path, argv: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(['git', '-C', str(repo), *argv],
                              env=_git_isolated_environment(), capture_output=True,
                              text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise GitHubAppError('APP_GIT_CONFIG_UNVERIFIED') from None


def _https_origin(repo: Path, slug: str) -> None:
    """Prove both effective fetch and *push* endpoints and refuse local rewrites."""
    slug = _repository(slug)
    expected = re.compile(r'https://github\.com/' + re.escape(slug) +
                          r'(?:\.git)?/?\Z', re.I)
    for argv in (['remote', 'get-url', 'origin'],
                 ['remote', 'get-url', '--push', 'origin']):
        result = _config_git(repo, argv)
        if result.returncode != 0 or not expected.fullmatch(result.stdout.strip()):
            raise GitHubAppError('APP_GIT_REQUIRES_EXACT_HTTPS_ORIGIN_OR_PUSH')
    # No file-local rewriting, HTTP trust overrides, includes, or credential
    # helpers may change the destination or leak App authentication. Global/system config
    # is deliberately excluded in both preflight and the real Git child.
    unsafe = _config_git(repo, ['config', '--local', '--get-regexp',
        r'^(url\.|http\.|credential\.|include\.|includeif\.)'])
    if unsafe.returncode not in (0, 1):
        raise GitHubAppError('APP_GIT_CONFIG_UNVERIFIED')
    if unsafe.returncode == 0 and unsafe.stdout.strip():
        raise GitHubAppError('APP_GIT_REWRITE_OR_CREDENTIAL_CONFIG_REFUSED')


def _safe_branch_ref(value: str) -> bool:
    prefix = 'refs/heads/'
    if not value.startswith(prefix):
        return False
    branch = value[len(prefix):]
    return bool(_BRANCH.fullmatch(branch) and '..' not in branch and '@{' not in branch
                and '//' not in branch and not branch.startswith(('.', '-'))
                and not branch.endswith(('/', '.', '.lock'))
                and '/.' not in branch and '\\' not in branch)


def _check_git_argv(argv: list[str], capability: str) -> None:
    """No caller-controlled URLs, alternate remotes, flags or arbitrary refs."""
    if not isinstance(argv, list) or not argv:
        raise GitHubAppError('APP_GIT_COMMAND_NOT_APPROVED')
    if argv == ['fetch', '--prune', 'origin']:
        if capability != 'repository_read':
            raise GitHubAppError('APP_GIT_FETCH_SCOPE_OR_ARGUMENTS_REFUSED')
        return
    if argv[0] == 'fetch':
        raise GitHubAppError('APP_GIT_FETCH_SCOPE_OR_ARGUMENTS_REFUSED')
    if (len(argv) == 4 and argv[:3] == ['ls-remote', '--exit-code', 'origin']
            and _safe_branch_ref(argv[3])):
        if capability not in ('repository_read', 'branch_write'):
            raise GitHubAppError('APP_GIT_READ_SCOPE_OR_ARGUMENTS_REFUSED')
        return
    if (len(argv) == 4 and argv[:3] == ['push', '--porcelain', 'origin']
            and argv[3].count(':') == 1):
        source, destination = argv[3].split(':')
        if source == destination and _safe_branch_ref(source) and capability == 'branch_write':
            return
    raise GitHubAppError('APP_GIT_SCOPE_OR_ARGUMENTS_REFUSED')


def run_git_authenticated(repo: str | Path, argv: list[str], access: InstallationAccess,
                          *, timeout: float = 150) -> subprocess.CompletedProcess[str]:
    """One exact native Git operation with an ephemeral fd-backed AskPass token."""
    repo = Path(repo).resolve()
    _https_origin(repo, access.repository)
    _check_git_argv(argv, access.capability)
    if access.expires_at <= int(time.time()) + 120:
        raise GitHubAppError('APP_TOKEN_TOO_CLOSE_TO_EXPIRY_FOR_GIT')
    with tempfile.TemporaryDirectory(prefix='devtool-app-auth-') as temp:
        helper = Path(temp) / 'askpass.py'
        helper.write_text(_ASKPASS, encoding='utf-8')
        helper.chmod(0o700)
        readfd, writefd = os.pipe()
        try:
            os.write(writefd, access.token.encode('utf-8'))
        finally:
            os.close(writefd)
        env = _git_isolated_environment()
        env.update(GIT_ASKPASS=str(helper), DEVTOOL_APP_AUTH_FD=str(readfd),
                   DEVTOOL_APP_AUTH_REPOSITORY=_repository(access.repository))
        cmd = ['git', '-c', 'credential.helper=',
               '-c', 'credential.useHttpPath=true',
               '-c', 'http.followRedirects=false',
               '-c', 'core.hooksPath=/dev/null',
               '-c', f'core.askPass={helper}',
               '-c', 'protocol.allow=never', '-c', 'protocol.https.allow=always',
               '-C', str(repo), *argv]
        try:
            return subprocess.run(cmd, env=env, pass_fds=(readfd,), capture_output=True,
                                  text=True, timeout=timeout, check=False)
        except (OSError, subprocess.SubprocessError):
            raise GitHubAppError('APP_GIT_OPERATION_UNAVAILABLE') from None
        finally:
            os.close(readfd)


def make_app_client(repository: str, capability: str, *, role: str = 'base'):
    """Auto-renewing Actions transport for GET polling, never auto-replays POST."""
    from .github_actions import GitHubActionsClient
    # The provider itself reuses M7's private configuration for base roles;
    # HEAD roles always require their distinct installation variables.
    provider = AppInstallationProvider.from_environment(role=role)
    class _AppClient(GitHubActionsClient):
        def __init__(self):
            self._app_access = None
            self._bound_repository = _repository(repository)
            super().__init__(token=None)
        def _assert_repository(self, slug):
            if _repository(slug).casefold()!=self._bound_repository.casefold():
                raise GitHubAppError('APP_CLIENT_CROSS_REPOSITORY_REFUSED')
        def _request(self, path):
            # Pagination and internal raw GET adapters must never escape the
            # chosen installation repository, even before the provider does a
            # real GitHub permission check.
            parsed=urlsplit(path)
            prefix='/repos/'+self._bound_repository
            if (parsed.scheme!='https' or parsed.netloc!='api.github.com'
                    or not (parsed.path.casefold()==prefix.casefold() or
                            parsed.path.casefold().startswith(prefix.casefold()+'/'))):
                raise GitHubAppError('APP_CLIENT_CROSS_REPOSITORY_REFUSED')
            return super()._request(path)
        def dispatch(self, slug, workflow_id, ref, inputs):
            self._assert_repository(slug)
            if capability!='actions_dispatch':
                raise GitHubAppError('APP_CLIENT_OPERATION_CAPABILITY_REFUSED')
            return super().dispatch(slug,workflow_id,ref,inputs)
        def run_mutation(self, slug, identity, action):
            self._assert_repository(slug)
            if capability!='actions_control':
                raise GitHubAppError('APP_CLIENT_OPERATION_CAPABILITY_REFUSED')
            return super().run_mutation(slug,identity,action)
        def artifact_download_location(self, slug, identity):
            self._assert_repository(slug)
            return super().artifact_download_location(slug,identity)
        def run_log_location(self, slug, identity):
            self._assert_repository(slug)
            return super().run_log_location(slug,identity)
        @property
        def _token(self):
            access = provider.issue(self._bound_repository,capability)
            self._app_access = access
            return access.token
        @_token.setter
        def _token(self,value):
            if value:
                raise GitHubAppError('APP_CLIENT_TOKEN_OVERRIDE_REFUSED')
        def app_receipt(self):
            return provider.issue(self._bound_repository,capability).public_receipt()
    return _AppClient()
