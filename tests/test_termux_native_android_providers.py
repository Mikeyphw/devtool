from __future__ import annotations

import io
import subprocess
import urllib.error
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from devtool import android_artifacts, android_build, android_instrumentation
from devtool.android_integration import android_builder_arguments_for_tasks, android_inspect_rows
from devtool.cli import _apply_runner_cli_options, build_parser
from devtool.config import generate_template, minimal_global_config_template
from devtool.models import ProjectConfig
from devtool.project_runners import CMakeRunner
from devtool.toolchains import toolchain_doctor_rows
from devtool.termux_toolchains import (
    PackageProbe,
    RuntimeIdentity,
    VALID_ANDROID_BUILD_TOOLS_PROVIDERS,
    VALID_NATIVE_CACHE_PROVIDERS,
    VALID_NATIVE_PROVIDERS,
    classify_native_requirement,
    normalize_android_build_tools_provider,
    normalize_native_cache_provider,
    normalize_native_provider,
    termux_android_tool_probe,
    termux_android_tool_providers,
    termux_cmake_probe,
    termux_native_provider,
)


def _exe(path: Path, output: str = "1.2.3") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\necho '{output}'\n", encoding="utf-8")
    path.chmod(0o755)


class TermuxNativeProviderTests(unittest.TestCase):
    def test_standalone_and_shared_provider_choices_stay_in_sync(self) -> None:
        self.assertEqual(android_build.NATIVE_PROVIDERS, VALID_NATIVE_PROVIDERS)
        self.assertEqual(android_build.NATIVE_CACHE_PROVIDERS, VALID_NATIVE_CACHE_PROVIDERS)
        self.assertEqual(android_build.ANDROID_BUILD_TOOLS_PROVIDERS, VALID_ANDROID_BUILD_TOOLS_PROVIDERS)

    def test_normalizers_and_aliases(self) -> None:
        self.assertEqual(normalize_native_provider("termux"), "termux-sysroot")
        self.assertEqual(normalize_native_provider("full-ndk"), "ndk")
        self.assertEqual(normalize_native_cache_provider("none"), "off")
        self.assertEqual(normalize_android_build_tools_provider("download"), "commit451")
        with self.assertRaises(ValueError):
            normalize_native_provider("bogus")

    def test_termux_native_provider_requires_sysroot_and_native_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "usr"
            for name in ("clang", "clang++", "ld.lld", "llvm-ar", "llvm-ranlib"):
                _exe(prefix / "bin" / name, "clang version 20.1.8")
            runtime = RuntimeIdentity("android", "termux", "arm64", "bionic", prefix)
            def which(name: str, path: str = ""):
                candidate = prefix / "bin" / name
                return str(candidate) if candidate.exists() else None
            def package(name: str, **_kw):
                return PackageProbe(name, True, "1.0", "dpkg")
            with patch("devtool.termux_toolchains.shutil.which", side_effect=which), patch(
                "devtool.termux_toolchains.termux_package_probe", side_effect=package
            ):
                provider = termux_native_provider(env={"PATH": str(prefix / "bin")}, runtime=runtime)
        self.assertTrue(provider.compatible)
        self.assertEqual(provider.provider, "termux-sysroot")
        self.assertIn("clang++", provider.capabilities)
        self.assertEqual(provider.package, "ndk-sysroot")

    def test_termux_native_provider_rejects_missing_sysroot(self) -> None:
        runtime = RuntimeIdentity("android", "termux", "arm64", "bionic", Path("/prefix"))
        with patch("devtool.termux_toolchains.shutil.which", return_value=None), patch(
            "devtool.termux_toolchains.termux_package_probe",
            return_value=PackageProbe("ndk-sysroot", False, reason="missing"),
        ):
            provider = termux_native_provider(env={"PATH": "/prefix/bin"}, runtime=runtime)
        self.assertFalse(provider.compatible)
        self.assertIn("ndk-sysroot", provider.rejection_reason)

    def test_cmake_probe_is_termux_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "usr"
            cmake = prefix / "bin" / "cmake"
            _exe(cmake, "cmake version 4.4.2")
            runtime = RuntimeIdentity("android", "termux", "arm64", "bionic", prefix)
            with patch("devtool.termux_toolchains.shutil.which", return_value=str(cmake)), patch(
                "devtool.termux_toolchains.termux_package_probe",
                return_value=PackageProbe("cmake", True, "4.4.2", "dpkg"),
            ):
                provider = termux_cmake_probe(env={"PATH": str(prefix / "bin")}, runtime=runtime)
        self.assertTrue(provider.compatible)
        self.assertEqual(provider.version, "4.4.2")

    def test_android_tool_probe_rejects_tool_outside_prefix(self) -> None:
        runtime = RuntimeIdentity("android", "termux", "arm64", "bionic", Path("/prefix"))
        with patch("devtool.termux_toolchains.shutil.which", return_value="/usr/bin/aapt2"):
            provider = termux_android_tool_probe("aapt2", env={"PATH": "/usr/bin"}, runtime=runtime)
        self.assertFalse(provider.available)
        self.assertIn("outside Termux PREFIX", provider.reason)


    def test_unversioned_termux_android_tool_uses_package_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "usr"
            zipalign = prefix / "bin" / "zipalign"
            _exe(zipalign, "usage")
            runtime = RuntimeIdentity("android", "termux", "arm64", "bionic", prefix)
            with patch("devtool.termux_toolchains.shutil.which", return_value=str(zipalign)), patch(
                "devtool.termux_toolchains.termux_package_probe",
                return_value=PackageProbe("aapt", True, "16.0.0.4", "dpkg"),
            ):
                provider = termux_android_tool_probe("zipalign", env={"PATH": str(prefix / "bin")}, runtime=runtime)
        self.assertTrue(provider.compatible)
        self.assertEqual(provider.version, "16.0.0.4")

    def test_termux_android_tool_catalog_includes_build_signing_and_device_tools(self) -> None:
        expected = {"aapt", "aapt2", "apksigner", "adb", "aidl", "zipalign", "split-select"}
        with patch("devtool.termux_toolchains.termux_android_tool_probe") as probe:
            probe.side_effect = lambda name, **_kw: __import__("devtool.termux_toolchains", fromlist=["ToolProvider"]).ToolProvider(
                name, "termux", True, path=Path(f"/prefix/bin/{name}"), version="1", compatible=True
            )
            providers = termux_android_tool_providers()
        self.assertEqual({item.capability for item in providers}, expected)

    def test_existing_android_consumers_can_use_termux_apksigner_and_adb_from_path(self) -> None:
        with patch("devtool.android_artifacts.shutil.which", side_effect=lambda name: "/prefix/bin/apksigner" if name == "apksigner" else None):
            self.assertEqual(android_artifacts._tool_from_sdk("apksigner"), "/prefix/bin/apksigner")
        with patch("devtool.android_instrumentation.shutil.which", return_value="/prefix/bin/adb"):
            self.assertEqual(android_instrumentation._device_prefix("device-1"), ["/prefix/bin/adb", "-s", "device-1"])

    def test_native_requirement_android_cmake_requires_full_ndk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "build.gradle.kts").write_text("android { externalNativeBuild { cmake { path = file(\"CMakeLists.txt\") } } }", encoding="utf-8")
            (root / "CMakeLists.txt").write_text("project(x)", encoding="utf-8")
            requirement = classify_native_requirement(root, android=True)
        self.assertTrue(requirement.requires_full_ndk)

    def test_native_requirement_plain_cmake_is_compiler_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "CMakeLists.txt").write_text("project(x)", encoding="utf-8")
            requirement = classify_native_requirement(root, android=False)
        self.assertEqual(requirement.capability, "compiler")

    def test_android_repo_with_unwired_cmake_is_not_forced_to_full_ndk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "build.gradle.kts").write_text("plugins { id(\"com.android.application\") }", encoding="utf-8")
            (root / "tools").mkdir()
            (root / "tools" / "CMakeLists.txt").write_text("project(host_helper)", encoding="utf-8")
            requirement = classify_native_requirement(root, android=True)
        self.assertEqual(requirement.capability, "compiler")

    def test_android_mk_requires_full_ndk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "build.gradle").write_text("apply plugin: 'com.android.application'", encoding="utf-8")
            (root / "jni").mkdir()
            (root / "jni" / "Android.mk").write_text("LOCAL_PATH := $(call my-dir)", encoding="utf-8")
            requirement = classify_native_requirement(root, android=True)
        self.assertTrue(requirement.requires_full_ndk)


class AndroidNativeProviderTests(unittest.TestCase):
    def _project(self, root: Path, *, native: bool) -> android_build.ProjectInfo:
        return android_build.ProjectInfo(root, "demo", True, True, 35, "35.0.0", 8, native, "28.2.13676358" if native else None, "3.22.1" if native else None)

    def test_full_android_native_build_rejects_termux_sysroot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(android_build.BuildError, "complete Android NDK"):
                android_build.select_native_provider(self._project(Path(tmp), native=True), "termux-sysroot")

    def test_full_android_native_build_auto_selects_ndk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(android_build.select_native_provider(self._project(Path(tmp), native=True), "auto"), "ndk")

    def test_termux_sysroot_can_satisfy_non_ndk_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch(
            "devtool.android_build.termux_ndk_sysroot_ready", return_value=(True, "ready")
        ):
            self.assertEqual(android_build.select_native_provider(self._project(Path(tmp), native=False), "termux-sysroot"), "termux-sysroot")

    def test_native_cache_uses_ccache_when_available(self) -> None:
        env: dict[str, str] = {}
        with patch("devtool.android_build.running_under_termux_python", return_value=True), patch(
            "devtool.android_build.termux_tool_path", return_value=Path("/prefix/bin/ccache")
        ):
            selected = android_build.configure_native_cache(env, "auto")
        self.assertEqual(selected, "ccache")
        self.assertEqual(env["CMAKE_C_COMPILER_LAUNCHER"], "/prefix/bin/ccache")

    def test_termux_build_tools_precede_commit451_in_auto_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sdk = Path(tmp) / "sdk"
            bt = sdk / "build-tools" / "35.0.0"
            bt.mkdir(parents=True)
            for name in android_build.ARM_BUILD_TOOLS_NATIVE_NAMES:
                _exe(bt / name)
            source = Path(tmp) / "zipalign"
            _exe(source)
            with patch("devtool.android_build.normalized_machine", return_value="arm64"), patch(
                "devtool.android_build.arm64_build_tools_replacements_needed", return_value=["zipalign"]
            ), patch("devtool.android_build.sdk_root_is_writable_for_install", return_value=True), patch(
                "devtool.android_build.running_under_termux_python", return_value=True
            ), patch("devtool.android_build.termux_tool_path", return_value=source), patch(
                "devtool.android_build.native_tool_matches_host", return_value=True
            ), patch("devtool.android_build.obtain_commit451_arm64_tool") as download:
                android_build.ensure_arm64_build_tools_compatible(sdk, "35.0.0", Path(tmp) / "cache", provider="auto", offline=True, force=False, dry_run=False)
            download.assert_not_called()

    def test_termux_build_tools_provider_fails_when_tool_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sdk = Path(tmp) / "sdk"
            (sdk / "build-tools" / "35.0.0").mkdir(parents=True)
            with patch("devtool.android_build.normalized_machine", return_value="arm64"), patch(
                "devtool.android_build.arm64_build_tools_replacements_needed", return_value=["aidl"]
            ), patch("devtool.android_build.sdk_root_is_writable_for_install", return_value=True), patch(
                "devtool.android_build.running_under_termux_python", return_value=True
            ), patch("devtool.android_build.termux_tool_path", return_value=None):
                with self.assertRaisesRegex(android_build.BuildError, "Termux build-tools provider requested"):
                    android_build.ensure_arm64_build_tools_compatible(sdk, "35.0.0", Path(tmp) / "cache", provider="termux", offline=True, force=False, dry_run=False)

    def test_native_termux_never_falls_back_to_glibc_commit451(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sdk = Path(tmp) / "sdk"
            (sdk / "build-tools" / "35.0.0").mkdir(parents=True)
            with patch("devtool.android_build.normalized_machine", return_value="arm64"), patch(
                "devtool.android_build.arm64_build_tools_replacements_needed", return_value=["aidl"]
            ), patch("devtool.android_build.sdk_root_is_writable_for_install", return_value=True), patch(
                "devtool.android_build.running_under_termux_python", return_value=True
            ), patch("devtool.android_build.termux_tool_path", return_value=None), patch(
                "devtool.android_build.obtain_commit451_arm64_tool"
            ) as download:
                with self.assertRaisesRegex(android_build.BuildError, "linux-glibc-arm64"):
                    android_build.ensure_arm64_build_tools_compatible(sdk, "35.0.0", Path(tmp) / "cache", provider="auto", offline=True, force=False, dry_run=False)
            download.assert_not_called()

    def test_glibc_arm64_host_can_fall_back_to_commit451(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sdk = Path(tmp) / "sdk"
            (sdk / "build-tools" / "35.0.0").mkdir(parents=True)
            downloaded = Path(tmp) / "aidl"
            _exe(downloaded)
            with patch("devtool.android_build.normalized_machine", return_value="arm64"), patch(
                "devtool.android_build.arm64_build_tools_replacements_needed", return_value=["aidl"]
            ), patch("devtool.android_build.sdk_root_is_writable_for_install", return_value=True), patch(
                "devtool.android_build.running_under_termux_python", return_value=False
            ), patch("devtool.android_build.obtain_commit451_arm64_tool", return_value=downloaded) as download:
                android_build.ensure_arm64_build_tools_compatible(sdk, "35.0.0", Path(tmp) / "cache", provider="auto", offline=True, force=False, dry_run=False)
            download.assert_called_once()

    def test_termux_runtime_probe_rejects_arch_matching_tool_that_cannot_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tool = Path(tmp) / "aidl"
            _exe(tool)
            with patch("devtool.android_build.native_tool_matches_host", return_value=True), patch(
                "devtool.android_build.running_under_termux_python", return_value=True
            ), patch("devtool.android_build.android_build_tool_executes", return_value=False):
                self.assertFalse(android_build.native_android_build_tool_compatible(tool, "aidl", "arm64"))

    def test_commit451_is_rejected_directly_on_native_termux(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch(
            "devtool.android_build.normalized_machine", return_value="arm64"
        ), patch("devtool.android_build.running_under_termux_python", return_value=True), patch(
            "devtool.android_build.urllib.request.urlopen"
        ) as urlopen:
            with self.assertRaisesRegex(android_build.BuildError, "Termux/Bionic"):
                android_build.obtain_commit451_arm64_tool(Path(tmp), "35.0.1", "aidl", offline=False, force=False, dry_run=False)
            urlopen.assert_not_called()

    def test_commit451_unpublished_release_fails_before_asset_download(self) -> None:
        error = urllib.error.HTTPError("https://example.invalid", 404, "Not Found", {}, None)
        with tempfile.TemporaryDirectory() as tmp, patch(
            "devtool.android_build.urllib.request.urlopen", side_effect=error
        ):
            with self.assertRaisesRegex(android_build.BuildError, "does not publish"):
                android_build.resolve_commit451_release_version(Path(tmp), "34.0.0", offline=False)

    def test_commit451_future_release_can_be_resolved_from_github_metadata(self) -> None:
        payload = io.BytesIO(b'{"tag_name":"platform-tools-38.0.0"}')
        with tempfile.TemporaryDirectory() as tmp, patch(
            "devtool.android_build.urllib.request.urlopen", return_value=payload
        ):
            resolved = android_build.resolve_commit451_release_version(Path(tmp), "38.0.0", offline=False)
        self.assertEqual(resolved, "38.0.0")

    def test_inspect_project_does_not_treat_unwired_cmake_as_android_native_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "build.gradle.kts").write_text(
                'plugins { id("com.android.application") version "8.8.0" }\nandroid { compileSdk = 35 }',
                encoding="utf-8",
            )
            (root / "tools").mkdir()
            (root / "tools" / "CMakeLists.txt").write_text("project(host_helper)", encoding="utf-8")
            project = android_build.inspect_project(root)
        self.assertFalse(project.native_build)

    def test_inspect_project_ndk_version_requires_full_ndk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "build.gradle.kts").write_text(
                'plugins { id("com.android.application") version "8.8.0" }\nandroid { compileSdk = 35; ndkVersion = "28.2.13676358" }',
                encoding="utf-8",
            )
            project = android_build.inspect_project(root)
        self.assertTrue(project.native_build)
        self.assertEqual(android_build.native_requirement(project), "android-ndk")

    def test_explicit_termux_aapt2_provider_fails_when_missing(self) -> None:
        with patch("devtool.android_build.termux_aapt2_path", return_value=None):
            with self.assertRaisesRegex(android_build.BuildError, "Termux AAPT2 provider requested"):
                android_build.select_termux_aapt2(Path("/sdk"), "android-35", required=True)

    def test_sdk_build_tools_provider_refuses_incompatible_native_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sdk = Path(tmp) / "sdk"
            (sdk / "build-tools" / "35.0.0").mkdir(parents=True)
            with patch("devtool.android_build.normalized_machine", return_value="arm64"), patch(
                "devtool.android_build.arm64_build_tools_replacements_needed", return_value=["aidl"]
            ), patch("devtool.android_build.sdk_root_is_writable_for_install", return_value=True):
                with self.assertRaisesRegex(android_build.BuildError, "incompatible"):
                    android_build.ensure_arm64_build_tools_compatible(sdk, "35.0.0", Path(tmp) / "cache", provider="sdk", offline=True, force=False, dry_run=False)

    def test_aapt2_capability_probe_rejects_failed_link(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sdk = Path(tmp) / "sdk"
            jar = sdk / "platforms" / "android-35" / "android.jar"
            jar.parent.mkdir(parents=True)
            jar.write_bytes(b"jar")
            aapt2 = Path(tmp) / "aapt2"
            _exe(aapt2)
            results = [subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 1)]
            with patch("devtool.android_build.subprocess.run", side_effect=results):
                self.assertFalse(android_build.termux_aapt2_supports_platform(aapt2, sdk, "android-35"))

    def test_aapt2_auto_can_select_termux_after_capability_probe(self) -> None:
        with patch("devtool.android_build.termux_aapt2_path", return_value=Path("/prefix/bin/aapt2")), patch(
            "devtool.android_build.termux_aapt2_supports_platform", return_value=True
        ):
            selected = android_build.select_termux_aapt2(Path("/sdk"), "android-35", required=False)
        self.assertEqual(selected, Path("/prefix/bin/aapt2"))


class IntegrationConfigTests(unittest.TestCase):
    def test_android_builder_forwards_nested_native_and_build_tools_config(self) -> None:
        config = ProjectConfig(
            repo_root=Path("/tmp/demo"), kind="android", project_root="/tmp/demo",
            runner_options={
                "android": {
                    "native": {"provider": "termux-sysroot", "cache": "ccache"},
                    "build_tools": {"version": "35.0.0", "provider": "termux", "aapt2_provider": "termux"},
                }
            },
        )
        argv = android_builder_arguments_for_tasks(config, ["assembleDebug"], scan_artifacts=False)
        self.assertIn("--native-provider", argv)
        self.assertIn("termux-sysroot", argv)
        self.assertIn("--native-cache", argv)
        self.assertIn("ccache", argv)
        self.assertIn("--arm-build-tools-provider", argv)
        self.assertIn("--aapt2-provider", argv)

    def test_cli_overrides_native_and_build_tools_providers(self) -> None:
        parser = build_parser()
        args = parser.parse_args([
            "--native-provider", "termux-sysroot", "--native-cache", "ccache",
            "--android-build-tools-provider", "termux", "--aapt2-provider", "termux", "inspect"
        ])
        config = ProjectConfig(repo_root=Path("/tmp/demo"), kind="android", runner_options={})
        _apply_runner_cli_options(config, args)
        self.assertEqual(config.runner_options["android"]["native"]["provider"], "termux-sysroot")
        self.assertEqual(config.runner_options["android"]["native"]["cache"], "ccache")
        self.assertEqual(config.runner_options["android"]["build_tools"]["provider"], "termux")
        self.assertEqual(config.runner_options["android"]["build_tools"]["aapt2_provider"], "termux")

    def test_cmake_runner_uses_termux_cmake_ninja_and_ccache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "CMakeLists.txt").write_text("project(x)", encoding="utf-8")
            config = ProjectConfig(repo_root=root, kind="cmake", runner_options={"cmake": {"provider": "termux", "cache": "ccache"}})
            runtime = RuntimeIdentity("android", "termux", "arm64", "bionic", Path("/prefix"))
            provider = lambda cap, path: __import__("devtool.termux_toolchains", fromlist=["ToolProvider"]).ToolProvider(cap, "termux", True, path=Path(path), version="1", runtime=runtime, compatible=True)
            with patch("devtool.project_runners.detect_current_environment", return_value="native-termux"), patch(
                "devtool.project_runners.resolve_build_policy"
            ) as policy, patch("devtool.project_runners.detect_runtime_identity", return_value=runtime), patch(
                "devtool.project_runners.termux_cmake_probe", return_value=provider("cmake", "/prefix/bin/cmake")
            ), patch("devtool.project_runners.termux_ninja_probe", return_value=provider("ninja", "/prefix/bin/ninja")), patch(
                "devtool.project_runners.termux_ccache_probe", return_value=provider("ccache", "/prefix/bin/ccache")
            ), patch("devtool.project_runners.termux_native_provider", return_value=provider("native-compiler", "/prefix/bin/clang")):
                policy.return_value.environment = "native-termux"
                spec = CMakeRunner().build_specs(config, "restore")[0]
        self.assertEqual(spec.argv[0], "/prefix/bin/cmake")
        self.assertIn("-G", spec.argv)
        self.assertIn("Ninja", spec.argv)
        self.assertIn("-DCMAKE_C_COMPILER=/prefix/bin/clang", spec.argv)
        self.assertIn("-DCMAKE_CXX_COMPILER=/prefix/bin/clang++", spec.argv)
        self.assertIn("-DCMAKE_C_COMPILER_LAUNCHER=/prefix/bin/ccache", spec.argv)

    def test_cmake_termux_provider_is_used_for_build_test_and_package_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            prefix = Path(tmp) / "prefix"
            root.mkdir()
            (root / "CMakeLists.txt").write_text("project(x)", encoding="utf-8")
            build = root / ".devtool" / "build" / "cmake"
            build.mkdir(parents=True)
            (build / "CMakeCache.txt").write_text("configured", encoding="utf-8")
            for tool in ("cmake", "ctest", "cpack"):
                _exe(prefix / "bin" / tool, "1.0")
            config = ProjectConfig(repo_root=root, kind="cmake", runner_options={"cmake": {"provider": "termux", "native_provider": "system", "cache": "off"}})
            runtime = RuntimeIdentity("android", "termux", "arm64", "bionic", prefix)
            cmake = __import__("devtool.termux_toolchains", fromlist=["ToolProvider"]).ToolProvider(
                "cmake", "termux", True, path=prefix / "bin" / "cmake", version="4.4.2", runtime=runtime, compatible=True
            )
            with patch("devtool.project_runners.detect_current_environment", return_value="native-termux"), patch(
                "devtool.project_runners.resolve_build_policy"
            ) as policy, patch("devtool.project_runners.detect_runtime_identity", return_value=runtime), patch(
                "devtool.project_runners.termux_cmake_probe", return_value=cmake
            ):
                policy.return_value.environment = "native-termux"
                runner = CMakeRunner()
                build_spec = runner.build_specs(config, "build")[0]
                test_spec = runner.build_specs(config, "test")[0]
                package_spec = runner.build_specs(config, "package")[0]
        self.assertEqual(build_spec.argv[0], str(prefix / "bin" / "cmake"))
        self.assertEqual(test_spec.argv[0], str(prefix / "bin" / "ctest"))
        self.assertEqual(package_spec.argv[0], str(prefix / "bin" / "cpack"))

    def test_explicit_termux_cmake_rejects_non_executable_ctest_sibling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            prefix = Path(tmp) / "prefix"
            root.mkdir()
            (root / "CMakeLists.txt").write_text("project(x)", encoding="utf-8")
            build = root / ".devtool" / "build" / "cmake"
            build.mkdir(parents=True)
            (build / "CMakeCache.txt").write_text("configured", encoding="utf-8")
            _exe(prefix / "bin" / "cmake", "cmake version 4.4.2")
            ctest = prefix / "bin" / "ctest"
            ctest.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            ctest.chmod(0o644)
            config = ProjectConfig(repo_root=root, kind="cmake", runner_options={"cmake": {"provider": "termux", "native_provider": "system", "cache": "off"}})
            runtime = RuntimeIdentity("android", "termux", "arm64", "bionic", prefix)
            provider_cls = __import__("devtool.termux_toolchains", fromlist=["ToolProvider"]).ToolProvider
            cmake = provider_cls("cmake", "termux", True, path=prefix / "bin" / "cmake", version="4.4.2", runtime=runtime, compatible=True)
            with patch("devtool.project_runners.detect_current_environment", return_value="native-termux"), patch(
                "devtool.project_runners.resolve_build_policy"
            ) as policy, patch("devtool.project_runners.detect_runtime_identity", return_value=runtime), patch(
                "devtool.project_runners.termux_cmake_probe", return_value=cmake
            ):
                policy.return_value.environment = "native-termux"
                with self.assertRaisesRegex(SystemExit, "executable ctest is unavailable"):
                    CMakeRunner().build_specs(config, "test")

    def test_config_templates_expose_phase_two_and_three_providers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "CMakeLists.txt").write_text("project(x)", encoding="utf-8")
            text = generate_template(root)
        self.assertIn('native_provider = "auto" # auto | termux-sysroot | system', text)
        self.assertIn('[android.native]', minimal_global_config_template())
        self.assertIn('[android.build_tools]', minimal_global_config_template())

    def test_toolchain_doctor_reports_termux_native_and_android_tools(self) -> None:
        config = ProjectConfig(repo_root=Path("/tmp/demo"), kind="python", project_root="/tmp/demo")
        runtime = RuntimeIdentity("android", "termux", "arm64", "bionic", Path("/prefix"), 36)
        provider_cls = __import__("devtool.termux_toolchains", fromlist=["ToolProvider"]).ToolProvider
        ready = lambda cap, path: provider_cls(cap, "termux", True, path=Path(path), version="1.0", runtime=runtime, compatible=True)
        with patch("devtool.toolchains.detect_runtime_identity", return_value=runtime), patch(
            "devtool.toolchains.termux_package_probe", return_value=PackageProbe("gradle", False)
        ), patch("devtool.toolchains.termux_gradle_probe", return_value=provider_cls("gradle", "termux", False, runtime=runtime, compatible=False)), patch(
            "devtool.toolchains.termux_installed_packages", return_value=[]
        ), patch("devtool.toolchains.termux_java_providers", return_value=[]), patch(
            "devtool.toolchains.termux_native_provider", return_value=ready("native-compiler", "/prefix/bin/clang")
        ), patch("devtool.toolchains.termux_cmake_probe", return_value=ready("cmake", "/prefix/bin/cmake")), patch(
            "devtool.toolchains.termux_ninja_probe", return_value=ready("ninja", "/prefix/bin/ninja")
        ), patch("devtool.toolchains.termux_ccache_probe", return_value=ready("ccache", "/prefix/bin/ccache")), patch(
            "devtool.toolchains.termux_android_tool_providers", return_value=(ready("aapt2", "/prefix/bin/aapt2"),)
        ):
            rows = dict(toolchain_doctor_rows(config, env={"PATH": ""}))
        self.assertIn("/prefix/bin/clang", rows["termux_native_provider"])
        self.assertIn("aapt2=1.0", rows["termux_android_tools"])

    def test_android_inspect_reports_configured_native_and_build_tool_providers(self) -> None:
        config = ProjectConfig(
            repo_root=Path("/tmp/demo"), kind="android", project_root="/tmp/demo",
            runner_options={"android": {"native": {"provider": "ndk", "cache": "off"}, "build_tools": {"provider": "termux", "aapt2_provider": "termux"}}},
        )
        fake = android_build.ProjectInfo(Path("/tmp/demo"), "demo", True, True, 35, "35.0.1", 8, False, None, None)
        with patch("devtool.android_integration.inspect_android_project", return_value=fake), patch(
            "devtool.android_integration.android_build.wrapper_distribution_version", return_value="8.13"
        ), patch("devtool.android_integration.android_build.infer_java_requirement") as req, patch(
            "devtool.android_integration.android_build.discover_sdk_root_with_source", return_value=(None, None)
        ), patch("devtool.android_integration.configured_tasks", return_value=[]), patch(
            "devtool.android_integration.stage_gradle_init_script", return_value=Path("/tmp/init.gradle")
        ):
            req.return_value.minimum = 17
            req.return_value.exact = None
            rows = dict(android_inspect_rows(config))
        self.assertEqual(rows["native_provider"], "ndk")
        self.assertEqual(rows["native_cache"], "off")
        self.assertEqual(rows["android_build_tools_provider"], "termux")
        self.assertEqual(rows["aapt2_provider"], "termux")


if __name__ == "__main__":
    unittest.main()
