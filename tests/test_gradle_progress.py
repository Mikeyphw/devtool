from __future__ import annotations

import json
import unittest

from rich.console import Console

from devtool.gradle_progress import GradleProgressTracker, GradleTaskCountColumn


class GradleProgressTests(unittest.TestCase):
    def make_tracker(self) -> GradleProgressTracker:
        return GradleProgressTracker(["./gradlew", "test"], "/repo", "test", Console(force_terminal=False))

    def event(self, sequence: int, kind: str, **values: object) -> str:
        return "DEVTOOL_EVENT " + json.dumps({"sequence": sequence, "kind": kind, **values})

    def test_tracks_exact_task_and_test_counts(self) -> None:
        tracker = self.make_tracker()
        snapshots: list[dict] = []
        tracker.update_callback = snapshots.append
        tracker.update_from_line(self.event(1, "task-graph", total=4))
        tracker.update_from_line(self.event(2, "task-start", task=":app:testDebugUnitTest", completed=1, total=4))
        tracker.update_from_line(self.event(3, "test-finish", test="demo.Tests.passes", passed=1, failed=0, skipped=0, total=1))
        tracker.update_from_line(self.event(4, "task-finish", task=":app:testDebugUnitTest", completed=2, total=4, outcome="success"))

        current = tracker.snapshot()
        self.assertEqual(current["completed"], 2)
        self.assertEqual(current["total"], 4)
        self.assertEqual(current["percent"], 50.0)
        self.assertEqual(current["tests_passed"], 1)
        self.assertEqual(current["current_task"], ":app:testDebugUnitTest")
        self.assertTrue(snapshots)

    def test_ignores_out_of_order_sequence(self) -> None:
        tracker = self.make_tracker()
        tracker.update_from_line(self.event(2, "task-graph", total=5))
        tracker.update_from_line(self.event(1, "task-graph", total=99))
        self.assertEqual(tracker.total, 5)

    def test_plain_gradle_output_updates_detail_when_events_are_absent(self) -> None:
        tracker = self.make_tracker()
        snapshots: list[dict] = []
        tracker.update_callback = snapshots.append
        tracker.update_from_line("> Task :app:compileDebugKotlin")
        self.assertEqual(tracker.current_task, ":app:compileDebugKotlin")
        self.assertEqual(tracker.detail, ":app:compileDebugKotlin")
        self.assertEqual(tracker.fallback_lines, 1)
        self.assertEqual(tracker.last_sequence, 0)
        self.assertTrue(snapshots)

    def test_plain_gradle_progress_keeps_first_failed_task_visible(self) -> None:
        tracker = self.make_tracker()
        tracker.update_from_line("> Task :platform:compileDebugKotlin FAILED")
        tracker.update_from_line("> Task :app:checkOfficialDebugDuplicateClasses")

        current = tracker.snapshot()
        self.assertEqual(current["failed_task"], ":platform:compileDebugKotlin")
        self.assertIn("Failed :platform:compileDebugKotlin", current["detail"])
        self.assertIn(":app:checkOfficialDebugDuplicateClasses", current["detail"])
        tracker.finish(1)
        self.assertIn(":platform:compileDebugKotlin", tracker.detail)

    def test_structured_gradle_progress_keeps_first_failed_task_visible(self) -> None:
        tracker = self.make_tracker()
        tracker.update_from_line(self.event(1, "task-graph", total=3))
        tracker.update_from_line(self.event(2, "task-finish", task=":ui:compileDebugKotlin", completed=1, total=3, outcome="failed"))
        tracker.update_from_line(self.event(3, "task-start", task=":app:checkDebugDuplicateClasses", completed=1, total=3))

        current = tracker.snapshot()
        self.assertEqual(current["failed_task"], ":ui:compileDebugKotlin")
        self.assertIn("Failed :ui:compileDebugKotlin", current["detail"])
        self.assertIn(":app:checkDebugDuplicateClasses", current["detail"])

    def test_structured_events_take_precedence_over_plain_gradle_output(self) -> None:
        tracker = self.make_tracker()
        snapshots: list[dict] = []
        tracker.update_callback = snapshots.append
        tracker.update_from_line(self.event(1, "task-graph", total=5))
        tracker.update_from_line("> Task :app:compileDebugKotlin")
        self.assertEqual(tracker.detail, "Gradle task graph ready: 5 tasks")
        self.assertTrue(snapshots[-1]["force"])
        self.assertEqual(tracker.fallback_lines, 0)


    def test_plain_gradle_success_infers_final_total_and_dependency_breakdown(self) -> None:
        tracker = GradleProgressTracker(
            ["./gradlew", ":app:compileDebugKotlin"],
            "/repo",
            "android-validation-explicit-tasks",
            Console(force_terminal=False),
            requested_tasks=(":app:compileDebugKotlin",),
        )
        tasks = [
            ":app:preBuild",
            ":app:checkKotlinGradlePluginConfigurationErrors",
            ":app:generateDebugResources",
            ":app:packageDebugResources",
            ":app:parseDebugLocalResources",
            ":app:generateDebugRFile",
            ":app:javaPreCompileDebug",
            ":app:mergeDebugShaders",
            ":app:compileDebugShaders",
            ":app:generateDebugAssets",
            ":app:mergeDebugAssets",
            ":app:kspDebugKotlin",
            ":app:compileDebugKotlin",
        ]
        for task in tasks:
            tracker.update_from_line(f"> Task {task}")

        self.assertEqual(tracker.completed, 13)
        self.assertIsNone(tracker.total)
        tracker.finish(0)

        current = tracker.snapshot()
        self.assertEqual(current["completed"], 13)
        self.assertEqual(current["total"], 13)
        self.assertEqual(current["requested_task_count"], 1)
        self.assertEqual(current["dependency_task_count"], 12)
        state = tracker._progress_state()
        self.assertEqual(state.extra_full, "1 requested + 12 deps")
        self.assertNotIn("discover", state.extra_full)

    def test_plain_gradle_duplicate_task_lines_do_not_inflate_count(self) -> None:
        tracker = self.make_tracker()
        tracker.update_from_line("> Task :app:compileDebugKotlin")
        tracker.update_from_line("> Task :app:compileDebugKotlin UP-TO-DATE")
        self.assertEqual(tracker.completed, 1)
        self.assertEqual(tracker.snapshot()["observed_tasks"], [":app:compileDebugKotlin"])

    def test_plain_gradle_actionable_summary_resolves_graph_total(self) -> None:
        tracker = GradleProgressTracker(
            ["./gradlew", ":app:compileDebugKotlin"],
            "/repo",
            "android-validation-explicit-tasks",
            Console(force_terminal=False),
            requested_tasks=(":app:compileDebugKotlin",),
        )
        tracker.update_from_line("> Task :app:kspDebugKotlin")
        tracker.update_from_line("> Task :app:compileDebugKotlin")
        tracker.update_from_line("13 actionable tasks: 8 executed, 5 up-to-date")
        self.assertEqual(tracker.total, 13)
        self.assertEqual(tracker.completed, 13)
        self.assertEqual(tracker.requested_task_count, 1)
        self.assertEqual(tracker.dependency_task_count, 12)

    def test_failed_plain_gradle_run_does_not_invent_a_complete_graph(self) -> None:
        tracker = GradleProgressTracker(
            ["./gradlew", ":app:compileDebugKotlin"],
            "/repo",
            "android-validation-explicit-tasks",
            Console(force_terminal=False),
            requested_tasks=(":app:compileDebugKotlin",),
        )
        tracker.update_from_line("> Task :app:kspDebugKotlin FAILED")
        tracker.finish(1)
        self.assertIsNone(tracker.total)
        self.assertNotIn("discovering", tracker._progress_state().extra_full)

    def test_terminal_task_count_column_always_includes_total_or_discovery_marker(self) -> None:
        from rich.progress import Progress

        progress = Progress()
        task_id = progress.add_task("Gradle", total=None, completed=3)
        task = progress.tasks[task_id]
        column = GradleTaskCountColumn()
        self.assertEqual(column.render(task).plain, "3/? tasks")
        progress.update(task_id, total=8, completed=3)
        self.assertEqual(column.render(progress.tasks[task_id]).plain, "3/8 tasks")


if __name__ == "__main__":
    unittest.main()
