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

    def test_failed_validation_reports_successful_rollback_as_recovery(self) -> None:
        results = [
            OperationResult("build", ["dotnet", "build"], 1, 1.0, "Build failed", []),
            OperationResult("apply-rollback", ["rsync", "rollback"], 0, 0.2, "Rollback completed", []),
        ]
        payload = _completion_payload(
            ok=False,
            totals={"warnings": 0, "errors": 1, "failed": 1, "skipped": 0},
            results=results,
        )
        self.assertEqual(payload["headline"], "Build failed; changes rolled back")
        self.assertIn("1 error", payload["detail"])
        self.assertTrue(payload["rolled_back"])
        rollback = next(item for item in payload["actions"] if item["name"] == "apply-rollback")
        self.assertEqual(rollback["status"], "ok")

    def test_finished_state_keeps_original_failure_phase_after_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sink = TaskerProgressSink(directory=Path(tmp), command="apply-overlay", repo_root="/repo", repo_name="repo")
            sink.phase_started("build", index=1, total=2)
            failed = OperationResult("build", ["dotnet", "build"], 1, 1.0, "Build failed", [])
            sink.phase_finished(failed)
            sink.phase_started("apply-rollback", index=2, total=2)
            rollback = OperationResult("apply-rollback", ["rsync", "rollback"], 0, 0.2, "Rollback completed", [])
            sink.phase_finished(rollback)
            sink.finished(ok=False, totals={"warnings": 0, "errors": 1, "failed": 1, "skipped": 0}, results=[failed, rollback])
            import json
            state = json.loads((Path(tmp) / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["phase"]["name"], "build")
            self.assertEqual(state["completion"]["headline"], "Build failed; changes rolled back")
            self.assertEqual(compact_operation_title(HudState(status="failed", phase_name="build", raw=state)),
                             "Build failed; changes rolled back")


if __name__ == "__main__":
    unittest.main()


class OverlayCompletionSummaryTests(unittest.TestCase):
    def test_success_copy_names_single_applied_overlay(self) -> None:
        metadata = {
            "overlay_index": 1,
            "overlay_total": 1,
            "overlay_label": "s3atelier_object_modl_mlod_workbench_overlay.zip",
            "overlay_target": "s3atelier_avalonia_S3Atelier_Modern",
            "overlay_artifact": "/home/mike/Downloads/s3atelier_object_modl_mlod_workbench_overlay.zip",
        }
        results = [
            OperationResult("apply-overlay", ["apply"], 0, 0.2, "ok", [], metadata=metadata),
            OperationResult("test", ["test"], 0, 0.3, "Passed! - Failed: 0, Passed: 5, Skipped: 0, Total: 5", [], metadata=metadata),
        ]
        payload = _completion_payload(
            ok=True,
            totals={"warnings": 0, "errors": 0, "failed": 0, "skipped": 0},
            results=results,
        )
        text = completion_copy_text(HudState(status="ok", raw={"completion": payload}))
        self.assertIn("Applied overlay:", text)
        self.assertIn("s3atelier_object_modl_mlod_workbench_overlay.zip", text)
        self.assertIn("s3atelier_avalonia_S3Atelier_Modern", text)

    def test_success_copy_names_ordered_overlay_set(self) -> None:
        results = []
        for index, label in ((1, "first_overlay.zip"), (2, "second_overlay.zip")):
            metadata = {
                "overlay_index": index,
                "overlay_total": 2,
                "overlay_label": label,
                "overlay_target": "s3atelier",
                "overlay_artifact": f"/downloads/{label}",
            }
            results.append(OperationResult("apply-overlay", ["apply"], 0, 0.1, "ok", [], metadata=metadata))
        payload = _completion_payload(
            ok=True,
            totals={"warnings": 0, "errors": 0, "failed": 0, "skipped": 0},
            results=results,
        )
        text = completion_copy_text(HudState(status="ok", raw={"completion": payload}))
        self.assertIn("Applied overlays:", text)
        self.assertLess(text.index("first_overlay.zip"), text.index("second_overlay.zip"))
