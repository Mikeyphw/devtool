from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from devtool.cli import main
from devtool.desktop.command_model import DevtoolCommandDraft
from devtool.desktop.control_center_cli import build_parser
from devtool.desktop.run_workflow import payload_from_mapping, review_payload, workflow_snapshot

ROOT = Path(__file__).resolve().parents[1]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".devtool.toml").write_text(
        """
[project]
kind = "python"

[targets.desktop]
kind = "python"
root = "."
build_command = "python -m compileall -q devtool"
test_command = "pytest tests"
execution_environment = "desktop-linux"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return repo


def test_command_model_emits_validate_and_test_selection_flags() -> None:
    draft = DevtoolCommandDraft(
        repository="/repo/demo",
        operation="validate",
        target="desktop",
        test_profile="fast",
        selectors=["tests/test_demo.py::test_ok"],
        test_ids=["pytest::test_ok"],
        tags=["ui"],
        exclude_tags=["slow"],
        test_path="tests",
        test_class="DemoTests",
        test_method="test_ok",
        test_filter="name~ok",
        test_tasks=[":app:testOfficialDebugUnitTest"],
        failed=True,
        changed=True,
        changed_from="origin/main",
        instrumented=True,
        yes=True,
    )
    argv = draft.argv()
    assert "validate" in argv
    assert argv[argv.index("--test-profile") + 1] == "fast"
    assert argv[argv.index("--select") + 1] == "tests/test_demo.py::test_ok"
    assert argv[argv.index("--id") + 1] == "pytest::test_ok"
    assert argv[argv.index("--tag") + 1] == "ui"
    assert argv[argv.index("--exclude-tag") + 1] == "slow"
    assert argv[argv.index("--filter") + 1] == "name~ok"
    assert argv[argv.index("--task") + 1] == ":app:testOfficialDebugUnitTest"
    assert "--failed" in argv
    assert "--changed" in argv
    assert argv[argv.index("--changed-from") + 1] == "origin/main"
    assert "--instrumented" in argv


def test_run_workflow_review_generates_apply_overlay_command() -> None:
    payload = payload_from_mapping(
        {
            "repository": "/repo/demo",
            "operation": "apply-overlay",
            "target": "desktop",
            "artifact": "overlay.zip",
            "validate": True,
            "commit": True,
            "commit_message": "Apply GTK run workflow",
            "keep_artifact": True,
            "no_manifest_check": True,
            "copy_mode": "json",
            "hud_mode": "desktop-window",
            "auto_hud": True,
            "yes": True,
            "environment": "DEVTOOL_TRACE=1",
            "extra_global_args": "--performance",
            "extra_operation_args": "--dry-run",
        }
    )
    review = review_payload(payload, devtool="/usr/bin/devtool")
    assert review.runnable
    assert review.argv[:2] == ["/usr/bin/devtool", "--copy-json"]
    assert "--hud-mode" in review.argv
    assert "--performance" in review.argv
    assert "apply-overlay" in review.argv
    assert "overlay.zip" in review.argv
    assert "--validate" in review.argv
    assert review.argv[review.argv.index("--commit") + 1] == "Apply GTK run workflow"
    assert review.argv[-1] == "--dry-run"
    assert review.environment == {"DEVTOOL_TRACE": "1"}
    assert "export DEVTOOL_TRACE=1" in review.command


def test_run_workflow_review_blocks_missing_artifact() -> None:
    payload = payload_from_mapping({"repository": "/repo/demo", "operation": "apply-overlays"})
    review = review_payload(payload)
    assert not review.runnable
    assert any("requires" in item for item in review.errors)


def test_workflow_snapshot_exposes_operation_catalog_and_default_review(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    snapshot = {
        "repository": {"path": str(repo)},
        "project": {"kind": "python", "selected_target": "desktop", "variant": "", "execution_environment": "desktop-linux"},
        "targets": [{"name": "desktop"}],
    }
    workflow = workflow_snapshot(snapshot).as_dict()
    assert workflow["selected_target"] == "desktop"
    assert any(item["id"] == "apply-overlay" for item in workflow["operations"])
    assert workflow["default_draft"]["operation"] == "validate"
    assert workflow["review"]["runnable"] is True
    assert "validate" in workflow["review"]["argv"]


def test_control_center_once_includes_run_workflow_payload(tmp_path: Path, capsys) -> None:
    repo = make_repo(tmp_path)
    assert main(["-r", str(repo), "control-center", "--once", "--page", "run", "--target", "desktop"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["run_workflow"]["selected_target"] == "desktop"
    assert payload["run_workflow"]["default_draft"]["repository"] == str(repo.resolve())
    assert "Run" in {item["label"] for item in payload["run_workflow"]["operations"]}


def test_control_center_parser_and_completion_include_run_page() -> None:
    args = build_parser().parse_args(["--page", "run", "--once"])
    assert args.page == "run"
    completion = read(ROOT / "devtool" / "completion.py")
    cli = read(ROOT / "devtool" / "cli.py")
    assert "overview runs history diagnostics repositories targets run settings" in completion
    assert '"run", "settings"' in cli


def test_gtk_control_center_source_contains_mutable_run_workflow() -> None:
    source = read(ROOT / "devtool" / "desktop" / "gtk_control_center.py")
    assert '"run": "Run"' in source
    assert "def _render_run_workflow" in source
    assert "review_payload(payload_from_mapping" in source
    assert "Copy command" in source
    assert "Preview dry-run" in source
    assert "subprocess.Popen" in source


def test_phase4_docs_describe_typed_run_workflow() -> None:
    docs = read(ROOT / "docs" / "DESKTOP_GTK_MIGRATION.md")
    readme = read(ROOT / "desktop" / "gtk-control-center" / "README.md")
    assert "Phase 4: GTK4 Run workflow" in docs
    assert "argv list first" in docs
    assert "devtool control-center --backend gtk4 --page run" in docs
    assert "Run page builds a typed" in readme


def test_backend_auto_still_uses_gtk4_for_phase4_page(tmp_path: Path) -> None:
    from devtool.desktop.control_center_cli import main as control_center_main

    repo = make_repo(tmp_path)
    with patch("devtool.desktop.gtk_control_center.Gtk4ControlCenter.available", return_value=True), \
         patch("devtool.desktop.gtk_control_center.Gtk4ControlCenter.run", return_value=0) as run:
        assert control_center_main(["-r", str(repo), "--page", "run"]) == 0
    assert run.call_args.kwargs["page"] == "run"
