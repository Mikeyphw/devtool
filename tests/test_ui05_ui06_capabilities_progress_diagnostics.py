from __future__ import annotations

import json
import tempfile
from pathlib import Path

from devtool.cli import main as devtool_main
from devtool.hud import AndroidAppHud, HudState
from devtool.models import Issue, OperationResult
from devtool.tasker import TaskerProgressSink
from devtool.ui_core import (
    capability_catalog,
    capabilities_for_surface,
    negotiate_capabilities,
    resolve_responsive_layout,
    runtime_state_to_ui_snapshot,
)
from devtool.ui_core.preferences import UIPreferences


def test_capability_matrix_and_fallback_negotiation_are_surface_native() -> None:
    desktop = capabilities_for_surface("desktop")
    android = capabilities_for_surface("android")
    machine = capabilities_for_surface("machine")
    assert desktop.keyboard and desktop.mouse and desktop.multi_pane
    assert not desktop.touch
    assert android.touch and android.notifications and android.native_icons
    assert not android.keyboard
    assert not machine.interactive

    negotiation = negotiate_capabilities("machine", ["tree-view", "dialogs", "clipboard"])
    assert negotiation.supported == ("tree_view",)
    assert set(negotiation.missing) == {"dialogs", "clipboard"}
    assert negotiation.fallbacks["dialogs"] == "inline confirmation"
    assert capability_catalog()["schema_version"] == 1


def test_responsive_policy_has_consistent_surface_breakpoints() -> None:
    prefs = UIPreferences(surface="tui", density="compact")
    assert resolve_responsive_layout("tui", width=50, preferences=prefs).mode == "single-pane"
    assert resolve_responsive_layout("tui", width=80, preferences=prefs).mode == "two-pane"
    assert resolve_responsive_layout("tui", width=130, preferences=prefs).mode == "three-pane"
    assert resolve_responsive_layout("desktop", width=800).panes == 1
    assert resolve_responsive_layout("desktop", width=1100).panes == 2
    assert resolve_responsive_layout("desktop", width=1600).panes == 3
    assert resolve_responsive_layout("android", width=420).mode == "single-pane"
    assert resolve_responsive_layout("android", width=720).mode == "two-pane"
    assert resolve_responsive_layout("android", width=900).mode == "three-pane"




def test_runtime_snapshot_builds_canonical_progress_hierarchy_and_summary() -> None:
    raw = {
        "run_id": "r1",
        "command": "validate",
        "status": "running",
        "phase_progress": [
            {
                "name": "generate",
                "node_id": "protocol.generate",
                "target": "protocol",
                "workflow": "release",
                "status": "ok",
                "percent": 100,
                "runner": "node",
                "execution_environment": "native-termux",
                "started_at": "2026-09-11T00:00:00Z",
                "finished_at": "2026-09-11T00:00:01Z",
                "duration_ms": 1000,
            },
            {
                "name": "pytest",
                "node_id": "api.pytest",
                "target": "api",
                "workflow": "release",
                "status": "running",
                "completed": 4,
                "total": 10,
                "runner": "python",
                "tests_passed": 4,
                "tests_total": 10,
            },
            {
                "name": "package",
                "node_id": "api.package",
                "parent_id": "api.pytest",
                "target": "api",
                "workflow": "release",
                "status": "pending",
                "depends_on": ["api.pytest"],
            },
        ],
    }
    snapshot = runtime_state_to_ui_snapshot(raw, surface="desktop", viewport={"width": 1500, "height": 900})
    payload = snapshot.as_dict()
    assert payload["progress_summary"]["total_nodes"] == 3
    assert payload["progress_summary"]["running_nodes"] == 1
    assert payload["responsive"]["mode"] == "three-pane"
    root = payload["progress_tree"][0]
    assert root["kind"] == "operation"
    release = next(item for item in root["children"] if item["kind"] == "workflow" and item["label"] == "release")
    targets = {item["label"]: item for item in release["children"] if item["kind"] == "target"}
    assert {"protocol", "api"}.issubset(targets)
    pytest_node = next(item for item in targets["api"]["children"] if item["id"] == "api.pytest")
    assert pytest_node["runner"] == "python"
    assert pytest_node["children"][0]["id"] == "api.package"
    assert payload["capabilities"]["multi_pane"] is True


def test_diagnostics_present_what_why_where_next_actions_and_details() -> None:
    raw = {
        "status": "failed",
        "command": "validate",
        "completion": {
            "failure_phase": "compile",
            "diagnostics": [
                {
                    "severity": "error",
                    "code": "KOTLIN",
                    "message": "Unresolved reference: MediaManifestRole",
                    "cause": "Generated protocol sources are stale",
                    "file": "MainViewModel.kt",
                    "line": 2558,
                    "related_node": "android.compile",
                    "technical_details": "compiler exited 1",
                }
            ],
        },
    }
    payload = runtime_state_to_ui_snapshot(raw, surface="tui").as_dict()
    assert payload["diagnostic_summary"] == {
        "total": 1,
        "errors": 1,
        "warnings": 0,
        "info": 0,
        "blocking": True,
        "primary_id": payload["diagnostics"][0]["id"],
        "schema_version": 1,
    }
    diagnostic = payload["diagnostic_presentations"][0]
    assert diagnostic["what_happened"] == "Unresolved reference: MediaManifestRole"
    assert diagnostic["why"] == "Generated protocol sources are stale"
    assert diagnostic["where"] == "MainViewModel.kt:2558"
    assert diagnostic["technical_details"] == "compiler exited 1"
    assert "logs.open" in diagnostic["next_actions"]
    assert "workflow.retry_node" in diagnostic["next_actions"]


def test_tasker_carries_workflow_node_runner_and_environment_into_ui_progress() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        sink = TaskerProgressSink(
            directory=Path(tmp), command="validate", repo_root="/repo", repo_name="demo", enabled=True
        )
        sink.started()
        sink.phase_started(
            "pytest",
            index=1,
            total=1,
            argv=["pytest"],
            metadata={
                "workflow_name": "release",
                "workflow_node_key": "api.pytest",
                "workflow_runner": "python",
                "workflow_target": "api",
                "execution_environment": "native-termux",
                "workflow_depends_on": ["protocol.generate"],
            },
        )
        sink.progress(completed=2, total=5, metrics={"tests_passed": 2, "tests_total": 5}, force=True)
        state = json.loads((Path(tmp) / "state.json").read_text())
        item = state["phase_progress"][0]
        assert item["workflow"] == "release"
        assert item["node_id"] == "api.pytest"
        assert item["runner"] == "python"
        assert item["execution_environment"] == "native-termux"
        semantic = state["ui"]
        assert semantic["progress"][0]["workflow"] == "release"
        assert semantic["progress"][0]["dependencies"] == ["protocol.generate"]


def test_completion_diagnostics_feed_structured_ui_error_model() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        sink = TaskerProgressSink(
            directory=Path(tmp), command="validate", repo_root="/repo", repo_name="demo", enabled=True
        )
        sink.started()
        issue = Issue("error", "compile", "src/App.kt", "CompileError", "Unresolved reference", line=42)
        result = OperationResult("compile", ["gradle", "compile"], 1, 1.0, "", [issue])
        sink.finished(ok=False, totals={"warnings": 0, "errors": 1, "failed": 1, "skipped": 0}, results=[result])
        state = json.loads((Path(tmp) / "state.json").read_text())
        presentation = state["ui"]["diagnostic_presentations"][0]
        assert presentation["where"] == "src/App.kt:42"
        assert presentation["what_happened"] == "Unresolved reference"
        assert state["ui"]["diagnostic_summary"]["blocking"] is True


def test_android_payload_publishes_ui05_ui06_contracts() -> None:
    state = HudState(
        status="running",
        command="validate",
        repo_name="demo",
        target="api",
        raw={
            "run_id": "android-ui06",
            "status": "running",
            "command": "validate",
            "target": "api",
            "phase_progress": [{"name": "pytest", "target": "api", "status": "running", "percent": 50}],
            "completion": {"diagnostics": [{"severity": "warning", "message": "Slow test"}]},
        },
    )
    _, payload = AndroidAppHud()._payload(state, Path("/tmp/android-ui06"))
    assert payload["ui_capability_schema_version"] == 1
    assert payload["ui_responsive_schema_version"] == 1
    assert payload["ui_progress_schema_version"] == 1
    assert payload["ui_diagnostic_schema_version"] == 1
    assert json.loads(payload["ui_capabilities_json"])["touch"] is True
    assert json.loads(payload["ui_responsive_json"])["mode"] == "single-pane"
    assert json.loads(payload["ui_progress_summary_json"])["total_nodes"] == 1
    assert json.loads(payload["ui_diagnostic_summary_json"])["warnings"] == 1


def test_ui_contract_cli_exposes_capabilities_responsive_and_schema_models(capsys) -> None:
    rc = devtool_main([
        "ui-contract",
        "--surface", "tui", "--width", "55",
        "--require-capability", "dialogs",
        "--require-capability", "notifications",
        "--json",
    ])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    envelope = json.loads(captured.out)
    data = envelope.get("data", envelope)
    assert data["capability_schema_version"] == 1
    assert data["responsive_schema_version"] == 1
    assert data["progress_schema_version"] == 1
    assert data["diagnostic_schema_version"] == 1
    assert data["responsive"]["mode"] == "single-pane"
    assert data["capability_negotiation"]["supported"] == ["dialogs"]
    assert data["capability_negotiation"]["missing"] == ["notifications"]
    assert data["diagnostic_model"]["sections"] == [
        "what_happened", "why", "where", "next_actions", "technical_details"
    ]
