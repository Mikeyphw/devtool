"""RM-11: source-reviewed JavaScript/TypeScript monorepo and artifact contracts.

Static source inspection never evaluates package scripts, and native execution
never treats a green process as a validated test or distribution receipt.
"""
from __future__ import annotations
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
from typing import Any
from .evidence_kernel import normalize_test_report, EvidenceError

class NodeError(ValueError):pass

MANAGERS={'npm':'package-lock.json','pnpm':'pnpm-lock.yaml','yarn':'yarn.lock','bun':'bun.lock'}
PIN=re.compile(r'^(npm|pnpm|yarn|bun)@([1-9]\d?\.\d+\.\d+)$')
IDENTITY=re.compile(r'^(?:@[-\w.]+/)?[-\w.]{1,80}$')
SAFE_PART=re.compile(r'^[\w.-]{1,80}$')
MAX_BYTES=2*1024*1024
MAX_FILES=200
MAX_PACKAGES=32
# No shell, pipes, install hooks, arbitrary Node loaders or evaluation.
SCRIPTS={
 'typecheck':(('tsc','--noEmit'),('tsc','-p','tsconfig.json','--noEmit')),
 'lint':(('eslint','.','--max-warnings=0'),('biome','check','.')),
 'test':(('vitest','run','--reporter=junit','--outputFile=dist/test-results.xml'),
         ('jest','--ci','--json','--outputFile=dist/test-results.json'),
         ('node','--test','--test-reporter=junit','--test-reporter-destination=dist/test-results.xml')),
 'build':(('vite','build','--outDir','dist'),('tsc','-p','tsconfig.json'),
          ('webpack','--mode','production','--output-path','dist')),
 'pack':(('npm','pack','--pack-destination','dist','--ignore-scripts'),),
}
OPERATION=dict((k,k) for k in SCRIPTS)


def _git_names(root:Path)->set[str]:
    p=subprocess.run(['git','-C',str(root),'ls-files','-z'],capture_output=True,check=True,timeout=15)
    return set(x.decode('utf-8') for x in p.stdout.split(b'\0') if x)


def _safe_path(path:str)->bool:
    return (isinstance(path,str) and 0<len(path)<=180 and not path.startswith('/')
            and '\\' not in path and all(SAFE_PART.fullmatch(p) and p not in ('.','..') for p in path.split('/')))


def _bytes(root:Path,name:str,tracked:set[str])->bytes:
    if name not in tracked or not _safe_path(name):raise NodeError('RM-11 untracked/unsafe file: '+name)
    p=root
    for part in name.split('/'):
        p=p/part
        if p.is_symlink():raise NodeError('RM-11 symlink source refused: '+name)
    if not p.is_file() or p.stat().st_size>MAX_BYTES:raise NodeError('RM-11 missing or oversized source: '+name)
    data=p.read_bytes()
    if b'\x00' in data:raise NodeError('RM-11 binary source refused: '+name)
    return data


def _json(root:Path,name:str,tracked:set[str])->dict:
    try:row=json.loads(_bytes(root,name,tracked))
    except (json.JSONDecodeError,UnicodeError) as exc:raise NodeError('RM-11 malformed JSON: '+name) from exc
    if not isinstance(row,dict):raise NodeError('RM-11 JSON root must be object: '+name)
    return row


def _workspace_pattern(pattern:str,tracked:set[str])->list[str]:
    if not isinstance(pattern,str) or not pattern or len(pattern)>120:
        raise NodeError('RM-11 invalid workspace pattern')
    # Explicit dirs or shallow wildcard only; never recursive or external.
    if '*' in pattern and (pattern.count('*')!=1 or not pattern.endswith('/*')):
        raise NodeError('RM-11 unreviewed recursive/workspace glob')
    if not _safe_path(pattern.replace('*','dummy')):
        raise NodeError('RM-11 workspace path escape')
    prefix=pattern[:-1] if pattern.endswith('*') else pattern+'/'
    return sorted({name.rsplit('/',1)[0] for name in tracked
                   if name.startswith(prefix) and name.endswith('/package.json')
                   and '/' not in name[len(prefix):-len('/package.json')]})


def _script(s:str,kind:str)->None:
    shard=re.fullmatch(r'vitest run --shard=([1-8])/([1-8]) --reporter=junit --outputFile=dist/test-results/shard-([0-9]{2}).xml',s or '') if kind=='test' and isinstance(s,str) else None
    if shard:
        i,n,num=map(int,shard.groups())
        if i>n or i!=num:raise NodeError('RM-11 invalid sharded JS output identity')
        return
    if not isinstance(s,str) or len(s)>180 or not any(s==' '.join(argv) for argv in SCRIPTS[kind]):
        raise NodeError('RM-11 arbitrary/unreviewed package-script injection refused: '+kind)


def node_workspace(root:Path, *, manager_selection:str='')->dict[str,Any]:
    root=root.resolve();tracked=_git_names(root)
    if len(tracked)>25000 or 'package.json' not in tracked:raise NodeError('RM-11 requires tracked root package.json')
    top=_json(root,'package.json',tracked)
    value=top.get('packageManager','')
    m=PIN.fullmatch(value) if isinstance(value,str) else None
    if not m:raise NodeError('RM-11 packageManager requires exact name@major.minor.patch')
    manager,manager_version=m.groups()
    if manager_selection and manager!=manager_selection:
        raise NodeError('RM-11 selected manager differs from committed packageManager')
    locks=[key for key in (*MANAGERS.values(),'bun.lockb') if key in tracked]
    expected=MANAGERS[manager]
    if expected not in locks or (not manager_selection and any(name!=expected for name in locks)):
        raise NodeError('RM-11 ambiguous mixed managers, binary bun lock or missing selected lockfile')
    lock=_bytes(root,expected,tracked)
    if not lock:raise NodeError('RM-11 empty selected lockfile')
    if manager=='npm':
        row=_json(root,expected,tracked)
        if row.get('lockfileVersion') not in (2,3) or not isinstance(row.get('packages'),dict) or '' not in row['packages']:
            raise NodeError('RM-11 unsupported npm package lock schema')
    elif manager=='pnpm':
        # Require the structural importers/lock sections; source lock remains byte-pinned.
        if not re.search(rb'(?m)^lockfileVersion:\s*[\'\"]?[69](?:\.0)?',lock) or not re.search(rb'(?m)^importers:\s*$',lock):
            raise NodeError('RM-11 pnpm lock lacks supported version/importer inventory')
    elif manager=='yarn':
        if not re.search(rb'(?m)^__metadata:\s*$',lock) or b'checksum:' not in lock:
            raise NodeError('RM-11 Yarn Berry lock lacks metadata/integrities')
        if '.yarnrc.yml' in tracked:
            yrc=_bytes(root,'.yarnrc.yml',tracked)
            if re.search(rb'(?m)^(?:yarnPath|plugins|enableScripts|unsafeHttpWhitelist):',yrc):
                raise NodeError('RM-11 executable Yarn rc configuration refused')
    elif manager=='bun':
        row=_json(root,expected,tracked)
        if not isinstance(row.get('lockfileVersion'),int) or not isinstance(row.get('packages'),dict):
            raise NodeError('RM-11 bun text lock requires package inventory')
    if any(name in tracked or any(p.startswith(name+'/') for p in tracked) for name in
           ('.npmrc','.pnpmfile.cjs','.pnp.cjs','.yarn/plugins','.yarn/releases','bunfig.toml','node_modules')):
        raise NodeError('RM-11 unreviewed package-manager configuration or vendored execution source')
    workspaces=top.get('workspaces',[])
    if isinstance(workspaces,dict):workspaces=workspaces.get('packages',[])
    if not isinstance(workspaces,list) or len(workspaces)>32:raise NodeError('RM-11 workspaces must be bounded list')
    if manager=='pnpm':
        if 'pnpm-workspace.yaml' not in tracked:raise NodeError('RM-11 pnpm monorepo requires pnpm-workspace.yaml')
        text=_bytes(root,'pnpm-workspace.yaml',tracked).decode('utf-8')
        patterns=re.findall(r'(?m)^\s*-\s*[\'\"]?([\w./*-]+)[\'\"]?\s*$',text)
        if not patterns or not text.lstrip().startswith('packages:'):
            raise NodeError('RM-11 unsupported pnpm workspace declarations')
        if workspaces and sorted(workspaces)!=sorted(patterns):
            raise NodeError('RM-11 conflicting pnpm/root workspace declarations')
        workspaces=patterns
    paths=set()
    for item in workspaces:
        paths.update(_workspace_pattern(item,tracked))
    if len(paths)>MAX_PACKAGES:raise NodeError('RM-11 workspace exceeds member limit')
    pkgs={}; names={};source_files=['package.json',expected]
    if manager=='pnpm':source_files.append('pnpm-workspace.yaml')
    if '.yarnrc.yml' in tracked and manager=='yarn':source_files.append('.yarnrc.yml')
    for path in ['',*sorted(paths)]:
        name=(path+'/' if path else '')+'package.json'
        manifest=top if not path else _json(root,name,tracked)
        pkgname=manifest.get('name')
        if not isinstance(pkgname,str) or not IDENTITY.fullmatch(pkgname) or pkgname in names:
            raise NodeError('RM-11 duplicate/unsafe package name')
        names[pkgname]=path
        for field in ('dependencies','devDependencies','optionalDependencies','peerDependencies'):
            if not isinstance(manifest.get(field,{}),dict) or len(manifest.get(field,{}))>256:
                raise NodeError('RM-11 invalid dependency declaration')
        scripts=manifest.get('scripts',{})
        if not isinstance(scripts,dict) or len(scripts)>20:
            raise NodeError('RM-11 invalid package scripts')
        # Dependency install is --ignore-scripts, but fail if install lifecycle hooks exist.
        if set(scripts)&{'preinstall','install','postinstall','prepare','prepublish','prepack','postpack',*(f'{prefix}{kind}' for prefix in ('pre','post') for kind in SCRIPTS)}:
            raise NodeError('RM-11 executable install/pack hook refused')
        for kind in set(scripts)&set(SCRIPTS):_script(scripts[kind],kind)
        if any(not isinstance(k,str) or len(k)>80 for k in scripts):
            raise NodeError('RM-11 unsafe script name')
        pkgs[path]={'name':pkgname,'version':manifest.get('version',''),'scripts':{k:scripts[k] for k in SCRIPTS if k in scripts},
                    'dependencies':{field:manifest.get(field,{}) for field in ('dependencies','devDependencies','optionalDependencies','peerDependencies')},
                    'path':path}
        if path:source_files.append(name)
    graph={}
    for path,pkg in pkgs.items():
        refs=[]
        for field,deps in pkg['dependencies'].items():
            for name,value in deps.items():
                if not isinstance(name,str) or not IDENTITY.fullmatch(name) or not isinstance(value,str) or len(value)>128:
                    raise NodeError('RM-11 invalid dependency value')
                if name in names:
                    target=names[name]
                    if target==path:raise NodeError('RM-11 self workspace dependency')
                    if not (value.startswith('workspace:') or value.startswith('file:') or (manager=='npm' and value==pkgs[target]['version'])):
                        raise NodeError('RM-11 internal dependency must be pinned as workspace: or file:')
                    if value.startswith('file:'):
                        rel=PurePosixPath(path or '.')/value[5:]
                        parts=[]
                        for section in rel.parts:
                            if section=='..':
                                if not parts:raise NodeError('RM-11 file: path escapes workspace')
                                parts.pop()
                            elif section!='.':parts.append(section)
                        if '/'.join(parts)!=target:raise NodeError('RM-11 file: path identity mismatch')
                    refs.append(target)
                elif value.startswith(('file:','link:','workspace:')):
                    raise NodeError('RM-11 undeclared local workspace dependency')
        graph[path]=sorted(set(refs))
    ordered=[]; visiting=set()
    def visit(node):
        if node in visiting:raise NodeError('RM-11 workspace dependency cycle')
        if node in ordered:return
        visiting.add(node)
        for dep in graph[node]:visit(dep)
        visiting.remove(node);ordered.append(node)
    for name in sorted(graph):visit(name)
    if manager=='npm':
        lockpkgs=row['packages']
        if any(path not in lockpkgs for path in pkgs):
            raise NodeError('RM-11 npm lock lacks reviewed workspace')
    if manager=='pnpm':
        if any(not re.search(r'(?m)^\s{2,4}[\'\"]?'+re.escape(path or '.')+r'[\'\"]?:\s*$',lock.decode(),re.ASCII) for path in pkgs):
            raise NodeError('RM-11 pnpm lock importer mismatch')
    if manager=='bun':
        if any((path or '') not in row['packages'] for path in pkgs):
            raise NodeError('RM-11 bun lock workspace mismatch')
    hashes={p:hashlib.sha256(_bytes(root,p,tracked)).hexdigest() for p in sorted(source_files)}
    digest=hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()
    return {'manager':manager,'manager_version':manager_version,'lockfile':expected,'lock_sha256':hashes[expected],
            'packages':pkgs,'names':names,'edges':graph,'topological_paths':ordered,'source_sha256':hashes,
            'source_digest':digest,'source_files':sorted(source_files),'execution_performed':False}


def affected_packages(workspace:dict, changed_paths:list[str])->list[str]:
    if not isinstance(changed_paths,list) or len(changed_paths)>500 or any(not _safe_path(p) for p in changed_paths):
        raise NodeError('RM-11 invalid changed path inventory')
    selected=set()
    if any(p in workspace['source_files'] and '/' not in p for p in changed_paths):
        selected=set(workspace['packages'])
    else:
        for path in workspace['packages']:
            if path and any(p.startswith(path+'/') for p in changed_paths):selected.add(path)
    changed=True
    while changed:
        prev=len(selected)
        selected.update(k for k,deps in workspace['edges'].items() if any(d in selected for d in deps))
        changed=len(selected)!=prev
    return [p for p in workspace['topological_paths'] if p in selected]


def inspect_node_job(root:Path,argv:list[str],metadata:dict)->dict:
    if not isinstance(argv,list) or len(argv)>7 or not argv or not isinstance(metadata,dict):
        raise NodeError('RM-11 bounded direct manager argv required')
    manager=argv[0]
    if manager not in MANAGERS:raise NodeError('RM-11 direct npm/pnpm/yarn/bun command required')
    ws=node_workspace(root,manager_selection=manager)
    # One reviewed script by root/workspace name, never raw script text from configuration.
    kind='';path=''
    if len(argv)==3 and argv[1]=='run':kind=argv[2]
    elif manager=='npm' and len(argv)==5 and argv[1:3]==['run',argv[2]] and argv[3]=='--workspace':
        kind=argv[2];path=ws['names'].get(argv[4],None)
    elif manager=='pnpm' and len(argv)==5 and argv[1]=='--filter' and argv[3]=='run':
        kind=argv[4];path=ws['names'].get(argv[2],None)
    elif manager=='yarn' and len(argv)==5 and argv[1]=='workspace' and argv[3]=='run':
        kind=argv[4];path=ws['names'].get(argv[2],None)
    elif manager=='bun' and len(argv)==5 and argv[1:3]==['run','--cwd']:
        kind=argv[4];path=argv[3] if argv[3] in ws['packages'] else None
    else:raise NodeError('RM-11 unreviewed package-manager script argv')
    if kind not in SCRIPTS or path is None or kind not in ws['packages'][path]['scripts']:
        raise NodeError('RM-11 script/target is not declared in tracked workspace')
    node=metadata.get('ci_node_version','22')
    if node not in ('20','22','24'):raise NodeError('RM-11 Node version must be 20/22/24')
    raw=metadata.get('ci_node_versions')
    versions=[node] if raw is None else raw.split(',') if isinstance(raw,str) else []
    if not 1<=len(versions)<=3 or len(set(versions))!=len(versions) or node not in versions or any(v not in ('20','22','24') for v in versions):
        raise NodeError('RM-11 requires 1..3 pinned Node major versions including primary')
    shard=metadata.get('ci_js_shard','')
    script=ws['packages'][path]['scripts'][kind]
    if shard:
        if not isinstance(shard,str) or not re.fullmatch(r'[1-8]/[1-8]',shard):
            raise NodeError('RM-11 unsafe JS shard metadata')
        if kind!='test' or '--shard='+shard+' ' not in script:
            raise NodeError('RM-11 native shard metadata must match reviewed script')
    elif '--shard=' in script:
        raise NodeError('RM-11 sharded test requires explicit owned shard metadata')
    # No scripts may get implicit build/test outputs from arbitrary locations.
    return {'provider':'node','toolchain':'node','version':node,'matrix_versions':versions,
            'version_source':'ci_node_version','workspace':ws,'manager':manager,
            'manager_version':ws['manager_version'],'lockfiles':ws['source_files'],
            'kind':kind,'package_path':path,'package_name':ws['packages'][path]['name'],
            'script':script,'shard':shard,
            'report_adapter':'js-junit' if kind=='test' else 'none',
            'cache_enabled':False,'output_role':'local-bytes-only','release_qualified':False}


def verify_js_report(root:Path,relative:str,kind:str)->dict:
    if kind!='test':raise NodeError('RM-11 JS report only applies to test jobs')
    if relative not in ('dist/test-results.xml','dist/test-results.json') and not re.fullmatch(r'dist/test-results/shard-0[1-8]\.xml',relative):
        raise NodeError('RM-11 unreviewed test report path')
    p=root/relative
    if p.is_symlink() or not p.is_file() or p.stat().st_size>MAX_BYTES:
        raise NodeError('RM-11 unsafe/missing JS test report')
    if p.suffix=='.xml':
        try:result=normalize_test_report(p,'js-junit')
        except EvidenceError as exc:raise NodeError(str(exc)) from None
        if result['failed'] or not result['passed']:raise NodeError('RM-11 failed/zero JS test count')
        return result
    try: obj=json.loads(p.read_text())
    except (ValueError,UnicodeError) as exc:raise NodeError('RM-11 malformed Jest JSON') from exc
    if (not isinstance(obj,dict) or any(type(obj.get(k))!=int for k in ('numTotalTests','numPassedTests','numFailedTests','numPendingTests'))
        or obj['numTotalTests']<1 or obj['numPassedTests']<1 or obj['numFailedTests']!=0
        or sum(obj[k] for k in ('numPassedTests','numFailedTests','numPendingTests'))!=obj['numTotalTests']
        or obj.get('success') is not True):raise NodeError('RM-11 failed or inconsistent Jest results')
    return {'kind':'js-jest','total':obj['numTotalTests'],'passed':obj['numPassedTests'],
            'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'release_qualified':False}


def verify_js_artifacts(root:Path,files:list[str],kind:str,workspace:dict,package_path:str)->list[dict]:
    if kind not in ('build','pack'):raise NodeError('RM-11 output verifier only applies to build/pack')
    if not files or len(files)>MAX_FILES or len(set(files))!=len(files):
        raise NodeError('RM-11 missing/duplicate output files')
    if kind=='build' and 'dist/manifest.json' not in files:
        raise NodeError('RM-11 built web application must own dist/manifest.json')
    receipts=[];expected_package=workspace['packages'][package_path]
    for relative in files:
        if not relative.startswith('dist/') or not _safe_path(relative):raise NodeError('RM-11 output outside dist root')
        p=root
        for part in relative.split('/'):
            p=p/part
            if p.is_symlink():raise NodeError('RM-11 symlink output ancestor refused')
        if not p.is_file() or p.stat().st_size>MAX_BYTES*8:
            raise NodeError('RM-11 output missing, symlinked or too large')
        data=p.read_bytes();digest=hashlib.sha256(data).hexdigest()
        if kind=='pack':
            if not relative.endswith('.tgz'):raise NodeError('RM-11 package output must be tgz')
            with tarfile.open(fileobj=io.BytesIO(data),mode='r:gz') as tf:
                members=tf.getmembers()
                if len(members)>MAX_FILES or any(not m.isfile() or m.issym() or m.islnk() or
                     not m.name.startswith('package/') or '..' in PurePosixPath(m.name).parts for m in members):
                    raise NodeError('RM-11 unsafe package tar members')
                pkg=next((m for m in members if m.name=='package/package.json'),None)
                if pkg is None:raise NodeError('RM-11 packed package manifest missing')
                obj=json.loads(tf.extractfile(pkg).read())
                if obj.get('name')!=expected_package['name'] or obj.get('version')!=expected_package['version']:
                    raise NodeError('RM-11 package tar identity mismatch')
        receipts.append({'path':relative,'sha256':digest,'size':len(data),'release_qualified':False})
    if kind=='build':
        try:manifest=json.loads((root/'dist/manifest.json').read_text())
        except (ValueError,UnicodeError) as exc:raise NodeError('RM-11 malformed web output manifest') from exc
        if not isinstance(manifest,dict) or set(manifest)!={'files'} or not isinstance(manifest['files'],dict):
            raise NodeError('RM-11 web manifest must declare exact owned files')
        named={'dist/'+k:v for k,v in manifest['files'].items() if isinstance(k,str)}
        if set(named)!=set(files)-{'dist/manifest.json'}:
            raise NodeError('RM-11 web bundle differs from declared manifest and artifact root')
        for relative,digest in named.items():
            if not _safe_path(relative) or not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest):
                raise NodeError('RM-11 invalid web manifest digest/path')
            if hashlib.sha256((root/relative).read_bytes()).hexdigest()!=digest:
                raise NodeError('RM-11 web manifest/output bytes mismatch')
    return receipts
