"""RM-12: bounded, read-only JVM reactor review and byte-level artifact acceptance.

Project discovery never executes Maven, Gradle, plugins, wrappers or build files.
Native target jobs retain execution ownership; no signer/release trust is claimed.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tomllib
from typing import Any
import xml.etree.ElementTree as ET
import zipfile


class JvmError(ValueError):
    pass

_LIMIT=1024*1024
_IDENT=re.compile(r'[A-Za-z][A-Za-z0-9_.-]{0,79}\Z')
_PATH=re.compile(r'[A-Za-z0-9_./-]{1,180}\Z')
_VERSIONS=('17','21','25')
_ANDROID=re.compile(r'com\.android\.(application|library|test)|com\.android\.tools\.build(?::gradle|</groupId>)|AndroidManifest\.xml|android\s*\{',re.I)


def _source(root:Path, rel:str) -> bytes:
    if not isinstance(rel,str) or not _PATH.fullmatch(rel) or rel.startswith('/') or any(p in ('','.','..') for p in rel.split('/')):
        raise JvmError('RM-12 unsafe source path: '+str(rel)[:100])
    here=root
    for part in rel.split('/'):
        here=here/part
        if here.is_symlink():raise JvmError('RM-12 symlink source refused')
    if not here.is_file() or here.stat().st_size>_LIMIT:raise JvmError('RM-12 missing/unbounded reviewed source: '+rel)
    p=subprocess.run(['git','-C',str(root),'ls-files','--error-unmatch','--',rel],capture_output=True,timeout=8)
    if p.returncode:raise JvmError('RM-12 source is not tracked: '+rel)
    b=here.read_bytes()
    if b'\x00' in b:raise JvmError('RM-12 binary source refused')
    return b


def _decode(data:bytes) -> str:
    try:return data.decode('utf-8')
    except UnicodeError:raise JvmError('RM-12 requires UTF-8 source') from None


def _digest(values:dict[str,str]) -> str:
    return hashlib.sha256(json.dumps(values,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def _graph(nodes:dict[str,dict], edges:dict[str,list[str]]) -> list[str]:
    if len(nodes)>24:raise JvmError('RM-12 project reactor exceeds 24 modules')
    order=[];state={}
    def visit(n:str):
        if state.get(n)==1:raise JvmError('RM-12 project dependency cycle')
        if state.get(n)==2:return
        state[n]=1
        for d in edges[n]:
            if d not in nodes or d==n:raise JvmError('RM-12 invalid dependency')
            visit(d)
        state[n]=2;order.append(n)
    for n in sorted(nodes):visit(n)
    return order


def _xml(data:bytes) -> ET.Element:
    if any(token in data.upper() for token in (b'<!DOCTYPE',b'<!ENTITY',b'<?XML-STYLESHEET')):
        raise JvmError('RM-12 unsafe XML declaration')
    try:return ET.fromstring(data)
    except ET.ParseError:raise JvmError('RM-12 malformed XML') from None


def _children(parent:ET.Element,name:str) -> list[ET.Element]:
    return [c for c in parent if c.tag.rsplit('}',1)[-1]==name]


def _one(parent:ET.Element,name:str,default:str='') -> str:
    xs=_children(parent,name)
    if len(xs)>1:raise JvmError('RM-12 ambiguous '+name)
    return (xs[0].text or '').strip() if xs else default


def _maven(root:Path) -> dict[str,Any]:
    for unreviewed in ('.mvn/maven.config','.mvn/jvm.config','.mvn/extensions.xml'):
        if (root/unreviewed).exists():
            raise JvmError('RM-12 Maven executable configuration requires separate review')
    sources={};modules={};idmap={}
    def visit(directory:str):
        path=(directory+'/' if directory else '')+'pom.xml'
        if path in sources or len(sources)>=24:raise JvmError('RM-12 repeated/unbounded Maven module')
        data=_source(root,path);sources[path]=hashlib.sha256(data).hexdigest()
        if _ANDROID.search(_decode(data)):
            raise JvmError('RM-12 Android Maven build must route to Android provider')
        node=_xml(data)
        if node.tag.rsplit('}',1)[-1]!='project':raise JvmError('RM-12 POM root must be project')
        props=_children(node,'properties')
        if len(props)>1:raise JvmError('RM-12 duplicate Maven properties')
        release=_one(props[0],'maven.compiler.release') if props else ''
        if release and release not in _VERSIONS:raise JvmError('RM-12 unsupported Maven Java release')
        parent=_children(node,'parent')
        group=_one(node,'groupId') or (_one(parent[0],'groupId') if parent else '')
        artifact=_one(node,'artifactId');version=_one(node,'version') or (_one(parent[0],'version') if parent else '')
        if not group or not artifact or not version or not _IDENT.fullmatch(group) or not _IDENT.fullmatch(artifact) or not re.fullmatch(r'[0-9][A-Za-z0-9_.-]{0,79}',version):
            raise JvmError('RM-12 Maven modules need literal group/artifact/version')
        key=group+':'+artifact
        if key in idmap:raise JvmError('RM-12 duplicate Maven coordinate')
        idmap[key]=directory or '.'
        deps=[]
        for parent_deps in _children(node,'dependencies'):
            for dep in _children(parent_deps,'dependency'):
                depgroup=_one(dep,'groupId');depname=_one(dep,'artifactId')
                if not depgroup or not depname or any(not _IDENT.fullmatch(v) for v in (depgroup,depname)):
                    raise JvmError('RM-12 Maven dependency needs literal coordinates')
                deps.append(depgroup+':'+depname)
        modules[directory or '.']={'coordinate':key,'version':version,'java_release':release,'depends_coordinates':deps,
                                   'pom':path,'packaging':_one(node,'packaging','jar')}
        if modules[directory or '.']['packaging'] not in ('pom','jar'):
            raise JvmError('RM-12 unsupported Maven packaging')
        sections=_children(node,'modules')
        if len(sections)>1:raise JvmError('RM-12 duplicate Maven module section')
        for section in sections:
            for item in _children(section,'module'):
                component=(item.text or '').strip()
                if not _PATH.fullmatch(component) or '/' in component or component in ('','.','..'):
                    raise JvmError('RM-12 Maven module path must be a direct safe child')
                visit((directory+'/' if directory else '')+component)
    visit('')
    edges={key:sorted({idmap[c] for c in value['depends_coordinates'] if c in idmap}) for key,value in modules.items()}
    order=_graph(modules,edges)
    return {'manager':'maven','modules':modules,'edges':edges,'order':order,'source_sha256':sources}


def _gradle(root:Path) -> dict[str,Any]:
    setting=next((n for n in ('settings.gradle.kts','settings.gradle') if (root/n).is_file()),None)
    if not setting:raise JvmError('RM-12 missing Gradle settings')
    data=_decode(_source(root,setting));sources={setting:hashlib.sha256(data.encode()).hexdigest()}
    if _ANDROID.search(data):raise JvmError('RM-12 Android Gradle project must route to Android provider')
    # Gradle settings scripts are executable code: accept only literal include
    # declarations and documented inert repository/name statements. No evaluation.
    cleaned=re.sub(r'(?m)^\s*//[^\n]*$', '',data)
    includes=[]
    for line in cleaned.splitlines():
        line=line.strip()
        if not line:continue
        if line.startswith('rootProject.name') and not re.fullmatch(r'rootProject\.name\s*=\s*[\"\'][A-Za-z0-9_-]+[\"\']',line):
            raise JvmError('RM-12 dynamic Gradle settings refused')
        if line.startswith(('rootProject.name','pluginManagement {','dependencyResolutionManagement {','repositories {','mavenCentral()','gradlePluginPortal()','google()','}','repositoriesMode.','repositoriesMode.set(','includeBuild(')):
            if line.startswith('includeBuild('):raise JvmError('RM-12 composite Gradle builds are not reviewed')
            if 'http://' in line or 'https://' in line:raise JvmError('RM-12 unreviewed repository location')
            continue
        match=re.fullmatch(r'include\s*\(?\s*([\'\"][^\n()]+[\'\"])\s*\)?\s*;?',line)
        if not match:raise JvmError('RM-12 dynamic Gradle settings refused')
        parts=re.findall(r'[\'\"](:[A-Za-z][A-Za-z0-9_-]*(?::[A-Za-z][A-Za-z0-9_-]*)*)[\'\"]',match.group(1))
        if not parts or ''.join(re.findall(r'[\'\"][^\'\"]+[\'\"]',match.group(1))) != re.sub(r'[\s,]','',match.group(1)):
            raise JvmError('RM-12 invalid literal Gradle include')
        includes+=parts
    if len(includes)!=len(set(includes)):raise JvmError('RM-12 duplicate Gradle include')
    names=[':']+includes
    if len(names)>24:raise JvmError('RM-12 Gradle project cap exceeded')
    catalog='gradle/libs.versions.toml'
    if (root/catalog).exists():
        raw=_source(root,catalog);sources[catalog]=hashlib.sha256(raw).hexdigest()
        try:tomllib.loads(_decode(raw))
        except (ValueError,UnicodeError):raise JvmError('RM-12 malformed Gradle version catalog') from None
    modules={};edges={}
    for name in names:
        directory='/'.join(name[1:].split(':')) if name!=':' else ''
        path=directory+'/build.gradle.kts' if directory else 'build.gradle.kts'
        alt=directory+'/build.gradle' if directory else 'build.gradle'
        if not (root/path).is_file():path=alt
        data=_decode(_source(root,path));sources[path]=hashlib.sha256(data.encode()).hexdigest()
        if _ANDROID.search(data):raise JvmError('RM-12 Android plugin must use Android provider')
        if re.search(r'java\.installations\.auto-download|org\.gradle\.java\.home|toolchain\s*\{\s*[^}]*vendor',data,re.S):
            raise JvmError('RM-12 unreviewed Gradle toolchain provisioning')
        pins=re.findall(r'(?:JavaLanguageVersion\.of|jvmToolchain)\(\s*([0-9]+)\s*\)',data)
        if len(set(pins))>1 or any(pin not in _VERSIONS for pin in pins):
            raise JvmError('RM-12 Gradle incompatible JDK toolchain pin')
        if re.search(r'\b(exec|Exec|ProcessBuilder|Runtime\.getRuntime|apply\s+from|evaluate|afterEvaluate)\b',data):
            raise JvmError('RM-12 unreviewed dynamic Gradle project')
        if name!=':' and not re.search(r'java(?:-library)?|org\.jetbrains\.kotlin\.jvm|kotlin\("jvm"\)',data):
            raise JvmError('RM-12 missing reviewed Gradle JVM plugin')
        deps=re.findall(r'project\(\s*[\'\"](:[A-Za-z0-9_:-]+)[\'\"]\s*\)',data)
        if re.search(r'project\(\s*[^\'\"]',data):raise JvmError('RM-12 dynamic Gradle project dependency')
        edges[name]=sorted(set(deps))
        modules[name]={'path':path,'directory':directory,'depends':edges[name], 'packaging':'jar',
                       'jdk_pin':pins[0] if pins else ''}
    order=_graph(modules,edges)
    locks=[name for name in ('gradle.lockfile','gradle/dependency-locks/runtimeClasspath.lockfile','gradle/verification-metadata.xml') if (root/name).is_file()]
    if not locks:raise JvmError('RM-12 Gradle requires a tracked dependency lock or verification metadata')
    for name in locks:
        data=_source(root,name);sources[name]=hashlib.sha256(data).hexdigest()
    return {'manager':'gradle','modules':modules,'edges':edges,'order':order,'source_sha256':sources}


def jvm_project(root:Path,*,manager:str) -> dict[str,Any]:
    if manager not in ('maven','gradle'):raise JvmError('RM-12 unknown JVM manager')
    if (root/'pom.xml').is_file() and any((root/p).is_file() for p in ('settings.gradle','settings.gradle.kts')):
        raise JvmError('RM-12 ambiguous Maven/Gradle project: choose separate reviewed roots')
    project=_maven(root) if manager=='maven' else _gradle(root)
    files=project['source_sha256']
    project.update({'source_files':sorted(files),'source_digest':_digest(files),'read_only':True})
    return project


def inspect_jvm_job(root:Path,argv:list[str],meta:dict[str,Any]) -> dict[str,Any]:
    if not isinstance(argv,list) or len(argv)<2 or any(not isinstance(x,str) for x in argv):
        raise JvmError('RM-12 requires literal JVM command')
    manager='maven' if argv[0]=='mvn' else 'gradle' if argv[0]=='gradle' else ''
    if not manager:raise JvmError('RM-12 only direct mvn/gradle commands allowed')
    project=jvm_project(root,manager=manager)
    jdk=meta.get('ci_jdk_version')
    matrix=meta.get('ci_jdk_versions',jdk)
    if jdk not in _VERSIONS or not isinstance(matrix,str):raise JvmError('RM-12 requires reviewed JDK version 17, 21 or 25')
    versions=matrix.split(',')
    if not 1<=len(versions)<=3 or len(set(versions))!=len(versions) or any(v not in _VERSIONS for v in versions) or jdk not in versions:
        raise JvmError('RM-12 invalid Java matrix')
    if manager=='maven':
        if argv[:5]!=['mvn','-B','-o','-ntp','-f']:
            raise JvmError('RM-12 Maven requires offline/no-transfer-progress argv')
        if len(argv)==7 and argv[5] in project['source_files'] and argv[5].endswith('pom.xml') and argv[6] in ('test','verify','package'):
            selected='.' if argv[5]=='pom.xml' else argv[5][:-8].rstrip('/')
            operation=argv[6]
        elif len(argv)==10 and argv[5]=='pom.xml' and argv[6]=='-pl' and argv[8]=='-am' and argv[9] in ('test','verify','package'):
            selected=argv[7]
            if selected not in project['modules'] or selected=='.':
                raise JvmError('RM-12 Maven -pl must select reviewed child module')
            operation=argv[9]
        else:
            raise JvmError('RM-12 Maven requires reviewed offline reactor or module command')
        if selected not in project['modules']:
            raise JvmError('RM-12 Maven selected unknown module')
        if operation=='package' and project['modules'][selected]['packaging']!='jar':
            raise JvmError('RM-12 Maven non-JAR packaging cannot produce JVM JAR output')
        min_release=project['modules'][selected]['java_release']
        if min_release and int(jdk)<int(min_release):raise JvmError('RM-12 JDK older than compiler.release')
    else:
        if len(argv)!=5 or argv[:3]!=['gradle','--offline','--no-daemon'] or argv[3]!='--console=plain' or not re.fullmatch(r'(?::[A-Za-z][A-Za-z0-9_-]*)*:(?:test|check|jar|assemble)',argv[4]):
            raise JvmError('RM-12 Gradle requires reviewed offline task argv')
        op=argv[4].rsplit(':',1)[-1];operation={'check':'test','assemble':'package','jar':'package'}.get(op,op)
        selected=argv[4].rsplit(':',1)[0] or ':'
        if selected not in project['modules']:raise JvmError('RM-12 unknown Gradle project task')
        project_pin=project['modules'][selected]['jdk_pin']
        if project_pin and int(jdk)<int(project_pin):
            raise JvmError('RM-12 JDK older than Gradle toolchain requirement')
    if operation=='verify':operation='test'
    tool_version=meta.get('ci_jvm_tool_version')
    grammar=(r'3\.9\.[0-9]{1,2}' if manager=='maven' else r'(?:8|9)\.[0-9]{1,2}(?:\.[0-9]{1,2})?')
    if not isinstance(tool_version,str) or not re.fullmatch(grammar,tool_version):
        raise JvmError('RM-12 exact reviewed Maven/Gradle tool version required')
    return {'provider':'jvm','manager':manager,'api_version':1,'toolchain':'java','version':jdk,'matrix_versions':versions,'tool_version':tool_version,
            'version_source':'native-job-ci_jdk_version','lockfiles':project['source_files'],
            'project':project,'selected':selected,'command':operation,'cache_enabled':False,
            'output_role':'local-bytes-only','report_adapter':'junit-xml','release_qualified':False}


def _archive(path:Path, *,limit:int=256) -> zipfile.ZipFile:
    if path.is_symlink() or not path.is_file() or path.stat().st_size>128*1024*1024:
        raise JvmError('RM-12 missing/unbounded regular JVM artifact')
    try:z=zipfile.ZipFile(path)
    except (OSError,zipfile.BadZipFile):raise JvmError('RM-12 invalid ZIP/JAR') from None
    names=set();total=0
    for item in z.infolist():
        name=item.filename
        if not name or name.casefold() in names or len(names)>=limit or name.startswith('/') or '\\' in name or '..' in PurePosixPath(name).parts or item.flag_bits&1:
            z.close();raise JvmError('RM-12 duplicate/unsafe/encrypted archive entry')
        if (item.external_attr>>16)&0o170000==0o120000:
            z.close();raise JvmError('RM-12 archive symlink refused')
        names.add(name.casefold());total+=item.file_size
        if item.file_size>16*1024*1024 or total>128*1024*1024:
            z.close();raise JvmError('RM-12 archive expansion budget exceeded')
    if z.testzip() is not None:
        z.close();raise JvmError('RM-12 corrupt ZIP member')
    return z


def verify_jar(path:Path, *,expected_name:str='') -> dict[str,Any]:
    with _archive(path) as z:
        names=set(z.namelist());manifest=next((n for n in names if n.upper()=='META-INF/MANIFEST.MF'),None)
        if not manifest:raise JvmError('RM-12 JAR lacks manifest')
        raw=z.read(manifest)
        if len(raw)>65536 or b'Manifest-Version:' not in raw or b'\x00' in raw:
            raise JvmError('RM-12 malformed JAR manifest')
        for field in raw.splitlines():
            if field.startswith(b'Class-Path:'):
                classpath=field.split(b':',1)[1].strip().split()
                if any(not re.fullmatch(rb'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\.jar',name)
                           or b'..' in name for name in classpath):
                    raise JvmError('RM-12 unsafe JAR manifest classpath')
        classes=[n for n in names if n.endswith('.class')]
        if not classes:raise JvmError('RM-12 JAR contains no compiled class')
        if any(not z.read(n).startswith(b'\xca\xfe\xba\xbe') for n in classes):
            raise JvmError('RM-12 JAR class bytecode magic mismatch')
        sf=[n for n in names if re.fullmatch(r'META-INF/[^/]+\.SF',n,re.I)]
        signatures=[n for n in names if re.fullmatch(r'META-INF/[^/]+\.(RSA|DSA|EC)',n,re.I)]
        if bool(sf)!=bool(signatures) or any(n.rsplit('.',1)[0].upper() not in {x.rsplit('.',1)[0].upper() for x in signatures} for n in sf):
            raise JvmError('RM-12 incomplete JAR signing structure')
        if sf:
            # jarsigner signature verification is not a trust-store, signer
            # identity, certificate-validity, or release-qualification claim.
            try:res=subprocess.run(['jarsigner','-verify','-strict',str(path)],capture_output=True,timeout=20)
            except (OSError,subprocess.TimeoutExpired):raise JvmError('RM-12 cannot verify claimed JAR signature') from None
            if res.returncode:raise JvmError('RM-12 JAR signature verification failed')
    if expected_name and path.name!=expected_name:raise JvmError('RM-12 JAR file identity mismatch')
    return {'kind':'jvm-jar','sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'classes':len(classes),
            'manifest':True,'signed_structure':bool(sf),'signature_verified_locally':bool(sf),
            'signer_authenticated':False,'release_qualified':False}


def verify_desktop_bundle(path:Path) -> dict[str,Any]:
    if not path.name.endswith('.zip'):raise JvmError('RM-12 desktop distribution must be ZIP')
    with _archive(path) as z:
        members=z.namelist()
        jars=[n for n in members if n.startswith('app/') and n.endswith('.jar')]
        launchers=[n for n in members if n.startswith('bin/') and not n.endswith('/')]
        if len(jars)!=1 or not launchers:raise JvmError('RM-12 desktop bundle needs app/*.jar and bin/launcher')
        if any(not z.read(n) for n in jars+launchers):raise JvmError('RM-12 empty desktop payload')
        nested=z.read(jars[0]);tmp=io.BytesIO(nested)
        try:
            with zipfile.ZipFile(tmp) as jar:
                if 'META-INF/MANIFEST.MF' not in jar.namelist() or not any(n.endswith('.class') for n in jar.namelist()):
                    raise JvmError('RM-12 invalid desktop JAR payload')
        except zipfile.BadZipFile:raise JvmError('RM-12 invalid nested JAR') from None
    return {'kind':'jvm-desktop-zip','sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'launcher_count':len(launchers),'signer_authenticated':False,'release_qualified':False}


def aggregate_junit(root:Path, project:dict[str,Any], selected:str, *, output:str='dist/jvm-tests.xml') -> dict[str,Any]:
    module=project['modules'][selected];directory=module.get('directory',selected if selected!='.' else '')
    prefix=(directory+'/' if directory else '')
    if project['manager']=='maven':report_dir=root/prefix/'target/surefire-reports'
    else:report_dir=root/prefix/'build/test-results/test'
    if report_dir.is_symlink() or not report_dir.is_dir():raise JvmError('RM-12 missing native JUnit directory')
    reports=sorted(report_dir.glob('TEST-*.xml'))
    if not reports or len(reports)>64:raise JvmError('RM-12 no bounded native JUnit reports')
    tests=failures=skipped=0;identities=set();suite=ET.Element('testsuite')
    for report in reports:
        if report.is_symlink() or report.stat().st_size>_LIMIT:raise JvmError('RM-12 unsafe native JUnit report')
        doc=_xml(report.read_bytes());tag=doc.tag.rsplit('}',1)[-1]
        groups=[doc] if tag=='testsuite' else list(doc) if tag=='testsuites' else []
        if not groups or any(g.tag.rsplit('}',1)[-1]!='testsuite' for g in groups):
            raise JvmError('RM-12 invalid JUnit root')
        for group in groups:
            for case in group:
                if case.tag.rsplit('}',1)[-1]!='testcase':continue
                identity=(case.get('classname',''),case.get('name',''))
                if not identity[1] or identity in identities:raise JvmError('RM-12 duplicate JUnit testcase')
                identities.add(identity);tests+=1
                for child in case:
                    if child.tag.rsplit('}',1)[-1] in ('failure','error'):failures+=1
                    if child.tag.rsplit('}',1)[-1]=='skipped':skipped+=1
                suite.append(ET.fromstring(ET.tostring(case)))
            if int(group.get('tests','-1'))!=len([c for c in group if c.tag.rsplit('}',1)[-1]=='testcase']):
                raise JvmError('RM-12 JUnit declared count mismatch')
    if tests==0 or failures or tests==skipped:raise JvmError('RM-12 JUnit failures, zero or entirely skipped')
    suite.set('tests',str(tests));suite.set('failures','0');suite.set('skipped',str(skipped))
    dest=root/output
    if dest.exists() or dest.is_symlink():raise JvmError('RM-12 JUnit output already exists')
    dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_bytes(ET.tostring(suite,encoding='utf-8',xml_declaration=True))
    return {'kind':'jvm-junit','tests':tests,'passed':tests-skipped,'skipped':skipped,'source_reports':len(reports),'sha256':hashlib.sha256(dest.read_bytes()).hexdigest()}


def materialize_jvm_package(root:Path,project:dict[str,Any],selected:str,artifact:str,*,desktop:bool=False) -> list[dict[str,Any]]:
    module=project['modules'][selected]
    directory=module.get('directory',selected if selected!='.' else '')
    if project['manager']=='maven':
        path=root/directory/'target'/f'{artifact}-{module["version"]}.jar'
    else:
        folder=root/directory/'build/libs'
        if folder.is_symlink():raise JvmError('RM-12 unsafe Gradle build directory')
        candidates=list(folder.glob('*.jar')) if folder.is_dir() else []
        if len(candidates)!=1:raise JvmError('RM-12 requires exactly one Gradle JAR output')
        path=candidates[0]
    # Check every generated artifact before making a derived dist/ file.
    if any(parent.is_symlink() for parent in (path,*path.parents) if parent!=root.parent) or not path.is_file():
        raise JvmError('RM-12 missing or symlinked source JAR')
    original=verify_jar(path)
    output=root/'dist'/f'{artifact}.jar'
    if output.exists() or output.is_symlink():raise JvmError('RM-12 refused pre-existing JAR output')
    output.parent.mkdir(exist_ok=True)
    output.write_bytes(path.read_bytes())
    receipt=verify_jar(output,expected_name=artifact+'.jar')
    if receipt['sha256']!=original['sha256']:
        output.unlink(missing_ok=True);raise JvmError('RM-12 JAR changed during output materialization')
    rows=[receipt]
    if desktop:
        with zipfile.ZipFile(output) as z:
            manifest=z.read('META-INF/MANIFEST.MF')
            entry=re.search(rb'(?m)^Main-Class:\s*([A-Za-z_$][A-Za-z0-9_$.]*)\s*$',manifest)
            if not entry:raise JvmError('RM-12 desktop app requires manifest Main-Class')
        dest=root/'dist'/f'{artifact}.zip'
        if dest.exists() or dest.is_symlink():raise JvmError('RM-12 preexisting desktop distribution')
        with zipfile.ZipFile(dest,'w',compression=zipfile.ZIP_DEFLATED) as z:
            launcher=b'#!/bin/sh\nset -eu\nexec java -jar "$(dirname "$0")/../app/'+artifact.encode()+b'.jar" "$@"\n'
            for name,content,mode in ((f'app/{artifact}.jar',output.read_bytes(),0o100644),('bin/start',launcher,0o100755)):
                info=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0))
                info.external_attr=mode<<16
                z.writestr(info,content,compress_type=zipfile.ZIP_DEFLATED)
        rows.append(verify_desktop_bundle(dest))
    return rows
