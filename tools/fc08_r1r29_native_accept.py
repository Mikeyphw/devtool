#!/usr/bin/env python3
"""R1R29 single native Termux acceptance entrypoint: no signing or release.

Executes actual CMake/CTest, Meson, ELF and owned-source cleanup through the
reviewed R1R28 native runner. Optionally inspects a user ZIP read-only and
creates/deletes an EXPLICITLY authorized, isolated storage canary.
"""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile

SCHEMA='devtool.fc08.r1r29.native-and-storage-acceptance.v1'


def _load(name: str):
    source=Path(__file__).resolve().with_name(name)
    spec=importlib.util.spec_from_file_location('devtool_fc08_r1r29_reviewed_'+name.replace('.','_'),source)
    if spec is None or spec.loader is None:
        raise ValueError('REVIEWED_NATIVE_SOURCE_UNAVAILABLE')
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    if Path(mod.__file__).resolve()!=source:
        raise ValueError('REVIEWED_NATIVE_SOURCE_MISMATCH')
    return mod


def accept(root:Path, *, artifact:Path|None=None, probe_dir:Path|None=None,
           approve_canary:bool=False):
    native=_load('fc08_r1r28_termux.py')
    storage=_load('fc08_r1r29_storage_probe.py')
    if approve_canary != (probe_dir is not None):
        raise ValueError('CANARY_EXPLICIT_OPTIN_REQUIRED')
    # Never start a device/package/consumer action. Native's own source and
    # working-tree guards run before any external filesystem canary.
    result=native.native_accept(root)
    if artifact is None and approve_canary:
        raise ValueError('ARTIFACT_REQUIRED_FOR_STORAGE_CANARY')
    obs=storage.probe(artifact,directory=probe_dir,approve_canary=approve_canary) if artifact else None
    canary_pass = bool(obs and obs.get('shared_storage_owned_canary_passed') is True)
    if approve_canary and not canary_pass:
        raise ValueError('STORAGE_CANARY_INCONCLUSIVE')
    return {'schema':SCHEMA,'source':result['source'],
            'native':result,'storage':obs,
            'native_termux_arm64_acceptance_passed':True,
            'shared_storage_owned_canary_passed':canary_pass,
            'real_user_artifact_cleanup_qualified':False,
            'android_signing_qualified':False,'device_installation_qualified':False,
            'consumer_qualified':False,'fc08_overall_qualified':False,
            'release_qualified':False}


def save(out:Path,obj:dict):
    if out.is_symlink() or (out.exists() and not out.is_file()):raise ValueError('UNSAFE_OUTPUT')
    out.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(prefix='.fc08-r1r29-',dir=out.parent)
    try:
        with os.fdopen(fd,'w') as f:
            json.dump(obj,f,sort_keys=True,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
        os.replace(temp,out)
    finally:
        if os.path.lexists(temp):os.unlink(temp)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--repo',type=Path,default=Path.cwd())
    parser.add_argument('--artifact',type=Path,help='Existing ZIP: read-only metadata probe')
    parser.add_argument('--probe-dir',type=Path,help='Create only an owned scratch canary with explicit consent')
    parser.add_argument('--yes-create-owned-canary',action='store_true')
    parser.add_argument('--out',type=Path,required=True)
    a=parser.parse_args()
    try:
        proof=accept(a.repo,artifact=a.artifact,probe_dir=a.probe_dir,
                     approve_canary=a.yes_create_owned_canary)
        save(a.out,proof)
    except (OSError,ValueError,RuntimeError):
        print('BLOCKED_FC08_R1R29_NATIVE_ACCEPTANCE_NOT_QUALIFIED',file=sys.stderr)
        return 2
    print('PASS_FC08_R1R29_NATIVE_AND_STORAGE_NON_RELEASE')
    return 0
if __name__=='__main__':raise SystemExit(main())
