from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from devtool.models import CommandSpec, ProjectConfig
from devtool.runners import run_specs


class RunnersTermuxTests(unittest.TestCase):
    def test_run_specs_wraps_dotnet_with_proot_on_termux(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            config = ProjectConfig(repo_root=repo_root, kind="dotnet")
            spec = CommandSpec("build", ["dotnet", "build", "Demo.sln"])

            def fake_run(argv, cwd, env, text, stdout, stderr, check):
                self.assertEqual(argv[0], "proot")
                self.assertEqual(argv[1], "-b")
                self.assertTrue(argv[2].endswith("/etc:/etc"))
                self.assertEqual(argv[3], "/data/data/com.termux/files/usr/bin/dotnet")
                self.assertEqual(argv[4:], ["build", "Demo.sln"])

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

    def test_run_specs_honors_termux_dotnet_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            config = ProjectConfig(repo_root=repo_root, kind="dotnet")
            spec = CommandSpec("test", ["dotnet", "test", "Demo.sln"])

            def fake_run(argv, cwd, env, text, stdout, stderr, check):
                self.assertEqual(argv[3], "/custom/dotnet")
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
