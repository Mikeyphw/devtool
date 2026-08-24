from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from devtool.cli import _doctor, build_parser, main
from devtool.config import generate_template, minimal_global_config_template
from devtool.models import ProjectConfig
from devtool.log_commands import _all_log_paths, _candidate_state_roots, _phone_log_mirror_dir
from devtool.proot_distro_runner import ProotRunnerConfig
from devtool.termux_chroot_runner import HOST_ONLY_COMMANDS
from devtool.termux_diagnostics import (
    PackageRecommendation,
    _android_termux_doctor_rows,
    _preferred_termux_openjdk_package,
    _proot_guest_probe_script,
    package_recommendation_rows,
    provider_explanation_rows,
    proot_guest_probe,
    termux_integration_doctor_rows,
    termux_package_recommendations,
    termux_report_payload,
)
from devtool.termux_toolchains import RuntimeIdentity, ToolProvider


def _config(root: Path, *, kind: str = "python", options: dict | None = None) -> ProjectConfig:
    return ProjectConfig(
        repo_root=root,
        kind=kind,
        project_root=str(root),
        selected_target="devtool",
        runner_options=options or {},
    )


def _termux_runtime(prefix: Path) -> RuntimeIdentity:
    return RuntimeIdentity("android", "termux", "arm64", "bionic", prefix, 36)


class TermuxFinalDiagnosticsTests(unittest.TestCase):
    def test_provider_explanation_prefers_complete_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrapper = root / "gradle" / "wrapper"
            wrapper.mkdir(parents=True)
            (root / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")
            (wrapper / "gradle-wrapper.properties").write_text("distributionUrl=x\n", encoding="utf-8")
            (wrapper / "gradle-wrapper.jar").write_bytes(b"jar")
            config = _config(root)
            with patch("devtool.termux_diagnostics.detect_runtime_identity", return_value=_termux_runtime(root / "usr")), patch(
                "devtool.termux_diagnostics.termux_gradle_probe",
                return_value=ToolProvider("gradle", "termux", True, path=root / "usr/bin/gradle", version="9.7.0", compatible=True),
            ), patch("devtool.termux_diagnostics.termux_java_providers", return_value=()), patch(
                "devtool.termux_diagnostics.termux_native_provider",
                return_value=ToolProvider("native-compiler", "termux-sysroot", False, compatible=False),
            ):
                rows = dict(provider_explanation_rows(config))
        self.assertIn("wrapper selected/preferred", rows["provider_explain_gradle"])

    def test_provider_explanation_rejects_sysroot_for_android_ndk_requirement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "build.gradle.kts").write_text("android { ndkVersion = \"28.0.0\"; externalNativeBuild { cmake { path = file(\"CMakeLists.txt\") } } }", encoding="utf-8")
            (root / "CMakeLists.txt").write_text("project(native)\n", encoding="utf-8")
            config = _config(root, kind="android")
            with patch("devtool.termux_diagnostics.detect_runtime_identity", return_value=_termux_runtime(root / "usr")), patch(
                "devtool.termux_diagnostics.termux_gradle_probe",
                return_value=ToolProvider("gradle", "termux", False, compatible=False),
            ), patch("devtool.termux_diagnostics.termux_java_providers", return_value=()), patch(
                "devtool.termux_diagnostics.termux_native_provider",
                return_value=ToolProvider("native-compiler", "termux-sysroot", True, path=root / "clang", compatible=True),
            ):
                rows = dict(provider_explanation_rows(config))
        self.assertIn("full Android NDK required", rows["provider_explain_native"])
        self.assertIn("compiler/sysroot capability only", rows["provider_explain_native"])

    def test_provider_explanation_treats_ndk_version_without_native_integration_as_policy_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "build.gradle.kts").write_text(
                'android { ndkVersion = "28.0.0" }', encoding="utf-8"
            )
            config = _config(root, kind="android")
            with patch("devtool.termux_diagnostics.detect_runtime_identity", return_value=_termux_runtime(root / "usr")), patch(
                "devtool.termux_diagnostics.termux_gradle_probe",
                return_value=ToolProvider("gradle", "termux", False, compatible=False),
            ), patch("devtool.termux_diagnostics.termux_java_providers", return_value=()), patch(
                "devtool.termux_diagnostics.termux_native_provider",
                return_value=ToolProvider("native-compiler", "termux-sysroot", True, path=root / "clang", compatible=True),
            ):
                rows = dict(provider_explanation_rows(config))
        self.assertIn("no native compiler work detected", rows["provider_explain_native"])

    def test_package_recommendations_are_read_only_and_skip_gradle_with_complete_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrapper = root / "gradle" / "wrapper"
            wrapper.mkdir(parents=True)
            (root / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")
            (wrapper / "gradle-wrapper.properties").write_text("distributionUrl=x\n", encoding="utf-8")
            (wrapper / "gradle-wrapper.jar").write_bytes(b"jar")
            config = _config(root)
            with patch("devtool.termux_diagnostics.detect_runtime_identity", return_value=_termux_runtime(root / "usr")), patch(
                "devtool.termux_diagnostics.proot_distro_available", return_value=""
            ), patch("devtool.termux_diagnostics.tmux_available", return_value=False), patch(
                "devtool.termux_diagnostics.termux_gradle_probe",
                return_value=ToolProvider("gradle", "termux", False, compatible=False),
            ), patch("devtool.termux_diagnostics.termux_java_providers", return_value=()), patch(
                "devtool.termux_diagnostics.termux_native_provider",
                return_value=ToolProvider("native-compiler", "termux-sysroot", False, compatible=False),
            ):
                recommendations = termux_package_recommendations(config)
        names = [item.package for item in recommendations]
        self.assertEqual(names[:2], ["proot-distro", "tmux"])
        self.assertNotIn("gradle", names)

    def test_explicit_termux_providers_generate_required_package_hints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "CMakeLists.txt").write_text("project(x)\n", encoding="utf-8")
            config = _config(root, options={"gradle": {"provider": "termux"}, "android": {"java": {"provider": "termux"}}})
            missing = ToolProvider("x", "termux", False, compatible=False)
            with patch("devtool.termux_diagnostics.detect_runtime_identity", return_value=_termux_runtime(root / "usr")), patch(
                "devtool.termux_diagnostics.proot_distro_available", return_value="/usr/bin/proot-distro"
            ), patch("devtool.termux_diagnostics.tmux_available", return_value=True), patch(
                "devtool.termux_diagnostics.termux_gradle_probe", return_value=missing
            ), patch("devtool.termux_diagnostics.termux_java_providers", return_value=()), patch(
                "devtool.termux_diagnostics.termux_native_provider", return_value=missing
            ), patch("devtool.termux_diagnostics.termux_cmake_probe", return_value=missing), patch(
                "devtool.termux_diagnostics.termux_ninja_probe", return_value=missing
            ), patch("devtool.termux_diagnostics.termux_ccache_probe", return_value=missing):
                recommendations = termux_package_recommendations(config)
        by_name = {item.package: item for item in recommendations}
        self.assertTrue(by_name["gradle"].required)
        self.assertTrue(by_name["openjdk-21"].required)
        # Native provider remains auto here, so missing Termux clang/sysroot
        # are optional suggestions; the real build may select the system provider.
        self.assertFalse(by_name["clang"].required)
        self.assertFalse(by_name["ndk-sysroot"].required)
        self.assertIn("cmake", by_name)
        self.assertIn("ninja", by_name)

    def test_explicit_termux_android_tools_recommend_current_termux_packages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _config(root, kind="android", options={"android": {"build_tools": {"provider": "termux"}}})
            runtime = _termux_runtime(root / "usr")
            missing = ToolProvider("tool", "termux", False, compatible=False)
            with patch("devtool.termux_diagnostics.detect_runtime_identity", return_value=runtime), patch(
                "devtool.termux_diagnostics.proot_distro_available", return_value="/usr/bin/proot-distro"
            ), patch("devtool.termux_diagnostics.tmux_available", return_value=True), patch(
                "devtool.termux_diagnostics.termux_gradle_probe", return_value=missing
            ), patch("devtool.termux_diagnostics.termux_java_providers", return_value=()), patch(
                "devtool.termux_diagnostics.termux_native_provider", return_value=missing
            ), patch("devtool.termux_diagnostics.termux_ccache_probe", return_value=ToolProvider("ccache", "termux", True, path=root / "ccache", compatible=True)), patch(
                "devtool.termux_diagnostics.termux_android_tool_providers", return_value=(
                    ToolProvider("aapt2", "termux", False, compatible=False),
                    ToolProvider("apksigner", "termux", False, compatible=False),
                    ToolProvider("adb", "termux", False, compatible=False),
                )
            ):
                recommendations = termux_package_recommendations(config)
        by_name = {item.package: item for item in recommendations}
        self.assertTrue(by_name["aapt"].required)
        self.assertTrue(by_name["apksigner"].required)
        self.assertIn("android-tools", by_name)

    def test_package_rows_never_claim_to_install_packages(self) -> None:
        config = _config(Path("/tmp/project"))
        with patch(
            "devtool.termux_diagnostics.termux_package_recommendations",
            return_value=(PackageRecommendation("tmux", "tmux", "detach builds"),),
        ):
            rows = dict(package_recommendation_rows(config))
        self.assertIn("pkg install tmux", rows["termux_package_install_hint"])
        self.assertIn("suggestion only", rows["termux_package_install_hint"])
        self.assertIn("never installs/upgrades", rows["termux_package_install_hint"])

    def test_proot_guest_probe_discovers_guest_tools_without_guest_devtool(self) -> None:
        completed = subprocess.CompletedProcess(
            ["proot-distro"],
            0,
            stdout="os=Ubuntu 24.04 LTS\npython3=/usr/bin/python3\njava=/usr/bin/java\ngradle=\ncmake=/usr/bin/cmake\nninja=/usr/bin/ninja\nclang=/usr/bin/clang\ngit=/usr/bin/git\n",
            stderr="",
        )
        with patch("devtool.termux_diagnostics.installed_distros", return_value=("ubuntu",)), patch(
            "devtool.termux_diagnostics.subprocess.run", return_value=completed
        ) as run:
            probe = proot_guest_probe("ubuntu", executable="/usr/bin/proot-distro")
        self.assertTrue(probe.reachable)
        self.assertEqual(probe.os_release, "Ubuntu 24.04 LTS")
        self.assertEqual(probe.tool("python3"), "/usr/bin/python3")
        argv = run.call_args.args[0]
        self.assertEqual(argv[:3], ["/usr/bin/proot-distro", "login", "ubuntu"])
        self.assertNotIn("devtool", " ".join(argv))


    def test_proot_guest_probe_script_is_valid_posix_shell(self) -> None:
        script = _proot_guest_probe_script(("python3", "java"))
        result = subprocess.run(["/bin/sh", "-n", "-c", script], text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        executed = subprocess.run(["/bin/sh", "-c", script], text=True, capture_output=True, check=False)
        self.assertEqual(executed.returncode, 0, executed.stderr)
        self.assertIn("os=", executed.stdout)
        self.assertIn("python3=", executed.stdout)
        self.assertIn("java=", executed.stdout)

    def test_package_recommendations_do_not_execute_package_manager(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _config(root)
            runtime = _termux_runtime(root / "usr")
            missing = ToolProvider("tool", "termux", False, compatible=False)
            with patch("devtool.termux_diagnostics.detect_runtime_identity", return_value=runtime), patch(
                "devtool.termux_diagnostics.proot_distro_available", return_value=""
            ), patch("devtool.termux_diagnostics.tmux_available", return_value=False), patch(
                "devtool.termux_diagnostics.termux_gradle_probe", return_value=missing
            ), patch("devtool.termux_diagnostics.termux_java_providers", return_value=()), patch(
                "devtool.termux_diagnostics.termux_native_provider", return_value=missing
            ), patch("devtool.termux_diagnostics.subprocess.run") as run:
                termux_package_recommendations(config)
            run.assert_not_called()

    def test_proot_guest_probe_reports_uninstalled_container_without_login(self) -> None:
        with patch("devtool.termux_diagnostics.installed_distros", return_value=("debian",)), patch(
            "devtool.termux_diagnostics.subprocess.run"
        ) as run:
            probe = proot_guest_probe("ubuntu", executable="/usr/bin/proot-distro")
        self.assertFalse(probe.installed)
        self.assertFalse(probe.reachable)
        self.assertIn("not installed", probe.reason)
        run.assert_not_called()

    def test_proot_guest_probe_reports_timeout_without_crashing(self) -> None:
        with patch("devtool.termux_diagnostics.installed_distros", return_value=("ubuntu",)), patch(
            "devtool.termux_diagnostics.subprocess.run", side_effect=subprocess.TimeoutExpired(["proot-distro"], 20)
        ):
            probe = proot_guest_probe("ubuntu", executable="/usr/bin/proot-distro")
        self.assertTrue(probe.installed)
        self.assertFalse(probe.reachable)
        self.assertIn("guest probe failed", probe.reason)

    def test_termux_doctor_payload_contains_runtime_providers_packages_and_proot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = root / "usr"
            config = _config(root, options={"termux": {"resource_profile": "balanced"}, "proot": {"distro": "ubuntu"}})
            ready = ToolProvider("tool", "termux", True, path=prefix / "bin/tool", version="1", compatible=True)
            with patch("devtool.termux_diagnostics.detect_runtime_identity", return_value=_termux_runtime(prefix)), patch(
                "devtool.termux_diagnostics.shutil.which", side_effect=lambda name, path=None: f"{prefix}/bin/{name}" if name in {"dpkg-query", "termux-wake-lock", "termux-wake-unlock"} else None
            ), patch("devtool.termux_diagnostics.termux_installed_packages", return_value=()), patch(
                "devtool.termux_diagnostics.termux_gradle_probe", return_value=ready
            ), patch("devtool.termux_diagnostics.termux_java_providers", return_value=(ready,)), patch(
                "devtool.termux_diagnostics.termux_native_provider", return_value=ready
            ), patch("devtool.termux_diagnostics.termux_cmake_probe", return_value=ready), patch(
                "devtool.termux_diagnostics.termux_ninja_probe", return_value=ready
            ), patch("devtool.termux_diagnostics.termux_ccache_probe", return_value=ready), patch(
                "devtool.termux_diagnostics.termux_android_tool_providers", return_value=(ready,)
            ), patch("devtool.termux_diagnostics.termux_cache_root", return_value=root / ".cache/devtool/host-termux"), patch(
                "devtool.termux_diagnostics.tmux_available", return_value=True
            ), patch("devtool.termux_diagnostics.proot_distro_available", return_value="/usr/bin/proot-distro"), patch(
                "devtool.termux_diagnostics.installed_distros", return_value=("ubuntu",)
            ), patch("devtool.termux_diagnostics.proot_guest_doctor_rows", return_value=[("proot_guest", "READY: ubuntu")]), patch(
                "devtool.termux_diagnostics.termux_package_recommendations", return_value=()
            ):
                payload = termux_report_payload(config, explain=True, proot_distro="ubuntu")
        names = {item["name"] for item in payload["rows"]}
        self.assertIn("termux_runtime", names)
        self.assertIn("termux_gradle", names)
        self.assertIn("termux_native", names)
        self.assertIn("proot_guest", names)
        self.assertIn("provider_explain_gradle", names)
        self.assertEqual(payload["package_recommendations"], [])



    def test_openjdk_package_recommendation_discovers_current_available_version_read_only(self) -> None:
        runtime = _termux_runtime(Path("/data/data/com.termux/files/usr"))
        env = {"PATH": "/data/data/com.termux/files/usr/bin"}
        result = Mock(returncode=0, stdout="openjdk-17\nopenjdk-21\nopenjdk-25\n", stderr="")
        with patch("devtool.termux_diagnostics.termux_installed_packages", return_value=()), patch(
            "devtool.termux_diagnostics.shutil.which", return_value="/data/data/com.termux/files/usr/bin/apt-cache"
        ), patch("devtool.termux_diagnostics.subprocess.run", return_value=result) as run:
            package = _preferred_termux_openjdk_package(env=env, runtime=runtime)
        self.assertEqual(package, "openjdk-25")
        self.assertEqual(run.call_args.args[0][:2], ["/data/data/com.termux/files/usr/bin/apt-cache", "pkgnames"])
        self.assertFalse(any(token in {"install", "upgrade"} for token in run.call_args.args[0]))

    def test_android_termux_doctor_reports_full_ndk_readiness_without_installing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sdk = root / "sdk"
            (sdk / "ndk" / "28.2.0").mkdir(parents=True)
            (root / "build.gradle.kts").write_text('android { ndkVersion = "28.2.0"; externalNativeBuild { cmake { path = file("CMakeLists.txt") } } }\n', encoding="utf-8")
            (root / "CMakeLists.txt").write_text("project(native)\n", encoding="utf-8")
            config = _config(root, kind="android")
            project = Mock(ndk_version="28.2.0")
            with patch("devtool.android_build.inspect_project", return_value=project), patch(
                "devtool.android_build.discover_sdk_root_with_source", return_value=(sdk, "local.properties sdk.dir")
            ), patch("devtool.android_build.installed_build_tools_versions", return_value=["35.0.1"]), patch(
                "devtool.android_build.ndk_is_ready", return_value=True
            ):
                rows = dict(_android_termux_doctor_rows(config))
        self.assertIn("android-ndk", rows["native_requirement"])
        self.assertIn("35.0.1", rows["android_sdk_build_tools"])
        self.assertIn("READY: 28.2.0", rows["android_full_ndk"])

    def test_termux_doctor_exposes_each_android_tool_and_execution_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = root / "usr"
            config = _config(root)
            aapt2 = ToolProvider("aapt2", "termux", True, path=prefix / "bin/aapt2", version="35.0.1", compatible=True)
            missing = ToolProvider("adb", "termux", False, package="android-tools", compatible=False, rejection_reason="adb missing")
            def which(name: str, path: str | None = None):
                values = {
                    "dpkg-query": str(prefix / "bin/dpkg-query"),
                    "ssh": str(prefix / "bin/ssh"),
                    "chrootctl": str(prefix / "bin/chrootctl"),
                    "termux-wake-lock": str(prefix / "bin/termux-wake-lock"),
                    "termux-wake-unlock": str(prefix / "bin/termux-wake-unlock"),
                }
                return values.get(name)
            unavailable = ToolProvider("tool", "termux", False, compatible=False, rejection_reason="not installed")
            with patch("devtool.termux_diagnostics.detect_runtime_identity", return_value=_termux_runtime(prefix)), patch(
                "devtool.termux_diagnostics.shutil.which", side_effect=which
            ), patch("devtool.termux_diagnostics.termux_installed_packages", return_value=()), patch(
                "devtool.termux_diagnostics.termux_gradle_probe", return_value=unavailable
            ), patch("devtool.termux_diagnostics.termux_java_providers", return_value=()), patch(
                "devtool.termux_diagnostics.termux_native_provider", return_value=unavailable
            ), patch("devtool.termux_diagnostics.termux_cmake_probe", return_value=unavailable), patch(
                "devtool.termux_diagnostics.termux_ninja_probe", return_value=unavailable
            ), patch("devtool.termux_diagnostics.termux_ccache_probe", return_value=unavailable), patch(
                "devtool.termux_diagnostics.termux_android_tool_providers", return_value=(aapt2, missing)
            ), patch("devtool.termux_diagnostics.termux_cache_root", return_value=root / ".cache/devtool/host-termux"), patch(
                "devtool.termux_diagnostics.tmux_available", return_value=True
            ), patch("devtool.termux_diagnostics.proot_distro_available", return_value="/usr/bin/proot-distro"), patch(
                "devtool.termux_diagnostics.installed_distros", return_value=("ubuntu",)
            ), patch("devtool.termux_diagnostics.termux_package_recommendations", return_value=()):
                rows = dict(termux_integration_doctor_rows(config))
        self.assertIn("READY", rows["termux_android_tool_aapt2"])
        self.assertIn("MISSING", rows["termux_android_tool_adb"])
        self.assertIn("/ssh", rows["ssh"])
        self.assertIn("/chrootctl", rows["chrootctl"])
        self.assertIn("no native compiler requirement", rows["native_requirement"])



    def test_explicit_xdg_state_does_not_follow_stale_global_hud_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            isolated = Path(tmp) / "isolated"
            stale = Path(tmp) / "stale-global-hud"
            android_tasker = Path(tmp) / "android-tasker"
            env = {
                "DEVTOOL_XDG_DIR": str(isolated),
                "ANDROID_ROOT": "/system",
                "ANDROID_DATA": "/data",
            }
            with patch.dict("os.environ", env, clear=True), patch(
                "devtool.tasker.default_tasker_dir", return_value=str(android_tasker)
            ) as default_tasker, patch(
                "devtool.tasker.read_active_hud_directory", return_value=stale
            ) as read_active:
                roots = _candidate_state_roots()
        self.assertEqual(roots, [isolated])
        self.assertNotIn(android_tasker, roots)
        self.assertNotIn(stale, roots)
        default_tasker.assert_not_called()
        read_active.assert_not_called()

    def test_explicit_xdg_log_isolation_disables_implicit_phone_mirror(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            isolated = Path(tmp) / "isolated"
            log_dir = isolated / "logs"
            log_dir.mkdir(parents=True)
            expected = log_dir / "20260814-180000-000001.log"
            expected.write_text("isolated", encoding="utf-8")
            env = {
                "DEVTOOL_XDG_DIR": str(isolated),
                "ANDROID_ROOT": "/system",
                "ANDROID_DATA": "/data",
            }
            with patch.dict("os.environ", env, clear=True):
                self.assertIsNone(_phone_log_mirror_dir())
                self.assertEqual(_all_log_paths(), [expected])

    def test_explicit_phone_mirror_can_opt_back_in_under_xdg_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            isolated = Path(tmp) / "isolated"
            mirror = Path(tmp) / "phone-mirror"
            mirror.mkdir(parents=True)
            mirrored = mirror / "20260814-180001-000002.log"
            mirrored.write_text("mirror", encoding="utf-8")
            env = {
                "DEVTOOL_XDG_DIR": str(isolated),
                "DEVTOOL_PHONE_LOG_MIRROR_DIR": str(mirror),
            }
            with patch.dict("os.environ", env, clear=True):
                self.assertEqual(_phone_log_mirror_dir(), mirror)
                self.assertEqual(_all_log_paths(), [mirrored])

class TermuxFinalCliContractTests(unittest.TestCase):
    def test_termux_command_is_host_only(self) -> None:
        self.assertIn("termux", HOST_ONLY_COMMANDS)

    def test_parser_exposes_termux_doctor_packages_explain_and_doctor_explain(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["termux", "doctor", "--distro", "ubuntu", "--explain", "--format", "json"])
        self.assertEqual(args.command, "termux")
        self.assertEqual(args.termux_command, "doctor")
        self.assertEqual(args.distro, "ubuntu")
        self.assertTrue(args.explain)
        self.assertEqual(args.format, "json")
        generic = parser.parse_args(["doctor", "--explain"])
        self.assertTrue(generic.explain)

    def test_termux_packages_json_dispatches_without_build_validation(self) -> None:
        config = _config(Path("/tmp/devtool"))
        with patch("devtool.cli.resolve_repository_root", return_value=config.repo_root), patch(
            "devtool.cli.load_project_config", return_value=config
        ), patch("devtool.cli.format_package_recommendations_json", return_value='[{"package":"tmux"}]'), patch(
            "devtool.cli._maybe_prepare_termux_auto_chroot", return_value=None
        ):
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                rc = main(["--repo", str(config.repo_root), "termux", "packages", "--format", "json"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(buffer.getvalue()), [{"package": "tmux"}])


    def test_termux_doctor_json_dispatches_detailed_payload(self) -> None:
        config = _config(Path("/tmp/devtool"))
        payload = {"rows": [{"name": "termux_runtime", "value": "READY"}], "package_recommendations": []}
        with patch("devtool.cli.resolve_repository_root", return_value=config.repo_root), patch(
            "devtool.cli.load_project_config", return_value=config
        ), patch("devtool.cli.termux_report_payload", return_value=payload) as report, patch(
            "devtool.cli._maybe_prepare_termux_auto_chroot", return_value=None
        ):
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                rc = main(["--repo", str(config.repo_root), "termux", "doctor", "--distro", "ubuntu", "--explain", "--format", "json"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(buffer.getvalue()), payload)
        report.assert_called_once_with(config, explain=True, proot_distro="ubuntu")

    def test_termux_explain_json_dispatches_provider_rows(self) -> None:
        config = _config(Path("/tmp/devtool"))
        rows = [("provider_explain_gradle", "wrapper selected")]
        with patch("devtool.cli.resolve_repository_root", return_value=config.repo_root), patch(
            "devtool.cli.load_project_config", return_value=config
        ), patch("devtool.cli.provider_explanation_rows", return_value=rows), patch(
            "devtool.cli._maybe_prepare_termux_auto_chroot", return_value=None
        ):
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                rc = main(["--repo", str(config.repo_root), "termux", "explain", "--format", "json"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(buffer.getvalue()), [{"name": "provider_explain_gradle", "value": "wrapper selected"}])

    def test_generic_doctor_forwards_explain_and_selected_proot_guest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _config(root)
            fake_runner = Mock(kind="python", display_name="Python")
            fake_runner.doctor_rows.return_value = []
            fake_caps = Mock()
            fake_caps.supported.return_value = ("doctor",)
            fake_plan = Mock(current="native-termux", requested="proot-distro", effective="proot-distro", reason="test", chroot_name="")
            fake_preflight = Mock(ok=True)
            args = Mock(explain=True, proot_distro="ubuntu", host=True, chroot="", execution_environment="proot-distro")
            with patch("devtool.cli.get_project_runner", return_value=fake_runner), patch(
                "devtool.cli.runner_capabilities", return_value=fake_caps
            ), patch("devtool.cli._execution_plan_for_args", return_value=fake_plan), patch(
                "devtool.cli.preflight_execution", return_value=fake_preflight
            ), patch("devtool.cli.toolchain_doctor_rows", return_value=[]), patch(
                "devtool.cli.termux_integration_doctor_rows", return_value=[("termux_runtime", "READY")]
            ) as termux_rows, patch("devtool.cli.render_status_table") as render:
                _doctor(config, args=args)
            termux_rows.assert_called_once_with(config, explain=True, proot_distro="ubuntu")
            rendered = render.call_args.args[0]
            self.assertIn(("termux_runtime", "READY"), rendered)

    def test_final_docs_manpage_and_completion_expose_termux_diagnostics(self) -> None:
        root = Path(__file__).resolve().parents[1]
        readme = (root / "README.md").read_text(encoding="utf-8")
        execution_docs = (root / "docs" / "EXECUTION_ENVIRONMENTS.md").read_text(encoding="utf-8")
        android_docs = (root / "docs" / "ANDROID_PROJECTS.md").read_text(encoding="utf-8")
        manpage = (root / "share" / "man" / "man1" / "devtool.1").read_text(encoding="utf-8")
        completion = (root / "completions" / "_devtool").read_text(encoding="utf-8")
        for text in (readme, execution_docs, android_docs):
            self.assertIn("termux doctor", text)
            self.assertIn("termux packages", text)
        self.assertIn("termux doctor|packages|explain", manpage)
        self.assertIn("--explain", manpage)
        self.assertIn("termux:inspect native Termux providers", completion)


    def test_config_templates_document_termux_and_proot_final_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = generate_template(Path(tmp))
        global_template = minimal_global_config_template()
        for text in (project, global_template):
            self.assertIn("[termux]", text)
            self.assertIn("resource_profile", text)
            self.assertIn("[termux.storage]", text)
            self.assertIn("shared_storage_policy", text)
            self.assertIn("[proot]", text)
            self.assertIn("auto_bind_repo", text)
            self.assertIn("cache_mount", text)
            self.assertIn("Android SDK provider bootstrap may install narrowly-scoped Termux prerequisites/repository metadata", text)
            self.assertIn("guest does not need devtool", text)


if __name__ == "__main__":
    unittest.main()
