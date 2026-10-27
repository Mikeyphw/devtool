"""RM-09: read-only, source-bound .NET solution and artifact contracts.

No MSBuild evaluation, restore, network access, credentials or signing trust is
inferred from manifest parsing. Native target-local Devtool jobs remain owners.
"""
from __future__ import annotations

from collections import defaultdict
from hashlib import sha256, sha512
import json
import base64
from pathlib import Path, PurePosixPath
import re
import subprocess
from xml.etree import ElementTree as ET
import zipfile

class DotNetError(ValueError):
    pass

MAX_SOURCE = 2_000_000
_NAME = re.compile(r'[a-zA-Z0-9_.-]{1,100}\Z')
_VERSION = re.compile(r'(?:8|9|10)\.0\.[0-9]{1,4}\Z')
_TFM = re.compile(r'net(?:8|9|10)\.0(?:-windows(?:[0-9.]+)?)?\Z')
_RIDS = {'linux-x64','linux-arm64','win-x64','win-arm64','osx-x64','osx-arm64'}
_SOLUTION = re.compile(r'^Project\([^\n]+\)\s*=\s*"[^"]+",\s*"([^"]+\.csproj)"',re.M|re.I)


def _path(root: Path, rel: str, *, existing=True) -> Path:
    if (not isinstance(rel,str) or len(rel)>220 or not rel or rel.startswith('/')
        or '\\' in rel or not re.fullmatch(r'[a-zA-Z0-9_.+/-]+',rel)
        or any(part in ('','.','..') for part in rel.split('/'))):
        raise DotNetError('RM-09 unsafe project/artifact path')
    target=root
    for piece in rel.split('/'):
        target=target/piece
        if target.is_symlink(): raise DotNetError('RM-09 symlinked source/artifact path')
    if existing and (not target.is_file() or target.stat().st_size>MAX_SOURCE):
        raise DotNetError('RM-09 missing or unbounded source: '+rel)
    return target


def _source(root: Path, rel: str) -> bytes:
    path=_path(root,rel)
    check=subprocess.run(['git','-C',str(root),'ls-files','--error-unmatch','--',rel],capture_output=True,timeout=10)
    if check.returncode: raise DotNetError('RM-09 requires tracked source: '+rel)
    raw=path.read_bytes()
    if b'\x00' in raw or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise DotNetError('RM-09 unsafe XML or binary source: '+rel)
    return raw


def _xml(raw: bytes, name: str):
    try:return ET.fromstring(raw)
    except ET.ParseError as exc:raise DotNetError('RM-09 malformed XML in '+name) from exc


def _tracked(root: Path):
    p=subprocess.run(['git','-C',str(root),'ls-files','-z'],capture_output=True,timeout=15,check=True)
    paths=[]
    for raw in p.stdout.split(b'\x00'):
        if not raw:continue
        try:name=raw.decode('utf8')
        except UnicodeDecodeError:continue
        if name.endswith(('.csproj','.sln','.slnx','global.json','packages.lock.json')) or name.lower().endswith('nuget.config'):
            _source(root,name)
            paths.append(name)
    if len(paths)>128:raise DotNetError('RM-09 project inventory exceeds cap')
    return sorted(paths)


def _related(parent: str, path: str) -> str:
    # Reject MSBuild property interpolation, absolute/windows paths and escapes.
    if not path or any(x in path for x in ('$','%','\\',':','*','?')):
        raise DotNetError('RM-09 unreviewed MSBuild path expression')
    base=PurePosixPath(parent).parent
    parts=[]
    for part in [*base.parts,*PurePosixPath(path).parts]:
        if part=='..':
            if not parts:raise DotNetError('RM-09 project reference escapes repository')
            parts.pop()
        elif part not in ('','.'):parts.append(part)
    return '/'.join(parts)


def solution_graph(root: Path, *, selected: str = '') -> dict:
    """Inspect tracked static project references and .sln/.slnx membership."""
    root=root.resolve();all_names=_tracked(root)
    projects=[name for name in all_names if name.endswith('.csproj')]
    solutions=[name for name in all_names if name.endswith(('.sln','.slnx'))]
    if not projects or len(projects)>48 or len(solutions)>16:
        raise DotNetError('RM-09 requires bounded tracked .NET projects/solutions')
    props={};edges={};sources={}
    for name in projects:
        xml=_xml(_source(root,name),name)
        refs=[];tfms=[];rids=[];flags=[];pack_identity={}
        for node in xml.iter():
            tag=node.tag.rsplit('}',1)[-1]
            if tag=='ProjectReference':
                ref=_related(name,node.attrib.get('Include',''))
                if not ref.endswith('.csproj') or ref not in projects or ref==name:
                    raise DotNetError('RM-09 missing/invalid tracked project reference: '+name)
                refs.append(ref)
            if tag in ('TargetFramework','TargetFrameworks','RuntimeIdentifier','RuntimeIdentifiers',
                       'UseWPF','UseWindowsForms','PackageId','Version'):
                val=(node.text or '').strip()
                if '$' in val:raise DotNetError('RM-09 conditional MSBuild property evaluation unsupported')
                if tag in ('PackageId','Version'):
                    if val and not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',val):
                        raise DotNetError('RM-09 unsafe NuGet identity in project')
                    pack_identity[tag]=val
                elif tag.startswith('TargetFramework'):tfms+=val.split(';')
                elif tag.startswith('RuntimeIdentifier'):rids+=val.split(';')
                else: flags.append((tag,val.lower()))
        if not tfms or any(not _TFM.fullmatch(x) for x in tfms):
            raise DotNetError('RM-09 project requires reviewed net8/net9/net10 target framework')
        if rids and any(r not in _RIDS for r in rids):raise DotNetError('RM-09 unknown runtime identifier')
        if len(set(refs))!=len(refs):raise DotNetError('RM-09 duplicate project reference')
        windows=any('-windows' in x for x in tfms) or any(k in ('UseWPF','UseWindowsForms') and v=='true' for k,v in flags)
        if windows and any(not x.endswith('windows') and '-windows' not in x for x in tfms):
            raise DotNetError('RM-09 mixed Windows-only/portable TFMs need separate native jobs')
        props[name]={'tfms':sorted(set(tfms)), 'rids':sorted(set(filter(None,rids))), 'windows_only':windows,'package_id':pack_identity.get('PackageId',Path(name).stem),'package_version':pack_identity.get('Version','')}
        edges[name]=sorted(refs)
        sources[name]=sha256(_source(root,name)).hexdigest()
    for name in solutions:
        raw=_source(root,name)
        if name.endswith('.sln'):
            try:refs=_SOLUTION.findall(raw.decode('utf8'))
            except UnicodeDecodeError:raise DotNetError('RM-09 malformed solution encoding') from None
        else:
            xml=_xml(raw,name)
            refs=[e.attrib.get('Path','') for e in xml.iter() if e.tag.rsplit('}',1)[-1]=='Project']
        if not refs:raise DotNetError('RM-09 empty or unrecognized solution: '+name)
        included=[_related(name,ref.replace('\\','/')) for ref in refs]
        if len(set(included))!=len(included) or any(p not in projects for p in included):
            raise DotNetError('RM-09 solution contains missing/duplicate/untracked projects')
        for p in included:
            if any(ref not in included for ref in edges[p]):
                raise DotNetError('RM-09 solution excludes local project dependency')
        sources[name]=sha256(raw).hexdigest()
    seen=set();active=set();order=[]
    def visit(p):
        if p in active:raise DotNetError('RM-09 project dependency cycle')
        if p in seen:return
        active.add(p)
        for dep in edges[p]:visit(dep)
        active.remove(p);seen.add(p);order.append(p)
    for p in projects:visit(p)
    if selected and selected not in projects+solutions:
        raise DotNetError('RM-09 job must select tracked solution/project')
    global_json='global.json'
    if global_json not in all_names:raise DotNetError('RM-09 requires tracked global.json SDK pin')
    try:spec=json.loads(_source(root,global_json))
    except ValueError:raise DotNetError('RM-09 malformed global.json') from None
    sdk=spec.get('sdk',{})
    version=sdk.get('version') if isinstance(sdk,dict) else None
    if not isinstance(version,str) or not _VERSION.fullmatch(version) or sdk.get('allowPrerelease',False) is True:
        raise DotNetError('RM-09 requires fixed reviewed .NET SDK version without prerelease')
    sources[global_json]=sha256(_source(root,global_json)).hexdigest()
    locks=[]
    for name in all_names:
        if name.endswith('packages.lock.json'):
            try:lock=json.loads(_source(root,name))
            except ValueError:raise DotNetError('RM-09 malformed NuGet lock') from None
            if not isinstance(lock,dict) or not isinstance(lock.get('dependencies'),dict):
                raise DotNetError('RM-09 invalid NuGet lock structure')
            locks.append(name);sources[name]=sha256(_source(root,name)).hexdigest()
        elif name.lower().endswith('nuget.config'):
            raw=_source(root,name);config=_xml(raw,name)
            # Disallow authenticated/custom/private package sources and cleartext feed credentials.
            if any(x.tag.rsplit('}',1)[-1] in ('packageSourceCredentials','apikeys','packageSources') for x in config.iter() if x is not config):
                raise DotNetError('RM-09 NuGet.Config source/credentials require separate qualification')
            sources[name]=sha256(raw).hexdigest()
    if not locks:raise DotNetError('RM-09 requires tracked NuGet lockfiles for restore')
    # For all projects, an own lockfile is mandatory to ensure restore does not
    # silently pick an unreviewed transitive version.
    for name in projects:
        folder=name.rsplit('/',1)[0] if '/' in name else ''
        own=(folder+'/' if folder else '')+'packages.lock.json'
        if own not in locks:raise DotNetError('RM-09 missing project NuGet lock: '+name)
    membership={}
    for name in solutions:
        raw=_source(root,name)
        if name.endswith('.sln'):
            refs=_SOLUTION.findall(raw.decode('utf8'))
        else:refs=[e.attrib.get('Path','') for e in _xml(raw,name).iter() if e.tag.rsplit('}',1)[-1]=='Project']
        membership[name]=sorted(_related(name,x.replace('\\','/')) for x in refs)
    return {'kind':'devtool.dotnet.solution-graph','sdk_version':version,'sources':sources,
            'projects':props,'solutions':membership,'edges':edges,'topological_projects':order,
            'selected':selected,'windows_only':(any(props[p]['windows_only'] for p in membership.get(selected,[selected])) if selected else False),
            'source_bound':True,'execution_performed':False,'release_qualified':False}


def inspect_dotnet_job(root: Path, argv: list[str], metadata: dict, *, host_os: str = 'linux') -> dict:
    if len(argv)<3 or argv[0]!='dotnet' or argv[1] not in ('restore','build','test','pack','publish'):
        raise DotNetError('RM-09 requires direct dotnet restore/build/test/pack/publish')
    if any(not isinstance(x,str) or len(x)>160 or '\\' in x or '$' in x or (';' in x and x!='trx;LogFileName=results.trx') for x in argv):
        raise DotNetError('RM-09 refuses shell/variable/interpolation arguments')
    command,selected=argv[1:3]
    i=3;opts={}
    while i<len(argv):
        arg=argv[i]
        if arg in ('--locked-mode','--no-restore','--no-build'):
            if arg in opts:raise DotNetError('RM-09 duplicate .NET switch')
            opts[arg]=True;i+=1;continue
        if arg in ('--configuration','--framework','--runtime','--output','--logger','--results-directory','--collect'):
            if arg in opts or i+1>=len(argv):raise DotNetError('RM-09 missing or duplicate argument')
            opts[arg]=argv[i+1];i+=2;continue
        raise DotNetError('RM-09 refuses unreviewed .NET argument: '+arg[:80])
    if opts.get('--configuration','Release') not in ('Release','Debug'):
        raise DotNetError('RM-09 invalid .NET configuration')
    if '--framework' in opts and not _TFM.fullmatch(opts['--framework']):
        raise DotNetError('RM-09 unsupported .NET framework')
    if '--runtime' in opts and opts['--runtime'] not in _RIDS:
        raise DotNetError('RM-09 unsupported .NET runtime identifier')
    if '--output' in opts and (opts['--output']!='dist' and
                               not re.fullmatch(r'dist/[a-zA-Z0-9_./-]{1,150}',opts['--output'])):
        raise DotNetError('RM-09 package output must be within dist/')
    if '--logger' in opts and opts['--logger'] not in ('trx;LogFileName=results.trx',):
        raise DotNetError('RM-09 requires fixed TRX logger name')
    coverage=metadata.get('ci_dotnet_coverage',False)
    if type(coverage) is not bool:
        raise DotNetError('RM-09 coverage opt-in must be boolean')
    if coverage and (command!='test' or opts.get('--collect')!='XPlat Code Coverage'):
        raise DotNetError('RM-09 native coverage requires --collect XPlat Code Coverage on test')
    if '--collect' in opts and (command!='test' or not coverage or opts['--collect']!='XPlat Code Coverage'):
        raise DotNetError('RM-09 unreviewed coverage collector')
    if command=='restore' and (opts.get('--locked-mode') is not True or len(opts)!=1):
        raise DotNetError('RM-09 restore must use locked-mode only')
    if command!='restore':
        if '--no-restore' not in opts or '--no-build' in opts:
            raise DotNetError('RM-09 hosted jobs require --no-restore (and forbid --no-build): locked restore is repeated inside the same native node')
    if command in ('pack','publish') and '--output' not in opts:
        raise DotNetError('RM-09 package command requires dist/ output')
    if command=='test' and (opts.get('--logger')!='trx;LogFileName=results.trx' or
        not re.fullmatch(r'dist(?:/[A-Za-z0-9_-]{1,64})?',opts.get('--results-directory',''))):
        raise DotNetError('RM-09 test requires fixed TRX logger and dist results directory')
    graph=solution_graph(root,selected=selected)
    members=graph['solutions'].get(selected,[selected])
    if graph['windows_only'] and host_os!='windows':
        raise DotNetError('RM-09 Windows-only APIs/workloads cannot run on reviewed Linux host')
    if host_os not in ('windows','linux'):
        raise DotNetError('RM-09 no reviewed .NET host bridge for '+str(host_os))
    tfms={x for p in members for x in graph['projects'][p]['tfms']}
    if '--framework' in opts and any(opts['--framework'] not in graph['projects'][p]['tfms'] for p in members):
        raise DotNetError('RM-09 framework not declared by every selected project')
    if len(tfms)>1 and '--framework' not in opts and command in ('build','test','publish'):
        raise DotNetError('RM-09 multi-TFM execution requires explicit --framework per native job')
    if graph['windows_only'] and '--framework' in opts and '-windows' not in opts['--framework']:
        # Dependent portable class-library projects are allowed only through a
        # Windows-only entry point; a bare portable TFM is not sufficient.
        raise DotNetError('RM-09 Windows entry point must select a Windows TFM')
    rids={x for p in members for x in graph['projects'][p]['rids']}
    if '--runtime' in opts and (opts['--runtime'] not in rids or
            not opts['--runtime'].startswith('win-' if host_os=='windows' else 'linux-')):
        raise DotNetError('RM-09 incompatible or undeclared host runtime identifier')
    if '--runtime' in opts and '--framework' not in opts and len(tfms)>1:
        raise DotNetError('RM-09 runtime requires explicit compatible TFM')
    package=None
    if command=='pack':
        if selected not in graph['projects']:
            raise DotNetError('RM-09 NuGet packaging requires explicit single .csproj owner')
        project=graph['projects'][selected]
        package={'package_id':project['package_id'],'version':project['package_version'],
                 'tfm':opts.get('--framework',project['tfms'][0]),'rid':opts.get('--runtime','')}
        if not package['version'] or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?',package['version']):
            raise DotNetError('RM-09 pack requires pinned project Version')
        if len(project['tfms'])!=1 and '--framework' not in opts:
            raise DotNetError('RM-09 multi-TFM pack requires explicit framework')
    shared=review_shared_restore(root,graph,selected,metadata.get('ci_dotnet_shared_solutions'),
                                 framework=opts.get('--framework',''),rid=opts.get('--runtime',''),
                                 host_os=host_os) if metadata.get('ci_dotnet_shared_solutions') is not None else None
    if shared and command=='restore':
        raise DotNetError('FC-06 shared restore requires a non-restore native job for observed consumption')
    return {'trx_path':opts.get('--results-directory','')+'/results.trx' if command=='test' else '',
            'package':package,'provider':'dotnet','toolchain':'dotnet' ,'version':graph['sdk_version'],
            'matrix_versions':[graph['sdk_version']], 'version_source':'global.json',
            'graph':graph,'command':command,'selected':selected,'lockfiles':list(graph['sources']),
            'coverage':coverage,'framework':opts.get('--framework',''),'runtime':opts.get('--runtime',''),
            'publish_output':opts.get('--output',''), 'host_os':host_os,
            'shared_restore':shared,
            'cache_enabled':False,'report_adapter':'trx','output_role':'local-bytes-only',
            'release_qualified':False}



def review_shared_restore(root: Path, graph: dict, selected: str, requested,
                          *, framework: str = '', rid: str = '', host_os: str = 'linux') -> dict:
    """Review SAME-JOB restore reuse; this is not GitHub cross-job filesystem sharing.

    Reject dynamic NuGet identities. SDK restores each solution with --locked-mode
    into one per-job store. Only the native runner can attest the resulting bytes.
    """
    if (type(requested) is not list or not 2 <= len(requested) <= 8 or
            any(not isinstance(x,str) or x not in graph['solutions'] for x in requested) or
            len(set(requested)) != len(requested) or selected not in requested):
        raise DotNetError('FC-06 requires 2–8 distinct reviewed solutions including the native selection')
    packages={}; appearances=defaultdict(set)
    for solution in requested:
        members=graph['solutions'][solution]
        if any(graph['projects'][p]['windows_only'] for p in members) and host_os!='windows':
            raise DotNetError('FC-06 Windows-only sharing requires reviewed Windows host')
        for project in members:
            prop=graph['projects'][project]
            if framework and framework not in prop['tfms']:
                raise DotNetError('FC-06 shared solution has incompatible framework')
            if rid and (rid not in prop['rids'] or not rid.startswith('win-' if host_os=='windows' else 'linux-')):
                raise DotNetError('FC-06 shared solution has incompatible runtime identifier')
            folder=project.rsplit('/',1)[0] if '/' in project else ''
            rel=(folder+'/' if folder else '')+'packages.lock.json'
            lock=json.loads(_source(root,rel))
            dependencies=lock.get('dependencies')
            if not isinstance(dependencies,dict) or not dependencies:
                raise DotNetError('FC-06 requires nonempty hash-locked NuGet dependencies')
            for tfm, entries in dependencies.items():
                if framework and tfm!=framework:continue
                if not isinstance(entries,dict):raise DotNetError('FC-06 invalid NuGet lock entries')
                for name,item in entries.items():
                    if not isinstance(item,dict):raise DotNetError('FC-06 malformed NuGet lock package')
                    # Project dependencies have no NuGet contentHash; do not claim a reusable package.
                    if item.get('type')=='Project':continue
                    version=item.get('resolved'); content=item.get('contentHash')
                    if (not isinstance(name,str) or not _NAME.fullmatch(name) or
                        not isinstance(version,str) or not re.fullmatch(r'[0-9]+(?:\.[0-9]+){1,3}(?:-[A-Za-z0-9.-]+)?',version) or
                        not isinstance(content,str)):
                        raise DotNetError('FC-06 requires pinned NuGet package identity and SHA-512 lock hash')
                    try:raw=base64.b64decode(content,validate=True)
                    except (ValueError,base64.binascii.Error):
                        raise DotNetError('FC-06 malformed NuGet content hash') from None
                    if len(raw)!=64:raise DotNetError('FC-06 NuGet lock hash must be SHA-512')
                    key=(name.lower(),version.lower())
                    if key in packages and packages[key]!=content:
                        raise DotNetError('FC-06 conflicting NuGet content hashes for one id/version')
                    packages[key]=content
                    appearances[key].add(solution)
    overlap=sorted(key for key,uses in appearances.items() if len(uses)>=2)
    if not overlap:raise DotNetError('FC-06 requires a genuinely shared locked package across solutions')
    locks={key:value for key,value in graph['sources'].items() if key.endswith('packages.lock.json')}
    manifest={'schema_version':1,'kind':'devtool.rm09-reviewed-shared-restore',
              'sdk_version':graph['sdk_version'],'solutions':list(requested),
              'selected':selected,'host_os':host_os,'framework':framework,'rid':rid,
              'source_hashes':dict(sorted(graph['sources'].items())),
              'lock_hashes':dict(sorted(locks.items())),
              'packages':[{'id':key[0],'version':key[1],'sha512':value}
                          for key,value in sorted(packages.items())],
              'shared_package_keys':[list(key) for key in overlap],
              'cross_job_reuse':False,'runtime_verified':False,'release_qualified':False}
    manifest['review_sha256']=sha256(json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return manifest


def verify_shared_restore_store(root: Path, review: dict, store: Path, *, sdk_version: str,
                                restored_solutions: list[str]) -> dict:
    """Verify actual NuGet package identity hashes AFTER locked restores; no signature claim."""
    if sdk_version!=review['sdk_version'] or restored_solutions!=review['solutions']:
        raise DotNetError('FC-06 restore execution differs from reviewed SDK/solution sequence')
    if not store.is_dir() or store.is_symlink():
        raise DotNetError('FC-06 isolated NuGet package store unavailable')
    for rel,digest in review['source_hashes'].items():
        if sha256(_source(root,rel)).hexdigest()!=digest:
            raise DotNetError('FC-06 source/lock changed during native shared restore')
    verified=[]; total_bytes=0
    if len(review['packages'])>128:
        raise DotNetError('FC-06 NuGet package evidence exceeds bounded inventory')
    for pkg in review['packages']:
        key=pkg['id'].lower();version=pkg['version'].lower()
        folder=store/key/version
        metadata=folder/(key+'.'+version+'.nupkg.sha512')
        archive=folder/(key+'.'+version+'.nupkg')
        if any(p.is_symlink() for p in (store/key,folder,metadata,archive)) or (
                not metadata.is_file() or not archive.is_file()):
            raise DotNetError('FC-06 restored NuGet package identity missing or symlinked: '+key)
        size=archive.stat().st_size;total_bytes+=size
        if not 0 < size <= 256*1024*1024 or total_bytes>1024*1024*1024:
            raise DotNetError('FC-06 NuGet archive exceeds bounded byte budget')
        if metadata.stat().st_size>256 or metadata.read_text(encoding='ascii').strip()!=pkg['sha512']:
            raise DotNetError('FC-06 restored NuGet package SHA-512 metadata differs from tracked lock: '+key)
        digest512=sha512();digest256=sha256()
        with archive.open('rb') as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b''):
                digest512.update(chunk);digest256.update(chunk)
        if base64.b64encode(digest512.digest()).decode()!=pkg['sha512']:
            raise DotNetError('FC-06 restored NuGet archive bytes differ from tracked SHA-512 lock: '+key)
        verified.append({'id':key,'version':version,'sha512':pkg['sha512'],
                         'archive_sha256':digest256.hexdigest(),'archive_size':size,
                         'metadata_sha256':sha256(metadata.read_bytes()).hexdigest()})
    return {'schema_version':1,'kind':'devtool.rm09-native-shared-restore-evidence',
            'review_sha256':review['review_sha256'],'sdk_version':sdk_version,
            'solutions':restored_solutions,'lock_hashes':review['lock_hashes'],
            'verified_packages':verified,'verified_package_count':len(verified),
            'verified_shared_package_count':len(review['shared_package_keys']),
            'same_job_store':True,'cross_job_reuse':False,
            'authenticated_host':False,'release_qualified':False}

def merge_trx(root: Path, paths: list[str], *, expected_project: str) -> dict:
    if not paths or len(paths)>32 or len(set(paths))!=len(paths):raise DotNetError('RM-09 TRX shards missing/duplicate')
    results={};failed=[]
    for rel in paths:
        path=_path(root,rel)
        if path.stat().st_size>8_000_000:raise DotNetError('RM-09 TRX too large')
        raw=path.read_bytes()
        if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():raise DotNetError('RM-09 unsafe TRX XML')
        tree=_xml(raw,rel)
        if tree.tag.rsplit('}',1)[-1]!='TestRun':raise DotNetError('RM-09 invalid TRX root')
        counters=[e for e in tree.iter() if e.tag.rsplit('}',1)[-1]=='Counters']
        runresults=[e for e in tree.iter() if e.tag.rsplit('}',1)[-1]=='UnitTestResult']
        if len(counters)!=1 or not runresults:raise DotNetError('RM-09 empty/invalid TRX counters')
        if counters[0].get('passed') is not None and int(counters[0].get('passed','-1'))!=sum(x.get('outcome')=='Passed' for x in runresults):
            raise DotNetError('RM-09 TRX passed counter differs from results')
        if counters[0].get('failed') is not None and int(counters[0].get('failed','-1'))!=sum(x.get('outcome')=='Failed' for x in runresults):
            raise DotNetError('RM-09 TRX failed counter differs from results')
        for item in runresults:
            uid=item.get('testId');name=item.get('testName');outcome=item.get('outcome')
            if not uid or not name or outcome not in ('Passed','Failed','NotExecuted','Timeout','Aborted'):
                raise DotNetError('RM-09 invalid TRX test identity/outcome')
            if uid in results:raise DotNetError('RM-09 duplicate test ID across TRX shards')
            results[uid]={'name':name,'outcome':outcome}
            if outcome!='Passed':failed.append(uid)
        if int(counters[0].get('total','-1'))!=len(runresults):
            raise DotNetError('RM-09 TRX counters/results mismatch')
    if failed:raise DotNetError('RM-09 TRX contains failed/nonexecuted tests: '+','.join(failed[:4]))
    return {'kind':'devtool.dotnet.trx-merge','project':expected_project,'shards':len(paths),
            'tests':len(results),'passed':len(results),'failed':0,'source_authenticated':False,
            'release_qualified':False}


def verify_nupkg(path: Path, *, package_id: str, version: str, tfm: str, rid: str = '') -> dict:
    if not _NAME.fullmatch(package_id) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[a-zA-Z0-9.-]+)?',version):
        raise DotNetError('RM-09 invalid NuGet package identity')
    if not _TFM.fullmatch(tfm) or (rid and rid not in _RIDS):
        raise DotNetError('RM-09 invalid NuGet target TFM/RID')
    if path.is_symlink() or not path.is_file() or path.stat().st_size>100_000_000:
        raise DotNetError('RM-09 missing/symlinked/oversized NuGet package')
    try:
        with zipfile.ZipFile(path) as z:
            infos=z.infolist();names=[i.filename for i in infos]
            if len(infos)>2048 or len(set(names))!=len(names) or sum(i.file_size for i in infos)>200_000_000:
                raise DotNetError('RM-09 duplicate/oversized NuGet package members')
            if any(n.startswith('/') or '\\' in n or any(p in ('','.','..') for p in n.rstrip('/').split('/')) for n in names):
                raise DotNetError('RM-09 unsafe NuGet member path')
            if any((info.external_attr >> 16) & 0o170000 == 0o120000 for info in infos):
                raise DotNetError('RM-09 NuGet symlink member refused')
            nuspec=[n for n in names if n.endswith('.nuspec') and '/' not in n]
            if len(nuspec)!=1:raise DotNetError('RM-09 expected exactly one root nuspec')
            if z.getinfo(nuspec[0]).file_size > 256_000:
                raise DotNetError('RM-09 oversized nuspec metadata')
            meta=_xml(z.read(nuspec[0]),'nuspec')
            ids=[(el.text or '').strip() for el in meta.iter() if el.tag.rsplit('}',1)[-1]=='id']
            vers=[(el.text or '').strip() for el in meta.iter() if el.tag.rsplit('}',1)[-1]=='version']
            if ids!=[package_id] or vers!=[version] or nuspec[0]!=package_id+'.nuspec':
                raise DotNetError('RM-09 NuGet package identity mismatch')
            if rid:
                matched=any(n.startswith('runtimes/'+rid+'/lib/'+tfm+'/') or
                            n.startswith('runtimes/'+rid+'/native/') for n in names)
            else:
                matched=any(n.startswith('lib/'+tfm+'/') or n.startswith('ref/'+tfm+'/') for n in names)
            if not matched:
                raise DotNetError('RM-09 NuGet target framework/runtime missing from package')
    except (zipfile.BadZipFile,ET.ParseError) as exc:
        raise DotNetError('RM-09 malformed NuGet zip/XML') from exc
    return {'kind':'devtool.dotnet.nupkg-verification','package_id':package_id,'version':version,
            'tfm':tfm,'rid':rid,'sha256':sha256(path.read_bytes()).hexdigest(),
            'bytes_verified':True,'signer_authenticated':False,'release_qualified':False}


def collect_dotnet_coverage(root: Path, node: str) -> dict:
    """Promote one native coverlet collector output to the declared owned path.

    The collector writes a random GUID subtree below dist/. Only this native
    test node can promote its exact one, verified XML report for byte handoff.
    """
    destination=_path(root,'dist/coverage.cobertura.xml',existing=False)
    if destination.exists() or destination.is_symlink():
        raise DotNetError('RM-09 native coverage destination already exists')
    directory=root/'dist'
    if not directory.is_dir() or directory.is_symlink():
        raise DotNetError('RM-09 native coverage output directory missing')
    choices=[]
    for path in directory.glob('*/coverage.cobertura.xml'):
        if path.is_symlink() or path.parent.is_symlink() or not path.is_file():
            raise DotNetError('RM-09 unsafe coverage collector file')
        if not re.fullmatch(r'[0-9a-fA-F-]{36}',path.parent.name):
            raise DotNetError('RM-09 unexpected collector directory')
        choices.append(path)
    if len(choices)!=1:
        raise DotNetError('RM-09 requires exactly one native coverage collector output')
    raw=choices[0].read_bytes()
    if not raw or len(raw)>8_000_000 or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise DotNetError('RM-09 unsafe native coverage XML')
    xml=_xml(raw,choices[0].as_posix())
    if xml.tag!='coverage' or not xml.attrib.get('line-rate'):
        raise DotNetError('RM-09 native coverage report is not Cobertura')
    try:
        rate=float(xml.attrib['line-rate'])
    except ValueError:
        raise DotNetError('RM-09 malformed Cobertura line rate') from None
    if not 0<=rate<=1 or not list(xml.iter('class')):
        raise DotNetError('RM-09 native coverage has no verified class data')
    destination.write_bytes(raw)
    return {'schema_version':1,'kind':'devtool.rm09-native-coverage','node':node,
            'path':'dist/coverage.cobertura.xml','sha256':sha256(raw).hexdigest(),
            'size':len(raw),'line_rate':rate,'release_qualified':False}


def make_publish_receipt(root: Path, outputs: list[str], selected: str,
                         sources: dict, node: str) -> dict:
    """Byte-bound publish receipt for all owned outputs (never claims signing)."""
    if not outputs or len(outputs)>48 or len(set(outputs))!=len(outputs):
        raise DotNetError('RM-09 publish file inventory missing/duplicated')
    entries={}
    for rel in sorted(outputs):
        if not rel.startswith('dist/'):
            raise DotNetError('RM-09 publish output must be beneath dist/')
        path=_path(root,rel,existing=False)
        if not path.is_file() or path.stat().st_size>512*1024*1024:
            raise DotNetError('RM-09 publish file missing, unsafe or oversized: '+rel)
        payload=path.read_bytes()
        entries[rel]={'size':len(payload),'sha256':sha256(payload).hexdigest()}
    digest=sha256(json.dumps(sources,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return {'schema_version':1,'kind':'devtool.rm09-publish-bytes','node':node,'selected':selected,
            'source_sha256':digest,'files':entries,'signed':False,'release_qualified':False}


def verify_publish_receipt(root: Path, receipt: dict) -> bool:
    if receipt.get('kind')!='devtool.rm09-publish-bytes' or not receipt.get('files'):
        raise DotNetError('RM-09 publish receipt has invalid identity')
    for rel,record in receipt['files'].items():
        path=_path(root,rel,existing=False)
        if not path.is_file() or path.stat().st_size!=record['size'] or sha256(path.read_bytes()).hexdigest()!=record['sha256']:
            raise DotNetError('RM-09 publish bytes mutated or removed: '+rel)
    return True
