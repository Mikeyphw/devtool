"""DN03 source-contract regression; actual native .NET execution remains hosted."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import subprocess
import pytest

from devtool import actions_dn03_dotnet as dn03

PIN = 'a' * 40
GRAPH_SHA = 'b' * 64


def repo(tmp_path: Path) -> Path:
    root = tmp_path / 'consumer'
    root.mkdir()
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    subprocess.run(['git', '-C', str(root), 'config', 'user.name', 'Devtool Tests'], check=True)
    subprocess.run(['git', '-C', str(root), 'config', 'user.email', 'devtool-tests@example.invalid'], check=True)
    (root / 'README.md').write_text('fixture\n')
    subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(root), 'commit', '-qm', 'fixture'], check=True)
    return root


def fixture(monkeypatch, *, legacy=True):
    def compile_graph(root, *, target, workflow, devtool_sha, host_profiles, cache_locks):
        assert (target, workflow, devtool_sha) == ('app', 'ci', PIN)
        assert host_profiles == 'ubuntu-24.04-x64' and not cache_locks
        return {
            'graph_sha256': GRAPH_SHA, 'runtime_sha': PIN,
            'provider_plan_sha256': 'c' * 64,
            'consumer_commit': 'd' * 40, 'consumer_tree': 'e' * 40,
            'jobs': [
                {'id':'node-01','node':'ci:restore','provider':'dotnet',
                 'profile_id':'ubuntu-24.04-x64',
                 'toolchain_version':'10.0.100',
                 'dotnet_legacy_runtime':'6.0.36' if legacy else ''},
                {'id':'node-02','node':'ci:build','provider':'dotnet',
                 'profile_id':'ubuntu-24.04-x64',
                 'toolchain_version':'10.0.100',
                 'dotnet_legacy_runtime':'6.0.36' if legacy else ''},
            ],
        }
    def workflow_yaml(graph):
        legacy_setup = ('      - name: Legacy .NET 6 runtime compatibility (EOL)\n'
                        '        uses: actions/setup-dotnet@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n') if legacy else ''
        return ('name: original\non:\n  workflow_dispatch:\njobs:\n' +
                ''.join('  '+j['id']+':\n    runs-on: ubuntu-24.04\n    steps:\n'
                '      - name: Pinned .NET SDK\n'
                '        uses: actions/setup-dotnet@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n'
                + legacy_setup +
                '      - name: Acquire immutable Devtool runtime\n'
                '        run: echo acquired\n' for j in graph['jobs']))
    monkeypatch.setattr(dn03, 'compile_graph', compile_graph)
    monkeypatch.setattr(dn03, 'workflow_yaml', workflow_yaml)


def test_dn03_hardens_existing_native_dag_and_keeps_sha(monkeypatch, tmp_path):
    fixture(monkeypatch)
    root = repo(tmp_path)
    workflow, receipt = dn03.compile_dn03(root, target='app', workflow='ci',devtool_sha=PIN)
    assert workflow.count('DN03 verify .NET host') == 2
    assert workflow.count('DN03 preserve local host review') == 2
    assert workflow.count('Acquire immutable Devtool runtime') == 2
    assert workflow.count('Legacy .NET 6 runtime compatibility') == 2
    assert receipt['graph_sha256'] == GRAPH_SHA
    assert receipt['workflow_sha256'] == sha256(workflow.encode()).hexdigest()
    assert receipt['native_nodes'] == ['ci:restore','ci:build']
    assert not receipt['remote_authenticated'] and not receipt['release_qualified']
    assert receipt['actual_host_checked'] is False
    assert 'if: always()' in workflow
    assert 'Microsoft.NETCore.App' in workflow
    import yaml
    tree = yaml.safe_load(workflow)
    assert len(tree['jobs']) == 2
    assert all(len(row['steps']) == 5 for row in tree['jobs'].values())
    for row in tree['jobs'].values():
        script = next(x['run'] for x in row['steps'] if x.get('name','').startswith('DN03 verify'))
        assert subprocess.run(['bash','-n'],input=script,text=True,capture_output=True).returncode == 0


def test_dn03_modern_only_does_not_inject_eol_provisioner(monkeypatch,tmp_path):
    fixture(monkeypatch,legacy=False)
    workflow, _ = dn03.compile_dn03(repo(tmp_path),target='app',workflow='ci',devtool_sha=PIN)
    assert workflow.count('DN03 verify .NET host') == 2
    assert 'Legacy .NET 6 runtime compatibility' not in workflow
    assert 'DN03_EXPECTED_RUNTIME: \'\'' in workflow


def test_dn03_plan_write_verify_never_overwrites_or_auto_commits(monkeypatch,tmp_path):
    fixture(monkeypatch)
    root=repo(tmp_path)
    before=dn03._git(root,'rev-parse','HEAD')
    plan=dn03.export(root,target='app',workflow='ci',devtool_sha=PIN,mode='plan')
    assert not (root/plan['workflow_path']).exists()
    with pytest.raises(dn03.DN03Error,match='explicit --yes'):
        dn03.export(root,target='app',workflow='ci',devtool_sha=PIN,mode='write')
    wrote=dn03.export(root,target='app',workflow='ci',devtool_sha=PIN,mode='write',confirmed=True)
    assert wrote['wrote_files']
    assert dn03._git(root,'rev-parse','HEAD')==before
    assert (root/wrote['workflow_path']).is_file()
    assert (root/wrote['receipt_path']).is_file()
    assert dn03.export(root,target='app',workflow='ci',devtool_sha=PIN,mode='verify')['verified_files']
    # The generated workflow/receipt remain verifiable after they are committed.
    subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(root), 'commit', '-qm', 'generated workflow'], check=True)
    assert dn03.export(root,target='app',workflow='ci',devtool_sha=PIN,mode='verify')['verified_files']
    with pytest.raises(dn03.DN03Error,match='overwrite'):
        dn03.export(root,target='app',workflow='ci',devtool_sha=PIN,mode='write',confirmed=True)
    (root/wrote['workflow_path']).write_text('tampered\n')
    with pytest.raises(dn03.DN03Error,match='differs'):
        dn03.export(root,target='app',workflow='ci',devtool_sha=PIN,mode='verify')


def test_dn03_invalid_source_pin_and_output_paths(monkeypatch,tmp_path):
    fixture(monkeypatch)
    root=repo(tmp_path)
    with pytest.raises(dn03.DN03Error,match='immutable'):
        dn03.compile_dn03(root,target='app',workflow='ci',devtool_sha='main')
    with pytest.raises(dn03.DN03Error,match='bounded'):
        dn03.compile_dn03(root,target='../app',workflow='ci',devtool_sha=PIN)
    for path in ('../evil','/root/x','.github/../outside','a\\b','a//b'):
        with pytest.raises(dn03.DN03Error):
            dn03._path(root,path)
    (root/'.github').symlink_to(root, target_is_directory=True)
    with pytest.raises(dn03.DN03Error,match='symlink'):
        dn03._path(root,'.github/workflows/poison.yml')


def test_dn03_refuses_mixed_providers_and_unreviewed_runtime(monkeypatch,tmp_path):
    fixture(monkeypatch)
    root=repo(tmp_path)
    old=dn03.compile_graph
    def mixed(*args,**kwargs):
        graph=old(*args,**kwargs)
        graph['jobs'][0]['provider']='node'
        return graph
    monkeypatch.setattr(dn03,'compile_graph',mixed)
    with pytest.raises(dn03.DN03Error,match='.NET-only'):
        dn03.compile_dn03(root,target='app',workflow='ci',devtool_sha=PIN)
    def unsafe(*args,**kwargs):
        graph=old(*args,**kwargs)
        graph['jobs'][1]['dotnet_legacy_runtime']='6.0.35'
        return graph
    monkeypatch.setattr(dn03,'compile_graph',unsafe)
    with pytest.raises(dn03.DN03Error,match='unreviewed'):
        dn03.compile_dn03(root,target='app',workflow='ci',devtool_sha=PIN)


def test_dn03_harden_refuses_missing_or_duplicated_source_anchors():
    job={'id':'node-01','node':'build','provider':'dotnet','profile_id':'ubuntu-24.04-x64',
         'toolchain_version':'10.0.100','dotnet_legacy_runtime':''}
    for bad in ('jobs:\n  node-01:\n    steps: []\n',
                'jobs:\n  node-01:\n    steps:\n      - name: Pinned .NET SDK\n',
                'jobs:\n  node-01:\n    steps:\n      - name: Acquire immutable Devtool runtime\n'):
        with pytest.raises(dn03.DN03Error):
            dn03._harden(bad,[job],PIN,GRAPH_SHA)


def test_dn03_manifest_is_genuine_overlay_contract():
    # The package's native overlay manifest is checked structurally by the
    # user's installed Devtool, not copied into the target as a source file.
    import os
    assert dn03.UPLOADER == 'cf430e030ddbb5b0abf93d22962f4752f3646cd9'
    assert dn03.SHA.fullmatch(PIN)
    assert not dn03.SHA.fullmatch('main')
