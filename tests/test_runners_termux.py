from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from devtool.models import CommandSpec, ProjectConfig
from devtool.tasker import TaskerProgressSink
from devtool.runners import run_specs


class RunnersTermuxTests(unittest.TestCase):
    def test_run_specs_wraps_dotnet_with_proot_on_termux(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            config = ProjectConfig(repo_root=repo_root, kind="dotnet")
            spec = CommandSpec("build", ["dotnet", "build", "Demo.sln"])

            calls: list[list[str]] = []

            def fake_run(argv, cwd, env, text, stdout, stderr, check):
                self.assertEqual(argv[0], "proot")
                self.assertEqual(argv[1], "-b")
                self.assertTrue(argv[2].endswith("/etc:/etc"))
                self.assertEqual(argv[3], "/data/data/com.termux/files/usr/bin/dotnet")
                self.assertIn(argv[4:], (["build", "Demo.sln"], ["build-server", "shutdown"]))
                calls.append(argv[4:])

                os_release_path = Path(argv[2].split(":", 1)[0]) / "os-release"
                self.assertTrue(os_release_path.exists())
                self.assertIn("ID=termux", os_release_path.read_text(encoding="utf-8"))

                class Result:
                    returncode = 0
                    stdout = "ok"
                return Result()

            with (
                patch.dict("os.environ", {"TERMUX_VERSION": "1"}, clear=False),
                patch("devtool.runners.shutil.which", side_effect=lambda name: {
                    "proot": "/data/data/com.termux/files/usr/bin/proot",
                    "dotnet": "/data/data/com.termux/files/usr/bin/dotnet",
                }.get(name)),
                patch("devtool.runners.subprocess.run", side_effect=fake_run),
            ):
                results, _ = run_specs(config, [spec], verbose=False)

            self.assertEqual(results[0].returncode, 0)
            self.assertEqual(results[0].argv[0], "proot")
            self.assertEqual(results[-1].name, "dotnet-build-server-shutdown")
            self.assertEqual(calls, [["build", "Demo.sln"], ["build-server", "shutdown"]])

    def test_run_specs_honors_termux_dotnet_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            config = ProjectConfig(repo_root=repo_root, kind="dotnet")
            spec = CommandSpec("test", ["dotnet", "test", "Demo.sln"])

            calls: list[list[str]] = []

            def fake_run(argv, cwd, env, text, stdout, stderr, check):
                self.assertEqual(argv[3], "/custom/dotnet")
                calls.append(argv[4:])
                class Result:
                    returncode = 0
                    stdout = "ok"
                return Result()

            with (
                patch.dict("os.environ", {"TERMUX_VERSION": "1", "DEVTOOL_TERMUX_DOTNET": "/custom/dotnet"}, clear=False),
                patch("devtool.runners.shutil.which", side_effect=lambda name: {
                    "proot": "/data/data/com.termux/files/usr/bin/proot",
                    "dotnet": "/data/data/com.termux/files/usr/bin/dotnet",
                }.get(name)),
                patch("devtool.runners.subprocess.run", side_effect=fake_run),
            ):
                results, _ = run_specs(config, [spec], verbose=False)

            self.assertEqual(results[0].returncode, 0)
            self.assertEqual(results[-1].name, "dotnet-build-server-shutdown")
            self.assertEqual(calls, [["test", "Demo.sln"], ["build-server", "shutdown"]])


class RunnersQuietModeTests(unittest.TestCase):
    def test_non_verbose_captures_process_output_without_streaming(self) -> None:
        import io
        import sys
        from contextlib import redirect_stdout

        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            config = ProjectConfig(repo_root=repo_root, kind="unknown")
            spec = CommandSpec("build", [sys.executable, "-c", "print('Determining projects to restore...')"])

            stream = io.StringIO()
            with redirect_stdout(stream):
                results, raw = run_specs(config, [spec], verbose=False)

            self.assertEqual(results[0].returncode, 0)
            self.assertIn("Determining projects to restore...", results[0].output)
            self.assertIn("Determining projects to restore...", raw)
            self.assertNotIn("Determining projects to restore...", stream.getvalue())

    def test_generic_progress_uses_command_specific_phase_label(self) -> None:
        from io import StringIO
        from rich.console import Console
        from devtool.generic_progress import GenericProgressTracker

        buffer = StringIO()
        console = Console(file=buffer, force_terminal=True, width=80)
        tracker = GenericProgressTracker(["bash", "-lc", "echo"], "/tmp", "clean", console)

        with tracker:
            tracker.update_from_line("removing stale cache")
            tracker.finish(0)

        output = buffer.getvalue()
        self.assertIn("cleaning", output)
        self.assertIn("removing stale cache", output)

    def test_generic_progress_uses_running_command_for_arbitrary_commands(self) -> None:
        from io import StringIO
        from rich.console import Console
        from devtool.generic_progress import GenericProgressTracker

        buffer = StringIO()
        console = Console(file=buffer, force_terminal=True, width=80)
        tracker = GenericProgressTracker(["bash", "-lc", "echo"], "/tmp", "sync", console)

        with tracker:
            tracker.update_from_line("syncing remote state")
            tracker.finish(0)

        output = buffer.getvalue()
        self.assertIn("running command", output)
        self.assertIn("syncing remote state", output)


if __name__ == "__main__":
    unittest.main()

class DotnetValidationNoBuildTests(unittest.TestCase):
    def test_validation_adds_no_build_to_implicit_dotnet_test_after_build(self) -> None:
        from devtool.runners import build_validation_specs

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = ProjectConfig(
                repo_root=root,
                kind="dotnet",
                restore_target="Demo.sln",
                build_target="Demo.sln",
                test_target="Demo.sln",
            )

            specs = build_validation_specs(config)

        test_specs = [spec for spec in specs if spec.name == "test"]
        self.assertEqual(len(test_specs), 1)
        self.assertIn("--no-build", test_specs[0].argv)

    def test_validation_does_not_add_no_build_when_build_is_skipped(self) -> None:
        from devtool.runners import build_validation_specs

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = ProjectConfig(repo_root=root, kind="dotnet", build_target="Demo.sln", test_target="Demo.sln")

            specs = build_validation_specs(config, skip_build=True)

        test_specs = [spec for spec in specs if spec.name == "test"]
        self.assertEqual(len(test_specs), 1)
        self.assertNotIn("--no-build", test_specs[0].argv)

    def test_standalone_dotnet_test_does_not_get_no_build(self) -> None:
        from devtool.runners import build_specs

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = ProjectConfig(repo_root=root, kind="dotnet", build_target="Demo.sln", test_target="Demo.sln")

            specs = build_specs(config, "test")

        self.assertEqual(len(specs), 1)
        self.assertNotIn("--no-build", specs[0].argv)

    def test_validation_adds_no_build_to_explicit_dotnet_test(self) -> None:
        from devtool.runners import build_validation_specs

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = ProjectConfig(
                repo_root=root,
                kind="dotnet",
                build_target="Demo.sln",
                test_target="Demo.sln",
                test_command="dotnet test Demo.sln --logger trx",
            )

            specs = build_validation_specs(config)

        test_specs = [spec for spec in specs if spec.name == "test"]
        self.assertEqual(len(test_specs), 1)
        self.assertIn("--no-build", test_specs[0].argv)

    def test_validation_adds_no_restore_to_explicit_dotnet_build(self) -> None:
        from devtool.runners import build_validation_specs

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = ProjectConfig(
                repo_root=root,
                kind="dotnet",
                restore_target="Demo.sln",
                build_target="Demo.sln",
                test_target="Demo.sln",
                build_command="dotnet build Demo.sln -c Release",
            )

            specs = build_validation_specs(config)

        build_specs = [spec for spec in specs if spec.name == "build"]
        self.assertEqual(len(build_specs), 1)
        self.assertIn("--no-restore", build_specs[0].argv)

class DotnetTestNoBuildFlagTests(unittest.TestCase):
    def test_user_no_build_adds_flag_to_implicit_dotnet_test(self) -> None:
        from devtool.runners import add_no_build_to_dotnet_test_specs, build_specs

        with tempfile.TemporaryDirectory() as tmp:
            config = ProjectConfig(repo_root=Path(tmp), kind="dotnet", build_target="Demo.sln", test_target="Demo.sln")
            specs = build_specs(config, "test")

        updated, changed = add_no_build_to_dotnet_test_specs(specs)
        self.assertEqual(changed, 1)
        self.assertEqual(updated[0].argv[:3], ["dotnet", "test", "--no-build"])
        self.assertIn("Demo.sln", updated[0].argv)

    def test_user_no_build_adds_flag_to_explicit_dotnet_test(self) -> None:
        from devtool.runners import add_no_build_to_dotnet_test_specs, build_specs

        with tempfile.TemporaryDirectory() as tmp:
            config = ProjectConfig(
                repo_root=Path(tmp),
                kind="dotnet",
                build_target="Demo.sln",
                test_target="Demo.sln",
                test_command="dotnet test Demo.sln --logger trx",
            )
            specs = build_specs(config, "test")

        updated, changed = add_no_build_to_dotnet_test_specs(specs, allow_explicit=True)
        self.assertEqual(changed, 1)
        self.assertEqual(updated[0].argv[:3], ["dotnet", "test", "--no-build"])
        self.assertIn("--logger", updated[0].argv)

class RunnersHudLiveProgressTests(unittest.TestCase):
    def test_build_project_outputs_are_forwarded_as_live_scoped_hud_progress(self) -> None:
        import json

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "A.csproj").write_text("", encoding="utf-8")
            (root / "B.csproj").write_text("", encoding="utf-8")
            (root / "Demo.sln").write_text(
                'Project("{GUID}") = "A", "A.csproj", "{A}"\nEndProject\n'
                'Project("{GUID}") = "B", "B.csproj", "{B}"\nEndProject\n',
                encoding="utf-8",
            )
            state_dir = root / "hud"
            sink = TaskerProgressSink(
                directory=state_dir,
                command="apply-overlay",
                repo_root=str(root),
                repo_name="demo",
                enabled=True,
                update_interval_s=0,
            )
            sink.started()
            config = ProjectConfig(repo_root=root, kind="dotnet")
            spec = CommandSpec(
                "build[s3atelier]",
                ["bash", "-lc", "printf '  A -> /tmp/A.dll\\n'; sleep 0.05; printf '  B -> /tmp/B.dll\\n'"],
                metadata={
                    "progress_argv": ["dotnet", "build", "Demo.sln"],
                    "progress_cwd": str(root),
                    "workspace": "s3atelier",
                    "bundle_target": "s3atelier-target",
                },
            )

            results, _ = run_specs(config, [spec], verbose=False, tasker_progress=sink)
            self.assertEqual(results[0].returncode, 0)
            events = [json.loads(line) for line in (state_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            progress = [event for event in events if event.get("event_type") == "progress"]
            self.assertTrue(any(event.get("current") == 1 and event.get("total") == 2 for event in progress))
            self.assertTrue(any(event.get("current") == 2 and event.get("total") == 2 for event in progress))
            self.assertTrue(all(event.get("phase", {}).get("workspace") == "s3atelier" for event in progress))

    def test_arbitrary_commands_publish_live_detail_updates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / "hud"
            sink = TaskerProgressSink(
                directory=state_dir,
                command="build",
                repo_root=str(root),
                repo_name="demo",
                enabled=True,
                update_interval_s=0,
            )
            sink.started()
            config = ProjectConfig(repo_root=root, kind="command")
            spec = CommandSpec(
                "build",
                ["bash", "-lc", "printf 'generic step 1\\n'; sleep 0.15; printf 'generic step 2\\n'"],
            )

            results, _ = run_specs(config, [spec], verbose=False, tasker_progress=sink)
            events = [json.loads(line) for line in (state_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]

        self.assertEqual(results[0].returncode, 0)
        progress = [event for event in events if event.get("event_type") == "progress"]
        self.assertTrue(any("generic step 1" in event.get("message", "") for event in progress))
        self.assertTrue(any("generic step 2" in event.get("message", "") for event in progress))
