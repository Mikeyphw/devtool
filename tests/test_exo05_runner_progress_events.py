from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from rich.console import Console

from devtool.core.events import EventBus, NodeStarted
from devtool.progress_events import RunnerExecutionAdapter
from devtool.gradle_progress import GradleProgressTracker
from devtool.npm_progress import NpmProgressTracker
from devtool.dotnet_progress import DotnetProgressTracker


def _adapter(monkeypatch, *, run_id: str = "exo05", parent: str = "build", tool: str = "gradle"):
    bus = EventBus()
    bus.publish(NodeStarted(run_id=run_id, node_id=parent, label="Build", kind="command"))
    monkeypatch.setattr("devtool.progress_events.publish_event", bus.publish)
    return bus, RunnerExecutionAdapter(run_id=run_id, parent_node_id=parent, tool=tool)


def test_gradle_structured_events_become_task_and_test_nodes(monkeypatch) -> None:
    bus, adapter = _adapter(monkeypatch)
    adapter.gradle_event({"kind": "task-graph", "tasks": [":compile", ":test"], "total": 2})
    adapter.gradle_event({"kind": "task-start", "task": ":compile"})
    adapter.gradle_event({
        "kind": "task-finish", "task": ":compile", "outcome": "up-to-date",
        "didWork": False, "artifactOutputs": ["/repo/build/app.jar"],
    })
    adapter.gradle_event({"kind": "test-start", "test": "pkg.UnitTest.ok"})
    adapter.gradle_event({"kind": "test-finish", "test": "pkg.UnitTest.ok", "outcome": "success", "durationMs": 12})

    snap = bus.execution_store.snapshot("exo05")
    task = next(node for node in snap.nodes if node.label == ":compile")
    test = next(node for node in snap.nodes if node.label == "pkg.UnitTest.ok")
    assert task.status == "succeeded"
    assert task.metadata["outcome"] == "up-to-date"
    assert task.metadata["artifact_outputs"] == ["/repo/build/app.jar"]
    assert test.status == "succeeded"
    assert test.kind == "gradle-test"
    assert snap.node("build").status == "running"





def test_gradle_successful_work_avoidance_outcomes_remain_distinct(monkeypatch) -> None:
    for index, outcome in enumerate(("up-to-date", "no-source", "from-cache"), start=1):
        run_id = f"exo05-outcome-{index}"
        bus, adapter = _adapter(monkeypatch, run_id=run_id)
        task = f":task{index}"
        adapter.gradle_event({"kind": "task-graph", "tasks": [task], "total": 1})
        adapter.gradle_event({"kind": "task-start", "task": task})
        adapter.gradle_event({"kind": "task-finish", "task": task, "outcome": outcome, "didWork": False})
        snap = bus.execution_store.snapshot(run_id)
        node = next(node for node in snap.nodes if node.label == task)
        assert node.status == "succeeded"
        assert node.metadata["outcome"] == outcome


def test_npm_planned_scripts_keep_duplicate_labels_as_distinct_nodes(monkeypatch) -> None:
    bus, adapter = _adapter(monkeypatch, tool="npm")
    steps = [SimpleNamespace(label="pkg:build"), SimpleNamespace(label="pkg:build")]
    adapter.register_npm_plan(steps)
    adapter.npm_script_event({"kind": "script-start", "label": "pkg:build"})
    adapter.npm_script_event({"kind": "script-finish", "label": "pkg:build", "status": "succeeded", "exit_code": 0})
    adapter.npm_script_event({"kind": "script-start", "label": "pkg:build"})
    adapter.finish(1)

    snap = bus.execution_store.snapshot("exo05")
    scripts = [node for node in snap.nodes if node.kind == "npm-script"]
    assert len(scripts) == 2
    assert scripts[0].node_id != scripts[1].node_id
    assert [node.status for node in scripts] == ["succeeded", "failed"]


def test_dotnet_units_are_typed_children_with_test_failure_ownership(monkeypatch) -> None:
    bus, adapter = _adapter(monkeypatch, tool="dotnet")
    adapter.dotnet_unit_event({"kind": "unit-finish", "unit": "restore:Api.csproj", "label": "restored Api.csproj"})
    adapter.dotnet_unit_event({"kind": "unit-finish", "unit": "build:Api", "label": "built Api"})
    adapter.dotnet_unit_event({"kind": "unit-start", "unit": "test:Api.Tests", "label": "testing Api.Tests.dll"})
    adapter.dotnet_unit_event({"kind": "unit-finish", "unit": "test:Api.Tests", "label": "testing Api.Tests.dll", "status": "failed", "exit_code": 1})

    snap = bus.execution_store.snapshot("exo05")
    statuses = {node.kind: node.status for node in snap.nodes if node.node_id != "build"}
    assert statuses["dotnet-restore"] == "succeeded"
    assert statuses["dotnet-build"] == "succeeded"
    assert statuses["dotnet-test"] == "failed"


def test_parent_progress_remains_measured_and_final_failure_is_not_faked(monkeypatch) -> None:
    bus, adapter = _adapter(monkeypatch, tool="generic")
    adapter.progress_snapshot({"completed": 43, "total": 100, "percent": 43.0, "unit_label": "items", "detail": "working"})
    adapter.progress_snapshot({"final": True, "returncode": 1, "completed": 43, "total": 100, "percent": 43.0, "unit_label": "items"})
    node = bus.execution_store.snapshot("exo05").node("build")
    assert node.progress.percent == 43.0
    # The adapter owns progress/sub-operations; the outer command lifecycle
    # still owns the parent terminal status in run_specs().
    assert node.status == "running"


def test_gradle_tracker_forwards_valid_structured_event_to_adapter_callback(tmp_path: Path) -> None:
    seen = []
    tracker = GradleProgressTracker(
        ["./gradlew", "build"], str(tmp_path), "build", Console(force_terminal=False),
        requested_tasks=("build",),
    )
    tracker.structured_event_callback = seen.append
    line = "DEVTOOL_EVENT " + json.dumps({
        "sequence": 1, "kind": "task-graph", "run_id": tracker.expected_run_id,
        "tasks": [":compile"], "total": 1,
    })
    tracker.update_from_line(line)
    assert seen and seen[0]["kind"] == "task-graph"


def test_npm_tracker_emits_script_lifecycle_callbacks(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(json.dumps({"name": "pkg", "scripts": {"build": "echo ok"}}))
    seen = []
    tracker = NpmProgressTracker(["npm", "run", "build"], str(tmp_path), "build", Console(force_terminal=False))
    tracker.script_event_callback = seen.append
    tracker.update_from_line("> pkg@1.0.0 build\n")
    tracker.finish(0)
    assert [event["kind"] for event in seen] == ["script-start", "script-finish"]


def test_dotnet_tracker_emits_restore_build_and_test_unit_callbacks(tmp_path: Path) -> None:
    project = tmp_path / "Api.csproj"
    project.write_text("<Project />")
    seen = []
    tracker = DotnetProgressTracker(["dotnet", "build", str(project)], str(tmp_path), "build", Console(force_terminal=False))
    tracker.unit_event_callback = seen.append
    tracker.update_from_line(f"  Restored {project} (in 20 ms).")
    tracker.update_from_line(f"  Api -> {tmp_path / 'bin/Debug/net8.0/Api.dll'}")
    assert any(event["unit"].startswith("restore:") for event in seen)
    assert any(event["unit"].startswith("build:") for event in seen)


def test_run_specs_keeps_duplicate_nonworkflow_commands_as_distinct_nodes(monkeypatch, tmp_path: Path) -> None:
    from devtool.models import CommandSpec, ProjectConfig
    from devtool.runners import run_specs

    bus = EventBus()
    monkeypatch.setattr("devtool.runners.publish_event", bus.publish)
    monkeypatch.setattr("devtool.progress_events.publish_event", bus.publish)
    monkeypatch.setattr("devtool.runners._run_process_live", lambda *a, **kw: (0, "ok"))
    config = ProjectConfig(repo_root=tmp_path, kind="generic")
    specs = [CommandSpec("build", ["echo", "one"]), CommandSpec("build", ["echo", "two"])]

    results, _ = run_specs(config, specs, verbose=False, event_run_id="dup-run")
    assert [result.returncode for result in results] == [0, 0]
    snap = bus.execution_store.snapshot("dup-run")
    commands = [node for node in snap.nodes if node.kind == "operation" and node.label == "build"]
    assert len(commands) == 2
    assert commands[0].node_id != commands[1].node_id
    assert all(node.status == "succeeded" for node in commands)


def test_run_specs_models_multispec_workflow_node_as_command_part_children(monkeypatch, tmp_path: Path) -> None:
    from devtool.models import CommandSpec, ProjectConfig
    from devtool.runners import run_specs

    bus = EventBus()
    monkeypatch.setattr("devtool.runners.publish_event", bus.publish)
    monkeypatch.setattr("devtool.progress_events.publish_event", bus.publish)
    monkeypatch.setattr("devtool.runners._run_process_live", lambda *a, **kw: (0, "ok"))
    config = ProjectConfig(repo_root=tmp_path, kind="generic")
    common = {"workflow_node_key": "app.build.compile", "workflow_node_spec_total": 2}
    specs = [
        CommandSpec("configure", ["echo", "configure"], metadata={**common, "workflow_node_spec_index": 1}),
        CommandSpec("compile", ["echo", "compile"], metadata={**common, "workflow_node_spec_index": 2}),
    ]

    results, _ = run_specs(config, specs, verbose=False, event_run_id="parts-run")
    assert [result.returncode for result in results] == [0, 0]
    snap = bus.execution_store.snapshot("parts-run")
    parent = snap.node("app.build.compile")
    parts = [node for node in snap.nodes if node.kind == "command-part"]
    assert parent.status == "succeeded"
    assert len(parts) == 2
    assert all(node.parent_id == parent.node_id for node in parts)
    assert [node.status for node in parts] == ["succeeded", "succeeded"]


def test_npm_tracker_treats_repeated_same_banner_as_new_invocation(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(json.dumps({"name": "pkg", "scripts": {"build": "echo ok"}}))
    seen = []
    tracker = NpmProgressTracker(["npm", "run", "build"], str(tmp_path), "build", Console(force_terminal=False))
    tracker.script_event_callback = seen.append
    tracker.update_from_line("> pkg@1.0.0 build\n")
    tracker.update_from_line("> pkg@1.0.0 build\n")
    tracker.finish(0)
    assert [event["kind"] for event in seen] == ["script-start", "script-finish", "script-start", "script-finish"]
