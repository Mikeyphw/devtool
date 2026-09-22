from __future__ import annotations

import json
from datetime import datetime

import pytest

from devtool.core.events import (
    EXECUTION_EVENT_SCHEMA_VERSION,
    ArtifactCreated,
    ArtifactProduced,
    BuildFinished,
    BuildStarted,
    DiagnosticEmitted,
    EventBus,
    NodeBlocked,
    NodeDiscovered,
    NodeFinished,
    NodeProgressChanged,
    NodeStarted,
    ProcessExited,
    ProcessOutputReceived,
    ProcessStarted,
    RunFinished,
    RunStarted,
    TaskFinished,
    TaskStarted,
    UnknownExecutionEvent,
    WarningDetected,
    event_from_dict,
    event_to_dict,
)


def test_bus_completes_monotonic_versioned_envelope() -> None:
    bus = EventBus()
    seen = []
    bus.subscribe_all(seen.append)

    first = TaskStarted(task="compile", run_id="run-a", node_id="node-a", source="runner")
    second = TaskFinished(task="compile", run_id="run-a", node_id="node-a", success=True, source="runner")
    bus.publish(first)
    bus.publish(second)

    assert first.schema_version == EXECUTION_EVENT_SCHEMA_VERSION == 2
    assert first.sequence == 1
    assert second.sequence == 2
    assert first.event_id == "run-a:event:1"
    assert second.event_id == "run-a:event:2"
    assert first.run_id == second.run_id == "run-a"
    assert first.node_id == second.node_id == "node-a"
    assert first.source == second.source == "runner"
    assert first.timestamp.endswith("Z")
    datetime.fromisoformat(first.timestamp.replace("Z", "+00:00"))


def test_bus_sequence_is_scoped_per_run() -> None:
    bus = EventBus()
    a = RunStarted(run_id="a")
    b = RunStarted(run_id="b")
    a2 = RunFinished(run_id="a")
    bus.publish(a)
    bus.publish(b)
    bus.publish(a2)
    assert (a.sequence, b.sequence, a2.sequence) == (1, 1, 2)


def test_legacy_metadata_identity_is_promoted_into_typed_envelope() -> None:
    bus = EventBus()
    event = TaskStarted(
        task="compile",
        run_id="",
        metadata={
            "run_id": "legacy-run",
            "workflow_node_key": "workflow:compile",
            "workflow_parent_id": "workflow:build",
        },
    )
    bus.publish(event)
    assert event.run_id == "legacy-run"
    assert event.node_id == "workflow:compile"
    assert event.parent_id == "workflow:build"


def test_legacy_task_event_is_polymorphic_node_event() -> None:
    bus = EventBus()
    canonical = []
    legacy = []
    bus.subscribe(NodeStarted, canonical.append)
    bus.subscribe(TaskStarted, legacy.append)
    event = TaskStarted(task="build", node_id="node:build")
    bus.publish(event)
    assert canonical == [event]
    assert legacy == [event]
    assert event.label == "build"


def test_same_listener_subscribed_to_base_and_subclass_receives_event_once() -> None:
    bus = EventBus()
    seen = []
    bus.subscribe(NodeStarted, seen.append)
    bus.subscribe(TaskStarted, seen.append)
    bus.publish(TaskStarted(task="build"))
    assert len(seen) == 1


def test_typed_protocol_has_required_execution_event_shapes() -> None:
    events = [
        RunStarted(command="validate", project="/repo"),
        NodeDiscovered(node_id="build", label="Build", depends_on=("prepare",)),
        NodeStarted(node_id="build", label="Build", argv=("go", "build")),
        NodeProgressChanged(node_id="build", current=4, total=10, percent=40, unit="packages"),
        ProcessStarted(node_id="build", process_id="p1", pid=123, argv=("go", "build"), cwd="/repo"),
        ProcessOutputReceived(node_id="build", process_id="p1", stream="stderr", text="warning"),
        DiagnosticEmitted(node_id="build", severity="error", code="E1", message="boom", file="x.go", line=9),
        ArtifactProduced(node_id="build", path="out/app.zip", artifact_id="a1", sha256="abc", size=7, artifact_type="zip"),
        ProcessExited(node_id="build", process_id="p1", exit_code=1, duration_seconds=1.5),
        NodeBlocked(node_id="zip", blocked_by=("build",), reason="dependency failed"),
        NodeFinished(node_id="build", status="failed", exit_code=1),
        RunFinished(status="failed", exit_code=1),
    ]
    expected = {
        "run.started", "node.discovered", "node.started", "node.progress",
        "process.started", "process.output", "diagnostic.emitted", "artifact.produced",
        "process.exited", "node.blocked", "node.finished", "run.finished",
    }
    assert {item.protocol_type for item in events} == expected


def test_event_round_trip_preserves_typed_fields() -> None:
    bus = EventBus()
    original = NodeDiscovered(
        run_id="run",
        node_id="zip",
        parent_id="build",
        source="workflow",
        label="Create ZIP",
        kind="archive",
        depends_on=("compile", "package"),
    )
    bus.publish(original)
    payload = event_to_dict(original)
    json.dumps(payload)
    restored = event_from_dict(payload)
    assert isinstance(restored, NodeDiscovered)
    assert restored.run_id == "run"
    assert restored.node_id == "zip"
    assert restored.parent_id == "build"
    assert restored.depends_on == ("compile", "package")
    assert restored.sequence == original.sequence
    assert restored.event_id == original.event_id
    assert payload["event_type"] == "node.discovered"


def test_compatibility_class_name_round_trip_is_preserved() -> None:
    bus = EventBus()
    event = TaskStarted(task="compile", node_id="compile", argv=("go", "build"))
    bus.publish(event)
    restored = event_from_dict(event_to_dict(event))
    assert isinstance(restored, TaskStarted)
    assert restored.task == "compile"
    assert restored.label == "compile"
    assert restored.argv == ("go", "build")


def test_unknown_future_event_degrades_without_crashing() -> None:
    payload = {
        "type": "FutureThing",
        "event_type": "node.teleported",
        "schema_version": 99,
        "run_id": "future-run",
        "sequence": 12,
        "event_id": "future-run:event:12",
        "timestamp": "2030-01-01T00:00:00Z",
        "node_id": "n",
        "new_field": {"x": 1},
    }
    restored = event_from_dict(payload)
    assert isinstance(restored, UnknownExecutionEvent)
    assert restored.raw_event_type == "node.teleported"
    assert restored.raw_payload["new_field"] == {"x": 1}
    with pytest.raises(ValueError):
        event_from_dict(payload, allow_unknown=False)


def test_legacy_events_promote_semantics_out_of_metadata() -> None:
    task = TaskFinished(
        task="archive",
        success=True,
        metadata={"workflow_state": "blocked", "returncode": 0, "failure_kind": "dependency_blocked"},
    )
    warning = WarningDetected(message="deprecated", metadata={"code": "W1", "file": "x.py", "line": 4})
    artifact = ArtifactCreated(path="out/app.zip", metadata={"id": "a", "sha256": "abc", "size": 42, "type": "zip"})
    finished = BuildFinished(success=False, metadata={"exit_code": 130})

    assert task.status == "blocked"
    assert task.failure_kind == "dependency_blocked"
    assert warning.severity == "warning"
    assert warning.code == "W1"
    assert warning.file == "x.py"
    assert warning.line == 4
    assert artifact.artifact_id == "a"
    assert artifact.sha256 == "abc"
    assert artifact.size == 42
    assert artifact.artifact_type == "zip"
    assert finished.status == "failed"
    assert finished.exit_code == 130


def test_progress_clamps_percent_but_preserves_measured_values() -> None:
    event = NodeProgressChanged(current=11, total=10, percent=110, unit="tasks")
    assert event.current == 11.0
    assert event.total == 10.0
    assert event.percent == 100.0



def test_bus_uses_current_environment_run_id_at_publication(monkeypatch) -> None:
    bus = EventBus()
    event = TaskStarted(task="compile")
    monkeypatch.setenv("DEVTOOL_RUN_ID", "late-bound-run")
    bus.publish(event)
    assert event.run_id == "late-bound-run"
    assert event.event_id == "late-bound-run:event:1"


def test_compatibility_terminal_events_preserve_explicit_status() -> None:
    cancelled = BuildFinished(success=False, status="cancelled", exit_code=130)
    skipped = TaskFinished(task="dry", success=True, status="skipped")
    blocked = TaskFinished(task="zip", success=False, status="blocked")
    assert cancelled.status == "cancelled"
    assert skipped.status == "skipped"
    assert blocked.status == "blocked"


def test_bus_rejects_sequence_regression_within_a_run() -> None:
    bus = EventBus()
    bus.publish(RunStarted(run_id="run", sequence=4))
    with pytest.raises(ValueError, match="sequence regression"):
        bus.publish(NodeStarted(run_id="run", sequence=3, node_id="n"))
    with pytest.raises(ValueError, match="sequence regression"):
        bus.publish(NodeStarted(run_id="run", sequence=4, node_id="n"))


def test_bus_rejects_event_id_that_disagrees_with_sequence() -> None:
    bus = EventBus()
    event = RunStarted(run_id="run", sequence=2, event_id="run:event:99")
    with pytest.raises(ValueError, match="does not match"):
        bus.publish(event)

def test_event_bus_rejects_non_protocol_values() -> None:
    bus = EventBus()
    with pytest.raises(TypeError):
        bus.publish(object())  # type: ignore[arg-type]
