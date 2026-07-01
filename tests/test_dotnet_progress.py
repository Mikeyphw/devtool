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

class DotnetProgressDynamicTests(unittest.TestCase):
    def test_tracker_expands_total_when_msbuild_reports_more_outputs_than_estimate(self) -> None:
        from io import StringIO
        from rich.console import Console
        from devtool.dotnet_progress import DotnetProgressTracker

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "App.csproj").write_text("", encoding="utf-8")
            buffer = StringIO()
            console = Console(file=buffer, force_terminal=True, width=80)
            tracker = DotnetProgressTracker(["dotnet", "build", "src/App.csproj"], str(root), "build", console)

            with tracker:
                tracker.update_from_line(f"  App -> {root}/src/bin/Debug/net8.0/App.dll")
                tracker.update_from_line(f"  Lib -> {root}/src/bin/Debug/net8.0/Lib.dll")
                tracker.finish(0)

            snap = tracker.snapshot()
            self.assertEqual(snap["completed"], 2)
            self.assertEqual(snap["total"], 2)
            self.assertIn("src/bin/Debug/net8.0/Lib.dll", snap["detail"])
            self.assertNotIn(str(root), snap["detail"])

    def test_tracker_reports_restored_project_relative_path(self) -> None:
        from io import StringIO
        from rich.console import Console
        from devtool.dotnet_progress import DotnetProgressTracker

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            project = root / "src" / "App.csproj"
            project.write_text("", encoding="utf-8")
            buffer = StringIO()
            console = Console(file=buffer, force_terminal=True, width=80)
            tracker = DotnetProgressTracker(["dotnet", "restore", "src/App.csproj"], str(root), "restore", console)

            with tracker:
                tracker.update_from_line(f"  Restored {project} (in 123 ms).")
                tracker.finish(0)

            snap = tracker.snapshot()
            self.assertIn("restored src/App.csproj", snap["detail"])
            self.assertNotIn(str(root), snap["detail"])

    def test_tracker_reports_test_assembly_relative_path(self) -> None:
        from io import StringIO
        from rich.console import Console
        from devtool.dotnet_progress import DotnetProgressTracker

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dll = root / "tests" / "App.Tests" / "bin" / "Debug" / "net8.0" / "App.Tests.dll"
            dll.parent.mkdir(parents=True)
            dll.write_text("", encoding="utf-8")
            buffer = StringIO()
            console = Console(file=buffer, force_terminal=True, width=80)
            tracker = DotnetProgressTracker(["dotnet", "test", "tests/App.Tests/App.Tests.csproj"], str(root), "test", console)

            with tracker:
                tracker.update_from_line(f"Test run for {dll} (.NETCoreApp,Version=v8.0)")
                tracker.finish(0)

            snap = tracker.snapshot()
            self.assertIn("testing tests/App.Tests/bin/Debug/net8.0/App.Tests.dll", snap["detail"])
            self.assertNotIn(str(root), snap["detail"])

class DotnetProgressLiveSafetyTests(unittest.TestCase):
    def test_tracker_does_not_crash_when_another_rich_live_is_active(self) -> None:
        from io import StringIO
        from rich.console import Console
        from rich.live import Live
        from devtool.dotnet_progress import DotnetProgressTracker

        buffer = StringIO()
        console = Console(file=buffer, force_terminal=True, width=80)
        outer = Live("outer", console=console, transient=True)
        outer.start()
        try:
            tracker = DotnetProgressTracker(["dotnet", "build", "App.csproj"], "/tmp", "build", console)
            with tracker:
                tracker.update_from_line("  App -> /tmp/bin/App.dll")
                tracker.finish(0)
        finally:
            outer.stop()

        snap = tracker.snapshot()
        self.assertEqual(snap["completed"], 1)
        self.assertEqual(snap["total"], 1)
        self.assertIn("App.dll", snap["detail"])
