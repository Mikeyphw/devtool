"""FC08 HOST-R1R17: .NET must not inherit Visual C++ MSBuild Platform=x64."""
from __future__ import annotations
import importlib.util
from pathlib import Path
import subprocess
from unittest import mock
import pytest

HERE=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('fc08_r1r17', HERE/'tools/fc08_host_workloads.py')
assert SPEC is not None and SPEC.loader is not None
h=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(h)


def _proc_observer():
    observed=[]
    def fake_run(argv,**kwargs):
        observed.append((argv,kwargs['env'].copy()))
        return subprocess.CompletedProcess(argv,0,b'OK',b'')
    return observed,fake_run


def test_real_dotnet_commands_strip_case_insensitive_platform_and_configuration(tmp_path):
    observed, fake=_proc_observer()
    # Windows environment variable keys are case insensitive, but Linux
    # regression tests exercise both spellings to prevent leaky filtering.
    with mock.patch.dict(h.os.environ,{
        'Platform':'x64','platform':'x64','CONFIGURATION':'Debug',
        'VSCMD_ARG_TGT_ARCH':'x64','FC08_KEEP_ME':'yes'},clear=True), \
        mock.patch.object(h.subprocess,'run',side_effect=fake):
        h.dotnet_must(['dotnet','restore','FC08.sln'],tmp_path,
                      env={'NUGET_PACKAGES':'fixture-cache'})
    assert len(observed)==1
    args,env=observed[0]
    assert args[:2]==['dotnet','restore']
    assert not ({'platform','configuration'} & {name.casefold() for name in env})
    assert env['FC08_KEEP_ME']=='yes'
    assert env['VSCMD_ARG_TGT_ARCH']=='x64'
    assert env['NUGET_PACKAGES']=='fixture-cache'


def test_msbuild_scrub_is_opt_in_and_msvc_still_inherits_native_arch(tmp_path):
    observed, fake=_proc_observer()
    with mock.patch.dict(h.os.environ,{'Platform':'x64','Configuration':'Release','VSCMD_ARG_TGT_ARCH':'x64'},clear=True), \
        mock.patch.object(h.subprocess,'run',side_effect=fake):
        h.must(['cmake','--build','b'],tmp_path)
        h.run(['cl.exe','main.cpp'],tmp_path)
    assert len(observed)==2
    for _,env in observed:
        assert env['Platform']=='x64'
        assert env['Configuration']=='Release'
        assert env['VSCMD_ARG_TGT_ARCH']=='x64'


def test_devtool_dotnet_solution_child_strips_msbuild_conflicting_env(tmp_path):
    (tmp_path/'FC08.sln').write_text('fixture')
    observed, fake=_proc_observer()
    with mock.patch.dict(h.os.environ,{'Platform':'x64','Configuration':'Debug','VSCMD_ARG_TGT_ARCH':'x64'},clear=True), \
        mock.patch.object(h,'git',return_value='FC08.sln'), \
        mock.patch.object(h.subprocess,'run',side_effect=fake):
        h.devtool(tmp_path,'build',solution='FC08.sln')
    assert len(observed)==1
    args,env=observed[0]
    assert '--solution' in args and 'FC08.sln' in args
    assert not ({'platform','configuration'} & {name.casefold() for name in env})
    assert env['VSCMD_ARG_TGT_ARCH']=='x64'


def test_non_dotnet_child_does_not_inherit_scrubbing(tmp_path):
    observed, fake=_proc_observer()
    with mock.patch.dict(h.os.environ,{'Platform':'x64'},clear=True), \
        mock.patch.object(h.subprocess,'run',side_effect=fake):
        h.devtool(tmp_path,'test')
    assert observed[0][1]['Platform']=='x64'


def test_dotnet_only_wrapper_refuses_non_dotnet(tmp_path):
    with pytest.raises(RuntimeError,match='REFUSED_NON_DOTNET_SUBPROCESS'):
        h.dotnet_must(['cmake','--build','build'],tmp_path)


def test_all_real_windows_managed_calls_use_scoped_sanitizer():
    source=(HERE/'tools/fc08_host_workloads.py').read_text()
    windows=source.split('def windows(base:Path)',1)[1].split('\ndef macos(base:Path)',1)[0]
    assert windows.count("dotnet_must(['dotnet'")==8
    assert "must(['dotnet'" not in windows.replace('dotnet_must(', '')
    assert "devtool(repo,'build',timeout=900,solution='FC08.sln')" in windows
    assert "drop_msbuild_env=(solution is not None)" in source
