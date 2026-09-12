from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console

from devtool.cli import main as devtool_main
from devtool.hud import HudState, TerminalHud
from devtool.tui import render_page
from devtool.ui_core.actions import ActionContext, actions_for_context
from devtool.ui_core.confirmations import confirmation_decision, normalize_confirmation_policy
from devtool.ui_core.notifications import notification_for_event, normalize_notification_policy
from devtool.ui_core.preferences import resolve_ui_preferences
from devtool.ui_core.viewmodels import runtime_state_to_ui_snapshot

ROOT = Path(__file__).resolve().parents[1]


def test_confirmation_policy_supports_never_smart_always() -> None:
    policy = normalize_confirmation_policy({"cancel": "always", "retry": "never"})
    assert confirmation_decision("operation.cancel", policy).required is True
    assert confirmation_decision("operation.retry", policy).required is False
    smart = confirmation_decision(
        "operation.cancel", {"cancel": "smart"},
        context={"destructive_phase_started": True},
    )
    assert smart.required is True
    assert smart.mode == "smart"


def test_ui_preferences_resolve_confirmation_and_notification_tables() -> None:
    prefs = resolve_ui_preferences({
        "confirmations": {"cancel": "always", "rollback": "never"},
        "notifications": {"started": True, "success": False, "failures": True},
    }, surface="tui")
    assert prefs.confirmation_policy["cancel"] == "always"
    assert prefs.confirmation_policy["rollback"] == "never"
    assert prefs.notification_policy["started"] is True
    assert prefs.notification_policy["success"] is False


def test_action_states_include_confirmation_semantics() -> None:
    actions = actions_for_context(
        ActionContext(status="running", command="apply-overlay", run_id="run-1"),
        confirmation_policy={"cancel": "always"},
    )
    cancel = next(item for item in actions if item.id == "operation.cancel")
    assert cancel.confirmation_mode == "always"
    assert cancel.requires_confirmation is True
    assert cancel.destructive is True


def test_notification_policy_classifies_attention_and_bell() -> None:
    failed = notification_for_event(
        "failed", status="failed", severity="error", title="Build failed", body="boom",
        policy={"failures": True, "bell_on_failure": True},
    )
    assert failed.level == "critical"
    assert failed.should_emit is True
    assert failed.bell is True
    started = notification_for_event("started", status="running", policy={"started": False})
    assert started.should_emit is False


def test_runtime_snapshot_carries_policy_notifications_and_action_confirmation() -> None:
    snapshot = runtime_state_to_ui_snapshot({
        "run_id": "r1",
        "status": "failed",
        "command": "validate",
        "repo_name": "demo",
        "message": "tests failed",
        "diagnostics": [{"severity": "error", "message": "boom"}],
    }, surface="tui", preferences={
        "surface": "tui",
        "profile": "standard",
        "confirmation_policy": {"cancel": "smart", "retry": "never", "rollback": "always", "delete_artifact": "always", "apply_artifact": "smart", "artifact_manifest": "smart", "dirty_tree": "smart", "snapshot_overwrite": "always"},
        "notification_policy": {"started": False, "phase_changes": False, "success": True, "warnings": True, "failures": True, "requires_action": True, "bell_on_failure": True, "bell_on_requires_action": False},
        "provenance": {},
    })
    assert snapshot.confirmation_policy["schema_version"] == 1
    assert snapshot.notification_policy["schema_version"] == 1
    assert snapshot.notifications[0]["level"] == "important" or snapshot.notifications[0]["level"] == "critical"
    retry = next(item for item in snapshot.actions if item.id == "operation.retry")
    assert retry.requires_confirmation is False


def test_terminal_hud_renders_semantic_progress_diagnostics_and_actions() -> None:
    raw = {
        "run_id": "r1", "status": "failed", "command": "validate", "repo_name": "demo",
        "message": "compile failed", "log_path": "/tmp/run.log",
        "phase_progress": [{"phase_id": "compile", "display_name": "Compile", "status": "failed", "percent": 70}],
        "diagnostics": [{"severity": "error", "message": "Missing symbol", "file": "a.py", "line": 7}],
    }
    state = HudState(title="devtool", text="compile failed", status="failed", command="validate", repo_name="demo", raw=raw)
    console = Console(record=True, width=100)
    hud = TerminalHud(console=console)
    console.print(hud.render(state, []))
    text = console.export_text()
    assert "Compile" in text
    assert "Missing symbol" in text
    assert "Retry" in text
    assert "Open log" in text


def test_tui_build_page_uses_semantic_live_panels(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("devtool.tui._repo_snapshot", lambda _root: {
        "repo": "demo", "root": str(tmp_path), "branch": "main", "commit": "abc", "dirty": False,
        "dirty_count": 0, "artifacts": [], "history": [], "core_events": [],
        "live": {
            "run_id": "r1", "status": "running", "command": "validate", "repo_name": "demo",
            "phase_name": "tests", "message": "running selected tests", "log_path": "/tmp/run.log",
            "phase_progress": [{"phase_id": "tests", "display_name": "Tests", "status": "running", "current": 2, "total": 4}],
        },
    })
    renderable, _ = render_page(tmp_path, page="build", width=100, height=30)
    console = Console(record=True, width=100)
    console.print(renderable)
    text = console.export_text()
    assert "Live build/test run" in text
    assert "Progress" in text
    assert "Actions" in text
    assert "Cancel" in text


def test_ui_contract_cli_exposes_confirmation_notification_contracts(capsys) -> None:
    rc = devtool_main(["ui-contract", "--surface", "tui", "--json"])
    assert rc in {None, 0}
    payload = json.loads(capsys.readouterr().out)
    payload = payload.get("data", payload)
    assert payload["confirmation_schema_version"] == 1
    assert payload["notification_schema_version"] == 1
    assert payload["confirmation_policy"]["policy"]["rollback"] in {"always", "never", "smart"}
    assert payload["notification_policy"]["events"]["failed"]["level"] == "critical"
