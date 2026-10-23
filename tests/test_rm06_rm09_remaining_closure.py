"""Completion checks: public affected export, network-isolated managers, single provisioning."""
from pathlib import Path
from types import SimpleNamespace
import json
import subprocess

import pytest

from devtool.actions_language_providers import provider_export, ProviderError, provider_plan
from devtool.actions_rm08_projects import python_project_manifest,ProjectReviewError
from devtool.actions_rm08_native import provision_python_manager, native_command
from tests.test_rm08_projects import repo as make_repo,add_python_project,commit
from tests.test_rm06_rm09_consolidated_closure import git
from devtool.cli import build_parser
from tests.test_rm08_projects import PIN


def owned_workspace(root):
    root.mkdir(parents=True)
    git(root,'init','-q')
    for project in ('one','two'):
        (root/project).mkdir()
        (root/project/'file.txt').write_text(project)
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
    git(root,'add','.');git(root,'-c','user.name=Test','-c','user.email=test@example.org','commit','-qm','base')


def test_affected_export_public_parser_and_one_selected_workflow(tmp_path):
    root=tmp_path/'proj';owned_workspace(root)
    base=git(root,'rev-parse','HEAD')
    (root/'one/file.txt').write_text('changed')
    git(root,'add','.');git(root,'-c','user.name=Test','-c','user.email=test@example.org','commit','-qm','edit')
    command=['actions','provider-export','--affected','--workflow','ci',
             '--devtool-sha',PIN,'--mode','multi-job','--affected-base',base]
    ns=build_parser().parse_args(command)
    assert ns.provider_affected and ns.provider_affected_base==base and not ns.adopt_target
    args=dict(target='',workflow='ci',devtool_sha=PIN,mode='multi-job',affected=True,affected_base=base)
    review=provider_export(root,**args)
    assert review['selected_targets']==['one'] and not review['fallback_full']
    assert review['status']=='REVIEW_ONLY' and not (root/'.github').exists()
    with pytest.raises(ProviderError,match='requires --mode multi-job'):
        provider_export(root,**{**args,'mode':'single-job'})
    created=provider_export(root,**args,write=True)
    assert created['writes_performed'] and set(created['workflow_paths'])=={'one'}
    assert provider_export(root,**args,verify=True)['status'].endswith('VERIFIED_NOT_REMOTE')
    with pytest.raises(ProviderError,match='existing generated workflow'):
        provider_export(root,**args,write=True)
    # A shared/unowned path changes the selection to a full, unpruned review.
    (root/'.github/workflows/devtool-one-ci-providers-multi.yml').unlink()
    git(root,'reset','--hard','-q','HEAD')
    (root/'.devtool.toml').write_text((root/'.devtool.toml').read_text()+'\n# shared\n')
    git(root,'add','.');git(root,'-c','user.name=Test','-c','user.email=test@example.org','commit','-qm','shared')
    full=provider_export(root,**args)
    assert full['fallback_full'] and full['selected_targets']==['one','two']


def _manager_root(tmp_path, manager):
    root=make_repo(tmp_path,python=True);add_python_project(root)
    (root/'requirements.txt').unlink()
    (root/(manager+'.lock')).write_text('''lock-version = "2.1"
[[package]]
name = "setuptools"
version = "75.1.0"
files = [{file="setuptools.whl", hash="sha256:'''+ 'a'*64 +'''"}]
''')
    conf=root/'.devtool.toml'
    conf.write_text(conf.read_text().replace('command = ["python3", "-m", "unittest"]',
        'command = ["python3", "-m", "unittest"]\nmetadata = {ci_python_policy="reviewed", ci_python_manager_native=true, ci_python_manager_version="2.2.1"}'))
    commit(root)
    return root


@pytest.mark.parametrize('manager', ['poetry','pdm'])
def test_poetry_pdm_native_network_denial_and_fail_closed(tmp_path, monkeypatch, manager):
    root=_manager_root(tmp_path,manager)
    manifest=python_project_manifest(root,require_package=True,native_manager=True)
    assert manifest['manager']==manager and manifest['install_lock']==manager+'.lock'
    graph_plan=provider_plan(root,target='app',workflow='ci',devtool_sha=PIN)
    assert graph_plan['nodes'][0]['provider']['native_manager_version']=='2.2.1'
    assert native_command(manager,'2.2.1','python')[0:5]==['unshare','--user','--map-root-user','--net','--']
    monkeypatch.setenv('RUNNER_TEMP',str(tmp_path))
    from devtool import actions_rm08_native as mod
    # Exercise the supported-host branch even on Termux/Android: subprocess is mocked,
    # but project/lock review, refusal, and the network-denial command are real.
    monkeypatch.setattr(mod, '_native_namespace_host_supported', lambda: True)
    # Mock only the executable/namespace installation, not the tracked manifest proof.
    original=subprocess.run
    calls=[]
    def fake_run(cmd,*a,**kw):
        if isinstance(cmd,list) and cmd[0] in ('poetry','pdm') and cmd[1]=='--version':
            return SimpleNamespace(returncode=0,stdout=('Poetry (version 2.2.1)' if manager=='poetry' else 'PDM, version 2.2.1'),stderr='')
        if isinstance(cmd,list) and cmd[0]=='unshare':
            calls.append((cmd,kw))
            assert '--net' in cmd and kw['env']['PIP_NO_INDEX']=='1'
            assert kw['env']['POETRY_CACHE_DIR']==str(tmp_path/'devtool-python-wheel-store')
            assert kw['env']['PDM_CACHE_DIR']==str(tmp_path/'devtool-python-wheel-store')
            return SimpleNamespace(returncode=7,stdout='',stderr='namespace unavailable')
        return original(cmd,*a,**kw)
    monkeypatch.setattr(mod.subprocess,'run',fake_run)
    def venv_create(self, dest):
        p=Path(dest)/'bin';p.mkdir(parents=True)
        (p/'python').write_text('local test stub')
    monkeypatch.setattr(mod.venv.EnvBuilder,'create',venv_create)
    spec=SimpleNamespace(argv=['python3','-m','unittest'],env={},name='run')
    selected={'provider':'python-shell','id':'one','python_project':manifest,'python_native_version':'2.2.1'}
    with pytest.raises(ProviderError,match='offline provisioning failed'):
        provision_python_manager(root,selected,[spec])
    assert len(calls)==1
    assert calls[0][0][5:7]==[manager,'sync']
    assert (tmp_path/'devtool-rm08-one').is_dir()
    with pytest.raises(ProviderError,match='already exists'):
        provision_python_manager(root,selected,[spec])
    (root/(manager+'.lock')).write_text('tampered')
    with pytest.raises(ProviderError,match='Malformed manager lockfile|identity drifted'):
        provision_python_manager(root,{**selected,'id':'other'},[spec])



@pytest.mark.parametrize('manager', ['poetry','pdm'])
def test_unsupported_host_fails_before_poetry_pdm_execution(tmp_path, monkeypatch, manager):
    root=_manager_root(tmp_path,manager)
    manifest=python_project_manifest(root,require_package=True,native_manager=True)
    from devtool import actions_rm08_native as mod
    # Explicitly model Termux/Android regardless of the host running pytest.
    monkeypatch.setattr(mod, '_native_namespace_host_supported', lambda: False)
    calls=[]
    def must_not_probe(*a,**kw):
        calls.append((a,kw))
        raise AssertionError('unsupported host must refuse before external execution')
    monkeypatch.setattr(mod.subprocess,'run',must_not_probe)
    selected={'provider':'python-shell','id':'android-refusal',
              'python_project':manifest,'python_native_version':'2.2.1'}
    spec=SimpleNamespace(argv=['python3','-m','unittest'],env={},name='run')
    with pytest.raises(ProviderError,match='require supported Linux network namespace'):
        provision_python_manager(root,selected,[spec])
    assert not calls
    assert not (tmp_path/'devtool-rm08-android-refusal').exists()


def test_manager_version_and_remote_sources_are_rejected(tmp_path):
    root=_manager_root(tmp_path,'poetry')
    with pytest.raises(ProviderError,match='unsupported'):
        native_command('poetry','0.9.5','python')
    with pytest.raises(ProviderError,match='unsupported'):
        native_command('pdm','2.1000.1','python')
    project=root/'pyproject.toml'
    project.write_text(project.read_text()+'\n[tool.poetry]\nsource = [{url="https://evil.example/simple"}]\n')
    with pytest.raises(ProjectReviewError,match='source'):
        python_project_manifest(root,require_package=True,native_manager=True)


def test_uv_manager_executes_only_one_provisioning_site():
    import inspect
    from devtool.actions_graph_export import run_node
    # Structural guard accompanies the existing positive/negative run-node
    # regressions so a second post-job installation cannot sneak back in.
    body=inspect.getsource(run_node)
    assert body.count('provision_python_manager(')==1


def test_uv_success_runs_one_native_job_and_one_sync(tmp_path,monkeypatch):
    from devtool.actions_graph_export import compile_graph, run_node
    from devtool import runners
    parent=tmp_path/'src';parent.mkdir()
    root=make_repo(parent,python=True);add_python_project(root)
    (root/'requirements.txt').unlink()
    (root/'uv.lock').write_text('''version = 1
[[package]]
name = "demo-pkg"
version = "1.0.0"
source = { editable = "." }
[[package]]
name = "build"
version = "1.2.2"
source = { registry = "https://pypi.org/simple" }
wheels = [{hash="sha256:'''+ 'a'*64 +'''"}]
''')
    conf=root/'.devtool.toml'
    conf.write_text(conf.read_text().replace('command = ["python3", "-m", "unittest"]',
       'command = ["python3", "-m", "unittest"]\nmetadata = {ci_python_policy="reviewed", ci_python_manager_native=true, ci_python_manager_version="0.9.5"}'))
    commit(root)
    graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
    monkeypatch.setenv('GITHUB_SHA',git(root,'rev-parse','HEAD'))
    monkeypatch.setenv('GITHUB_RUN_ID','321')
    monkeypatch.setenv('GITHUB_RUN_ATTEMPT','1')
    monkeypatch.setenv('RUNNER_TEMP',str(tmp_path))
    observed={'sync':0,'job':0}
    original=subprocess.run
    def fake_run(cmd,*a,**kw):
        if isinstance(cmd,list) and cmd==['uv','--version']:
            return SimpleNamespace(returncode=0,stdout='uv 0.9.5\n',stderr='')
        if isinstance(cmd,list) and cmd[:4]==['uv','sync','--frozen','--offline']:
            observed['sync']+=1
            location=Path(kw['env']['UV_PROJECT_ENVIRONMENT'])/'bin'
            location.mkdir(parents=True);(location/'python').write_text('fixture executable')
            return SimpleNamespace(returncode=0,stdout='',stderr='')
        return original(cmd,*a,**kw)
    monkeypatch.setattr(subprocess,'run',fake_run)
    def native_run(config,specs,**kw):
        observed['job']+=1
        assert len(specs)==1 and specs[0].argv[0].endswith('/bin/python')
        return [SimpleNamespace(returncode=0,skipped=False)],None
    monkeypatch.setattr(runners,'run_specs',native_run)
    receipt=run_node(root,target='app',workflow='ci',devtool_sha=PIN,
        graph_sha=graph['graph_sha256'],job_id=graph['jobs'][0]['id'],
        inputs=tmp_path/'inputs',outputs=tmp_path/'outputs')
    assert receipt['status']=='LOCAL_JOB_SUCCEEDED'
    assert observed=={'sync':1,'job':1}
