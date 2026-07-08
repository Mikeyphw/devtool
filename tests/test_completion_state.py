from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from devtool.hud import HudState, completion_copy_text, compact_detail_text, compact_operation_title, pipeline_label
from devtool.models import Issue, OperationResult
from devtool.tasker import TaskerProgressSink, _completion_payload


class CompletionStateTests(unittest.TestCase):
    def _results(self) -> list[OperationResult]:
        return [
            OperationResult("apply-overlay", ["devtool"], 0, 0.06, "Applied overlay", []),
            OperationResult("restore", ["dotnet", "restore"], 0, 4.25, "Restore completed", []),
            OperationResult("build", ["dotnet", "build"], 0, 22.61, "Build succeeded", [Issue("warning", "build", "App.cs", "CS0618", "Old API", 12)]),
            OperationResult("test", ["dotnet", "test"], 0, 20.64, "Passed! - Failed: 0, Passed: 207, Skipped: 0, Total: 207", []),
            OperationResult("git-commit", ["git", "commit"], 0, 0.10, "[main b8f3a62] Update", []),
            OperationResult("artifact-cleanup", ["cleanup"], 0, 0.00, "clean", []),
        ]

    def test_completion_payload_includes_tests_warning_commit_and_duration(self) -> None:
        payload = _completion_payload(ok=True, totals={"warnings": 1, "errors": 0, "failed": 0, "skipped": 0}, results=self._results())
        self.assertEqual(payload["headline"], "Completed successfully")
        self.assertEqual(payload["tests"]["total"], 207)
        self.assertEqual(payload["commit_hash"], "b8f3a62")
        self.assertIn("1 warning", payload["detail"])
        self.assertIn("App.cs:12", payload["warning_details"][0])
        self.assertGreater(payload["duration_s"], 47)

    def test_finished_event_persists_completion_payload_and_notification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sink = TaskerProgressSink(directory=Path(tmp), command="apply-overlay", repo_root="/repo", repo_name="repo")
            sink.finished(ok=True, totals={"warnings": 1, "errors": 0, "failed": 0, "skipped": 0}, results=self._results())
            import json
            state = json.loads((Path(tmp) / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["completion"]["tests"]["passed"], 207)
            self.assertEqual(state["notification"]["title"], "Completed successfully")
            self.assertEqual(state["progress"]["percent"], 100.0)

    def test_hud_uses_descriptive_final_completion(self) -> None:
        completion = _completion_payload(ok=True, totals={"warnings": 1, "errors": 0, "failed": 0, "skipped": 0}, results=self._results())
        state = HudState(status="ok", raw={"completion": completion})
        self.assertEqual(compact_operation_title(state), "Completed successfully")
        self.assertIn("207 passed", compact_detail_text(state))
        self.assertIn("Git commit: b8f3a62", completion_copy_text(state))
        self.assertIn("✓ Test", pipeline_label(state, {}))
        self.assertIn("✓ Commit", pipeline_label(state, {}))


if __name__ == "__main__":
    unittest.main()
