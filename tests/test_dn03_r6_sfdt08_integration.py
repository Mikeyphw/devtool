"""DN03-R6 + SFDT08: native TRX and JUnit gates coexist without trust promotion."""
from __future__ import annotations

import pytest
from devtool.actions_node_evidence import (
    NodeEvidenceError, build_node_evidence, verify_node_evidence,
)
from tests.test_dn03_r6_typed_trx import staged, build, verify

SRC = 'a' * 40
TREE = 'b' * 40
JUNIT_DIR = 'app-tv/build/test-results/testDebugUnitTest'


@pytest.fixture(autouse=True)
def stable_yaml(monkeypatch):
    monkeypatch.setattr('devtool.actions_graph_export.workflow_yaml', lambda graph: 'stable native YAML')


def test_sfdt08_junit_gate_is_preserved(tmp_path):
    report = tmp_path / JUNIT_DIR / 'TEST-Sample.xml'
    report.parent.mkdir(parents=True)
    report.write_text('<testsuite tests="1" failures="0" skipped="0">'
                      '<testcase name="one" classname="Sample"/></testsuite>')
    node = {'id': 'node-01', 'node': 'demo.ci.tv', 'job': 'tv',
            'provider': 'android-gradle', 'profile_id': 'ubuntu-24.04-x64',
            'outputs': [], 'junit_report_dir': JUNIT_DIR}
    graph = {'jobs': [node], 'graph_sha256': 'c' * 64, 'consumer_tree': TREE,
             'target': 'demo', 'workflow': 'ci'}
    receipt = build_node_evidence(graph, node, source=SRC, source_tree=TREE,
                                  run_id=55, attempt=2, staging=tmp_path / 'staging',
                                  junit_root=tmp_path)
    checked = verify_node_evidence(receipt, graph, node, source=SRC, source_tree=TREE,
                                   run_id=55, attempt=2, junit_root=tmp_path)
    assert receipt['typed_evidence']['tests']['format'] == 'junit'
    assert checked['effective_gates']['test']['state'] == 'PASS'
    assert checked['verified_trx'] is None
    assert checked['remote_authenticated'] is False
    assert checked['effective_gates']['release']['state'] == 'UNKNOWN'


def test_r6_native_trx_gate_is_preserved(tmp_path):
    _, stage, graph, job, rel = staged(tmp_path)
    receipt = build(stage, graph, job)
    checked = verify(receipt, stage, graph, job)
    assert checked['verified_trx']['path'] == rel
    assert receipt['typed_evidence']['tests']['format'] == 'trx'
    assert receipt['typed_evidence']['tests']['total'] == 2
    assert checked['effective_gates']['test']['state'] == 'UNKNOWN'
    assert checked['remote_authenticated'] is False
    assert checked['release_qualified'] is False


def test_ambiguous_junit_and_dotnet_trx_rejected(tmp_path):
    _, stage, graph, job, _ = staged(tmp_path)
    job['junit_report_dir'] = JUNIT_DIR
    with pytest.raises(NodeEvidenceError, match='mutually exclusive'):
        build_node_evidence(graph, job, source=SRC, source_tree=TREE,
                            run_id=19, attempt=2, staging=stage,
                            junit_root=tmp_path)


def test_non_test_node_stays_not_run(tmp_path):
    node = {'id': 'node-01', 'node': 'app.ci.build', 'job': 'build',
            'provider': 'dotnet', 'dotnet_command': 'build',
            'profile_id': 'ubuntu-24.04-x64', 'outputs': []}
    graph = {'jobs': [node], 'graph_sha256': 'c' * 64,
             'consumer_tree': TREE, 'target': 'app', 'workflow': 'ci'}
    receipt = build_node_evidence(graph, node, source=SRC, source_tree=TREE,
                                  run_id=9, attempt=1, staging=tmp_path / 'none')
    checked = verify_node_evidence(receipt, graph, node, source=SRC, source_tree=TREE,
                                   run_id=9, attempt=1)
    assert receipt['typed_evidence']['gates']['test']['state'] == 'NOT_RUN'
    assert checked['verified_trx'] is None
