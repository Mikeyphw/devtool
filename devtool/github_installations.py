"""GA-02: source-bound, token-free installation/repository identity receipts.

M4 remains the ONLY credential issuer; M7 remains the diagnostic authority.
This module never stores a token, private key or serialized JWT.  A relation,
fork or public metadata observation can NEVER grant a GitHub write.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping
import re
import time
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener
from urllib.parse import quote

from .github_app import _NoRedirect

from .github_app import (AppConfig, AppInstallationProvider, GitHubAppError,
                         _ProviderHTTP, _jwt, _repository)

_SHA = re.compile(r'[a-f0-9]{40}\Z')
_BRANCH = re.compile(r'[A-Za-z0-9._/-]{1,128}\Z')


class InstallationIdentityRefused(RuntimeError):
    """Only fixed non-secret diagnostic codes may leave this trust boundary."""


def _id(value: Any, code: str) -> int:
    if type(value) is not int or value < 1:
        raise InstallationIdentityRefused(code)
    return value


def _branch(value: str) -> str:
    if (not isinstance(value, str) or not _BRANCH.fullmatch(value)
            or value.startswith(('/', '-')) or value.endswith(('/', '.lock'))
            or '..' in value or '//' in value or '@{' in value):
        raise InstallationIdentityRefused('GA02_BRANCH_UNSAFE')
    return value


@dataclass(frozen=True, slots=True)
class RepositoryObservation:
    repository: str
    repository_id: int
    owner_login: str
    owner_id: int
    installation_id: int | None
    installation_account_id: int | None
    app_id: int | None
    role: str
    selection: str
    visibility: str
    fork: bool
    parent_repository: str | None
    parent_repository_id: int | None
    parent_owner_id: int | None
    default_branch: str
    archived: bool
    suspended: bool
    evidence: str

    def receipt(self) -> dict[str, Any]:
        return {'schema':'devtool.ga02.repository-observation.v1',
                'repository':self.repository, 'repository_id':self.repository_id,
                'owner_login':self.owner_login,'owner_id':self.owner_id,
                'installation_id':self.installation_id,
                'installation_account_id':self.installation_account_id,
                'app_id':self.app_id,'role':self.role,'selection':self.selection,
                'visibility':self.visibility,'fork':self.fork,
                'parent_repository':self.parent_repository,
                'parent_repository_id':self.parent_repository_id,
                'parent_owner_id':self.parent_owner_id,
                'default_branch':self.default_branch,'archived':self.archived,
                'suspended':self.suspended,'evidence':self.evidence,
                'contains_credentials':False,'write_authorized':False}


def _repository_metadata(slug: str, obj: Any, *, allow_private: bool) -> dict[str, Any]:
    if (not isinstance(obj,dict) or not isinstance(obj.get('full_name'),str)
            or obj['full_name'].casefold()!=slug.casefold()):
        raise InstallationIdentityRefused('GA02_REPOSITORY_SLUG_DRIFT')
    numeric=_id(obj.get('id'),'GA02_REPOSITORY_ID_MISSING')
    owner=obj.get('owner')
    if (not isinstance(owner,dict) or
            str(owner.get('login','')).casefold()!=slug.split('/')[0].casefold()):
        raise InstallationIdentityRefused('GA02_REPOSITORY_OWNER_DRIFT')
    owner_id=_id(owner.get('id'),'GA02_REPOSITORY_OWNER_ID_MISSING')
    visible=obj.get('visibility')
    private=obj.get('private')
    if visible not in ('private','public','internal') or type(private) is not bool:
        raise InstallationIdentityRefused('GA02_VISIBILITY_UNVERIFIED')
    if (private and visible!='private') or (not private and visible=='private'):
        raise InstallationIdentityRefused('GA02_VISIBILITY_CONTRADICTION')
    if not allow_private and (private or visible!='public'):
        raise InstallationIdentityRefused('GA02_PUBLIC_UPSTREAM_PRIVATE_OR_INACCESSIBLE')
    fork=obj.get('fork')
    archived=obj.get('archived')
    if type(fork) is not bool or type(archived) is not bool:
        raise InstallationIdentityRefused('GA02_FORK_OR_ARCHIVE_UNVERIFIED')
    branch=_branch(obj.get('default_branch'))
    parent_name=parent_id=parent_owner_id=None
    if fork:
        p=obj.get('parent')
        if isinstance(p,dict):
            parent_name=_repository(p.get('full_name'))
            parent_id=_id(p.get('id'),'GA02_FORK_PARENT_ID_UNVERIFIED')
            o=p.get('owner')
            if not isinstance(o,dict):
                raise InstallationIdentityRefused('GA02_FORK_PARENT_OWNER_UNVERIFIED')
            parent_owner_id=_id(o.get('id'),'GA02_FORK_PARENT_OWNER_ID_UNVERIFIED')
        # Private fork parents may be redacted by GitHub. In that situation
        # parent provenance is UNKNOWN; relationship checks must fail closed.
    elif obj.get('parent') is not None:
        raise InstallationIdentityRefused('GA02_NONFORK_HAS_PARENT')
    return {'repository_id':numeric,'owner_id':owner_id,'owner_login':owner['login'],
            'visibility':visible,'fork':fork,'archived':archived,
            'default_branch':branch,'parent_repository':parent_name,
            'parent_repository_id':parent_id,'parent_owner_id':parent_owner_id}


def observe_installed(repository: str, *, role: str = 'base',
                      environ: Mapping[str,str] | None = None,
                      http: Any | None = None, clock=time.time) -> RepositoryObservation:
    """Reconcile real M4 App token scope with independently fetched repo metadata.

    ``role='head'`` deliberately requires the *head* installation's private
    config. Never fall back to the base installation or a PAT for other owners.
    """
    try:
        slug=_repository(repository)
        if role not in ('base','head'):
            raise InstallationIdentityRefused('GA02_ROLE_NOT_ADMITTED')
        if environ is None:
            if role=='base':
                from .federation_app_auth import installed_app_environment
                environ=installed_app_environment()
            else:
                # M4 forbids reuse of the base key for a different owner.
                import os
                environ=os.environ
        config=AppConfig.from_environment(environ,role=role)
        transport=http if http is not None else _ProviderHTTP()
        provider=AppInstallationProvider(config,http=transport,clock=clock)
        access=provider.issue(slug,'repository_read')
        remote=transport.request('GET','/repos/'+slug,access.token)
        verified=_repository_metadata(slug,remote,allow_private=True)
        if access.repository_id!=verified['repository_id'] or access.repository.casefold()!=slug.casefold():
            raise InstallationIdentityRefused('GA02_REPOSITORY_NUMERIC_ID_DRIFT')
        installation=transport.request('GET','/app/installations/'+str(config.installation_id),
                                       _jwt(config,int(clock())))
        if (installation.get('id')!=access.installation_id or
            installation.get('app_id') not in (None,config.app_id) or
            installation.get('suspended_at') is not None):
            raise InstallationIdentityRefused('GA02_INSTALLATION_CHANGED_OR_SUSPENDED')
        acc=installation.get('account')
        if (not isinstance(acc,dict) or
            str(acc.get('login','')).casefold()!=config.account.casefold() or
            str(acc.get('login','')).casefold()!=slug.split('/')[0].casefold()):
            raise InstallationIdentityRefused('GA02_INSTALLATION_ACCOUNT_DRIFT')
        account_id=_id(acc.get('id'),'GA02_INSTALLATION_ACCOUNT_ID_UNVERIFIED')
        if account_id != verified['owner_id']:
            raise InstallationIdentityRefused('GA02_OWNER_NUMERIC_ID_DRIFT')
        selection=installation.get('repository_selection')
        if selection not in ('all','selected'):
            raise InstallationIdentityRefused('GA02_INSTALLATION_SELECTION_INVALID')
        # The M4 grant itself proves selection for the selected repository;
        # the actual 'selected' membership must not be inferred for any other repo.
        return RepositoryObservation(slug,verified['repository_id'],verified['owner_login'],
            verified['owner_id'],access.installation_id,account_id,config.app_id,
            role,selection,verified['visibility'],verified['fork'],
            verified['parent_repository'],verified['parent_repository_id'],
            verified['parent_owner_id'],verified['default_branch'],
            verified['archived'],False,'SCOPED_APP_READ_VERIFIED')
    except InstallationIdentityRefused:
        raise
    except GitHubAppError as exc:
        raise InstallationIdentityRefused(str(exc)) from None
    except (ValueError,KeyError,TypeError,AttributeError):
        raise InstallationIdentityRefused('GA02_INSTALLATION_OBSERVATION_UNVERIFIED') from None


class AnonymousGitHubMetadata:
    """Real anonymous, HTTPS-only, bounded GitHub repository read transport.

    Never accepts an Authorization header, raw credential or redirect, never
    reads arbitrary URLs. No-install upstream evidence is strictly read-only.
    """
    is_anonymous = True

    def __init__(self, *, opener: Any | None = None, timeout: float = 12.0):
        self._opener = opener or build_opener(_NoRedirect())
        self._timeout = timeout

    def request(self, method: str, path: str) -> dict[str, Any]:
        if method != 'GET' or not isinstance(path,str) or not path.startswith('/repos/'):
            raise InstallationIdentityRefused('GA02_PUBLIC_HTTP_ENDPOINT_REFUSED')
        suffix=path[len('/repos/'):]
        parts=suffix.split('/')
        if len(parts) < 2 or len(parts)>4:
            raise InstallationIdentityRefused('GA02_PUBLIC_HTTP_SLUG_INVALID')
        slug='/'.join(parts[:2])
        if slug!=_repository(slug):
            raise InstallationIdentityRefused('GA02_PUBLIC_HTTP_SLUG_INVALID')
        if len(parts)>2:
            if (len(parts)!=4 or parts[2] not in ('commits','branches') or
                    not parts[3] or '/' in parts[3] or '%' in parts[3] or
                    parts[3].startswith(('-', '.')) or '..' in parts[3] or
                    any(c in parts[3] for c in ('?', '#', '@', '\\', '\r', '\n'))):
                raise InstallationIdentityRefused('GA02_PUBLIC_HTTP_ENDPOINT_REFUSED')

        headers={'Accept':'application/vnd.github+json',
                 'X-GitHub-Api-Version':'2022-11-28',
                 'User-Agent':'devtool-ga02-anonymous-metadata'}
        try:
            req=Request('https://api.github.com'+path,headers=headers,method='GET')
            with self._opener.open(req,timeout=self._timeout) as response:
                if response.status != 200:
                    raise InstallationIdentityRefused('GA02_PUBLIC_HTTP_UNEXPECTED_STATUS')
                raw=response.read(1024*1024+1)
        except (HTTPError,URLError,TimeoutError,OSError):
            raise InstallationIdentityRefused('GA02_PUBLIC_HTTP_GET_UNAVAILABLE') from None
        if len(raw)>1024*1024:
            raise InstallationIdentityRefused('GA02_PUBLIC_HTTP_RESPONSE_TOO_LARGE')
        try:
            obj=json.loads(raw)
        except (ValueError,UnicodeError):
            raise InstallationIdentityRefused('GA02_PUBLIC_HTTP_JSON_INVALID') from None
        if not isinstance(obj,dict):
            raise InstallationIdentityRefused('GA02_PUBLIC_HTTP_JSON_SHAPE_INVALID')
        return obj


def observe_public_upstream(repository: str, *, http: Any | None = None) -> RepositoryObservation:
    """Public metadata only, NEVER a permission grant or private fallback.

    Caller supplies an explicit unauthenticated HTTP transport exposing GET.
    It must be a separate public-only interface, not an App credential client.
    """
    slug=_repository(repository)
    if http is None:
        http=AnonymousGitHubMetadata()
    if not getattr(http,'is_anonymous',False):
        raise InstallationIdentityRefused('GA02_PUBLIC_OBSERVER_MUST_BE_ANONYMOUS')
    try:
        meta=_repository_metadata(slug,http.request('GET','/repos/'+slug),allow_private=False)
    except (GitHubAppError,KeyError,TypeError,ValueError):
        raise InstallationIdentityRefused('GA02_PUBLIC_UPSTREAM_METADATA_UNAVAILABLE') from None
    return RepositoryObservation(slug,meta['repository_id'],meta['owner_login'],
        meta['owner_id'],None,None,None,'public','none',meta['visibility'],
        meta['fork'],meta['parent_repository'],meta['parent_repository_id'],
        meta['parent_owner_id'],meta['default_branch'],meta['archived'],False,
        'PUBLIC_ANONYMOUS_METADATA_ONLY')


def prewrite_snapshot(observed: RepositoryObservation, *, http: Any,
                      token: str, branch: str, expected_sha: str,
                      require_protected: bool = False) -> dict[str, Any]:
    """Read only. Pin remote HEAD and branch governance before any proposal.

    Passing this function NEVER grants a write: that requires a separate M4/M5
    write authorization and native durable delivery-session intent.
    """
    if observed.evidence!='SCOPED_APP_READ_VERIFIED' or observed.archived or observed.suspended:
        raise InstallationIdentityRefused('GA02_WRITE_PREVIEW_REQUIRES_ACTIVE_APP')
    if not isinstance(token,str) or not token:
        raise InstallationIdentityRefused('GA02_WRITE_PREVIEW_READ_TOKEN_REQUIRED')
    branch=_branch(branch)
    if not isinstance(expected_sha,str) or not _SHA.fullmatch(expected_sha):
        raise InstallationIdentityRefused('GA02_EXPECTED_SOURCE_SHA_INVALID')
    slug=observed.repository
    try:
        commit=http.request('GET','/repos/'+slug+'/commits/'+quote(branch,safe=''),token)
        info=http.request('GET','/repos/'+slug+'/branches/'+quote(branch,safe=''),token)
    except (GitHubAppError,KeyError,TypeError,ValueError):
        raise InstallationIdentityRefused('GA02_REMOTE_PREWRITE_GET_UNAVAILABLE') from None
    remote_sha=commit.get('sha') if isinstance(commit,dict) else None
    protected=info.get('protected') if isinstance(info,dict) else None
    if remote_sha!=expected_sha:
        raise InstallationIdentityRefused('GA02_REMOTE_BASE_SHA_DRIFT')
    if type(protected) is not bool:
        raise InstallationIdentityRefused('GA02_BRANCH_PROTECTION_UNKNOWN')
    if require_protected and not protected:
        raise InstallationIdentityRefused('GA02_BRANCH_PROTECTION_NOT_ENFORCED')
    return {'schema':'devtool.ga02.prewrite-snapshot.v1',
            'repository':slug,'repository_id':observed.repository_id,
            'installation_id':observed.installation_id,'branch':branch,
            'base_sha':remote_sha,'protected':protected,
            'write_authorized':False,'write_executed':False,
            'source_observation':'SCOPED_APP_READ_VERIFIED'}
