from __future__ import annotations

import json

import pytest

from devtool.core.events import (
    ArtifactProduced,
    DiagnosticEmitted,
    EventBus,
    MetricChanged,
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
)
from devtool.core.execution_store import ExecutionSequenceError, ExecutionStore, ExecutionTransitionError
from devtool.models import WorkflowStepRef
from devtool.workflow_graph import ExecutionGraph, WorkflowNode


def _publish_build(bus: EventBus, *, run_id: str = "run") -> None:
    bus.publish(RunStarted(run_id=run_id, command="build", project="/repo", source="test"))
    bus.publish(NodeDiscovered(run_id=run_id, node_id="download", label="Download", kind="download"))
    bus.publish(NodeDiscovered(run_id=run_id, node_id="compile", label="Compile", kind="build", depends_on=("download",)))
    bus.publish(NodeDiscovered(run_id=run_id, node_id="zip", label="Create ZIP", kind="archive", depends_on=("compile",)))


def test_event_bus_reduces_before_notifying_listeners() -> None:
    bus = EventBus()
    observed = []

    def listener(event):
        snapshot = bus.execution_store.snapshot(event.run_id)
        observed.append((event.protocol_type, snapshot.last_event_id, snapshot.status))

    bus.subscribe_all(listener)
    event = RunStarted(run_id="ordered", command="validate")
    bus.publish(event)

    assert observed == [("run.started", event.event_id, "running")]


def test_store_tracks_live_operation_counts_without_ui_parsing() -> None:
    bus = EventBus()
    _publish_build(bus)

    snapshot = bus.execution_store.snapshot("run")
    assert snapshot.counts.total == 3
    assert snapshot.counts.planned == 3

    bus.publish(NodeStarted(run_id="run", node_id="download", label="Download"))
    bus.publish(NodeProgressChanged(run_id="run", node_id="download", current=50, total=100, percent=50, unit="MB"))
    running = bus.execution_store.snapshot("run")
    assert running.counts.running == 1
    assert running.node("download").progress.percent == 50
    assert running.node("download").progress.unit == "MB"

    bus.publish(NodeFinished(run_id="run", node_id="download", status="succeeded"))
    bus.publish(NodeStarted(run_id="run", node_id="compile", label="Compile"))
    bus.publish(NodeProgressChanged(run_id="run", node_id="compile", current=7, total=10, percent=70, unit="packages"))
    mid = bus.execution_store.snapshot("run")
    assert mid.counts.succeeded == 1
    assert mid.counts.running == 1
    assert mid.counts.planned == 1

    bus.publish(NodeFinished(run_id="run", node_id="compile", status="failed", exit_code=1, failure_kind="process_exit"))
    bus.publish(NodeBlocked(run_id="run", node_id="zip", blocked_by=("compile",), reason="dependency failed"))
    bus.publish(RunFinished(run_id="run", status="failed", exit_code=1))
    final = bus.execution_store.snapshot("run")

    assert final.status == "failed"
    assert final.counts.total == 3
    assert final.counts.succeeded == 1
    assert final.counts.failed == 1
    assert final.counts.blocked == 1
    assert final.counts.terminal == 3
    assert final.node("compile").progress.percent == 70  # termination is not fake 100% work
    assert final.node("compile").failure_kind == "process_exit"
    assert final.node("zip").blocked_by == ("compile",)


def test_terminal_run_does_not_forge_success_for_unobserved_planned_nodes() -> None:
    bus = EventBus()
    _publish_build(bus, run_id="truth")
    bus.publish(NodeStarted(run_id="truth", node_id="download", label="Download"))
    bus.publish(NodeFinished(run_id="truth", node_id="download", status="succeeded"))
    bus.publish(RunFinished(run_id="truth", status="succeeded", exit_code=0))

    snapshot = bus.execution_store.snapshot("truth")
    assert snapshot.status == "succeeded"
    assert snapshot.counts.succeeded == 1
    assert snapshot.counts.planned == 2
    assert snapshot.node("compile").status == "planned"
    assert snapshot.node("zip").status == "planned"


def test_store_preserves_diagnostic_process_artifact_and_metric_ownership() -> None:
    bus = EventBus()
    bus.publish(RunStarted(run_id="owned", command="build"))
    bus.publish(NodeStarted(run_id="owned", node_id="compile", label="Compile", argv=("go", "build")))
    bus.publish(ProcessStarted(run_id="owned", node_id="compile", process_id="proc-1", pid=123, argv=("go", "build"), cwd="/repo"))
    bus.publish(ProcessOutputReceived(run_id="owned", node_id="compile", process_id="proc-1", stream="stderr", text="undefined: Symbol"))
    bus.publish(DiagnosticEmitted(run_id="owned", node_id="compile", severity="error", code="GO1", message="undefined: Symbol", file="x.go", line=9))
    bus.publish(MetricChanged(run_id="owned", node_id="compile", name="packages", value=7, unit="packages"))
    bus.publish(ArtifactProduced(run_id="owned", node_id="compile", artifact_id="bin", path="out/repoctl", sha256="abc", size=42, artifact_type="binary"))
    bus.publish(ProcessExited(run_id="owned", node_id="compile", process_id="proc-1", exit_code=1, duration_seconds=1.5))
    bus.publish(NodeFinished(run_id="owned", node_id="compile", status="failed", exit_code=1))

    snapshot = bus.execution_store.snapshot("owned")
    node = snapshot.node("compile")
    assert node.process_ids == ("proc-1",)
    assert len(node.diagnostic_ids) == 1
    assert node.artifact_ids == ("bin",)
    assert node.metrics["packages"] == 7
    assert snapshot.processes[0].status == "failed"
    assert snapshot.processes[0].stderr_events == 1
    assert snapshot.diagnostics[0].node_id == "compile"
    assert snapshot.diagnostics[0].code == "GO1"
    assert snapshot.artifacts[0].node_id == "compile"
    assert snapshot.artifacts[0].path == "out/repoctl"


def test_existing_workflow_graph_is_reused_as_runtime_planning_truth() -> None:
    first = WorkflowNode(
        key="api.build.download",
        target="api",
        workflow="build",
        index=1,
        ref=WorkflowStepRef(raw="job:download", kind="job", name="download", step_id="download"),
    )
    second = WorkflowNode(
        key="api.build.package",
        target="api",
        workflow="build",
        index=2,
        ref=WorkflowStepRef(raw="runner:python#build", kind="runner", name="python", action="build", step_id="package"),
        depends_on=(first.key,),
    )
    graph = ExecutionGraph(target="api", workflow="build", nodes=(first, second))

    store = ExecutionStore()
    snapshot = store.plan_graph(graph, run_id="graph-run")

    assert [node.node_id for node in snapshot.nodes] == [first.key, second.key]
    assert snapshot.node(second.key).depends_on == (first.key,)
    assert snapshot.node(second.key).metadata["runner"] == "python"
    assert snapshot.graph_sources == ({"target": "api", "workflow": "build", "targets": []},)


def test_legacy_same_label_operations_get_distinct_identity_but_finish_pairs_to_active_node() -> None:
    bus = EventBus()
    bus.publish(RunStarted(run_id="legacy"))

    first = TaskStarted(run_id="legacy", task="build")
    bus.publish(first)
    bus.publish(TaskFinished(run_id="legacy", task="build", success=True))
    second = TaskStarted(run_id="legacy", task="build")
    bus.publish(second)
    bus.publish(TaskFinished(run_id="legacy", task="build", success=False, status="failed", exit_code=1))

    snapshot = bus.execution_store.snapshot("legacy")
    assert first.node_id
    assert second.node_id
    assert first.node_id != second.node_id
    assert snapshot.counts.total == 2
    assert snapshot.counts.succeeded == 1
    assert snapshot.counts.failed == 1


def test_event_replay_is_idempotent_but_sequence_regression_is_rejected() -> None:
    source_bus = EventBus()
    event = NodeStarted(run_id="replay", node_id="compile", label="Compile")
    source_bus.publish(event)

    store = ExecutionStore()
    first = store.apply(event)
    second = store.apply(event)
    assert first.to_dict() == second.to_dict()
    assert second.counts.total == 1

    older = NodeProgressChanged(run_id="replay", node_id="compile", percent=10)
    older.sequence = 0
    older.event_id = "replay:event:0"
    with pytest.raises(ExecutionSequenceError):
        store.apply(older)


def test_conflicting_terminal_transition_is_rejected() -> None:
    bus = EventBus()
    bus.publish(NodeStarted(run_id="transition", node_id="compile", label="Compile"))
    bus.publish(NodeFinished(run_id="transition", node_id="compile", status="succeeded"))
    with pytest.raises(ExecutionTransitionError):
        bus.publish(NodeFinished(run_id="transition", node_id="compile", status="failed", exit_code=1))


def test_snapshot_is_json_safe_and_detached_from_mutable_store_state() -> None:
    bus = EventBus()
    bus.publish(NodeStarted(run_id="snapshot", node_id="compile", label="Compile"))
    snapshot = bus.execution_store.snapshot("snapshot")
    payload = snapshot.to_dict()
    json.dumps(payload)

    payload["nodes"][0]["metadata"]["mutated"] = True
    payload["nodes"][0]["progress"]["percent"] = 99
    fresh = bus.execution_store.snapshot("snapshot")
    assert "mutated" not in fresh.node("compile").metadata
    assert fresh.node("compile").progress.percent is None


def test_failed_reduction_is_atomic_and_bus_reuses_unpublished_sequence() -> None:
    bus = EventBus()
    bus.publish(NodeStarted(run_id="atomic", node_id="compile", label="Compile"))
    bus.publish(NodeFinished(run_id="atomic", node_id="compile", status="succeeded"))
    before = bus.execution_store.snapshot("atomic").to_dict()

    rejected = NodeFinished(run_id="atomic", node_id="compile", status="failed", exit_code=1)
    with pytest.raises(ExecutionTransitionError):
        bus.publish(rejected)

    after = bus.execution_store.snapshot("atomic").to_dict()
    assert after == before

    accepted = MetricChanged(run_id="atomic", name="post_reject", value=True)
    bus.publish(accepted)
    assert accepted.sequence == rejected.sequence
    assert bus.execution_store.snapshot("atomic").metrics["post_reject"] is True


def test_parallel_publish_order_matches_sequence_and_store_order() -> None:
    from concurrent.futures import ThreadPoolExecutor

    bus = EventBus()
    seen = []
    bus.subscribe_all(lambda event: seen.append((event.sequence, event.event_id)))

    def publish(index: int) -> None:
        bus.publish(MetricChanged(run_id="parallel", name=f"m{index}", value=index))

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(publish, range(40)))

    sequences = [item[0] for item in seen]
    assert sequences == list(range(1, 41))
    assert bus.execution_store.snapshot("parallel").last_sequence == 40


def test_snapshot_nested_metadata_is_detached_from_store() -> None:
    bus = EventBus()
    bus.publish(NodeStarted(
        run_id="deepcopy",
        node_id="compile",
        label="Compile",
        metadata={"nested": {"items": [1, 2]}},
    ))
    first = bus.execution_store.snapshot("deepcopy")
    first.node("compile").metadata["nested"]["items"].append(3)
    fresh = bus.execution_store.snapshot("deepcopy")
    assert fresh.node("compile").metadata == {"nested": {"items": [1, 2]}}


def test_cli_build_envelope_establishes_run_id_before_run_started(monkeypatch) -> None:
    import devtool.cli as cli

    published = []
    monkeypatch.delenv("DEVTOOL_RUN_ID", raising=False)
    monkeypatch.setattr(cli, "publish_event", published.append)
    monkeypatch.setattr(cli, "_main_after_parse", lambda *args, **kwargs: 0)

    assert cli.main(["build"]) == 0
    assert len(published) == 3
    assert isinstance(published[0], RunStarted)
    from devtool.core.events import GraphSealed
    assert isinstance(published[1], GraphSealed)
    assert isinstance(published[2], RunFinished)
    assert published[0].run_id
    assert published[0].run_id == published[1].run_id == published[2].run_id


def test_run_specs_seeds_workflow_nodes_before_execution(tmp_path) -> None:
    from devtool.core.events import collect_json_events, get_execution_store
    from devtool.models import CommandSpec, ProjectConfig
    from devtool.runners import run_specs

    config = ProjectConfig(repo_root=tmp_path, kind="command", runner="command", project_root=str(tmp_path))
    first = CommandSpec(
        "download",
        ["sh", "-c", "echo download"],
        metadata={
            "workflow_graph": True,
            "workflow_node_key": "build.download",
            "workflow_node_id": "download",
            "workflow_node_index": 1,
            "workflow_ref": "job:download",
            "workflow_ref_kind": "job",
            "workflow_depends_on": [],
            "workflow_target": "app",
            "workflow_name": "build",
            "workflow_runner": "command",
        },
    )
    second = CommandSpec(
        "zip",
        ["sh", "-c", "echo zip"],
        metadata={
            "workflow_graph": True,
            "workflow_node_key": "build.zip",
            "workflow_node_id": "zip",
            "workflow_node_index": 2,
            "workflow_ref": "job:zip",
            "workflow_ref_kind": "job",
            "workflow_depends_on": ["build.download"],
            "workflow_target": "app",
            "workflow_name": "build",
            "workflow_runner": "command",
        },
    )

    with collect_json_events() as captured:
        run_specs(config, [first, second], verbose=False, dry_run=True)

    run_events = [item for item in captured.events if item["source"] in {"workflow", "runner"}]
    assert [item["event_type"] for item in run_events[:2]] == ["node.discovered", "node.discovered"]
    assert [item["node_id"] for item in run_events[:2]] == ["build.download", "build.zip"]
    assert run_events[1]["depends_on"] == ["build.download"]
    run_id = run_events[0]["run_id"]
    assert all(item["run_id"] == run_id for item in run_events)

    snapshot = get_execution_store().snapshot(run_id)
    assert snapshot.counts.total == 2
    assert snapshot.counts.skipped == 2
    assert snapshot.node("build.zip").depends_on == ("build.download",)


def test_parallel_workflow_scheduler_uses_one_run_and_seeds_full_plan_first(tmp_path) -> None:
    from devtool.core.events import collect_json_events
    from devtool.models import CommandSpec, ProjectConfig
    from devtool.workflow_scheduler import run_workflow_specs

    config = ProjectConfig(repo_root=tmp_path, kind="command", runner="command", project_root=str(tmp_path))

    def spec(name: str, key: str) -> CommandSpec:
        return CommandSpec(
            name,
            ["sh", "-c", f"echo {name}"],
            metadata={
                "workflow_graph": True,
                "workflow_node_key": key,
                "workflow_node_id": name,
                "workflow_node_index": 1,
                "workflow_ref": f"job:{name}",
                "workflow_ref_kind": "job",
                "workflow_depends_on": [],
                "workflow_target": "app",
                "workflow_name": "build",
                "workflow_runner": "command",
                "workflow_write_scope_known": True,
                "workflow_read_only": True,
                "workflow_write_scopes": [],
                "workflow_read_scopes": [],
            },
        )

    with collect_json_events() as captured:
        results, _raw, plan = run_workflow_specs(
            config,
            [spec("a", "build.a"), spec("b", "build.b")],
            verbose=False,
            dry_run=True,
            continue_on_error=False,
            log_path=None,
            parallel_requested=True,
            max_parallel_nodes=2,
        )

    assert plan is not None and plan.parallel_effective
    assert len(results) == 2
    relevant = [item for item in captured.events if item["source"] in {"workflow", "runner"}]
    assert [item["event_type"] for item in relevant[:2]] == ["node.discovered", "node.discovered"]
    assert {item["node_id"] for item in relevant[:2]} == {"build.a", "build.b"}
    first_started = next(index for index, item in enumerate(relevant) if item["event_type"] == "node.started")
    assert first_started >= 2
    assert len({item["run_id"] for item in relevant}) == 1
