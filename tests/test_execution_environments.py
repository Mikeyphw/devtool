from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import argparse

from devtool.cli import _execution_plan_for_args, _maybe_prepare_termux_auto_chroot, _prepare_specs_for_execution
from devtool.execution_environments import (
    AUTO,
    ANDROID_DEVICE,
    CHROOT,
    DESKTOP_LINUX,
    NATIVE_TERMUX,
    assert_chroot_path_safe,
    default_termux_chroot_path_map,
    detect_current_environment,
    inside_chroot,
    preflight_execution,
    resolve_execution_plan,
    translate_guest_path,
    translate_host_path,
)
from devtool.models import CommandSpec, ProjectConfig


class ExecutionEnvironmentDetectionTests(unittest.TestCase):
    def test_detects_native_termux(self) -> None:
        env = {
            "HOME": "/data/data/com.termux/files/home",
            "PREFIX": "/data/data/com.termux/files/usr",
            "TERMUX_VERSION": "0.119",
        }
        self.assertEqual(detect_current_environment(env), NATIVE_TERMUX)

    def test_chroot_guard_wins_over_termux_paths(self) -> None:
        env = {
            "HOME": "/data/data/com.termux/files/home",
            "TERMUX_VERSION": "0.119",
            "DEVTOOL_EXECUTION_CONTEXT": "chroot",
        }
        self.assertTrue(inside_chroot(env))
        self.assertEqual(detect_current_environment(env), CHROOT)

    def test_desktop_is_default(self) -> None:
        self.assertEqual(detect_current_environment({"HOME": "/home/mike"}), DESKTOP_LINUX)


class ExecutionPlanTests(unittest.TestCase):
    def test_auto_keeps_current_environment(self) -> None:
        env = {"HOME": "/home/mike"}
        plan = resolve_execution_plan(env=env)
        self.assertEqual(plan.current, DESKTOP_LINUX)
        self.assertEqual(plan.effective, DESKTOP_LINUX)
        self.assertFalse(plan.delegated)

    def test_chroot_request_is_not_delegated_again_inside_chroot(self) -> None:
        env = {
            "HOME": "/home/mike",
            "DEVTOOL_EXECUTION_CONTEXT": "chroot",
            "DEVTOOL_CHROOT_CONTEXT": "androidos",
        }
        plan = resolve_execution_plan(requested=CHROOT, chroot_name="androidos", env=env)
        self.assertEqual(plan.effective, CHROOT)
        self.assertFalse(plan.delegated)
        self.assertIn("recursive delegation suppressed", plan.reason)

    def test_device_environment_is_modelled_but_preflight_reports_backend_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plan = resolve_execution_plan(requested=ANDROID_DEVICE, env={"HOME": "/home/mike"})
            result = preflight_execution(Path(tmp), plan)
        self.assertFalse(result.ok)
        self.assertEqual(result.failures()[0].name, "device-backend")


class SharedPathMappingTests(unittest.TestCase):
    def test_default_termux_home_mapping_and_round_trip(self) -> None:
        env = {"HOME": "/data/data/com.termux/files/home"}
        mappings = default_termux_chroot_path_map(env)
        host = "/data/data/com.termux/files/home/Code/demo"
        guest = translate_host_path(host, mappings)
        self.assertEqual(guest, "/home/termux/Code/demo")
        self.assertEqual(translate_guest_path(guest, mappings), host)


    def test_desktop_home_never_creates_automatic_chroot_mapping(self) -> None:
        mappings = default_termux_chroot_path_map({"HOME": "/home/mike"})
        self.assertFalse(any(item.host == "/home/mike" for item in mappings))

    def test_termux_paths_are_rejected_inside_chroot_plans(self) -> None:
        with self.assertRaisesRegex(ValueError, "native Termux path"):
            assert_chroot_path_safe(
                ["python", "/data/data/com.termux/files/usr/bin/python3.14"],
                context="unit-test",
            )

    def test_explicit_mapping_takes_priority(self) -> None:
        env = {
            "HOME": "/data/data/com.termux/files/home",
            "DEVTOOL_TERMUX_CHROOT_PATH_MAP": "/data/data/com.termux/files/home=/home/mike",
        }
        mappings = default_termux_chroot_path_map(env)
        self.assertEqual(
            translate_host_path("/data/data/com.termux/files/home/Code/demo", mappings),
            "/home/mike/Code/demo",
        )

    def test_chroot_preflight_checks_shared_path_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "HOME": tmp,
                "TERMUX_VERSION": "0.119",
                "DEVTOOL_TERMUX_CHROOT_HOME": "/home/mike",
            }
            plan = resolve_execution_plan(requested=CHROOT, env=env)
            with patch("devtool.execution_environments.shutil.which", return_value="/usr/bin/chrootctl"):
                result = preflight_execution(Path(tmp), plan)
        self.assertTrue(result.ok)
        self.assertIn("/home/mike", next(c.detail for c in result.checks if c.name == "path-map"))


class CliExecutionIntegrationTests(unittest.TestCase):
    @staticmethod
    def _args(**values):
        defaults = {
            "host": False,
            "chroot": "",
            "execution_environment": "auto",
            "matrix": "",
            "matrix_mode": "rid",
            "dry_run": False,
            "_devtool_termux_host_chroot_backend": False,
            "_devtool_chroot_path_map": None,
            "_devtool_chroot_name": "",
        }
        defaults.update(values)
        return argparse.Namespace(**defaults)

    def test_configured_environment_is_used_when_cli_is_auto(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ProjectConfig(
                repo_root=Path(tmp),
                kind="python",
                execution_environment="chroot",
                chroot_name="ubuntu",
            )
            with patch.dict("os.environ", {"HOME": "/home/mike"}, clear=True):
                plan = _execution_plan_for_args(config, self._args())
        self.assertEqual(plan.effective, CHROOT)
        self.assertEqual(plan.chroot_name, "ubuntu")


    def test_termux_auto_chroot_falls_back_when_repository_is_unmapped(self) -> None:
        config = ProjectConfig(
            repo_root=Path("/data/data/com.termux/files/usr/tmp/unmapped-project"),
            kind="android",
            execution_environment="auto",
            chroot_name="androidos",
        )
        env = {
            "HOME": "/data/data/com.termux/files/home",
            "PREFIX": "/data/data/com.termux/files/usr",
            "TERMUX_VERSION": "0.119",
        }
        with patch.dict("os.environ", env, clear=True), \
                patch("devtool.android_integration.shutil.which", return_value="/data/data/com.termux/files/usr/bin/chrootctl"):
            plan = _execution_plan_for_args(config, self._args())

        self.assertEqual(plan.requested, AUTO)
        self.assertEqual(plan.effective, NATIVE_TERMUX)
        self.assertIn("not mapped", plan.reason)

    def test_termux_explicit_chroot_keeps_strict_unmapped_preflight(self) -> None:
        config = ProjectConfig(
            repo_root=Path("/data/data/com.termux/files/usr/tmp/unmapped-project"),
            kind="android",
            execution_environment="chroot",
            chroot_name="androidos",
        )
        env = {
            "HOME": "/data/data/com.termux/files/home",
            "PREFIX": "/data/data/com.termux/files/usr",
            "TERMUX_VERSION": "0.119",
        }
        with patch.dict("os.environ", env, clear=True), \
                patch("devtool.execution_environments.shutil.which", return_value="/data/data/com.termux/files/usr/bin/chrootctl"):
            plan = _execution_plan_for_args(config, self._args())
            preflight = preflight_execution(config.repo_root, plan)

        self.assertEqual(plan.effective, CHROOT)
        self.assertTrue(any(item.name == "path-map" and item.status == "incompatible" for item in preflight.checks))

    def test_desktop_chroot_request_fails_before_command_wrapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ProjectConfig(
                repo_root=Path(tmp),
                kind="python",
                execution_environment="chroot",
                chroot_name="androidos",
            )
            spec = CommandSpec("test", ["python", "-m", "pytest"])
            with patch.dict("os.environ", {"HOME": "/home/mike"}, clear=True), \
                    patch("devtool.execution_environments.shutil.which", return_value="/usr/bin/chrootctl"):
                with self.assertRaisesRegex(SystemExit, "only from native Termux"):
                    _prepare_specs_for_execution(config, [spec], self._args())

    def test_inside_chroot_rejects_termux_interpreter_from_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ProjectConfig(repo_root=Path(tmp), kind="python", execution_environment="chroot")
            spec = CommandSpec("test", ["/data/data/com.termux/files/usr/bin/python3.14", "-V"])
            env = {
                "HOME": "/home/termux",
                "DEVTOOL_EXECUTION_CONTEXT": "chroot",
                "DEVTOOL_CHROOT_CONTEXT": "androidos",
            }
            with patch.dict("os.environ", env, clear=True):
                with self.assertRaisesRegex(ValueError, "native Termux path"):
                    _prepare_specs_for_execution(config, [spec], self._args())

    def test_desktop_auto_chroot_hook_never_loads_or_invokes_chroot(self) -> None:
        args = self._args(no_auto_chroot=False, command="validate")
        with patch.dict("os.environ", {"HOME": "/home/mike"}, clear=True), \
                patch("devtool.cli.load_project_config") as load_config:
            self.assertIsNone(_maybe_prepare_termux_auto_chroot(["validate"], args))
        load_config.assert_not_called()

    def test_chroot_context_suppresses_recursive_wrapping_and_preserves_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ProjectConfig(
                repo_root=Path(tmp),
                kind="python",
                execution_environment="chroot",
                chroot_name="androidos",
            )
            original = CommandSpec("test", ["python", "-m", "pytest"])
            env = {
                "HOME": "/home/mike",
                "DEVTOOL_EXECUTION_CONTEXT": "chroot",
                "DEVTOOL_CHROOT_CONTEXT": "androidos",
            }
            with patch.dict("os.environ", env, clear=True):
                prepared = _prepare_specs_for_execution(config, [original], self._args())

        self.assertEqual(prepared[0].argv, ["python", "-m", "pytest"])
        self.assertEqual(prepared[0].metadata["execution_environment"], CHROOT)
        self.assertFalse(prepared[0].metadata["execution_delegated"])
        self.assertTrue(prepared[0].metadata["execution_preflight"])


if __name__ == "__main__":
    unittest.main()
