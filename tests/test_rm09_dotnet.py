"""RM-09 behavior audit: static graph safety, hosted plan, TRX, NuGet bytes."""
import json
from pathlib import Path
import subprocess
import zipfile
import pytest
from devtool.actions_rm09_dotnet import solution_graph, inspect_dotnet_job, merge_trx, verify_nupkg, DotNetError
from devtool.actions_language_providers import provider_plan, provider_export, ProviderError
from devtool.actions_graph_export import compile_graph, workflow_yaml
from tests.test_next03_language_providers import PIN


def git(root,*args):
    return subprocess.run(['git','-C',str(root),*args],text=True,capture_output=True,check=True).stdout.strip()


def dotnet_repo(tmp_path, *, windows=False, two=True):
    r=tmp_path/'repo';r.mkdir()
    (r/'global.json').write_text('{"sdk":{"version":"8.0.416","rollForward":"disable"}}')
    for project in ('Lib','App') if two else ('App',):
        d=r/project;d.mkdir()
        (d/'packages.lock.json').write_text('{"version":1,"dependencies":{"net8.0":{}}}')
        tag='net8.0-windows' if windows and project=='App' else 'net8.0'
        ref='<ItemGroup><ProjectReference Include="../Lib/Lib.csproj" /></ItemGroup>' if two and project=='App' else ''
        (d/(project+'.csproj')).write_text(f'<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>{tag}</TargetFramework></PropertyGroup>{ref}</Project>')
    sln='Microsoft Visual Studio Solution File, Format Version 12.00\n' + ''.join(
        f'Project("{{00000000-0000-0000-0000-000000000000}}") = "{p}", "{p}/{p}.csproj", "{{00000000-0000-0000-0000-000000000000}}"\nEndProject\n'
        for p in (('Lib','App') if two else ('App',)))
    (r/'All.sln').write_text(sln)
    (r/'.devtool.toml').write_text('''[project]
runner = "python"
[workspace]
default_target = "app"
[wrapper]
schema = 2
[targets.app]
runner = "python"
root = "."
[targets.app.jobs.restore]
runner = "command"
command = ["dotnet","restore","All.sln","--locked-mode"]
[targets.app.jobs.build]
runner = "command"
command = ["dotnet","build","All.sln","--configuration","Release","--no-restore"]
[targets.app.workflows]
ci = [{id="restore",ref="job:restore",depends_on=[]},{id="build",ref="job:build",depends_on=["restore"]}]
''')
    (r/'devtoolw').write_text('#!/bin/sh\n')
    git(r,'init','-q');git(r,'add','.');git(r,'-c','user.name=Test','-c','user.email=test@example.org','commit','-qm','fixture')
    return r


def test_solution_project_topological_graph_and_two_solution_override(tmp_path):
    r=dotnet_repo(tmp_path)
    graph=solution_graph(r,selected='All.sln')
    assert graph['topological_projects']==['Lib/Lib.csproj','App/App.csproj']
    assert graph['edges']['App/App.csproj']==['Lib/Lib.csproj']
    (r/'Lib.slnx').write_text('<Solution><Project Path="Lib/Lib.csproj" /></Solution>')
    git(r,'add','.');git(r,'-c','user.name=Test','-c','user.email=test@example.org','commit','-qm','second')
    graph=solution_graph(r,selected='Lib.slnx')
    assert graph['solutions']['Lib.slnx']==['Lib/Lib.csproj']
    assert graph['sources']['All.sln']


def test_graph_fails_on_dependency_escape_cycle_missing_and_untracked(tmp_path):
    r=dotnet_repo(tmp_path)
    p=r/'App/App.csproj'
    p.write_text(p.read_text().replace('../Lib/Lib.csproj','../../../outside.csproj'))
    with pytest.raises(DotNetError,match='escapes'):solution_graph(r,selected='All.sln')
    p.write_text(p.read_text().replace('../../../outside.csproj','../Lib/Lib.csproj'))
    q=r/'Lib/Lib.csproj'
    q.write_text(q.read_text().replace('</Project>','<ItemGroup><ProjectReference Include="../App/App.csproj" /></ItemGroup></Project>'))
    with pytest.raises(DotNetError,match='cycle'):solution_graph(r,selected='All.sln')
    q.write_text(q.read_text().replace('<ItemGroup><ProjectReference Include="../App/App.csproj" /></ItemGroup>',''))
    (r/'App/packages.lock.json').unlink()
    with pytest.raises(DotNetError,match='missing'):solution_graph(r,selected='All.sln')


def test_linux_host_refuses_windows_and_unreviewed_sdk(tmp_path):
    r=dotnet_repo(tmp_path,windows=True)
    assert solution_graph(r,selected='All.sln')['windows_only']
    with pytest.raises(DotNetError,match='Windows-only'):
        inspect_dotnet_job(r,['dotnet','build','All.sln','--no-restore'],{})
    with pytest.raises(ProviderError):provider_plan(r,target='app',workflow='ci',devtool_sha=PIN)
    (r/'global.json').write_text('{"sdk":{"version":"8.0.x"}}')
    with pytest.raises(DotNetError,match='fixed'):solution_graph(r,selected='All.sln')


def test_direct_native_provider_and_pinned_multi_job_setup(tmp_path):
    r=dotnet_repo(tmp_path)
    plan=provider_plan(r,target='app',workflow='ci',devtool_sha=PIN)
    assert plan['providers']==['dotnet']
    graph=compile_graph(r,target='app',workflow='ci',devtool_sha=PIN)
    assert len(graph['jobs'])==2 and graph['jobs'][1]['needs']==[graph['jobs'][0]['id']]
    yaml=workflow_yaml(graph)
    assert 'setup-dotnet@67a3573c9a986a3f9c594539f4ab511d57bb3ce9' in yaml
    assert "dotnet-version: '8.0.416'" in yaml
    assert yaml.count('actions/setup-dotnet@')==2
    with pytest.raises(ProviderError,match='multi-job'):
        provider_export(r,target='app',workflow='ci',devtool_sha=PIN)
    assert provider_export(r,target='app',workflow='ci',devtool_sha=PIN,mode='multi-job')['status']=='REVIEW_ONLY'
    assert git(r,'status','--porcelain')==''


def test_argv_bounded_and_rid_policy(tmp_path):
    r=dotnet_repo(tmp_path)
    base=['dotnet','test','All.sln','--no-restore','--logger','trx;LogFileName=results.trx','--results-directory','dist']
    assert inspect_dotnet_job(r,base,{})['command']=='test'
    with pytest.raises(DotNetError,match='incompatible'):
        inspect_dotnet_job(r,['dotnet','publish','All.sln','--no-restore','--output','dist/a','--runtime','win-x64'],{})
    with pytest.raises(DotNetError,match='unreviewed'):
        inspect_dotnet_job(r,base+['--property:Version=1.2.3'],{})
    with pytest.raises(DotNetError,match='locked'):
        inspect_dotnet_job(r,['dotnet','restore','All.sln'],{})


def trx(root,path,name,outcome='Passed',uid='1',total='1'):
    (root/path).parent.mkdir(parents=True,exist_ok=True)
    (root/path).write_text(f'<TestRun><Results><UnitTestResult testId="{uid}" testName="{name}" outcome="{outcome}" /></Results><ResultSummary><Counters total="{total}" /></ResultSummary></TestRun>')


def test_trx_merge_positive_failure_duplicate_and_bad_counts(tmp_path):
    trx(tmp_path,'dist/a.trx','A');trx(tmp_path,'dist/b.trx','B',uid='2')
    result=merge_trx(tmp_path,['dist/a.trx','dist/b.trx'],expected_project='App')
    assert result['tests']==2 and result['release_qualified'] is False
    trx(tmp_path,'dist/b.trx','B',uid='1')
    with pytest.raises(DotNetError,match='duplicate'):merge_trx(tmp_path,['dist/a.trx','dist/b.trx'],expected_project='App')
    trx(tmp_path,'dist/b.trx','B',outcome='Failed',uid='2')
    with pytest.raises(DotNetError,match='failed'):merge_trx(tmp_path,['dist/a.trx','dist/b.trx'],expected_project='App')
    trx(tmp_path,'dist/b.trx','B',uid='2',total='9')
    with pytest.raises(DotNetError,match='counter'):merge_trx(tmp_path,['dist/a.trx','dist/b.trx'],expected_project='App')


def test_nuget_real_zip_identity_rid_and_traversal(tmp_path):
    p=tmp_path/'App.1.2.3.nupkg'
    def build(members):
        with zipfile.ZipFile(p,'w') as z:
            z.writestr('App.nuspec','<package><metadata><id>App</id><version>1.2.3</version></metadata></package>')
            for k,v in members.items():z.writestr(k,v)
    build({'lib/net8.0/App.dll':b'bytes'})
    receipt=verify_nupkg(p,package_id='App',version='1.2.3',tfm='net8.0')
    assert receipt['bytes_verified'] and not receipt['signer_authenticated']
    with pytest.raises(DotNetError,match='missing'):verify_nupkg(p,package_id='App',version='1.2.3',tfm='net9.0')
    with pytest.raises(DotNetError,match='mismatch'):verify_nupkg(p,package_id='Wrong',version='1.2.3',tfm='net8.0')
    build({'runtimes/win-x64/lib/net8.0/App.dll':b'x'})
    with pytest.raises(DotNetError,match='missing'):verify_nupkg(p,package_id='App',version='1.2.3',tfm='net8.0',rid='linux-x64')
    build({'../evil':b'x','lib/net8.0/App.dll':b'bytes'})
    with pytest.raises(DotNetError,match='unsafe'):verify_nupkg(p,package_id='App',version='1.2.3',tfm='net8.0')


def test_native_dotnet_trx_node_verifies_before_artifact_transfer(tmp_path,monkeypatch):
    from devtool.actions_graph_export import run_node
    from types import SimpleNamespace
    from devtool import runners
    r=dotnet_repo(tmp_path)
    config=r/'.devtool.toml'; text=config.read_text()
    text=text.replace('[targets.app.workflows]', '''[targets.app.jobs.test]
runner = "command"
command = ["dotnet","test","All.sln","--no-restore","--logger","trx;LogFileName=results.trx","--results-directory","dist"]
outputs = ["dist/results.trx"]
[targets.app.workflows]''')
    text=text.replace('depends_on=["restore"]}]','depends_on=["restore"]},{id="test",ref="job:test",depends_on=["build"]}]')
    config.write_text(text)
    git(r,'add','.');git(r,'-c','user.name=Test','-c','user.email=test@example.org','commit','-qm','tests')
    graph=compile_graph(r,target='app',workflow='ci',devtool_sha=PIN)
    monkeypatch.setenv('GITHUB_SHA',git(r,'rev-parse','HEAD'))
    monkeypatch.setenv('GITHUB_RUN_ID','7101');monkeypatch.setenv('GITHUB_RUN_ATTEMPT','1')
    def direct(config,specs,**kw):
        if specs[0].argv[:2]==['dotnet','restore']:
            return [SimpleNamespace(returncode=0,skipped=False)],''
        assert specs[0].argv[:2]==['dotnet','test']
        trx(r,'dist/results.trx','sample',uid='55')
        return [SimpleNamespace(returncode=0,skipped=False)],''
    monkeypatch.setattr(runners,'run_specs',direct)
    result=run_node(r,target='app',workflow='ci',devtool_sha=PIN,graph_sha=graph['graph_sha256'],
                    job_id=graph['jobs'][-1]['id'],inputs=tmp_path/'input',outputs=tmp_path/'out')
    assert result['status']=='LOCAL_JOB_SUCCEEDED'
    assert (tmp_path/'out'/graph['jobs'][-1]['id']/'payload/dist/results.trx').is_file()


def test_dotnet_pack_output_contract_verifier_and_native_order(tmp_path):
    r=dotnet_repo(tmp_path)
    p=r/'App/App.csproj';p.write_text(p.read_text().replace('</PropertyGroup>','<Version>1.2.3</Version></PropertyGroup>'))
    cfg=r/'.devtool.toml';text=cfg.read_text()
    text=text.replace('[targets.app.workflows]', '''[targets.app.jobs.pack]
runner = "command"
command = ["dotnet","pack","App/App.csproj","--no-restore","--output","dist"]
outputs = ["dist/App.1.2.3.nupkg"]
[targets.app.workflows]''')
    text=text.replace('depends_on=["restore"]}]','depends_on=["restore"]},{id="pack",ref="job:pack",depends_on=["build"]}]')
    cfg.write_text(text)
    git(r,'add','.');git(r,'-c','user.name=Test','-c','user.email=test@example.org','commit','-qm','pack')
    with pytest.raises(ProviderError,match='native build dependency'):
        provider_plan(r,target='app',workflow='ci',devtool_sha=PIN)
    # A packaged project must have its own native restore/build nodes, not use
    # the other solution's outputs as implicit proof of build success.
    text=cfg.read_text().replace('command = ["dotnet","build","All.sln","--configuration","Release","--no-restore"]','command = ["dotnet","build","App/App.csproj","--configuration","Release","--no-restore"]')
    cfg.write_text(text)
    with pytest.raises(ProviderError,match='native restore dependency'):
        provider_plan(r,target='app',workflow='ci',devtool_sha=PIN)
    text=cfg.read_text().replace('command = ["dotnet","restore","All.sln","--locked-mode"]','command = ["dotnet","restore","App/App.csproj","--locked-mode"]')
    cfg.write_text(text)
    plan=provider_plan(r,target='app',workflow='ci',devtool_sha=PIN)
    assert plan['nodes'][-1]['provider']['package']['version']=='1.2.3'


def test_native_dotnet_pack_validates_nuget_bytes_before_staging(tmp_path,monkeypatch):
    from devtool.actions_graph_export import run_node
    from devtool import runners
    from types import SimpleNamespace
    r=dotnet_repo(tmp_path)
    p=r/'App/App.csproj';p.write_text(p.read_text().replace('</PropertyGroup>','<Version>1.2.3</Version></PropertyGroup>'))
    cfg=r/'.devtool.toml';txt=cfg.read_text()
    txt=txt.replace('"All.sln"','"App/App.csproj"')
    txt=txt.replace('[targets.app.workflows]','''[targets.app.jobs.pack]
runner = "command"
command = ["dotnet","pack","App/App.csproj","--no-restore","--output","dist"]
outputs = ["dist/App.1.2.3.nupkg"]
[targets.app.workflows]''')
    txt=txt.replace('depends_on=["restore"]}]','depends_on=["restore"]},{id="pack",ref="job:pack",depends_on=["build"]}]')
    cfg.write_text(txt)
    git(r,'add','.');git(r,'-c','user.name=Test','-c','user.email=test@example.org','commit','-qm','pack')
    graph=compile_graph(r,target='app',workflow='ci',devtool_sha=PIN)
    monkeypatch.setenv('GITHUB_SHA',git(r,'rev-parse','HEAD'))
    monkeypatch.setenv('GITHUB_RUN_ID','808');monkeypatch.setenv('GITHUB_RUN_ATTEMPT','1')
    bad=[True]
    def direct(config,specs,**kw):
        if specs[0].argv[:2]==['dotnet','restore']:
            return [SimpleNamespace(returncode=0,skipped=False)],''
        dest=r/'dist/App.1.2.3.nupkg';dest.parent.mkdir(exist_ok=True)
        with zipfile.ZipFile(dest,'w') as z:
            z.writestr('App.nuspec','<package><metadata><id>App</id><version>1.2.3</version></metadata></package>')
            z.writestr('lib/net9.0/App.dll' if bad[0] else 'lib/net8.0/App.dll',b'dll')
        return [SimpleNamespace(returncode=0,skipped=False)],''
    monkeypatch.setattr(runners,'run_specs',direct)
    kwargs={'target':'app','workflow':'ci','devtool_sha':PIN,'graph_sha':graph['graph_sha256'],
            'job_id':graph['jobs'][-1]['id'],'inputs':tmp_path/'input','outputs':tmp_path/'output'}
    with pytest.raises(ProviderError,match='NuGet target'):
        run_node(r,**kwargs)
    assert not (tmp_path/'output'/graph['jobs'][-1]['id']).exists()
    (r/'dist/App.1.2.3.nupkg').unlink();bad[0]=False
    assert run_node(r,**kwargs)['status']=='LOCAL_JOB_SUCCEEDED'
    assert (tmp_path/'output'/graph['jobs'][-1]['id']/'payload/dist/App.1.2.3.nupkg').is_file()


def test_two_solution_shared_restore_collision_requires_native_order(tmp_path):
    r=dotnet_repo(tmp_path)
    (r/'Lib.slnx').write_text('<Solution><Project Path="Lib/Lib.csproj" /></Solution>')
    cfg=r/'.devtool.toml';text=cfg.read_text()
    text=text.replace('[targets.app.workflows]','''[targets.app.jobs.restore_shared]
runner = "command"
command = ["dotnet","restore","Lib.slnx","--locked-mode"]
[targets.app.workflows]''')
    text=text.replace('depends_on=["restore"]}]','depends_on=["restore"]},{id="shared",ref="job:restore_shared",depends_on=[]}]')
    cfg.write_text(text)
    git(r,'add','.');git(r,'-c','user.name=Test','-c','user.email=test@example.org','commit','-qm','two-solutions')
    with pytest.raises(ProviderError,match='overlapping NuGet restore'):
        provider_plan(r,target='app',workflow='ci',devtool_sha=PIN)
    cfg.write_text(text.replace('ref="job:restore_shared",depends_on=[]','ref="job:restore_shared",depends_on=["restore"]'))
    plan=provider_plan(r,target='app',workflow='ci',devtool_sha=PIN)
    assert 'dotnet' in plan['providers'] and len(plan['nodes'])==3
