from __future__ import annotations

import sys
from pathlib import Path

from devtool.core.events import EventBus, NodeFinished, NodeStarted
from devtool.models import CommandSpec, ProjectConfig
from devtool.progress_events import RunnerExecutionAdapter
from devtool.runners import run_specs


def _adapter(monkeypatch, tmp_path: Path, *, run_id: str = "exo06", parent: str = "build"):
    bus = EventBus()
    bus.publish(NodeStarted(run_id=run_id, node_id=parent, label="Build", kind="command"))
    monkeypatch.setattr("devtool.progress_events.publish_event", bus.publish)
    return bus, RunnerExecutionAdapter(run_id=run_id, parent_node_id=parent, tool="gradle")


def test_process_failure_and_diagnostic_are_owned_by_exact_node(monkeypatch, tmp_path: Path) -> None:
    bus, adapter = _adapter(monkeypatch, tmp_path)
    process_id = adapter.process_started(pid=4321, argv=("go", "build", "./..."), cwd=str(tmp_path))
    diagnostic_id = adapter.diagnostic(
        severity="error",
        code="GO_BUILD",
        message="undefined: SomeSymbol",
        file="pkg/file.go",
        line=19,
        failure_kind="compile_failure",
        log_path="/logs/build.log",
        evidence=("/logs/build.log", "pkg/file.go:19"),
    )
    adapter.process_exited(
        exit_code=1,
        duration_seconds=1.25,
        failure_kind="compile_failure",
        log_path="/logs/build.log",
        evidence=("/logs/build.log",),
    )
    bus.publish(NodeFinished(
        run_id="exo06",
        node_id="build",
        status="failed",
        exit_code=1,
        failure_kind="compile_failure",
        process_id=process_id,
        diagnostic_ids=(diagnostic_id,),
        evidence=("/logs/build.log",),
    ))

    snap = bus.execution_store.snapshot("exo06")
    node = snap.node("build")
    process = next(item for item in snap.processes if item.process_id == process_id)
    diagnostic = next(item for item in snap.diagnostics if item.diagnostic_id == diagnostic_id)

    assert node.failure_process_id == process_id
    assert node.primary_failure_diagnostic_id == diagnostic_id
    assert "/logs/build.log" in node.evidence_refs
    assert process.node_id == node.node_id
    assert process.failure_kind == "compile_failure"
    assert process.log_path == "/logs/build.log"
    assert diagnostic.node_id == node.node_id
    assert diagnostic.process_id == process_id
    assert diagnostic.failure_kind == "compile_failure"


def test_gradle_task_failure_and_artifact_outputs_have_task_ownership(monkeypatch, tmp_path: Path) -> None:
    bus, adapter = _adapter(monkeypatch, tmp_path)
    artifact = tmp_path / "app.jar"
    artifact.write_bytes(b"jar-bytes")

    adapter.gradle_event({"kind": "task-graph", "tasks": [":app:jar"], "total": 1})
    adapter.gradle_event({"kind": "task-start", "task": ":app:jar"})
    adapter.gradle_event({
        "kind": "task-finish",
        "task": ":app:jar",
        "outcome": "failed",
        "didWork": True,
        "failure": "jar packaging failed",
        "artifactOutputs": [str(artifact)],
    })

    snap = bus.execution_store.snapshot("exo06")
    task = next(node for node in snap.nodes if node.label == ":app:jar")
    diag = next(item for item in snap.diagnostics if item.node_id == task.node_id)
    produced = next(item for item in snap.artifacts if item.path == str(artifact))

    assert task.status == "failed"
    assert task.primary_failure_diagnostic_id == diag.diagnostic_id
    assert diag.code == "GRADLE_TASK"
    assert produced.node_id == task.node_id
    assert produced.producer_node_id == task.node_id
    assert produced.producer_task == ":app:jar"
    assert produced.sha256
    assert produced.size == len(b"jar-bytes")


def test_live_and_registry_artifact_observations_merge_by_producer_and_path(monkeypatch, tmp_path: Path) -> None:
    bus, adapter = _adapter(monkeypatch, tmp_path)
    artifact = tmp_path / "app.apk"
    artifact.write_bytes(b"apk")
    child = adapter._discover(kind="gradle-task", key=":assemble", label=":assemble")
    runtime_id = adapter.artifact(path=str(artifact), producer_node_id=child, producer_task=":assemble")

    from devtool.core.events import ArtifactProduced
    bus.publish(ArtifactProduced(
        run_id="exo06",
        node_id=child,
        producer_node_id=child,
        path=str(artifact),
        artifact_id="registry-123",
        sha256="deadbeef",
        size=3,
        artifact_type="apk",
        producer_task=":assemble",
        verification_status="verified",
        source="artifact_registry",
    ))

    snap = bus.execution_store.snapshot("exo06")
    matches = [item for item in snap.artifacts if item.path == str(artifact)]
    assert len(matches) == 1
    merged = matches[0]
    assert merged.artifact_id == runtime_id
    assert merged.registry_artifact_id == "registry-123"
    assert merged.verification_status == "verified"
    assert merged.sha256 == "deadbeef"


def test_run_specs_records_real_process_ownership(monkeypatch, tmp_path: Path) -> None:
    bus = EventBus()
    monkeypatch.setattr("devtool.runners.publish_event", bus.publish)
    monkeypatch.setattr("devtool.progress_events.publish_event", bus.publish)
    config = ProjectConfig(repo_root=tmp_path, kind="generic")
    spec = CommandSpec("python-smoke", [sys.executable, "-c", "print('owned')"])

    results, _ = run_specs(config, [spec], verbose=False, event_run_id="process-run")
    assert results[0].returncode == 0

    snap = bus.execution_store.snapshot("process-run")
    command = next(node for node in snap.nodes if node.label == "python-smoke")
    assert command.status == "succeeded"
    assert len(command.process_ids) == 1
    process = next(item for item in snap.processes if item.process_id == command.process_ids[0])
    assert process.pid and process.pid > 0
    assert process.argv[0] == sys.executable
    assert process.exit_code == 0
    assert process.node_id == command.node_id


def test_duplicate_diagnostic_observations_do_not_inflate_node_counts(monkeypatch, tmp_path: Path) -> None:
    bus, adapter = _adapter(monkeypatch, tmp_path)
    first = adapter.diagnostic(severity="error", code="E1", message="same", file="x.py", line=1)
    second = adapter.diagnostic(severity="error", code="E1", message="same", file="x.py", line=1)
    assert first == second
    snap = bus.execution_store.snapshot("exo06")
    node = snap.node("build")
    assert node.diagnostic_ids == (first,)
    assert len(snap.diagnostics) == 1


def test_large_live_artifact_defers_hash_until_registry(monkeypatch, tmp_path: Path) -> None:
    bus, adapter = _adapter(monkeypatch, tmp_path)
    artifact = tmp_path / "large.aab"
    with artifact.open("wb") as handle:
        handle.truncate(8 * 1024 * 1024 + 1)
    child = adapter._discover(kind="gradle-task", key=":bundle", label=":bundle")
    adapter.artifact(path=str(artifact), producer_node_id=child, producer_task=":bundle")
    produced = next(item for item in bus.execution_store.snapshot("exo06").artifacts if item.path == str(artifact))
    assert produced.size == 8 * 1024 * 1024 + 1
    assert produced.sha256 == ""


def test_failed_process_without_parser_issue_gets_truthful_process_exit_diagnostic(monkeypatch, tmp_path: Path) -> None:
    bus = EventBus()
    monkeypatch.setattr("devtool.runners.publish_event", bus.publish)
    monkeypatch.setattr("devtool.progress_events.publish_event", bus.publish)
    config = ProjectConfig(repo_root=tmp_path, kind="generic")
    spec = CommandSpec("fail-smoke", [sys.executable, "-c", "raise SystemExit(7)"])
    results, _ = run_specs(config, [spec], verbose=False, event_run_id="failure-run")
    assert results[0].returncode == 7
    snap = bus.execution_store.snapshot("failure-run")
    node = next(item for item in snap.nodes if item.label == "fail-smoke")
    assert node.status == "failed"
    assert node.failure_process_id
    assert node.primary_failure_diagnostic_id
    diagnostic = next(item for item in snap.diagnostics if item.diagnostic_id == node.primary_failure_diagnostic_id)
    assert diagnostic.code == "PROCESS_EXIT"
    assert diagnostic.message == "fail-smoke exited with code 7"
    assert diagnostic.process_id == node.failure_process_id
    assert diagnostic.log_path in node.evidence_refs
