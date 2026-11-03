"""DN03-R3: fail-closed native per-node re-verification and exporter contracts."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import pytest
import yaml

from devtool import actions_dn03_r3 as r3
from devtool import actions_dn03_r3_verify as verifier

DEVTOOL = 'a' * 40
GRAPH = 'b' * 64
PROVIDER = 'c' * 64
SOURCE = None


def _git(root: Path, *args: str) -> str:
    return subprocess.run(['git', '-C', str(root), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / 'repo'
    root.mkdir()
    _git(root, 'init', '-q')
    _git(root, 'config', 'user.name', 'R3 Tests')
    _git(root, 'config', 'user.email', 'r3-tests@example.invalid')
    (root / 'README.md').write_text('fixture\n', encoding='utf-8')
    _git(root, 'add', '.')
    _git(root, 'commit', '-qm', 'fixture')
    return root


def _graph(root: Path | None = None):
    return dict(graph_sha256=GRAPH, runtime_sha=DEVTOOL,
                provider_plan_sha256=PROVIDER,
                consumer_commit=_git(root, 'rev-parse', 'HEAD') if root else 'e' * 40,
                jobs=[
                    dict(id='node01', node='ci:restore', job='restore', provider='dotnet',
                         profile_id='ubuntu-24.04-x64', toolchain_version='10.0.100',
                         dotnet_legacy_runtime='6.0.36', outputs=['dist/a.txt']),
                    dict(id='node02', node='ci:test', job='test', provider='dotnet',
                         profile_id='ubuntu-24.04-x64', toolchain_version='10.0.100',
                         dotnet_legacy_runtime='', outputs=[]),
                ])


def _r2_yaml(g):
    parts=['name: DN03 fixture\non:\n  workflow_dispatch:\njobs:\n']
    for job in g['jobs']:
        parts.extend([
            f'  {job["id"]}:\n    runs-on: ubuntu-24.04\n    steps:\n',
            '      - name: DN03 verify .NET host and record local source-bound evidence\n',
            '        run: echo host\n',
            '      - name: Acquire immutable Devtool runtime\n',
            '        run: echo acquire\n',
            '      - name: Verify dependencies and run native target-local node\n',
            '        run: echo native\n',
        ])
        if job['outputs']:
            parts.extend([f'      - name: Upload {job["id"]} declared bytes (not authenticated)\n',
                          '        run: echo upload\n'])
        parts.extend(['      - name: Record local run attempt (not authentication)\n',
                      '        run: echo record\n'])
    return ''.join(parts)


def _fixture(monkeypatch, root: Path):
    g=_graph(root)
    text=_r2_yaml(g)
    receipt=dict(graph_sha256=GRAPH, provider_plan_sha256=PROVIDER,
                 devtool_sha=DEVTOOL, native_nodes=[j['node'] for j in g['jobs']],
                 workflow_sha256=hashlib.sha256(text.encode()).hexdigest())
    monkeypatch.setattr(r3.dn03, 'compile_dn03', lambda *a,**kw:(text,receipt))
    monkeypatch.setattr(r3.dn03, 'compile_graph', lambda *a,**kw:g)
    return g


def _native_context(root: Path, tmp_path: Path):
    temp=tmp_path / 'host'
    temp.mkdir()
    (temp/'rm06-output').mkdir()
    (temp/'rm06-evidence'/'node01').mkdir(parents=True)
    (temp/'dn03-host'/'node01').mkdir(parents=True)
    env=dict(GITHUB_ACTIONS='true', RUNNER_OS='Linux', RUNNER_ARCH='X64',
             GITHUB_SHA=_git(root, 'rev-parse','HEAD'),
             GITHUB_REPOSITORY='Mikeyphw/consumer',
             GITHUB_WORKSPACE=str(root), RUNNER_TEMP=str(temp),
             GITHUB_RUN_ID='70001', GITHUB_RUN_ATTEMPT='2')
    (temp/'rm06-evidence'/'node01'/'node-evidence.json').write_text('{"receipt":"test"}\n')
    (temp/'dn03-host'/'node01'/'host.json').write_text(json.dumps(dict(
        kind='devtool.dn03-host-review',source_sha=env['GITHUB_SHA'],
        devtool_sha=DEVTOOL,graph_sha256=GRAPH,node='node01',
        sdk='10.0.100',legacy_runtime='6.0.36',run_id=70001,
        run_attempt=2,hosted_authenticated=False,release_qualified=False,
        runtime_inventory_checked=True,runtime_receipt_sha256='9'*64)))
    return temp,env


def test_r3_inserts_post_native_and_pre_upload_steps(monkeypatch,tmp_path):
    root=_repo(tmp_path)
    _fixture(monkeypatch,root)
    y,rec=r3.compile_r3(root,target='app',workflow='ci',devtool_sha=DEVTOOL)
    data=yaml.safe_load(y)
    assert len(data['jobs'])==2
    for job in data['jobs'].values():
        steps=job['steps']
        names=[x['name'] for x in steps]
        assert names.index('Verify dependencies and run native target-local node') < names.index('DN03-R3 reverify native .NET node, host and output bytes')
        assert names.index('DN03-R3 reverify native .NET node, host and output bytes') < names.index('Record local run attempt (not authentication)')
        script=next(x['run'] for x in steps if x['name'].startswith('DN03-R3'))
        result=subprocess.run(['bash','-n'],input=script,text=True,capture_output=True)
        assert result.returncode==0,result.stderr
        assert '$RUNNER_TEMP/devtool-venv/bin/python' in script
        assert '--graph-sha '+GRAPH in script
    assert rec['workflow_sha256']==hashlib.sha256(y.encode()).hexdigest()
    assert rec['post_native_node_evidence_required'] is True
    assert rec['real_generated_workflow_executed'] is False
    assert not rec['remote_authenticated'] and not rec['release_qualified']


def test_r3_prevents_payload_upload_before_verification(monkeypatch,tmp_path):
    root=_repo(tmp_path)
    _fixture(monkeypatch,root)
    y,_=r3.compile_r3(root,target='app',workflow='ci',devtool_sha=DEVTOOL)
    names=[x['name'] for x in yaml.safe_load(y)['jobs']['node01']['steps']]
    assert names.index('DN03-R3 reverify native .NET node, host and output bytes') < names.index('Upload node01 declared bytes (not authenticated)')


def test_r3_plan_write_verify_and_commit_stability(monkeypatch,tmp_path):
    root=_repo(tmp_path)
    _fixture(monkeypatch,root)
    plan=r3.export(root,target='app',workflow='ci',devtool_sha=DEVTOOL,mode='plan')
    assert not (root/plan['workflow_path']).exists()
    with pytest.raises(r3.R3ExportError,match='explicit --yes'):
        r3.export(root,target='app',workflow='ci',devtool_sha=DEVTOOL,mode='write')
    saved=r3.export(root,target='app',workflow='ci',devtool_sha=DEVTOOL,mode='write',confirmed=True)
    assert saved['wrote_files']
    assert r3.export(root,target='app',workflow='ci',devtool_sha=DEVTOOL,mode='verify')['verified_files']
    _git(root,'add','.')
    _git(root,'commit','-qm','workflow and receipt')
    assert r3.export(root,target='app',workflow='ci',devtool_sha=DEVTOOL,mode='verify')['verified_files']
    with pytest.raises(r3.R3ExportError,match='overwrite'):
        r3.export(root,target='app',workflow='ci',devtool_sha=DEVTOOL,mode='write',confirmed=True)
    (root/saved['workflow_path']).write_text('tampered\n')
    with pytest.raises(r3.R3ExportError,match='differs'):
        r3.export(root,target='app',workflow='ci',devtool_sha=DEVTOOL,mode='verify')


def test_r3_refuses_native_graph_drift(monkeypatch,tmp_path):
    root=_repo(tmp_path)
    g=_fixture(monkeypatch,root)
    g['graph_sha256']='f'*64
    with pytest.raises(r3.R3ExportError,match='graph changed'):
        r3.compile_r3(root,target='app',workflow='ci',devtool_sha=DEVTOOL)


@pytest.mark.parametrize('bad', ['no anchor', 'duplicate anchor', 'out of order'])
def test_r3_refuses_invalid_insertion_anchor(bad):
    g=_graph()
    y=_r2_yaml(g)
    if bad=='no anchor':
        y=y.replace('      - name: Record local run attempt (not authentication)\n','      - name: erased\n',1)
    elif bad=='duplicate anchor':
        y=y.replace('      - name: Verify dependencies and run native target-local node\n','      - name: Verify dependencies and run native target-local node\n      - name: Verify dependencies and run native target-local node\n',1)
    else:
        y=y.replace('      - name: Verify dependencies and run native target-local node\n', '      - name: early\n',1)
    with pytest.raises(r3.R3ExportError):
        r3._enhance_native_workflow(y,g['jobs'],target='app',workflow='ci',devtool_sha=DEVTOOL,graph_sha=GRAPH)


def test_r3_verifier_accepts_rehashed_native_receipt_without_trust_claim(tmp_path, monkeypatch):
    root=_repo(tmp_path)
    temp,env=_native_context(root,tmp_path)
    import devtool.actions_graph_export as graph_module
    import devtool.actions_node_evidence as evidence_module
    monkeypatch.setattr(graph_module,'compile_graph', lambda *a,**kw:_graph(root))
    def verified(native,graph,job,**kwargs):
        assert native['receipt']=='test'
        assert kwargs['staging']==temp/'rm06-output'
        assert kwargs['run_id']==70001 and kwargs['attempt']==2
        return dict(identity_matched=True,structure_valid=True,
                    remote_authenticated=False,release_qualified=False)
    monkeypatch.setattr(evidence_module,'verify_node_evidence',verified)
    rec=verifier.verify_native_node(root,target='app',workflow='ci',
          devtool_sha=DEVTOOL,graph_sha=GRAPH,job_id='node01',
          outputs=temp/'rm06-output',environment=env)
    assert rec['native_job_qualified'] is True
    assert rec['test_reports_independently_verified'] is False
    assert rec['remote_authenticated'] is False
    assert (temp/'rm06-evidence'/'node01'/'dn03-r3-node-verification.json').is_file()
    with pytest.raises(FileExistsError):
        verifier.verify_native_node(root,target='app',workflow='ci',
          devtool_sha=DEVTOOL,graph_sha=GRAPH,job_id='node01',
          outputs=temp/'rm06-output',environment=env)


@pytest.mark.parametrize('mutation',[
    'wrong-run', 'wrong-source', 'wrong-host', 'symlink-host',
    'wrong-graph', 'bad-native', 'missing-native', 'wrong-output', 'missing-runtime',
])
def test_r3_verifier_fail_closed(mutation,tmp_path,monkeypatch):
    root=_repo(tmp_path)
    temp,env=_native_context(root,tmp_path)
    import devtool.actions_graph_export as graph_module
    import devtool.actions_node_evidence as evidence_module
    graph=_graph(root)
    monkeypatch.setattr(graph_module,'compile_graph',lambda *a,**kw:graph)
    monkeypatch.setattr(evidence_module,'verify_node_evidence',lambda *a,**kw:dict(
        identity_matched=True,structure_valid=True,
        remote_authenticated=False,release_qualified=False))
    evidence=temp/'rm06-evidence'/'node01'/'node-evidence.json'
    host=temp/'dn03-host'/'node01'/'host.json'
    if mutation=='wrong-run':env['GITHUB_RUN_ATTEMPT']='3'
    if mutation=='wrong-source':env['GITHUB_SHA']='0'*40
    if mutation=='wrong-host':
        a=json.loads(host.read_text());a['sdk']='10.0.101';host.write_text(json.dumps(a))
    if mutation=='symlink-host':
        host.unlink();host.symlink_to(root/'README.md')
    if mutation=='wrong-graph':graph['graph_sha256']='0'*64
    if mutation=='bad-native':evidence.write_text('{broken')
    if mutation=='missing-native':evidence.unlink()
    if mutation=='wrong-output':(temp/'other').mkdir()
    if mutation=='missing-runtime':
        a=json.loads(host.read_text());a.pop('runtime_receipt_sha256');host.write_text(json.dumps(a))
    with pytest.raises((verifier.R3Refused,ValueError,OSError)):
        verifier.verify_native_node(root,target='app',workflow='ci',
          devtool_sha=DEVTOOL,graph_sha=GRAPH,job_id='node01',
          outputs=(temp/'other' if mutation=='wrong-output' else temp/'rm06-output'),
          environment=env)
    assert not (temp/'rm06-evidence'/'node01'/'dn03-r3-node-verification.json').exists()


def test_r3_refuses_falsely_authenticated_native_node(tmp_path,monkeypatch):
    root=_repo(tmp_path)
    temp,env=_native_context(root,tmp_path)
    import devtool.actions_graph_export as graph_module
    import devtool.actions_node_evidence as evidence_module
    monkeypatch.setattr(graph_module,'compile_graph',lambda *a,**kw:_graph(root))
    monkeypatch.setattr(evidence_module,'verify_node_evidence',lambda *a,**kw:dict(
        identity_matched=True,structure_valid=True,
        remote_authenticated=True,release_qualified=False))
    with pytest.raises(verifier.R3Refused,match='trust claim'):
        verifier.verify_native_node(root,target='app',workflow='ci',
          devtool_sha=DEVTOOL,graph_sha=GRAPH,job_id='node01',
          outputs=temp/'rm06-output',environment=env)


def test_r3_integrates_with_real_dn03_r2_hardening(monkeypatch,tmp_path):
    """Exercise R2 _harden and R3 insertion together rather than mocking R2 output."""
    root=_repo(tmp_path)
    g=_graph(root)
    # The actual DN03-R2 compiler searches these frozen RM-06 markers.
    skeleton='name: compiled\non:\n  workflow_dispatch:\njobs:\n'
    for job in g['jobs']:
        skeleton+=f'  {job["id"]}:\n    runs-on: ubuntu-24.04\n    steps:\n'
        skeleton+='      - name: Pinned .NET SDK\n        run: echo pinned\n'
        if job['dotnet_legacy_runtime']:
            skeleton+=("      - name: Legacy .NET 6 runtime compatibility (EOL)\n"
                       "        uses: actions/setup-dotnet@26b0ec14cb23fa6904739307f278c14f94c95bf1\n"
                       "        with:\n          dotnet-version: '6.0.428'\n"
                       "          cache: false\n")
        skeleton+='      - name: Acquire immutable Devtool runtime\n        run: echo acquired\n'
        skeleton+='      - name: Verify dependencies and run native target-local node\n        run: echo native\n'
        if job['outputs']:
            skeleton+=f'      - name: Upload {job["id"]} declared bytes (not authenticated)\n        run: echo byte-upload\n'
        skeleton+='      - name: Record local run attempt (not authentication)\n        run: echo record\n'
    g.update(target='app',workflow='ci')
    monkeypatch.setattr(r3.dn03,'compile_graph',lambda *a,**kw:g)
    monkeypatch.setattr(r3.dn03,'workflow_yaml',lambda *a,**kw:skeleton)
    result,receipt=r3.compile_r3(root,target='app',workflow='ci',devtool_sha=DEVTOOL)
    y=yaml.safe_load(result)
    assert len(y['jobs'])==2
    for item in y['jobs'].values():
        names=[x['name'] for x in item['steps']]
        assert names.index('Acquire immutable Devtool runtime') < names.index('DN03 verify .NET host and record local source-bound evidence')
        assert names.index('Acquire immutable Devtool runtime') < names.index('DN03-R3 reverify native .NET node, host and output bytes')
    assert receipt['workflow_sha256']==hashlib.sha256(result.encode()).hexdigest()
    assert 'DN03 install SHA-512-pinned .NET 6 runtime only' in result
