#!/usr/bin/env python3
"""R1R28-H1: source-clean, opt-in bounded hosted dispatch experiments.

Child results are held outside the checkout until the LAST scenario has
completed, because each child independently verifies git status is clean.
On failure, preserve bounded, redacted diagnostics, never child stdout/stderr.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

SCRIPTS={
    'cycle':'fc08_dispatch_cycle.py',
    'rejection-retry':'fc08_dispatch_rejection_retry.py',
    'lost-response':'fc08_dispatch_uncertain_outcome.py',
}
SAFE_FAILURE_CATEGORIES=frozenset({
    'FC08_R1R26_SOURCE_IDENTITY_INVALID',
    'FC08_R1R26_WORKFLOW_TOKEN_MISSING',
    'FC08_R1R26_POLL_BOUNDS_INVALID',
    'FC08_R1R26_UNSAFE_OR_NONEMPTY_STATE_ROOT',
    'FC08_R1R26_DEFN_REJECTION_UNPROVEN',
    'FC08_R1R26_INVALID_REF_NOT_REJECTED',
    'FC08_R1R26_DEFN_REJECTION_NOT_JOURNALED',
    'FC08_R1R26_RETRY_NOT_CORRELATED',
    'FC08_R1R26_RESUME_DUPLICATE_POST_OR_ID_MISMATCH',
    'FC08_R1R26_JOURNAL_STATE_MISMATCH',
    'FC08_R1R26_UNSAFE_JOURNAL_FILE',
    'FC08_R1R26_INVALID_JOURNAL_RECORD',
    'FC08_R1R26_SOURCE_SHA_INVALID',
    'FC08_R1R26_UNREVIEWED_CLIENT',
    'FC08_DISPATCH_OPT_IN_OR_HOST_IDENTITY_MISSING',
    'FC08_DISPATCH_INVALID_SOURCE_OR_RUN_ID',
    'FC08_DISPATCH_SOURCE_MISMATCH',
    'FC08_DISPATCH_DIRTY_REVIEWED_SOURCE',
    'EXTERNAL_DISPATCH_UNVERIFIED',
})

class MatrixBlocked(RuntimeError):
    def __init__(self, reason: str, scenario: str | None = None):
        super().__init__(reason)
        self.reason=reason
        self.scenario=scenario


def plan(scenario:str, *, authorized:bool, env:dict[str,str]):
    if scenario not in (*SCRIPTS,'all'):
        raise RuntimeError('INVALID_SCENARIO')
    if not authorized or env.get('FC08_DISPATCH_AUTHORIZED')!='true':
        raise RuntimeError('EXPLICIT_OPT_IN_REQUIRED')
    if (env.get('GITHUB_ACTIONS')!='true' or env.get('GITHUB_REPOSITORY')!='Mikeyphw/devtool'
        or env.get('GITHUB_REF')!='refs/heads/main'
        or not re.fullmatch(r'[a-f0-9]{40}',env.get('GITHUB_SHA',''))):
        raise RuntimeError('HOSTED_SOURCE_IDENTITY_REQUIRED')
    return list(SCRIPTS if scenario=='all' else [scenario])


def _safe_failure_receipt(raw:bytes | None, scenario:str, reason:str) -> dict:
    """Never publish child raw failure bodies, tokens, paths or exception strings."""
    result={'scenario':scenario,'status':'BLOCKED_UNQUALIFIED',
            'failure_reason':reason,'release_qualified':False,
            'fc08_overall_qualified':False}
    if raw is not None:
        result['original_receipt_sha256']=hashlib.sha256(raw).hexdigest()
        if len(raw)<=65536:
            try:
                doc=json.loads(raw)
            except (ValueError,UnicodeError):
                doc=None
            if isinstance(doc,dict):
                category=doc.get('error_category')
                if isinstance(category,str) and category in SAFE_FAILURE_CATEGORIES:
                    result['error_category']=category
                else:
                    result['error_category']='EXTERNAL_DISPATCH_UNVERIFIED'
                if doc.get('status')=='BLOCKED_UNQUALIFIED':
                    result['child_status']='BLOCKED_UNQUALIFIED'
    return result


def _stage_bytes(path:Path, data:bytes) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise MatrixBlocked('UNSAFE_OUTPUT')
    if path.parent.is_symlink():
        raise MatrixBlocked('UNSAFE_OUTPUT_PARENT')
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.parent.is_symlink():
        raise MatrixBlocked('UNSAFE_OUTPUT_PARENT')
    # Do not write partial JSON records if the runner is interrupted.
    fd,name=tempfile.mkstemp(prefix='.fc08-matrix-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as fp:
            fp.write(data);fp.flush();os.fsync(fp.fileno())
        os.replace(name,path)
    finally:
        if os.path.lexists(name):os.unlink(name)


def _json_bytes(data:dict) -> bytes:
    return (json.dumps(data,indent=2,sort_keys=True)+'\n').encode('utf-8')


def execute(scenario:str, *, authorized:bool, env:dict[str,str], out:Path):
    selected=plan(scenario,authorized=authorized,env=env)
    root=Path(__file__).resolve().parents[1]
    if out.is_symlink() or (out.exists() and not out.is_file()):
        raise RuntimeError('UNSAFE_OUTPUT')
    results=[]
    staged=[]
    failed=None
    # Do NOT write any out/fc08 JSON until all children have finished. Each
    # child calls source_identity() and rejects newly untracked repository files.
    with tempfile.TemporaryDirectory(prefix='fc08-r1r28-matrix-') as td:
        for name in selected:
            receipt=Path(td)/(name+'.json')
            try:
                cp=subprocess.run([sys.executable,str(root/'tools'/SCRIPTS[name]),
                                   '--out',str(receipt)],cwd=root,env=env,
                                  capture_output=True,timeout=600)
                child_reason='CHILD_EXIT_NONZERO' if cp.returncode else None
            except subprocess.TimeoutExpired:
                child_reason='CHILD_TIMEOUT'
            except (OSError,subprocess.SubprocessError):
                child_reason='CHILD_EXECUTION_ERROR'
            raw=None
            if receipt.is_symlink():
                child_reason='UNSAFE_CHILD_RECEIPT'
            elif receipt.is_file():
                raw=receipt.read_bytes()
                if len(raw)>65536:child_reason='UNBOUNDED_CHILD_RECEIPT'
            elif child_reason is None:
                child_reason='MISSING_CHILD_RECEIPT'
            if child_reason is None:
                try:
                    doc=json.loads(raw)
                except (ValueError,UnicodeError):
                    child_reason='INVALID_CHILD_RECEIPT'
                else:
                    if (not isinstance(doc,dict) or
                        any(doc.get(k) is not False for k in
                            ('release_qualified','fc08_overall_qualified'))):
                        child_reason='UNSAFE_QUALIFICATION_PROMOTION'
            if child_reason is not None:
                safe=_safe_failure_receipt(raw,name,child_reason)
                staged.append((name,_json_bytes(safe)))
                results.append({'scenario':name,'status':'BLOCKED_UNQUALIFIED',
                                'receipt_name':'r1r28-matrix-'+name+'.json',
                                'receipt_sha256':hashlib.sha256(staged[-1][1]).hexdigest(),
                                'release_qualified':False})
                failed={'scenario':name,'reason':child_reason,
                        'error_category':safe.get('error_category','EXTERNAL_DISPATCH_UNVERIFIED')}
                break
            staged.append((name,raw))
            results.append({'scenario':name,'receipt_name':'r1r28-matrix-'+name+'.json',
                            'receipt_sha256':hashlib.sha256(raw).hexdigest(),
                            'status':'PASS','release_qualified':False})
    # ONLY NOW publish source-bound original success receipts and sanitized
    # failed receipts (no child stderr/stdout or private API response bodies).
    for name,content in staged:
        _stage_bytes(out.parent/('r1r28-matrix-'+name+'.json'),content)
    result={'schema':'devtool.fc08.r1r28.resilience-matrix.v1',
            'source_sha':env['GITHUB_SHA'],'scenarios':results,
            'status':'BLOCKED_UNQUALIFIED' if failed else 'PASS',
            'real_network_timeout_or_5xx_proven':False,
            'unknown_outcome_auto_retry_permitted':False,
            'release_qualified':False,'fc08_overall_qualified':False}
    if failed:result['failure']=failed
    _stage_bytes(out,_json_bytes(result))
    if failed:raise MatrixBlocked('SCENARIO_FAILED_'+failed['scenario'].replace('-','_').upper(),failed['scenario'])
    return result


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--scenario',choices=[*SCRIPTS,'all'],required=True)
    ap.add_argument('--out',required=True,type=Path)
    ap.add_argument('--authorized',action='store_true')
    a=ap.parse_args()
    try:execute(a.scenario,authorized=a.authorized,env=dict(os.environ),out=a.out)
    except (MatrixBlocked,RuntimeError,ValueError,OSError,json.JSONDecodeError) as exc:
        category=exc.reason if isinstance(exc,MatrixBlocked) else 'UNVERIFIED_EXECUTION'
        if category not in {'CHILD_EXIT_NONZERO','CHILD_TIMEOUT','CHILD_EXECUTION_ERROR',
                            'UNSAFE_CHILD_RECEIPT','UNBOUNDED_CHILD_RECEIPT',
                            'MISSING_CHILD_RECEIPT','INVALID_CHILD_RECEIPT',
                            'UNSAFE_QUALIFICATION_PROMOTION','UNSAFE_OUTPUT',
                            'UNSAFE_OUTPUT_PARENT'} and not category.startswith('SCENARIO_FAILED_'):
            category='UNVERIFIED_EXECUTION'
        print('BLOCKED_FC08_R1R28_MATRIX_'+category,file=sys.stderr)
        return 2
    print('PASS_FC08_R1R28_MATRIX_EXPLICIT_NON_RELEASE')
    return 0
if __name__=='__main__':sys.exit(main())
