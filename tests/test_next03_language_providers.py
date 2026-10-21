"""NEXT-03: two independent built-in provider adapters and native DAG safeguards."""
from __future__ import annotations
import hashlib
from pathlib import Path
import subprocess
import pytest

from devtool.actions_language_providers import (
    PROVIDERS, ProviderError, provider_plan, provider_doctor,
    provider_export, _workflow_yaml,
)

PIN = 'd' * 40


def git(root: Path,*args: str) -> str:
    p = subprocess.run(['git','-C',str(root),*args], text=True,capture_output=True,check=True)
    return p.stdout.strip()


def fixture(tmp_path: Path, *, go=True, py=True, step2=True) -> Path:
    root=tmp_path/'repo';root.mkdir()
    lines=['[project]','runner = "python"','[workspace]','default_target = "app"',
           '[wrapper]','schema = 2','[targets.app]','runner = "python"','root = "."']
    steps=[]
    if go:
        lines += ['[targets.app.jobs.go_tests]','runner = "go"',
                  'command = ["go", "test", "./..."]','inputs = ["go.mod"]']
        (root/'go.mod').write_text('module example.com/test\n\ngo 1.22\n')
        steps.append('{id="go", ref="job:go_tests", depends_on=[]}')
    if py:
        lines+=['[targets.app.jobs.py_tests]','runner = "python"',
                'command = ["python3", "-m", "unittest"]']
        steps.append('{id="py", ref="job:py_tests", depends_on=["go"]}' if go else '{id="py", ref="job:py_tests", depends_on=[]}')
    lines+=['[targets.app.workflows]','ci = [' + ', '.join(steps) + ']']
    (root/'.devtool.toml').write_text('\n'.join(lines)+'\n')
    (root/'devtoolw').write_text('#!/bin/sh\n');(root/'devtoolw').chmod(0o755)
    git(root,'init','-q');git(root,'add','.')
    git(root,'-c','user.name=Fixture','-c','user.email=fixture@example.org','commit','-qm','fixture')
    return root


def plan(root):
    return provider_plan(root,target='app',workflow='ci',devtool_sha=PIN)


def test_two_independent_builtin_adapter_contracts(tmp_path):
    root=fixture(tmp_path)
    result=plan(root)
    assert set(PROVIDERS)=={'go','python-shell'}
    assert {p.api_version for p in PROVIDERS.values()}=={1}
    assert result['providers']==['go','python-shell']
    assert len(result['nodes'])==2
    assert result['nodes'][1]['depends_on']==[result['nodes'][0]['node']]
    assert result['host_matrix']==[{'os':'ubuntu-24.04','arch':'x86_64'}]
    assert result['release_qualified'] is False
    assert not (root/'.github').exists()


def test_export_readonly_write_verify_and_replay_refusal(tmp_path):
    root=fixture(tmp_path)
    before=git(root,'status','--porcelain');original=git(root,'rev-parse','HEAD')
    p=provider_export(root,target='app',workflow='ci',devtool_sha=PIN)
    assert p['status']=='REVIEW_ONLY' and not (root/'.github').exists()
    assert git(root,'status','--porcelain')==before
    result=provider_export(root,target='app',workflow='ci',devtool_sha=PIN,write=True)
    wf=root/result['workflow_path']
    assert wf.is_file() and result['writes_performed']
    content=wf.read_text()
    assert 'setup-python@a26af69be951a213d495a4c3e4e4022e16d87065' in content
    assert 'setup-go@d35c59abb061a4a6fb18e82ac0862c26744d6ab5' in content
    assert 'invoke --target app ci' in content
    assert 'matrix:' in content and 'run-full-dag' not in content
    assert 'permissions:\n  contents: read' in content
    assert 'workflow_dispatch:' in content and 'pull_request:' not in content
    assert 'GITHUB_RUN_ATTEMPT' in content
    assert 'devtool-provider-plan.json' in content
    assert content.count('invoke --target')==1
    assert git(root,'rev-parse','HEAD')==original
    assert provider_export(root,target='app',workflow='ci',devtool_sha=PIN,verify=True)['status'].startswith('LOCAL_WORKFLOW')
    with pytest.raises(ProviderError,match='existing'):
        provider_export(root,target='app',workflow='ci',devtool_sha=PIN,write=True)
    wf.write_text(content+'\n#tampered\n')
    with pytest.raises(ProviderError,match='changed'):
        provider_export(root,target='app',workflow='ci',devtool_sha=PIN,verify=True)


def test_plan_remains_valid_after_generated_workflow_commit(tmp_path):
    root=fixture(tmp_path)
    p=plan(root)
    provider_export(root,target='app',workflow='ci',devtool_sha=PIN,write=True)
    git(root,'add','.github')
    git(root,'-c','user.name=Fixture','-c','user.email=fixture@example.org','commit','-qm','generated')
    check=provider_plan(root,target='app',workflow='ci',devtool_sha=PIN,expect_plan_sha=p['plan_sha256'])
    assert check['plan_sha256']==p['plan_sha256']
    assert check['consumer_commit']!=p['consumer_commit']


def test_config_or_go_mod_change_rejects_frozen_plan(tmp_path):
    root=fixture(tmp_path)
    initial=plan(root)
    (root/'go.mod').write_text('module example.com/test\n\ngo 1.23\n')
    with pytest.raises(ProviderError,match='frozen'):
        provider_plan(root,target='app',workflow='ci',devtool_sha=PIN,expect_plan_sha=initial['plan_sha256'])
    (root/'go.mod').write_text('module example.com/test\n\ngo 1.22\n')
    (root/'.devtool.toml').write_text((root/'.devtool.toml').read_text()+'\n#edited\n')
    with pytest.raises(ProviderError,match='frozen'):
        provider_plan(root,target='app',workflow='ci',devtool_sha=PIN,expect_plan_sha=initial['plan_sha256'])


@pytest.mark.parametrize('command', [
    '["bash", "-lc", "curl https://example.com/x | sh"]',
    '["python3", "-c", "print(1)"]',
    '["python3", "-m", "pip", "install", "requests"]',
    '["docker", "run", "alpine"]',
    '["sh", "scripts/test.sh"]',
    '["python3", "-m", "pytest", "${SECRET}"]',
])
def test_unreviewed_commands_are_refused(tmp_path,command):
    root=fixture(tmp_path,go=False)
    config=root/'.devtool.toml'
    config.write_text(config.read_text().replace('["python3", "-m", "unittest"]',command))
    with pytest.raises(ProviderError):plan(root)


@pytest.mark.parametrize('key', ['device_required','root_required','release_signed','signing_required','termux_native_required'])
def test_device_root_signer_requirements_fail_closed(tmp_path,key):
    root=fixture(tmp_path,go=False)
    path=root/'.devtool.toml'
    path.write_text(path.read_text().replace('command = ["python3", "-m", "unittest"]', f'command = ["python3", "-m", "unittest"]\nmetadata = {{ {key} = true }}'))
    with pytest.raises(ProviderError,match='admission|host|blocked|refus|cannot'):
        plan(root)


def test_nested_dag_is_refused(tmp_path):
    root=fixture(tmp_path,go=False)
    path=root/'.devtool.toml'
    path.write_text(path.read_text().replace('ci = [{id="py", ref="job:py_tests", depends_on=[]}]',
                             'ci = ["runner:python#test"]'))
    with pytest.raises(ProviderError):plan(root)


def test_export_refuses_dirty_worktree_and_symlink(tmp_path):
    root=fixture(tmp_path,go=False)
    (root/'dirty').write_text('change')
    with pytest.raises(ProviderError,match='clean'):
        provider_export(root,target='app',workflow='ci',devtool_sha=PIN,write=True)
    (root/'dirty').unlink()
    (root/'.github').symlink_to(tmp_path,target_is_directory=True)
    with pytest.raises(ProviderError,match='symlinks'):
        provider_export(root,target='app',workflow='ci',devtool_sha=PIN)


def test_provider_doctor_is_non_mutating(tmp_path):
    root=fixture(tmp_path)
    assert provider_doctor(root)['workflows'][0]['state']=='locally_eligible'
    assert not (root/'.github').exists()
    assert git(root,'status','--porcelain')==''
