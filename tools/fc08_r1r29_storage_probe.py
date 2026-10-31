#!/usr/bin/env python3
"""R1R29: Android shared-storage diagnostics without touching downloaded ZIPs.

Default mode is read-only. An explicitly approved canary creates/deletes ONLY a
new O_EXCL owned random file in the selected directory; never deletes anything
else or relaxes Devtool's LIFE cleanup ownership checks.
"""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import stat
import sys
import tempfile
import uuid

SCHEMA='devtool.fc08.r1r29.shared-storage-probe.v1'


def _native():
    if (platform.system()!='Linux' or platform.machine().lower() not in ('aarch64','arm64')
        or not sys.prefix.startswith('/data/data/com.termux/files/usr')):
        raise ValueError('ACTUAL_NATIVE_TERMUX_REQUIRED')


def _snapshot(path:Path)->dict:
    s=path.lstat()
    if not stat.S_ISREG(s.st_mode):
        raise ValueError('INSPECTED_ARTIFACT_NOT_REGULAR_FILE')
    return {'dev':s.st_dev,'inode':s.st_ino,'size':s.st_size,
            'mtime_ns':s.st_mtime_ns,'ctime_ns':s.st_ctime_ns,'mode':stat.S_IMODE(s.st_mode)}


def inspect(path:Path)->dict:
    """Metadata only, never read/modify the artifact's actual file contents."""
    if path.is_symlink():raise ValueError('ARTIFACT_SYMLINK_REFUSED')
    first=_snapshot(path)
    second=_snapshot(path)
    return {'path_sha256':hashlib.sha256(str(path.absolute()).encode()).hexdigest(),
            'kind':'regular-file','stable_during_observation':first==second,
            'identity_field_names':['dev','inode','size','mtime_ns','ctime_ns'],
            'storage_fingerprint':hashlib.sha256(json.dumps(first,sort_keys=True).encode()).hexdigest(),
            'bytes_read_from_artifact':0,'source_deleted':False,
            'safe_to_retry_unlink':False}


def canary(directory:Path,*, approved:bool, require_native:bool=True)->dict:
    if not approved:raise ValueError('EXPLICIT_CANARY_AUTHORIZATION_REQUIRED')
    if require_native:_native()
    real=directory.resolve(strict=True)
    if not real.is_dir():raise ValueError('CANARY_DIRECTORY_REQUIRED')
    s=real.stat()
    if not stat.S_ISDIR(s.st_mode):raise ValueError('CANARY_DIRECTORY_REQUIRED')
    # Directory is exclusively resolved before opening. No pre-existing file is
    # ever used as a canary; O_EXCL is mandatory.
    flags=os.O_RDONLY|getattr(os,'O_DIRECTORY',0)|getattr(os,'O_NOFOLLOW',0)
    dfd=os.open(real,flags)
    name='.fc08-r1r29-owned-'+uuid.uuid4().hex+'.probe'
    created=False
    try:
        if (os.fstat(dfd).st_dev,os.fstat(dfd).st_ino)!=(s.st_dev,s.st_ino):
            raise ValueError('CANARY_PARENT_CHANGED')
        fileflags=os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0)
        fd=os.open(name,fileflags,0o600,dir_fd=dfd)
        created=True
        try:
            os.write(fd,b'FC08_R1R29_PRIVATE_CANARY\n');os.fsync(fd)
            owned=os.fstat(fd)
        finally:os.close(fd)
        observed=os.stat(name,dir_fd=dfd,follow_symlinks=False)
        if ((owned.st_dev,owned.st_ino,owned.st_size,owned.st_mtime_ns,owned.st_ctime_ns) !=
            (observed.st_dev,observed.st_ino,observed.st_size,observed.st_mtime_ns,observed.st_ctime_ns)):
            raise ValueError('CANARY_IDENTITY_DRIFT')
        if (os.fstat(dfd).st_dev,os.fstat(dfd).st_ino)!=(s.st_dev,s.st_ino):
            raise ValueError('CANARY_PARENT_CHANGED')
        os.unlink(name,dir_fd=dfd)
        created=False
        return {'status':'PASS','canary_created':True,'canary_removed':True,
                'parent_dev':s.st_dev,'parent_inode':s.st_ino,
                'canary_identity_consistent':True,
                'real_artifact_touched':False,'cleanup_policy_relaxed':False}
    except (ValueError,OSError):
        # A failed identity check NEVER authorizes unlink, including this canary.
        return {'status':'BLOCKED_UNQUALIFIED','canary_created':created,
                'canary_removed':False,'residue_name':name if created else None,
                'real_artifact_touched':False,'cleanup_policy_relaxed':False,
                'manual_review_required':True}
    finally:os.close(dfd)


def probe(artifact:Path,*, directory:Path|None=None,approve_canary:bool=False, require_native:bool=True)->dict:
    if require_native:_native()
    observation=inspect(artifact)
    result={'schema':SCHEMA,'observed_at':datetime.now(timezone.utc).isoformat(),
            'host':'native-termux-arm64' if require_native else 'offline-contract',
            'artifact':observation,'canary':None,'artifact_deleted':False,
            'actual_shared_storage_cleanup_qualified':False,
            'native_termux_qualified':False,'fc08_overall_qualified':False,
            'release_qualified':False}
    if approve_canary:
        if directory is None:raise ValueError('EXPLICIT_PROBE_DIRECTORY_REQUIRED')
        result['canary']=canary(directory,approved=True,require_native=require_native)
        result['shared_storage_owned_canary_passed']=result['canary']['status']=='PASS'
    return result


def main()->int:
    p=argparse.ArgumentParser()
    p.add_argument('--artifact',type=Path,required=True,help='Existing artifact: metadata only; never deleted')
    p.add_argument('--probe-dir',type=Path)
    p.add_argument('--yes-create-owned-canary',action='store_true')
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    try:
        if a.yes_create_owned_canary != (a.probe_dir is not None):
            raise ValueError('EXPLICIT_CANARY_DIRECTORY_AND_OPTIN_REQUIRED')
        result=probe(a.artifact,directory=a.probe_dir,approve_canary=a.yes_create_owned_canary)
        if a.out.is_symlink() or (a.out.exists() and not a.out.is_file()):
            raise ValueError('UNSAFE_OUTPUT')
        a.out.parent.mkdir(parents=True,exist_ok=True)
        fd,tmp=tempfile.mkstemp(prefix='.fc08-r1r29-',dir=a.out.parent)
        try:
            with os.fdopen(fd,'w') as f:
                json.dump(result,f,sort_keys=True,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
            os.replace(tmp,a.out)
        finally:
            if os.path.lexists(tmp):os.unlink(tmp)
        if result['canary'] is not None and result['canary']['status']!='PASS':
            print('BLOCKED_FC08_R1R29_SHARED_STORAGE_CANARY_UNQUALIFIED',file=sys.stderr)
            return 2
    except (OSError,ValueError,TypeError):
        print('BLOCKED_FC08_R1R29_STORAGE_PROBE',file=sys.stderr)
        return 2
    print('PASS_FC08_R1R29_STORAGE_OBSERVATION_NON_RELEASE')
    return 0
if __name__=='__main__':raise SystemExit(main())
