"""RM-13: bounded source-reviewed C/C++ project, test, and binary contracts.

Read-only inspection never invokes CMake/Meson, toolchains, wrappers or project
scripts. This code does not assert trust in generated output or remote runners.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from typing import Any

class CppError(ValueError): pass

PATH = re.compile(r'[A-Za-z0-9_./+-]{1,180}\Z')
NAME = re.compile(r'[A-Za-z0-9_-]{1,64}\Z')
COMPILERS={'gcc':('12','13','14'), 'clang':('16','17','18','19'), 'msvc':('19.4',)}
BUILDS=('Debug','Release','RelWithDebInfo')
SANITIZERS={'none':(), 'address':('address',), 'undefined':('undefined',),
            'address,undefined':('address','undefined')}
EXTS={'.c','.cc','.cpp','.cxx','.h','.hpp','.hh','.hxx','.cmake','.ini','.wrap'}
LIMIT=1024*1024


def _read(root:Path, rel:str, *, max_bytes:int=LIMIT)->bytes:
    if (not isinstance(rel,str) or not PATH.fullmatch(rel) or rel.startswith('/') or
        any(s in ('','.','..') for s in rel.split('/')) or '\\' in rel):
        raise CppError('RM-13 unsafe project source path: '+str(rel)[:100])
    p=root
    for item in rel.split('/'):
        p=p/item
        if p.is_symlink():raise CppError('RM-13 source symlink refused: '+rel)
    if not p.is_file() or p.stat().st_size>max_bytes:
        raise CppError('RM-13 missing or unbounded source: '+rel)
    result=subprocess.run(['git','-C',str(root),'ls-files','--error-unmatch','--',rel],capture_output=True,timeout=8)
    if result.returncode:raise CppError('RM-13 source is not Git tracked: '+rel)
    data=p.read_bytes()
    if b'\0' in data:raise CppError('RM-13 NUL source refused')
    return data


def _tracked_sources(root:Path)->dict[str,str]:
    result=subprocess.run(['git','-C',str(root),'ls-files','-z'],capture_output=True,check=True,timeout=10)
    selected=[]
    for raw in result.stdout.split(b'\0'):
        if not raw:continue
        try:rel=raw.decode('utf-8')
        except UnicodeDecodeError:raise CppError('RM-13 non-UTF8 source path') from None
        p=Path(rel)
        if (p.suffix.lower() in EXTS or p.name in ('CMakeLists.txt','CMakePresets.json','CMakeUserPresets.json','meson.build','meson_options.txt')):
            if rel.startswith(('build/','dist/','.git/')):continue
            selected.append(rel)
    if not selected or len(selected)>128:raise CppError('RM-13 unbounded or absent native sources')
    return {x:hashlib.sha256(_read(root,x)).hexdigest() for x in sorted(selected)}


def _text(data:bytes)->str:
    try:return data.decode('utf-8')
    except UnicodeDecodeError:raise CppError('RM-13 requires UTF-8 project source') from None


def _review_cmake(text:str)->None:
    # cmake is a programming language. We admit declarative build primitives,
    # not arbitrary configure-time side effects or third-party downloads.
    forbidden=(r'\bexecute_process\s*\(',r'\bfile\s*\(\s*(?:DOWNLOAD|UPLOAD|WRITE|APPEND|REMOVE|GENERATE|GLOB)',
               r'\b(?:ExternalProject(?:_[A-Za-z]+)?|FetchContent(?:_[A-Za-z]+)?|CPMAddPackage|include|add_custom_command|add_custom_target|install)\s*\(',
               r'\b(?:cmake_language|eval|try_run|configure_file)\s*\(',r'\$ENV\{|\$\{[^}]+\}.*(?:\||;|`)',
               r'\b(?:find_program|find_package)\s*\(')
    if any(re.search(p,text,re.I) for p in forbidden) or len(text)>LIMIT:
        raise CppError('RM-13 unsafe/unreviewed executable CMake build operation')
    if not re.search(r'\bcmake_minimum_required\s*\(',text,re.I) or not re.search(r'\bproject\s*\(',text,re.I):
        raise CppError('RM-13 missing CMake project declaration')


def _review_meson(text:str)->None:
    forbidden=(r'\b(?:run_command|run_target|custom_target|subproject|dependency|find_program|configure_file|fs\.|import|generator|install_data|install_subdir)\s*\(',
               r'\b(?:get_option|environment|meson\.add_|meson\.get_)\s*\(',r'\b(?:foreach|if|elif|else)\b')
    if any(re.search(p,text,re.I) for p in forbidden) or not re.search(r'\bproject\s*\(',text):
        raise CppError('RM-13 unsafe/unreviewed executable Meson declaration')


def _review_preset(root:Path, sources:dict[str,str], name:str, build_type:str)->dict:
    if 'CMakeUserPresets.json' in sources:raise CppError('RM-13 user CMake presets cannot be reviewed')
    if 'CMakePresets.json' not in sources or 'CMakeLists.txt' not in sources:
        raise CppError('RM-13 requires tracked CMakePresets.json and CMakeLists.txt')
    _review_cmake(_text(_read(root,'CMakeLists.txt')))
    try:cfg=json.loads(_read(root,'CMakePresets.json'))
    except (ValueError,UnicodeError):raise CppError('RM-13 malformed CMake presets') from None
    if not isinstance(cfg,dict) or set(cfg)-{'version','cmakeMinimumRequired','configurePresets','buildPresets','testPresets'}:
        raise CppError('RM-13 unsupported/unsafe CMake preset configuration')
    if cfg.get('version') not in (3,4,5,6,7,8,9,10):raise CppError('RM-13 unsupported preset version')
    conf=cfg.get('configurePresets')
    if not isinstance(conf,list) or len(conf)>12:raise CppError('RM-13 missing or unbounded configure presets')
    selected=[p for p in conf if isinstance(p,dict) and p.get('name')==name]
    if len(selected)!=1:raise CppError('RM-13 missing or repeated configured CMake preset')
    p=selected[0]
    if set(p)-{'name','generator','binaryDir','cacheVariables','description','displayName'}:
        raise CppError('RM-13 CMake preset contains unreviewed executable policy')
    if p.get('generator')!='Ninja' or p.get('binaryDir')!='${sourceDir}/build/'+name:
        raise CppError('RM-13 reviewed CMake preset requires Ninja and bounded build directory')
    vars=p.get('cacheVariables',{})
    if not isinstance(vars,dict) or set(vars)-{'CMAKE_BUILD_TYPE','CMAKE_EXPORT_COMPILE_COMMANDS'} or vars.get('CMAKE_BUILD_TYPE')!=build_type:
        raise CppError('RM-13 unsupported CMake cache variables/build type')
    if vars.get('CMAKE_EXPORT_COMPILE_COMMANDS','OFF') not in ('ON','OFF'):
        raise CppError('RM-13 nonliteral compilation database option')
    for key in ('buildPresets','testPresets'):
        arr=cfg.get(key,[])
        if not isinstance(arr,list) or len(arr)>12:raise CppError('RM-13 unsafe '+key)
        for sub in arr:
            if not isinstance(sub,dict) or set(sub)-{'name','configurePreset','description','displayName','output','execution'}:
                raise CppError('RM-13 unsupported '+key+' policy')
            if sub.get('configurePreset')!=name or not isinstance(sub.get('name'),str) or not NAME.fullmatch(sub['name']):
                raise CppError('RM-13 preset mapping diverges from selected configure preset')
            if set(sub)&{'output','execution'}:raise CppError('RM-13 unreviewed test/build override')
    return {'name':name,'build_dir':'build/'+name,'generator':'Ninja'}


def _review_meson_profile(root:Path,sources:dict[str,str],name:str)->dict:
    if 'meson.build' not in sources:raise CppError('RM-13 missing tracked meson.build')
    _review_meson(_text(_read(root,'meson.build')))
    path='ci/meson-'+name+'.ini'
    if path not in sources:raise CppError('RM-13 requires reviewed tracked Meson profile '+path)
    content=_text(_read(root,path))
    # Meson machine files can execute binaries. Accept only literal build options.
    if not re.fullmatch(r'\[built-in options\]\s*\nbuildtype\s*=\s*["\'](?:debug|release|debugoptimized)["\']\s*\n?',content):
        raise CppError('RM-13 unsupported or executable Meson native/cross file')
    return {'name':name,'build_dir':'build/'+name,'generator':'meson'}


def cpp_project(root:Path, *, manager:str, preset:str, build_type:str)->dict[str,Any]:
    root=root.resolve()
    if manager not in ('cmake','meson') or build_type not in BUILDS or not isinstance(preset,str) or not NAME.fullmatch(preset):
        raise CppError('RM-13 unknown native manager, preset or build type')
    sources=_tracked_sources(root)
    if manager=='cmake':p=_review_preset(root,sources,preset,build_type)
    else:p=_review_meson_profile(root,sources,preset)
    # Read-only does not execute the build system, even when project scripts are untrusted.
    for rel in sources:
        if rel.endswith('/CMakeLists.txt'):_review_cmake(_text(_read(root,rel)))
        if rel.endswith('/meson.build'):_review_meson(_text(_read(root,rel)))
    return {'manager':manager,'preset':preset,'build_type':build_type,'profile':p,
            'source_sha256':sources,'source_digest':hashlib.sha256(json.dumps(sources,sort_keys=True).encode()).hexdigest()}


def inspect_cpp_job(root:Path, argv:list[str], meta:dict)->dict:
    if not isinstance(meta,dict):raise CppError('RM-13 metadata must be mapping')
    manager=meta.get('ci_cpp_manager','cmake');preset=meta.get('ci_cpp_preset','ci')
    manager_version=meta.get('ci_cpp_manager_version','')
    if not isinstance(manager_version,str) or not re.fullmatch(r'[0-9]{1,2}\.[0-9]{1,2}(?:\.[0-9]{1,2})?',manager_version):
        raise CppError('RM-13 build system requires exact reviewed CMake/Meson version')
    build_type=meta.get('ci_cpp_build_type','Release')
    compiler=meta.get('ci_cpp_compiler','gcc');version=meta.get('ci_cpp_compiler_version','13')
    sanitizer=meta.get('ci_cpp_sanitizer','none');operation=meta.get('ci_cpp_operation','')
    if compiler not in COMPILERS or version not in COMPILERS[compiler]:raise CppError('RM-13 unreviewed compiler/version')
    matrix_raw=meta.get('ci_cpp_compiler_matrix','')
    if not isinstance(matrix_raw,str) or len(matrix_raw)>120:
        raise CppError('RM-13 compiler matrix must be bounded literal text')
    matrix=[]
    if matrix_raw:
        parts=matrix_raw.split(',')
        if len(parts)>4 or len(set(parts))!=len(parts):
            raise CppError('RM-13 compiler matrix duplicate/too many cells')
        for token in parts:
            pair=token.split(':')
            if len(pair)!=2 or pair[0] not in COMPILERS or pair[1] not in COMPILERS[pair[0]]:
                raise CppError('RM-13 invalid compiler matrix member')
            matrix.append(token)
        if compiler+':'+version not in matrix:
            raise CppError('RM-13 compiler matrix must include primary pin')
        # No multiple compiler versions with the same display identity.
        matrix=sorted(matrix)
    else:matrix=[compiler+':'+version]
    if any(x.startswith('msvc:') for x in matrix) and sanitizer not in ('none','address'):
        raise CppError('RM-13 incompatible sanitizer across MSVC matrix')
    if sanitizer not in SANITIZERS or (compiler=='msvc' and sanitizer not in ('none','address')) or (build_type=='Release' and sanitizer!='none'):
        raise CppError('RM-13 compiler/sanitizer/build-type matrix incompatible')
    project=cpp_project(root,manager=manager,preset=preset,build_type=build_type)
    build_dir=project['profile']['build_dir']
    selected={'configure','build','test','package','symbols'}
    if operation not in selected:raise CppError('RM-13 operation must be explicit reviewed kind')
    commands={
       'cmake': {'configure':['cmake','--preset',preset],
                 'build':['cmake','--build',build_dir],
                 'test':['ctest','--test-dir',build_dir,'--output-junit','dist/cpp-tests.xml'],
                 'package':['cmake','--build',build_dir],
                 'symbols':['cmake','--build',build_dir]},
       'meson': {'configure':['meson','setup',build_dir,'--native-file','ci/meson-'+preset+'.ini','--buildtype',{'Debug':'debug','Release':'release','RelWithDebInfo':'debugoptimized'}[build_type]],
                 'build':['meson','compile','-C',build_dir],
                 'test':['meson','test','-C',build_dir,'--no-rebuild','--print-errorlogs'],
                 'package':['meson','compile','-C',build_dir],
                 'symbols':['meson','compile','-C',build_dir]}}
    if argv!=commands[manager][operation]:raise CppError('RM-13 direct argv diverges from reviewed operation')
    fmt=meta.get('ci_cpp_abi','elf-x86_64' if compiler!='msvc' else 'pe-x86_64')
    if fmt not in ('elf-x86_64','elf-aarch64','pe-x86_64','macho-x86_64'):
        raise CppError('RM-13 unknown artifact ABI contract')
    if compiler=='msvc' and fmt!='pe-x86_64':raise CppError('RM-13 MSVC binary must use PE/AMD64')
    if compiler in ('gcc','clang') and fmt=='pe-x86_64':raise CppError('RM-13 PE artifacts require reviewed Windows MSVC')
    system_deps=meta.get('ci_cpp_system_deps',[])
    if not isinstance(system_deps,list) or len(system_deps)>16 or any(not isinstance(x,str) or not NAME.fullmatch(x) for x in system_deps):
        raise CppError('RM-13 system dependencies must be explicitly named/bounded')
    if system_deps:raise CppError('RM-13 unreviewed system dependencies require a separate pinned profile')
    binary_name=meta.get('ci_cpp_binary_name','')
    if operation in ('package','symbols') and (not isinstance(binary_name,str) or not NAME.fullmatch(binary_name)):
        raise CppError('RM-13 native artifact requires explicit reviewed binary name')
    symbols=meta.get('ci_cpp_symbols',[])
    if not isinstance(symbols,list) or len(symbols)>24 or any(not isinstance(x,str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,95}',x) for x in symbols):
        raise CppError('RM-13 invalid required symbol names')
    return {'provider':'cpp','manager':manager,'version':version,'matrix_versions':[version],'compiler_matrix':matrix,
            'compiler':compiler,'compiler_version':version,'manager_version':manager_version,'sanitizer':sanitizer,'command':operation,
            'project':project,'abi':fmt,'symbols':symbols,'binary_name':binary_name,'version_source':'reviewed-native-ci-metadata',
            'lockfiles':list(project['source_sha256']),'cache_enabled':False,
            'report_adapter':'ctest-junit' if operation=='test' else 'compiler-diagnostic',
            'output_role':'local-bytes-only','release_qualified':False}


def verify_binary(path:Path, *, abi:str, symbols:list[str]|None=None)->dict:
    if path.is_symlink() or not path.is_file() or not 0<path.stat().st_size<64*1024*1024:
        raise CppError('RM-13 missing, symlink or unbounded native artifact')
    buf=path.read_bytes()
    arch=''
    if buf[:4]==b'\x7fELF':
        if len(buf)<64 or buf[4]!=2 or buf[5]!=1:raise CppError('RM-13 ELF class/endianness mismatch')
        machine=struct.unpack_from('<H',buf,18)[0]
        arch={62:'elf-x86_64',183:'elf-aarch64'}.get(machine,'')
        if struct.unpack_from('<H',buf,16)[0] not in (1,2,3):raise CppError('RM-13 ELF file type invalid')
    elif buf[:2]==b'MZ':
        if len(buf)<128:raise CppError('RM-13 truncated PE')
        off=struct.unpack_from('<I',buf,60)[0]
        if off+24>len(buf) or buf[off:off+4]!=b'PE\0\0':raise CppError('RM-13 malformed PE/COFF header')
        machine=struct.unpack_from('<H',buf,off+4)[0]
        arch={0x8664:'pe-x86_64'}.get(machine,'')
    elif buf[:4] in (b'\xcf\xfa\xed\xfe',b'\xfe\xed\xfa\xcf'):
        if len(buf)<32:raise CppError('RM-13 truncated Mach-O')
        endian='<' if buf[:4]==b'\xcf\xfa\xed\xfe' else '>'
        machine=struct.unpack_from(endian+'I',buf,4)[0]
        arch={0x01000007:'macho-x86_64'}.get(machine,'')
    if arch!=abi:raise CppError('RM-13 wrong native architecture/ABI: declared '+abi+', found '+str(arch))
    found=[]
    if symbols:
        if abi.startswith('pe-'):
            raise CppError('RM-13 PE symbol verification requires pre-reviewed PDB tooling')
        try:r=subprocess.run(['nm','-g','--defined-only',str(path)],capture_output=True,text=True,timeout=10)
        except (OSError,subprocess.TimeoutExpired):raise CppError('RM-13 symbol inspection tool unavailable') from None
        if r.returncode:raise CppError('RM-13 uninspectable symbols')
        found=sorted(set(re.findall(r'(?m)^[0-9a-fA-F]*\s+[A-Za-z]\s+(\S+)\s*$',r.stdout)))
        if not set(symbols).issubset(found):raise CppError('RM-13 declared symbol absent from binary')
    return {'abi':arch,'sha256':hashlib.sha256(buf).hexdigest(),'size':len(buf),
            'required_symbols':symbols or [],'verified_local_bytes':True,'release_qualified':False}


def verify_archive(path:Path,*,expected_abi:str)->dict:
    if path.is_symlink() or not path.is_file() or not 0<path.stat().st_size<64*1024*1024:
        raise CppError('RM-13 invalid archive')
    abi_members=0
    if path.suffix=='.zip':
        try:
            with zipfile.ZipFile(path) as z:
                infos=z.infolist()
                if not infos or len(infos)>256:raise CppError('RM-13 archive file count invalid')
                names=[]
                for i in infos:
                    name=i.filename
                    if ('\\' in name or name.startswith('/') or name.endswith('/') or
                        any(x in ('','.','..') for x in name.split('/')) or
                        i.file_size>32*1024*1024 or i.flag_bits&1 or (i.external_attr>>16)&0o170000==0o120000):
                        raise CppError('RM-13 unsafe archive content')
                    names.append(name)
                if len(names)!=len(set(x.casefold() for x in names)):raise CppError('RM-13 duplicate archive entries')
                total=0
                for i in infos:
                    total+=i.file_size
                    if total>128*1024*1024 or (i.file_size>1024*1024 and i.file_size>200*max(i.compress_size,1)):
                        raise CppError('RM-13 archive decompression budget exceeded')
                    data=z.read(i)
                    if data[:4]==b'\x7fELF':
                        if len(data)<64 or data[4:6]!=b'\x02\x01' or struct.unpack_from('<H',data,16)[0] not in (1,2,3) or {62:'elf-x86_64',183:'elf-aarch64'}.get(struct.unpack_from('<H',data,18)[0])!=expected_abi:
                            raise CppError('RM-13 embedded ELF has wrong ABI')
                        abi_members+=1
                    elif data[:2]==b'MZ':
                        if len(data)<128:
                            raise CppError('RM-13 embedded PE malformed')
                        offset=struct.unpack_from('<I',data,60)[0]
                        if (offset+24>len(data) or data[offset:offset+4]!=b'PE\0\0' or
                            struct.unpack_from('<H',data,offset+4)[0]!=0x8664 or expected_abi!='pe-x86_64'):
                            raise CppError('RM-13 embedded PE has wrong ABI')
                        abi_members+=1
                    elif data[:4] in (b'\xcf\xfa\xed\xfe',b'\xfe\xed\xfa\xcf'):
                        if len(data)<32:raise CppError('RM-13 embedded Mach-O malformed')
                        endian='<' if data[:4]==b'\xcf\xfa\xed\xfe' else '>'
                        cpu=struct.unpack_from(endian+'I',data,4)[0]
                        if cpu!=0x01000007 or expected_abi!='macho-x86_64':
                            raise CppError('RM-13 embedded Mach-O has wrong ABI')
                        abi_members+=1
                    elif name.lower().endswith(('.elf','.so','.dll','.exe','.dylib','.o','.obj')):
                        raise CppError('RM-13 purported native ZIP member cannot be ABI-verified')
                if not abi_members:
                    raise CppError('RM-13 native ZIP has no ABI-verified members')
                if 'byte-receipt.json' in names:
                    if abi_members!=1:raise CppError('RM-13 ambiguous ZIP byte receipt for multiple native members')
                    try:
                        receipt=json.loads(z.read('byte-receipt.json'))
                    except (ValueError,UnicodeError):
                        raise CppError('RM-13 invalid native ZIP byte receipt') from None
                    if not isinstance(receipt,dict) or not re.fullmatch('[0-9a-f]{64}',str(receipt.get('sha256',''))) or receipt.get('abi')!=expected_abi:
                        raise CppError('RM-13 invalid native ZIP byte receipt identity')
                    native_hashes=[hashlib.sha256(z.read(i)).hexdigest() for i in names if i!='byte-receipt.json' and (
                        z.read(i)[:4]==b'\x7fELF' or z.read(i)[:2]==b'MZ' or
                        z.read(i)[:4] in (b'\xcf\xfa\xed\xfe',b'\xfe\xed\xfa\xcf'))]
                    if native_hashes != [receipt['sha256']]:
                        raise CppError('RM-13 native ZIP byte receipt differs from verified payload')
        except (zipfile.BadZipFile,RuntimeError) as exc:raise CppError('RM-13 corrupt archive') from exc
    elif path.suffix=='.a':
        data=path.read_bytes()
        if not data.startswith(b'!<arch>\n'):raise CppError('RM-13 invalid ar archive signature')
        offset=8;members=0
        while offset<len(data):
            if offset+60>len(data) or data[offset+58:offset+60]!=b'`\n':raise CppError('RM-13 bad ar member header')
            try:n=int(data[offset+48:offset+58].strip())
            except ValueError:raise CppError('RM-13 bad ar member length') from None
            if n<0 or offset+60+n>len(data):raise CppError('RM-13 truncated ar member')
            header=data[offset:offset+60]
            name=header[:16].strip().decode('ascii','replace').rstrip('/')
            content=data[offset+60:offset+60+n]
            if name not in ('','/', '//','SYM64'):
                if len(content)<20 or content[:4]!=b'\x7fELF' or content[4:6]!=b'\x02\x01':
                    raise CppError('RM-13 uninspectable ar object member')
                machine=struct.unpack_from('<H',content,18)[0]
                if {62:'elf-x86_64',183:'elf-aarch64'}.get(machine)!=expected_abi:
                    raise CppError('RM-13 archive object ABI mismatches declaration')
                abi_members+=1
            offset+=60+n+(n%2);members+=1
            if members>512:raise CppError('RM-13 archive exceeds member limit')
        if not members or not abi_members:raise CppError('RM-13 empty archive or no verified objects')
    else:raise CppError('RM-13 only reviewed zip/ar archives supported')
    return {'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'size':path.stat().st_size,
            'format':path.suffix,'declared_abi':expected_abi,'abi_verified_member_count':abi_members,'archive_structure_verified':True,
            'qualification':'ABI_VERIFIED',
            'release_qualified':False}


def normalize_ctest_report(path:Path)->dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size>LIMIT:raise CppError('RM-13 missing or unbounded CTest JUnit')
    data=path.read_bytes()
    if any(x in data.upper() for x in (b'<!DOCTYPE',b'<!ENTITY',b'<?XML-STYLESHEET')):
        raise CppError('RM-13 unsafe CTest XML')
    try:root=ET.fromstring(data)
    except ET.ParseError:raise CppError('RM-13 malformed CTest XML') from None
    suites=[root] if root.tag=='testsuite' else list(root) if root.tag=='testsuites' else []
    if not suites or any(x.tag!='testsuite' for x in suites):raise CppError('RM-13 malformed JUnit suite root')
    seen=set();passed=failed=skipped=0
    for suite in suites:
        for case in suite.findall('testcase'):
            name=(case.attrib.get('classname',''),case.attrib.get('name',''))
            if not name[1] or name in seen:raise CppError('RM-13 duplicated/unidentified CTest case')
            seen.add(name)
            bad=bool(case.findall('failure') or case.findall('error'))
            skip=bool(case.findall('skipped'))
            failed+=int(bad);skipped+=int(skip);passed+=int(not bad and not skip)
        expected_counts=(('tests',len(suite.findall('testcase'))),
                         ('failures',sum(bool(c.findall('failure')) for c in suite.findall('testcase'))))
        if any(k in suite.attrib and int(suite.attrib[k]) != v for k,v in expected_counts):
            raise CppError('RM-13 CTest report summary inconsistent')
    if not passed or failed or skipped:raise CppError('RM-13 CTest zero success/failures/skips cannot qualify')
    return {'passed':passed,'failed':failed,'skipped':skipped,'sha256':hashlib.sha256(data).hexdigest(),
            'release_qualified':False}
