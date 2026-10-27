"""Promise-by-promise adversarial tests for the consolidated RM06–RM09 closure."""
import json
from pathlib import Path
import subprocess
import pytest

from devtool.actions_graph_export import compile_affected_graphs,compile_graph,workflow_yaml
from devtool.actions_language_providers import provider_plan,ProviderError
from devtool.actions_rm09_dotnet import (DotNetError,inspect_dotnet_job,
    collect_dotnet_coverage,make_publish_receipt,verify_publish_receipt)
from devtool.actions_rm08_projects import python_project_manifest,ProjectReviewError
from tests.test_rm09_dotnet import dotnet_repo,git
from tests.test_rm08_projects import repo as make_repo,add_python_project,commit,PIN


def test_windows_dotnet_bridge_is_actual_execution_path_and_other_providers_blocked(tmp_path):
    # Parent directory must exist for the fixture root.
    parent=tmp_path/'win';parent.mkdir()
    dot=dotnet_repo(parent,windows=True,two=False)
    graph=compile_graph(dot,target='app',workflow='ci',devtool_sha=PIN,host_profiles='windows-2022-x64',cache_locks=True)
    assert len(graph['jobs'])==2
    assert all(job['runner']=='windows-2022' and job['provider']=='dotnet' for job in graph['jobs'])
    assert graph['jobs'][0]['cache_policy']['enabled']
    assert graph['jobs'][0]['cache_policy']['key']==compile_graph(dot,target='app',workflow='ci',devtool_sha=PIN,
            host_profiles='windows-2022-x64',cache_locks=True)['jobs'][0]['cache_policy']['key']
    y=workflow_yaml(graph)
    assert 'shell: bash' in y and 'Scripts/python.exe' in y and 'python -m venv' in y
    assert 'NUGET_PACKAGES: ${{ runner.temp }}/devtool-nuget-store' in y
    with pytest.raises(DotNetError,match='Windows-only'):
        inspect_dotnet_job(dot,['dotnet','build','All.sln','--no-restore'],{})
    other_parent=tmp_path/'other';other_parent.mkdir()
    other=make_repo(other_parent,nested=False)
    with pytest.raises(ProviderError,match='windows-2022-x64'):
        compile_graph(other,target='app',workflow='ci',devtool_sha=PIN,host_profiles='windows-2022-x64')


def test_windows_tfm_and_rid_validation_refuses_mixed_or_cross_host(tmp_path):
    root=dotnet_repo(tmp_path,two=False,windows=True)
    src=root/'App/App.csproj'
    src.write_text(src.read_text().replace('</PropertyGroup>',
        '<RuntimeIdentifiers>win-x64;linux-x64</RuntimeIdentifiers></PropertyGroup>'))
    git(root,'add','.');git(root,'-c','user.name=Test','-c','user.email=test@example.org','commit','-qm','runtime')
    base=['dotnet','publish','App/App.csproj','--no-restore','--output','dist/published']
    selection=inspect_dotnet_job(root,base+['--framework','net8.0-windows','--runtime','win-x64'],{},host_os='windows')
    assert selection['runtime']=='win-x64' and selection['framework']=='net8.0-windows'
    for args in (['--framework','net8.0'],['--framework','net9.0'],
                 ['--framework','net8.0-windows','--runtime','linux-x64']):
        with pytest.raises(DotNetError):
            inspect_dotnet_job(root,base+args,{},host_os='windows')
    with pytest.raises(DotNetError):
        inspect_dotnet_job(root,base+['--runtime','win-x64'],{},host_os='linux')


def test_dotnet_native_coverage_single_source_and_verified_node_bytes(tmp_path):
    dist=tmp_path/'dist';(dist/'01234567-89ab-cdef-0123-456789abcdef').mkdir(parents=True)
    candidate=dist/'01234567-89ab-cdef-0123-456789abcdef/coverage.cobertura.xml'
    candidate.write_text('<coverage line-rate="0.75"><packages><package><classes>'
                         '<class name="A" filename="a.cs" /></classes></package></packages></coverage>')
    record=collect_dotnet_coverage(tmp_path,'test-node')
    assert record['node']=='test-node' and record['line_rate']==0.75
    assert record['sha256'] and (dist/'coverage.cobertura.xml').read_bytes()==candidate.read_bytes()
    with pytest.raises(DotNetError,match='exists'):collect_dotnet_coverage(tmp_path,'test-node')
    (dist/'coverage.cobertura.xml').unlink()
    (dist/'another-collection').mkdir()
    (dist/'another-collection/coverage.cobertura.xml').write_text('<coverage line-rate="1"><class/></coverage>')
    with pytest.raises(DotNetError,match='unexpected|exactly one'):collect_dotnet_coverage(tmp_path,'test-node')


def test_dotnet_publish_receipt_detects_mutation_removal_and_unbounded_paths(tmp_path):
    dist=tmp_path/'dist';dist.mkdir(); (dist/'app.dll').write_bytes(b'ONE')
    record=make_publish_receipt(tmp_path,['dist/app.dll'],'App/App.csproj',{'App/App.csproj':'1'*64},'node-build')
    assert record['files']['dist/app.dll']['size']==3
    assert record['node']=='node-build' and verify_publish_receipt(tmp_path,record)
    (dist/'app.dll').write_bytes(b'TWO')
    with pytest.raises(DotNetError,match='mutated'):verify_publish_receipt(tmp_path,record)
    (dist/'app.dll').unlink()
    with pytest.raises(DotNetError,match='mutated'):verify_publish_receipt(tmp_path,record)
    with pytest.raises(DotNetError,match='dist'):
        make_publish_receipt(tmp_path,['App.csproj'],'App',{},'node')


def test_native_uv_review_requires_lock_pin_and_no_export(tmp_path):
    root=make_repo(tmp_path,python=True);add_python_project(root)
    (root/'requirements.txt').unlink()
    (root/'uv.lock').write_text('''version = 1
[[package]]
name = "demo-pkg"
version = "1.0.0"
source = { editable = "." }
[[package]]
name = "setuptools"
version = "75.1.0"
source = { registry = "https://pypi.org/simple" }
wheels = [{ url = "https://files.pythonhosted.org/setuptools.whl", hash = "sha256:'''+ 'a'*64 +'''" }]
''')
    cfg=root/'.devtool.toml'
    cfg.write_text(cfg.read_text().replace('command = ["python3", "-m", "unittest"]',
       'command = ["python3", "-m", "unittest"]\nmetadata = {ci_python_policy="reviewed", ci_python_manager_native=true, ci_python_manager_version="0.9.5"}'))
    commit(root)
    manifest=python_project_manifest(root,require_package=True,native_manager=True)
    assert manifest['manager']=='uv' and manifest['native_manager']
    assert manifest['install_lock']=='uv.lock'
    plan=provider_plan(root,target='app',workflow='ci',devtool_sha=PIN)
    assert plan['nodes'][0]['provider']['native_manager_version']=='0.9.5'
    graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
    assert graph['jobs'][0]['python_native_version']=='0.9.5'
    assert 'uv --version' in workflow_yaml(graph)
    assert 'requirements-ci.txt' not in workflow_yaml(graph)
    for replace in ('{ editable = "../escape" }','{ git = "https://evil.test/x" }'):
        (root/'uv.lock').write_text((root/'uv.lock').read_text().replace('{ editable = "." }', replace))
        with pytest.raises(ProjectReviewError,match='source'):
            python_project_manifest(root,require_package=True,native_manager=True)
        (root/'uv.lock').write_text(git(root,'show','HEAD:uv.lock'))
    (root/'uv.lock').write_text((root/'uv.lock').read_text().replace('a'*64,'b'*64))
    assert manifest['source_sha256']!=python_project_manifest(root,require_package=True,native_manager=True)['source_sha256']


def test_rm06_affected_ambiguous_unowned_and_empty_fall_back(tmp_path):
    root=tmp_path/'project';root.mkdir();git(root,'init','-q')
    for project in ['one','two']:
        (root/project).mkdir();(root/project/'file.txt').write_text(project)
    (root/'.devtool.toml').write_text('''[project]
runner="python"
[workspace]
default_target="one"
[wrapper]
schema=2
[targets.one]
runner="python"
root="one"
[targets.one.jobs.test]
runner="python"
command=["python3","-m","unittest"]
[targets.one.workflows]
ci=[{id="test",ref="job:test",depends_on=[]}]
[targets.two]
runner="python"
root="two"
[targets.two.jobs.test]
runner="python"
command=["python3","-m","unittest"]
[targets.two.workflows]
ci=[{id="test",ref="job:test",depends_on=[]}]
''')
    (root/'devtoolw').write_text('#!/bin/sh\n')
    git(root,'add','.');git(root,'-c','user.name=Test','-c','user.email=test@example.org','commit','-qm','fixture')
    def selection(paths):return compile_affected_graphs(root,workflow='ci',devtool_sha=PIN,changed_paths=paths)
    exclusive=selection(['one/file.txt'])
    assert exclusive['selected_targets']==['one'] and not exclusive['fallback_full']
    assert selection(['two/file.txt'])['selected_targets']==['two']
    for paths in (['.devtool.toml'],['one/file.txt','unowned.txt'],[],None):
        result=selection(paths)
        assert result['fallback_full'] and result['selected_targets']==['one','two']
    assert selection(['one/file.txt'])['selection_sha256']==exclusive['selection_sha256']


def test_native_uv_sync_failure_never_runs_job_or_publishes(tmp_path,monkeypatch):
    from devtool.actions_graph_export import run_node
    import subprocess as sp
    from types import SimpleNamespace
    from devtool import runners
    root=make_repo(tmp_path,python=True);add_python_project(root)
    (root/'requirements.txt').unlink()
    (root/'uv.lock').write_text('''version = 1
[[package]]
name="demo-pkg"
version="1.0.0"
source={editable="."}
[[package]]
name="build"
version="1.2.2"
source={registry="https://pypi.org/simple"}
wheels=[{hash="sha256:'''+ 'a'*64 +'''"}]
''')
    conf=root/'.devtool.toml'
    conf.write_text(conf.read_text().replace('command = ["python3", "-m", "unittest"]',
        'command = ["python3", "-m", "unittest"]\nmetadata = {ci_python_policy="reviewed", ci_python_manager_native=true, ci_python_manager_version="0.9.5"}'))
    commit(root)
    graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
    monkeypatch.setenv('GITHUB_SHA',git(root,'rev-parse','HEAD'))
    monkeypatch.setenv('GITHUB_RUN_ID','9001')
    monkeypatch.setenv('GITHUB_RUN_ATTEMPT','1')
    original=sp.run
    observed=[]
    def intercepted(args,*a,**kw):
        if args[:1]==['uv']:
            observed.append((list(args),kw))
            if args==['uv','--version']:
                return SimpleNamespace(returncode=0,stdout='uv 0.9.5\n',stderr='')
            assert args[:4]==['uv','sync','--frozen','--offline']
            assert kw['env']['UV_PYTHON_DOWNLOADS']=='never' and kw['env']['PIP_NO_INDEX']=='1'
            return SimpleNamespace(returncode=4,stdout='',stderr='offline wheel not present')
        return original(args,*a,**kw)
    monkeypatch.setattr(sp,'run',intercepted)
    def should_not_run(*a,**kw):
        pytest.fail('RM-08 native job ran after failed frozen/offline sync')
    monkeypatch.setattr(runners,'run_specs',should_not_run)
    with pytest.raises(ProviderError,match='frozen/offline uv sync failed'):
        run_node(root,target='app',workflow='ci',devtool_sha=PIN,graph_sha=graph['graph_sha256'],
                 job_id=graph['jobs'][0]['id'],inputs=tmp_path/'inputs',outputs=tmp_path/'outputs')
    assert len(observed)==2 and not (tmp_path/'outputs').exists()
