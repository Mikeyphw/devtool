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

SOURCE = Path(__file__).resolve().parents[1]
LANES = ('github-actions-dag','linux-polyglot','windows-dotnet-msvc','macos-native')
EXPECTED = {'linux-polyglot': 'Linux', 'windows-dotnet-msvc': 'Windows', 'macos-native': 'Darwin'}
EXPECTED_CHECKS = {
    'github-actions-dag': ('immutable-source','negative-exit-propagation','dag-fanout'),
    'linux-polyglot': ('python','python-uv','python-poetry','python-pdm','go','node','rust','maven','cmake','meson','negative-exit-propagation'),
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
        capture_success_tail:bool=False)->dict[str,Any]:
    env=os.environ.copy();env.update(extra_env or {})
    p=subprocess.run(argv,cwd=cwd,capture_output=True,timeout=timeout,env=env)
    # Only failed commands disclose bounded, sanitized diagnostic tails. Never
    # include the process environment, raw payload or bearer tokens in receipts.
    return {'argv':argv,'code':p.returncode,'stdout_sha256':sha_bytes(p.stdout),
            'stderr_sha256':sha_bytes(p.stderr),'stdout_tail':_public_diagnostic(p.stdout) if p.returncode or capture_success_tail else '',
            'stderr_tail':_public_diagnostic(p.stderr) if p.returncode or capture_success_tail else ''}


def must(argv:list[str], cwd:Path,*,timeout:int=240, env:dict[str,str]|None=None,
         capture_success_tail:bool=False)->dict[str,Any]:
    row=run(argv,cwd,timeout=timeout,extra_env=env,capture_success_tail=capture_success_tail)
    if row['code']:
        # Console-rich Devtool diagnostics are often on stdout, not stderr.
        # Reporting only stderr previously hid the actual native failures.
        raise RuntimeError('NATIVE_WORKLOAD_FAILED code='+str(row['code'])+
            ' command='+str([Path(argv[0]).name,*argv[1:2]])+
            ' stdout_sha256='+row['stdout_sha256']+' stderr_sha256='+row['stderr_sha256']+
            ' stdout_tail='+row['stdout_tail'][-1400:]+ ' stderr_tail='+row['stderr_tail'][-1400:])
    return row


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
    return must(argv,SOURCE,timeout=timeout,env=_devtool_child_environment())


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
    go=fixture(base,'go','go',{'go.mod':'module example.org/fc08\n\ngo 1.23\n','sample.go':'package fc08\nfunc V() int {return 8}\n','sample_test.go':'package fc08\nimport "testing"\nfunc TestV(t *testing.T){if V()!=8 {t.Fatal("incorrect")}}\n'})
    data['go']={'runner':'go','build':devtool(go,'build',timeout=360)['stdout_sha256'],'test':devtool(go,'test',timeout=360)['stdout_sha256']}
    node=fixture(base,'node','node',{'package.json':'{"name":"fc08-native","private":true,"version":"1.0.0","scripts":{"build":"node build.cjs","test":"node test.cjs"}}\n','build.cjs':"require('fs').writeFileSync('build.txt','real-build\\n');\n",'test.cjs':"const fs=require('fs');if(fs.readFileSync('build.txt','utf8')!=='real-build\\n')process.exit(8);\n"})
    data['node']={'runner':'node','build':devtool(node,'build',timeout=360)['stdout_sha256'],'test':devtool(node,'test',timeout=360)['stdout_sha256']}
    rust=fixture(base,'rust','rust',{'Cargo.toml':'[package]\nname="fc08_rust"\nversion="0.1.0"\nedition="2021"\n','src/lib.rs':'pub fn value()->i32{8}\n#[cfg(test)] mod tests{#[test] fn work(){assert_eq!(super::value(),8);}}\n'})
    must(['cargo','generate-lockfile','--offline'],rust,timeout=100)
    data['rust']={'runner':'rust','build':devtool(rust,'build',timeout=400)['stdout_sha256'],'test':devtool(rust,'test',timeout=400)['stdout_sha256']}
    maven=fixture(base,'maven','maven',{'pom.xml':'''<project xmlns="http://maven.apache.org/POM/4.0.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd"><modelVersion>4.0.0</modelVersion><groupId>org.fc08</groupId><artifactId>fc08</artifactId><version>1.0.0</version><properties><maven.compiler.release>17</maven.compiler.release></properties><build><plugins><plugin><groupId>org.apache.maven.plugins</groupId><artifactId>maven-compiler-plugin</artifactId><version>3.13.0</version></plugin><plugin><groupId>org.apache.maven.plugins</groupId><artifactId>maven-surefire-plugin</artifactId><version>3.5.2</version></plugin></plugins></build><dependencies><dependency><groupId>org.junit.jupiter</groupId><artifactId>junit-jupiter</artifactId><version>5.11.4</version><scope>test</scope></dependency></dependencies></project>''',
       'src/main/java/org/fc08/Smoke.java':'package org.fc08; public class Smoke { public static int value(){return 8;} }\n',
       'src/test/java/org/fc08/SmokeTest.java':'package org.fc08; import org.junit.jupiter.api.Test; import static org.junit.jupiter.api.Assertions.assertEquals; public class SmokeTest { @Test public void test() { assertEquals(8,Smoke.value()); }}\n'})
    data['maven']={'runner':'maven','build':devtool(maven,'build',timeout=600)['stdout_sha256'],'test':devtool(maven,'test',timeout=600)['stdout_sha256']}
    reports=list(maven.rglob('TEST-*.xml'))
    if not reports:raise RuntimeError('NO_REAL_MAVEN_SUREFIRE_XML')
    data['maven']['report_sha256']=sha_file(reports[0])
    data['cmake']=build_cmake(base,'cmake')
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



def validate_dotnet_test_evidence(results:Path, test:dict[str,Any])->dict[str,str]:
    """Require actual executed passing VSTest tests and genuine Coverlet XML.

    A zero-test ``dotnet test`` can return 0 and create no artifacts. Never
    regard that exit code alone as qualification; on failure include only the
    bounded, sanitized diagnostics saved specifically for this command.
    """
    trx=sorted(results.rglob('*.trx')) if results.is_dir() else []
    cov=sorted(results.rglob('coverage.cobertura.xml')) if results.is_dir() else []
    if len(trx)!=1 or len(cov)!=1:
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
    try:
        coverage=ET.parse(cov[0]).getroot()
        if coverage.tag!='coverage' or int(coverage.get('lines-valid','0'))<1:
            raise ValueError('missing real covered source')
    except (OSError,ET.ParseError,ValueError) as exc:
        raise RuntimeError('INVALID_OR_EMPTY_COVERAGE') from exc
    return {'result':test['stdout_sha256'],'trx':sha_file(trx[0]),'coverage':sha_file(cov[0])}

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
    must(['dotnet','new','sln','-n','FC08'],repo,timeout=120)
    must(['dotnet','sln','FC08.sln','add',str(lib),str(checks)],repo,timeout=90)
    # Initially generate committed lockfiles in disposable fixture before
    # requiring locked restore. Real cold and warm restores share one cache path.
    must(['dotnet','restore','FC08.sln','--use-lock-file'],repo,timeout=900)
    locks=list(repo.rglob('packages.lock.json'))
    if len(locks)!=2:raise RuntimeError('MISSING_DOTNET_LOCKFILES')
    must(['git','add','.'],repo)
    must(['git','-c','user.name=FC08 Fixture','-c','user.email=fc08@example.invalid','commit','-qm','locked graph'],repo)
    pkg=base/'cold-nuget-cache'
    env={'NUGET_PACKAGES':str(pkg),'DOTNET_CLI_TELEMETRY_OPTOUT':'1'}
    data={}
    data['dotnet-cold-restore']={'result':must(['dotnet','restore','FC08.sln','--locked-mode','--force'],repo,timeout=900,env=env)['stdout_sha256'],'locks':[sha_file(x) for x in locks]}
    data['dotnet-warm-restore']={'result':must(['dotnet','restore','FC08.sln','--locked-mode'],repo,timeout=900,env=env)['stdout_sha256']}
    data['dotnet-build']={'result':devtool(repo,'build',timeout=900,solution='FC08.sln')['stdout_sha256']}
    test=must(['dotnet','test','Checks/Checks.csproj','-c','Release',
        '--settings','fc08.runsettings','--logger','trx;LogFileName=fc08.trx',
        '--collect','XPlat Code Coverage','--results-directory',str(base/'test-results')],
        repo,timeout=900,env=env,capture_success_tail=True)
    data['dotnet-trx-coverage']=validate_dotnet_test_evidence(base/'test-results',test)
    packdir=base/'packs'
    must(['dotnet','pack',str(lib),'-c','Release','--no-restore','-o',str(packdir)],repo,timeout=600,env=env)
    packages=list(packdir.glob('*.nupkg'))
    if len(packages)!=1:raise RuntimeError('NO_REAL_NUPKG')
    data['dotnet-pack']={'sha256':sha_file(packages[0])}
    publ=base/'publish'
    must(['dotnet','publish',str(lib),'-c','Release','--no-restore','-o',str(publ)],repo,timeout=600,env=env)
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
    return {'immutable-source':{'source_verified':True},'negative-exit-propagation':fail_negative(base),
            'dag-fanout':{'required_jobs':['github-actions-dag','linux-polyglot','windows-dotnet-msvc','macos-native'],
                          'status':'FANOUT_SCHEDULED_NOT_YET_AUTHENTICATED'}}


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
