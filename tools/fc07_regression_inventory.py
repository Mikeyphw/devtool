#!/usr/bin/env python3
"""FC-07: test inventory, bounded pytest collection/execution and truthful receipts.

No authority promotion: a shard PASS means only that those tests exited 0.
No network or package installation is performed by this inventory tool itself.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

SHARDS = 12
SCHEMA = 'devtool-fc07-regression-inventory-v1'

def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(['git','-C',str(root),*args],text=True,timeout=30).strip()

def modules(root: Path) -> list[str]:
    files = _git(root, 'ls-files','--','tests/test_*.py').splitlines()
    selected=sorted(set(x for x in files if x.startswith('tests/test_') and x.endswith('.py')))
    # Devtool applies source-bound overlays before staging their Git commit.
    # The exact reviewed FC-07 module must participate during this interval;
    # do not sweep in arbitrary untracked files or silently omit it.
    pending='tests/test_fc07_regression_authority.py'
    if (root/pending).is_file() and pending not in selected:
        selected.append(pending)
        selected.sort()
    if not selected or any(not (root/x).is_file() or (root/x).is_symlink() for x in selected):
        raise RuntimeError('incomplete or unsupported tracked test inventory')
    return selected

def shard_id(module: str) -> int:
    # Entire test module stays in one shard; stable across interpreters and PYTHONHASHSEED.
    return int(hashlib.sha256(module.encode('utf8')).hexdigest()[:8],16) % SHARDS

def manifest(root: Path) -> dict:
    files=modules(root)
    out=[{'shard':i,'files':[p for p in files if shard_id(p)==i]} for i in range(SHARDS)]
    assert sorted(sum((row['files'] for row in out),[])) == files
    return {'schema':SCHEMA,'git_commit':_git(root,'rev-parse','HEAD'),
            'git_tree':_git(root,'rev-parse','HEAD^{tree}'),
            'tracked_test_modules':len(files),'shards':out,
            'status':'INVENTORY_ONLY_NO_TEST_EXECUTION'}

def _run(args: list[str], root: Path, log: Path, timeout: int) -> tuple[str,int | None,float]:
    env=dict(os.environ)
    env['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
    env['PYTEST_ADDOPTS']=''
    started=time.monotonic()
    with log.open('wb') as output:
        process = subprocess.Popen(args,cwd=root,stdout=output,stderr=subprocess.STDOUT,
                                   start_new_session=True,env=env)
        try:
            ret=process.wait(timeout=timeout)
            return ('PASS' if ret==0 else 'FAILED',ret,round(time.monotonic()-started,3))
        except subprocess.TimeoutExpired:
            # Terminate the entire process group, not just pytest's parent.
            try: os.killpg(process.pid,signal.SIGKILL)
            except (ProcessLookupError,PermissionError,AttributeError): process.kill()
            process.wait()
            return ('TIMEOUT',None,round(time.monotonic()-started,3))

def _junit_counts(file:Path)->dict[str,int] | None:
    if not file.is_file(): return None
    try:
        tree=ET.parse(file)
        cases=tree.findall('.//testcase')
        return {'cases':len(cases),'passed':sum(not any(x.tag in ('failure','error','skipped') for x in case) for case in cases),
                'failed':sum(any(x.tag=='failure' for x in case) for case in cases),
                'errors':sum(any(x.tag=='error' for x in case) for case in cases),
                'skipped':sum(any(x.tag=='skipped' for x in case) for case in cases)}
    except ET.ParseError:return None

def execute(root:Path, command:str, shard:int | None, timeout:int, output:Path) -> dict:
    if timeout<1 or timeout>3600:raise ValueError('timeout must be 1..3600 seconds')
    m=manifest(root)
    files=([p for row in m['shards'] for p in row['files']] if shard is None else m['shards'][shard]['files'])
    if command=='run' and shard is None:raise ValueError('run requires --shard; all shards must be accounted for individually')
    output.parent.mkdir(parents=True,exist_ok=True)
    log=output.with_suffix('.log')
    junit=output.with_suffix('.junit.xml')
    argv=[sys.executable,'-m','pytest','-q','-p','no:cacheprovider','--disable-warnings']
    if command=='collect': argv += ['--collect-only']
    else: argv += ['--junitxml',str(junit.resolve())]
    argv+=files
    status,exit_code,duration=_run(argv,root,log,timeout)
    counts=_junit_counts(junit) if command=='run' else None
    effective='PASS' if status=='PASS' and (command=='collect' or counts is not None) else ('INCOMPLETE' if status=='PASS' else status)
    return {'schema':SCHEMA,'command':command,'shard':shard,'module_count':len(files),
            'git_commit':m['git_commit'],'git_tree':m['git_tree'],
            'status':effective,'pytest_exit_code':exit_code,'timeout_seconds':timeout,
            'duration_seconds':duration,'counts':counts,'log_path':str(log),'junit_path':str(junit) if command=='run' else None,
            'log_sha256':hashlib.sha256(log.read_bytes()).hexdigest(),
            'external_host_device_release_qualified':False}

def main()->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['inventory','collect','run'])
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output',type=Path)
    parser.add_argument('--shard',type=int,choices=range(SHARDS))
    parser.add_argument('--timeout',type=int,default=180)
    args=parser.parse_args()
    root=args.root.resolve()
    result=manifest(root) if args.command=='inventory' else execute(root,args.command,args.shard,args.timeout,(args.output or root/'fc07-regression-result.json').resolve())
    raw=json.dumps(result,indent=2,sort_keys=True)+'\n'
    if args.output:args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(raw)
    else:sys.stdout.write(raw)
    return 0 if args.command=='inventory' or result['status']=='PASS' else 1

if __name__=='__main__':raise SystemExit(main())
