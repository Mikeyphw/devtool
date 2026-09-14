from __future__ import annotations

import json
import math
import os
import socket
import stat
import sys
import threading
import time
from pathlib import Path

from rich.console import Console

from devtool.desktop.command_model import DevtoolCommandDraft, split_extra_arguments
from devtool.desktop.control_center_model import (
    DesktopControlCenterModel,
    _raw_runs_from_ipc_or_hud,
    _run_summary_dict,
)
from devtool.desktop.gtk_runtime import append_gtk_log
from devtool.desktop.history_store import DesktopHistoryStore
from devtool.desktop.ipc_client import DesktopIpcClient
from devtool.desktop.preferences import DesktopPreferences
from devtool.desktop.run_process import DesktopRunLauncher, _line_severity
from devtool.desktop.run_workflow import RunWorkflowDraftPayload, draft_from_payload
from devtool.interactive.validation_menu import ValidationCatalog, _preview_control
from devtool.ui_core.actions import ActionContext, available_action_ids
from devtool.ui_core.capabilities import negotiate_capabilities
from devtool.ui_core.confirmations import confirmation_decision
from devtool.ui_core.preferences import resolve_ui_preferences
from devtool.ui_core.viewmodels import runtime_state_to_ui_snapshot
from devtool.tasker import TaskerProgressSink
from devtool.tui import TerminalProfile, _semantic_live_panels


def action_ids(snapshot: dict[str, object]) -> set[str]:
    return {str(item.get("id")) for item in snapshot.get("actions", []) if isinstance(item, dict)}


def test_string_false_does_not_enable_retry_resume_or_cancel() -> None:
    running = runtime_state_to_ui_snapshot(
        {"status": "running", "run_id": "r1", "command": "validate", "cancellable": "false"},
        surface="tui",
    ).as_dict()
    failed = runtime_state_to_ui_snapshot(
        {"status": "failed", "run_id": "r2", "command": "validate", "retryable": "false", "resumable": "false"},
        surface="tui",
    ).as_dict()
    assert "operation.cancel" not in action_ids(running)
    assert "operation.retry" not in action_ids(failed)
    assert "validation.resume" not in action_ids(failed)


def test_cancel_requires_run_id_to_be_enabled_and_artifact_actions_surface() -> None:
    payload = runtime_state_to_ui_snapshot({"status": "running", "command": "validate", "run_id": ""}, surface="tui").as_dict()
    cancel = next(item for item in payload["actions"] if item["id"] == "operation.cancel")
    assert cancel["enabled"] is False
    ids = available_action_ids(ActionContext(status="ok", command="apply-overlay", has_artifact=True))
    assert "artifact.apply" in ids


def test_rollback_failed_is_failed_terminal_and_retryable() -> None:
    ids = available_action_ids(ActionContext(status="rollback-failed", command="validate", retryable=True))
    assert "operation.retry" in ids
    assert "validation.retry_failed" in ids


def test_tui_live_progress_uses_canonical_summary_fields() -> None:
    snapshot = {
        "live": {
            "status": "running",
            "run_id": "r1",
            "command": "validate",
            "phase_progress": [
                {"node_id": "done", "name": "done", "status": "ok", "percent": 100},
                {"node_id": "run", "name": "run", "status": "running", "percent": 50},
            ],
        }
    }
    panels = _semantic_live_panels(snapshot, TerminalProfile(100, 30, "narrow", False))
    console = Console(record=True, width=120)
    for panel in panels:
        console.print(panel)
    text = console.export_text()
    assert "75% · 1/2" in text
    assert "run" in text


def test_progress_tree_preserves_cancelled_cyclic_and_duplicate_nodes() -> None:
    raw = {
        "status": "running",
        "run_id": "r",
        "command": "validate",
        "phase_progress": [
            {"node_id": "dupe", "name": "first", "status": "running", "workflow": "wf", "target": "app"},
            {"node_id": "dupe", "name": "second", "status": "cancelled", "workflow": "wf", "target": "app"},
            {"node_id": "a", "parent_id": "b", "name": "a", "status": "pending"},
            {"node_id": "b", "parent_id": "a", "name": "b", "status": "pending"},
        ],
    }
    payload = runtime_state_to_ui_snapshot(raw, surface="desktop").as_dict()
    ids = {item["id"] for item in payload["progress"]}
    assert {"dupe", "dupe#2", "a", "b"}.issubset(ids)
    tree_text = json.dumps(payload["progress_tree"])
    assert '"status": "cancelled"' in tree_text
    assert '"id": "a"' in tree_text and '"id": "b"' in tree_text


def test_duplicate_diagnostics_from_different_phases_are_preserved() -> None:
    raw = {
        "status": "failed",
        "results": [
            {"name": "compile", "errors": [{"message": "boom", "code": "E"}]},
            {"name": "test", "errors": [{"message": "boom", "code": "E"}]},
        ],
    }
    payload = runtime_state_to_ui_snapshot(raw, surface="desktop").as_dict()
    assert payload["diagnostic_summary"]["errors"] == 2
    assert {item["phase"] for item in payload["diagnostics"]} == {"compile", "test"}


def test_confirmation_string_false_context_values_are_false() -> None:
    cancel = confirmation_decision(
        "operation.cancel",
        {"cancel": "smart"},
        context={"destructive_phase_started": "false", "has_uncommitted_mutation": "false", "confirm_cancel": "false", "default": "false"},
    )
    assert cancel.required is False
    assert cancel.default is False
    apply = confirmation_decision(
        "artifact.apply",
        {"apply_artifact": "smart"},
        context={"destructive": "false", "deletes": "false", "overwrites": "false", "untrusted": "false", "manifest_confirmation": "false"},
    )
    assert apply.required is False


def test_explicit_empty_env_is_respected_for_ui_preferences(monkeypatch) -> None:
    monkeypatch.setenv("DEVTOOL_UI_ICONS", "ascii")
    assert resolve_ui_preferences({}, surface="tui", env={}).icons == "auto"
    assert resolve_ui_preferences({}, surface="tui", env={"DEVTOOL_UI_ICONS": "ascii"}).icons == "ascii"


def test_malformed_numeric_preferences_and_progress_do_not_crash_snapshot() -> None:
    payload = runtime_state_to_ui_snapshot(
        {
            "status": "running",
            "phase_progress": [{"name": "bad", "status": "running", "percent": float("inf")}],
        },
        surface="tui",
        preferences={"surface": "tui", "profile": "standard", "provenance": "ipc", "max_steps": "inf"},
        requested_capabilities=["not-a-real-capability"],
    ).as_dict()
    assert isinstance(payload["presentation"]["max_steps"], int) and payload["presentation"]["max_steps"] >= 0
    assert payload["progress"][0]["percent"] is None
    assert "not_a_real_capability" in payload["capability_negotiation"]["missing"]


def test_tasker_non_finite_progress_is_not_exported(tmp_path: Path) -> None:
    sink = TaskerProgressSink(directory=tmp_path, command="validate", repo_root=str(tmp_path), repo_name="demo", enabled=True)
    sink.started()
    sink.phase_started("compile", index=1, total=1)
    sink.progress(percent=float("nan"), force=True)
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["progress"].get("percent") is None
    assert "percent" not in state["phase_progress"][0] or state["phase_progress"][0]["percent"] is None


def test_desktop_command_variant_partial_quotes_and_empty_argv_preserved(tmp_path: Path) -> None:
    assert split_extra_arguments('--flag "unfinished') == ['--flag "unfinished']
    draft = DevtoolCommandDraft(repository=tmp_path, operation="test", variant="debug", extra_operation_args=[""])
    argv = draft.argv()
    assert argv[argv.index("--variant") + 1] == "debug"
    payload_argv = draft_from_payload(RunWorkflowDraftPayload(repository=str(tmp_path), operation="test", variant="release")).argv()
    assert payload_argv[payload_argv.index("--variant") + 1] == "release"


def test_desktop_launcher_preserves_empty_argv_and_reports_cancelled(tmp_path: Path) -> None:
    launcher = DesktopRunLauncher(state_dir=tmp_path / "runs")
    handle = launcher.start([sys.executable, "-c", "import sys, time; print(len(sys.argv), flush=True); time.sleep(5)", ""], cwd=tmp_path)
    try:
        deadline = time.monotonic() + 2
        while not handle.stdout_lines and time.monotonic() < deadline:
            time.sleep(0.05)
        assert handle.stdout_lines == ["2"]
        assert launcher.cancel(handle) is True
        deadline = time.monotonic() + 5
        while handle.returncode is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert handle.status == "cancelled"
        assert "cancelled" in handle.summary()
    finally:
        if handle.process.poll() is None:
            handle.process.kill()


def test_desktop_launcher_callback_errors_do_not_kill_pump(tmp_path: Path) -> None:
    launcher = DesktopRunLauncher(state_dir=tmp_path / "runs")

    def bad_callback(*_args: object) -> None:
        raise RuntimeError("callback boom")

    handle = launcher.start([sys.executable, "-c", "print('ok')"], cwd=tmp_path, on_output=bad_callback)
    deadline = time.monotonic() + 5
    while handle.returncode is None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert handle.returncode == 0
    assert handle.status == "completed"
    assert handle.stdout_lines == ["ok"]


def test_desktop_ipc_reads_fragmented_json(tmp_path: Path) -> None:
    socket_path = tmp_path / "ipc.sock"
    payload = b'{"ok":true,"runs":[{"run_id":"r1"}]}' + b"\n"

    def server() -> None:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as srv:
            srv.bind(str(socket_path))
            srv.listen(1)
            conn, _ = srv.accept()
            with conn:
                conn.recv(128)
                conn.sendall(payload[:8])
                time.sleep(0.05)
                conn.sendall(payload[8:])

    thread = threading.Thread(target=server, daemon=True)
    thread.start()
    deadline = time.monotonic() + 2
    while not socket_path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    snapshot = DesktopIpcClient(socket_path=socket_path, timeout_s=2).snapshot()
    assert snapshot["ok"] is True
    assert snapshot["runs"][0]["run_id"] == "r1"


def test_desktop_history_limit_zero_and_nonfinite_model_numbers(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("devtool.artifacts.load_history", lambda _repo: [{"id": "a"}, {"id": "b"}])
    assert DesktopHistoryStore(tmp_path).load(limit=0) == []
    model = DesktopControlCenterModel(repository=tmp_path, history_limit="inf", recent_events="nan")
    assert model.history_limit == 25
    assert model.recent_events == 8


def test_desktop_hud_run_selection_prefers_live_ipc_over_stale_hud() -> None:
    runs = _raw_runs_from_ipc_or_hud(
        {"runs": [{"run_id": "live", "status": "running", "sequence": 3}]},
        {"run_id": "stale", "status": "running", "sequence": 1},
    )
    assert [item["run_id"] for item in runs] == ["live", "stale"]


def test_desktop_false_error_lines_and_nonfinite_summary_percent() -> None:
    assert _line_severity("BUILD SUCCESSFUL in 1s, 0 errors, 0 failed") == ""
    assert _line_severity("warning: but 0 errors") == "warning"
    summary = _run_summary_dict({"run_id": "r", "status": "running", "percent": float("inf")})
    assert summary["percent"] is None or isinstance(summary["percent"], int)


def test_desktop_logs_are_private(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    path = append_gtk_log("control-center", "token=SECRET123")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    launcher = DesktopRunLauncher(state_dir=tmp_path / "runs")
    handle = launcher.start([sys.executable, "-c", "print('done')"], cwd=tmp_path)
    deadline = time.monotonic() + 5
    while handle.returncode is None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert stat.S_IMODE(handle.log_path.stat().st_mode) == 0o600


def test_validation_control_preview_omits_revision_for_read_only_actions() -> None:
    catalog = ValidationCatalog(config=object(), session={"session_id": "s", "revision": 7, "state": "running"})  # type: ignore[arg-type]
    assert "--if-revision" not in _preview_control(catalog, "status")
    assert "--if-revision 7" in _preview_control(catalog, "pause")
