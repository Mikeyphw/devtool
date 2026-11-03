"""DN02: bounded CLI aliases, reviewed host environment and runtime attestations."""
from __future__ import annotations
import json
from types import SimpleNamespace
import pytest

from devtool.actions_rm09_dotnet import (
    DotNetError, inspect_dotnet_job, normalize_dotnet_argv, verify_dotnet_host,
)
from devtool.actions_language_providers import provider_plan, ProviderError
from devtool.actions_graph_export import compile_graph, workflow_yaml
from tests.test_rm09_dotnet import dotnet_repo, git
from tests.test_next03_language_providers import PIN


def _legacy_repo(tmp_path):
    root=dotnet_repo(tmp_path)
    for project in ('Lib','App'):
        path=root/project/(project+'.csproj')
        path.write_text(path.read_text().replace('<TargetFramework>net8.0</TargetFramework>',
                                                 '<TargetFramework>net6.0</TargetFramework>'))
        (root/project/'packages.lock.json').write_text(
            '{"version":1,"dependencies":{"net6.0":{}}}')
    (root/'global.json').write_text(json.dumps(
        {'sdk': {'version':'10.0.100','rollForward':'disable'}}))
    config=root/'.devtool.toml'
    text=config.read_text()
    text=text.replace('[targets.app.jobs.build]',
                      '[targets.app.jobs.restore.metadata]\nci_dotnet_allow_eol = true\n'
                      '[targets.app.jobs.build]')
    text=text.replace('[targets.app.workflows]',
                      '[targets.app.jobs.build.metadata]\nci_dotnet_allow_eol = true\n'
                      '[targets.app.workflows]')
    config.write_text(text)
    git(root,'add','.')
    git(root,'-c','user.name=Test','-c','user.email=test@example.org',
        'commit','-qm','DN02 legacy fixture')
    return root


def test_dn02_aliases_are_canonical_and_duplicate_rejected(tmp_path):
    root=dotnet_repo(tmp_path)
    literal=['dotnet','build','App/App.csproj','-c','Release','--no-restore']
    review=inspect_dotnet_job(root,literal,{})
    assert review['canonical_argv']==[
        'dotnet','build','App/App.csproj','--configuration','Release','--no-restore']
    assert normalize_dotnet_argv(literal)==review['canonical_argv']
    with pytest.raises(DotNetError,match='duplicate'):
        inspect_dotnet_job(root,literal+['--configuration','Debug'],{})
    with pytest.raises(DotNetError,match='unreviewed'):
        inspect_dotnet_job(root,literal+['-p:Version=99'],{})
    with pytest.raises(DotNetError):
        inspect_dotnet_job(root,['dotnet','build','App/App.csproj','-c','$(pwd)','--no-restore'],{})


def test_dn02_explicit_runtime_version_and_host_inheritance(tmp_path):
    root=_legacy_repo(tmp_path)
    declaration=inspect_dotnet_job(root,
        ['dotnet','build','All.sln','-c','Release','--no-restore'],
        {'ci_dotnet_allow_eol':True})
    assert declaration['version']=='10.0.100'
    assert declaration['legacy_runtime_version']=='6.0.36'
    assert declaration['required_runtime_families']==['6.0']
    assert declaration['eol_opt_in'] is True
    graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
    assert all(job['dotnet_legacy_runtime']=='6.0.36' for job in graph['jobs'])
    assert all(job['dotnet_canonical_argv'] for job in graph['jobs'])
    yaml=workflow_yaml(graph)
    assert yaml.count("dotnet-version: '10.0.100'")==2
    assert yaml.count("dotnet-version: '6.0.428'")==2
    assert yaml.count('Legacy .NET 6 runtime compatibility (EOL)')==2
    assert yaml.count('actions/setup-dotnet@26b0ec14cb23fa6904739307f278c14f94c95bf1')==4


def test_dn02_regular_dotnet_workflow_does_not_install_legacy_sdk(tmp_path):
    root=dotnet_repo(tmp_path)
    graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
    yaml=workflow_yaml(graph)
    assert "dotnet-version: '6.0.428'" not in yaml
    assert yaml.count('actions/setup-dotnet@')==2
    assert all(not job['dotnet_legacy_runtime'] for job in graph['jobs'])


def test_dn02_host_verifier_requires_exact_runtime_and_sdk(tmp_path,monkeypatch):
    seen=[]
    def fake_run(argv, **kwargs):
        seen.append(list(argv))
        if argv==['dotnet','--version']:
            return SimpleNamespace(returncode=0,stdout='10.0.100\n')
        if argv==['dotnet','--list-runtimes']:
            return SimpleNamespace(returncode=0,stdout=(
                'Microsoft.NETCore.App 6.0.36 [/dotnet/shared/Microsoft.NETCore.App]\n'
                'Microsoft.WindowsDesktop.App 6.0.36 [C:/dotnet/shared/Microsoft.WindowsDesktop.App]\n'))
        pytest.fail('unexpected command')
    monkeypatch.setattr('devtool.actions_rm09_dotnet.subprocess.run',fake_run)
    ok=verify_dotnet_host(tmp_path,sdk_version='10.0.100',
                          legacy_runtime_version='6.0.36',windows_desktop=True)
    assert ok['verified_runtimes']==[
        {'family':'Microsoft.NETCore.App','version':'6.0.36'},
        {'family':'Microsoft.WindowsDesktop.App','version':'6.0.36'}]
    assert not ok['authenticated_host'] and not ok['release_qualified']
    assert seen==[['dotnet','--version'],['dotnet','--list-runtimes']]
    with pytest.raises(DotNetError,match='unreviewed'):
        verify_dotnet_host(tmp_path,sdk_version='10.0.100',legacy_runtime_version='6.0.x')
    with pytest.raises(DotNetError,match='SDK differs'):
        verify_dotnet_host(tmp_path,sdk_version='9.0.101')
    def no_runtime(argv,**kwargs):
        if '--version' in argv:return SimpleNamespace(returncode=0,stdout='10.0.100\n')
        return SimpleNamespace(returncode=0,stdout='Microsoft.NETCore.App 6.0.35 [x]\n')
    monkeypatch.setattr('devtool.actions_rm09_dotnet.subprocess.run',no_runtime)
    with pytest.raises(DotNetError,match='missing exact legacy runtime'):
        verify_dotnet_host(tmp_path,sdk_version='10.0.100',legacy_runtime_version='6.0.36')


def test_dn02_reviewed_job_environment_only(tmp_path):
    root=dotnet_repo(tmp_path)
    config=root/'.devtool.toml'
    text=config.read_text()
    text=text.replace('[targets.app.jobs.build]',
                      '[targets.app.jobs.build]\nenv = {DOTNET_NOLOGO = "1"}')
    config.write_text(text)
    git(root,'add','.')
    git(root,'-c','user.name=Test','-c','user.email=test@example.org','commit','-qm','reviewed env')
    assert provider_plan(root,target='app',workflow='ci',devtool_sha=PIN)['providers']==['dotnet']
    for environment in ('{DOTNET_ROOT = "/tmp/override"}',
                        '{NUGET_PACKAGES = "/tmp/attacker"}',
                        '{DOTNET_NOLOGO = "0"}'):
        config.write_text(text.replace('{DOTNET_NOLOGO = "1"}',environment))
        git(root,'add','.')
        git(root,'-c','user.name=Test','-c','user.email=test@example.org','commit','-qm','host env denial')
        with pytest.raises(ProviderError,match='unreviewed .NET job environment'):
            provider_plan(root,target='app',workflow='ci',devtool_sha=PIN)
