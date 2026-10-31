#!/usr/bin/env python3
"""Manually authorized bounded dispatch experiments; no automatic fanout."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import hashlib

SCRIPTS={
    'cycle':'fc08_dispatch_cycle.py',
    'rejection-retry':'fc08_dispatch_rejection_retry.py',
    'lost-response':'fc08_dispatch_uncertain_outcome.py',
}
def plan(scenario:str, *, authorized:bool, env:dict[str,str]):
    if scenario not in (*SCRIPTS,'all'):
        raise RuntimeError('INVALID_SCENARIO')
    if not authorized or env.get('FC08_DISPATCH_AUTHORIZED')!='true':
        raise RuntimeError('EXPLICIT_OPT_IN_REQUIRED')
    if (env.get('GITHUB_ACTIONS')!='true' or env.get('GITHUB_REPOSITORY')!='Mikeyphw/devtool'
        or env.get('GITHUB_REF')!='refs/heads/main'
        or len(env.get('GITHUB_SHA',''))!=40):
        raise RuntimeError('HOSTED_SOURCE_IDENTITY_REQUIRED')
    return list(SCRIPTS if scenario=='all' else [scenario])
def execute(scenario:str, *, authorized:bool, env:dict[str,str], out:Path):
    selected=plan(scenario,authorized=authorized,env=env)
    root=Path(__file__).resolve().parents[1]
    if out.is_symlink():raise RuntimeError('UNSAFE_OUTPUT')
    with tempfile.TemporaryDirectory(prefix='fc08-r1r28-matrix-') as td:
        results=[]
        for name in selected:
            receipt=Path(td)/(name+'.json')
            cp=subprocess.run([sys.executable,str(root/'tools'/SCRIPTS[name]),'--out',str(receipt)],
                              cwd=root,env=env,capture_output=True,timeout=600)
            if cp.returncode or not receipt.is_file() or receipt.is_symlink():
                raise RuntimeError('SCENARIO_FAILED_'+name.replace('-','_').upper())
            payload=receipt.read_bytes()
            if len(payload)>65536:raise RuntimeError('UNBOUNDED_RECEIPT')
            doc=json.loads(payload)
            if any(doc.get(k) is not False for k in ('release_qualified','fc08_overall_qualified')):
                raise RuntimeError('UNSAFE_QUALIFICATION_PROMOTION')
            # Retain each reviewed source receipt in the Actions artifact. A
            # digest without the matching bytes is not independently auditable.
            saved=out.parent/('r1r28-matrix-'+name+'.json')
            if saved.is_symlink():raise RuntimeError('UNSAFE_SCENARIO_OUTPUT')
            saved.parent.mkdir(parents=True,exist_ok=True)
            saved.write_bytes(payload)
            results.append({'scenario':name,'receipt_name':saved.name,
                            'receipt_sha256':hashlib.sha256(payload).hexdigest(),
                            'status':'PASS', 'release_qualified':False})
    result={'schema':'devtool.fc08.r1r28.resilience-matrix.v1','source_sha':env['GITHUB_SHA'],
            'scenarios':results,'real_network_timeout_or_5xx_proven':False,
            'unknown_outcome_auto_retry_permitted':False,'release_qualified':False,
            'fc08_overall_qualified':False}
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    return result

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--scenario',choices=[*SCRIPTS,'all'],required=True)
    ap.add_argument('--out',required=True,type=Path);ap.add_argument('--authorized',action='store_true');a=ap.parse_args()
    try:execute(a.scenario,authorized=a.authorized,env=dict(os.environ),out=a.out)
    except (RuntimeError,ValueError,OSError,subprocess.SubprocessError,json.JSONDecodeError) as exc:
        print('BLOCKED_FC08_R1R28_MATRIX_'+(str(exc) if isinstance(exc,RuntimeError) and str(exc).replace('_','').replace('-','').isupper() else type(exc).__name__),file=sys.stderr)
        return 2
    print('PASS_FC08_R1R28_MATRIX_EXPLICIT_NON_RELEASE')
    return 0
if __name__=='__main__':sys.exit(main())
