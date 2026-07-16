
from __future__ import annotations

import os
import sys
from pathlib import Path

from devtool.models import CommandSpec, ProjectConfig
from devtool.runners import run_specs


def test_run_specs_does_not_wait_forever_for_stdout_held_by_grandchild(tmp_path: Path) -> None:
    script = tmp_path / "hold_stdout.py"
    child = tmp_path / "child.py"
    child.write_text("import time\nprint('grandchild alive', flush=True)\ntime.sleep(30)\n", encoding="utf-8")
    script.write_text(
        "import subprocess, sys, time\n"
        "print('tests finished', flush=True)\n"
        "subprocess.Popen([sys.executable, sys.argv[1]])\n"
        "time.sleep(0.1)\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    config = ProjectConfig(kind="generic", repo_root=tmp_path)
    results, raw = run_specs(
        config,
        [CommandSpec("test", [sys.executable, str(script), str(child)])],
        verbose=False,
    )
    assert results[0].returncode == 0
    assert "tests finished" in raw


def test_run_specs_streams_child_output_to_shared_log(tmp_path: Path) -> None:
    script = tmp_path / "emit.py"
    script.write_text("print('live line', flush=True)\n", encoding="utf-8")
    log_path = tmp_path / "run.log"
    config = ProjectConfig(kind="generic", repo_root=tmp_path)

    results, raw = run_specs(
        config,
        [CommandSpec("build", [sys.executable, str(script)])],
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
        "#!/usr/bin/env python3\n"
        "import time\n"
        "print('Test run for /tmp/Demo.Tests.dll', flush=True)\n"
        "print('Passed! - Failed: 0, Passed: 3, Skipped: 0, Total: 3, Duration: 1 ms', flush=True)\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    fake_dotnet.chmod(0o755)
    monkeypatch.setenv("DEVTOOL_TEST_COMPLETION_GRACE", "0.2")
    config = ProjectConfig(kind="generic", repo_root=tmp_path)
    results, raw = run_specs(config, [CommandSpec("test", [str(fake_dotnet), "test"])], verbose=False)
    assert results[0].returncode == 0
    assert "final test summary" in raw


def test_dotnet_test_final_failed_summary_hang_is_stopped_as_failure(tmp_path: Path, monkeypatch) -> None:
    fake_dotnet = tmp_path / "dotnet"
    fake_dotnet.write_text(
        "#!/usr/bin/env python3\n"
        "import time\n"
        "print('Failed! - Failed: 1, Passed: 2, Skipped: 0, Total: 3, Duration: 1 ms', flush=True)\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    fake_dotnet.chmod(0o755)
    monkeypatch.setenv("DEVTOOL_TEST_COMPLETION_GRACE", "0.2")
    config = ProjectConfig(kind="generic", repo_root=tmp_path)
    results, raw = run_specs(config, [CommandSpec("test", [str(fake_dotnet), "test"])], verbose=False)
    assert results[0].returncode == 1
    assert "final test summary" in raw


def test_dotnet_test_failed_summary_is_not_overwritten_by_later_passing_summary(tmp_path: Path, monkeypatch) -> None:
    fake_dotnet = tmp_path / "dotnet"
    fake_dotnet.write_text(
        "#!/usr/bin/env python3\n"
        "import time\n"
        "print('Failed! - Failed: 1, Passed: 75, Skipped: 0, Total: 76, Duration: 1 ms', flush=True)\n"
        "print('Passed! - Failed: 0, Passed: 11, Skipped: 0, Total: 11, Duration: 1 ms', flush=True)\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    fake_dotnet.chmod(0o755)
    monkeypatch.setenv("DEVTOOL_TEST_COMPLETION_GRACE", "0.2")
    config = ProjectConfig(kind="generic", repo_root=tmp_path)
    results, raw = run_specs(config, [CommandSpec("test", [str(fake_dotnet), "test"])], verbose=False)
    assert results[0].returncode == 1
    assert results[0].failure_kind == "test_failure"
    assert "final test summary" in raw


def test_run_specs_promotes_parsed_error_diagnostics_to_failure(tmp_path: Path) -> None:
    script = tmp_path / "fake_test.py"
    test_file = tmp_path / "tests" / "VisualSnapshotTests.cs"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("// test", encoding="utf-8")
    script.write_text(
        "import sys\n"
        "print('  Failed S3Atelier.App.Tests.VisualSnapshotTests.Case [3 ms]')\n"
        "print('  Error Message:')\n"
        "print('   Assert.Equal() Failure: Strings differ')\n"
        "print('  Stack Trace:')\n"
        f"print('     at Test() in {test_file}:line 52')\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    config = ProjectConfig(kind="generic", repo_root=tmp_path)
    results, raw = run_specs(config, [CommandSpec("test", [sys.executable, str(script)])], verbose=False)
    assert results[0].returncode == 1
    assert results[0].failure_kind == "test_failure"
    assert results[0].issues[0].code == "XUNIT"
    assert "detected error diagnostics" in raw
