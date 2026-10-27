from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from devtool import ssh_qualification as q
from devtool import ssh_r07_reliability as rel
from devtool import ssh_returns as ret
from devtool import ssh_tmux_runner as tmux


@pytest.mark.parametrize('platform,home,prefix,kind', [
    ('termux','/data/data/com.termux/files/home','/data/data/com.termux/files/usr','termux-native'),
    ('termux','/home/user','/usr','termux-reported-unverified'),
    ('linux','/home/user','/usr','linux-login-shell'),
    ('unknown','/','', 'unknown'),
])
def test_remote_login_substrate_is_conservative(platform,home,prefix,kind):
    p=rel.worker_substrate(platform,home,prefix,proot_available=True,proot_ready=True)
    assert p['login_shell']==kind
    assert p['guest_execution_attested'] is False
    assert 'explicit confirmation' in p['supervisor_policy']


def test_stage_receipt_is_atomic_and_retains_stages_without_secrets(tmp_path):
    receipt = rel.QualificationReceipt.start(tmp_path,'tablet','user@192.168.1.2')
    receipt.checkpoint('source-sync', 'started')
    receipt.checkpoint('source-sync', 'passed')
    receipt.checkpoint('return-transfer','started')
    receipt.finish(passed=False,failure_type='ValueError')
    saved=json.loads(receipt.path.read_text())
    assert saved['state']=='failed'
    assert saved['stage']=='return-transfer'
    assert saved['qualification']=='not-qualified'
    assert [e['state'] for e in saved['stages']]==['started','passed','started','failed']
    assert 'user@192.168.1.2' not in receipt.path.read_text()
    assert oct(receipt.path.stat().st_mode & 0o777)=='0o600'
    assert not list(receipt.path.parent.glob('*.tmp'))


def test_unsafe_receipt_location_or_stage_is_rejected(tmp_path):
    (tmp_path/'.devtool').symlink_to(tmp_path / 'somewhere')
    with pytest.raises(ValueError,match='symlinked'):
        rel.QualificationReceipt.start(tmp_path,'tablet','worker')
    with pytest.raises(ValueError,match='safe profile'):
        rel.QualificationReceipt.start(tmp_path,'../bad','worker')


def test_receipt_preserves_interrupted_stage(tmp_path):
    receipt = rel.QualificationReceipt.start(tmp_path,'tablet','worker')
    receipt.checkpoint('tmux-probe','started')
    assert json.loads(receipt.path.read_text())['state']=='running'
    assert json.loads(receipt.path.read_text())['qualification']=='pending'


def _bundle(tmp_path, monkeypatch, run='run-123', session='session-0'):
    cfg=SimpleNamespace(profile='tablet',destination='u@host',port=8022)
    monkeypatch.setenv('DEVTOOL_RUN_ID',run)
    bundle=ret.create_return_bundle(cfg,local_root=tmp_path,host_cwd=str(tmp_path),remote_root='/remote',session=session,
                                   project_config=SimpleNamespace(runner_options={}),spec_metadata={},source_sync_id='sync-abc')
    return bundle


def test_inventory_reports_complete_partial_and_missing_without_recovery(tmp_path,monkeypatch):
    b=_bundle(tmp_path,monkeypatch)
    b.status_path.write_text('returncode=0\nstate=complete\n')
    before=b.status_path.read_bytes()
    r=rel.return_recovery_inventory(tmp_path,profile='tablet',destination='u@host')
    assert r['count']==1 and r['records'][0]['state']=='complete'
    assert r['records'][0]['recoverable'] is False
    assert b.status_path.read_bytes()==before
    b.status_path.write_text('returncode=23\nstate=partial\n')
    assert rel.return_recovery_inventory(tmp_path)['records'][0]['recoverable'] is True
    b.status_path.unlink()
    assert rel.return_recovery_inventory(tmp_path)['records'][0]['state']=='pending'
    assert not b.status_path.exists()


def test_inventory_does_not_follow_symlinks_and_refuses_forged_status(tmp_path,monkeypatch):
    b=_bundle(tmp_path,monkeypatch)
    b.status_path.symlink_to(tmp_path/'other')
    assert rel.return_recovery_inventory(tmp_path)['records'][0]['state']=='invalid'
    b.status_path.unlink()
    b.status_path.write_text('state=complete\nreturncode=2\n')
    assert rel.return_recovery_inventory(tmp_path)['records'][0]['state']=='invalid'
    b.status_path.write_text('state=complete\nstate=complete\nreturncode=0\n')
    assert rel.return_recovery_inventory(tmp_path)['records'][0]['state']=='invalid'
    b.manifest_path.rename(b.manifest_path.with_suffix('.bak'))
    b.manifest_path.symlink_to(b.manifest_path.with_suffix('.bak'))
    assert rel.return_recovery_inventory(tmp_path)['records'][0]['state']=='invalid'


def test_inventory_foreign_profile_ignored_and_missing_root_read_only(tmp_path,monkeypatch):
    assert rel.return_recovery_inventory(tmp_path)['records']==[]
    _bundle(tmp_path,monkeypatch)
    assert rel.return_recovery_inventory(tmp_path,profile='other')['count']==0


def test_performance_never_self_qualifies_unobserved_external_gates():
    r=rel.performance_projection({'project_validation_first_seconds':8,'project_validation_second_seconds':2})
    assert r['cold_warm_observed'] and r['warm_to_cold_ratio']==0.25
    assert not r['fs03_qualified'] and not r['notification_qualified']
    assert rel.performance_projection({})['warm_to_cold_ratio'] is None
    with pytest.raises(ValueError): rel.performance_projection({'tmux_seconds':-1})
    with pytest.raises(ValueError): rel.performance_projection({'tmux_seconds':float('nan')})


def test_qualification_failure_keeps_safe_durable_record(monkeypatch,tmp_path):
    cfg=tmux.SSHRunnerConfig(host='worker',profile='tablet',mode='commands',worker_tmux=True,workspace_mode='managed')
    monkeypatch.setattr(q,'worker_doctor_payload',lambda config: {'healthy':True})
    monkeypatch.setattr(q,'probe_worker',lambda config: (_ for _ in ()).throw(ValueError('SECRET-TOKEN-abc')))
    with pytest.raises(ValueError,match='SECRET-TOKEN'):
        q.qualify_worker(cfg,tmp_path)
    receipts=list((tmp_path/'.devtool'/'ssh-qualification'/'tablet').glob('*.json'))
    assert len(receipts)==1
    contents=receipts[0].read_text()
    assert 'SECRET-TOKEN' not in contents
    data=json.loads(contents)
    assert data['state']=='failed'
    assert data['stage']=='worker-probe'
    assert data['qualification']=='not-qualified'


def test_source_identity_is_recorded_for_committed_fixture(tmp_path):
    import subprocess
    subprocess.run(['git','init','-q'], cwd=tmp_path, check=True)
    subprocess.run(['git','-c','user.name=Fixture','-c','user.email=fixture@example.test','commit','--allow-empty','-qm','fixture'],cwd=tmp_path,check=True)
    receipt=rel.QualificationReceipt.start(tmp_path,'tablet','user@private-device.local')
    identity=json.loads(receipt.path.read_text())['source_identity']
    assert identity['state']=='local-git'
    assert len(identity['commit'])==40 and len(identity['tree'])==40
    assert identity['worktree_verified'] is False
    assert 'user@private-device.local' not in receipt.path.read_text()


def test_failure_receipt_stays_stage_specific_and_safe(tmp_path):
    receipt=rel.QualificationReceipt.start(tmp_path,'tablet','worker')
    receipt.checkpoint('tmux-probe','started')
    receipt.finish(passed=False,failure_type='TimeoutExpired')
    record=json.loads(receipt.path.read_text())
    assert record['failure_stage']=='tmux-probe'
    assert record['stages'][-1]['state']=='failed'
    assert record['qualification']=='not-qualified'


def test_inventory_includes_orphaned_status_and_is_strictly_bounded(tmp_path):
    state=tmp_path/'.devtool'/'ssh-return'/'a-run'/'a-session'
    state.mkdir(parents=True)
    (state/'transfer.status').write_text('state=partial\nreturncode=23\n')
    result=rel.return_recovery_inventory(tmp_path)
    assert result['count']==1 and result['records'][0]['state']=='orphaned'
    assert not result['records'][0]['recoverable']
    for n in range(4):
        (tmp_path/'.devtool'/'ssh-return'/f'bad-{n}').symlink_to(tmp_path)
    result=rel.return_recovery_inventory(tmp_path,limit=2)
    assert result['truncated'] is True and result['count']==2
    assert [v['state'] for v in result['records']]==['orphaned','unsafe']
    with pytest.raises(ValueError,match='limit'):
        rel.return_recovery_inventory(tmp_path,limit=0)


def test_inventory_rejects_symlinked_private_root(tmp_path):
    (tmp_path/'.devtool').symlink_to(tmp_path/'outer')
    result=rel.return_recovery_inventory(tmp_path)
    assert result['ok'] is False and result['reason']=='symlinked-state-root'


def test_native_termux_requires_home_and_prefix_not_guest_inherited_env():
    result=rel.worker_substrate('termux','/root','/data/data/com.termux/files/usr',proot_available=True,proot_ready=True)
    assert result['login_shell']=='termux-reported-unverified'
    assert not result['guest_execution_attested']


def test_explicit_keyboard_interrupt_records_failed_stage(monkeypatch,tmp_path):
    cfg=tmux.SSHRunnerConfig(host='worker',profile='tablet',mode='commands',worker_tmux=True,workspace_mode='managed')
    monkeypatch.setattr(q,'worker_doctor_payload',lambda config: {'healthy':True})
    def interrupted(config):
        raise KeyboardInterrupt()
    monkeypatch.setattr(q,'probe_worker',interrupted)
    with pytest.raises(KeyboardInterrupt):
        q.qualify_worker(cfg,tmp_path)
    files=list((tmp_path/'.devtool'/'ssh-qualification'/'tablet').glob('*.json'))
    assert len(files)==1
    saved=json.loads(files[0].read_text())
    assert saved['state']=='failed' and saved['failure_stage']=='worker-probe'
    assert saved['failed_exception_type']=='KeyboardInterrupt'


def test_unmeasured_performance_is_not_misrepresented_as_observed():
    observed=rel.performance_projection({'sync_seconds':1.5})
    assert observed['measured_positive_stages']==['sync_seconds']
    assert 'return_seconds' in observed['unmeasured_or_skipped_stages']
    assert not observed['cold_warm_observed']
    assert not observed['fs03_qualified'] and not observed['notification_qualified']
