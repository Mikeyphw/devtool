
from __future__ import annotations

import shutil
from pathlib import Path

from devtool.models import CommandSpec, ProjectConfig
from devtool.runners import run_specs
from tests._compat import portable_shell_argv


def test_run_specs_does_not_wait_forever_for_stdout_held_by_grandchild(tmp_path: Path) -> None:
    child = tmp_path / "child.sh"
    child.write_text("printf 'grandchild alive\n'\nsleep 30\n", encoding="utf-8")
    script = tmp_path / "hold_stdout.sh"
    script.write_text(
        "printf 'tests finished\n'\n"
        'sh "$1" &\n'
        "sleep 0.05\n"
        "exit 0\n",
        encoding="utf-8",
    )
    config = ProjectConfig(kind="generic", repo_root=tmp_path)
    results, raw = run_specs(
        config,
        [CommandSpec("test", [shutil.which("sh") or "sh", str(script), str(child)])],
        verbose=False,
        shutdown_dotnet=False,
    )
    assert results[0].returncode == 0
    assert "tests finished" in raw


def test_run_specs_streams_child_output_to_shared_log(tmp_path: Path) -> None:
    script = tmp_path / "emit.sh"
    script.write_text("printf 'live line\n'\n", encoding="utf-8")
    log_path = tmp_path / "run.log"
    config = ProjectConfig(kind="generic", repo_root=tmp_path)

    results, raw = run_specs(
        config,
        [CommandSpec("build", [shutil.which("sh") or "sh", str(script)])],
        verbose=False,
        log_path=log_path,
    )

    text = log_path.read_text(encoding="utf-8")
    assert results[0].returncode == 0
    assert "live line" in raw
    assert "live line" in text
    assert text.count("$ ") == 1


def test_dotnet_test_final_summary_hang_is_stopped_as_success(tmp_path: Path, monkeypatch) -> None:
    fake_dotnet = tmp_path / "dotnet"
    fake_dotnet.write_text(
        f"#!{shutil.which('sh') or '/bin/sh'}\n"
        "printf 'Test run for /tmp/Demo.Tests.dll\n'\n"
        "printf 'Passed! - Failed: 0, Passed: 3, Skipped: 0, Total: 3, Duration: 1 ms\n'\n"
        "sleep 30\n",
        encoding="utf-8",
    )
    fake_dotnet.chmod(0o755)
    monkeypatch.setenv("DEVTOOL_TEST_COMPLETION_GRACE", "0.05")
    config = ProjectConfig(kind="generic", repo_root=tmp_path)
    results, raw = run_specs(config, [CommandSpec("test", [str(fake_dotnet), "test"])], verbose=False)
    assert results[0].returncode == 0
    assert "final test summary" in raw


def test_dotnet_test_final_failed_summary_hang_is_stopped_as_failure(tmp_path: Path, monkeypatch) -> None:
    fake_dotnet = tmp_path / "dotnet"
    fake_dotnet.write_text(
        f"#!{shutil.which('sh') or '/bin/sh'}\n"
        "printf 'Failed! - Failed: 1, Passed: 2, Skipped: 0, Total: 3, Duration: 1 ms\n'\n"
        "sleep 30\n",
        encoding="utf-8",
    )
    fake_dotnet.chmod(0o755)
    monkeypatch.setenv("DEVTOOL_TEST_COMPLETION_GRACE", "0.05")
    config = ProjectConfig(kind="generic", repo_root=tmp_path)
    results, raw = run_specs(config, [CommandSpec("test", [str(fake_dotnet), "test"])], verbose=False)
    assert results[0].returncode == 1
    assert "final test summary" in raw


def test_dotnet_test_failed_summary_is_not_overwritten_by_later_passing_summary(tmp_path: Path, monkeypatch) -> None:
    fake_dotnet = tmp_path / "dotnet"
    fake_dotnet.write_text(
        f"#!{shutil.which('sh') or '/bin/sh'}\n"
        "printf 'Failed! - Failed: 1, Passed: 75, Skipped: 0, Total: 76, Duration: 1 ms\n'\n"
        "printf 'Passed! - Failed: 0, Passed: 11, Skipped: 0, Total: 11, Duration: 1 ms\n'\n"
        "sleep 30\n",
        encoding="utf-8",
    )
    fake_dotnet.chmod(0o755)
    monkeypatch.setenv("DEVTOOL_TEST_COMPLETION_GRACE", "0.05")
    config = ProjectConfig(kind="generic", repo_root=tmp_path)
    results, raw = run_specs(config, [CommandSpec("test", [str(fake_dotnet), "test"])], verbose=False)
    assert results[0].returncode == 1
    assert results[0].failure_kind == "test_failure"
    assert "final test summary" in raw


def test_run_specs_promotes_parsed_error_diagnostics_to_failure(tmp_path: Path) -> None:
    script = tmp_path / "fake_test.sh"
    test_file = tmp_path / "tests" / "VisualSnapshotTests.cs"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("// test", encoding="utf-8")
    script.write_text(
        "printf '%s\n' '  Failed S3Atelier.App.Tests.VisualSnapshotTests.Case [3 ms]' "
        "'  Error Message:' '   Assert.Equal() Failure: Strings differ' '  Stack Trace:' "
        f"'     at Test() in {test_file}:line 52'\n",
        encoding="utf-8",
    )
    config = ProjectConfig(kind="generic", repo_root=tmp_path)
    spec = CommandSpec(
        "test",
        [shutil.which("sh") or "sh", str(script)],
        metadata={"trust_stdout_diagnostics": True},
    )
    results, raw = run_specs(config, [spec], verbose=False)
    assert results[0].returncode == 1
    assert results[0].failure_kind == "test_failure"
    assert results[0].issues[0].code == "XUNIT"
    assert "detected error diagnostics" in raw


def test_run_specs_does_not_promote_successful_captured_stdout_diagnostics_for_generic_commands(tmp_path: Path) -> None:
    script = tmp_path / "emit_fixture_text.sh"
    script.write_text(
        "printf '%s\n' 'x.py:1:1: F821 undefined name' "
        "'src/a.py:1:1: error: fixture text only' "
        "'e: file:///tmp/Demo.kt: (7, 12): Kotlin fixture text' "
        "'> Task :app:testDebugUnitTest FAILED' "
        "'  Failed Example.Tests.Case [3 ms]' '  Error Message:' "
        "'   Assert.Equal() Failure: fixture text only'\n",
        encoding="utf-8",
    )
    config = ProjectConfig(kind="generic", repo_root=tmp_path)
    results, raw = run_specs(config, [CommandSpec("build", [shutil.which("sh") or "sh", str(script)])], verbose=False)
    assert results[0].returncode == 0
    assert results[0].failure_kind == ""
    assert not [issue for issue in results[0].issues if issue.severity in {"warning", "error"}]
    assert results[0].metadata["suppressed_stdout_diagnostics"] >= 4
    assert "successful process boundary" in results[0].metadata["suppressed_stdout_diagnostics_reason"]
    assert "detected error diagnostics" not in raw


def test_run_specs_can_opt_in_to_fatal_successful_stdout_diagnostics(tmp_path: Path) -> None:
    script = tmp_path / "emit_real_diagnostic.sh"
    script.write_text("printf 'x.py:1:1: F821 undefined name\n'\n", encoding="utf-8")
    config = ProjectConfig(kind="generic", repo_root=tmp_path)
    spec = CommandSpec(
        "build",
        [shutil.which("sh") or "sh", str(script)],
        metadata={"trust_stdout_diagnostics": True},
    )
    results, raw = run_specs(config, [spec], verbose=False)
    assert results[0].returncode == 1
    assert results[0].failure_kind in {"compile_failure", "unknown_failure"}
    assert any(issue.code == "F821" for issue in results[0].issues)
    assert "detected error diagnostics" in raw
