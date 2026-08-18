from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from devtool import android_build, android_integration, termux_toolchains
from devtool.models import ProjectConfig
from devtool.termux_toolchains import ToolProvider


def _project_config(root: Path, *, aapt2_provider: str = "termux") -> ProjectConfig:
    root.mkdir(parents=True, exist_ok=True)
    (root / "settings.gradle.kts").write_text('rootProject.name = "fixture"\n')
    (root / "build.gradle.kts").write_text(
        'plugins { id("com.android.application") version "9.4.0" apply false }\n'
    )
    return ProjectConfig(
        repo_root=root,
        project_root=str(root),
        kind="android",
        build_target=str(root),
        runner_options={
            "android": {"aapt2_provider": aapt2_provider, "max_workers": 2},
            "gradle": {"max_workers": 2, "structured_events": False},
            "tasks": {},
        },
    )


def test_aidl_runtime_probe_uses_help_not_interface_version_flag() -> None:
    result = subprocess.CompletedProcess(["aidl", "--help"], 0, stdout=b"usage\n", stderr=b"")
    with patch("devtool.android_build.subprocess.run", return_value=result) as run:
        assert android_build.android_build_tool_executes(Path("/tmp/aidl"), "aidl")
    assert run.call_args.args[0] == ["/tmp/aidl", "--help"]


def test_aidl_runtime_probe_rejects_shell_fallback_even_if_process_spawned() -> None:
    result = subprocess.CompletedProcess(
        ["aidl", "--help"], 2, stdout=b"",
        stderr=b"aidl: 1: Syntax error: word unexpected (expecting \")\")\n",
    )
    with patch("devtool.android_build.subprocess.run", return_value=result):
        assert not android_build.android_build_tool_executes(Path("/tmp/aidl"), "aidl")


def test_termux_aidl_provider_uses_help_and_requires_success() -> None:
    sentinel = ToolProvider("aidl", "termux", False, compatible=False)
    with patch("devtool.termux_toolchains._termux_binary_probe", return_value=sentinel) as probe:
        termux_toolchains.termux_android_tool_probe("aidl")
    kwargs = probe.call_args.kwargs
    assert kwargs["version_args"] == ("--help",)
    assert kwargs["accepted_returncodes"] == (0,)


def test_commit451_arm64_repair_set_includes_aapt2() -> None:
    assert android_build.ARM_BUILD_TOOLS_NATIVE_NAMES == (
        "aapt2", "aidl", "zipalign", "split-select"
    )


def test_glibc_arm64_repair_can_install_commit451_aapt2(tmp_path: Path) -> None:
    sdk = tmp_path / "sdk"
    directory = sdk / "build-tools" / "36.0.0"
    directory.mkdir(parents=True)
    target = directory / "aapt2"
    target.write_bytes(b"old-x86")
    target.chmod(0o755)
    source = tmp_path / "aapt2-arm64"
    source.write_bytes(b"new-arm64")
    source.chmod(0o755)

    with (
        patch("devtool.android_build.normalized_machine", return_value="arm64"),
        patch("devtool.android_build.arm64_build_tools_replacements_needed", return_value=["aapt2"]),
        patch("devtool.android_build.sdk_root_is_writable_for_install", return_value=True),
        patch("devtool.android_build.running_under_termux_python", return_value=False),
        patch("devtool.android_build.obtain_commit451_arm64_tool", return_value=source) as obtain,
        patch("devtool.android_build.native_android_build_tool_compatible", return_value=True),
    ):
        android_build.ensure_arm64_build_tools_compatible(
            sdk, "36.0.0", tmp_path / "cache",
            provider="auto", offline=True, force=False, dry_run=False,
        )

    obtain.assert_called_once_with(
        tmp_path / "cache", "36.0.0", "aapt2",
        offline=True, force=False, dry_run=False,
    )
    assert target.read_bytes() == b"new-arm64"


def test_chroot_remaps_termux_aapt2_provider_to_linux_auto(tmp_path: Path) -> None:
    config = _project_config(tmp_path / "project")
    with patch("devtool.android_integration._android_execution_mode", return_value="chroot"):
        argv = android_integration.android_builder_arguments_for_tasks(
            config, [":app:assembleDebug"], scan_artifacts=False,
        )
    index = argv.index("--aapt2-provider")
    assert argv[index + 1] == "auto"


def test_native_termux_keeps_explicit_termux_aapt2_provider(tmp_path: Path) -> None:
    config = _project_config(tmp_path / "project")
    with patch("devtool.android_integration._android_execution_mode", return_value="native-termux"):
        argv = android_integration.android_builder_arguments_for_tasks(
            config, [":app:assembleDebug"], scan_artifacts=False,
        )
    index = argv.index("--aapt2-provider")
    assert argv[index + 1] == "termux"


def test_aapt2_warning_suppression_is_not_self_referential() -> None:
    args = android_build.gradle_arguments_with_aapt2_override([], Path("/opt/devtool/aapt2"))
    suppression = next(
        value for value in args
        if value.startswith("-Pandroid.suppressUnsupportedOptionWarnings=")
    )
    assert suppression == (
        "-Pandroid.suppressUnsupportedOptionWarnings="
        "android.aapt2FromMavenOverride"
    )
    assert "android.suppressUnsupportedOptionWarnings," not in suppression


def test_complete_wrapper_survives_broken_termux_gradle_optimization(tmp_path: Path) -> None:
    wrapper_dir = tmp_path / "gradle" / "wrapper"
    wrapper_dir.mkdir(parents=True)
    (wrapper_dir / "gradle-wrapper.properties").write_text(
        "distributionUrl=https\\://services.gradle.org/distributions/gradle-8.13-bin.zip\n"
    )
    (wrapper_dir / "gradle-wrapper.jar").write_bytes(b"wrapper")
    wrapper = tmp_path / "gradlew"
    wrapper.write_text("#!/bin/sh\nexit 0\n")
    wrapper.chmod(0o755)

    with patch("devtool.android_build.running_under_termux_python", return_value=True), patch(
        "devtool.android_build.shutil.which", return_value="/stale/missing/gradle"
    ):
        selected = android_build.select_gradle(
            tmp_path,
            explicit=None,
            version=None,
            cache_root=tmp_path / ".cache",
            offline=False,
            force=False,
        )

    assert selected == wrapper.resolve()

