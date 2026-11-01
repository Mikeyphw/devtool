"""R39-R1: source-compatible R38 execution-policy consolidation."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

from devtool.models import CommandSpec, OperationResult, ProjectConfig, WorkspaceConfig, WorkflowSchedulerConfig
from devtool.workflow_execution_policy import (
    WorkflowExecutionPolicyError, resolve_workflow_execution_policy,
)
from devtool.workflow_scheduler import WorkflowScheduleError, run_workflow_specs


def _policy(*, audit='off', scratch='off', dry=False, plan=True, parallel=False, envs=({},)):
    return resolve_workflow_execution_policy(
        write_audit_mode=audit, scratch_isolation_mode=scratch,
        dry_run=dry, plan_exists=plan, parallel_requested=parallel,
        command_envs=envs, environment={},
    )


@pytest.mark.parametrize('audit,scratch,reason', [
    ('off', 'off', None),
    ('enforce', 'off', 'r38-tracked-write-audit'),
    ('off', 'enforce', 'r38-run-scratch-isolation'),
    ('enforce', 'enforce', 'r38-run-scratch-isolation'),
    ('ENFORCE', ' Enforce ', 'r38-run-scratch-isolation'),
])
def test_modes_and_historical_reuse_precedence(audit, scratch, reason):
    value = _policy(audit=audit, scratch=scratch)
    assert value.audit_enabled == (audit.strip().lower() == 'enforce')
    assert value.scratch_enabled == (scratch.strip().lower() == 'enforce')
    assert value.reuse_disabled_reason == reason


def test_dry_run_disables_both_and_never_rejects_serial_or_reserved_env():
    value = _policy(audit='enforce', scratch='enforce', dry=True, parallel=True,
                    plan=False, envs=({'TMPDIR':'untouched'},))
    assert not value.audit_enabled and not value.scratch_enabled
    assert value.reuse_disabled_reason is None


@pytest.mark.parametrize('audit,scratch,error', [
    ('no', 'off', 'R38_INVALID_WRITE_AUDIT_MODE'),
    ('off', 'false', 'R38_INVALID_SCRATCH_ISOLATION_MODE'),
    ('enforce', 'off', 'R38_WRITE_AUDIT_REQUIRES_SERIAL_WORKFLOW'),
])
def test_invalid_modes_and_parallel_audit_refused(audit, scratch, error):
    with pytest.raises(WorkflowExecutionPolicyError, match=error):
        _policy(audit=audit, scratch=scratch, parallel=True)


@pytest.mark.parametrize('audit,scratch,error', [
    ('enforce','off','R38_WRITE_AUDIT_REQUIRES_SERIAL_WORKFLOW'),
    ('off','enforce','R38_SCRATCH_REQUIRES_NATIVE_WORKFLOW_NODES'),
])
def test_missing_plan_fail_closed(audit, scratch, error):
    with pytest.raises(WorkflowExecutionPolicyError, match=error):
        _policy(audit=audit, scratch=scratch, plan=False)


@pytest.mark.parametrize('name', [
    'TMPDIR','tmpdir','XDG_CACHE_HOME','npm_config_cache',
    'DEVTOOL_WORKFLOW_TMPDIR','DEVTOOL_R38_SCRATCH_ROOT',
])
def test_reserved_override_refused_for_any_node(name):
    with pytest.raises(WorkflowExecutionPolicyError, match='R38_SCRATCH_ENV_OVERRIDE_REFUSED'):
        _policy(scratch='enforce', envs=({'SAFE':'1'}, {name:'bad'}))


def test_r38_fallback_environment_and_explicit_override():
    value = resolve_workflow_execution_policy(
        write_audit_mode=None, scratch_isolation_mode='off', dry_run=False,
        plan_exists=True, parallel_requested=False, command_envs=({},),
        environment={'DEVTOOL_R38_WRITE_AUDIT':' enforce ',
                     'DEVTOOL_R38_SCRATCH_ISOLATION':'enforce'},
    )
    assert value.audit_enabled and not value.scratch_enabled


def _config(root):
    return ProjectConfig(repo_root=root, kind='command', runner='command', project_root=str(root),
                         workspace=WorkspaceConfig(scheduler=WorkflowSchedulerConfig(
                             parallel=False, max_parallel_nodes=2)))


def _spec(key):
    return CommandSpec(key, ['true'], metadata={
        'workflow_node_key':key,'workflow_target':'app','workflow_name':'build',
        'workflow_depends_on':[],'workflow_read_only':True,
        'workflow_write_scope_known':True,'workflow_write_scopes':[],
    })


def test_invalid_second_command_env_never_mutates_first_spec_or_mints(tmp_path, monkeypatch):
    import devtool.workflow_scheduler as scheduler
    monkeypatch.setattr(scheduler, 'provision_node_temp',
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError('minted')))
    a,b=_spec('a'),_spec('b')
    original_a=dict(a.metadata)
    b.env['TMPDIR']='forged'
    with pytest.raises(WorkflowScheduleError, match='R38_SCRATCH_ENV_OVERRIDE_REFUSED'):
        run_workflow_specs(_config(tmp_path), [a,b], verbose=False, dry_run=False,
                           continue_on_error=False, log_path=None,
                           scratch_isolation_mode='enforce')
    assert a.metadata == original_a
    assert 'reuse_disabled' not in b.metadata


def test_audit_snapshot_refusal_leaves_metadata_intact(tmp_path):
    a=_spec('a')
    metadata=dict(a.metadata)
    # Not a Git repository: R38 must reject, and must not modify caller state.
    with pytest.raises(WorkflowScheduleError, match='R38_GIT_PROOF_UNAVAILABLE'):
        run_workflow_specs(_config(tmp_path), [a], verbose=False, dry_run=False,
                           continue_on_error=False, log_path=None,
                           write_audit_mode='enforce')
    assert a.metadata == metadata


def test_native_default_path_untouched(tmp_path, monkeypatch):
    import devtool.runners as runners
    launched=[]
    def fake(config, specs, **kwargs):
        launched.append((dict(specs[0].env),dict(specs[0].metadata)))
        return [OperationResult('a', [],0,0,'ok', [], metadata={'workflow_node_key':'a'})], 'ok'
    monkeypatch.setattr(runners,'run_specs',fake)
    s=_spec('a')
    out,_,_=run_workflow_specs(_config(tmp_path),[s],verbose=False,dry_run=False,
                                continue_on_error=False,log_path=None,
                                write_audit_mode='off',scratch_isolation_mode='off')
    assert out[0].returncode==0
    assert launched[0][0]=={}
    assert 'reuse_disabled' not in launched[0][1]


def test_historical_error_precedence_audit_serial_before_scratch_option():
    with pytest.raises(WorkflowExecutionPolicyError, match='R38_WRITE_AUDIT_REQUIRES_SERIAL_WORKFLOW'):
        _policy(audit='enforce', scratch='invalid', parallel=True)


def test_both_policies_use_single_native_lease_and_audit(tmp_path):
    repo=tmp_path/'repo';repo.mkdir()
    (repo/'tracked.txt').write_text('initial')
    for args in (('init','-q'),('config','user.name','R39 Test'),
                 ('config','user.email','r39@example.invalid'),('add','.'),
                 ('commit','-qm','base')):
        subprocess.run(['git','-C',str(repo),*args],check=True,capture_output=True)
    spec=_spec('a')
    spec.argv=[sys.executable,'-c','print("hello")']
    spec.cwd=str(repo)
    result,_,plan=run_workflow_specs(
        _config(repo),[spec],verbose=False,dry_run=False,
        continue_on_error=False,log_path=None,
        write_audit_mode='enforce',scratch_isolation_mode='enforce',
    )
    assert plan is not None and result[0].returncode==0
    assert result[0].metadata['workflow_r38_tracked_write_audit']['status']=='pass'
    assert result[0].metadata['workflow_temporary_cleanup']['status']=='cleaned'
    assert spec.metadata['reuse_disabled_reason']=='r38-run-scratch-isolation'
    assert not Path(spec.env['DEVTOOL_R38_SCRATCH_ROOT']).exists()
