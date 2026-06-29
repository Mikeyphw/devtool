from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from repo_devtool.dotnet_progress import dotnet_progress_plan


class DotnetProgressTests(unittest.TestCase):
    def test_detects_plain_dotnet_restore_solution_total(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "tests").mkdir()
            (root / "src" / "App.csproj").write_text("", encoding="utf-8")
            (root / "tests" / "App.Tests.csproj").write_text("", encoding="utf-8")
            sln = root / "App.sln"
            sln.write_text(
                'Project("{GUID}") = "App", "src\\App.csproj", "{P1}"\nEndProject\n'
                'Project("{GUID}") = "App.Tests", "tests\\App.Tests.csproj", "{P2}"\nEndProject\n',
                encoding="utf-8",
            )

            plan = dotnet_progress_plan(["dotnet", "restore", "App.sln"], str(root))

            self.assertIsNotNone(plan)
            assert plan is not None
            self.assertEqual(plan.command, "restore")
            self.assertEqual(plan.total, 2)

    def test_test_command_prefers_test_project_total(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "tests").mkdir()
            (root / "src" / "App.csproj").write_text("", encoding="utf-8")
            (root / "tests" / "App.Tests.csproj").write_text("", encoding="utf-8")
            sln = root / "App.sln"
            sln.write_text(
                'Project("{GUID}") = "App", "src\\App.csproj", "{P1}"\nEndProject\n'
                'Project("{GUID}") = "App.Tests", "tests\\App.Tests.csproj", "{P2}"\nEndProject\n',
                encoding="utf-8",
            )

            plan = dotnet_progress_plan(["dotnet", "test", "App.sln"], str(root))

            self.assertIsNotNone(plan)
            assert plan is not None
            self.assertEqual(plan.command, "test")
            self.assertEqual(plan.total, 1)

    def test_detects_termux_proot_wrapped_dotnet(self) -> None:
        plan = dotnet_progress_plan(
            ["proot", "-b", "/tmp/etc:/etc", "/data/data/com.termux/files/usr/bin/dotnet", "build", "App.csproj"],
            "/tmp",
        )

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.command, "build")
        self.assertEqual(plan.total, 1)

    def test_tracker_can_render_a_progress_line(self) -> None:
        from io import StringIO
        from rich.console import Console
        from repo_devtool.dotnet_progress import DotnetProgressTracker

        buffer = StringIO()
        console = Console(file=buffer, force_terminal=True, width=100)
        tracker = DotnetProgressTracker(["dotnet", "build", "App.csproj"], "/tmp", "build", console)

        with tracker:
            tracker.update_from_line("  App -> /tmp/bin/App.dll")
            tracker.finish(0)

        self.assertIn("dotnet build", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
