from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from repo_devtool.models import CommandSpec, ProjectConfig
from repo_devtool.runners import run_specs


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
                patch("repo_devtool.runners.shutil.which", side_effect=lambda name: {
                    "proot": "/data/data/com.termux/files/usr/bin/proot",
                    "dotnet": "/data/data/com.termux/files/usr/bin/dotnet",
                }.get(name)),
                patch("repo_devtool.runners.subprocess.run", side_effect=fake_run),
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
                patch("repo_devtool.runners.shutil.which", side_effect=lambda name: {
                    "proot": "/data/data/com.termux/files/usr/bin/proot",
                    "dotnet": "/data/data/com.termux/files/usr/bin/dotnet",
                }.get(name)),
                patch("repo_devtool.runners.subprocess.run", side_effect=fake_run),
            ):
                results, _ = run_specs(config, [spec], verbose=False)

            self.assertEqual(results[0].returncode, 0)


if __name__ == "__main__":
    unittest.main()
