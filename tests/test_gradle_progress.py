from __future__ import annotations

import json
import unittest

from rich.console import Console

from devtool.gradle_progress import GradleProgressTracker


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

    def test_ignores_non_event_output(self) -> None:
        tracker = self.make_tracker()
        tracker.update_from_line("> Task :app:compileDebugKotlin")
        self.assertEqual(tracker.last_sequence, 0)


if __name__ == "__main__":
    unittest.main()
