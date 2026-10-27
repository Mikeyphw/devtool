"""RM-10 source-reviewed Cargo workspace/provider and byte-only artifact checks.

Planning never runs Cargo, executes manifest scripts, installs SDKs or downloads
crates. The native Devtool target runner remains the only executor.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import fnmatch
import json
import re
import subprocess
import tarfile
import tomllib
import struct


class CargoError(ValueError):
    pass

MAX_SOURCE=1_048_576
_NAME=re.compile(r'[A-Za-z][A-Za-z0-9_-]{0,63}\Z')
_VERSION=re.compile(r'\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?\Z')
_CHANNEL=re.compile(r'1\.(?:[7-9]\d|\d{3})\.\d+\Z')
_ALLOWED={'x86_64-unknown-linux-gnu','aarch64-unknown-linux-gnu'}
_ARCH={'x86_64-unknown-linux-gnu':(62,'ELF64-little-x86_64'),'aarch64-unknown-linux-gnu':(183,'ELF64-little-aarch64')}
_CROSS_LINKER='aarch64-linux-gnu-gcc'


def _path(root:Path,rel:str,*,existing=True)->Path:
    if (not isinstance(rel,str) or not rel or len(rel)>210 or rel.startswith('/')
            or '\\' in rel or not re.fullmatch(r'[a-zA-Z0-9_./+-]+',rel)
            or any(p in ('','.','..') for p in rel.split('/'))):
        raise CargoError('RM-10 unsafe manifest/artifact path')
    path=root
    for piece in rel.split('/'):
        path=path/piece
        if path.is_symlink():raise CargoError('RM-10 symlink path refused')
    if existing and (not path.is_file() or path.stat().st_size>MAX_SOURCE):
        raise CargoError('RM-10 missing or oversized source: '+rel)
    return path


def _tracked(root:Path)->list[str]:
    try:
        p=subprocess.run(['git','-C',str(root),'ls-files','-z'],capture_output=True,check=True,timeout=15)
    except (OSError,subprocess.CalledProcessError,subprocess.TimeoutExpired) as e:
        raise CargoError('RM-10 tracked source unavailable') from e
    names=[n.decode('utf-8') for n in p.stdout.split(b'\0') if n]
    if len(names)>10000:raise CargoError('RM-10 tracked inventory exceeds bound')
    return names


def _source(root:Path,rel:str,names:set[str])->bytes:
    if rel not in names:raise CargoError('RM-10 requires tracked manifest: '+rel)
    raw=_path(root,rel).read_bytes()
    if b'\0' in raw:raise CargoError('RM-10 binary manifest refused')
    return raw


def _manifest(root:Path,rel:str,names:set[str])->dict:
    try:content=tomllib.loads(_source(root,rel,names).decode('utf-8'))
    except (UnicodeError,tomllib.TOMLDecodeError) as e:
        raise CargoError('RM-10 malformed manifest: '+rel) from e
    if not isinstance(content,dict):raise CargoError('RM-10 malformed Cargo TOML')
    return content


def cargo_workspace(root:Path)->dict:
    root=root.resolve();names=set(_tracked(root))
    # Cargo configuration can inject aliases, external runners, linker commands,
    # network sources and environment variables behind an otherwise reviewed argv.
    if '.cargo/config' in names or '.cargo/config.toml' in names:
        raise CargoError('RM-10 unreviewed .cargo config/runner execution refused')
    root_manifest=_manifest(root,'Cargo.toml',names)
    tool=_manifest(root,'rust-toolchain.toml',names)
    channel=tool.get('toolchain',{}).get('channel')
    if not isinstance(channel,str) or not _CHANNEL.fullmatch(channel):
        raise CargoError('RM-10 rust-toolchain must pin exact Rust semver (no stable/nightly/ranges)')
    profile=tool['toolchain']
    if set(profile)-{'channel','components','targets','profile'} or profile.get('profile','minimal') not in ('minimal','default'):
        raise CargoError('RM-10 unsupported toolchain installer/profile declaration')
    components=profile.get('components',[])
    if not isinstance(components,list) or len(set(components))!=len(components) or any(c not in ('rustfmt','clippy') for c in components):
        raise CargoError('RM-10 unknown/unreviewed rustup component')
    targets=profile.get('targets', ['x86_64-unknown-linux-gnu'])
    if not isinstance(targets,list) or not targets or len(set(targets))!=len(targets) or any(t not in _ALLOWED for t in targets):
        raise CargoError('RM-10 unsupported cross-compile target or implicit installation')
    ws=root_manifest.get('workspace',{})
    if ws and not isinstance(ws,dict):raise CargoError('RM-10 malformed workspace')
    found=sorted(n for n in names if n=='Cargo.toml' or n.endswith('/Cargo.toml'))
    if len(found)>64:raise CargoError('RM-10 Cargo manifest inventory exceeds bound')
    candidates=['.'] if 'package' in root_manifest else []
    patterns=ws.get('members',[]) if ws else []
    if not isinstance(patterns,list) or len(patterns)>32 or any(not isinstance(p,str) or not p or p.startswith('/') or '..' in p.split('/') or '\\' in p for p in patterns):
        raise CargoError('RM-10 unreviewed workspace members')
    for pattern in patterns:
        matches=[p[:-11] for p in found if p!='Cargo.toml' and
                 len(p[:-11].split('/'))==len(pattern.split('/')) and fnmatch.fnmatchcase(p[:-11],pattern)]
        if not matches:raise CargoError('RM-10 empty/missing workspace member pattern')
        candidates+=matches
    if not ws and not candidates:raise CargoError('RM-10 missing workspace/package root')
    if not ws and len(found)>1:raise CargoError('RM-10 ambiguous unreviewed nested package')
    if len(set(candidates))!=len(candidates) or len(candidates)>32:
        raise CargoError('RM-10 duplicate or too many workspace members')
    if ws and (set(ws)-{'members','resolver','package','dependencies','exclude','default-members'}):
        raise CargoError('RM-10 unreviewed workspace configuration')
    if ws and ws.get('resolver','2') not in ('2','3'):
        raise CargoError('RM-10 unreviewed workspace resolver')
    excluded=ws.get('exclude',[]) if ws else []
    if not isinstance(excluded,list) or len(excluded)>32 or any(not isinstance(x,str) or x.startswith('/') or '..' in x.split('/') for x in excluded):
        raise CargoError('RM-10 unsafe workspace exclude')
    if excluded:
        excludes=set(excluded)
        if any('*' in x or '?' in x or '[' in x for x in excludes):
            raise CargoError('RM-10 unreviewed workspace exclude glob')
        candidates=[c for c in candidates if c not in excludes]
    default_members=ws.get('default-members',[]) if ws else []
    if not isinstance(default_members,list) or any(x not in candidates for x in default_members):
        raise CargoError('RM-10 invalid workspace default members')
    shared_package=ws.get('package',{}) if ws else {}
    shared_deps=ws.get('dependencies',{}) if ws else {}
    if not isinstance(shared_package,dict) or not isinstance(shared_deps,dict):
        raise CargoError('RM-10 malformed inherited workspace metadata')
    patches=root_manifest.get('patch',{})
    if patches:
        if not isinstance(patches,dict) or set(patches)!={'crates-io'} or not isinstance(patches['crates-io'],dict):
            raise CargoError('RM-10 unreviewed Cargo patch source')
        for name,entry in patches['crates-io'].items():
            if not isinstance(entry,dict) or set(entry)-{'path','package'} or not isinstance(entry.get('path'),str):
                raise CargoError('RM-10 unsafe local patch')
            if '/' in entry['path'] and any(c in entry['path'] for c in '*?['):
                raise CargoError('RM-10 unsafe patch glob')
            if entry['path'] not in candidates:
                raise CargoError('RM-10 patch must refer to a reviewed local member')
    members={};deps={};features={};sha={};all_names=set()
    for folder in sorted(candidates):
        rel='Cargo.toml' if folder=='.' else folder+'/Cargo.toml'
        obj=root_manifest if folder=='.' else _manifest(root,rel,names)
        pack=obj.get('package')
        if isinstance(pack,dict) and pack.get('version')=={'workspace':True}:
            pack={**pack,'version':shared_package.get('version')}
        if not isinstance(pack,dict) or not _NAME.fullmatch(str(pack.get('name',''))) or not _VERSION.fullmatch(str(pack.get('version',''))):
            raise CargoError('RM-10 member requires explicit package name/version: '+rel)
        name=pack['name']
        if name in all_names:raise CargoError('RM-10 duplicate package names')
        all_names.add(name)
        if pack.get('build') not in (None,False):raise CargoError('RM-10 build scripts refused without reviewed host permissions')
        if (folder+'/build.rs' if folder!='.' else 'build.rs') in names:
            raise CargoError('RM-10 implicit build.rs refused')
        feature=obj.get('features',{})
        if not isinstance(feature,dict) or len(feature)>48 or any(not _NAME.fullmatch(k) or not isinstance(v,list) or len(v)>48 or any(not isinstance(f,str) or not re.fullmatch(r'[A-Za-z0-9_:/?+-]{1,90}',f) for f in v) for k,v in feature.items()):
            raise CargoError('RM-10 unsafe feature declarations')
        members[folder]={'name':name,'version':pack['version'],'manifest':rel}
        deps[folder]=obj
        features[name]=sorted(feature)
        sha[rel]=sha256(_source(root,rel,names)).hexdigest()
    # Keep the admitted subset of feature syntax auditable; arbitrary Cargo
    # feature expansion is deferred rather than resolved with undocumented logic.
    for folder,obj in deps.items():
        local_features=obj.get('features',{})
        dependencies={}
        for typ in ('dependencies','dev-dependencies','build-dependencies'):
            raw=obj.get(typ,{})
            if isinstance(raw,dict):dependencies.update(raw)
        for name,refs in local_features.items():
            for reference in refs:
                if reference in local_features:continue
                if reference.startswith('dep:'):
                    dep=dependencies.get(reference[4:])
                    if not isinstance(dep,dict) or dep.get('optional') is not True:
                        raise CargoError('RM-10 feature dependency is not a declared optional dependency')
                    continue
                if '/' in reference:
                    dependency,feature=reference.split('/',1)
                    if dependency not in dependencies or not _NAME.fullmatch(feature):
                        raise CargoError('RM-10 feature references unreviewed dependency')
                    continue
                raise CargoError('RM-10 unknown Cargo feature expansion')
        seen=set();stack=set()
        def visit_feature(name):
            if name in stack:raise CargoError('RM-10 Cargo feature expansion cycle')
            if name in seen:return
            stack.add(name)
            for ref in local_features[name]:
                if ref in local_features:visit_feature(ref)
            stack.remove(name);seen.add(name)
        for name in local_features:visit_feature(name)
    folder_by_name={v['name']:k for k,v in members.items()}
    for name,entry in patches.get('crates-io',{}).items():
        member=members[entry['path']]
        if member['name']!=entry.get('package',name):
            raise CargoError('RM-10 patch name does not match reviewed workspace member')
    edges={k:[] for k in members}
    for folder,obj in deps.items():
        for typ in ('dependencies','dev-dependencies','build-dependencies'):
            dependencies=obj.get(typ,{})
            if not isinstance(dependencies,dict):raise CargoError('RM-10 malformed dependency table')
            for name,info in dependencies.items():
                workspace_relative=False
                if isinstance(info,dict) and info.get('workspace') is True:
                    workspace_relative=True
                    inherited=shared_deps.get(name)
                    if not isinstance(inherited,dict) or 'path' not in inherited or set(info)-{'workspace','features','optional'}:
                        raise CargoError('RM-10 unreviewed inherited workspace dependency')
                    info={**inherited, **{k:v for k,v in info.items() if k!='workspace'}}
                if isinstance(info,dict) and ('git' in info or 'registry' in info or 'branch' in info):
                    raise CargoError('RM-10 unreviewed git/custom registry dependency')
                if not isinstance(info,dict) or 'path' not in info:continue
                raw=info['path']
                if not isinstance(raw,str) or raw.startswith('/') or '\\' in raw or '*' in raw:
                    raise CargoError('RM-10 unsafe path dependency')
                parts=[]
                for part in ([*(() if workspace_relative or folder=='.' else folder.split('/')),*raw.split('/')]):
                    if part=='..':
                        if not parts:raise CargoError('RM-10 path dependency escapes checkout')
                        parts.pop()
                    elif part not in ('','.'):parts.append(part)
                dest='/'.join(parts) or '.'
                if dest not in members:raise CargoError('RM-10 local dependency is not a reviewed workspace member: '+dest)
                if dest!=folder and dest not in edges[folder]:edges[folder].append(dest)
        edges[folder].sort()
    visiting=set();visited=set();order=[]
    def visit(n):
        if n in visiting:raise CargoError('RM-10 workspace dependency cycle')
        if n in visited:return
        visiting.add(n)
        for dep in edges[n]:visit(dep)
        visiting.remove(n);visited.add(n);order.append(n)
    for n in sorted(members):visit(n)
    lock='Cargo.lock';data=_manifest(root,lock,names)
    if any(isinstance(pkg,dict) and isinstance(pkg.get('source'),str) and
           (pkg['source'].startswith('git+') or not pkg['source'].startswith('registry+https://github.com/rust-lang/crates.io-index'))
           for pkg in data.get('package',[]) if isinstance(data.get('package'),list)):
        raise CargoError('RM-10 unreviewed lockfile registry/git source')
    if data.get('version') not in (3,4) or not isinstance(data.get('package'),list):
        raise CargoError('RM-10 Cargo.lock must have reviewed version/package structure')
    # No manifest-only claim: lock must include every local package identity.
    indexed={(p.get('name'),p.get('version')) for p in data['package'] if isinstance(p,dict)}
    if any((p['name'],p['version']) not in indexed for p in members.values()):
        raise CargoError('RM-10 Cargo.lock lacks a workspace member identity')
    sha[lock]=sha256(_source(root,lock,names)).hexdigest()
    sha['rust-toolchain.toml']=sha256(_source(root,'rust-toolchain.toml',names)).hexdigest()
    return {'schema_version':1,'members':members,'edges':edges,'topological_members':order,
            'features':features,'toolchain':channel,'components':components,'targets':targets,
            'source_sha256':sha,'lockfiles':sorted(sha),'release_qualified':False}


def inspect_cargo_job(root:Path,argv:list[str],metadata:dict)->dict:
    if not isinstance(argv,list) or len(argv)>20 or not argv or argv[0]!='cargo' or any(not isinstance(a,str) or not re.fullmatch(r'[A-Za-z0-9_./:+,=@%^-]{1,120}|--',a) for a in argv):
        raise CargoError('RM-10 requires bounded direct cargo argv (no shell)')
    manifest=cargo_workspace(root)
    if len(argv)<2:raise CargoError('RM-10 Cargo operation is required')
    target=metadata.get('ci_rust_target','x86_64-unknown-linux-gnu')
    json_requested=metadata.get('ci_rust_cargo_json',False) is True
    review_argv=list(argv)
    if json_requested and '--message-format=json' in review_argv:
        review_argv.remove('--message-format=json')
    operation=argv[1];allow={'fmt','clippy','test','build','package'}
    if operation not in allow:raise CargoError('RM-10 unreviewed Cargo operation (no run/publish/install)')
    # Fixed command templates refuse extra shell-like cargo flags and escape valves.
    template={
       'fmt':['cargo','fmt','--all','--','--check'],
       'clippy':['cargo','clippy','--workspace','--all-targets','--offline','--locked','--','-D','warnings'],
       'test':['cargo','test','--workspace','--offline','--locked'],
       'build':['cargo','build','--workspace','--release','--offline','--locked'],
    }
    chosen=''
    if operation=='package':
        if review_argv[:3]!=['cargo','package','-p'] or len(review_argv)!=6 or review_argv[4:]!=['--offline','--locked']:
            raise CargoError('RM-10 cargo package requires -p NAME --offline --locked')
        chosen=review_argv[3]
        if chosen not in {p['name'] for p in manifest['members'].values()}:
            raise CargoError('RM-10 unreviewed crate package name')
    elif operation in ('test','build','clippy') and '--features' in review_argv:
        # All feature flags are selected explicitly in native command bytes;
        # metadata alone cannot silently change what Cargo actually executes.
        if review_argv.count('--features')!=1 or review_argv.index('--features')!=3:
            raise CargoError('RM-10 unsafe Cargo feature argv position')
        variant=[*template[operation][:3],*review_argv[3:5],*template[operation][3:]]
        if target!='x86_64-unknown-linux-gnu':variant+=['--target',target]
        if review_argv!=variant:raise CargoError('RM-10 Cargo args differ from reviewed feature profile')
    elif review_argv!=template[operation]+(['--target',target] if target!='x86_64-unknown-linux-gnu' and operation=='build' else []):
        raise CargoError('RM-10 Cargo args differ from reviewed offline/failing template or cross-target policy')
    if operation in ('fmt','clippy') and operation not in ('fmt' if 'rustfmt' in manifest['components'] else '', 'clippy' if 'clippy' in manifest['components'] else ''):
        raise CargoError('RM-10 missing pinned toolchain fmt/Clippy component')
    ver=manifest['toolchain']
    versions=metadata.get('ci_rust_versions',ver)
    if not isinstance(versions,str) or len(versions)>80:raise CargoError('RM-10 unreviewed toolchain matrix')
    cells=versions.split(',')
    if not 1<=len(cells)<=2 or len(set(cells))!=len(cells) or ver not in cells or any(not _CHANNEL.fullmatch(v) for v in cells):
        raise CargoError('RM-10 toolchain matrix must include pinned source version and at most two semver cells')
    sets=metadata.get('ci_rust_feature_sets','')
    if not isinstance(sets,str) or len(sets)>360:
        raise CargoError('RM-10 unreviewed feature matrix')
    matrix=[]
    if sets:
        matrix=sets.split(';')
        if not 1<=len(matrix)<=3 or len(set(matrix))!=len(matrix):
            raise CargoError('RM-10 feature matrix cap or duplicate cell')
        if any(not cell or (cell!='none' and (len(cell)>128 or len(set(cell.split(',')))!=len(cell.split(',')))) for cell in matrix):
            raise CargoError('RM-10 invalid feature matrix cell')
    target=metadata.get('ci_rust_target','x86_64-unknown-linux-gnu')
    if target not in _ALLOWED or target not in manifest['targets']:
        raise CargoError('RM-10 unsupported triple requires qualified cross toolchain')
    feature_names=metadata.get('ci_rust_features','')
    if not isinstance(feature_names,str) or len(feature_names)>128:raise CargoError('RM-10 unreviewed feature selection')
    selected=feature_names.split(',') if feature_names else []
    available={pkg+'/'+feature for pkg,features in manifest['features'].items() for feature in features}
    if any(f not in available for f in selected) or len(set(selected))!=len(selected):
        raise CargoError('RM-10 feature declaration not in reviewed workspace')
    if matrix:
        if (','.join(selected) if selected else 'none') != matrix[0]:
            raise CargoError('RM-10 reviewed feature matrix must begin with native source command selection')
        for cell in matrix:
            names=[] if cell=='none' else cell.split(',')
            if any(f not in available for f in names):
                raise CargoError('RM-10 unreviewed matrix feature')
    else:
        matrix=[','.join(selected) if selected else 'none']
    if target!='x86_64-unknown-linux-gnu':
        if operation!='build' or review_argv[-2:]!=['--target',target]:
            raise CargoError('RM-10 cross target requires reviewed build --target triple and linker')
    if selected:
        if operation not in ('test','build','clippy') or '--features' not in review_argv or review_argv[4]!=','.join(selected):
            raise CargoError('RM-10 selected features require matching explicit Cargo --features argv')
    elif '--features' in review_argv:
        raise CargoError('RM-10 feature argv requires reviewed metadata identity')
    capture=metadata.get('ci_rust_cargo_json',False)
    if not isinstance(capture,bool):
        raise CargoError('RM-10 Cargo JSON capture must be a reviewed boolean')
    if capture and operation not in ('test','build','clippy'):
        raise CargoError('RM-10 Cargo JSON capture unsupported for this operation')
    # Existing Cargo templates do not opt into JSON by default. Capture is
    # supported with explicit native command --message-format=json only.
    if capture and argv.count('--message-format=json')!=1:
        raise CargoError('RM-10 Cargo JSON capture requires exactly one explicit compiler JSON command')
    return {'provider':'rust-cargo','toolchain':'rust','version':ver,'matrix_versions':cells,
            'version_source':'rust-toolchain.toml','workspace':manifest,'target_triple':target,
            'command':operation,'selected_features':selected,'feature_matrix':matrix,'cargo_json_capture':capture,'package_name':chosen,'lockfiles':manifest['lockfiles'],
            'report_adapter':'native-exit-cargo-json-optional','output_role':'local-bytes-only',
            'cache_enabled':False,'release_qualified':False}


def verify_rust_binary(path:Path,*,target:str)->dict:
    if target not in _ALLOWED:raise CargoError('RM-10 unreviewed Rust binary target triple')
    if path.is_symlink() or not path.is_file() or not (0<path.stat().st_size<=200_000_000):
        raise CargoError('RM-10 missing, symlinked or oversized Rust binary')
    with path.open('rb') as f:header=f.read(64)
    if len(header)<64 or header[:4]!=b'\x7fELF' or header[4]!=2 or header[5]!=1 or struct.unpack('<H',header[18:20])[0]!=_ARCH[target][0]:
        raise CargoError('RM-10 Rust ELF architecture/format differs from declared target triple')
    return {'path':str(path),'sha256':sha256(path.read_bytes()).hexdigest(), 'bytes':path.stat().st_size,
            'target':target,'format':_ARCH[target][1],'signer_authenticated':False,'release_qualified':False}


def verify_crate(path:Path,*,name:str,version:str)->dict:
    if not _NAME.fullmatch(name) or not _VERSION.fullmatch(version):raise CargoError('RM-10 invalid crate identity')
    if path.is_symlink() or path.name!=f'{name}-{version}.crate' or not path.is_file() or path.stat().st_size>100_000_000:
        raise CargoError('RM-10 crate filename/source mismatch')
    try:
        with tarfile.open(path,'r:gz') as archive:
            items=archive.getmembers();seen=set();manifest=False;size=0;prefix=f'{name}-{version}/'
            if not (1<=len(items)<=5000):raise CargoError('RM-10 crate member count invalid')
            for item in items:
                filename=item.name
                if filename in seen or not filename.startswith(prefix) or '\\' in filename or any(p in ('','.','..') for p in filename[len(prefix):].rstrip('/').split('/')):
                    raise CargoError('RM-10 crate has duplicate, traversal or foreign-root member')
                seen.add(filename)
                if not (item.isfile() or item.isdir()) or item.issym() or item.islnk() or item.isdev():
                    raise CargoError('RM-10 unsafe crate archive file type')
                size+=item.size
                if item.size>40_000_000 or size>200_000_000:raise CargoError('RM-10 excessive crate archive size')
                if filename==prefix+'Cargo.toml' and item.isfile():
                    raw=archive.extractfile(item).read(1_048_577)
                    if len(raw)>1_048_576:raise CargoError('RM-10 oversized crate Cargo.toml')
                    doc=tomllib.loads(raw.decode())
                    p=doc.get('package',{})
                    if p.get('name')!=name or p.get('version')!=version:
                        raise CargoError('RM-10 crate embedded Cargo.toml identity mismatch')
                    manifest=True
            if not manifest:raise CargoError('RM-10 crate missing embedded package manifest')
    except (OSError,tarfile.TarError,tomllib.TOMLDecodeError,UnicodeError) as exc:
        raise CargoError('RM-10 malformed crate archive') from exc
    return {'crate':name,'version':version,'sha256':sha256(path.read_bytes()).hexdigest(),
            'bytes':path.stat().st_size,'structure_verified':True,'signer_authenticated':False,'release_qualified':False}


def verify_cargo_json(path:Path)->dict:
    """Optional Cargo compiler/event JSON output, never equated to passing tests."""
    if path.is_symlink() or not path.is_file() or path.stat().st_size>10_000_000:
        raise CargoError('RM-10 missing or unbounded Cargo JSON events')
    rows=path.read_bytes().splitlines()
    if not rows or len(rows)>20000:raise CargoError('RM-10 empty or excessive Cargo JSON stream')
    allowed={'compiler-artifact','compiler-message','build-script-executed','build-finished','test','suite'}
    events=[];finish=False
    for raw in rows:
        try:event=json.loads(raw)
        except (ValueError,UnicodeError) as exc:raise CargoError('RM-10 malformed Cargo JSON line') from exc
        if not isinstance(event,dict) or event.get('reason') not in allowed:
            raise CargoError('RM-10 unknown Cargo JSON event')
        if event.get('reason')=='build-finished':
            if event.get('success') is not True:raise CargoError('RM-10 failed Cargo JSON build')
            finish=True
        if event.get('reason') in ('test','suite') and event.get('event')=='failed':
            raise CargoError('RM-10 failed Rust test JSON event')
        events.append(event['reason'])
    if not finish:raise CargoError('RM-10 Cargo JSON compiler stream lacks successful end marker')
    return {'events':len(events),'sha256':sha256(path.read_bytes()).hexdigest(),
            'is_test_pass':False,'release_qualified':False}


def feature_cell_argv(argv:list[str],feature_cell:str)->list[str]:
    """Derive one exact reviewed Cargo feature cell without a shell or arbitrary flags."""
    if feature_cell=='none':names=[]
    else:
        names=feature_cell.split(',')
        if not names or len(set(names))!=len(names) or any(not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]*/[A-Za-z][A-Za-z0-9_-]*',n) for n in names):
            raise CargoError('RM-10 invalid reviewed feature cell')
    if argv[:2] not in (['cargo','test'],['cargo','build'],['cargo','clippy']):
        return list(argv)
    base=list(argv)
    if '--features' in base:
        i=base.index('--features');del base[i:i+2]
    if names:base[3:3]=['--features',','.join(names)]
    return base


def verify_native_cargo_output(output:str,*,operation:str,source_sha256:dict,version:str,run_id:str,attempt:str,expected_features:list[str],target:str,argv:list[str]|None=None,rust_env:dict|None=None)->dict:
    """Normalize only genuine bounded Cargo compiler JSON plus libtest summaries.

    Compiler events never alone count as tests passed; cargo test must emit at
    least one actual libtest summary, with nonzero executed tests and no failures.
    """
    if operation not in ('test','build','clippy') or not isinstance(output,str) or len(output)>4_000_000:
        raise CargoError('RM-10 invalid/unbounded native Cargo event output')
    events=[];tests=[];artifacts=[];finished=False
    for line in output.splitlines():
        if line.startswith('{'):
            try:e=json.loads(line)
            except ValueError as exc:raise CargoError('RM-10 malformed Cargo JSON event') from exc
            if not isinstance(e,dict) or e.get('reason') not in ('compiler-artifact','compiler-message','build-script-executed','build-finished'):
                raise CargoError('RM-10 unexpected Cargo event')
            if finished:raise CargoError('RM-10 event after Cargo build-finished')
            if e['reason']=='compiler-artifact':
                filenames=e.get('filenames')
                if not isinstance(filenames,list) or len(filenames)>48 or any(not isinstance(f,str) or len(f)>400 for f in filenames):
                    raise CargoError('RM-10 invalid Cargo artifact filename inventory')
                artifacts.extend(filenames)
                if len(artifacts)>1000:raise CargoError('RM-10 excessive Cargo artifacts')
                reported=e.get('features',[])
                if not isinstance(reported,list):raise CargoError('RM-10 invalid compiler feature inventory')
            if e['reason']=='compiler-message' and e.get('message',{}).get('level')=='error':
                raise CargoError('RM-10 compiler emitted error')
            if e['reason']=='build-finished':
                if e.get('success') is not True:raise CargoError('RM-10 unsuccessful Cargo compilation')
                finished=True
            events.append(e['reason'])
        elif line.strip().startswith('test result:'):
            m=re.fullmatch(r'test result: (ok|FAILED)\. (\d+) passed; (\d+) failed; (\d+) ignored; (\d+) measured; (\d+) filtered out;.*',line.strip())
            if not m:raise CargoError('RM-10 unrecognized Rust libtest summary')
            if m.group(1)!='ok' or int(m.group(3))!=0:raise CargoError('RM-10 Rust test failure')
            tests.append({'passed':int(m.group(2)),'failed':0,'ignored':int(m.group(4)),'filtered_out':int(m.group(6))})
    if not finished:raise CargoError('RM-10 native compiler stream missing successful completion')
    if operation=='test' and (not tests or sum(t['passed'] for t in tests)==0):
        raise CargoError('RM-10 native test stream has no passing tests')
    return {'schema_version':1,'compiler_events':len(events),'artifacts':sorted(set(artifacts)),
            'test_suites':len(tests),'test_passed':sum(t['passed'] for t in tests),
            'command':operation,'toolchain':version,'target':target,'features':expected_features,
            'source_manifest_sha256':dict(sorted(source_sha256.items())),
            'reviewed_native_argv':list(argv or []),
            'effective_rust_flags':(rust_env or {}).get('RUSTFLAGS',''),
            'effective_encoded_rust_flags':(rust_env or {}).get('CARGO_ENCODED_RUSTFLAGS',''),
            'run_id':run_id,'attempt':attempt,'sha256':sha256(output.encode()).hexdigest(),
            'authenticated':False,'release_qualified':False}
