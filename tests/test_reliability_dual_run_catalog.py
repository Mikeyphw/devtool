"""DT-14: real dual RUN-V3 authority integration, no remote or Android writes."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from devtool import storage_layout as sl
from devtool.run_store import RunCatalog


def fixture(tmp_path, monkeypatch, *, active=False, collision=False):
    repo = tmp_path / 'repo'
    repo.mkdir()
    state = tmp_path / 'state'
    custom = tmp_path / 'custom'
    monkeypatch.setenv('XDG_STATE_HOME', str(state))
    monkeypatch.setenv('DEVTOOL_XDG_DIR', str(custom))
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'data'))
    roots = sl.legacy_family_dirs(repo, 'run-v3')
    assert len(roots) == 2
    for i, root in enumerate(roots):
        catalog = RunCatalog.open(root)
        catalog.add_run({'run_id': 'run-0' if collision else f'run-{i}',
                         'status': 'running' if active and i == 1 else 'completed',
                         'created_at': i})
        catalog.save()
    return repo, roots, custom


def digests(roots):
    return tuple(sl._fingerprint_dir(root)[0] for root in roots)


def test_plan_is_read_only_and_reports_provenance(tmp_path,monkeypatch):
    repo, roots, custom = fixture(tmp_path,monkeypatch)
    before = digests(roots)
    out = sl.reconcile_run_catalog_plan(repo)
    assert out['ready'] and out['mutates'] is False
    assert out['merged_counts']['runs'] == 2
    assert tuple(row['digest'] for row in out['source_catalogs']) == before
    assert not sl._marker(repo,'run-v3').exists()
    assert not sl.external_family_dir(repo,'run-v3').exists()
    assert digests(roots) == before


def test_approved_exact_reconciliation_retains_all_records_and_old_bytes(tmp_path,monkeypatch):
    repo, roots, custom = fixture(tmp_path,monkeypatch)
    before = digests(roots)
    report = sl.reconcile_run_catalogs(repo,confirmed=True,expected_digests=before)
    assert report['state'] == 'RECONCILED' and report['originals_preserved'] is True
    assert digests(roots) == before
    assert sl.run_catalog_dir(repo) == sl.external_family_dir(repo,'run-v3')
    catalog = RunCatalog.open(sl.run_catalog_dir(repo),create=False)
    assert set(catalog.runs) == {'run-0','run-1'}
    assert sl._read_marker(repo,'run-v3')['catalog_sha256'] == report['catalog_sha256']
    assert sl.reconcile_run_catalog_plan(repo)['ready'] is False
    with pytest.raises(sl.StorageLayoutError,match='Stale storage writer'):
        RunCatalog.open(roots[0],create=False).save()


def test_unconfirmed_and_wrong_digests_do_not_publish(tmp_path,monkeypatch):
    repo, roots, custom = fixture(tmp_path,monkeypatch)
    before = digests(roots)
    with pytest.raises(sl.StorageLayoutError,match='requires --yes'):
        sl.reconcile_run_catalogs(repo,expected_digests=before)
    with pytest.raises(sl.StorageLayoutError,match='explicit exact match'):
        sl.reconcile_run_catalogs(repo,confirmed=True,expected_digests=(before[0], '0'*64))
    assert not sl._marker(repo,'run-v3').exists()
    assert digests(roots) == before


def test_active_history_is_protected_not_reclassified(tmp_path,monkeypatch):
    repo, roots, custom = fixture(tmp_path,monkeypatch,active=True)
    before=digests(roots)
    plan=sl.reconcile_run_catalog_plan(repo)
    assert not plan['ready'] and plan['blockers']
    with pytest.raises(sl.StorageLayoutError,match='blocked'):
        sl.reconcile_run_catalogs(repo,confirmed=True,expected_digests=before)
    assert digests(roots) == before
    assert not sl._marker(repo,'run-v3').exists()


def test_unknown_history_is_protected(tmp_path,monkeypatch):
    repo, roots, custom = fixture(tmp_path,monkeypatch)
    old=RunCatalog.open(roots[1],create=False)
    old.runs['run-1']['status']='applied-pending-validation'
    old.save()
    plan=sl.reconcile_run_catalog_plan(repo)
    assert not plan['ready']
    assert any('unknown statuses' in b for b in plan['blockers'])


def test_run_id_collision_is_refused_even_with_matching_status(tmp_path,monkeypatch):
    repo, roots, custom = fixture(tmp_path,monkeypatch,collision=True)
    assert not sl.reconcile_run_catalog_plan(repo)['ready']
    with pytest.raises(sl.StorageLayoutError,match='blocked'):
        sl.reconcile_run_catalogs(repo,confirmed=True,expected_digests=digests(roots))
    assert not sl._marker(repo,'run-v3').exists()


def test_existing_unrelated_canonical_data_never_overwritten(tmp_path,monkeypatch):
    repo, roots, custom = fixture(tmp_path,monkeypatch)
    out=sl.external_family_dir(repo,'run-v3')
    sl.ensure_private_storage_dir(out)
    (out/'other.txt').write_text('not our state')
    assert 'canonical-run-root-already-populated' in sl.reconcile_run_catalog_plan(repo)['blockers']
    with pytest.raises(sl.StorageLayoutError,match='blocked'):
        sl.reconcile_run_catalogs(repo,confirmed=True,expected_digests=digests(roots))
    assert (out/'other.txt').read_text()=='not our state'


def test_byte_identical_interrupted_publication_recovers_marker(tmp_path,monkeypatch):
    repo, roots, custom = fixture(tmp_path,monkeypatch)
    records,payload,_ = sl._reconciled_catalog(repo)
    dest=sl.external_family_dir(repo,'run-v3')
    sl.ensure_private_storage_dir(dest)
    (dest/'catalog.json').write_bytes(payload)
    before=digests(roots)
    assert sl.reconcile_run_catalog_plan(repo)['publication_recovery_pending']
    out=sl.reconcile_run_catalogs(repo,confirmed=True,expected_digests=before)
    assert out['catalog_sha256']==hashlib.sha256(payload).hexdigest()
    assert sl.run_catalog_dir(repo)==dest
    assert digests(roots)==before


def test_source_drift_fails_before_publication(tmp_path,monkeypatch):
    repo, roots, custom=fixture(tmp_path,monkeypatch)
    recorded=digests(roots)
    changed=RunCatalog.open(roots[1],create=False)
    changed.add_run({'run_id':'newer','status':'completed'})
    changed.save()
    with pytest.raises(sl.StorageLayoutError,match='explicit exact match'):
        sl.reconcile_run_catalogs(repo,confirmed=True,expected_digests=recorded)
    assert not sl._marker(repo,'run-v3').exists()


def test_symlinked_legacy_parent_refused(tmp_path,monkeypatch):
    repo, roots, custom=fixture(tmp_path,monkeypatch)
    old=roots[1]
    moved=old.parent.with_name('private-not-canonical')
    os.rename(old.parent,moved)
    old.parent.symlink_to(moved, target_is_directory=True)
    assert not sl.reconcile_run_catalog_plan(repo)['ready']
    with pytest.raises(sl.StorageLayoutError,match='blocked'):
        sl.reconcile_run_catalogs(repo,confirmed=True,expected_digests=('0'*64,'1'*64))
