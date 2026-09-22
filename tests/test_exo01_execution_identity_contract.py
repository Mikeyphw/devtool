from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from devtool.core.execution_contract import (
    EventId,
    ExecutionResult,
    ExecutionStatus,
    NodeId,
    ProcessId,
    RunId,
    canonical_execution_status,
    legacy_phase_node_id,
)
from devtool.core.results import CommandResult
from devtool.desktop.run_process import DesktopRunLauncher
from devtool.models import Issue, OperationResult
from devtool.tasker import TaskerProgressSink
from tests._compat import portable_shell_argv


def test_execution_identity_types_are_string_compatible_and_validate_empty_values() -> None:
    run_id = RunId("run-123")
    node_id = NodeId.workflow("target.workflow.build")
    event_id = EventId.for_sequence(run_id, 7)
    process_id = ProcessId.for_pid(run_id, 1234)

    assert isinstance(run_id, str)
    assert str(node_id) == "target.workflow.build"
    assert str(event_id) == "run-123:event:7"
    assert str(process_id) == "run-123:pid:1234"
    assert json.dumps({"run_id": run_id, "node_id": node_id}) == '{"run_id": "run-123", "node_id": "target.workflow.build"}'

    with pytest.raises(ValueError):
        RunId("")
    with pytest.raises(ValueError):
        EventId.for_sequence(run_id, -1)
    with pytest.raises(ValueError):
        ProcessId.for_pid(run_id, 0)


def test_dynamic_node_identity_is_deterministic_parent_scoped_and_instance_safe() -> None:
    first = NodeId.dynamic(parent_id="build", kind="download", key="toolchain", instance=1)
    same = NodeId.dynamic(parent_id="build", kind="download", key="toolchain", instance=1)
    repeated = NodeId.dynamic(parent_id="build", kind="download", key="toolchain", instance=2)
    other_parent = NodeId.dynamic(parent_id="release", kind="download", key="toolchain", instance=1)

    assert first == same
    assert first != repeated
    assert first != other_parent
    assert str(first).startswith("build/download:toolchain@")


def test_canonical_execution_status_maps_legacy_vocabulary_without_treating_unknown_as_success() -> None:
    assert canonical_execution_status("ok") is ExecutionStatus.SUCCEEDED
    assert canonical_execution_status("passed") is ExecutionStatus.SUCCEEDED
    assert canonical_execution_status("complete") is ExecutionStatus.SUCCEEDED
    assert canonical_execution_status("interrupted") is ExecutionStatus.CANCELLED
    assert canonical_execution_status("not-applicable") is ExecutionStatus.SKIPPED
    assert ExecutionStatus.FAILED.terminal is True
    assert ExecutionStatus.RUNNING.terminal is False
    with pytest.raises(ValueError):
        canonical_execution_status("mystery-success")


def test_operation_and_command_results_share_execution_result_contract(monkeypatch) -> None:
    monkeypatch.setenv("DEVTOOL_RUN_ID", "run-result")
    operation = OperationResult(
        "compile",
        ["go", "build"],
        0,
        1.25,
        "output",
        [Issue("warning", "go build", "x.go", "W1", "careful")],
        metadata={"workflow_node_key": "target.workflow.compile", "workflow_parent_id": "target.workflow.build"},
    )
    canonical = operation.to_execution_result()
    command = operation.to_command_result()

    assert canonical.status is ExecutionStatus.SUCCEEDED
    assert canonical.run_id == "run-result"
    assert canonical.node_id == "target.workflow.compile"
    assert canonical.parent_id == "target.workflow.build"
    assert canonical.warnings == ("careful",)
    assert command.to_execution_result().status is ExecutionStatus.SUCCEEDED
    assert command.metadata["execution_status"] == "succeeded"
    assert command.metadata["node_id"] == "target.workflow.compile"

    failed = OperationResult(
        "compile",
        ["go", "build"],
        0,
        0.1,
        "",
        [Issue("error", "go build", "x.go", "E1", "undefined symbol")],
    ).to_execution_result()
    assert failed.status is ExecutionStatus.FAILED
    assert failed.exit_code == 1

    skipped_command = CommandResult(
        success=True,
        metadata={"name": "lint", "skipped": True, "execution_status": "skipped"},
    )
    assert skipped_command.to_execution_result().status is ExecutionStatus.SKIPPED



def test_operation_result_preserves_blocked_and_cancelled_semantics() -> None:
    blocked = OperationResult(
        "zip", ["zip"], 0, 0.0, "blocked", [], skipped=True,
        failure_kind="dependency_blocked",
        metadata={"workflow_node_state": "blocked"},
    ).to_execution_result()
    cancelled = OperationResult(
        "build", ["build"], 130, 0.0, "cancelled", [],
        metadata={"workflow_node_state": "cancelled"},
    ).to_execution_result()

    assert blocked.status is ExecutionStatus.BLOCKED
    assert blocked.success is False
    assert cancelled.status is ExecutionStatus.CANCELLED
    assert cancelled.success is False

def test_legacy_phase_ids_distinguish_repeated_same_label_operations() -> None:
    first = legacy_phase_node_id(name="build", target="app", instance="1:1")
    second = legacy_phase_node_id(name="build", target="app", instance="2:2")
    assert first != second


def test_tasker_plan_uses_node_identity_not_display_tuple_and_keeps_duplicate_builds_distinct(tmp_path: Path) -> None:
    sink = TaskerProgressSink(
        directory=tmp_path,
        command="validate",
        repo_root="/repo/demo",
        repo_name="demo",
        enabled=True,
        update_interval_s=0,
    )
    sink.started()
    plan = [
        {"name": "build", "target": "app", "workspace": "root"},
        {"name": "build", "target": "app", "workspace": "root"},
    ]
    sink.plan_phases(plan)
    # Replaying the same legacy plan is idempotent even though duplicate labels
    # inside that plan remain distinct from one another.
    sink.plan_phases(plan)

    assert len(sink.planned_phases) == 2
    node_ids = [item["node_id"] for item in sink.planned_phases]
    assert len(set(node_ids)) == 2
    assert all(node_ids)

    sink.phase_started("build", index=1, total=2, metadata={"bundle_target": "app", "workspace": "root"})
    sink.progress(completed=1, total=2, force=True)
    sink.phase_started("build", index=2, total=2, metadata={"bundle_target": "app", "workspace": "root"})
    sink.progress(completed=1, total=4, force=True)

    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert len(state["pipeline"]) == 2
    assert len(state["phase_progress"]) == 2
    live_ids = {item["node_id"] for item in state["phase_progress"]}
    assert live_ids == set(node_ids)


def test_tasker_preserves_workflow_node_key_exactly_as_authoritative_identity(tmp_path: Path) -> None:
    sink = TaskerProgressSink(
        directory=tmp_path,
        command="validate",
        repo_root="/repo/demo",
        repo_name="demo",
        enabled=True,
        update_interval_s=0,
    )
    sink.plan_phases([
        {"name": "build", "target": "app", "workflow_node_key": "app.release.compile"},
    ])
    assert sink.planned_phases[0]["node_id"] == "app.release.compile"
    assert sink.planned_phases[0]["identity_source"] == "workflow"


def test_desktop_launcher_exports_one_run_identity_under_new_and_compatibility_env_names(tmp_path: Path) -> None:
    launcher = DesktopRunLauncher(state_dir=tmp_path / "runs")
    seen: list[tuple[str, str]] = []
    handle = launcher.start(
        portable_shell_argv("printf '%s|%s\\n' \"$DEVTOOL_RUN_ID\" \"$DEVTOOL_DESKTOP_RUN_ID\""),
        cwd=tmp_path,
        on_output=lambda channel, line: seen.append((channel, line)),
    )
    deadline = time.monotonic() + 5
    while handle.returncode is None and time.monotonic() < deadline:
        time.sleep(0.05)

    assert handle.returncode == 0
    output = "\n".join(line for channel, line in seen if channel == "stdout")
    assert output == f"{handle.run_id}|{handle.run_id}"
    assert handle.process_id == f"{handle.run_id}:pid:{handle.process.pid}"
