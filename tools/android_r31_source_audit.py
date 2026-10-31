#!/usr/bin/env python3
"""ANDROID-R31 host-safe source guard for machine-readable non-release evidence."""
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

_FILES=('devtool/actions_android.py','devtool/actions_android_handoff.py',
        'tools/android_r31_consumer_handoff.py','tests/test_android_r31_consumer_handoff.py')


def source_audit(repo:Path)->dict:
    repo=repo.resolve(strict=True)
    def git(*args:str)->str:
        p=subprocess.run(['git','-C',str(repo),*args],capture_output=True,text=True,timeout=15)
        if p.returncode:raise ValueError('SOURCE_GIT_UNAVAILABLE')
        return p.stdout.strip()
    sha=git('rev-parse','HEAD');tree=git('rev-parse','HEAD^{tree}')
    if not re.fullmatch('[a-f0-9]{40}',sha) or not re.fullmatch('[a-f0-9]{40}',tree):
        raise ValueError('SOURCE_IDENTITY_INVALID')
    if git('status','--porcelain','--untracked-files=all'):
        raise ValueError('SOURCE_WORKTREE_DIRTY')
    checks={}
    for name in _FILES:
        file=repo/name
        if file.is_symlink() or not file.is_file() or git('ls-files','--error-unmatch',name)!=name:
            raise ValueError('REVIEWED_SOURCE_UNAVAILABLE')
        checks[name]=hashlib.sha256(file.read_bytes()).hexdigest()
    return {'schema':'devtool.android.r31.source-gate.v1','status':'PASS',
            'source':{'sha':sha,'tree':tree},'reviewed_source_sha256':checks,
            'real_consumer_hosted_build_executed':False,
            'real_apk_signature_independently_verified':False,
            'consumer_installation_qualified':False,'release_qualified':False,
            'fc09_qualified':False}


def main()->int:
    p=argparse.ArgumentParser();p.add_argument('--repo',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    try:
        doc=source_audit(a.repo)
        dest=a.out
        if dest.is_symlink() or (dest.exists() and not dest.is_file()) or dest.parent.is_symlink():
            raise ValueError('UNSAFE_OUTPUT')
        dest.parent.mkdir(parents=True,exist_ok=True)
        fd,tmp=tempfile.mkstemp(prefix='.android-r31-',dir=dest.parent)
        try:
            with os.fdopen(fd,'w') as stream:
                os.fchmod(stream.fileno(),0o600)
                json.dump(doc,stream,sort_keys=True,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
            os.replace(tmp,dest)
        finally:
            if os.path.lexists(tmp):os.unlink(tmp)
    except (ValueError,OSError,subprocess.SubprocessError):
        print('BLOCKED_ANDROID_R31_SOURCE_GATE',file=sys.stderr)
        return 2
    print('PASS_ANDROID_R31_SOURCE_GATE_NON_RELEASE')
    return 0
if __name__=='__main__':raise SystemExit(main())
