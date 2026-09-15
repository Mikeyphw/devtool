from __future__ import annotations

import io
import json
import os
import subprocess
import time
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest

from devtool import android_build
from devtool.android_build import BuildError, JavaRequirement, ProjectInfo
from devtool.android_integration import android_builder_arguments_for_tasks
from devtool.config import ProjectConfig, detect_project_kind


def _project(root: Path, *, android: bool = True, app: bool = True, compile_sdk: int | None = 36, agp: str = "9.2.1") -> ProjectInfo:
    return ProjectInfo(
        root=root,
        name=root.name,
        android=android,
        android_application=app,
        compile_sdk=compile_sdk,
        build_tools=None,
        agp_major=int(agp.split(".", 1)[0]) if agp else None,
        native_build=False,
        ndk_version=None,
        cmake_version=None,
        agp_version=agp,
    )


def _write_wrapper(root: Path, version: str) -> None:
    (root / "gradle" / "wrapper").mkdir(parents=True)
    (root / "gradle" / "wrapper" / "gradle-wrapper.properties").write_text(
        f"distributionUrl=https\\://services.gradle.org/distributions/gradle-{version}-bin.zip\n",
        encoding="utf-8",
    )
    (root / "gradle" / "wrapper" / "gradle-wrapper.jar").write_bytes(b"jar")
    launcher = root / "gradlew"
    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o755)


def test_wrapper_version_remains_authoritative_over_gradle_version_override(tmp_path: Path) -> None:
    _write_wrapper(tmp_path, "7.6")
    with pytest.raises(BuildError, match="Gradle 7.6|wrapper 7.6"):
        android_build.select_gradle(
            tmp_path,
            explicit=None,
            version="8.10.2",
            cache_root=tmp_path / "cache",
            offline=True,
            force=False,
            bootstrap_downloads="never",
            bootstrap_network="deny",
            provider="wrapper",
            project=_project(tmp_path, agp="8.8.0"),
        )


def test_android_gradle_string_false_options_do_not_emit_flags(tmp_path: Path) -> None:
    (tmp_path / "settings.gradle.kts").write_text('rootProject.name = "demo"\n', encoding="utf-8")
    (tmp_path / "build.gradle.kts").write_text('plugins { id("com.android.application") version "9.2.1" apply false }\n', encoding="utf-8")
    cfg = ProjectConfig(
        repo_root=tmp_path,
        project_root=str(tmp_path),
        kind="android",
        runner="android",
        runner_options={
            "android": {
                "skip_sdk_install": "false",
                "auto_sdk": "false",
                "offline": "false",
                "force_setup": "false",
                "prefer_native_termux": "false",
                "storage": {"manage_gradle_user_cache": "false", "keep_provider_downloads": "false"},
            },
            "gradle": {"no_daemon": "false", "parallel": "false", "build_cache": "false", "structured_events": "false"},
            "artifacts": {"scan": "false"},
        },
    )
    argv = android_builder_arguments_for_tasks(cfg, [":app:assembleDebug"], scan_artifacts=True)
    assert "--skip-sdk-install" not in argv
    assert "--auto-sdk" not in argv
    assert "--offline" not in argv
    assert "--force-setup" not in argv
    assert "--manage-gradle-user-cache" not in argv
    assert "--keep-provider-downloads" not in argv
    assert "--no-daemon" not in argv
    assert "--parallel" not in argv
    assert "--build-cache" not in argv
    assert "--no-artifact-scan" in argv


def test_gradle_cleanup_pid_filter_keeps_unrelated_project(tmp_path: Path) -> None:
    mine = tmp_path / "mine"
    other = tmp_path / "other"
    mine.mkdir()
    other.mkdir()
    ps = subprocess.CompletedProcess(
        ["ps"],
        0,
        stdout=(
            f"101 {os.getuid()} org.gradle.launcher.GradleMain\n"
            f"202 {os.getuid()} org.gradle.launcher.GradleMain\n"
        ),
        stderr="",
    )
    with patch("devtool.android_build.subprocess.run", return_value=ps), patch(
        "devtool.android_build._process_cwd", side_effect=lambda pid: mine if pid == 101 else other
    ):
        assert android_build.gradle_process_pids(mine) == [101]


def test_active_provider_staging_is_not_deleted(tmp_path: Path) -> None:
    active = tmp_path / "android-sdk-providers" / "termuxvoid" / f".sdk-{os.getpid()}.tmp"
    active.mkdir(parents=True)
    android_build.clear_abandoned_android_provider_staging(tmp_path, dry_run=False)
    assert active.exists()


def test_distribution_path_dot_is_not_wrapper_cache_hit(tmp_path: Path) -> None:
    _write_wrapper(tmp_path, "8.13")
    (tmp_path / "gradle" / "wrapper" / "gradle-wrapper.properties").write_text(
        "distributionUrl=https\\://services.gradle.org/distributions/gradle-8.13-bin.zip\n"
        "distributionPath=.\n",
        encoding="utf-8",
    )
    with patch("devtool.android_build.gradle_version", return_value=None):
        assert not android_build.gradle_wrapper_distribution_cached(tmp_path, "8.13", gradle_user_home=tmp_path / "gh")


def test_commented_android_plugin_does_not_make_jvm_project_android(tmp_path: Path) -> None:
    (tmp_path / "settings.gradle.kts").write_text('rootProject.name = "jvm"\n', encoding="utf-8")
    (tmp_path / "build.gradle.kts").write_text(
        "// id(\"com.android.application\") version \"9.2.1\"\nplugins { java }\n",
        encoding="utf-8",
    )
    project = android_build.inspect_project(tmp_path)
    assert not project.android
    assert not project.android_application


def test_commented_java_toolchain_does_not_force_exact_requirement(tmp_path: Path) -> None:
    (tmp_path / "settings.gradle.kts").write_text('rootProject.name = "jvm"\n', encoding="utf-8")
    (tmp_path / "build.gradle.kts").write_text(
        "// java { toolchain { languageVersion.set(JavaLanguageVersion.of(21)) } }\nplugins { java }\n",
        encoding="utf-8",
    )
    requirement = android_build.infer_java_requirement(_project(tmp_path, android=False, app=False, agp=""))
    assert requirement == JavaRequirement(minimum=8, exact=None)


class _ShortResponse:
    headers = {"Content-Length": "10"}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, size: int = -1) -> bytes:
        if getattr(self, "used", False):
            return b""
        self.used = True
        return b"short"


def test_truncated_download_is_rejected_and_part_removed(tmp_path: Path) -> None:
    dest = tmp_path / "download.zip"
    with patch("devtool.android_build.urllib.request.urlopen", return_value=_ShortResponse()):
        with pytest.raises(BuildError, match="partial download|reported"):
            android_build.download("https://example.test/tool.zip", dest, offline=False)
    assert not dest.exists()
    assert not dest.with_suffix(".zip.part").exists()


def test_non_executable_java_home_fails_as_build_error_not_permission_error(tmp_path: Path) -> None:
    java_home = tmp_path / "jdk"
    (java_home / "bin").mkdir(parents=True)
    (java_home / "bin" / "java").write_text("", encoding="utf-8")
    (java_home / "bin" / "javac").write_text("", encoding="utf-8")
    with patch("devtool.android_build.subprocess.run", side_effect=PermissionError("denied")):
        with pytest.raises(BuildError, match="Could not determine Java version"):
            android_build.find_java_home(java_home, 17)


def test_malformed_android_env_ints_do_not_crash_parse_args(tmp_path: Path) -> None:
    env = {
        "DEVTOOL_ANDROID_MIN_FREE_MB": "bogus",
        "DEVTOOL_ANDROID_TARGET_FREE_MB": "bogus",
        "DEVTOOL_ANDROID_MANAGED_GRADLE_RETENTION": "bogus",
        "DEVTOOL_GRADLE_LOCK_TIMEOUT_MS": "bogus",
    }
    with patch.dict(os.environ, env, clear=False), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        args = android_build.parse_args([str(tmp_path)])
    assert args.space_min_free_mb == android_build.DEFAULT_ANDROID_SPACE_MIN_FREE_MB
    assert args.space_target_free_mb == android_build.DEFAULT_ANDROID_SPACE_TARGET_FREE_MB
    assert args.managed_gradle_retention == android_build.DEFAULT_ANDROID_MANAGED_GRADLE_RETENTION
    assert args.gradle_lock_timeout_ms == 600000


def test_managed_gradle_offline_rejects_installed_tree_without_archive_sidecars(tmp_path: Path) -> None:
    home = tmp_path / "cache" / "gradle" / "gradle-8.13"
    launcher = home / "bin" / "gradle"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.chmod(0o755)
    (home / ".devtool-gradle.json").write_text(
        json.dumps({"version": "8.13", "archive_sha256": "a" * 64}),
        encoding="utf-8",
    )
    with patch("devtool.android_build.gradle_version", return_value="8.13"):
        with pytest.raises(android_build.BuildError, match="failed version/provenance verification"):
            android_build.managed_gradle("8.13", tmp_path / "cache", offline=True, force=False)


def test_partial_ndk_without_clang_pair_is_not_ready(tmp_path: Path) -> None:
    ndk = tmp_path / "sdk" / "ndk" / "29.0.0"
    (ndk / "toolchains" / "llvm" / "prebuilt" / "linux-x86_64" / "bin").mkdir(parents=True)
    (ndk / "build" / "cmake").mkdir(parents=True)
    (ndk / "build" / "cmake" / "android.toolchain.cmake").write_text("", encoding="utf-8")
    (ndk / "source.properties").write_text("Pkg.Revision = 29.0.0\n", encoding="utf-8")
    assert not android_build.ndk_is_ready(tmp_path / "sdk", "29.0.0")
    (ndk / "toolchains" / "llvm" / "prebuilt" / "linux-x86_64" / "bin" / "clang").write_text("", encoding="utf-8")
    assert not android_build.ndk_is_ready(tmp_path / "sdk", "29.0.0")
    (ndk / "toolchains" / "llvm" / "prebuilt" / "linux-x86_64" / "bin" / "clang++").write_text("", encoding="utf-8")
    assert android_build.ndk_is_ready(tmp_path / "sdk", "29.0.0")


def test_sdk_dry_run_does_not_mutate_local_properties(tmp_path: Path) -> None:
    android_build.update_local_properties(tmp_path, tmp_path / "sdk", dry_run=True)
    assert not (tmp_path / "local.properties").exists()


def test_no_device_adb_probe_result_is_not_cached(tmp_path: Path) -> None:
    adb = tmp_path / "adb"
    adb.write_text("#!/bin/sh\n", encoding="utf-8")
    calls = [
        subprocess.CompletedProcess([str(adb), "devices"], 0, stdout="List of devices attached\n\n", stderr=""),
        subprocess.CompletedProcess([str(adb), "devices"], 0, stdout="List of devices attached\nemu-1\tdevice\n", stderr=""),
    ]
    with patch("devtool.android_build._adb_candidates_for_sdk", return_value=[adb]), patch(
        "devtool.android_build.subprocess.run", side_effect=calls
    ):
        first = android_build.connected_android_devices(tmp_path)
        second = android_build.connected_android_devices(tmp_path)
    assert first[0] == []
    assert second[0] == ["emu-1"]


def test_partial_build_tools_without_d8_jar_is_not_ready(tmp_path: Path) -> None:
    build_tools = tmp_path / "sdk" / "build-tools" / "36.0.0"
    build_tools.mkdir(parents=True)
    (build_tools / "source.properties").write_text("Pkg.Revision = 36.0.0\n", encoding="utf-8")
    assert not android_build.build_tools_is_ready(tmp_path / "sdk", "36.0.0")


def test_version_catalog_android_plugin_alias_does_not_reclassify_jvm_project(tmp_path: Path) -> None:
    (tmp_path / "settings.gradle.kts").write_text('rootProject.name = "jvm"\n', encoding="utf-8")
    (tmp_path / "build.gradle.kts").write_text("plugins { java }\n", encoding="utf-8")
    (tmp_path / "gradle").mkdir()
    (tmp_path / "gradle" / "libs.versions.toml").write_text(
        '[plugins]\nandroid-application = { id = "com.android.application", version = "9.2.1" }\n',
        encoding="utf-8",
    )
    assert detect_project_kind(tmp_path) == "gradle"
    assert not android_build.inspect_project(tmp_path).android_application


def test_deeply_nested_android_module_identity_is_preserved(tmp_path: Path) -> None:
    module = tmp_path / "features" / "phone" / "app"
    (module / "src" / "main").mkdir(parents=True)
    (module / "src" / "main" / "AndroidManifest.xml").write_text("<manifest />", encoding="utf-8")
    (tmp_path / "settings.gradle.kts").write_text('rootProject.name = "deep"\ninclude(":features:phone:app")\n', encoding="utf-8")
    (module / "build.gradle.kts").write_text(
        'plugins { id("com.android.application") version "9.2.1" }\nandroid { compileSdk = 36 }\n',
        encoding="utf-8",
    )
    project = android_build.inspect_project(tmp_path)
    assert project.android
    assert project.android_application
    assert project.compile_sdk == 36
    assert project.agp_major == 9


def test_devtool_directory_does_not_affect_android_inspection(tmp_path: Path) -> None:
    (tmp_path / "settings.gradle.kts").write_text('rootProject.name = "jvm"\n', encoding="utf-8")
    (tmp_path / "build.gradle.kts").write_text("plugins { java }\n", encoding="utf-8")
    shadow = tmp_path / ".devtool" / "shadow" / "src" / "main"
    shadow.mkdir(parents=True)
    (shadow / "AndroidManifest.xml").write_text("<manifest />", encoding="utf-8")
    (tmp_path / ".devtool" / "shadow" / "build.gradle.kts").write_text(
        'plugins { id("com.android.application") version "9.2.1" }\nandroid { compileSdk = 36 }\n',
        encoding="utf-8",
    )
    assert not android_build.inspect_project(tmp_path).android


def test_gradle_prerelease_does_not_match_stable_request() -> None:
    assert not android_build.gradle_version_matches("9.7-rc-1", "9.7")
    assert not android_build.gradle_version_matches("9.7-milestone-1", "9.7")
    assert android_build.gradle_version_matches("9.7.1", "9.7")


def test_bad_gradle_jvm_env_values_do_not_crash_after_argparse(tmp_path: Path) -> None:
    with patch.dict(os.environ, {"DEVTOOL_GRADLE_JVM_HEAP_MB": "bad", "DEVTOOL_GRADLE_METASPACE_MB": "bad"}, clear=False), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        args = android_build.parse_args([str(tmp_path), "--constrained"])
        value = android_build.gradle_jvmargs(args)
    assert "-Xmx1024m" in value


def test_active_sdk_temp_cache_is_not_deleted(tmp_path: Path) -> None:
    temp = tmp_path / "sdk" / ".temp"
    temp.mkdir(parents=True)
    now = time.time()
    os.utime(temp, (now, now))
    android_build.clear_android_download_debris(tmp_path / "sdk", dry_run=False)
    assert temp.exists()


def test_sdk_component_none_is_case_normalized(tmp_path: Path) -> None:
    sdk = tmp_path / "sdk"
    sdk.mkdir()
    project = _project(tmp_path, compile_sdk=36)
    result = android_build.ensure_android_sdk(
        project,
        sdk,
        tmp_path / "cache",
        force_setup=False,
        platform_override="NONE",
        build_tools_override="None",
        ndk_override="NONE",
        cmake_override="none",
        skip_install=True,
        offline=True,
        env={},
        dry_run=True,
    )
    assert result[1:] == (None, None, None, None)
