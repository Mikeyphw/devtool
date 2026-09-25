from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import time
from unittest import mock

import pytest

from devtool.cli import build_parser
from devtool.completion import ZSH_COMPLETION
from devtool.config import load_project_config
from devtool.core.events import EventBus, NodeDiscovered, NodeFinished, NodeStarted
from devtool.federation_execution import run_federated_group, run_federated_workflow
from devtool.federation_plan import build_federated_plan
from devtool.federation_scheduler import build_federated_schedule
from devtool.repository_identity import build_repository_identity
from devtool.workflow_scheduler import WorkflowScheduleNode, scheduling_conflict


def _child(
    root: Path,
    *,
    workflow: str = "work",
    outputs: tuple[str, ...] = ("out/**",),
    read_only: bool = False,
    resource_class: str = "",
    global_reservations: tuple[str, ...] = (),
    runner: str = "command",
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    output_line = f"outputs = {list(outputs)!r}".replace("'", '"') if outputs else ""
    metadata: list[str] = []
    if read_only:
        metadata.append("read_only = true")
    if resource_class:
        metadata.append(f'resource_class = "{resource_class}"')
    if global_reservations:
        rendered = ", ".join(f'"{item}"' for item in global_reservations)
        metadata.append(f"global_reservations = [{rendered}]")
    metadata_block = "\n[jobs.one.metadata]\n" + "\n".join(metadata) if metadata else ""
    (root / ".devtool.toml").write_text(
        f'''[project]
runner = "command"

[workspace]
default_target = "app"

[jobs.one]
runner = "{runner}"
command = ["true"]
inputs = ["input/**"]
{output_line}
{metadata_block}

[targets.app]
runner = "command"
root = "."

[targets.app.workflows]
{workflow} = ["job:one"]
''',
        encoding="utf-8",
    )


def _parent(
    tmp_path: Path,
    *,
    child_options: dict[str, dict[str, object]] | None = None,
    with_join: bool = True,
    with_group: bool = True,
) -> Path:
    parent = tmp_path / "parent"
    parent.mkdir()
    options = child_options or {}
    names = ["alpha", "beta"] + (["join"] if with_join else [])
    for name in names:
        _child(tmp_path / name, **options.get(name, {}))

    join_step = ',\n  { id = "join", ref = "join::work", depends_on = ["alpha", "beta"] }' if with_join else ""
    group = '\n[federation.groups]\nready = ["release"]\n' if with_group else ""
    (parent / ".devtool.toml").write_text(
        f'''[project]
runner = "command"

[workspace.scheduler]
parallel = false
max_parallel_nodes = 4
max_gradle_nodes = 1
max_dotnet_nodes = 1

[federation]
schema = 1

[federation.repositories.alpha]
path = "../alpha"

[federation.repositories.beta]
path = "../beta"
'''
        + ('\n[federation.repositories.join]\npath = "../join"\n' if with_join else "")
        + f'''
[federation.workflows.release]
steps = [
  {{ id = "alpha", ref = "alpha::work" }},
  {{ id = "beta", ref = "beta::work" }}{join_step}
]
{group}''',
        encoding="utf-8",
    )
    return parent


def _load(root: Path):
    with mock.patch.dict(
        os.environ,
        {"DEVTOOL_GLOBAL_CONFIG": str(root / "missing-global.toml")},
        clear=False,
    ):
        return load_project_config(
            str(root),
            None,
            allow_ambiguous_target=True,
            allow_ambiguous_project_kind=True,
        )


def _schedule(config, *, parallel: bool = True, max_parallel: int = 4):
    workflow = config.federation.workflows["release"]
    plan = build_federated_plan(config, "release")
    return build_federated_schedule(
        workflow,
        plan,
        parallel_requested=parallel,
        max_parallel=max_parallel,
        max_gradle=config.workspace.scheduler.max_gradle_nodes,
        max_dotnet=config.workspace.scheduler.max_dotnet_nodes,
    )


def test_parallel_scheduler_runs_only_dependency_ready_children_in_waves(tmp_path: Path) -> None:
    config = _load(_parent(tmp_path))
    schedule = _schedule(config)
    assert schedule.parallel_requested is True
    assert schedule.parallel_effective is True
    assert [wave.nodes for wave in schedule.waves] == [("alpha", "beta"), ("join",)]
    assert schedule.by_step["join"].depends_on == ("alpha", "beta")


def test_max_parallel_cap_is_enforced_by_shared_scheduler(tmp_path: Path) -> None:
    config = _load(_parent(tmp_path))
    schedule = _schedule(config, max_parallel=1)
    assert schedule.parallel_effective is False
    assert [len(wave.nodes) for wave in schedule.waves] == [1, 1, 1]


def test_explicit_global_reservation_conflict_serializes_cross_repo_children(tmp_path: Path) -> None:
    options = {
        "alpha": {"global_reservations": ("device:usb0",)},
        "beta": {"global_reservations": ("device:usb0",)},
    }
    config = _load(_parent(tmp_path, child_options=options, with_join=False))
    schedule = _schedule(config)
    assert [len(wave.nodes) for wave in schedule.waves] == [1, 1]
    left, right = schedule.nodes
    assert "device:usb0" in left.global_reservations
    assert "global reservation conflict" in scheduling_conflict(
        # The federation scheduler deliberately compiles to the shared workflow
        # scheduler contract. Rebuild its two nodes through the plan's waves by
        # inspecting the persisted reservation evidence rather than a second
        # federation-only conflict implementation.
        WorkflowScheduleNode(
            key=left.step_id, target=left.target, workflow=left.workflow,
            runner="federation", action="invoke", job_scope="federation",
            job_target=left.target, job_local_name=left.workflow,
            job_key=left.step_id, depends_on=left.depends_on,
            spec_indexes=(), spec_names=(), reads=left.reads, writes=left.writes,
            writes_known=True, read_only=left.read_only,
            resource_class=left.resource_class, global_reservations=left.global_reservations,
            scope_reason=left.scope_reason,
        ),
        WorkflowScheduleNode(
            key=right.step_id, target=right.target, workflow=right.workflow,
            runner="federation", action="invoke", job_scope="federation",
            job_target=right.target, job_local_name=right.workflow,
            job_key=right.step_id, depends_on=right.depends_on,
            spec_indexes=(), spec_names=(), reads=right.reads, writes=right.writes,
            writes_known=True, read_only=right.read_only,
            resource_class=right.resource_class, global_reservations=right.global_reservations,
            scope_reason=right.scope_reason,
        ),
    )


def test_conservative_resource_classes_are_projected_from_child_workflows(tmp_path: Path) -> None:
    options = {
        "alpha": {"resource_class": "device-exclusive"},
        "beta": {"resource_class": "network-heavy"},
    }
    config = _load(_parent(tmp_path, child_options=options, with_join=False))
    schedule = _schedule(config)
    assert schedule.by_step["alpha"].resource_class == "device-exclusive"
    assert schedule.by_step["beta"].resource_class == "network-heavy"
    assert "resource:device-exclusive" in schedule.by_step["alpha"].global_reservations
    assert "resource:network-heavy" in schedule.by_step["beta"].global_reservations


def test_read_only_child_is_classified_generic_read_only(tmp_path: Path) -> None:
    options = {
        "alpha": {"outputs": (), "read_only": True},
        "beta": {"outputs": (), "read_only": True},
    }
    config = _load(_parent(tmp_path, child_options=options, with_join=False))
    schedule = _schedule(config)
    assert {node.resource_class for node in schedule.nodes} == {"generic-read-only"}
    assert all(node.read_only for node in schedule.nodes)
    assert schedule.parallel_effective is True

@pytest.mark.parametrize(
    "resource_class",
    ["cpu-heavy", "gradle-heavy", "dotnet-heavy", "device-exclusive", "network-heavy"],
)
def test_named_federation_resource_classes_survive_child_projection(
    tmp_path: Path, resource_class: str
) -> None:
    options = {
        "alpha": {"resource_class": resource_class},
        "beta": {"outputs": (), "read_only": True},
    }
    config = _load(_parent(tmp_path, child_options=options, with_join=False))
    schedule = _schedule(config)
    assert schedule.by_step["alpha"].resource_class == resource_class
    assert schedule.by_step["beta"].resource_class == "generic-read-only"


def test_failed_child_blocks_dependent_join_without_invoking_it(tmp_path: Path) -> None:
    config = _load(_parent(tmp_path))
    invoked: list[str] = []

    def executor(_argv: list[str], env: dict[str, str]) -> int:
        repository = env["DEVTOOL_FEDERATION_REPOSITORY"]
        invoked.append(repository)
        return 1 if repository == "alpha" else 0

    report = run_federated_workflow(
        config,
        "release",
        reenter=lambda _argv: 99,
        publish=EventBus().publish,
        snapshot_lookup=lambda _run_id: None,
        parent_run_id="frx-y01-blocked",
        parallel=True,
        max_parallel=2,
        child_executor=executor,
    )
    assert report.status == "failed"
    assert "join" not in invoked
    states = {child.step_id: child.status for child in report.children}
    assert states == {"alpha": "failed", "beta": "succeeded", "join": "blocked"}


def test_parallel_executor_really_overlaps_safe_children_and_persists_schedule(tmp_path: Path) -> None:
    config = _load(_parent(tmp_path))
    bus = EventBus()
    lock = threading.Lock()
    active = 0
    peak = 0

    def executor(argv: list[str], env: dict[str, str]) -> int:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(0.12)
            return 0
        finally:
            with lock:
                active -= 1

    report = run_federated_workflow(
        config,
        "release",
        reenter=lambda _argv: 99,
        publish=bus.publish,
        snapshot_lookup=lambda _run_id: None,
        parent_run_id="frx-y01-parent",
        parallel=True,
        max_parallel=2,
        child_executor=executor,
    )
    assert report.ok
    assert peak == 2
    assert report.schedule["parallel_requested"] is True
    assert report.schedule["parallel_effective"] is True
    assert report.schedule["waves"][0]["nodes"] == ["alpha", "beta"]
    parent = bus.execution_store.snapshot("frx-y01-parent")
    assert parent is not None and parent.status == "succeeded"
    assert all(node.metadata.get("scheduler") for node in parent.nodes)
    assert {node.metadata.get("scheduler_wave") for node in parent.nodes} == {1, 2}


def test_parallel_children_use_isolated_durable_exo_directories(tmp_path: Path) -> None:
    config = _load(_parent(tmp_path, with_join=False))
    seen_dirs: dict[str, str] = {}

    def executor(argv: list[str], env: dict[str, str]) -> int:
        repository = env["DEVTOOL_FEDERATION_REPOSITORY"]
        root = Path(argv[argv.index("--repo") + 1])
        directory = Path(env["DEVTOOL_TASKER_DIR"])
        seen_dirs[repository] = str(directory)
        assert env["DEVTOOL_NO_AUTO_HUD"] == "1"
        assert env["DEVTOOL_FEDERATION_CHILD_STATE_DIR"] == str(directory)
        directory.mkdir(parents=True, exist_ok=True)
        lineage = {
            "kind": "federation",
            "relationship": "child",
            "parent_run_id": env["DEVTOOL_FEDERATION_PARENT_RUN_ID"],
            "parent_node_id": env["DEVTOOL_FEDERATION_PARENT_NODE_ID"],
            "child_run_id": env["DEVTOOL_RUN_ID"],
            "federation_workflow": env["DEVTOOL_FEDERATION_WORKFLOW"],
            "federation_step": env["DEVTOOL_FEDERATION_STEP"],
            "repository": repository,
            "target": env["DEVTOOL_FEDERATION_TARGET"],
            "child_workflow": env["DEVTOOL_FEDERATION_CHILD_WORKFLOW"],
            "federation_ref": env["DEVTOOL_FEDERATION_REF"],
            "requested_ref": env["DEVTOOL_FEDERATION_REQUESTED_REF"],
            "persisted_marker": "yes",
        }
        payload = {
            "schema_version": 1,
            "run_id": env["DEVTOOL_RUN_ID"],
            "execution": {
                "run_id": env["DEVTOOL_RUN_ID"],
                "status": "succeeded",
                "run_lineage": lineage,
                "repository_identity": build_repository_identity(root, root / ".devtool.toml").as_dict(),
                "artifacts": [],
                "diagnostics": [],
                "processes": [],
                "metrics": {},
            },
        }
        (directory / "execution.json").write_text(json.dumps(payload), encoding="utf-8")
        return 0

    report = run_federated_workflow(
        config,
        "release",
        reenter=lambda _argv: 99,
        publish=EventBus().publish,
        snapshot_lookup=lambda _run_id: None,
        parent_run_id="frx-y01-durable",
        parallel=True,
        max_parallel=2,
        child_executor=executor,
    )
    assert report.ok
    assert set(seen_dirs) == {"alpha", "beta"}
    assert len(set(seen_dirs.values())) == 2
    assert all("federation-children/frx-y01-durable/" in value for value in seen_dirs.values())
    assert all(child.run_lineage.get("persisted_marker") == "yes" for child in report.children)


def test_parent_exo_events_expose_queued_running_and_terminal_scheduler_truth(tmp_path: Path) -> None:
    config = _load(_parent(tmp_path, with_join=False))
    bus = EventBus()
    events: list[object] = []

    def publish(event: object) -> None:
        events.append(event)
        bus.publish(event)

    report = run_federated_workflow(
        config,
        "release",
        reenter=lambda _argv: 99,
        publish=publish,
        snapshot_lookup=lambda _run_id: None,
        parent_run_id="frx-y01-states",
        parallel=True,
        max_parallel=2,
        child_executor=lambda _argv, _env: 0,
    )
    assert report.ok
    discovered = [event for event in events if isinstance(event, NodeDiscovered)]
    started = [event for event in events if isinstance(event, NodeStarted)]
    finished = [event for event in events if isinstance(event, NodeFinished)]
    assert {event.metadata.get("scheduler_state") for event in discovered} == {"queued"}
    assert {event.metadata.get("scheduler_state") for event in started} == {"running"}
    assert {event.metadata.get("scheduler_state") for event in finished} == {"succeeded"}
    assert all("scheduler_wave" in event.metadata for event in discovered + started + finished)


def test_cancelled_parallel_child_immediately_requests_sibling_cancel_and_cancels_pending(tmp_path: Path) -> None:
    config = _load(_parent(tmp_path))
    bus = EventBus()
    beta_started = threading.Event()
    beta_cancelled = threading.Event()
    beta_run: dict[str, str] = {}
    cancellation_requests: list[str] = []

    def executor(argv: list[str], env: dict[str, str]) -> int:
        repository = env["DEVTOOL_FEDERATION_REPOSITORY"]
        if repository == "beta":
            beta_run["id"] = env["DEVTOOL_RUN_ID"]
            beta_started.set()
            assert beta_cancelled.wait(1.0), "sibling cancellation was not propagated"
            return 130
        assert beta_started.wait(1.0), "beta did not become active"
        return 130

    def request_cancel(run_id: str) -> None:
        cancellation_requests.append(run_id)
        if run_id == beta_run.get("id"):
            beta_cancelled.set()

    report = run_federated_workflow(
        config,
        "release",
        reenter=lambda _argv: 99,
        publish=bus.publish,
        snapshot_lookup=lambda _run_id: None,
        parent_run_id="frx-y01-cancel",
        request_child_cancel=request_cancel,
        parallel=True,
        max_parallel=2,
        child_executor=executor,
    )
    assert report.status == "cancelled"
    assert report.exit_code == 130
    assert beta_run["id"] in cancellation_requests
    states = {child.step_id: child.status for child in report.children}
    assert states["alpha"] == "cancelled"
    assert states["beta"] == "cancelled"
    assert states["join"] == "cancelled"
    parent = bus.execution_store.snapshot("frx-y01-cancel")
    assert parent is not None and parent.status == "cancelled"
    assert parent.node("federation:release:join").status == "cancelled"


def test_parallel_group_uses_same_scheduler_contract(tmp_path: Path) -> None:
    config = _load(_parent(tmp_path, with_join=False, with_group=True))
    report = run_federated_group(
        config,
        "ready",
        reenter=lambda _argv: 99,
        publish=EventBus().publish,
        snapshot_lookup=lambda _run_id: None,
        parent_run_id="frx-y01-group",
        parallel=True,
        max_parallel=2,
        child_executor=lambda _argv, _env: 0,
    )
    assert report.ok
    assert report.group == "ready"
    assert report.schedule["parallel_requested"] is True
    assert report.schedule["parallel_effective"] is True


def test_cli_exposes_parallel_and_max_parallel_for_workflow_and_group() -> None:
    parser = build_parser()
    workflow = parser.parse_args(["federation", "run", "release", "--parallel", "--max-parallel", "3"])
    assert workflow.parallel is True
    assert workflow.max_parallel == 3
    group = parser.parse_args(["federation", "run", "--group", "ready", "--parallel"])
    assert group.group == "ready"
    assert group.parallel is True
    assert parser.parse_args(["federation", "run", "release"]).max_parallel is None
    assert parser.parse_args(["federation", "run", "release", "--max-parallel", "0"]).max_parallel == 0
    assert "--parallel[run dependency-ready federation children concurrently when safe]" in ZSH_COMPLETION
    assert "--max-parallel[maximum concurrent federation children; implies --parallel]" in ZSH_COMPLETION


def test_default_execution_path_remains_sequential_and_does_not_use_parallel_executor(tmp_path: Path) -> None:
    config = _load(_parent(tmp_path, with_join=False))
    bus = EventBus()
    called: list[str] = []

    def reenter(argv: list[str]) -> int:
        called.append(argv[argv.index("--repo") + 1])
        return 0

    report = run_federated_workflow(
        config,
        "release",
        reenter=reenter,
        publish=bus.publish,
        snapshot_lookup=lambda _run_id: None,
        parent_run_id="frx-y01-sequential",
        child_executor=lambda _argv, _env: pytest.fail("parallel executor must not be used by default"),
    )
    assert report.ok
    assert len(called) == 2
    assert report.schedule == {}
