from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from devtool.clipboard import ClipboardResult
from devtool.execution import build_chroot_exec_argv, wrap_specs_for_chroot
from devtool.models import CommandSpec
from devtool.termux_chroot_runner import build_inner_command, build_runner_command, copy_from_machine_file, prepare_host_clipboard_run, translate_devtool_args


class ChrootExecutionWrapperTests(unittest.TestCase):
    def test_wrap_specs_uses_termux_chrootctl_zsh_shape(self) -> None:
        spec = CommandSpec("build", ["dotnet", "build", "Demo.sln", "/p:DefineConstants=A B"])
        wrapped = wrap_specs_for_chroot([spec], "androidos")
        self.assertEqual(wrapped[0].argv[:6], ["sudo", "chrootctl", "--name", "androidos", "exec", "--zsh"])
        self.assertEqual(wrapped[0].argv[6], "-lc")
        self.assertEqual(wrapped[0].argv[7], "dotnet build Demo.sln '/p:DefineConstants=A B'")
        self.assertEqual(wrapped[0].metadata["execution_mode"], "chroot")
        self.assertEqual(wrapped[0].metadata["chroot"], "androidos")

    def test_chroot_exec_can_skip_sudo_with_env(self) -> None:
        with patch.dict("os.environ", {"DEVTOOL_CHROOT_SUDO": ""}, clear=False):
            argv = build_chroot_exec_argv("echo ok", "androidos")
        self.assertEqual(argv, ["chrootctl", "--name", "androidos", "exec", "--zsh", "-lc", "echo ok"])


class TermuxChrootRunnerTests(unittest.TestCase):
    def test_translates_core_path_flags_and_consumes_chroot_flag(self) -> None:
        env = {
            "HOME": "/data/data/com.termux/files/home",
            "DEVTOOL_TERMUX_CHROOT_USER": "mike",
            "DEVTOOL_CHROOTCTL": "chrootctl",
            "DEVTOOL_CHROOT_SUDO": "sudo",
        }
        config, forwarded = translate_devtool_args(
            [
                "--chroot", "androidos",
                "--repo", "/data/data/com.termux/files/home/Code/sims3tools",
                "apply-overlay", "--validate",
                "/data/data/com.termux/files/home/Downloads/fix.zip",
                "--machine-file=/data/data/com.termux/files/home/out/result.json",
            ],
            env=env,
            cwd="/data/data/com.termux/files/home/Code/sims3tools",
        )
        self.assertEqual(config.chroot_name, "androidos")
        self.assertNotIn("--chroot", forwarded)
        self.assertEqual(config.cwd, "/home/mike/Code/sims3tools")
        self.assertIn("/home/mike/Code/sims3tools", forwarded)
        self.assertIn("/home/mike/Downloads/fix.zip", forwarded)
        self.assertIn("--machine-file=/home/mike/out/result.json", forwarded)

    def test_batch_apply_steps_are_translated(self) -> None:
        env = {"HOME": "/data/data/com.termux/files/home", "DEVTOOL_TERMUX_CHROOT_HOME": "/home/mike"}
        _, forwarded = translate_devtool_args(
            ["batch", "restore", "apply-overlay=/data/data/com.termux/files/home/Downloads/fix.zip", "validate"],
            env=env,
            cwd="/data/data/com.termux/files/home/Code/demo",
        )
        self.assertIn("apply-overlay=/home/mike/Downloads/fix.zip", forwarded)


    def test_copy_flag_is_host_handled_with_machine_file(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            env = {"HOME": home, "DEVTOOL_TERMUX_CHROOT_HOME": "/home/mike"}
            config, forwarded = translate_devtool_args(
                ["--copy", "--copy-via", "termux", "--repo", f"{home}/Code/demo", "validate"],
                env=env,
                cwd=f"{home}/Code/demo",
            )
            self.assertEqual(config.copy_mode, "raw")
            self.assertEqual(config.copy_via, "termux")
            self.assertNotIn("--copy", forwarded)
            self.assertNotIn("--copy-via", forwarded)

            with patch.dict("os.environ", {"HOME": home}, clear=False):
                prepared, machine_path, cleanup = prepare_host_clipboard_run(config, forwarded)

        self.assertTrue(cleanup)
        self.assertIsNotNone(machine_path)
        self.assertIn("--machine-file", prepared)
        self.assertIn("--no-clipboard", prepared)
        machine_index = prepared.index("--machine-file")
        self.assertTrue(prepared[machine_index + 1].startswith("/home/mike/"))

    def test_copy_from_machine_file_uses_host_clipboard_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = {
                "clipboard": {
                    "raw": "raw output",
                    "summary": "summary output",
                    "json": {"warnings": 0, "errors": 0, "failed": 0},
                }
            }
            path = Path(tmp) / "machine.json"
            path.write_text(__import__("json").dumps(payload), encoding="utf-8")
            copied: list[tuple[str, str]] = []

            def fake_copy(text: str, preferred: str = "auto"):
                copied.append((text, preferred))
                return ClipboardResult(True, backend="termux-clipboard-set")

            with patch("devtool.termux_chroot_runner.copy_text", side_effect=fake_copy):
                self.assertTrue(copy_from_machine_file(path, "raw", "termux"))

        self.assertEqual(copied, [("raw output", "termux")])

    def test_builds_exact_outer_command(self) -> None:
        env = {"HOME": "/data/data/com.termux/files/home", "DEVTOOL_TERMUX_CHROOT_HOME": "/home/mike"}
        config, forwarded = translate_devtool_args(
            ["validate", "--skip-test"],
            env=env,
            cwd="/data/data/com.termux/files/home/Code/demo",
        )
        outer = build_runner_command(config, forwarded)
        self.assertEqual(outer[:7], ["sudo", "chrootctl", "--name", "androidos", "exec", "--zsh", "-lc"])
        self.assertEqual(outer[7], "cd /home/mike/Code/demo && exec devtool validate --skip-test")
        self.assertEqual(build_inner_command(config, forwarded), outer[7])


if __name__ == "__main__":
    unittest.main()
