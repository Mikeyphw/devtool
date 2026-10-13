from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from rich.console import Console

from devtool.cli import _maybe_copy
from devtool.hud import AndroidAppHud, HudState, TerminalHud
from devtool.models import OperationResult
from devtool.summary import build_json_summary
from devtool.tasker import _completion_test_totals
from devtool.ui_core.viewmodels import runtime_state_to_ui_snapshot


def _go_metadata(*, passed: int = 33, failed: int = 0, skipped: int = 0) -> dict:
    return {
        "go_test": {
            "tests": {
                "passed": passed,
                "failed": failed,
                "skipped": skipped,
                "total": passed + failed + skipped,
            },
            "packages": {"passed": 10, "failed": 0, "skipped": 0, "no_tests": 4, "total": 14},
        }
    }


def test_final_json_summary_promotes_legacy_go_test_counts() -> None:
    result = OperationResult(
        "test", ["go", "test", "-json", "./..."], 0, 1.0, "", [], metadata=_go_metadata()
    )
    payload = build_json_summary([result], repo_root="/repo")
    assert payload["tests"] == {"passed": 33, "failed": 0, "skipped": 0, "total": 33}
    assert payload["go_test"]["packages"]["passed"] == 10


def test_tasker_completion_uses_go_bridge_without_double_counting_canonical_tests() -> None:
    legacy = OperationResult(
        "test", ["go", "test", "-json", "./..."], 0, 1.0, "", [], metadata=_go_metadata()
    )
    assert _completion_test_totals([legacy]) == {"passed": 33, "failed": 0, "skipped": 0, "total": 33}

    canonical = OperationResult(
        "test", ["go", "test", "-json", "./..."], 0, 1.0, "", [],
        metadata={**_go_metadata(), "tests": {"passed": 33, "failed": 0, "skipped": 0, "total": 33}},
    )
    assert _completion_test_totals([canonical]) == {"passed": 33, "failed": 0, "skipped": 0, "total": 33}


def test_ui_progress_summary_aggregates_live_test_metrics_and_completion_wins() -> None:
    live = runtime_state_to_ui_snapshot({
        "status": "running",
        "command": "validate",
        "phase_progress": [
            {"node_id": "go-a", "name": "go-a", "status": "ok", "tests_passed": 20, "tests_total": 20},
            {"node_id": "go-b", "name": "go-b", "status": "running", "tests_passed": 13, "tests_total": 13},
        ],
    }, surface="machine").as_dict()
    summary = live["progress_summary"]
    assert summary["test_metrics_available"] is True
    assert (summary["tests_passed"], summary["tests_failed"], summary["tests_skipped"], summary["tests_total"]) == (33, 0, 0, 33)

    finished = runtime_state_to_ui_snapshot({
        "status": "ok",
        "command": "validate",
        # Deliberately inflated live nodes: completion is final authority.
        "phase_progress": [
            {"node_id": "aggregate", "name": "aggregate", "status": "ok", "tests_passed": 66, "tests_total": 66},
        ],
        "completion": {"tests": {"passed": 33, "failed": 0, "skipped": 0, "total": 33}},
    }, surface="machine").as_dict()
    final_summary = finished["progress_summary"]
    assert (final_summary["tests_passed"], final_summary["tests_total"]) == (33, 33)




def test_android_hud_payload_reads_tests_from_semantic_ui_summary() -> None:
    state = HudState(
        title="Test", text="running", status="running", phase_name="test", command="validate",
        repo_name="demo", target="demo",
        raw={
            "run_id": "go-run",
            "sequence": 1,
            "phase": {"name": "test", "target": "demo"},
            "phase_progress": [
                {"node_id": "go", "name": "go test", "status": "running", "percent": 80,
                 "tests_passed": 33, "tests_failed": 0, "tests_skipped": 0, "tests_total": 33,
                 "test_assemblies": 2},
            ],
        },
    )
    _action, payload = AndroidAppHud()._payload(state, Path("/tmp/go-run"))
    assert (payload["passed"], payload["failed"], payload["skipped"], payload["test_total"]) == (33, 0, 0, 33)
    assert payload["test_assemblies"] == 2
    semantic = json.loads(payload["ui_progress_summary_json"])
    assert semantic["tests_passed"] == 33


def test_cli_copy_summary_prefers_structured_go_test_counts_over_console_scraping() -> None:
    payload = {
        "warnings": 0,
        "errors": 0,
        "failed": 0,
        "skipped": 0,
        "results": [
            {"name": "test", "metadata": _go_metadata(), "issues": []},
        ],
    }
    copied: list[str] = []
    clipboard_result = SimpleNamespace(success=True, backend="test", reason="")
    args = SimpleNamespace(no_clipboard=False, copy=True, copy_via="auto")
    with patch("devtool.cli.copy_text", side_effect=lambda text, **_kwargs: copied.append(text) or clipboard_result), patch("devtool.cli.console.print"):
        _maybe_copy("go test completed without a textual count summary", "test: ok exit=0 time=1.0s", json.dumps(payload), args)
    assert len(copied) == 1
    assert "Tests: 33 passed, 0 failed, 0 skipped (33 total)" in copied[0]
    assert "counts unavailable" not in copied[0]
    assert "Go packages: 10 passed, 0 failed, 0 skipped, 4 no tests (14 total)" in copied[0]
