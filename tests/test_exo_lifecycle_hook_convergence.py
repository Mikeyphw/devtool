from __future__ import annotations

import hashlib
import json
from pathlib import Path

from devtool.artifact_validation import (
    run_artifact_finalize_script,
    run_artifact_preflight_script,
    run_artifact_validation_script,
)
from devtool.artifacts import write_archive_entries
from devtool.core.events import RunStarted, get_event_bus, get_execution_store, publish_event
from devtool.core.execution_subscribers import ExecutionProjectionSubscriber


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _archive(path: Path) -> tuple[Path, dict]:
    preflight = b"printf 'preflight-ok\\n'\n"
    validate = b"printf 'validate-ok\\n'\n"
    finalize = b"cat >/dev/null; printf 'finalize-ok\\n'\n"
    manifest = {
        "schema_version": 2,
        "type": "overlay",
        "target": "devtool",
        "preflight": {
            "phases": ["before_extract"],
            "script": {
                "path": ".devtool-artifact/preflight.sh",
                "runner": "bash",
                "sha256": _sha(preflight),
                "timeout_seconds": 20,
            },
        },
        "validation": {
            "required": True,
            "restore": False,
            "build": False,
            "test": False,
            "script": {
                "path": ".devtool-artifact/validate.sh",
                "runner": "bash",
                "sha256": _sha(validate),
                "timeout_seconds": 20,
            },
        },
        "finalize": {
            "phases": ["before_commit", "after_commit"],
            "script": {
                "path": ".devtool-artifact/finalize.sh",
                "runner": "bash",
                "protocol": "json-v1",
                "sha256": _sha(finalize),
                "timeout_seconds": 20,
            },
        },
    }
    write_archive_entries(
        path,
        {
            ".devtool-artifact.json": json.dumps(manifest).encode(),
            ".devtool-artifact/preflight.sh": preflight,
            ".devtool-artifact/validate.sh": validate,
            ".devtool-artifact/finalize.sh": finalize,
        },
    )
    return path, manifest


def _assert_hook_snapshot(run_id: str, *, kind: str, phase: str) -> None:
    snapshot = get_execution_store().snapshot(run_id)
    assert snapshot is not None
    matches = [
        node for node in snapshot.nodes
        if node.kind == "artifact-hook"
        and node.metadata.get("hook_kind") == kind
        and node.metadata.get("hook_phase") == phase
    ]
    assert len(matches) == 1
    node = matches[0]
    assert node.status == "succeeded"
    assert node.metadata.get("lifecycle_authority") == "exo"
    processes = [process for process in snapshot.processes if process.node_id == node.node_id]
    assert len(processes) == 1
    assert processes[0].status == "succeeded"
    assert processes[0].exit_code == 0


def test_artifact_lifecycle_scripts_are_authoritative_exo_nodes_and_processes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    archive, manifest = _archive(tmp_path / "hooks.zip")
    run_id = "run-hook-exo-convergence"
    store = get_execution_store()
    store.clear(run_id)
    publish_event(RunStarted(run_id=run_id, command="apply-overlay", project=str(repo), source="test"))

    preflight = run_artifact_preflight_script(
        repo, archive, phase="before_extract", manifest=manifest, run_id=run_id
    )
    validator = run_artifact_validation_script(repo, archive, manifest=manifest, run_id=run_id)
    before_commit = run_artifact_finalize_script(
        repo, archive, phase="before_commit", manifest=manifest, run_id=run_id
    )
    after_commit = run_artifact_finalize_script(
        repo, archive, phase="after_commit", manifest=manifest, run_id=run_id
    )

    assert preflight and preflight.returncode == 0
    assert validator and validator.returncode == 0
    assert before_commit and before_commit.returncode == 0
    assert after_commit and after_commit.returncode == 0
    _assert_hook_snapshot(run_id, kind="preflight", phase="before_extract")
    _assert_hook_snapshot(run_id, kind="validation", phase="validate")
    _assert_hook_snapshot(run_id, kind="finalize", phase="before_commit")
    _assert_hook_snapshot(run_id, kind="finalize", phase="after_commit")


def test_hook_failure_is_visible_in_exo_node_process_and_diagnostic(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    script = b"printf 'bad-hook\\n'; exit 7\n"
    manifest = {
        "schema_version": 2,
        "type": "overlay",
        "target": "devtool",
        "validation": {
            "required": True,
            "restore": False,
            "build": False,
            "test": False,
            "script": {
                "path": ".devtool-artifact/validate.sh",
                "runner": "bash",
                "sha256": _sha(script),
                "timeout_seconds": 20,
            },
        },
    }
    archive = tmp_path / "bad.zip"
    write_archive_entries(
        archive,
        {
            ".devtool-artifact.json": json.dumps(manifest).encode(),
            ".devtool-artifact/validate.sh": script,
        },
    )
    run_id = "run-hook-exo-failure"
    store = get_execution_store()
    store.clear(run_id)
    publish_event(RunStarted(run_id=run_id, command="apply-overlay", project=str(repo), source="test"))

    result = run_artifact_validation_script(repo, archive, manifest=manifest, run_id=run_id)
    assert result and result.returncode == 7
    snapshot = store.snapshot(run_id)
    assert snapshot is not None
    node = next(node for node in snapshot.nodes if node.kind == "artifact-hook")
    assert node.status == "failed"
    assert node.exit_code == 7
    assert node.failure_kind == "validation_failure"
    assert node.diagnostic_ids
    process = next(process for process in snapshot.processes if process.node_id == node.node_id)
    assert process.status == "failed"
    assert process.exit_code == 7


def test_retried_hook_gets_distinct_exo_attempt_node(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    archive, manifest = _archive(tmp_path / "retry.zip")
    run_id = "run-hook-exo-retry"
    store = get_execution_store()
    store.clear(run_id)
    publish_event(RunStarted(run_id=run_id, command="apply-overlay", project=str(repo), source="test"))

    first = run_artifact_validation_script(repo, archive, manifest=manifest, run_id=run_id)
    second = run_artifact_validation_script(repo, archive, manifest=manifest, run_id=run_id)
    assert first and first.returncode == 0
    assert second and second.returncode == 0
    snapshot = store.snapshot(run_id)
    assert snapshot is not None
    nodes = [node for node in snapshot.nodes if node.kind == "artifact-hook" and node.metadata.get("hook_kind") == "validation"]
    assert len(nodes) == 2
    assert len({node.node_id for node in nodes}) == 2
    assert sorted(int(node.metadata.get("hook_attempt", 0)) for node in nodes) == [1, 2]


def test_hook_exo_events_feed_the_durable_ui_execution_projection(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    archive, manifest = _archive(tmp_path / "ui.zip")
    run_id = "run-hook-exo-ui"
    bus = get_event_bus()
    bus.execution_store.clear(run_id)
    projection_dir = tmp_path / "runtime"
    projection = ExecutionProjectionSubscriber(
        store=bus.execution_store, run_id=run_id, directory=projection_dir
    )
    bus.subscribe_all(projection)
    try:
        publish_event(RunStarted(run_id=run_id, command="apply-overlay", project=str(repo), source="test"))
        result = run_artifact_validation_script(repo, archive, manifest=manifest, run_id=run_id)
        assert result and result.returncode == 0
        payload = json.loads((projection_dir / "execution.json").read_text(encoding="utf-8"))
        nodes = payload["execution"]["nodes"]
        lifecycle = [node for node in nodes if node.get("kind") == "artifact-hook"]
        assert len(lifecycle) == 1
        assert lifecycle[0]["status"] == "succeeded"
        assert lifecycle[0]["metadata"]["lifecycle_authority"] == "exo"
        processes = payload["execution"]["processes"]
        assert any(process.get("node_id") == lifecycle[0]["node_id"] and process.get("status") == "succeeded" for process in processes)
    finally:
        bus.unsubscribe_all(projection)
        projection.close()
