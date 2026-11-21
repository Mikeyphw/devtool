"""Recovery-scoped GitHub REST transport reusing Devtool's credential resolver.

Remote repository *lifecycle* grants are separate from Devtool's delivery/App
capabilities. This transport never claims a credential is permitted to delete a
repository merely because it can read repository metadata. Only the recovery
engine's approved, durable mutation state authorizes write attempts.
"""
from __future__ import annotations
import json
import os
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

_API = 'https://api.github.com'
_MAX_BODY = 8*1024*1024
_MAX_PAYLOAD = 2*1024*1024
_PATH = re.compile(r'[A-Za-z0-9_.~%/-]{1,512}\Z')
_ALLOWED_ROOTS = frozenset({'repos','user','orgs','installation','app'})
_VERSION = '2022-11-28'

class RecoveryTransportError(RuntimeError):
    """Fail-closed, redacted HTTP transport failure."""

class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _endpoint(value: str) -> str:
    """Accept only relative documented API paths, never a URL or another host."""
    if not isinstance(value,str) or len(value)>750 or '\\' in value or any(ord(c)<32 for c in value):
        raise RecoveryTransportError('RECOVERY_API_ENDPOINT_INVALID')
    if value.startswith(('http:','https:','//','/')):
        raise RecoveryTransportError('RECOVERY_API_ABSOLUTE_URL_REFUSED')
    p=urlsplit('/'+value)
    if p.scheme or p.netloc or p.fragment or not p.path.startswith('/'):
        raise RecoveryTransportError('RECOVERY_API_ORIGIN_REFUSED')
    parts=p.path[1:].split('/')
    if parts[0] not in _ALLOWED_ROOTS or any(x in ('','.','..') for x in parts):
        raise RecoveryTransportError('RECOVERY_API_RESOURCE_REFUSED')
    if not _PATH.fullmatch(p.path[1:]):
        raise RecoveryTransportError('RECOVERY_API_PATH_REFUSED')
    if '%' in p.path:
        # GitHub uses percent escapes for branch names and release tag paths.
        from urllib.parse import unquote
        decoded=unquote(p.path)
        if ('//' in decoded or '/./' in decoded or '/../' in decoded or '\\' in decoded):
            raise RecoveryTransportError('RECOVERY_API_ENCODED_TRAVERSAL_REFUSED')
    if p.query and (len(p.query)>250 or not re.fullmatch(r'[A-Za-z0-9_.~%=&+,-]*',p.query)):
        raise RecoveryTransportError('RECOVERY_API_QUERY_REFUSED')
    return p.path + ('?'+p.query if p.query else '')


def _repo_for_endpoint(path: str) -> str:
    """Bind repository operations to an explicit GitHub owner/repository slug."""
    parts = path.split('?', 1)[0].lstrip('/').split('/')
    if len(parts) >= 3 and parts[0] == 'repos' and all(
        re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', component) for component in parts[1:3]
    ):
        return parts[1] + '/' + parts[2]
    return ''


def _call(path: str, *, method: str, token: str | None, payload: Any,
          missing_ok: bool, opener: Any) -> Any:
    body=None if payload is None else json.dumps(payload, separators=(',',':')).encode('utf-8')
    if body is not None and len(body)>_MAX_PAYLOAD:
        raise RecoveryTransportError('RECOVERY_API_PAYLOAD_OVERSIZE')
    headers={'Accept':'application/vnd.github+json',
             'X-GitHub-Api-Version':_VERSION,'User-Agent':'devtool-recovery-native/2'}
    if token:headers['Authorization']='Bearer '+token
    if body is not None:headers['Content-Type']='application/json'
    req=Request(_API+path, data=body, headers=headers,method=method)
    try:
        with opener.open(req, timeout=30) as response:
            status=response.status
            if status not in ((200,201,202,204) if method!='GET' else (200,)):
                raise RecoveryTransportError('RECOVERY_GITHUB_HTTP_STATUS_UNEXPECTED')
            data=response.read(_MAX_BODY+1)
    except HTTPError as exc:
        if exc.code==404 and missing_ok and method=='GET':return None
        code='UNAUTHORIZED' if exc.code==401 else 'FORBIDDEN_OR_UNVERIFIED' if exc.code==403 else 'NOT_FOUND_OR_INACCESSIBLE' if exc.code==404 else 'HTTP_ERROR'
        raise RecoveryTransportError('RECOVERY_GITHUB_'+code+'_'+str(exc.code)) from None
    except (URLError,TimeoutError,OSError):
        raise RecoveryTransportError('RECOVERY_GITHUB_UNAVAILABLE_OUTCOME_UNCERTAIN') from None
    if len(data)>_MAX_BODY:raise RecoveryTransportError('RECOVERY_GITHUB_BODY_OVERSIZE')
    if not data:return {}
    try:return json.loads(data)
    except (UnicodeError,ValueError):
        raise RecoveryTransportError('RECOVERY_GITHUB_INVALID_JSON') from None


def request_json(endpoint: str, *, method: str='GET', payload: Any=None,
                 missing_ok: bool=False, opener: Any=None) -> Any:
    path=_endpoint(endpoint)
    if method not in ('GET','POST','PUT','PATCH','DELETE'):
        raise RecoveryTransportError('RECOVERY_API_METHOD_REFUSED')
    if method=='GET' and payload is not None:
        raise RecoveryTransportError('RECOVERY_API_GET_PAYLOAD_REFUSED')
    if method!='GET' and missing_ok:
        raise RecoveryTransportError('RECOVERY_API_WRITE_MISSING_OK_REFUSED')
    repo=_repo_for_endpoint(path)
    http=opener if opener is not None else build_opener(_NoRedirect())
    if method=='GET':
        # Resolve from Devtool's read provider, bound to the exact target repo.
        from ..github_actions import resolve_token, GitHubActionsError
        try:
            token, _identity=resolve_token('auto', repository=repo,
                                           capability='repository_read')
        except GitHubActionsError:
            raise RecoveryTransportError('RECOVERY_READ_CREDENTIAL_REFUSED') from None
    else:
        # An ordinary delivery/App read credential must never implicitly gain
        # repository administration authority. This explicit source is separate
        # from GH_TOKEN and is never recorded or sent in command arguments.
        if not repo:
            raise RecoveryTransportError('RECOVERY_WRITE_REPOSITORY_SCOPE_REQUIRED')
        token=os.environ.get('DEVTOOL_RECOVERY_ADMIN_TOKEN','').strip()
        if not token or any(ord(c)<=32 or ord(c)==127 for c in token):
            raise RecoveryTransportError('RECOVERY_ADMIN_CREDENTIAL_REQUIRED')
        # Re-query effective repository administrator access with this SAME
        # credential before EACH write. No mutation is attempted if the proof
        # fails, is incomplete, or targets a different repo.
        proof=_call('/repos/'+repo, method='GET',token=token,payload=None,
                    missing_ok=False,opener=http)
        if not isinstance(proof,dict) or not isinstance(proof.get('permissions'),dict) or proof['permissions'].get('admin') is not True:
            raise RecoveryTransportError('RECOVERY_ADMIN_PERMISSION_UNVERIFIED')
    return _call(path,method=method,token=token,payload=payload,
                 missing_ok=missing_ok,opener=http)
