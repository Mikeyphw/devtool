#!/usr/bin/env python3
"""FC08 real-host Devtool runner gate. Observation != host workload != release.

This executes actual Devtool CLI native runners against throwaway tracked fixture
repositories. Every named lane must finish all its required checks. Receipts
contain source SHA/tree, run/attempt and hashes, but never a release PASS.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

SOURCE = Path(__file__).resolve().parents[1]
LANES = ('github-actions-dag','linux-polyglot','windows-dotnet-msvc','macos-native')
EXPECTED = {'linux-polyglot': 'Linux', 'windows-dotnet-msvc': 'Windows', 'macos-native': 'Darwin'}
EXPECTED_CHECKS = {
    'github-actions-dag': ('immutable-source','negative-exit-propagation','dag-fanout','dag-verifier-adversarial','github-live-restricted-dispatch-refusal'),
    'linux-polyglot': ('python','python-uv','python-poetry','python-pdm','go','node','rust','maven','cmake','meson','negative-exit-propagation','linux-lock-drift-refusal','linux-native-abi-refusal','linux-native-abi-malformed-refusal','linux-cargo-frozen-lock-refusal'),
    'windows-dotnet-msvc': ('dotnet-cold-restore','dotnet-warm-restore','dotnet-build','dotnet-trx-coverage','dotnet-pack','dotnet-publish','msvc-cmake','negative-exit-propagation'),
    'macos-native': ('native-cmake','unsupported-host-refusal','negative-exit-propagation'),
}


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(p: Path) -> str:
    return sha_bytes(p.read_bytes())


def git(root: Path, *args: str) -> str:
    v=subprocess.run(['git','-C',str(root),*args],capture_output=True,text=True,timeout=30)
    if v.returncode: raise RuntimeError('GIT_FAILURE '+str(args[:2]))
    return v.stdout.strip()


def identity(env: dict[str,str], *, allow_evidence_dirs: bool = False) -> dict[str,Any]:
    sha=git(SOURCE,'rev-parse','HEAD')
    tree=git(SOURCE,'rev-parse','HEAD^{tree}')
    expected_sha=env.get('GITHUB_SHA','')
    if not re.fullmatch('[0-9a-f]{40}',expected_sha) or expected_sha!=sha:
        raise RuntimeError('REFUSED_SOURCE_SHA_MISMATCH')
    changes=git(SOURCE,'status','--porcelain','--untracked-files=all').splitlines()
    if allow_evidence_dirs:
        changes=[x for x in changes if not (x.startswith('?? fc08-raw/') or x.startswith('?? out/'))]
    if changes:
        raise RuntimeError('REFUSED_DIRTY_HOSTED_SOURCE')
    if env.get('GITHUB_REPOSITORY')!='Mikeyphw/devtool':
        raise RuntimeError('REFUSED_GITHUB_REPOSITORY')
    if not env.get('GITHUB_REF','').startswith('refs/heads/'):
        raise RuntimeError('REFUSED_NONBRANCH_REF')
    if not (env.get('GITHUB_RUN_ID','').isdigit() and int(env['GITHUB_RUN_ID'])>0 and
            env.get('GITHUB_RUN_ATTEMPT','').isdigit() and int(env['GITHUB_RUN_ATTEMPT'])>0):
        raise RuntimeError('REFUSED_INVALID_GITHUB_RUN_IDENTITY')
    return {'repository':env['GITHUB_REPOSITORY'],'sha':sha,'tree':tree,'run_id':int(env['GITHUB_RUN_ID']),
            'attempt':int(env['GITHUB_RUN_ATTEMPT']),'ref':env['GITHUB_REF']}


def _public_diagnostic(data: bytes, *, limit: int = 1900) -> str:
    """Only bounded, redacted subprocess diagnostics may enter public CI receipts."""
    lines = data.decode('utf-8', errors='replace')[-max(1, limit):]
    lines = re.sub(r'(?i)(bearer\s+|authorization[=:]\s*|token[=:]\s*|password[=:]\s*|secret[=:]\s*)[^\s]+',
                   r'\1[REDACTED]', lines)
    lines = re.sub(r'(?i)(https?://)[^/\s:@]+:[^@/\s]+@', r'\1[REDACTED]@', lines)
    return lines


def run(argv: list[str],cwd: Path,*,timeout: int=240,extra_env:dict[str,str]|None=None,
        capture_success_tail:bool=False,drop_msbuild_env:bool=False)->dict[str,Any]:
    env=os.environ.copy();env.update(extra_env or {})
    if drop_msbuild_env:
        # ilammy/msvc-dev-cmd sets Platform=x64 for native cl.exe. A newly
        # generated SDK .sln only has Debug/Release | Any CPU; passing x64 to
        # MSBuild makes even dotnet restore fail with MSB4126. Keep all other
        # native compiler variables intact. Windows env names are insensitive.
        for key in list(env):
            if key.casefold() in ('platform','configuration'):
                del env[key]
    p=subprocess.run(argv,cwd=cwd,capture_output=True,timeout=timeout,env=env)
    # Only failed commands disclose bounded, sanitized diagnostic tails. Never
    # include the process environment, raw payload or bearer tokens in receipts.
    return {'argv':argv,'code':p.returncode,'stdout_sha256':sha_bytes(p.stdout),
            'stderr_sha256':sha_bytes(p.stderr),'stdout_tail':_public_diagnostic(p.stdout) if p.returncode or capture_success_tail else '',
            'stderr_tail':_public_diagnostic(p.stderr) if p.returncode or capture_success_tail else ''}


def must(argv:list[str], cwd:Path,*,timeout:int=240, env:dict[str,str]|None=None,
         capture_success_tail:bool=False,drop_msbuild_env:bool=False)->dict[str,Any]:
    row=run(argv,cwd,timeout=timeout,extra_env=env,capture_success_tail=capture_success_tail,
            drop_msbuild_env=drop_msbuild_env)
    if row['code']:
        # Console-rich Devtool diagnostics are often on stdout, not stderr.
        # Reporting only stderr previously hid the actual native failures.
        raise RuntimeError('NATIVE_WORKLOAD_FAILED code='+str(row['code'])+
            ' command='+str([Path(argv[0]).name,*argv[1:2]])+
            ' stdout_sha256='+row['stdout_sha256']+' stderr_sha256='+row['stderr_sha256']+
            ' stdout_tail='+row['stdout_tail'][-1400:]+ ' stderr_tail='+row['stderr_tail'][-1400:])
    return row


def dotnet_must(argv:list[str],cwd:Path,*,timeout:int=240,env:dict[str,str]|None=None,
                capture_success_tail:bool=False)->dict[str,Any]:
    """One entry point for Windows .NET subprocesses in the real-host fixture."""
    if not argv or argv[0]!='dotnet':
        raise RuntimeError('REFUSED_NON_DOTNET_SUBPROCESS')
    return must(argv,cwd,timeout=timeout,env=env,capture_success_tail=capture_success_tail,
                drop_msbuild_env=True)

def fixture(base:Path, name:str, runner:str, files:dict[str,str],config:str|None=None)->Path:
    folder=base/name;folder.mkdir()
    if config is None:
        config='[project]\nrunner="'+runner+'"\nplatform="server"\n[workspace]\ndefault_target="app"\n[targets.app]\nrunner="'+runner+'"\nroot="."\n'
    files={'.devtool.toml':config, '.gitignore':'.devtool/\nbuild/\nnode_modules/\nobj/\nbin/\ncoverage/\nTestResults/\n',**files}
    for rel,body in files.items():
        p=folder/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(body)
    must(['git','init','-q'],folder)
    must(['git','add','.'],folder)
    must(['git','-c','user.name=FC08 Fixture','-c','user.email=fc08@example.invalid','commit','-qm','source fixture'],folder)
    return folder


def _fixture_termux_environment(*, python_executable: str | None = None,
                                python_prefix: str | None = None) -> dict[str,str]:
    """Restore *observed* Termux identity for nested Devtool fixture commands.

    The outer artifact validator may sanitize HOME/PREFIX before spawning its
    Python tests. On physical Android this yields diagnostic ``host-android``
    rather than native-termux. We must not change production normalization or
    forge a platform selector: only the *actual interpreter* installed under
    the canonical Termux Python prefix can justify restoring that PREFIX to
    the subprocess. Never do so for arbitrary user-supplied interpreters,
    desktop Linux or a guest chroot Python.
    """
    termux_prefix = Path('/data/data/com.termux/files/usr')
    canonical_prefix = termux_prefix.resolve()
    observed = Path(python_prefix if python_prefix is not None else sys.prefix)
    executable = python_executable if python_executable is not None else sys.executable
    resolved = shutil.which(executable) if executable else None
    if not resolved:
        return {}
    try:
        if observed.resolve() == canonical_prefix and Path(resolved).resolve().is_relative_to(canonical_prefix):
            return {'PREFIX':str(termux_prefix)}
    except (OSError, RuntimeError):
        return {}
    return {}


def _devtool_child_environment() -> dict[str, str]:
    """Keep real Windows Devtool CLI pipes UTF-8 without changing .NET/MSVC.

    GHA Windows Python may inherit a locale-specific charmap (e.g. cp1252).
    Rich emits Unicode success glyphs, which can otherwise turn a genuinely
    successful native compilation into a Python encoding exception. Only the
    Devtool CLI child receives this override; no global encoding is changed.
    """
    env = _fixture_termux_environment()
    if platform.system() == 'Windows':
        env.update({'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'})
    return env


def devtool(root:Path,action:str,*,timeout:int=420,solution:str|None=None)->dict[str,Any]:
    argv=[sys.executable,'-m','devtool','--repo',str(root),'--target','app']
    if solution is not None:
        # FC08 creates the Windows solution after fixture initialization.  The
        # real CLI must receive an explicit reviewed target, never guess one.
        if solution != 'FC08.sln' or not (root/solution).is_file() or git(root,'ls-files','--error-unmatch',solution)!=solution:
            raise RuntimeError('REFUSED_MISSING_OR_UNTRACKED_DOTNET_SOLUTION')
        argv.extend(['--solution',solution])
    argv.append(action)
    return must(argv,SOURCE,timeout=timeout,env=_devtool_child_environment(),
                drop_msbuild_env=(solution is not None))


def fail_negative(root:Path)->dict[str,Any]:
    broken=fixture(root,'negative','command',{'sentinel.txt':'refusal\n'},
        '[project]\nrunner="command"\nplatform="server"\nbuild_command="python -c \'import sys;print(987654321);sys.exit(37)\'"\n[workspace]\ndefault_target="app"\n[targets.app]\nrunner="command"\nroot="."\n')
    row=run([sys.executable,'-m','devtool','--repo',str(broken),'--target','app','build'],
            SOURCE,timeout=180,extra_env=_devtool_child_environment())
    if row['code']==0 or '987654321' not in row['stdout_tail'] or 'exit 37' not in row['stdout_tail']:
        raise RuntimeError('REFUSED_NEGATIVE_COMMAND_DID_NOT_EXECUTE_AND_FAIL_AS_EXPECTED')
    return {'rejection_exit':row['code'],'checked':'executed sentinel and true native exit 37'}


def build_cmake(root:Path,name:str,*,expected_compiler:str|None=None)->dict[str,Any]:
    project=fixture(root,name,'cmake',{
        'CMakeLists.txt':'cmake_minimum_required(VERSION 3.20)\nproject(FC08Native CXX)\nenable_testing()\nadd_executable(fc08main main.cpp)\nadd_test(NAME fc08_native COMMAND fc08main)\n',
        'main.cpp':'#include <cstdint>\nint main(){return sizeof(std::uint64_t)==8?0:31;}\n'})
    outputs=[devtool(project,'build',timeout=480),devtool(project,'test',timeout=240)]
    builds=list(project.rglob('CMakeCache.txt'))
    if not builds:raise RuntimeError('MISSING_REAL_CMAKE_CACHE')
    cache=builds[0].read_text(errors='replace')
    if expected_compiler:
        ids=list(project.rglob('CMakeCXXCompiler.cmake'))
        if len(ids)!=1 or expected_compiler.upper() not in ids[0].read_text(errors='replace').upper():
            raise RuntimeError('WRONG_NATIVE_ABI_COMPILER')
    return {'runner':'cmake','operations':['build','test'],'cmake_cache_sha256':sha_file(builds[0]),
            'command_hashes':[x['stdout_sha256'] for x in outputs]}


def build_meson(root:Path)->dict[str,Any]:
    project=fixture(root,'meson','meson',{'meson.build':"project('fc08', 'cpp', default_options : ['cpp_std=c++17'])\ne = executable('fc08main', 'main.cpp')\ntest('fc08_native',e)\n",'main.cpp':'int main(){return sizeof(void*) >= 4 ? 0 : 11;}\n'})
    x=[devtool(project,'build',timeout=480),devtool(project,'test',timeout=240)]
    logs=list(project.rglob('meson-log.txt'))
    if not logs:raise RuntimeError('NO_REAL_MESON_BUILD_LOG')
    return {'runner':'meson','operations':['build','test'],'meson_log_sha256':sha_file(logs[0]),'command_hashes':[a['stdout_sha256'] for a in x]}


def linux_lock_drift_refusal(uv_repo:Path)->dict[str,Any]:
    """Prove real uv locked/offline execution refuses a changed manifest.

    Never mutate a user's project. The disposable uv fixture must already have
    completed its real lock and run. A stale lock must not silently resolve
    online, update itself, or qualify as a valid locked invocation.
    """
    manifest=uv_repo/'pyproject.toml'
    lock=uv_repo/'uv.lock'
    before=manifest.read_bytes()
    lock_before=sha_file(lock)
    if b'version="0.1.0"' not in before:
        raise RuntimeError('FC08_UV_MANIFEST_BASE_UNEXPECTED')
    try:
        manifest.write_bytes(before.replace(b'version="0.1.0"',b'version="0.2.0"',1))
        drift_sha=sha_file(manifest)
        attempt=run(['uv','lock','--check','--offline'],uv_repo,timeout=120)
        if attempt['code']==0:
            raise RuntimeError('FC08_UV_STALE_LOCK_ACCEPTED')
        if sha_file(lock)!=lock_before:
            raise RuntimeError('FC08_UV_LOCK_MUTATED_BY_REFUSAL')
    finally:
        manifest.write_bytes(before)
    restored=must(['uv','lock','--check','--offline'],uv_repo,timeout=120)
    if sha_file(lock)!=lock_before:
        raise RuntimeError('FC08_UV_LOCK_CHANGED_AFTER_RESTORE')
    return {'native':'real uv offline lock check','drift_manifest_sha256':drift_sha,
            'lock_sha256':lock_before,'refusal_exit':attempt['code'],
            'refusal_stdout_sha256':attempt['stdout_sha256'],
            'refusal_stderr_sha256':attempt['stderr_sha256'],
            'restored_check_sha256':restored['stdout_sha256']}


def _cargo_frozen_refusal_diagnostic(refused:dict[str,Any])->dict[str,str]:
    """Classify Cargo's *primary error*, never an unrelated nonzero exit.

    Cargo <=0.93 said "needs to be updated" and >=0.94 says "cannot
    update the lock file". Both must explicitly attribute the refusal to
    --locked/--frozen. Only bounded, already-redacted tails are examined.
    No raw diagnostic, temporary file path, or credentials enter PASS receipts.
    """
    raw=(str(refused.get('stderr_tail',''))+'\n'+str(refused.get('stdout_tail','')))
    plain=re.sub(r'\x1b\[[0-9;]*[A-Za-z]','',raw)
    errors=[line.strip() for line in plain.splitlines()
            if re.match(r'^\s*error\s*:',line,re.IGNORECASE)]
    patterns={
        'CARGO_LOCKFILE_UPDATE_BLOCKED_LEGACY':
            r'^error\s*:\s*the lock file\s+(?:.+?\s+)?needs to be updated\s+but\s+--(?:locked|frozen)\s+was passed to prevent this\.?$',
        'CARGO_LOCKFILE_UPDATE_BLOCKED_MODERN':
            r'^error\s*:\s*cannot update the lock file\s+(?:.+?\s+)?because\s+--(?:locked|frozen)\s+was passed to prevent this\.?$',
    }
    kinds=[key for line in errors for key,regex in patterns.items()
           if re.fullmatch(regex,line,re.IGNORECASE)]
    category=kinds[0] if len(errors)==1 and len(kinds)==1 else (
        'MISSING_CARGO_ERROR_LINE' if not errors else 'NON_LOCKFILE_CARGO_ERROR')
    if errors:
        # These are public only on failure. Redact environment-specific paths
        # and access credentials again; avoid echoing arbitrary cargo stdout.
        safe_line=re.sub(r'(?i)\bauthorization\s*[=:]\s*bearer\s+[^\s;]+',
                         'authorization=[REDACTED]',errors[0])
        excerpt=_public_diagnostic(safe_line.encode('utf-8'),limit=220)
        # Some programs spell `Authorization=Bearer <secret>` as two tokens;
        # generic `authorization=` redaction covers only the first. Scrub
        # bearer payloads independently before writing public failure output.
        excerpt=re.sub(r'(?i)\bbearer\s+[^\s;]+','Bearer [REDACTED]',excerpt)
        excerpt=re.sub(r'(?:[A-Za-z]:[\\/]|/)[^\s\x60\"\']+','[PATH]',excerpt)
        excerpt=re.sub(r'[\r\n\t]+',' ',excerpt)[:220]
    else:
        excerpt='[NO_CARGO_ERROR_LINE]'
    return {'category':category,'error_excerpt':excerpt,
            'diagnostic_sha256':sha_bytes(raw.encode('utf-8'))}


def linux_cargo_frozen_lock_refusal(rust_repo:Path)->dict[str,Any]:
    """Force a *dependency graph* lock drift and require real frozen build refusal.

    `cargo metadata --no-deps` may accept a stale Cargo.lock without resolving
    the dependency graph. A fresh LOCAL PATH dependency changes the lock graph,
    does not use a registry or network, and `cargo build --frozen` must refuse
    to update the existing lock. Run only on owned disposable FC08 fixtures.
    """
    manifest=rust_repo/'Cargo.toml'
    lock=rust_repo/'Cargo.lock'
    original=manifest.read_bytes()
    probe=rust_repo/'fc08_lock_probe'
    if (original.count(b'version="0.1.0"')!=1 or not lock.is_file() or
        b'[dependencies]' in original or probe.exists() or probe.is_symlink()):
        raise RuntimeError('FC08_CARGO_MANIFEST_OR_LOCK_BASE_UNEXPECTED')
    baseline_lock=sha_file(lock)
    command=['cargo','build','--frozen']
    baseline=must(command,rust_repo,timeout=150)
    if sha_file(lock)!=baseline_lock:
        raise RuntimeError('FC08_CARGO_BASELINE_MUTATED_LOCK')
    changed_sha=''
    refused=None
    try:
        (probe/'src').mkdir(parents=True)
        (probe/'Cargo.toml').write_text('[package]\nname="fc08_lock_probe"\nversion="0.1.0"\nedition="2021"\n')
        (probe/'src/lib.rs').write_text('pub fn marker() -> u8 { 8 }\n')
        manifest.write_bytes(original+b'\n[dependencies]\nfc08_lock_probe = { path = "fc08_lock_probe" }\n')
        changed_sha=sha_file(manifest)
        refused=run(command,rust_repo,timeout=150)
        if refused['code']==0:
            raise RuntimeError('FC08_CARGO_STALE_FROZEN_LOCK_ACCEPTED')
        if sha_file(lock)!=baseline_lock:
            raise RuntimeError('FC08_CARGO_FROZEN_REFUSAL_CHANGED_LOCK')
        # A nonzero exit alone is insufficient: require Cargo's lockfile error,
        # not a compiler, manifest parse, permission, or network failure.
        cargo_diagnostic=_cargo_frozen_refusal_diagnostic(refused)
        if cargo_diagnostic['category'] not in (
            'CARGO_LOCKFILE_UPDATE_BLOCKED_LEGACY',
            'CARGO_LOCKFILE_UPDATE_BLOCKED_MODERN'):
            raise RuntimeError('FC08_CARGO_REFUSAL_NOT_LOCKFILE_DRIFT'+
                ' category='+cargo_diagnostic['category']+
                ' exit='+str(refused['code'])+
                ' stderr_sha256='+str(refused.get('stderr_sha256',''))+
                ' diagnostic_sha256='+cargo_diagnostic['diagnostic_sha256']+
                ' excerpt='+cargo_diagnostic['error_excerpt'])
    finally:
        manifest.write_bytes(original)
        if probe.is_dir() and not probe.is_symlink():
            shutil.rmtree(probe)
    restored=must(command,rust_repo,timeout=150)
    if sha_file(lock)!=baseline_lock or manifest.read_bytes()!=original or probe.exists():
        raise RuntimeError('FC08_CARGO_RESTORATION_MODIFIED_SOURCE')
    return {'native':'cargo build --frozen real local path dependency lock drift',
            'original_lock_sha256':baseline_lock,'drift_manifest_sha256':changed_sha,
            'refusal_exit':refused['code'],'refusal_stderr_sha256':refused['stderr_sha256'],
            'refusal_diagnostic_category':cargo_diagnostic['category'],
            'refusal_diagnostic_sha256':cargo_diagnostic['diagnostic_sha256'],
            'baseline_sha256':baseline['stdout_sha256'],
            'restored_sha256':restored['stdout_sha256']}


def github_live_restricted_dispatch_refusal(ident:dict[str,Any], *, token:str|None=None)->dict[str,Any]:
    """One real GitHub dispatch refusal under the *read-only* workflow token.

    The separate observation workflow is used to avoid recursive fanout if
    permissions regress. Refuse a 200/204 result or ambiguous 404, never
    print a bearer token, HTTP response body, or request headers. This is
    NOT evidence of a successful dispatch/retry/deduplication lifecycle.
    """
    if os.environ.get('GITHUB_ACTIONS')!='true' or ident.get('repository')!='Mikeyphw/devtool':
        raise RuntimeError('FC08_RESTRICTED_API_ONLY_REAL_GITHUB_RUN')
    sha=str(ident.get('sha',''))
    if not re.fullmatch(r'[a-f0-9]{40}',sha) or ident.get('ref')!='refs/heads/main':
        raise RuntimeError('FC08_RESTRICTED_API_INVALID_SOURCE_REF')
    secret=token if token is not None else os.environ.get('GITHUB_TOKEN','')
    if not secret or not secret.strip():
        raise RuntimeError('FC08_RESTRICTED_GITHUB_TOKEN_MISSING')
    # Read-only workflow policy explicitly denies all unspecified scopes,
    # including Actions write. No operator/user PAT or external token is used.
    base='https://api.github.com/repos/Mikeyphw/devtool'
    workflow='devtool-fc08-host-evidence.yml'
    headers={'Accept':'application/vnd.github+json',
             'Authorization':'Bearer '+secret,
             'X-GitHub-Api-Version':'2022-11-28',
             'User-Agent':'devtool-fc08-readonly-refusal'}
    get=Request(base+'/contents/.github/workflows/'+workflow+'?ref='+quote(sha),headers=headers,method='GET')
    try:
        with urlopen(get,timeout=20) as response:
            if response.status!=200:
                raise RuntimeError('FC08_RESTRICTED_API_SOURCE_NOT_READABLE')
            data=json.loads(response.read(65536))
    except (HTTPError,URLError,TimeoutError,OSError,ValueError) as exc:
        raise RuntimeError('FC08_RESTRICTED_API_SOURCE_READ_FAILED') from exc
    if (data.get('type')!='file' or data.get('path')!='.github/workflows/'+workflow):
        raise RuntimeError('FC08_RESTRICTED_API_WRONG_WORKFLOW_IDENTITY')
    payload=json.dumps({'ref':'main'},sort_keys=True,separators=(',',':')).encode()
    post=Request(base+'/actions/workflows/'+workflow+'/dispatches',headers={**headers,
                 'Content-Type':'application/json'},data=payload,method='POST')
    try:
        with urlopen(post,timeout=20) as response:
            status=response.status
    except HTTPError as exc:
        status=exc.code
    except (URLError,TimeoutError,OSError) as exc:
        raise RuntimeError('FC08_RESTRICTED_API_DISPATCH_TRANSPORT_UNVERIFIED') from exc
    if status!=403:
        raise RuntimeError('FC08_RESTRICTED_DISPATCH_NOT_PROVEN_DENIED http_status='+str(status))
    return {'source_workflow_read_http_status':200,'dispatch_http_status':403,
            'scope':'real GitHub API refusal with contents:read/actions:none',
            'dispatch_target':workflow,'request_sha256':sha_bytes(payload),
            'live_success_retry_or_dedup_qualified':False,'release_qualified':False}


def linux_native_abi_refusal(cmake_repo:Path)->dict[str,Any]:
    """Run the production RM-13 ABI verifier on the actual compiled ELF."""
    from devtool.actions_rm13_cpp import CppError,verify_binary
    if platform.machine().lower() not in ('x86_64','amd64'):
        raise RuntimeError('FC08_LINUX_ABI_HOST_UNSUPPORTED_FOR_EXACT_NEGATIVE')
    binaries=[p for p in cmake_repo.rglob('fc08main') if p.is_file() and not p.is_symlink()]
    if len(binaries)!=1:
        raise RuntimeError('FC08_REAL_CMAKE_BINARY_MISSING_OR_AMBIGUOUS')
    binary=binaries[0]
    accepted=verify_binary(binary,abi='elf-x86_64')
    if (accepted.get('abi')!='elf-x86_64' or accepted.get('verified_local_bytes') is not True or accepted.get('release_qualified') is not False):
        raise RuntimeError('FC08_LINUX_REAL_BINARY_NOT_ELF_X86_64')
    try:
        verify_binary(binary,abi='elf-aarch64')
    except CppError as exc:
        if 'wrong native architecture/ABI' not in str(exc):
            raise RuntimeError('FC08_LINUX_ABI_REFUSAL_WRONG_REASON') from exc
    else:
        raise RuntimeError('FC08_LINUX_WRONG_ABI_ACCEPTED')
    return {'native':'real CMake ELF through reviewed RM-13 verifier',
            'binary_sha256':sha_file(binary),'accepted_abi':'elf-x86_64',
            'refused_abi':'elf-aarch64','refusal':'wrong native architecture/ABI'}



def linux_native_abi_malformed_refusal(cmake_repo:Path)->dict[str,Any]:
    """Exercise RM-13 against malformed copies of an actually compiled ELF.

    Only write into an owned temporary directory. A malformed header, a foreign
    machine ID and a symlink must each fail for their specific reason; the
    original executable remains byte-for-byte unchanged. This proves Linux
    parser refusals, NOT any cross-compiled or signed artifact qualification.
    """
    from devtool.actions_rm13_cpp import CppError, verify_binary
    if platform.machine().lower() not in ('x86_64','amd64'):
        raise RuntimeError('FC08_LINUX_MALFORMED_ABI_UNSUPPORTED_HOST')
    binaries=[p for p in cmake_repo.rglob('fc08main') if p.is_file() and not p.is_symlink()]
    if len(binaries)!=1:
        raise RuntimeError('FC08_LINUX_MALFORMED_ABI_BINARY_AMBIGUOUS')
    binary=binaries[0]
    original=binary.read_bytes()
    if len(original)<64 or original[:4]!=b'\x7fELF' or original[4]!=2 or original[5]!=1 or original[18:20]!=b'>\x00':
        raise RuntimeError('FC08_LINUX_MALFORMED_ABI_NOT_REAL_X86_64_ELF')
    original_sha=sha_bytes(original)
    with tempfile.TemporaryDirectory(prefix='fc08-abi-refusal-') as temporary:
        root=Path(temporary)
        bad_class=root/'malformed-class'
        bad_class.write_bytes(original[:4]+b'\x01'+original[5:])
        bad_machine=root/'malformed-machine'
        bad_machine.write_bytes(original[:18]+b'\x00\x00'+original[20:])
        linked=root/'symlink-artifact'
        linked.symlink_to(binary)
        cases=(
            ('ELF-class',bad_class,'ELF class/endianness mismatch'),
            ('unknown-machine',bad_machine,'wrong native architecture/ABI'),
            ('symlink',linked,'missing, symlink or unbounded native artifact'),
        )
        for case, path, reason in cases:
            try:
                verify_binary(path,abi='elf-x86_64')
            except CppError as exc:
                if reason not in str(exc):
                    raise RuntimeError('FC08_LINUX_MALFORMED_ABI_WRONG_REFUSAL_REASON:'+case) from exc
            else:
                raise RuntimeError('FC08_LINUX_MALFORMED_ABI_ACCEPTED:'+case)
        hashes={'ELF-class':sha_file(bad_class),'unknown-machine':sha_file(bad_machine)}
    if binary.read_bytes()!=original or sha_file(binary)!=original_sha:
        raise RuntimeError('FC08_LINUX_MALFORMED_ABI_MUTATED_REAL_BINARY')
    return {'native':'RM-13 on real CMake ELF with owned malformed copies',
            'original_elf_sha256':original_sha,'rejected_cases':['ELF-class','unknown-machine','symlink'],
            'malformed_sha256':hashes,'original_binary_unchanged':True,
            'release_qualified':False}


def dag_verifier_adversarial(base:Path,ident:dict[str,Any])->dict[str,Any]:
    """Exercise actual hosted summary executable with synthetic hostile needs.

    These are *verifier* negative cases, not real GitHub dispatch, token
    permissions, runner access or consumer qualification. Those remain OPEN.
    """
    raw=base/'adversarial-host-receipts'
    raw.mkdir()
    for lane in LANES:
        folder=raw/('fc08-real-'+lane+'-attempt'+str(ident['attempt']))
        folder.mkdir()
        checks={name:{'synthetic_verifier_fixture':True} for name in EXPECTED_CHECKS[lane]}
        payload={'schema':'devtool.fc08.host-real-workload.v1','lane':lane,'identity':ident,
                 'checks':checks,'checks_sha256':sha_bytes(json.dumps(checks,sort_keys=True,separators=(',',':')).encode()),
                 'status':'WORKLOAD_COMPLETED_NEEDS_INDEPENDENT_REVIEW',
                 'release_qualified':False,'device_qualified':False}
        (folder/(lane+'.json')).write_text(json.dumps(payload,sort_keys=True))
    command=[sys.executable,str(SOURCE/'tools/fc08_host_summary.py'),
             '--input',str(raw),'--out',str(base/'adversarial-host-summary.json')]
    needs={key:{'result':'success'} for key in ('dag','linux','windows','macos')}
    def call(case_needs:dict[str,Any])->dict[str,Any]:
        env={'FC08_NEEDS_JSON':json.dumps(case_needs)}
        return run(command,SOURCE,timeout=80,extra_env=env)
    baseline=call(needs)
    out=base/'adversarial-host-summary.json'
    if baseline['code'] or not out.is_file():
        raise RuntimeError('FC08_HOST_VERIFIER_VALID_CONTROL_FAILED '+baseline['stderr_tail'])
    content=json.loads(out.read_text())
    if content.get('release_qualified') is not False or content.get('fc08_overall_qualified') is not False:
        raise RuntimeError('FC08_ADVERSARIAL_VERIFIER_FALSE_PROMOTION')
    baseline_hash=sha_file(out)
    out.unlink()
    rejected={}
    for key,broken,reason in (
        ('failed-dag',{**needs,'dag':{'result':'failure'}},'FAILED_OR_MISSING_GITHUB_HOST_JOB:dag'),
        ('failed-windows',{**needs,'windows':{'result':'failure'}},'FAILED_OR_MISSING_GITHUB_HOST_JOB:windows'),
        ('missing-needs',{'dag':{'result':'success'}},'MISSING_OR_UNEXPECTED_GITHUB_HOST_JOBS'),
    ):
        attempt=call(broken)
        if attempt['code']==0 or reason not in attempt['stderr_tail'] or out.exists():
            raise RuntimeError('FC08_HOST_VERIFIER_NEGATIVE_NOT_REFUSED:'+key)
        rejected[key]={'exit':attempt['code'],'stderr_sha256':attempt['stderr_sha256']}
    lane='linux-polyglot'
    receipt=raw/('fc08-real-'+lane+'-attempt'+str(ident['attempt']))/(lane+'.json')
    original=receipt.read_bytes()
    try:
        body=json.loads(original)
        body['identity']['attempt']=ident['attempt']+1
        receipt.write_text(json.dumps(body,sort_keys=True))
        attempt=call(needs)
        if attempt['code']==0 or 'WRONG_LANE_OR_SOURCE_IDENTITY' not in attempt['stderr_tail'] or out.exists():
            raise RuntimeError('FC08_HOST_VERIFIER_REPLAY_ACCEPTED')
        rejected['replayed-attempt']={'exit':attempt['code'],'stderr_sha256':attempt['stderr_sha256']}
    finally:
        receipt.write_bytes(original)
    return {'scope':'hosted real verifier with synthetic negative inputs only',
            'valid_control_sha256':baseline_hash,'negative_cases':rejected,
            'live_dispatch_denial_qualified':False,'token_permissions_qualified':False}


def linux(base:Path)->dict[str,Any]:
    if platform.system()!='Linux':raise RuntimeError('WRONG_LINUX_HOST')
    data={}
    # The Python provider's explicit unittest adapter is genuine execution.
    # No packaging frontend should be inferred from a synthetic pyproject.toml;
    # independent uv/Poetry/PDM lock+run checks remain mandatory below.
    python_cfg='[project]\nrunner="python"\nplatform="server"\ntest_command="python -m unittest discover -v"\n[workspace]\ndefault_target="app"\n[targets.app]\nrunner="python"\nroot="."\n'
    py=fixture(base,'python','python',{
        'fc08.py':'def plus(a,b):return a+b\n',
        'test_fc08.py':'import unittest\nfrom fc08 import plus\nclass Smoke(unittest.TestCase):\n def test_plus(self):self.assertEqual(plus(3,4),7)\n'}, config=python_cfg)
    data['python']={'runner':'python','test':devtool(py,'test',timeout=300)['stdout_sha256']}
    # Real manager work on a fresh, zero-external-dependency local project.
    # The managers are not treated as equivalent to full locked restore/offline qualification.
    for name in ('uv','poetry','pdm'):
        pkg=base/('manager-'+name);pkg.mkdir()
        (pkg/'pyproject.toml').write_text(
            '[project]\nname="fc08-manager-smoke"\nversion="0.1.0"\nrequires-python=">=3.11"\ndependencies=[]\n'
            '[build-system]\nrequires=["setuptools>=68"]\nbuild-backend="setuptools.build_meta"\n'
            '[tool.poetry]\nname="fc08-manager-smoke"\nversion="0.1.0"\ndescription="host provider smoke"\nauthors=["FC08 <fc08@example.invalid>"]\n'
            '[tool.poetry.dependencies]\npython=">=3.11"\n')
        (pkg/'fc08_manager_smoke.py').write_text('def value():return 8\n')
        if name=='uv':
            lock=['uv','lock'];execute=['uv','run','python','-c','import fc08_manager_smoke; assert fc08_manager_smoke.value()==8'];lf='uv.lock'
        elif name=='poetry':
            lock=['poetry','lock'];execute=['poetry','run','python','-c','import fc08_manager_smoke; assert fc08_manager_smoke.value()==8'];lf='poetry.lock'
        else:
            lock=['pdm','lock'];execute=['pdm','run','python','-c','import fc08_manager_smoke; assert fc08_manager_smoke.value()==8'];lf='pdm.lock'
        must(lock,pkg,timeout=420)
        row=must(execute,pkg,timeout=420)
        lockfile=pkg/lf
        if not lockfile.is_file():raise RuntimeError('MISSING_REAL_MANAGER_LOCK_'+name)
        data['python-'+name]={'native':'lock+run','lock_sha256':sha_file(lockfile),'run_sha256':row['stdout_sha256']}
    data['linux-lock-drift-refusal']=linux_lock_drift_refusal(base/'manager-uv')
    go=fixture(base,'go','go',{'go.mod':'module example.org/fc08\n\ngo 1.23\n','sample.go':'package fc08\nfunc V() int {return 8}\n','sample_test.go':'package fc08\nimport "testing"\nfunc TestV(t *testing.T){if V()!=8 {t.Fatal("incorrect")}}\n'})
    data['go']={'runner':'go','build':devtool(go,'build',timeout=360)['stdout_sha256'],'test':devtool(go,'test',timeout=360)['stdout_sha256']}
    node=fixture(base,'node','node',{'package.json':'{"name":"fc08-native","private":true,"version":"1.0.0","scripts":{"build":"node build.cjs","test":"node test.cjs"}}\n','build.cjs':"require('fs').writeFileSync('build.txt','real-build\\n');\n",'test.cjs':"const fs=require('fs');if(fs.readFileSync('build.txt','utf8')!=='real-build\\n')process.exit(8);\n"})
    data['node']={'runner':'node','build':devtool(node,'build',timeout=360)['stdout_sha256'],'test':devtool(node,'test',timeout=360)['stdout_sha256']}
    rust=fixture(base,'rust','rust',{'Cargo.toml':'[package]\nname="fc08_rust"\nversion="0.1.0"\nedition="2021"\n','src/lib.rs':'pub fn value()->i32{8}\n#[cfg(test)] mod tests{#[test] fn work(){assert_eq!(super::value(),8);}}\n'})
    must(['cargo','generate-lockfile','--offline'],rust,timeout=100)
    data['rust']={'runner':'rust','build':devtool(rust,'build',timeout=400)['stdout_sha256'],'test':devtool(rust,'test',timeout=400)['stdout_sha256']}
    data['linux-cargo-frozen-lock-refusal']=linux_cargo_frozen_lock_refusal(rust)
    maven=fixture(base,'maven','maven',{'pom.xml':'''<project xmlns="http://maven.apache.org/POM/4.0.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd"><modelVersion>4.0.0</modelVersion><groupId>org.fc08</groupId><artifactId>fc08</artifactId><version>1.0.0</version><properties><maven.compiler.release>17</maven.compiler.release></properties><build><plugins><plugin><groupId>org.apache.maven.plugins</groupId><artifactId>maven-compiler-plugin</artifactId><version>3.13.0</version></plugin><plugin><groupId>org.apache.maven.plugins</groupId><artifactId>maven-surefire-plugin</artifactId><version>3.5.2</version></plugin></plugins></build><dependencies><dependency><groupId>org.junit.jupiter</groupId><artifactId>junit-jupiter</artifactId><version>5.11.4</version><scope>test</scope></dependency></dependencies></project>''',
       'src/main/java/org/fc08/Smoke.java':'package org.fc08; public class Smoke { public static int value(){return 8;} }\n',
       'src/test/java/org/fc08/SmokeTest.java':'package org.fc08; import org.junit.jupiter.api.Test; import static org.junit.jupiter.api.Assertions.assertEquals; public class SmokeTest { @Test public void test() { assertEquals(8,Smoke.value()); }}\n'})
    data['maven']={'runner':'maven','build':devtool(maven,'build',timeout=600)['stdout_sha256'],'test':devtool(maven,'test',timeout=600)['stdout_sha256']}
    reports=list(maven.rglob('TEST-*.xml'))
    if not reports:raise RuntimeError('NO_REAL_MAVEN_SUREFIRE_XML')
    data['maven']['report_sha256']=sha_file(reports[0])
    data['cmake']=build_cmake(base,'cmake')
    data['linux-native-abi-refusal']=linux_native_abi_refusal(base/'cmake')
    data['linux-native-abi-malformed-refusal']=linux_native_abi_malformed_refusal(base/'cmake')
    data['meson']=build_meson(base)
    data['negative-exit-propagation']=fail_negative(base)
    return data


def windows_dotnet_fixture_config() -> str:
    # --target app resolves [targets.app] BEFORE top-level --solution overrides.
    # The reviewed solution must therefore be declared on the selected target,
    # not merely passed to the CLI: otherwise DotnetRunner gets an empty target.
    return ('[project]\nrunner="dotnet"\nplatform="server"\n'
            '[workspace]\ndefault_target="app"\n'
            '[targets.app]\nrunner="dotnet"\nroot="."\n'
            'solution="FC08.sln"\n')



def _validate_cobertura_document(path:Path, *, require_lines:bool)->None:
    try:
        coverage=ET.parse(path).getroot()
        if coverage.tag!='coverage':
            raise ValueError('unexpected coverage XML root')
        if require_lines and int(coverage.get('lines-valid','0'))<1:
            raise ValueError('missing real covered source')
    except (OSError,ET.ParseError,ValueError) as exc:
        raise RuntimeError('INVALID_OR_EMPTY_COVERAGE') from exc


def _reported_vstest_coverage(results:Path, candidates:list[Path], test:dict[str,Any])->Path:
    """Bind VSTest's single reported Coverlet attachment to a file in results.

    The collector can leave multiple coverage.cobertura.xml files in its results
    root. Accepting an arbitrary first match is unsafe; the successful VSTest
    process must identify the qualifying attachment itself. Never infer its
    identity from filesystem ordering, identical basenames or timestamps.
    """
    tail=str(test.get('stdout_tail',''))
    mentions=[line.strip().strip('"') for line in tail.splitlines()
              if line.strip().strip('"').replace('\\','/').lower().endswith('/coverage.cobertura.xml')]
    if len(mentions)!=1:
        raise RuntimeError('AMBIGUOUS_OR_MISSING_VSTEST_COVERAGE_ATTACHMENT count='+str(len(mentions)))
    try:
        reported=Path(mentions[0]).resolve(strict=True)
        root=results.resolve(strict=True)
        if not reported.is_relative_to(root):
            raise ValueError('outside results root')
    except (OSError,ValueError,RuntimeError) as exc:
        raise RuntimeError('REFUSED_VSTEST_COVERAGE_ATTACHMENT_OUTSIDE_RESULTS') from exc
    if reported not in {p.resolve(strict=True) for p in candidates}:
        raise RuntimeError('REFUSED_UNEXPECTED_VSTEST_COVERAGE_ATTACHMENT')
    return reported


def validate_dotnet_test_evidence(results:Path, test:dict[str,Any])->dict[str,Any]:
    """Require a positive TRX and a genuine VSTest-attached Coverlet XML.

    With a single coverage file, the older strict behavior remains. When the
    collector writes extras, VSTest must name exactly one positive attachment;
    independently discovered extras are parsed but cannot qualify as coverage.
    """
    trx=sorted(results.rglob('*.trx')) if results.is_dir() else []
    cov=sorted(results.rglob('coverage.cobertura.xml')) if results.is_dir() else []
    if len(trx)!=1 or not cov:
        raise RuntimeError('MISSING_TRX_OR_COVERAGE trx_count='+str(len(trx))+
            ' coverage_count='+str(len(cov))+
            ' stdout_tail='+_public_diagnostic(str(test.get('stdout_tail','')).encode(),limit=1250)+
            ' stderr_tail='+_public_diagnostic(str(test.get('stderr_tail','')).encode(),limit=450))
    try:
        root=ET.parse(trx[0]).getroot()
        summary=root.find('.//{*}ResultSummary')
        counters=root.find('.//{*}Counters')
        if summary is None or summary.get('outcome')!='Completed' or counters is None:
            raise ValueError('missing TRX counters')
        total=int(counters.get('total','0'))
        executed=int(counters.get('executed','0'))
        passed=int(counters.get('passed','0'))
        failed=int(counters.get('failed','0'))
        if total<1 or executed!=total or passed!=total or failed!=0:
            raise ValueError('test execution not fully passed')
    except (OSError,ET.ParseError,ValueError) as exc:
        raise RuntimeError('INVALID_OR_ZERO_TEST_TRX') from exc

    if len(cov)==1:
        selected=cov[0]
        # When the subprocess explicitly reports an attachment, enforce that
        # identity even in the single-file case (missing/stale attachments
        # must not be silently replaced by an unrelated filesystem hit).
        if any(line.strip().strip('"').replace('\\','/').lower().endswith('/coverage.cobertura.xml')
               for line in str(test.get('stdout_tail','')).splitlines()):
            selected=_reported_vstest_coverage(results,cov,test)
    else:
        selected=_reported_vstest_coverage(results,cov,test)
        for extra in cov:
            if extra.resolve()!=selected.resolve():
                _validate_cobertura_document(extra,require_lines=False)
    _validate_cobertura_document(selected,require_lines=True)
    receipt={'result':test['stdout_sha256'],'trx':sha_file(trx[0]),'coverage':sha_file(selected)}
    if len(cov)>1:
        # Preserve full evidence inventory without claiming extra XML qualified.
        receipt['coverage_candidates_sha256']=sorted(sha_file(p) for p in cov)
        receipt['coverage_candidate_count']=len(cov)
    return receipt

def windows(base:Path)->dict[str,Any]:
    if os.name!='nt' or platform.system()!='Windows':raise RuntimeError('WRONG_WINDOWS_HOST')
    # GHA installs .NET and initializes MSVC in a separate dedicated job step.
    # Create actual .NET reference graph, NuGet lock, and test/coverage artifacts.
    cfg=windows_dotnet_fixture_config()
    repo=fixture(base,'dotnet','dotnet',{
        # Force the VSTest-compatible .NET 8 SDK even when newer SDKs are
        # preinstalled on the GitHub runner. setup-dotnet supplies .NET 8.
        'global.json':'{"sdk":{"version":"8.0.100","rollForward":"latestFeature"}}\n',
        'fc08.runsettings':'<RunSettings><RunConfiguration><TreatNoTestsAsError>true</TreatNoTestsAsError></RunConfiguration></RunSettings>\n',
        'Lib/Lib.csproj':'<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net8.0</TargetFramework><Nullable>enable</Nullable></PropertyGroup></Project>\n',
        'Lib/Calc.cs':'namespace FC08; public static class Calc { public static int Add(int a,int b) => a+b; }\n',
        'Checks/Checks.csproj':'<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net8.0</TargetFramework><IsTestProject>true</IsTestProject><IsPackable>false</IsPackable></PropertyGroup><ItemGroup><ProjectReference Include="../Lib/Lib.csproj"/><PackageReference Include="Microsoft.NET.Test.Sdk" Version="17.12.0"/><PackageReference Include="xunit" Version="2.9.2"/><PackageReference Include="xunit.runner.visualstudio" Version="3.0.1"/><PackageReference Include="coverlet.collector" Version="6.0.2"/></ItemGroup></Project>\n',
        'Checks/CalcTests.cs':'using Xunit;using FC08;public class CalcTests {[Fact]public void Adds() {Assert.Equal(7,Calc.Add(3,4));}}\n',
    },config=cfg)
    lib=repo/'Lib/Lib.csproj'; checks=repo/'Checks/Checks.csproj'
    # .NET 8 generates .sln by default; --format is only supported by SDK 9.0.200+.
    dotnet_must(['dotnet','new','sln','-n','FC08'],repo,timeout=120)
    dotnet_must(['dotnet','sln','FC08.sln','add',str(lib),str(checks)],repo,timeout=90)
    # Initially generate committed lockfiles in disposable fixture before
    # requiring locked restore. Real cold and warm restores share one cache path.
    dotnet_must(['dotnet','restore','FC08.sln','--use-lock-file'],repo,timeout=900)
    locks=list(repo.rglob('packages.lock.json'))
    if len(locks)!=2:raise RuntimeError('MISSING_DOTNET_LOCKFILES')
    must(['git','add','.'],repo)
    must(['git','-c','user.name=FC08 Fixture','-c','user.email=fc08@example.invalid','commit','-qm','locked graph'],repo)
    pkg=base/'cold-nuget-cache'
    env={'NUGET_PACKAGES':str(pkg),'DOTNET_CLI_TELEMETRY_OPTOUT':'1'}
    data={}
    data['dotnet-cold-restore']={'result':dotnet_must(['dotnet','restore','FC08.sln','--locked-mode','--force'],repo,timeout=900,env=env)['stdout_sha256'],'locks':[sha_file(x) for x in locks]}
    data['dotnet-warm-restore']={'result':dotnet_must(['dotnet','restore','FC08.sln','--locked-mode'],repo,timeout=900,env=env)['stdout_sha256']}
    data['dotnet-build']={'result':devtool(repo,'build',timeout=900,solution='FC08.sln')['stdout_sha256']}
    test=dotnet_must(['dotnet','test','Checks/Checks.csproj','-c','Release',
        '--settings','fc08.runsettings','--logger','trx;LogFileName=fc08.trx',
        '--collect','XPlat Code Coverage','--results-directory',str(base/'test-results')],
        repo,timeout=900,env=env,capture_success_tail=True)
    data['dotnet-trx-coverage']=validate_dotnet_test_evidence(base/'test-results',test)
    packdir=base/'packs'
    dotnet_must(['dotnet','pack',str(lib),'-c','Release','--no-restore','-o',str(packdir)],repo,timeout=600,env=env)
    packages=list(packdir.glob('*.nupkg'))
    if len(packages)!=1:raise RuntimeError('NO_REAL_NUPKG')
    data['dotnet-pack']={'sha256':sha_file(packages[0])}
    publ=base/'publish'
    dotnet_must(['dotnet','publish',str(lib),'-c','Release','--no-restore','-o',str(publ)],repo,timeout=600,env=env)
    if not list(publ.glob('Lib.dll')):raise RuntimeError('NO_REAL_DOTNET_PUBLISH')
    data['dotnet-publish']={'artifact_sha256':sha_file(publ/'Lib.dll')}
    data['msvc-cmake']=build_cmake(base,'msvc',expected_compiler='MSVC')
    data['negative-exit-propagation']=fail_negative(base)
    return data


def macos(base:Path)->dict[str,Any]:
    if platform.system()!='Darwin':raise RuntimeError('WRONG_MACOS_HOST')
    data={'native-cmake':build_cmake(base,'native')}
    # A selected Windows-only managed project cannot silently count as native
    # macOS provider evidence, even if dotnet happens to be installed.
    from devtool.actions_rm09_dotnet import inspect_dotnet_job, DotNetError
    r=fixture(base,'windows-only','python',{
        'global.json':'{"sdk":{"version":"8.0.416","rollForward":"disable"}}',
        'All.sln':'Microsoft Visual Studio Solution File, Format Version 12.00\nProject("{00000000-0000-0000-0000-000000000000}") = "App", "App/App.csproj", "{00000000-0000-0000-0000-000000000000}"\nEndProject\n',
        'App/App.csproj':'<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net8.0-windows</TargetFramework></PropertyGroup></Project>',
        'App/packages.lock.json':'{"version":1,"dependencies":{"net8.0-windows":{}}}',
    },config='[project]\nrunner="python"\n[workspace]\ndefault_target="app"\n[targets.app]\nrunner="python"\nroot="."\n[targets.app.jobs.build]\nrunner="command"\ncommand=["dotnet","build","All.sln","--no-restore"]\n[targets.app.workflows]\nci=[{id="build",ref="job:build",depends_on=[]}]\n')
    try: inspect_dotnet_job(r,['dotnet','build','All.sln','--no-restore'],{})
    except DotNetError as ex:
        if 'Windows-only' not in str(ex):raise RuntimeError('REFUSAL_REASON_NOT_WINDOWS_ONLY') from ex
        data['unsupported-host-refusal']={'refused':True,'reason':'Windows-only build on macOS refused'}
    else:raise RuntimeError('UNSUPPORTED_WINDOWS_ONLY_PROVIDER_ADMITTED')
    data['negative-exit-propagation']=fail_negative(base)
    return data


def dag(base:Path)->dict[str,Any]:
    if not os.environ.get('GITHUB_ACTIONS')=='true':raise RuntimeError('NOT_REAL_GITHUB_ACTIONS')
    ident=identity(dict(os.environ))
    return {'immutable-source':{'source_verified':True},'negative-exit-propagation':fail_negative(base),
            'dag-fanout':{'required_jobs':['github-actions-dag','linux-polyglot','windows-dotnet-msvc','macos-native'],
                          'status':'FANOUT_SCHEDULED_NOT_YET_AUTHENTICATED'},
            'dag-verifier-adversarial':dag_verifier_adversarial(base,ident),
            'github-live-restricted-dispatch-refusal':github_live_restricted_dispatch_refusal(ident)}


def validate_receipt(record:dict[str,Any],lane:str,identity_expected:dict[str,Any])->None:
    if lane not in LANES:raise ValueError('UNREGISTERED_FC08_LANE')
    if record.get('schema')!='devtool.fc08.host-real-workload.v1' or record.get('lane')!=lane or record.get('identity')!=identity_expected:
        raise ValueError('WRONG_LANE_OR_SOURCE_IDENTITY')
    if record.get('release_qualified') is not False or record.get('device_qualified') is not False or record.get('status')!='WORKLOAD_COMPLETED_NEEDS_INDEPENDENT_REVIEW':
        raise ValueError('FALSE_QUALIFICATION')
    got=set(record.get('checks',{})); expected=set(EXPECTED_CHECKS[lane])
    if got!=expected:raise ValueError('MISSING_OR_FOREIGN_CHECKS:'+repr((expected-got,got-expected)))
    if not re.fullmatch('[a-f0-9]{64}',str(record.get('checks_sha256',''))):raise ValueError('UNHASHED_CHECKS')
    if record['checks_sha256']!=sha_bytes(json.dumps(record['checks'],sort_keys=True,separators=(',',':')).encode()):
        raise ValueError('CHECK_DIGEST_MISMATCH')


def main(argv:list[str]|None=None)->int:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--lane',choices=LANES,required=True)
    ap.add_argument('--out',required=True)
    a=ap.parse_args(argv)
    ident=identity(dict(os.environ))
    if a.lane in EXPECTED and platform.system()!=EXPECTED[a.lane]:raise RuntimeError('WRONG_PHYSICAL_HOST')
    out=Path(a.out).resolve();out.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='fc08-real-') as tmp:
        base=Path(tmp)
        checks={'linux-polyglot':linux,'windows-dotnet-msvc':windows,'macos-native':macos,'github-actions-dag':dag}[a.lane](base)
    row={'schema':'devtool.fc08.host-real-workload.v1','lane':a.lane,'identity':ident,
        'host':{'os':platform.system(),'machine':platform.machine(),'python':platform.python_version()},
        'checks':checks,'checks_sha256':sha_bytes(json.dumps(checks,sort_keys=True,separators=(',',':')).encode()),
        'status':'WORKLOAD_COMPLETED_NEEDS_INDEPENDENT_REVIEW','release_qualified':False,'device_qualified':False,
        'next_authority':'INDEPENDENT_ARTIFACT_AND_PROVIDER_VALIDATION'}
    validate_receipt(row,a.lane,ident)
    out.write_text(json.dumps(row,sort_keys=True,indent=2)+'\n')
    print(f'PASS_FC08_REAL_HOST_WORKLOAD_EXECUTED lane={a.lane} checks={len(checks)} receipt_sha256={sha_file(out)} qualified=false',flush=True)
    return 0

def emit_failure_diagnostic(argv:list[str], exc:Exception)->Path|None:
    # A failed workload must leave an attributable, source-bound diagnostic
    # artifact; it must never be mistaken for the successful lane receipt.
    try:
        lane=argv[argv.index('--lane')+1];out=Path(argv[argv.index('--out')+1])
        if lane not in LANES: return None
        ident={'sha':os.environ.get('GITHUB_SHA',''),'tree':'',
               'run_id':os.environ.get('GITHUB_RUN_ID',''),
               'attempt':os.environ.get('GITHUB_RUN_ATTEMPT',''),
               'ref':os.environ.get('GITHUB_REF','')}
        try: ident['tree']=git(SOURCE,'rev-parse','HEAD^{tree}')
        except Exception: pass
        body={'schema':'devtool.fc08.host-real-failure.v1','lane':lane,'identity':ident,
              'status':'FAILED_NOT_QUALIFIED','release_qualified':False,
              'category':type(exc).__name__,'diagnostic':_public_diagnostic(str(exc).encode(),limit=2300)}
        dest=out  # CI publishes this FAILED receipt at the usual artifact path; never a PASS record
        dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_text(json.dumps(body,indent=2,sort_keys=True)+'\n')
        return dest
    except (ValueError,IndexError,OSError): return None


if __name__=='__main__':
    try:raise SystemExit(main())
    except (RuntimeError,ValueError,TypeError,subprocess.SubprocessError,TimeoutError,OSError) as exc:
        dest=emit_failure_diagnostic(sys.argv[1:],exc)
        print('BLOCKED_FC08_REAL_HOST_WORKLOAD:'+type(exc).__name__+':'+_public_diagnostic(str(exc).encode())+
              (' diagnostic='+str(dest) if dest else ''),file=sys.stderr)
        raise SystemExit(2)
