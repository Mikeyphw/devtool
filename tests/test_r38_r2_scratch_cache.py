from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest

from devtool.models import CommandSpec, OperationResult, ProjectConfig, WorkspaceConfig, WorkflowSchedulerConfig
from devtool.workflow_scheduler import WorkflowScheduleError, run_workflow_specs
from devtool.workflow_scratch import create_isolated_environment, reserved_environment_names


def config(root: Path) -> ProjectConfig:
    return ProjectConfig(repo_root=root, kind="command", runner="command", project_root=str(root),
                         workspace=WorkspaceConfig(scheduler=WorkflowSchedulerConfig(parallel=False, max_parallel_nodes=2)))


def spec(key: str, dependencies: tuple[str, ...] = ()) -> CommandSpec:
    return CommandSpec(key, [sys.executable, "-c", "print('ok')"], metadata={
        "workflow_node_key": key, "workflow_target": "app", "workflow_name": "build",
        "workflow_depends_on": list(dependencies), "workflow_read_only": True,
        "workflow_write_scope_known": True, "workflow_write_scopes": []})


def run(root: Path, specs: list[CommandSpec], *, mode="enforce", dry=False, parallel=False):
    return run_workflow_specs(config(root), specs, verbose=False, dry_run=dry,
                              continue_on_error=False, log_path=None,
                              parallel_requested=parallel, scratch_isolation_mode=mode)


def test_defaults_keep_execution_environment_untouched(tmp_path, monkeypatch):
    import devtool.runners as runners
    checked=[]
    def fake(config, specs, **kwargs):
        checked.append(dict(specs[0].env))
        return [OperationResult(specs[0].name, [], 0, .01, "", [], metadata={"workflow_node_key": specs[0].metadata["workflow_node_key"]})], ""
    monkeypatch.setattr(runners, "run_specs", fake)
    s=spec("n")
    res, _, _=run(tmp_path, [s], mode="off")
    assert res[0].returncode==0
    assert checked[0]=={}
    assert s.env=={}


def test_enforce_mints_distinct_owned_env_and_cleans_both(tmp_path, monkeypatch):
    import devtool.runners as runners
    observed={}
    def fake(config, specs, **kwargs):
        s=specs[0]
        key=s.metadata['workflow_node_key']
        env=dict(s.env)
        observed[key]=env
        for k in ('TMPDIR','TEMP','TMP','XDG_CACHE_HOME','PIP_CACHE_DIR','GOCACHE','GRADLE_USER_HOME','UV_CACHE_DIR','PYTHONPYCACHEPREFIX','npm_config_cache'):
            assert Path(env[k]).is_dir()
        Path(env['XDG_CACHE_HOME'], 'cache.dat').write_text(key)
        return [OperationResult(key, [], 0, .01, '', [], metadata={'workflow_node_key': key})], ''
    monkeypatch.setattr(runners, 'run_specs',fake)
    specs=[spec('a'),spec('b')]
    res,_,_=run(tmp_path, specs, parallel=True)
    assert len(res)==2 and all(x.returncode==0 for x in res)
    assert observed['a']['TMPDIR']!=observed['b']['TMPDIR']
    assert observed['a']['XDG_CACHE_HOME']!=observed['b']['XDG_CACHE_HOME']
    assert all(not Path(env['DEVTOOL_R38_SCRATCH_ROOT']).exists() for env in observed.values())
    assert all(x.metadata['workflow_temporary_cleanup']['status']=='cleaned' for x in res)
    assert all(x.metadata['reuse_disabled'] for x in specs)


def test_invalid_mode_fails_before_worker(tmp_path):
    with pytest.raises(WorkflowScheduleError,match='R38_INVALID_SCRATCH_ISOLATION_MODE'):
        run(tmp_path,[spec('n')],mode='unknown')


@pytest.mark.parametrize('forged', ['TMPDIR','tmpdir','XDG_CACHE_HOME','npm_config_cache','DEVTOOL_WORKFLOW_TMPDIR','DEVTOOL_R38_SCRATCH_ROOT'])
def test_spec_override_refused_before_provision(tmp_path,forged,monkeypatch):
    import devtool.workflow_scheduler as scheduler
    def forbidden(*a,**k):
        raise AssertionError('Should reject before provisioning')
    monkeypatch.setattr(scheduler,'provision_node_temp',forbidden)
    s=spec('n');s.env[forged]='/tmp/evil'
    with pytest.raises(WorkflowScheduleError,match='R38_SCRATCH_ENV_OVERRIDE_REFUSED'):
        run(tmp_path,[s])


def test_second_spec_override_caught_before_any_mint(tmp_path,monkeypatch):
    import devtool.workflow_scheduler as scheduler
    monkeypatch.setattr(scheduler,'provision_node_temp',lambda *a,**k: (_ for _ in ()).throw(AssertionError('mint')))
    a,b=spec('a'),spec('b');b.env['GOCACHE']='/tmp/out'
    with pytest.raises(WorkflowScheduleError,match='R38_SCRATCH_ENV_OVERRIDE_REFUSED'):
        run(tmp_path,[a,b])


def test_ownership_inode_mismatch_refused(tmp_path):
    p=tmp_path/'owned';p.mkdir(mode=0o700)
    st=p.stat()
    with pytest.raises(ValueError,match='R38_SCRATCH_OWNERSHIP_MISMATCH'):
        create_isolated_environment(p,(st.st_dev,st.st_ino+1))
    assert list(p.iterdir())==[]


def test_real_native_child_honors_temporary_env_and_cleanup(tmp_path,monkeypatch):
    s=spec('a')
    s.argv=[sys.executable,'-c', '''import os,pathlib; a=pathlib.Path(os.environ['TMPDIR']);b=pathlib.Path(os.environ['XDG_CACHE_HOME']);assert a.is_dir() and b.is_dir(); (b/'item').write_text('ok');print(os.environ['DEVTOOL_R38_SCRATCH_ROOT'])''']
    s.cwd=str(tmp_path)
    results, raw, plan=run(tmp_path,[s])
    assert len(results)==1 and results[0].returncode==0,raw
    assert results[0].metadata['workflow_temporary_cleanup']['status']=='cleaned'
    assert not Path(s.env['DEVTOOL_R38_SCRATCH_ROOT']).exists()


def test_dry_run_never_provisions_or_rewrites_env(tmp_path,monkeypatch):
    import devtool.workflow_scheduler as scheduler
    monkeypatch.setattr(scheduler, 'provision_node_temp',lambda *a,**kw: (_ for _ in ()).throw(AssertionError('no provisioning')))
    s=spec('n')
    run(tmp_path,[s],dry=True)
    assert not s.env


def test_regression_workflow_uses_node24_compatible_actions():
    workflow=(Path(__file__).resolve().parents[1]/'.github/workflows/devtool-full-repository-pytest.yml').read_text()
    for name,major in [('checkout','v6'),('setup-python','v6'),('upload-artifact','v7'),('download-artifact','v8')]:
        assert f'actions/{name}@{major}' in workflow
    assert 'actions/checkout@v4' not in workflow
    assert 'actions/setup-python@v5' not in workflow
    assert 'actions/upload-artifact@v4' not in workflow
    assert 'actions/download-artifact@v4' not in workflow
