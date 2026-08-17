from __future__ import annotations

import io
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from devtool.android_build import (
    BuildError,
    JavaRequirement,
    ProjectInfo,
    artifact_variant,
    default_tasks,
    discover_sdk_root,
    discover_sdk_root_with_source,
    download,
    default_build_tools_version,
    ensure_arm64_build_tools_compatible,
    find_java_home,
    inspect_project,
    ensure_android_sdk,
    managed_sdk_root_for_host,
    native_tool_matches_host,
    run_sdkmanager_with_progress,
    main as android_build_main,
    run as android_build_run,
    normalize_gradle_arguments,
    parse_args,
    split_gradle_passthrough_args,
    gradle_jvmargs,
    gradle_arguments_with_aapt2_override,
    should_cleanup_gradle,
    planned_artifact_names,
    select_revanced_asset,
    select_cached_revanced_aapt2,
    select_sdk_build_tools_aapt2,
    maybe_reexec_in_chroot,
    wrapper_distribution_version,
    _critical_memory_pressure as android_critical_memory_pressure,
    _memory_guard_limits as android_memory_guard_limits,
    _try_root_cache_reclaim as android_try_root_cache_reclaim,
)
from devtool.android_integration import (
    android_builder_arguments,
    android_builder_arguments_for_tasks,
    android_doctor_rows,
    android_inspect_rows,
    android_validation_lint_tasks,
    configured_tasks,
    refresh_android_builders_for_specs,
    stage_android_builder,
    stage_gradle_init_script,
    should_prefer_android_chroot,
)
from devtool.config import detect_project_kind, discover_gradle_roots, load_project_config
from devtool.diagnostics import parse_issues
from devtool.models import CommandSpec, ProjectConfig
from devtool.project_runners import AndroidGradleRunner, get_project_runner
from devtool.runners import (
    build_specs,
    build_validation_specs,
    _command_delegates_memory_guard,
    _critical_memory_pressure as runner_critical_memory_pressure,
    _memory_guard_limits as runner_memory_guard_limits,
)


def make_android_project(root: Path, *, application: bool = True) -> None:
    (root / "app" / "src" / "main").mkdir(parents=True)
    (root / "app" / "src" / "main" / "AndroidManifest.xml").write_text("<manifest package='demo'/>", encoding="utf-8")
    (root / "settings.gradle.kts").write_text('rootProject.name = "DemoApp"\ninclude(":app")\n', encoding="utf-8")
    plugin = "com.android.application" if application else "com.android.library"
    (root / "build.gradle.kts").write_text(
        f'''plugins {{ id("{plugin}") version "8.8.0" apply false }}\n''',
        encoding="utf-8",
    )
    (root / "app" / "build.gradle.kts").write_text(
        f'''plugins {{ id("{plugin}") }}\nandroid {{ compileSdk = 35 }}\n''',
        encoding="utf-8",
    )
    (root / "gradle" / "wrapper").mkdir(parents=True)
    (root / "gradle" / "wrapper" / "gradle-wrapper.properties").write_text(
        "distributionUrl=https\\://services.gradle.org/distributions/gradle-8.10.2-bin.zip\n",
        encoding="utf-8",
    )
    (root / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")
    (root / "gradlew").chmod(0o755)


class AndroidProjectInspectionTests(unittest.TestCase):
    def test_detects_android_and_extracts_static_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            project = inspect_project(root)

            self.assertEqual(detect_project_kind(root), "android")
            self.assertTrue(project.android)
            self.assertTrue(project.android_application)
            self.assertEqual(project.compile_sdk, 35)
            self.assertEqual(project.agp_major, 8)
            self.assertEqual(default_tasks(project), ["assembleDebug"])
            self.assertEqual(wrapper_distribution_version(root), "8.10.2")

    def test_detects_compile_sdk_from_version_catalog_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            (root / "gradle" / "libs.versions.toml").parent.mkdir(parents=True, exist_ok=True)
            (root / "gradle" / "libs.versions.toml").write_text(
                '[versions]\nandroid-compile-sdk = "36"\n',
                encoding="utf-8",
            )
            (root / "app" / "build.gradle.kts").write_text(
                'plugins { id("com.android.application") }\n'
                'android { compileSdk = libs.versions.android.compile.sdk.get().toInt() }\n',
                encoding="utf-8",
            )

            project = inspect_project(root)

        self.assertEqual(project.compile_sdk, 36)

    def test_discovers_nested_android_root_without_reporting_modules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            nested = repo / "app" / "XDM.Android"
            make_android_project(nested)
            # A module build file is part of the enclosing Gradle build, not a
            # second project root.
            roots = discover_gradle_roots(repo, android_only=True)
            detected = detect_project_kind(repo)

        self.assertEqual(roots, [nested.resolve()])
        self.assertEqual(detected, "android")

    def test_artifact_naming_is_stable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"apk")
            names = planned_artifact_names([source], root, "DemoApp", None)

        self.assertEqual(names, ["demo-app-debug.apk"])
        self.assertEqual(artifact_variant(source), "debug")

    def test_incompatible_local_properties_sdk_is_skipped_for_host(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bad_sdk = root / "bad-sdk"
            good_sdk = root / "good-sdk"
            bad_tool = bad_sdk / "build-tools" / "36.0.0" / "zipalign"
            good_tool = good_sdk / "build-tools" / "36.0.0" / "zipalign"
            bad_tool.parent.mkdir(parents=True)
            good_tool.parent.mkdir(parents=True)
            # ELF64 x86_64 e_machine = 62.
            bad_tool.write_bytes(b"\x7fELF" + bytes([2, 1, 1]) + bytes(11) + (62).to_bytes(2, "little") + bytes(64))
            # ELF64 AArch64 e_machine = 183.
            good_tool.write_bytes(b"\x7fELF" + bytes([2, 1, 1]) + bytes(11) + (183).to_bytes(2, "little") + bytes(64))
            (root / "local.properties").write_text(f"sdk.dir={bad_sdk}\n", encoding="utf-8")

            def writable(path: Path) -> bool:
                return path.resolve() != bad_sdk.resolve()

            with patch("devtool.android_build.normalized_machine", return_value="arm64"), patch(
                "devtool.android_build.sdk_root_is_writable_for_install",
                side_effect=writable,
            ), patch.dict(os.environ, {"ANDROID_HOME": str(good_sdk)}, clear=False):
                discovered = discover_sdk_root_with_source(None, root)

        self.assertEqual(discovered, (good_sdk.resolve(), "ANDROID_HOME"))

    def test_writable_incompatible_sdk_is_selected_for_automatic_repair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sdk_root = root / "sdk"
            tool = sdk_root / "build-tools" / "36.0.0" / "aidl"
            tool.parent.mkdir(parents=True)
            tool.write_bytes(
                b"\x7fELF"
                + bytes([2, 1, 1])
                + bytes(11)
                + (62).to_bytes(2, "little")
                + bytes(64)
            )
            (root / "local.properties").write_text(
                f"sdk.dir={sdk_root}\n",
                encoding="utf-8",
            )

            with patch("devtool.android_build.normalized_machine", return_value="arm64"), patch(
                "devtool.android_build.sdk_root_is_writable_for_install",
                return_value=True,
            ):
                discovered = discover_sdk_root_with_source(None, root)

        self.assertEqual(discovered, (sdk_root.resolve(), "local.properties sdk.dir"))

    def test_local_properties_sdk_root_is_preferred_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sdk_root = root / "Android" / "Sdk"
            sdk_root.mkdir(parents=True)
            (root / "local.properties").write_text(f"sdk.dir={sdk_root}\n", encoding="utf-8")

            discovered = discover_sdk_root(None, root)
            discovered_with_source = discover_sdk_root_with_source(None, root)

        self.assertEqual(discovered, sdk_root.resolve())
        self.assertEqual(discovered_with_source, (sdk_root.resolve(), "local.properties sdk.dir"))

    def test_download_reports_percent_bytes_and_speed(self) -> None:
        class FakeResponse(io.BytesIO):
            def __init__(self, payload: bytes) -> None:
                super().__init__(payload)
                self.headers = {"Content-Length": str(len(payload))}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                self.close()
                return False

        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "archive.zip"
            payload = b"x" * (512 * 1024)
            output = io.StringIO()
            with patch("devtool.android_build.urllib.request.urlopen", return_value=FakeResponse(payload)), redirect_stdout(output):
                selected = download(
                    "https://example.invalid/archive.zip",
                    destination,
                    offline=False,
                )
            downloaded = destination.read_bytes()

        rendered = output.getvalue()
        self.assertEqual(selected, destination)
        self.assertEqual(downloaded, payload)
        self.assertIn("Download progress:", rendered)
        self.assertIn("100%", rendered)
        self.assertIn("512.0 KiB / 512.0 KiB", rendered)
        self.assertIn("/s", rendered)

    def test_sdkmanager_carriage_return_progress_is_flushed_as_lines(self) -> None:
        script = (
            "import sys; "
            "sys.stdout.write('[== 5% Downloading package]\\r'); sys.stdout.flush(); "
            "sys.stdout.write('[========== 55% Installing package]\\r'); sys.stdout.flush(); "
            "sys.stdout.write('[================ 100% Done]\\r'); sys.stdout.flush()"
        )
        output = io.StringIO()
        with redirect_stdout(output):
            result = run_sdkmanager_with_progress(
                [sys.executable, "-c", script],
                env=os.environ,
                input_text=None,
                dry_run=False,
            )

        rendered = output.getvalue()
        self.assertEqual(result.returncode, 0)
        self.assertIn("SDK installation progress:   5%", rendered)
        self.assertIn("SDK installation progress:  55%", rendered)
        self.assertIn("SDK installation progress: 100%", rendered)

    def test_managed_sdk_uses_architecture_cache_when_default_is_incompatible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_root = Path(tmp)
            default_sdk = cache_root / "android-sdk"
            default_sdk.mkdir()

            with patch("devtool.android_build.sdk_host_compatible", return_value=False), patch(
                "devtool.android_build.normalized_machine", return_value="arm64"
            ):
                selected = managed_sdk_root_for_host(cache_root)

        self.assertEqual(selected, (cache_root / "android-sdk-arm64").resolve())

    def test_compile_sdk_36_defaults_to_build_tools_36(self) -> None:
        project = ProjectInfo(
            root=Path("/tmp/project"),
            name="Demo",
            android=True,
            android_application=True,
            compile_sdk=36,
            build_tools=None,
            agp_major=9,
            native_build=False,
            ndk_version=None,
            cmake_version=None,
        )

        self.assertEqual(default_build_tools_version(project), "36.0.0")

    def test_unknown_compile_sdk_defaults_to_published_commit451_build_tools(self) -> None:
        project = ProjectInfo(
            root=Path("/tmp/project"),
            name="Demo",
            android=True,
            android_application=True,
            compile_sdk=None,
            build_tools=None,
            agp_major=None,
            native_build=False,
            ndk_version=None,
            cmake_version=None,
        )

        self.assertEqual(default_build_tools_version(project), "35.0.1")

    def test_commit451_tools_replace_incompatible_sdk_binaries(self) -> None:
        x86_header = (
            b"\x7fELF"
            + bytes([2, 1, 1])
            + bytes(11)
            + (62).to_bytes(2, "little")
            + bytes(64)
        )
        arm_header = (
            b"\x7fELF"
            + bytes([2, 1, 1])
            + bytes(11)
            + (183).to_bytes(2, "little")
            + bytes(64)
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sdk_root = root / "sdk"
            cache_root = root / "cache"
            build_tools = sdk_root / "build-tools" / "36.0.0"
            build_tools.mkdir(parents=True)
            payloads = {
                name: arm_header + name.encode("ascii")
                for name in ("aidl", "zipalign", "split-select")
            }
            for name in payloads:
                target = build_tools / name
                target.write_bytes(x86_header + name.encode("ascii"))
                target.chmod(0o755)

            manifest = "\n".join(
                f"{hashlib.sha256(payload).hexdigest()}  {name}"
                for name, payload in payloads.items()
            ) + "\n"

            def fake_download(url: str, destination: Path, **kwargs) -> Path:
                destination.parent.mkdir(parents=True, exist_ok=True)
                if url.endswith("/SHA256SUMS"):
                    destination.write_text(manifest, encoding="utf-8")
                else:
                    name = url.rsplit("/", 1)[-1]
                    destination.write_bytes(payloads[name])
                return destination

            output = io.StringIO()
            with patch("devtool.android_build.normalized_machine", return_value="arm64"), patch(
                "devtool.android_build.running_under_termux_python", return_value=False
            ), patch(
                "devtool.android_build.android_build_tool_executes", return_value=True
            ), patch(
                "devtool.android_build.download",
                side_effect=fake_download,
            ), redirect_stdout(output):
                ensure_arm64_build_tools_compatible(
                    sdk_root,
                    "36.0.0",
                    cache_root,
                    provider="auto",
                    offline=False,
                    force=False,
                    dry_run=False,
                )

            for name in payloads:
                self.assertTrue(native_tool_matches_host(build_tools / name, "arm64"))
                self.assertTrue((build_tools / f"{name}.google-x86_64").is_file())
            self.assertIn("Repairing Android Build Tools 36.0.0 for ARM64", output.getvalue())

    def test_commit451_35_0_0_uses_published_35_0_1_release(self) -> None:
        x86_header = (
            b"\x7fELF"
            + bytes([2, 1, 1])
            + bytes(11)
            + (62).to_bytes(2, "little")
            + bytes(64)
        )
        arm_header = (
            b"\x7fELF"
            + bytes([2, 1, 1])
            + bytes(11)
            + (183).to_bytes(2, "little")
            + bytes(64)
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sdk_root = root / "sdk"
            cache_root = root / "cache"
            build_tools = sdk_root / "build-tools" / "35.0.0"
            build_tools.mkdir(parents=True)
            payloads = {
                name: arm_header + name.encode("ascii")
                for name in ("aidl", "zipalign", "split-select")
            }
            for name in payloads:
                target = build_tools / name
                target.write_bytes(x86_header + name.encode("ascii"))
                target.chmod(0o755)

            manifest = "\n".join(
                f"{hashlib.sha256(payload).hexdigest()}  {name}"
                for name, payload in payloads.items()
            ) + "\n"
            urls: list[str] = []

            def fake_download(url: str, destination: Path, **kwargs) -> Path:
                urls.append(url)
                destination.parent.mkdir(parents=True, exist_ok=True)
                if url.endswith("/SHA256SUMS"):
                    destination.write_text(manifest, encoding="utf-8")
                else:
                    name = url.rsplit("/", 1)[-1]
                    destination.write_bytes(payloads[name])
                return destination

            with patch("devtool.android_build.normalized_machine", return_value="arm64"), patch(
                "devtool.android_build.running_under_termux_python", return_value=False
            ), patch(
                "devtool.android_build.android_build_tool_executes", return_value=True
            ), patch(
                "devtool.android_build.download",
                side_effect=fake_download,
            ):
                ensure_arm64_build_tools_compatible(
                    sdk_root,
                    "35.0.0",
                    cache_root,
                    provider="auto",
                    offline=False,
                    force=False,
                    dry_run=False,
                )

            self.assertTrue(urls)
            self.assertTrue(
                all("/platform-tools-35.0.1/" in url for url in urls),
                urls,
            )

    def test_arm_build_tools_provider_can_be_disabled(self) -> None:
        args = parse_args(["--arm-build-tools-provider", "none"])

        self.assertEqual(args.arm_build_tools_provider, "none")

    def test_android_sdk_readiness_runs_arm64_native_tool_repair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_root = root / "project"
            project_root.mkdir()
            sdk_root = root / "sdk"
            (sdk_root / "platforms" / "android-36").mkdir(parents=True)
            (sdk_root / "platforms" / "android-36" / "android.jar").write_bytes(b"jar")
            (sdk_root / "build-tools" / "36.0.0").mkdir(parents=True)
            project = ProjectInfo(
                root=project_root,
                name="Demo",
                android=True,
                android_application=True,
                compile_sdk=36,
                build_tools="36.0.0",
                agp_major=9,
                native_build=False,
                ndk_version=None,
                cmake_version=None,
            )

            with patch(
                "devtool.android_build.ensure_arm64_build_tools_compatible"
            ) as repair:
                ensure_android_sdk(
                    project,
                    sdk_root,
                    root / "cache",
                    force_setup=False,
                    platform_override="auto",
                    build_tools_override="auto",
                    ndk_override="none",
                    cmake_override="none",
                    skip_install=False,
                    offline=False,
                    env={"PATH": "/usr/bin"},
                    dry_run=False,
                    arm_build_tools_provider="auto",
                )

        repair.assert_called_once()
        self.assertEqual(repair.call_args.kwargs["provider"], "auto")

    def test_read_only_implicit_sdk_falls_back_to_managed_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_root = root / "project"
            project_root.mkdir()
            system_sdk = root / "system-sdk"
            system_sdk.mkdir()
            cache_root = root / "cache"
            cache_root.mkdir()
            env = {"PATH": "/usr/bin", "ANDROID_HOME": str(system_sdk)}
            project = ProjectInfo(
                root=project_root,
                name="Demo",
                android=True,
                android_application=True,
                compile_sdk=36,
                build_tools=None,
                agp_major=9,
                native_build=False,
                ndk_version=None,
                cmake_version=None,
            )

            def writable(path: Path) -> bool:
                return path.resolve() != system_sdk.resolve()

            with patch("devtool.android_build.sdk_root_is_writable_for_install", side_effect=writable), patch(
                "devtool.android_build.download"
            ):
                selected, *_ = ensure_android_sdk(
                    project,
                    system_sdk,
                    cache_root,
                    force_setup=False,
                    platform_override="auto",
                    build_tools_override="auto",
                    ndk_override="none",
                    cmake_override="none",
                    skip_install=False,
                    offline=False,
                    env=env,
                    dry_run=True,
                    allow_managed_fallback=True,
                )

        expected = (cache_root / "android-sdk").resolve()
        self.assertEqual(selected, expected)
        self.assertEqual(env["ANDROID_HOME"], str(expected))
        self.assertEqual(env["ANDROID_SDK_ROOT"], str(expected))
        self.assertTrue(env["PATH"].startswith(str(expected / "cmdline-tools" / "latest" / "bin")))

    def test_android_auto_sdk_option_is_threaded_into_builder_and_reporting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="android",
                runner_options={"android": {"auto_sdk": True}},
            )
            with patch.dict(os.environ, {
                "HOME": str(root / "home"),
                "PREFIX": "/usr",
                "DEVTOOL_ANDROID_BUILDER": str(root / "state" / "build-apk"),
                "DEVTOOL_GRADLE_INIT_SCRIPT": str(root / "state" / "events.gradle"),
            }, clear=True):
                args = android_builder_arguments(config, "build")
                inspect_rows = dict(android_inspect_rows(config))
                doctor_rows = dict(android_doctor_rows(config))

        self.assertIn("--auto-sdk", args)
        self.assertEqual(inspect_rows["sdk_install"], "auto")
        self.assertEqual(inspect_rows["auto_sdk"], "yes")
        self.assertEqual(inspect_rows["execution_mode"], "host")
        self.assertEqual(doctor_rows["sdk_install"], "auto")
        self.assertEqual(doctor_rows["execution_mode"], "host")

    def test_chroot_android_defaults_to_no_bootstrap_downloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = ProjectConfig(repo_root=root, project_root=str(root), kind="android")
            termux = {
                "HOME": "/data/data/com.termux/files/home",
                "PREFIX": "/data/data/com.termux/files/usr",
                "TERMUX_VERSION": "0.119",
                "DEVTOOL_ANDROID_BUILDER": str(root / "state" / "build-apk"),
                "DEVTOOL_GRADLE_INIT_SCRIPT": str(root / "state" / "events.gradle"),
            }
            with patch.dict(os.environ, termux, clear=True), patch(
                "devtool.android_integration.shutil.which",
                return_value="/data/data/com.termux/files/usr/bin/chrootctl",
            ):
                args = android_builder_arguments(config, "build")
                inspect_rows = dict(android_inspect_rows(config))

        self.assertIn("--toolchain-downloads", args)
        self.assertEqual(args[args.index("--toolchain-downloads") + 1], "never")
        self.assertEqual(args[args.index("--bootstrap-network") + 1], "deny")
        self.assertEqual(inspect_rows["bootstrap_downloads"], "never")

    def test_android_builder_threads_explicit_toolchain_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="android",
                runner_options={
                    "android": {
                        "sdk_root": "/opt/android-sdk",
                        "java_home": "/usr/lib/jvm/java-21-openjdk-amd64",
                        "gradle": "/opt/gradle/bin/gradle",
                        "gradle_user_home": "/root/.gradle",
                    },
                    "bootstrap": {"downloads": "never", "network": "deny"},
                },
            )
            with patch.dict(os.environ, {
                "DEVTOOL_ANDROID_BUILDER": str(root / "state" / "build-apk"),
                "DEVTOOL_GRADLE_INIT_SCRIPT": str(root / "state" / "events.gradle"),
            }, clear=False):
                args = android_builder_arguments(config, "build")

        self.assertEqual(args[args.index("--sdk-root") + 1], "/opt/android-sdk")
        self.assertEqual(args[args.index("--java-home") + 1], "/usr/lib/jvm/java-21-openjdk-amd64")
        self.assertEqual(args[args.index("--gradle-command") + 1], "/opt/gradle/bin/gradle")
        self.assertEqual(args[args.index("--gradle-user-home") + 1], "/root/.gradle")
        self.assertEqual(args[args.index("--toolchain-downloads") + 1], "never")

    def test_download_policy_never_blocks_managed_sdk_creation(self) -> None:
        project = ProjectInfo(
            root=Path("/tmp/demo"),
            name="demo",
            android=True,
            android_application=True,
            compile_sdk=35,
            build_tools=None,
            agp_major=8,
            native_build=False,
            ndk_version=None,
            cmake_version=None,
        )
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(BuildError, "Download policy: never"):
                ensure_android_sdk(
                    project,
                    None,
                    Path(tmp),
                    force_setup=False,
                    platform_override="auto",
                    build_tools_override="auto",
                    ndk_override="none",
                    cmake_override="none",
                    skip_install=False,
                    offline=False,
                    env={},
                    dry_run=True,
                    bootstrap_downloads="never",
                    bootstrap_network="deny",
                )

    def test_android_chroot_is_preferred_from_termux_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = ProjectConfig(repo_root=root, project_root=str(root), kind="android")

            with patch.dict(os.environ, {"PREFIX": "/data/data/com.termux/files/usr", "HOME": "/data/data/com.termux/files/home"}, clear=False), patch(
                "devtool.android_integration.shutil.which",
                return_value="/data/data/com.termux/files/usr/bin/chrootctl",
            ):
                self.assertTrue(should_prefer_android_chroot(config))

            with patch.dict(os.environ, {"PREFIX": "/data/data/com.termux/files/usr", "HOME": "/data/data/com.termux/files/home"}, clear=False), patch(
                "devtool.android_integration.shutil.which",
                return_value="/data/data/com.termux/files/usr/bin/chrootctl",
            ):
                native_config = ProjectConfig(
                    repo_root=root,
                    project_root=str(root),
                    kind="android",
                    runner_options={"android": {"prefer_native_termux": True}},
                )
                self.assertFalse(should_prefer_android_chroot(native_config))


    def test_android_chroot_is_never_auto_selected_on_desktop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = ProjectConfig(repo_root=root, project_root=str(root), kind="android")
            with patch.dict(os.environ, {"HOME": "/home/mike", "PREFIX": "/usr"}, clear=True), patch(
                "devtool.android_integration.shutil.which",
                return_value="/usr/bin/chrootctl",
            ):
                self.assertFalse(should_prefer_android_chroot(config))

    def test_revanced_asset_selection_prefers_arm_archives(self) -> None:
        release = {
            "assets": [
                {"name": "aapt2-linux-x86_64.zip"},
                {"name": "aapt2-arm64-v8a.zip"},
                {"name": "aapt2-armeabi-v7a.zip"},
            ]
        }

        arm64 = select_revanced_asset(release, "arm64")
        armv7 = select_revanced_asset(release, "armv7")

        self.assertEqual(arm64["name"], "aapt2-arm64-v8a.zip")
        self.assertEqual(armv7["name"], "aapt2-armeabi-v7a.zip")

    def test_maybe_reexec_in_chroot_delegates_termux_host_invocations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            termux_home = repo / "termux-home"
            termux_home.mkdir()
            chroot_script = termux_home / ".cache" / "gradle-build" / "chroot" / "gradle-build.py"
            chroot_script.parent.mkdir(parents=True)
            chroot_script.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            command: list[str] = []

            class Result:
                returncode = 0

            def fake_run(argv, *args, **kwargs):
                command.extend(argv)
                return Result()

            with (
                patch.dict(
                    os.environ,
                    {
                        "HOME": str(termux_home),
                        "PREFIX": "/data/data/com.termux/files/usr",
                        "GRADLE_BUILD_CHROOT_TERMUX_HOME": "/home/alice",
                    },
                    clear=False,
                ),
                patch("devtool.android_build.running_under_termux_python", return_value=True),
                patch("devtool.android_build.chroot_is_available", return_value=(True, "")),
                patch("devtool.android_build.stage_builder_for_chroot", return_value=(chroot_script, Path("/home/alice/.cache/gradle-build/chroot/gradle-build.py"))),
                patch("devtool.android_build.Path.cwd", return_value=termux_home / "Code" / "demo"),
                patch("devtool.android_build.subprocess.run", side_effect=fake_run),
            ):
                rc = maybe_reexec_in_chroot(["--repo", str(termux_home / "Code" / "demo"), "validate"])

        self.assertEqual(rc, 0)
        self.assertEqual(command[:6], ["sudo", "chrootctl", "--name", "androidos", "exec", "--"])
        self.assertEqual(command[6], "zsh")
        self.assertEqual(command[7], "-ilc")
        self.assertIn("GRADLE_BUILD_CHROOT_ACTIVE=1", command[8])
        self.assertIn("/home/alice/Code/demo", command[8])

    def test_gradle_aapt2_notice_is_not_misclassified_as_rust(self) -> None:
        issues = parse_issues(
            Path("/tmp/repo"),
            "lint",
            "WARNING: The option setting 'android.aapt2FromMavenOverride=/tmp/aapt2' is experimental.",
        )

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "GRADLE_INFRA")

    def test_android_validation_lints_only_artifact_affected_modules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            root = repo / "app" / "XDM.Android"
            make_android_project(root)
            for module in ("browser-integration", "persistence", "protocol-test-lab"):
                module_root = root / module
                (module_root / "src" / "main" / "kotlin").mkdir(parents=True)
                (module_root / "build.gradle.kts").write_text(
                    'plugins { id("com.android.library") }\n', encoding="utf-8"
                )
            core_model = root / "core-model"
            (core_model / "src" / "main" / "kotlin").mkdir(parents=True)
            (core_model / "build.gradle.kts").write_text(
                'plugins { kotlin("jvm") }\n', encoding="utf-8"
            )
            config = ProjectConfig(
                repo_root=repo,
                project_root=str(root),
                kind="android",
                runner_options={
                    "validation": {
                        "changed_paths": [
                            "app/XDM.Android/app/src/main/kotlin/Main.kt",
                            "app/XDM.Android/browser-integration/src/main/kotlin/Parser.kt",
                            "app/XDM.Android/persistence/src/main/kotlin/Database.kt",
                            "app/XDM.Android/core-model/src/main/kotlin/Model.kt",
                            "app/XDM.Android/docs/PHASE.md",
                        ]
                    }
                },
            )

            tasks, scope, modules = android_validation_lint_tasks(config)

        self.assertEqual(scope, "affected-modules")
        self.assertEqual(modules, [":app", ":browser-integration", ":persistence"])
        self.assertEqual(tasks, [
            ":app:lintDebug",
            ":browser-integration:lintDebug",
            ":persistence:lintDebug",
        ])
        self.assertNotIn(":protocol-test-lab:lintDebug", tasks)
        self.assertNotIn(":core-model:lintDebug", tasks)

    def test_android_validation_uses_full_lint_for_shared_gradle_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="android",
                runner_options={
                    "validation": {"changed_paths": ["gradle/libs.versions.toml"]}
                },
            )

            tasks, scope, modules = android_validation_lint_tasks(config)

        self.assertEqual(tasks, ["lintDebug"])
        self.assertEqual(scope, "full-gradle-wide-change")
        self.assertEqual(modules, [])

    def test_android_validation_skips_lint_for_docs_only_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="android",
                runner_options={
                    "validation": {"changed_paths": ["docs/PHASE.md", "README.md"]}
                },
            )

            tasks, scope, modules = android_validation_lint_tasks(config)

        self.assertEqual(tasks, [])
        self.assertEqual(scope, "skipped-non-code-changes")
        self.assertEqual(modules, [])

    def test_android_lint_validation_gets_larger_bounded_metaspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="android",
                runner_options={
                    "validation": {
                        "android_safe": True,
                        "gradle_heap_mb": 768,
                        "gradle_metaspace_mb": 384,
                        "memory_guard_mb": 558,
                    }
                },
            )

            lint_args = android_builder_arguments_for_tasks(
                config, ["lintDebug"], scan_artifacts=False, validation=True
            )
            test_args = android_builder_arguments_for_tasks(
                config, [":app:testDebugUnitTest"], scan_artifacts=False, validation=True
            )

        lint_index = lint_args.index("--gradle-metaspace-mb")
        test_index = test_args.index("--gradle-metaspace-mb")
        self.assertEqual(lint_args[lint_index + 1], "640")
        self.assertEqual(test_args[test_index + 1], "384")

    def test_android_packaging_validation_gets_ksp_safe_metaspace_and_fail_fast(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="android",
                runner_options={
                    "validation": {
                        "android_safe": True,
                        "gradle_metaspace_mb": 384,
                    }
                },
            )

            args = android_builder_arguments_for_tasks(
                config, [":app:assembleOfficialDebug"], scan_artifacts=True, validation=True
            )

        metaspace_index = args.index("--gradle-metaspace-mb")
        self.assertEqual(args[metaspace_index + 1], "768")
        self.assertIn("--fail-fast", args)

    def test_combined_android_validation_uses_higher_metaspace_floor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="android",
                runner_options={"validation": {"android_safe": True}},
            )

            args = android_builder_arguments_for_tasks(
                config,
                [":app:assembleOfficialDebug", ":app:testOfficialDebugUnitTest", ":app:lintOfficialDebug"],
                scan_artifacts=True,
                validation=True,
            )

        metaspace_index = args.index("--gradle-metaspace-mb")
        self.assertEqual(args[metaspace_index + 1], "896")

    def test_android_builder_appends_configured_gradle_passthrough_args(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="android",
                runner_options={
                    "gradle": {
                        "structured_events": False,
                        "args": ["-Pxdm.requireAria2Runtime=true"],
                    }
                },
            )

            args = android_builder_arguments_for_tasks(
                config, [":transfer-aria2:verifyAria2Runtime", "assembleDebug"], scan_artifacts=True
            )

        self.assertIn("--", args)
        separator = args.index("--")
        self.assertEqual(args[separator + 1 :], ["-Pxdm.requireAria2Runtime=true"])

    def test_android_validation_can_explicitly_collect_all_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="android",
                runner_options={
                    "validation": {
                        "android_safe": True,
                        "fail_fast": False,
                    }
                },
            )

            args = android_builder_arguments_for_tasks(
                config, ["assembleDebug"], scan_artifacts=True, validation=True
            )

        self.assertNotIn("--fail-fast", args)

    def test_constrained_gradle_options_are_accepted_and_bounded(self) -> None:
        args = parse_args([
            "/tmp/demo",
            "--task",
            ":app:testDebugUnitTest",
            "--no-artifact-scan",
            "--no-daemon",
            "--constrained",
            "--gradle-jvm-heap-mb",
            "799",
            "--memory-guard-mb",
            "558",
            "--cpu-limit",
            "2",
        ])

        value = gradle_jvmargs(args)

        self.assertTrue(should_cleanup_gradle(args))
        self.assertIsNotNone(value)
        self.assertIn("-Xmx799m", value or "")
        self.assertIn("-XX:MaxMetaspaceSize=599m", value or "")
        self.assertIn("-XX:ReservedCodeCacheSize=128m", value or "")
        self.assertIn("-XX:ActiveProcessorCount=2", value or "")

    def test_memory_guard_does_not_shrink_explicit_gradle_heap(self) -> None:
        args = parse_args([
            "/tmp/demo",
            "--task",
            "assembleDebug",
            "--constrained",
            "--gradle-jvm-heap-mb",
            "768",
            "--memory-guard-mb",
            "558",
        ])

        self.assertIn("-Xmx768m", gradle_jvmargs(args) or "")

    def test_android_packaging_validation_uses_d8_heap_floor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="android",
                runner_options={
                    "validation": {
                        "android_safe": True,
                        "gradle_heap_mb": 768,
                        "memory_guard_mb": 558,
                    }
                },
            )
            with patch.dict(os.environ, {
                "HOME": "/data/data/com.termux/files/home",
                "PREFIX": "/data/data/com.termux/files/usr",
                "TERMUX_VERSION": "0.119",
                "DEVTOOL_GRADLE_INIT_SCRIPT": str(root / "state" / "events.gradle"),
            }, clear=True):
                packaging = android_builder_arguments_for_tasks(
                    config, ["assembleDebug"], scan_artifacts=False, validation=True
                )
                tests = android_builder_arguments_for_tasks(
                    config, [":app:testDebugUnitTest"], scan_artifacts=False, validation=True
                )

        packaging_heap = packaging[packaging.index("--gradle-jvm-heap-mb") + 1]
        test_heap = tests[tests.index("--gradle-jvm-heap-mb") + 1]
        self.assertEqual(packaging_heap, "1024")
        self.assertEqual(test_heap, "768")

    def test_android_packaging_heap_floor_is_configurable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="android",
                runner_options={
                    "validation": {
                        "android_safe": True,
                        "gradle_heap_mb": 768,
                        "gradle_packaging_heap_mb": 1280,
                    }
                },
            )
            with patch.dict(os.environ, {
                "HOME": "/data/data/com.termux/files/home",
                "PREFIX": "/data/data/com.termux/files/usr",
                "TERMUX_VERSION": "0.119",
                "DEVTOOL_GRADLE_INIT_SCRIPT": str(root / "state" / "events.gradle"),
            }, clear=True):
                argv = android_builder_arguments_for_tasks(
                    config, ["assembleDebug"], scan_artifacts=False, validation=True
                )

        self.assertEqual(argv[argv.index("--gradle-jvm-heap-mb") + 1], "1280")

    def test_constrained_gradle_command_adds_lock_timeout_and_jvmargs(self) -> None:
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
            with (
                patch("devtool.android_build.maybe_reexec_in_chroot", return_value=None),
                patch("devtool.android_build.resolve_project_root", return_value=root),
                patch("devtool.android_build.inspect_project", return_value=project),
                patch("devtool.android_build.infer_java_requirement", return_value=JavaRequirement(17)),
                patch("devtool.android_build.find_java_home", return_value=java_home),
                patch("devtool.android_build.select_gradle", return_value=gradle),
                patch("devtool.android_build.run", side_effect=fake_run),
                patch("devtool.android_build.gradle_process_pids", return_value=[]),
            ):
                rc = android_build_main([
                    str(root),
                    "--task",
                    "build",
                    "--no-artifact-scan",
                    "--no-daemon",
                    "--constrained",
                    "--gradle-jvm-heap-mb",
                    "799",
                    "--memory-guard-mb",
                    "558",
                    "--cpu-limit",
                    "2",
                ])

        self.assertEqual(rc, 0)
        build_command = next(command for command in commands if "build" in command)
        self.assertTrue(any(item.startswith("-Dorg.gradle.jvmargs=-Xmx") for item in build_command))
        self.assertIn("-Dorg.gradle.cache.internal.lockTimeout=600000", build_command)
        self.assertIn("-Dorg.gradle.vfs.watch=false", build_command)
        self.assertIn("-Dorg.gradle.parallel=false", build_command)
        self.assertIn("-Pkotlin.compiler.execution.strategy=in-process", build_command)


    def test_auto_aapt2_does_not_query_remote_revanced_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sdk = root / "sdk"
            java_home = root / "jdk"
            gradle = root / "gradle"
            commands: list[list[str]] = []
            project = ProjectInfo(
                root=root,
                name="demo",
                android=True,
                android_application=True,
                compile_sdk="36",
                build_tools="36.0.0",
                agp_major=9,
                native_build=False,
                ndk_version=None,
                cmake_version=None,
            )

            def fake_run(argv, *args, **kwargs):
                commands.append([str(value) for value in argv])
                return subprocess.CompletedProcess(commands[-1], 0, "", "")

            with (
                patch("devtool.android_build.maybe_reexec_in_chroot", return_value=None),
                patch("devtool.android_build.resolve_project_root", return_value=root),
                patch("devtool.android_build.inspect_project", return_value=project),
                patch("devtool.android_build.infer_java_requirement", return_value=JavaRequirement(17)),
                patch("devtool.android_build.find_java_home", return_value=java_home),
                patch("devtool.android_build.ensure_android_sdk", return_value=(sdk, "android-36", "36.0.0", None, None)),
                patch("devtool.android_build.running_under_termux_python", return_value=False),
                patch("devtool.android_build.normalized_machine", return_value="arm64"),
                patch("devtool.android_build.select_sdk_build_tools_aapt2", return_value=None),
                patch("devtool.android_build.select_cached_revanced_aapt2", return_value=None),
                patch("devtool.android_build.obtain_revanced_aapt2", side_effect=AssertionError("auto must not query ReVanced metadata")),
                patch("devtool.android_build.select_gradle", return_value=gradle),
                patch("devtool.android_build.run", side_effect=fake_run),
                patch("devtool.android_build.gradle_process_pids", return_value=[]),
            ):
                rc = android_build_main([str(root), "--task", ":app:assembleDebug", "--no-artifact-scan"])

        self.assertEqual(rc, 0)
        build_command = commands[-1]
        self.assertFalse(any("aapt2FromMavenOverride" in item for item in build_command))

    def test_explicit_revanced_aapt2_provider_still_allows_remote_provisioning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sdk = root / "sdk"
            java_home = root / "jdk"
            gradle = root / "gradle"
            aapt2 = root / "aapt2"
            commands: list[list[str]] = []
            project = ProjectInfo(
                root=root,
                name="demo",
                android=True,
                android_application=True,
                compile_sdk="36",
                build_tools="36.0.0",
                agp_major=9,
                native_build=False,
                ndk_version=None,
                cmake_version=None,
            )

            def fake_run(argv, *args, **kwargs):
                commands.append([str(value) for value in argv])
                return subprocess.CompletedProcess(commands[-1], 0, "", "")

            with (
                patch("devtool.android_build.maybe_reexec_in_chroot", return_value=None),
                patch("devtool.android_build.resolve_project_root", return_value=root),
                patch("devtool.android_build.inspect_project", return_value=project),
                patch("devtool.android_build.infer_java_requirement", return_value=JavaRequirement(17)),
                patch("devtool.android_build.find_java_home", return_value=java_home),
                patch("devtool.android_build.ensure_android_sdk", return_value=(sdk, "android-36", "36.0.0", None, None)),
                patch("devtool.android_build.running_under_termux_python", return_value=False),
                patch("devtool.android_build.obtain_revanced_aapt2", return_value=aapt2) as obtain,
                patch("devtool.android_build.select_gradle", return_value=gradle),
                patch("devtool.android_build.run", side_effect=fake_run),
                patch("devtool.android_build.gradle_process_pids", return_value=[]),
            ):
                rc = android_build_main([
                    str(root),
                    "--task",
                    ":app:assembleDebug",
                    "--aapt2-provider",
                    "revanced",
                    "--no-artifact-scan",
                ])

        self.assertEqual(rc, 0)
        obtain.assert_called_once()
        self.assertTrue(any(f"-Pandroid.aapt2FromMavenOverride={aapt2}" == item for item in commands[-1]))

    def test_auto_aapt2_can_use_verified_cached_revanced_without_remote_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            release = root / "cache" / "aapt2" / "revanced" / "v1"
            release.mkdir(parents=True)
            aapt2 = release / "aapt2"
            archive = release / "aapt2-arm64-v8a.zip"
            aapt2.write_bytes(b"binary")
            archive.write_bytes(b"archive")
            metadata = {
                "asset_name": archive.name,
                "archive_sha256": hashlib.sha256(b"archive").hexdigest(),
                "binary_sha256": hashlib.sha256(b"binary").hexdigest(),
            }
            (release / "provenance.json").write_text(__import__("json").dumps(metadata), encoding="utf-8")
            with patch("devtool.android_build.validate_aapt2", return_value=aapt2) as validate:
                selected = select_cached_revanced_aapt2(root / "cache")

        self.assertEqual(selected, aapt2)
        validate.assert_called_once_with(aapt2, dry_run=False)

    def test_sdk_build_tools_aapt2_requires_capability_probe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            aapt2 = root / "sdk" / "build-tools" / "36.0.0" / "aapt2"
            aapt2.parent.mkdir(parents=True)
            aapt2.write_text("#!/bin/sh\n", encoding="utf-8")
            with patch("devtool.android_build.termux_aapt2_supports_platform", return_value=True) as supports:
                selected = select_sdk_build_tools_aapt2(root / "sdk", "36.0.0", "android-36")

        self.assertEqual(selected, aapt2)
        supports.assert_called_once_with(aapt2, root / "sdk", "android-36", dry_run=False)

    def test_custom_aapt2_suppresses_experimental_option_warning(self) -> None:
        arguments = gradle_arguments_with_aapt2_override([], Path("/opt/devtool/aapt2"))

        self.assertIn(
            "-Pandroid.aapt2FromMavenOverride=/opt/devtool/aapt2",
            arguments,
        )
        self.assertIn(
            "-Pandroid.suppressUnsupportedOptionWarnings=android.aapt2FromMavenOverride",
            arguments,
        )
        self.assertEqual(arguments[-1], "--console=plain")

    def test_custom_aapt2_merges_existing_warning_suppressions(self) -> None:
        arguments = gradle_arguments_with_aapt2_override(
            [
                "-Pandroid.suppressUnsupportedOptionWarnings=android.otherExperimentalOption",
                "--console=rich",
            ],
            Path("/opt/devtool/aapt2"),
        )

        suppression = next(
            value
            for value in arguments
            if value.startswith("-Pandroid.suppressUnsupportedOptionWarnings=")
        )
        self.assertEqual(
            suppression,
            "-Pandroid.suppressUnsupportedOptionWarnings="
            "android.otherExperimentalOption,android.aapt2FromMavenOverride",
        )
        self.assertEqual(
            sum(value.startswith("-Pandroid.suppressUnsupportedOptionWarnings=") for value in arguments),
            1,
        )
        self.assertEqual(arguments[-1], "--console=rich")

    def test_default_agp_aapt2_does_not_add_suppression_property(self) -> None:
        arguments = gradle_arguments_with_aapt2_override([], None)

        self.assertFalse(any("aapt2FromMavenOverride" in value for value in arguments))
        self.assertFalse(any("suppressUnsupportedOptionWarnings" in value for value in arguments))

    def test_gradle_build_continues_across_independent_task_failures_by_default(self) -> None:
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
            with (
                patch("devtool.android_build.maybe_reexec_in_chroot", return_value=None),
                patch("devtool.android_build.resolve_project_root", return_value=root),
                patch("devtool.android_build.inspect_project", return_value=project),
                patch("devtool.android_build.infer_java_requirement", return_value=JavaRequirement(17)),
                patch("devtool.android_build.find_java_home", return_value=java_home),
                patch("devtool.android_build.select_gradle", return_value=gradle),
                patch("devtool.android_build.run", side_effect=fake_run),
            ):
                rc = android_build_main([str(root), "--task", "build", "--no-artifact-scan"])

        self.assertEqual(rc, 0)
        build_command = commands[-1]
        self.assertIn("--continue", build_command)
        self.assertIn("--stacktrace", build_command)

    def test_gradle_build_fail_fast_disables_continue(self) -> None:
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
            with (
                patch("devtool.android_build.maybe_reexec_in_chroot", return_value=None),
                patch("devtool.android_build.resolve_project_root", return_value=root),
                patch("devtool.android_build.inspect_project", return_value=project),
                patch("devtool.android_build.infer_java_requirement", return_value=JavaRequirement(17)),
                patch("devtool.android_build.find_java_home", return_value=java_home),
                patch("devtool.android_build.select_gradle", return_value=gradle),
                patch("devtool.android_build.run", side_effect=fake_run),
            ):
                rc = android_build_main([str(root), "--task", "build", "--no-artifact-scan", "--fail-fast"])

        self.assertEqual(rc, 0)
        self.assertNotIn("--continue", commands[-1])


class AndroidConfigAndRunnerTests(unittest.TestCase):
    def test_auto_detected_nested_android_root_is_used_for_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            nested = repo / "app" / "XDM.Android"
            make_android_project(nested)
            config = load_project_config(str(repo), None)

        self.assertEqual(config.kind, "android")
        self.assertEqual(config.project_root, str(nested.resolve()))
        self.assertEqual(config.build_target, str(nested.resolve()))

    def test_named_android_target_overrides_mixed_repo_detection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Desktop.sln").write_text("", encoding="utf-8")
            android = root / "apps" / "android"
            make_android_project(android)
            (root / ".devtool.toml").write_text(
                '''[project]\nkind = "auto"\n\n[targets.mobile]\ntype = "android"\nroot = "apps/android"\n\n[targets.mobile.android]\nvariant = "release"\nprefer_native_termux = true\n\n[targets.mobile.gradle]\nmax_workers = 2\n\n[targets.mobile.tasks]\nbuild = ["assembleRelease"]\n\n[targets.mobile.artifacts]\noutput_directory = "artifacts/android"\n''',
                encoding="utf-8",
            )
            config = load_project_config(str(root), None, target_name="mobile")

        self.assertEqual(config.kind, "android")
        self.assertEqual(config.project_root, str(android.resolve()))
        self.assertEqual(config.runner_options["android"]["variant"], "release")
        self.assertEqual(config.runner_options["gradle"]["max_workers"], 2)
        self.assertEqual(config.runner_options["tasks"]["build"], ["assembleRelease"])
        self.assertIsInstance(get_project_runner(config), AndroidGradleRunner)

    def test_android_platform_auto_is_resolved_before_staged_builder_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            (root / "app" / "build.gradle.kts").write_text(
                'plugins { id("com.android.application") }\nandroid { compileSdk = 36 }\n',
                encoding="utf-8",
            )
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="android",
                runner_options={"android": {"platform": "auto"}},
            )

            args = android_builder_arguments_for_tasks(config, [":app:assembleDebug"], scan_artifacts=False)

        self.assertIn("--android-platform", args)
        self.assertEqual(args[args.index("--android-platform") + 1], "36")
        self.assertNotEqual(args[args.index("--android-platform") + 1], "auto")

    def test_android_platform_auto_resolves_version_catalog_compile_sdk_before_builder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            (root / "gradle" / "libs.versions.toml").parent.mkdir(parents=True, exist_ok=True)
            (root / "gradle" / "libs.versions.toml").write_text(
                '[versions]\nandroid-compile-sdk = { strictly = "36" }\n',
                encoding="utf-8",
            )
            (root / "app" / "build.gradle.kts").write_text(
                'plugins { id("com.android.application") }\n'
                'android { compileSdk = libs.versions.android.compile.sdk.get().toInt() }\n',
                encoding="utf-8",
            )
            config = ProjectConfig(repo_root=root, project_root=str(root), kind="android")

            args = android_builder_arguments_for_tasks(config, [":app:assembleDebug"], scan_artifacts=False)

        self.assertEqual(args[args.index("--android-platform") + 1], "36")

    def test_explicit_android_platform_override_remains_authoritative(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="android",
                runner_options={"android": {"platform": "35"}},
            )

            args = android_builder_arguments_for_tasks(config, [":app:assembleDebug"], scan_artifacts=False)

        self.assertEqual(args[args.index("--android-platform") + 1], "35")

    def test_android_build_and_validation_specs_use_staged_shared_builder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            staged = root / ".state" / "build-apk"
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="android",
                build_target=str(root),
                restore_target=str(root),
                runner_options={
                    "android": {"variant": "debug", "aapt2_provider": "auto"},
                    "gradle": {"max_workers": 2},
                    "tasks": {},
                    "artifacts": {"output_directory": str(root / "artifacts")},
                },
            )
            init_script = root / ".state" / "devtool-events.init.gradle"
            with patch.dict(os.environ, {
                "HOME": str(root / "home"),
                "PREFIX": "/usr",
                "DEVTOOL_ANDROID_BUILDER": str(staged),
                "DEVTOOL_GRADLE_INIT_SCRIPT": str(init_script),
            }, clear=True):
                build = build_specs(config, "build")[0]
                validation = build_validation_specs(config)
                init_script_exists = init_script.is_file()

        self.assertEqual(build.argv[0], "python3")
        self.assertEqual(Path(build.argv[1]), staged)
        self.assertIn("assembleDebug", build.argv)
        self.assertIn("--aapt2-provider", build.argv)
        self.assertIn("--output-dir", build.argv)
        self.assertEqual([spec.name for spec in validation], ["android-validation"])
        combined = validation[0]
        self.assertEqual(combined.metadata["validation_scope"], "android-full")
        self.assertEqual(combined.metadata["report_parsers"], ["gradle-junit", "android-lint"])
        self.assertEqual(combined.metadata["validation_components"], ["build", "test", "lint"])
        self.assertTrue(combined.metadata["contains_tests"])
        self.assertTrue(combined.metadata["coalesced_gradle_validation"])
        self.assertEqual(combined.metadata["progress_parser"], "gradle")
        self.assertIn("assembleDebug", combined.argv)
        self.assertIn("testDebugUnitTest", combined.argv)
        self.assertIn("lintDebug", combined.argv)
        self.assertNotIn("help", combined.argv)
        self.assertIn("--parallel", combined.argv)
        self.assertIn("--build-cache", combined.argv)
        self.assertIn("--daemon-idle-timeout-ms", combined.argv)
        self.assertTrue(init_script_exists)

    def test_android_build_defaults_to_devtool_artifact_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = ProjectConfig(repo_root=root, project_root=str(root), kind="android")
            with patch.dict(os.environ, {"DEVTOOL_GRADLE_INIT_SCRIPT": str(root / "init.gradle")}, clear=False):
                args = android_builder_arguments(config, "build")

        output_index = args.index("--output-dir")
        self.assertEqual(args[output_index + 1], ".devtool/build-artifacts")

    def test_termux_validation_uses_affected_module_lint_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            root = repo / "app" / "XDM.Android"
            make_android_project(root)
            for module in ("browser-integration", "persistence", "protocol-test-lab"):
                module_root = root / module
                (module_root / "src" / "main" / "kotlin").mkdir(parents=True)
                (module_root / "build.gradle.kts").write_text(
                    'plugins { id("com.android.library") }\n', encoding="utf-8"
                )
            core_model = root / "core-model"
            (core_model / "src" / "main" / "kotlin").mkdir(parents=True)
            (core_model / "build.gradle.kts").write_text(
                'plugins { kotlin("jvm") }\n', encoding="utf-8"
            )
            config = ProjectConfig(
                repo_root=repo,
                project_root=str(root),
                kind="android",
                runner_options={
                    "tasks": {
                        "build": ["assembleDebug"],
                        "test": [":app:testDebugUnitTest"],
                        "lint": ["lintDebug"],
                    },
                    "validation": {
                        "changed_paths": [
                            "app/XDM.Android/app/src/main/kotlin/Main.kt",
                            "app/XDM.Android/browser-integration/src/main/kotlin/Parser.kt",
                            "app/XDM.Android/persistence/src/main/kotlin/Database.kt",
                            "app/XDM.Android/core-model/src/main/kotlin/Model.kt",
                        ]
                    },
                },
            )
            with patch.dict(os.environ, {
                "HOME": "/data/data/com.termux/files/home",
                "PREFIX": "/data/data/com.termux/files/usr",
                "TERMUX_VERSION": "0.119",
                "DEVTOOL_ANDROID_BUILDER": str(root / "state" / "build-apk"),
                "DEVTOOL_GRADLE_INIT_SCRIPT": str(root / "state" / "events.gradle"),
            }, clear=True):
                validation = build_validation_specs(config)

        lint = next(spec for spec in validation if spec.metadata["validation_components"] == ["lint"])
        self.assertEqual(lint.metadata["lint_scope"], "affected-modules")
        self.assertEqual(
            lint.metadata["gradle_tasks"],
            [":app:lintDebug", ":browser-integration:lintDebug", ":persistence:lintDebug"],
        )
        self.assertNotIn(":protocol-test-lab:lintDebug", lint.argv)
        self.assertNotIn(":core-model:lintDebug", lint.argv)
        metaspace_index = lint.argv.index("--gradle-metaspace-mb")
        self.assertEqual(lint.argv[metaspace_index + 1], "640")

    def test_termux_android_validation_splits_phases_and_caps_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="android",
                runner_options={
                    "tasks": {
                        "build": ["assembleDebug"],
                        "test": ["testDebugUnitTest"],
                        "lint": ["lintDebug"],
                    }
                },
            )
            with patch.dict(os.environ, {
                "HOME": "/data/data/com.termux/files/home",
                "PREFIX": "/data/data/com.termux/files/usr",
                "TERMUX_VERSION": "0.119",
                "DEVTOOL_ANDROID_BUILDER": str(root / "state" / "build-apk"),
                "DEVTOOL_GRADLE_INIT_SCRIPT": str(root / "state" / "events.gradle"),
            }, clear=True):
                validation = build_validation_specs(config)

        self.assertEqual(
            [spec.name for spec in validation],
            ["android-validation-build", "android-validation-test", "android-validation-lint"],
        )
        self.assertEqual([spec.metadata["validation_phase_total"] for spec in validation], [3, 3, 3])
        self.assertEqual(
            [spec.metadata["validation_phase_name"] for spec in validation],
            ["package", "unit-test", "lint"],
        )
        self.assertEqual(
            [spec.metadata["validation_components"] for spec in validation],
            [["package"], ["unit-test"], ["lint"]],
        )
        self.assertEqual(
            [spec.metadata["validation_resource_profile"] for spec in validation],
            ["android-package", "android-unit-test", "android-lint"],
        )
        for spec in validation:
            worker_index = spec.argv.index("--max-workers")
            self.assertEqual(spec.argv[worker_index + 1], "1")
            self.assertIn("--constrained", spec.argv)
            self.assertIn("--low-priority", spec.argv)
            cpu_index = spec.argv.index("--cpu-limit")
            self.assertEqual(spec.argv[cpu_index + 1], "2")
            self.assertIn("--no-daemon", spec.argv)
            self.assertNotIn("--parallel", spec.argv)
            self.assertIn("--memory-guard-mb", spec.argv)
            self.assertFalse(spec.metadata["coalesced_gradle_validation"])

    def test_split_android_validation_removes_cross_phase_duplicate_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="android",
                runner_options={
                    "validation": {"split_gradle": True},
                    "tasks": {
                        "build": ["assembleDebug", "testDebugUnitTest", "lintDebug"],
                        "test": ["testDebugUnitTest"],
                        "lint": [":lintDebug"],
                    },
                },
            )
            with patch.dict(os.environ, {
                "HOME": "/data/data/com.termux/files/home",
                "PREFIX": "/data/data/com.termux/files/usr",
                "TERMUX_VERSION": "0.119",
                "DEVTOOL_ANDROID_BUILDER": str(root / "state" / "build-apk"),
                "DEVTOOL_GRADLE_INIT_SCRIPT": str(root / "state" / "events.gradle"),
            }, clear=True):
                validation = build_validation_specs(config)

        self.assertEqual(
            [(spec.name, spec.metadata["gradle_tasks"]) for spec in validation],
            [
                ("android-validation-build", ["assembleDebug"]),
                ("android-validation-test", ["testDebugUnitTest"]),
                ("android-validation-lint", [":lintDebug"]),
            ],
        )

    def test_memory_guard_uses_adaptive_thresholds_for_ksp_dips(self) -> None:
        android_sustained, android_hard = android_memory_guard_limits(558)
        runner_sustained, runner_hard = runner_memory_guard_limits(558)

        self.assertEqual((android_sustained, android_hard), (runner_sustained, runner_hard))
        self.assertLess(android_sustained, 450)
        self.assertLess(android_hard, android_sustained)

    def test_memory_guard_allows_450_mib_when_swap_and_psi_are_healthy(self) -> None:
        snapshot = (450, 1024, 2048, 0.0, 0.0)
        history = (470, 466, 462, 458, 454, 452, 451, 450)
        sustained, _ = android_memory_guard_limits(558)

        android_result = android_critical_memory_pressure(snapshot, history, sustained)
        runner_result = runner_critical_memory_pressure(snapshot, history, sustained)

        self.assertEqual(android_result, runner_result)
        self.assertFalse(android_result[0])

    def test_memory_guard_stops_only_with_correlated_pressure_signals(self) -> None:
        snapshot = (340, 20, 2048, 35.0, 2.0)
        history = (460, 445, 430, 415, 400, 380, 360, 340)
        sustained, _ = android_memory_guard_limits(558)

        critical, details = android_critical_memory_pressure(snapshot, history, sustained)

        self.assertTrue(critical)
        self.assertIn("swap", details)
        self.assertIn("PSI", details)

    def test_android_builder_owns_explicit_memory_guard_without_outer_duplicate(self) -> None:
        self.assertTrue(
            _command_delegates_memory_guard(
                ["python3", "build-apk", ".", "--memory-guard-mb", "558"]
            )
        )
        self.assertFalse(_command_delegates_memory_guard(["./gradlew", "test"]))

    def test_root_cache_reclaim_uses_termux_su_without_prompting(self) -> None:
        completed = subprocess.CompletedProcess(["su"], 0)
        with (
            patch("devtool.android_build.os.geteuid", return_value=2000),
            patch(
                "devtool.android_build.shutil.which",
                side_effect=lambda name: "/system/xbin/su" if name == "su" else None,
            ),
            patch("devtool.android_build.subprocess.run", return_value=completed) as run_mock,
        ):
            self.assertTrue(android_try_root_cache_reclaim())

        argv = run_mock.call_args.args[0]
        self.assertEqual(argv[:2], ["/system/xbin/su", "-c"])
        self.assertIn("drop_caches", argv[2])
        self.assertIs(run_mock.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(run_mock.call_args.kwargs["timeout"], 5)

    def test_android_builder_memory_guard_aborts_before_system_pressure_escalates(self) -> None:
        snapshot = (1, 0, 0, 100.0, 100.0)
        with patch("devtool.android_build._memory_snapshot", return_value=snapshot), patch(
            "devtool.android_build.time.sleep", return_value=None
        ):
            result = android_build_run(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                check=False,
                quiet=True,
                memory_guard_mb=512,
            )
        self.assertEqual(result.returncode, 75)

    def test_explicit_android_validation_still_receives_safe_resource_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="android",
                execution_environment="chroot",
                build_command="./gradlew assembleDebug",
                test_command="./gradlew testDebugUnitTest",
            )
            with patch.dict(os.environ, {}, clear=True):
                validation = build_validation_specs(config)

        self.assertEqual([spec.name for spec in validation], ["restore", "build", "test", "lint"])
        self.assertEqual(
            [spec.metadata["validation_resource_profile"] for spec in validation],
            ["android-safe", "android-safe", "android-safe", "android-lint"],
        )
        for spec in validation:
            self.assertEqual(spec.env["DEVTOOL_MAX_WORKERS"], "1")
            self.assertEqual(spec.env["DEVTOOL_VALIDATION_CPU_LIMIT"], "2")
            self.assertIn("-Dorg.gradle.workers.max=1", spec.env["GRADLE_OPTS"])
            self.assertIn("-XX:ActiveProcessorCount=2", spec.env["JAVA_TOOL_OPTIONS"])
            self.assertGreaterEqual(spec.metadata["validation_memory_guard_mb"], 512)

    def test_android_builder_and_gradle_init_default_to_xdg_data_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = ProjectConfig(repo_root=root, project_root=str(root), kind="android")
            xdg = root / "xdg"
            with patch.dict(
                os.environ,
                {
                    "DEVTOOL_XDG_DIR": str(xdg),
                    "DEVTOOL_ANDROID_BUILDER": "",
                    "DEVTOOL_GRADLE_INIT_SCRIPT": "",
                },
                clear=False,
            ):
                build = build_specs(config, "build")[0]
                init_index = build.argv.index("--init-script")
                builder = Path(build.argv[1])
                init_script = Path(build.argv[init_index + 1])
                builder_exists = builder.is_file()
                init_script_exists = init_script.is_file()

        self.assertEqual(builder, xdg / "android" / "build-apk")
        self.assertEqual(init_script, xdg / "gradle" / "devtool-events.init.gradle")
        self.assertTrue(builder_exists)
        self.assertTrue(init_script_exists)

    def test_configured_tasks_override_inference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="android",
                runner_options={"tasks": {"build": [":app:assembleRelease"]}},
            )
            with patch.dict(os.environ, {"DEVTOOL_GRADLE_INIT_SCRIPT": str(root / "init.gradle")}, clear=False):
                args = android_builder_arguments(config, "build")

        self.assertEqual(configured_tasks(config, "build"), [":app:assembleRelease"])
        self.assertIn(":app:assembleRelease", args)

    def test_inspect_and_doctor_rows_are_available_without_running_gradle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            staged = root / "state" / "build-apk"
            config = ProjectConfig(repo_root=root, project_root=str(root), kind="android")
            with patch.dict(os.environ, {
                "DEVTOOL_ANDROID_BUILDER": str(staged),
                "DEVTOOL_GRADLE_INIT_SCRIPT": str(root / "state" / "init.gradle"),
            }, clear=False):
                inspect_rows = dict(android_inspect_rows(config))
                doctor_rows = dict(android_doctor_rows(config))

            self.assertEqual(inspect_rows["project_type"], "Android application")
            self.assertEqual(inspect_rows["compile_sdk"], "35")
            self.assertIn("android_project", doctor_rows)
            self.assertTrue(staged.is_file())

    def test_android_test_and_lint_tasks_are_inferred_from_variant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            staged = root / "state" / "build-apk"
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="android",
                runner_options={"android": {"variant": "release"}},
            )
            init_script = root / "state" / "events.gradle"
            with patch.dict(
                os.environ,
                {
                    "DEVTOOL_ANDROID_BUILDER": str(staged),
                    "DEVTOOL_GRADLE_INIT_SCRIPT": str(init_script),
                },
                clear=False,
            ):
                test_spec = build_specs(config, "test")[0]
                lint_spec = build_specs(config, "lint")[0]

        self.assertIn("testReleaseUnitTest", test_spec.argv)
        self.assertIn("lintRelease", lint_spec.argv)
        self.assertIn("--no-artifact-scan", test_spec.argv)
        self.assertIn("--init-script", test_spec.argv)
        self.assertEqual(test_spec.argv.count("--no-configuration-cache"), 1)
        self.assertEqual(test_spec.metadata["report_parser"], "gradle-junit")
        self.assertEqual(lint_spec.metadata["report_parser"], "android-lint")

    def test_cli_variant_overrides_android_option_for_inferred_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            staged = root / "state" / "build-apk"
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="android",
                variant="release",
                runner_options={"android": {"variant": "debug"}},
            )
            with patch.dict(
                os.environ,
                {
                    "DEVTOOL_ANDROID_BUILDER": str(staged),
                    "DEVTOOL_GRADLE_INIT_SCRIPT": str(root / "state" / "events.gradle"),
                },
                clear=False,
            ):
                build = build_specs(config, "build")[0]
                test = build_specs(config, "test")[0]
                lint = build_specs(config, "lint")[0]

        self.assertIn("assembleRelease", build.argv)
        self.assertIn("testReleaseUnitTest", test.argv)
        self.assertIn("lintRelease", lint.argv)
        self.assertEqual(build.metadata["variant"], "release")
        self.assertEqual(build.env["DEVTOOL_VARIANT"], "release")


    def test_structured_gradle_events_do_not_duplicate_console_option(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="android",
                runner_options={"gradle": {"structured_events": True}},
            )
            with patch.dict(os.environ, {"DEVTOOL_GRADLE_INIT_SCRIPT": str(root / "events.gradle")}, clear=False):
                args = android_builder_arguments(config, "build")

        self.assertIn("--init-script", args)
        self.assertEqual(args.count("--no-configuration-cache"), 1)
        self.assertNotIn("--configuration-cache", args)
        self.assertEqual([value for value in args if str(value).startswith("--console")], [])

    def test_attached_gradle_project_properties_are_passed_through(self) -> None:
        raw, gradle_args = split_gradle_passthrough_args([
            "demo",
            "-Pxdm.requireAria2Runtime=true",
            "--task",
            "assembleDebug",
            "-P",
            "36",
            "--",
            "--info",
        ])

        self.assertEqual(raw, ["demo", "--task", "assembleDebug", "-P", "36"])
        self.assertEqual(gradle_args, ["-Pxdm.requireAria2Runtime=true", "--info"])

        args = parse_args([
            "demo",
            "-Pxdm.requireAria2Runtime=true",
            "--task",
            "assembleDebug",
            "-P",
            "36",
            "--",
            "--info",
        ])
        self.assertEqual(args.android_platform, "36")
        self.assertEqual(args.gradle_args, ["-Pxdm.requireAria2Runtime=true", "--info"])

    def test_gradle_console_arguments_are_normalized_with_last_explicit_value_winning(self) -> None:
        self.assertEqual(
            normalize_gradle_arguments(["--init-script", "/tmp/events.gradle", "--console=plain", "--console", "rich"]),
            ["--init-script", "/tmp/events.gradle", "--console=rich"],
        )
        self.assertEqual(
            normalize_gradle_arguments(["--stacktrace", "--console=plain", "--console=plain"]),
            ["--stacktrace", "--console=plain"],
        )
        self.assertEqual(normalize_gradle_arguments(["--stacktrace"]), ["--stacktrace", "--console=plain"])
        self.assertEqual(normalize_gradle_arguments(["--stacktrace"], plain_console=False), ["--stacktrace"])

    def test_staged_gradle_init_script_is_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "devtool-events.init.gradle"
            staged = stage_gradle_init_script(destination=target)
            text = staged.read_text(encoding="utf-8")

        self.assertEqual(staged, target.resolve())
        self.assertIn("DEVTOOL_EVENT", text)
        self.assertIn("task-graph", text)
        self.assertIn("test-finish", text)

    def test_staged_builder_is_self_contained_and_executable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "build-apk"
            staged = stage_android_builder(destination=target)
            text = staged.read_text(encoding="utf-8")

            self.assertTrue(os.access(staged, os.X_OK))
            self.assertIn("def standalone_main", text)
            self.assertNotIn("from devtool", text)


    def test_android_builder_omits_default_auto_provider_flags_for_stale_chroot_builder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="android",
                build_target=str(root),
                restore_target=str(root),
                runner_options={
                    "android": {
                        "variant": "debug",
                        "java_provider": "auto",
                        "ndk_host_provider": "auto",
                        "qemu_ld_prefix": "auto",
                        "qemu_install": "auto",
                        "native_provider": "auto",
                        "native_cache": "auto",
                    },
                    "gradle": {"provider": "auto"},
                    "tasks": {},
                },
            )

            args = android_builder_arguments_for_tasks(config, [":app:assembleDebug"], scan_artifacts=False)

        omitted = {
            "--java-provider",
            "--ndk-host-provider",
            "--qemu-ld-prefix",
            "--qemu-install",
            "--native-provider",
            "--native-cache",
            "--gradle-provider",
        }
        for flag in omitted:
            self.assertNotIn(flag, args)
        self.assertIn("--aapt2-provider", args)
        self.assertIn("--android-platform", args)
        self.assertIn("--build-tools", args)

    def test_android_builder_preserves_explicit_non_default_provider_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="android",
                build_target=str(root),
                restore_target=str(root),
                runner_options={
                    "android": {
                        "variant": "debug",
                        "java_provider": "system",
                        "ndk_host_provider": "qemu",
                        "qemu_ld_prefix": "none",
                        "qemu_install": "never",
                        "native_provider": "ndk",
                        "native_cache": "ccache",
                    },
                    "gradle": {"provider": "wrapper"},
                    "tasks": {},
                },
            )

            args = android_builder_arguments_for_tasks(config, [":app:assembleDebug"], scan_artifacts=False)

        expected = {
            "--java-provider": "system",
            "--ndk-host-provider": "qemu",
            "--qemu-ld-prefix": "none",
            "--qemu-install": "never",
            "--native-provider": "ndk",
            "--native-cache": "ccache",
            "--gradle-provider": "wrapper",
        }
        for flag, value in expected.items():
            self.assertIn(flag, args)
            self.assertEqual(args[args.index(flag) + 1], value)

    def test_execution_boundary_refreshes_stale_android_builder_parser(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "build-apk"
            target.write_text(
                "#!/usr/bin/env python3\n# legacy parser without bootstrap policy options\n",
                encoding="utf-8",
            )
            spec = CommandSpec(
                "android-validation",
                [
                    "python3",
                    str(target),
                    ".",
                    "--toolchain-downloads",
                    "never",
                    "--bootstrap-network",
                    "deny",
                ],
                metadata={"android_builder": str(target)},
            )

            refresh_android_builders_for_specs([spec])
            refreshed = target.read_text(encoding="utf-8")

        self.assertIn("--toolchain-downloads", refreshed)
        self.assertIn("--bootstrap-network", refreshed)
        self.assertIn("def standalone_main", refreshed)

    def test_staged_builder_refresh_leaves_no_shared_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "build-apk"
            target.write_text("stale\n", encoding="utf-8")

            stage_android_builder(destination=target)

            leftovers = list(root.glob(".build-apk.*.tmp"))

        self.assertEqual(leftovers, [])

    def test_find_java_home_prefers_exact_toolchain_match_over_newer_jdk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            java17 = root / "java-17-openjdk-amd64"
            java21 = root / "java-21-openjdk-amd64"
            for home, version in ((java17, "17.0.19"), (java21, "21.0.11")):
                bin_dir = home / "bin"
                bin_dir.mkdir(parents=True)
                script = (
                    "#!/bin/sh\n"
                    "if [ \"$1\" = \"-version\" ]; then\n"
                    f"  echo 'openjdk version \"{version}\"' 1>&2\n"
                    "  exit 0\n"
                    "fi\n"
                    "exit 0\n"
                )
                for name in ("java", "javac"):
                    path = bin_dir / name
                    path.write_text(script, encoding="utf-8")
                    path.chmod(0o755)

            with patch("devtool.android_build.java_home_candidates", return_value=[java21, java17]):
                selected = find_java_home(None, 17, exact=17)

        self.assertEqual(selected, java17)


if __name__ == "__main__":
    unittest.main()
