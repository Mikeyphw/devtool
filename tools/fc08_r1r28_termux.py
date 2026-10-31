#!/usr/bin/env python3
"""R1R28: native ARM64 Termux real-build acceptance; never runs on CI Ubuntu.

No root operations, no package installs, no changes outside private tempdir.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

SCHEMA = 'devtool.fc08.termux-arm64-native-acceptance.v1'

def environment():
    prefix = Path(sys.prefix)
    if (platform.system() != 'Linux' or platform.machine().lower() not in {'aarch64', 'arm64'}
            or not str(prefix).startswith('/data/data/com.termux/files/usr')):
        raise RuntimeError('NATIVE_TERMUX_ARM64_REQUIRED')
    if not Path('/data/data/com.termux/files/usr').exists():
        raise RuntimeError('NATIVE_TERMUX_PREFIX_UNAVAILABLE')
    for name in ('git', 'cmake', 'ctest', 'meson', 'ninja', 'clang'):
        if shutil.which(name) is None:
            raise RuntimeError('NATIVE_TOOL_UNAVAILABLE_' + name.upper())

def run(args, *, cwd: Path):
    result = subprocess.run(args, cwd=cwd, capture_output=True, timeout=120)
    if result.returncode:
        raise RuntimeError('NATIVE_COMMAND_FAILED_' + Path(args[0]).name.upper())
    return {'returncode': result.returncode,
            'stdout_sha256': hashlib.sha256(result.stdout).hexdigest(),
            'stderr_sha256': hashlib.sha256(result.stderr).hexdigest()}

def source(root):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args], text=True, timeout=20).strip()
    return {'sha': git('rev-parse', 'HEAD'), 'tree': git('rev-parse', 'HEAD^{tree}')}

def native_accept(root: Path, *, temp_root: Path | None = None, verify_host: bool = True):
    if verify_host: environment()
    root = root.resolve(strict=True)
    src = source(root)
    with tempfile.TemporaryDirectory(prefix='fc08-r1r28-native-', dir=temp_root) as folder:
        path = Path(folder)
        if path.is_symlink() or path.stat().st_uid != os.getuid():
            raise RuntimeError('TEMP_OWNERSHIP_UNSAFE')
        cmake = path/'cmake'; cmake.mkdir()
        (cmake/'main.c').write_text('int main(void){return 0;}\n')
        (cmake/'CMakeLists.txt').write_text('cmake_minimum_required(VERSION 3.16)\nproject(fc08_native C)\nenable_testing()\nadd_executable(fc08native main.c)\nadd_test(NAME native_exec COMMAND fc08native)\n')
        cbuild = cmake/'build'
        steps = {'cmake_configure': run(['cmake','-S',str(cmake),'-B',str(cbuild),'-G','Ninja'],cwd=path),
                 'cmake_build':run(['cmake','--build',str(cbuild)],cwd=path),
                 'ctest':run(['ctest','--test-dir',str(cbuild),'--output-on-failure'],cwd=path)}
        binary = cbuild/'fc08native'
        data=binary.read_bytes()
        if (len(data) < 20 or data[:4] != b'\x7fELF' or data[4] != 2 or
                int.from_bytes(data[18:20], 'little') != 183):
            raise RuntimeError('NATIVE_ELF_ARM64_ABI_MISMATCH')
        steps['arm64_elf_sha256']=hashlib.sha256(data).hexdigest()
        meson = path/'meson'; meson.mkdir()
        (meson/'main.c').write_text('int main(void){return 0;}\n')
        (meson/'meson.build').write_text("project('fc08_native', 'c')\ntest('native_exec', executable('fc08native', 'main.c'))\n")
        mbuild = meson/'build'
        steps['meson_setup']=run(['meson','setup',str(mbuild),str(meson)],cwd=path)
        steps['meson_compile']=run(['meson','compile','-C',str(mbuild)],cwd=path)
        steps['meson_test']=run(['meson','test','-C',str(mbuild),'--print-errorlogs'],cwd=path)
        # Owned-temp recovery/refusal: never follow a symlink when deleting a consumed source.
        from devtool.artifact_cleanup_worker import cleanup_source_snapshot
        victim = path/'victim';victim.write_bytes(b'keep')
        link = path/'symlink';link.symlink_to(victim)
        try:
            cleanup_source_snapshot(link)
        except ValueError:
            pass
        else:
            raise RuntimeError('SYMLINK_SOURCE_ACCEPTED')
        if victim.read_bytes()!=b'keep':
            raise RuntimeError('SYMLINK_VICTIM_MODIFIED')
        steps['owned_temp_and_symlink_refusal']='PASS'
    return {'schema': SCHEMA, 'source':src,'host':'native-termux-arm64',
            'observed_at':datetime.now(timezone.utc).isoformat(),
            'checks':steps, 'native_arm64_compilation_passed':True,
            'device_signing_qualified':False,'installed_android_qualified':False,
            'consumer_qualified':False,'fc08_overall_qualified':False,'release_qualified':False,
            'transaction_rollback_integration_qualified':False}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--repo',type=Path,default=Path.cwd());ap.add_argument('--out',type=Path,required=True);a=ap.parse_args()
    try:
        data=native_accept(a.repo)
    except (OSError,RuntimeError,subprocess.SubprocessError) as exc:
        # No raw exception/path outputs; failures remain bounded and unqualified.
        print('BLOCKED_FC08_R1R28_TERMUX_NATIVE_'+(str(exc) if isinstance(exc,RuntimeError) and str(exc).isupper() else type(exc).__name__),file=sys.stderr)
        return 2
    dest=a.out.expanduser().absolute()
    if dest.is_symlink():return 2
    dest.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile('w',dir=dest.parent,delete=False) as f:
        json.dump(data,f,sort_keys=True,indent=2);f.write('\n');f.flush();os.fsync(f.fileno());name=f.name
    os.replace(name,dest)
    print('PASS_FC08_R1R28_NATIVE_TERMUX_ARM64_SOURCE_BOUND_NON_RELEASE')
    return 0
if __name__=='__main__':sys.exit(main())
