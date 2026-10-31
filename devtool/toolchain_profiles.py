"""RM-07 reviewed host/toolchain profiles and non-installer capability inspection.

These profiles are policy, not evidence that a remote runner exists or is usable.
No paths or commands provided by callers are executed by profile selection.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
from typing import Any

# Pinned known setup actions, not floating major version tags.
CACHE_ACTION_SHA = '0057852bfaa89a56745cba8c7296529d2fc39830'  # actions/cache v4.3.0
MAX_CELLS = 2
MAX_TOTAL_JOBS = 48
MAX_LOCK_BYTES = 1_048_576


class ProfileError(ValueError):
    pass


@dataclass(frozen=True)
class ToolchainProfile:
    identifier: str
    os: str
    arch: str
    hosted_runner: str | None
    hosted_export: bool
    supported_providers: tuple[str, ...]
    python_version: str
    timeout_minutes: int
    reason: str = ''

    def describe(self) -> dict[str, Any]:
        return asdict(self)


PROFILES = {
    p.identifier: p for p in (
        ToolchainProfile('ubuntu-24.04-x64','linux','x86_64','ubuntu-24.04',True,('go','python-shell','dotnet','rust-cargo','node','jvm','shell-ci','cpp','android-gradle'),'3.12',60),
        ToolchainProfile('macos-15-intel','macos','x86_64','macos-15-intel',True,('go','python-shell','node','jvm','cpp'),'3.12',60),
        ToolchainProfile('windows-2022-x64','windows','x86_64','windows-2022',True,('dotnet','cpp'),'3.12',60,
                         'Reviewed Bash/Git-for-Windows .NET bridge only; other providers refused'),
        ToolchainProfile('termux-android-arm64','android','aarch64',None,False,('go','python-shell','node','jvm','shell-ci','cpp'),'3.12',60,
                         'native Termux is a local host, not a GitHub-hosted runner'),
    )
}
DEFAULT = 'ubuntu-24.04-x64'
_IDENTIFIER = re.compile(r'[a-z0-9][a-z0-9.-]{0,47}\Z')
_PINNED_GO = re.compile(r'1\.(?:2[0-9]|[3-9][0-9])(?:\.\d+)?\Z')
_PINNED_PY = re.compile(r'3\.(?:1[0-9]|[2-9][0-9])(?:\.\d+)?\Z')
_FORBIDDEN_LOCK_PARTS = {'secret','secrets','credential','credentials','token','tokens','password','private','.git','.ssh','.aws'}


def select_profiles(requested: str = '') -> tuple[ToolchainProfile, ...]:
    if not isinstance(requested,str) or len(requested) > 160:
        raise ProfileError('RM-07 host profile selection must be bounded text')
    labels = requested.split(',') if requested else [DEFAULT]
    if len(labels)>MAX_CELLS or len(set(labels))!=len(labels):
        raise ProfileError('RM-07 host profile matrix exceeds cap or repeats a cell')
    selected=[]
    for label in labels:
        if not _IDENTIFIER.fullmatch(label):
            raise ProfileError('RM-07 unsafe host profile identifier')
        profile=PROFILES.get(label)
        if profile is None:
            raise ProfileError('RM-07 unknown host profile: '+label)
        if not profile.hosted_export:
            raise ProfileError('RM-07 hosted export unavailable for '+label+': '+profile.reason)
        selected.append(profile)
    return tuple(selected)


def validate_versions(providers: list[dict[str, Any]], profiles: tuple[ToolchainProfile, ...]) -> None:
    for row in providers:
        provider = row.get('provider')
        version = row.get('version')
        if provider == 'go':
            if not isinstance(version,str) or not _PINNED_GO.fullmatch(version):
                raise ProfileError('RM-07 Go toolchain version must be an explicit supported 1.x version')
        elif provider == 'shell-ci':
            if version not in ('5','posix'):
                raise ProfileError('RM-14 shell major or POSIX standard version required')
        elif provider == 'cpp':
            compiler=row.get('compiler')
            if compiler=='msvc':
                if version!='19.4':raise ProfileError('RM-13 MSVC requires reviewed 19.4 toolset')
            elif compiler in ('gcc','clang'):
                if version not in ({'gcc':('12','13','14'),'clang':('16','17','18','19')}[compiler]):
                    raise ProfileError('RM-13 invalid compiler version')
            else:raise ProfileError('RM-13 unknown native compiler')
        elif provider == 'android-gradle':
            if version not in ('17','21'):
                raise ProfileError('Hosted Android Gradle requires reviewed JDK 17 or 21')
        elif provider == 'jvm':
            if version not in ('17','21','25'):
                raise ProfileError('RM-12 JVM requires reviewed JDK 17, 21 or 25')
        elif provider == 'node':
            if version not in ('20','22','24'):
                raise ProfileError('RM-11 Node version requires a reviewed 20/22/24 major')
        elif provider == 'rust-cargo':
            if not isinstance(version,str) or not re.fullmatch(r'1\.(?:[7-9]\d|\d{3})\.\d+',version):
                raise ProfileError('RM-10 requires exact reviewed Rust toolchain semver')
        elif provider == 'dotnet':
            if not isinstance(version,str) or not re.fullmatch(r'(?:8|9|10)\.0\.[0-9]{1,4}',version):
                raise ProfileError('RM-09 dotnet SDK requires reviewed global.json version')
        elif provider == 'python-shell':
            if not isinstance(version,str) or not _PINNED_PY.fullmatch(version):
                raise ProfileError('RM-07 Python toolchain version must be pinned')
        else:
            raise ProfileError('RM-07 no reviewed provider for '+str(provider)[:40])
        for profile in profiles:
            if provider=='cpp':
                if (row['compiler']=='msvc')!=(profile.os=='windows'):
                    raise ProfileError('RM-13 compiler/host matrix incompatible')
                if row['compiler'] in ('gcc','clang') and profile.os!='linux':
                    raise ProfileError('RM-13 GCC/Clang require reviewed Linux host profile')
                if row['sanitizer']!='none' and row['compiler']=='msvc' and row['sanitizer']!='address':
                    raise ProfileError('RM-13 unsupported MSVC sanitizer')
            if provider not in profile.supported_providers:
                raise ProfileError('RM-07 provider unavailable on host '+profile.identifier)
            if provider=='python-shell' and version not in ('3.11','3.12','3.13'):
                raise ProfileError('RM-07 host Python version differs from reviewed provider declaration')


def _tracked_regular(root: Path, name: str) -> bool:
    # No globs, generated paths, symlinks, traversal, hidden credentials, or directories.
    if (not isinstance(name,str) or not name or len(name)>160 or '\\' in name or name.startswith('/')
            or any(p in ('','.','..') or p.lower() in _FORBIDDEN_LOCK_PARTS for p in name.split('/'))
            or not re.fullmatch(r'[a-zA-Z0-9_./-]+',name)):
        return False
    node=root
    for part in name.split('/'):
        node=node/part
        if node.is_symlink():return False
    if not node.is_file() or node.stat().st_size>MAX_LOCK_BYTES:
        return False
    try:
        r=subprocess.run(['git','-C',str(root),'ls-files','--error-unmatch','--',name],
                         stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=8)
        return r.returncode==0
    except (OSError,subprocess.TimeoutExpired):
        return False


def cache_policy(root: Path, row: dict[str, Any], profile: ToolchainProfile, *, enabled: bool) -> dict[str, Any]:
    """Strict opt-in Go build cache only. Cache path is fixed and never from repo input.

    Lock contents influence the cache key. They are never used as shell or Action
    expressions; no credentials or source tree directories enter the cache path.
    """
    if not enabled:
        return {'enabled':False,'reason':'opt-in disabled'}
    if row['provider']=='cpp':
        sources=row.get('lockfiles',[])
        if not isinstance(sources,list) or not sources or len(sources)>128 or any(not _tracked_regular(root,s) for s in sources):
            raise ProfileError('RM-13 compiler cache source inventory must be tracked regular files')
        # Compiler intermediates are never cached across host/provenance boundaries.
        # Native compiler cache is not yet admitted: fail closed, not false hit.
        raise ProfileError('RM-13 compiler intermediate caching refused (source/ABI proof required)')
    if row['provider']=='jvm':
        locks=row.get('lockfiles',[])
        if not isinstance(locks,list) or not locks or len(locks)>30 or any(not _tracked_regular(root,x) for x in locks):
            raise ProfileError('RM-12 JVM cache requires tracked, regular reviewed source')
        digests={n:hashlib.sha256((root/n).read_bytes()).hexdigest() for n in sorted(locks)}
        key=hashlib.sha256(json.dumps(digests,sort_keys=True).encode()).hexdigest()
        manager=row.get('manager','')
        if manager not in ('maven','gradle'):raise ProfileError('RM-12 unknown JVM manager for cache')
        return {'enabled':True,'action_sha':CACHE_ACTION_SHA,
                'path':'~/.m2/repository' if manager=='maven' else '~/.gradle/caches/modules-2/files-2.1',
                'key':f'devtool-rm12-{profile.identifier}-{manager}-jdk{row["version"]}-{key}',
                'contains_credentials':False,'scope':'fixed dependency artifact store, not settings or credentials'}
    if row['provider']=='node':
        from .actions_rm11_node import node_workspace, NodeError
        try:ws=node_workspace(root,manager_selection=row['manager'])
        except NodeError as exc:raise ProfileError(str(exc)) from None
        if ws['source_files']!=sorted(set(row.get('lockfiles',[]))):
            raise ProfileError('RM-11 cache source file inventory differs from reviewed Node plan')
        for name in ws['source_files']:
            if not _tracked_regular(root,name):
                # .yarnrc.yml is reviewed as source, not a cache-path dependency.
                if name!='.yarnrc.yml' or not _tracked_regular(root,name):
                    raise ProfileError('RM-11 unsafe Node cache lock source')
        manager=ws['manager']
        digest=hashlib.sha256(json.dumps(ws['source_sha256'],sort_keys=True).encode()).hexdigest()
        return {'enabled':True,'action_sha':CACHE_ACTION_SHA,
                'path':'${{ runner.temp }}/devtool-node-store/'+manager,
                'key':'devtool-rm11-'+profile.identifier+'-node-'+str(row['version'])+'-'+manager+'-'+digest,
                'contains_credentials':False,'scope':'reviewed package-manager content store only'}
    if row['provider']=='dotnet':
        graph=row.get('graph')
        selected=row.get('selected')
        if not isinstance(graph,dict) or selected not in list(graph.get('projects',{}))+list(graph.get('solutions',{})):
            raise ProfileError('RM-09 missing selected tracked .NET solution identity')
        sources=graph.get('sources',{})
        if (not isinstance(sources,dict) or not sources or len(sources)>128 or
                any(not _tracked_regular(root,n) or hashlib.sha256((root/n).read_bytes()).hexdigest()!=digest
                    for n,digest in sources.items())):
            raise ProfileError('RM-09 untrusted .NET restore/cache source identity')
        digest=hashlib.sha256(json.dumps({'selected':selected,'sources':sources,'sdk':row['version']},
            sort_keys=True,separators=(',',':')).encode()).hexdigest()
        return {'enabled':True,'action_sha':CACHE_ACTION_SHA,
                'path':'${{ runner.temp }}/devtool-nuget-store',
                'key':f'devtool-rm09-nuget-{profile.identifier}-{digest}',
                'source_sha256':sources,'restore_keys':[],'contains_credentials':False,
                'scope':'fixed NuGet global package content only, no configs or credentials'}
    if row['provider']=='python-shell':
        project=row.get('project')
        if not isinstance(project,dict) or not project.get('project_present'):
            return {'enabled':False,'reason':'no reviewed Python packaging project'}
        sources=project.get('source_files',[])
        if (not isinstance(sources,list) or not sources or len(sources)>8 or
                len(set(sources))!=len(sources) or
                any(not _tracked_regular(root,n) for n in sources)):
            raise ProfileError('RM-07 Python cache refuses untracked, secret or unsafe project/lock input')
        if project.get('manager') not in ('pip','uv','poetry','pdm'):
            raise ProfileError('RM-07 Python cache refuses unknown manager')
        digests={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in sorted(sources)}
        source=hashlib.sha256(json.dumps(digests,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        # Cache only a fixed wheel content store, never venvs, source, dotfiles,
        # user-supplied cache paths, pip configuration, tokens or credentials.
        return {'enabled':True,'action_sha':CACHE_ACTION_SHA,
                'path':'${{ runner.temp }}/devtool-python-wheel-store',
                'key':f'devtool-rm07-python-{profile.identifier}-py{row["version"]}-{project["manager"]}-{source}',
                'source_sha256':digests,'restore_keys':[],'contains_credentials':False,
                'scope':'fixed ephemeral wheel-content store (no project or credentials)'}
    if row['provider']!='go':
        return {'enabled':False,'reason':'no reviewed cache store for this provider'}
    locks=row.get('lockfiles',[])
    if (not isinstance(locks,list) or 'go.mod' not in locks or 'go.sum' not in locks
            or len(locks)>4 or len(set(locks))!=len(locks)):
        raise ProfileError('RM-07 Go cache requires exactly reviewed tracked Go lock sources including go.sum')
    if any(not _tracked_regular(root,name) for name in locks):
        raise ProfileError('RM-07 unsafe, symlinked, missing, or untracked cache lockfile')
    raw=(root/'go.mod').read_bytes()
    if b'\x00' in raw or re.search(rb'(?m)^\s*replace\s',raw):
        raise ProfileError('RM-07 Go cache refuses unreviewed replace directives or binary lockfile')
    digests={p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in sorted(locks)}
    digest=hashlib.sha256(json.dumps(digests,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    # Static runner-local Go compiler cache; *never* cache source, env, ~/.gitconfig,
    # ~/.ssh, tokens, module download credentials or arbitrary user-selected paths.
    return {'enabled':True,'action_sha':CACHE_ACTION_SHA,
            'path':'~/Library/Caches/go-build' if profile.os=='macos' else '~/.cache/go-build',
            'key':f'devtool-go-build-{profile.identifier}-{row["version"].replace(".","-")}-{digest}',
            'source_sha256':digests,'restore_keys':[], 'contains_credentials':False,
            'scope':'hosted compiler cache only (not package downloads)'}


def inspect_local_tools(*, timeout: float = 1.5) -> dict[str, Any]:
    """Read-only bounded discovery. Unknown is not missing or installed."""
    specs={'python':(['python3','python'],'--version'), 'go':(['go'],'version'),
           'git':(['git'],'--version'), 'node':(['node'],'--version'),
           'java':(['java'],'-version'), 'dotnet':(['dotnet'],'--version'),
           'rustc':(['rustc'],'--version'), 'cargo':(['cargo'],'--version'),
           'cc':(['cc','clang','gcc'],'--version')}
    found={}
    for label,(executables,option) in specs.items():
        path=next((shutil.which(cmd) for cmd in executables if shutil.which(cmd)),None)
        if not path:
            found[label]={'state':'missing','version':None,'executable':None}
            continue
        try:
            cp=subprocess.run([path,option],capture_output=True,text=True,timeout=timeout,
                              stdin=subprocess.DEVNULL)
            out=(cp.stdout or cp.stderr).splitlines()
            raw=out[0].strip()[:180] if out else ''
            found[label]={'state':'installed' if cp.returncode==0 and raw else 'unknown',
                          'version':raw or None,'executable':path}
        except (OSError,subprocess.TimeoutExpired,UnicodeError):
            found[label]={'state':'unknown','version':None,'executable':path}
    return found


def capability_report(root: Path, *, probe: bool = True) -> dict[str, Any]:
    from .host_context import detect_host_context
    host=detect_host_context()
    return {'schema_version':1,'kind':'devtool.actions.rm07-capability-doctor',
            'read_only':True,'installed':inspect_local_tools() if probe else {},
            'host':{'kind':host.host_kind,'os':host.os_family,'architecture':host.architecture,
                    'substrate':host.current_substrate},
            'profiles':[PROFILES[key].describe() for key in sorted(PROFILES)],
            'setup_action_pins':{'checkout':'11bd71901bbe5b1630ceea73d27597364c9af683',
                                 'python':'a26af69be951a213d495a4c3e4e4022e16d87065',
                                 'go':'d35c59abb061a4a6fb18e82ac0862c26744d6ab5',
                                 'cache':CACHE_ACTION_SHA},
            'no_automatic_installation':True,'hosted_runner_observed':False,
            'remote_attested':False,'release_qualified':False}
