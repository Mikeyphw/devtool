#!/usr/bin/env python3
"""M8 finite, user-approved event continuation and source-bound PR maintenance.

A webhook is only a wakeup. Native Devtool session and CI receipts remain authoritative.
All non-idempotent PR writes must use the canonical delivery session intent ledger.
No implicit reruns, force pushes, merges, release actions or background workers.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request
from urllib.parse import quote
from uuid import UUID

from . import m8_webhooks as wh

_SCHEMA = 'devtool.m8.automation-policy.v1'
_EVENTS = frozenset(f'{name}:{action}' for name, actions in wh.EVENTS.items() for action in actions)
_EFFECTS = frozenset({'observe', 'pr_reconcile', 'pr_comment', 'pr_labels_add',
                      'pr_body_set', 'pr_labels_replace', 'commit_status',
                      'ci_dispatch', 'ci_rerun'})
_SHA = re.compile(r'[0-9a-f]{40}\Z')
_HEX32 = re.compile(r'[0-9a-f]{32}\Z')
_LABEL = re.compile(r'[A-Za-z0-9_.-]{1,48}\Z')
_BRANCH = re.compile(r'[A-Za-z0-9._/-]{1,160}\Z')
_REPO = re.compile(r'[A-Za-z0-9-]{1,39}/[A-Za-z0-9_.-]{1,100}\Z')
_CONTEXT = re.compile(r'devtool/m8/[A-Za-z0-9_.-]{1,60}\Z')


class M8Refusal(RuntimeError):
    pass


def refuse(code: str) -> None:
    raise M8Refusal(code)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dt(value: str) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', value):
        refuse('M8_POLICY_EXPIRY_FORMAT')
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        refuse('M8_POLICY_EXPIRY_INVALID')


def _hash_pin(value: str) -> bool:
    return type(value) is str and bool(re.fullmatch(r'[a-f0-9]{64}', value))


def _positive(value: Any) -> bool:
    return type(value) is int and value > 0


def _canonical(data: Mapping[str, Any]) -> bytes:
    return (json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n').encode('utf-8')


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Policy:
    schema: str
    policy_id: str
    repository: str
    repository_id: int
    installation_id: int
    head_sha: str
    head_branch: str
    base_branch: str
    base_sha: str
    pr_number: int
    session_id: str
    expires_at: str
    events: tuple[str, ...]
    effects: tuple[str, ...]
    labels: tuple[str, ...]
    comment: str
    status_context: str
    status_state: str
    workflow: str
    rerun_run_id: int
    max_events: int
    pr_body: str = ''
    pr_body_before_sha256: str = ''
    pr_labels_before_sha256: str = ''

    @classmethod
    def parse(cls, raw: Mapping[str, Any], *, at: datetime | None = None) -> 'Policy':
        optional = {'pr_body', 'pr_body_before_sha256', 'pr_labels_before_sha256'}
        if (not isinstance(raw, dict) or
                not (set(raw) == set(cls.__dataclass_fields__) or
                     set(raw) == set(cls.__dataclass_fields__) - optional)):
            refuse('M8_POLICY_SCHEMA')
        if any(type(raw[k]) is not list for k in ('events', 'effects', 'labels')):
            refuse('M8_POLICY_COLLECTIONS')
        data = {**raw, **{k: tuple(raw[k]) for k in ('events', 'effects', 'labels')}}
        p = cls(**data)
        if p.schema != _SCHEMA: refuse('M8_POLICY_VERSION')
        try:
            if str(UUID(p.policy_id)) != p.policy_id: refuse('M8_POLICY_ID')
        except (ValueError, TypeError):
            refuse('M8_POLICY_ID')
        if (not isinstance(p.repository, str) or not _REPO.fullmatch(p.repository)
                or not all(_positive(x) for x in (p.repository_id, p.installation_id, p.pr_number))
                or not isinstance(p.head_sha, str) or not _SHA.fullmatch(p.head_sha)
                or not isinstance(p.base_sha, str) or not _SHA.fullmatch(p.base_sha)
                or not isinstance(p.session_id, str) or not _HEX32.fullmatch(p.session_id)):
            refuse('M8_POLICY_IDENTITY')
        if (not isinstance(p.head_branch, str) or not _BRANCH.fullmatch(p.head_branch)
                or not isinstance(p.base_branch, str) or not _BRANCH.fullmatch(p.base_branch)
                or '..' in p.head_branch.split('/') or '..' in p.base_branch.split('/')):
            refuse('M8_POLICY_BRANCH')
        if (not p.events or len(p.events) > 20 or len(set(p.events)) != len(p.events)
                or any(type(x) is not str or x not in _EVENTS for x in p.events)):
            refuse('M8_POLICY_EVENTS')
        if (not p.effects or len(set(p.effects)) != len(p.effects)
                or any(type(x) is not str or x not in _EFFECTS for x in p.effects)
                or 'observe' not in p.effects):
            refuse('M8_POLICY_EFFECTS')
        if (len(p.labels) > 8 or len(set(p.labels)) != len(p.labels)
                or any(type(x) is not str or not _LABEL.fullmatch(x) for x in p.labels)):
            refuse('M8_POLICY_LABELS')
        if (type(p.comment) is not str or len(p.comment) > 500 or '\x00' in p.comment
                or type(p.status_context) is not str or type(p.status_state) is not str
                or type(p.workflow) is not str):
            refuse('M8_POLICY_CONTENT')
        if ('pr_comment' in p.effects) != bool(p.comment): refuse('M8_POLICY_COMMENT_SCOPE')
        label_effects = {'pr_labels_add', 'pr_labels_replace'} & set(p.effects)
        if (len(label_effects)>1 or
                ('pr_labels_add' in label_effects and not p.labels) or
                (not label_effects and bool(p.labels))):
            refuse('M8_POLICY_LABEL_SCOPE')
        if (type(p.pr_body) is not str or len(p.pr_body) > 4096
                or '\x00' in p.pr_body or type(p.pr_body_before_sha256) is not str
                or type(p.pr_labels_before_sha256) is not str):
            refuse('M8_POLICY_PR_BODY_SCOPE')
        if 'pr_body_set' in p.effects:
            if (not _hash_pin(p.pr_body_before_sha256)
                    or len(_canonical({'body':p.pr_body})) > 8192):
                refuse('M8_POLICY_PR_BODY_PREIMAGE_REQUIRED')
        elif p.pr_body or p.pr_body_before_sha256:
            refuse('M8_POLICY_PR_BODY_SCOPE')
        if 'pr_labels_replace' in p.effects:
            if not _hash_pin(p.pr_labels_before_sha256):
                refuse('M8_POLICY_LABEL_PREIMAGE_REQUIRED')
        elif p.pr_labels_before_sha256:
            refuse('M8_POLICY_LABEL_PREIMAGE_UNAPPROVED')
        if 'commit_status' in p.effects:
            if not _CONTEXT.fullmatch(p.status_context) or p.status_state not in {'pending','success','failure','error'}:
                refuse('M8_POLICY_STATUS_SCOPE')
        elif p.status_context or p.status_state:
            refuse('M8_POLICY_STATUS_SCOPE')
        if bool({'ci_dispatch','ci_rerun'} & set(p.effects)):
            if p.workflow != 'devtool-full-repository-pytest.yml': refuse('M8_POLICY_WORKFLOW_SCOPE')
        elif p.workflow:
            refuse('M8_POLICY_WORKFLOW_SCOPE')
        if ('ci_rerun' in p.effects and not _positive(p.rerun_run_id)) or (
                'ci_rerun' not in p.effects and p.rerun_run_id != 0):
            refuse('M8_POLICY_RERUN_ID_SCOPE')
        if type(p.max_events) is not int or not 1 <= p.max_events <= 1000:
            refuse('M8_POLICY_EVENT_BOUND')
        t = at or _now()
        expiry = _dt(p.expires_at)
        if expiry <= t or expiry > t + timedelta(days=7):
            refuse('M8_POLICY_EXPIRED_OR_TOO_LONG')
        return p

    def public(self) -> dict[str, Any]:
        d = asdict(self)
        # A user's approved message is not a secret, but keep it out of routine output.
        d['comment_sha256'] = _hash(self.comment.encode()) if self.comment else ''
        d.pop('comment')
        d['pr_body_sha256'] = (_hash(self.pr_body.encode('utf-8'))
             if 'pr_body_set' in self.effects else '')
        d.pop('pr_body')
        return d


def _policy_path(root: Path) -> Path:
    return wh.root_at(root) / 'automation-policy.json'


def load_policy(root: Path, *, allow_expired: bool = False) -> Policy:
    path = _policy_path(root)
    raw = wh.read(path)
    if allow_expired:
        # Only for read-only status output; never approve or execute expired work.
        expiry = _dt(raw.get('expires_at'))
        at = min(_now(), expiry - timedelta(seconds=1))
        at = max(at, expiry - timedelta(days=7))
        p = Policy.parse(raw, at=at)
    else:
        p = Policy.parse(raw)
    if not allow_expired and (wh.root_at(root) / 'automation-revoked.json').exists():
        refuse('M8_POLICY_REVOKED')
    return p


def _native_session(p: Policy, *, confirmation: tuple[str, str, str | None] | None = None) -> dict[str, Any]:
    from .delivery_session import status, m8_confirmation_status, DeliverySessionError
    if confirmation is None:
        d = status(p.session_id)
        eligible = d.get('resumable') is True
    else:
        operation, key, intent_id = confirmation
        try:
            d = m8_confirmation_status(p.session_id, operation, key, intent_id=intent_id)
        except DeliverySessionError:
            refuse('M8_NATIVE_CONFIRMATION_INTENT_DRIFT')
        eligible = (d.get('resumable') is False and
                    isinstance(d.get('confirmation_intent_id'), str) and
                    (intent_id is None or d['confirmation_intent_id'] == intent_id))
    if (d.get('repository') != p.repository or d.get('head_sha') != p.head_sha
            or d.get('head') != p.head_branch or d.get('base') != p.base_branch
            or (d.get('pr') or {}).get('number') != p.pr_number
            or d.get('source_binding') != 'matching' or not eligible):
        refuse('M8_NATIVE_SESSION_DRIFT')
    return d


def _client(p: Policy, auth: str, capability: str = 'pr_read',
            operation: str = ''):
    if auth == 'app':
        if operation:
            from .ga01_scoped_auth import capability_for_operation, AppOperationRefused
            try:
                if capability_for_operation(operation) != capability:
                    refuse('M8_GA01_CAPABILITY_MISMATCH')
            except AppOperationRefused:
                refuse('M8_GA01_OPERATION_UNAUTHORIZED')
        from .github_app import AppInstallationProvider, make_app_client
        provider = AppInstallationProvider.from_environment()
        if provider.config.installation_id != p.installation_id:
            refuse('M8_APP_INSTALLATION_ID_DRIFT')
        return make_app_client(p.repository, capability)
    from .github_actions import GitHubActionsClient, resolve_token
    token, _ = resolve_token(auth)
    return GitHubActionsClient(token)


def verify_live(p: Policy, auth: str = 'auto', client: Any = None, *,
                confirmation: tuple[str, str, str | None] | None = None) -> tuple[dict[str, Any], Any, dict[str, Any]]:
    session = _native_session(p) if confirmation is None else _native_session(p, confirmation=confirmation)
    client = client or _client(p, auth)
    repo = client._get(f'/repos/{p.repository}')
    if repo.get('id') != p.repository_id or repo.get('full_name') != p.repository:
        refuse('M8_LIVE_REPOSITORY_RECREATED_OR_RENAMED')
    branch = client._get(f'/repos/{p.repository}/branches/{quote(p.head_branch,safe="")}')
    if (branch.get('commit') or {}).get('sha') != p.head_sha:
        refuse('M8_LIVE_BRANCH_DRIFT')
    pr = client._get(f'/repos/{p.repository}/pulls/{p.pr_number}')
    head, base = pr.get('head') or {}, pr.get('base') or {}
    if (pr.get('number') != p.pr_number or pr.get('state') != 'open'
            or head.get('sha') != p.head_sha or head.get('ref') != p.head_branch
            or (head.get('repo') or {}).get('full_name') != p.repository
            or base.get('ref') != p.base_branch or base.get('sha') != p.base_sha
            or (base.get('repo') or {}).get('full_name') != p.repository):
        refuse('M8_LIVE_PR_SOURCE_OR_BASE_DRIFT')
    return session, client, pr


def approve(root: Path, raw: dict[str, Any], *, yes: bool, auth: str = 'auto', client: Any = None) -> dict[str, Any]:
    if yes is not True: refuse('M8_APPROVAL_REQUIRED')
    p = Policy.parse(raw)
    pin = wh.current_pin(root)
    if any((getattr(pin, name) != getattr(p, name)) for name in
           ('repository','repository_id','installation_id','head_sha','pr_number','session_id')):
        refuse('M8_POLICY_PIN_MISMATCH')
    verify_live(p, auth, client)
    root = wh.root_at(root)
    with wh.locked(root):
        path = root / 'automation-policy.json'
        if path.is_symlink(): refuse('M8_POLICY_SYMLINK')
        if path.exists():
            if wh.read(path) != raw: refuse('M8_POLICY_IMMUTABLE')
            return {'state':'EXISTING','policy_sha256':_hash(_canonical(raw))}
        wh.record(path, raw)
    return {'state':'APPROVED','policy_sha256':_hash(_canonical(raw)),
            'expiry':p.expires_at,'source_sha':p.head_sha,'automatic_mutations':False}


def revoke(root: Path, *, yes: bool) -> dict[str, Any]:
    if yes is not True: refuse('M8_REVOCATION_CONFIRMATION_REQUIRED')
    root = wh.root_at(root)
    p = load_policy(root, allow_expired=True)
    with wh.locked(root):
        path = root/'automation-revoked.json'
        if path.exists(): return {'state':'ALREADY_REVOKED'}
        wh.record(path, {'policy_id':p.policy_id,'revoked_at':_now().isoformat()})
    return {'state':'REVOKED'}


def _read_rows(root: Path, directory: str, *, limit: int = 1000) -> list[dict[str, Any]]:
    base = wh.root_at(root)/directory
    if base.is_symlink(): refuse('M8_JOURNAL_SYMLINK')
    if not base.exists(): return []
    if not base.is_dir() or base.stat().st_mode & 0o077: refuse('M8_JOURNAL_UNSAFE')
    files=sorted(base.glob('*.json'))
    if len(files)>limit: refuse('M8_JOURNAL_LIMIT')
    return [wh.read(f) for f in files]


def _journal(root: Path, subdir: str, key: str, row: dict[str, Any]):
    root=wh.root_at(root)
    base=root/subdir
    if base.is_symlink(): refuse('M8_JOURNAL_SYMLINK')
    base.mkdir(mode=0o700, exist_ok=True)
    if base.stat().st_mode & 0o077: refuse('M8_JOURNAL_UNSAFE')
    path=base/(key+'.json')
    with wh.locked(root):
        if len(list(base.glob('*.json')))>=1000 and not path.exists():refuse('M8_JOURNAL_LIMIT')
        if path.exists():
            if wh.read(path)!=row: refuse('M8_JOURNAL_COLLISION')
            return False
        wh.record(path,row)
        return True


def _observe(p: Policy, root: Path, request_id: str, auth: str) -> dict[str, Any]:
    from .delivery_qualification import qualify as native_qualify
    if not _HEX32.fullmatch(request_id): refuse('M8_REQUEST_ID_INVALID')
    result=native_qualify(p.session_id,request_id=request_id,auth=auth)
    if result.get('head_sha') != p.head_sha or result.get('session_id') != p.session_id:
        refuse('M8_NATIVE_B3_IDENTITY')
    return {'required_ci':(result.get('required_delivery_ci') or {}).get('state'),
            'regression':(result.get('repository_regression') or {}).get('state'),
            'pre_merge':result.get('pre_merge_qualification'),
            'read_only':True}


def process_events(root: Path, *, request_id: str, auth: str='auto', limit: int = 100) -> dict[str, Any]:
    p=load_policy(root)
    verify_live(p,auth)
    if 'observe' not in p.effects: refuse('M8_POLICY_NO_OBSERVE')
    root=wh.root_at(root)
    event_dir=root/'events'
    if event_dir.is_symlink():refuse('M8_EVENT_DIR_SYMLINK')
    files=sorted(event_dir.glob('*.json'))
    if len(files)>p.max_events:refuse('M8_EVENT_BACKLOG_EXCEEDS_APPROVAL')
    entries=[wh.read(x) for x in files]
    done={r.get('delivery') for r in _read_rows(root,'processed',limit=p.max_events)}
    if len(done)>=p.max_events: refuse('M8_POLICY_EVENT_QUOTA')
    counts={'observed':0,'duplicate':0,'ignored':0}
    for entry in reversed(entries):
        uid=entry.get('delivery')
        if not isinstance(uid,str): refuse('M8_EVENT_JOURNAL_CORRUPT')
        if uid in done: counts['duplicate']+=1;continue
        signal=entry.get('signal') or {}
        if f"{signal.get('kind')}:{signal.get('action')}" not in p.events:
            counts['ignored']+=1
            _journal(root,'processed',uid,{'delivery':uid,'state':'IGNORED_POLICY','policy_id':p.policy_id})
            done.add(uid)
            continue
        if len(done)>=p.max_events: refuse('M8_POLICY_EVENT_QUOTA')
        receipt=_observe(p,root,request_id,auth)
        _journal(root,'processed',uid,{'delivery':uid,'state':'OBSERVED','policy_id':p.policy_id,
                                      'request_id':request_id,**receipt})
        done.add(uid);counts['observed']+=1
    return {'schema':'devtool.m8.event-follow.v2','state':'OBSERVED','read_only':True,
            'automatic_mutations':False,**counts}


def poll_once(root: Path, *, request_id: str, auth: str='auto', client: Any = None) -> dict[str, Any]:
    """Termux-friendly bounded pull fallback. No public webhook endpoint needed."""
    p=load_policy(root)
    _,_,pr=verify_live(p,auth,client)
    receipt=_observe(p,root,request_id,auth)
    snapshot={'schema':'devtool.m8.poll.v1','policy_id':p.policy_id,'head_sha':p.head_sha,
              'pr_number':p.pr_number,'pr_state':pr.get('state'),
              'pr_draft':pr.get('draft'),
              'pr_updated_at':pr.get('updated_at'),
              'read_only':True,'qualification':receipt}
    fingerprint=_hash(_canonical(snapshot))
    written=_journal(root,'poll-observations',fingerprint,snapshot)
    return {'state':'CHANGED' if written else 'UNCHANGED','read_only':True,
            'sha256':fingerprint,'qualification':receipt,'pr_number':p.pr_number}


def _native_intent(p:Policy, operation:str, key:str):
    from .delivery_session import record_intent
    rec=record_intent(p.session_id,operation,key,approved=True)
    if not rec.get('new') or rec.get('state')!='unresolved':
        refuse('M8_EXISTING_NATIVE_INTENT_READ_ONLY_RECONCILE')
    return rec['intent_id']


def _native_confirm(p:Policy, operation:str, key:str, proof:dict[str,Any], *, intent_id:str):
    from .delivery_session import confirm_intent
    receipt=confirm_intent(p.session_id,operation,key,proof,expected_intent_id=intent_id)
    if receipt.get('confirmed') is not True: refuse('M8_INTENT_NOT_CONFIRMED')


def _approved_write(client:Any, p:Policy, operation:str, number_or_sha:str,
                    payload:dict[str,Any]):
    """Only five finite GitHub writes, never arbitrary URL/method/payload.

    Use the native M8 intent before any network request; never retry
    PATCH/PUT/POST whose outcome is unknown.
    """
    from .github_actions import API_ORIGIN, API_VERSION, GitHubActionsError, GitHubDispatchOutcomeUnknown
    from urllib.request import Request
    allowed={
        'pr_comment':('POST',f'/repos/{p.repository}/issues/{p.pr_number}/comments'),
        'pr_labels':('POST',f'/repos/{p.repository}/issues/{p.pr_number}/labels'),
        'commit_status':('POST',f'/repos/{p.repository}/statuses/{p.head_sha}'),
        'pr_body_set':('PATCH',f'/repos/{p.repository}/pulls/{p.pr_number}'),
        'pr_labels_replace':('PUT',f'/repos/{p.repository}/issues/{p.pr_number}/labels')}
    if operation not in allowed: refuse('M8_WRITE_OPERATION_INVALID')
    method,path=allowed[operation]
    if operation in ('pr_body_set','pr_labels_replace'):
        if number_or_sha!=str(p.pr_number):
            refuse('M8_PR_WRITE_TARGET_NOT_APPROVED')
        approved = ({'body':p.pr_body} if operation=='pr_body_set'
                    else {'labels':list(p.labels)})
        if type(payload) is not dict or payload!=approved:
            refuse('M8_PR_WRITE_PAYLOAD_NOT_APPROVED')
    if hasattr(client, 'assert_approved_pr_write') and method in ('PATCH','PUT'):
        client.assert_approved_pr_write(p.repository,operation,method,p.pr_number)
    elif getattr(client,'_app_access',None) is not None and method in ('PATCH','PUT'):
        refuse('M8_APP_PR_WRITE_ADAPTER_MISSING')
    token=getattr(client,'_token',None)
    if not isinstance(token,str) or not token: refuse('M8_AUTHENTICATED_CLIENT_REQUIRED')
    raw=_canonical(payload)
    if len(raw)>(8192 if operation=='pr_body_set' else 2048):
        refuse('M8_WRITE_BODY_TOO_LARGE')
    headers={'Accept':'application/vnd.github+json','X-GitHub-Api-Version':API_VERSION,
             'User-Agent':'devtool-m8-approved-native/1','Content-Type':'application/json',
             'Authorization':'Bearer '+token}
    req=Request(API_ORIGIN+path,data=raw,headers=headers,method=method)
    try:
        with client._opener.open(req,timeout=client.timeout) as response:
            status=int(response.status)
            data=response.read(65537)
    except HTTPError as exc:
        if int(exc.code) in (400,401,403,404,409,412,422):
            raise GitHubActionsError('M8 GitHub write definitively rejected by provider') from None
        raise GitHubDispatchOutcomeUnknown('M8 PR mutation outcome uncertain; reconcile read-only') from None
    except (URLError,TimeoutError,OSError):
        raise GitHubDispatchOutcomeUnknown('M8 PR mutation transport outcome uncertain') from None
    if status not in (200,201) or len(data)>65536:
        raise GitHubDispatchOutcomeUnknown('M8 unexpected PR mutation response; outcome uncertain')
    try: d=json.loads(data)
    except (UnicodeError,ValueError):
        raise GitHubDispatchOutcomeUnknown('M8 invalid mutation receipt; outcome uncertain') from None
    if not isinstance(d,dict) and not (operation in ('pr_labels',
            'pr_labels_replace') and isinstance(d,list)):
        raise GitHubDispatchOutcomeUnknown('M8 invalid mutation receipt type; outcome uncertain')
    return d


def _effect_key(p:Policy,effect:str,payload:dict[str,Any]) -> str:
    return 'm8:'+p.policy_id+':'+effect+':'+_hash(_canonical(payload))[:24]


def _run_native_ci(root:Path,p:Policy,effect:str,auth:str,client:Any,session:dict[str,Any]) -> dict[str,Any]:
    """Only the canonical Devtool Actions CLI may POST workflow dispatch/reruns.

    Our append-only intent stops uncertain command replay across separate
    operator invocations. Native Actions owns its separate dispatch/run journal.
    """
    if effect=='ci_rerun':
        run=client.get_run(p.repository,p.rerun_run_id)
        if (run.get('id')!=p.rerun_run_id or run.get('workflow_id')!=383977915 or
                run.get('head_sha')!=p.head_sha or run.get('head_branch')!=p.head_branch or
                run.get('event')!='workflow_dispatch' or run.get('status')!='completed' or
                run.get('conclusion') not in ('failure','cancelled','timed_out')):
            refuse('M8_RERUN_SOURCE_OR_STATE_NOT_ELIGIBLE')
    repo_root=Path(str(session.get('repo_root') or ''))
    if not repo_root.is_dir(): refuse('M8_NATIVE_WORKTREE_MISSING')
    key=_hash(_canonical({'effect':effect,'policy_id':p.policy_id,'head_sha':p.head_sha}))
    intent={'kind':effect,'policy_id':p.policy_id,'source_sha':p.head_sha,
            'state':'INTENT_RECORDED_NO_AUTO_RETRY'}
    if not _journal(root,'ci-intents',key,intent):
        refuse('M8_CI_INTENT_ALREADY_EXISTS_READ_ONLY_RECONCILE')
    argv=[sys.executable,'-B','-m','devtool','--host','--no-clipboard',
          '--repo',str(repo_root),'actions']
    if effect=='ci_dispatch':
        argv+=['run',p.workflow,'--github-repo',p.repository,'--ref',p.head_branch,
               '--auth',auth,'--require-local-head','--resume-if-existing','--yes','--json']
    else:
        argv+=['rerun','--github-repo',p.repository,'--run-id',str(p.rerun_run_id),
               '--failed-only','--yes','--json']
    try:
        proc=subprocess.run(argv,check=False,capture_output=True,text=True,
                            timeout=180,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
    except (OSError,subprocess.TimeoutExpired):
        refuse('M8_NATIVE_ACTIONS_OUTCOME_UNKNOWN_NO_RETRY')
    if proc.returncode:
        refuse('M8_NATIVE_ACTIONS_NONZERO_RECONCILE_NO_RETRY')
    try: envelope=json.loads(proc.stdout)
    except ValueError:refuse('M8_NATIVE_ACTIONS_RECEIPT_UNKNOWN_NO_RETRY')
    if envelope.get('ok') is not True or envelope.get('exit_code')!=0:
        refuse('M8_NATIVE_ACTIONS_UNQUALIFIED_NO_RETRY')
    data=envelope.get('data')
    if not isinstance(data,dict):refuse('M8_NATIVE_ACTIONS_ENVELOPE_INVALID_NO_RETRY')
    if effect=='ci_dispatch':
        if (data.get('repository')!=p.repository or data.get('ref')!=p.head_branch or
                data.get('pre_dispatch_sha')!=p.head_sha or
                data.get('workflow_id')!=383977915 or data.get('dispatched') is not True):
            refuse('M8_NATIVE_DISPATCH_CORRELATION_UNKNOWN_NO_RETRY')
    result={'effect':effect,'state':'NATIVE_ACTIONS_RECEIPT_OBSERVED',
            'policy_id':p.policy_id,'head_sha':p.head_sha,
            'request_id':data.get('request_id'), 'run_id':data.get('run_id'),
            'read_only':False,'release_qualification':'NOT_PERFORMED'}
    _journal(root,'ci-results',key,result)
    return result


def maintain(root:Path, effect:str, *, yes:bool,auth:str='auto', request_id:str='', client:Any=None) -> dict[str,Any]:
    if yes is not True: refuse('M8_MAINTENANCE_REQUIRES_EXPLICIT_YES')
    p=load_policy(root)
    if effect not in p.effects or effect=='observe': refuse('M8_MAINTENANCE_EFFECT_NOT_APPROVED')
    session,reader,_=verify_live(p,auth,client)
    if effect=='pr_reconcile':
        from .delivery_github import pr
        result=pr(p.session_id,yes=True,auth=auth)
        return {'state':'NATIVE_PR_RECONCILED','native':result,'automatic_mutations':False}
    if effect in {'ci_dispatch','ci_rerun'}:
        return _run_native_ci(root,p,effect,auth,reader,session)
    if effect=='commit_status' and p.status_state=='success':
        if _observe(p,root,request_id,auth).get('pre_merge')!='PASSED':
            refuse('M8_CUSTOM_SUCCESS_STATUS_REQUIRES_NATIVE_B3')
    capability='status_write' if effect=='commit_status' else 'pr_maintain'
    selected_operation=({'pr_body_set':'m8.pr.body.set',
                         'pr_labels_replace':'m8.pr.labels.replace'}.get(effect,''))
    writer=client or _client(p,auth,capability,operation=selected_operation)
    if auth=='app' and selected_operation and not hasattr(writer,
            'assert_approved_pr_write'):
        refuse('M8_APP_PR_WRITE_ADAPTER_MISSING')
    # Always re-read remote identity with the same exact permission-scoped client.
    verify_live(p,auth,writer)
    if effect=='pr_comment':
        marker=f'<!-- devtool-m8:{p.policy_id}:{p.head_sha[:12]} -->'
        body=p.comment+'\n\n'+marker
        request={'body':body}
        operation='pr_comment'
        key=_effect_key(p,effect,request)
        intent_id=_native_intent(p,operation,key)
        response=_approved_write(writer,p,operation,str(p.pr_number),request)
        comment_id=response.get('id') if isinstance(response,dict) else None
        if not _positive(comment_id):refuse('M8_COMMENT_RECEIPT_UNKNOWN_NO_RETRY')
        check=writer._get(f'/repos/{p.repository}/issues/comments/{comment_id}')
        if (check.get('id')!=comment_id or check.get('body')!=body or
                check.get('issue_url') != f'https://api.github.com/repos/{p.repository}/issues/{p.pr_number}'):
            refuse('M8_COMMENT_POSTIMAGE_UNVERIFIED_NO_RETRY')
        verify_live(p,auth,writer,confirmation=(operation,key,intent_id))
        _native_confirm(p,operation,key,{'head_sha':p.head_sha,'pr_number':p.pr_number,'comment_id':comment_id},intent_id=intent_id)
        return {'state':'NATIVE_CONFIRMED','effect':effect,'comment_id':comment_id}
    if effect=='pr_labels_add':
        request={'labels':list(p.labels)}
        operation='pr_labels'
        before=writer._get(f'/repos/{p.repository}/issues/{p.pr_number}')
        existing={a['name'] for a in before.get('labels',[]) if isinstance(a,dict) and isinstance(a.get('name'),str)}
        if set(p.labels)<=existing:
            return {'state':'ALREADY_PRESENT_READ_ONLY','labels':list(p.labels)}
        key=_effect_key(p,effect,request)
        intent_id=_native_intent(p,operation,key)
        _approved_write(writer,p,operation,str(p.pr_number),request)
        after=writer._get(f'/repos/{p.repository}/issues/{p.pr_number}')
        found={a['name'] for a in after.get('labels',[]) if isinstance(a,dict) and isinstance(a.get('name'),str)}
        if not set(p.labels)<=found:refuse('M8_LABEL_POSTIMAGE_UNVERIFIED_NO_RETRY')
        verify_live(p,auth,writer,confirmation=(operation,key,intent_id))
        _native_confirm(p,operation,key,{'head_sha':p.head_sha,'pr_number':p.pr_number,
                                         'labels':list(p.labels)},intent_id=intent_id)
        return {'state':'NATIVE_CONFIRMED','effect':effect,'labels':list(p.labels)}
    if effect=='pr_body_set':
        before=writer._get(f'/repos/{p.repository}/pulls/{p.pr_number}')
        old=before.get('body') if isinstance(before,dict) else None
        if old is None:old=''
        if type(old) is not str or _hash(old.encode('utf-8'))!=p.pr_body_before_sha256:
            refuse('M8_PR_BODY_PREIMAGE_DRIFT_NO_WRITE')
        if old==p.pr_body:
            return {'state':'ALREADY_PRESENT_READ_ONLY','effect':effect}
        operation='pr_body_set'
        request={'body':p.pr_body}
        key=_effect_key(p,effect,request)
        intent_id=_native_intent(p,operation,key)
        _approved_write(writer,p,operation,str(p.pr_number),request)
        after=writer._get(f'/repos/{p.repository}/pulls/{p.pr_number}')
        if not isinstance(after,dict) or after.get('body')!=p.pr_body:
            refuse('M8_PR_BODY_POSTIMAGE_UNVERIFIED_NO_RETRY')
        _,_,latest=verify_live(p,auth,writer,confirmation=(operation,key,intent_id))
        if latest.get('body')!=p.pr_body:refuse('M8_PR_BODY_POSTIMAGE_UNVERIFIED_NO_RETRY')
        _native_confirm(p,operation,key,{'head_sha':p.head_sha,'pr_number':p.pr_number,
            'body_sha256':_hash(p.pr_body.encode('utf-8'))},intent_id=intent_id)
        return {'state':'NATIVE_CONFIRMED','effect':effect}
    if effect=='pr_labels_replace':
        before=writer._get(f'/repos/{p.repository}/issues/{p.pr_number}')
        labels=before.get('labels') if isinstance(before,dict) else None
        if not isinstance(labels,list) or any(not isinstance(x,dict) or
                 type(x.get('name')) is not str for x in labels):
            refuse('M8_LABEL_PREIMAGE_UNVERIFIED_NO_WRITE')
        old={x['name'] for x in labels}
        old_hash=_hash(_canonical({'labels':sorted(old)}))
        if len(old)!=len(labels) or old_hash!=p.pr_labels_before_sha256:
            refuse('M8_LABEL_PREIMAGE_DRIFT_NO_WRITE')
        if old==set(p.labels):
            return {'state':'ALREADY_PRESENT_READ_ONLY','effect':effect}
        operation='pr_labels_replace'
        request={'labels':list(p.labels)}
        key=_effect_key(p,effect,request)
        intent_id=_native_intent(p,operation,key)
        _approved_write(writer,p,operation,str(p.pr_number),request)
        after=writer._get(f'/repos/{p.repository}/issues/{p.pr_number}')
        result=after.get('labels') if isinstance(after,dict) else None
        if not isinstance(result,list) or any(not isinstance(x,dict) or
                type(x.get('name')) is not str for x in result):
            refuse('M8_LABEL_POSTIMAGE_UNVERIFIED_NO_RETRY')
        names=[x['name'] for x in result]
        if len(names)!=len(set(names)) or set(names)!=set(p.labels):
            refuse('M8_LABEL_POSTIMAGE_UNVERIFIED_NO_RETRY')
        verify_live(p,auth,writer,confirmation=(operation,key,intent_id))
        _native_confirm(p,operation,key,{'head_sha':p.head_sha,'pr_number':p.pr_number,
            'labels':list(p.labels)},intent_id=intent_id)
        return {'state':'NATIVE_CONFIRMED','effect':effect}
    if effect=='commit_status':
        request={'state':p.status_state,'context':p.status_context,
                 'description':'M8 operator-approved, non-release status'}
        operation='commit_status'
        key=_effect_key(p,effect,request)
        intent_id=_native_intent(p,operation,key)
        response=_approved_write(writer,p,operation,p.head_sha,request)
        status_id=response.get('id') if isinstance(response,dict) else None
        if not _positive(status_id):refuse('M8_STATUS_RECEIPT_UNKNOWN_NO_RETRY')
        check=writer._get(f'/repos/{p.repository}/commits/{p.head_sha}/status')
        matching=[s for s in (check.get('statuses') or []) if isinstance(s,dict) and
                  s.get('id')==status_id and s.get('context')==p.status_context and
                  s.get('state')==p.status_state and s.get('description')==request['description']]
        if len(matching)!=1:refuse('M8_STATUS_POSTIMAGE_UNVERIFIED_NO_RETRY')
        verify_live(p,auth,writer,confirmation=(operation,key,intent_id))
        _native_confirm(p,operation,key,{'head_sha':p.head_sha,'status_id':status_id,'context':p.status_context},intent_id=intent_id)
        return {'state':'NATIVE_CONFIRMED','effect':effect,'status_id':status_id}
    refuse('M8_MAINTENANCE_UNKNOWN_OPERATION')


def reconcile_managed_write(root: Path, effect: str, *, yes: bool, prior_process_finished: bool,
                            auth: str = 'auto', client: Any = None) -> dict[str, Any]:
    """Resolve an existing GA-01 intent by read-only exact-postimage evidence.

    No new intent, remote write, policy renewal or replay is permitted. Expiry
    and revocation block new mutations, not reconciliation of an old receipt.
    The original foreground process must be finished before invoking this.
    """
    if yes is not True or prior_process_finished is not True:
        refuse('M8_RECONCILIATION_REQUIRES_EXPLICIT_ACKNOWLEDGEMENT')
    p = load_policy(root, allow_expired=True)
    pin = wh.current_pin(root)
    if any(getattr(pin, name) != getattr(p, name) for name in
           ('repository','repository_id','installation_id','head_sha','pr_number','session_id')):
        refuse('M8_POLICY_PIN_MISMATCH')
    if effect not in p.effects or effect not in ('pr_body_set','pr_labels_replace'):
        refuse('M8_RECONCILIATION_EFFECT_NOT_APPROVED')
    request = {'body':p.pr_body} if effect == 'pr_body_set' else {'labels':list(p.labels)}
    key = _effect_key(p,effect,request)
    session, reader, remote = verify_live(p,auth,client,confirmation=(effect,key,None))
    intent_id = session['confirmation_intent_id']
    proof = {'head_sha':p.head_sha,'pr_number':p.pr_number}
    if effect == 'pr_body_set':
        if remote.get('body') != p.pr_body:
            refuse('M8_PR_BODY_POSTIMAGE_UNVERIFIED_NO_RETRY')
        proof['body_sha256'] = _hash(p.pr_body.encode('utf-8'))
    else:
        issue = reader._get(f'/repos/{p.repository}/issues/{p.pr_number}')
        labels = issue.get('labels') if isinstance(issue,dict) else None
        if not isinstance(labels,list) or any(not isinstance(x,dict) or
                type(x.get('name')) is not str for x in labels):
            refuse('M8_LABEL_POSTIMAGE_UNVERIFIED_NO_RETRY')
        names = [x['name'] for x in labels]
        if len(names) != len(set(names)) or set(names) != set(p.labels):
            refuse('M8_LABEL_POSTIMAGE_UNVERIFIED_NO_RETRY')
        proof['labels'] = list(p.labels)
    _,_,latest = verify_live(p,auth,reader,confirmation=(effect,key,intent_id))
    if effect == 'pr_body_set' and latest.get('body') != p.pr_body:
        refuse('M8_PR_BODY_POSTIMAGE_UNVERIFIED_NO_RETRY')
    if effect == 'pr_labels_replace':
        issue = reader._get(f'/repos/{p.repository}/issues/{p.pr_number}')
        labels = issue.get('labels') if isinstance(issue,dict) else None
        if not isinstance(labels,list) or any(not isinstance(x,dict) or
                type(x.get('name')) is not str for x in labels):
            refuse('M8_LABEL_POSTIMAGE_UNVERIFIED_NO_RETRY')
        names = [x['name'] for x in labels]
        if len(names) != len(set(names)) or set(names) != set(p.labels):
            refuse('M8_LABEL_POSTIMAGE_UNVERIFIED_NO_RETRY')
    _native_confirm(p,effect,key,proof,intent_id=intent_id)
    return {'state':'NATIVE_RECONCILED','effect':effect,'intent_id':intent_id,
            'remote_write_performed':False,'operation_replayed':False}


def m6_events(repo_root:Path, *, limit:int=20) -> list[dict[str,Any]]:
    """Read-only M6 event projection from M8 private journals; no network/writes.

    An M8 event must belong to the same *currently checked out* source SHA.
    Never expose raw GitHub payloads, comments, keys, headers, or private paths.
    """
    if type(limit) is not int or not 1 <= limit <= 40:
        raise ValueError('M8 live projection limit must be 1..40')
    state_home=Path(os.environ.get('XDG_STATE_HOME') or Path.home()/'.local/state')
    root=state_home/'devtool/m8-webhooks'
    if not root.is_dir() or root.is_symlink():return []
    policy_file=root/'automation-policy.json'
    if not policy_file.is_file() or policy_file.is_symlink():return []
    try:
        info=wh.read(policy_file)
        expected=str(info.get('head_sha') or '')
        if not _SHA.fullmatch(expected):return []
        result=subprocess.run(['git','-C',str(repo_root),'rev-parse','HEAD'],
                              check=False,text=True,capture_output=True,timeout=5)
        if result.returncode or result.stdout.strip()!=expected:return []
        approved=str(info.get('policy_id') or '')
        rows=[]
        revoked=root/'automation-revoked.json'
        if revoked.exists():
            wh.read(revoked)
            rows.append({'domain':'m8','event_id':approved[:64] or 'm8-revoked',
                         'kind':'M8 automation revoked','status':'blocked','timestamp':None,
                         'detail':{'state':'revoked'},'chronology':'immutable approval and revocation'})
        else:
            expiry=_dt(info.get('expires_at'))
            if expiry<=_now():
                rows.append({'domain':'m8','event_id':approved[:64] or 'm8-expired',
                             'kind':'M8 automation policy expired','status':'blocked',
                             'timestamp':None,'detail':{'state':'expired'},
                             'chronology':'immutable approval expiry'})
        base=root/'processed'
        if base.is_dir() and not base.is_symlink() and not base.stat().st_mode & 0o077:
            for path in sorted(base.glob('*.json'))[-limit:]:
                data=wh.read(path)
                uid=str(data.get('delivery') or '')
                if not re.fullmatch(r'[0-9a-f-]{36}',uid):continue
                state=str(data.get('state') or 'unknown')
                rows.append({'domain':'m8','event_id':uid,'kind':'M8 webhook observation',
                             'status':'completed' if state in ('OBSERVED','IGNORED_POLICY') else 'blocked',
                             'timestamp':None,'detail':{'state':state[:30]},
                             'chronology':'signed delivery UUID, durable local observation'})
        return rows[-limit:]
    except (OSError,ValueError,wh.Refusal,subprocess.TimeoutExpired):
        return []


def state(root:Path) -> dict[str,Any]:
    p=load_policy(root,allow_expired=True)
    return {'schema':'devtool.m8.state.v1','policy':p.public(),
            'revoked':(wh.root_at(root)/'automation-revoked.json').exists(),
            'event_count':len(_read_rows(root,'processed')),
            'poll_count':len(_read_rows(root,'poll-observations')),
            'release_qualification':'NOT_PERFORMED'}


def main(argv: list[str] | None = None) -> int:
    ap=argparse.ArgumentParser(description='M8 explicit policy, source-bound polling and PR maintenance')
    ap.add_argument('--state-dir',type=Path,default=Path.home()/'.local/state/devtool/m8-webhooks')
    sub=ap.add_subparsers(dest='command',required=True)
    approve_parser=sub.add_parser('approve');approve_parser.add_argument('--policy',type=Path,required=True)
    approve_parser.add_argument('--auth',choices=('app','gh','auto'),default='auto')
    approve_parser.add_argument('--yes',action='store_true')
    revoke_parser=sub.add_parser('revoke');revoke_parser.add_argument('--yes',action='store_true')
    sub.add_parser('status')
    for command in ('follow','poll'):
        item=sub.add_parser(command);item.add_argument('--request-id',required=True)
        item.add_argument('--auth',choices=('app','gh','auto'),default='auto')
        item.add_argument('--checks',type=int,default=1)
        item.add_argument('--interval',type=int,default=15)
    m=sub.add_parser('maintain');m.add_argument('--effect',choices=sorted(_EFFECTS-{'observe'}),required=True)
    m.add_argument('--auth',choices=('app','gh','auto'),default='auto')
    m.add_argument('--request-id',default='');m.add_argument('--yes',action='store_true')
    recovery=sub.add_parser('reconcile',help='Read-only remote postimage reconciliation of a pending managed PR write')
    recovery.add_argument('--effect',choices=('pr_body_set','pr_labels_replace'),required=True)
    recovery.add_argument('--auth',choices=('app','gh','auto'),default='auto')
    recovery.add_argument('--yes',action='store_true')
    recovery.add_argument('--prior-process-finished',action='store_true')
    args=ap.parse_args(argv)
    os.umask(0o077)
    try:
        if args.command=='approve':
            raw=json.loads(args.policy.read_text(encoding='utf-8'))
            result=approve(args.state_dir,raw,yes=args.yes,auth=args.auth)
        elif args.command=='revoke':result=revoke(args.state_dir,yes=args.yes)
        elif args.command=='status':result=state(args.state_dir)
        elif args.command=='reconcile':
            result=reconcile_managed_write(args.state_dir,args.effect,yes=args.yes,
                    prior_process_finished=args.prior_process_finished,auth=args.auth)
        elif args.command=='maintain':
            result=maintain(args.state_dir,args.effect,yes=args.yes,auth=args.auth,
                            request_id=args.request_id)
        else:
            if not 1<=args.checks<=120 or not 5<=args.interval<=300:refuse('M8_WATCH_BOUNDS')
            for i in range(args.checks):
                result=(process_events(args.state_dir,request_id=args.request_id,auth=args.auth)
                        if args.command=='follow' else poll_once(args.state_dir,request_id=args.request_id,auth=args.auth))
                print(json.dumps(result,sort_keys=True),flush=True)
                if i+1<args.checks: time.sleep(args.interval)
        print(json.dumps(result,sort_keys=True,indent=2),flush=True)
        return 0
    except (M8Refusal,wh.Refusal) as exc:
        print(json.dumps({'state':'REFUSED','reason':str(exc)}),flush=True)
        return 2
    except (OSError,ValueError,KeyError,TypeError) as exc:
        print(json.dumps({'state':'INCONCLUSIVE','reason':type(exc).__name__}),flush=True)
        return 3


if __name__=='__main__':
    sys.exit(main())
