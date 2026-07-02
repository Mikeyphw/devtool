from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from devtool.models import OperationResult
from devtool.tasker import ANDROID_TASKER_DIR, TaskerProgressSink, default_tasker_dir, send_logcat_event, send_progress_notification, send_tasker_broadcast, start_auto_hud


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
            notification = json.loads((Path(tmp) / "notification.json").read_text(encoding="utf-8"))
            jsonl_lines = (Path(tmp) / "events.jsonl").read_text(encoding="utf-8").strip().splitlines()

            self.assertEqual(state["producer"], "devtool")
            self.assertEqual(state["event_type"], "finished")
            self.assertEqual(state["status"], "ok")
            self.assertEqual(latest["run_id"], state["run_id"])
            self.assertGreaterEqual(len(events), 4)
            self.assertEqual(len(runs), 1)
            self.assertEqual(state["current"], state["total"])
            self.assertEqual(notification["current"], notification["total"])
            self.assertIn("title", notification)
            self.assertIn("text", notification)
            self.assertGreaterEqual(len(jsonl_lines), 4)

    def test_progress_event_writes_current_total_for_tasker_progress_bar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sink = TaskerProgressSink(
                directory=Path(tmp),
                command="validate",
                repo_root="/repo/demo",
                repo_name="demo",
                enabled=True,
                update_interval_s=0,
            )

            sink.phase_started("build", index=1, total=1, argv=["dotnet", "build"])
            sink.progress(detail="building", completed=2, total=5, force=True)

            notification = json.loads((Path(tmp) / "notification.json").read_text(encoding="utf-8"))
            latest = json.loads((Path(tmp) / "latest_event.json").read_text(encoding="utf-8"))
            self.assertEqual(notification["current"], 2)
            self.assertEqual(notification["total"], 5)
            self.assertEqual(latest["current"], 2)
            self.assertEqual(latest["total"], 5)
            self.assertEqual(latest["progress"]["current"], 2)

    def test_send_logcat_event_uses_android_log_tag(self) -> None:
        with patch("devtool.tasker.shutil.which", return_value="/system/bin/log"), patch("devtool.tasker.subprocess.run") as run_mock:
            run_mock.return_value.returncode = 0
            ok = send_logcat_event("DEVTOOL", {"type": "progress", "current": 1, "total": 2})
        self.assertTrue(ok)
        argv = run_mock.call_args.args[0]
        self.assertEqual(argv[:3], ["log", "-t", "DEVTOOL"])
        self.assertIn('"current":1', argv[3])

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

    def test_default_tasker_dir_uses_xdg_on_desktop_even_if_sdcard_exists(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "XDG_STATE_HOME": "/home/mike/.local/state",
                "PREFIX": "/usr",
            },
            clear=True,
        ), patch("devtool.tasker.Path.exists", return_value=True):
            self.assertEqual(default_tasker_dir(), "/home/mike/.local/state/devtool")

    def test_default_tasker_dir_uses_android_path_on_termux(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "PREFIX": "/data/data/com.termux/files/usr",
                "TERMUX_VERSION": "0.118.0",
            },
            clear=True,
        ):
            self.assertEqual(default_tasker_dir(), ANDROID_TASKER_DIR)

    def test_start_auto_hud_is_best_effort_when_directory_denied(self) -> None:
        with patch("devtool.tasker.Path.mkdir", side_effect=PermissionError("denied")):
            self.assertFalse(start_auto_hud(Path("/sdcard/Tasker/devtool")))


if __name__ == "__main__":
    unittest.main()
