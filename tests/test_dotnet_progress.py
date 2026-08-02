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

        output = buffer.getvalue()
        self.assertIn(".NET", output)
        self.assertIn("1/1 projects", output)
        self.assertIn("build ok", output)


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

class DotnetProgressSeparateCommandTests(unittest.TestCase):
    def test_tracker_description_includes_phase_index_for_validate_batches(self) -> None:
        from io import StringIO
        from rich.console import Console
        from devtool.dotnet_progress import DotnetProgressTracker

        buffer = StringIO()
        console = Console(file=buffer, force_terminal=True, width=90)
        tracker = DotnetProgressTracker(
            ["dotnet", "restore", "App.sln"],
            "/tmp",
            "restore",
            console,
            phase_index=1,
            phase_total=3,
        )

        with tracker:
            tracker.update_from_line("  Restored /tmp/App.csproj (in 1 ms).")
            tracker.finish(0)

        output = buffer.getvalue()
        self.assertIn("overall 1/3", output)
        self.assertIn(".NET", output)
        self.assertNotIn("dotnet restore", output)
        self.assertIn("restore ok", tracker.snapshot()["detail"])

    def test_tracker_leaves_render_non_transient_for_separate_completed_bars(self) -> None:
        from io import StringIO
        from rich.console import Console
        from devtool.dotnet_progress import DotnetProgressTracker

        buffer = StringIO()
        console = Console(file=buffer, force_terminal=True, width=90)
        restore = DotnetProgressTracker(["dotnet", "restore", "App.csproj"], "/tmp", "restore", console, phase_index=1, phase_total=2)
        build = DotnetProgressTracker(["dotnet", "build", "App.csproj"], "/tmp", "build", console, phase_index=2, phase_total=2)

        with restore:
            restore.update_from_line("  Restored /tmp/App.csproj (in 1 ms).")
            restore.finish(0)
        with build:
            build.update_from_line("  App -> /tmp/bin/Debug/net8.0/App.dll")
            build.finish(0)

        output = buffer.getvalue()
        self.assertIn("overall 1/2", output)
        self.assertIn("overall 2/2", output)
        self.assertEqual(output.count(".NET"), 2)
        self.assertNotIn("dotnet restore", output)
        self.assertNotIn("dotnet build", output)

class DotnetProgressFalseIssueCountTests(unittest.TestCase):
    def test_successful_build_summary_does_not_count_zero_error_warning_lines_as_issues(self) -> None:
        from io import StringIO
        from rich.console import Console
        from devtool.dotnet_progress import DotnetProgressTracker

        buffer = StringIO()
        console = Console(file=buffer, force_terminal=True, width=100)
        tracker = DotnetProgressTracker(["dotnet", "build", "App.csproj"], "/tmp", "build", console)

        with tracker:
            tracker.update_from_line("Build succeeded.")
            tracker.update_from_line("    0 Warning(s)")
            tracker.update_from_line("    0 Error(s)")
            tracker.finish(0)

        snap = tracker.snapshot()
        self.assertEqual(snap["warnings"], 0)
        self.assertEqual(snap["errors"], 0)
        self.assertIn("build ok", snap["detail"])
        self.assertNotIn("errors", snap["detail"])

    def test_successful_test_result_failed_zero_does_not_count_as_error(self) -> None:
        from io import StringIO
        from rich.console import Console
        from devtool.dotnet_progress import DotnetProgressTracker

        buffer = StringIO()
        console = Console(file=buffer, force_terminal=True, width=100)
        tracker = DotnetProgressTracker(["dotnet", "test", "App.Tests.csproj"], "/tmp", "test", console)

        with tracker:
            tracker.update_from_line("Passed!  - Failed:     0, Passed:    27, Skipped:     0, Total:    27, Duration: 9 s - App.Tests.dll (net8.0)")
            tracker.finish(0)

        snap = tracker.snapshot()
        self.assertEqual(snap["errors"], 0)
        self.assertIn("test ok", snap["detail"])
        self.assertNotIn("diagnostics", snap["detail"])

class DotnetProgressStableTotalTests(unittest.TestCase):
    def test_tracker_does_not_chase_underestimated_total_before_finish(self) -> None:
        from io import StringIO
        from rich.console import Console
        from devtool.dotnet_progress import DotnetProgressTracker

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "App.csproj").write_text("", encoding="utf-8")
            tracker = DotnetProgressTracker(["dotnet", "build", "src/App.csproj"], str(root), "build", Console(file=StringIO(), force_terminal=True, width=80))

            with tracker:
                tracker.update_from_line(f"  App -> {root}/src/bin/Debug/net8.0/App.dll")
                tracker.update_from_line(f"  Lib -> {root}/src/bin/Debug/net8.0/Lib.dll")
                running = tracker.snapshot()
                tracker.finish(0)

            finished = tracker.snapshot()
            self.assertEqual(running["completed"], 2)
            self.assertIsNone(running["total"])
            self.assertEqual(finished["completed"], 2)
            self.assertEqual(finished["total"], 2)

    def test_build_total_includes_transitive_project_references(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / "src" / "App" / "App.csproj"
            lib = root / "src" / "Lib" / "Lib.csproj"
            core = root / "shared" / "Core" / "Core.csproj"
            app.parent.mkdir(parents=True)
            lib.parent.mkdir(parents=True)
            core.parent.mkdir(parents=True)
            app.write_text('<Project><ItemGroup><ProjectReference Include="../Lib/Lib.csproj" /></ItemGroup></Project>', encoding="utf-8")
            lib.write_text('<Project><ItemGroup><ProjectReference Include="../../shared/Core/Core.csproj" /></ItemGroup></Project>', encoding="utf-8")
            core.write_text('<Project />', encoding="utf-8")

            plan = dotnet_progress_plan(["dotnet", "build", "src/App/App.csproj"], str(root))

            self.assertIsNotNone(plan)
            assert plan is not None
            self.assertEqual(plan.total, 3)

class DotnetProgressStructuredUiTests(unittest.TestCase):
    def test_target_panel_labels_test_assemblies_and_aggregates_each_dll(self) -> None:
        from io import StringIO
        from rich.console import Console
        from devtool.dotnet_progress import DotnetProgressTracker

        buffer = StringIO()
        console = Console(file=buffer, force_terminal=True, width=120)
        tracker = DotnetProgressTracker(
            ["dotnet", "test", "Suite.sln"],
            "/tmp",
            "test[target-a]",
            console,
            phase_index=3,
            phase_total=6,
            display_target="target-a",
            target_phase_index=3,
            target_phase_total=3,
        )

        with tracker:
            for index, passed in enumerate((75, 80, 70), start=1):
                tracker.update_from_line(f"Test run for /tmp/tests/Suite{index}.Tests.dll (.NETCoreApp,Version=v8.0)")
                tracker.update_from_line(
                    f"Passed!  - Failed: 0, Passed: {passed}, Skipped: 0, Total: {passed}, "
                    f"Duration: 1 s - Suite{index}.Tests.dll (net8.0)"
                )
            tracker.finish(0)

        output = buffer.getvalue()
        snapshot = tracker.snapshot()
        self.assertIn("target-a", output)
        self.assertIn("target phase 3/3", output)
        self.assertIn("overall 3/6", output)
        self.assertIn("test assemblies", output)
        self.assertIn("225 passed", output)
        self.assertEqual(snapshot["completed"], 3)
        self.assertEqual(snapshot["total"], 3)
        self.assertEqual(snapshot["unit_label"], "test assemblies")
        self.assertEqual(snapshot["tests_passed"], 225)
        self.assertEqual(snapshot["test_assemblies"], 3)

    def test_duplicate_test_summary_for_same_assembly_is_not_double_counted(self) -> None:
        from io import StringIO
        from rich.console import Console
        from devtool.dotnet_progress import DotnetProgressTracker

        tracker = DotnetProgressTracker(
            ["dotnet", "test", "Suite.Tests.csproj"],
            "/tmp",
            "test",
            Console(file=StringIO(), force_terminal=True, width=100),
        )
        summary = "Passed!  - Failed: 0, Passed: 12, Skipped: 1, Total: 13, Duration: 1 s - Suite.Tests.dll (net8.0)"
        with tracker:
            tracker.update_from_line("Test run for /tmp/Suite.Tests.dll (.NETCoreApp,Version=v8.0)")
            tracker.update_from_line(summary)
            tracker.update_from_line(summary)
            tracker.finish(0)

        snapshot = tracker.snapshot()
        self.assertEqual(snapshot["tests_passed"], 12)
        self.assertEqual(snapshot["tests_skipped"], 1)
        self.assertEqual(snapshot["tests_total"], 13)

class DotnetTargetPhaseLayoutTests(unittest.TestCase):
    def test_each_target_gets_its_own_restore_build_test_phase_count(self) -> None:
        from devtool.models import CommandSpec, ProjectConfig
        from devtool.runners import _dotnet_phase_layout

        config = ProjectConfig(repo_root=Path("/repo"), kind="dotnet")
        specs = []
        for target in ("alpha", "beta"):
            for command in ("restore", "build", "test"):
                specs.append(
                    CommandSpec(
                        command,
                        ["dotnet", command, f"/repo/{target}.sln"],
                        metadata={"bundle_target": target},
                    )
                )

        layout = _dotnet_phase_layout(specs, config)

        self.assertEqual(layout[0], ("alpha", 1, 3))
        self.assertEqual(layout[2], ("alpha", 3, 3))
        self.assertEqual(layout[3], ("beta", 1, 3))
        self.assertEqual(layout[5], ("beta", 3, 3))

    def test_same_named_test_dlls_in_different_framework_paths_count_separately(self) -> None:
        from io import StringIO
        from rich.console import Console
        from devtool.dotnet_progress import DotnetProgressTracker

        tracker = DotnetProgressTracker(
            ["dotnet", "test", "Suite.Tests.csproj"],
            "/repo",
            "test",
            Console(file=StringIO(), force_terminal=True, width=100),
        )
        with tracker:
            tracker.update_from_line("Test run for /repo/bin/Debug/net8.0/Suite.Tests.dll (.NETCoreApp,Version=v8.0)")
            tracker.update_from_line("Passed!  - Failed: 0, Passed: 10, Skipped: 0, Total: 10, Duration: 1 s - Suite.Tests.dll (net8.0)")
            tracker.update_from_line("Test run for /repo/bin/Debug/net9.0/Suite.Tests.dll (.NETCoreApp,Version=v9.0)")
            tracker.update_from_line("Passed!  - Failed: 0, Passed: 11, Skipped: 0, Total: 11, Duration: 1 s - Suite.Tests.dll (net9.0)")
            tracker.finish(0)

        snapshot = tracker.snapshot()
        self.assertEqual(snapshot["test_assemblies"], 2)
        self.assertEqual(snapshot["tests_passed"], 21)
