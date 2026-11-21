#!/usr/bin/env python3
"""Opt-in campaign planning, offline fixture construction and gated empty-repo bootstrap.
No recovery execution, credential provisioning, or remote Git pushes here.
Only the separately approved create-primary command writes to GitHub.
"""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys
import zipfile

BASE = '860671c4782ce58b3344871248456c516fb8ce08'
PARENT = '9345d3f59aaa52fdfddb34b55eaaa9fb38b49024'
BRANCH = 'feature/native-repository-recovery-rebased-m5-20261120'
SOURCE_REPO = 'Mikeyphw/devtool'
ROLES = {'primary':'p','minimal':'m','negative':'n','host':'h','consumer':'c','dependency':'d','fork_parent':'f','fork_child':'k'}
MATRIX = tuple(f'F{i:02}' for i in range(1,57))
UPLOAD = 'ea165f8d65b6e75b540449e92b4886f43607fa02'

class Refused(RuntimeError):
    pass

def need(condition, reason):
    if not condition:
        raise Refused(reason)

def digest(data):
    return hashlib.sha256(data).hexdigest()

def encoded(data):
    return (json.dumps(data, sort_keys=True, indent=2)+'\n').encode()

def write_new(path, data):
    with path.open('xb') as out:
        os.fchmod(out.fileno(),0o600)
        out.write(data)
        out.flush()
        os.fsync(out.fileno())
    fd=os.open(path.parent,os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)

def private_dir(path):
    path = Path(os.path.abspath(path))
    for component in (path,*path.parents):
        need(not component.is_symlink(),'SYMLINK_PATH_REFUSED')
    info=path.stat()
    need(stat.S_ISDIR(info.st_mode) and info.st_uid==os.getuid() and not info.st_mode&0o077,'PRIVATE_OWNED_DIRECTORY_REQUIRED')
    need(not any(str(path).startswith(x) for x in ('/sdcard/','/storage/emulated/','/mnt/media_rw/')),'SHARED_STORAGE_REFUSED')
    return path

def new_state(path):
    path=Path(os.path.abspath(path)); private_dir(path.parent)
    path.mkdir(mode=0o700,exist_ok=False)
    return private_dir(path)

def plan_data(owner, campaign, head):
    need(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?',owner) is not None,'OWNER_INVALID')
    need(re.fullmatch(r'[a-f0-9]{24}',campaign) is not None,'CAMPAIGN_ID_INVALID')
    need(re.fullmatch(r'[a-f0-9]{40}',head) is not None,'SOURCE_PIN_INVALID')
    names={role:f'{owner}/repo-rebuild-e2e-{campaign}{suffix}' for role,suffix in ROLES.items()}
    return {'schema':'devtool.recovery.full-fixture.plan.v1','campaign':campaign,'owner':owner,
        'devtool_head':head,'devtool_base':BASE,'roles':names,
        'repository_ids':{x:None for x in names},'remote_authorization':False,
        'primary':{'private':True,'fork':False,'branches':['main','feature/open','feature/closed','feature/merge','workflow/branch-only'],
        'tags':['fixture-annotated','fixture-lightweight'],'issues':['open','closed'],
        'pulls':['open','closed-unmerged','merged'],'release_assets':['fixture.bin','fixture.zip'],
        'workflow_files':['fixture-pr.yml','fixture-dispatch.yml','branch-only.yml'],
        'variables':['FIXTURE_LABEL','CANARY_SHA256'],'secrets':['REBUILD_CANARY'],
        'environments':['fixture-env'],'labels':['fixture-bug','fixture-review'],
        'milestones':['fixture-milestone'],'requires_lfs':True,'requires_independent_storage':True},
        'matrix':{x:{'verdict':'NOT_RUN','evidence':[]} for x in MATRIX}}

def load_plan(state):
    state=private_dir(state)
    for path in (state/'plan.json',state/'plan.sha256'):
        need(not path.is_symlink(),'PLAN_SYMLINK')
        info=path.stat()
        need(stat.S_ISREG(info.st_mode) and info.st_uid==os.getuid() and not info.st_mode&0o077,'PLAN_FILE_INSECURE')
    raw=(state/'plan.json').read_bytes()
    need((state/'plan.sha256').read_text().strip()==digest(raw),'PLAN_DIGEST_MISMATCH')
    p=json.loads(raw)
    need(p==plan_data(p['owner'],p['campaign'],p['devtool_head']),'PLAN_SCHEMA_OR_SCOPE_MISMATCH')
    return p

def git(repo,*args,env=None):
    print('FIXTURE_GIT',*args,flush=True)
    out=subprocess.run(['git','-C',str(repo),*args],env=env,check=True,text=True,stdout=subprocess.PIPE).stdout.strip()
    if env is not None and out: print(out,flush=True)
    return out

def source_check(root,expected):
    root=Path(root).absolute()
    need(root==Path(git(root,'rev-parse','--show-toplevel')),'EXACT_WORKTREE_REQUIRED')
    need(git(root,'rev-parse','HEAD')==expected and git(root,'symbolic-ref','--short','HEAD')==BRANCH,'SOURCE_HEAD_OR_BRANCH_DRIFT')
    need(not git(root,'status','--porcelain=v1','--untracked-files=all'),'SOURCE_DIRTY')
    need(git(root,'remote','get-url','origin') in ('git@github.com:Mikeyphw/devtool.git','git@github.com:Mikeyphw/devtool','https://github.com/Mikeyphw/devtool.git','https://github.com/Mikeyphw/devtool'),'SOURCE_ORIGIN_REFUSED')
    need(re.fullmatch(r'[0-9a-f]{40}',git(root,'rev-parse','HEAD^{tree}')) is not None,'SOURCE_TREE_INVALID')
    git(root,'merge-base','--is-ancestor',PARENT,expected)
    return root

def workflow(dispatch=True):
    if not dispatch:
        return 'name: Fixture PR\non: [pull_request]\npermissions: {}\njobs:\n  synthetic:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo fixture-pr-ok\n'
    return f'''name: Fixture dispatch
on:
  workflow_dispatch:
    inputs:
      outcome:
        type: choice
        options: [success, failure, canary]
        default: success
        required: true
permissions: {{}}
jobs:
  synthetic:
    runs-on: ubuntu-latest
    steps:
      - name: Create synthetic receipt
        env:
          OUTCOME: ${{{{ inputs.outcome }}}}
          CANARY: ${{{{ secrets.REBUILD_CANARY }}}}
          EXPECTED: ${{{{ vars.CANARY_SHA256 }}}}
        run: |
          python3 - <<'END'
          import os, hashlib, hmac
          from pathlib import Path
          outcome = os.environ['OUTCOME']
          if outcome == 'failure': raise SystemExit(2)
          if outcome == 'canary':
              value = os.environ['CANARY']
              assert len(value) >= 32 and hmac.compare_digest(hashlib.sha256(value.encode()).hexdigest(), os.environ['EXPECTED'])
          Path('fixture-receipt.txt').write_text('synthetic-ok\\n')
          END
      - uses: actions/upload-artifact@{UPLOAD}
        with:
          name: fixture-receipt
          path: fixture-receipt.txt
          if-no-files-found: error
          retention-days: 7
'''

def seed_local(state, *, with_lfs=True):
    p=load_plan(state); state=Path(state)
    need(not with_lfs or shutil.which('git-lfs'),'GIT_LFS_REQUIRED_FOR_FULL_FIXTURE')
    # Isolate git configuration, templates and hooks; never use an existing repo.
    work=state/'seed'; work.mkdir(mode=0o700)
    home=work/'isolated-home'; home.mkdir(mode=0o700)
    template=work/'empty-template'; template.mkdir(mode=0o700)
    global_config=home/'empty-gitconfig'; global_config.touch(mode=0o600,exist_ok=False)
    repo=work/'repository'; repo.mkdir(mode=0o700)
    env={k:v for k,v in os.environ.items() if not k.startswith(('GIT_','GH_')) and k not in ('XDG_CONFIG_HOME','DEVTOOL_RECOVERY_ADMIN_TOKEN')}
    env.update(HOME=str(home),GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=str(global_config),GIT_TERMINAL_PROMPT='0',GIT_AUTHOR_DATE='2025-01-01T00:00:00Z',GIT_COMMITTER_DATE='2025-01-01T00:00:00Z',LC_ALL='C')
    def run(*a): return git(repo,*a,env=env)
    run('init','--initial-branch=main','--template='+str(template)); run('config','user.name','Synthetic Fixture'); run('config','user.email','fixture@example.invalid'); run('config','commit.gpgsign','false'); run('config','tag.gpgsign','false')
    def put(name,data):
        path=repo/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(data)
    put('README.md',b'# Synthetic recovery fixture\nNo production content.\n')
    put('fixture.json',encoded({'campaign':p['campaign'],'role':'primary','repository':p['roles']['primary']}))
    put('.github/workflows/fixture-pr.yml',workflow(False).encode())
    put('.github/workflows/fixture-dispatch.yml',workflow().encode())
    lfs_data=bytes(range(256))*128
    if with_lfs:
        run('lfs','install','--local'); run('lfs','track','*.fixture-lfs'); put('object.fixture-lfs',lfs_data)
    run('add','--all'); run('commit','-m','fixture: synthetic base'); main=run('rev-parse','HEAD')
    for branch in ('feature/open','feature/closed','feature/merge','workflow/branch-only'):
        run('checkout','-b',branch,main)
        if branch.startswith('workflow/'):
            put('.github/workflows/branch-only.yml',workflow().replace('Fixture dispatch','Branch-only fixture').encode())
        else: put('branches/'+branch.split('/')[1]+'.txt',(branch+'\n').encode())
        run('add','--all'); run('commit','-m','fixture: '+branch)
    run('checkout','main'); run('tag','fixture-lightweight',main); run('tag','-a','fixture-annotated','-m','Synthetic release tag',main)
    refs=dict(line.split(' ',1) for line in run('for-each-ref','--format=%(refname) %(objectname)','refs/heads','refs/tags').splitlines())
    need(len(refs)==7,'LOCAL_REF_COUNT_MISMATCH'); run('fsck','--full','--strict')
    if with_lfs:
        pointer=run('show','HEAD:object.fixture-lfs')
        need('oid sha256:'+digest(lfs_data) in pointer and 'size '+str(len(lfs_data)) in pointer,'LFS_POINTER_MISMATCH')
        run('lfs','fsck')
        oid=digest(lfs_data); need((repo/'.git/lfs/objects'/oid[:2]/oid[2:4]/oid).read_bytes()==lfs_data,'LFS_OBJECT_MISMATCH')
    assets=work/'release-assets'; assets.mkdir(mode=0o700)
    (assets/'fixture.bin').write_bytes(bytes(range(256))*4)
    with zipfile.ZipFile(assets/'fixture.zip','x',compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(zipfile.ZipInfo('receipt.txt',(2025,1,1,0,0,0)),b'synthetic release asset\n')
    with zipfile.ZipFile(assets/'fixture.zip') as archive: need(archive.testzip() is None,'ASSET_ZIP_CORRUPT')
    manifest={'schema':1,'plan_sha256':digest((state/'plan.json').read_bytes()),'refs':refs,'main':main,'lfs':{'verified_local':with_lfs,'sha256':digest(lfs_data) if with_lfs else None},'assets':{x.name:{'size':x.stat().st_size,'sha256':digest(x.read_bytes())} for x in sorted(assets.iterdir())},'remote_mutation':False,'live_acceptance':False,'seed_status':'LOCAL_FULL_SEED' if with_lfs else 'LOCAL_SMOKE_ONLY'}
    write_new(work/'seed-manifest.json',encoded(manifest)); print('FIXTURE_LOCAL_SEED='+manifest['seed_status'],flush=True)
    return manifest

def gh_env():
    return {**{k:v for k,v in os.environ.items() if k not in ('GH_DEBUG','GIT_TRACE','GIT_CURL_VERBOSE')},'GH_HOST':'github.com','GH_PROMPT_DISABLED':'1'}

def gh_get(endpoint):
    p=subprocess.run(['gh','api','--hostname','github.com','-X','GET',endpoint],check=False,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=60,env=gh_env())
    need(p.returncode==0,'GITHUB_READ_FAILED_REDACTED_'+str(p.returncode))
    return json.loads(p.stdout)

def verify_delivery(root, head, session, request):
    need(head!=PARENT,'NEW_NATIVE_FIXTURE_SOURCE_REQUIRED')
    need(re.fullmatch(r'[a-f0-9]{32}',session) is not None and re.fullmatch(r'[a-f0-9]{32}',request) is not None and request!='f'*32,'NATIVE_DELIVERY_RECEIPT_REQUIRED')
    sys.path.insert(0,str(root))
    from devtool.delivery_qualification import qualify
    result=qualify(session,request_id=request)
    ci=result.get('required_delivery_ci') or {}; reg=result.get('repository_regression') or {}
    need(result.get('head_sha')==head and result.get('session_id')==session and result.get('base_repository')==SOURCE_REPO and result.get('head_repository')==SOURCE_REPO and result.get('read_only') is True and result.get('blocking_reason') is None and result.get('pre_merge_qualification')=='PASSED' and ci.get('passed') is True and reg.get('passed') is True and reg.get('request_id')==request and reg.get('source_sha')==head and reg.get('shards_passed')==24 and reg.get('jobs_passed')==25 and reg.get('summary_passed') is True and reg.get('artifacts_verified')==25,'FRESH_NATIVE_HOSTED_QUALIFICATION_REQUIRED')
    print('FIXTURE_DEVTOOL_SOURCE_CI=INDEPENDENTLY_VERIFIED',flush=True)

def preflight(state, root, independent, session, request):
    p=load_plan(state); root=source_check(root,p['devtool_head'])
    for cmd in ('bash','git','gh','git-lfs','age','jq','sha256sum'):
        need(shutil.which(cmd),'MISSING_TOOL_'+cmd); print('FIXTURE_TOOL='+cmd,flush=True)
    independent=private_dir(independent)
    need(independent.stat().st_dev!=Path(state).stat().st_dev,'INDEPENDENT_FILESYSTEM_REQUIRED')
    pr=gh_get('repos/'+SOURCE_REPO+'/pulls/41')
    need(pr['state']=='open' and pr['draft'] is True and pr['head']['sha']==p['devtool_head'] and pr['head']['ref']==BRANCH and pr['head']['repo']['full_name']==SOURCE_REPO and pr['base']['sha']==BASE and pr['base']['ref']=='main','REMOTE_PR_DRIFT')
    need(gh_get('repos/'+SOURCE_REPO+'/branches/main')['commit']['sha']==BASE,'REMOTE_MAIN_DRIFT')
    actor=gh_get('user'); need(actor['login']==p['owner'] and actor['type']=='User','PERSONAL_ACTOR_MISMATCH')
    # Use exact source's existing protected token probe; no credential values printed.
    sys.path.insert(0,str(root))
    from devtool.recovery.credentials import _token_api
    admin=_token_api('user','DEVTOOL_RECOVERY_ADMIN_TOKEN')
    need(admin.get('id')==actor.get('id') and admin.get('login')==actor.get('login'),'ADMIN_ACTOR_MISMATCH')
    verify_delivery(root,p['devtool_head'],session,request)
    evidence={'schema':1,'delivery_session':session,'regression_request':request,'source':p['devtool_head'],'plan_sha256':digest((Path(state)/'plan.json').read_bytes()),'actor_id':actor['id'],'storage_devices_distinct':True,'independent_path':str(independent),'checked_at':dt.datetime.now(dt.timezone.utc).isoformat(),'fixture_permissions':'NOT_YET_PROVABLE','remote_mutation':False,'live_acceptance':False}
    write_new(Path(state)/'preflight.json',encoded(evidence)); print('FIXTURE_PREFLIGHT=READ_ONLY_PASS',flush=True)


def create_primary(state, root, ack, plan_sha):
    """Only a freshly approved empty private primary. Never push or delete."""
    state=private_dir(state); p=load_plan(state); root=source_check(root,p['devtool_head'])
    repo=p['roles']['primary']; raw=(state/'plan.json').read_bytes()
    need(ack==repo and plan_sha==digest(raw),'EXACT_CREATE_APPROVAL_REQUIRED')
    pre=state/'preflight.json'; need(pre.is_file() and not pre.is_symlink(),'PREFLIGHT_RECEIPT_REQUIRED')
    info=pre.stat(); need(info.st_uid==os.getuid() and not info.st_mode&0o077,'PREFLIGHT_INSECURE')
    receipt=json.loads(pre.read_bytes())
    age=dt.datetime.now(dt.timezone.utc)-dt.datetime.fromisoformat(receipt['checked_at'])
    need(receipt['source']==p['devtool_head'] and receipt['plan_sha256']==plan_sha and dt.timedelta(0)<=age<dt.timedelta(minutes=15),'PREFLIGHT_STALE_OR_MISMATCHED')
    independent=private_dir(receipt['independent_path']); need(independent.stat().st_dev!=state.stat().st_dev,'INDEPENDENT_FILESYSTEM_REQUIRED')
    need(not (state/'primary-create-intent.json').exists() and not (state/'primary-created.json').exists(),'CREATE_ALREADY_ATTEMPTED_NO_REPLAY')
    pr=gh_get('repos/'+SOURCE_REPO+'/pulls/41'); need(pr['state']=='open' and pr['draft'] is True and pr['head']['sha']==p['devtool_head'] and pr['head']['ref']==BRANCH and pr['head']['repo']['full_name']==SOURCE_REPO and pr['base']['sha']==BASE and pr['base']['ref']=='main','REMOTE_PR_DRIFT')
    need(gh_get('repos/'+SOURCE_REPO+'/branches/main')['commit']['sha']==BASE,'REMOTE_MAIN_DRIFT')
    actor=gh_get('user'); need(actor['login']==p['owner'] and actor['id']==receipt['actor_id'] and actor['type']=='User','ACTOR_DRIFT')
    sys.path.insert(0,str(root))
    from devtool.recovery.credentials import _token_api, _admin_observation
    from devtool.recovery.provider import request_json
    admin=_token_api('user','DEVTOOL_RECOVERY_ADMIN_TOKEN'); need(admin.get('id')==actor['id'] and admin.get('login')==actor['login'],'ADMIN_ACTOR_MISMATCH')
    # Require a complete LOCAL seed, not a smoke-only fixture. No ref or metadata push in this stage.
    seed=state/'seed/seed-manifest.json'; need(seed.is_file() and not seed.is_symlink(),'LOCAL_SEED_REQUIRED')
    m=json.loads(seed.read_bytes()); need(m.get('plan_sha256')==plan_sha and m.get('seed_status')=='LOCAL_FULL_SEED' and m.get('lfs',{}).get('verified_local') is True,'FULL_LFS_SEED_REQUIRED')
    verify_delivery(root,p['devtool_head'],receipt['delivery_session'],receipt['regression_request'])
    marker='Synthetic RepoRebuild fixture '+p['campaign']
    write_new(state/'primary-create-intent.json',encoded({'schema':1,'repo':repo,'actor_id':actor['id'],'plan_sha256':plan_sha,'source':p['devtool_head'],'approval':ack,'marker':marker,'time':dt.datetime.now(dt.timezone.utc).isoformat()}))
    print('FIXTURE_CREATE_INTENT_RECORDED; one POST only; no retry on uncertain response',flush=True)
    result=subprocess.run(['gh','api','--hostname','github.com','-X','POST','user/repos','--input','-'],input=encoded({'name':repo.split('/')[1],'private':True,'auto_init':False,'description':marker,'has_issues':True,'has_projects':False,'has_wiki':False}),stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=gh_env(),timeout=90,check=False)
    need(result.returncode==0,'CREATE_OUTCOME_UNCERTAIN_NO_REPLAY_'+str(result.returncode))
    created=json.loads(result.stdout); rid=created.get('id')
    need(type(rid) is int and rid>0 and created.get('full_name')==repo and created.get('private') is True,'CREATE_RESPONSE_IDENTITY_UNVERIFIED')
    # Record observed numeric ID before any further provider operations.
    write_new(state/'primary-created.json',encoded({'schema':1,'repository':repo,'id':rid,'source':p['devtool_head'],'plan_sha256':plan_sha,'marker':marker}))
    current=gh_get('repos/'+repo)
    need(current.get('id')==rid and current.get('full_name')==repo and current.get('private') is True and current.get('fork') is False and current.get('archived') is False and current.get('description')==marker and current.get('owner',{}).get('id')==actor['id'],'CREATED_IDENTITY_UNVERIFIED')
    _admin_observation(repo,rid)
    write_new(state/'primary-disable-actions-intent.json',encoded({'repository':repo,'id':rid,'enabled':False}))
    request_json('repos/'+repo+'/actions/permissions',method='PUT',payload={'enabled':False})
    policy=gh_get('repos/'+repo+'/actions/permissions'); need(policy.get('enabled') is False,'ACTIONS_DISABLE_NOT_OBSERVED')
    need(gh_get('repos/'+repo+'/branches')==[],'UNEXPECTED_CREATED_REFS')
    write_new(state/'primary-bootstrap.json',encoded({'schema':1,'repository':repo,'id':rid,'plan_sha256':plan_sha,'actions_disabled':True,'empty_refs':True,'source':p['devtool_head'],'live_recovery_acceptance':False}))
    print('FIXTURE_PRIMARY_BOOTSTRAP=VERIFIED_EMPTY_PRIVATE_ACTIONS_DISABLED',flush=True)
    print('FIXTURE_PRIMARY_ID='+str(rid),flush=True)

def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__); subs=parser.add_subparsers(dest='cmd',required=True)
    a=subs.add_parser('plan'); a.add_argument('--state',type=Path,required=True); a.add_argument('--owner',required=True); a.add_argument('--source',type=Path,required=True); a.add_argument('--head',required=True)
    a=subs.add_parser('seed-local'); a.add_argument('--state',type=Path,required=True); a.add_argument('--smoke-without-lfs',action='store_true')
    a=subs.add_parser('preflight'); a.add_argument('--state',type=Path,required=True); a.add_argument('--source',type=Path,required=True); a.add_argument('--independent',type=Path,required=True); a.add_argument('--delivery-session',required=True); a.add_argument('--regression-request',required=True)
    a=subs.add_parser('create-primary'); a.add_argument('--state',type=Path,required=True); a.add_argument('--source',type=Path,required=True); a.add_argument('--ack-create',required=True); a.add_argument('--plan-sha256',required=True)
    a=subs.add_parser('status'); a.add_argument('--state',type=Path,required=True)
    args=parser.parse_args()
    try:
        if args.cmd=='plan':
            source_check(args.source,args.head); state=new_state(args.state); p=plan_data(args.owner,secrets.token_hex(12),args.head); data=encoded(p)
            write_new(state/'plan.json',data); write_new(state/'plan.sha256',(digest(data)+'\n').encode()); print('FIXTURE_PLAN_SHA256='+digest(data)); print('FIXTURE_PRIMARY='+p['roles']['primary'])
        elif args.cmd=='seed-local': seed_local(args.state,with_lfs=not args.smoke_without_lfs)
        elif args.cmd=='preflight': preflight(args.state,args.source,args.independent,args.delivery_session,args.regression_request)
        elif args.cmd=='create-primary': create_primary(args.state,args.source,args.ack_create,args.plan_sha256)
        else:
            p=load_plan(args.state); print(json.dumps(p,indent=2,sort_keys=True))
        print('FIXTURE_REMOTE_MUTATION='+('PRIMARY_BOOTSTRAP_ONLY' if args.cmd=='create-primary' else 'NONE')); print('FIXTURE_LIVE_ACCEPTANCE=NOT_RUN')
        return 0
    except (Refused,OSError,ValueError,KeyError,subprocess.SubprocessError) as exc:
        print('FIXTURE_REFUSED='+type(exc).__name__+': '+str(exc),file=sys.stderr); return 2
if __name__=='__main__': raise SystemExit(main())
