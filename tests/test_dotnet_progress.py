from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from devtool.dotnet_progress import dotnet_progress_plan


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
        from devtool.dotnet_progress import DotnetProgressTracker

        buffer = StringIO()
        console = Console(file=buffer, force_terminal=True, width=100)
        tracker = DotnetProgressTracker(["dotnet", "build", "App.csproj"], "/tmp", "build", console)

        with tracker:
            tracker.update_from_line("  App -> /tmp/bin/App.dll")
            tracker.finish(0)

        self.assertIn("dotnet build", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()

class DotnetProgressRelativePathTests(unittest.TestCase):
    def test_tracker_uses_repo_relative_project_detail(self) -> None:
        import tempfile
        from pathlib import Path
        from rich.console import Console
        from devtool.dotnet_progress import DotnetProgressTracker

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "src" / "App" / "App.csproj"
            project.parent.mkdir(parents=True)
            project.write_text("<Project />", encoding="utf-8")
            console = Console(force_terminal=False, width=60)
            tracker = DotnetProgressTracker(["dotnet", "restore", str(project)], str(root), "restore", console)
            tracker.update_callback = lambda snapshot: None
            tracker.update_from_line(f"  Restored {project} (in 10 ms).")
            self.assertIn("src/App/App.csproj", tracker.detail)
            self.assertNotIn(str(root), tracker.detail)
