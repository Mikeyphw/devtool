from __future__ import annotations

import argparse
import io
import os
import subprocess
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from devtool.android_build import JavaRequirement, ProjectInfo, main as android_build_main
from devtool.android_integration import android_builder_arguments_for_tasks
from devtool.build_optimization import (
    android_validation_cli_resolution_notes,
    optimized_validation_env,
    resolve_android_validation_phase_policy,
)
from devtool.cli import _apply_validation_resource_overrides
from devtool.models import ProjectConfig


def _args(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "command": "validate-all",
        "max_workers": None,
        "cpu_limit": None,
        "gradle_heap_mb": None,
        "memory_guard_mb": None,
        "validation_low_priority": None,
        "validation_parallel": None,
        "validation_build_cache": None,
        "validation_daemon": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _make_android_project(root: Path) -> None:
    (root / "settings.gradle.kts").write_text('rootProject.name = "demo"\ninclude(":app")\n', encoding="utf-8")
    app = root / "app"
    app.mkdir()
    (app / "build.gradle.kts").write_text(
        'plugins { id("com.android.application") }\nandroid { namespace = "demo"; compileSdk = 36 }\n',
        encoding="utf-8",
    )


def test_cli_max_workers_outranks_android_safe_phase_default() -> None:
    config = ProjectConfig(
        repo_root=Path.cwd(),
        kind="android",
        execution_environment="native-termux",
        runner_options={
            "gradle": {"max_workers": 1, "parallel": False},
            "validation": {
                "android_safe": True,
                "max_workers": 1,
                "cpu_limit": 2,
                "parallel": False,
                "phase_profiles": {"package": {"max_workers": 1}},
            },
        },
    )
    _apply_validation_resource_overrides(config, _args(max_workers=3, memory_guard_mb=0))
    with patch("devtool.build_optimization.os.cpu_count", return_value=8), patch(
        "devtool.build_optimization.memory_info_mb", return_value=(4096, 8192)
    ):
        package = resolve_android_validation_phase_policy(config, "package")
        notes = android_validation_cli_resolution_notes(config, "package")

    assert package.max_workers == 3
    assert package.memory_guard_mb == 0
    joined = "\n".join(notes)
    assert "--max-workers requested=3 -> effective=3 (applied)" in joined
    assert "validation=1" in joined
    assert "gradle=1" in joined
    assert "phase-profile[package]=1" in joined
    assert "--memory-guard-mb requested=0 -> effective=0 (applied)" in joined


def test_cli_worker_request_reports_hardware_clamp() -> None:
    config = ProjectConfig(
        repo_root=Path.cwd(),
        kind="android",
        execution_environment="native-termux",
        runner_options={"validation": {"android_safe": True, "max_workers": 1}},
    )
    _apply_validation_resource_overrides(config, _args(command="validate", max_workers=99))
    with patch("devtool.build_optimization.os.cpu_count", return_value=8), patch(
        "devtool.build_optimization.memory_info_mb", return_value=(4096, 8192)
    ):
        package = resolve_android_validation_phase_policy(config, "package")
        notes = android_validation_cli_resolution_notes(config, "package")

    assert package.max_workers == 8
    joined = "\n".join(notes)
    assert "requested=99 -> effective=8 (clamped)" in joined
    assert "host cpu_count=8" in joined


def test_reapplying_cli_override_preserves_original_provenance() -> None:
    config = ProjectConfig(
        repo_root=Path.cwd(),
        kind="android",
        execution_environment="native-termux",
        runner_options={
            "gradle": {"max_workers": 1},
            "validation": {"android_safe": True, "max_workers": 1},
        },
    )
    args = _args(max_workers=3)
    _apply_validation_resource_overrides(config, args)
    _apply_validation_resource_overrides(config, args)
    record = config.runner_options["validation"]["_cli_resource_overrides"]["max_workers"]
    assert record["prior"]["validation"] == 1
    assert record["prior"]["gradle"] == 1


def test_cli_parallel_removes_hidden_android_safe_parallel_false() -> None:
    config = ProjectConfig(
        repo_root=Path.cwd(),
        kind="android",
        execution_environment="native-termux",
        runner_options={
            "gradle": {"parallel": False},
            "validation": {"android_safe": True, "parallel": False},
        },
    )
    _apply_validation_resource_overrides(config, _args(validation_parallel=True))
    with patch("devtool.build_optimization.os.cpu_count", return_value=8), patch(
        "devtool.build_optimization.memory_info_mb", return_value=(4096, 8192)
    ):
        env = optimized_validation_env(config, "android")

    assert "-Dorg.gradle.parallel=false" not in env.get("GRADLE_OPTS", "")
    assert config.runner_options["validation"]["parallel"] is True
    assert config.runner_options["gradle"]["parallel"] is True


def test_android_builder_passes_worker_resolution_and_cli_heap_without_phase_floor() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_android_project(root)
        config = ProjectConfig(
            repo_root=root,
            project_root=str(root),
            kind="android",
            execution_environment="native-termux",
            runner_options={
                "gradle": {"max_workers": 1},
                "validation": {
                    "android_safe": True,
                    "max_workers": 3,
                    "cpu_limit": 2,
                    "gradle_heap_mb": 512,
                    "phase_profiles": {"package": {"max_workers": 1}},
                    "_cli_resource_overrides": {
                        "max_workers": {
                            "value": 3,
                            "flag": "--max-workers",
                            "prior": {"validation": 1, "gradle": 1},
                        },
                        "gradle_heap_mb": {
                            "value": 512,
                            "flag": "--gradle-heap-mb",
                            "prior": {"validation": 768},
                        },
                    },
                },
            },
        )
        with patch.dict(
            os.environ,
            {
                "HOME": "/data/data/com.termux/files/home",
                "PREFIX": "/data/data/com.termux/files/usr",
                "TERMUX_VERSION": "0.119",
                "DEVTOOL_GRADLE_INIT_SCRIPT": str(root / "events.gradle"),
            },
            clear=True,
        ), patch("devtool.build_optimization.os.cpu_count", return_value=8), patch(
            "devtool.build_optimization.memory_info_mb", return_value=(4096, 8192)
        ):
            argv = android_builder_arguments_for_tasks(
                config,
                ["assembleDebug"],
                scan_artifacts=False,
                validation=True,
                validation_phase="package",
            )

    assert argv[argv.index("--max-workers") + 1] == "3"
    assert argv[argv.index("--gradle-jvm-heap-mb") + 1] == "512"
    resolutions = [argv[index + 1] for index, value in enumerate(argv[:-1]) if value == "--resource-resolution"]
    joined = "\n".join(resolutions)
    assert "--max-workers requested=3 -> effective=3 (applied)" in joined
    assert "phase-profile[package]=1" in joined
    assert "--gradle-heap-mb requested=512 -> effective=512 (applied)" in joined


def test_gradle_process_summary_reports_effective_configuration_and_flag_resolution() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        java_home = root / "jdk"
        gradle = root / "gradlew"
        commands: list[list[str]] = []

        def fake_run(argv, *args, **kwargs):
            commands.append([str(value) for value in argv])
            return subprocess.CompletedProcess(commands[-1], 0, "", "")

        project = ProjectInfo(
            root=root,
            name="demo",
            android=False,
            android_application=False,
            compile_sdk=None,
            build_tools=None,
            agp_major=None,
            native_build=False,
            ndk_version=None,
            cmake_version=None,
        )
        output = io.StringIO()
        with (
            patch("devtool.android_build.maybe_reexec_in_chroot", return_value=None),
            patch("devtool.android_build.resolve_project_root", return_value=root),
            patch("devtool.android_build.inspect_project", return_value=project),
            patch("devtool.android_build.infer_java_requirement", return_value=JavaRequirement(21)),
            patch("devtool.android_build.find_java_home", return_value=java_home),
            patch("devtool.android_build.select_gradle", return_value=gradle),
            patch("devtool.android_build.run", side_effect=fake_run),
            patch("devtool.android_build.gradle_process_pids", return_value=[]),
            redirect_stdout(output),
        ):
            rc = android_build_main(
                [
                    str(root),
                    "--task",
                    "build",
                    "--no-artifact-scan",
                    "--constrained",
                    "--max-workers",
                    "3",
                    "--cpu-limit",
                    "2",
                    "--gradle-jvm-heap-mb",
                    "1024",
                    "--gradle-metaspace-mb",
                    "768",
                    "--no-daemon",
                    "--parallel",
                    "--build-cache",
                    "--resource-resolution",
                    "max_workers: --max-workers requested=3 -> effective=3 (applied); overrides validation=1",
                ]
            )

    assert rc == 0
    rendered = output.getvalue()
    assert "Gradle process summary" in rendered
    assert "Tasks (1): build" in rendered
    assert "workers=3" in rendered
    assert "cpu-limit=2" in rendered
    assert "parallel=on" in rendered
    assert "daemon=off/single-use" in rendered
    assert "heap=1024 MiB" in rendered
    assert "metaspace=768 MiB" in rendered
    assert "build-cache=on" in rendered
    assert "Flag resolution:" in rendered
    assert "requested=3 -> effective=3 (applied)" in rendered
    assert "Gradle result: success" in rendered

    build_command = next(command for command in commands if "build" in command)
    assert "--parallel" in build_command
    assert "-Dorg.gradle.parallel=false" not in build_command
