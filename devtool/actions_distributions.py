"""NEXT-04: bounded, read-only Go/Python distribution verification.

The native DAG produces outputs. This module NEVER executes package builders or
imports produced wheels: it validates a repository-declared artifact inventory.
A hash confirms bytes, not signing, authenticity, or release qualification.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import struct
import tarfile
import zipfile
from email.parser import Parser
from pathlib import Path, PurePosixPath
from typing import Any

from .actions_adoption import _git, _inside_repo, _safe_slug

CONTRACT_PATH = 'ci/devtool-distributions.json'
MAX_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_FILES = 5000
MAX_MEMBER_BYTES = 128 * 1024 * 1024
_SAFE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\Z')
_VERSION = re.compile(r'[A-Za-z0-9][A-Za-z0-9.!+_-]{0,79}\Z')
_SHA256 = re.compile(r'[a-f0-9]{64}\Z')
_SAFE_OUTPUT = re.compile(r'[A-Za-z0-9_./+\-]{1,240}\Z')
_GO_MOD = re.compile(r'^module\s+(\S+)\s*$', re.M)
_GO_VERSION = re.compile(r'^go\s+(1\.[0-9]+(?:\.[0-9]+)?)\s*$', re.M)


class DistributionError(ValueError):
    pass


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _checked_path(root: Path, rel: str, *, must_exist: bool) -> Path:
    if (not isinstance(rel, str) or not _SAFE_OUTPUT.fullmatch(rel) or '\\' in rel
            or rel.startswith('/') or not PurePosixPath(rel).parts
            or any(x in ('', '.', '..') for x in rel.split('/'))):
        raise DistributionError('Distribution path must be bounded and relative')
    path = _inside_repo(root, rel)
    if must_exist:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_BYTES:
            raise DistributionError('Distribution missing, symlinked, or too large: '+rel)
    return path


def _tracked(root: Path, rel: str) -> bool:
    from subprocess import run
    p = run(['git','-C',str(root),'ls-files','--error-unmatch','--',rel],
            capture_output=True, timeout=10)
    return p.returncode == 0


def _text(root: Path, name: str, *, limit: int = 2_000_000) -> str:
    p = _checked_path(root, name, must_exist=True)
    if not _tracked(root,name) or p.stat().st_size > limit:
        raise DistributionError('Required source must be tracked and bounded: '+name)
    return p.read_text(encoding='utf-8')


def _name(name: Any, label: str) -> str:
    if not isinstance(name, str) or not _SAFE_ID.fullmatch(name):
        raise DistributionError('Invalid '+label)
    return name


def _norm(name: str) -> str:
    return re.sub(r'[-_.]+','-',name).lower()


def _module(root: Path) -> dict[str, Any]:
    text = _text(root,'go.mod')
    mod, version = _GO_MOD.search(text), _GO_VERSION.search(text)
    if not mod or not version:
        raise DistributionError('Go module/version missing in tracked go.mod')
    if (root/'go.work').exists():
        work = _text(root,'go.work')
        if not _GO_VERSION.search(work) or not re.search(r'(?m)^use\s+(?:\.|\(\s*$)',work):
            raise DistributionError('Unsupported Go workspace: root module must be explicitly used')
    return {'module':mod.group(1),'go_version':version.group(1),
            'source_sha256':_digest(text.encode()),
            'locks':{n:_digest(_text(root,n).encode()) for n in ('go.sum','go.work','go.work.sum') if (root/n).exists()}}


def _python_source(root: Path) -> dict[str, Any]:
    import tomllib
    text = _text(root, 'pyproject.toml')
    try:
        meta = tomllib.loads(text)
    except (ValueError, TypeError) as exc:
        raise DistributionError('Invalid pyproject TOML') from exc
    project = meta.get('project',{})
    if not isinstance(project,dict) or not isinstance(project.get('name'),str):
        raise DistributionError('Python source requires [project] name')
    if not isinstance(project.get('version'),str):
        raise DistributionError('Dynamic/unpinned Python package version is not admitted')
    name, ver = _name(project['name'],'Python project'),project['version']
    if not _VERSION.fullmatch(ver):
        raise DistributionError('Invalid pinned Python version')
    locks = {n:_digest(_text(root,n).encode()) for n in
             ('uv.lock','poetry.lock','pdm.lock','requirements.txt') if (root/n).exists()}
    if not locks:
        raise DistributionError('Python distribution requires at least one tracked dependency lock/policy')
    if 'requirements.txt' in locks:
        packages = set()
        for line in _text(root,'requirements.txt').splitlines():
            line=line.strip()
            if not line or line.startswith('#'): continue
            if (not re.match(r'^[a-zA-Z0-9_.-]+==[^=\s;]+(?:\s+--hash=sha256:[a-f0-9]{64})+\s*$', line)
                and line not in ('--require-hashes','--no-index')):
                raise DistributionError('requirements.txt has an unpinned/non-hashed dependency')
            if '==' in line:
                packages.add(_norm(line.split('==',1)[0]))
        if 'build' not in packages:
            raise DistributionError('Python distribution requires a hash-pinned build tool in requirements.txt')
    return {'name':name,'version':ver,'source_sha256':_digest(text.encode()),'locks':locks,
            'entrypoints':sorted(project.get('scripts',{})) if isinstance(project.get('scripts',{}),dict) else [],
            'entrypoint_targets':dict(project.get('scripts',{})) if isinstance(project.get('scripts',{}),dict) else {}}


def _read_archive_member(z: zipfile.ZipFile, n: str) -> bytes:
    info=z.getinfo(n)
    if info.file_size > MAX_MEMBER_BYTES:
        raise DistributionError('Oversized wheel member')
    return z.read(info)


def _safe_archive_name(name: str) -> None:
    if ('\\' in name or name.startswith('/') or not name or
        any(p in ('','.', '..') for p in name.rstrip('/').split('/'))):
        raise DistributionError('Unsafe archive member path')


def _wheel_filename_tags(path: Path, *, name: str, version: str) -> set[str]:
    # PEP 427 filename: distribution-version(-build)?-python-abi-platform.whl.
    # Do not load wheel or packaging dynamically just to validate untrusted bytes.
    fields = path.name[:-4].split('-')
    if len(fields) not in (5, 6):
        raise DistributionError('Wheel filename is malformed')
    distribution, declared_version = fields[:2]
    if _norm(distribution) != _norm(name) or declared_version != version:
        raise DistributionError('Wheel filename does not match declared distribution')
    if len(fields) == 6 and not re.fullmatch(r'[0-9][A-Za-z0-9_]*', fields[2]):
        raise DistributionError('Wheel build tag malformed')
    triples = fields[-3:]
    if any(not re.fullmatch(r'[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*', field) for field in triples):
        raise DistributionError('Wheel compatibility tag malformed')
    python, abi, platform = (part.split('.') for part in triples)
    if len(python)*len(abi)*len(platform) > 256:
        raise DistributionError('Wheel expanded compatibility tags exceed bound')
    return {f'{py}-{a}-{plat}' for py in python for a in abi for plat in platform}


def verify_wheel(path: Path, *, name: str, version: str, entrypoints: list[str],
                 entrypoint_targets: dict[str,str] | None = None) -> dict[str, Any]:
    if path.suffix!='.whl': raise DistributionError('Expected .whl extension')
    filename_tags = _wheel_filename_tags(path, name=name, version=version)
    try:
        with zipfile.ZipFile(path) as z:
            infos=z.infolist()
            if len(infos)>MAX_ARCHIVE_FILES or len({x.filename for x in infos})!=len(infos):
                raise DistributionError('Wheel has duplicate or too many members')
            total=0
            for info in infos:
                _safe_archive_name(info.filename)
                if (info.external_attr >>16) & 0o170000 == 0o120000:
                    raise DistributionError('Wheel contains symlink')
                total+=info.file_size
                if info.file_size>MAX_MEMBER_BYTES or total>MAX_BYTES:
                    raise DistributionError('Wheel expanded size exceeds limit')
            dist_infos={n.split('/')[0] for n in z.namelist() if n.endswith('.dist-info/METADATA')}
            if len(dist_infos)!=1:
                raise DistributionError('Wheel must have exactly one dist-info/METADATA')
            di=next(iter(dist_infos))
            metadata=Parser().parsestr(_read_archive_member(z,di+'/METADATA').decode('utf-8'))
            if _norm(metadata.get('Name',''))!=_norm(name) or metadata.get('Version')!=version:
                raise DistributionError('Wheel metadata name/version mismatch')
            wheel=Parser().parsestr(_read_archive_member(z,di+'/WHEEL').decode('utf-8'))
            if wheel.get('Wheel-Version','').split('.')[0]!='1' or not wheel.get_all('Tag'):
                raise DistributionError('Wheel tag/version missing or unsupported')
            declared_tags = wheel.get_all('Tag') or []
            if len(declared_tags) != len(set(declared_tags)) or not set(declared_tags).issubset(filename_tags):
                raise DistributionError('Wheel WHEEL tags conflict with filename compatibility tags')
            record=di+'/RECORD'
            raw=_read_archive_member(z,record).decode('utf-8')
            rows=list(csv.reader(io.StringIO(raw)))
            records={}
            for row in rows:
                if len(row)!=3 or row[0] in records:
                    raise DistributionError('Malformed or duplicate wheel RECORD')
                records[row[0]]=(row[1],row[2])
            actual={x.filename for x in infos if not x.is_dir()}
            if set(records)!=actual:
                raise DistributionError('Wheel RECORD does not inventory exact wheel members')
            for file,(hashval,size) in records.items():
                if file == record:
                    if hashval or size: raise DistributionError('Wheel RECORD self-hash must be blank')
                    continue
                if not hashval.startswith('sha256=') or not size.isdecimal():
                    raise DistributionError('Wheel member lacks SHA256 and size')
                import base64
                content=_read_archive_member(z,file)
                expected=base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b'=').decode()
                if hashval[7:]!=expected or int(size)!=len(content):
                    raise DistributionError('Wheel RECORD hash/size mismatch')
            if entrypoints:
                from configparser import ConfigParser
                content=_read_archive_member(z,di+'/entry_points.txt').decode('utf-8')
                parser=ConfigParser(interpolation=None)
                parser.read_string(content)
                if not parser.has_section('console_scripts') or not set(entrypoints).issubset(parser['console_scripts']):
                    raise DistributionError('Declared Python console scripts absent from wheel')
                if entrypoint_targets is not None:
                    for command in entrypoints:
                        if parser['console_scripts'][command].strip() != entrypoint_targets[command]:
                            raise DistributionError('Wheel console script target disagrees with pyproject.toml')
            return {'format':'wheel','metadata_name':metadata['Name'],'version':metadata['Version'],
                    'tags':wheel.get_all('Tag'),'members':len(actual),'record_sha256_verified':True,
                    'entrypoints_verified':entrypoints}
    except (OSError,zipfile.BadZipFile,UnicodeError,KeyError,ValueError,RuntimeError) as exc:
        if isinstance(exc,DistributionError): raise
        raise DistributionError('Wheel validation failed: '+type(exc).__name__) from None


def verify_sdist(path: Path, *, name: str, version: str) -> dict[str, Any]:
    if not path.name.endswith(('.tar.gz','.tar.xz')):
        raise DistributionError('Only tar.gz/tar.xz sdists are admitted')
    try:
        with tarfile.open(path,mode='r:*') as tar:
            members=tar.getmembers()
            if len(members)>MAX_ARCHIVE_FILES or not members:
                raise DistributionError('Sdist has too many/zero members')
            names=set();meta=[];total=0
            for m in members:
                _safe_archive_name(m.name)
                if m.name in names:raise DistributionError('Duplicate sdist member')
                names.add(m.name)
                if not(m.isfile() or m.isdir()):raise DistributionError('Sdist symlink/special file refused')
                total+=m.size
                if m.size>MAX_MEMBER_BYTES or total>MAX_BYTES:raise DistributionError('Sdist exceeds extraction size bound')
                if m.isfile() and len(PurePosixPath(m.name).parts)==2 and m.name.endswith('/PKG-INFO'):
                    meta.append(m)
            if len(meta)!=1:raise DistributionError('Sdist must contain root PKG-INFO')
            src=tar.extractfile(meta[0]);assert src is not None
            attrs=Parser().parsestr(src.read(MAX_MEMBER_BYTES+1).decode('utf-8'))
            if _norm(attrs.get('Name',''))!=_norm(name) or attrs.get('Version')!=version:
                raise DistributionError('Sdist metadata mismatch')
            return {'format':'sdist','metadata_name':attrs['Name'],'version':attrs['Version'],
                    'members':len(members),'metadata_verified':True}
    except (OSError,tarfile.TarError,UnicodeError,ValueError) as exc:
        if isinstance(exc,DistributionError):raise
        raise DistributionError('Sdist validation failed: '+type(exc).__name__) from None


def verify_go_binary(path: Path, *, goos: str, goarch: str, cgo: bool, module: str = '') -> dict[str, Any]:
    if (goos,goarch) not in {('linux','amd64'),('linux','arm64'),('windows','amd64'),
                           ('windows','arm64'),('darwin','amd64'),('darwin','arm64')}:
        raise DistributionError('Unsupported Go OS/architecture tuple')
    if cgo and (goos,goarch)!=('linux','amd64'):
        raise DistributionError('CGO cross compilation requires an independently qualified toolchain')
    with path.open('rb') as f:head=f.read(512)
    target = ('ELF' if goos=='linux' else 'PE' if goos=='windows' else 'Mach-O')
    if goos=='linux':
        machine={'amd64':62,'arm64':183}[goarch]
        if len(head)<20 or head[:4]!=b'\x7fELF' or head[4]!=2 or head[5]!=1 or int.from_bytes(head[18:20],'little')!=machine:
            raise DistributionError('Go ELF architecture/format differs from declared target')
    elif goos=='windows':
        if len(head)<64 or head[:2]!=b'MZ':raise DistributionError('Expected Windows PE executable')
        off=int.from_bytes(head[0x3c:0x40],'little')
        with path.open('rb') as f:
            f.seek(off)
            pe=f.read(6)
        if pe[:4]!=b'PE\0\0' or int.from_bytes(pe[4:6],'little')!={'amd64':0x8664,'arm64':0xaa64}[goarch]:
            raise DistributionError('Windows PE architecture mismatch')
    else:
        if len(head)<8 or head[:4]!=b'\xcf\xfa\xed\xfe' or int.from_bytes(head[4:8],'little')!={'amd64':0x01000007,'arm64':0x0100000c}[goarch]:
            raise DistributionError('Darwin Mach-O architecture mismatch')
    buildinfo = {'present':False,'module_matches_source':False}
    if module:
        # Inspect Go build info without executing the candidate binary. A forged
        # module path is not authenticity; the result remains candidate evidence.
        import subprocess
        try:
            process = subprocess.run(['go','version','-m',str(path)],capture_output=True,
                                     text=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            raise DistributionError('Go binary buildinfo inspection unavailable') from None
        if process.returncode or not any(line.strip().split('\t')[:2]==['mod', module] for line in process.stdout.splitlines()):
            raise DistributionError('Go binary module metadata does not match source')
        # A matching ELF/Mach-O/PE header does not prove the recorded toolchain
        # target or CGO setting. Read Go buildinfo without executing the binary.
        settings: dict[str,str] = {}
        for line in process.stdout.splitlines():
            parts = line.strip().split('\t', 1)
            if len(parts) != 2 or parts[0] != 'build' or '=' not in parts[1]:
                continue
            key, value = parts[1].split('=', 1)
            if key in settings:
                raise DistributionError('Duplicate Go buildinfo setting: '+key)
            settings[key] = value
        if any(settings.get(key) != value for key, value in
               (('GOOS', goos), ('GOARCH', goarch), ('CGO_ENABLED', '1' if cgo else '0'))):
            raise DistributionError('Go binary buildinfo GOOS/GOARCH/CGO differs from declaration')
        buildinfo = {'present':True,'module_matches_source':True,'goos_matches':True,
                     'goarch_matches':True,'cgo_matches':True}
    return {'format':target,'goos':goos,'goarch':goarch,'cgo_enabled':cgo,
            'binary_header_verified':True,'go_buildinfo':buildinfo,
            'native_runnable_on_host':goos=='linux' and goarch=='amd64'}


def distribution_contract(root: Path, *, target: str, workflow: str) -> dict[str, Any]:
    root=root.resolve();target=_safe_slug(target,role='Target');workflow=_safe_slug(workflow,role='Workflow')
    raw=_text(root,CONTRACT_PATH)
    try:doc=json.loads(raw)
    except json.JSONDecodeError:raise DistributionError('Invalid distribution JSON') from None
    if (not isinstance(doc,dict) or set(doc)!={'schema_version','target','workflow','distributions'}
            or doc['schema_version']!=1 or doc['target']!=target or doc['workflow']!=workflow):
        raise DistributionError('Distribution contract identity/schema mismatch')
    entries=doc['distributions']
    if not isinstance(entries,list) or not 1<=len(entries)<=32:
        raise DistributionError('Distribution inventory must contain 1–32 outputs')
    rows=[];ids=set();paths=set()
    for entry in entries:
        if not isinstance(entry,dict) or not {'id','kind','path'}.issubset(entry):
            raise DistributionError('Distribution record missing required fields')
        ident=_name(entry['id'],'distribution ID')
        if ident in ids:raise DistributionError('Duplicate distribution ID')
        ids.add(ident)
        kind=entry['kind']
        allowed={'go':{'id','kind','path','module','goos','goarch','cgo_enabled'},
                 'wheel':{'id','kind','path','name','version','entrypoints'},
                 'sdist':{'id','kind','path','name','version'}}
        if kind not in allowed or set(entry)!=allowed[kind]:
            raise DistributionError('Unknown/missing distribution fields')
        path=entry['path'];_checked_path(root,path,must_exist=False)
        if not path.startswith('dist/') or path.startswith('dist/.'):
            raise DistributionError('Candidate output paths must be within dist/')
        if path in paths:raise DistributionError('Duplicate output path')
        paths.add(path)
        if kind=='go':
            module=_module(root)
            if entry['module']!=module['module'] or type(entry['cgo_enabled']) is not bool:
                raise DistributionError('Go module/CGO identity mismatch')
            if (entry['goos'],entry['goarch']) not in {('linux','amd64'),('linux','arm64'),('windows','amd64'),('windows','arm64'),('darwin','amd64'),('darwin','arm64')}:
                raise DistributionError('Unsupported Go distribution target')
            if entry['cgo_enabled'] and (entry['goos'],entry['goarch'])!=('linux','amd64'):
                raise DistributionError('CGO cross compilation not admitted')
            source=module
        else:
            source=_python_source(root)
            if _norm(entry['name'])!=_norm(source['name']) or entry['version']!=source['version']:
                raise DistributionError('Python distribution differs from pyproject metadata')
            if kind=='wheel':
                ep=entry['entrypoints']
                if not isinstance(ep,list) or len(ep)>64 or any(not isinstance(i,str) or not _SAFE_ID.fullmatch(i) for i in ep):
                    raise DistributionError('Wheel entrypoints must be bounded identifiers')
                if not set(ep).issubset(source['entrypoints']):
                    raise DistributionError('Wheel entrypoints differ from pyproject scripts')
        rows.append({'id':ident,'kind':kind,'path':path,'expected':entry,'source':source})
    return {'schema_version':1,'kind':'devtool.actions.distribution-contract','source_path':CONTRACT_PATH,
            'contract_sha256':_digest(raw.encode()),'target':target,'workflow':workflow,'entries':rows,
            'read_only':True,'source_attested':False,'formal_release_qualified':False}


def verify_distributions(root: Path, *, target: str, workflow: str,
                         require_outputs: bool = True) -> dict[str, Any]:
    """Read-only, zero side effects. Missing, forged, or malformed outputs fail closed."""
    root=root.resolve()
    base=distribution_contract(root,target=target,workflow=workflow)
    import os, subprocess
    # Unlike review-only planning, artifact verification refuses source mutations
    # made by a job after GitHub checked out its reviewed commit.
    tracked = {CONTRACT_PATH}
    for row in base['entries']:
        tracked.add('go.mod' if row['kind']=='go' else 'pyproject.toml')
        tracked.update(row['source']['locks'])
    for rel in sorted(tracked):
        p=_checked_path(root,rel,must_exist=True)
        observed=subprocess.run(['git','-C',str(root),'show','HEAD:'+rel],capture_output=True,timeout=12)
        if observed.returncode or _digest(observed.stdout)!=_file_digest(p):
            raise DistributionError('Tracked source changed after Git commit: '+rel)
    expected_sha=os.environ.get('GITHUB_SHA','')
    head=_git(root,'rev-parse','HEAD')
    if expected_sha and expected_sha != head:
        raise DistributionError('GitHub source SHA and checked-out HEAD differ')
    checks=[]
    for row in base['entries']:
        entry=row['expected'];path=_checked_path(root,entry['path'],must_exist=require_outputs)
        if not path.is_file():
            checks.append({'id':entry['id'],'state':'NOT_RUN','path':entry['path']})
            continue
        if entry['kind']=='go':details=verify_go_binary(path,goos=entry['goos'],goarch=entry['goarch'],cgo=entry['cgo_enabled'],module=entry['module'])
        elif entry['kind']=='wheel':details=verify_wheel(path,name=entry['name'],version=entry['version'],entrypoints=entry['entrypoints'],entrypoint_targets=row['source']['entrypoint_targets'])
        else:details=verify_sdist(path,name=entry['name'],version=entry['version'])
        checks.append({'id':entry['id'],'state':'LOCAL_BYTES_VERIFIED','path':entry['path'],
                       'size':path.stat().st_size,'sha256':_file_digest(path),'details':details})
    return {'schema_version':1,'kind':'devtool.actions.distribution-verification',
            'contract_sha256':base['contract_sha256'],'target':target,'workflow':workflow,
            'source_commit':head,'checks':checks,'all_local_bytes_verified':all(x['state']=='LOCAL_BYTES_VERIFIED' for x in checks),
            'remote_source_authenticated':False,'hosted_run_attested':False,
            'signatures_authenticated':False,'native_device_qualified':False,
            'formal_release_qualified':False,'read_only':True}
