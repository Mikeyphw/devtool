from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from types import SimpleNamespace

from devtool import cli, logstore


def test_live_stream_private_key_redaction_keeps_state_across_lines() -> None:
    redactor = logstore.StreamingLogRedactor()
    output = "".join(
        [
            redactor.redact("before\n"),
            redactor.redact("-----BEGIN OPENSSH PRIVATE KEY-----\n"),
            redactor.redact("FAKESECRETBODY1234567890\n"),
            redactor.redact("another-secret-line\n"),
            redactor.redact("-----END OPENSSH PRIVATE KEY-----\n"),
            redactor.redact("after\n"),
        ]
    )
    assert "FAKESECRETBODY1234567890" not in output
    assert "another-secret-line" not in output
    assert output.count("<redacted:private-key>") == 1
    assert "before" in output and "after" in output


def test_redaction_does_not_treat_diagnostic_identifier_substrings_as_secrets() -> None:
    diagnostic = (
        "GoogleDriveProductContractTest."
        "managedDriveConfigurationIsReservedPrivateAndCredentialFreeAtUiBoundary: expected true\n"
    )
    assert logstore.redact_sensitive_text(diagnostic) == diagnostic
    assert "secret-value" not in logstore.redact_sensitive_text("DB_PASSWORD=secret-value\n")
    assert "secret-value" not in logstore.redact_sensitive_text("AUTH_TOKEN: secret-value\n")


def test_log_identifier_uses_resolved_duplicate_download_variant(tmp_path: Path) -> None:
    base = tmp_path / "overlay_v2.zip"
    duplicate = tmp_path / "overlay_v2-1.zip"
    base.write_bytes(b"old")
    duplicate.write_bytes(b"new")
    os.utime(base, (10, 10))
    os.utime(duplicate, (20, 20))
    args = argparse.Namespace(command="apply-overlay", artifact=str(base))
    assert cli._log_identifier_for_args(args) == duplicate.name


def test_run_log_start_is_nonempty_and_records_resolved_artifact(tmp_path: Path) -> None:
    artifact = tmp_path / "patch.zip"
    artifact.write_bytes(b"x")
    args = argparse.Namespace(command="apply-overlay", artifact=str(artifact), target="devtool")
    config = SimpleNamespace(repo_root=tmp_path, selected_target="devtool")
    from unittest.mock import patch
    with patch.dict(os.environ, dict(os.environ), clear=True):
        text = cli._run_log_start_text(args, config)
    path = tmp_path / "run.log"
    logstore.write_log(text, path)
    assert path.stat().st_size > 0
    saved = path.read_text(encoding="utf-8")
    assert "--- devtool run start ---" in saved
    assert f"Devtool artifact: {artifact}" in saved
    assert "Devtool status: running" in saved


def test_terminal_log_summary_is_durable_and_authoritative(tmp_path: Path) -> None:
    args = argparse.Namespace(command="test", artifact="")
    path = tmp_path / "run.log"
    logstore.write_log("--- devtool run start ---\nDevtool status: running", path)
    started = datetime.now(timezone.utc)
    finished = started + timedelta(seconds=1)
    summary = "Devtool completed successfully.\n\nDiagnostics: 2 warnings, 0 errors"
    cli._append_run_log_summary(
        path,
        args,
        None,
        {"warnings": 2, "errors": 0, "failed": 0, "skipped": 0},
        summary + "\nTests: 3 passed, 0 failed, 0 skipped (3 total)\nGit commit: abc1234",
        started_at=started,
        finished_at=finished,
        exit_code=0,
        results=[],
    )
    saved = path.read_text(encoding="utf-8")
    assert "--- devtool run final ---" in saved
    assert "Devtool status: ok" in saved
    assert "Devtool exit code: 0" in saved
    assert "Devtool completed successfully." in saved
    assert "Diagnostics: 2 warnings, 0 errors" in saved
    assert "Devtool tests: 3 passed, 0 failed, 0 skipped (3 total)" in saved
    assert "Devtool commit: abc1234" in saved


def test_logs_latest_cli_uses_multiroot_resolver(monkeypatch, tmp_path: Path, capsys) -> None:
    selected = tmp_path / "bridge.log"
    selected.write_text("bridge", encoding="utf-8")
    monkeypatch.setattr(cli, "resolve_log_path", lambda selector, use_fzf=False: selected)

    def forbidden_latest():
        raise AssertionError("logs latest must not use the primary-root-only latest_log()")

    monkeypatch.setattr(cli, "latest_log", forbidden_latest)
    assert cli.main(["logs", "latest"]) == 0
    rendered = capsys.readouterr().out.replace("\n", "").strip()
    assert str(selected) in rendered


def _capture_copy(raw_output: str, payload: dict, *, summary: str = "test: failed exit=1 time=1.0s", log_path: Path | None = None) -> str:
    import json
    from types import SimpleNamespace
    from unittest.mock import patch

    copied: list[str] = []
    result = SimpleNamespace(success=True, backend="test", reason="")
    args = SimpleNamespace(no_clipboard=False, copy=True, copy_via="auto", command="test")
    args._devtool_run_id = "run-test-123"
    with patch("devtool.cli.copy_text", side_effect=lambda text, preferred="auto": copied.append(text) or result), patch("devtool.cli.console.print"):
        cli._maybe_copy(raw_output, summary, json.dumps(payload), args, log_path=log_path)
    assert len(copied) == 1
    return copied[0]


def test_clipboard_uses_canonical_counts_and_does_not_reinflate_gradle_wrappers(tmp_path: Path) -> None:
    raw_output = """
$ /data/data/com.termux/files/usr/bin/gradle :app:installBkxRcloneAndroidRuntime
FAILURE: Build failed with an exception.
Execution failed for task ':app:installBkxRcloneAndroidRuntime'.
error: Command failed (1): /data/data/com.termux/files/usr/bin/gradle :app:installBkxRcloneAndroidRuntime
"""
    payload = {
        "warnings": 4,
        "errors": 1,
        "failed": 1,
        "skipped": 0,
        "results": [
            {
                "name": "android-validation-explicit-tasks",
                "returncode": 1,
                "issues": [
                    {
                        "severity": "error",
                        "command": "android-validation-explicit-tasks",
                        "file": "-",
                        "line": None,
                        "code": "GRADLE_TASK",
                        "message": "Gradle task failed: :app:installBkxRcloneAndroidRuntime",
                    }
                ],
            }
        ],
        "overlays": [
            {
                "index": 1,
                "total": 1,
                "label": "bkx_android_native16k_rclone_source_rebuild_v2.zip",
                "target": "bkx_android",
                "artifact": "/sdcard/Download/bkx_android_native16k_rclone_source_rebuild_v2.zip",
                "warnings": 4,
                "errors": 1,
                "duration_seconds": 360.63,
            }
        ],
    }
    text = _capture_copy(raw_output, payload, log_path=tmp_path / "run.log")
    assert "- Diagnostics: 4 warnings, 1 errors" in text
    assert "FAILURE: Build failed with an exception." not in text.split("Errors:", 1)[1].split("Failure details:", 1)[0]
    assert "Run: run-test-123" in text
    assert f"Log: {tmp_path / 'run.log'}" in text


def test_clipboard_dedupe_keeps_repeated_messages_at_distinct_locations() -> None:
    payload = {
        "warnings": 0,
        "errors": 3,
        "failed": 1,
        "skipped": 0,
        "results": [
            {
                "name": "android-validation-build",
                "returncode": 1,
                "issues": [
                    {
                        "severity": "error",
                        "command": "android-validation-build",
                        "file": "app/src/MainViewModel.kt",
                        "line": 3636,
                        "code": "KOTLIN",
                        "message": "Unresolved reference 'privateNetworkApproved'.",
                    },
                    {
                        "severity": "error",
                        "command": "android-validation-build",
                        "file": "app/src/MainViewModel.kt",
                        "line": 3644,
                        "code": "KOTLIN",
                        "message": "Unresolved reference 'privateNetworkApproved'.",
                    },
                    {
                        "severity": "error",
                        "command": "android-validation-build",
                        "file": "-",
                        "line": None,
                        "code": "GRADLE_TASK",
                        "message": "Gradle task failed: :app:compileDebugKotlin",
                    },
                ],
            }
        ],
    }
    text = _capture_copy("", payload)
    assert "app/src/MainViewModel.kt:3636: KOTLIN: Unresolved reference 'privateNetworkApproved'." in text
    assert "app/src/MainViewModel.kt:3644: KOTLIN: Unresolved reference 'privateNetworkApproved'." in text
    assert "- Diagnostics:" not in text or "3 errors" in text
    assert "Failure reason:\n- android validation build failed with exit 1: app/src/MainViewModel.kt:3636" in text


def test_clipboard_summary_redacts_secrets_even_when_copy_text_is_mocked() -> None:
    payload = {
        "warnings": 0,
        "errors": 1,
        "failed": 1,
        "skipped": 0,
        "results": [
            {
                "name": "test",
                "returncode": 1,
                "output": "DB_PASSWORD=secret-value\nerror: failed with AUTH_TOKEN=secret-token\n",
                "issues": [
                    {
                        "severity": "error",
                        "command": "test",
                        "file": "-",
                        "line": None,
                        "code": "GENERIC",
                        "message": "failed with DB_PASSWORD=secret-value",
                    }
                ],
            }
        ],
    }
    text = _capture_copy("DB_PASSWORD=secret-value\nerror: failed with AUTH_TOKEN=secret-token\n", payload)
    assert "secret-value" not in text
    assert "secret-token" not in text
    assert "<redacted>" in text


def test_termux_chroot_copy_uses_summary_by_default_and_keeps_raw_explicit() -> None:
    from devtool import termux_chroot_runner as runner

    config, forwarded = runner.translate_devtool_args(["--copy", "test"], env={"HOME": "/data/data/com.termux/files/home"}, cwd="/repo")
    assert config.copy_mode == "summary"
    assert "--copy" not in forwarded
    payload = {"clipboard": {"raw": "raw output", "summary": "summary output", "json": {"ok": True}}}
    assert runner._clipboard_text_from_machine_payload(payload, config.copy_mode) == "summary output"
    assert runner._clipboard_text_from_machine_payload(payload, "raw") == "raw output"


def test_terminal_log_summary_records_failure_kind_and_rollback_state(tmp_path: Path) -> None:
    from devtool.models import OperationResult

    args = argparse.Namespace(command="apply-overlay", artifact="")
    path = tmp_path / "failed.log"
    logstore.write_log("--- devtool run start ---\nDevtool status: running", path)
    started = datetime.now(timezone.utc)
    finished = started + timedelta(seconds=2)
    results = [
        OperationResult("android-validation-build", ["gradle"], 1, 1.0, "", [], failure_kind="compile_failure"),
        OperationResult("apply-rollback", ["devtool", "rollback"], 0, 0.1, "Rolled back changes.", []),
    ]
    cli._append_run_log_summary(
        path, args, None, {"warnings": 0, "errors": 1, "failed": 1, "skipped": 0},
        "Devtool failed.\nRecovery: rollback succeeded",
        started_at=started, finished_at=finished, exit_code=1, results=results,
    )
    saved = path.read_text(encoding="utf-8")
    assert "Devtool failure kind: compile_failure" in saved
    assert "Devtool rollback: succeeded" in saved
