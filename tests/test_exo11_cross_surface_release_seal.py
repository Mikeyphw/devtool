from __future__ import annotations

import json

from devtool.core.events import (
    DiagnosticEmitted,
    EventBus,
    MetricChanged,
    NodeDiscovered,
    NodeFinished,
    NodeProgressChanged,
    NodeStarted,
    ProcessExited,
    RunFinished,
    RunStarted,
)
from devtool.core.execution_recovery import recover_event_bus
from devtool.core.execution_subscribers import ExecutionProjectionSubscriber
from devtool.desktop.control_center_model import _run_summary_dict
from devtool.desktop.hud_store import load_hud_snapshot
from devtool.hud import AndroidAppHud, snapshot_from_directory, state_from_directory
from devtool.progress_events import RunnerExecutionAdapter
from devtool.ui_core.actions import ActionContext, actions_for_context
from devtool.ui_core.viewmodels import runtime_state_to_ui_snapshot


def _failed_canonical_run(root, *, run_id="exo11-seal"):
    bus = EventBus()
    sub = ExecutionProjectionSubscriber(
        store=bus.execution_store,
        run_id=run_id,
        directory=root,
        checkpoint_interval=2,
    )
    bus.subscribe_all(sub)
    bus.publish(RunStarted(run_id=run_id, command="validate", project="devtool"))
    bus.publish(NodeDiscovered(run_id=run_id, node_id="compile", label="Compile", kind="gradle-task"))
    bus.publish(NodeStarted(run_id=run_id, node_id="compile", label="Compile", kind="gradle-task"))
    bus.publish(NodeProgressChanged(run_id=run_id, node_id="compile", percent=43, current=43, total=100, unit="percent"))
    bus.publish(DiagnosticEmitted(
        run_id=run_id,
        node_id="compile",
        diagnostic_id="diag-compile",
        severity="error",
        code="COMPILE",
        message="Compilation failed",
        process_id="proc-compile",
        failure_kind="process_exit",
        log_path="/logs/compile.log",
        evidence=("/logs/compile.log",),
    ))
    bus.publish(ProcessExited(
        run_id=run_id,
        node_id="compile",
        process_id="proc-compile",
        exit_code=1,
        failure_kind="process_exit",
        log_path="/logs/compile.log",
        evidence=("/logs/compile.log",),
    ))
    bus.publish(NodeFinished(
        run_id=run_id,
        node_id="compile",
        status="failed",
        exit_code=1,
        process_id="proc-compile",
        diagnostic_ids=("diag-compile",),
        evidence=("/logs/compile.log",),
    ))
    bus.publish(RunFinished(run_id=run_id, status="failed", exit_code=1))
    return bus


def test_release_seal_cross_surface_truth_agrees_and_legacy_cannot_override(tmp_path):
    bus = _failed_canonical_run(tmp_path)
    (tmp_path / "state.json").write_text(json.dumps({
        "run_id": "exo11-seal",
        "status": "ok",
        "percent": 100,
        "phase_name": "Legacy success",
    }))

    terminal = state_from_directory(tmp_path, prefer_execution=True)
    snapshot = snapshot_from_directory(tmp_path, prefer_execution=True)
    desktop = load_hud_snapshot(tmp_path)
    summary = _run_summary_dict({
        "run_id": "exo11-seal",
        "status": "ok",
        "percent": 100,
        "execution": snapshot["execution"],
    })
    android_action, android = AndroidAppHud()._payload(
        state_from_directory(tmp_path),
        tmp_path,
    )

    assert terminal.status == "failed"
    assert terminal.percent == 43
    assert desktop.state["status"] == "failed"
    assert desktop.state["progress"]["measured_percent"] == 43
    assert summary["status"] == "failed"
    assert summary["percent"] == 43
    assert android_action == "fail"
    assert android["status"] == "failed"
    assert android["progress"] == 43
    assert json.loads(android["execution_json"])["run_id"] == "exo11-seal"

    original = bus.execution_store.snapshot("exo11-seal")
    recovered, report = recover_event_bus(tmp_path)
    replayed = recovered.execution_store.snapshot("exo11-seal")
    assert replayed.to_dict() == original.to_dict()
    assert report.events_replayed == 8
    assert report.last_sequence == original.last_sequence


def test_recovery_retains_unknown_future_events_and_resumes_sequence(tmp_path):
    bus = EventBus()
    sub = ExecutionProjectionSubscriber(store=bus.execution_store, run_id="future", directory=tmp_path)
    bus.subscribe_all(sub)
    bus.publish(RunStarted(run_id="future", command="build"))
    bus.publish(NodeStarted(run_id="future", node_id="node", label="Node"))
    bus.publish(NodeProgressChanged(run_id="future", node_id="node", percent=10))
    bus.publish(NodeFinished(run_id="future", node_id="node", status="succeeded"))

    journal = tmp_path / "execution-events.jsonl"
    with journal.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "type": "FutureExecutionEvent",
            "event_type": "execution.future.v99",
            "schema_version": 99,
            "run_id": "future",
            "event_id": "future:event:5",
            "sequence": 5,
            "timestamp": "2026-09-22T00:00:00+00:00",
            "node_id": "node",
            "parent_id": "",
            "source": "future-client",
            "future_payload": {"new": True},
        }, sort_keys=True) + "\n")

    recovered, report = recover_event_bus(tmp_path)
    assert report.unknown_events == 1
    assert report.last_sequence == 5
    assert recovered.execution_store.snapshot("future").last_sequence == 5

    live = MetricChanged(run_id="future", node_id="node", name="after_recovery", value=1)
    recovered.publish(live)
    assert live.sequence == 6
    assert live.event_id == "future:event:6"


def test_durable_journal_is_bounded_redacted_and_checkpointed(tmp_path):
    bus = EventBus()
    sub = ExecutionProjectionSubscriber(
        store=bus.execution_store,
        run_id="redact",
        directory=tmp_path,
        checkpoint_interval=1,
    )
    bus.subscribe_all(sub)
    adapter = RunnerExecutionAdapter(
        run_id="redact",
        parent_node_id="command",
        tool="generic",
        provenance={"transport": "ssh", "destination": "host", "token": "secret-provenance"},
    )

    import devtool.progress_events as progress_events
    original = progress_events.publish_event
    progress_events.publish_event = bus.publish
    try:
        bus.publish(NodeStarted(run_id="redact", node_id="command", label="Command"))
        adapter.process_started(
            pid=123,
            argv=("tool", "--password", "super-secret", "--token=also-secret"),
            cwd="/repo",
        )
    finally:
        progress_events.publish_event = original

    lines = [json.loads(line) for line in (tmp_path / "execution-events.jsonl").read_text().splitlines()]
    process = next(item for item in lines if item["event_type"] == "process.started")
    serialized = json.dumps(process)
    assert "super-secret" not in serialized
    assert "also-secret" not in serialized
    assert "secret-provenance" not in serialized
    assert "<redacted>" in serialized

    snapshot = bus.execution_store.snapshot("redact")
    proc = snapshot.processes[0]
    assert "super-secret" not in " ".join(proc.argv)
    assert proc.provenance["transport"] == "ssh"
    assert proc.provenance["token"] == "<redacted>"

    checkpoint = json.loads((tmp_path / "execution-checkpoint.json").read_text())
    assert checkpoint["kind"] == "devtool.execution.checkpoint"
    assert checkpoint["run_id"] == "redact"
    assert checkpoint["journal_events"] >= 2


def test_process_output_journal_is_bounded(tmp_path):
    from devtool.core.events import ProcessOutputReceived, ProcessStarted

    bus = EventBus()
    sub = ExecutionProjectionSubscriber(store=bus.execution_store, run_id="bounded", directory=tmp_path)
    bus.subscribe_all(sub)
    bus.publish(NodeStarted(run_id="bounded", node_id="node", label="Node"))
    bus.publish(ProcessStarted(run_id="bounded", node_id="node", process_id="p", pid=1, argv=("tool",), cwd="/repo"))
    bus.publish(ProcessOutputReceived(run_id="bounded", node_id="node", process_id="p", stream="stdout", text="x" * 10000))

    payload = json.loads((tmp_path / "execution-events.jsonl").read_text().splitlines()[-1])
    assert payload["output_truncated"] is True
    assert payload["original_length"] == 10000
    assert len(payload["text"]) == 2048
    assert bus.execution_store.snapshot("bounded").processes[0].last_output == "x" * 4096


def test_node_targeted_control_payloads_never_depend_on_display_label():
    actions = actions_for_context(ActionContext(
        status="failed",
        command="validate",
        run_id="run-identity",
        node_id="node-stable-id",
        target="devtool",
        retryable=True,
        resumable=True,
    ))
    by_id = {item.id: item for item in actions}
    assert by_id["operation.retry"].payload["run_id"] == "run-identity"
    assert by_id["operation.retry"].payload["node_id"] == "node-stable-id"
    assert by_id["workflow.retry_node"].payload["node_id"] == "node-stable-id"

    state = {
        "run_id": "run-identity",
        "status": "failed",
        "command": "validate",
        "target": "devtool",
        "phase": {"node_id": "node-stable-id", "name": "Human label may change"},
        "phase_name": "Human label may change",
        "retryable": True,
    }
    ui = runtime_state_to_ui_snapshot(state, surface="desktop").as_dict()
    retry = next(item for item in ui["actions"] if item["id"] == "operation.retry")
    retry_node = next(item for item in ui["actions"] if item["id"] == "workflow.retry_node")
    assert retry["payload"]["run_id"] == "run-identity"
    assert retry["payload"]["node_id"] == "node-stable-id"
    assert retry_node["payload"]["node_id"] == "node-stable-id"


def test_execution_provenance_covers_ssh_proot_and_chroot():
    from devtool.runners import _execution_provenance

    ssh = _execution_provenance({
        "execution_mode": "ssh-command",
        "ssh_destination": "user@example",
        "ssh_profile": "tablet",
        "ssh_workspace_id": "ws-1",
    }, ["ssh", "user@example"])
    assert ssh["transport"] == "ssh"
    assert ssh["destination"] == "user@example"
    assert ssh["profile"] == "tablet"

    proot = _execution_provenance({
        "execution_mode": "proot-distro",
        "proot_distribution": "debian",
        "execution_environment": "termux",
    }, ["proot-distro", "login", "debian"])
    assert proot["transport"] == "proot-distro"
    assert proot["distribution"] == "debian"
    assert proot["execution_environment"] == "termux"

    chroot = _execution_provenance({}, ["chrootctl", "--name", "androidos", "exec", "--", "gradle"])
    assert chroot["transport"] == "chrootctl"
    assert chroot["chroot"] == "androidos"
