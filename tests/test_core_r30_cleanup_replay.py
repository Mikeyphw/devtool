"""Synthetic read/write tests never authorize deletion of a user ZIP."""
import json
import importlib.util
from pathlib import Path
import subprocess
import sys
import types

import pytest
from devtool import artifact_cleanup_worker as worker
ROOT=Path(__file__).resolve().parents[1]


def _load_reviewed_tool(stem: str):
    """Bypass foreign ``tools`` packages (Hermes Agent on Termux)."""
    source=(ROOT/'tools'/(stem+'.py')).resolve(strict=True)
    if source.parent != (ROOT/'tools').resolve(strict=True):
        raise ValueError('REVIEWED_TOOL_OUTSIDE_REPOSITORY')
    spec=importlib.util.spec_from_file_location('_core_r30_reviewed_'+stem,source)
    if spec is None or spec.loader is None:
        raise RuntimeError('REVIEWED_TOOL_IMPORT_UNAVAILABLE')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if Path(module.__file__).resolve(strict=True)!=source:
        raise RuntimeError('REVIEWED_TOOL_IDENTITY_MISMATCH')
    return module


replay=_load_reviewed_tool('core_r30_cleanup_replay')
audit=_load_reviewed_tool('core_r30_contract_audit')


def test_worker_outer_refusal_never_echoes_secret_or_path(capsys,monkeypatch,tmp_path):
    secret='top-secret-xyz-no-logging'
    def fail(*args,**kwargs):
        raise ValueError(secret+' '+str(tmp_path/'private/overlay.zip'))
    monkeypatch.setattr(worker,'_read_authority',fail)
    rc=worker.main(['--repo-root',str(tmp_path),'--reason','after successful apply',
                    '--authority-fd','5',str(tmp_path/'artifact.zip')])
    doc=json.loads(capsys.readouterr().out)
    assert rc==1 and doc['returncode']==1
    assert doc['metadata']['error_category']=='APPLY_AUTHORITY_REFUSED'
    assert secret not in json.dumps(doc) and str(tmp_path) not in json.dumps(doc)
    assert doc['metadata']['safe_to_retry_unlink'] is False


def test_worker_setup_refusal_strips_raw_message(monkeypatch,tmp_path):
    secret='never-echo-sentinel'
    def fail(*args,**kwargs):raise ValueError(secret)
    monkeypatch.setattr(worker,'external_apply_cleanup_plan_id',fail)
    repo=tmp_path/'repo';repo.mkdir()
    subprocess.run(['git','init','-q',str(repo)],check=True)
    victim=tmp_path/'something.zip';victim.write_bytes(b'preserved')
    d=worker.cleanup_apply_source_artifacts(repo,[victim],reason='after successful apply')
    assert d['returncode']==1 and secret not in json.dumps(d)
    assert victim.read_bytes()==b'preserved'


def test_synthetic_replay_requires_explicit_approval(tmp_path):
    with pytest.raises(ValueError,match='EXPLICIT_SYNTHETIC_REPLAY_APPROVAL_REQUIRED'):
        replay.replay(ROOT,tmp_path,authorized=False)
    assert not list(tmp_path.glob(replay.FIXTURE_PREFIX+"*.zip"))


def test_synthetic_real_worker_authority_drift_and_no_auth(tmp_path,monkeypatch):
    monkeypatch.setattr(replay,'_git',lambda *args,cwd: '' if args[0]=='status' else 'a'*40)
    scratch=tmp_path/'Download';scratch.mkdir()
    neighbor=scratch/'real-user-overlay.zip';neighbor.write_bytes(b'MY BYTES')
    proof=replay.replay(ROOT,scratch,authorized=True)
    assert proof['status']=='PASS',proof
    assert set(proof['scenarios'])=={'positive','drift','no-authority'}
    assert proof['scenarios']['positive']['passed']
    assert proof['scenarios']['drift']['passed']
    assert proof['scenarios']['no-authority']['passed']
    assert proof['real_apply_lifecycle_qualified'] is False
    assert proof['release_qualified'] is False
    assert neighbor.read_bytes()==b'MY BYTES'
    assert proof['scenarios']['drift']['fixture_removed_after_new_authority'] is True
    assert proof['scenarios']['no-authority']['fixture_removed_after_new_authority'] is True
    leftovers=list(scratch.glob(replay.FIXTURE_PREFIX+'*.zip'))
    assert not leftovers and not proof['residue_names']
    assert not any(str(scratch) in json.dumps(x) for x in proof['scenarios'].values())


def test_replay_rejects_dirty_source_before_creating_file(tmp_path,monkeypatch):
    monkeypatch.setattr(replay,'_git',lambda *args,cwd: 'untracked.data' if args[0]=='status' else 'a'*40)
    with pytest.raises(ValueError,match='SOURCE_WORKTREE_DIRTY'):
        replay.replay(ROOT,tmp_path,authorized=True)
    assert not list(tmp_path.glob(replay.FIXTURE_PREFIX+"*.zip"))


def test_owned_file_refuses_preexisting_and_symlinks(tmp_path,monkeypatch):
    assert replay._new_owned_file(tmp_path,payload=b'own').read_bytes()==b'own'
    monkeypatch.setattr(replay.uuid,'uuid4',lambda: type('Obj',(),{'hex':'f'*32})())
    name=tmp_path/(replay.FIXTURE_PREFIX+'f'*32+'.zip')
    name.write_bytes(b'preexisting')
    with pytest.raises(FileExistsError):replay._new_owned_file(tmp_path,payload=b'bad')
    assert name.read_bytes()==b'preexisting'


def test_source_audit_refuses_dirty_before_external_checks(tmp_path,monkeypatch):
    monkeypatch.setattr(audit,'source',lambda repo: (_ for _ in ()).throw(ValueError('SOURCE_WORKTREE_DIRTY')))
    with pytest.raises(ValueError,match='SOURCE_WORKTREE_DIRTY'):
        audit.check(tmp_path)


def test_source_audit_closes_no_release_gate(tmp_path,monkeypatch):
    monkeypatch.setattr(audit,'source',lambda repo: {'sha':'a'*40,'tree':'b'*40})
    # synthetic generator, source-only; never touches the real repository
    gen=tmp_path/'tools'/'generate_complete_feature_guides.py'
    gen.parent.mkdir();gen.write_text('print("current")\n')
    cp=subprocess.CompletedProcess([],0,stdout=b'current',stderr=b'')
    monkeypatch.setattr(audit.subprocess,'run',lambda *args,**kwargs:cp)
    doc=audit.check(tmp_path)
    assert doc['status']=='PASS' and doc['release_qualified'] is False
    assert set(doc['checks'])=={'oci_canonical_embedded_digest','generated_guide_current','source_unchanged'}


def test_helper_loader_is_immune_to_foreign_tools_package(monkeypatch):
    # Mimic Termux Hermes Agent, which installs a concrete top-level tools
    # package before Devtool's unpackaged tools/ directory on sys.path.
    foreign=types.ModuleType('tools')
    foreign.__file__='/data/data/com.termux/files/usr/share/hermes-agent/tools/__init__.py'
    monkeypatch.setitem(sys.modules,'tools',foreign)
    for name in ('core_r30_cleanup_replay','core_r30_contract_audit'):
        loaded=_load_reviewed_tool(name)
        assert Path(loaded.__file__).resolve()==(ROOT/'tools'/(name+'.py')).resolve()
    assert sys.modules['tools'] is foreign
