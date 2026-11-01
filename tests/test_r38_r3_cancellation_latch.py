"""R38-R3: cancellation authority between scheduler admission and process spawn."""
from __future__ import annotations

from pathlib import Path
from threading import Event

import pytest
from devtool.models import CommandSpec, OperationResult, ProjectConfig, WorkspaceConfig, WorkflowSchedulerConfig
from devtool.workflow_scheduler import run_workflow_specs


class CancellationHUD:
    current_phase = None

    def __init__(self):
        self.signal = Event()
        self.checked = 0
        self.plans = []

    def plan_phases(self, phases):
        self.plans.append(phases)

    def cancel_requested(self):
        self.checked += 1
        return self.signal.is_set()


def config(root: Path, parallel=False):
    return ProjectConfig(repo_root=root, kind="command", runner="command", project_root=str(root),
                         workspace=WorkspaceConfig(scheduler=WorkflowSchedulerConfig(parallel=parallel, max_parallel_nodes=3)))


def spec(key: str, depends=()):
    return CommandSpec(key, ["true"], metadata={
        "workflow_node_key": key, "workflow_target": "app", "workflow_name": "build",
        "workflow_depends_on": list(depends), "workflow_read_only": True,
        "workflow_write_scope_known": True, "workflow_write_scopes": [],
    })


def fake_runner(launched):
    def run(config, current, **kwargs):
        key = current[0].metadata["workflow_node_key"]
        cancelled = kwargs.get("workflow_initial_cancelled", False)
        blocked = any(kwargs.get("workflow_initial_statuses", {}).get(d) != "passed"
                      for d in current[0].metadata.get("workflow_depends_on", []))
        if cancelled or blocked:
            state = "cancelled" if cancelled else "blocked"
            return [OperationResult(key, [], 0, 0, state, [], metadata={"workflow_node_key": key, "workflow_node_state": state})], state
        launched.append(key)
        return [OperationResult(key, ["true"], 0, 0, "ok", [], metadata={"workflow_node_key": key})], "ok"
    return run


def test_observer_stop_before_spawn_cancels_entire_dag(tmp_path, monkeypatch):
    import devtool.runners as runners
    hud = CancellationHUD()
    launched = []
    monkeypatch.setattr(runners, "run_specs", fake_runner(launched))
    events=[]

    def observer(key, status, results):
        events.append((key, status))
        if status == "started" and key == "a":
            hud.signal.set()

    results, _, plan = run_workflow_specs(config(tmp_path), [spec("a"), spec("b")],
        verbose=False, dry_run=False, continue_on_error=False, log_path=None,
        tasker_progress=hud, parallel_requested=True, node_observer=observer)
    assert launched == []
    assert plan is not None
    assert dict(plan.node_outcomes) == {"a": "cancelled", "b": "cancelled"}
    assert all(x.metadata["workflow_node_state"] == "cancelled" for x in results)
    assert ("a", "terminal") in events and ("b", "terminal") in events


def test_cancel_latches_across_transient_hud_reset(tmp_path, monkeypatch):
    import devtool.runners as runners
    hud = CancellationHUD()
    launched = []
    def runner(config, current, **kwargs):
        key = current[0].metadata["workflow_node_key"]
        if key == "a":
            # Real work emits a cancellation, while its synthetic HUD clears it.
            # The worker proxy must see that stop and preserve it afterwards.
            hud.signal.set()
            assert kwargs["tasker_progress"].cancel_requested() is True
            hud.signal.clear()
            launched.append(key)
            return [OperationResult(key, ["true"], 0, 0, "ok", [], metadata={"workflow_node_key": key})], "ok"
        return fake_runner(launched)(config, current, **kwargs)
    monkeypatch.setattr(runners, "run_specs", runner)
    results, _, plan = run_workflow_specs(config(tmp_path), [spec("a"), spec("b", ("a",))],
        verbose=False, dry_run=False, continue_on_error=False, log_path=None,
        tasker_progress=hud, parallel_requested=True)
    assert launched == ["a"]
    assert plan is not None
    assert dict(plan.node_outcomes) == {"a": "passed", "b": "cancelled"}
    assert results[-1].metadata["workflow_node_state"] == "cancelled"


def test_cancellation_disabled_keeps_normal_execution(tmp_path, monkeypatch):
    import devtool.runners as runners
    launched=[]
    monkeypatch.setattr(runners, "run_specs", fake_runner(launched))
    _, _, plan = run_workflow_specs(config(tmp_path), [spec("a"), spec("b", ("a",))],
        verbose=False, dry_run=False, continue_on_error=False, log_path=None,
        tasker_progress=CancellationHUD(), parallel_requested=True)
    assert launched == ["a", "b"]
    assert dict(plan.node_outcomes) == {"a": "passed", "b": "passed"}


def test_workflow_artifact_actions_are_node24(tmp_path):
    path = Path(__file__).resolve().parents[1] / ".github/workflows/devtool-full-repository-pytest.yml"
    source = path.read_text()
    assert source.count("actions/upload-artifact@v7") == 2
    assert source.count("actions/download-artifact@v8") == 1
    for old in ("actions/upload-artifact@v5", "actions/download-artifact@v5"):
        assert old not in source
