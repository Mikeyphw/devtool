from __future__ import annotations

import json
from pathlib import Path

from devtool.desktop.ui_semantics import build_desktop_semantic_view, primary_desktop_action
from devtool.hud import AndroidAppHud, parse_hud_state
from devtool.ui_core.viewmodels import runtime_state_to_ui_snapshot

ROOT = Path(__file__).resolve().parents[1]
JAVA = ROOT / "android" / "devtool-live-hud" / "app" / "src" / "main" / "java" / "io" / "github" / "mikeceead" / "devtoolhud"


def _runtime_state(status: str = "running") -> dict:
    return {
        "run_id": "ui09-ui10",
        "status": status,
        "command": "validate",
        "repo_root": "/repo",
        "repo_name": "repo",
        "target": "api",
        "targets": ["api"],
        "text": "Running selected validation",
        "phase": {"name": "test", "display_name": "Tests", "target": "api"},
        "phase_progress": [
            {
                "name": "test",
                "status": "running",
                "target": "api",
                "percent": 50,
                "current": 1,
                "total": 2,
                "unit_label": "tests",
            }
        ],
        "diagnostics": [
            {
                "severity": "warning",
                "message": "A warning",
                "cause": "Example cause",
                "file": "tests/test_demo.py",
                "line": 9,
            }
        ],
    }


def test_desktop_semantic_adapter_is_renderer_ready() -> None:
    ui = runtime_state_to_ui_snapshot(_runtime_state(), surface="desktop").as_dict()
    view = build_desktop_semantic_view(ui).as_dict()
    assert view["schema_version"] == 1
    assert view["operation"]["status"] == "running"
    assert view["progress_summary"]["running_nodes"] >= 1
    assert view["diagnostics"][0]["what_happened"] == "A warning"
    assert any(item["route"] == "overview" for item in view["navigation"])
    assert primary_desktop_action(view)["id"] in {"operation.cancel", "logs.open", "workflow.inspect"}


def test_android_hud_payload_carries_semantic_ui_core_contract(tmp_path: Path) -> None:
    raw = _runtime_state()
    raw["ui_presentations"] = {
        "android": {"profile": "developer", "density": "compact", "icons": "auto"}
    }
    state = parse_hud_state(raw)
    _action, payload = AndroidAppHud()._payload(state, tmp_path)
    assert json.loads(payload["ui_operation_json"])["status"] == "running"
    assert json.loads(payload["ui_presentation_json"])["profile"] == "developer"
    assert json.loads(payload["ui_capabilities_json"])["touch"] is True
    assert json.loads(payload["ui_progress_summary_json"])["running_nodes"] >= 1
    assert json.loads(payload["ui_progress_tree_json"])
    assert json.loads(payload["ui_diagnostics_json"])[0]["what_happened"] == "A warning"
    assert any(item["id"] == "operation.cancel" for item in json.loads(payload["ui_actions_json"]))
    assert any(item["route"] == "overview" for item in json.loads(payload["ui_navigation_json"]))
    assert isinstance(json.loads(payload["ui_notification_policy_json"]), dict)
    assert isinstance(json.loads(payload["ui_confirmation_policy_json"]), dict)


def test_desktop_control_center_and_hud_use_semantic_adapter() -> None:
    control = (ROOT / "devtool" / "desktop" / "control_center_model.py").read_text(encoding="utf-8")
    gtk = (ROOT / "devtool" / "desktop" / "gtk_control_center.py").read_text(encoding="utf-8")
    hud = (ROOT / "devtool" / "desktop" / "gtk_hud.py").read_text(encoding="utf-8")
    assert "build_desktop_semantic_view(semantic_ui).as_dict()" in control
    assert 'payload.get("desktop_ui"' in gtk
    assert "primary_desktop_action(desktop_ui)" in gtk
    assert "build_desktop_semantic_view(semantic_ui).as_dict()" in hud
    assert 'desktop_action(desktop_view, "operation.cancel")' in hud
    assert 'desktop_view.get("progress_tree")' in hud


def test_android_companion_parses_ui_core_semantics_natively() -> None:
    semantic = (JAVA / "UiSemanticState.java").read_text(encoding="utf-8")
    hud_update = (JAVA / "HudUpdate.java").read_text(encoding="utf-8")
    notification = (JAVA / "NotificationController.java").read_text(encoding="utf-8")
    overlay = (JAVA / "OverlayHudService.java").read_text(encoding="utf-8")
    run_store = (JAVA / "RunStore.java").read_text(encoding="utf-8")
    assert 'intent.getStringExtra("ui_operation_json")' in semantic
    assert 'intent.getStringExtra("ui_progress_tree_json")' in semantic
    assert 'intent.getStringExtra("ui_actions_json")' in semantic
    assert 'intent.getStringExtra("ui_navigation_json")' in semantic
    assert 'intent.getStringExtra("ui_notifications_json")' in semantic
    assert "progressSteps()" in semantic
    assert "UiSemanticState ui" in hud_update
    assert "this.ui.progressSteps()" in hud_update
    assert 'ui.notificationTitle(ui.operationTitle(""))' in hud_update
    assert "semanticActionsFor(update)" in notification
    assert 'actionId.equals("operation.cancel")' in notification
    assert 'builder.getExtras().putString("devtool.attention"' in notification
    assert "update.ui.diagnosticItems(2)" in overlay
    assert "String effectiveStatus = update.effectiveStatus();" in run_store
    assert "String effectivePhase = update.ui.operationPhase(update.phase);" in run_store
    assert "String effectiveDetail = update.ui.operationDetail(update.detail);" in run_store
    assert "record.stepsJson = update.stepsJsonForStore();" in run_store
    assert "String stepsJsonForStore()" in hud_update


def test_android_semantic_contract_remains_additive_to_bridge_protocol() -> None:
    protocol = (JAVA / "BridgeProtocol.java").read_text(encoding="utf-8")
    receiver = (JAVA / "BridgeReceiver.java").read_text(encoding="utf-8")
    assert "static final int VERSION = 1" in protocol
    assert "protocolVersion != BridgeProtocol.VERSION" in receiver
    assert "HudUpdate.fromIntent(intent)" in receiver
    # UI Core travels as additive extras; older clients/senders keep protocol v1 compatibility.
    assert "UiSemanticState.fromIntent(intent)" in (JAVA / "HudUpdate.java").read_text(encoding="utf-8")


def test_android_semantic_payload_avoids_duplicate_full_snapshot() -> None:
    hud_source = (ROOT / "devtool" / "hud.py").read_text(encoding="utf-8")
    assert '"ui_operation_json"' in hud_source
    assert '"ui_notifications_json"' in hud_source
    assert '"ui_snapshot_json"' not in hud_source


def test_ui09_ui10_docs_and_report_are_present() -> None:
    assert (ROOT / "docs" / "architecture" / "ui-desktop-android-migration.md").is_file()
    assert (ROOT / "UI09_UI10_DESKTOP_ANDROID_UI_CORE_MIGRATION_REPORT.md").is_file()
    assert (ROOT / "android" / "devtool-live-hud" / "docs" / "UI_CORE_PROTOCOL.md").is_file()
