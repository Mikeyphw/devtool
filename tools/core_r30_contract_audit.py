#!/usr/bin/env python3
"""Read-only exact-source CORE-R30 OCI + generated-guide drift admission gate.

Checks the original canonical OCI-05 pinned resource bytes and Devtool's own
complete-feature guide generator. It does NOT regenerate/repair resources,
execute a container, qualify a signer, or mutate the repository.
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

SCHEMA='devtool.core-r30.source-contract-readonly-audit.v1'

def source(repo:Path):
    def git(*args):return subprocess.check_output(['git','-C',str(repo),*args],text=True,timeout=20).strip()
    sha,tree=git('rev-parse','HEAD'),git('rev-parse','HEAD^{tree}')
    if not re.fullmatch('[0-9a-f]{40}',sha) or not re.fullmatch('[0-9a-f]{40}',tree):
        raise ValueError('SOURCE_IDENTITY_INVALID')
    if git('status','--porcelain','--untracked-files=all'):
        raise ValueError('SOURCE_WORKTREE_DIRTY')
    return {'sha':sha,'tree':tree}

def check(repo:Path):
    repo=repo.resolve(strict=True)
    identity=source(repo)
    env=dict(os.environ)
    env['PYTHONPATH']=str(repo)+os.pathsep+env.get('PYTHONPATH','')
    script=repo/'tools'/'generate_complete_feature_guides.py'
    if not script.is_file() or script.is_symlink():raise ValueError('GUIDE_GENERATOR_UNAVAILABLE')
    cp=subprocess.run([sys.executable,str(script),'--check'],cwd=repo,env=env,capture_output=True,timeout=90)
    if cp.returncode or b'current' not in cp.stdout:
        raise ValueError('GENERATED_GUIDE_DRIFT')
    # Run verified contract loader in an isolated child to avoid trusting
    # shadowed devtool module imports or leaking private exception bodies.
    code='''from devtool.oci_image import load_android_oci_contract\nc=load_android_oci_contract()\nassert c["definition_id"]=="android-arm64-api36-v2"\n'''
    op=subprocess.run([sys.executable,'-c',code],cwd=repo,env=env,capture_output=True,timeout=30)
    if op.returncode:raise ValueError('OCI_CANONICAL_EMBEDDED_DIGEST_DRIFT')
    if source(repo)!=identity:raise ValueError('SOURCE_CHANGED_DURING_AUDIT')
    return {'schema':SCHEMA,'source':identity,'status':'PASS',
            'checks':{'oci_canonical_embedded_digest':'PASS',
                      'generated_guide_current':'PASS',
                      'source_unchanged':'PASS'},
            'windows_signing_qualified':False,'android_device_qualified':False,
            'native_termux_qualified':False,'consumer_qualified':False,
            'fc08_overall_qualified':False,'release_qualified':False}

def _atomic(path:Path,doc:dict):
    path=path.absolute()
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError('UNSAFE_OUTPUT')
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(prefix='.core-r30-audit-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as f:
            json.dump(doc,f,sort_keys=True,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
        os.replace(temp,path)
    finally:
        if os.path.lexists(temp):os.unlink(temp)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--repo',type=Path,default=Path.cwd())
    parser.add_argument('--out',type=Path,required=True)
    a=parser.parse_args()
    try:
        doc=check(a.repo);_atomic(a.out,doc)
    except (OSError,ValueError,subprocess.SubprocessError):
        print('BLOCKED_CORE_R30_SOURCE_CONTRACT_UNQUALIFIED',file=sys.stderr)
        return 2
    print('PASS_CORE_R30_SOURCE_CONTRACT_NON_RELEASE');return 0
if __name__=='__main__':raise SystemExit(main())
