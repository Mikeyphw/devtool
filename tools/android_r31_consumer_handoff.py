#!/usr/bin/env python3
"""ANDROID-R31 read-only consumer handoff. No POST, download, install or release."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

from devtool.actions_android_handoff import handoff
from devtool.github_actions import GitHubActionsError


def atomic_json(path:Path,data:dict)->None:
    if path.is_symlink() or (path.exists() and not path.is_file()) or path.parent.is_symlink():
        raise ValueError('UNSAFE_RECEIPT_DESTINATION')
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(prefix='.android-r31-',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            os.fchmod(stream.fileno(),0o600)
            json.dump(data,stream,indent=2,sort_keys=True)
            stream.write('\n');stream.flush();os.fsync(stream.fileno())
        os.replace(temp,path)
    finally:
        if os.path.lexists(temp):os.unlink(temp)


def main()->int:
    p=argparse.ArgumentParser()
    p.add_argument('--repo',type=Path,required=True)
    p.add_argument('--collected',type=Path,required=True)
    p.add_argument('--consumer',choices=('relatorio','telegram'),required=True)
    p.add_argument('--repository',required=True)
    p.add_argument('--source-sha',required=True)
    p.add_argument('--run-id',type=int,required=True)
    p.add_argument('--run-attempt',type=int,required=True)
    p.add_argument('--package',required=True)
    p.add_argument('--signer-sha256',required=True)
    p.add_argument('--workflow',required=True)
    p.add_argument('--variant',required=True)
    p.add_argument('--format',choices=('apk','aab'),default='apk')
    p.add_argument('--role',choices=('application','instrumentation'),default='application')
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    try:
        if a.collected.is_symlink() or a.collected.stat().st_size>1024*1024:
            raise ValueError('UNSAFE_INPUT')
        doc=json.loads(a.collected.read_text('utf-8'))
        result=handoff(a.repo,doc,consumer=a.consumer,repository=a.repository,
                       source_sha=a.source_sha,run_id=a.run_id,run_attempt=a.run_attempt,
                       package=a.package,signer_sha256=a.signer_sha256,workflow=a.workflow,
                       variant=a.variant,fmt=a.format,role=a.role)
        atomic_json(a.out,result)
    except (GitHubActionsError,OSError,ValueError,TypeError,UnicodeError) as exc:
        reason=str(exc) if isinstance(exc,GitHubActionsError) and str(exc).startswith('ANDROID_R31_') else 'UNVERIFIED_INPUT'
        print('BLOCKED_'+reason,file=sys.stderr)
        return 2
    print('PASS_ANDROID_R31_SIGNED_PACKAGE_REGISTRY_HANDOFF_NON_RELEASE')
    return 0

if __name__=='__main__':raise SystemExit(main())
