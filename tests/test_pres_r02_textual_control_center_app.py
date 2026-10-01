from __future__ import annotations

import importlib.util
import inspect
import json
import os
import tomllib
from types import SimpleNamespace
from pathlib import Path

import pytest

from devtool.textual_control_center_runtime import (
    CONTROL_CENTER_PAGES,
    CanonicalControlActionDispatcher,
    InMemoryControlCenterProvider,
    RecordingControlActionDispatcher,
    create_textual_control_center_app,
)


def _payload() -> dict:
    return {
        "runs": [
            {"run_id": "run-a", "status": "running", "command": "validate", "project": "demo"},
            {"run_id": "run-b", "status": "failed", "command": "test", "project": "demo"},
        ],
        "nodes_by_run": {
            "run-a": [
                {"node_id": "root", "label": "Validate", "status": "running", "parent_id": ""},
                {"node_id": "gradle", "label": "Gradle", "status": "running", "parent_id": "root"},
            ],
        },
        "logs": {"path": "/tmp/run-a.log", "lines": ["line one", "line two"], "bounded": True},
        "diagnostics": [
            {
                "diagnostic_id": "diag-1",
                "severity": "error",
                "summary": "Compile failed",
                "message": "Compiler returned exit 1",
                "failure_kind": "test_failure",
                "file": "src/Main.kt",
                "remediation": "Fix the compile error",
                "evidence": ["run-a.log"],
                "source": "execution-store",
                "run_id": "run-a",
                "node_id": "gradle",
            }
        ],
        "history": [{"history_id": "hist-1", "applied_at": "2026-10-01T00:00:00Z", "status": "ok", "artifact": "x.zip", "operation": "apply-overlay"}],
        "targets": [{"name": "devtool", "kind": "python", "runner": "command", "platform": "termux"}],
        "workers": [{"profile": "tablet", "status": "ready", "provider": "ssh-native", "target": "devtool"}],
        "validation": {
            "active": True,
            "session_id": "validation-1",
            "revision": 2,
            "state": "running",
            "phases": [{"key": "lint", "label": "Lint", "status": "running", "attempts": 1, "progress": {"percent": 50}}],
        },
        "artifacts": [{"artifact_id": "artifact-1", "path": "build/app.apk", "artifact_type": "apk", "verification_status": "verified", "run_id": "run-a"}],
        "overlays": [{"history_id": "overlay-1", "artifact": "fix.zip", "status": "ok", "operation": "apply-overlay", "applied_at": "now"}],
        "evidence": [{"evidence_id": "evidence-1", "run_id": "run-a", "file_count": 3, "created_at": "now", "path": "/tmp/evidence-1"}],
    }


def test_r02_runtime_declares_all_concrete_control_center_pages() -> None:
    assert CONTROL_CENTER_PAGES == (
        "dashboard", "runs", "logs", "diagnostics", "history", "targets", "workers",
        "validation", "artifacts", "overlays", "evidence",
    )


def test_r02_runtime_source_is_widget_tree_not_single_static_debug_shell() -> None:
    import devtool.textual_control_center_runtime as runtime
    import devtool.tui_textual as tui_textual

    source = inspect.getsource(runtime.create_textual_control_center_app)
    entrypoint_source = inspect.getsource(tui_textual.run_textual_tui_runtime)
    for token in ("ContentSwitcher", "DataTable", "Tree", "RichLog", "Input", "ModalScreen", "HorizontalScroll", "set_interval", "log-follow-toggle", "log-copy"):
        assert token in source
    assert "Last control:" not in source
    assert "devtool-textual-dashboard" not in entrypoint_source
    assert "create_textual_control_center_app" in entrypoint_source
    assert "#page-switcher { height: 1fr; min-height: 0; }" in source
    assert "with HorizontalScroll(id=\"action-bar\")" in source


def test_in_memory_provider_is_projection_only_and_live_mutable() -> None:
    provider = InMemoryControlCenterProvider(_payload())
    first = provider.snapshot(selected_run="run-a", selected_node="gradle")
    assert first["owns_truth"] is False
    assert first["selected_run"] == "run-a"
    assert first["nodes_by_run"]["run-a"][1]["node_id"] == "gradle"
    provider.update(logs={"path": "/tmp/run-a.log", "lines": ["new"], "bounded": True})
    second = provider.snapshot(selected_run="run-a")
    assert second["version"] == first["version"] + 1
    assert second["logs"]["lines"] == ["new"]


def test_recording_dispatcher_proves_actions_leave_frontend() -> None:
    dispatcher = RecordingControlActionDispatcher(responses={"cancel": (True, "accepted")})
    ok, message = dispatcher.dispatch("cancel", run_id="run-a", node_id="gradle")
    assert ok is True and message == "accepted"
    assert dispatcher.calls == [{"action_id": "cancel", "run_id": "run-a", "node_id": "gradle", "payload": {}}]


def test_production_provider_source_reuses_canonical_authorities() -> None:
    import devtool.textual_control_center_runtime as runtime

    source = inspect.getsource(runtime.CanonicalControlCenterProvider)
    module_source = inspect.getsource(runtime)
    assert "get_execution_store" in module_source
    assert "RunCatalog.open" in module_source
    assert "load_active_session" in module_source
    assert "load_history" in module_source
    assert "load_all_provider_evidence" in module_source
    assert "state_dir() / \"evidence\"" in module_source
    assert "logs_dir()" in module_source
    assert "owns_truth" in source




def test_canonical_dispatcher_delegates_to_existing_runtime_authorities(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import devtool.textual_control_center_runtime as runtime

    calls: list[tuple] = []
    monkeypatch.setattr(runtime, "request_run_cancel", lambda run_id, **kwargs: (calls.append(("cancel", run_id, kwargs)) or (True, "cancelled")))
    monkeypatch.setattr(runtime, "request_run_action", lambda run_id, action, **kwargs: (calls.append(("run-action", run_id, action, kwargs)) or (True, "accepted")))

    class Result:
        returncode = 0
        output = "transaction-ok"

    monkeypatch.setattr(runtime, "rollback_transaction", lambda path, **kwargs: (calls.append(("rollback", path, kwargs)) or Result()))
    monkeypatch.setattr(runtime, "resume_transaction_result_publication", lambda path: (calls.append(("resume-publication", path)) or Result()))
    session = SimpleNamespace(session_id="session-a", revision=4)
    monkeypatch.setattr(runtime, "load_active_session", lambda *args, **kwargs: session)
    monkeypatch.setattr(runtime, "write_control_request", lambda current, action, **kwargs: (calls.append(("validation", current.session_id, action, kwargs)) or {"action": action}))

    dispatcher = CanonicalControlActionDispatcher(tmp_path)
    assert dispatcher.dispatch("cancel", run_id="run-a")[0]
    assert dispatcher.dispatch("retry", run_id="run-a", node_id="node-a")[0]
    assert dispatcher.dispatch("validation.pause")[0]
    assert dispatcher.dispatch("rollback", payload={"transaction_path": "/tmp/transaction.json"})[0]
    assert dispatcher.dispatch("recover", payload={"transaction_path": "/tmp/transaction.json"})[0]

    assert any(item[0] == "cancel" and item[1] == "run-a" for item in calls)
    assert any(item[0] == "run-action" and item[2] == "operation.retry" and item[3]["payload"]["node_id"] == "node-a" for item in calls)
    assert any(item[0] == "validation" and item[2] == "pause-after-phase" for item in calls)
    assert any(item[0] == "rollback" for item in calls)
    assert any(item[0] == "resume-publication" for item in calls)


def test_r02_campaign_ledger_and_workflow_match_delivered_boundary() -> None:
    root = Path(__file__).resolve().parents[1]
    ledger = json.loads((root / "PRES_R02_PROMISE_LEDGER.json").read_text(encoding="utf-8"))
    assert ledger["overlay"] == "PRES-R02"
    assert ledger["position"] == "62/76"
    assert ledger["gate_window_position"] == "2/4"
    assert ledger["implementation_position"] == "2/3"
    assert ledger["status"] == "implemented_pending_native_qualification"
    assert ledger["promises"] and all(row["status"] == "delivered" for row in ledger["promises"])
    assert any("main-thread host" in item for item in ledger["still_open"])
    assert any("PRES-R03" in item and "shortcut" in item for item in ledger["still_open"])
    assert any("Artifact/overlay apply" in item for item in ledger["still_open"])

    report = (root / "PRES_R02_CONCRETE_TEXTUAL_CONTROL_CENTER_REPORT.md").read_text(encoding="utf-8")
    assert "gate-window overlay 2 of 4" in report
    assert "full-plan 62/76" in report
    assert "PRES-R03 — 63/76" in report

    config = tomllib.loads((root / ".devtool.toml").read_text(encoding="utf-8"))
    command = config["wrapper"]["commands"]["pres-r02"]
    assert command["workflow"] == "roadmap-pres-r02"
    assert command["target"] == "devtool"
    workflow = config["targets"]["devtool"]["workflows"]["roadmap-pres-r02"]
    refs = [item["ref"] for item in workflow]
    assert refs == [
        "job:source-syntax",
        "test:roadmap-pres-r02-control-center",
        "test:roadmap-pres-r02-pres-regression",
        "test:roadmap-pres-r02-real-textual-app",
    ]
    test_defs = {row["id"]: row for row in config["test"] if isinstance(row, dict) and row.get("id")}
    real_app = test_defs["roadmap-pres-r02-real-textual-app"]
    assert real_app["env"]["DEVTOOL_PRES_R02_REQUIRE_TEXTUAL"] == "1"

def _require_textual() -> None:
    if importlib.util.find_spec("textual") is not None:
        return
    if os.environ.get("DEVTOOL_PRES_R02_REQUIRE_TEXTUAL") == "1":
        pytest.fail("PRES-R02 requires the concrete Textual package for App.run_test()/Pilot qualification")
    pytest.skip("Textual is optional on this construction host")


def test_real_textual_control_center_pages_selection_confirmation_and_live_refresh(tmp_path: Path) -> None:
    import asyncio

    _require_textual()
    from textual.widgets import Button, ContentSwitcher, DataTable, Input, RichLog, Tree

    provider = InMemoryControlCenterProvider(_payload())
    dispatcher = RecordingControlActionDispatcher()
    app = create_textual_control_center_app(
        repo_root=tmp_path,
        page="dashboard",
        provider=provider,
        dispatcher=dispatcher,
        refresh_interval=0.05,
    )

    async def exercise() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            switcher = app.query_one("#page-switcher", ContentSwitcher)
            assert switcher.current == "dashboard"
            assert app.query_one("#dashboard-runs", DataTable).row_count == 2

            await pilot.press("u")
            await pilot.pause()
            assert switcher.current == "runs"
            run_table = app.query_one("#run-table", DataTable)
            run_table.move_cursor(row=0, column=0, animate=False)
            await pilot.pause()
            assert app.selected_run in {"run-a", "run-b"}

            if app.selected_run != "run-a":
                for row_index in range(run_table.row_count):
                    run_table.move_cursor(row=row_index, column=0, animate=False)
                    await pilot.pause()
                    if app.selected_run == "run-a":
                        break
            assert app.selected_run == "run-a"
            node_tree = app.query_one("#node-tree", Tree)
            assert len(node_tree.root.children) >= 1

            await pilot.press("l")
            await pilot.pause()
            assert switcher.current == "logs"
            assert app.query_one("#logs-view", RichLog) is not None
            assert app._last_log_lines == ["line one", "line two"]
            log_filter = app.query_one("#log-filter", Input)
            log_filter.value = "two"
            await pilot.pause()
            assert app._last_log_lines == ["line two"]
            log_filter.value = ""
            await pilot.pause()
            assert app._last_log_lines == ["line one", "line two"]

            await pilot.click("#log-follow-toggle")
            await pilot.pause()
            assert app._log_follow is False
            provider.update(logs={"path": "/tmp/run-a.log", "lines": ["paused update"], "bounded": True})
            await pilot.pause(0.12)
            assert app._last_log_lines == ["line one", "line two"]
            await pilot.click("#log-follow-toggle")
            await pilot.pause()
            assert app._log_follow is True
            assert app._last_log_lines == ["paused update"]

            await pilot.press("g")
            await pilot.pause()
            assert switcher.current == "diagnostics"
            assert app.query_one("#diagnostics-table", DataTable).row_count == 1
            assert getattr(app.focused, "id", None) == "diagnostics-table"

            # Destructive run control must go through an actual visible control,
            # real ModalScreen confirmation, and then the injected dispatcher rather
            # than returning/debug-printing a request dict. Shortcut normalization is
            # intentionally R03-owned; this R02 proof exercises the concrete action UI.
            app.selected_run = "run-a"
            app.selected_node = "gradle"
            app._update_action_state()
            await pilot.click("#action-cancel")
            await pilot.pause()
            assert app.screen.__class__.__name__ == "ConfirmationModal"
            await pilot.click("#confirm-yes")
            await pilot.pause()
            assert dispatcher.calls[-1]["action_id"] == "cancel"
            assert dispatcher.calls[-1]["run_id"] == "run-a"
            assert dispatcher.calls[-1]["node_id"] == "gradle"

            # The global action strip is part of the viewport contract, not content
            # that may fall below the ContentSwitcher. On a Termux-like narrow
            # screen it remains reachable via its horizontal scroll container.
            await pilot.resize_terminal(64, 22)
            await pilot.pause()
            action_cancel = app.query_one("#action-cancel", Button)
            action_cancel.scroll_visible()
            await pilot.pause()
            await pilot.click("#action-cancel")
            await pilot.pause()
            assert app.screen.__class__.__name__ == "ConfirmationModal"
            await pilot.click("#confirm-no")
            await pilot.pause()

            provider.update(
                runs=[{"run_id": "run-a", "status": "passed", "command": "validate", "project": "demo"}],
                logs={"path": "/tmp/run-a.log", "lines": ["done"], "bounded": True},
            )
            await pilot.pause(0.12)
            assert app.snapshot_data.get("version", 0) >= 1
            assert app.snapshot_data["runs"][0]["status"] == "passed"
            assert app._last_log_lines == ["done"]

            await pilot.press("?")
            await pilot.pause()
            assert app.screen.__class__.__name__ == "HelpModal"
            await pilot.click("#help-close")
            await pilot.pause()
            await pilot.press("q")


    asyncio.run(exercise())

def test_r02_no_fixed_100x28_dimensions_in_interactive_app() -> None:
    import devtool.textual_control_center_runtime as runtime
    source = inspect.getsource(runtime.create_textual_control_center_app)
    assert "width=100" not in source
    assert "height=28" not in source
    assert "on_resize" in source
    assert "call_after_refresh(self._focus_page_primary, target)" in source
    assert '"diagnostics": "#diagnostics-table"' in source
