"""Source-bound external credential recovery; never persists credential values.

GitHub repository Actions secrets expose only metadata. In particular, a secret
name does not prove that a fine-grained PAT still selects a recreated repository.
The PAT path needs a user-supplied token and a permission-gated read probe. The
GitHub App path uses the documented, authorized user installation API.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any
import urllib.parse

PAT_SETTINGS = 'https://github.com/settings/personal-access-tokens'
SAFE_ENV = re.compile(r'^[A-Z][A-Z0-9_]{0,100}$')
SAFE_REPO = re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')
SAFE_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,100}$')
HIGH_RISK = re.compile(r'(FEDERATION|(?:^|_)(?:PAT|TOKEN|GITHUB_APP)(?:_|$))',re.I)
# These endpoints require repository-specific authorization. Public contents or
# a normal GET /repos/OWNER/REPO can never be used as proof of PAT access.
AUTHENTICATED_PROBES = {
    'actions-secrets': 'actions/secrets',
    'actions-variables': 'actions/variables',
    'actions-permissions': 'actions/permissions',
}
READABLE_PERMISSIONS = {'read': 1, 'write': 2, 'admin': 3}


def _rr():
    from . import engine as cli
    return cli


def _verify_policy(policy: dict, repo: str) -> dict:
    rr = _rr()
    rr.require(isinstance(policy,dict) and policy.get('schema')==1,
               'credential policy must have schema=1')
    rr.require(policy.get('repository')==repo,
               'credential policy repository does not match the session')
    rr.require(set(policy)=={'schema','repository','credentials'},
               'unknown credential policy fields refused (no embedded secrets)')
    items=policy.get('credentials')
    rr.require(isinstance(items,list) and len(items)<=100,
               'credential policy credentials must be a bounded list')
    names=set()
    clean=[]
    for item in items:
        rr.require(isinstance(item,dict),'invalid credential policy entry')
        name=item.get('name');kind=item.get('kind')
        rr.require(isinstance(name,str) and SAFE_NAME.fullmatch(name),
                   'invalid credential name')
        rr.require(name not in names,'duplicate credential dependency: '+name)
        names.add(name)
        fields=({'name','kind','repositories','token_env','probe','secret_hosts'}
                if kind=='fine_grained_pat' else
                {'name','kind','repositories','installation_id','admin_token_env',
                 'auto_repair','required_permissions'})
        rr.require(not (set(item)-fields),
                   'unknown credential properties refused (never embed token values): '+name)
        rr.require(kind in ('fine_grained_pat','github_app'),
                   'unsupported credential kind for '+name)
        repositories=item.get('repositories')
        rr.require(isinstance(repositories,list) and len(repositories)>0 and
                   len(repositories)<=25 and len(set(repositories))==len(repositories) and
                   all(isinstance(r,str) and SAFE_REPO.fullmatch(r) for r in repositories),
                   'invalid credential dependency repositories: '+name)
        rr.require(repo in repositories,'credential dependency must include recreated repo: '+name)
        if kind=='fine_grained_pat':
            var=item.get('token_env')
            rr.require(isinstance(var,str) and SAFE_ENV.fullmatch(var) and
                       var not in ('GH_TOKEN','GITHUB_TOKEN'),
                       'PAT token_env must name a dedicated environment variable: '+name)
            probe=item.get('probe')
            rr.require(probe is None or probe in AUTHENTICATED_PROBES,
                       'PAT probe must be a recognized permission-requiring endpoint')
            hosts=item.get('secret_hosts',[])
            rr.require(isinstance(hosts,list) and len(hosts)<=25 and
                       all(isinstance(h,str) and SAFE_REPO.fullmatch(h) for h in hosts),
                       'invalid secret_hosts for '+name)
            rr.require(len(set(hosts))==len(hosts),'duplicate secret host for '+name)
        else:
            installation_id=item.get('installation_id')
            rr.require(type(installation_id) is int and installation_id>0,
                       'GitHub App requires a positive installation_id')
            var=item.get('admin_token_env')
            rr.require(isinstance(var,str) and SAFE_ENV.fullmatch(var) and
                       var not in ('GH_TOKEN','GITHUB_TOKEN'),
                       'GitHub App admin_token_env must name a dedicated environment variable')
            permissions=item.get('required_permissions',{})
            rr.require(isinstance(permissions,dict) and len(permissions)<=20 and
                       all(isinstance(k,str) and re.fullmatch(r'[a-z_]{2,40}',k) and
                           v in READABLE_PERMISSIONS for k,v in permissions.items()),
                       'invalid GitHub App required_permissions for '+name)
            rr.require(type(item.get('auto_repair',False)) is bool,
                       'GitHub App auto_repair must be a boolean')
        clean.append(item)
    return {'schema':1,'repository':repo,'credentials':clean}


def load_policy(path: Path, repo: str) -> dict:
    rr=_rr()
    return _verify_policy(rr.read_json(path),repo)


def secret_candidates(config:dict) -> list[str]:
    """Conservative indicators. Unknown external repos must be explicitly declared."""
    result=set()
    for key in ('actions','dependabot','codespaces'):
        for s in (config.get('secrets') or {}).get(key,[]):
            n=s.get('name','')
            if HIGH_RISK.search(n) and n not in ('GITHUB_TOKEN','GH_TOKEN'):result.add(n)
    for env in (config.get('environments') or {}).values():
        for s in env.get('secrets',[]) or []:
            n=s.get('name','')
            if HIGH_RISK.search(n) and n not in ('GITHUB_TOKEN','GH_TOKEN'):result.add(n)
    return sorted(result)


def _token_api(endpoint: str, env_name: str, *, method='GET') -> Any:
    """Token travels only as child process environment; never in argv or receipts.

    Errors intentionally do not include subprocess output (it may contain
    Authorization headers, debug traces or untrusted response content).
    """
    rr=_rr()
    rr.require(not endpoint.startswith(('https:','http:')) and not endpoint.startswith('/') and
               '\n' not in endpoint, 'unsafe credential probe endpoint')
    token=os.environ.get(env_name)
    rr.require(bool(token and token.strip()),
               'credential source unavailable: export '+env_name+' securely')
    args=['gh','api',endpoint]
    if method!='GET':args.extend(['-X',method])
    env=os.environ.copy()
    env['GH_TOKEN']=token
    env.pop('GITHUB_TOKEN',None)
    env.update({'GH_HOST':'github.com','GH_PROMPT_DISABLED':'1','GIT_TERMINAL_PROMPT':'0',
                'DEVTOOL_EXECUTION_PRESENTATION':'plain','DEVTOOL_JOB_UI':'plain',
                'DEVTOOL_TEXTUAL_APPLY_HOST':'0','DEVTOOL_TEXTUAL_EXECUTION_HOST':'0'})
    try:
        p=subprocess.run(args,capture_output=True,text=True,timeout=70,env=env)
    except (OSError,subprocess.TimeoutExpired) as exc:
        raise rr.Refused('credential API transport failed; no credential value logged') from exc
    rr.require(p.returncode==0,'credential API refused '+method+' '+endpoint+
               ' (invalid token, missing scope, repository selection or permissions; '
               'response redacted)')
    if not (p.stdout or '').strip():return {}
    try:return json.loads(p.stdout)
    except json.JSONDecodeError as exc:
        raise rr.Refused('credential API returned invalid JSON') from exc


def _pages(endpoint: str, env_name: str, *, key: str) -> list:
    rr=_rr()
    rows=[]
    for page in range(1,101):
        join='&' if '?' in endpoint else '?'
        obj=_token_api(endpoint+f'{join}per_page=100&page={page}',env_name)
        rr.require(isinstance(obj,dict) and isinstance(obj.get(key),list),
                   'unexpected credential API pagination schema')
        rows.extend(obj[key])
        if len(obj[key])<100:
            count=obj.get('total_count')
            rr.require(count is None or len(rows)>=count,
                       'credential API inventory pagination incomplete')
            return rows
    raise rr.Refused('credential API pagination exceeded safe bound')


def _secret_metadata(item:dict) -> list[dict]:
    rr=_rr()
    rows=[]
    for host in item.get('secret_hosts',[]):
        path=f'repos/{host}/actions/secrets/{urllib.parse.quote(item["name"],safe="")}'
        value=rr.api(path,missing_ok=True)
        rows.append({'repository':host,'name':item['name'],
                     'status':'present-unverified' if isinstance(value,dict) else 'missing'})
    return rows


def _set_actions_secret(host:str, name:str, token:str)->None:
    """Provision a known credential via gh stdin, with no token argv/log/receipt."""
    rr=_rr()
    info=rr.repo_info(host)
    rr.require(info is not None and (info.get('permissions') or {}).get('admin'),
               'repository admin required to reprovision Actions secret: '+host)
    env=os.environ.copy()
    env.update({'GH_PROMPT_DISABLED':'1','GIT_TERMINAL_PROMPT':'0',
                'DEVTOOL_EXECUTION_PRESENTATION':'plain','DEVTOOL_JOB_UI':'plain',
                'DEVTOOL_TEXTUAL_APPLY_HOST':'0','DEVTOOL_TEXTUAL_EXECUTION_HOST':'0'})
    try:
        p=subprocess.run(['gh','secret','set',name,'-R',host],input=token,
                         capture_output=True,text=True,timeout=80,env=env)
    except (OSError,subprocess.TimeoutExpired) as exc:
        raise rr.Refused('Actions secret reprovision transport failed; response redacted') from exc
    rr.require(p.returncode==0,'Actions secret reprovision failed; response redacted')
    detail=rr.api(f'repos/{host}/actions/secrets/{urllib.parse.quote(name,safe="")}')
    rr.require(isinstance(detail,dict) and detail.get('name')==name,
               'provisioned Actions secret could not be inspected by name')


def _pat_verify(item:dict, *, source: str, provision=False,
                session=None, journal=None) -> dict:
    rr=_rr()
    name=item['name']
    result={'name':name,'kind':'fine_grained_pat','source':source,'status':'unverified',
            'secret_metadata':[],'repositories':[],'guided_repair':{
                'url':PAT_SETTINGS,'steps':'Edit token repository selection to include each current '
                'repository; if expired/revoked create a new PAT and reprovision consuming secrets. '
                'Then provide a protected API probe or source-bound integration test.'}}
    try:
        result['secret_metadata']=_secret_metadata(item)
    except rr.Refused:
        result['secret_metadata']=[{'status':'inaccessible-unverified'}]
    if not os.environ.get(item['token_env']):
        result['reason']='value-unavailable: GitHub cannot reveal stored secret values'
        return result
    try:
        who=_token_api('user',item['token_env'])
        rr.require(isinstance(who,dict) and bool(who.get('login')),
                   'PAT authentication response lacks an authenticated identity')
        result['authenticated_identity']=who['login']
        if not item.get('probe'):
            result['reason']='authenticated-but-repository-authorization-unproved'
            return result
        for repo in item['repositories']:
            path='repos/'+repo+'/'+AUTHENTICATED_PROBES[item['probe']]
            payload=_token_api(path,item['token_env'])
            rr.require(isinstance(payload,dict),
                       'PAT permission-gated endpoint returned unexpected data')
            result['repositories'].append({'repository':repo,'current_id':rr.repo_info(repo)['id'],
                                          'probe':item['probe'],'status':'verified'})
        if item.get('secret_hosts'):
            if provision:
                rr.require(session is not None and journal is not None,
                           'PAT provisioning requires a durable recovery session')
                for host in item['secret_hosts']:
                    # A lost response leaves an unresolved intent. A secret
                    # value cannot be read back, so a later token cannot be
                    # assumed to be the exact previously submitted value.
                    prior=journal.get('pat_provision_intents',{}).get(name+'@'+host)
                    rr.require(not prior or prior.get('phase')=='verified',
                               'PAT provision response uncertain; inspect and reconcile outside automatic replay')
                    state={'credential':name,'repository':host,
                           'replacement_id':journal.get('created_new_id'), 'phase':'intent'}
                    journal.setdefault('pat_provision_intents',{})[name+'@'+host]=state
                    rr.save_state(session,journal)
                    _set_actions_secret(host,name,os.environ[item['token_env']])
                    state['phase']='verified'
                    rr.save_state(session,journal)
                result['status']='verified'
                result['reason']='token authorization proved and supplied token reprovisioned to all declared secret hosts'
            else:
                result['status']='unverified'
                result['reason']='PAT authorization proved, but stored Actions secret value cannot be read or equated to this token'
        else:
            result['status']='verified'
            result['reason']='standalone token authorization proved; no deployed secret hosts declared'
    except (rr.Refused,KeyError,TypeError,ValueError) as exc:
        result['status']='unverified'
        result['reason']=('PAT proof failed or incomplete (invalid/expired token, wrong selection, '
                          'missing endpoint permission, or repository identity unavailable)')
        result.pop('authenticated_identity',None)
        # Avoid accidentally including privileged API responses in any receipt.
    return result


def _app_inventory(item:dict, *, expected_repo: str, expected_id: int) -> dict:
    rr=_rr()
    admin=item['admin_token_env']
    installations=_pages('user/installations',admin,key='installations')
    matching=[x for x in installations if x.get('id')==item['installation_id']]
    rr.require(len(matching)==1,'GitHub App installation unavailable or inaccessible')
    install=matching[0]
    rr.require((install.get('account') or {}).get('login','').lower()==expected_repo.split('/')[0].lower(),
               'GitHub App installation account mismatch')
    rr.require(install.get('repository_selection') in ('all','selected'),
               'GitHub App installation selection unavailable')
    rr.require(install.get('suspended_at') is None,'GitHub App installation is suspended')
    for permission,needed in item.get('required_permissions',{}).items():
        value=(install.get('permissions') or {}).get(permission)
        rr.require(READABLE_PERMISSIONS.get(value,0)>=READABLE_PERMISSIONS[needed],
                   'GitHub App installation lacks required permission '+permission)
    if install['repository_selection']=='all':
        return {'selection':'all','current_repo_selected':True,'selected_ids':[]}
    rows=_pages(f'user/installations/{item["installation_id"]}/repositories',admin,
                key='repositories')
    selected={x['id'] for x in rows if isinstance(x,dict) and type(x.get('id')) is int}
    return {'selection':'selected','current_repo_selected':expected_id in selected,
            'selected_ids':sorted(selected)}


def _app_verify(item:dict, repo:str, *, apply=False, journal=None, session=None) -> dict:
    rr=_rr()
    result={'name':item['name'],'kind':'github_app','status':'unverified',
            'installation_id':item['installation_id'],'repositories':[]}
    if not os.environ.get(item['admin_token_env']):
        result['reason']='missing GitHub App installation-management token source'
        return result
    for dependent in item['repositories']:
        current=rr.repo_info(dependent)
        if current is None:
            result['reason']='dependent repository unavailable: '+dependent
            return result
        if dependent==repo and apply and not (current.get('permissions') or {}).get('admin'):
            result['reason']='recreated repository admin permission required for App installation repair'
            return result
        old_intent=(journal or {}).get('app_intents',{}).get(item['name'])
        if old_intent and old_intent['repository']==dependent:
            rr.require(int(old_intent['repository_id'])==int(current['id']) and
                       old_intent['installation_id']==item['installation_id'],
                       'GitHub App repair journal has repository or installation ID drift')
        elif old_intent and dependent==repo:
            raise rr.Refused('GitHub App repair journal has repository ID drift')
        try:
            inventory=_app_inventory(item,expected_repo=dependent,expected_id=current['id'])
        except (rr.Refused,KeyError,TypeError,ValueError):
            result['reason']='installation access or required permissions cannot be verified'
            return result
        if not inventory['current_repo_selected']:
            if not (apply and item.get('auto_repair',False) and dependent==repo):
                result['repositories'].append({'repository':dependent,'id':current['id'],
                                               'status':'missing-installation-access'})
                result['reason']='authorized installation must select this repository'
                return result
            # Durable intent BEFORE the PUT. On lost API response, the next run
            # observes selection and completes without issuing a second PUT.
            if session is not None:
                intent={'credential':item['name'],'installation_id':item['installation_id'],
                        'repository':dependent,'repository_id':current['id'],
                        'state':'intent'}
                journal.setdefault('app_intents',{})[item['name']]=intent
                rr.save_state(session,journal)
            _token_api(f'user/installations/{item["installation_id"]}/repositories/{current["id"]}',
                       item['admin_token_env'],method='PUT')
            verified=_app_inventory(item,expected_repo=dependent,expected_id=current['id'])
            rr.require(verified['current_repo_selected'],
                       'GitHub App repository grant not observed after update')
            if session is not None:
                journal['app_intents'][item['name']]['state']='verified'
                rr.save_state(session,journal)
        if old_intent and old_intent['repository']==dependent and inventory['current_repo_selected'] and session is not None:
            # The prior PUT may have succeeded before its response was lost.
            journal['app_intents'][item['name']]['state']='verified'
            rr.save_state(session,journal)
        result['repositories'].append({'repository':dependent,'id':current['id'],
                                       'status':'verified'})
    result['status']='verified'
    result['reason']='all repositories selected with required installation permissions'
    return result


def preflight(session: Path, state:dict, policy_path:Path|None, *, accept_manual=False, record=True) -> dict:
    """Mandatory feasibility gate before original repository deletion.

    A PAT tied to selected repository identities cannot be fixed automatically
    after recreation. The user must knowingly accept that manual follow-up.
    """
    rr=_rr()
    config=rr.read_json(session/'primary'/'configuration.json')
    source_audit_path=session/'primary'/'dependency-audit.json'
    source_audit=rr.read_json(source_audit_path) if source_audit_path.is_file() else {}
    candidates=sorted(set(secret_candidates(config)) |
                      {n for n in source_audit.get('referenced_secret_names',[])
                       if HIGH_RISK.search(n) and n not in ('GITHUB_TOKEN','GH_TOKEN')})
    bound=session/'credential-policy.json'
    if not record:
        rr.require(policy_path is None and bound.is_file() and not bound.is_symlink(),
                   'credential recheck requires the existing bound policy')
    if state.get('credential_policy_sha256'):
        rr.require(bound.is_file() and not bound.is_symlink() and
                   rr.sha256(bound)==state['credential_policy_sha256'],
                   'bound credential policy changed since preparation')
    if policy_path is not None:
        policy=load_policy(policy_path,state['repo'])
        if bound.exists():
            rr.require(rr.read_json(bound)==policy,'stored credential policy is immutable for this session')
        else:rr.write_json(bound,policy)
    elif bound.exists():
        policy=load_policy(bound,state['repo'])
    else:policy={'schema':1,'repository':state['repo'],'credentials':[]}
    managed={x['name'] for x in policy['credentials']}
    unknown=sorted(set(candidates)-managed)
    entries=[]
    needs_manual=[]
    for item in policy['credentials']:
        if item['kind']=='fine_grained_pat':
            result=_pat_verify(item,source='original')
            entries.append(result)
            needs_manual.append(item['name']+': PAT selection cannot be expanded automatically after recreation')
            if result['status']!='verified':
                needs_manual.append(item['name']+': pre-deletion PAT authorization unverified')
        else:
            result=_app_verify(item,state['repo'],apply=False)
            entries.append(result)
            if result['status']!='verified':
                needs_manual.append(item['name']+': GitHub App installation recovery not proven feasible')
    if unknown:needs_manual.append('unclassified high-risk secrets: '+', '.join(unknown))
    blocked=bool(needs_manual and not accept_manual)
    result={'schema':1,'repo':state['repo'],'candidates':candidates,
            'policy_sha256':rr.sha256(bound) if bound.is_file() else None,
            'entries':entries,'unclassified':unknown,'manual_obligations':needs_manual,
            'status':'blocked' if blocked else 'prepared-with-credential-obligations' if needs_manual
                     else 'prepared-no-known-credential-dependencies'}
    if record:
        rr.write_json(session/'credential-readiness.json',result)
        state['credential_policy_sha256']=result['policy_sha256']
        state['credential_manual_risk_accepted']=bool(accept_manual)
        state['credential_preflight']=result['status']
        rr.save_state(session,state)
    rr.require(not blocked,'credential pre-deletion feasibility BLOCKED; see credential-readiness.json. '
               'Use --credentials-policy and resolve dependencies, or explicitly '
               '--accept-credential-manual-risk (never marks credentials operational).')
    return result


def require_declaration(session: Path, state: dict, policy_path: Path | None) -> dict:
    """An empty declaration is explicit; absence cannot imply no inbound dependencies."""
    from . import external_dependencies as external
    rr = _rr()
    bound = session / 'credential-policy.json'
    rr.require(policy_path is not None or bound.is_file(),
               'explicit --credentials-policy required (declare even an empty credentials list)')
    policy = load_policy(policy_path if policy_path is not None else bound, state['repo'])
    if bound.exists():
        rr.require(not bound.is_symlink() and rr.read_json(bound) == policy,
                   'stored credential policy is immutable for this session')
    external.validate_policy(policy)
    return policy


def _admin_observation(repo, expected_id):
    """Same intended REST administrator and actual gh lifecycle identity, read-only.

    This is current-repository authorization, not proof of future repository
    selection or deletion/creation scope.  Never log a token or provider body.
    """
    rr = _rr()
    admin_env = 'DEVTOOL_RECOVERY_ADMIN_TOKEN'
    actor = _token_api('user', admin_env)
    admin = _token_api('repos/' + repo, admin_env)
    try:
        env = os.environ.copy()
        env.update({'GH_HOST':'github.com', 'GH_PROMPT_DISABLED':'1', 'GIT_TERMINAL_PROMPT':'0'})
        def read_cli(endpoint):
            p = subprocess.run(['gh','api','--hostname','github.com',endpoint],
                               env=env, capture_output=True, text=True, timeout=70)
            rr.require(p.returncode == 0, 'GitHub lifecycle CLI identity probe failed (redacted)')
            return json.loads(p.stdout)
        cli_actor = read_cli('user')
        cli_admin = read_cli('repos/' + repo)
    except (OSError, ValueError, subprocess.SubprocessError):
        raise rr.Refused('GitHub lifecycle CLI identity probe unavailable (redacted)') from None
    rr.require(isinstance(actor,dict) and isinstance(cli_actor,dict),
               'Recovery administrator account evidence malformed')
    login = actor.get('login')
    rr.require(isinstance(login, str) and bool(login) and cli_actor.get('login') == login,
               'Recovery REST and lifecycle CLI authenticated identities differ')
    for row in (admin, cli_admin):
        rr.require(isinstance(row, dict) and row.get('full_name') == repo and
                   row.get('id') == expected_id and (row.get('permissions') or {}).get('admin') is True,
                   'Current exact-repository administrator access not independently proved')
    return {'repository':repo, 'repository_id':expected_id, 'login':login, 'admin':True}


def seal_preparation(session: Path, state: dict, original_id: int) -> None:
    """Anchor the successful immutable policy, readiness, external inventory and actor."""
    rr = _rr()
    from . import external_dependencies as external
    policy = require_declaration(session, state, None)
    bound = session / 'credential-policy.json'
    ready = session / 'credential-readiness.json'
    original = rr.read_json(ready)
    digest = rr.sha256(bound)
    rr.require(type(original_id) is int and original_id > 0 and
               state.get('credential_policy_sha256') == digest == original.get('policy_sha256') and
               original.get('repo') == state['repo'] and
               original.get('status') in ('prepared-with-credential-obligations','prepared-no-known-credential-dependencies'),
               'credential readiness not prepared or policy identity mismatch')
    observation = external.observe(policy, expected_target_id=original_id)
    admin = _admin_observation(state['repo'], original_id)
    state['credential_readiness_sha256'] = rr.sha256(ready)
    state['external_credential_inventory'] = observation
    state['credential_deletion_authority'] = admin
    rr.save_state(session, state)


def verify_preparation_anchor(session: Path, state: dict):
    rr = _rr()
    from . import external_dependencies as external
    bound = session / 'credential-policy.json'
    ready = session / 'credential-readiness.json'
    expected = state.get('credential_policy_sha256')
    readiness = state.get('credential_readiness_sha256')
    rr.require(isinstance(expected, str) and re.fullmatch(r'[0-9a-f]{64}', expected) and
               isinstance(readiness, str) and re.fullmatch(r'[0-9a-f]{64}', readiness) and
               bound.is_file() and not bound.is_symlink() and ready.is_file() and not ready.is_symlink() and
               rr.sha256(bound) == expected and rr.sha256(ready) == readiness,
               'CREDENTIAL_PREPARATION_ANCHOR_REQUIRED: legacy or drifted prepared session; prepare a new verified session before deletion')
    policy = load_policy(bound, state['repo'])
    original = rr.read_json(ready)
    rr.require(original.get('repo') == state['repo'] and original.get('policy_sha256') == expected and
               original.get('status') == state.get('credential_preflight') and
               original.get('status') in ('prepared-with-credential-obligations','prepared-no-known-credential-dependencies') and
               isinstance(state.get('external_credential_inventory'), dict) and
               isinstance(state.get('credential_deletion_authority'), dict),
               'Credential preparation receipt does not match journal')
    external.validate_policy(policy)
    return policy, original


def recheck_before_delete(session: Path, state: dict):
    """Independently recheck without replacing history, waivers or policy.

    Both originally waived and verified dependencies must retain exactly the
    approved readiness evidence; a permission loss or a new obligation blocks.
    This must be called immediately before each possible DELETE, including a
    resumed delete-intent while the original repository still exists.
    """
    rr = _rr()
    from . import external_dependencies as external
    policy, original = verify_preparation_anchor(session, state)
    current = preflight(session, state, None,
                       accept_manual=state.get('credential_manual_risk_accepted') is True,
                       record=False)
    rr.require(current == original,
               'CREDENTIAL_READINESS_DRIFT: authorization, identities or acknowledged obligations changed')
    actual = external.observe(policy, expected_target_id=state['original_repo_id'],
                              prior=state['external_credential_inventory'])
    rr.require(actual == state['external_credential_inventory'],
               'EXTERNAL_CREDENTIAL_INVENTORY_DRIFT: prepare a new session')
    admin = _admin_observation(state['repo'], state['original_repo_id'])
    rr.require(admin == state['credential_deletion_authority'],
               'RECOVERY_ADMIN_IDENTITY_DRIFT')
    # Preserve the original anchored readiness; store a separate recheck receipt.
    report = {'schema':1, 'repo':state['repo'], 'original_repository_id':state['original_repo_id'],
              'policy_sha256':state['credential_policy_sha256'],
              'readiness_sha256':state['credential_readiness_sha256'],
              'observed_at':rr.now(), 'status':'CURRENT_FEASIBILITY_REVERIFIED',
              'future_replacement_access_verified':False,
              'external_dependency_inventory':actual, 'admin':admin}
    rr.write_json(session/'credential-predelete-recheck.json', report)
    rr.log('Credential readiness, dependency identities and administrator reverified before deletion', style='green')
    return report


def post_restore(session:Path, state:dict, *, apply_apps=False, apply_pats=False,
                 record=True) -> dict:
    """Mandatory post-recreation acceptance: unknown != verified.

    Assumes the caller already holds the session lock for apply_apps=True.
    """
    rr=_rr()
    bound=session/'credential-policy.json'
    expected=state.get('credential_policy_sha256')
    if expected or bound.exists():
        rr.require(isinstance(expected,str) and bound.is_file() and not bound.is_symlink() and rr.sha256(bound)==expected,
                   'credential policy provenance has changed since preflight or is not anchored')
    policy=load_policy(bound,state['repo']) if bound.exists() else {
        'schema':1,'repository':state['repo'],'credentials':[]}
    from . import external_dependencies as external
    if apply_apps or apply_pats:
        external.validate_policy(policy)
        # Explicit writes cannot be redirected to a stale or wrong secret host.
        rr.require(type(state.get('created_new_id')) is int,'replacement repository identity missing')
        external.observe(policy, expected_target_id=state.get('created_new_id'),
                         prior=state.get('external_credential_inventory'), probe_token=True)
    rows=[]
    for item in policy['credentials']:
        if item['kind']=='fine_grained_pat':
            rows.append(_pat_verify(item,source='replacement',provision=apply_pats,
                                    session=session if apply_pats else None,
                                    journal=state if apply_pats else None))
        else:rows.append(_app_verify(item,state['repo'],apply=apply_apps,
                                     journal=state if apply_apps else None,
                                     session=session if apply_apps else None))
    config=rr.read_json(session/'primary'/'configuration.json')
    source_audit_path=session/'primary'/'dependency-audit.json'
    source_audit=rr.read_json(source_audit_path) if source_audit_path.is_file() else {}
    candidates=set(secret_candidates(config)) | {
        n for n in source_audit.get('referenced_secret_names',[]) if HIGH_RISK.search(n) and n not in ('GITHUB_TOKEN','GH_TOKEN')}
    unknown=sorted(candidates - {x['name'] for x in policy['credentials']})
    federation = external.qualify(session, state, policy)
    external_inventory_verified=True
    if policy['credentials'] and not external.applies(state['repo']):
        try:
            rr.require(type(state.get('created_new_id')) is int,'replacement repository identity missing')
            external.observe(policy, expected_target_id=state.get('created_new_id'),
                             prior=state.get('external_credential_inventory'), probe_token=False)
        except (rr.Refused, OSError, KeyError, ValueError, TypeError):
            external_inventory_verified=False
    if external.applies(state['repo']):
        if federation.get('qualified') is True:
            # Actual fresh hosted evidence independently proves the deployed
            # secret worked; the local supplied PAT is separately probed.
            for row in rows:
                if row['name'] == external.SECRET and row['kind'] == 'fine_grained_pat':
                    row['status'] = 'verified'
                    row['reason'] = 'current protected PAT proof and independently verified hosted federation receipt'
        else:
            if not any(x['name'] == external.SECRET for x in policy['credentials']):
                unknown = sorted(set(unknown) | {external.SECRET})
    verified=(external_inventory_verified and not unknown and all(x['status']=='verified' for x in rows) and
              (not external.applies(state['repo']) or federation.get('qualified') is True))
    result={'schema':1,'repo':state['repo'],'replacement_id':state.get('created_new_id'),
            'federation':federation,
            'external_inventory_verified':external_inventory_verified,
            'policy_sha256':expected,'credential_results':rows,'unclassified':unknown,
            'status':'verified' if verified else 'CREDENTIAL_ACCESS_UNVERIFIED',
            'secret_presence_is_not_permission_proof':True}
    if record:
        rr.write_json(session/'credential-acceptance.json',result)
        state['credential_acceptance_sha256']=rr.sha256(session/'credential-acceptance.json')
        state['credential_recovery_status']=result['status']
        rr.save_state(session,state)
        report_path=session/'report.json'
        if report_path.is_file():
            report=rr.read_json(report_path)
            report['credential_recovery']=result
            report['fully_operationally_verified']=False
            if not verified:
                report['recovery_classification']='RESTORED_WITH_ACTION_REQUIRED'
                report['manual_recovery_requirements']=sorted(set(
                    report.get('manual_recovery_requirements',[])+[
                    'credential authorization unverified; inspect credential-acceptance.json']))
            rr.write_json(report_path,report)
    return result


def status_summary(result:dict)->str:
    parts=[f'{x["name"]}: {x["status"]}' for x in result.get('credential_results',result.get('entries',[]))]
    if result.get('unclassified'):parts.append('unclassified: '+', '.join(result['unclassified']))
    return result.get('status','unknown')+'; '+('; '.join(parts) or 'no declared credential dependencies')


def bind_post_restore_policy(session:Path, state:dict, policy_path:Path)->dict:
    """Attach a missing policy to a legacy session without rewriting backup evidence.

    A previously anchored policy can only be re-supplied byte-identically, and
    this action never changes the original session's readiness verdict.
    """
    rr=_rr()
    value=load_policy(policy_path,state['repo'])
    from . import external_dependencies as external
    # A legacy completed MMRL session may be bound to the actual host policy,
    # but a wrong-host dependency can never be introduced by this command.
    external.validate_policy(value)
    dest=session/'credential-policy.json'
    if dest.exists():
        rr.require(rr.read_json(dest)==value,'cannot replace a bound credential policy')
    else:
        rr.write_json(dest,value)
    digest=rr.sha256(dest)
    anchored=state.get('credential_policy_sha256')
    rr.require(not anchored or anchored==digest,
               'credential policy digest mismatch with pre-deletion journal')
    state['credential_policy_sha256']=digest
    rr.save_state(session,state)
    return value
