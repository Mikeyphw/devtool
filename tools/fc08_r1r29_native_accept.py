#!/usr/bin/env python3
"""R1R29 single native Termux acceptance entrypoint: no signing or release.

Executes actual CMake/CTest, Meson, ELF and owned-source cleanup through the
reviewed R1R28 native runner. Optionally inspects a user ZIP read-only and
creates/deletes an EXPLICITLY authorized, isolated storage canary.
"""
from __future__ import annotations
import argparse
import re
import subprocess
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
           approve_canary:bool=False, on_stage=None):
    if on_stage:on_stage('reviewed-native-import')
    native=_load('fc08_r1r28_termux.py')
    storage=_load('fc08_r1r29_storage_probe.py')
    if approve_canary != (probe_dir is not None):
        raise ValueError('CANARY_EXPLICIT_OPTIN_REQUIRED')
    # Never start a device/package/consumer action. Native's own source and
    # working-tree guards run before any external filesystem canary.
    if on_stage:on_stage('native-build')
    result=native.native_accept(root)
    if artifact is None and approve_canary:
        raise ValueError('ARTIFACT_REQUIRED_FOR_STORAGE_CANARY')
    if on_stage:on_stage('shared-storage-observation')
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


SAFE_ERRORS={
    'NATIVE_TERMUX_ARM64_REQUIRED','NATIVE_TERMUX_PREFIX_UNAVAILABLE',
    'NATIVE_SOURCE_WORKTREE_DIRTY','NATIVE_SOURCE_DIRTY_AFTER_TEST',
    'TEMP_OWNERSHIP_UNSAFE','NATIVE_ELF_ARM64_ABI_MISMATCH',
    'SYMLINK_SOURCE_ACCEPTED','SYMLINK_VICTIM_MODIFIED',
    'NATIVE_REPLACED_SOURCE_DELETED','NATIVE_REPLACEMENT_MODIFIED',
    'NATIVE_OWNED_CLEANUP_INCOMPLETE',
    'CANARY_EXPLICIT_OPTIN_REQUIRED','ARTIFACT_REQUIRED_FOR_STORAGE_CANARY',
    'STORAGE_CANARY_INCONCLUSIVE','ACTUAL_NATIVE_TERMUX_REQUIRED',
    'ARTIFACT_SYMLINK_REFUSED','INSPECTED_ARTIFACT_NOT_REGULAR_FILE',
    'CANARY_DIRECTORY_REQUIRED','REVIEWED_NATIVE_SOURCE_UNAVAILABLE',
    'REVIEWED_NATIVE_SOURCE_MISMATCH','UNSAFE_OUTPUT',
}
TOOLS={'GIT','CMAKE','CTEST','MESON','NINJA','CLANG'}
COMMANDS={'GIT','CMAKE','CTEST','MESON','NINJA','CLANG'}

def safe_category(exc:BaseException)->str:
    # Never leak exception messages from libraries, subprocesses or user paths.
    value=str(exc)
    if type(exc) in (RuntimeError,ValueError) and value in SAFE_ERRORS:
        return value
    if type(exc) is RuntimeError and value.startswith('NATIVE_TOOL_UNAVAILABLE_') and value.split('NATIVE_TOOL_UNAVAILABLE_',1)[1] in TOOLS:
        return value
    if type(exc) is RuntimeError and value.startswith('NATIVE_COMMAND_FAILED_') and value.split('NATIVE_COMMAND_FAILED_',1)[1] in COMMANDS:
        return value
    if isinstance(exc,subprocess.TimeoutExpired):
        return 'NATIVE_COMMAND_TIMEOUT'
    if isinstance(exc,subprocess.CalledProcessError):
        return 'GIT_SOURCE_COMMAND_FAILED'
    return 'NATIVE_ACCEPTANCE_UNCLASSIFIED'


def blocked_receipt(*,phase:str,category:str)->dict:
    return {'schema':SCHEMA,'status':'BLOCKED_UNQUALIFIED',
            'phase':phase,'error_category':category,
            'native_termux_arm64_acceptance_passed':False,
            'shared_storage_owned_canary_passed':False,
            'real_user_artifact_cleanup_qualified':False,
            'android_signing_qualified':False,'device_installation_qualified':False,
            'consumer_qualified':False,'fc08_overall_qualified':False,
            'release_qualified':False}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--repo',type=Path,default=Path.cwd())
    parser.add_argument('--artifact',type=Path,help='Existing ZIP: read-only metadata probe')
    parser.add_argument('--probe-dir',type=Path,help='Create only an owned scratch canary with explicit consent')
    parser.add_argument('--yes-create-owned-canary',action='store_true')
    parser.add_argument('--out',type=Path,required=True)
    a=parser.parse_args()
    stage='preflight'
    def report_stage(value):
        nonlocal stage
        stage=value
    try:
        stage='native-environment'
        _load('fc08_r1r28_termux.py').environment()
        proof=accept(a.repo,artifact=a.artifact,probe_dir=a.probe_dir,
                     approve_canary=a.yes_create_owned_canary,on_stage=report_stage)
        stage='success-receipt-write'
        save(a.out,proof)
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as exc:
        category=safe_category(exc)
        try:
            save(a.out,blocked_receipt(phase=stage,category=category))
        except (OSError,ValueError):
            print('BLOCKED_FC08_R1R29_NATIVE_RECEIPT_WRITE_FAILED',file=sys.stderr)
        print('BLOCKED_FC08_R1R29_NATIVE_ACCEPTANCE:'+category,file=sys.stderr)
        return 2
    print('PASS_FC08_R1R29_NATIVE_AND_STORAGE_NON_RELEASE')
    return 0
if __name__=='__main__':raise SystemExit(main())
