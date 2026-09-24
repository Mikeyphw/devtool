from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from rich.console import Console

from devtool.gradle_progress import GradleProgressTracker
from devtool.models import CommandSpec
from devtool.runners import _requested_gradle_tasks_for_spec, _run_process_live
from devtool.tasker import TaskerProgressSink


class GradleTaskExecutionTelemetryTests(unittest.TestCase):
    def tracker(self, *, requested=()) -> GradleProgressTracker:
        return GradleProgressTracker(
            ["./gradlew", *requested],
            "/repo",
            "android-validation-explicit-tasks",
            Console(force_terminal=False),
            requested_tasks=requested,
        )

    def event(self, sequence: int, kind: str, **values: object) -> str:
        return "DEVTOOL_EVENT " + json.dumps({"sequence": sequence, "kind": kind, **values})

    def test_parallel_plain_console_resume_headers_are_not_new_executions(self) -> None:
        tracker = self.tracker(requested=(":verifyAtt05WebAttentionTests",))

        self.assertTrue(tracker.update_from_line("> Task :verifyAtt05WebAttentionTests\n"))
        self.assertTrue(tracker.update_from_line("> Task :app:compileReleaseKotlin\n"))
        self.assertFalse(tracker.update_from_line("> Task :verifyAtt05WebAttentionTests\n"))
        self.assertTrue(tracker.update_from_line("> Task :core:domain:test FAILED\n"))
        self.assertFalse(tracker.update_from_line("> Task :verifyAtt05WebAttentionTests\n"))

        snap = tracker.snapshot()
        self.assertEqual(snap["task_execution_count"], 3)
        self.assertEqual(snap["task_resume_count"], 2)
        self.assertEqual(snap["task_resume_counts"], {":verifyAtt05WebAttentionTests": 2})
        self.assertEqual(snap["duplicate_execution_start_count"], 0)
        self.assertEqual(snap["task_header_counts"][":verifyAtt05WebAttentionTests"], 3)

    def test_terminal_outcome_is_visible_and_not_treated_as_resume(self) -> None:
        tracker = self.tracker()
        self.assertTrue(tracker.update_from_line("> Task :app:compileDebugKotlin\n"))
        self.assertTrue(tracker.update_from_line("> Task :app:compileDebugKotlin UP-TO-DATE\n"))
        self.assertFalse(tracker.update_from_line("> Task :app:compileDebugKotlin UP-TO-DATE\n"))

        snap = tracker.snapshot()
        self.assertEqual(snap["task_execution_count"], 1)
        self.assertEqual(snap["task_completed_count"], 1)
        self.assertEqual(snap["task_resume_count"], 0)
        self.assertEqual(snap["duplicate_terminal_header_count"], 1)
        self.assertEqual(snap["task_outcomes"], {":app:compileDebugKotlin": "up-to-date"})



    def test_plain_console_preserves_no_source_and_from_cache_outcomes(self) -> None:
        tracker = self.tracker()
        self.assertTrue(tracker.update_from_line("> Task :app:generate NO-SOURCE\n"))
        self.assertTrue(tracker.update_from_line("> Task :app:compile FROM-CACHE\n"))
        snap = tracker.snapshot()
        self.assertEqual(snap["task_outcomes"][":app:generate"], "no-source")
        self.assertEqual(snap["task_outcomes"][":app:compile"], "from-cache")

    def test_structured_duplicate_task_start_is_reported_as_execution_anomaly(self) -> None:
        tracker = self.tracker()
        tracker.update_from_line(self.event(1, "task-start", task=":app:test", completed=0, total=1))
        tracker.update_from_line(self.event(2, "task-start", task=":app:test", completed=0, total=1))
        tracker.update_from_line(self.event(3, "task-finish", task=":app:test", completed=1, total=1, outcome="success"))

        snap = tracker.snapshot()
        self.assertEqual(snap["task_execution_count"], 1)
        self.assertEqual(snap["duplicate_execution_start_count"], 1)
        self.assertEqual(snap["task_completed_count"], 1)

    def test_duplicate_requested_tasks_are_preserved_for_detection_then_deduplicated_for_display(self) -> None:
        tracker = self.tracker(
            requested=(":app:testDebugUnitTest", ":app:lintDebug", ":app:testDebugUnitTest")
        )
        snap = tracker.snapshot()
        self.assertEqual(snap["requested_tasks"], [":app:testDebugUnitTest", ":app:lintDebug"])
        self.assertEqual(snap["requested_duplicate_count"], 1)
        self.assertEqual(snap["requested_duplicate_tasks"], {":app:testDebugUnitTest": 2})
        self.assertIn("duplicates=1", tracker.task_telemetry_line())

    def test_requested_task_extractor_does_not_destroy_duplicate_telemetry(self) -> None:
        spec = CommandSpec(
            "android-validation-explicit-tasks",
            ["python3", "build-apk", "/repo"],
            cwd="/repo",
            metadata={
                "progress_parser": "gradle",
                "gradle_tasks": [":app:test", ":app:lint", ":app:test"],
            },
        )
        self.assertEqual(
            _requested_gradle_tasks_for_spec(spec, list(spec.argv)),
            [":app:test", ":app:lint", ":app:test"],
        )

    def test_default_human_stream_collapses_resume_headers_but_raw_output_keeps_them(self) -> None:
        code = (
            "print('> Task :verifyAtt05WebAttentionTests', flush=True);"
            "print('web output chunk 1', flush=True);"
            "print('> Task :app:compileReleaseKotlin', flush=True);"
            "print('> Task :verifyAtt05WebAttentionTests', flush=True);"
            "print('web output chunk 2', flush=True);"
            "print('> Task :verifyAtt05WebAttentionTests', flush=True);"
            "print('web output chunk 3', flush=True)"
        )
        stdout = StringIO()
        with patch("devtool.runners.console", Console(force_terminal=False)), redirect_stdout(stdout):
            rc, raw = _run_process_live(
                [sys.executable, "-c", code],
                cwd=os.getcwd(),
                env={**os.environ, "DEVTOOL_COMMAND_HEARTBEAT_SECONDS": "0"},
                verbose=False,
                command_name="android-validation-explicit-tasks",
                progress_parser="gradle",
                requested_gradle_tasks=[":verifyAtt05WebAttentionTests"],
            )

        self.assertEqual(rc, 0)
        self.assertEqual(raw.count("> Task :verifyAtt05WebAttentionTests"), 3)
        human = stdout.getvalue()
        self.assertEqual(human.count("> Task :verifyAtt05WebAttentionTests\n"), 1)
        self.assertIn("web output chunk 1", human)
        self.assertIn("web output chunk 2", human)
        self.assertIn("web output chunk 3", human)
        self.assertIn("resumed_headers=2", human)
        self.assertIn("duplicate_execution_starts=0", human)

    def test_verbose_mode_keeps_raw_repeated_headers(self) -> None:
        code = (
            "print('> Task :demo', flush=True);"
            "print('> Task :other', flush=True);"
            "print('> Task :demo', flush=True)"
        )
        stdout = StringIO()
        with patch("devtool.runners.console", Console(force_terminal=False)), redirect_stdout(stdout):
            rc, _ = _run_process_live(
                [sys.executable, "-c", code],
                cwd=os.getcwd(),
                env={**os.environ, "DEVTOOL_COMMAND_HEARTBEAT_SECONDS": "0"},
                verbose=True,
                command_name="build",
                progress_parser="gradle",
            )
        self.assertEqual(rc, 0)
        self.assertEqual(stdout.getvalue().count("> Task :demo\n"), 2)

    def test_tasker_persists_gradle_execution_telemetry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sink = TaskerProgressSink(
                directory=Path(tmp),
                command="validate",
                repo_root="/repo/demo",
                repo_name="demo",
                enabled=True,
                update_interval_s=0,
            )
            sink.phase_started("android-validation-explicit-tasks", index=1, total=1)
            sink.progress(
                detail="Running :app:test",
                completed=10,
                total=20,
                metrics={
                    "requested_duplicate_count": 1,
                    "requested_duplicate_tasks": {":app:test": 2},
                    "task_execution_count": 10,
                    "task_completed_count": 9,
                    "task_resume_count": 7,
                    "task_resume_counts": {":verifyAtt05WebAttentionTests": 7},
                    "task_header_count": 17,
                    "task_header_counts": {":verifyAtt05WebAttentionTests": 8},
                    "task_outcomes": {":app:compileDebugKotlin": "up-to-date"},
                    "duplicate_execution_start_count": 0,
                    "duplicate_terminal_header_count": 0,
                },
                force=True,
            )
            latest = json.loads((Path(tmp) / "latest_event.json").read_text(encoding="utf-8"))
            phase = latest["phase_progress"][0]
            self.assertEqual(phase["task_execution_count"], 10)
            self.assertEqual(phase["task_resume_count"], 7)
            self.assertEqual(phase["requested_duplicate_tasks"], {":app:test": 2})
            self.assertEqual(phase["task_resume_counts"], {":verifyAtt05WebAttentionTests": 7})


if __name__ == "__main__":
    unittest.main()
