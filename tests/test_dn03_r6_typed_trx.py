"""DN03-R6: real staged TRX parser, typed evidence and cross-node binding."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import pytest

from devtool import actions_graph_export as graph_export
from devtool import actions_node_evidence as node_evidence
from devtool.actions_trx_evidence import TrxEvidenceError, verified_native_trx

SRC = 'a' * 40
TREE = 'b' * 40
GRAPH = 'c' * 64


@pytest.fixture(autouse=True)
def stable_yaml(monkeypatch):
    monkeypatch.setattr(graph_export, 'workflow_yaml', lambda graph: 'stable native YAML')


def report(rows=(('t1', 'e1', 'Passed'), ('t2', 'e2', 'Passed')),
           *, total=None, passed=None, failed=None, doctype=False):
    total = len(rows) if total is None else total
    passed = sum(r[2] == 'Passed' for r in rows) if passed is None else passed
    failed = sum(r[2] == 'Failed' for r in rows) if failed is None else failed
    results = ''.join(
        f'<UnitTestResult testId="{ident}" executionId="{execution}" '
        f'testName="name{idx}" outcome="{outcome}" />'
        for idx, (ident, execution, outcome) in enumerate(rows)
    )
    pre = '<!DOCTYPE TestRun [<!ENTITY xx "forbidden">]>' if doctype else ''
    return (pre + '<TestRun xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010">'
            '<Results>' + results + '</Results><ResultSummary outcome="Completed">'
            f'<Counters total="{total}" passed="{passed}" failed="{failed}" />'
            '</ResultSummary></TestRun>').encode()


def staged(tmp_path, *, data=None):
    root = tmp_path / 'consumer'
    root.mkdir()
    rel = 'dist/pure/results.trx'
    p = root / rel
    p.parent.mkdir(parents=True)
    p.write_bytes(report() if data is None else data)
    job = {'id': 'node-03', 'node': 'app.native.test_pure', 'job': 'test_pure',
           'provider': 'dotnet', 'profile_id': 'ubuntu-24.04-x64',
           'dotnet_command': 'test', 'dotnet_selected': 'Tests/Pure/Pure.csproj',
           'outputs': [rel]}
    graph = {'jobs': [job], 'graph_sha256': GRAPH, 'consumer_tree': TREE,
             'target': 'app', 'workflow': 'native'}
    output = tmp_path / 'rm06-output'
    output.mkdir()
    graph_export.stage_outputs(root, output, graph, job, SRC, '19', 2)
    return root, output, graph, job, rel


def build(output, graph, job):
    return node_evidence.build_node_evidence(graph, job, source=SRC, source_tree=TREE,
                                              run_id=19, attempt=2, staging=output)


def verify(record, output, graph, job, *, source=SRC, attempt=2):
    return node_evidence.verify_node_evidence(record, graph, job, source=source,
                                               source_tree=TREE, run_id=19,
                                               attempt=attempt, staging=output)


@pytest.mark.parametrize('reviewed', [
    'All.sln', 'All.slnx', 'App/App.csproj',
])
def test_native_dotnet_solution_or_project_entry_is_accepted(tmp_path, reviewed):
    # RM-09 genuinely accepts solution-selected tests; R6 must retain that
    # historical behavior without weakening the signed handoff identity.
    _, stage, graph, job, rel = staged(tmp_path)
    job['dotnet_selected'] = reviewed
    receipt = build(stage, graph, job)
    result = verify(receipt, stage, graph, job)
    assert result['verified_trx']['path'] == rel
    assert result['verified_trx']['passed'] == 2
    assert result['remote_authenticated'] is False


@pytest.mark.parametrize('unreviewed', [
    '', '../All.sln', '/tmp/All.sln', 'App//All.sln',
    './All.sln', '$(Injected).sln', 'All.sln;true',
    'All.proj', 'a\\b.sln',
])
def test_unreviewed_solution_path_remains_refused(tmp_path, unreviewed):
    _, stage, graph, job, _ = staged(tmp_path)
    job['dotnet_selected'] = unreviewed
    with pytest.raises((TrxEvidenceError, node_evidence.NodeEvidenceError),
                       match='selected|reviewed'):
        build(stage, graph, job)


def test_verified_trx_is_byte_bound_into_original_typed_node(tmp_path):
    root, stage, graph, job, rel = staged(tmp_path)
    rec = build(stage, graph, job)
    assert rec['typed_evidence']['tests'] == {
        'format': 'trx', 'total': 2, 'failed': 0, 'skipped': 0,
        'report_sha256': sha256(report()).hexdigest(), 'required': True}
    assert rec['typed_evidence']['gates']['test']['state'] == 'PASS'
    assert rec['typed_evidence']['gates']['test']['authority'] == 'local_staged_trx_reparsed'
    assert rec['typed_evidence']['artifacts'][0]['sha256'] == rec['typed_evidence']['tests']['report_sha256']
    check = verify(rec, stage, graph, job)
    assert check['identity_matched'] and check['verified_trx']['passed'] == 2
    assert check['verified_trx']['path'] == rel
    # A locally verified report does not authenticate remote provenance.
    assert check['remote_authenticated'] is False and check['release_qualified'] is False
    assert check['effective_gates']['test']['state'] == 'UNKNOWN'


@pytest.mark.parametrize('bad', [
    report(rows=()),
    report(rows=(('t1', 'e1', 'Failed'),)),
    report(rows=(('t1', 'e1', 'NotExecuted'),)),
    report(rows=(('t1', 'e1', 'Timeout'),)),
    report(rows=(('t1', 'e1', 'Passed'),), total=2),
    report(rows=(('t1', 'e1', 'Passed'),), passed=0),
    report(rows=(('t1', 'e1', 'Passed'), ('t1', 'e2', 'Passed'))),
    # Explicitly reject absent execution identities even if RM-09 counters pass.
    report().replace(b' executionId="e1"', b''),
    # Do not accept repeated (testId, executionId) in one TRX report.
    report(rows=(('t1', 'e1', 'Passed'), ('t1', 'e1', 'Passed'))),
    report(rows=(('t1', 'e1', 'Passed'),), doctype=True),
    b'<garbled>',
    b'',
])
def test_missing_failing_skipped_duplicate_and_corrupt_trx_fail_closed(tmp_path, bad):
    _, stage, graph, job, _ = staged(tmp_path, data=bad)
    with pytest.raises((TrxEvidenceError, node_evidence.NodeEvidenceError, ValueError)):
        build(stage, graph, job)


def test_forged_claim_with_recomputed_outer_digest_still_refused(tmp_path):
    _, stage, graph, job, _ = staged(tmp_path)
    correct = build(stage, graph, job)
    for mutation in ('count', 'digest', 'test_gate', 'additional_artifact'):
        fake = deepcopy(correct)
        typed = fake['typed_evidence']
        if mutation == 'count': typed['tests']['total'] = 3
        elif mutation == 'digest': typed['tests']['report_sha256'] = 'f' * 64
        elif mutation == 'test_gate': typed['gates']['test']['authority'] = 'fake_remote_attestation'
        else: typed['artifacts'].append(deepcopy(typed['artifacts'][0]))
        fake['receipt_sha256'] = node_evidence._digest({k:v for k,v in fake.items() if k!='receipt_sha256'})
        with pytest.raises(node_evidence.NodeEvidenceError):
            verify(fake, stage, graph, job)


def test_tampered_stage_or_replayed_run_attempt_refused(tmp_path):
    _, stage, graph, job, rel = staged(tmp_path)
    good = build(stage, graph, job)
    with pytest.raises(node_evidence.NodeEvidenceError, match='identity'):
        verify(good, stage, graph, job, attempt=3)
    path = stage / job['id'] / 'payload' / rel
    path.write_bytes(report(rows=(('t1','e1','Passed'),)))
    with pytest.raises(node_evidence.NodeEvidenceError):
        verify(good, stage, graph, job)


def test_no_staging_cannot_claim_verified_tests(tmp_path):
    _, stage, graph, job, _ = staged(tmp_path)
    record = build(stage, graph, job)
    with pytest.raises(node_evidence.NodeEvidenceError, match='staging'):
        node_evidence.verify_node_evidence(record, graph, job, source=SRC,
          source_tree=TREE, run_id=19, attempt=2, staging=None)


def test_nontest_dotnet_keeps_not_run_even_with_declared_outputs(tmp_path):
    _, stage, graph, job, _ = staged(tmp_path)
    job['dotnet_command'] = 'build'
    rec = build(stage, graph, job)
    assert rec['typed_evidence']['tests']['format'] == 'none'
    assert rec['typed_evidence']['gates']['test']['state'] == 'NOT_RUN'
    assert verify(rec, stage, graph, job)['verified_trx'] is None


def test_output_identity_and_symlink_refused(tmp_path):
    _, stage, graph, job, rel = staged(tmp_path)
    p = stage / job['id'] / 'payload' / rel
    out = tmp_path / 'foreign.trx'
    out.write_bytes(p.read_bytes())
    p.unlink()
    p.symlink_to(out)
    with pytest.raises((TrxEvidenceError, node_evidence.NodeEvidenceError)):
        build(stage, graph, job)



@pytest.mark.parametrize('coverage_xml,accepted', [
    (b'<coverage line-rate="0.75"><packages><package><classes><class name="A" /></classes></package></packages></coverage>', True),
    (b'<coverage line-rate="1.50"><class name="A" /></coverage>', False),
    (b'<coverage line-rate="0.25"></coverage>', False),
    (b'<other line-rate="0.25"><class name="A" /></other>', False),
    (b'<!DOCTYPE coverage><coverage line-rate="0.5"><class name="A" /></coverage>', False),
    (b'<coverage broken>', False),
])
def test_optional_owned_cobertura_is_independently_bound(tmp_path, coverage_xml, accepted):
    root, _, graph, job, trx_path = staged(tmp_path)
    cov_path = 'dist/coverage.cobertura.xml'
    (root / cov_path).write_bytes(coverage_xml)
    job['outputs'].append(cov_path)
    job['dotnet_coverage'] = True
    stage = tmp_path / 'rm06-output-with-coverage'
    stage.mkdir()
    graph_export.stage_outputs(root, stage, graph, job, SRC, '19', 2)
    if not accepted:
        with pytest.raises((TrxEvidenceError, node_evidence.NodeEvidenceError)):
            build(stage, graph, job)
        return
    receipt = build(stage, graph, job)
    verified = verify(receipt, stage, graph, job)['verified_trx']
    assert verified['passed'] == 2
    assert verified['coverage']['sha256'] == sha256(coverage_xml).hexdigest()
    assert verified['coverage']['class_count'] == 1
    assert verified['coverage']['line_rate'] == 0.75


def test_declared_missing_coverage_refuses_even_when_trx_passes(tmp_path):
    _, stage, graph, job, _ = staged(tmp_path)
    job['dotnet_coverage'] = True
    with pytest.raises((TrxEvidenceError, node_evidence.NodeEvidenceError)):
        build(stage, graph, job)

def test_r3_verification_receipt_includes_exact_test_report_digest(tmp_path, monkeypatch):
    from tests.test_dn03_r3_verified_export import _repo, _native_context, _graph, DEVTOOL, GRAPH as GRAPH_R3
    from devtool import actions_dn03_r3_verify as r3
    root = _repo(tmp_path)
    temp, env = _native_context(root, tmp_path)
    import devtool.actions_graph_export as graph_module
    g = _graph(root)
    g['jobs'][0]['dotnet_command'] = 'test'
    g['jobs'][0]['outputs'] = ['dist/pure/results.trx']
    monkeypatch.setattr(graph_module, 'compile_graph', lambda *a, **kw: g)
    expected = {'path': 'dist/pure/results.trx', 'sha256': 'd'*64, 'passed': 2,
                'tests': {'format':'trx','total':2,'failed':0,'skipped':0,'report_sha256':'d'*64,'required':True},
                'authority': 'local_staged_trx_reparsed','remote_authenticated': False,'release_qualified':False}
    monkeypatch.setattr(node_evidence, 'verify_node_evidence', lambda *a, **kw: {
        'identity_matched':True,'structure_valid':True,'remote_authenticated':False,
        'release_qualified':False,'verified_trx':expected})
    receipt = r3.verify_native_node(root,target='app',workflow='ci',devtool_sha=DEVTOOL,
        graph_sha=GRAPH_R3,job_id='node01',outputs=temp/'rm06-output',environment=env)
    assert receipt['test_reports_independently_verified'] is True
    assert receipt['test_report'] == expected
    assert receipt['node_evidence_sha256'] == sha256((temp/'rm06-evidence/node01/node-evidence.json').read_bytes()).hexdigest()


def test_r4_rejects_mismatched_test_receipt_even_when_outer_local_digest_is_consistent(tmp_path, monkeypatch):
    from tests.test_dn03_r4_cross_node import receipt_fixture, verify
    from devtool import actions_dn03_r4_verify as r4v
    root, evidence, env, jid = receipt_fixture(monkeypatch, tmp_path)
    from devtool import actions_graph_export as graph_module
    original = graph_module.compile_graph
    def modified(*a, **kw):
        g = original(*a, **kw)
        g['jobs'][0]['dotnet_command'] = 'test'
        return g
    monkeypatch.setattr(graph_module, 'compile_graph', modified)
    # The fixture native node receipt is source-bound but not a real TRX; the
    # R4 re-parser must not silently turn that into passing test evidence.
    with pytest.raises(r4v.R4Refused, match='TRX|test report|native typed evidence'):
        verify(root, evidence, env)
