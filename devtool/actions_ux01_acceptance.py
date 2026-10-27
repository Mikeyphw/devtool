"""Native source-bound local Actions UX01 acceptance; no GitHub dispatch or release."""
from __future__ import annotations

import argparse
from datetime import datetime,timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

PATHS = ('devtool/actions_workspace.py','devtool/actions_ux01_acceptance.py',
         'devtool/actions_cli.py','devtool/github_actions.py', 'devtool/core_cli_schema.json',
         'tests/test_actions_ux01_workspace.py','docs/DEVTOOL_ACTIONS_WORKSPACE.md',
         'docs/DEVTOOL_IMPROVEMENTS.md')
MESSAGE='feat(actions): add read-only source-aware local operator workspace'
TESTS=('tests/test_actions_ux01_workspace.py', 'tests/test_actions_g03_compact_narrow.py',
       'tests/test_actions_g03_pipeline_interfaces.py','tests/test_actions_03_run_lifecycle.py',
       'tests/test_actions_02_workflow_dispatch.py','tests/test_core_x01_registry.py')

def git(root:Path,*args:str) -> str:
    p=subprocess.run(['git','-C',str(root),*args],capture_output=True,text=True,timeout=15)
    if p.returncode:raise RuntimeError('Git source identity unavailable')
    return p.stdout.strip()

def sha(path:Path) ->str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def qualify(root:Path)->dict:
    if git(root,'status','--porcelain=v1','--untracked-files=all'):
        raise RuntimeError('Actions UX acceptance requires clean source')
    source=git(root,'rev-parse','HEAD')
    tree=git(root,'rev-parse','HEAD^{tree}')
    if git(root,'log','-1','--format=%s')!=MESSAGE:
        raise RuntimeError('Actions UX01 native auto-commit is not HEAD')
    if git(root,'log','-1','--format=%s','HEAD^')!='fix(security): consolidate final H1 H2 consumer hardening and evidence contracts':
        raise RuntimeError('Required final hardening source is not direct parent')
    changed=sorted(git(root,'diff-tree','--no-commit-id','--name-only','-r','HEAD').splitlines())
    if changed != sorted(PATHS):
        raise RuntimeError('Actions UX01 commit scope mismatch')
    if not (root/'docs/RMP_FINAL_HARDENING.md').is_file():
        raise RuntimeError('Required final H1/H2 source contract absent')
    digests={p:sha(root/p) for p in PATHS if (root/p).is_file() and not (root/p).is_symlink()}
    if len(digests)!=len(PATHS):raise RuntimeError('Source file missing or symlinked')
    cmd=[sys.executable,'-m','pytest','-q',*TESTS,'--disable-warnings']
    run=subprocess.run(cmd,cwd=root,capture_output=True,text=True,timeout=180)
    output=(run.stdout+'\n'+run.stderr)[-24000:]
    m=re.search(r'(\d+) passed',output)
    s=re.search(r'(\d+) skipped',output)
    passed=int(m.group(1)) if m else 0
    skipped=int(s.group(1)) if s else 0
    textual=importlib.util.find_spec('textual') is not None
    if run.returncode or passed<65-3 or (textual and skipped):
        raise RuntimeError('Actions UX01 acceptance tests not fully satisfied')
    return {'schema_version':1,'producer':'devtool-actions-ux01-acceptance',
            'overall':'PASS_ACTIONS_UX01_LOCAL_ACCEPTANCE' if textual and not skipped else 'PASS_ACTIONS_UX01_CORE_UI_PENDING',
            'pytest_passed':passed,'pytest_skipped':skipped,'textual_present':textual,
            'source_commit':source,'source_tree':tree,'source_sha256':digests,
            'pytest_sha256':hashlib.sha256(output.encode()).hexdigest(),
            'timestamp_utc':datetime.now(timezone.utc).isoformat(),
            'automatic_push':False,'remote_workflows_dispatched':False,
            'source_attested_remote':False,'release_qualified':False,
            'formal_h1_closed':False,'formal_h2_closed':False}

def main()->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,required=True)
    a=parser.parse_args()
    try:
        root=a.repo.resolve()
        data=qualify(root)
        state=Path(os.environ.get('XDG_STATE_HOME') or str(Path.home()/'.local/state'))/'devtool/actions/ux01'
        state.mkdir(parents=True,exist_ok=True,mode=0o700)
        file=state/('acceptance-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.json')
        fd,name=tempfile.mkstemp(dir=state,prefix='.ux01-',suffix='.tmp')
        try:
            with os.fdopen(fd,'w') as f:
                os.fchmod(f.fileno(),0o600)
                json.dump(data,f,sort_keys=True,indent=2)
                f.write('\n');f.flush();os.fsync(f.fileno())
            os.replace(name,file)
        finally:
            if os.path.exists(name):os.unlink(name)
        print(json.dumps({**{k:data[k] for k in ('overall','pytest_passed','pytest_skipped','textual_present','source_commit')},'receipt':str(file)},sort_keys=True))
        return 0
    except (OSError,RuntimeError,subprocess.TimeoutExpired) as exc:
        print(json.dumps({'overall':'BLOCKED_ACTIONS_UX01','reason':str(exc),'release_qualified':False}),file=sys.stderr)
        return 2
if __name__=='__main__':raise SystemExit(main())
