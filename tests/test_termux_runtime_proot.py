from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

from devtool.cli import build_parser, _apply_runner_cli_options, _prepare_specs_for_execution
from devtool.config import load_project_config
from devtool.execution_environments import DESKTOP_LINUX, NATIVE_TERMUX, PROOT_DISTRO, normalize_execution_environment, preflight_execution, resolve_execution_plan
from devtool.models import CommandSpec, Issue, ProjectConfig
from devtool.runners import _looks_like_chrootctl_exec, _run_process_live, _termux_chroot_stall_hint_seconds, _termux_chroot_stall_message, run_specs
from devtool.proot_distro_runner import ProotRunnerConfig, cleanup_proot_staging, resolve_proot_config, validate_proot_config, wrap_specs_for_proot_distro
from devtool.termux_runtime import (
    adapt_termux_script_argv,
    apply_termux_resource_defaults,
    classify_runtime_failure,
    export_artifacts,
    gradle_stall_message,
    is_android_shared_storage,
    select_termux_resource_profile,
    termux_cache_root,
    termux_runtime_doctor_rows,
    termux_shebang_warning,
    native_termux_executable_hint,
    termux_wake_lock,
)

TERMUX_ENV = {
    "HOME": "/data/data/com.termux/files/home",
    "PREFIX": "/data/data/com.termux/files/usr",
    "TERMUX_VERSION": "0.119",
}


def _write_elf64(path: Path, *, machine: int, interpreter: str = "") -> None:
    """Write the minimum ELF64 data needed by the Termux preflight parser."""
    import struct

    header = bytearray(64)
    header[:4] = b"\x7fELF"
    header[4] = 2  # ELFCLASS64
    header[5] = 1  # little-endian
    header[6] = 1  # ELF version
    struct.pack_into("<H", header, 16, 2)
    struct.pack_into("<H", header, 18, machine)
    struct.pack_into("<I", header, 20, 1)
    if interpreter:
        raw = interpreter.encode("utf-8") + b"\0"
        phoff = 64
        phentsize = 56
        interp_offset = phoff + phentsize
        struct.pack_into("<Q", header, 32, phoff)
        struct.pack_into("<H", header, 54, phentsize)
        struct.pack_into("<H", header, 56, 1)
        ph = bytearray(phentsize)
        struct.pack_into("<I", ph, 0, 3)  # PT_INTERP
        struct.pack_into("<Q", ph, 8, interp_offset)
        struct.pack_into("<Q", ph, 32, len(raw))
        path.write_bytes(bytes(header) + bytes(ph) + raw)
    else:
        path.write_bytes(bytes(header))
    path.chmod(0o755)


class TermuxRuntimeTests(unittest.TestCase):
    def test_shared_storage_classification(self):
        self.assertTrue(is_android_shared_storage("/sdcard/Code/demo"))
        self.assertTrue(is_android_shared_storage("/storage/emulated/0/Code/demo"))
        self.assertFalse(is_android_shared_storage("/data/data/com.termux/files/home/Code/demo"))

    def test_resource_profiles_are_bounded(self):
        conservative = select_termux_resource_profile("conservative", available_mb=2048, cpus=8)
        balanced = select_termux_resource_profile("balanced", available_mb=4096, cpus=8)
        performance = select_termux_resource_profile("performance", available_mb=8192, cpus=8)
        self.assertEqual(conservative.max_workers, 1)
        self.assertLessEqual(balanced.max_workers, 2)
        self.assertLessEqual(performance.max_workers, 4)
        self.assertGreaterEqual(performance.gradle_heap_mb, balanced.gradle_heap_mb)

    def test_resource_defaults_do_not_override_explicit_values(self):
        options = {"gradle": {"max_workers": 7}, "validation": {"gradle_heap_mb": 999}}
        with patch.dict(os.environ, TERMUX_ENV, clear=True):
            apply_termux_resource_defaults(options, profile_name="balanced")
        self.assertEqual(options["gradle"]["max_workers"], 7)
        self.assertEqual(options["validation"]["gradle_heap_mb"], 999)
        self.assertIn("memory_guard_mb", options["validation"])

    def test_termux_cache_root_is_backend_isolated(self):
        root = termux_cache_root({"HOME": "/data/data/com.termux/files/home"})
        self.assertEqual(str(root), "/data/data/com.termux/files/home/.cache/devtool/host-termux")

    def test_runtime_failure_classifies_glibc_and_404(self):
        self.assertEqual(classify_runtime_failure("CANNOT LINK EXECUTABLE: ld-linux-aarch64.so.1") .kind, "libc-mismatch")
        self.assertEqual(classify_runtime_failure("HTTP Error 404: Not Found URL") .kind, "http-404")

    def test_artifact_export_copies_only_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "app.apk"
            src.write_bytes(b"apk")
            out = root / "Download"
            copied = export_artifacts([src, root / "missing.apk"], out)
            self.assertEqual(copied, [out / "app.apk"])
            self.assertEqual((out / "app.apk").read_bytes(), b"apk")

    def test_wake_lock_acquire_and_release(self):
        completed = Mock(returncode=0)
        with patch.dict(os.environ, TERMUX_ENV, clear=True), patch("devtool.termux_runtime.shutil.which", side_effect=lambda name: f"/termux/{name}"), patch("devtool.termux_runtime.subprocess.run", return_value=completed) as run:
            with termux_wake_lock("on") as acquired:
                self.assertTrue(acquired)
            self.assertEqual(run.call_args_list[0].args[0], ["/termux/termux-wake-lock"])
            self.assertEqual(run.call_args_list[-1].args[0], ["/termux/termux-wake-unlock"])

    def test_gradle_wrapper_timeout_is_promoted_to_network_failure_kind(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = ProjectConfig(repo_root=root, kind="gradle", project_root=str(root))
            spec = CommandSpec("build", ["./gradlew", "build"], cwd=str(root))
            output = (
                'Exception in thread "main" java.net.SocketTimeoutException: Connect timed out\n'
                '    at org.gradle.wrapper.GradleWrapperMain.main(GradleWrapperMain.java:34)\n'
            )
            with patch("devtool.runners._run_process_live", return_value=(1, output)), patch(
                "devtool.execution_environments.detect_current_environment", return_value=NATIVE_TERMUX
            ):
                results, _ = run_specs(config, [spec], verbose=False, shutdown_dotnet=False)

        self.assertEqual(results[0].failure_kind, "network_timeout")
        self.assertEqual(results[0].metadata["runtime_failure_hint"]["kind"], "network-timeout")
        self.assertIn("Devtool runtime diagnostic (network-timeout)", results[0].output)

    def test_runtime_failure_classifies_tls_and_timeout(self):
        self.assertEqual(classify_runtime_failure("SSL certificate problem: certificate verify failed").kind, "tls")
        self.assertEqual(classify_runtime_failure("Connection timed out while downloading").kind, "network-timeout")

    def test_runtime_failure_classifies_dns(self):
        self.assertEqual(classify_runtime_failure("Could not resolve host: repo.example").kind, "dns")
        self.assertEqual(
            classify_runtime_failure("Temporary failure in name resolution", native_termux=False).kind, "dns"
        )

    def test_gradle_stall_diagnostic_does_not_claim_process_is_killed(self):
        message = gradle_stall_message(180)
        self.assertIn("no Gradle output for 180 seconds", message)
        self.assertIn("process was not stopped", message)
        self.assertIn("--offline", message)
        self.assertIn("--info", message)

    def test_wake_lock_releases_when_body_raises(self):
        completed = Mock(returncode=0)
        with patch.dict(os.environ, TERMUX_ENV, clear=True), patch(
            "devtool.termux_runtime.shutil.which", side_effect=lambda name, **kwargs: f"/termux/{name}"
        ), patch("devtool.termux_runtime.subprocess.run", return_value=completed) as run:
            with self.assertRaisesRegex(RuntimeError, "boom"):
                with termux_wake_lock("on"):
                    raise RuntimeError("boom")
        self.assertEqual(run.call_args_list[-1].args[0], ["/termux/termux-wake-unlock"])

    def test_termux_script_argv_adapts_missing_linux_shebang_without_editing_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            script = root / "tool.sh"
            script.write_text("#!/bin/bash\necho ok\n", encoding="utf-8")
            script.chmod(0o755)
            env = {**TERMUX_ENV, "PATH": "/termux/bin"}
            with patch("devtool.termux_runtime.Path.exists", return_value=False), patch(
                "devtool.termux_runtime.shutil.which", return_value="/termux/bin/bash"
            ):
                argv, detail = adapt_termux_script_argv([str(script), "arg"], cwd=root, env=env)
            self.assertEqual(argv, ["/termux/bin/bash", str(script), "arg"])
            self.assertIn("adapted conventional shebang", detail)
            self.assertTrue(script.read_text(encoding="utf-8").startswith("#!/bin/bash"))

    def test_termux_shebang_warning_is_empty_off_termux(self):
        with tempfile.TemporaryDirectory() as td:
            script = Path(td) / "tool"
            script.write_text("#!/usr/bin/python3\n", encoding="utf-8")
            self.assertEqual(termux_shebang_warning(script, env={"HOME": "/home/me"}), "")

    def test_gradle_quiet_diagnostic_warns_without_stopping_process(self):
        env = os.environ.copy()
        env["DEVTOOL_GRADLE_STALL_WARNING_SECONDS"] = "0.05"
        code, output = _run_process_live(
            [sys.executable, "-c", "import time; time.sleep(0.18)"],
            cwd=os.getcwd(),
            env=env,
            verbose=True,
            progress_parser="gradle",
        )
        self.assertEqual(code, 0)
        self.assertIn("Devtool stall diagnostic", output)
        self.assertIn("process was not stopped", output)

    def test_chroot_exec_quiet_diagnostic_warns_without_stopping_process(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            chrootctl = root / "chrootctl"
            chrootctl.write_text(
                "#!/usr/bin/env python3\nimport time\ntime.sleep(0.18)\n",
                encoding="utf-8",
            )
            chrootctl.chmod(0o755)
            env = os.environ.copy()
            env["DEVTOOL_TERMUX_CHROOT_STALL_HINT_SECONDS"] = "0.05"
            argv = [str(chrootctl), "--name", "androidos", "exec", "--", "zsh", "-lc", ":"]
            code, output = _run_process_live(
                argv,
                cwd=str(root),
                env=env,
                verbose=True,
            )
        self.assertEqual(code, 0)
        self.assertIn("Android chroot executor androidos is still running", output)
        self.assertIn("process was not stopped", output)
        self.assertIn("Use -v", output)

    def test_chroot_stall_helpers_detect_exec_and_bound_seconds(self):
        self.assertTrue(_looks_like_chrootctl_exec(["sudo", "chrootctl", "--name", "androidos", "exec", "--", "zsh"]))
        self.assertFalse(_looks_like_chrootctl_exec(["chrootctl", "--name", "androidos", "start", "--login"]))
        self.assertEqual(_termux_chroot_stall_hint_seconds({"DEVTOOL_TERMUX_CHROOT_STALL_HINT_SECONDS": "bad"}), 30.0)
        self.assertEqual(_termux_chroot_stall_hint_seconds({"DEVTOOL_TERMUX_CHROOT_STALL_HINT_SECONDS": "99999"}), 3600.0)
        self.assertIn("androidos", _termux_chroot_stall_message(["chrootctl", "--name", "androidos", "exec"], 7))

    def test_shared_storage_deny_policy_stops_before_execution(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="python",
                execution_environment="native-termux",
                runner_options={"termux": {"storage": {"shared_storage_policy": "deny"}}},
            )
            args = build_parser().parse_args(["--execution-environment", "native-termux", "validate"])
            plan = resolve_execution_plan(requested="native-termux", env=TERMUX_ENV)
            with patch("devtool.cli._execution_plan_for_args", return_value=plan), patch(
                "devtool.termux_runtime.is_android_shared_storage", return_value=True
            ):
                with self.assertRaisesRegex(SystemExit, "Android shared storage"):
                    _prepare_specs_for_execution(config, [CommandSpec("test", ["true"], cwd=str(root))], args)

    def test_verified_android_artifact_can_export_after_local_verification(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            apk = root / "app-debug.apk"
            apk.write_bytes(b"apk")
            export_dir = root / "Download"
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="android",
                runner_options={"termux": {"artifacts": {"export_dir": str(export_dir)}}},
            )
            spec = CommandSpec(
                "package",
                [sys.executable, "-c", "print('ok')"],
                cwd=str(root),
                metadata={"collect_artifacts": True, "project_root": str(root)},
            )
            metadata = {"valid": 1, "invalid": 0, "count": 1, "items": [{"path": str(apk), "valid": True}]}
            with patch("devtool.android_artifacts.collect_and_verify_android_artifacts", return_value=(metadata, [])):
                results, _ = run_specs(config, [spec], verbose=False, shutdown_dotnet=False)
            self.assertEqual(results[0].returncode, 0)
            self.assertEqual((export_dir / apk.name).read_bytes(), b"apk")
            self.assertEqual(results[0].metadata["artifacts"]["exported"], [str(export_dir / apk.name)])

    def test_removable_storage_is_classified_as_shared_storage(self):
        self.assertTrue(is_android_shared_storage("/storage/1234-5678/Code/demo"))
        self.assertTrue(is_android_shared_storage("/mnt/media_rw/1234-5678/Code/demo"))

    def test_libc_failure_hint_is_only_termux_native_but_network_hints_are_generic(self):
        self.assertIsNone(classify_runtime_failure("CANNOT LINK EXECUTABLE: ld-linux-aarch64.so.1", native_termux=False))
        self.assertEqual(classify_runtime_failure("HTTP Error 404: Not Found", native_termux=False).kind, "http-404")

    def test_wake_lock_timeout_is_optional_in_auto_and_required_in_on_mode(self):
        with patch.dict(os.environ, TERMUX_ENV, clear=True), patch(
            "devtool.termux_runtime.shutil.which", return_value="/termux/termux-wake-lock"
        ), patch("devtool.termux_runtime.subprocess.run", side_effect=__import__('subprocess').TimeoutExpired("wake", 10)):
            with termux_wake_lock("auto") as acquired:
                self.assertFalse(acquired)
            with self.assertRaisesRegex(RuntimeError, "wake-lock|wake lock"):
                with termux_wake_lock("on"):
                    pass

    def test_android_artifact_export_is_skipped_after_verification_error(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            apk = root / "bad.apk"
            apk.write_bytes(b"bad")
            export_dir = root / "Download"
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="android",
                runner_options={"termux": {"artifacts": {"export_dir": str(export_dir)}}},
            )
            spec = CommandSpec(
                "package", [sys.executable, "-c", "print('ok')"], cwd=str(root),
                metadata={"collect_artifacts": True, "project_root": str(root)},
            )
            metadata = {"valid": 0, "invalid": 1, "count": 1, "items": [{"path": str(apk), "valid": False}]}
            issues = [Issue("error", "package", str(apk), "APK_INVALID", "invalid artifact")]
            with patch("devtool.android_artifacts.collect_and_verify_android_artifacts", return_value=(metadata, issues)):
                results, _ = run_specs(config, [spec], verbose=False, shutdown_dotnet=False)
            self.assertFalse((export_dir / apk.name).exists())
            self.assertEqual(results[0].metadata["artifacts"]["exported"], [])
            self.assertEqual(results[0].metadata["artifacts"]["export_skipped_reason"], "artifact verification reported errors")

    def test_guest_execution_failure_does_not_get_termux_libc_hint(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = ProjectConfig(repo_root=root, project_root=str(root), kind="python")
            spec = CommandSpec(
                "guest-test",
                [sys.executable, "-c", "import sys; print('CANNOT LINK EXECUTABLE: glibc'); sys.exit(1)"],
                cwd=str(root), metadata={"execution_mode": "proot-distro"},
            )
            with patch("devtool.execution_environments.detect_current_environment", return_value=NATIVE_TERMUX):
                results, _ = run_specs(config, [spec], verbose=False, shutdown_dotnet=False)
            self.assertEqual(results[0].returncode, 1)
            self.assertNotEqual(results[0].metadata.get("runtime_failure_kind"), "libc-mismatch")

    def test_termux_doctor_reports_runtime_hardening_and_proot(self):
        config = ProjectConfig(
            repo_root=Path("/data/data/com.termux/files/home/Code/demo"),
            kind="python",
            runner_options={"termux": {"resource_profile": "balanced", "storage": {"shared_storage_policy": "warn"}}},
        )
        with patch("devtool.termux_runtime.shutil.which", side_effect=lambda name: f"/termux/{name}"), patch(
            "devtool.proot_distro_runner.proot_distro_available", return_value="/termux/proot-distro"
        ), patch("devtool.proot_distro_runner.installed_distros", return_value=("ubuntu", "debian")):
            rows = dict(termux_runtime_doctor_rows(config, env=TERMUX_ENV))
        self.assertEqual(rows["termux_repo_storage"], "private")
        self.assertEqual(rows["termux_wake_lock"], "READY")
        self.assertEqual(rows["tmux"], "READY")
        self.assertIn("ubuntu", rows["proot_containers"])


class ProotRunnerTests(unittest.TestCase):
    def _config(self, root: Path) -> ProjectConfig:
        return ProjectConfig(repo_root=root, kind="python", project_root=str(root), runner_options={"proot": {"distro": "ubuntu"}})

    def test_execution_environment_alias(self):
        self.assertEqual(normalize_execution_environment("proot"), PROOT_DISTRO)
        plan = resolve_execution_plan(requested="proot-distro", env=TERMUX_ENV)
        self.assertEqual(plan.effective, PROOT_DISTRO)

    def test_cli_exposes_proot_and_termux_options(self):
        parser = build_parser()
        args = parser.parse_args(["--proot-distro", "ubuntu", "--proot-user", "dev", "--proot-bind", "/a:/b", "--termux-resource-profile", "balanced", "validate"])
        self.assertEqual(args.proot_distro, "ubuntu")
        self.assertEqual(args.proot_user, "dev")
        self.assertEqual(args.proot_bind, ["/a:/b"])

    def test_config_section_is_loaded(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "pyproject.toml").write_text('[project]\nname="demo"\nversion="0.1"\n', encoding="utf-8")
            (root / ".devtool.toml").write_text('[project]\nkind="python"\n[proot]\ndistro="ubuntu"\nrepo_mount="/workspace/demo"\n[termux]\nwake_lock="off"\nresource_profile="conservative"\n[termux.storage]\nshared_storage_policy="deny"\n', encoding="utf-8")
            cfg = load_project_config(str(root), None)
            self.assertEqual(cfg.runner_options["proot"]["distro"], "ubuntu")
            self.assertEqual(cfg.runner_options["termux"]["storage"]["shared_storage_policy"], "deny")

    def test_relative_guest_bind_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "destination must be absolute"):
            validate_proot_config(ProotRunnerConfig(distro="ubuntu", binds=["/host:relative"]))

    def test_isolated_and_minimal_are_mutually_exclusive(self):
        with self.assertRaisesRegex(ValueError, "mutually exclusive"):
            validate_proot_config(ProotRunnerConfig(distro="ubuntu", isolated=True, minimal=True))

    def test_wrap_binds_repo_and_preserves_progress_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            script = root / "tool.py"
            script.write_text("print('ok')", encoding="utf-8")
            spec = CommandSpec("test", ["python3", str(script)], env={"CI": "1", "JAVA_HOME": "/host/jdk"}, cwd=str(root), metadata={"progress_parser": "python"})
            cfg = ProotRunnerConfig(distro="ubuntu", cwd="/workspace/project", repo_mount="/workspace/project")
            with patch("devtool.proot_distro_runner.proot_distro_available", return_value="/usr/bin/proot-distro"), patch("devtool.proot_distro_runner.installed_distros", return_value=("ubuntu",)):
                wrapped = wrap_specs_for_proot_distro([spec], cfg, repo_root=root)[0]
            self.assertEqual(wrapped.argv[:4], ["/usr/bin/proot-distro", "login", "ubuntu", "--user"])
            self.assertIn(f"{root.resolve()}:/workspace/project", wrapped.argv)
            self.assertIn("/workspace/project/tool.py", wrapped.argv)
            self.assertNotIn("JAVA_HOME=/host/jdk", wrapped.argv)
            self.assertEqual(wrapped.metadata["progress_argv"], spec.argv)
            self.assertEqual(wrapped.metadata["execution_mode"], "proot-distro")

    def test_external_helper_is_staged_not_host_path_leaked(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as hd:
            root = Path(td)
            helper = Path(hd) / "helper.py"
            helper.write_text("print(1)", encoding="utf-8")
            spec = CommandSpec("build", ["python3", str(helper)], cwd=str(root))
            cfg = ProotRunnerConfig(distro="ubuntu")
            with patch("devtool.proot_distro_runner.proot_distro_available", return_value="proot-distro"), patch("devtool.proot_distro_runner.installed_distros", return_value=("ubuntu",)):
                wrapped = wrap_specs_for_proot_distro([spec], cfg, repo_root=root)[0]
            self.assertFalse(any(str(helper) == token for token in wrapped.argv))
            self.assertTrue(any(token.startswith("/run/devtool/") for token in wrapped.argv))

    def test_resolve_config_honors_cli_and_no_devtool_guest_requirement(self):
        root = Path("/tmp/repo")
        cfg = self._config(root)
        args = argparse.Namespace(proot_distro="debian", proot_user="dev", proot_cwd="/work", proot_repo_mount="/work", proot_bind=[], proot_env=[], proot_no_auto_bind_repo=False, proot_shared_home=False, proot_shared_tmp=False, proot_isolated=False, proot_minimal=False, proot_stage_dir="")
        resolved = resolve_proot_config(cfg, args)
        self.assertEqual(resolved.distro, "debian")
        self.assertEqual(resolved.cwd, "/work")
        self.assertFalse(hasattr(resolved, "tool"))

    def test_missing_requested_container_is_rejected_even_when_none_installed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            spec = CommandSpec("test", ["python3", "-V"], cwd=str(root))
            with patch("devtool.proot_distro_runner.proot_distro_available", return_value="proot-distro"), patch(
                "devtool.proot_distro_runner.installed_distros", return_value=()
            ):
                with self.assertRaisesRegex(ValueError, "is not installed .*none"):
                    wrap_specs_for_proot_distro([spec], ProotRunnerConfig(distro="ubuntu"), repo_root=root)

    def test_container_discovery_failure_is_not_treated_as_installed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            spec = CommandSpec("test", ["python3", "-V"], cwd=str(root))
            with patch("devtool.proot_distro_runner.proot_distro_available", return_value="proot-distro"), patch(
                "devtool.proot_distro_runner.installed_distros", return_value=None
            ):
                with self.assertRaisesRegex(ValueError, "Could not query installed"):
                    wrap_specs_for_proot_distro([spec], ProotRunnerConfig(distro="ubuntu"), repo_root=root)

    def test_proot_translates_key_value_paths_and_safe_environment_paths(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "input.txt"
            source.write_text("x", encoding="utf-8")
            spec = CommandSpec(
                "configure",
                ["cmake", f"-DINPUT={source}"],
                env={"CUSTOM_PATH": str(source), "JAVA_HOME": "/host/jdk", "UV_CACHE_DIR": "/host/cache/uv"},
                cwd=str(root),
            )
            with patch("devtool.proot_distro_runner.proot_distro_available", return_value="proot-distro"), patch(
                "devtool.proot_distro_runner.installed_distros", return_value=("ubuntu",)
            ):
                wrapped = wrap_specs_for_proot_distro(
                    [spec], ProotRunnerConfig(distro="ubuntu"), repo_root=root,
                    project_env={"PROJECT_FLAG": "yes", "ANDROID_HOME": "/host/sdk"},
                )[0]
            self.assertIn("-DINPUT=/workspace/project/input.txt", wrapped.argv)
            self.assertIn("CUSTOM_PATH=/workspace/project/input.txt", wrapped.argv)
            self.assertIn("PROJECT_FLAG=yes", wrapped.argv)
            self.assertNotIn("JAVA_HOME=/host/jdk", wrapped.argv)
            self.assertNotIn("UV_CACHE_DIR=/host/cache/uv", wrapped.argv)
            self.assertNotIn("ANDROID_HOME=/host/sdk", wrapped.argv)

    def test_bind_without_destination_uses_resolved_absolute_path_for_both_sides(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            extra = root / "extra"
            extra.mkdir()
            spec = CommandSpec("test", ["true"], cwd=str(root))
            cfg = ProotRunnerConfig(distro="ubuntu", binds=[str(extra)])
            with patch("devtool.proot_distro_runner.proot_distro_available", return_value="proot-distro"), patch(
                "devtool.proot_distro_runner.installed_distros", return_value=("ubuntu",)
            ):
                wrapped = wrap_specs_for_proot_distro([spec], cfg, repo_root=root)[0]
            binding = f"{extra.resolve()}:{extra.resolve()}"
            self.assertIn(binding, wrapped.argv)

    def test_proot_stage_cleanup_removes_only_owned_stage(self):
        with tempfile.TemporaryDirectory() as td:
            stage = Path(td) / "stage"
            stage.mkdir()
            keep = Path(td) / "keep"
            keep.mkdir()
            specs = [
                CommandSpec("one", ["true"], metadata={"proot_stage_owned": True, "proot_stage_dir": str(stage)}),
                CommandSpec("two", ["true"], metadata={"proot_stage_owned": False, "proot_stage_dir": str(keep)}),
            ]
            cleanup_proot_staging(specs)
            self.assertFalse(stage.exists())
            self.assertTrue(keep.exists())

    def test_proot_preflight_is_available_on_regular_linux_when_command_exists(self):
        plan = resolve_execution_plan(requested="proot-distro", env={"HOME": "/home/dev"})
        self.assertEqual(plan.current, DESKTOP_LINUX)
        with tempfile.TemporaryDirectory() as td, patch(
            "devtool.execution_environments.shutil.which", return_value="/usr/bin/proot-distro"
        ):
            preflight = preflight_execution(Path(td), plan)
        self.assertTrue(preflight.ok)

    def test_termux_only_proot_flags_are_rejected_on_regular_linux(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            spec = CommandSpec("test", ["true"], cwd=str(root))
            with patch("devtool.proot_distro_runner.detect_current_environment", return_value=DESKTOP_LINUX):
                with self.assertRaisesRegex(ValueError, "available only on Termux"):
                    wrap_specs_for_proot_distro(
                        [spec], ProotRunnerConfig(distro="ubuntu", isolated=True), repo_root=root, dry_run=True
                    )

    def test_dry_run_does_not_create_stage_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            stage_base = root.parent / f"{root.name}-stage-dry-run"
            shutil.rmtree(stage_base, ignore_errors=True)
            spec = CommandSpec("test", ["python3", "-V"], cwd=str(root))
            wrapped = wrap_specs_for_proot_distro(
                [spec], ProotRunnerConfig(distro="ubuntu", stage_dir=str(stage_base)), repo_root=root, dry_run=True
            )
            self.assertFalse(stage_base.exists())
            self.assertFalse(wrapped[0].metadata["proot_stage_owned"])

    def test_missing_explicit_bind_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            missing = root / "missing"
            spec = CommandSpec("test", ["true"], cwd=str(root))
            with self.assertRaisesRegex(ValueError, "bind source does not exist"):
                wrap_specs_for_proot_distro(
                    [spec], ProotRunnerConfig(distro="ubuntu", binds=[f"{missing}:/extra"]), repo_root=root, dry_run=True
                )

    def test_duplicate_guest_bind_destination_is_rejected(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as extra_td:
            root = Path(td)
            other = Path(extra_td)
            spec = CommandSpec("test", ["true"], cwd=str(root))
            with self.assertRaisesRegex(ValueError, "bound from both"):
                wrap_specs_for_proot_distro(
                    [spec],
                    ProotRunnerConfig(distro="ubuntu", binds=[f"{other}:/workspace/project"]),
                    repo_root=root, dry_run=True,
                )

    def test_unmapped_absolute_host_directory_requires_bind(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as outside_td:
            root = Path(td)
            outside = Path(outside_td)
            spec = CommandSpec("test", ["tool", str(outside)], cwd=str(root))
            with self.assertRaisesRegex(ValueError, "not mapped into the PRoot guest"):
                wrap_specs_for_proot_distro([spec], ProotRunnerConfig(distro="ubuntu"), repo_root=root, dry_run=True)

    def test_explicit_custom_guest_cwd_is_authoritative(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            spec = CommandSpec("test", ["true"], cwd=str(root))
            cfg = ProotRunnerConfig(distro="ubuntu", repo_mount="/workspace/project", cwd="/opt/build")
            wrapped = wrap_specs_for_proot_distro([spec], cfg, repo_root=root, dry_run=True)[0]
            idx = wrapped.argv.index("--work-dir")
            self.assertEqual(wrapped.argv[idx + 1], "/opt/build")
            self.assertEqual(wrapped.metadata["proot_guest_cwd"], "/opt/build")

    def test_locale_family_is_forwarded_but_backend_paths_are_not(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            spec = CommandSpec("test", ["true"], cwd=str(root), env={"JAVA_HOME": "/host/jdk"})
            with patch.dict(os.environ, {"LC_CTYPE": "C.UTF-8", "JAVA_HOME": "/host/jdk"}, clear=False):
                wrapped = wrap_specs_for_proot_distro([spec], ProotRunnerConfig(distro="ubuntu"), repo_root=root, dry_run=True)[0]
            self.assertIn("LC_CTYPE=C.UTF-8", wrapped.argv)
            self.assertNotIn("JAVA_HOME=/host/jdk", wrapped.argv)

    def test_proot_wrapper_never_uses_detached_or_no_kill_on_exit_modes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            wrapped = wrap_specs_for_proot_distro(
                [CommandSpec("test", ["true"], cwd=str(root))],
                ProotRunnerConfig(distro="ubuntu"), repo_root=root, dry_run=True,
            )[0]
            self.assertNotIn("--detach", wrapped.argv)
            self.assertNotIn("--no-kill-on-exit", wrapped.argv)

    def test_proot_termux_guest_flags_are_forwarded(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            spec = CommandSpec("test", ["true"], cwd=str(root))
            with patch("devtool.proot_distro_runner.detect_current_environment", return_value=NATIVE_TERMUX):
                wrapped = wrap_specs_for_proot_distro(
                    [spec],
                    ProotRunnerConfig(
                        distro="ubuntu", user="dev", cwd="/work", shared_home=True,
                        shared_tmp=True, isolated=True,
                    ),
                    repo_root=root, dry_run=True,
                )[0]
            self.assertEqual(wrapped.argv[wrapped.argv.index("--user") + 1], "dev")
            self.assertEqual(wrapped.argv[wrapped.argv.index("--work-dir") + 1], "/work")
            self.assertIn("--shared-home", wrapped.argv)
            self.assertIn("--shared-tmp", wrapped.argv)
            self.assertIn("--isolated", wrapped.argv)
            self.assertNotIn("--minimal", wrapped.argv)

    def test_proot_minimal_flag_is_forwarded_on_termux(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch("devtool.proot_distro_runner.detect_current_environment", return_value=NATIVE_TERMUX):
                wrapped = wrap_specs_for_proot_distro(
                    [CommandSpec("test", ["true"], cwd=str(root))],
                    ProotRunnerConfig(distro="ubuntu", minimal=True), repo_root=root, dry_run=True,
                )[0]
            self.assertIn("--minimal", wrapped.argv)
            self.assertNotIn("--isolated", wrapped.argv)

    def test_repo_artifact_path_maps_into_guest_without_copying(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            out = root / "build" / "artifact.zip"
            out.parent.mkdir()
            spec = CommandSpec("package", ["tool", f"--output={out}"], cwd=str(root))
            wrapped = wrap_specs_for_proot_distro(
                [spec], ProotRunnerConfig(distro="ubuntu"), repo_root=root, dry_run=True
            )[0]
            self.assertIn("--output=/workspace/project/build/artifact.zip", wrapped.argv)
            self.assertIn({"host": str(root.resolve()), "guest": "/workspace/project"}, wrapped.metadata["proot_bindings"])

    def test_wrapping_failure_cleans_owned_stage(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as outside_td, tempfile.TemporaryDirectory() as stage_td:
            root = Path(td)
            stage_base = Path(stage_td) / "stage"
            outside = Path(outside_td)
            spec = CommandSpec("test", ["tool", str(outside)], cwd=str(root))
            with patch("devtool.proot_distro_runner.proot_distro_available", return_value="proot-distro"), patch(
                "devtool.proot_distro_runner.installed_distros", return_value=("ubuntu",)
            ):
                with self.assertRaisesRegex(ValueError, "not mapped"):
                    wrap_specs_for_proot_distro(
                        [spec], ProotRunnerConfig(distro="ubuntu", stage_dir=str(stage_base)), repo_root=root
                    )
            self.assertEqual(list(stage_base.glob("ubuntu-*")), [])

    def test_termux_host_elf_command_resolves_by_guest_command_name(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            prefix = root / "termux-prefix"
            fake = prefix / "bin" / "clang"
            fake.parent.mkdir(parents=True)
            fake.write_bytes(b"\x7fELFfake")
            spec = CommandSpec("build", [str(fake), "--version"], cwd=str(root))
            with patch("devtool.proot_distro_runner._is_termux_host_path", return_value=True), patch.dict(
                os.environ, {"PREFIX": str(prefix)}, clear=False
            ):
                wrapped = wrap_specs_for_proot_distro(
                    [spec], ProotRunnerConfig(distro="ubuntu"), repo_root=root, dry_run=True
                )[0]
            self.assertIn("clang", wrapped.argv)
            self.assertNotIn(str(fake), wrapped.argv)

    def test_python_proot_plan_uses_guest_python_and_repo_bound_venv_without_guest_devtool(self):
        from devtool.project_runners import PythonRunner
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "requirements.txt").write_text("pytest\n", encoding="utf-8")
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="python",
                execution_environment="proot-distro",
                python_executable="/data/data/com.termux/files/usr/bin/python3",
                runner_options={"python": {"manager": "venv", "test_framework": "pytest"}},
            )
            specs = PythonRunner().build_specs(config, "restore")
            self.assertTrue(specs)
            combined = " ".join(" ".join(spec.argv) for spec in specs)
            self.assertNotIn("/data/data/com.termux/files/usr/bin/python", combined)
            self.assertIn("python3 -m devtool.python_support environment-snapshot", combined)
            self.assertIn(str(root / ".devtool" / "proot" / "python"), combined)
            wrapped = wrap_specs_for_proot_distro(
                specs, ProotRunnerConfig(distro="ubuntu"),
                repo_root=root, dry_run=True,
            )
            wrapped_text = " ".join(" ".join(spec.argv) for spec in wrapped)
            self.assertIn("PYTHONPATH=/run/devtool/pythonpath", wrapped_text)
            self.assertIn("PYTHONPYCACHEPREFIX=/var/cache/devtool/pycache", wrapped_text)
            self.assertIn("CARGO_TARGET_DIR=/var/cache/devtool/native/cargo", wrapped_text)
            self.assertIn("SKBUILD_BUILD_DIR=/var/cache/devtool/native/scikit-build", wrapped_text)
            self.assertNotIn("/data/data/com.termux/files/usr/bin/python", wrapped_text)
            self.assertNotIn("/.cache/devtool/python/", wrapped_text)

    def test_proot_recomputes_host_cache_paths_against_guest_cache_bind(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            host_cache = Path.home() / ".cache" / "devtool" / "python" / "example" / "proot-distro"
            spec = CommandSpec(
                "build",
                ["python3", "-c", "print('ok')"],
                cwd=str(root),
                env={
                    "PYTHONPYCACHEPREFIX": str(host_cache / "pycache"),
                    "CARGO_TARGET_DIR": str(host_cache / "native" / "cargo"),
                    "SKBUILD_BUILD_DIR": str(host_cache / "native" / "scikit-build"),
                    "npm_config_cache": str(host_cache / "npm"),
                    "YARN_CACHE_FOLDER": str(host_cache / "yarn"),
                    "BUN_INSTALL_CACHE_DIR": str(host_cache / "bun"),
                    "XDG_CACHE_HOME": str(host_cache / "xdg"),
                    "CCACHE_DIR": str(host_cache / "ccache"),
                },
            )
            wrapped = wrap_specs_for_proot_distro(
                [spec], ProotRunnerConfig(distro="ubuntu"),
                repo_root=root, dry_run=True,
            )[0]
            text = " ".join(wrapped.argv)
            self.assertIn("PYTHONPYCACHEPREFIX=/var/cache/devtool/pycache", text)
            self.assertIn("CARGO_TARGET_DIR=/var/cache/devtool/native/cargo", text)
            self.assertIn("SKBUILD_BUILD_DIR=/var/cache/devtool/native/scikit-build", text)
            self.assertIn("npm_config_cache=/var/cache/devtool/npm", text)
            self.assertIn("YARN_CACHE_FOLDER=/var/cache/devtool/yarn", text)
            self.assertIn("BUN_INSTALL_CACHE_DIR=/var/cache/devtool/bun", text)
            self.assertIn("XDG_CACHE_HOME=/var/cache/devtool/xdg", text)
            self.assertIn("CCACHE_DIR=/var/cache/devtool/ccache", text)
            self.assertNotIn(str(host_cache), text)

    def test_python_helper_and_persistent_guest_cache_are_staged_without_guest_install(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as managed_td:
            root = Path(td)
            managed_root = Path(managed_td)
            stage_base = managed_root / "stage"
            cache_base = managed_root / "cache"
            spec = CommandSpec(
                "audit", ["python3", "-m", "devtool.python_support", "source-audit", str(root)], cwd=str(root)
            )
            cfg = ProotRunnerConfig(
                distro="ubuntu", stage_dir=str(stage_base), cache_dir=str(cache_base)
            )
            with patch("devtool.proot_distro_runner.proot_distro_available", return_value="proot-distro"), patch(
                "devtool.proot_distro_runner.installed_distros", return_value=("ubuntu",)
            ):
                wrapped = wrap_specs_for_proot_distro([spec], cfg, repo_root=root)[0]
            stage = Path(wrapped.metadata["proot_stage_dir"])
            self.assertTrue((stage / "pythonpath" / "devtool" / "python_support.py").is_file())
            self.assertTrue((stage / "pythonpath" / "devtool" / "xdg.py").is_file())
            self.assertTrue((cache_base / "ubuntu").is_dir())
            self.assertIn(f"{(cache_base / 'ubuntu').resolve()}:/var/cache/devtool", wrapped.argv)
            self.assertIn("PYTHONPATH=/run/devtool/pythonpath", wrapped.argv)
            self.assertIn("PIP_CACHE_DIR=/var/cache/devtool/pip", wrapped.argv)
            self.assertIn("GRADLE_USER_HOME=/var/cache/devtool/gradle", wrapped.argv)
            cleanup_proot_staging([wrapped])
            self.assertFalse(stage.exists())
            self.assertTrue((cache_base / "ubuntu").exists())

    def test_proot_execution_does_not_select_host_termux_cmake_provider(self):
        from devtool.project_runners import CMakeRunner
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "CMakeLists.txt").write_text("project(x)", encoding="utf-8")
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="cmake",
                execution_environment="proot-distro",
                runner_options={"cmake": {"provider": "auto"}},
            )
            with patch("devtool.project_runners.detect_current_environment", return_value=NATIVE_TERMUX), patch(
                "devtool.project_runners.termux_cmake_probe"
            ) as probe:
                spec = CMakeRunner().build_specs(config, "restore")[0]
            self.assertEqual(spec.argv[0], "cmake")
            # Project/source paths may legitimately live under the Termux home
            # when devtool itself is running natively on Android. The isolation
            # contract is about the selected *tool provider*: a Termux-host
            # CMake executable must never be passed into the PRoot Linux guest.
            self.assertFalse(spec.argv[0].startswith("/data/data/com.termux/files/usr/"))
            probe.assert_not_called()

    def test_termux_cache_root_rejects_shared_storage_xdg_cache(self):
        env = {
            "HOME": "/data/data/com.termux/files/home",
            "XDG_CACHE_HOME": "/sdcard/cache",
        }
        self.assertEqual(
            str(termux_cache_root(env)),
            "/data/data/com.termux/files/home/.cache/devtool/host-termux",
        )

    def test_native_termux_executable_preflight_rejects_wrong_architecture(self):
        with tempfile.TemporaryDirectory() as td:
            binary = Path(td) / "tool"
            _write_elf64(binary, machine=62)  # x86_64
            env = {**TERMUX_ENV, "PATH": td}
            with patch("devtool.termux_runtime.platform.machine", return_value="aarch64"):
                hint = native_termux_executable_hint([str(binary)], cwd=td, env=env)
        self.assertIsNotNone(hint)
        self.assertEqual(hint.kind, "abi-mismatch")
        self.assertIn("x86_64", hint.detail)
        self.assertIn("aarch64", hint.detail)

    def test_native_termux_executable_preflight_rejects_glibc_interpreter(self):
        with tempfile.TemporaryDirectory() as td:
            binary = Path(td) / "tool"
            _write_elf64(binary, machine=183, interpreter="/lib/ld-linux-aarch64.so.1")
            with patch("devtool.termux_runtime.platform.machine", return_value="aarch64"):
                hint = native_termux_executable_hint([str(binary)], cwd=td, env=TERMUX_ENV)
        self.assertIsNotNone(hint)
        self.assertEqual(hint.kind, "libc-mismatch")
        self.assertIn("Android/Bionic", hint.detail)

    def test_native_termux_executable_preflight_accepts_bionic_interpreter(self):
        with tempfile.TemporaryDirectory() as td:
            binary = Path(td) / "tool"
            _write_elf64(binary, machine=183, interpreter="/system/bin/linker64")
            with patch("devtool.termux_runtime.platform.machine", return_value="aarch64"):
                hint = native_termux_executable_hint([str(binary)], cwd=td, env=TERMUX_ENV)
        self.assertIsNone(hint)

    def test_prepare_specs_performs_native_elf_preflight_before_execution(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            binary = root / "linux-tool"
            _write_elf64(binary, machine=183, interpreter="/lib/ld-linux-aarch64.so.1")
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="command",
                execution_environment="native-termux",
            )
            args = build_parser().parse_args(["--execution-environment", "native-termux", "validate"])
            plan = resolve_execution_plan(requested="native-termux", env=TERMUX_ENV)
            with patch("devtool.cli._execution_plan_for_args", return_value=plan), patch.dict(
                os.environ, TERMUX_ENV, clear=True
            ), patch("devtool.termux_runtime.platform.machine", return_value="aarch64"):
                with self.assertRaisesRegex(SystemExit, "Native Termux executable preflight failed .*libc-mismatch"):
                    _prepare_specs_for_execution(
                        config, [CommandSpec("build", [str(binary)], cwd=str(root))], args
                    )

    def test_invalid_shared_storage_policy_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="python",
                execution_environment="native-termux",
                runner_options={"termux": {"storage": {"shared_storage_policy": "sometimes"}}},
            )
            args = build_parser().parse_args(["--execution-environment", "native-termux", "validate"])
            plan = resolve_execution_plan(requested="native-termux", env=TERMUX_ENV)
            with patch("devtool.cli._execution_plan_for_args", return_value=plan):
                with self.assertRaisesRegex(SystemExit, "Invalid Termux shared-storage policy"):
                    _prepare_specs_for_execution(config, [CommandSpec("test", ["true"], cwd=str(root))], args)

    def test_termux_resource_defaults_apply_to_proot_work_on_the_phone(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = ProjectConfig(
                repo_root=root, project_root=str(root), kind="python", execution_environment="auto",
                runner_options={},
            )
            args = build_parser().parse_args(["--proot-distro", "ubuntu", "validate"])
            with patch.dict(os.environ, TERMUX_ENV, clear=True):
                _apply_runner_cli_options(config, args)
            self.assertEqual(config.execution_environment, "proot-distro")
            self.assertIn("max_workers", config.runner_options.get("gradle", {}))
            self.assertIn("gradle_heap_mb", config.runner_options.get("validation", {}))

    def test_termux_resource_defaults_do_not_leak_into_ssh_commands(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for argv, execution_environment in (
                (["--execution-environment", "ssh", "validate"], "ssh"),
                (["--ssh", "build-host", "validate"], "auto"),
            ):
                with self.subTest(argv=argv):
                    config = ProjectConfig(
                        repo_root=root, project_root=str(root), kind="python",
                        execution_environment=execution_environment, runner_options={},
                    )
                    args = build_parser().parse_args(argv)
                    with patch.dict(os.environ, TERMUX_ENV, clear=True):
                        _apply_runner_cli_options(config, args)
                    self.assertNotIn("max_workers", config.runner_options.get("gradle", {}))
                    self.assertNotIn("gradle_heap_mb", config.runner_options.get("validation", {}))
                    self.assertNotIn("memory_guard_mb", config.runner_options.get("validation", {}))

    def test_proot_config_rejects_reserved_backend_environment(self):
        for item in (
            "DEVTOOL_EXECUTION_CONTEXT=chroot",
            "DEVTOOL_PROOT_DISTRO=evil",
            "DEVTOOL_INSIDE_TERMUX_CHROOT=1",
            "DEVTOOL_CHROOT_CONTEXT=evil",
            "DEVTOOL_SSH_MODE=evil",
        ):
            with self.subTest(item=item), self.assertRaisesRegex(ValueError, "devtool-managed backend variable"):
                validate_proot_config(ProotRunnerConfig(distro="ubuntu", env=[item]))

    def test_proot_drops_project_and_spec_backend_environment_overrides(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            spec = CommandSpec(
                "test", ["true"], cwd=str(root),
                env={
                    "DEVTOOL_EXECUTION_CONTEXT": "chroot",
                    "DEVTOOL_PROOT_DISTRO": "evil",
                    "DEVTOOL_INSIDE_TERMUX_CHROOT": "1",
                    "SAFE_VALUE": "ok",
                },
            )
            wrapped = wrap_specs_for_proot_distro(
                [spec], ProotRunnerConfig(distro="ubuntu"), repo_root=root, dry_run=True,
                project_env={"DEVTOOL_EXECUTION_OTHER": "bad", "OTHER_SAFE": "yes"},
            )[0]
            self.assertIn("DEVTOOL_EXECUTION_CONTEXT=proot-distro", wrapped.argv)
            self.assertIn("DEVTOOL_PROOT_DISTRO=ubuntu", wrapped.argv)
            self.assertNotIn("DEVTOOL_EXECUTION_CONTEXT=chroot", wrapped.argv)
            self.assertNotIn("DEVTOOL_PROOT_DISTRO=evil", wrapped.argv)
            self.assertNotIn("DEVTOOL_INSIDE_TERMUX_CHROOT=1", wrapped.argv)
            self.assertNotIn("DEVTOOL_EXECUTION_OTHER=bad", wrapped.argv)
            self.assertIn("SAFE_VALUE=ok", wrapped.argv)
            self.assertIn("OTHER_SAFE=yes", wrapped.argv)

    def test_termux_package_script_command_is_resolved_inside_proot_guest(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            prefix = root / "termux-prefix"
            gradle = prefix / "bin" / "gradle"
            gradle.parent.mkdir(parents=True)
            gradle.write_text("#!/data/data/com.termux/files/usr/bin/sh\necho gradle\n", encoding="utf-8")
            gradle.chmod(0o755)
            spec = CommandSpec("build", [str(gradle), "--version"], cwd=str(root))
            with patch.dict(os.environ, {"PREFIX": str(prefix)}, clear=False):
                wrapped = wrap_specs_for_proot_distro(
                    [spec], ProotRunnerConfig(distro="ubuntu"), repo_root=root, dry_run=True
                )[0]
            self.assertIn("gradle", wrapped.argv)
            self.assertNotIn(str(gradle), wrapped.argv)
            self.assertFalse(any("/run/devtool/" in token and token.endswith("gradle") for token in wrapped.argv))



if __name__ == "__main__":
    unittest.main()
