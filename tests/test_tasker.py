from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from devtool.models import OperationResult
from devtool.tasker import TaskerProgressSink, send_progress_notification, send_tasker_broadcast


class TaskerProgressTests(unittest.TestCase):
    def test_sink_writes_state_latest_and_event_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sink = TaskerProgressSink(
                directory=Path(tmp),
                command="validate",
                repo_root="/repo/demo",
                repo_name="demo",
                enabled=True,
            )

            sink.started()
            sink.phase_started("restore", index=1, total=3, argv=["dotnet", "restore", "App.sln"])
            sink.progress(detail="restored App", completed=1, total=3, force=True)
            sink.phase_finished(OperationResult("restore", ["dotnet", "restore"], 0, 1.25, "", []))
            sink.finished(ok=True, totals={"warnings": 0, "errors": 0, "failed": 0, "skipped": 0}, results=[], notification={"title": "PASS", "body": "done"})

            state = json.loads((Path(tmp) / "state.json").read_text(encoding="utf-8"))
            latest = json.loads((Path(tmp) / "latest_event.json").read_text(encoding="utf-8"))
            events = list((Path(tmp) / "events").glob("*.json"))
            runs = list((Path(tmp) / "runs").glob("*.json"))

            self.assertEqual(state["producer"], "devtool")
            self.assertEqual(state["event_type"], "finished")
            self.assertEqual(state["status"], "ok")
            self.assertEqual(latest["run_id"], state["run_id"])
            self.assertGreaterEqual(len(events), 4)
            self.assertEqual(len(runs), 1)

    def test_send_tasker_broadcast_uses_event_and_state_paths(self) -> None:
        with patch("devtool.tasker.shutil.which", return_value="/system/bin/am"), patch("devtool.tasker.subprocess.run") as run_mock:
            run_mock.return_value.returncode = 0
            ok = send_tasker_broadcast("net.mph.devtool.EVENT", Path("/tmp/event.json"), Path("/tmp/state.json"), "progress", "rid")
        self.assertTrue(ok)
        argv = run_mock.call_args.args[0]
        self.assertIn("net.mph.devtool.EVENT", argv)
        self.assertIn("/tmp/event.json", argv)
        self.assertIn("/tmp/state.json", argv)

    def test_send_progress_notification_uses_stable_id(self) -> None:
        with patch("devtool.tasker.shutil.which", return_value="/usr/bin/termux-notification"), patch("devtool.tasker.subprocess.run") as run_mock:
            run_mock.return_value.returncode = 0
            ok = send_progress_notification(notification_id="7771", title="devtool", body="running", ongoing=True)
        self.assertTrue(ok)
        argv = run_mock.call_args.args[0]
        self.assertEqual(argv[:5], ["termux-notification", "--id", "7771", "--title", "devtool"])
        self.assertIn("--ongoing", argv)


if __name__ == "__main__":
    unittest.main()
