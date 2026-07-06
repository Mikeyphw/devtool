from __future__ import annotations

import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from devtool.hud import HudState, TermuxNotificationHud, choose_hud_mode, last_phase_highlights, latest_log_path, parse_hud_state, render_ascii_bar, state_from_directory
from devtool.cli import build_parser


class HudTests(unittest.TestCase):
    def test_parse_simple_notification_current_total(self) -> None:
        state = parse_hud_state({"title": "Build", "text": "Compiling", "current": 3, "total": 10})
        self.assertEqual(state.title, "Build")
        self.assertEqual(state.text, "Compiling")
        self.assertEqual(state.current, 3)
        self.assertEqual(state.total, 10)
        self.assertEqual(state.percent, 30)
        self.assertIn("30%", state.display_title)

    def test_parse_rich_event_envelope(self) -> None:
        state = parse_hud_state(
            {
                "status": "running",
                "event_type": "progress",
                "repo_name": "demo",
                "command": "validate",
                "phase": {"name": "build"},
                "progress": {"completed": 2, "total": 5, "detail": "Building"},
                "notification": {"title": "devtool RUN", "text": "demo • validate"},
            }
        )
        self.assertEqual(state.current, 2)
        self.assertEqual(state.total, 5)
        self.assertEqual(state.phase_name, "build")
        self.assertIn("demo", state.display_text)
        self.assertIn("validate", state.display_text)

    def test_state_from_directory_prefers_notification_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "state.json").write_text(json.dumps({"title": "old", "current": 1, "total": 2}), encoding="utf-8")
            (root / "notification.json").write_text(json.dumps({"title": "new", "current": 4, "total": 8}), encoding="utf-8")
            state = state_from_directory(root)
        self.assertEqual(state.title, "new")
        self.assertEqual(state.current, 4)
        self.assertEqual(state.total, 8)


    def test_state_from_directory_preserves_target_when_notification_is_primary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "state.json").write_text(json.dumps({"repo_name": "demo", "command": "validate", "target": "Demo.sln", "phase": {"name": "test"}, "current": 1, "total": 2}), encoding="utf-8")
            (root / "notification.json").write_text(json.dumps({"title": "Tests", "text": "running", "current": 4, "total": 8, "target": "Demo.Tests.csproj"}), encoding="utf-8")
            state = state_from_directory(root)
        self.assertEqual(state.repo_name, "demo")
        self.assertEqual(state.phase_name, "test")
        self.assertEqual(state.target, "Demo.Tests.csproj")
        self.assertEqual(state.current, 4)
        self.assertEqual(state.total, 8)

    def test_recent_error_lines_highlight_failures_without_zero_failed_noise(self) -> None:
        from devtool.hud import recent_error_lines
        events = [
            {"event_type": "progress", "message": "tests: 6 passed, 0 failed, 0 skipped"},
            {"event_type": "phase_finished", "phase": {"name": "build"}, "result": {"status": "failed"}, "message": "Build failed"},
        ]
        self.assertEqual(recent_error_lines(events), ["Build failed"])


    def test_raw_vstest_success_is_not_an_error(self) -> None:
        from devtool.hud import event_icon, event_is_error, friendly_event_line, recent_error_lines

        event = {"event_type": "progress", "phase": {"name": "test"}, "status": "failed", "message": "Passed! - Failed: 0, Passed: 12, Skipped: 0, Total: 12, Duration: 1 s"}
        self.assertFalse(event_is_error(event))
        self.assertEqual(recent_error_lines([event]), [])
        self.assertEqual(friendly_event_line(event), "Tests passing: 12 passed")
        self.assertEqual(event_icon(event), "✓")

    def test_raw_vstest_failure_is_an_error(self) -> None:
        from devtool.hud import event_icon, event_is_error, friendly_event_line, recent_error_lines

        event = {"event_type": "progress", "phase": {"name": "test"}, "message": "Failed! - Failed: 2, Passed: 10, Skipped: 1, Total: 13, Duration: 1 s"}
        self.assertTrue(event_is_error(event))
        self.assertEqual(recent_error_lines([event]), ["Tests failing: 10 passed, 2 failed, 1 skipped"])
        self.assertEqual(friendly_event_line(event), "Tests failing: 10 passed, 2 failed, 1 skipped")
        self.assertEqual(event_icon(event), "❌")

    def test_gtk_dashboard_hud_source_uses_compact_sections(self) -> None:
        import inspect
        import devtool.hud as hud_module

        source = inspect.getsource(hud_module.GtkDesktopHud.run)
        self.assertIn("DEVTOOL", source)
        self.assertIn("Validate → Apply → Test → Commit", source)
        self.assertIn("ERRORS", source)
        self.assertIn("WARNINGS", source)
        self.assertIn("RECENT", source)
        self.assertIn("state_context_label", source)

    def test_render_ascii_bar_clamps_progress(self) -> None:
        self.assertEqual(render_ascii_bar(5, 10, width=10), "█████░░░░░")
        self.assertEqual(render_ascii_bar(20, 10, width=10), "██████████")
        self.assertEqual(render_ascii_bar(None, None, width=4), "░░░░")

    def test_choose_hud_mode_auto_prefers_termux_gui_then_notification_then_terminal(self) -> None:
        with patch("devtool.hud.TermuxGuiHud.available", return_value=True), patch("devtool.hud.shutil.which", return_value=None):
            self.assertEqual(choose_hud_mode("auto"), "android-gui")
        with patch("devtool.hud._is_termux_android", return_value=True), patch("devtool.hud.TermuxGuiHud.available", return_value=False), patch("devtool.hud.shutil.which", return_value="/bin/termux-notification"):
            self.assertEqual(choose_hud_mode("auto"), "android-notify")
        with patch("devtool.hud._is_termux_android", return_value=False), patch("devtool.hud.TermuxGuiHud.available", return_value=False), patch("devtool.hud.GtkDesktopHud.available", return_value=False), patch("devtool.hud.DesktopNotificationHud.available", return_value=False), patch("devtool.hud.shutil.which", return_value=None):
            self.assertEqual(choose_hud_mode("auto"), "terminal")

    def test_notification_hud_builds_stable_argv(self) -> None:
        hud = TermuxNotificationHud(notification_id="42")
        argv = hud._build_argv(HudState(title="Build", text="Compiling", current=2, total=4))
        self.assertIn("termux-notification", argv[0])
        self.assertIn("--id", argv)
        self.assertIn("42", argv)
        self.assertIn("Build (50%)", argv)
        self.assertIn("Compiling", argv)


    def test_last_phase_highlights_keep_restore_build_test_details(self) -> None:
        events = [
            {"event_type": "progress", "phase": {"name": "restore"}, "message": "restored App.Core", "progress": {"detail": "restored App.Core"}},
            {"event_type": "progress", "phase": {"name": "build"}, "message": "built App.UI -> bin/Debug/App.UI.dll"},
            {"event_type": "progress", "phase": {"name": "test"}, "message": "Starting test execution, please wait..."},
            {"event_type": "progress", "phase": {"name": "test"}, "message": "tests: 6 passed, 0 failed, 0 skipped"},
        ]
        highlights = last_phase_highlights(events)
        self.assertEqual(highlights["restore"], "Restored App.Core")
        self.assertEqual(highlights["build"], "Built App.UI")
        self.assertEqual(highlights["test"], "Tests passing: 6 passed")

    def test_latest_log_path_reads_result_and_final_results(self) -> None:
        events = [
            {"event_type": "phase_finished", "result": {"log_path": "/tmp/restore.log"}},
            {"event_type": "finished", "results": [{"log_path": "/tmp/build.log"}, {"log_path": "/tmp/test.log"}]},
        ]
        self.assertEqual(latest_log_path(events), "/tmp/test.log")

    def test_cli_accepts_hud_command(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["hud", "--mode", "terminal", "--once"])
        self.assertEqual(args.command, "hud")
        self.assertEqual(args.mode, "terminal")
        self.assertTrue(args.once)


class HudV345Tests(unittest.TestCase):
    def test_snapshot_from_directory_prefers_notification_and_tails_events(self) -> None:
        from devtool.hud import snapshot_from_directory
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "notification.json").write_text(json.dumps({"title": "Build", "current": 2, "total": 4}), encoding="utf-8")
            (root / "events.jsonl").write_text("\n".join(json.dumps({"event_type": "progress", "message": f"step {i}", "current": i, "total": 5}) for i in range(5)), encoding="utf-8")
            snapshot = snapshot_from_directory(root, recent_events=2)
        self.assertEqual(snapshot["version"], 5)
        self.assertEqual(snapshot["state"]["title"], "Build")
        self.assertEqual(snapshot["state"]["percent"], 50)
        self.assertEqual(len(snapshot["events"]), 2)
        self.assertEqual(snapshot["events"][-1]["message"], "step 4")

    def test_choose_hud_mode_aliases_v3_v4_v5(self) -> None:
        from devtool.hud import choose_hud_mode
        self.assertEqual(choose_hud_mode("v3"), "terminal")
        self.assertEqual(choose_hud_mode("v4"), "web")
        self.assertEqual(choose_hud_mode("v5"), "studio")
        self.assertEqual(choose_hud_mode("dashboard"), "web")
        self.assertEqual(choose_hud_mode("all"), "studio")

    def test_web_dashboard_once_prints_snapshot(self) -> None:
        from devtool.hud import WebDashboardHud
        import io
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "notification.json").write_text(json.dumps({"title": "Test", "current": 1, "total": 2}), encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = WebDashboardHud(port=0).run(directory=root, once=True)
            data = json.loads(buf.getvalue())
        self.assertEqual(rc, 0)
        self.assertEqual(data["state"]["title"], "Test")
        self.assertEqual(data["state"]["percent"], 50)

    def test_cli_accepts_hud_v5_web_flags(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["hud", "--mode", "v5", "--host", "0.0.0.0", "--port", "9999", "--studio-notify", "--once"])
        self.assertEqual(args.command, "hud")
        self.assertEqual(args.mode, "v5")
        self.assertEqual(args.host, "0.0.0.0")
        self.assertEqual(args.port, 9999)
        self.assertTrue(args.studio_notify)

    def test_choose_desktop_aliases(self) -> None:
        from devtool.hud import choose_hud_mode
        with patch("devtool.hud.GtkDesktopHud.available", return_value=False), patch("devtool.hud.DesktopNotificationHud.available", return_value=True):
            self.assertEqual(choose_hud_mode("desktop"), "desktop-notify")
        with patch("devtool.hud.GtkDesktopHud.available", return_value=True), patch("devtool.hud.DesktopNotificationHud.available", return_value=True):
            self.assertEqual(choose_hud_mode("v6"), "desktop-window")
        self.assertEqual(choose_hud_mode("desktop-window"), "desktop-window")

    def test_desktop_notification_argv_contains_progress_hint(self) -> None:
        from devtool.hud import DesktopNotificationHud
        hud = DesktopNotificationHud(notification_id="42")
        argv = hud._build_argv(HudState(title="Build", text="Compiling", current=2, total=4))
        self.assertEqual(argv[0], "notify-send")
        self.assertIn("--app-name=devtool", argv)
        self.assertIn("--hint=int:value:50", argv)
        self.assertIn("Build (50%)", argv)

    def test_cli_accepts_desktop_modes(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["hud", "--mode", "desktop", "--once"])
        self.assertEqual(args.mode, "desktop")
        args = parser.parse_args(["hud", "--mode", "desktop-window", "--once"])
        self.assertEqual(args.mode, "desktop-window")
        args = parser.parse_args(["hud", "--mode", "v6", "--once"])
        self.assertEqual(args.mode, "v6")


    def test_android_hud_modes_are_accepted(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["hud", "--mode", "android-gui", "--once"])
        self.assertEqual(args.mode, "android-gui")
        args = parser.parse_args(["hud", "--mode", "android-notify", "--once"])
        self.assertEqual(args.mode, "android-notify")
        args = parser.parse_args(["hud", "--mode", "termux-gui", "--once"])
        self.assertEqual(args.mode, "termux-gui")

    def test_android_hud_aliases_and_termux_auto(self) -> None:
        from devtool.hud import choose_hud_mode
        self.assertEqual(choose_hud_mode("termux-gui"), "android-gui")
        self.assertEqual(choose_hud_mode("android-overlay"), "android-gui")
        with patch("devtool.hud._is_termux_android", return_value=True), \
             patch("devtool.hud.TermuxGuiHud.available", return_value=True), \
             patch("devtool.hud.shutil.which", return_value=None), \
             patch("devtool.hud.GtkDesktopHud.available", return_value=True):
            self.assertEqual(choose_hud_mode("desktop"), "android-gui")
            self.assertEqual(choose_hud_mode("auto"), "android-gui")
        with patch("devtool.hud._is_termux_android", return_value=True), \
             patch("devtool.hud.TermuxGuiHud.available", return_value=False), \
             patch("devtool.hud.shutil.which", return_value="/data/data/com.termux/files/usr/bin/termux-notification"):
            self.assertEqual(choose_hud_mode("desktop"), "android-notify")

    def test_termux_gui_activity_prefers_overlay(self) -> None:
        from devtool.hud import TermuxGuiHud

        calls: list[dict[str, object]] = []

        class FakeActivity:
            def __init__(self, connection: object, **kwargs: object) -> None:
                calls.append(kwargs)
            def sendoverlayevents(self, send: bool) -> None: pass
            def setposition(self, x: int, y: int) -> None: pass

        fake_tg = types.SimpleNamespace(Activity=FakeActivity)
        activity, overlay_active, presentation = TermuxGuiHud._make_activity(fake_tg, object(), overlay=True)
        self.assertIsInstance(activity, FakeActivity)
        self.assertTrue(overlay_active)
        self.assertEqual(presentation, "floating overlay")
        self.assertEqual(calls[-1].get("overlay"), True)

    def test_termux_gui_activity_falls_back_without_overlay_permission(self) -> None:
        from devtool.hud import TermuxGuiHud

        calls: list[dict[str, object]] = []

        class FakeConnection:
            def toast(self, text: str, long: bool = False) -> None: pass

        class FakeActivity:
            def __init__(self, connection: object, **kwargs: object) -> None:
                calls.append(kwargs)
                if kwargs.get("overlay"):
                    raise RuntimeError("overlay permission missing")

        fake_tg = types.SimpleNamespace(Activity=FakeActivity)
        activity, overlay_active, presentation = TermuxGuiHud._make_activity(fake_tg, FakeConnection(), overlay=True)
        self.assertIsInstance(activity, FakeActivity)
        self.assertFalse(overlay_active)
        self.assertEqual(presentation, "normal activity")
        self.assertEqual(calls[0].get("overlay"), True)
        self.assertNotIn("overlay", calls[-1])

    def test_gtk_desktop_hud_pins_gdk3_before_import(self) -> None:
        import inspect
        import devtool.hud as hud_module

        source = inspect.getsource(hud_module.GtkDesktopHud._require_gtk3)
        self.assertLess(source.index('gi.require_version("Gdk", "3.0")'), source.index("from gi.repository import Gdk"))
        self.assertLess(source.index('gi.require_version("Gtk", "3.0")'), source.index("from gi.repository import Gdk"))


if __name__ == "__main__":
    unittest.main()

class AndroidBridgeHudTests(unittest.TestCase):
    def test_android_bridge_aliases_and_chroot_auto(self) -> None:
        from devtool.hud import choose_hud_mode
        self.assertEqual(choose_hud_mode("android-chroot"), "android-bridge")
        self.assertEqual(choose_hud_mode("termux-bridge"), "android-bridge")
        with patch("devtool.hud._is_termux_android", return_value=False), \
             patch("devtool.hud._is_termux_chroot_bridge", return_value=True), \
             patch("devtool.hud.GtkDesktopHud.available", return_value=True), \
             patch("devtool.hud.TermuxGuiHud.available", return_value=False):
            self.assertEqual(choose_hud_mode("desktop"), "android-bridge")
            self.assertEqual(choose_hud_mode("auto"), "android-bridge")
        self.assertEqual(choose_hud_mode("desktop-window"), "desktop-window")

    def test_default_android_bridge_dir_uses_env(self) -> None:
        from devtool.hud import default_android_bridge_dir
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"DEVTOOL_HUD_BRIDGE_DIR": tmp}, clear=False):
                self.assertEqual(default_android_bridge_dir(), Path(tmp))

    def test_android_bridge_hud_mirrors_state(self) -> None:
        from devtool.hud import AndroidBridgeHud
        with tempfile.TemporaryDirectory() as source_tmp, tempfile.TemporaryDirectory() as bridge_tmp:
            source = Path(source_tmp)
            bridge = Path(bridge_tmp)
            (source / "notification.json").write_text(json.dumps({"title": "Build", "text": "Compiling", "target": "Demo.sln", "current": 1, "total": 2}), encoding="utf-8")
            (source / "events.jsonl").write_text(json.dumps({"event_type": "progress", "message": "built Demo"}) + "\n", encoding="utf-8")
            rc = AndroidBridgeHud(bridge_dir=bridge).run(directory=source, interval_s=0.05, once=True)
            self.assertEqual(rc, 0)
            mirrored = json.loads((bridge / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(mirrored["title"], "Build")
            self.assertTrue((bridge / "bridge-status.json").exists())
            self.assertIn("built Demo", (bridge / "events.jsonl").read_text(encoding="utf-8"))

    def test_cli_accepts_android_bridge_commands(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["hud", "--mode", "android-bridge", "--once"])
        self.assertEqual(args.mode, "android-bridge")
        args = parser.parse_args(["android-hud-bridge", "--mode", "android-notify", "--state-dir", "/tmp/hud", "--once"])
        self.assertEqual(args.command, "android-hud-bridge")
        self.assertEqual(args.mode, "android-notify")
        self.assertEqual(args.state_dir, "/tmp/hud")
