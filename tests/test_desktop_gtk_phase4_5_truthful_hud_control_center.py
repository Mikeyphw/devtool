from __future__ import annotations

import json
from pathlib import Path

from devtool.desktop.control_center_model import _diagnostics_from_runs_and_events
from devtool.desktop.repository_store import DesktopRepositoryStore
from devtool.hud import HudState, canonical_diagnostics, live_issue_counts, operation_steps, primary_failure_message, state_metric_text

ROOT = Path(__file__).resolve().parents[1]
GTK = ROOT / "devtool" / "desktop" / "gtk_control_center.py"
HUD = ROOT / "devtool" / "desktop" / "gtk_hud.py"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_phase4_canonical_counts_prevent_conflicting_hud_totals() -> None:
    state = HudState(
        status="failed",
        raw={
            "totals": {"errors": 3, "warnings": 6},
            "diagnostics": [
                {"severity": "error", "message": "Generated build output in inventory", "phase": "validate"},
                {"severity": "warning", "message": "Deprecated metadata", "phase": "manifest"},
            ],
        },
    )
    events = [
        {"event_type": "progress", "message": "1 error 0 warnings"},
        {"event_type": "progress", "severity": "warning", "message": "Deprecated metadata"},
    ]

    assert live_issue_counts(events, state) == (3, 6)
    assert "3 errors · 6 warnings" in state_metric_text(state, events)
    assert primary_failure_message(events, state) == "Generated build output in inventory"
    assert canonical_diagnostics(events, state)[0]["source"] == "state"


def test_phase4_skipped_pipeline_is_not_rendered_as_complete_progress() -> None:
    state = HudState(status="failed", command="apply-overlay", phase_name="artifact-validate-script")
    events = [
        {"event_type": "phase-finished", "phase": {"name": "artifact-validate-script"}, "result": {"status": "failed"}},
        {"event_type": "phase-finished", "phase": {"name": "apply-rollback"}, "result": {"status": "ok"}},
    ]
    steps = {step.key: step for step in operation_steps(state, events)}

    assert steps["validate"].status == "failed"
    assert steps["validate"].progress == 100
    assert steps["build"].status == "skipped"
    assert steps["build"].progress == 0
    assert steps["rollback"].status == "ok"
    assert steps["rollback"].detail == "Repository restored"


def test_control_center_model_uses_same_canonical_diagnostics_as_hud() -> None:
    run = {
        "status": "failed",
        "totals": {"errors": 2, "warnings": 1},
        "diagnostics": [
            {"severity": "error", "message": "Primary failure", "code": "E1"},
            {"severity": "error", "message": "Primary failure", "code": "E1"},
            {"severity": "warning", "message": "Secondary warning", "code": "W1"},
        ],
    }

    diagnostics = _diagnostics_from_runs_and_events([run], [])

    assert [(item["severity"], item["message"]) for item in diagnostics] == [
        ("error", "Primary failure"),
        ("warning", "Secondary warning"),
    ]


def test_phase5_control_center_controls_are_filterable_and_not_decorative() -> None:
    source = read(GTK)

    for token in (
        "search.connect(\"search-changed\", render_filtered)",
        "severity.connect(\"changed\", render_filtered)",
        "status_filter.connect(\"changed\", render_filtered)",
        "Run again",
        "_run_again_callback",
        "Review command before running",
        "_open_terminal_callback",
        "--working-directory",
        "_open_file_callback(log_path)",
        "_remember_repository_callback",
        "_refresh_git_state_callback",
        "_build_workflow_callback",
    ):
        assert token in source
    assert "(\"Build workflow\", None)" not in source
    assert "(\"Show active run\", None)" not in source
    assert "(\"Refresh git state\", None)" not in source
    assert "Enable clipboard monitor" not in source


def test_phase5_repository_remember_writes_recent_repository_list(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    saved = DesktopRepositoryStore(repo).remember(state_dir=state)
    data = json.loads(saved.read_text(encoding="utf-8"))

    assert saved == state / "repositories.json"
    assert data[0]["path"] == str(repo.resolve())
    assert "remembered_at" in data[0]


def test_hud_source_labels_parser_observations_separately_from_canonical_counts() -> None:
    source = read(HUD)
    assert "Counts" in source
    assert "canonical" in source
    assert "Metric" not in source
