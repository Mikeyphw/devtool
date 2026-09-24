from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from .state import UISnapshot
from .viewmodels import runtime_state_to_ui_snapshot

MACHINE_SCHEMA_VERSION = 2
IPC_PROTOCOL_VERSION = 2
TASKER_SCHEMA_VERSION = 2


@dataclass(slots=True)
class MachineState:
    producer: str = "devtool"
    run_id: str = ""
    workspace: dict[str, Any] = field(default_factory=dict)
    selection: dict[str, Any] = field(default_factory=dict)
    operation: dict[str, Any] = field(default_factory=dict)
    workflow: dict[str, Any] = field(default_factory=dict)
    test_selection: dict[str, Any] = field(default_factory=dict)
    progress: dict[str, Any] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    actions: list[dict[str, Any]] = field(default_factory=list)
    navigation: list[dict[str, Any]] = field(default_factory=list)
    notifications: list[dict[str, Any]] = field(default_factory=list)
    presentation: dict[str, Any] = field(default_factory=dict)
    capabilities: dict[str, Any] = field(default_factory=dict)
    responsive: dict[str, Any] = field(default_factory=dict)
    federation: dict[str, Any] = field(default_factory=dict)
    schema_version: int = MACHINE_SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _workflow_state(raw: Mapping[str, Any], snapshot: UISnapshot) -> dict[str, Any]:
    phase = _mapping(raw.get("phase"))
    progress = _mapping(raw.get("progress"))
    current_node = str(
        phase.get("node_id")
        or phase.get("phase_id")
        or phase.get("name")
        or snapshot.operation.phase
        or ""
    )
    return {
        "name": str(phase.get("workflow") or phase.get("workflow_name") or raw.get("workflow") or ""),
        "current_node": current_node,
        "target": snapshot.operation.target,
        "targets": list(snapshot.selection.targets or snapshot.workspace.targets),
        "pipeline": list(raw.get("pipeline") or []),
        "phase_progress": list(raw.get("phase_progress") or []),
        "runner": snapshot.operation.runner,
        "execution_environment": snapshot.operation.execution_environment,
        "detail": str(progress.get("detail") or snapshot.operation.detail or ""),
    }


def machine_state_from_runtime(
    raw: Mapping[str, Any] | None,
    *,
    snapshot: UISnapshot | None = None,
    preferences: Mapping[str, Any] | None = None,
) -> MachineState:
    state = raw or {}
    ui = snapshot or runtime_state_to_ui_snapshot(state, surface="machine", preferences=preferences)
    return MachineState(
        producer=str(state.get("producer") or "devtool"),
        run_id=str(state.get("run_id") or ui.operation.run_id or ""),
        workspace=asdict(ui.workspace),
        selection=asdict(ui.selection),
        operation=asdict(ui.operation),
        workflow=_workflow_state(state, ui),
        test_selection=_mapping(state.get("test_selection")),
        progress={
            "summary": dict(ui.progress_summary),
            "tree": list(ui.progress_tree),
            "nodes": [asdict(item) for item in ui.progress],
        },
        diagnostics={
            "summary": dict(ui.diagnostic_summary),
            "presentations": list(ui.diagnostic_presentations),
            "items": [asdict(item) for item in ui.diagnostics],
        },
        actions=[asdict(item) for item in ui.actions],
        navigation=[asdict(item) for item in ui.navigation],
        notifications=list(ui.notifications),
        presentation=asdict(ui.presentation),
        capabilities={
            "surface": "machine",
            "supported": dict(ui.capabilities),
            "negotiation": dict(ui.capability_negotiation),
        },
        responsive=dict(ui.responsive),
        federation=dict(ui.federation),
    )


def tasker_fields(machine: MachineState | Mapping[str, Any]) -> dict[str, Any]:
    value = machine.as_dict() if isinstance(machine, MachineState) else dict(machine)
    operation = _mapping(value.get("operation"))
    workflow = _mapping(value.get("workflow"))
    progress = _mapping(value.get("progress"))
    summary = _mapping(progress.get("summary"))
    diagnostics = _mapping(value.get("diagnostics"))
    diag_summary = _mapping(diagnostics.get("summary"))
    actions = value.get("actions") if isinstance(value.get("actions"), list) else []
    action_ids = [str(item.get("id")) for item in actions if isinstance(item, Mapping) and item.get("id")]
    percent = summary.get("overall_percent")
    if percent is None:
        current = summary.get("completed_nodes")
        total = summary.get("total_nodes")
        if isinstance(current, int) and isinstance(total, int) and total > 0:
            percent = round((current / total) * 100)
    return {
        "DEVTOOL_MACHINE_SCHEMA": MACHINE_SCHEMA_VERSION,
        "DEVTOOL_RUN_ID": str(value.get("run_id") or operation.get("run_id") or ""),
        "DEVTOOL_OPERATION": str(operation.get("command") or ""),
        "DEVTOOL_STATUS": str(operation.get("status") or ""),
        "DEVTOOL_TARGET": str(operation.get("target") or ""),
        "DEVTOOL_PHASE": str(operation.get("phase") or ""),
        "DEVTOOL_WORKFLOW": str(workflow.get("name") or ""),
        "DEVTOOL_WORKFLOW_NODE": str(workflow.get("current_node") or ""),
        "DEVTOOL_PROGRESS": int(percent) if isinstance(percent, (int, float)) else -1,
        "DEVTOOL_WARNINGS": int(diag_summary.get("warnings") or 0),
        "DEVTOOL_ERRORS": int(diag_summary.get("errors") or 0),
        "DEVTOOL_ACTIONS": action_ids,
    }


def ipc_contract() -> dict[str, Any]:
    return {
        "protocol_version": IPC_PROTOCOL_VERSION,
        "machine_schema_version": MACHINE_SCHEMA_VERSION,
        "messages": [
            "hello", "subscribe", "snapshot", "register", "registered", "publish", "run_update",
            "action", "action_ack", "cancel", "cancel_ack", "remove_run", "run_removed", "ping", "pong",
        ],
        "actions": ["operation.cancel"],
        "compatibility": {
            "legacy_cancel": True,
            "legacy_run_fields": True,
            "legacy_state_schema": 1,
        },
    }
