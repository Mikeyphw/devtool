"""Source-bound native acceptance for ACTIONS-A04 generated workflow invocation hotfix."""
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

PARENT = '8706a4330ef42b1eadd2b6762d71ef4d71e17cfb'
PATHS = (
 'devtool/actions_adoption.py', 'devtool/actions_a04_acceptance.py',
 'docs/DEVTOOL_ACTIONS_ADOPTION.md', 'docs/DEVTOOL_ACTIONS_PROMISE_AUDIT.md',
 'docs/DEVTOOL_IMPROVEMENTS.md', 'tests/test_actions_a04_runtime.py',
)
TESTS = (
 'tests/test_actions_a04_runtime.py',
 'tests/test_actions_a03_adoption.py',
 'tests/test_actions_ux02_handoff.py',
)

def sha(data:bytes)->str:return hashlib.sha256(data).hexdigest()
def git(root:Path,*args:str)->str:
 p=subprocess.run(['git','-C',str(root),*args],capture_output=True,text=True,timeout=20)
 if p.returncode:raise ValueError('Git history unavailable: '+' '.join(args))
 return p.stdout.strip()

def main(argv:list[str]|None=None)->int:
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--repo',type=Path,required=True)
 args=parser.parse_args(argv)
 root=args.repo.expanduser().resolve()
 try:
  head=git(root,'rev-parse','HEAD')
  if git(root,'rev-parse','HEAD^')!=PARENT:
   raise ValueError('A04 source is not a direct child of accepted A03 '+PARENT)
  if git(root,'status','--porcelain','--untracked-files=normal'):
   raise ValueError('Source worktree must be clean')
  prior=Path.home()/'.local/state/devtool/actions/a03/acceptance-20261019T115408Z.json'
  if not prior.is_file() or prior.is_symlink():raise ValueError('Native A03 acceptance receipt unavailable')
  accepted=json.loads(prior.read_text())
  if (accepted.get('overall')!='PASS_ACTIONS_A03_LOCAL_ACCEPTANCE'
      or accepted.get('source_commit')!=PARENT
      or accepted.get('pytest_passed')!=47
      or accepted.get('pytest_skipped')!=0):
   raise ValueError('A03 source acceptance mismatch')
  ptree=git(root,'rev-parse','HEAD^^{tree}')
  if accepted.get('source_tree')!=ptree:
   raise ValueError('A03 acceptance source tree differs from A04 parent')
  hashes={}
  for rel in PATHS:
   p=root/rel
   if p.is_symlink() or not p.is_file():raise ValueError('Source missing/symlinked: '+rel)
   raw=p.read_bytes()
   saved=subprocess.run(['git','-C',str(root),'show','HEAD:'+rel],capture_output=True,timeout=20)
   if saved.returncode or saved.stdout!=raw:raise ValueError('Source differs from committed tree: '+rel)
   hashes[rel]=sha(raw)
  ledger=subprocess.run(['git','-C',str(root),'show','HEAD^:docs/DEVTOOL_IMPROVEMENTS.md'],capture_output=True,check=True).stdout
  if not (root/'docs/DEVTOOL_IMPROVEMENTS.md').read_bytes().startswith(ledger):
   raise ValueError('Improvements ledger history altered')
  proc=subprocess.run([sys.executable,'-m','pytest','-q',*TESTS],cwd=root,capture_output=True,text=True,timeout=220)
  if proc.returncode:raise ValueError('A04 tests failed: '+proc.stdout[-1200:]+proc.stderr[-300:])
  found=re.search(r'(\d+) passed(?:, (\d+) skipped)?',proc.stdout)
  if not found:raise ValueError('Test receipt counts unavailable')
  passed=int(found.group(1)); skipped=int(found.group(2) or 0)
  if passed<20 or skipped:raise ValueError('Expected full source/CLI regression suite')
  rec={'schema_version':1,'producer':'devtool-actions-a04-local-acceptance',
       'overall':'PASS_ACTIONS_A04_LOCAL_ACCEPTANCE',
       'source_commit':head,'parent_commit':PARENT,'source_tree':git(root,'rev-parse','HEAD^{tree}'),
       'source_sha256':hashes,'pytest_passed':passed,'pytest_skipped':skipped,
       'pytest_output_sha256':sha((proc.stdout+proc.stderr).encode()),
       'prior_a03_receipt_sha256':sha(prior.read_bytes()),
       'remote_dispatch':False,'consumer_ci_qualified':False,'release_qualified':False,
       'formal_h1_closed':False,'formal_h2_closed':False,
       'timestamp_utc':datetime.now(timezone.utc).isoformat()}
  directory=Path.home()/'.local/state/devtool/actions/a04';directory.mkdir(parents=True,exist_ok=True)
  dest=directory/('acceptance-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
  fd=os.open(dest,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
  with os.fdopen(fd,'w') as out:json.dump(rec,out,sort_keys=True,indent=2);out.write('\n')
  print(json.dumps({'overall':rec['overall'],'pytest_passed':passed,'pytest_skipped':skipped,
                    'receipt':str(dest),'source_commit':head},sort_keys=True))
  return 0
 except (OSError,ValueError,TypeError,subprocess.CalledProcessError,subprocess.TimeoutExpired,json.JSONDecodeError) as exc:
  print(json.dumps({'overall':'REFUSED_ACTIONS_A04_ACCEPTANCE','reason':str(exc)},sort_keys=True),file=sys.stderr)
  return 2

if __name__=='__main__':raise SystemExit(main())
