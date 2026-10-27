"""R01 trust regression: overlapping CLI/runner RUN-V3 writes and pinned G2."""
from __future__ import annotations

import hashlib
import json
import subprocess
from argparse import Namespace
from pathlib import Path

import pytest
from devtool.cli import _finalize_cli_run_instance, _register_cli_run_instance
from devtool.core_runtime import private_run_root
from devtool.run_store import AttachmentDescriptor, RunCatalog
from devtool.actions_qualification import qualification_report
from devtool.github_actions import GitHubActionsError

ROOT = Path(__file__).resolve().parents[1]


def test_finalizer_cannot_drop_late_artifact_evidence(tmp_path, monkeypatch):
    repo = tmp_path / 'repo'
    repo.mkdir()
    opts = Namespace(repo=str(repo), command='apply')
    run_id = '4' * 32
    _register_cli_run_instance(opts, ['apply', 'overlay'], run_id)
    run_root = private_run_root(repo)
    witness = run_root / 'runner-witness.json'
    witness.write_text('{"source":"runner"}\n')
    real_open = RunCatalog.open.__func__
    injected = False

    def concurrent_open(cls, path, *, create=True):
        nonlocal injected
        opened = real_open(cls, path, create=create)
        if not injected:
            injected = True
            # A runner commits a new row and attachment after the finalizer
            # read its initial snapshot, before it writes its terminal status.
            late = real_open(cls, path, create=create)
            row = dict(late.runs[run_id])
            row.update(artifact_id='artifact:late', artifact_plan_id='plan:late',
                       artifact_qualification_receipt_id='qualification:late')
            late.add_run(row)
            late.add_attachment(AttachmentDescriptor.from_file(
                run_id=run_id, path=witness, kind='artifact-execution-record',
                media_type='application/json'))
            late.save()
        return opened

    monkeypatch.setattr(RunCatalog, 'open', classmethod(concurrent_open))
    _finalize_cli_run_instance(opts, run_id, status='succeeded', exit_code=0)
    got = real_open(RunCatalog, run_root, create=False)
    row = got.runs[run_id]
    assert row['status'] == 'succeeded' and row['exit_code'] == 0
    assert row['artifact_id'] == 'artifact:late'
    assert row['artifact_plan_id'] == 'plan:late'
    assert row['artifact_qualification_receipt_id'] == 'qualification:late'
    assert any(a.kind == 'artifact-execution-record' and a.run_id == run_id
               and a.sha256 == hashlib.sha256(witness.read_bytes()).hexdigest()
               for a in got.attachments.values())


def test_g2_immutable_ancestry_exact_in_source_checkout():
    proof = json.loads((ROOT / 'docs/actions/ACTIONS_G02_SOURCE_TRANSITION_20261020.json').read_text())
    frozen = json.loads((ROOT / 'docs/actions/ACTIONS_G08_QUALIFICATION_MATRIX.json').read_text())
    original = next(row['sha256'] for wave in frozen['waves'] if wave['id'] == 'G2'
                    for row in wave['source_contracts'] if row['path'] == proof['path'])
    assert original == proof['original']['sha256']
    # The accepted R01 G2 blob is immutable historical evidence, not a pin on
    # the evolving current-source pipeline (R04B and later modify that file).
    current = subprocess.run(['git', '-C', str(ROOT), 'show', 'HEAD:' + proof['path']],
                             capture_output=True, check=True)
    assert current.stdout == (ROOT / proof['path']).read_bytes()
    for stage in ('original', 'accepted_successor'):
        obj = proof[stage]
        result = subprocess.run(['git', '-c', 'safe.directory=' + str(ROOT), '-C', str(ROOT), 'show',
                                 obj['git_commit'] + ':' + proof['path']], capture_output=True)
        assert result.returncode == 0, result.stderr.decode(errors='replace')[:300]
        assert hashlib.sha256(result.stdout).hexdigest() == obj['sha256']
    assert proof['qualified_release'] is False


def test_g2_authenticated_source_only_and_tamper_refusal(tmp_path):
    import shutil
    def fixture(base):
        repo = base / 'devtool'
        frozen = json.loads((ROOT / 'docs/actions/ACTIONS_G08_QUALIFICATION_MATRIX.json').read_text())
        paths = {'docs/actions/ACTIONS_G08_QUALIFICATION_MATRIX.json',
                 frozen['original_ledger_path'],
                 'docs/actions/ACTIONS_G03_R01_FIXTURE_TRANSITION_20261020.json',
                 'docs/actions/ACTIONS_G02_SOURCE_TRANSITION_20261020.json',
                 'docs/actions/ACTIONS_G04_JARSIGNER_UNSIGNED_VERDICT_HOTFIX.md',
                 'tests/test_actions_g04_unsigned_jarsigner_truth.py',
                 'docs/actions/ACTIONS_G03_NARROW_TEXTUAL_FIX.md',
                 'tests/test_actions_g03_compact_narrow.py',
                 'docs/actions/ACTIONS_G03_G8_SOURCE_AMENDMENT_20261014.md',
                 'tests/test_actions_g08_g03_amendment.py',
                 'tests/test_actions_a08_local_handoff.py',
                 'docs/actions/ACTIONS_A08_PRIVATE_HANDOFF.md'}
        paths.update(c['path'] for w in frozen['waves'] for c in w['source_contracts'])
        for name in paths:
            target = repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            # Validate the frozen G8/R01 authority against its original accepted
            # source snapshot, not against later R04-R09 implementation edits.
            historical = subprocess.run(['git', '-C', str(ROOT), 'show',
                                         'ea93004437c1f5d7a1c8353aef834b1844ddcc38:' + name], capture_output=True)
            if historical.returncode:
                raise AssertionError('Missing immutable R01 historical Git object: ' + name)
            target.write_bytes(historical.stdout)
        return repo
    repo = fixture(tmp_path)
    baseline = qualification_report(repo, strict_source=True)
    records = [x for x in baseline['verified_g2_source_amendments'] if x['wave'] == 'G2']
    assert len(records) == 1 and records[0]['path'] == 'devtool/actions_pipeline.py'
    assert baseline['qualified_release'] is False
    assert baseline['pending_required_gate_count'] == 8
    for rel in ('docs/actions/ACTIONS_G02_SOURCE_TRANSITION_20261020.json',
                'devtool/actions_pipeline.py'):
        check = tmp_path / ('tamper-' + rel.replace('/', '-'))
        check.mkdir()
        candidate = fixture(check)
        path = candidate / rel
        path.write_bytes(path.read_bytes() + b'\nunauthorized')
        with pytest.raises(GitHubActionsError, match='source contract'):
            qualification_report(candidate, strict_source=True)


def test_index_preserves_open_external_gates():
    index = json.loads((ROOT / 'docs/DEVTOOL_IMPROVEMENT_INDEX.json').read_text())
    assert index['baseline']['sha'] == '6f7b1de146838dca2458eaa35d54cc8dd4445f70'
    ids = {row['id'] for row in index['issues']}
    assert all('DT-%03d' % n in ids for n in range(1, 24))
    assert len(ids) == len(index['issues'])
    for key in ('ENV-A06','ANDROID-A16','DIST-A01','DIST-A12','DIST-A13','TEST-A11'):
        entry = next(row for row in index['issues'] if row['id'] == key)
        assert entry['qualification_status'] == 'BLOCKED_EXTERNAL'
    assert index['formal_release_qualified'] is False
