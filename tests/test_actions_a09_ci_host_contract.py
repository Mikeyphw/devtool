"""A09 host-admission regression: Go source jobs, never device or release jobs."""
from __future__ import annotations
import subprocess
from pathlib import Path
import pytest

from devtool.actions_adoption import AdoptionError, _yaml, adopt, _SETUP_GO_SHA

PIN='f40381625178dea16aad189626617e7967b3da41'

def repo(tmp_path: Path, *, args: str = '["bash", "scripts/devtool/check_native.sh"]',
         metadata: str = 'read_only = true, qualification = "go-source-only"',
         inputs: str = '["go.mod", "scripts/devtool/check_native.sh"]',
         runner: str = 'command', go_mod: bool = True,
         workflow: str = 'native = ["job:native"]') -> Path:
    root=tmp_path/'skybind';root.mkdir(parents=True)
    config=f'''[project]
runner = "command"
[workspace]
default_target = "skybind"
[wrapper]
schema = 2
[targets.skybind]
runner = "command"
root = "."
[targets.skybind.jobs.native]
runner = "{runner}"
command = {args}
inputs = {inputs}
metadata = {{ {metadata} }}
[targets.skybind.workflows]
{workflow}
'''
    (root/'.devtool.toml').write_text(config)
    if go_mod:(root/'go.mod').write_text('module example.com/skybind\n\ngo 1.23\n')
    scripts=root/'scripts/devtool';scripts.mkdir(parents=True)
    (scripts/'check_native.sh').write_text('go version\n')
    wrapper=root/'devtoolw';wrapper.write_text('#!/bin/sh\n');wrapper.chmod(0o755)
    subprocess.run(['git','init','-q',str(root)],check=True)
    subprocess.run(['git','-C',str(root),'add','.'],check=True)
    subprocess.run(['git','-C',str(root),'-c','user.name=A09', '-c','user.email=a09@example.test','commit','-qm','source'],check=True)
    return root

def test_go_native_review_and_preview_are_non_mutating(tmp_path):
    r=repo(tmp_path)
    result=adopt(r,target='skybind',workflow='native',devtool_sha=PIN)
    assert result['overall']=='PASS_ACTIONS_A03_REVIEW_ONLY'
    assert result['ci_host_profile']=='go'
    assert result['device_qualified'] is False and result['source_attested'] is False
    assert not (r/'.github').exists()

def test_go_native_write_and_verify_pinned_sdk(tmp_path):
    r=repo(tmp_path)
    result=adopt(r,target='skybind',workflow='native',devtool_sha=PIN,write=True)
    f=r/result['workflow_path']; t=f.read_text()
    assert result['ci_host_profile']=='go'
    assert f'uses: actions/setup-go@{_SETUP_GO_SHA}' in t
    assert 'go-version-file: go.mod' in t
    assert 'Verify Go host toolchain' in t
    assert t.index('Provision pinned Go SDK')<t.index('Run native Devtool DAG')
    assert PIN in t and 'permissions:\n  contents: read' in t
    assert adopt(r,target='skybind',workflow='native',devtool_sha=PIN,verify=True)['ci_host_profile']=='go'

def test_go_missing_or_symlinked_module_is_refused(tmp_path):
    r=repo(tmp_path,go_mod=False)
    with pytest.raises(AdoptionError,match='go.mod'):
        adopt(r,target='skybind',workflow='native',devtool_sha=PIN)
    (r/'go.mod').symlink_to(r/'scripts/devtool/check_native.sh')
    with pytest.raises(AdoptionError,match='go.mod'):
        adopt(r,target='skybind',workflow='native',devtool_sha=PIN)

def test_go_bad_directive_is_refused(tmp_path):
    r=repo(tmp_path)
    (r/'go.mod').write_text('module example.com/a\n\nreplace github.com/x => ./x\n')
    with pytest.raises(AdoptionError,match='go directive'):
        adopt(r,target='skybind',workflow='native',devtool_sha=PIN)

def test_device_root_signer_and_non_command_runner_fail_closed(tmp_path):
    for idx, (metadata, runner) in enumerate((
        ('read_only = true, device_required = true', 'command'),
        ('read_only = true, root_required = true', 'command'),
        ('read_only = true, release_signed = true', 'command'),
        ('read_only = true', 'android'),
    )):
        r=repo(tmp_path/str(idx),metadata=metadata,runner=runner)
        with pytest.raises(AdoptionError,match='device, root, signer or unsupported'):
            adopt(r,target='skybind',workflow='native',devtool_sha=PIN)

def test_opaque_command_and_privileged_executable_denied(tmp_path):
    for idx, cmd in enumerate(('["adb", "install", "x.apk"]','["docker", "run", "--rm", "img"]', '["custom", "arg"]')):
        r=repo(tmp_path/str(idx),args=cmd)
        with pytest.raises(AdoptionError,match='refuses privileged|cannot prove'):
            adopt(r,target='skybind',workflow='native',devtool_sha=PIN)

def test_go_requires_read_only_declaration(tmp_path):
    r=repo(tmp_path,metadata='area = "go-tests"')
    with pytest.raises(AdoptionError,match='read_only'):
        adopt(r,target='skybind',workflow='native',devtool_sha=PIN)

def test_unknown_ci_host_profile_cannot_be_injected():
    with pytest.raises(AdoptionError,match='profile'):
        _yaml('skybind','native',PIN,'$(touch /tmp/evil)')

def test_ordinary_python_dag_retains_existing_a03_environment(tmp_path):
    r=repo(tmp_path,args='["python3", "-m", "unittest"]',inputs='["tests/test_bootstrap.py"]',go_mod=False)
    result=adopt(r,target='skybind',workflow='native',devtool_sha=PIN)
    assert result['ci_host_profile']=='python-shell'
    text=_yaml('skybind','native',PIN)
    assert 'setup-go' not in text and 'go-version-file' not in text
