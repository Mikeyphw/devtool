from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from devtool.models import OperationResult
from devtool.tasker import ANDROID_TASKER_DIR, DEFAULT_EVENT_HTTP_URL, TaskerProgressSink, default_tasker_dir, send_local_http_event, start_auto_hud


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
            self.assertEqual(len(runs), 0)
            self.assertIn("process_start", state)
            self.assertIn("boot_id", state)
            self.assertIn("heartbeat_at", state)
            self.assertEqual(state["current"], state["total"])
            self.assertEqual(notification["current"], notification["total"])
            self.assertIn("title", notification)
            self.assertIn("text", notification)
            self.assertGreaterEqual(len(jsonl_lines), 4)


    def test_sink_uses_shared_run_id_from_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"DEVTOOL_RUN_ID": "shared-run"}, clear=False):
            sink = TaskerProgressSink(
                directory=Path(tmp),
                command="validate",
                repo_root="/repo/demo",
                repo_name="demo",
                enabled=True,
            )
            self.assertEqual(sink.run_id, "shared-run")
            sink.started()
            run_files = list((Path(tmp) / "runs").glob("*.json"))
            self.assertEqual([item.name for item in run_files], ["shared-run.json"])
            sink.finished(ok=True, totals={"warnings": 0, "errors": 0, "failed": 0, "skipped": 0}, results=[])
            self.assertFalse((Path(tmp) / "runs" / "shared-run.json").exists())


    def test_sink_persists_per_run_overlay_preference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sink = TaskerProgressSink(
                directory=Path(tmp),
                command="validate",
                repo_root="/repo/demo",
                repo_name="demo",
                enabled=True,
                overlay=True,
            )
            sink.started()
            state = json.loads((Path(tmp) / "state.json").read_text(encoding="utf-8"))
            self.assertTrue(state["overlay"])
            sink.finished(ok=True, totals={"warnings": 0, "errors": 0, "failed": 0, "skipped": 0}, results=[])

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

    def test_sink_publishes_planned_multi_target_pipeline_and_live_issue_totals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sink = TaskerProgressSink(
                directory=Path(tmp),
                command="apply-overlay",
                repo_root="/repo/demo",
                repo_name="demo",
                enabled=True,
                update_interval_s=0,
            )
            sink.started()
            sink.plan_phases([
                {"name": "build[s3atelier]", "workspace": "s3atelier", "target": "s3atelier-target"},
                {"name": "build[s3pi-base]", "workspace": "s3pi-base", "target": "s3pi-target"},
            ])
            sink.phase_started(
                "build[s3atelier]",
                index=1,
                total=2,
                argv=["dotnet", "build", "S3Atelier.Modern.sln"],
                metadata={"workspace": "s3atelier", "bundle_target": "s3atelier-target"},
            )
            sink.progress(detail="built 4 of 10 projects", completed=4, total=10, warnings=2, errors=1, force=True)

            latest = json.loads((Path(tmp) / "latest_event.json").read_text(encoding="utf-8"))
            self.assertEqual(latest["current"], 4)
            self.assertEqual(latest["total"], 10)
            self.assertEqual(latest["totals"], {"warnings": 2, "errors": 1})
            self.assertEqual(latest["phase"]["workspace"], "s3atelier")
            self.assertEqual(latest["targets"], ["s3atelier-target", "s3pi-target"] )
            self.assertEqual(len(latest["pipeline"]), 2)
            self.assertEqual(len(latest["phase_progress"]), 1)
            live_phase = latest["phase_progress"][0]
            self.assertEqual(live_phase["name"], "build[s3atelier]")
            self.assertEqual(live_phase["workspace"], "s3atelier")
            self.assertEqual(live_phase["completed"], 4)
            self.assertEqual(live_phase["total"], 10)
            self.assertEqual(live_phase["percent"], 40.0)
            self.assertEqual(live_phase["warnings"], 2)
            self.assertEqual(live_phase["errors"], 1)

    def test_send_local_http_event_posts_full_json_to_localhost(self) -> None:
        response = Mock()
        response.status = 204
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        large_detail = "x" * 2048
        with patch("devtool.tasker.urllib.request.urlopen", return_value=response) as urlopen_mock:
            ok = send_local_http_event({"type": "progress", "current": 1, "total": 2, "detail": large_detail})

        self.assertTrue(ok)
        request = urlopen_mock.call_args.args[0]
        self.assertEqual(request.full_url, DEFAULT_EVENT_HTTP_URL)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.headers["Content-type"], "application/json; charset=utf-8")
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["current"], 1)
        self.assertEqual(body["detail"], large_detail)

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


def test_direct_termux_android_bridge_autostarts_native_renderer() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        runtime = root / "runtime"
        registry = root / "registry"
        completed = Mock(returncode=0, stdout="4321\n")
        with patch.dict("os.environ", {
            "DEVTOOL_HUD_REGISTRY_DIR": str(registry),
            "DEVTOOL_HUD_SINGLETON": "kill",
        }, clear=False), patch("devtool.hud.choose_hud_mode", return_value="android-bridge"), patch(
            "devtool.hud._is_termux_android", return_value=True
        ), patch("devtool.tasker._pid_is_alive", return_value=False), patch(
            "devtool.tasker.subprocess.run", return_value=completed
        ) as run:
            assert start_auto_hud(runtime, mode="android-bridge")
        launcher = run.call_args.args[0][-1]
        assert "--mode android-bridge" in launcher
        assert "--follow-active" in launcher


def test_devtool_no_hud_hard_disables_auto_hud(tmp_path) -> None:
    with patch.dict("os.environ", {"DEVTOOL_NO_HUD": "1"}, clear=False), patch(
        "devtool.tasker.subprocess.run"
    ) as run:
        assert start_auto_hud(tmp_path / "hud", mode="desktop") is False
    run.assert_not_called()
