from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from devtool.config import load_project_config
from devtool.core.events import EventBus, RunFinished, RunStarted
from devtool.core.execution_store import ExecutionTransitionError
from devtool.federation_execution import run_federated_workflow
from devtool.invocation_origin import invocation_metadata_for_args
from devtool.repository_identity import build_repository_identity


def _child(root: Path, workflow: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / ".devtool.toml").write_text(
        f'''[project]\nrunner="command"\n[workspace]\ndefault_target="app"\n[targets.app]\nrunner="command"\nroot="."\n[jobs.one]\nrunner="command"\ncommand=["echo","one"]\n[targets.app.workflows]\n{workflow}=["job:one"]\n''',
        encoding="utf-8",
    )


def _parent(tmp_path: Path) -> Path:
    parent = tmp_path / "parent"
    backend = tmp_path / "backend"
    android = tmp_path / "android"
    parent.mkdir()
    _child(backend, "prepare")
    _child(android, "release")
    (parent / ".devtool.toml").write_text(
        '''[project]\nrunner="command"\n[federation]\nschema=1\n[federation.repositories.backend]\npath="../backend"\n[federation.repositories.android]\npath="../android"\n[federation.workflows.release]\nsteps=[\n {id="prepare",ref="backend::prepare"},\n {id="android",ref="android::release",depends_on=["prepare"]},\n]\n''',
        encoding="utf-8",
    )
    return parent


def _load(root: Path):
    with mock.patch.dict(os.environ, {"DEVTOOL_GLOBAL_CONFIG": str(root / "missing-global.toml")}, clear=False):
        return load_project_config(str(root), None, allow_ambiguous_target=True, allow_ambiguous_project_kind=True)


def _fake_reenter(bus: EventBus, *, fail_repository: str = ""):
    seen: list[tuple[str, tuple[str, ...]]] = []

    def callback(argv: list[str]) -> int:
        run_id = os.environ["DEVTOOL_RUN_ID"]
        repository = os.environ["DEVTOOL_FEDERATION_REPOSITORY"]
        seen.append((run_id, tuple(argv)))
        root = Path(argv[argv.index("--repo") + 1])
        metadata = invocation_metadata_for_args(SimpleNamespace(
            _devtool_invocation_source="federation",
            _devtool_invocation_command=os.environ.get("DEVTOOL_FEDERATION_STEP", "child"),
            _devtool_wrapper_schema="",
            command="invoke",
        ))
        metadata["repository_identity"] = build_repository_identity(root, root / ".devtool.toml").as_dict()
        bus.publish(RunStarted(
            run_id=run_id,
            command="invoke",
            project=str(root),
            source="federation",
            metadata=metadata,
        ))
        rc = 7 if repository == fail_repository else 0
        bus.publish(RunFinished(
            run_id=run_id,
            status="failed" if rc else "succeeded",
            exit_code=rc,
            source="federation",
        ))
        return rc

    return callback, seen


def test_execution_store_persists_first_class_run_lineage(tmp_path: Path) -> None:
    bus = EventBus()
    run_id = "child-1"
    bus.publish(RunStarted(
        run_id=run_id,
        command="invoke",
        project=str(tmp_path),
        metadata={
            "run_lineage": {
                "kind": "federation",
                "relationship": "child",
                "parent_run_id": "parent-1",
                "parent_node_id": "federation:release:one",
                "child_run_id": run_id,
            }
        },
    ))
    snapshot = bus.execution_store.snapshot(run_id)
    assert snapshot is not None
    assert snapshot.schema_version == 7
    assert snapshot.run_lineage["parent_run_id"] == "parent-1"
    assert snapshot.to_dict()["run_lineage"]["child_run_id"] == run_id


def test_execution_store_rejects_self_parenting_lineage() -> None:
    bus = EventBus()
    with pytest.raises(ExecutionTransitionError, match="cannot name itself"):
        bus.publish(RunStarted(
            run_id="same",
            metadata={"run_lineage": {"parent_run_id": "same", "child_run_id": "same"}},
        ))


def test_federated_run_uses_distinct_child_runs_and_persists_lineage(tmp_path: Path) -> None:
    config = _load(_parent(tmp_path))
    bus = EventBus()
    callback, seen = _fake_reenter(bus)
    report = run_federated_workflow(
        config,
        "release",
        reenter=callback,
        publish=bus.publish,
        snapshot_lookup=bus.execution_store.snapshot,
        parent_run_id="federation-parent",
    )
    assert report.ok
    assert [child.step_id for child in report.children] == ["prepare", "android"]
    child_ids = [child.run_id for child in report.children]
    assert len(set(child_ids)) == 2
    assert "federation-parent" not in child_ids
    assert [run for run, _ in seen] == child_ids
    parent = bus.execution_store.snapshot("federation-parent")
    assert parent is not None and parent.status == "succeeded"
    assert parent.run_lineage["relationship"] == "coordinator"
    for child in report.children:
        snap = bus.execution_store.snapshot(child.run_id)
        assert snap is not None
        assert snap.run_lineage["relationship"] == "child"
        assert snap.run_lineage["parent_run_id"] == "federation-parent"
        assert snap.run_lineage["parent_node_id"] == child.parent_node_id
        assert snap.repository_identity["fingerprint"] == child.repository_identity["fingerprint"]


def test_failed_child_blocks_dependent_step_without_creating_second_child_run(tmp_path: Path) -> None:
    config = _load(_parent(tmp_path))
    bus = EventBus()
    callback, seen = _fake_reenter(bus, fail_repository="backend")
    report = run_federated_workflow(
        config,
        "release",
        reenter=callback,
        publish=bus.publish,
        snapshot_lookup=bus.execution_store.snapshot,
        parent_run_id="parent-fail",
    )
    assert report.status == "failed"
    assert report.exit_code == 7
    assert len(seen) == 1
    assert report.children[0].status == "failed"
    assert report.children[1].status == "blocked"
    assert report.children[1].run_id == ""
    parent = bus.execution_store.snapshot("parent-fail")
    assert parent is not None
    blocked = parent.node("federation:release:android")
    assert blocked is not None and blocked.status == "blocked"


def test_child_environment_is_restored_after_recursive_invocation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _load(_parent(tmp_path))
    bus = EventBus()
    callback, _ = _fake_reenter(bus)
    monkeypatch.setenv("DEVTOOL_RUN_ID", "outer-run")
    run_federated_workflow(
        config,
        "release",
        reenter=callback,
        publish=bus.publish,
        snapshot_lookup=bus.execution_store.snapshot,
        parent_run_id="parent-env",
    )
    assert os.environ["DEVTOOL_RUN_ID"] == "outer-run"
    assert "DEVTOOL_FEDERATION_PARENT_RUN_ID" not in os.environ
