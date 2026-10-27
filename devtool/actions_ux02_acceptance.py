"""Native local acceptance for ACTIONS-UX02; reuses source-bound UX01 proof."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

MESSAGE='feat(actions): bind exact run handoff and pre-POST DAG source'
PARENT_MESSAGE='feat(actions): add read-only source-aware local operator workspace'
FILES=(
    'devtool/actions_cli.py','devtool/actions_pipeline.py','devtool/core_cli_schema.json',
    'devtool/github_actions.py','devtool/actions_handoff.py',
    'devtool/actions_ux02_acceptance.py','tests/test_actions_06_pipeline_dag.py',
    'tests/test_actions_ux02_handoff.py','docs/DEVTOOL_ACTIONS_EXACT_HANDOFF.md',
    'docs/DEVTOOL_IMPROVEMENTS.md')
TESTS=(
    'tests/test_actions_ux02_handoff.py','tests/test_actions_06_pipeline_dag.py',
    'tests/test_actions_02_workflow_dispatch.py','tests/test_actions_04_artifact_retrieval.py',
    'tests/test_actions_ux01_workspace.py','tests/test_core_x01_registry.py')


def git(root,*args):
    p=subprocess.run(['git','-C',str(root),*args],capture_output=True,timeout=15)
    if p.returncode:raise RuntimeError('Git source identity unavailable')
    return p.stdout.decode(errors='replace').strip()


def sha(b):return hashlib.sha256(b).hexdigest()


def qualify(root):
    if git(root,'status','--porcelain=v1','--untracked-files=all'):
        raise RuntimeError('Worktree must be clean')
    commit=git(root,'rev-parse','HEAD')
    parent=git(root,'rev-parse','HEAD^')
    if git(root,'log','-1','--format=%s')!=MESSAGE or git(root,'log','-1','--format=%s','HEAD^')!=PARENT_MESSAGE:
        raise RuntimeError('Native UX02→UX01 commit lineage does not match')
    changed=sorted(git(root,'diff-tree','--no-commit-id','--name-only','-r','HEAD').splitlines())
    if changed!=sorted(FILES):raise RuntimeError('UX02 commit scope mismatch')
    hashes={}
    for name in FILES:
        path=root/name
        if path.is_symlink() or not path.is_file():raise RuntimeError('Missing/linked source: '+name)
        hashes[name]=sha(path.read_bytes())
    state=Path(os.environ.get('XDG_STATE_HOME') or str(Path.home()/'.local/state'))/'devtool/actions/ux01'
    if state.is_symlink() or not state.is_dir():raise RuntimeError('Prior native UX01 receipt directory missing')
    valid=[]
    for path in sorted(state.glob('acceptance-*.json')):
        if path.is_symlink() or not path.is_file() or path.stat().st_size>256*1024:continue
        try: r=json.loads(path.read_text())
        except (ValueError,OSError,UnicodeError):continue
        if (r.get('overall') != 'PASS_ACTIONS_UX01_LOCAL_ACCEPTANCE' or
            r.get('source_commit')!=parent or r.get('textual_present') is not True or
            type(r.get('pytest_passed')) is not int or r['pytest_passed'] < 79 or
            r.get('pytest_skipped') != 0 or
            r.get('source_tree') != git(root,'rev-parse','HEAD~1^{tree}')):
            continue
        original=r.get('source_sha256')
        if not isinstance(original,dict) or not original:continue
        if any(not isinstance(value,str) or not re.fullmatch('[0-9a-f]{64}',value) or
               sha(subprocess.run(['git','-C',str(root),'show','HEAD^:'+name],
                                 capture_output=True,timeout=15,check=True).stdout)!=value
               for name,value in original.items()):
            continue
        valid.append(path)
    if not valid:raise RuntimeError('No verified UX01 native receipt matching direct parent source')
    cmd=[sys.executable,'-m','pytest','-q',*TESTS,'--disable-warnings']
    test=subprocess.run(cmd,cwd=root,capture_output=True,text=True,timeout=240)
    output=(test.stdout+'\n'+test.stderr)[-30000:]
    m=re.search(r'(\d+) passed',output)
    skipped=re.search(r'(\d+) skipped',output)
    passed=int(m.group(1)) if m else 0
    if test.returncode or passed<90 or skipped:
        raise RuntimeError('Exact-run integration suite incomplete/failed')
    return {'schema_version':1,'producer':'devtool-actions-ux02-local-acceptance',
            'overall':'PASS_ACTIONS_UX02_LOCAL_ACCEPTANCE',
            'source_commit':commit,'source_tree':git(root,'rev-parse','HEAD^{tree}'),
            'parent_ux01_commit':parent,'parent_ux01_receipt':str(valid[-1]),
            'source_sha256':hashes,'pytest_passed':passed,'pytest_skipped':0,
            'pytest_output_sha256':sha(output.encode()),
            'timestamp_utc':datetime.now(timezone.utc).isoformat(),
            'actual_github_dispatches':0,'automatic_push':False,
            'release_qualified':False,'formal_h1_closed':False,'formal_h2_closed':False}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo',type=Path,required=True)
    a=ap.parse_args()
    try:
        root=a.repo.expanduser().resolve()
        data=qualify(root)
        home=Path(os.environ.get('XDG_STATE_HOME') or str(Path.home()/'.local/state'))/'devtool/actions/ux02'
        home.mkdir(parents=True,exist_ok=True,mode=0o700)
        name='acceptance-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.json'
        fd,tmp=tempfile.mkstemp(dir=home,prefix='.ux02-',suffix='.tmp')
        try:
            with os.fdopen(fd,'w') as f:
                os.fchmod(f.fileno(),0o600)
                json.dump(data,f,indent=2,sort_keys=True)
                f.write('\n');f.flush();os.fsync(f.fileno())
            path=home/name
            os.replace(tmp,path)
        finally:
            if os.path.exists(tmp):os.unlink(tmp)
        print(json.dumps({'overall':data['overall'],'pytest_passed':data['pytest_passed'],
             'receipt':str(path),'source_commit':data['source_commit']},sort_keys=True))
        return 0
    except (RuntimeError,ValueError,OSError,subprocess.TimeoutExpired,subprocess.CalledProcessError) as e:
        print(json.dumps({'overall':'BLOCKED_ACTIONS_UX02','reason':str(e),
                          'release_qualified':False}),file=sys.stderr)
        return 2

if __name__=='__main__':raise SystemExit(main())
