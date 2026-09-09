from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from devtool.clipboard import ClipboardResult
from devtool.execution import build_chroot_exec_argv, wrap_specs_for_chroot
from devtool.models import CommandSpec, ProjectConfig
from devtool.termux_chroot_runner import ChrootIdentity, _chroot_user_switch_probe_timeout, _emit_chroot_inactivity_hint, _identity_switch_command, _inactivity_hint_seconds, _plain_log_path_for, build_inner_command, build_runner_command, copy_from_machine_file, normalize_hud_mode_for_termux_host, prepare_host_clipboard_run, repair_termux_home_mapping, run_auto_chroot, should_delegate_full_for_chroot_only_repo, translate_devtool_args, wrap_specs_for_termux_host_chroot
from devtool.runners import run_specs


class ChrootExecutionWrapperTests(unittest.TestCase):
    def test_wrap_specs_uses_termux_chrootctl_zsh_shape(self) -> None:
        spec = CommandSpec("build", ["dotnet", "build", "Demo.sln", "/p:DefineConstants=A B"])
        wrapped = wrap_specs_for_chroot([spec], "androidos")
        self.assertEqual(wrapped[0].argv[:6], ["sudo", "chrootctl", "--name", "androidos", "exec", "--"])
        self.assertEqual(wrapped[0].argv[6], "zsh")
        self.assertEqual(wrapped[0].argv[7], "-lc")
        self.assertEqual(
            wrapped[0].argv[8],
            'export PATH=/home/linuxbrew/.linuxbrew/bin:"$PATH"; exec env DEVTOOL_EXECUTION_CONTEXT=chroot DEVTOOL_INSIDE_TERMUX_CHROOT=1 DEVTOOL_CHROOT_CONTEXT=androidos dotnet build Demo.sln \'/p:DefineConstants=A B\'',
        )
        self.assertEqual(wrapped[0].metadata["execution_mode"], "chroot")
        self.assertEqual(wrapped[0].metadata["chroot"], "androidos")
        self.assertEqual(wrapped[0].metadata["progress_argv"], spec.argv)

    def test_chroot_exec_can_skip_sudo_with_env(self) -> None:
        with patch.dict("os.environ", {"DEVTOOL_CHROOT_SUDO": ""}, clear=False):
            argv = build_chroot_exec_argv("echo ok", "androidos")
        self.assertEqual(
            argv,
            ["chrootctl", "--name", "androidos", "exec", "--", "zsh", "-lc", 'export PATH=/home/linuxbrew/.linuxbrew/bin:"$PATH"; echo ok'],
        )


class TermuxChrootRunnerTests(unittest.TestCase):
    @staticmethod
    def identity(user: str = "mike", home: str = "/home/mike") -> ChrootIdentity:
        return ChrootIdentity(user=user, home=home, shell="/bin/zsh")

    def test_translates_core_path_flags_and_consumes_chroot_flag(self) -> None:
        env = {
            "HOME": "/data/data/com.termux/files/home",
            "DEVTOOL_TERMUX_CHROOT_USER": "mike",
            "DEVTOOL_TERMUX_CHROOT_HOME": "/home/mike",
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
        self.assertIn("cd /home/mike/Code/chroot-only &&", build_inner_command(config, forwarded, identity=self.identity()))

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
        expected = missing
        termux_home = env["HOME"].rstrip("/")
        if missing.startswith(termux_home + "/"):
            expected = env["DEVTOOL_TERMUX_CHROOT_HOME"].rstrip("/") + missing[len(termux_home):]
        exists.assert_called_once_with(expected, "androidos", env=env)

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
            env = {"HOME": home, "PREFIX": "/data/data/com.termux/files/usr", "TERMUX_VERSION": "0.119", "DEVTOOL_TERMUX_CHROOT_HOME": "/home/mike"}
            config, forwarded = translate_devtool_args(
                ["--copy", "--copy-via", "termux", "--repo", f"{home}/Code/demo", "validate"],
                env=env,
                cwd=f"{home}/Code/demo",
            )
            self.assertEqual(config.copy_mode, "summary")
            self.assertEqual(config.copy_via, "termux")
            self.assertNotIn("--copy", forwarded)
            self.assertNotIn("--copy-via", forwarded)

            with patch.dict("os.environ", {"HOME": home, "DEVTOOL_XDG_DIR": "", "XDG_CACHE_HOME": ""}, clear=False):
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
        with patch("devtool.termux_chroot_runner.resolve_chroot_identity", return_value=self.identity()):
            outer = build_runner_command(config, forwarded)
        self.assertEqual(outer[:8], ["sudo", "chrootctl", "--name", "androidos", "exec", "--", "zsh", "-lc"])
        self.assertIn("runuser -u mike", outer[8])
        self.assertIn("HOME=/home/mike", outer[8])
        self.assertIn("cd /home/mike/Code/demo && exec env DEVTOOL_EXECUTION_CONTEXT=chroot", outer[8])
        self.assertNotIn("/sdcard", outer[8])
        self.assertNotIn("/storage/emulated", outer[8])
        inner = build_inner_command(config, forwarded, identity=self.identity())
        self.assertIn("runuser -u mike", inner)
        self.assertIn("cd /home/mike/Code/demo && exec env DEVTOOL_EXECUTION_CONTEXT=chroot", inner)

    def test_plain_log_path_drops_ansi_suffix(self) -> None:
        path = Path("/tmp/devtool-chroot-20260706-235754.ansi.log")
        self.assertEqual(_plain_log_path_for(path), Path("/tmp/devtool-chroot-20260706-235754.log"))

    def test_inner_command_uses_exec_env_assignments(self) -> None:
        env = {"HOME": "/data/data/com.termux/files/home", "DEVTOOL_TERMUX_CHROOT_HOME": "/home/mike"}
        config, forwarded = translate_devtool_args(["validate"], env=env, cwd="/data/data/com.termux/files/home/Code/demo")
        inner = build_inner_command(config, forwarded, identity=self.identity())
        self.assertIn("exec env DEVTOOL_EXECUTION_CONTEXT=chroot DEVTOOL_INSIDE_TERMUX_CHROOT=1", inner)
        self.assertNotIn("exec DEVTOOL_INSIDE_TERMUX_CHROOT=1", inner)


    def test_user_switch_probe_is_timeout_guarded_and_has_plain_su_fallback(self) -> None:
        with patch.dict("os.environ", {"DEVTOOL_CHROOT_USER_SWITCH_TIMEOUT": "3"}, clear=False):
            command = _identity_switch_command(self.identity(), "echo ok")
        self.assertIn("devtool_user_switch_probe()", command)
        self.assertIn("timeout -k 2s 3s", command)
        self.assertIn("devtool_user_switch_probe runuser -u mike -- /bin/zsh -c ':'", command)
        self.assertIn("devtool_user_switch_probe sudo -H -u mike env HOME=/home/mike USER=mike LOGNAME=mike /bin/zsh -c ':'", command)
        self.assertIn("devtool_user_switch_probe su -s /bin/zsh mike -c ':'", command)
        self.assertIn("devtool_user_switch_probe su -l -s /bin/zsh mike -c ':'", command)
        self.assertNotIn("devtool_user_switch_probe su mike -s /bin/zsh", command)
        self.assertNotIn("devtool_user_switch_probe su - mike -s /bin/zsh", command)
        self.assertIn("runuser/sudo/su probes failed or timed out after 3s", command)


    def test_user_switch_su_exec_uses_util_linux_safe_option_order(self) -> None:
        command = _identity_switch_command(self.identity(), "echo ok")
        self.assertIn("elif command -v sudo", command)
        self.assertIn("then exec sudo -H -u mike env HOME=/home/mike USER=mike LOGNAME=mike /bin/zsh -lc", command)
        self.assertIn("then exec su -s /bin/zsh mike -c", command)
        self.assertIn("then exec su -l -s /bin/zsh mike -c", command)
        self.assertNotIn("exec su mike -s /bin/zsh", command)
        self.assertNotIn("exec su - mike -s /bin/zsh", command)

    def test_user_switch_probe_timeout_is_bounded(self) -> None:
        self.assertEqual(_chroot_user_switch_probe_timeout({"DEVTOOL_CHROOT_USER_SWITCH_TIMEOUT": "0"}), 1)
        self.assertEqual(_chroot_user_switch_probe_timeout({"DEVTOOL_CHROOT_USER_SWITCH_TIMEOUT": "9999"}), 300)
        self.assertEqual(_chroot_user_switch_probe_timeout({"DEVTOOL_CHROOT_USER_SWITCH_TIMEOUT": "bad"}), 8)

    def test_user_switch_probe_has_bounded_shell_fallback_without_timeout_binary(self) -> None:
        with patch.dict("os.environ", {"DEVTOOL_CHROOT_USER_SWITCH_TIMEOUT": "4"}, clear=False):
            command = _identity_switch_command(self.identity(), "echo ok")
        self.assertIn("else \"$@\" & devtool_probe_pid=$!", command)
        self.assertIn("devtool_probe_deadline=4", command)
        self.assertIn("while kill -0 \"$devtool_probe_pid\"", command)
        self.assertIn("kill -9 \"$devtool_probe_pid\"", command)
        self.assertIn("return 124", command)
        self.assertIn("wait \"$devtool_probe_pid\"", command)

    def test_auto_chroot_android_bridge_resolves_identity_from_path_map(self) -> None:
        env = {
            "HOME": "/data/data/com.termux/files/home",
            "PREFIX": "/data/data/com.termux/files/usr",
            "TERMUX_VERSION": "0.119",
            "DEVTOOL_TERMUX_CHROOT_HOME": "/home/mike",
            "DEVTOOL_TERMUX_CHROOT_USER": "mike",
            "DEVTOOL_TERMUX_CHROOT_STALL_HINT_SECONDS": "0",
        }
        calls: list[tuple[str, tuple[tuple[str, str], ...]]] = []

        def fake_resolve(chroot_name, mappings, *, env=None):
            calls.append((chroot_name, tuple(mappings)))
            return self.identity()

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("devtool.termux_chroot_runner.resolve_chroot_identity", side_effect=fake_resolve),
            patch("devtool.termux_chroot_runner.build_runner_command", return_value=["/bin/sh", "-c", "printf ok"]),
            patch("devtool.termux_chroot_runner._start_android_hud_bridge"),
            patch("devtool.termux_chroot_runner._run_live_pty", return_value=0),
            patch.dict("os.environ", {"DEVTOOL_XDG_DIR": tmp}, clear=False),
        ):
            rc = run_auto_chroot(["--repo", env["HOME"] + "/Code/demo", "--auto-hud", "validate"], env=env, cwd=env["HOME"] + "/Code/demo")
        self.assertEqual(rc, 0)
        self.assertTrue(calls)
        self.assertEqual(calls[-1][0], "androidos")
        self.assertIn(("/data/data/com.termux/files/home", "/home/mike"), calls[-1][1])

    def test_inactivity_hint_seconds_is_configurable_and_bounded(self) -> None:
        self.assertEqual(_inactivity_hint_seconds({"DEVTOOL_TERMUX_CHROOT_STALL_HINT_SECONDS": "0"}), 0)
        self.assertEqual(_inactivity_hint_seconds({"DEVTOOL_TERMUX_CHROOT_STALL_HINT_SECONDS": "bad"}), 30)
        self.assertEqual(_inactivity_hint_seconds({"DEVTOOL_TERMUX_CHROOT_STALL_HINT_SECONDS": "99999"}), 3600)

    def test_inactivity_hint_mentions_log_and_verbose_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.ansi.log"
            _emit_chroot_inactivity_hint(path, pid=123, idle_seconds=45)
            text = path.read_text(encoding="utf-8")
        self.assertIn("Chroot command is still running after 45s", text)
        self.assertIn("Use -v", text)
        self.assertIn("pid 123", text)


    def test_inner_command_preserves_shared_run_id(self) -> None:
        env = {"HOME": "/data/data/com.termux/files/home", "DEVTOOL_TERMUX_CHROOT_HOME": "/home/termux"}
        config, forwarded = translate_devtool_args(["validate"], env=env, cwd="/data/data/com.termux/files/home/Code/demo")
        with patch.dict("os.environ", {"DEVTOOL_RUN_ID": "shared-run"}, clear=False):
            inner = build_inner_command(config, forwarded, identity=self.identity(home="/home/termux", user="termux"))
        self.assertIn("DEVTOOL_RUN_ID=shared-run", inner)

    def test_default_termux_home_is_not_guessed_before_probe_repair(self) -> None:
        env = {"HOME": "/data/data/com.termux/files/home"}
        config, forwarded = translate_devtool_args(
            ["--repo", "/data/data/com.termux/files/home/Code/sims3tools", "validate"],
            env=env,
            cwd="/data/data/com.termux/files/home/Code/sims3tools",
        )
        self.assertEqual(config.cwd, "/data/data/com.termux/files/home/Code/sims3tools")
        self.assertIn("/data/data/com.termux/files/home/Code/sims3tools", forwarded)

    def test_host_chroot_wrapper_cd_and_translates_absolute_paths(self) -> None:
        env = {"HOME": "/data/data/com.termux/files/home", "DEVTOOL_TERMUX_CHROOT_HOME": "/home/termux", "DEVTOOL_TERMUX_CHROOT_USER": "termux"}
        spec = CommandSpec("build", ["dotnet", "build", "/data/data/com.termux/files/home/Code/demo/Demo.sln"])
        with patch("devtool.termux_chroot_runner.resolve_chroot_identity", return_value=self.identity(user="termux", home="/home/termux")):
            wrapped = wrap_specs_for_termux_host_chroot(
                [spec],
                "androidos",
                repo_root="/data/data/com.termux/files/home/Code/demo",
                env=env,
            )
        self.assertEqual(wrapped[0].argv[:8], ["sudo", "chrootctl", "--name", "androidos", "exec", "--", "zsh", "-lc"])
        command = wrapped[0].argv[8]
        self.assertIn('exec env -i', command)
        self.assertIn('HOME=/home/termux', command)
        self.assertIn('PATH=/home/linuxbrew/.linuxbrew/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', command)
        self.assertIn('DEVTOOL_EXECUTION_CONTEXT=chroot', command)
        self.assertIn('dotnet build /home/termux/Code/demo/Demo.sln', command)
        self.assertEqual(wrapped[0].env, {})
        self.assertEqual(wrapped[0].metadata["execution_mode"], "termux-host-chroot")
        self.assertEqual(wrapped[0].metadata["chroot_cwd"], "/home/termux/Code/demo")
        self.assertEqual(wrapped[0].metadata["progress_argv"], spec.argv)
        self.assertEqual(wrapped[0].metadata["progress_cwd"], "/data/data/com.termux/files/home/Code/demo")


    def test_host_chroot_wrapper_honors_custom_inner_path_prefix(self) -> None:
        env = {
            "HOME": "/data/data/com.termux/files/home",
            "DEVTOOL_TERMUX_CHROOT_HOME": "/home/termux",
            "DEVTOOL_TERMUX_CHROOT_USER": "termux",
            "DEVTOOL_CHROOT_PATH_PREFIX": "/opt/node/bin",
        }
        spec = CommandSpec("build", ["gradle", "help"])
        with patch(
            "devtool.termux_chroot_runner.resolve_chroot_identity",
            return_value=self.identity(user="termux", home="/home/termux"),
        ):
            wrapped = wrap_specs_for_termux_host_chroot(
                [spec],
                "androidos",
                repo_root="/data/data/com.termux/files/home/Code/demo",
                env=env,
            )

        command = wrapped[0].argv[8]

        self.assertIn(
            "PATH=/opt/node/bin:/usr/local/sbin:/usr/local/bin:"
            "/usr/sbin:/usr/bin:/sbin:/bin",
            command,
        )

    def test_wrapper_strips_termux_toolchain_paths_before_chroot_execution(self) -> None:
        spec = CommandSpec(
            "artifact-validator",
            ["env", "/data/data/com.termux/files/usr/bin/python3.14", "-V"],
            env={
                "PATH": "/data/data/com.termux/files/usr/bin:/usr/bin",
                "JAVA_HOME": "/data/data/com.termux/files/usr/lib/jvm/java-17",
                "CUSTOM_ROOT": "/data/data/com.termux/files/home/Code/demo",
            },
            cwd="/data/data/com.termux/files/home/Code/demo",
        )
        with patch("devtool.termux_chroot_runner.resolve_chroot_identity", return_value=self.identity(user="termux", home="/home/termux")):
            wrapped = wrap_specs_for_termux_host_chroot(
                [spec],
                "androidos",
                repo_root="/data/data/com.termux/files/home/Code/demo",
                env={
                    "HOME": "/data/data/com.termux/files/home",
                    "PREFIX": "/data/data/com.termux/files/usr",
                    "TERMUX_VERSION": "0.119",
                    "DEVTOOL_TERMUX_CHROOT_HOME": "/home/termux",
                    "DEVTOOL_TERMUX_CHROOT_USER": "termux",
                },
            )[0]
        command = wrapped.argv[-1]
        self.assertIn("env python3 -V", command)
        self.assertIn("PATH=/usr/bin", command)
        self.assertIn("CUSTOM_ROOT=/home/termux/Code/demo", command)
        self.assertNotIn("JAVA_HOME=", command)
        self.assertNotIn("/data/data/com.termux/", command)


    def test_host_chroot_wrapper_preserves_run_id_for_structured_gradle_events(self) -> None:
        spec = CommandSpec(
            "android-validation-explicit-tasks",
            [
                "python3",
                "/data/data/com.termux/files/home/.local/share/devtool/runtime/android/build-apk",
                "/data/data/com.termux/files/home/Code/demo",
                "--task",
                ":app:compileDebugKotlin",
                "--",
                "--init-script",
                "/data/data/com.termux/files/home/.local/share/devtool/runtime/gradle/devtool-events.init.gradle",
            ],
            cwd="/data/data/com.termux/files/home/Code/demo",
            metadata={"progress_parser": "gradle"},
        )
        with patch("devtool.termux_chroot_runner.resolve_chroot_identity", return_value=self.identity(user="termux", home="/home/termux")):
            wrapped = wrap_specs_for_termux_host_chroot(
                [spec],
                "androidos",
                repo_root="/data/data/com.termux/files/home/Code/demo",
                env={
                    "HOME": "/data/data/com.termux/files/home",
                    "DEVTOOL_TERMUX_CHROOT_HOME": "/home/termux",
                    "DEVTOOL_TERMUX_CHROOT_USER": "termux",
                    "DEVTOOL_RUN_ID": "shared-gradle-run",
                },
            )[0]

        command = wrapped.argv[-1]
        self.assertIn("DEVTOOL_RUN_ID=shared-gradle-run", command)
        self.assertIn(
            "/home/termux/.local/share/devtool/runtime/gradle/devtool-events.init.gradle",
            command,
        )
        self.assertIn("DEVTOOL_RUN_ID", wrapped.metadata["chroot_translated_env"])

    def test_full_chroot_runner_refuses_desktop_linux(self) -> None:
        with patch.dict("os.environ", {"HOME": "/home/mike"}, clear=True):
            self.assertEqual(run_auto_chroot(["validate"]), 2)

    def test_run_specs_uses_original_dotnet_command_for_wrapped_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            solution = repo / "Demo.sln"
            solution.write_text("", encoding="utf-8")
            original = CommandSpec("build", ["dotnet", "build", str(solution)])
            with patch("devtool.termux_chroot_runner.resolve_chroot_identity", return_value=self.identity(user="termux", home="/home/termux")):
                wrapped = wrap_specs_for_termux_host_chroot(
                    [original],
                    "androidos",
                    repo_root=repo,
                    env={"HOME": "/data/data/com.termux/files/home", "DEVTOOL_TERMUX_CHROOT_HOME": "/home/termux", "DEVTOOL_TERMUX_CHROOT_USER": "termux"},
                    path_map=[(str(repo), "/home/termux/Code/demo")],
                )
            config = ProjectConfig(repo_root=repo, kind="dotnet")

            with (
                patch("devtool.runners.dotnet_progress_plan", return_value=object()) as plan,
                patch("devtool.runners._run_process_live", return_value=(0, "Build succeeded.")) as run_live,
                patch.dict("os.environ", {"DEVTOOL_XDG_DIR": str(repo / ".devtool-state")}, clear=False),
            ):
                results, _raw = run_specs(config, wrapped, verbose=False)

            self.assertEqual(results[0].returncode, 0)
            self.assertEqual(results[0].metadata["execution_mode"], "termux-host-chroot")
            self.assertEqual(results[0].metadata["chroot"], "androidos")
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
