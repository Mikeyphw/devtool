from __future__ import annotations

import json
from pathlib import Path

from devtool.core.events import EventBus, NodeDiscovered, NodeFinished, NodeProgressChanged, NodeStarted, RunFinished, RunStarted
from devtool.core.execution_subscribers import canonical_execution_document
from devtool.hud import canonical_diagnostics, live_issue_counts, operation_steps, snapshot_from_directory, state_from_directory


def _failed_execution_document() -> dict:
    bus = EventBus()
    bus.publish(RunStarted(run_id="exo08", command="validate", project="demo"))
    bus.publish(NodeDiscovered(run_id="exo08", node_id="build", label="Build", kind="build"))
    bus.publish(NodeStarted(run_id="exo08", node_id="build", label="Build", kind="build"))
    bus.publish(NodeProgressChanged(run_id="exo08", node_id="build", percent=43, current=43, total=100, unit="percent", detail="Compiling"))
    # EXO-06 guarantees real executions normally carry a diagnostic. For this HUD
    # migration fixture the failed lifecycle/progress truth is enough.
    bus.publish(NodeFinished(run_id="exo08", node_id="build", status="failed", exit_code=1, failure_kind="compile_failure"))
    bus.publish(RunFinished(run_id="exo08", status="failed", exit_code=1))
    return canonical_execution_document(bus.execution_store.snapshot("exo08"))


def test_state_from_directory_prefers_canonical_execution_over_stale_legacy_state(tmp_path: Path) -> None:
    (tmp_path / "execution.json").write_text(json.dumps(_failed_execution_document()))
    (tmp_path / "state.json").write_text(json.dumps({"status": "ok", "command": "validate", "percent": 100, "phase_name": "Done"}))
    state = state_from_directory(tmp_path, prefer_execution=True)
    assert state.status == "failed"
    assert state.percent == 43
    assert state.phase_name == "Build"
    assert state.raw["execution_truth"] == "canonical"
    assert state.raw["execution"]["run_id"] == "exo08"


def test_operation_steps_are_direct_execution_nodes_not_phase_name_inference(tmp_path: Path) -> None:
    (tmp_path / "execution.json").write_text(json.dumps(_failed_execution_document()))
    state = state_from_directory(tmp_path, prefer_execution=True)
    steps = operation_steps(state, [{"event_type": "phase-finished", "phase": {"name": "test"}, "status": "ok"}])
    assert [(step.key, step.label, step.status, step.progress) for step in steps] == [("build", "Build", "failed", 43)]


def test_snapshot_exposes_canonical_runtime_state_for_tui(tmp_path: Path) -> None:
    (tmp_path / "execution.json").write_text(json.dumps(_failed_execution_document()))
    payload = snapshot_from_directory(tmp_path, prefer_execution=True)
    assert payload["version"] == 5
    assert payload["execution"]["run_id"] == "exo08"
    assert payload["runtime_state"]["execution_truth"] == "canonical"
    assert payload["runtime_state"]["progress"]["measured_percent"] == 43
    assert payload["runtime_state"]["progress"]["resolution_percent"] == 100
    assert payload["paths"]["execution"].endswith("execution.json")


def test_canonical_execution_suppresses_legacy_event_diagnostic_guessing(tmp_path: Path) -> None:
    document = _failed_execution_document()
    execution = document["execution"]
    execution["diagnostics"] = [{
        "diagnostic_id": "diag-1", "node_id": "build", "severity": "error", "code": "E42",
        "message": "canonical compiler failure", "file": "src/a.py", "line": 7, "column": 2,
        "process_id": "proc-1", "failure_kind": "compile_failure", "log_path": "/logs/build.log",
        "evidence": ["/logs/build.log"], "timestamp": "", "source": "runner",
    }]
    (tmp_path / "execution.json").write_text(json.dumps(document))
    state = state_from_directory(tmp_path, prefer_execution=True)
    guessed_event = {"event_type": "error", "message": "different guessed error"}
    diagnostics = canonical_diagnostics([guessed_event], state)
    assert [item["message"] for item in diagnostics] == ["canonical compiler failure"]
    assert diagnostics[0]["node_id"] == "build"
    assert diagnostics[0]["process_id"] == "proc-1"
    assert live_issue_counts([guessed_event], state) == (1, 0)


def test_non_terminal_surfaces_keep_legacy_state_until_their_migration(tmp_path: Path) -> None:
    (tmp_path / "execution.json").write_text(json.dumps(_failed_execution_document()))
    (tmp_path / "state.json").write_text(json.dumps({"status": "ok", "command": "validate", "current": 1, "total": 1, "phase_name": "Legacy"}))
    state = state_from_directory(tmp_path)
    assert state.status == "ok"
    assert state.phase_name == "Legacy"
    assert "execution_truth" not in state.raw


def test_tui_repo_snapshot_reads_canonical_runtime_projection(monkeypatch, tmp_path: Path) -> None:
    from devtool import tasker
    from devtool.tui import _repo_snapshot

    repo = tmp_path / "demo"
    repo.mkdir()
    hud = tmp_path / "hud"
    hud.mkdir()
    (hud / "execution.json").write_text(json.dumps(_failed_execution_document()))
    monkeypatch.setattr(tasker, "DEFAULT_TASKER_DIR", hud)

    snapshot = _repo_snapshot(repo)
    assert snapshot["live"]["execution_truth"] == "canonical"
    assert snapshot["live"]["execution"]["run_id"] == "exo08"
    assert snapshot["live"]["progress"]["measured_percent"] == 43
