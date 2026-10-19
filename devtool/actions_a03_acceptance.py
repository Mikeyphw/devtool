"""Source-bound ACTIONS-A03 native acceptance for repository adoption contracts."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

PARENT='95ed8f774b5729396a3dae0acb389853e90dd95e'
PATHS=(
 'devtool/actions_adoption.py',
 'devtool/actions_a03_acceptance.py',
 'devtool/actions_cli.py',
 'devtool/core_cli_schema.json',
 'docs/DEVTOOL_ACTIONS_ADOPTION.md',
 'tests/test_actions_a03_adoption.py',
 'docs/DEVTOOL_IMPROVEMENTS.md',
)
TESTS=(
 'tests/test_actions_a03_adoption.py',
 'tests/test_exo_target_local_jobs_adoption.py',
 'tests/test_exo_target_local_jobs_preflight_discovery.py',
 'tests/test_actions_ux02_handoff.py',
 'tests/test_actions_ux01_workspace.py',
)


def sha(data:bytes)->str:return hashlib.sha256(data).hexdigest()


def git(root,*args):
 p=subprocess.run(['git','-C',str(root),*args],capture_output=True,text=True,timeout=20)
 if p.returncode:raise ValueError('Git history/source query failed: '+' '.join(args))
 return p.stdout.strip()


def main():
 a=argparse.ArgumentParser(description=__doc__);a.add_argument('--repo',type=Path,required=True)
 args=a.parse_args();root=args.repo.expanduser().resolve()
 try:
  head=git(root,'rev-parse','HEAD')
  if git(root,'rev-parse','HEAD^')!=PARENT:raise ValueError('Source commit parent differs from accepted UX02 commit')
  if git(root,'status','--porcelain','--untracked-files=normal'):
   raise ValueError('Working tree not clean')
  prev=Path.home()/'.local/state/devtool/actions/ux02/acceptance-20261019T104444Z.json'
  if prev.is_symlink() or not prev.is_file():raise ValueError('UX02 native receipt unavailable')
  previous=json.loads(prev.read_text())
  if previous.get('source_commit')!=PARENT or previous.get('overall')!='PASS_ACTIONS_UX02_LOCAL_ACCEPTANCE' or previous.get('pytest_passed')!=90:
   raise ValueError('Native UX02 parent acceptance mismatched')
  hashes={}
  for rel in PATHS:
   f=root/rel
   if f.is_symlink() or not f.is_file():raise ValueError('Source file missing or symlinked: '+rel)
   raw=f.read_bytes()
   reference=subprocess.run(['git','-C',str(root),'show','HEAD:'+rel],capture_output=True,timeout=20)
   if reference.returncode or reference.stdout!=raw:raise ValueError('Source bytes differ from HEAD: '+rel)
   hashes[rel]=sha(raw)
  if importlib.util.find_spec('textual') is None:raise ValueError('Native Textual dependency missing; UX01 Textual qualification cannot be carried forward')
  proc=subprocess.run([sys.executable,'-m','pytest','-q',*TESTS],cwd=root,capture_output=True,text=True,timeout=220)
  if proc.returncode:raise ValueError('A03 regression suite failed: '+proc.stdout[-1200:]+proc.stderr[-300:])
  import re
  found=re.search(r'(\d+) passed(?:, (\d+) skipped)?',proc.stdout)
  if not found:raise ValueError('Unable to parse test count')
  passed=int(found.group(1));skipped=int(found.group(2) or 0)
  if passed<40 or skipped:raise ValueError('Expected full Textual-enabled adoption/regression suite')
  rec={'schema_version':1,'producer':'devtool-actions-a03-local-acceptance',
       'overall':'PASS_ACTIONS_A03_LOCAL_ACCEPTANCE',
       'source_commit':head,'parent_commit':PARENT,
       'source_tree':git(root,'rev-parse','HEAD^{tree}'),
       'source_sha256':hashes,'pytest_passed':passed,'pytest_skipped':skipped,
       'pytest_output_sha256':sha((proc.stdout+proc.stderr).encode()),
       'prior_ux02_receipt_sha256':sha(prev.read_bytes()),
       'remote_dispatch':False,'formal_h1_closed':False,'formal_h2_closed':False,
       'release_qualified':False,
       'timestamp_utc':datetime.now(timezone.utc).isoformat()}
  out=Path.home()/'.local/state/devtool/actions/a03';out.mkdir(parents=True,exist_ok=True)
  file=out/('acceptance-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.json')
  fd=os.open(file,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
  with os.fdopen(fd,'w') as f:json.dump(rec,f,sort_keys=True,indent=2);f.write('\n')
  print(json.dumps({'overall':rec['overall'],'pytest_passed':passed,'pytest_skipped':skipped,
                   'receipt':str(file),'source_commit':head},sort_keys=True))
  return 0
 except (OSError,ValueError,TypeError,subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
  print(json.dumps({'overall':'REFUSED_ACTIONS_A03_ACCEPTANCE','reason':str(exc)},sort_keys=True),file=sys.stderr)
  return 2

if __name__=='__main__':raise SystemExit(main())
