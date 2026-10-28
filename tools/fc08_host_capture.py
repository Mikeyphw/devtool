#!/usr/bin/env python3
"""Operator-side FC08-HOST-R1R7 GitHub dispatch, source-pin and raw evidence capture.

Only --register-default may add one reviewed workflow file to main; never
create or mutate the retired feature branch. A capture never grants device/release status.
"""
from __future__ import annotations
import argparse,base64,hashlib,json,os
from pathlib import Path
import re,subprocess,sys,time
from typing import Any

# This script is executed from tools/, so import the sibling directly.
# A foreign installed top-level `tools` package must never become authority.
from fc08_host_workloads import LANES,validate_receipt,sha_bytes

REPO='Mikeyphw/devtool'
BRANCH='main'
BASE='dc23352e8f9d3ce3370ce82b88e067bd6b10deae'
EXPECTED_MAIN='8f31ce44e0f0021a7060f430a0cd527c2bf5a1bf'
WF='.github/workflows/devtool-fc08-host-workloads.yml'


def gh(*args:str,raw:bool=False,timeout:int=150)->bytes|str:
    p=subprocess.run(['gh',*args],capture_output=True,timeout=timeout)
    if p.returncode:raise RuntimeError('GH_COMMAND_REFUSED '+' '.join(args[:4])+' : '+p.stderr.decode(errors='replace')[-360:])
    return p.stdout if raw else p.stdout.decode()

def api(path:str)->Any:return json.loads(gh('api',f'repos/{REPO}/{path}'))

def git(root:Path,*args:str)->str:return subprocess.check_output(['git','-C',str(root),*args],text=True,timeout=25).strip()

def decode_file(row:dict)->bytes:
    if row.get('encoding')!='base64':raise ValueError('UNSUPPORTED_GITHUB_CONTENT_ENCODING')
    return base64.b64decode(row['content'],validate=True)

def validate_host(run:dict,jobs:list,arts:list,sha:str,attempt:int=1)->dict:
    if run.get('head_sha')!=sha or run.get('run_attempt')!=attempt or run.get('head_branch')!=BRANCH or not (str(run.get('path','')).split('@')[0].lstrip('/')==WF or str(run.get('path','')).split('@')[0].endswith('/'+WF)):
        raise ValueError('WRONG_SOURCE_BRANCH_OR_RUN_IDENTITY')
    if run.get('status')!='completed':
        raise ValueError('HOSTED_WORKLOAD_RUN_NOT_COMPLETED')
    if run.get('conclusion')!='success':
        raise ValueError('HOSTED_WORKLOAD_RUN_FAILED_NOT_QUALIFIED')
    expected={'FC08 real github-actions-dag','FC08 real linux-polyglot','FC08 real windows-dotnet-msvc','FC08 real macos-native','FC08 fail-closed four-lane evidence inventory'}
    if len(jobs)!=5 or {z.get('name') for z in jobs}!=expected or any(z.get('conclusion')!='success' for z in jobs):
        raise ValueError('BAD_INCOMPLETE_OR_DUPLICATE_GITHUB_JOBS')
    names={f'fc08-real-{l}-attempt{attempt}' for l in LANES}|{f'fc08-real-host-summary-attempt{attempt}'}
    if len(arts)!=5 or {z.get('name') for z in arts}!=names:raise ValueError('BAD_INCOMPLETE_ARTIFACT_SET')
    for z in arts:
        if z.get('expired') or not re.fullmatch('sha256:[0-9a-f]{64}',str(z.get('digest',''))):raise ValueError('MISSING_AUTHENTICATED_ARTIFACT_ZIP_DIGEST')
    return {a['name']:a for a in arts}

def main()->int:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo-dir',default=os.path.expanduser('~/Code/devtool'))
    ap.add_argument('--out-dir',default=os.path.expanduser('~/storage/downloads'))
    ap.add_argument('--register-default',action='store_true',help='Explicitly allow new read-only workflow registration on main')
    ap.add_argument('--run-id',type=int,help='Recover existing run; do not dispatch')
    a=ap.parse_args()
    root=Path(a.repo_dir).resolve()
    sha=git(root,'rev-parse','HEAD');tree=git(root,'rev-parse','HEAD^{tree}')
    if git(root,'status','--porcelain','--untracked-files=all') or git(root,'symbolic-ref','-q','--short','HEAD')!=BRANCH:
        raise ValueError('REFUSED_LOCAL_HEAD_PARENT_BRANCH_OR_WORKTREE')
    # The reviewed G1 R1R4 commit must remain an actual ancestor, not the
    # *immediate* parent. Subsequent exact-source HOST remediation commits are
    # valid only when ancestry, remote SHA/tree and clean branch all agree.
    try:
        git(root,'merge-base','--is-ancestor',BASE,sha)
    except subprocess.CalledProcessError as exc:
        raise ValueError('REFUSED_REQUIRED_G1_ANCESTRY') from exc
    branch=api('branches/'+BRANCH)
    if branch['commit']['sha']!=sha:raise ValueError('REFUSED_LOCAL_BRANCH_NOT_PUSHED_TO_GITHUB')
    commit=api('commits/'+sha)
    if commit['commit']['tree']['sha']!=tree:raise ValueError('REFUSED_REMOTE_SOURCE_TREE_DRIFT')
    wf=(root/WF).read_bytes()
    if b'contents: write' in wf or b'workflow_dispatch:' not in wf:raise ValueError('REFUSED_WORKFLOW_NOT_READ_ONLY')
    main=api('branches/main')
    try: existing=decode_file(api('contents/'+WF+'?ref=main'))
    except RuntimeError as exc:
        if '404' not in str(exc):raise
        existing=None
    if existing is None:
        if not a.register_default:raise ValueError('REFUSED_WORKFLOW_NOT_ON_MAIN_PASS_REGISTER_DEFAULT')
        if main['commit']['sha']!=EXPECTED_MAIN:raise ValueError('REFUSED_UNEXPECTED_DEFAULT_BRANCH_HEAD')
        response=json.loads(gh('api',f'repos/{REPO}/contents/{WF}','-X','PUT',
            '-f','message=ci(fc08): register read-only real-host workload workflow',
            '-f','branch=main','-f','content='+base64.b64encode(wf).decode()))
        if not response.get('commit',{}).get('sha'):raise ValueError('WORKFLOW_REGISTRATION_UNCONFIRMED')
        print('PASS_FC08_REAL_HOST_DEFAULT_REGISTRATION commit='+response['commit']['sha'],flush=True)
    elif existing!=wf:
        raise ValueError('REFUSED_MAIN_FEATURE_WORKFLOW_BYTES_DIFFER')
    print('PASS_FC08_REAL_HOST_SOURCE_PRECHECK sha='+sha+' tree='+tree,flush=True)
    if a.run_id is None:
        before={x['id'] for x in api('actions/runs?per_page=100')['workflow_runs']}
        gh('workflow','run','devtool-fc08-host-workloads.yml','--repo',REPO,'--ref',BRANCH)
        run_id=None;until=time.monotonic()+240
        while time.monotonic()<until:
            x=api('actions/runs?per_page=100')['workflow_runs']
            chosen=[z for z in x if z['id'] not in before and z.get('head_sha')==sha and z.get('event')=='workflow_dispatch' and (str(z.get('path','')).split('@')[0].lstrip('/')==WF or str(z.get('path','')).split('@')[0].endswith('/'+WF))]
            if len(chosen)>1:raise ValueError('AMBIGUOUS_NEW_WORKFLOW_DISPATCH')
            if chosen:run_id=chosen[0]['id'];break
            time.sleep(6)
        if run_id is None:raise RuntimeError('DISPATCH_RUN_ID_NOT_DISCOVERED')
    else:run_id=a.run_id
    print('FC08_REAL_HOST_RUN_URL=https://github.com/'+REPO+'/actions/runs/'+str(run_id),flush=True)
    dest=Path(a.out_dir)/(f'Devtool-FC08-HOST-{run_id}-{sha[:12]}');dest.mkdir(parents=True,exist_ok=True)
    until=time.monotonic()+4200
    while True:
        run=api('actions/runs/'+str(run_id))
        if run['status']=='completed':break
        if time.monotonic()>=until:raise RuntimeError('FC08_HOST_REAL_RUN_TIMEOUT')
        print('FC08_REAL_HOST_STATUS '+str(run_id)+' '+run['status'],flush=True)
        time.sleep(20)
    j=api(f'actions/runs/{run_id}/jobs?per_page=100');art=api(f'actions/runs/{run_id}/artifacts?per_page=100')
    for name,payload in [('run',run),('jobs',j),('artifacts',art)]:
        (dest/(name+'.json')).write_text(json.dumps(payload,indent=2,sort_keys=True)+'\n')
    if j.get('total_count')!=len(j['jobs']) or art.get('total_count')!=len(art['artifacts']):raise ValueError('INCOMPLETE_GITHUB_API_PAGE')
    found=validate_host(run,j['jobs'],art['artifacts'],sha)
    ident={'repository':REPO,'sha':sha,'tree':tree,'run_id':run_id,'attempt':1,'ref':'refs/heads/'+BRANCH}
    records={}
    for name,row in sorted(found.items()):
        folder=dest/name;folder.mkdir(exist_ok=True)
        gh('run','download',str(run_id),'--repo',REPO,'--name',name,'--dir',str(folder),timeout=240)
        leaves=[x for x in folder.rglob('*') if x.is_file()]
        if len(leaves)!=1 or leaves[0].is_symlink():raise ValueError('BAD_ARTIFACT_CONTENT_TREE:'+name)
        raw=leaves[0].read_bytes();data=json.loads(raw)
        if name.startswith('fc08-real-host-summary'):
            if data.get('identity')!=ident or data.get('release_qualified') is not False or data.get('fc08_overall_qualified') is not False:
                raise ValueError('INVALID_HOST_SUMMARY_RELEASE_ASSERTION')
            summary=data
        else:
            lane=name.removeprefix('fc08-real-').split('-attempt')[0]
            if leaves[0].name!=lane+'.json':raise ValueError('WRONG_LANE_RECEIPT_FILE')
            validate_receipt(data,lane,ident)
            records[lane]={'receipt_sha256':sha_bytes(raw),'artifact_id':row['id'],'zip_digest':row['digest']}
            print('FC08_REAL_HOST_LANE_VERIFIED '+lane+' receipt_sha256='+sha_bytes(raw)[:16],flush=True)
    if set(records)!=set(LANES) or any(summary['lanes'][k]['receipt_sha256']!=records[k]['receipt_sha256'] for k in records):
        raise ValueError('FAIL_CLOSED_HOST_SUMMARY_MISMATCH')
    receipt={'schema':'devtool.fc08.real.host.capture.v1','status':'PASS_EVIDENCE_CAPTURE_ONLY',
        'github':{'run_id':run_id,'attempt':1,'commit':sha,'tree':tree,'branch':BRANCH},'lanes':records,
        'host_qualified':False,'device_qualified':False,'release_qualified':False}
    (dest/'AUDIT.json').write_text(json.dumps(receipt,indent=2,sort_keys=True)+'\n')
    (dest/'SHA256SUMS.txt').write_text(''.join(sha_bytes(x.read_bytes())+'  '+x.relative_to(dest).as_posix()+'\n' for x in sorted(dest.rglob('*')) if x.is_file() and x.name!='SHA256SUMS.txt'))
    print('PASS_FC08_REAL_HOST_EVIDENCE_CAPTURE run_id='+str(run_id)+' lanes=4/4 qualified=false\nFC08_REAL_HOST_EVIDENCE_DIR='+str(dest),flush=True)
    return 0

if __name__=='__main__':
    try:sys.exit(main())
    except (RuntimeError,ValueError,KeyError,OSError,subprocess.SubprocessError,TimeoutError) as exc:
        print('BLOCKED_FC08_REAL_HOST_CAPTURE:'+type(exc).__name__+':'+str(exc)[:400],file=sys.stderr)
        sys.exit(2)
