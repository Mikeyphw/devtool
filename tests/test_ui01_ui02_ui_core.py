from __future__ import annotations

import json
from pathlib import Path

from devtool.cli import main as devtool_main
from devtool.desktop.control_center_model import DesktopControlCenterModel
from devtool.hud import AndroidAppHud, parse_hud_state, snapshot_from_directory
from devtool.tasker import TaskerProgressSink
from devtool.ui_core import (
    ACTION_REGISTRY,
    NAVIGATION_REGISTRY,
    ActionContext,
    action_catalog,
    actions_for_context,
    navigation_catalog,
    navigation_for_surface,
    runtime_state_to_ui_snapshot,
)
from devtool.ui_core.navigation import navigation_icon_map, navigation_label_for_route, navigation_route_map, navigation_shortcut_map


def test_ui_core_registries_have_stable_unique_ids() -> None:
    assert list(ACTION_REGISTRY) == [item["id"] for item in action_catalog()]
    assert len(ACTION_REGISTRY) == len(set(ACTION_REGISTRY))
    navigation_ids = [item.id for item in NAVIGATION_REGISTRY]
    assert len(navigation_ids) == len(set(navigation_ids))
    assert [item["id"] for item in navigation_catalog()] == navigation_ids


def test_action_registry_centralizes_running_run_actions() -> None:
    actions = actions_for_context(ActionContext(
        status="running",
        command="validate",
        run_id="run-1",
        log_path="/tmp/run.log",
        target="api",
        has_diagnostics=True,
    ))
    ids = [item.id for item in actions]
    assert ids == [
        "operation.cancel",
        "logs.open",
        "summary.copy",
        "diagnostics.copy",
        "workflow.inspect",
    ]
    cancel = actions[0]
    assert cancel.destructive is True
    assert cancel.label == "Cancel operation"
    assert cancel.notification_label == "Stop"
    assert cancel.payload["run_id"] == "run-1"
    assert cancel.payload["target"] == "api"


def test_action_registry_centralizes_failed_validation_actions() -> None:
    actions = actions_for_context(ActionContext(
        status="failed",
        command="validate",
        run_id="run-2",
        target="server",
        has_diagnostics=True,
    ))
    ids = [item.id for item in actions]
    assert ids[:2] == ["operation.retry", "validation.retry_failed"]
    assert "summary.copy" in ids
    assert "diagnostics.copy" in ids
    assert "workflow.inspect" in ids


def test_navigation_registry_preserves_current_desktop_routes() -> None:
    routes = navigation_route_map("desktop")
    assert list(routes) == [
        "overview",
        "runs",
        "history",
        "artifacts",
        "diagnostics",
        "repositories",
        "targets",
        "run",
        "settings",
    ]
    assert routes["overview"] == "Overview"
    assert routes["runs"] == "Active runs"
    icons = navigation_icon_map("desktop")
    assert icons["overview"] == "⌂"
    assert icons["settings"] == "⚙"
    assert navigation_label_for_route("tui", "dashboard") == "Dashboard"


def test_runtime_state_builds_versioned_semantic_snapshot() -> None:
    raw = {
        "run_id": "abc",
        "status": "running",
        "command": "validate",
        "repo_name": "demo",
        "repo_root": "/repo/demo",
        "target": "api",
        "targets": ["api", "web"],
        "execution_environment": "native-termux",
        "log_path": "/tmp/demo.log",
        "phase": {"name": "test", "runner": "python", "target": "api"},
        "phase_progress": [
            {"name": "generate", "status": "ok", "percent": 100, "target": "api", "runner": "node"},
            {"name": "test", "status": "running", "current": 3, "total": 5, "target": "api", "runner": "python"},
        ],
        "diagnostics": [{"severity": "warning", "code": "W1", "message": "Example warning"}],
    }
    snapshot = runtime_state_to_ui_snapshot(raw, surface="desktop")
    payload = snapshot.as_dict()
    assert payload["schema_version"] == 1
    assert payload["surface"] == "desktop"
    assert payload["workspace"]["name"] == "demo"
    assert payload["selection"]["targets"] == ["api", "web"]
    assert payload["operation"]["run_id"] == "abc"
    assert payload["operation"]["phase"] == "test"
    assert payload["progress"][1]["percent"] == 60
    assert payload["progress"][1]["runner"] == "python"
    assert payload["diagnostics"][0]["code"] == "W1"
    assert payload["actions"][0]["id"] == "operation.cancel"
    assert payload["navigation"][0]["route"] == "overview"
    json.dumps(payload)


def test_tasker_state_publishes_machine_ui_snapshot(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("devtool.tasker.send_local_http_event", lambda *args, **kwargs: True)
    monkeypatch.setattr(TaskerProgressSink, "_publish_ipc", lambda self, state: None)
    sink = TaskerProgressSink(
        directory=tmp_path,
        command="validate",
        repo_root=str(tmp_path),
        repo_name="demo",
        target="api",
        enabled=True,
    )
    sink.started(message="Starting")
    try:
        payload = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
        assert payload["ui"]["schema_version"] == 1
        assert payload["ui"]["surface"] == "machine"
        assert payload["ui"]["operation"]["command"] == "validate"
        assert payload["ui"]["actions"][0]["id"] == "operation.cancel"
        assert any(item["id"] == "workflows" for item in payload["ui"]["navigation"])
    finally:
        sink._cleanup_run_file()


def test_hud_snapshot_exposes_semantic_ui_state(tmp_path: Path) -> None:
    (tmp_path / "state.json").write_text(json.dumps({
        "run_id": "hud-1",
        "sequence": 1,
        "updated_at": "2026-09-11T00:00:00Z",
        "status": "failed",
        "command": "build",
        "repo_name": "demo",
        "repo_root": str(tmp_path),
        "target": "android",
        "log_path": "/tmp/build.log",
        "diagnostics": [{"severity": "error", "message": "Compile failed"}],
    }), encoding="utf-8")
    payload = snapshot_from_directory(tmp_path)
    assert payload["version"] == 5
    assert payload["ui"]["surface"] == "hud"
    assert [item["id"] for item in payload["ui"]["actions"]][:2] == ["operation.retry", "logs.open"]
    assert payload["ui"]["diagnostics"][0]["message"] == "Compile failed"


def test_desktop_control_center_exposes_ui_core_navigation(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".devtool.toml").write_text('''\n[project]\nkind = "python"\n\n[targets.api]\nrunner = "python"\nplatform = "server"\nroot = "."\n''', encoding="utf-8")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "state.json").write_text(json.dumps({
        "run_id": "desktop-1",
        "status": "running",
        "command": "test",
        "repo_name": tmp_path.name,
        "repo_root": str(tmp_path),
        "target": "api",
    }), encoding="utf-8")
    monkeypatch.setattr("devtool.desktop.control_center_model.DesktopIpcClient.snapshot", lambda self: {"connected": False, "runs": []})
    payload = DesktopControlCenterModel(repository=tmp_path, target="api", state_dir=state_dir).snapshot().as_dict()
    assert payload["ui"]["surface"] == "desktop"
    assert payload["ui"]["workspace"]["targets"] == ["api"]
    assert [item["route"] for item in payload["ui"]["navigation"]] == list(navigation_route_map("desktop"))
    assert payload["ui"]["operation"]["command"] == "test"


def test_android_navigation_surface_matches_existing_top_level_destinations() -> None:
    routes = [item.route for item in navigation_for_surface("android")]
    assert routes == ["overview", "repos", "run", "logs", "settings"]



def test_tui_shortcuts_are_central_navigation_contract() -> None:
    assert navigation_shortcut_map("tui") == {"d": "dashboard", "b": "build", "o": "overlays", "w": "workers"}


def test_android_hud_bridge_payload_carries_ui_contract(tmp_path: Path) -> None:
    state = parse_hud_state({
        "run_id": "android-1",
        "sequence": 9,
        "updated_at": "2026-09-11T01:00:00Z",
        "status": "running",
        "command": "validate",
        "repo_name": "demo",
        "repo_root": str(tmp_path),
        "target": "android",
        "log_path": "/tmp/android.log",
        "phase": {"name": "test", "runner": "android", "target": "android"},
    })
    _action, payload = AndroidAppHud()._payload(state, tmp_path)
    assert payload["ui_schema_version"] == 1
    actions = json.loads(payload["ui_actions_json"])
    navigation = json.loads(payload["ui_navigation_json"])
    assert actions[0]["id"] == "operation.cancel"
    assert [item["route"] for item in navigation] == ["overview", "repos", "run", "logs", "settings"]


def test_ui_contract_cli_exposes_versioned_surface_contract(capsys) -> None:
    rc = devtool_main(["ui-contract", "--surface", "desktop", "--json"])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    payload = json.loads(captured.out)
    assert payload["exit_code"] == 0
    data = payload["data"]
    assert data["schema_version"] == 1
    assert data["surface"] == "desktop"
    assert data["actions"][0]["id"] == "operation.cancel"
    assert [item["route"] for item in data["navigation"]] == list(navigation_route_map("desktop"))
