"""DT08/03/12: source-bound native milestone ledger and existing guards."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

EXPECTED_IDS={'DT-20261116-01','DT-20261117-02','DT-20261117-03','DT-20261117-04',
    'DT-20261120-05','DT-20261121-06','DT-20261121-07','DT-20261121-08',
    *{f'DT-20261122-{n:02d}' for n in range(9,19)},
    *{f'DT-20261123-{n:02d}' for n in range(19,28)},
    *{f'DT-20261124-{n:02d}' for n in range(28,35)}}
SOURCE_FIXES={'DT-20261123-19','DT-20261123-20','DT-20261123-21',
    'DT-20261123-23','DT-20261124-28','DT-20261124-29',
    'DT-20261124-33','DT-20261124-34'}


def test_native_milestone_has_34_source_bound_nonaccepted_dispositions():
    root=Path(__file__).resolve().parents[1]
    data=json.loads((root/'docs/DEVTOOL_HOTFIX_BUG_DISPOSITION.json').read_text())
    rows=data['entries'];ids=[r['id'] for r in rows]
    assert len(ids)==len(set(ids))==34
    assert set(ids)==EXPECTED_IDS
    assert data['source_commit']=='05e120d185ce8e81287b591bac6a221054abe94e'
    assert data['source_tree']=='501f23e6fe0ed753090282e6bfc4ea7158c5c5d1'
    assert data['candidate_fix_count']==len(SOURCE_FIXES)
    assert data['acceptance_gate']=='PENDING_NATIVE_PR_CI24_SECURITY'
    for row in rows:
        assert row['native_fix_commit']=='NOT_COMMITTED_BY_THIS_HOTFIX'
        assert row['new_hosted_ci']=='NOT_RUN'
        assert row['new_ci24']=='NOT_RUN'
        assert row['release_qualified'] is False
    for row in rows:
        if row['id'] in SOURCE_FIXES:
            assert row['assessment'].startswith('HOTFIX_SOURCE_CANDIDATE')
    assert (root/'docs/DEVTOOL_LEDGER_HOTFIX_RECONCILIATION.md').is_file()


def test_m3_failed_validation_is_not_labelled_postcommit_adoption():
    from devtool.native_delivery_continuity import classify_native_commit_phase
    failed=SimpleNamespace(name='artifact-validate-script',returncode=2,skipped=False)
    assert classify_native_commit_phase([failed])=='PRECOMMIT_VALIDATION_FAILED'
    successful=SimpleNamespace(name='git-commit',returncode=0,skipped=False)
    assert classify_native_commit_phase([successful])=='COMMITTED_ADOPTION_REQUIRED'
    uncertain=SimpleNamespace(name='git-commit',returncode=1,skipped=False)
    assert classify_native_commit_phase([uncertain])=='COMMIT_UNCERTAIN_REQUIRES_HISTORY'
    assert classify_native_commit_phase([])=='NO_QUALIFIED_COMMIT_RESULT'


def test_declared_disabled_phases_stay_disabled_when_validate_explicit(monkeypatch):
    from devtool import cli
    manifest={'type':'overlay','validation':{'required':True,'restore':False,
                                              'build':False,'test':False}}
    monkeypatch.setattr(cli,'_artifact_arg_list',lambda *_:['ignored.zip'])
    monkeypatch.setattr(cli,'_read_artifact_manifest',lambda *_:manifest)
    flags=argparse.Namespace(command='apply-overlay',artifact='ignored.zip',
        validate=True,validate_only=False,defer_validate=False,static_validate=False,
        no_validate=False,skip_restore=False,skip_build=False,skip_test=False,
        target='',solution='',test_task=[],commit='',no_commit=True,commit_mode='none',
        preview=False,diff=False,interactive=False)
    cli._apply_artifact_manifest_defaults(flags)
    assert flags.validate is True
    assert flags.skip_restore and flags.skip_build and flags.skip_test
    # The source validator is distinct and must remain eligible for execution.
    assert flags.static_validate is False
