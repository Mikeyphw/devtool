"""M7: secret-free, read-only-first GitHub credential verification.

This module consumes the merged M4 installation provider and GitHub Actions
GET transport. It does not create repositories, rotate credentials, push, POST
PRs, dispatch workflows, merge, or approve an installation.

An explicitly requested App effective-read probe mints ONE ephemeral,
repository-scoped Contents:read token through the M4 provider; this is not a
repository mutation or proof of write capability.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from typing import Any, Callable, Mapping

from .github_app import (
    AppConfig, AppInstallationProvider, GitHubAppError, _PERMISSIONS,
    _ProviderHTTP, _jwt, _permission_ok, _private_key_bytes, _repository,
)
from .github_actions import (
    API_ORIGIN, GitHubActionsClient, GitHubActionsError, resolve_token,
)

_SCHEMA = 'devtool.m7.credential-health.v1'
_BASELINE_SCHEMA = 'devtool.m7.credential-baseline.v1'
_SHA256 = re.compile(r'[a-f0-9]{64}\Z')
_OPS = ('repository_read', 'pr_read', 'pr_create', 'branch_write',
        'checks_read', 'actions_dispatch')
_STATES = frozenset({'VALID', 'EXPIRED', 'WRONG_INSTALLATION',
                     'MISSING_REPOSITORY', 'PERMISSION_DENIED',
                     'REQUIRES_APPROVAL', 'PROVIDER_UNAVAILABLE', 'UNVERIFIED'})


class CredentialDoctorError(RuntimeError):
    """A bounded, secret-free status code (never an upstream exception)."""


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _result(repository: str, mode: str) -> dict[str, Any]:
    return {
        'schema': _SCHEMA, 'repository': repository, 'mode': mode,
        'status': 'UNVERIFIED', 'reason': 'PROBE_NOT_PERFORMED',
        'identity_verified': False, 'effective_read_verified': False,
        'write_execution_verified': False, 'token_expiry_verified': False,
        'credential_exposed': False, 'remote_repository_mutation': False,
        'permissions': {}, 'capabilities': {}, 'observed_at': _utc(),
    }


def _reason(exc: BaseException) -> tuple[str, str]:
    """Fail closed, never propagate provider payloads or user-supplied values."""
    raw = str(exc)
    if isinstance(exc, GitHubAppError):
        if raw in {'APP_TOKEN_INVALID_OR_EXPIRED', 'APP_TOKEN_TOO_CLOSE_TO_EXPIRY_FOR_GIT'}:
            return 'EXPIRED', 'APP_TOKEN_EXPIRED_OR_TOO_CLOSE_TO_EXPIRY'
        if 'INSTALLATION' in raw and ('IDENTITY' in raw or 'UNAVAILABLE' in raw):
            return 'WRONG_INSTALLATION', 'APP_INSTALLATION_IDENTITY_UNVERIFIED'
        if raw in {'APP_ACCOUNT_REPOSITORY_MISMATCH', 'APP_TOKEN_REPOSITORY_ACCESS_UNVERIFIED',
                   'APP_TOKEN_REPOSITORY_OR_PERMISSION_UNVERIFIED'}:
            return 'MISSING_REPOSITORY', 'APP_REPOSITORY_ACCESS_UNVERIFIED'
        if 'PERMISSION' in raw:
            return 'PERMISSION_DENIED', 'APP_REQUIRED_PERMISSION_MISSING'
        if 'PRIVATE_KEY' in raw or raw.startswith('APP_ID_') or raw.startswith('APP_ACCOUNT_'):
            return 'UNVERIFIED', 'APP_LOCAL_CONFIGURATION_INVALID'
        if 'AUTHENTICATION' in raw:
            return 'UNVERIFIED', 'APP_AUTHENTICATION_UNVERIFIED'
        if 'HTTP_REDIRECT' in raw:
            return 'PROVIDER_UNAVAILABLE', 'APP_REDIRECT_REFUSED'
        return 'PROVIDER_UNAVAILABLE', 'APP_PROVIDER_PROBE_FAILED'
    if isinstance(exc, GitHubActionsError):
        if 'authentication failed' in raw.lower():
            return 'EXPIRED', 'TOKEN_UNAUTHORIZED_OR_EXPIRED'
        if 'access denied' in raw.lower():
            return 'PERMISSION_DENIED', 'TOKEN_PERMISSION_OR_SSO_DENIED'
        if 'not found' in raw.lower() or 'not accessible' in raw.lower():
            return 'MISSING_REPOSITORY', 'REPOSITORY_HIDDEN_OR_UNAVAILABLE'
        return 'PROVIDER_UNAVAILABLE', 'GITHUB_API_PROBE_UNAVAILABLE'
    return 'UNVERIFIED', 'LOCAL_PREFLIGHT_UNVERIFIED'


def _matrix(permissions: Mapping[str, str]) -> dict[str, dict[str, Any]]:
    return {
        operation: {
            'requires': dict(_PERMISSIONS[operation]),
            'state': ('GRANTED_BY_INSTALLATION' if _permission_ok(permissions, _PERMISSIONS[operation])
                      else 'PERMISSION_DENIED'),
            'effective_write_tested': False,
        }
        for operation in _OPS
    }


def _public_key_fingerprint(config: AppConfig) -> str:
    """Hash public DER, not PEM bytes or the private key."""
    from cryptography.hazmat.primitives import serialization
    key = serialization.load_pem_private_key(
        _private_key_bytes(config.private_key_path), password=None)
    public = key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(public).hexdigest()


def _installation_identity(raw: Mapping[str, Any], config: AppConfig) -> None:
    if (type(raw.get('id')) is not int or raw['id'] != config.installation_id
            or raw.get('app_id') not in (None, config.app_id)
            or not isinstance(raw.get('account'), Mapping)
            or str(raw['account'].get('login', '')).casefold() != config.account.casefold()
            or raw.get('suspended_at')):
        raise GitHubAppError('APP_INSTALLATION_IDENTITY_UNVERIFIED')


def _linked_installation(installation: Mapping[str, Any], linked: Mapping[str, Any],
                         config: AppConfig) -> None:
    _installation_identity(installation, config)
    _installation_identity(linked, config)
    selection = installation.get('repository_selection')
    if selection not in ('all', 'selected'):
        raise CredentialDoctorError('INSTALLATION_SELECTION_UNVERIFIED')
    account = installation.get('account', {})
    if type(account.get('id')) is not int or account['id'] < 1:
        raise CredentialDoctorError('INSTALLATION_ACCOUNT_NUMERIC_ID_UNVERIFIED')


def inspect_app(repository: str, *, env: Mapping[str, str] | None = None,
                role: str = 'base', http: Any = None,
                probe: bool = False, clock: Callable[[], float] = time.time,
                key_fingerprint: bool = True) -> dict[str, Any]:
    """Prove installation identity/grants with GET; optional M4 read token probe.

    Does not ever issue write-scoped credentials, even when they are granted.
    """
    repository = _repository(repository)
    result = _result(repository, 'app')
    try:
        if env is None:
            if role == 'head':
                # Never reuse an unrelated base installation for a fork owner.
                env = os.environ
            else:
                from .federation_app_auth import installed_app_environment
                env = installed_app_environment()
        config = AppConfig.from_environment(env, role=role)
        result.update(app_id=config.app_id, installation_id=config.installation_id,
                      installation_account=config.account)
        provider_http = http if http is not None else _ProviderHTTP()
        jwt = _jwt(config, int(clock()))
        installation = provider_http.request(
            'GET', f'/app/installations/{config.installation_id}', jwt)
        linked = provider_http.request(
            'GET', f'/repos/{repository}/installation', jwt)
        _linked_installation(installation, linked, config)
        result['identity_verified'] = True
        result['account_id'] = installation['account']['id']
        result['repository_selection'] = installation['repository_selection']
        result['installation_suspended'] = False
        grants = installation.get('permissions')
        if (not isinstance(grants, dict) or
                any(not isinstance(k, str) or v not in ('read', 'write')
                    for k,v in grants.items())):
            raise CredentialDoctorError('INSTALLATION_PERMISSIONS_UNVERIFIED')
        result['permissions'] = {k: grants[k] for k in sorted(grants)}
        result['capabilities'] = _matrix(grants)
        result['workflow_file_update'] = (
            'GRANTED_BY_INSTALLATION' if grants.get('workflows') == 'write'
            else 'UNVERIFIED_OR_NOT_GRANTED')
        if key_fingerprint:
            result['public_key_sha256'] = _public_key_fingerprint(config)
        if probe:
            # Only Contents:read, never write-scoped POST operations.
            access = AppInstallationProvider(config,http=provider_http,clock=clock).issue(
                repository,'repository_read')
            receipt = access.public_receipt()
            remaining = int(access.expires_at-int(clock()))
            if remaining < 300:
                raise GitHubAppError('APP_TOKEN_INVALID_OR_EXPIRED')
            result['repository_id'] = access.repository_id
            result['effective_read_verified'] = True
            result['token_expiry_verified'] = True
            result['probe_token_expires_at'] = receipt['expires_at']
            result['probe_token_ttl_seconds'] = remaining
            result['probe_token_capability'] = 'repository_read'
        else:
            result['repository_id'] = None
            result['probe_token_capability'] = 'NOT_ISSUED'
        denied = [op for op,row in result['capabilities'].items()
                  if row['state'] == 'PERMISSION_DENIED']
        result['status'] = ('PERMISSION_DENIED' if denied else
                            'VALID' if probe else 'UNVERIFIED')
        result['reason'] = ('INSTALLATION_PERMISSIONS_MISSING' if denied else
                            'READ_ACCESS_VERIFIED_WRITE_METADATA_ONLY' if probe else
                            'INSTALLATION_METADATA_ONLY_EFFECTIVE_ACCESS_UNVERIFIED')
        result['missing_capabilities'] = denied
    except (GitHubAppError, CredentialDoctorError, OSError, ValueError, TypeError,
            ImportError) as exc:
        status, reason = _reason(exc)
        result['status'] = status
        result['reason'] = reason if not isinstance(exc, CredentialDoctorError) else str(exc)
    # Never add tokens, key paths, private key bytes, JWT or raw exception text.
    return result


def inspect_user_token(repository: str, *, mode: str = 'auto',
                       client: GitHubActionsClient | None = None,
                       token_source: str | None = None) -> dict[str, Any]:
    """Prove repo-specific GET for PAT/gh; no inference of write grants or expiry."""
    repository = _repository(repository)
    if mode not in ('auto','env','gh'):
        raise CredentialDoctorError('USER_TOKEN_MODE_INVALID')
    result = _result(repository, mode)
    try:
        if client is None:
            token, source = resolve_token(mode)
            if not token:
                result['reason'] = 'CREDENTIAL_NOT_CONFIGURED'
                return result
            client = GitHubActionsClient(token)
        else:
            source = token_source or mode
        result['selected_provider'] = source
        raw, headers = client._request(API_ORIGIN + f'/repos/{repository}')
        if (str(raw.get('full_name','')).casefold()!=repository.casefold() or
                type(raw.get('id')) is not int or raw['id'] < 1):
            result['status'] = 'MISSING_REPOSITORY'
            result['reason'] = 'REPOSITORY_IDENTITY_UNVERIFIED'
            return result
        result['repository_id'] = raw['id']
        result['effective_read_verified'] = True
        result['identity_verified'] = True
        result['repo_metadata_permissions'] = {
            name: bool((raw.get('permissions') or {}).get(name))
            for name in ('admin','maintain','push','pull','triage')
        }
        result['capabilities'] = {
            op: {'state': ('VALID' if op == 'repository_read' else 'UNVERIFIED'),
                 'effective_write_tested': False} for op in _OPS
        }
        # GitHub may supply PAT expiry metadata. Absent header is NOT evidence
        # of an unexpiring credential or of a fixed expiration date.
        expiration = next((value for key,value in headers.items()
                           if key.lower()=='github-authentication-token-expiration'), None)
        if expiration:
            try:
                from email.utils import parsedate_to_datetime
                expires = parsedate_to_datetime(expiration)
                if expires.tzinfo is None:
                    raise ValueError('missing timezone')
                result['token_expiry_verified'] = True
                result['token_expires_at'] = expires.astimezone(timezone.utc).isoformat()
                if expires <= datetime.now(timezone.utc):
                    result['status'] = 'EXPIRED'
                    result['reason'] = 'PAT_EXPIRY_HEADER_IN_PAST'
                    return result
            except (TypeError,ValueError,OverflowError):
                pass
        result['status'] = 'VALID'
        result['reason'] = 'USER_TOKEN_REPOSITORY_GET_VERIFIED_WRITE_UNVERIFIED'
    except (GitHubActionsError,OSError,ValueError,TypeError) as exc:
        result['status'],result['reason'] = _reason(exc)
    return result


def compare_baseline(current: Mapping[str, Any], baseline: Mapping[str, Any]) -> dict[str, Any]:
    """Detect identity/permission reductions, including same-name recreated repos."""
    if baseline.get('schema') != _BASELINE_SCHEMA:
        raise CredentialDoctorError('BASELINE_SCHEMA_INVALID')
    old = baseline.get('snapshot')
    if not isinstance(old, Mapping) or old.get('repository') != current.get('repository'):
        raise CredentialDoctorError('BASELINE_REPOSITORY_MISMATCH')
    fields = ('app_id','installation_id','account_id','repository_id',
              'repository_selection','public_key_sha256')
    changes = []
    for key in fields:
        a,b=old.get(key),current.get(key)
        if a is not None and b is not None and a != b:
            changes.append({'field':key,'old':a,'new':b,'kind':'IDENTITY_CHANGED'})
        elif a is not None and b is None:
            changes.append({'field':key,'kind':'EVIDENCE_UNAVAILABLE'})
    previous = old.get('permissions') or {}
    current_grants = current.get('permissions') or {}
    rank={'none':0,'read':1,'write':2}
    for name,value in sorted(previous.items()):
        if rank.get(current_grants.get(name),0) < rank.get(value,0):
            changes.append({'field':'permission:'+name,'kind':'PERMISSION_REDUCED',
                            'previous':value,'current':current_grants.get(name,'none')})
    if current.get('status') not in ('VALID','UNVERIFIED'):
        changes.append({'field':'health','kind':'CURRENT_CREDENTIAL_BLOCKED',
                        'state':current.get('status')})
    return {'schema':'devtool.m7.credential-drift.v1',
            'repository':current.get('repository'),
            'state':'DRIFT_DETECTED' if changes else 'NO_DRIFT_OBSERVED',
            'changes':changes,'remote_repository_mutation':False,
            'baseline_is_local_advisory':True}


def _snapshot(data: Mapping[str, Any]) -> dict[str, Any]:
    keys = ('repository','mode','app_id','installation_id','account_id','repository_id',
            'repository_selection','public_key_sha256','permissions','status',
            'identity_verified','effective_read_verified')
    return {'schema':_BASELINE_SCHEMA, 'saved_at':_utc(),
            'snapshot':{k:data[k] for k in keys if k in data}}


def _validate_private_baseline_path(path: Path, *, writing: bool) -> None:
    if path.is_symlink() or not path.is_absolute():
        raise CredentialDoctorError('BASELINE_PATH_UNSAFE')
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir():
        raise CredentialDoctorError('BASELINE_DIRECTORY_UNSAFE')
    mode = parent.stat().st_mode
    if parent.stat().st_uid != os.getuid() or mode & 0o077:
        raise CredentialDoctorError('BASELINE_DIRECTORY_NOT_PRIVATE')
    if not writing and (not path.is_file() or path.stat().st_uid!=os.getuid()
                        or path.stat().st_mode & 0o077
                        or path.stat().st_size > 65536):
        raise CredentialDoctorError('BASELINE_FILE_UNSAFE')


def write_baseline(path: Path, result: Mapping[str, Any]) -> None:
    """Explicit local write; no overwrite, no secrets, no symlinks."""
    path = Path(path).expanduser().absolute()
    _validate_private_baseline_path(path,writing=True)
    if path.exists() or path.is_symlink():
        raise CredentialDoctorError('BASELINE_ALREADY_EXISTS_NO_OVERWRITE')
    if not result.get('identity_verified') or not result.get('effective_read_verified'):
        raise CredentialDoctorError('BASELINE_REQUIRES_EFFECTIVE_READ_PROBE')
    raw=(json.dumps(_snapshot(result),sort_keys=True,indent=2)+'\n').encode()
    if len(raw)>65536:
        raise CredentialDoctorError('BASELINE_TOO_LARGE')
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(fd,'wb') as out:
        out.write(raw);out.flush();os.fsync(out.fileno())


def load_baseline(path: Path) -> dict[str,Any]:
    path=Path(path).expanduser().absolute()
    _validate_private_baseline_path(path,writing=False)
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
    try:
        with os.fdopen(fd,'rb') as stream:
            raw=json.loads(stream.read(65537))
    except (OSError,ValueError,UnicodeError):
        raise CredentialDoctorError('BASELINE_CORRUPTED') from None
    if not isinstance(raw,dict) or raw.get('schema')!=_BASELINE_SCHEMA:
        raise CredentialDoctorError('BASELINE_SCHEMA_INVALID')
    return raw


def predelete_feasibility(health: Mapping[str, Any], *, backup_sha256: str = '',
                          dependencies_verified: bool = False) -> dict[str, Any]:
    """Mandatory hard-stop: source credentials cannot attest a future repo ID.

    This is a *pre-delete* feasibility report, never approval to delete.
    Even all-repository App access does not establish future repo creation
    authority, dependent credentials or post-recreation acceptance.
    """
    blockers=[]
    if not health.get('effective_read_verified'):
        blockers.append('CURRENT_REPOSITORY_READ_UNVERIFIED')
    if health.get('repository_selection')!='all' and health.get('mode')=='app':
        blockers.append('REINSTALLATION_OR_REPOSITORY_SELECTION_REQUIRES_APPROVAL')
    if not _SHA256.fullmatch(backup_sha256):
        blockers.append('VERIFIED_BACKUP_EVIDENCE_REQUIRED')
    if not dependencies_verified:
        blockers.append('DEPENDENT_REPOSITORY_CAPABILITIES_UNVERIFIED')
    blockers.append('FUTURE_REPOSITORY_CREATION_AND_CREDENTIAL_AUTHORITY_UNVERIFIED')
    return {'schema':'devtool.m7.predelete-feasibility.v1',
            'repository':health.get('repository'),'state':'BLOCKED',
            'destructive_operation_authorized':False,
            'backup_digest_supplied':bool(_SHA256.fullmatch(backup_sha256)),
            'blockers':blockers,'remote_repository_mutation':False}


def postrecreate_acceptance(health: Mapping[str, Any], *, previous_repository_id: int,
                            dependencies_verified: bool = False,
                            federation_verified: bool = False) -> dict[str, Any]:
    """Verify new numeric repository identity without approving restoration."""
    blockers=[]
    new_id=health.get('repository_id')
    if (type(previous_repository_id) is not int or previous_repository_id<1
            or type(new_id) is not int or new_id<1
            or new_id==previous_repository_id):
        blockers.append('RECREATED_REPOSITORY_ID_NOT_INDEPENDENTLY_VERIFIED')
    if health.get('status')!='VALID' or not health.get('effective_read_verified'):
        blockers.append('RECREATED_REPOSITORY_ACCESS_NOT_VERIFIED')
    for op in _OPS:
        if (health.get('capabilities') or {}).get(op,{}).get('state')!='GRANTED_BY_INSTALLATION':
            blockers.append('RECREATED_REPOSITORY_CAPABILITY_'+op.upper()+'_NOT_VERIFIED')
    if not dependencies_verified:
        blockers.append('DEPENDENT_REPOSITORY_CAPABILITIES_PENDING')
    if not federation_verified:
        blockers.append('FEDERATION_AND_ACTIONS_REQUIRE_INDEPENDENT_ACCEPTANCE')
    return {'schema':'devtool.m7.postrecreate-acceptance.v1',
            'repository':health.get('repository'),'previous_repository_id':previous_repository_id,
            'new_repository_id':new_id if isinstance(new_id,int) else None,
            'state':'CAPABILITY_PROBES_VERIFIED' if not blockers else 'BLOCKED',
            'blockers':blockers,'remote_repository_mutation':False,
            'delivery_or_release_qualified':False}


_GUIDANCE={
 'EXPIRED': 'Renew the credential in its provider, then run a fresh effective-read probe.',
 'WRONG_INSTALLATION': 'Verify App ID, installation ID, account, suspension and GitHub installation selection.',
 'MISSING_REPOSITORY': 'Grant this exact repository through GitHub approval and verify its immutable numeric ID.',
 'PERMISSION_DENIED': 'Review per-operation installation/PAT permissions and organization SSO, then request GitHub approval.',
 'REQUIRES_APPROVAL': 'Use the GitHub installation or authorization consent UI; Devtool cannot consent for you.',
 'PROVIDER_UNAVAILABLE': 'Check GitHub connectivity/rate limits; do not retry uncertain write operations.',
 'UNVERIFIED': 'Supply explicitly selected credentials and run the requested read-only or scoped-read probe.',
 'VALID': 'No credential repair indicated by this read-only scope. Write permissions remain metadata-only.',
}


def repair_guidance(health: Mapping[str, Any]) -> dict[str, Any]:
    state = str(health.get('status') or 'UNVERIFIED')
    if state not in _STATES:state='UNVERIFIED'
    return {'schema':'devtool.m7.guided-repair.v1','state':state,
            'repository':health.get('repository'),
            'guidance':_GUIDANCE[state],
            'installation_change_performed':False,
            'credential_rotation_performed':False,
            'remote_repository_mutation':False}


def inspect_ssh(repository: str, *, probe: bool = False) -> dict[str, Any]:
    """Explicit batch SSH ls-remote: proves read only, never Git push access."""
    import subprocess
    repository = _repository(repository)
    result = _result(repository,'ssh')
    result['selected_provider'] = 'ssh'
    if not probe:
        result['reason'] = 'SSH_REPOSITORY_ACCESS_NOT_PROBED'
        return result
    env = os.environ.copy()
    env['GIT_TERMINAL_PROMPT'] = '0'
    env['GIT_SSH_COMMAND'] = 'ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=10'
    for name in list(env):
        if name.startswith('GIT_TRACE') or name in ('GIT_CURL_VERBOSE','GIT_ASKPASS'):
            env.pop(name,None)
    url = f'git@github.com:{repository}.git'
    try:
        completed=subprocess.run(['git','ls-remote',url,'HEAD'],
              env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
              text=True,check=False,timeout=30)
        if completed.returncode or not re.fullmatch(r'[a-f0-9]{40}\s+HEAD\s*',completed.stdout):
            result['status']='UNVERIFIED'
            result['reason']='SSH_REPOSITORY_READ_FAILED_OR_HOST_UNVERIFIED'
            return result
        result['status']='VALID'
        result['reason']='SSH_READ_VERIFIED_WRITE_NOT_TESTED'
        result['effective_read_verified']=True
        result['identity_verified']=False  # ssh -T does not prove an installation ID.
    except (OSError,subprocess.TimeoutExpired):
        result['status']='PROVIDER_UNAVAILABLE'
        result['reason']='SSH_PROVIDER_UNAVAILABLE'
    return result
