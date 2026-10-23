"""RM-08: tracked Go workspace and Python packaging review; no implicit installs.

All discovery is read-only, bounded and source-oriented.  A successful review is
not remote provenance, artifact authenticity, or release qualification.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import subprocess
import tomllib
from typing import Any

MAX_MANIFEST = 1024 * 1024
MAX_FILES = 64
_GO_VERSION = re.compile(r'^go\s+(1\.(?:2[0-9]|[3-9][0-9])(?:\.\d+)?)\s*$', re.M)
_GO_MODULE = re.compile(r'^module\s+([A-Za-z0-9][A-Za-z0-9./_+~-]{0,200})\s*$', re.M)
_SAFE_DIR = re.compile(r'[A-Za-z0-9_./-]{1,160}\Z')
_BACKENDS = {
    'setuptools.build_meta': 'setuptools',
    'hatchling.build': 'hatchling',
    'flit_core.buildapi': 'flit-core',
    'poetry.core.masonry.api': 'poetry-core',
    'pdm.backend': 'pdm-backend',
}
_LOCKS = {'pip': 'requirements.txt', 'uv': 'uv.lock',
          'poetry': 'poetry.lock', 'pdm': 'pdm.lock'}


class ProjectReviewError(ValueError):
    pass


def _trusted(root: Path, rel: str, *, required: bool = True) -> bytes | None:
    if (not isinstance(rel, str) or not _SAFE_DIR.fullmatch(rel) or rel.startswith('/')
            or '\\' in rel or any(part in ('', '.', '..') for part in rel.split('/'))):
        raise ProjectReviewError('Untrusted project manifest path')
    cur = root
    for part in rel.split('/'):
        cur = cur / part
        if cur.is_symlink():
            raise ProjectReviewError('Symlink in project manifest path: ' + rel)
    if not cur.exists() and not required:
        return None
    if not cur.is_file() or cur.stat().st_size > MAX_MANIFEST:
        raise ProjectReviewError('Missing, oversized, or nonregular project manifest: ' + rel)
    try:
        proc = subprocess.run(['git','-C',str(root),'ls-files','--error-unmatch','--',rel],
                              stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL,timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        raise ProjectReviewError('Cannot verify Git-tracked manifest') from None
    if proc.returncode:
        raise ProjectReviewError('Manifest is not tracked: ' + rel)
    return cur.read_bytes()


def _tracked_manifests(root: Path, filename: str) -> list[str]:
    p = subprocess.run(['git','-C',str(root),'ls-files','-z','--',f'**/{filename}',filename],
                       capture_output=True,timeout=10,check=True)
    paths = sorted({x.decode('utf-8') for x in p.stdout.split(b'\0') if x})
    if len(paths) > MAX_FILES:
        raise ProjectReviewError('Too many tracked '+filename+' manifests')
    for rel in paths:
        _trusted(root,rel)
    return paths


def _go_replacements(raw: str, *, origin: str, directories: set[str]) -> list[dict[str, str]]:
    """Admit only literal repo-local module replacements with tracked destinations.

    Do not execute Go's replacement resolver during source review. Unknown,
    version-qualified, remote, duplicate, or escaping targets fail closed.
    """
    result=[];block=False
    for original in raw.splitlines():
        line=original.split('//',1)[0].strip()
        if line=='replace (':
            if block: raise ProjectReviewError('Nested Go replace block')
            block=True;continue
        if block and line==')':block=False;continue
        if line.startswith('replace '):line=line[8:].strip()
        elif not block:continue
        chunks=line.split('=>')
        if len(chunks)!=2:raise ProjectReviewError('Go replace requires one literal mapping')
        old,new=chunks[0].strip(),chunks[1].strip()
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9./_+~-]{0,200}',old) or ' ' in old:
            raise ProjectReviewError('Version-specific/unsafe Go replacement refused')
        if not new.startswith('./') and new!='.':
            raise ProjectReviewError('Go replacement must be local repository module')
        clean=new[2:] if new.startswith('./') else '.'
        if (not clean or not _SAFE_DIR.fullmatch(clean) or
                any(part in ('', '..', '.') for part in clean.split('/'))):
            raise ProjectReviewError('Go replacement escapes repository')
        if origin != '.':
            clean=(origin+'/'+clean) if clean!='.' else origin
        if clean not in directories:raise ProjectReviewError('Go replacement destination not a tracked module: '+clean)
        if any(row['source']==old for row in result):raise ProjectReviewError('Duplicate Go replacement')
        result.append({'source':old,'directory':clean})
    if block or len(result)>MAX_FILES:raise ProjectReviewError('Malformed/excessive Go replacements')
    return result


def _go_requirements(raw: str) -> list[str]:
    """Collect exact module identities used for local graph edges, no downloads."""
    result=[];block=False
    for original in raw.splitlines():
        line=original.split('//',1)[0].strip()
        if line=='require (':block=True;continue
        if block and line==')':block=False;continue
        if line.startswith('require '):line=line[8:].strip()
        elif not block:continue
        parts=line.split()
        if len(parts)!=2 or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9./_+~-]{0,200}',parts[0]):
            raise ProjectReviewError('Unreviewed Go require directive')
        result.append(parts[0])
    if block or len(result)>MAX_FILES:raise ProjectReviewError('Malformed/excessive Go requirements')
    return result


def _go_paths(raw: str, tag: str) -> list[str]:
    # A deliberate narrow grammar: comment-free literal single and block forms.
    result=[]; block=False
    for rawline in raw.splitlines():
        line=rawline.strip()
        if not line or line.startswith('//'):continue
        if block:
            if line==')':block=False;continue
            if line.startswith('replace ') or '=>' in line:
                raise ProjectReviewError('Go workspace replace is not reviewed')
            result.append(line)
        elif line==tag+' (':block=True
        elif line.startswith(tag+' '):result.append(line[len(tag)+1:].strip())
        elif line.startswith('replace ') or line=='replace (':
            raise ProjectReviewError('Go workspace replace is not reviewed')
    if block or len(result)>MAX_FILES or len(set(result))!=len(result):
        raise ProjectReviewError('Malformed, duplicated, or excessive Go workspace members')
    return result


def go_workspace_manifest(root: Path, *, directory: str = '.') -> dict[str, Any]:
    """Review all tracked Go modules; select a root or `go -C DIR` module.

    go.work `use` is constrained to tracked repo-local module roots. External
    `replace`/workspace redirects are rejected instead of being provisioned.
    """
    root=root.resolve()
    if directory!='.' and (not _SAFE_DIR.fullmatch(directory) or directory.startswith('/')
                           or any(p in ('','.','..') for p in directory.split('/'))):
        raise ProjectReviewError('Go selected module directory is unsafe')
    mods=_tracked_manifests(root,'go.mod')
    if not mods:
        raise ProjectReviewError('No tracked Go module')
    rows=[];selected=None
    for path in mods:
        raw=_trusted(root,path)
        assert raw is not None
        text=raw.decode('utf-8')
        version=_GO_VERSION.search(text);module=_GO_MODULE.search(text)
        toolchain=re.search(r'(?m)^toolchain\s+go(1\.[0-9]+(?:\.[0-9]+)?)\s*$',text)
        if toolchain and (not version or toolchain.group(1)!=version.group(1)):
            raise ProjectReviewError('Go auto-switching toolchain conflicts with pinned module version')
        if not version or not module:
            raise ProjectReviewError('Go module/version declaration missing or unsupported: '+path)
        rel=path.rsplit('/',1)[0] if '/' in path else '.'
        row={'directory':rel,'manifest':path,'module':module.group(1),
             'version':version.group(1),'sha256':hashlib.sha256(raw).hexdigest(),
             'requires':_go_requirements(text)}
        rows.append(row)
        if directory==rel:selected=row
    if selected is None:
        raise ProjectReviewError('Go -C directory does not select an exact tracked module')
    names=[m['module'] for m in rows]
    if len(names)!=len(set(names)):
        raise ProjectReviewError('Duplicate Go module identity')
    directories={m['directory'] for m in rows}
    replacements=[]
    for mod in rows:
        content=(_trusted(root,mod['manifest']) or b'').decode('utf-8')
        replacements.extend(_go_replacements(content,origin=mod['directory'],directories=directories))
    work=_trusted(root,'go.work',required=False)
    members=[]
    if work is not None:
        text=work.decode('utf-8')
        version=_GO_VERSION.search(text)
        if not version:raise ProjectReviewError('Go workspace version is missing')
        if version.group(1)!=selected['version']:
            raise ProjectReviewError('Go workspace version differs from selected pinned module')
        members=_go_paths(text,'use')
        replacements.extend(_go_replacements(text,origin='.',directories=directories))
        if not members:raise ProjectReviewError('Go workspace has no literal use entries')
        normalized=[]
        for member in members:
            clean=member[2:] if member.startswith('./') else member
            if clean=='.':clean='.'
            if (not clean or clean.startswith('/') or not _SAFE_DIR.fullmatch(clean)
                or any(x in ('','..') for x in clean.split('/'))):
                raise ProjectReviewError('Go workspace use escapes reviewed repository')
            if clean not in {r['directory'] for r in rows}:
                raise ProjectReviewError('Go workspace member is not a tracked module: '+clean)
            normalized.append(clean)
        if selected['directory'] not in normalized:
            raise ProjectReviewError('Selected Go module absent from go.work use entries')
        members=sorted(normalized)
    if len({r['source'] for r in replacements})!=len(replacements):
        raise ProjectReviewError('Ambiguous Go replacements across go.mod/go.work')
    module_to_dir={row['module']:row['directory'] for row in rows}
    replacement_map={r['source']:r['directory'] for r in replacements}
    edges={row['directory']:set() for row in rows}
    for row in rows:
        for dep in row['requires']:
            target=replacement_map.get(dep,module_to_dir.get(dep))
            if target and target!=row['directory']:
                edges[row['directory']].add(target)
    # Fail closed for circular local source dependencies before generated jobs.
    active=set();completed=set();order=[]
    def visit(key: str) -> None:
        if key in active:raise ProjectReviewError('Cyclic local Go workspace graph')
        if key in completed:return
        active.add(key)
        for dep in sorted(edges[key]):visit(dep)
        active.remove(key);completed.add(key);order.append(key)
    for key in sorted(edges):visit(key)
    source_paths=sorted({r['manifest'] for r in rows}|({'go.work'} if work else set()))
    for mod in rows:
        rel=mod['manifest'].removesuffix('go.mod')+'go.sum'
        if _trusted(root,rel,required=False) is not None:source_paths.append(rel)
    if work is not None and _trusted(root,'go.work.sum',required=False) is not None:
        source_paths.append('go.work.sum')
    return {'schema_version':1,'kind':'devtool.go-workspace-manifest',
            'selected':selected,'modules':rows,'workspace_members':members,
            'local_replacements':replacements,'workspace_edges':{k:sorted(v) for k,v in edges.items()},
            'workspace_build_order':order,'source_files':sorted(set(source_paths)),
            'release_qualified':False}


def python_project_manifest(root: Path, *, require_package: bool = False, native_manager: bool = False) -> dict[str, Any]:
    root=root.resolve()
    raw=_trusted(root,'pyproject.toml',required=require_package)
    if raw is None:
        return {'schema_version':1,'kind':'devtool.python-project-manifest',
                'project_present':False,'source_files':[], 'release_qualified':False}
    try: data=tomllib.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeError):raise ProjectReviewError('Malformed pyproject.toml') from None
    build=data.get('build-system')
    if not isinstance(build,dict) or not isinstance(build.get('build-backend'),str):
        raise ProjectReviewError('Python build backend must be explicit')
    backend=build['build-backend']
    if backend not in _BACKENDS or 'backend-path' in build:
        raise ProjectReviewError('Unreviewed Python backend or local backend-path')
    requirements=build.get('requires')
    if (not isinstance(requirements,list) or not 1<=len(requirements)<=16
            or any(not isinstance(x,str) or not re.fullmatch(r'[A-Za-z0-9_.-]+==[A-Za-z0-9_.+!-]+',x)
                   for x in requirements)):
        raise ProjectReviewError('Build backend requirements must be exact pinned versions')
    if not any(x.split('==',1)[0].lower().replace('_','-')==_BACKENDS[backend] for x in requirements):
        raise ProjectReviewError('Build backend requirement missing from pinned list')
    if len({x.split('==',1)[0].lower() for x in requirements})!=len(requirements):
        raise ProjectReviewError('Duplicate Python backend requirement')
    present={kind:path for kind,path in _LOCKS.items() if _trusted(root,path,required=False) is not None}
    if len(present)!=1:
        raise ProjectReviewError('Python packaging requires exactly one reviewed dependency policy lock')
    manager,path=next(iter(present.items()))
    # Default pip hash-export policy remains backward compatible. Explicit
    # manager-native uv mode uses the tracked uv.lock directly, without export.
    install_lock=path
    if native_manager and manager!='uv':
        raise ProjectReviewError('RM-08 native offline execution currently admits only uv; Poetry/PDM need a verified network-denial mechanism')
    if manager!='pip':
        try:locked=tomllib.loads((_trusted(root,path) or b'').decode('utf-8'))
        except (ValueError, UnicodeError):
            raise ProjectReviewError('Malformed manager lockfile') from None
        packages=locked.get('package',[])
        if not isinstance(packages,list) or not packages or len(packages)>4096:
            raise ProjectReviewError('Manager lock has no bounded package set')
        for package in packages:
            if not isinstance(package,dict) or not isinstance(package.get('name'),str) or not isinstance(package.get('version'),str):
                raise ProjectReviewError('Unpinned manager lock entry')
            if not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',package['name']) or not re.fullmatch(r'[0-9][A-Za-z0-9_.+!-]{0,80}',package['version']):
                raise ProjectReviewError('Unsafe manager package pin')
            source=package.get('source',{})
            own_name=data.get('project',{}).get('name','')
            own_name=own_name.lower().replace('_','-') if isinstance(own_name,str) else ''
            is_root=(native_manager and package['name'].lower().replace('_','-')==own_name
                     and source=={'editable':'.'})
            if source and not is_root and (not isinstance(source,dict) or
                    any(key in source for key in ('git','url','path','editable','registry-url')) or
                    (source.get('registry') not in (None,'https://pypi.org/simple'))):
                raise ProjectReviewError('Manager lock contains unreviewed registry/local/remote source')
            files=package.get('files',[])  # Poetry/PDM (uv has wheels/sdist)
            archives=files if files else package.get('wheels',[])
            if not isinstance(archives,list) or (native_manager and not is_root and not archives):
                raise ProjectReviewError('Native manager lock archive hashes missing')
            # Require integrity information for every archive candidate.
            for item in archives:
                if not isinstance(item,dict) or not re.fullmatch(r'(?:sha256:)?[0-9a-f]{64}',str(item.get('hash',''))):
                    raise ProjectReviewError('Manager lock archive missing SHA-256')
        # CI export must be explicitly hash-pinned independently of the
        # manager's own mutable installation/frontend behavior.
        install_lock=path if native_manager else 'requirements-ci.txt'
        if not native_manager and _trusted(root,install_lock,required=False) is None:
            raise ProjectReviewError('Manager lock requires tracked requirements-ci.txt hash export')
    if manager=='pip':
        from .actions_distributions import _python_source, DistributionError
        try: _python_source(root)  # hash-pinned requirements with build installed
        except DistributionError as e:raise ProjectReviewError(str(e)) from None
        requirements_text=(_trusted(root,path) or b'').decode('utf-8')
        backend_pkg=_BACKENDS[backend]
        if not re.search(r'(?m)^'+re.escape(backend_pkg)+r'==[^\s]+\s+--hash=sha256:[a-f0-9]{64}',requirements_text):
            raise ProjectReviewError('Python backend must be hash-pinned in pip build requirements')
    elif not native_manager:
        # Full hash policy on the tracked pip export; no trusted installer
        # equivalence is inferred without matching each locked package pin.
        export=(_trusted(root,install_lock) or b'').decode('utf-8')
        if not export.strip():raise ProjectReviewError('Manager hash export is empty')
        pinned={}
        for line in export.splitlines():
            line=line.strip()
            if not line or line.startswith('#') or line=='--require-hashes':continue
            match=re.fullmatch(r'([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+!-]+)\s+--hash=sha256:([a-f0-9]{64})',line)
            if not match:raise ProjectReviewError('Manager pip export must contain exact hashes')
            key=match.group(1).lower().replace('_','-')
            if key in pinned:raise ProjectReviewError('Duplicate manager pip export package')
            pinned[key]=match.group(2)
        for package in packages:
            key=package['name'].lower().replace('_','-')
            if pinned.get(key)!=package['version']:
                raise ProjectReviewError('Manager lock does not match tracked hash export')
        if set(pinned)!={pkg['name'].lower().replace('_','-') for pkg in packages}:
            raise ProjectReviewError('Pip export includes packages not in manager lock')
    return {'schema_version':1,'kind':'devtool.python-project-manifest',
            'project_present':True,'backend':backend,'build_requirements':requirements,
            'manager':manager,'lockfile':path,'install_lock':install_lock,
            'native_manager':native_manager,
            'source_sha256':{name:__import__('hashlib').sha256(_trusted(root,name) or b'').hexdigest()
                             for name in ['pyproject.toml',path]+([install_lock] if install_lock!=path else [])},
            'source_files':['pyproject.toml',path]+([install_lock] if install_lock!=path else []),
            'dependency_install_supported':True,
            'python_versions':['3.11','3.12','3.13'], 'release_qualified':False}


def smoke_wheel(root: Path, wheel: str, *, name: str, version: str,
                entrypoint: str, timeout: int = 30, args: tuple[str, ...] = ('--help',)) -> dict[str, Any]:
    """Opt-in isolated offline install + invoke, after structural wheel verification.

    The wheel is executed as consumer-owned code in a disposable venv.  This is
    a functional smoke check, not sandboxing malicious packages or attestation.
    """
    import os
    import sys
    import tempfile
    import venv
    from .actions_distributions import verify_wheel
    root=root.resolve()
    if not (5<=timeout<=60) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}',entrypoint) or not (0<=len(args)<=5) or any(not re.fullmatch(r'[A-Za-z0-9_./=-]{1,80}',a) or a.startswith('/') or '..' in a.split('/') for a in args):
        raise ProjectReviewError('Invalid wheel smoke invocation')
    if not re.fullmatch(r'dist/[A-Za-z0-9_.+\-]{1,180}\.whl',wheel):
        raise ProjectReviewError('Wheel smoke path must be a root-relative dist/*.whl')
    path=root/wheel
    if path.is_symlink() or not path.is_file() or path.stat().st_size>512*1024*1024:
        raise ProjectReviewError('Wheel smoke candidate is missing or unsafe')
    project=python_project_manifest(root,require_package=True)
    if not project['dependency_install_supported']:
        raise ProjectReviewError('Wheel smoke requires reviewed hash-pinned pip policy')
    verified=verify_wheel(path,name=name,version=version,entrypoints=[entrypoint])
    # No network, no transitive resolution, no editable source, no user site.
    with tempfile.TemporaryDirectory(prefix='devtool-rm08-wheel-') as temp:
        envroot=Path(temp)/'venv'
        venv.EnvBuilder(with_pip=True,clear=True,symlinks=False).create(str(envroot))
        py=envroot/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
        exe=envroot/('Scripts' if os.name=='nt' else 'bin')/entrypoint
        env={**os.environ,'PIP_NO_INDEX':'1','PIP_DISABLE_PIP_VERSION_CHECK':'1',
             'PYTHONNOUSERSITE':'1','PYTHONDONTWRITEBYTECODE':'1'}
        try:
            install=subprocess.run([str(py),'-m','pip','install','--no-index','--no-deps',
                                    '--no-cache-dir','--no-build-isolation',str(path)],
                                   stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                   timeout=timeout,env=env,cwd=temp)
            if install.returncode:
                raise ProjectReviewError('Offline pip wheel installation failed')
            if not exe.is_file() or exe.is_symlink():
                raise ProjectReviewError('Wheel did not install declared console script')
            run=subprocess.run([str(exe),*args],stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                               timeout=timeout,env=env,cwd=temp)
            if run.returncode:
                raise ProjectReviewError('Installed wheel entrypoint invocation failed')
        except (OSError,subprocess.TimeoutExpired) as exc:
            raise ProjectReviewError('Wheel smoke execution unavailable or timed out') from exc
    return {'schema_version':1,'kind':'devtool.python-offline-wheel-smoke',
            'wheel_sha256':__import__('devtool.actions_distributions',fromlist=['_file_digest'])._file_digest(path),
            'entrypoint':entrypoint,'arguments':list(args),'installed_without_network':True,'invoked_exit_zero':True,
            'wheel_record_verified':verified['record_sha256_verified'],
            'remote_attested':False,'release_qualified':False}


def merge_coverage_xml(root: Path, reports: list[str]) -> dict[str, Any]:
    """Read-only bounded coverage.py XML merge; reject duplicate file identities.

    Counts are reporter statements, not independently measured execution.  The
    merge refuses overlapping files rather than silently double counting.
    """
    import xml.etree.ElementTree as ET
    if not isinstance(reports,list) or not 1<=len(reports)<=16 or len(set(reports))!=len(reports):
        raise ProjectReviewError('Coverage merge requires 1..16 unique shard paths')
    root=root.resolve();files={};seen=[]
    for rel in reports:
        if (not isinstance(rel,str) or not _SAFE_DIR.fullmatch(rel)
                or not rel.startswith('dist/') or '..' in rel.split('/')):
            raise ProjectReviewError('Unsafe coverage report location')
        file=root/rel
        if file.is_symlink() or not file.is_file() or file.stat().st_size>2*1024*1024:
            raise ProjectReviewError('Coverage report missing, symlinked or too large')
        raw=file.read_bytes()
        if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
            raise ProjectReviewError('XML entity/doctype refused')
        try:document=ET.fromstring(raw)
        except ET.ParseError:raise ProjectReviewError('Malformed coverage XML') from None
        if document.tag!='coverage':raise ProjectReviewError('Not coverage.py XML')
        classes=document.findall('.//class')
        if not classes or len(classes)>20000:
            raise ProjectReviewError('Coverage XML has no files or too many files')
        for item in classes:
            name=item.get('filename','')
            if not name or not _SAFE_DIR.fullmatch(name) or name.startswith('/') or '..' in name.split('/'):
                raise ProjectReviewError('Unsafe covered source filename')
            if name in files:
                raise ProjectReviewError('Conflicting/duplicate covered file identity: '+name)
            lines=item.findall('./lines/line')
            if not lines or len(lines)>100000:
                raise ProjectReviewError('Coverage file has no line-level evidence')
            numbers=set();hits=0
            for line in lines:
                number=line.get('number','');hit=line.get('hits','')
                if not number.isdecimal() or int(number)<1 or int(number) in numbers or not hit.isdecimal():
                    raise ProjectReviewError('Coverage line data is invalid or duplicated')
                numbers.add(int(number));hits+=int(hit)>0
            files[name]={'covered':hits,'valid':len(lines)}
        seen.append({'path':rel,'sha256':hashlib.sha256(raw).hexdigest()})
    covered=sum(v['covered'] for v in files.values())
    valid=sum(v['valid'] for v in files.values())
    return {'schema_version':1,'kind':'devtool.python-coverage-merge',
            'source_files':len(files),'covered_lines':covered,'valid_lines':valid,
            'line_rate':round(covered/valid,8), 'shards':seen,
            'tests_authenticated':False,'release_qualified':False}


def smoke_sdist(root: Path, sdist: str, *, name: str, version: str,
                entrypoint: str, timeout: int = 60, args: tuple[str,...] = ('--help',)) -> dict[str, Any]:
    """Offline non-isolating sdist build/install with an independently checked archive.

    Backend and its dependencies must already be provisioned from a reviewed
    hash-locked wheelhouse. No implicit dependency fetch or resolver is allowed.
    """
    import os, tempfile, venv
    from .actions_distributions import verify_sdist, _file_digest
    if not re.fullmatch(r'dist/[A-Za-z0-9_.+\-]{1,180}\.tar\.gz',sdist):
        raise ProjectReviewError('Sdist smoke requires dist/*.tar.gz candidate')
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}',entrypoint) or not 0<=len(args)<=5 or any(
            not re.fullmatch(r'[A-Za-z0-9_./=-]{1,80}',item) for item in args):
        raise ProjectReviewError('Unsafe sdist smoke entrypoint/arguments')
    root=root.resolve();candidate=root/sdist
    if candidate.is_symlink() or not candidate.is_file() or candidate.stat().st_size>512*1024*1024:
        raise ProjectReviewError('Unsafe or missing sdist')
    project=python_project_manifest(root,require_package=True)
    if not project['dependency_install_supported']:
        raise ProjectReviewError('Sdist smoke requires reviewed hash-locked backend dependencies')
    checked=verify_sdist(candidate,name=name,version=version)
    wheelhouse=root/'dist'/'wheelhouse'
    if wheelhouse.is_symlink() or not wheelhouse.is_dir():
        raise ProjectReviewError('Offline sdist smoke requires dist/wheelhouse with pinned backend wheels')
    wheels=list(wheelhouse.iterdir())
    if not wheels or len(wheels)>64 or any(item.is_symlink() or not item.is_file() or
            not re.fullmatch(r'[A-Za-z0-9_.+\-]{1,180}\.whl',item.name)
            or item.stat().st_size>128*1024*1024 for item in wheels):
        raise ProjectReviewError('Unsafe sdist wheelhouse contents')
    # `--no-build-isolation` plus no-index prevents implicit backend downloads.
    with tempfile.TemporaryDirectory(prefix='devtool-rm08-sdist-') as temp:
        envroot=Path(temp)/'venv'
        venv.EnvBuilder(with_pip=True,clear=True,symlinks=False).create(str(envroot))
        py=envroot/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
        exe=envroot/('Scripts' if os.name=='nt' else 'bin')/entrypoint
        environment={**os.environ,'PIP_NO_INDEX':'1','PIP_DISABLE_PIP_VERSION_CHECK':'1','PYTHONNOUSERSITE':'1'}
        try:
            bootstrap=subprocess.run([str(py),'-m','pip','install','--no-index',
                '--find-links',str(wheelhouse),'--require-hashes','-r',
                str(root/project['install_lock'])],cwd=temp,env=environment,
                capture_output=True,timeout=timeout)
            if bootstrap.returncode:
                raise ProjectReviewError('Offline hashed sdist backend provisioning failed')
            result=subprocess.run([str(py),'-m','pip','install','--no-index','--no-deps',
                                    '--no-build-isolation','--no-cache-dir',str(candidate)],
                                   cwd=temp,env=environment,capture_output=True,timeout=timeout)
            if result.returncode or not exe.is_file() or exe.is_symlink():
                raise ProjectReviewError('Offline sdist build/install or console script failed')
            command=subprocess.run([str(exe),*args],cwd=temp,env=environment,
                                   capture_output=True,timeout=timeout)
            if command.returncode:raise ProjectReviewError('Installed sdist entrypoint failed')
        except (OSError,subprocess.TimeoutExpired) as exc:
            raise ProjectReviewError('Sdist smoke cannot complete in bounded time') from exc
    return {'schema_version':1,'kind':'devtool.python-offline-sdist-smoke',
            'sdist_sha256':_file_digest(candidate),'entrypoint':entrypoint,
            'arguments':list(args),'installed_without_network':True,'invoked_exit_zero':True,
            'archive_verified':bool(checked),'release_qualified':False}


def verify_go_repeat_build(root: Path, command: list[str], env: dict[str,str]) -> dict[str,Any]:
    """Rebuild candidate twice from the same tracked local module without network.

    The declared build must request -trimpath -buildvcs=false and CGO_ENABLED=0.
    Compare candidate, first rebuild and second rebuild byte-for-byte; inspect
    declared GOOS/GOARCH/module through the production verifier too.
    """
    import os, tempfile
    from .actions_distributions import verify_go_binary, _file_digest
    root=root.resolve()
    if command[:2]==['go','-C'] and len(command)>3:
        directory=command[2];argv=command[3:]
    elif command[0]=='go':directory='.';argv=command[1:]
    else:raise ProjectReviewError('Repeat-build command is not Go')
    if not argv or argv[0]!='build' or argv.count('-o')!=1 or '-trimpath' not in argv or '-buildvcs=false' not in argv:
        raise ProjectReviewError('Repeat-build requires reviewed deterministic Go build flags')
    output=argv[argv.index('-o')+1]
    if not re.fullmatch(r'dist/[A-Za-z0-9_.-]{1,96}',output):
        raise ProjectReviewError('Repeat-build output must be a single module-local dist file')
    workspace=go_workspace_manifest(root,directory=directory)
    candidate=root/('' if directory=='.' else directory+'/')/output
    if candidate.is_symlink() or not candidate.is_file():
        raise ProjectReviewError('Repeat-build candidate unavailable')
    if env.get('CGO_ENABLED','0')!='0':
        raise ProjectReviewError('Repeat-build does not attest CGO-enabled binaries')
    goos=env.get('GOOS','linux');goarch=env.get('GOARCH','amd64')
    declared=verify_go_binary(candidate,goos=goos,goarch=goarch,cgo=False,
                               module=workspace['selected']['module'])
    original=_file_digest(candidate)
    safe_env={**os.environ,'GOOS':goos,'GOARCH':goarch,'CGO_ENABLED':'0',
              'GOTOOLCHAIN':'local','GOPROXY':'off','GOSUMDB':'off','GOFLAGS':''}
    built=[]
    with tempfile.TemporaryDirectory(prefix='devtool-rm08-go-rebuild-') as temp:
        for index in range(2):
            dest=Path(temp)/('candidate'+str(index))
            repeat=argv.copy();repeat[repeat.index('-o')+1]=str(dest)
            try:
                run=subprocess.run(['go']+(['-C',directory] if directory!='.' else [])+repeat,
                                   cwd=root,env=safe_env,capture_output=True,timeout=120)
            except (OSError,subprocess.TimeoutExpired) as exc:
                raise ProjectReviewError('Go deterministic rebuild failed or timed out') from exc
            if run.returncode or not dest.is_file():
                raise ProjectReviewError('Go deterministic rebuild failed (offline deps/toolchain required)')
            digest=_file_digest(dest)
            if digest!=original:raise ProjectReviewError('Go repeat-build differs from candidate output')
            built.append(digest)
    return {'schema_version':1,'kind':'devtool.go-repeat-build',
            'module':workspace['selected']['module'],'candidate_sha256':original,
            'independent_rebuild_sha256':built,'matching':True,
            'binary_header_verified':declared['binary_header_verified'],
            'authenticated':False,'release_qualified':False}
