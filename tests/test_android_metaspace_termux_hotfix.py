from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from devtool.android_build import _parse_adb_device_states, gradle_jvmargs, parse_args
from devtool.android_integration import android_builder_arguments_for_tasks
from devtool.cli import (
    _apply_runner_cli_options,
    _apply_validation_resource_overrides,
    build_parser,
)
from devtool.diagnostics import classify_failure, parse_issues
from devtool.models import ProjectConfig


def _project(root: Path) -> None:
    (root / "settings.gradle.kts").write_text('rootProject.name = "demo"\ninclude(":app")\n', encoding="utf-8")
    app = root / "app"
    app.mkdir()
    (app / "build.gradle.kts").write_text(
        'plugins { id("com.android.application") }\nandroid { namespace = "demo"; compileSdk = 36 }\n',
        encoding="utf-8",
    )


def test_validation_cli_can_disable_devtool_metaspace_cap(tmp_path: Path) -> None:
    parser = build_parser()
    args = parser.parse_args(["validate", "--no-metaspace-guard"])
    assert args.disable_metaspace_guard is True

    config = ProjectConfig(
        repo_root=tmp_path,
        project_root=str(tmp_path),
        kind="android",
        execution_environment="native-termux",
        runner_options={"validation": {"android_safe": True}},
    )
    _project(tmp_path)
    _apply_validation_resource_overrides(config, args)
    with patch.dict(
        os.environ,
        {"HOME": "/data/data/com.termux/files/home", "PREFIX": "/data/data/com.termux/files/usr", "TERMUX_VERSION": "0.119"},
        clear=True,
    ):
        argv = android_builder_arguments_for_tasks(
            config,
            [":app:assembleOfficialDebug"],
            scan_artifacts=False,
            validation=True,
            validation_phase="package",
        )
    assert "--no-gradle-metaspace-cap" in argv
    assert "--gradle-metaspace-mb" not in argv
    notes = [argv[i + 1] for i, value in enumerate(argv[:-1]) if value == "--resource-resolution"]
    assert any("effective=uncapped-by-devtool" in note for note in notes)


def test_gradle_metaspace_zero_alias_disables_cap(tmp_path: Path) -> None:
    parser = build_parser()
    args = parser.parse_args(["validate", "--gradle-metaspace-mb", "0"])
    assert args.gradle_metaspace_mb == 0


def test_standalone_constrained_builder_can_omit_max_metaspace() -> None:
    args = parse_args([
        "/tmp/demo", "--task", "assembleDebug", "--constrained",
        "--gradle-jvm-heap-mb", "1024", "--no-gradle-metaspace-cap",
    ])
    value = gradle_jvmargs(args) or ""
    assert "-Xmx1024m" in value
    assert "MaxMetaspaceSize" not in value


def test_termux_profile_can_be_bypassed_for_one_invocation(tmp_path: Path) -> None:
    parser = build_parser()
    args = parser.parse_args(["--no-termux-resource-profile", "validate", "--resource-profile", "standard"])
    config = ProjectConfig(
        repo_root=tmp_path,
        project_root=str(tmp_path),
        kind="android",
        execution_environment="native-termux",
        runner_options={},
    )
    with patch.dict(
        os.environ,
        {"HOME": "/data/data/com.termux/files/home", "PREFIX": "/data/data/com.termux/files/usr", "TERMUX_VERSION": "0.119"},
        clear=True,
    ):
        _apply_runner_cli_options(config, args)
    assert config.runner_options["termux"]["resource_profile"] == "off"
    assert config.runner_options["termux"]["_resource_profile_applied"] is False
    assert config.runner_options["validation"]["android_safe"] is False
    assert "gradle_heap_mb" not in config.runner_options["validation"]


def test_adb_error_text_is_not_parsed_as_device_state() -> None:
    assert _parse_adb_device_states("error: protocol fault (couldn't read status)\n") == {}


def test_metaspace_oom_is_primary_fatal_resource_diagnostic(tmp_path: Path) -> None:
    output = """The Daemon will expire after the build after running out of JVM Metaspace.
The currently configured max heap space is '1 GiB' and the configured max metaspace is '384 MiB'.
> Task :ext:compileDebugKotlin FAILED
e: java.lang.OutOfMemoryError: Metaspace
warning: Low storage before Android toolchain setup: 2.7 GiB free. Devtool will prune safe regenerable caches toward 6.0 GiB free space.
warning: Storage remains low after safe Devtool cleanup: 2.7 GiB free. Active SDK/NDK/QEMU trees and user Gradle dependency caches were preserved.
"""
    issues = parse_issues(tmp_path, "android-validation-build", output)
    assert issues[0].code == "GRADLE_METASPACE_OOM"
    assert issues[0].severity == "error"
    assert "384 MiB" in issues[0].message
    assert ":ext:compileDebugKotlin" in issues[0].message
    assert sum(1 for issue in issues if issue.code == "LOW_STORAGE") == 2
    assert classify_failure("android-validation-build", output, 1) == "metaspace_oom"


def test_failure_clipboard_prefers_metaspace_root_and_dedupes_secondaries(tmp_path: Path) -> None:
    import json
    from types import SimpleNamespace
    from unittest.mock import patch
    from devtool.cli import _maybe_copy

    raw_output = """> Task :ext:compileDebugKotlin FAILED
FAILURE: Build failed with an exception.
java.lang.OutOfMemoryError: Metaspace
"""
    issues = parse_issues(tmp_path, "android-validation-build", raw_output)
    payload = {
        "warnings": 0,
        "errors": len([i for i in issues if i.severity == "error"]),
        "failed": 1,
        "skipped": 0,
        "results": [{
            "name": "android-validation-build",
            "returncode": 1,
            "failure_kind": "metaspace_oom",
            "output": raw_output,
            "issues": [{
                "severity": i.severity, "command": i.command, "file": i.file,
                "code": i.code, "message": i.message, "line": i.line,
            } for i in issues],
        }],
    }
    copied: list[str] = []
    args = SimpleNamespace(no_clipboard=False, copy=True, copy_via="auto")
    result = SimpleNamespace(success=True, backend="test", reason="")
    with patch("devtool.cli.copy_text", side_effect=lambda text: copied.append(text) or result), patch("devtool.cli.console.print"):
        _maybe_copy(raw_output, "android-validation-build: failed exit=1 time=1.0s", json.dumps(payload), args)
    text = copied[0]
    assert "Failure reason:" in text
    assert "GRADLE_METASPACE_OOM" in text
    errors = text.split("Errors:", 1)[1].split("Failed:", 1)[0] if "Errors:" in text else ""
    assert "Gradle task failed" not in errors
    assert "FAILURE: Build failed with an exception" not in errors


def test_standalone_builder_preserves_child_exit_code() -> None:
    from devtool.android_build import BuildError, standalone_main
    with patch("devtool.android._standalone.main", side_effect=BuildError("child failed", exit_code=1)):
        assert standalone_main([]) == 1
