"""Regression for Devtool canonical/legacy CI24 qualification receipt identity.

No GitHub API and no native transaction are invoked in these tests.
"""
from __future__ import annotations
import json
from pathlib import Path
import pytest
from devtool import qualification_workflows as q
from devtool.github_actions import GitHubActionsError

SHA = "a" * 40


def setup_paths(tmp_path: Path, monkeypatch):
    root = tmp_path / 'source'
    root.mkdir()
    legacy = tmp_path / 'xdg' / 'devtool'
    current = tmp_path / 'custom'
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'xdg'))
    monkeypatch.setenv('DEVTOOL_XDG_DIR', str(current))
    return root, current, legacy


def valid_record(root, kind='ci24', state='DISPATCH_OUTCOME_UNKNOWN'):
    return {'schema':1, 'kind':kind, 'repo_root':str(root.resolve()),
            'source_sha':SHA, 'state':state, 'workflow_id':42,
            'repository':'Mikeyphw/devtool','run_id':None, 'request_id':None}


def test_qualification_uses_canonical_state_root_and_private_journal(tmp_path, monkeypatch):
    root, current, legacy = setup_paths(tmp_path, monkeypatch)
    path = q._state_path(root,SHA,'ci24')
    assert path.is_relative_to(current)
    assert not path.is_relative_to(legacy)
    q._save(root,SHA,'ci24', valid_record(root))
    assert path.exists()
    assert q._load(root,SHA,'ci24')['source_sha']==SHA


def test_prior_ci24_receipt_discovered_but_not_moved_or_duplicated(tmp_path, monkeypatch):
    root, current, legacy = setup_paths(tmp_path, monkeypatch)
    old = legacy / 'qualification-workflows' / q._state_dirs(root)[0].name / f'{SHA}-ci24.json'
    q._durable_record(old,valid_record(root))
    assert q._load(root,SHA,'ci24')['state']=='DISPATCH_OUTCOME_UNKNOWN'
    assert q._state_path(root,SHA,'ci24')==old
    q._save(root,SHA,'ci24',valid_record(root,state='WAITING_REMOTE'))
    assert q._load(root,SHA,'ci24')['state']=='WAITING_REMOTE'
    assert not (current/'qualification-workflows'/old.parent.name/old.name).exists()


def test_legacy_receipt_prevents_another_ci24_post(tmp_path, monkeypatch):
    root, current, legacy = setup_paths(tmp_path, monkeypatch)
    old = legacy/'qualification-workflows'/q._state_dirs(root)[0].name/f'{SHA}-ci24.json'
    q._durable_record(old,valid_record(root))
    monkeypatch.setattr(q,'_identity',lambda _: (SHA,'main'))
    monkeypatch.setattr(q,'dispatch_workflow',lambda *a,**kw:pytest.fail('DUPLICATE POST'))
    with pytest.raises(GitHubActionsError,match='NEVER dispatch twice'):
        q.ci24_start(root,client=object(),slug='Mikeyphw/devtool')


def test_dual_state_root_receipts_refuse_duplicate_and_no_auto_migration(tmp_path, monkeypatch):
    root, current, legacy = setup_paths(tmp_path, monkeypatch)
    canonical, old = q._state_dirs(root)
    q._durable_record(canonical/f'{SHA}-ci24.json', valid_record(root))
    q._durable_record(old/f'{SHA}-ci24.json', valid_record(root))
    with pytest.raises(GitHubActionsError,match='Conflicting canonical and legacy'):
        q._load(root,SHA,'ci24')
    assert (canonical/f'{SHA}-ci24.json').exists()
    assert (old/f'{SHA}-ci24.json').exists()


def test_legacy_lock_refuses_new_ci24_operation(tmp_path, monkeypatch):
    root, current, legacy = setup_paths(tmp_path, monkeypatch)
    old=q._state_dirs(root)[1]
    old.mkdir(mode=0o700,parents=True)
    (old/f'{SHA}-ci24.lock').mkdir(mode=0o700)
    with pytest.raises(GitHubActionsError,match='Legacy qualification operation active'):
        with q._lock(root,SHA,'ci24'):
            pytest.fail('lock should not be owned')


def test_relative_xdg_state_home_does_not_create_cwd_relative_journals(tmp_path, monkeypatch):
    root, current, legacy = setup_paths(tmp_path, monkeypatch)
    monkeypatch.delenv('DEVTOOL_XDG_DIR')
    monkeypatch.setenv('XDG_STATE_HOME','relative-state')
    assert q._state_dir(root).is_absolute()
    assert q._state_path(root,SHA,'ci24').is_absolute()
