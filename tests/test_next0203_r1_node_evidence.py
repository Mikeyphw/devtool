"""NEXT-02/03-R1: strict node identity and provider-neutral local projection."""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from devtool.actions_node_evidence import (
    NodeEvidenceError, build_node_evidence, verify_node_evidence, write_node_evidence,
)
from devtool.actions_graph_export import stage_outputs

SRC = 'a' * 40
TREE = 'b' * 40
PROVIDERS = ('go', 'python-shell', 'dotnet', 'rust-cargo', 'node', 'jvm', 'shell-ci', 'cpp', 'android-gradle')


def inputs(provider='go', *, outputs=()):
    node = {'id': 'node-01', 'node': 'app.ci.step', 'job': 'step',
            'provider': provider, 'profile_id': 'ubuntu-24.04-x64', 'outputs': list(outputs)}
    graph = {'jobs': [node], 'graph_sha256': 'c'*64, 'consumer_tree': TREE,
             'target': 'app', 'workflow': 'ci'}
    return graph, node


@pytest.fixture(autouse=True)
def workflow(monkeypatch):
    monkeypatch.setattr('devtool.actions_graph_export.workflow_yaml', lambda graph: 'reviewed yaml')


def make(tmp_path, provider='go', *, outputs=()):
    graph, node = inputs(provider, outputs=outputs)
    if outputs:
        source = tmp_path / 'consumer'
        for name in outputs:
            target = source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'verified-local-bytes')
        stage_outputs(source, tmp_path/'staging', graph, node, SRC, '19', 2)
    receipt = build_node_evidence(graph, node, source=SRC, source_tree=TREE,
                                  run_id=19, attempt=2, staging=tmp_path/'staging')
    return graph, node, receipt


def checked(receipt, graph, node, *, staging=None, **override):
    return verify_node_evidence(receipt, graph, node, source=override.get('source', SRC),
                                source_tree=override.get('source_tree', TREE),
                                run_id=override.get('run_id', 19),
                                attempt=override.get('attempt', 2), staging=staging)


@pytest.mark.parametrize('provider', PROVIDERS)
def test_provider_neutral_without_inferred_trust(tmp_path, provider):
    graph, node, receipt = make(tmp_path, provider)
    result = checked(receipt, graph, node)
    assert result['identity_matched'] and not result['remote_authenticated']
    assert result['effective_gates']['build']['state'] == 'UNKNOWN'
    assert receipt['typed_evidence']['gates']['test']['state'] == 'NOT_RUN'
    for name in ('signature','release','native_device','artifact_authenticity'):
        assert receipt['typed_evidence']['gates'][name]['state'] == 'UNKNOWN'
    assert receipt['typed_evidence']['source']['tree'] == TREE


@pytest.mark.parametrize('change', [
    {'source':'d'*40}, {'source_tree':'e'*40}, {'run_id':20}, {'attempt':3}
])
def test_wrong_source_run_attempt_rejected(tmp_path, change):
    graph,node,receipt = make(tmp_path)
    with pytest.raises(NodeEvidenceError, match='identity mismatch'):
        checked(receipt, graph, node, **change)


def test_fake_gates_cannot_be_promoted_even_with_recomputed_digest(tmp_path):
    from devtool.actions_node_evidence import _digest
    graph,node,receipt=make(tmp_path)
    for domain in ('test','release','signature','artifact_authenticity','native_device'):
        fake = json.loads(json.dumps(receipt))
        fake['typed_evidence']['gates'][domain]['state'] = 'PASS'
        fake['receipt_sha256'] = _digest({k:v for k,v in fake.items() if k != 'receipt_sha256'})
        with pytest.raises(NodeEvidenceError, match='cannot PASS|cannot promote'):
            checked(fake, graph, node)


def test_native_staged_artifact_rehash_and_exact_run(tmp_path):
    graph,node,receipt = make(tmp_path, outputs=('dist/bin',))
    assert checked(receipt,graph,node,staging=tmp_path/'staging')['identity_matched']
    assert receipt['typed_evidence']['gates']['artifact_integrity']['state'] == 'PASS'
    payload = tmp_path/'staging'/'node-01'/'payload'/'dist'/'bin'
    payload.write_bytes(b'tampered')
    with pytest.raises(NodeEvidenceError, match='handoff'):
        checked(receipt,graph,node,staging=tmp_path/'staging')


def test_native_staged_artifact_attempt_replay_refused(tmp_path):
    graph,node,receipt = make(tmp_path, outputs=('dist/bin',))
    handoff=tmp_path/'staging'/'node-01'/'receipt.json'
    data=json.loads(handoff.read_text());data['attempt']=1
    handoff.write_text(json.dumps(data))
    with pytest.raises(NodeEvidenceError, match='handoff'):
        checked(receipt,graph,node,staging=tmp_path/'staging')


def test_external_data_cannot_claim_extra_outputs(tmp_path):
    graph,node,receipt=make(tmp_path)
    fake=json.loads(json.dumps(receipt))
    fake['typed_evidence']['artifacts']=[{'role':'node-output','type':'unverified-bytes',
        'path':'dist/evil','sha256':'f'*64,'signature_state':'UNKNOWN','producer':'go'}]
    from devtool.actions_node_evidence import _digest
    fake['receipt_sha256']=_digest({k:v for k,v in fake.items() if k!='receipt_sha256'})
    with pytest.raises(NodeEvidenceError, match='artifact list'):
        checked(fake,graph,node)


def test_write_create_only_no_symlink_or_overwrite(tmp_path):
    graph,node,receipt=make(tmp_path)
    folder=write_node_evidence(tmp_path/'rm06-output',node['id'],receipt)
    assert folder.is_file()
    assert os.stat(folder).st_mode & 0o077 == 0
    with pytest.raises(FileExistsError):
        write_node_evidence(tmp_path/'rm06-output',node['id'],receipt)
    (tmp_path/'unsafe').symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(NodeEvidenceError, match='Symlink'):
        write_node_evidence(tmp_path/'unsafe'/'rm06-output',node['id'],receipt)


def test_bad_receipt_digest_refused(tmp_path):
    graph,node,receipt=make(tmp_path)
    receipt['provider']='python-shell'
    with pytest.raises(NodeEvidenceError, match='digest'):
        checked(receipt,graph,node)


def test_source_review_tree_is_distinct_from_execution_tree(tmp_path):
    graph,node = inputs()
    receipt=build_node_evidence(graph,node,source=SRC,source_tree='e'*40,run_id=19,attempt=2,staging=tmp_path/'none')
    assert receipt['review_tree'] == TREE
    assert receipt['source_tree'] == 'e'*40
    assert checked(receipt,graph,node,source_tree='e'*40)['identity_matched']


def test_no_test_proof_from_successful_execution(tmp_path):
    graph,node,receipt=make(tmp_path)
    assert receipt['typed_evidence']['gates']['build']['state'] == 'PASS'
    assert receipt['typed_evidence']['tests']['format'] == 'none'
    assert checked(receipt,graph,node)['effective_gates']['build']['state'] == 'UNKNOWN'


def test_actual_export_yaml_requires_node_receipt_if_job_successful(tmp_path,monkeypatch):
    monkeypatch.undo()  # use real native YAML compiler
    from devtool.actions_graph_export import workflow_yaml
    from tests.test_rm06_graph_export import compile_fixture
    root,graph=compile_fixture(tmp_path)
    yaml=workflow_yaml(graph)
    assert 'node-evidence.json' in yaml
    # SFDT05 replaced the legacy inline successful-job assertion with a
    # source/run-bound attempt receipt. Missing typed evidence must fail closed.
    assert 'id: sfdt_native' in yaml
    assert 'if: always()' in yaml
    assert 'SFDT_NATIVE_OUTCOME: ${{ steps.sfdt_native.outcome }}' in yaml
    assert 'SFDT_JOB_STATUS: ${{ job.status }}' in yaml
    assert 'attempt-evidence.json' in yaml
    assert 'typed_node_evidence_present=evidence_present' in yaml
    assert 'status="OBSERVED_SUCCESS" if complete else "UNQUALIFIED"' in yaml
    assert 'if native=="success" and not complete: sys.exit(7)' in yaml
    assert 'Upload review evidence' in yaml
