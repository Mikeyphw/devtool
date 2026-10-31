"""R32 SHA-pinned Python scripts and nested A09 Go source identity."""
from __future__ import annotations
import hashlib
from pathlib import Path
import subprocess
import pytest

from devtool.actions_language_providers import provider_plan, ProviderError
from devtool.actions_adoption import adopt, AdoptionError
from devtool.config import load_wrapper_project_config

PIN='c'*40


def git(root):
    subprocess.run(['git','init','-q',str(root)],check=True)
    subprocess.run(['git','-C',str(root),'add','.'],check=True)
    subprocess.run(['git','-C',str(root),'-c','user.name=Test','-c','user.email=t@example.org','commit','-qm','source'],check=True)


def python_repo(tmp_path,*,script='print("source check")\n',sha=None,args='["python3","tools/source_validate.py"]',outputs='[]'):
    root=tmp_path/'repo';root.mkdir(parents=True)
    (root/'tools').mkdir(); (root/'tools/source_validate.py').write_text(script)
    sha=sha or hashlib.sha256(script.encode()).hexdigest()
    (root/'.devtool.toml').write_text('''[project]
runner = "command"
[workspace]
default_target = "app"
[targets.app]
runner = "command"
[targets.app.jobs.source]
runner = "command"
command = '''+args+'''
outputs = '''+outputs+'''
metadata = {ci_toolchain="python-shell", ci_python_script_sha256="'''+sha+'''"}
[targets.app.workflows]
check = ["job:source"]
''')
    git(root)
    return root


def test_reviewed_tracked_python_script_admitted_and_digest_bound(tmp_path):
    root=python_repo(tmp_path)
    p=provider_plan(root,target='app',workflow='check',devtool_sha=PIN)
    assert p['nodes'][0]['provider']['python_script_review']['path']=='tools/source_validate.py'
    assert 'tools/source_validate.py' in p['reviewed_source_digests']
    (root/'tools/source_validate.py').write_text('print("tampered")\n')
    with pytest.raises(ProviderError,match='digest'):provider_plan(root,target='app',workflow='check',devtool_sha=PIN)


def test_python_script_rejects_unsafe_relative_path_untracked_and_mismatched_args(tmp_path):
    root=python_repo(tmp_path)
    source=(root/'.devtool.toml').read_text()
    (root/'.devtool.toml').write_text(source.replace('tools/source_validate.py','../tools/source_validate.py'))
    with pytest.raises(ProviderError,match='script requires'):provider_plan(root,target='app',workflow='check',devtool_sha=PIN)
    (root/'.devtool.toml').write_text(source.replace('["python3","tools/source_validate.py"]','["python3","tools/source_validate.py","--output","secrets"]'))
    with pytest.raises(ProviderError,match='arguments'):provider_plan(root,target='app',workflow='check',devtool_sha=PIN)
    (root/'.devtool.toml').write_text(source)
    subprocess.run(['git','-C',str(root),'rm','--cached','tools/source_validate.py'],check=True,capture_output=True)
    with pytest.raises(ProviderError,match='tracked'):provider_plan(root,target='app',workflow='check',devtool_sha=PIN)


def test_read_only_outputs_are_refused_by_config_parser(tmp_path):
    root=python_repo(tmp_path,outputs='["dist/out.json"]')
    source=(root/'.devtool.toml').read_text().replace('ci_toolchain="python-shell",','read_only=true, ci_toolchain="python-shell",')
    (root/'.devtool.toml').write_text(source)
    with pytest.raises(SystemExit,match='read_only=true contradicts declared outputs'):
        load_wrapper_project_config(str(root),None)


def go_repo(tmp_path):
    root=tmp_path/'repo';root.mkdir(parents=True)
    (root/'go').mkdir()
    (root/'go/go.mod').write_text('module example.com/nested\n\ngo 1.23\n')
    (root/'devtoolw').write_text('#!/bin/sh\n');(root/'devtoolw').chmod(0o755)
    (root/'.devtool.toml').write_text('''[project]
runner = "command"
[workspace]
default_target = "go"
[wrapper]
schema = 2
[targets.go]
runner = "command"
[targets.go.jobs.test]
runner = "command"
command = ["go", "-C", "go", "test", "./..."]
inputs = ["go/go.mod"]
metadata = {read_only=true, ci_toolchain="go"}
[targets.go.workflows]
check = ["job:test"]
''')
    git(root)
    return root


def test_a09_nested_go_setup_module_and_source(tmp_path):
    root=go_repo(tmp_path)
    result=adopt(root,target='go',workflow='check',devtool_sha=PIN)
    assert result['go_manifest']=='go/go.mod'
    assert result['ci_host_profile']=='go'
    assert result['selected_jobs'][0]['go_manifest']=='go/go.mod'
    assert result['remote_dispatch'] is False


def test_a09_nested_go_refuses_untracked_manifest(tmp_path):
    root=go_repo(tmp_path)
    subprocess.run(['git','-C',str(root),'rm','--cached','go/go.mod'],check=True,capture_output=True)
    with pytest.raises(AdoptionError,match='go.mod'):
        adopt(root,target='go',workflow='check',devtool_sha=PIN)


def test_a09_nested_go_shell_source_check_uses_declared_module(tmp_path):
    root=go_repo(tmp_path)
    cfg=root/'.devtool.toml'
    cfg.write_text(cfg.read_text().replace('["go", "-C", "go", "test", "./..."]',
                   '["bash", "scripts/check.sh"]'))
    (root/'scripts').mkdir()
    (root/'scripts/check.sh').write_text('cd go && go test ./...\n')
    subprocess.run(['git','-C',str(root),'add','.'],check=True)
    subprocess.run(['git','-C',str(root),'-c','user.name=Test','-c','user.email=t@example.org','commit','-qm','shell'],check=True)
    selected=adopt(root,target='go',workflow='check',devtool_sha=PIN)
    assert selected['go_manifest']=='go/go.mod'
