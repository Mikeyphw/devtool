#!/usr/bin/env python3
"""RepoRebuild: safety-first GitHub repo backup, recreate and resume (Python 3.10+).

No overlays, Gradle, clipboard, device access, force pushes, or PR merges.
Actions history and its artifact ZIPs are archived locally, NEVER forged on GitHub.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from . import safety

from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, BarColumn, TextColumn, DownloadColumn, TransferSpeedColumn

from . import __version__
VERSION = __version__
CONSOLE = Console()
REPO_RE = re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')
DONE = 'done'
DEFAULT_ROOT = Path.home()/'.local'/'share'/'devtool'/'recovery'
DEFAULT_MAX_ARTIFACT_MB = 2048
RISK_NOTE = ('GitHub cannot recreate original PR/issue numbers, authors, reviews, stars, '+
 'check runs, Actions run IDs, workflow logs or historical Actions artifact IDs. '+
 'Secret values require an independent encrypted vault. External systems, '+
 'Actions history, nonportable integrations and repository identities require '+
 'explicit recovery/validation. No deletion without a documented readiness gate.')

class Refused(RuntimeError):
    pass

def require(ok, reason):
    if not ok:
        raise Refused(reason)

def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()

def log(message, *, style='cyan'):
    CONSOLE.print(message, style=style)

def sha256(path: Path):
    h=hashlib.sha256()
    with path.open('rb') as src:
        for chunk in iter(lambda:src.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()

def json_bytes(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2)+'\n').encode()

def atomic_bytes(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    require(not path.is_symlink(), f'symlink refused: {path}')
    fd, name=tempfile.mkstemp(dir=path.parent, prefix='.write-')
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,'wb') as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        os.replace(name,path)
        dirfd=os.open(path.parent, os.O_RDONLY)
        try:os.fsync(dirfd)
        finally:os.close(dirfd)
    finally:
        if os.path.exists(name):os.unlink(name)

def write_json(path: Path, obj):
    atomic_bytes(path, json_bytes(obj))

def read_json(path: Path):
    require(path.is_file() and not path.is_symlink(), f'missing/unsafe JSON {path}')
    return json.loads(path.read_text())

def safe_root(path:Path):
    expanded=path.expanduser()
    require(not expanded.is_symlink(),'symlink session root refused')
    path=expanded.resolve(strict=False)
    require(str(path) not in ('/','.'), 'unsafe root')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    require(path.is_dir() and not path.is_symlink(), 'unsafe root directory')
    require(path.stat().st_uid==os.getuid(),
            'destination not owned by current user (Android shared storage such as '
            '/sdcard may not support POSIX ownership): '+str(path))
    require(path.stat().st_mode & 0o022==0,
            'destination is group/world writable: '+str(path))
    os.chmod(path,0o700)
    return path

def run(argv, *, cwd=None, timeout=180, input_data=None, check=True, capture=True):
    args=[str(a) for a in argv]
    env=os.environ.copy()
    env.update({'GH_HOST':'github.com','GH_PROMPT_DISABLED':'1','GIT_TERMINAL_PROMPT':'0',
                'DEVTOOL_EXECUTION_PRESENTATION':'plain','DEVTOOL_JOB_UI':'plain',
                'DEVTOOL_TEXTUAL_APPLY_HOST':'0','DEVTOOL_TEXTUAL_EXECUTION_HOST':'0'})
    try:
        p=subprocess.run(args,cwd=cwd,input=input_data,capture_output=capture,
                         text=True if capture or input_data is not None else False,
                         timeout=timeout,env=env)
    except subprocess.TimeoutExpired as e:
        raise Refused(f'timed out: {args[:3]}') from e
    if check and p.returncode:
        detail=((p.stderr or p.stdout or '') if capture else '')[-950:]
        raise Refused(f'command failed ({p.returncode}): {args[:3]} {detail}')
    return p

def api(endpoint:str, *, method='GET', payload=None, missing_ok=False):
    """Use Devtool's canonical credential selection and bounded HTTP transport."""
    from .provider import RecoveryTransportError, request_json
    try:
        return request_json(endpoint, method=method, payload=payload, missing_ok=missing_ok)
    except RecoveryTransportError as exc:
        raise Refused(str(exc)) from None

def collect(endpoint:str, *, key=None, limit_pages=1000):
    values=[]
    count=None
    for page in range(1,limit_pages+1):
        join='&' if '?' in endpoint else '?'
        data=api(endpoint+f'{join}per_page=100&page={page}')
        arr=data.get(key) if key else data
        require(isinstance(arr,list), f'paginated response not list: {endpoint}')
        if isinstance(data,dict) and count is None:count=data.get('total_count')
        values+=arr
        if len(arr)<100:
            require(count is None or len(values)>=count,
                    f'API pagination incomplete ({len(values)} of {count}) {endpoint}')
            return values
    raise Refused(f'pagination limit reached: {endpoint}')

def repo_info(repo):
    return api('repos/'+repo,missing_ok=True)

def remote_url(repo):
    proto=run(['gh','config','get','git_protocol','-h','github.com'],check=False)
    return (f'git@github.com:{repo}.git' if proto.returncode==0 and proto.stdout.strip()=='ssh'
            else f'https://github.com/{repo}.git')

def refs_from_git(gitdir:Path):
    s=run(['git','--git-dir',gitdir,'for-each-ref',
           '--format=%(refname) %(objectname)','refs/heads','refs/tags'],timeout=300).stdout
    return dict(line.split(' ',1) for line in s.splitlines() if line)

def live_refs(repo):
    s=run(['git','ls-remote','--refs','--heads','--tags',remote_url(repo)],timeout=240).stdout
    return dict(line.split('\t',1)[::-1] for line in s.splitlines())

def archive_paths(root:Path):
    result={}
    for p in sorted(root.rglob('*')):
        require(not p.is_symlink(), f'backup has symlink: {p}')
        if p.is_file() and p.relative_to(root).as_posix() not in ('MANIFEST.json','VERIFIED'):
            result[p.relative_to(root).as_posix()]={'bytes':p.stat().st_size,'sha256':sha256(p)}
    return result

def zip_ok(path):
    # ZIPs are evidence, never installed or extracted. Keep CRC verification bounded.
    with zipfile.ZipFile(path) as z:
        members=z.infolist()
        names=[info.filename for info in members]
        require(len(names)==len(set(names)),f'duplicate ZIP names: {path}')
        total=0
        for info in members:
            name=info.filename
            member=PurePosixPath(name)
            mode=(info.external_attr >> 16) & 0o170000
            require(not member.is_absolute() and '..' not in member.parts and
                    '\\' not in name and not name.startswith('/') and
                    not re.match(r'^[A-Za-z]:',name) and '\x00' not in name,
                    f'unsafe ZIP member {name}')
            require(mode!=0o120000, f'ZIP symlink refused: {name}')
            require(info.file_size<=8*1024**3, f'ZIP member too large: {name}')
            total+=info.file_size
            require(total<=16*1024**3, f'ZIP expansion limit exceeded: {path}')
        require(z.testzip() is None, f'ZIP CRC failed: {path}')

class DropCrossOriginAuth(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target=urllib.parse.urlparse(newurl)
        require(target.scheme=='https', 'non-HTTPS artifact redirect refused')
        redirected=super().redirect_request(req,fp,code,msg,headers,newurl)
        if redirected is None:return None
        if urllib.parse.urlparse(req.full_url).netloc != target.netloc:
            for k in list(redirected.headers):
                if k.lower()=='authorization':del redirected.headers[k]
            for k in list(redirected.unredirected_hdrs):
                if k.lower()=='authorization':del redirected.unredirected_hdrs[k]
        return redirected

def download_api_binary(endpoint, dest:Path, *, token:str, max_bytes:int,
                        expected_size:int|None=None, mime='application/vnd.github+json'):
    """Stream GitHub file endpoint through HTTPS 302; don't put token in CLI argv.
    Python's redirect handler explicitly removes Bearer on cross-host redirects.
    """
    require(endpoint.startswith('repos/'), 'unsafe binary API endpoint')
    uri='https://api.github.com/'+endpoint
    opener=urllib.request.build_opener(DropCrossOriginAuth())
    req=urllib.request.Request(uri,headers={
        'Accept':mime,'Authorization':'Bearer '+token,
        'User-Agent':'repo-rebuild/'+VERSION,
        'X-GitHub-Api-Version':'2022-11-28'})
    dest.parent.mkdir(parents=True,exist_ok=True)
    require(not dest.is_symlink(), 'download target symlink')
    fd,tmp=tempfile.mkstemp(prefix='.download-',dir=dest.parent)
    total=0
    try:
        with os.fdopen(fd,'wb') as out,opener.open(req,timeout=180) as stream:
            require(urllib.parse.urlparse(stream.geturl()).scheme=='https', 'HTTP download refused')
            media_type=(stream.headers.get('Content-Type','').lower()
                        if getattr(stream,'headers',None) else '')
            require(not media_type.startswith(('application/json','text/html')),
                    'binary endpoint returned metadata/html instead of bytes')
            with Progress(TextColumn('[bold blue]Downloading'), BarColumn(),
                          DownloadColumn(), TransferSpeedColumn(),
                          console=CONSOLE, transient=True) as progress:
                task=progress.add_task('bytes',total=expected_size)
                while True:
                    chunk=stream.read(1024*1024)
                    if not chunk:break
                    total+=len(chunk)
                    require(total<=max_bytes,'download exceeds per-file configured cap')
                    out.write(chunk)
                    progress.update(task,advance=len(chunk))
            out.flush();os.fsync(out.fileno())
        require(total>0,'empty downloaded file')
        if expected_size is not None:
            # GitHub's reported compressed artifact size should equal ZIP bytes.
            require(total==expected_size, f'artifact size mismatch expected={expected_size} got={total}')
        os.replace(tmp,dest)
        return {'bytes':total,'sha256':sha256(dest)}
    except Exception:
        if os.path.exists(tmp):os.unlink(tmp)
        raise


# ---- Configuration and dependency safety: source-bound inventory, never secret values ----
def endpoint_env(name):
    return urllib.parse.quote(name,safe='')

def collect_optional(endpoint,key):
    # Unsupported feature = an explicit absent record; forbidden access is a hard failure.
    first=api(endpoint + ('&' if '?' in endpoint else '?')+'per_page=100&page=1',missing_ok=True)
    if first is None:return None
    require(isinstance(first,dict) and isinstance(first.get(key),list),
            'unexpected GitHub inventory schema: '+endpoint)
    return collect(endpoint,key=key)

def selected_org_bindings(repo,repo_id,owner_type):
    if owner_type!='Organization':return {'status':'not-applicable','selected':[]}
    owner=repo.split('/',1)[0]
    matches=[]
    # Organization listing requires permissions beyond repository admin.
    # Inaccessible organization scope is UNKNOWN, never treated as empty.
    for kind,endpoint in (('actions-secrets',f'orgs/{owner}/actions/secrets'),
                          ('dependabot-secrets',f'orgs/{owner}/dependabot/secrets'),
                          ('codespaces-secrets',f'orgs/{owner}/codespaces/secrets'),
                          ('actions-variables',f'orgs/{owner}/actions/variables')):
        key='variables' if kind.endswith('variables') else 'secrets'
        values=collect_optional(endpoint,key)
        require(values is not None,'organization configuration not inspectable: '+endpoint)
        for item in values:
            if item.get('visibility')!='selected':continue
            name=item['name']
            path=endpoint+'/'+endpoint_env(name)+'/repositories'
            selected=collect_optional(path,'repositories')
            require(selected is not None,'selected organization binding unavailable: '+path)
            if any(int(x['id'])==int(repo_id) for x in selected):
                matches.append({'kind':kind,'name':name,'original_repository_id':repo_id})
    return {'status':'verified','selected':sorted(matches,key=lambda x:(x['kind'],x['name']))}

def capture_configuration(repo,info):
    base=f'repos/{repo}'
    actions=collect_optional(base+'/actions/secrets','secrets')
    dependabot=collect_optional(base+'/dependabot/secrets','secrets')
    codespaces=collect_optional(base+'/codespaces/secrets','secrets')
    require(actions is not None and dependabot is not None,
            'required repository secret inventory inaccessible')
    envs=collect_optional(base+'/environments','environments')
    require(envs is not None,'repository environment inventory inaccessible')
    environments={}
    for env in envs:
        name=env['name'];slug=endpoint_env(name)
        # The listing is not an authoritative substitute for the full
        # environment record containing protection_rules and reviewer details.
        definition=api(base+'/environments/'+slug)
        require(isinstance(definition,dict) and definition.get('name')==name and
                isinstance(definition.get('protection_rules'),list),
                'full environment protection inventory unavailable: '+name)
        entry={'definition':definition,
           'secrets':collect_optional(base+'/environments/'+slug+'/secrets','secrets'),
           'variables':collect_optional(base+'/environments/'+slug+'/variables','variables')}
        require(entry['secrets'] is not None and entry['variables'] is not None,
                'environment secret/variable inventory unavailable: '+name)
        if (env.get('deployment_branch_policy') or {}).get('custom_branch_policies'):
            entry['branch_policies']=collect_optional(
                base+'/environments/'+slug+'/deployment-branch-policies','branch_policies')
            require(entry['branch_policies'] is not None,
                    'environment deployment branch policies unavailable: '+name)
        else:entry['branch_policies']=[]
        environments[name]=entry
    permissions=api(base+'/actions/permissions/workflow')
    variables=collect_optional(base+'/actions/variables','variables')
    require(variables is not None,'repository Actions variables unavailable')
    hooks=[]
    for old_hook in collect(base+'/hooks'):
        # Webhook URLs frequently embed credentials in query/path. Preserve only
        # auditable routing metadata, never an unreadable signing token or URL.
        cfg=old_hook.get('config') or {}
        uri=urllib.parse.urlsplit(cfg.get('url') or '')
        hooks.append({'id':old_hook['id'],'name':old_hook.get('name'),
            'active':old_hook.get('active'),'events':old_hook.get('events',[]),
            'configuration':{'host':uri.hostname or '',
                'content_type':cfg.get('content_type'),
                'insecure_ssl':cfg.get('insecure_ssl')},
            'manual_restore_required':True})
    deploy_keys=collect(base+'/keys')
    rulesets=collect(base+'/rulesets?includes_parents=false')
    ruleset_details=[]
    for x in rulesets:
        full=api(base+'/rulesets/'+str(x['id']))
        ruleset_details.append(full)
    protections={}
    for b in collect(base+'/branches'):
        if b.get('protected'):
            name=b['name']
            protected=api(base+'/branches/'+endpoint_env(name)+'/protection',missing_ok=True)
            if protected is None:
                # Branch may be protected *only* by a repository ruleset.
                require(ruleset_details,'protected branch cannot be explained by classic protection or ruleset: '+name)
                continue
            signatures=api(base+'/branches/'+endpoint_env(name)+
                           '/protection/required_signatures',missing_ok=True)
            if signatures is not None:protected['required_signatures']=signatures
            protections[name]=protected
    return {'schema':1,'repo':repo,'repo_id':info['id'],'secrets':{
          'actions':actions,'dependabot':dependabot,'codespaces':codespaces or []},
          'codespaces_inventory':'available' if codespaces is not None else 'unsupported-or-inaccessible',
          'variables':variables,'environments':environments,
          'workflow_permissions':permissions,'organization':
              selected_org_bindings(repo,info['id'],(info.get('owner') or {}).get('type')),
          'webhooks':hooks,'deploy_keys':deploy_keys,'rulesets':ruleset_details,
          'branch_protections':protections}

def config_manual_obligations(config):
    items=[]
    if config.get('webhooks'):items.append('webhooks (secret tokens not readable)')
    # Deploy public keys, standard branch protections, and repository rulesets can
    # be recreated, but external private-key custody remains outside GitHub.
    if config.get('deploy_keys'):items.append('deploy keys: check external private-key ownership')
    if config.get('organization',{}).get('selected'):
        items.append('selected organization credentials/variables need new repository-ID reauthorization')
    for name,protection in config.get('branch_protections',{}).items():
        if (protection.get('required_signatures') or {}).get('enabled') or protection.get('required_deployments'):
            items.append('branch requires nonstandard protection migration: '+name)
    if config.get('codespaces_inventory')!='available':
        items.append('Codespaces repository secrets inventory unavailable/unsupported')
    for name,item in config.get('environments',{}).items():
        if any(r.get('type') not in ('wait_timer','required_reviewers','branch_policy')
               for r in item['definition'].get('protection_rules',[])):
            items.append('unsupported environment protection rule: '+name)
    return items

def backup_configuration(repo,root):
    dest=root/'configuration.json'
    if not dest.exists():
        log('Inventoring secrets (names only), variables, environments and integrations…')
        write_json(dest,capture_configuration(repo,read_json(root/'repo.json')))
    # No secrets values are ever written to the backup.
    config=read_json(dest)
    require(config['repo_id']==read_json(root/'repo.json')['id'],
            'configuration snapshot belongs to a different repository')

def backup_dependencies(repo,root):
    path=root/'dependency-audit.json'
    if path.exists():return
    if (root/'EMPTY_GIT_REPOSITORY').exists():
        write_json(path,{'schema':1,'repo':repo,'scanned_text_blobs':0,'findings':[],
            'scan_gaps':[],'referenced_pr_numbers':[], 'referenced_workflow_ids':[],
            'referenced_run_ids':[],'referenced_secret_names':[],
            'method':'empty repository'})
        return
    with tempfile.TemporaryDirectory(prefix='repo-rebuild-audit-') as td:
        mirror=Path(td)/'source.git'
        run(['git','clone','--mirror',root/'source.bundle',mirror],timeout=2400)
        audit=safety.scan_dependencies(mirror,repo,read_json(root/'source-refs.json'),run,
                                       read_json(root/'repo.json')['id'])
        write_json(path,audit)

def require_independent_backup(session,state):
    external=state.get('independent_copy')
    if not external:
        require(state.get('accept_local_only_risk'),
                'no independent-filesystem copy verified; pass --independent-copy-dir to '
                'prepare or explicitly --accept-local-only-risk (same-device data-loss risk)')
        return
    path=Path(external)
    require(path.exists() and path.is_dir() and not path.is_symlink(),
            'independent snapshot copy disappeared')
    require(path.stat().st_dev!=session.stat().st_dev,
            'independent snapshot no longer on a distinct filesystem')
    m=verify_snapshot(path)
    require(m==verify_snapshot(session/'primary'),'independent snapshot diverged')

def copy_independent_backup(session,state,directory):
    if directory is None:return
    source=session/'primary'
    root=safe_root(directory)
    require(root.stat().st_dev!=session.stat().st_dev,
            '--independent-copy-dir must be another filesystem (not merely another folder)')
    target=root/(session.parent.name+'-'+session.name)
    if target.exists():
        require(not target.is_symlink() and verify_snapshot(target)==verify_snapshot(source),
                'independent backup path conflicts with another snapshot')
    else:
        need=sum(f['bytes'] for f in read_json(source/'MANIFEST.json')['files'].values())
        require(shutil.disk_usage(root).free>int(need*1.12)+128*1024**2,
                'not enough free space for verified independent backup')
        staging=root/('.repo-rebuild-staging-'+uuid.uuid4().hex[:12])
        shutil.copytree(source,staging,symlinks=False)
        try:
            require(verify_snapshot(staging)==verify_snapshot(source),
                    'independent backup verification failed')
            os.replace(staging,target)
        except Exception:
            # Do not delete copies that may contain uniquely recoverable bytes.
            raise
    state['independent_copy']=str(target.resolve())
    save_state(session,state)

def predict_nonportable_objects(backup):
    repo=read_json(backup/'repo.json')
    saved=read_json(backup/'source-refs.json')
    source_name=repo['full_name']
    upstream={}
    if repo.get('fork') and (repo.get('parent') or {}).get('full_name'):
        upstream=live_refs(repo['parent']['full_name'])
    unsupportable=[]
    for pr in read_json(backup/'metadata'/'pulls.json'):
        num=pr['number'];head=pr['head'];target='refs/heads/'+head['ref']
        reasons=[]
        if ((head.get('repo') or {}).get('full_name') or '').lower()!=source_name.lower():
            reasons.append('external-or-deleted-head-repository')
        if target not in saved or saved.get(target)!=head['sha']:
            reasons.append('missing-or-mismatched-head-ref')
        if upstream.get(target) and upstream[target]!=saved.get(target):
            reasons.append('fork-upstream-ref-collision')
        if pr.get('merged') or pr.get('merged_at'):
            reasons.append('merged-history-cannot-be-recreated')
        if reasons:unsupportable.append({'number':num,'reasons':reasons})
    registrations=[]
    if not (backup/'EMPTY_GIT_REPOSITORY').exists():
        original_default=repo.get('default_branch')
        old_commit=saved.get('refs/heads/'+str(original_default))
        if old_commit:
            with tempfile.TemporaryDirectory(prefix='repo-rebuild-workflow-proof-') as tmp:
                mirror=Path(tmp)/'source.git'
                run(['git','clone','--mirror',backup/'source.bundle',mirror],timeout=2400)
                for wf in read_json(backup/'actions'/'workflows.json'):
                    if wf.get('state')=='deleted':continue
                    path=wf.get('path','')
                    if not re.fullmatch(r'\.github/workflows/[^/]+\.ya?ml',path):
                        registrations.append({'path':path,'reason':'unexpected-historical-workflow-path'})
                        continue
                    blob=run(['git','--git-dir',mirror,'ls-tree',old_commit,'--',path],timeout=30).stdout
                    if not blob.strip():
                        branch_sources=[]
                        for branch_ref,branch_sha in sorted(saved.items()):
                            if branch_ref.startswith('refs/heads/') and branch_sha!=old_commit:
                                evidence=run(['git','--git-dir',mirror,'ls-tree',
                                              branch_sha,'--',path],timeout=30).stdout
                                if evidence.strip():
                                    branch_sources.append(branch_ref[len('refs/heads/'):])
                        registrations.append({'path':path,
                            'reason':('branch-specific-workflow-not-in-default'
                                      if branch_sources else 'historical-workflow-without-default-source'),
                            'source_branches':branch_sources})
    named=safety.assess_named_repo_refs(
        read_json(backup/'dependency-audit.json'),source_name,saved,upstream)
    return {'nonportable_pull_requests':unsupportable,
            'nonportable_workflow_registrations':registrations,
            'named_reference_assessments':named,
            'named_reference_risks':[x for x in named if
                 x['replacement_name_status']!='verified-original-named-ref'],
            'fork_upstream_current_refs_checked':bool(upstream)}

def prepare_security(session,state,*,vault_file=None,age_identity=None,
                     accept_missing_secrets=False,accept_manual_recovery=False,
                     credentials_policy=None,accept_credential_manual_risk=False):
    config=read_json(session/'primary'/'configuration.json')
    deps=read_json(session/'primary'/'dependency-audit.json')
    req=safety.secret_requirements(config)
    predicted=predict_nonportable_objects(session/'primary')
    from . import credentials
    credential_feasibility=credentials.preflight(
        session,state,credentials_policy,accept_manual=accept_credential_manual_risk)
    vault=None
    if vault_file is not None:
        require(age_identity is not None,'--age-identity required with --secrets-vault')
        require(shutil.which('age'),'age executable required for encrypted vault')
        vault=safety.parse_age_vault(vault_file,age_identity,state['repo'],run)
        state['vault_sha256']=sha256(vault_file)
    missing=(safety.validate_vault(vault,state['repo'],req) if vault is not None
             else [{'app':a,'environment':e,'name':n} for a,e,n in req])
    obligations=config_manual_obligations(config)
    if predicted['nonportable_pull_requests']:
        obligations.append(f'{len(predicted["nonportable_pull_requests"])} PRs cannot retain original identity/history')
    if predicted['nonportable_workflow_registrations']:
        obligations.append(f'{len(predicted["nonportable_workflow_registrations"])} workflow registrations not in original default source')
    if predicted['named_reference_risks']:
        obligations.append(f'{len(predicted["named_reference_risks"])} same-repository named references will change or cannot be proved')
    blockers=([f'missing secret recovery sources: {len(missing)}'] if missing and not accept_missing_secrets else [])
    risky=[f for f in deps['findings'] if f['status'] in (
        'will-change','manual-review','unavailable-object','pinned-workflow-missing',
        'object-not-reachable-from-restored-refs','same-repository-ref-verify')]
    if risky:
        obligations.append(f'review {len(risky)} identity-dependent workflow/source references')
    if deps['scan_gaps']:
        obligations.append(f'review {len(deps["scan_gaps"])} unscanned source files')
    if (risky or deps['scan_gaps'] or predicted['named_reference_risks']) and not accept_manual_recovery:
        blockers+=['unresolved dependency references / scan gaps']
    if obligations and not accept_manual_recovery:
        blockers+=['manual recovery required: '+', '.join(obligations)]
    state['accept_missing_secrets']=bool(accept_missing_secrets)
    state['accept_manual_recovery']=bool(accept_manual_recovery)
    state['missing_secret_names']=missing
    state['manual_obligations']=obligations
    state['dependency_scan_gaps']=len(deps['scan_gaps'])
    state['preflight_blockers']=blockers
    write_json(session/'readiness.json',{'schema':1,'repo':state['repo'],
        'required_secrets':len(req),'missing_secrets':missing,
        'credential_recovery':credential_feasibility,
        'feasibility':predicted,
        'manual_recovery':obligations,'dependency_statuses':safety.status_counts(deps),
        'dependency_scan_gaps':deps['scan_gaps'],'blockers':blockers,
        'secret_values_in_snapshot':False,
        'note':'Live GitHub identities, external dependencies and Actions history cannot be faithfully recreated.'})
    save_state(session,state)
    require(not blockers,'pre-deletion readiness BLOCKED: '+', '.join(blockers)+'; see readiness.json')

def normalized_protection_payload(old):
    """Project GET protection document to GitHub PUT writable fields."""
    checks=old.get('required_status_checks')
    review=old.get('required_pull_request_reviews')
    restrictions=old.get('restrictions')
    payload={
        'required_status_checks':({'strict':bool(checks.get('strict')),
             'contexts':checks.get('contexts',[]),
             'checks':[{'context':c['context'],'app_id':c.get('app_id')}
                       for c in checks.get('checks',[])]} if checks else None),
        'enforce_admins':bool((old.get('enforce_admins') or {}).get('enabled')),
        'required_pull_request_reviews':None,
        'restrictions':None}
    if review:
        allowed=('dismiss_stale_reviews','require_code_owner_reviews',
                 'required_approving_review_count','require_last_push_approval')
        rr={k:review[k] for k in allowed if k in review}
        dismiss=review.get('dismissal_restrictions') or {}
        if dismiss:
            rr['dismissal_restrictions']={k:[] for k in ('users','teams','apps')}
            rr['dismissal_restrictions']['users']=[x['login'] for x in dismiss.get('users',[])]
            rr['dismissal_restrictions']['teams']=[x['slug'] for x in dismiss.get('teams',[])]
            rr['dismissal_restrictions']['apps']=[x['slug'] for x in dismiss.get('apps',[])]
        payload['required_pull_request_reviews']=rr
    if restrictions:
        payload['restrictions']={
            'users':[x['login'] for x in restrictions.get('users',[])],
            'teams':[x['slug'] for x in restrictions.get('teams',[])],
            'apps':[x['slug'] for x in restrictions.get('apps',[])]}
    for name in ('required_linear_history','allow_force_pushes','allow_deletions',
                 'block_creations','required_conversation_resolution','lock_branch',
                 'allow_fork_syncing'):
        if name in old:
            obj=old[name]
            payload[name]=bool(obj.get('enabled')) if isinstance(obj,dict) else bool(obj)
    return payload

def verify_protection(old,actual):
    expected=normalized_protection_payload(old)
    observed=normalized_protection_payload(actual)
    require(expected==observed,'restored branch protection differs')

def restore_repository_rules_and_keys(repo,config,state,session):
    base='repos/'+repo
    for key in config.get('deploy_keys',[]):
        existing=collect(base+'/keys')
        same=[x for x in existing if x.get('key')==key['key']]
        require(len(same)<=1,'duplicate deploy key on replacement')
        if not same:
            api(base+'/keys',method='POST',payload={
                'title':key['title'],'key':key['key'],'read_only':key['read_only']})
        require(any(x.get('key')==key['key'] and
                    bool(x.get('read_only'))==bool(key['read_only'])
                    for x in collect(base+'/keys')),'deploy key not restored')
    for name,old in sorted(config.get('branch_protections',{}).items()):
        uri=base+'/branches/'+endpoint_env(name)+'/protection'
        expected=normalized_protection_payload(old)
        current=api(uri,missing_ok=True)
        if current is None or normalized_protection_payload(current)!=expected:
            api(uri,method='PUT',payload=expected)
        verify_protection(old,api(uri))
    for old in config.get('rulesets',[]):
        target=old['target'];name=old['name']
        payload={k:old[k] for k in ('name','target','enforcement','conditions',
                                   'rules','bypass_actors') if k in old}
        require(payload.get('enforcement') in ('active','disabled','evaluate'),
                'invalid source ruleset enforcement')
        existing=[r for r in collect(base+'/rulesets?includes_parents=false')
                  if r.get('name')==name and r.get('target')==target]
        require(len(existing)<=1,'ambiguous replacement ruleset '+name)
        if not existing:
            created=api(base+'/rulesets',method='POST',payload=payload)
            existing=[created]
        new=api(base+'/rulesets/'+str(existing[0]['id']))
        for field in ('name','target','enforcement','conditions'):
            if field in payload:require(new.get(field)==payload[field],
                        'ruleset attribute mismatch: '+name+'/'+field)
        def r_sig(r):return (r.get('type'),r.get('parameters',{}))
        require([r_sig(x) for x in new.get('rules',[])]==
                [r_sig(x) for x in payload.get('rules',[])],
                'ruleset rule/parameters mismatch: '+name)
        require(new.get('bypass_actors',[])==payload.get('bypass_actors',[]),
                'ruleset bypass actors mismatch: '+name)
        state.setdefault('ruleset_ids',{})[str(old['id'])]=new['id']
        save_state(session,state)

def restorable_environment_policy(definition):
    """Compare the policy GitHub actually enforces, not just its name."""
    rules=definition.get('protection_rules')
    require(isinstance(rules,list), 'full environment protection rules were not captured')
    waits=[x for x in rules if x.get('type')=='wait_timer']
    reviews=[x for x in rules if x.get('type')=='required_reviewers']
    require(len(waits)<=1 and len(reviews)<=1,
            'ambiguous environment protection rules')
    review=reviews[0] if reviews else {}
    reviewers=[]
    for user in review.get('reviewers',[]):
        identity=user.get('reviewer') or {}
        require(user.get('type') in ('User','Team') and isinstance(identity.get('id'),int),
                'unrestorable environment reviewer identity')
        reviewers.append({'type':user['type'],'id':identity['id']})
    return {'wait_timer':int(waits[0].get('wait_timer',0)) if waits else 0,
            'prevent_self_review':bool(review.get('prevent_self_review',False)),
            'reviewers':sorted(reviewers,key=lambda x:(x['type'],x['id'])),
            'deployment_branch_policy':definition.get('deployment_branch_policy')}


def verify_environment_policy(original,restored):
    expected=restorable_environment_policy(original)
    observed=restorable_environment_policy(restored)
    require(expected==observed,
            'restored environment policy differs: '+str(original.get('name')))


def restore_security_configuration(session,state,*,vault_file=None,age_identity=None):
    if state['stage'] not in ('actions-done','configuration-intent'):return
    if state['stage']=='actions-done':state['stage']='configuration-intent';save_state(session,state)
    config=read_json(session/'primary'/'configuration.json')
    repo=state['repo'];base=f'repos/{repo}'
    # Authenticate the exact encrypted source before *any* remote mutation.
    required=safety.secret_requirements(config)
    missing_now={(x['app'],x['environment'],x['name'])
                 for x in state.get('missing_secret_names',[])}
    if required and any((a,e,n) not in missing_now for a,e,n in required):
        require(vault_file is not None and age_identity is not None and
                vault_file.is_file() and sha256(vault_file)==state.get('vault_sha256'),
                'exact verified vault and age identity required before restoration')
    get_new_repo(state)
    desired=config['workflow_permissions']
    current=api(base+'/actions/permissions/workflow')
    fields=('default_workflow_permissions','can_approve_pull_request_reviews')
    payload={k:desired[k] for k in fields if k in desired}
    if any(current.get(k)!=v for k,v in payload.items()):
        api(base+'/actions/permissions/workflow',method='PUT',payload=payload)
    after=api(base+'/actions/permissions/workflow')
    require(all(after.get(k)==v for k,v in payload.items()),
            'default GITHUB_TOKEN workflow permissions mismatch')
    for var in config['variables']:
        name=var['name'];value=var['value']
        url=base+'/actions/variables/'+endpoint_env(name)
        current=api(url,missing_ok=True)
        if current is None:api(base+'/actions/variables',method='POST',payload={'name':name,'value':value})
        elif current.get('value')!=value:
            api(url,method='PATCH',payload={'name':name,'value':value})
        require(api(url).get('value')==value,'repository variable mismatch: '+name)
    for name,env in sorted(config['environments'].items()):
        uri=base+'/environments/'+endpoint_env(name)
        rules=env['definition']['protection_rules']
        unknown=[r['type'] for r in rules
                 if r.get('type') not in ('wait_timer','required_reviewers','branch_policy')]
        if unknown:
            state.setdefault('unrestored_environment_protections',{})[name]=unknown
            save_state(session,state)
        payload=restorable_environment_policy(env['definition'])
        # On crash, PUT may be retried safely. Check all supported policy fields,
        # including wait timer and required reviewers, not only branch selection.
        api(uri,method='PUT',payload=payload)
        current=api(uri)
        require(current.get('name')==name,'environment name not restored: '+name)
        verify_environment_policy(env['definition'],current)
        for branch in env['branch_policies']:
            branch_url=uri+'/deployment-branch-policies'
            existing=collect(branch_url,key='branch_policies')
            if not any(x.get('name')==branch.get('name') and x.get('type')==branch.get('type')
                       for x in existing):
                api(branch_url,method='POST',payload={'name':branch['name'],'type':branch['type']})
        remaining=collect(uri+'/deployment-branch-policies',key='branch_policies') if env['branch_policies'] else []
        require(all(any(x.get('name')==branch['name'] and x.get('type')==branch['type']
                        for x in remaining) for branch in env['branch_policies']),
                'deployment branch policy not restored: '+name)
        for var in env['variables']:
            url=uri+'/variables/'+endpoint_env(var['name'])
            old=api(url,missing_ok=True)
            if old is None:api(uri+'/variables',method='POST',payload={'name':var['name'],'value':var['value']})
            elif old.get('value')!=var['value']:
                api(url,method='PATCH',payload={'name':var['name'],'value':var['value']})
            require(api(url).get('value')==var['value'], 'environment variable mismatch '+name+'/'+var['name'])
    restore_repository_rules_and_keys(repo,config,state,session)
    # Organization secrets/variables are retained but selected-repo access is bound to old ID.
    org=config['organization']
    if org.get('status')=='verified':
        owner=repo.split('/')[0]
        for item in org['selected']:
            kind=item['kind'];name=item['name']
            endpoint={'actions-secrets':'actions/secrets','dependabot-secrets':'dependabot/secrets',
                      'codespaces-secrets':'codespaces/secrets','actions-variables':'actions/variables'}[kind]
            path=f'orgs/{owner}/{endpoint}/{endpoint_env(name)}/repositories/{state["created_new_id"]}'
            api(path,method='PUT')
            members=collect(f'orgs/{owner}/{endpoint}/{endpoint_env(name)}/repositories',key='repositories')
            require(any(int(x['id'])==int(state['created_new_id']) for x in members),
                    'organization binding not restored: '+kind+'/'+name)
            state.setdefault('org_rebound',[]).append(kind+'/'+name)
            save_state(session,state)
    req=safety.secret_requirements(config)
    missing={(x['app'],x['environment'],x['name']) for x in state.get('missing_secret_names',[])}
    if vault_file is not None:
        require(age_identity is not None and sha256(vault_file)==state.get('vault_sha256'),
                'secret vault changed since verified preflight or age identity missing')
        vault=safety.parse_age_vault(vault_file,age_identity,repo,run)
    else:vault=None
    if req and not vault and any((a,e,n) not in missing for a,e,n in req):
        raise Refused('resumption requires the original encrypted vault and --age-identity')
    for app,env,name in req:
        if (app,env,name) in missing:continue
        value=safety.vault_value(vault,app,env,name)
        argv=['gh','secret','set',name,'--repo',repo]
        if app=='environment':argv+=['--env',env]
        else:argv+=['--app',app]
        # Sensitive value travels only via process stdin; never argv, journal or stdout.
        p=run(argv,input_data=value,check=False,timeout=90)
        require(p.returncode==0,'secret provisioning failed for '+app+'/'+env+'/'+name)
        state.setdefault('provisioned_secrets',[]).append({'app':app,'environment':env,'name':name})
        save_state(session,state)
    for app,env,name in req:
        if (app,env,name) in missing:continue
        if app=='environment':
            path=base+'/environments/'+endpoint_env(env)+'/secrets'
        else:path=base+('/codespaces/secrets' if app=='codespaces' else '/'+app+'/secrets')
        arr=collect(path,key='secrets')
        require(any(x['name']==name for x in arr),'secret name missing after restore: '+name)
    # Secrets can only be checked by name/scope. Never claim equality of secret values.
    state['secret_values_verified']=False
    state['stage']='configuration-done';save_state(session,state)

def backup_git(repo,root):
    dest=root/'source.bundle'
    if dest.exists():
        require((root/'source-refs.json').exists(),'incomplete Git backup detected; inspect source.bundle')
        return
    with tempfile.TemporaryDirectory(prefix='repo-rebuild-mirror-') as tmp:
        bare=Path(tmp)/'source.git'
        log('Cloning complete Git mirror…')
        run(['git','clone','--mirror',remote_url(repo),bare],timeout=2400)
        refs=refs_from_git(bare)
        require(refs==live_refs(repo),'remote refs changed during backup; restart a fresh snapshot')
        run(['git','--git-dir',bare,'fsck','--full','--no-reflogs'],timeout=1600)
        if not refs:
            write_json(root/'source-refs.json',{})
            atomic_bytes(root/'EMPTY_GIT_REPOSITORY',b'No refs existed in the original repository.\n')
            return
        if run(['git','lfs','version'],check=False).returncode==0:
            log('Fetching all Git LFS objects…')
            run(['git','--git-dir',bare,'lfs','fetch','--all','origin'],timeout=5400)
            run(['git','--git-dir',bare,'lfs','fsck','--objects'],timeout=900)
            lfsdir=bare/'lfs'/'objects'
            if lfsdir.is_dir():shutil.copytree(lfsdir,root/'lfs'/'objects')
            write_json(root/'lfs-status.json',{'available':True,'fetch':'pass'})
        else:
            # Refuse a source that appears to declare Git LFS without a verified fetch.
            detects=run(['git','--git-dir',bare,'grep','-Il','filter=lfs',*[
                r for r in refs if r.startswith('refs/heads/')], '--','**/.gitattributes','.gitattributes'],
                check=False,timeout=300)
            require(detects.returncode!=0 or not detects.stdout.strip(),
                    'Git LFS pointers detected but git-lfs unavailable')
            write_json(root/'lfs-status.json',{'available':False,'fetch':'not-installed; no LFS attrs detected'})
        tempbundle=root/'source.bundle.partial'
        if tempbundle.exists():tempbundle.unlink()
        run(['git','--git-dir',bare,'bundle','create',tempbundle,'--all'],timeout=2400)
        run(['git','bundle','verify',tempbundle],cwd=bare,timeout=500)
        os.replace(tempbundle,dest)
        write_json(root/'source-refs.json',refs)
        require(dest.stat().st_size>1024,'bundle too small')

def backup_metadata(repo,root):
    meta=root/'metadata'
    meta.mkdir(exist_ok=True)
    endpoint_map={
      'pulls':'pulls?state=all', 'issues':'issues?state=all',
      'labels':'labels','milestones':'milestones?state=all',
      'releases':'releases','branches':'branches',
    }
    for title,path in endpoint_map.items():
        dst=meta/(title+'.json')
        if not dst.exists():
            log(f'Archiving {title}…')
            write_json(dst,collect(f'repos/{repo}/{path}'))
    for issue in read_json(meta/'issues.json'):
        n=issue['number']
        p=meta/'issue-comments'/f'{n}.json'
        if not p.exists():write_json(p,collect(f'repos/{repo}/issues/{n}/comments'))
    for pr in read_json(meta/'pulls.json'):
        n=pr['number']
        for title,ep in [('reviews',f'pulls/{n}/reviews'),
                         ('review-comments',f'pulls/{n}/comments'),
                         ('timeline-events',f'issues/{n}/events')]:
            p=meta/'pr-details'/f'{n}-{title}.json'
            if not p.exists():write_json(p,collect(f'repos/{repo}/{ep}'))

def backup_actions(repo,root,*,skip_logs,max_bytes,allow_gaps,skip_action_artifacts=False):
    target=root/'actions';target.mkdir(exist_ok=True)
    for title,url,key in [
      ('runs',f'repos/{repo}/actions/runs','workflow_runs'),
      ('artifacts',f'repos/{repo}/actions/artifacts','artifacts'),
      ('workflows',f'repos/{repo}/actions/workflows','workflows')]:
        path=target/(title+'.json')
        if not path.exists():
            log(f'Archiving Actions {title}…')
            write_json(path,collect(url,key=key))
    if not (target/'permissions.json').exists():
        write_json(target/'permissions.json',api(f'repos/{repo}/actions/permissions'))
    if read_json(target/'permissions.json').get('allowed_actions')=='selected':
        selected=target/'selected-actions.json'
        if not selected.exists():
            write_json(selected,api(f'repos/{repo}/actions/permissions/selected-actions'))
    runs=read_json(target/'runs.json')
    for r in runs:
        id_=int(r['id'])
        dst=target/'jobs'/f'{id_}.json'
        if not dst.exists():write_json(dst,collect(f'repos/{repo}/actions/runs/{id_}/jobs',key='jobs'))
    token=run(['gh','auth','token']).stdout.strip()
    require(bool(token),'gh auth token empty')
    gaps=[]
    skipped=[]
    for a in read_json(target/'artifacts.json'):
        aid=int(a['id']);dst=target/'artifact-zips'/f'{aid}.zip'
        if skip_action_artifacts:
            require(not dst.exists(), 'cached artifact ZIP must be cleaned before skip: '+str(dst))
            skipped.append({'id':aid,'name':a.get('name'),'reason':'intentionally-skipped'})
            continue
        if a.get('expired'):
            gaps.append({'type':'artifact-expired','id':aid,'name':a.get('name')})
            continue
        if not dst.exists():
            log(f'Downloading artifact {aid}: {a.get("name", "")}')
            try:
                download_api_binary(f'repos/{repo}/actions/artifacts/{aid}/zip',dst,
                   token=token,max_bytes=max_bytes,expected_size=a.get('size_in_bytes'))
            except (urllib.error.HTTPError,urllib.error.URLError,Refused,OSError) as e:
                gaps.append({'type':'artifact-unavailable','id':aid,'reason':type(e).__name__+' '+str(getattr(e,'code',''))})
                continue
        try:
            zip_ok(dst)
            github_digest=a.get('digest')
            if github_digest:
                require(github_digest=='sha256:'+sha256(dst),
                        f'GitHub artifact SHA-256 digest mismatch: {aid}')
        except (zipfile.BadZipFile,Refused) as e:
            # A known-invalid downloaded ZIP must not survive into VERIFIED.
            # Keep a gap record and allow explicit archive-gap authorization.
            if dst.is_file() and not dst.is_symlink():dst.unlink()
            gaps.append({'type':'artifact-invalid','id':aid,'reason':type(e).__name__+' '+str(e)[:250]})
    if not skip_logs:
        for r in runs:
            runid=int(r['id']);dst=target/'run-logs'/f'{runid}.zip'
            if not dst.exists():
                try:
                    download_api_binary(f'repos/{repo}/actions/runs/{runid}/logs',dst,
                        token=token,max_bytes=max_bytes)
                except (urllib.error.HTTPError,urllib.error.URLError,Refused,OSError) as e:
                    gaps.append({'type':'run-logs-unavailable','id':runid,'reason':type(e).__name__+' '+str(getattr(e,'code',''))})
                    continue
            try:zip_ok(dst)
            except (zipfile.BadZipFile,Refused) as e:
                if dst.is_file() and not dst.is_symlink():dst.unlink()
                gaps.append({'type':'run-logs-invalid','id':runid,'reason':type(e).__name__+' '+str(e)[:250]})
    write_json(target/'coverage.json',{'gaps':gaps,'skipped_artifacts':skipped,'action_artifacts_requested':not skip_action_artifacts,'logs_requested':not skip_logs,
       'runs':len(runs),'artifacts_recorded':len(read_json(target/'artifacts.json')),
       'artifacts_downloaded':len(list((target/'artifact-zips').glob('*.zip'))) if (target/'artifact-zips').exists() else 0})
    require(allow_gaps or not gaps,
        f'{len(gaps)} unavailable/expired Actions items: inspect actions/coverage.json; '
        're-run prepare with --accept-archive-gaps only if knowingly accepted')

def backup_snapshot(repo,root,*,skip_logs,max_bytes,allow_gaps,skip_action_artifacts=False):
    root.mkdir(parents=True,exist_ok=True)
    require(not root.is_symlink(), 'unsafe snapshot directory')
    if (root/'VERIFIED').exists():
        verify_snapshot(root);return
    info=repo_info(repo)
    require(info is not None,'repository absent before backup')
    # GitHub's repo payload can include ephemeral temp_clone_token. Never archive it.
    info.pop('temp_clone_token',None)
    if not (root/'repo.json').exists():write_json(root/'repo.json',info)
    else:require(read_json(root/'repo.json')['id']==info['id'],'snapshot repository identity changed')
    backup_git(repo,root)
    backup_metadata(repo,root)
    backup_configuration(repo,root)
    backup_dependencies(repo,root)
    backup_actions(repo,root,skip_logs=skip_logs,max_bytes=max_bytes,allow_gaps=allow_gaps,skip_action_artifacts=skip_action_artifacts)
    archive_release_assets(repo,root,max_bytes,allow_gaps)
    # Crash remnants are never treated as durable, verified evidence.
    for tmp in root.rglob('.download-*'):
        require(tmp.is_file() and not tmp.is_symlink(),'unsafe partial download')
        tmp.unlink()
    final_refs=live_refs(repo)
    require(final_refs==read_json(root/'source-refs.json'),
            'repository refs changed during capture; restart a new snapshot')
    original=read_json(root/'repo.json')
    require(repo_info(repo)['id']==original['id'],'repository replaced during capture')
    manifest={'schema':1,'version':VERSION,'repo':repo,'original_repo_id':original['id'],
       'fork':bool(original.get('fork')),
       'upstream':(original.get('parent') or {}).get('full_name'),
       'visibility':original.get('visibility','public'),
       'default_branch':original.get('default_branch'),
       'created_at':now(),'files':archive_paths(root)}
    write_json(root/'MANIFEST.json',manifest)
    verify_snapshot(root)
    atomic_bytes(root/'VERIFIED',b'verified; not a digital signature\n')
    log(f'Verified backup: {root}',style='green')

def verify_snapshot(root):
    m=read_json(root/'MANIFEST.json')
    require(m.get('schema')==1 and REPO_RE.fullmatch(m.get('repo','')),
            'invalid snapshot schema/repo')
    listed=m['files']
    actual=archive_paths(root)
    require(actual==listed,'backup file inventory/SHA-256 drift')
    is_empty=(root/'EMPTY_GIT_REPOSITORY').exists()
    require(is_empty or (root/'source.bundle').is_file(),'missing Git bundle')
    require(not (is_empty and (root/'source.bundle').exists()),'conflicting empty/bundle markers')
    for relative in listed:
        if relative.endswith('.zip'):zip_ok(root/relative)
    if is_empty:
        require(read_json(root/'source-refs.json')=={},'empty Git marker with nonempty refs')
    else:
        with tempfile.TemporaryDirectory(prefix='repo-rebuild-check-') as td:
            bare=Path(td)/'source.git'
            run(['git','clone','--mirror',root/'source.bundle',bare],timeout=2400)
            require(refs_from_git(bare)==read_json(root/'source-refs.json'),
                    'Git bundle refs inconsistent')
            run(['git','--git-dir',bare,'fsck','--full','--no-reflogs'],timeout=1600)
    config=read_json(root/'configuration.json')
    require(config['schema']==1 and config['repo_id']==m['original_repo_id'],
            'configuration inventory is missing or wrong repository')
    deps=read_json(root/'dependency-audit.json')
    require(deps['schema']==1 and deps['repo'].lower()==m['repo'].lower(),
            'dependency audit missing or wrong repository')
    artifacts=read_json(root/'actions'/'artifacts.json')
    coverage=read_json(root/'actions'/'coverage.json')
    gap_artifact_ids={int(g['id']) for g in coverage.get('gaps',[])
                      if str(g.get('type','')).startswith('artifact-')}
    known_artifact_ids={int(a['id']) for a in artifacts}
    skipped_artifact_ids={int(a['id']) for a in coverage.get('skipped_artifacts', [])}
    require(skipped_artifact_ids <= known_artifact_ids, 'unknown intentionally skipped artifact')
    require(not skipped_artifact_ids.intersection(gap_artifact_ids), 'skipped and missing artifacts overlap')
    require(gap_artifact_ids <= known_artifact_ids,'unknown Actions artifact gap')
    for a in artifacts:
        aid=int(a['id'])
        dst=root/'actions'/'artifact-zips'/f'{aid}.zip'
        if dst.exists():
            require(aid not in skipped_artifact_ids, 'intentionally skipped artifact present')
            require(not a.get('expired'),'expired artifact represented as downloaded')
            require(dst.stat().st_size==int(a['size_in_bytes']),
                    'archived Actions artifact size mismatch')
            if a.get('digest'):
                require('sha256:'+sha256(dst)==a['digest'],
                        'archived Actions artifact digest mismatch')
        else:
            require(aid in gap_artifact_ids or aid in skipped_artifact_ids,'missing artifact not recorded as a gap or intentional skip')
    require(coverage['artifacts_recorded']==len(artifacts),
            'Actions artifact coverage count mismatch')
    require(coverage['artifacts_downloaded']==sum(
        (root/'actions'/'artifact-zips'/f'{a["id"]}.zip').exists() for a in artifacts),
            'Actions downloaded artifact coverage count mismatch')
    for rel in read_json(root/'metadata'/'releases.json'):
        for asset in rel.get('assets') or []:
            dest=root/'release-assets'/f'{asset["id"]}.bin'
            if dest.exists():
                require(dest.stat().st_size==int(asset['size']),
                        'archived release asset size mismatch')
                if asset.get('digest'):
                    require('sha256:'+sha256(dest)==asset['digest'],
                            'archived release asset digest mismatch')
                if asset.get('name','').lower().endswith('.zip'):
                    zip_ok(dest)
            else:
                gap=read_json(root/'release-asset-coverage.json')
                require(any(g.get('asset_id')==asset['id'] for g in gap.get('gaps',[])),
                        'missing release asset not recorded as gap')
    return m


def snapshot_pr_signature(pr):
    return {'number':pr['number'],'title':pr['title'],'body':pr.get('body') or '',
      'state':pr['state'],'draft':bool(pr.get('draft',False)),
      'head_ref':pr['head']['ref'],'head_sha':pr['head']['sha'],
      'head_repo':(pr['head'].get('repo') or {}).get('full_name'),
      'base_ref':pr['base']['ref'],'base_sha':pr['base']['sha']}

def predelete_compare(backup:Path):
    meta=read_json(backup/'MANIFEST.json')
    repo=meta['repo']
    live=repo_info(repo)
    require(live is not None and live['id']==meta['original_repo_id'],
            'original repository identity changed; deletion refused')
    require(bool(live.get('fork'))==meta['fork'] and
           (live.get('parent') or {}).get('full_name')==meta['upstream'],
            'fork topology changed')
    require(live.get('default_branch')==meta['default_branch'],
            'default branch changed')
    require(live_refs(repo)==read_json(backup/'source-refs.json'),
            'remote Git refs drifted since backup')
    current=collect(f'repos/{repo}/pulls?state=all')
    saved=read_json(backup/'metadata'/'pulls.json')
    require([snapshot_pr_signature(p) for p in current]==
            [snapshot_pr_signature(p) for p in saved],
            'PR records changed since backup')
    # Fail closed on other mutable metadata, not only source heads and PRs.
    def issue_sig(x):
        return (x['number'],x['title'],x.get('body') or '',x['state'],
                x.get('updated_at'), tuple(sorted(y['name'] for y in x.get('labels',[]))),
                (x.get('milestone') or {}).get('number'))
    original_issues=read_json(backup/'metadata'/'issues.json')
    require([issue_sig(x) for x in collect(f'repos/{repo}/issues?state=all')]==
            [issue_sig(x) for x in original_issues], 'issues changed since backup')
    # Comments and reviews are immutable evidence once captured. Their edits
    # are easy to miss in the outer issue/PR updated_at fields, so verify the
    # exact archive records again at the destructive boundary.
    for x in original_issues:
        n=x['number']
        stored=read_json(backup/'metadata'/'issue-comments'/f'{n}.json')
        current=collect(f'repos/{repo}/issues/{n}/comments')
        def comment_sig(v):
            return (v['id'],v.get('body'),v.get('updated_at'),
                    (v.get('user') or {}).get('id'))
        require([comment_sig(v) for v in current]==[comment_sig(v) for v in stored],
                f'issue/PR #{n} comments changed since backup')
    for x in saved:
        n=x['number']
        for suffix,endpoint in (
                ('reviews',f'pulls/{n}/reviews'),
                ('review-comments',f'pulls/{n}/comments'),
                ('timeline-events',f'issues/{n}/events')):
            stored=read_json(backup/'metadata'/'pr-details'/f'{n}-{suffix}.json')
            current=collect(f'repos/{repo}/{endpoint}')
            def signature(v):
                return (v.get('id'),v.get('body'),v.get('state'),
                        v.get('event'),v.get('updated_at'),v.get('submitted_at'))
            require([signature(v) for v in current]==[signature(v) for v in stored],
                    f'PR #{n} {suffix} changed since backup')
    original_releases=read_json(backup/'metadata'/'releases.json')
    def release_sig(x):
        return (x['id'],x['tag_name'],x.get('body'),x.get('name'),x.get('draft'),
                x.get('prerelease'),tuple(sorted(a['id'] for a in x.get('assets') or [])))
    require([release_sig(x) for x in collect(f'repos/{repo}/releases')]==
            [release_sig(x) for x in original_releases], 'releases changed since backup')
    for name,path,kwargs in (
            ('labels','labels',{}),('milestones','milestones?state=all',{}),
            ('workflows','actions/workflows',{'key':'workflows'})):
        original_path=(backup/'actions'/'workflows.json' if name=='workflows'
                       else backup/'metadata'/(name+'.json'))
        existing=collect(f'repos/{repo}/{path}',**kwargs)
        saved_collection=read_json(original_path)
        if name=='labels':
            sig=lambda x:(x['name'],x.get('color'),x.get('description'))
        elif name=='milestones':
            sig=lambda x:(x['number'],x['title'],x.get('description'),x['state'],
                          x.get('due_on'),x.get('updated_at'))
        else:
            sig=lambda x:(x['id'],x['path'],x.get('state'))
        require([sig(x) for x in existing]==[sig(x) for x in saved_collection],
                f'{name} changed since backup')
    require(api(f'repos/{repo}/actions/permissions')==
            read_json(backup/'actions'/'permissions.json'),
            'Actions policy changed since backup')
    if (backup/'actions'/'selected-actions.json').exists():
        require(api(f'repos/{repo}/actions/permissions/selected-actions')==
                read_json(backup/'actions'/'selected-actions.json'),
                'selected Actions policy changed since backup')
    require(capture_configuration(repo,live)==read_json(backup/'configuration.json'),
            'secrets/configuration/integration inventory drifted since backup')
    # New artifact IDs/runs after backup means the archive is incomplete.
    current_ids={x['id'] for x in collect(f'repos/{repo}/actions/artifacts',key='artifacts')}
    saved_ids={x['id'] for x in read_json(backup/'actions'/'artifacts.json')}
    require(current_ids==saved_ids,'GitHub Actions artifacts changed since backup')
    current_runs={x['id'] for x in collect(f'repos/{repo}/actions/runs',key='workflow_runs')}
    old_runs={x['id'] for x in read_json(backup/'actions'/'runs.json')}
    require(current_runs==old_runs,'GitHub Actions runs changed since backup')
    current_runs_full=collect(f'repos/{repo}/actions/runs',key='workflow_runs')
    archived={x['id']:x for x in read_json(backup/'actions'/'runs.json')}
    require(all(x.get('status')==archived[x['id']].get('status') and
                x.get('conclusion')==archived[x['id']].get('conclusion')
                for x in current_runs_full), 'Actions run statuses drifted since backup')
    active=[r for r in current_runs_full if
            r.get('status') in ('queued','in_progress','waiting','requested','pending')]
    require(not active, f'{len(active)} archived Actions runs were active at backup; refuse deletion')
    return live

def secured_session_dir(repo, *, base=DEFAULT_ROOT):
    require(REPO_RE.fullmatch(repo),'invalid OWNER/REPO')
    return safe_root(base / repo.replace('/','--').lower())

def state_path(session:Path):return session/'state.json'

def new_session(repo,*,base=DEFAULT_ROOT):
    root=secured_session_dir(repo,base=base)
    session=root/(dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])
    require(not session.exists(),'session collision')
    session.mkdir(mode=0o700)
    state={'schema':1,'session_id':session.name,'repo':repo,'stage':'backup',
           'created_at':now(),'approved':False,'created_new_id':None,
           'objects':{},'labels':{},'milestones':{},'releases':{},
           'accept_archive_gaps':False,'skip_logs':False,
           'max_artifact_mb':DEFAULT_MAX_ARTIFACT_MB}
    save_state(session,state)
    log(f'New durable recovery session: {session}',style='bold blue')
    return session

def save_state(session,state):
    state['updated_at']=now()
    write_json(state_path(session),state)

def load_state(session):
    s=read_json(state_path(session))
    require(s.get('schema')==1 and REPO_RE.fullmatch(s.get('repo','')),
            'invalid recovery journal')
    require(s.get('session_id')==session.name,'journal location mismatch')
    return s

@contextlib.contextmanager
def locked(session):
    require(session.is_dir() and not session.is_symlink(),'unsafe session directory')
    require(session.stat().st_uid==os.getuid(),'session owned by another user')
    require(session.stat().st_mode & 0o022==0,'unsafe writable session directory')
    fd=os.open(session/'.lock',os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o600)
    try:
        try:fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise Refused('session already running in another process')
        yield
    finally:
        fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)

def prepare(session:Path, *, accept_archive_gaps=False, skip_logs=False,
            max_artifact_mb=DEFAULT_MAX_ARTIFACT_MB, skip_action_artifacts=False,
            independent_copy_dir=None,accept_local_only_risk=False,secrets_vault=None,
            age_identity=None,accept_missing_secrets=False,accept_manual_recovery=False,
            credentials_policy=None,accept_credential_manual_risk=False):
    with locked(session):
        state=load_state(session)
        require(state['stage'] in ('backup','prepared'),
                'preparation cannot restart after deletion approval')
        if state['stage']=='prepared':
            verify_pair(session,state)
            require_independent_backup(session,state)
            from . import credentials
            credentials.verify_preparation_anchor(session,state)
            log('Backups and credential preparation anchor already verified',style='green');return
        repo=state['repo']
        for exe in ('gh','git'):
            require(shutil.which(exe),f'{exe} is required')
        require(run(['gh','auth','status'],check=False).returncode==0,'gh not authenticated')
        require(run(['git','lfs','version'],check=False).returncode==0,
                'git-lfs required to prove full LFS coverage; install git-lfs first')
        info=repo_info(repo)
        require(info and info.get('full_name','').lower()==repo.lower(),
                'repository identity does not match requested OWNER/REPO')
        repo=info['full_name']
        state['repo']=repo
        save_state(session,state)
        from . import credentials
        credentials.require_declaration(session,state,credentials_policy)
        require(info and info.get('permissions',{}).get('admin'),
                'repository admin permission required for recreation')
        owner=info.get('owner') or {}
        user=api('user').get('login','')
        require(user,'cannot confirm authenticated GitHub account')
        if owner.get('type')=='User':
            require(owner.get('login','').lower()==user.lower(),
                    'cannot recreate repository owned by another personal account')
            require(info.get('visibility')!='internal',
                    'internal repositories require an organization owner')
        else:
            require(owner.get('type')=='Organization',
                    'unsupported repository owner type')
            log('Organization target: verify your create-repository permission '
                'before approving deletion; admin rights alone may not suffice',style='yellow')
        require(not info.get('archived'),'cannot recreate an archived repository automatically')
        require(info.get('visibility') in ('public','private','internal'),
                'unsupported repository visibility')
        require(not info.get('fork') or (info.get('parent') or {}).get('full_name'),
                'fork parent not discoverable')
        if info.get('fork'):
            upstream=repo_info(info['parent']['full_name'])
            require(upstream and not upstream.get('archived'),'fork upstream unavailable')
            require(upstream.get('allow_forking',True) is not False,
                    'upstream currently disallows forking; recreation cannot succeed')
        else:upstream=None
        require(max_artifact_mb>0,'max artifact size must be positive')
        require(not (accept_local_only_risk and independent_copy_dir is not None),
                'choose independent copy OR local-only risk acceptance')
        if independent_copy_dir is not None:
            # Fail early on Android shared storage/ownership, wrong filesystem,
            # and near-empty destinations rather than after hours of downloads.
            external=safe_root(independent_copy_dir)
            require(external.stat().st_dev != session.stat().st_dev,
                    'independent backup destination is on the session filesystem')
            require(os.access(external, os.W_OK | os.X_OK),
                    'independent backup destination is not writable')
            require(shutil.disk_usage(external).free > 128*1024**2,
                    'independent backup destination has insufficient initial free space')
        estimate=int(info.get('size',0))*1024
        require(shutil.disk_usage(session).free>int(estimate*1.2)+128*1024**2,
                'insufficient free space estimate before backup')
        state.update({'accept_archive_gaps':bool(accept_archive_gaps),
                      'skip_logs':bool(skip_logs),'max_artifact_mb':max_artifact_mb,
                      'accept_local_only_risk':bool(accept_local_only_risk)})
        save_state(session,state)
        primary=session/'primary'
        backup_snapshot(repo,primary,skip_logs=skip_logs,
             max_bytes=max_artifact_mb*1024*1024,allow_gaps=accept_archive_gaps,skip_action_artifacts=skip_action_artifacts)
        require(read_json(primary/'repo.json')['id']==info['id'],
                'original repo changed while capturing')
        # Protect against mutation with a second byte-verified local copy.
        secondary=session/'secondary'
        if secondary.exists():
            try:
                verify_snapshot(secondary)
            except (Refused,OSError,ValueError,KeyError,zipfile.BadZipFile) as e:
                require(secondary.is_dir() and not secondary.is_symlink(),
                        'unexpected secondary backup path; manual review required')
                quarantine=session/('secondary-incomplete-'+uuid.uuid4().hex[:12])
                log(f'Quarantining interrupted secondary copy to {quarantine}: {e}',style='yellow')
                os.replace(secondary,quarantine)
        if not secondary.exists():
            need=sum(f['bytes'] for f in read_json(primary/'MANIFEST.json')['files'].values())
            require(shutil.disk_usage(session).free>int(need*1.12)+128*1024**2,
                    'insufficient space for secondary verified backup')
            log('Creating a second verified local copy…')
            stage=session/('.secondary-copy-'+uuid.uuid4().hex[:12])
            shutil.copytree(primary,stage,symlinks=False)
            verify_snapshot(stage)
            os.replace(stage,secondary)
        verify_snapshot(secondary)
        require(read_json(primary/'MANIFEST.json')==read_json(secondary/'MANIFEST.json'),
                'secondary backup differs')
        copy_independent_backup(session,state,independent_copy_dir)
        require_independent_backup(session,state)
        prepare_security(session,state,vault_file=secrets_vault,age_identity=age_identity,
                         accept_missing_secrets=accept_missing_secrets,
                         accept_manual_recovery=accept_manual_recovery,
                         credentials_policy=credentials_policy,
                         accept_credential_manual_risk=accept_credential_manual_risk)
        credentials.seal_preparation(session,state,info['id'])
        predelete_compare(primary)
        state['manifest_sha256']=sha256(primary/'MANIFEST.json')
        state['original_repo_id']=info['id']
        state['original_visibility']=info['visibility']
        state['topology']='fork' if info.get('fork') else 'standalone'
        state['parent_repo']=(info.get('parent') or {}).get('full_name')
        state['stage']='prepared'
        save_state(session,state)
        log(f'PREPARED: {session}',style='green bold')
        render_status(session,state)

def verify_pair(session,state):
    primary=session/'primary'; secondary=session/'secondary'
    a=verify_snapshot(primary)
    b=verify_snapshot(secondary)
    require(a==b,'primary/secondary manifests differ')
    require(sha256(primary/'MANIFEST.json')==state['manifest_sha256'],
            'backups do not match journal anchor')
    require(a['repo']==state['repo'] and a['original_repo_id']==state['original_repo_id'],
            'backup/journal repository mismatch')
    return a

def get_new_repo(state,*,optional=False):
    r=repo_info(state['repo'])
    if r is None:
        if optional:return None
        raise Refused('new GitHub repository not yet visible')
    require(r['id']!=state['original_repo_id'],
            'ORIGINAL repository still exists or was restored under same ID')
    require(bool(r.get('fork'))==(state['topology']=='fork'),
            'new repository fork type differs')
    require((r.get('parent') or {}).get('full_name')==state['parent_repo'],
            'new repository upstream differs')
    if state.get('original_visibility') is not None:
        require(r.get('visibility')==state['original_visibility'],
                'new repository visibility differs from original')
    if state.get('created_new_id') is not None:
        require(r['id']==state['created_new_id'],
                'new repository ID changed since journal checkpoint')
    return r

def delete_original(session,state):
    if state['stage'] not in ('prepared','delete-intent'):return
    from . import credentials
    require(state.get('approved') is True,'exact deletion approval not journaled')
    if state['stage']=='prepared':
        require_independent_backup(session,state)
        require(not state.get('preflight_blockers'),'preflight no longer cleared')
        credentials.verify_preparation_anchor(session,state)
        predelete_compare(session/'primary')
        state['stage']='delete-intent';save_state(session,state)
    existing=repo_info(state['repo'])
    if existing is not None:
        require(existing['id']==state['original_repo_id'],
                'different repository now occupies target; refuse delete')
        credentials.recheck_before_delete(session,state)
        # Preserve the original complete source/metadata drift check at the last
        # possible boundary, after the additional credential observations.
        predelete_compare(session/'primary')
        # Recheck target identity after credential/dependency observations.
        immediately_current=repo_info(state['repo'])
        require(immediately_current is not None and immediately_current.get('id')==state['original_repo_id'],
                'original repository changed during final credential gate; refuse delete')
        log(f'Deleting EXACT original repository ID {existing["id"]}',style='yellow bold')
        p=run(['gh','repo','delete',state['repo'],'--yes'],timeout=240,check=False)
        if p.returncode:
            # The server might complete a deletion even if gh exits nonzero.
            require(repo_info(state['repo']) is None,
                    'deletion refused; check gh auth refresh -s delete_repo: '+(p.stderr or '')[-350:])
    require(repo_info(state['repo']) is None,
            'deletion response returned, but repository still exists')
    state['stage']='deleted';save_state(session,state)
    log('Original repository deleted; verified absent',style='yellow')

def create_replacement(session,state):
    if state['stage'] not in ('deleted','create-intent'):return
    if state['stage']=='deleted':
        state['stage']='create-intent';save_state(session,state)
    current=get_new_repo(state,optional=True)
    created_this_call=False
    if current is not None and not state.get('created_new_id'):
        require(False,'recreated repository ID has no journal provenance; '
                'use --adopt-created-repo-id ID after independently verifying creation')
    if current is None:
        created_this_call=True
        state['creation_attempted_at']=now();save_state(session,state)
        if state['topology']=='fork':
            owner,name=state['repo'].split('/',1)
            parent=state['parent_repo']
            args=['gh','repo','fork',parent,'--clone=false','--fork-name',name]
            authenticated=api('user')['login']
            if authenticated.lower()!=owner.lower():args+=['--org',owner]
        else:
            visibility=read_json(session/'primary'/'repo.json')['visibility']
            args=['gh','repo','create',state['repo'],'--'+visibility,'--clone=false']
        log(f'Creating {state["topology"]} repository…')
        p=run(args,timeout=300,check=False)
        if p.returncode:
            # GitHub may have created it even when the CLI failed. Either way,
            # the result has no trustworthy creation receipt, so stop and
            # require exact operator adoption of the independently checked ID.
            raise Refused('creation CLI did not confirm success; inspect the new repository '
                          'and resume with --adopt-created-repo-id EXACT_ID when appropriate')
    for attempt in range(18):
        r=get_new_repo(state,optional=True)
        if r and (state['topology']!='fork' or r.get('default_branch')):break
        time.sleep(2)
    else:raise Refused('new repository not visible yet; resume after GitHub creates it')
    require(created_this_call or state.get('created_new_id')==r['id'],
            'replacement identity could not be authenticated from the journal')
    # Even a successful CLI exit must not silently adopt an unrelated repository
    # created in the name-reuse window. Match the recorded creation time.
    if created_this_call:
        created=dt.datetime.fromisoformat(r['created_at'].replace('Z','+00:00'))
        attempted=dt.datetime.fromisoformat(state['creation_attempted_at'])
        require(created>=attempted-dt.timedelta(seconds=120),
                'newly visible repository predates this recreation attempt')
    state['created_new_id']=r['id']
    if state['topology']=='fork':
        seeded=live_refs(state['repo'])
        require(seeded,'newly created fork has no Git refs yet')
        upstream=live_refs(state['parent_repo'])
        require(all(upstream.get(ref)==sha for ref,sha in seeded.items()),
                'fork has refs differing from upstream before restoration')
        if state.get('fork_seed_refs') is not None:
            require(state['fork_seed_refs']==seeded,
                    'adopted fork seed refs differ from the journal; stop for review')
        else:
            state['fork_seed_refs']=seeded
        if state.get('seed_default_branch') is not None:
            require(state['seed_default_branch']==r.get('default_branch'),
                    'fork default branch changed since creation checkpoint')
        else:
            state['seed_default_branch']=r.get('default_branch')
    # The replacement identity and inherited refs MUST be journaled before any
    # network mutation that can fail. A restart must never create a second repo.
    save_state(session,state)
    suspend_actions_for_restore(session,state)
    state['stage']='created';save_state(session,state)
    log(f'New repository verified: {state["repo"]} ID={r["id"]}',style='green')

def restored_source(session):
    cache=session/'restored-source.git'
    if not cache.exists():
        with tempfile.TemporaryDirectory(prefix='.restore-source-',dir=session) as td:
            work=Path(td)/'source.git'
            run(['git','clone','--mirror',session/'primary'/'source.bundle',work],timeout=2400)
            # Preserve separately captured LFS objects before publishing the cache.
            lfs=session/'primary'/'lfs'/'objects'
            if lfs.is_dir():
                dst=work/'lfs'/'objects'
                dst.mkdir(parents=True,exist_ok=True)
                shutil.copytree(lfs,dst,dirs_exist_ok=True)
            require(refs_from_git(work)==read_json(session/'primary'/'source-refs.json'),
                    'private restore cache source refs inconsistent')
            os.replace(work,cache)
    require(cache.is_dir() and not cache.is_symlink(),'unsafe restored source directory')
    require(refs_from_git(cache)==read_json(session/'primary'/'source-refs.json'),
            'cached restore mirror refs drifted')
    return cache

def expected_ref_mapping(state,backup,existing):
    """Never replace a ref inherited from the upstream fork.

    Conflicting original refs survive at deterministic archive refs. A PR whose
    head was archived cannot be reconstructed with the original head identity.
    """
    saved=read_json(backup/'source-refs.json')
    if state['topology']!='fork':
        return saved.copy(),None
    seed=state.get('fork_seed_refs',existing)
    original_default=read_json(backup/'repo.json').get('default_branch')
    target={}
    default_archive=None
    for ref,sha in saved.items():
        if ref in seed and seed[ref]!=sha:
            kind,short= ('heads',ref[len('refs/heads/'):]) if ref.startswith('refs/heads/') else ('tags',ref[len('refs/tags/'):])
            archive=(f'refs/{kind}/archive/pre-recreate-'
                     f'{state["original_repo_id"]}-{sha[:12]}/{short}')
            require(archive not in target,'colliding archive ref '+archive)
            target[archive]=sha
            if ref=='refs/heads/'+str(original_default):default_archive=archive
        else:
            target[ref]=sha
    recon=state.get('default_reconciliation') or {}
    if recon.get('phase')=='verified':
        require(recon.get('original_ref')=='refs/heads/'+str(original_default) and
                recon.get('original_sha')==saved.get(recon['original_ref']) and
                recon.get('original_archive')==default_archive and
                recon.get('seed_sha')==seed.get(recon['original_ref']),
                'default reconciliation provenance does not match verified backup')
        target[recon['original_ref']]=recon['original_sha']
        target[recon['preserved_ref']]=recon['seed_sha']
    return target,default_archive


def suspend_actions_for_restore(session,state):
    """Disable execution with the only GitHub-supported disabled-policy field.

    `allowed_actions` and `sha_pinning_required` are invalid when enabled=false.
    Do not reconfigure the policy or loosen restrictions during restoration.
    """
    repo=state['repo']
    path=f'repos/{repo}/actions/permissions'
    data=api(path)
    if data.get('enabled') is not False:
        api(path,method='PUT',payload={'enabled':False})
    require(api(path).get('enabled') is False,
            'Actions could not be suspended before Git ref restoration')
    state['actions_suspended']=True
    save_state(session,state)


def apply_registered_workflow_states(session,state):
    """Restore registered workflow enablement only after Actions are enabled.

    Historical registry rows absent from the replacement remain archival evidence,
    not fabricated registrations. Never dispatch workflows in this function.
    """
    path=f'repos/{state["repo"]}/actions/workflows'
    source=read_json(session/'primary'/'actions'/'workflows.json')
    registered={x['path']:x for x in collect(path,key='workflows')}
    for wf in source:
        if wf.get('state')=='deleted' or wf['path'] not in registered:
            continue
        actual=registered[wf['path']]
        wanted=wf.get('state')=='active'
        if (actual.get('state')=='active')!=wanted:
            verb='enable' if wanted else 'disable'
            api(f'{path}/{actual["id"]}/{verb}',method='PUT')
    final={x['path']:x for x in collect(path,key='workflows')}
    require(all((final[w['path']].get('state')=='active')==(w.get('state')=='active')
                for w in source if w.get('state')!='deleted' and w['path'] in final),
            'registered workflow activation state differs from original')


def restore_actions_enabled_after_safety(session,state,*,unresolved):
    """Apply the original policy only at the safe activation boundary.

    For originally disabled Actions, keep it disabled: the REST API does not
    guarantee full restoration of selected policy fields while enabled=false.
    """
    original=read_json(session/'primary'/'actions'/'permissions.json')
    wanted=bool(original.get('enabled',False))
    path=f'repos/{state["repo"]}/actions/permissions'
    current=api(path)
    if unresolved and wanted:
        state['actions_left_disabled_for_manual_recovery']=True
        save_state(session,state)
        require(current.get('enabled') is False,
                'Actions became enabled before unresolved recovery was completed')
        return
    if not wanted:
        require(current.get('enabled') is False,
                'Actions originally disabled but replacement is enabled')
        state['actions_policy_deferred_while_disabled']={
            k:original[k] for k in ('allowed_actions','sha_pinning_required')
            if k in original and current.get(k)!=original[k]}
        state['actions_left_disabled_for_manual_recovery']=False
        save_state(session,state)
        return
    fields=('allowed_actions','sha_pinning_required')
    desired={'enabled':True,**{k:original[k] for k in fields if k in original}}
    try:
        if any(current.get(k)!=v for k,v in desired.items()):
            # Set enabled=true and its restrictive policy in ONE request. Never
            # enable with the replacement's default unrestricted Actions policy.
            api(path,method='PUT',payload=desired)
        require(all(api(path).get(k)==v for k,v in desired.items()),
                'Actions policy mismatch after activation')
        if original.get('allowed_actions')=='selected':
            saved=read_json(session/'primary'/'actions'/'selected-actions.json')
            selected=f'{path}/selected-actions'
            if api(selected)!=saved:
                api(selected,method='PUT',payload=saved)
            require(api(selected)==saved,'selected Actions allowlist mismatch')
        apply_registered_workflow_states(session,state)
    except Exception as exc:
        # Best-effort safe rollback, without pretending to undo any already
        # dispatched external work. Journal the condition even if rollback fails.
        state['actions_left_disabled_for_manual_recovery']=True
        state['actions_activation_failed']=str(exc)[:500]
        save_state(session,state)
        try:
            api(path,method='PUT',payload={'enabled':False})
            require(api(path).get('enabled') is False,
                    'GitHub did not confirm Actions re-suspension')
        except Exception as rollback:
            raise Refused('Actions activation failed AND re-suspension failed; '
                          'URGENT manual intervention: '+str(rollback)) from exc
        raise Refused('Actions activation incomplete; Actions re-suspended: '+str(exc)) from exc
    state.pop('actions_activation_failed',None)
    state['actions_left_disabled_for_manual_recovery']=False
    state['actions_policy_restored']=True
    save_state(session,state)

def restore_refs(session,state):
    if state['stage'] not in ('created','refs-intent'):return
    if state['stage']=='created':state['stage']='refs-intent';save_state(session,state)
    get_new_repo(state)
    require(state.get('actions_suspended'),
            'Actions suspension not journaled; refuse ref push')
    require(api(f'repos/{state["repo"]}/actions/permissions').get('enabled') is False,
            'Actions unexpectedly enabled before ref restoration')
    saved_refs=read_json(session/'primary'/'source-refs.json')
    if not saved_refs:
        require(state['topology']=='standalone','empty fork restoration needs manual review')
        require(live_refs(state['repo'])=={},'new empty repo acquired unexpected refs')
        state['stage']='refs-done';save_state(session,state)
        return
    cache=restored_source(session)
    before=live_refs(state['repo'])
    if state['topology']=='fork':
        for ref,sha in state.get('fork_seed_refs',{}).items():
            require(before.get(ref)==sha,
                    'upstream-seeded fork ref changed or was removed: '+ref)
        known=set(state.get('fork_seed_refs',{}))
        oldref=read_json(session/'primary'/'source-refs.json')
        archives=(f'refs/heads/archive/pre-recreate-{state["original_repo_id"]}-',
                  f'refs/tags/archive/pre-recreate-{state["original_repo_id"]}-')
        require(all(ref in known or ref in oldref or ref.startswith(archives)
                    for ref in before), 'unexpected new refs appeared during restore')
    if state['topology']=='standalone':
        require(set(before)<=set(saved_refs),
                'unexpected refs appeared in the replacement; refuse to overwrite or ignore')
    expected,archive=expected_ref_mapping(state,session/'primary',before)
    for ref,sha in sorted(expected.items()):
        actual=live_refs(state['repo']).get(ref)
        if actual==sha:continue
        require(actual is None,f'non-fast-forward/conflicting existing Git ref {ref}')
        log(f'Restoring Git ref {ref}')
        run(['git','--git-dir',cache,'push',remote_url(state['repo']),
             f'{sha}:{ref}'],timeout=900)
        require(live_refs(state['repo']).get(ref)==sha,
                f'push succeeded but ref mismatch {ref}')
    lfs=session/'primary'/'lfs'/'objects'
    if lfs.exists():
        log('Pushing captured Git LFS objects…')
        existing_remotes=run(['git','--git-dir',cache,'remote']).stdout.splitlines()
        if 'repo-rebuild-target' not in existing_remotes:
            run(['git','--git-dir',cache,'remote','add','repo-rebuild-target',remote_url(state['repo'])])
        else:
            found=run(['git','--git-dir',cache,'remote','get-url','repo-rebuild-target']).stdout.strip()
            require(found==remote_url(state['repo']),'restore LFS remote changed')
        run(['git','--git-dir',cache,'lfs','push','--all','repo-rebuild-target'],timeout=5400)
    result=live_refs(state['repo'])
    require(all(result.get(ref)==sha for ref,sha in expected.items()),
            'restored refs do not match backup')
    if state['topology']=='fork':
        require(all(result.get(ref)==sha for ref,sha in state['fork_seed_refs'].items()),
                'upstream-seeded refs not preserved')
    intended=read_json(session/'primary'/'repo.json').get('default_branch')
    current=get_new_repo(state)
    if intended and current.get('default_branch')!=intended:
        # The original default ref might conflict with the new upstream. Keep
        # the new upstream default intact instead of claiming to restore it.
        original_sha=read_json(session/'primary'/'source-refs.json').get('refs/heads/'+intended)
        if result.get('refs/heads/'+intended)==original_sha:
            api(f'repos/{state["repo"]}',method='PATCH',payload={'default_branch':intended})
            require(get_new_repo(state).get('default_branch')==intended,
                    'restored default branch selection failed')
        else:
            require(state['topology']=='fork' and archive is not None,
                    'unrestored standalone default ref')
            log('Original default ref conflicted with upstream; preserved as '+archive,
                style='yellow')
    state['master_archive']=archive
    state['stage']='refs-done';save_state(session,state)
    log('Git refs restored without force push',style='green')

def sentinel(session,kind,old):
    return f'<!-- repo-rebuild session={session.name} kind={kind} original={old} -->'

def body_with_source(session,kind,number,body):
    return ((body or '') + '\n\n---\n' +
            'Archived from the original repository before recreation. ' +
            'Original discussion, review metadata, Actions history and artifacts are in the local verified backup.\n' +
            sentinel(session,kind,number) + '\n')

def find_import(repo,session,kind,old):
    # Recover from the crash window between HTTP POST and local state commit.
    mark=sentinel(session,kind,old)
    matches=[x for x in collect(f'repos/{repo}/issues?state=all')
             if mark in (x.get('body') or '')]
    require(len(matches)<=1,f'duplicate recovery marker for {kind} #{old}')
    if not matches:return None
    is_pr='pull_request' in matches[0]
    require(is_pr==(kind=='pr'),'restored object type conflict')
    return matches[0]

def mapped_label(state,name):
    return state.get('label_aliases',{}).get(name,name)


def archive_label_alias(state,old):
    fingerprint=hashlib.sha256(json_bytes({
        'name':old['name'],'color':old['color'],
        'description':old.get('description') or ''})).hexdigest()[:12]
    return f'repo-rebuild-{fingerprint}-'+old['name'][:22]

def restore_labels_and_milestones(session,state):
    if state['stage'] not in ('refs-done','metadata-intent'):return
    if state['stage']=='refs-done':state['stage']='metadata-intent';save_state(session,state)
    repo=state['repo']
    # If issues were disabled, temporarily enable them for importer-created issues.
    if any('pull_request' not in x for x in
           read_json(session/'primary'/'metadata'/'issues.json')):
        current=get_new_repo(state)
        if not current.get('has_issues',True):
            api(f'repos/{repo}',method='PATCH',payload={'has_issues':True})
    existing={x['name']:x for x in collect(f'repos/{repo}/labels')}
    for x in read_json(session/'primary'/'metadata'/'labels.json'):
        name=x['name']
        assigned=mapped_label(state,name)
        if assigned==name and name in existing and not (
                existing[name]['color'].lower()==x['color'].lower() and
                (existing[name].get('description') or '')==(x.get('description') or '')):
            assigned=archive_label_alias(state,x)
            state.setdefault('label_aliases',{})[name]=assigned
            save_state(session,state)
            log(f'Preserving upstream label {name!r}; original restored as {assigned!r}',
                style='yellow')
        if assigned in existing:
            require(existing[assigned]['color'].lower()==x['color'].lower() and
                    (existing[assigned].get('description') or '')==(x.get('description') or ''),
                    f'archived-label conflict: {assigned}')
        else:
            created=api(f'repos/{repo}/labels',method='POST',payload={
                'name':assigned,'color':x['color'],'description':x.get('description') or ''})
            existing[assigned]=created
        state['labels'][name]=True;save_state(session,state)
    existing_ms=collect(f'repos/{repo}/milestones?state=all')
    for old in read_json(session/'primary'/'metadata'/'milestones.json'):
        num=str(old['number'])
        if num in state['milestones']:
            require(any(x['number']==state['milestones'][num] for x in existing_ms),
                    f'milestone disappeared: {num}')
            continue
        marker=f'[repo-rebuild original milestone #{num}]'
        found=[x for x in existing_ms if marker in (x.get('description') or '')]
        require(len(found)<=1,f'duplicate milestone: {num}')
        if found:m=found[0]
        else:
            m=api(f'repos/{repo}/milestones',method='POST',payload={
                'title':old['title'],
                'description':(old.get('description') or '')+'\n'+marker,
                'due_on':old.get('due_on')})
            existing_ms.append(m)
        if old['state']=='closed' and m['state']!='closed':
            api(f'repos/{repo}/milestones/{m["number"]}',method='PATCH',payload={'state':'closed'})
        state['milestones'][num]=m['number'];save_state(session,state)
    state['stage']='metadata-done';save_state(session,state)

def verify_import(repo,kind,created,old,*,accept_base_drift=False,allow_intermediate_state=False):
    require(created['title']==old['title'],f'{kind} title mismatch')
    if kind=='pr':
        require(created['head']['ref']==old['head']['ref'] and
                created['head']['sha']==old['head']['sha'] and
                created['base']['ref']==old['base']['ref'] and
                bool(created.get('draft'))==bool(old.get('draft')),
                'restored PR head SHA/base/draft mismatch')
        if old['base'].get('sha') and not accept_base_drift:
            require(created['base']['sha']==old['base']['sha'],
                    'restored PR base SHA mismatch')
    require(allow_intermediate_state or created['state']==old['state'],
            f'{kind} state mismatch')


def verify_imported_issue_details(repo,new_number,old,session,kind,state):
    actual=api(f'repos/{repo}/issues/{new_number}')
    marker=sentinel(session,kind,old['number'])
    require(marker in (actual.get('body') or ''),'restored provenance marker missing')
    require((actual.get('body') or '').startswith((old.get('body') or '')+'\n\n---\n'),
            'restored original issue/PR description changed')
    require(actual['title']==old['title'] and actual['state']==old['state'],
            'restored issue/PR title or state changed')
    expected_labels={mapped_label(state,x['name']) for x in old.get('labels') or []}
    require({x['name'] for x in actual.get('labels') or []}==expected_labels,
            'restored issue/PR labels mismatch')
    expected_milestone=(state['milestones'].get(str(old['milestone']['number']))
                        if old.get('milestone') else None)
    actual_milestone=(actual.get('milestone') or {}).get('number')
    require(actual_milestone==expected_milestone,'restored milestone mismatch')
    return actual

def restore_objects(session,state):
    if state['stage'] not in ('metadata-done','objects-intent'):return
    if state['stage']=='metadata-done':state['stage']='objects-intent';save_state(session,state)
    repo=state['repo']
    original=read_json(session/'primary'/'repo.json')
    current=get_new_repo(state)
    old_default=original.get('default_branch')
    if state['topology']=='fork' and old_default and original['id']!=current['id']:
        old_sha=read_json(session/'primary'/'source-refs.json').get('refs/heads/'+old_default)
        new_sha=live_refs(repo).get('refs/heads/'+old_default)
        if old_sha and old_sha!=new_sha and not state.get('accepted_base_drift'):
            raise Refused('upstream default branch changed: original base '+str(old_sha)+
                ' new base '+str(new_sha)+'; explicitly accept with --accept-base-drift')
    issues=read_json(session/'primary'/'metadata'/'issues.json')
    prs={x['number']:x for x in read_json(session/'primary'/'metadata'/'pulls.json')}
    for old in sorted(issues,key=lambda x:x['number']):
        n=old['number'];kind='pr' if 'pull_request' in old else 'issue'
        key=f'{kind}:{n}'
        # Look for a provenance-marked object before making any archival
        # decision.  A previous HTTP POST may have succeeded just before a
        # process crash; never lose or overwrite that recovered identity.
        existing=find_import(repo,session,kind,n)
        prior=state['objects'].get(key, {})
        if prior.get('archived_only'):
            require(kind=='pr' and not prior.get('number') and existing is None,
                    f'archived-only journal conflicts with a live import: {key}')
            continue
        if existing is None and prior.get('number') is not None:
            raise Refused(f'journal says {key} restored but no GitHub object found')
        if kind=='pr' and existing is None:
            p=prs[n]
            # A merged historical PR cannot be replayed as an actual merge.
            # Its head commit is commonly already part of the base history,
            # causing GitHub POST /pulls to return HTTP 422. Preserve the
            # immutable archived PR/reviews instead of fabricating a new PR.
            if p.get('merged') or p.get('merged_at'):
                state['objects'][key]={
                    'archived_only':True,
                    'reason':'merged-history-cannot-be-recreated',
                }
                save_state(session,state)
                log(f'Original merged PR #{n} archived-only (merge history preserved in backup)',
                    style='yellow')
                continue
            head_ref='refs/heads/'+p['head']['ref']
            saved_head=read_json(session/'primary'/'source-refs.json').get(head_ref)
            current_head=live_refs(repo).get(head_ref)
            unsupported=((p['head'].get('repo') or {}).get('full_name') or '').lower()!=repo.lower()
            unsupported=unsupported or not saved_head or current_head!=saved_head
            if unsupported:
                state['objects'][key]={
                    'archived_only':True,
                    'reason':'missing/external/colliding original head',
                }
                save_state(session,state)
                log(f'External, deleted, or upstream-conflicting PR #{n} archived-only',
                    style='yellow')
                continue
        if existing:
            require(existing['title']==old['title'],'imported object title changed')
            prior=state['objects'].get(key, {})
            require(not prior or prior.get('number')==existing['number'],
                    'journaled imported object identity changed: '+key)
            if kind=='pr':
                detail=api(f'repos/{repo}/pulls/{existing["number"]}')
                verify_import(repo,kind,detail,prs[n],
                              accept_base_drift=bool(state.get('accepted_base_drift')),
                              allow_intermediate_state=True)
            state['objects'][key]={'number':existing['number']}
            save_state(session,state)
        else:
            if key in state['objects']:
                raise Refused(f'journal says {key} restored but no GitHub object found')
            if kind=='pr':
                p=prs[n]
                head=p['head']['ref'];base=p['base']['ref']
                refs=live_refs(repo)
                expected=read_json(session/'primary'/'source-refs.json')
                require(refs.get('refs/heads/'+head)==expected.get('refs/heads/'+head),
                        f'PR #{n} missing expected head')
                require('refs/heads/'+base in refs,f'PR #{n} missing base')
                if refs['refs/heads/'+base]!=p['base']['sha']:
                    require(state.get('accepted_base_drift'),
                            f'PR #{n} base SHA differs from backup; '
                            'explicitly accept with --accept-base-drift')
                    state.setdefault('accepted_pr_base_drifts',{})[str(n)]={
                        'previous':p['base']['sha'],'restored_base':refs['refs/heads/'+base]}
                    save_state(session,state)
                payload={'title':p['title'],'body':body_with_source(session,'pr',n,p.get('body')),
                         'head':head,'base':base,'draft':bool(p.get('draft'))}
                try:
                    new=api(f'repos/{repo}/pulls',method='POST',payload=payload)
                except Refused as exc:
                    raise Refused(f'original unmerged PR #{n} creation refused: {exc}; '
                                  'journal unchanged; inspect the GitHub validation error '
                                  'before retrying') from exc
            else:
                payload={'title':old['title'],
                    'body':body_with_source(session,'issue',n,old.get('body')),
                    'labels':[mapped_label(state,x['name']) for x in old.get('labels',[])],
                    'milestone':state['milestones'].get(str(old['milestone']['number']))
                                 if old.get('milestone') else None}
                new=api(f'repos/{repo}/issues',method='POST',payload=payload)
            state['objects'][key]={'number':new['number']};save_state(session,state)
            log(f'Created {kind} old #{n} → new #{new["number"]}',style='green')
        new_number=state['objects'][key]['number']
        if kind=='pr':
            # Labels and milestones are issue-level fields even for PRs.
            target_labels=[mapped_label(state,x['name']) for x in old.get('labels') or []]
            target_ms=(state['milestones'].get(str(old['milestone']['number']))
                       if old.get('milestone') else None)
            current_issue=api(f'repos/{repo}/issues/{new_number}')
            current_labels={x['name'] for x in current_issue.get('labels') or []}
            current_ms=(current_issue.get('milestone') or {}).get('number')
            if current_labels!=set(target_labels) or current_ms!=target_ms:
                api(f'repos/{repo}/issues/{new_number}',method='PATCH',payload={
                    'labels':target_labels,'milestone':target_ms})
        if kind=='issue' and old['state']=='closed':
            current_issue=api(f'repos/{repo}/issues/{new_number}')
            if current_issue['state']!='closed':
                api(f'repos/{repo}/issues/{new_number}',method='PATCH',payload={'state':'closed'})
        if kind=='pr' and prs[n]['state']=='closed':
            # Closed PRs are recreated as closed, never merged.
            p=api(f'repos/{repo}/pulls/{new_number}')
            if p['state']!='closed':
                api(f'repos/{repo}/pulls/{new_number}',method='PATCH',payload={'state':'closed'})
        verify_imported_issue_details(repo,new_number,old,session,kind,state)
        if kind=='pr':
            verify_import(repo,kind,api(f'repos/{repo}/pulls/{new_number}'),prs[n],
                          accept_base_drift=bool(state.get('accepted_base_drift')))
    state['stage']='objects-done';save_state(session,state)

# Releases: metadata and assets are preserved in the backup. GitHub release asset
# IDs/URLs cannot be preserved. Upload of archived assets is optional and fail-closed.
def attest_restored_asset(repo,remote,local):
    require(remote.get('size')==local.stat().st_size,'restored release asset size mismatch')
    actual=sha256(local)
    digest=remote.get('digest')
    if digest:
        require(digest=='sha256:'+actual,'restored release asset SHA-256 mismatch')
        return
    # Older GitHub responses can omit `digest`. Never accept size alone.
    log('GitHub omitted release asset digest; verifying remote bytes',style='yellow')
    with tempfile.TemporaryDirectory(prefix='repo-rebuild-asset-verify-') as td:
        downloaded=Path(td)/'asset.bin'
        token=run(['gh','auth','token']).stdout.strip()
        require(token,'authenticated GitHub token missing')
        download_api_binary(f'repos/{repo}/releases/assets/{remote["id"]}',downloaded,
                            token=token,max_bytes=local.stat().st_size+1024*1024,
                            expected_size=local.stat().st_size,mime='application/octet-stream')
        require(sha256(downloaded)==actual,'restored release asset remote-byte mismatch')

def restore_releases(session,state):
    if state['stage'] not in ('objects-done','releases-intent'):return
    if state['stage']=='objects-done':state['stage']='releases-intent';save_state(session,state)
    repo=state['repo']
    originals=read_json(session/'primary'/'metadata'/'releases.json')
    existing={x['tag_name']:x for x in collect(f'repos/{repo}/releases')}
    original_refs=read_json(session/'primary'/'source-refs.json')
    expected_refs,_=expected_ref_mapping(state,session/'primary',live_refs(repo))
    for rel in originals:
        tag=rel['tag_name']
        require(tag,'release missing tag')
        original_tag=original_refs.get('refs/tags/'+tag)
        if not original_tag or expected_refs.get('refs/tags/'+tag)!=original_tag:
            log('Release original tag conflicted with upstream or is missing: '+tag,
                style='yellow')
            state.setdefault('archived_only_releases',{})[tag]=(
                'original tag unavailable at its original name')
            save_state(session,state)
            continue
        if tag not in existing:
            opts=['gh','release','create',tag,'--repo',repo,'--verify-tag',
                  '--title',rel.get('name') or tag]
            if rel.get('draft'):opts+=['--draft']
            if rel.get('prerelease'):opts+=['--prerelease']
            # Avoid shell quoting issues by using a protected, ephemeral notes file.
            with tempfile.TemporaryDirectory(prefix='repo-rebuild-notes-') as td:
                notes=Path(td)/'notes.txt';notes.write_text(rel.get('body') or '')
                run(opts+['--notes-file',notes],timeout=300)
            existing={x['tag_name']:x for x in collect(f'repos/{repo}/releases')}
            require(tag in existing,f'new release {tag} not found')
        current=existing[tag]
        require((current.get('name') or tag)==(rel.get('name') or tag),
                f'release name mismatch {tag}')
        old_assets=rel.get('assets') or []
        current_assets={x['name']:x for x in current.get('assets') or []}
        for asset in old_assets:
            file=session/'primary'/'release-assets'/f'{asset["id"]}.bin'
            if not file.is_file():
                gaps=read_json(session/'primary'/'release-asset-coverage.json')['gaps']
                require(any(g['asset_id']==asset['id'] for g in gaps),
                        f'uncatalogued missing release asset {asset["id"]}')
                log(f'Archived-only release asset {asset["id"]}: unavailable in original',style='yellow')
                state.setdefault('unrestored_release_assets',[]).append(asset['id'])
                save_state(session,state)
                continue
            if asset['name'] in current_assets:
                attest_restored_asset(repo,current_assets[asset['name']],file)
                state.setdefault('release_asset_ids',{})[str(asset['id'])]=current_assets[asset['name']]['id']
                save_state(session,state)
                continue
            # gh release upload never overwrites here (no --clobber).
            with tempfile.TemporaryDirectory(prefix='repo-rebuild-upload-') as td:
                dst=Path(td)/asset['name']
                require('/' not in asset['name'] and '\\' not in asset['name'] and
                        asset['name'] not in ('.','..'),'unsafe release asset name')
                shutil.copy2(file,dst)
                run(['gh','release','upload',tag,dst,'--repo',repo],timeout=1800)
            current=api(f'repos/{repo}/releases/tags/{urllib.parse.quote(tag,safe="")}')
            current_assets={x['name']:x for x in current.get('assets') or []}
            require(asset['name'] in current_assets,'release asset not uploaded')
            attest_restored_asset(repo,current_assets[asset['name']],file)
            state.setdefault('release_asset_ids',{})[str(asset['id'])]=current_assets[asset['name']]['id']
            save_state(session,state)
        state['releases'][tag]=True;save_state(session,state)
    state['stage']='releases-done';save_state(session,state)

def restore_actions_permissions(session,state):
    if state['stage'] not in ('releases-done','actions-intent'):return
    if state['stage']=='releases-done':
        state['stage']='actions-intent';save_state(session,state)
    repo=state['repo']
    current=api(f'repos/{repo}/actions/permissions')
    require(state.get('actions_suspended'),'Actions suspension checkpoint missing')
    require(current.get('enabled') is False,
            'Actions unexpectedly enabled during workflow registration audit')
    # GitHub refuses PUT allowed_actions/sha_pinning_required when Actions are
    # disabled; it also requires enabled on the permissions PUT. Defer all
    # policy writes until post-configuration activation or manual attestation.
    state['actions_policy_deferred_until_safe_activation']=True
    save_state(session,state)
    original_wf=read_json(session/'primary'/'actions'/'workflows.json')
    if original_wf:
        current_wf=collect(f'repos/{repo}/actions/workflows',key='workflows')
        by_path={w['path']:w for w in current_wf}
        for wf in original_wf:
            path=wf['path']
            if wf.get('state')=='deleted':
                state.setdefault('archived_only_workflows',[]).append(path)
                continue
            if path not in by_path:
                # Historical GitHub workflow registrations may outlive their workflow
                # source. Verify *absence* at original default HEAD; do not invent source.
                _rr_saved = read_json(session/'primary'/'repo.json')
                _rr_default = _rr_saved.get('default_branch')
                _rr_source_sha = read_json(session/'primary'/'source-refs.json').get(
                    'refs/heads/' + str(_rr_default))
                require(isinstance(_rr_source_sha, str) and len(_rr_source_sha) == 40,
                        'original default HEAD unavailable: ' + path)
                require(isinstance(path, str) and path.startswith('.github/workflows/') and
                        path.endswith(('.yml', '.yaml')) and
                        '..' not in path.split('/') and '\\' not in path and
                        not path.startswith('/'),
                        'unsafe historical workflow path: ' + str(path))
                _rr_mirror = restored_source(session)
                _rr_tree = run(['git', '--git-dir', _rr_mirror, 'ls-tree',
                                _rr_source_sha, '--', path]).stdout.splitlines()
                require(len(_rr_tree) <= 1,
                        'ambiguous source proof for historical workflow: ' + path)
                _rr_original_blob = None
                if _rr_tree:
                    require(('\t' + path) in _rr_tree[0],
                            'unexpected original workflow tree path: ' + path)
                    _rr_fields = _rr_tree[0].split('\t', 1)[0].split()
                    require(len(_rr_fields) == 3 and _rr_fields[0] in ('100644', '100755') and
                            _rr_fields[1] == 'blob' and len(_rr_fields[2]) == 40,
                            'invalid original workflow Git blob: ' + path)
                    _rr_original_blob = _rr_fields[2]
                _rr_current_branch = get_new_repo(state).get('default_branch')
                require(isinstance(_rr_current_branch, str) and _rr_current_branch,
                        'replacement fork has no default branch')
                import urllib.parse as _rr_urllib_parse
                _rr_live_file = api('repos/' + repo + '/contents/' +
                                   _rr_urllib_parse.quote(path, safe='/') + '?ref=' +
                                   _rr_urllib_parse.quote(_rr_current_branch, safe=''),
                                   missing_ok=True)
                if _rr_live_file is None:
                    _rr_new_blob = None
                else:
                    require(isinstance(_rr_live_file, dict) and
                            _rr_live_file.get('type') == 'file' and
                            _rr_live_file.get('path') == path and
                            isinstance(_rr_live_file.get('sha'), str) and
                            len(_rr_live_file['sha']) == 40,
                            'unexpected new-fork workflow content response: ' + path)
                    _rr_new_blob = _rr_live_file['sha']
                if _rr_original_blob is None:
                    _rr_reason = 'historical-registry-entry; absent-at-original-default-HEAD'
                elif _rr_new_blob is None:
                    _rr_reason = 'absent-on-new-default; original-in-verified-Git-backup'
                elif _rr_new_blob == _rr_original_blob:
                    _rr_reason = 'source-present-identical-but-not-registered'
                else:
                    _rr_reason = 'new-default-source-differs; original-in-verified-Git-backup'
                state.setdefault('unregistered_workflows', {})[path] = {
                    'original_state': wf.get('state'),
                    'original_git_blob': _rr_original_blob,
                    'original_default_sha': _rr_source_sha,
                    'original_default_source_present': _rr_original_blob is not None,
                    'new_default_branch': _rr_current_branch,
                    'new_default_git_blob': _rr_new_blob,
                    'source_provenance': ('verified-original-default-Git-blob'
                                          if _rr_original_blob is not None else
                                          'historical-registry-metadata-only'),
                    'source_branches': [
                        _ref[len('refs/heads/'):] for _ref,_sha in sorted(
                            read_json(session/'primary'/'source-refs.json').items())
                        if _ref.startswith('refs/heads/') and
                        run(['git','--git-dir',_rr_mirror,'ls-tree',_sha,'--',path],
                            timeout=30).stdout.strip()],
                    'reason': _rr_reason,
                    'registry_evidence': 'source-bound',
                }
                save_state(session, state)
                log('ACTIONS REGISTRATION GAP (not restored): ' + path +
                    ' [' + _rr_reason + ']', style='yellow')
                continue
            state.setdefault('unregistered_workflows', {}).pop(path, None)
            old_active=wf.get('state')=='active'
            new_active=by_path[path].get('state')=='active'
            if old_active!=new_active:
                state.setdefault('deferred_workflow_states',{})[path] = (
                    'active' if old_active else 'disabled_manually')
                save_state(session,state)
        # Workflow activation is also deferred until Actions policy can be
        # restored without allowing execution with unprovisioned credentials.
    # Archived artifacts and run history remain local; this reconfigures only
    # permissions, never fabricates runs or uploads historical artifacts.
    state['stage']='actions-done';save_state(session,state)

def verify_recovery_configuration(session,state,*,require_all_secrets=False):
    """Read-only second attestation of configuration after all replay stages."""
    config=read_json(session/'primary'/'configuration.json')
    repo=state['repo'];base='repos/'+repo
    current=api(base+'/actions/permissions/workflow')
    desired=config['workflow_permissions']
    for name in ('default_workflow_permissions','can_approve_pull_request_reviews'):
        if name in desired:
            require(current.get(name)==desired[name],
                    'GITHUB_TOKEN workflow permissions drifted: '+name)
    for var in config['variables']:
        current=api(base+'/actions/variables/'+endpoint_env(var['name']))
        require(current.get('value')==var['value'],
                'repository variable drift: '+var['name'])
    available=[]
    for app in ('actions','dependabot','codespaces'):
        if app=='codespaces' and config.get('codespaces_inventory')!='available':continue
        endpoint=base+('/codespaces/secrets' if app=='codespaces' else '/'+app+'/secrets')
        available.extend((app,'',item['name']) for item in collect(endpoint,key='secrets'))
    for name,record in config['environments'].items():
        uri=base+'/environments/'+endpoint_env(name)
        present=api(uri,missing_ok=True)
        require(present is not None and present.get('name')==name,
                'environment missing: '+name)
        verify_environment_policy(record['definition'],present)
        for var in record['variables']:
            got=api(uri+'/variables/'+endpoint_env(var['name']),missing_ok=True)
            require(got is not None and got.get('value')==var['value'],
                    'environment variable drift: '+name+'/'+var['name'])
        available.extend(('environment',name,item['name']) for item in collect(uri+'/secrets',key='secrets'))
        saved={(x['name'],x['type']) for x in record['branch_policies']}
        if saved:
            got={(x['name'],x['type']) for x in collect(uri+'/deployment-branch-policies',key='branch_policies')}
            require(saved<=got,'environment deployment branches drifted: '+name)
    known=set(available)
    missing={(x['app'],x['environment'],x['name']) for x in state.get('missing_secret_names',[])}
    for app,env,name in safety.secret_requirements(config):
        if (app,env,name) in missing and not require_all_secrets:continue
        require((app,env,name) in known,'secret disappeared after provisioning: '+app+'/'+env+'/'+name)
    current_keys=collect(base+'/keys')
    for original in config.get('deploy_keys',[]):
        require(any(x['key']==original['key'] and bool(x['read_only'])==bool(original['read_only'])
                    for x in current_keys),'deploy key missing after restoration')
    for name,original in config.get('branch_protections',{}).items():
        current=api(base+'/branches/'+endpoint_env(name)+'/protection',missing_ok=True)
        require(current is not None,'branch protection missing: '+name)
        verify_protection(original,current)
    for old_id,new_id in state.get('ruleset_ids',{}).items():
        require(api(base+'/rulesets/'+str(new_id),missing_ok=True) is not None,
                'restored ruleset disappeared: '+old_id)
    org=config.get('organization',{})
    if org.get('status')=='verified':
        owner=repo.split('/',1)[0]
        for x in org['selected']:
            endpoint={'actions-secrets':'actions/secrets','dependabot-secrets':'dependabot/secrets',
                      'codespaces-secrets':'codespaces/secrets','actions-variables':'actions/variables'}[x['kind']]
            selected=collect('orgs/'+owner+'/'+endpoint+'/'+endpoint_env(x['name'])+'/repositories',
                             key='repositories')
            require(any(int(v['id'])==int(state['created_new_id']) for v in selected),
                    'organization selected binding lost: '+x['name'])


def final_verify(session,state):
    r=get_new_repo(state)
    verify_recovery_configuration(session,state)
    b=session/'primary'
    saved=read_json(b/'source-refs.json')
    live=live_refs(state['repo'])
    expected,archive=expected_ref_mapping(state,b,live)
    require(all(live.get(ref)==sha for ref,sha in expected.items()),
            'final verification Git ref mismatch')
    if state['topology']=='fork':
        require(all(live.get(ref)==sha for ref,sha in state['fork_seed_refs'].items()),
                'final verification upstream-seeded ref drift')
    originals=read_json(b/'metadata'/'issues.json')
    original_prs={x['number']:x for x in read_json(b/'metadata'/'pulls.json')}
    for x in originals:
        kind='pr' if 'pull_request' in x else 'issue'
        found=find_import(state['repo'],session,kind,x['number'])
        key=f'{kind}:{x["number"]}'
        if state['objects'].get(key,{}).get('archived_only'):
            require(kind=='pr','only PRs can be archived-only')
            continue
        require(found is not None,'final verification missing imported '+key)
        verify_imported_issue_details(state['repo'],found['number'],x,session,kind,state)
        if kind=='pr':
            detail=api(f'repos/{state["repo"]}/pulls/{found["number"]}')
            verify_import(state['repo'],kind,detail,original_prs[x['number']],
                          accept_base_drift=bool(state.get('accepted_base_drift')))
    for rel in read_json(b/'metadata'/'releases.json'):
        if rel['tag_name'] in state.get('archived_only_releases',{}):continue
        current_release=api(f'repos/{state["repo"]}/releases/tags/{urllib.parse.quote(rel["tag_name"],safe="")}',
                            missing_ok=True)
        require(current_release is not None,'release missing '+rel['tag_name'])
        require((current_release.get('name') or rel['tag_name'])==(rel.get('name') or rel['tag_name']) and
                (current_release.get('body') or '')==(rel.get('body') or '') and
                bool(current_release.get('draft'))==bool(rel.get('draft')) and
                bool(current_release.get('prerelease'))==bool(rel.get('prerelease')),
                'release metadata drift '+rel['tag_name'])
        actual_assets={a['name']:a for a in current_release.get('assets') or []}
        for asset in rel.get('assets') or []:
            original_asset=b/'release-assets'/f'{asset["id"]}.bin'
            if not original_asset.exists():continue  # explicitly archived gap
            require(asset['name'] in actual_assets and
                    actual_assets[asset['name']]['size']==original_asset.stat().st_size,
                    'release asset restore missing/mismatched '+asset['name'])
            digest=actual_assets[asset['name']].get('digest')
            if digest:require(digest=='sha256:'+sha256(original_asset),
                              'release asset restored digest changed '+asset['name'])
    original=read_json(b/'repo.json')
    # Restore safe repository display settings. Secrets, protections and workflow
    # histories are explicitly NOT copied.
    settings={'description':original.get('description') or '',
              'homepage':original.get('homepage') or '',
              'has_issues':bool(original.get('has_issues',True)),
              'has_wiki':bool(original.get('has_wiki',True)),
              'has_projects':bool(original.get('has_projects',True))}
    api(f'repos/{state["repo"]}',method='PATCH',payload=settings)
    new_workflows=collect(f'repos/{state["repo"]}/actions/workflows',key='workflows')
    deps=read_json(b/'dependency-audit.json')
    migrations=safety.migration_plan(deps,state['objects'],state['original_repo_id'],r['id'],
                                  read_json(b/'actions'/'workflows.json'),new_workflows,
                                  release_asset_ids=state.get('release_asset_ids',{}),
                                  ruleset_ids=state.get('ruleset_ids',{}))
    write_json(session/'migration-plan.json',migrations)
    write_json(session/'report.json',{ 'repo':state['repo'],'original_id':state['original_repo_id'],
       'new_id':r['id'],'topology':state['topology'],'saved_refs':len(saved),
       'restored_objects':state['objects'],'releases':len(state['releases']),
       'actions_artifacts_archived':read_json(b/'actions'/'coverage.json'),
       'history_recreated_on_github':False,
       'secret_names_provisioned':state.get('provisioned_secrets',[]),
       'secret_values_verified':False,
       'secret_names_missing':state.get('missing_secret_names',[]),
       'organization_selected_bindings_rebound':state.get('org_rebound',[]),
       'manual_recovery_requirements':state.get('manual_obligations',[]),
       'dependency_audit':safety.status_counts(deps),
       'dependency_migration_plan':'migration-plan.json',
       'dependency_scan_gaps':deps.get('scan_gaps',[]),
       'actions_policy_deferred_while_disabled':state.get('actions_policy_deferred_while_disabled',{}),
       'ci_execution':'not-performed; run appropriate required checks and validate hosted HEAD',
       'repo_settings_applied':'basic display settings only',
       'upstream_seed_refs_preserved':len(state.get('fork_seed_refs',{})),
       'conflicting_original_default_archived_at':state.get('master_archive'),
       'unrestored_release_assets':sorted(set(state.get('unrestored_release_assets',[]))),
       'archived_only_releases':state.get('archived_only_releases',{}),
       'aliased_labels_due_to_seed_conflicts':state.get('label_aliases',{}),
       'archived_only_pull_requests':sorted(k for k,v in state['objects'].items()
                                            if v.get('archived_only')),
       'explicitly_accepted_pr_base_drifts':state.get('accepted_pr_base_drifts',{}),
       'completed_at':now(),
       'limitations':RISK_NOTE})
    if state.get('unregistered_workflows'):
        _rr_report = read_json(session/'report.json')
        _rr_report['actions_workflows_not_registered'] = state['unregistered_workflows']
        _rr_report['actions_workflow_registration_complete'] = False
        write_json(session/'report.json', _rr_report)
        log('ACTION REQUIRED: some Actions workflows are not registered; see report.json', style='yellow')
    from . import credentials
    credential_acceptance=credentials.post_restore(session,state,apply_apps=False,record=True)
    pending_manual=list(state.get('manual_obligations',[]))
    org_selected=read_json(b/'configuration.json').get('organization',{}).get('selected',[])
    if org_selected and len(set(state.get('org_rebound',[])))>=len(org_selected):
        pending_manual=[x for x in pending_manual if not x.startswith(
            'selected organization credentials/variables need new repository-ID reauthorization')]
    saved_actions_policy=read_json(b/'actions'/'permissions.json')
    current_actions_policy=api(f'repos/{state["repo"]}/actions/permissions')
    if saved_actions_policy.get('enabled') is False:
        deferred={key:saved_actions_policy[key]
                  for key in ('allowed_actions','sha_pinning_required')
                  if key in saved_actions_policy and
                  current_actions_policy.get(key)!=saved_actions_policy[key]}
        state['actions_policy_deferred_while_disabled']=deferred
        if deferred:
            pending_manual.append('originally disabled Actions policy fields differ; '
                                  'cannot safely restore them while disabled')
    if credential_acceptance['status']!='verified':
        pending_manual.append('credential authorization unverified; inspect credential-acceptance.json')
    unresolved=bool(state.get('unregistered_workflows') or
                    state.get('missing_secret_names') or pending_manual or
                    deps.get('scan_gaps') or migrations.get('review_required') or
                    state.get('archived_only_releases') or
                    any(v.get('archived_only') for v in state['objects'].values()))
    restore_actions_enabled_after_safety(session,state,unresolved=unresolved)
    report=read_json(session/'report.json')
    report['manual_recovery_requirements']=pending_manual
    report['credential_recovery']=credential_acceptance
    report['actions_left_disabled_for_manual_recovery']=bool(
        state.get('actions_left_disabled_for_manual_recovery'))
    report['actions_policy_deferred_while_disabled']=state.get(
        'actions_policy_deferred_while_disabled',{})
    report['recovery_classification']=('RESTORED_WITH_ACTION_REQUIRED' if unresolved else
                                       'RESTORED_NOT_OPERATIONALLY_TESTED')
    report['fully_operationally_verified']=False
    write_json(session/'report.json',report)
    state['stage']='done-with-action-required' if unresolved else DONE
    save_state(session,state)
    log(report['recovery_classification'],style='yellow')
    render_status(session,state)

def resume(session,*,ack_delete=None,accept_base_drift=False,
           secrets_vault=None,age_identity=None,adopt_created_repo_id=None):
    with locked(session):
        state=load_state(session)
        if state['stage'] in (DONE,'done-with-action-required','done-manual-attested'):
            render_status(session,state);return
        require(state['stage']!='backup','backup incomplete: use prepare to resume backup')
        verify_pair(session,state)
        require_independent_backup(session,state)
        require(not state.get('preflight_blockers'), 'cannot resume blocked preparation')
        if adopt_created_repo_id is not None:
            require(state['stage']=='create-intent' and state.get('created_new_id') is None,
                    'adoption only for an interrupted create-intent without committed ID')
            candidate=get_new_repo(state,optional=True)
            require(candidate and int(candidate['id'])==int(adopt_created_repo_id) and
                    state.get('creation_attempted_at'), 'candidate is not the intended new repository')
            created=dt.datetime.fromisoformat(candidate['created_at'].replace('Z','+00:00'))
            attempted=dt.datetime.fromisoformat(state['creation_attempted_at'])
            require(created>=attempted-dt.timedelta(seconds=120),
                    'replacement was created before this operation')
            state['created_new_id']=candidate['id'];save_state(session,state)
        if not state['approved']:
            require(ack_delete==state['repo'],
                    'explicit deletion approval missing: use --ack-delete OWNER/REPO')
            require(state['stage']=='prepared','approval not previously recorded; unsafe stage')
            state['approved']=True;save_state(session,state)
        if accept_base_drift:
            state['accepted_base_drift']=True;save_state(session,state)
        delete_original(session,state)
        create_replacement(session,state)
        restore_refs(session,state)
        restore_labels_and_milestones(session,state)
        restore_objects(session,state)
        restore_releases(session,state)
        restore_actions_permissions(session,state)
        restore_security_configuration(session,state,vault_file=secrets_vault,age_identity=age_identity)
        if state['stage']=='configuration-done':final_verify(session,state)
        require(state['stage'] in (DONE,'done-with-action-required'),
                'resume stopped at unsupported stage '+state['stage'])

def attest_manual_recovery(session,*,ack_external_recovery):
    """Allow manually repaired integrations to progress, never call them proven."""
    with locked(session):
        state=load_state(session)
        require(ack_external_recovery==state['repo'],
                'manual recovery acknowledgement must match exact OWNER/REPO')
        require(state['stage']=='done-with-action-required',
                'manual attestation is only for completed recovery with outstanding requirements')
        verify_pair(session,state)
        require_independent_backup(session,state)
        get_new_repo(state)
        source=session/'primary'
        expected,_=expected_ref_mapping(state,source,live_refs(state['repo']))
        current=live_refs(state['repo'])
        require(all(current.get(ref)==sha for ref,sha in expected.items()),
                'Git refs have changed since restoration')
        # All named secrets must now exist; the API cannot verify actual values.
        verify_recovery_configuration(session,state,require_all_secrets=True)
        from . import credentials
        credential_proof=credentials.post_restore(session,state,apply_apps=False,record=True)
        require(credential_proof['status']=='verified',
                'external credential authorization is not verified; manual attestation cannot bypass it')
        old_workflows=read_json(source/'actions'/'workflows.json')
        new_workflows={x['path']:x for x in collect('repos/'+state['repo']+'/actions/workflows',key='workflows')}
        missing=[x['path'] for x in old_workflows if x.get('state')!='deleted' and
                 x['path'] not in new_workflows]
        # GitHub workflow registrations may outlive the workflow's presence on
        # the original default branch. They cannot be fabricated by replaying
        # metadata. Permit explicit manual acknowledgment ONLY if the missing
        # registration was already source-proven absent at the default HEAD.
        gaps=state.get('unregistered_workflows',{})
        require(all(path in gaps and
                    gaps[path].get('original_default_source_present') is False
                    for path in missing),
                'required default-branch workflow registrations still missing: '+
                ', '.join(missing[:5]))
        state['manually_acknowledged_archived_workflow_registrations']=missing
        save_state(session,state)
        current_actions=api('repos/'+state['repo']+'/actions/permissions')
        if current_actions.get('enabled') is True:
            require(state.get('post_recovery_actions_activation')=='enabled-unqualified',
                    'Actions activation was not journaled')
            original_policy=read_json(source/'actions'/'permissions.json')
            require(all(current_actions.get(k)==original_policy.get(k)
                        for k in ('enabled','allowed_actions','sha_pinning_required')),
                    'Actions policy differs from the verified original')
        else:
            restore_actions_enabled_after_safety(session,state,unresolved=False)
        report=read_json(session/'report.json')
        report['manual_recovery_attestation']={
            'at':now(),'acknowledged_repo':state['repo'],
            'scope':'external dependencies, archived-only identity/history, original secret values',
            'secret_values_verified':False,
            'hosted_workflow_success_verified':False}
        report['actions_left_disabled_for_manual_recovery']=False
        report['archived_only_workflow_registrations_acknowledged']=missing
        report['recovery_classification']='RESTORED_AFTER_MANUAL_ATTESTATION_NOT_OPERATIONALLY_TESTED'
        report['fully_operationally_verified']=False
        write_json(session/'report.json',report)
        state['stage']='done-manual-attested';save_state(session,state)
        log(report['recovery_classification'],style='yellow')


def verified_ci_dispatch(session,*,workflow,ref,ack_dispatch,timeout_seconds=1800):
    """Explicit, opt-in live CI qualification. Does not guess what is safe to run."""
    with locked(session):
        state=load_state(session)
        require(state['stage'] in (DONE,'done-with-action-required','done-manual-attested'),
                'finish repository recovery before dispatching CI')
        repo=state['repo']
        require(ack_dispatch==repo,'CI workflow dispatch requires --ack-dispatch OWNER/REPO')
        require(not state.get('actions_left_disabled_for_manual_recovery'),
                'Actions deliberately disabled while recovery is unresolved')
        get_new_repo(state)
        refs=live_refs(repo)
        sha=refs.get('refs/heads/'+ref)
        require(sha and re.fullmatch('[0-9a-fA-F]{40}',sha),
                'CI ref must be a live named branch')
        require(workflow.startswith('.github/workflows/') and
                workflow.endswith(('.yml','.yaml')) and '..' not in workflow.split('/'),
                'CI workflow must be a safe repository workflow path')
        all_wf=collect(f'repos/{repo}/actions/workflows',key='workflows')
        require(any(w.get('path')==workflow and w.get('state')=='active' for w in all_wf),
                'CI workflow is not registered and enabled')
        require(api(f'repos/{repo}/actions/permissions').get('enabled') is True,
                'repository Actions are disabled')
        details=run(['gh','workflow','view',workflow,'--repo',repo,'--yaml'],timeout=45).stdout
        require('workflow_dispatch' in details,
                'chosen workflow has no apparent workflow_dispatch trigger')
        def runs():
            p=run(['gh','run','list','--repo',repo,'--workflow',workflow,
                   '--limit','50','--json',
                   'databaseId,headSha,status,conclusion,event,url'],timeout=60)
            data=json.loads(p.stdout)
            require(isinstance(data,list),'unexpected Actions run JSON')
            return data
        before={int(x['databaseId']) for x in runs()}
        log('Dispatching explicit CI workflow '+workflow+' @ '+ref)
        run(['gh','workflow','run',workflow,'--repo',repo,'--ref',ref],timeout=90)
        found=[]
        for i in range(30):
            candidates=[x for x in runs() if int(x['databaseId']) not in before and
                        x.get('headSha')==sha and x.get('event')=='workflow_dispatch']
            if candidates:
                found=candidates;break
            time.sleep(2)
        require(len(found)==1,'could not uniquely attribute dispatched workflow to new run; '
                'do not assume another workflow run belongs to this test')
        runid=int(found[0]['databaseId'])
        result=run(['gh','run','watch',str(runid),'--repo',repo,'--exit-status'],
                   timeout=timeout_seconds,check=False)
        matches=[x for x in runs() if int(x['databaseId'])==runid]
        require(len(matches)==1,'CI run disappeared before conclusion verification')
        actual=matches[0]
        report=read_json(session/'report.json')
        record={'workflow':workflow,'ref':ref,'head_sha':sha,'run_id':runid,
                'url':actual.get('url'),'status':actual.get('status'),
                'conclusion':actual.get('conclusion'),
                'verified_success':result.returncode==0 and actual.get('status')=='completed' and
                    actual.get('conclusion')=='success'}
        report.setdefault('verified_ci_runs',[]).append(record)
        report['ci_execution']='one explicitly dispatched workflow; see verified_ci_runs'
        write_json(session/'report.json',report)
        require(record['verified_success'],'CI did not complete successfully; report persisted')
        log('CI verified for '+workflow+' at '+sha,style='green')

def render_status(session,state):
    t=Table(title='RepoRebuild session',show_lines=False)
    t.add_column('Field',style='bold');t.add_column('Value')
    for label,value in [('Repository',state['repo']),('Session',str(session)),
                        ('Stage',state['stage']),('Original ID',state.get('original_repo_id')),
                        ('New ID',state.get('created_new_id')),
                        ('Topology',state.get('topology')),
                        ('Approved deletion',state.get('approved')),
                        ('Independent copy',state.get('independent_copy') or 'NOT VERIFIED'),
                        ('Unresolved secrets',len(state.get('missing_secret_names',[]))),
                        ('Manual recovery',len(state.get('manual_obligations',[]))),
                        ('Credential recovery',state.get('credential_recovery_status') or 'NOT_ATTESTED')]:
        t.add_row(label,str(value))
    CONSOLE.print(t)
    for name in ('readiness.json','credential-readiness.json','credential-predelete-recheck.json',
                 'credential-acceptance.json','migration-plan.json','report.json'):
        if (session/name).is_file():log('Evidence: '+str(session/name),style='blue')

def archive_release_assets(repo,root,max_bytes,allow_gaps):
    releases=read_json(root/'metadata'/'releases.json')
    token=run(['gh','auth','token']).stdout.strip()
    gaps=[]
    for rel in releases:
        for asset in rel.get('assets') or []:
            asset_id=int(asset['id']);dst=root/'release-assets'/f'{asset_id}.bin'
            if dst.exists():continue
            try:
                download_api_binary(f'repos/{repo}/releases/assets/{asset_id}',dst,
                    token=token,max_bytes=max_bytes,
                    expected_size=asset.get('size'),mime='application/octet-stream')
                if asset.get('digest'):
                    require(asset['digest']=='sha256:'+sha256(dst),
                            f'release asset source digest mismatch {asset_id}')
            except (urllib.error.HTTPError,urllib.error.URLError,Refused,OSError) as e:
                if dst.is_file() and not dst.is_symlink():dst.unlink()
                gaps.append({'asset_id':asset_id,'release':rel['tag_name'],'reason':type(e).__name__+' '+str(e)[:250]})
                continue
            if asset.get('name','').lower().endswith('.zip'):
                try:zip_ok(dst)
                except (Refused,zipfile.BadZipFile) as e:
                    dst.unlink()
                    gaps.append({'asset_id':asset_id,'release':rel['tag_name'],
                                 'reason':'invalid release ZIP: '+str(e)[:250]})
    write_json(root/'release-asset-coverage.json',{'gaps':gaps,'expected':sum(len(x.get('assets') or []) for x in releases)})
    require(not gaps or allow_gaps,
        f'{len(gaps)} release assets not archived; inspect release-asset-coverage.json')

# Keep backup state and guard stages separate from any target worktree.
def inspect_repo(repo):
    require(REPO_RE.fullmatch(repo),'repo must be OWNER/REPO')
    data=repo_info(repo)
    require(data is not None,'repository is not accessible')
    table=Table(title=f'RepoRebuild inspection: {repo}')
    table.add_column('Property',style='bold');table.add_column('Value')
    for k,v in [('Repository ID',data.get('id')),
                ('Kind','fork' if data.get('fork') else 'standalone'),
                ('Upstream',(data.get('parent') or {}).get('full_name','—')),
                ('Visibility',data.get('visibility')),
                ('Default branch',data.get('default_branch')),
                ('Admin access',(data.get('permissions') or {}).get('admin'))]:
        table.add_row(k,str(v))
    CONSOLE.print(table)
    runs=collect(f'repos/{repo}/actions/runs',key='workflow_runs')
    artifacts=collect(f'repos/{repo}/actions/artifacts',key='artifacts')
    log(f'Actions: {len(runs)} runs listed, {len(artifacts)} artifacts; '
        f'{sum(bool(a.get("expired")) for a in artifacts)} expired',style='yellow')
    log(RISK_NOTE,style='yellow')

def list_sessions(repo,root):
    parent=secured_session_dir(repo,base=root)
    table=Table(title=f'Recovery sessions — {repo}')
    table.add_column('Session');table.add_column('Stage');table.add_column('Backup/restore ID')
    for path in sorted(parent.iterdir(),reverse=True):
        if path.is_dir() and (path/'state.json').is_file():
            s=load_state(path)
            table.add_row(str(path),s['stage'],
                          f'{s.get("original_repo_id")} → {s.get("created_new_id")}')
    CONSOLE.print(table)

def guided_rebuild(repo,*,root=DEFAULT_ROOT,independent_copy_dir=None,
                   accept_local_only_risk=False,secrets_vault=None,age_identity=None,
                   accept_missing_secrets=False,accept_manual_recovery=False,
                   credentials_policy=None,accept_credential_manual_risk=False):
    """A readable interactive happy-path, preserving the same explicit delete gate."""
    inspect_repo(repo)
    CONSOLE.print(Panel(RISK_NOTE,title='Before rebuilding',style='yellow'))
    if not Confirm.ask('Create two verified local backups?',default=False):
        log('No repository modifications performed',style='yellow');return
    session=new_session(repo,base=root)
    prepare(session,independent_copy_dir=independent_copy_dir,
            accept_local_only_risk=accept_local_only_risk,secrets_vault=secrets_vault,
            age_identity=age_identity,accept_missing_secrets=accept_missing_secrets,
            accept_manual_recovery=accept_manual_recovery,
            credentials_policy=credentials_policy,
            accept_credential_manual_risk=accept_credential_manual_risk)
    CONSOLE.print(Panel(
        f'[green]Backups verified.[/green]\nSession: {session}\n'
        'Independent-filesystem backup and secret restoration are required unless explicitly waived.\n'
        'GitHub historical identities will not be recreated.',
        title='Prepared recovery',style='green'))
    if not Confirm.ask('Delete the original repository and recreate it now?',default=False):
        log('Prepared session remains available; repository was not deleted',style='yellow')
        CONSOLE.print('Resume with: devtool recovery execute --session '+
                      repr(str(session))+' --ack-delete '+repo)
        return
    confirmed=Prompt.ask('Type the exact OWNER/REPO to approve deletion',default='')
    require(confirmed==repo,'exact repository confirmation mismatch; no deletion')
    resume(session,ack_delete=confirmed,secrets_vault=secrets_vault,age_identity=age_identity)

