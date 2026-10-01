from __future__ import annotations

import json
from pathlib import Path

import pytest

from devtool.core.events import (
    EventBus,
    NodeDiscovered,
    NodeFinished,
    NodeStarted,
    RunFinished,
    RunStarted,
    get_execution_store,
)
from devtool.core.execution_lifecycle import audit_execution_lifecycle, finalize_execution_run
from devtool.core.execution_subscribers import ExecutionProjectionSubscriber
from devtool.models import CommandSpec, OperationResult, ProjectConfig, WorkflowSchedulerConfig, WorkspaceConfig
from devtool.workflow_scheduler import run_workflow_specs


def _bus(tmp_path: Path, run_id: str) -> EventBus:
    bus = EventBus()
    bus.subscribe_all(ExecutionProjectionSubscriber(store=bus.execution_store, run_id=run_id, directory=tmp_path))
    return bus


def test_observed_nonterminal_descendants_are_reconciled_without_overwriting_primary_failure(tmp_path: Path) -> None:
    run_id = "exec-g3-observed"
    bus = _bus(tmp_path, run_id)
    bus.publish(RunStarted(run_id=run_id, command="invoke", project="devtool"))
    bus.publish(NodeDiscovered(run_id=run_id, node_id="root.failure", label="root failure"))
    bus.publish(NodeStarted(run_id=run_id, node_id="root.failure", label="root failure"))
    bus.publish(NodeFinished(run_id=run_id, node_id="root.failure", status="failed", exit_code=7, failure_kind="test_failure"))
    bus.publish(NodeDiscovered(run_id=run_id, node_id="xdm_modern.validate.$runner", label="XDM validate runner"))
    bus.publish(NodeStarted(run_id=run_id, node_id="xdm_modern.validate.$runner", label="XDM validate runner"))
    bus.publish(NodeDiscovered(
        run_id=run_id,
        node_id="xgo_final_wave12.validate.wave12_final_gate",
        label="wave12 final gate",
        depends_on=("root.failure",),
    ))

    snapshot = finalize_execution_run(run_id, status="failed", exit_code=7, bus=bus)
    assert snapshot.status == "failed"
    assert snapshot.exit_code == 7
    assert snapshot.graph_sealed is True
    assert snapshot.node("root.failure").status == "failed"
    runner = snapshot.node("xdm_modern.validate.$runner")
    gate = snapshot.node("xgo_final_wave12.validate.wave12_final_gate")
    assert runner.status == "cancelled"
    assert runner.transition_origin == "lifecycle_finalizer_inferred"
    assert gate.status == "blocked"
    assert gate.blocked_by == ("root.failure",)
    assert audit_execution_lifecycle(run_id, bus=bus).clean is True


def test_success_with_unobserved_required_node_fails_closed_instead_of_forging_success(tmp_path: Path) -> None:
    run_id = "exec-g3-fail-closed"
    bus = _bus(tmp_path, run_id)
    bus.publish(RunStarted(run_id=run_id, command="build", project="devtool"))
    bus.publish(NodeDiscovered(run_id=run_id, node_id="required.but.unobserved", label="required"))

    snapshot = finalize_execution_run(run_id, status="succeeded", exit_code=0, bus=bus)
    assert snapshot.status == "failed"
    assert snapshot.exit_code == 1
    node = snapshot.node("required.but.unobserved")
    assert node.status == "failed"
    assert node.failure_kind == "unobserved_required_work"
    assert node.transition_origin == "lifecycle_finalizer_inferred"
    assert audit_execution_lifecycle(run_id, bus=bus).clean is True


def test_historical_failed_run_with_dangling_descendants_can_be_recovered_idempotently(tmp_path: Path) -> None:
    run_id = "exec-g3-recovery"
    bus = _bus(tmp_path, run_id)
    bus.publish(RunStarted(run_id=run_id, command="validate", project="devtool"))
    bus.publish(NodeDiscovered(run_id=run_id, node_id="failed", label="failed"))
    bus.publish(NodeStarted(run_id=run_id, node_id="failed", label="failed"))
    bus.publish(NodeFinished(run_id=run_id, node_id="failed", status="failed", exit_code=2))
    bus.publish(NodeDiscovered(run_id=run_id, node_id="dangling.gate", label="dangling", depends_on=("failed",)))
    # Historical buggy finalization persisted the run terminal event while a node remained planned.
    bus.publish(RunFinished(run_id=run_id, status="failed", exit_code=2))

    snapshot = finalize_execution_run(run_id, status="failed", exit_code=2, bus=bus)
    assert snapshot.status == "failed"
    assert snapshot.node("dangling.gate").status == "blocked"
    assert snapshot.graph_sealed is True
    second = finalize_execution_run(run_id, status="failed", exit_code=2, bus=bus)
    assert second.to_dict() == snapshot.to_dict()


def test_parallel_worker_exception_emits_terminal_node_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import devtool.runners as runners

    get_execution_store().clear()
    config = ProjectConfig(
        repo_root=tmp_path,
        kind="command",
        runner="command",
        project_root=str(tmp_path),
        workspace=WorkspaceConfig(scheduler=WorkflowSchedulerConfig(parallel=True, max_parallel_nodes=2)),
    )
    specs = [
        CommandSpec("boom", ["true"], metadata={"workflow_graph": True, "workflow_node_key": "xdm_modern.validate.$runner", "workflow_write_scope_known": True, "workflow_write_scopes": ["a"]}),
        CommandSpec("ok", ["true"], metadata={"workflow_graph": True, "workflow_node_key": "xgo_final_wave12.validate.wave12_final_gate", "workflow_write_scope_known": True, "workflow_write_scopes": ["b"]}),
    ]
    original = runners.run_specs

    def fake_run_specs(*args, **kwargs):
        local_specs = args[1]
        if local_specs[0].name == "boom":
            raise RuntimeError("delegated runner exploded")
        return original(*args, **kwargs)

    monkeypatch.setattr(runners, "run_specs", fake_run_specs)
    results, _raw, plan = run_workflow_specs(
        config,
        specs,
        verbose=False,
        dry_run=False,
        continue_on_error=True,
        log_path=None,
        parallel_requested=True,
        max_parallel_nodes=2,
    )
    assert plan is not None and plan.parallel_effective
    assert any(item.failure_kind == "workflow_worker_exception" for item in results)
    snapshot = get_execution_store().snapshot()
    assert snapshot is not None
    failed = snapshot.node("xdm_modern.validate.$runner")
    assert failed is not None and failed.status == "failed"
    assert failed.transition_origin == "scheduler_inferred"
    assert failed.failure_kind == "workflow_worker_exception"


def test_exec_g3_campaign_and_promise_fixed_point() -> None:
    root = Path(__file__).resolve().parents[1]
    order = (root / "PRES_REMEDIATION_IMPLEMENTATION_ORDER_20261001.md").read_text(encoding="utf-8")
    assert "68/77 PRES-UX01" in order
    assert "69/77 EXEC-G3" in order
    assert "70/77 PRES-G3" in order

    x04 = json.loads((root / "EXEC_X04_PROMISE_LEDGER.json").read_text(encoding="utf-8"))
    covered = set(x04["retained_from_previous_gates"]) | set(x04["satisfied_by_implementation"])
    assert {f"EXEC-P{i:03d}" for i in range(1, 82)} <= covered
    exports = json.loads((root / "EXEC_X04_EXPORTED_OBLIGATIONS_20260929.json").read_text(encoding="utf-8"))["exports"]
    g3 = [row["obligation"] for row in exports if row["owner"] == "EXEC-G3"]
    assert len(g3) == 1
    assert "mixed executed/reused/materialized graph" in g3[0]
    assert "late-failure selective reuse" in g3[0]
    assert "same-run interruption continuation" in g3[0]
    assert "final closure disposition" in g3[0]


def test_exec_g3_final_gate_closes_mixed_provenance_and_lifecycle() -> None:
    from devtool.exec_g3_gate import run_gate

    root = Path(__file__).resolve().parents[1]
    payload = run_gate(root)
    assert payload["ok"] is True
    assert payload["promise_audit"]["explicit_promises"] == 82
    assert payload["mixed_provenance"]["initial"] == {
        "executed": "run",
        "reused": "reuse",
        "materialized": "materialize",
    }
    assert payload["mixed_provenance"]["late_input_change"] == {
        "executed": "run",
        "reused": "invalidated",
        "materialized": "materialize",
    }
    assert payload["finalization"]["run_status"] == "failed"
    assert payload["finalization"]["exit_code"] == 7
    assert payload["finalization"]["audit_clean"] is True
