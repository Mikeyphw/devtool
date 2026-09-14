from __future__ import annotations

import argparse
import io
import json
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console

from devtool.api_output import invocation_envelope
from devtool.artifact_pipeline import _gradle_event_candidates
from devtool.cli import _append_run_log_summary
from devtool.diagnostics import classify_failure, parse_issues
from devtool.gradle_progress import GradleProgressTracker
from devtool.log_commands import ActiveRun, _merge_active_runs
from devtool.logstore import redact_sensitive_value
from devtool.models import Issue, OperationResult
from devtool.summary import build_json_summary, build_runner_json, calculate_totals, _extract_test_totals
from devtool.tasker import _completion_test_totals, _result_to_json


SECRET = "sk-super-secret-token-value-1234567890"


def test_recursive_redaction_covers_json_keys_and_text_values() -> None:
    payload = {
        "token": SECRET,
        "nested": {"password": "p455w0rd"},
        "output": f"Authorization: Bearer {SECRET}",
    }
    rendered = json.dumps(redact_sensitive_value(payload))
    assert SECRET not in rendered
    assert "p455w0rd" not in rendered
    assert "<redacted" in rendered


def test_universal_json_envelope_redacts_stdout_stderr_result_and_events() -> None:
    envelope = invocation_envelope(
        command="devtool test",
        repo="/repo",
        exit_code=1,
        stdout=json.dumps({"token": SECRET, "message": f"Authorization: Bearer {SECRET}"}),
        stderr=f"error Authorization: Bearer {SECRET}",
        started_at=datetime.now(timezone.utc),
        duration_seconds=0.1,
        events=[{"kind": "log", "api_key": SECRET}],
    )
    rendered = json.dumps(envelope)
    assert SECRET not in rendered
    assert envelope["data"]["token"] == "<redacted>"
    assert envelope["stderr"] != f"error Authorization: Bearer {SECRET}"


def test_json_summary_redacts_safe_runner_clipboard_json_and_raw_surfaces() -> None:
    result = OperationResult(
        "safe-runner",
        ["cmd", "--token", SECRET],
        1,
        0.1,
        f"Authorization: Bearer {SECRET}",
        [Issue("ERROR", "safe-runner", "-", "GENERIC", f"failed with {SECRET}")],
        metadata={"credentials": SECRET},
    )
    payload = build_runner_json(
        [result],
        repo_root="/repo",
        command="devtool --json safe-runner",
        raw_output=f"raw {SECRET}",
        summary_text=f"summary {SECRET}",
    )
    rendered = json.dumps(payload)
    assert SECRET not in rendered
    assert payload["clipboard"]["json"]["results"][0]["status"] == "failed"


def test_totals_are_case_insensitive_and_failed_without_diagnostics_counts_error() -> None:
    results = [
        OperationResult("warn", [], 0, 0, "", [Issue("WARNING", "cmd", "-", "W", "warn")]),
        OperationResult("err", [], 0, 0, "", [Issue("Error", "cmd", "-", "E", "err")]),
        OperationResult("silent-failure", [], 2, 0, "", []),
        OperationResult("skipped-nonzero", [], 1, 0, "", [], skipped=True),
    ]
    totals = calculate_totals(results)
    assert totals == {"warnings": 1, "errors": 2, "failed": 2, "skipped": 1}


def test_result_status_fails_when_returncode_zero_has_error_diagnostic() -> None:
    result = OperationResult("lint", [], 0, 0, "", [Issue("error", "lint", "a.py", "F401", "bad")])
    payload = build_json_summary([result], repo_root="/repo")
    assert payload["failed"] == 1
    assert payload["results"][0]["status"] == "failed"


def test_final_log_status_uses_error_totals_even_when_exit_zero(tmp_path: Path) -> None:
    log = tmp_path / "run.log"
    args = argparse.Namespace(command="validate", artifact="", _devtool_run_id="run-1")
    _append_run_log_summary(
        log,
        args,
        None,
        {"warnings": 0, "errors": 1, "failed": 0, "skipped": 0},
        "Diagnostics: 0 warnings, 1 errors",
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        exit_code=0,
    )
    text = log.read_text()
    assert "Devtool status: failed" in text
    assert "Devtool exit code: 0" in text


def test_pytest_summary_supports_all_failed_and_all_skipped() -> None:
    failed = OperationResult("test", [], 1, 0, "2 failed in 0.10s", [])
    skipped = OperationResult("test", [], 0, 0, "3 skipped in 0.10s", [], skipped=True)
    assert _extract_test_totals([failed]) == (0, 2, 0, 2)
    assert _extract_test_totals([skipped]) == (0, 0, 3, 3)
    assert _completion_test_totals([failed]) == {"passed": 0, "failed": 2, "skipped": 0, "total": 2}


def test_malformed_structured_test_metadata_does_not_crash() -> None:
    result = OperationResult("test", [], 0, 0, "", [], metadata={"tests": {"passed": ["bad"], "failed": "x", "skipped": None, "total": {}}})
    assert _extract_test_totals([result]) == (0, 0, 0, 0)


def test_dotnet_diagnostics_accept_relative_and_windows_paths() -> None:
    issues = parse_issues(Path("/repo"), "build", "src/App.cs(12,3): error CS1002: ; expected [App.csproj]")
    assert issues and issues[0].severity == "error" and issues[0].file == "src/App.cs"
    win = parse_issues(Path("/repo"), "build", r"C:\repo\App.cs(5,2): warning CS0168: unused [App.csproj]")
    assert win and win[0].severity == "warning" and win[0].code == "CS0168"


def test_msbuild_zero_warning_error_summaries_are_not_diagnostics() -> None:
    output = "Build succeeded.\n    0 Warning(s)\n    0 Error(s)\n"
    assert parse_issues(Path("/repo"), "build", output) == []


def test_generic_success_text_is_not_promoted_to_warning() -> None:
    output = "warning: build completed successfully with 0 warnings"
    assert parse_issues(Path("/repo"), "build", output) == []


def test_ruff_error_codes_are_errors_not_warnings() -> None:
    output = "F401 imported but unused\n--> src/app.py:3:1\nB006 do not use mutable defaults\n--> src/app.py:5:1\n"
    issues = parse_issues(Path("/repo"), "lint", output)
    by_code = {issue.code: issue.severity for issue in issues}
    assert by_code["F401"] == "error"
    assert by_code["B006"] == "warning"


def test_gradle_structured_event_outcome_and_booleans_are_strict(tmp_path: Path) -> None:
    console = Console(file=io.StringIO(), force_terminal=False)
    tracker = GradleProgressTracker([], str(tmp_path), "build", console)
    tracker.update_from_line('DEVTOOL_EVENT {"kind":"task-finish","task":":app:build","outcome":"FAILED","didWork":"false","completed":1,"total":1}')
    assert tracker.errors == 1
    assert tracker.task_outcomes[":app:build"] == "failed"
    assert tracker.parse_event('DEVTOOL_EVENT {"kind":"task-finish","task":":app:x","outcome":"success","didWork":"maybe"}') is None


def test_gradle_artifact_event_does_not_treat_string_false_as_did_work(tmp_path: Path) -> None:
    artifact = tmp_path / "app.apk"
    artifact.write_text("apk")
    event = {"kind": "task-finish", "task": ":app:assemble", "outcome": "SUCCESS", "didWork": "false", "artifactOutputs": [str(artifact)]}
    candidates = _gradle_event_candidates("DEVTOOL_EVENT " + json.dumps(event), tmp_path)
    assert candidates and candidates[0].state == "discovered"


def test_workflow_json_failed_result_cannot_be_marked_passed_by_metadata() -> None:
    result = OperationResult(
        "build",
        [],
        1,
        0,
        "boom",
        [],
        metadata={"workflow_node_key": "target.build", "workflow_state": "passed", "workflow_target": "target", "workflow_name": "ci"},
    )
    nodes = build_json_summary([result], repo_root="/repo")["workflow"]["nodes"]
    assert nodes[0]["state"] == "failed"


def test_artifact_summary_treats_string_false_as_invalid_and_unverified() -> None:
    result = OperationResult(
        "package",
        [],
        0,
        0,
        "",
        [],
        metadata={"artifacts": {"verification_requested": "false", "items": [{"path": "a.apk", "valid": "false"}]}}
    )
    artifacts = build_json_summary([result], repo_root="/repo")["artifacts"]
    assert artifacts["valid"] == 0
    assert artifacts["invalid"] == 1
    assert artifacts["verification_requested"] is False


def test_legacy_failure_classifier_prioritizes_interrupted_and_resource_exits_over_tests() -> None:
    assert classify_failure("test", "Interrupted by user; tests failed", 130) == "interrupted"
    assert classify_failure("test", "failed tests but process was killed by memory pressure", 137) == "resource_interrupted"


def test_structured_gradle_outcome_case_is_failure() -> None:
    console = Console(file=io.StringIO(), force_terminal=False)
    tracker = GradleProgressTracker([], "/repo", "build", console)
    tracker.update_from_line('DEVTOOL_EVENT {"kind":"task-finish","task":":x","outcome":"FaIlEd","completed":1,"total":1}')
    assert tracker.errors == 1
    assert tracker.failed_task == ":x"


def test_active_log_merge_keeps_authoritative_lock_status() -> None:
    lock = ActiveRun("run", 123, "devtool", "/repo", "failed", "validation", "2026-01-01T00:00:02Z", None)
    hud = ActiveRun("run", 123, "devtool --rich", "/repo", "running", "build", "2026-01-01T00:00:01Z", Path("run.log"))
    merged = _merge_active_runs(lock, hud)
    assert merged.status == "failed"
    assert merged.phase == "validation"


def test_tasker_result_json_uses_error_diagnostic_status_and_redaction() -> None:
    result = OperationResult("lint", [], 0, 0, "", [Issue("ERROR", "lint", "a.py", "F401", SECRET)], metadata={"token": SECRET})
    payload = _result_to_json(result)
    rendered = json.dumps(payload)
    assert payload["status"] == "failed"
    assert SECRET not in rendered
