from __future__ import annotations

import json
from pathlib import Path

from devtool.core.events import (
    DiagnosticEmitted,
    EventBus,
    NodeDiscovered,
    NodeFinished,
    NodeProgressChanged,
    NodeStarted,
    RunFinished,
    RunStarted,
)
from devtool.core.execution_subscribers import canonical_execution_document
from devtool.desktop.control_center_model import _raw_runs_from_ipc_or_hud, _run_summary_dict
from devtool.desktop.execution_truth import canonical_run_from_ipc, desktop_runtime_payload
from devtool.desktop.hud_store import load_hud_snapshot
from devtool.desktop.run_process import DesktopRunHandle


def _document(run_id: str = "desktop-run") -> dict:
    bus = EventBus()
    bus.publish(RunStarted(run_id=run_id, command="validate", project="demo"))
    bus.publish(NodeDiscovered(run_id=run_id, node_id="compile", label="Compile", kind="build"))
    bus.publish(NodeStarted(run_id=run_id, node_id="compile", label="Compile", kind="build"))
    bus.publish(NodeProgressChanged(run_id=run_id, node_id="compile", percent=43, current=43, total=100, unit="percent", detail="Compiling"))
    bus.publish(DiagnosticEmitted(
        run_id=run_id,
        node_id="compile",
        diagnostic_id="diag-desktop",
        severity="error",
        code="E9",
        message="canonical desktop failure",
        process_id=f"{run_id}:pid:123",
        failure_kind="compile_failure",
        log_path="/logs/compile.log",
        evidence=("/logs/compile.log",),
        source="runner",
    ))
    bus.publish(NodeFinished(
        run_id=run_id,
        node_id="compile",
        status="failed",
        exit_code=1,
        failure_kind="compile_failure",
        process_id=f"{run_id}:pid:123",
        diagnostic_ids=("diag-desktop",),
    ))
    bus.publish(RunFinished(run_id=run_id, status="failed", exit_code=1))
    return canonical_execution_document(bus.execution_store.snapshot(run_id))


def test_desktop_hud_store_prefers_execution_json_over_stale_state(tmp_path: Path) -> None:
    (tmp_path / "execution.json").write_text(json.dumps(_document()), encoding="utf-8")
    (tmp_path / "state.json").write_text(json.dumps({
        "run_id": "desktop-run", "status": "ok", "percent": 100, "phase_name": "Done",
    }), encoding="utf-8")

    snap = load_hud_snapshot(tmp_path)

    assert snap.state["desktop_truth"] == "canonical"
    assert snap.state["status"] == "failed"
    assert snap.state["progress"]["measured_percent"] == 43
    assert snap.ui["progress_summary"]["measured_percent"] == 43
    assert snap.ui["progress_summary"]["overall_percent"] == 43
    assert snap.ui["progress_summary"]["resolution_percent"] == 100


def test_ipc_run_embedded_execution_overrides_legacy_success_fields() -> None:
    item = {"run_id": "desktop-run", "status": "ok", "percent": 100, **_document()}
    # IPC subscriber embeds execution under the run payload rather than replacing
    # the legacy envelope; canonicalization must still win.
    item = {"run_id": "desktop-run", "status": "ok", "percent": 100, "execution": _document()["execution"]}
    runtime = desktop_runtime_payload(item)
    assert runtime["desktop_truth"] == "canonical"
    assert runtime["status"] == "failed"
    assert runtime["progress"]["measured_percent"] == 43
    assert canonical_run_from_ipc({"runs": [item]}, "desktop-run")["phase"]["node_id"] == "compile"


def test_control_center_summary_uses_measured_percent_not_legacy_percent() -> None:
    run = {"run_id": "desktop-run", "status": "ok", "percent": 100, "execution": _document()["execution"]}
    summary = _run_summary_dict(run)
    assert summary["status"] == "failed"
    assert summary["percent"] == 43
    assert summary["errors"] == 1
    assert summary["steps"][0]["key"] == "compile"
    assert summary["steps"][0]["progress"] == 43


def test_raw_runs_from_ipc_canonicalizes_before_merging_hud_fallback() -> None:
    ipc_run = {"run_id": "desktop-run", "status": "ok", "percent": 100, "execution": _document()["execution"]}
    runs = _raw_runs_from_ipc_or_hud({"runs": [ipc_run]}, {"run_id": "desktop-run", "status": "ok", "percent": 100})
    assert len(runs) == 1
    assert runs[0]["desktop_truth"] == "canonical"
    assert runs[0]["status"] == "failed"


def test_desktop_run_handle_canonical_state_overrides_output_observations(tmp_path: Path) -> None:
    class Process:
        pid = 123
        def poll(self): return 1

    handle = DesktopRunHandle(
        run_id="desktop-run",
        argv=["devtool", "validate"],
        cwd=tmp_path,
        environment={},
        log_path=tmp_path / "run.log",
        process=Process(),  # type: ignore[arg-type]
        status="completed",
        returncode=0,
        current_phase="guessed phase",
        diagnostics=[{"severity": "warning", "message": "guessed warning", "source": "stderr"}],
    )
    assert handle.apply_runtime_state({"execution": _document()["execution"]}) is True
    assert handle.semantic_status == "failed"
    assert handle.semantic_phase == "Compile"
    assert [item["message"] for item in handle.semantic_diagnostics] == ["canonical desktop failure"]
    assert "failed · Compile" in handle.summary()
    assert "exit 0" not in handle.summary()
    assert handle.failure_summary() == "canonical desktop failure"
