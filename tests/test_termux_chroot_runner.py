from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from devtool.clipboard import ClipboardResult
from devtool.execution import build_chroot_exec_argv, wrap_specs_for_chroot
from devtool.models import CommandSpec, ProjectConfig
from devtool.termux_chroot_runner import _plain_log_path_for, build_inner_command, build_runner_command, copy_from_machine_file, normalize_hud_mode_for_termux_host, prepare_host_clipboard_run, repair_termux_home_mapping, should_delegate_full_for_chroot_only_repo, translate_devtool_args, wrap_specs_for_termux_host_chroot
from devtool.runners import run_specs


class ChrootExecutionWrapperTests(unittest.TestCase):
    def test_wrap_specs_uses_termux_chrootctl_zsh_shape(self) -> None:
        spec = CommandSpec("build", ["dotnet", "build", "Demo.sln", "/p:DefineConstants=A B"])
        wrapped = wrap_specs_for_chroot([spec], "androidos")
        self.assertEqual(wrapped[0].argv[:6], ["sudo", "chrootctl", "--name", "androidos", "exec", "--"])
        self.assertEqual(wrapped[0].argv[6], "zsh")
        self.assertEqual(wrapped[0].argv[7], "-lc")
        self.assertEqual(wrapped[0].argv[8], "dotnet build Demo.sln '/p:DefineConstants=A B'")
        self.assertEqual(wrapped[0].metadata["execution_mode"], "chroot")
        self.assertEqual(wrapped[0].metadata["chroot"], "androidos")
        self.assertEqual(wrapped[0].metadata["progress_argv"], spec.argv)

    def test_chroot_exec_can_skip_sudo_with_env(self) -> None:
        with patch.dict("os.environ", {"DEVTOOL_CHROOT_SUDO": ""}, clear=False):
            argv = build_chroot_exec_argv("echo ok", "androidos")
        self.assertEqual(argv, ["chrootctl", "--name", "androidos", "exec", "--", "zsh", "-lc", "echo ok"])


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


    def test_explicit_repo_becomes_full_chroot_start_directory(self) -> None:
        env = {"HOME": "/data/data/com.termux/files/home", "DEVTOOL_TERMUX_CHROOT_HOME": "/home/mike"}
        config, forwarded = translate_devtool_args(
            ["--repo", "/home/mike/Code/chroot-only", "validate"],
            env=env,
            cwd="/data/data/com.termux/files/home",
        )
        self.assertEqual(config.cwd, "/home/mike/Code/chroot-only")
        self.assertIn("/home/mike/Code/chroot-only", forwarded)
        self.assertTrue(build_inner_command(config, forwarded).startswith("cd /home/mike/Code/chroot-only &&"))

    def test_repairs_default_home_mapping_when_repo_is_under_another_chroot_user(self) -> None:
        env = {"HOME": "/data/data/com.termux/files/home"}
        mappings = [(env["HOME"], "/home/termux"), ("/mnt", "/mnt")]
        with (
            patch("devtool.termux_chroot_runner.chroot_directory_exists", return_value=False),
            patch("devtool.termux_chroot_runner.discover_chroot_home_for_termux_path", return_value="/home/mike"),
        ):
            repaired = repair_termux_home_mapping(
                mappings,
                host_path=env["HOME"] + "/Code/demo",
                chroot_name="androidos",
                env=env,
            )
        self.assertEqual(repaired[0], (env["HOME"], "/home/mike"))
        self.assertNotIn((env["HOME"], "/home/termux"), repaired)

    def test_chroot_only_repo_requests_full_delegation(self) -> None:
        env = {"HOME": "/data/data/com.termux/files/home", "DEVTOOL_TERMUX_CHROOT_HOME": "/home/mike"}
        with tempfile.TemporaryDirectory() as tmp, patch(
            "devtool.termux_chroot_runner.chroot_directory_exists", return_value=True
        ) as exists:
            missing = str(Path(tmp) / "not-visible-on-host")
            self.assertTrue(should_delegate_full_for_chroot_only_repo(
                ["--repo", missing, "validate"],
                chroot_name="androidos",
                env=env,
                cwd=tmp,
            ))
        exists.assert_called_once_with(missing, "androidos", env=env)

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
        self.assertEqual(outer[:8], ["sudo", "chrootctl", "--name", "androidos", "exec", "--", "zsh", "-lc"])
        self.assertEqual(outer[8], "cd /home/mike/Code/demo && exec env DEVTOOL_INSIDE_TERMUX_CHROOT=1 DEVTOOL_CHROOT_CONTEXT=androidos ~/.local/bin/devtool validate --skip-test")
        self.assertEqual(build_inner_command(config, forwarded), outer[8])

    def test_plain_log_path_drops_ansi_suffix(self) -> None:
        path = Path("/tmp/devtool-chroot-20260706-235754.ansi.log")
        self.assertEqual(_plain_log_path_for(path), Path("/tmp/devtool-chroot-20260706-235754.log"))

    def test_inner_command_uses_exec_env_assignments(self) -> None:
        env = {"HOME": "/data/data/com.termux/files/home", "DEVTOOL_TERMUX_CHROOT_HOME": "/home/mike"}
        config, forwarded = translate_devtool_args(["validate"], env=env, cwd="/data/data/com.termux/files/home/Code/demo")
        inner = build_inner_command(config, forwarded)
        self.assertIn("exec env DEVTOOL_INSIDE_TERMUX_CHROOT=1", inner)
        self.assertNotIn("exec DEVTOOL_INSIDE_TERMUX_CHROOT=1", inner)


    def test_inner_command_preserves_shared_run_id(self) -> None:
        env = {"HOME": "/data/data/com.termux/files/home", "DEVTOOL_TERMUX_CHROOT_HOME": "/home/termux"}
        config, forwarded = translate_devtool_args(["validate"], env=env, cwd="/data/data/com.termux/files/home/Code/demo")
        with patch.dict("os.environ", {"DEVTOOL_RUN_ID": "shared-run"}, clear=False):
            inner = build_inner_command(config, forwarded)
        self.assertIn("DEVTOOL_RUN_ID=shared-run", inner)

    def test_default_termux_home_maps_to_home_termux(self) -> None:
        env = {"HOME": "/data/data/com.termux/files/home"}
        config, forwarded = translate_devtool_args(
            ["--repo", "/data/data/com.termux/files/home/Code/sims3tools", "validate"],
            env=env,
            cwd="/data/data/com.termux/files/home/Code/sims3tools",
        )
        self.assertEqual(config.cwd, "/home/termux/Code/sims3tools")
        self.assertIn("/home/termux/Code/sims3tools", forwarded)

    def test_host_chroot_wrapper_cd_and_translates_absolute_paths(self) -> None:
        env = {"HOME": "/data/data/com.termux/files/home"}
        spec = CommandSpec("build", ["dotnet", "build", "/data/data/com.termux/files/home/Code/demo/Demo.sln"])
        wrapped = wrap_specs_for_termux_host_chroot(
            [spec],
            "androidos",
            repo_root="/data/data/com.termux/files/home/Code/demo",
            env=env,
        )
        self.assertEqual(wrapped[0].argv[:8], ["sudo", "chrootctl", "--name", "androidos", "exec", "--", "zsh", "-lc"])
        self.assertEqual(wrapped[0].argv[8], "cd /home/termux/Code/demo && dotnet build /home/termux/Code/demo/Demo.sln")
        self.assertEqual(wrapped[0].metadata["execution_mode"], "termux-host-chroot")
        self.assertEqual(wrapped[0].metadata["chroot_cwd"], "/home/termux/Code/demo")
        self.assertEqual(wrapped[0].metadata["progress_argv"], spec.argv)
        self.assertEqual(wrapped[0].metadata["progress_cwd"], "/data/data/com.termux/files/home/Code/demo")

    def test_run_specs_uses_original_dotnet_command_for_wrapped_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            solution = repo / "Demo.sln"
            solution.write_text("", encoding="utf-8")
            original = CommandSpec("build", ["dotnet", "build", str(solution)])
            wrapped = wrap_specs_for_termux_host_chroot(
                [original],
                "androidos",
                repo_root=repo,
                env={"HOME": "/data/data/com.termux/files/home"},
                path_map=[(str(repo), "/home/termux/Code/demo")],
            )
            config = ProjectConfig(repo_root=repo, kind="dotnet")

            with (
                patch("devtool.runners.dotnet_progress_plan", return_value=object()) as plan,
                patch("devtool.runners._run_process_live", return_value=(0, "Build succeeded.")) as run_live,
            ):
                results, _raw = run_specs(config, wrapped, verbose=False)

            self.assertEqual(results[0].returncode, 0)
            plan.assert_called_once_with(original.argv, str(repo))
            kwargs = run_live.call_args.kwargs
            self.assertEqual(kwargs["progress_argv"], original.argv)
            self.assertEqual(kwargs["progress_cwd"], str(repo))
            self.assertNotEqual(run_live.call_args.args[0], original.argv)

    def test_host_mode_translates_desktop_hud_to_android_gui(self) -> None:
        self.assertEqual(normalize_hud_mode_for_termux_host("desktop-window"), "android-bridge")
        self.assertEqual(normalize_hud_mode_for_termux_host("android-bridge"), "android-bridge")
        self.assertEqual(normalize_hud_mode_for_termux_host("android-notify"), "android-bridge")


if __name__ == "__main__":
    unittest.main()
