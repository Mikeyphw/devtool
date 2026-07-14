from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from devtool.hud import AndroidAppHud, HudState, choose_hud_mode, desktop_hud_starts_expanded, events_for_state, last_phase_highlights, latest_log_path, live_issue_counts, operation_steps, parse_hud_state, render_ascii_bar, state_from_directory
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

    def test_state_from_directory_keeps_higher_progress_when_notification_regresses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "state.json").write_text(json.dumps({"title": "Build", "current": 7, "total": 10}), encoding="utf-8")
            (root / "notification.json").write_text(json.dumps({"title": "Build", "current": 1, "total": 2}), encoding="utf-8")
            state = state_from_directory(root)
        self.assertEqual(state.current, 7)
        self.assertEqual(state.total, 10)
        self.assertEqual(state.percent, 70)

    def test_recent_error_lines_highlight_failures_without_zero_failed_noise(self) -> None:
        from devtool.hud import recent_error_lines
        events = [
            {"event_type": "progress", "message": "tests: 6 passed, 0 failed, 0 skipped"},
            {"event_type": "phase_finished", "phase": {"name": "build"}, "result": {"status": "failed"}, "message": "Build failed"},
        ]
        self.assertEqual(recent_error_lines(events), ["Build failed"])

    def test_events_for_state_excludes_previous_run_errors(self) -> None:
        state = HudState(status="running", raw={"run_id": "current"})
        events = [
            {"run_id": "previous", "event_type": "phase_finished", "phase": {"name": "build"}, "result": {"status": "failed"}, "message": "Build failed"},
            {"run_id": "current", "event_type": "started", "status": "running", "message": "Started"},
        ]
        self.assertEqual(events_for_state(events, state), [events[1]])

    def test_recent_errors_prefer_actionable_failure_over_generic_final_line(self) -> None:
        from devtool.hud import recent_error_lines
        events = [
            {"event_type": "phase_finished", "phase": {"name": "test"}, "result": {"status": "failed"}, "message": "Tests failed"},
            {"event_type": "finished", "status": "failed", "message": "Completed with errors"},
        ]
        self.assertEqual(recent_error_lines(events), ["Tests failed"])

    def test_successful_rollback_is_not_reported_as_an_error(self) -> None:
        from devtool.hud import event_is_error, friendly_event_line
        event = {
            "event_type": "phase_finished",
            "phase": {"name": "apply-rollback"},
            "result": {"status": "ok", "exit_code": 0},
            "message": "Apply Rollback completed",
        }
        self.assertFalse(event_is_error(event))
        self.assertEqual(friendly_event_line(event), "Changes rolled back")


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
        self.assertIn("_render_operation_steps", source)
        self.assertIn("hud-step-progress", source)
        self.assertNotIn("Validate → Apply → Test → Commit", source)
        self.assertIn("ERRORS", source)
        self.assertIn("WARNINGS", source)
        self.assertIn("RECENT", source)
        self.assertIn("state_context_label", source)
        self.assertIn("card.pack_start(actions", source)
        self.assertNotIn("expanded_section.pack_start(actions", source)


    def test_operation_steps_show_apply_pipeline_and_live_phase(self) -> None:
        state = HudState(status="running", command="apply-overlay", phase_name="build", current=3, total=10)
        events = [
            {"event_type": "phase-finished", "phase": {"name": "apply-overlay"}, "result": {"status": "ok"}},
            {"event_type": "phase-finished", "phase": {"name": "restore"}, "result": {"status": "ok"}},
            {"event_type": "phase-started", "phase": {"name": "build"}, "result": {"status": "running"}},
        ]
        steps = operation_steps(state, events)
        self.assertEqual([step.key for step in steps], ["extract", "restore", "build", "test", "commit"])
        self.assertEqual([step.status for step in steps], ["ok", "ok", "running", "pending", "pending"])
        self.assertEqual(steps[2].progress, 30)

    def test_operation_steps_keep_live_progress_for_multiple_workspace_targets(self) -> None:
        from devtool.hud import state_context_label

        pipeline = [
            {"name": "restore[s3atelier]", "workspace": "s3atelier", "target": "s3atelier_avalonia_S3Atelier_Modern"},
            {"name": "build[s3atelier]", "workspace": "s3atelier", "target": "s3atelier_avalonia_S3Atelier_Modern"},
            {"name": "test[s3atelier]", "workspace": "s3atelier", "target": "s3atelier_avalonia_S3Atelier_Modern"},
            {"name": "restore[s3pi-base]", "workspace": "s3pi-base", "target": "s3pi_base_S3pi_Base"},
            {"name": "build[s3pi-base]", "workspace": "s3pi-base", "target": "s3pi_base_S3pi_Base"},
            {"name": "test[s3pi-base]", "workspace": "s3pi-base", "target": "s3pi_base_S3pi_Base"},
        ]
        state = HudState(
            status="running",
            command="apply-overlay",
            phase_name="build[s3atelier]",
            current=4,
            total=10,
            repo_name="sims3tools",
            raw={
                "pipeline": pipeline,
                "targets": ["s3atelier_avalonia_S3Atelier_Modern", "s3pi_base_S3pi_Base"],
                "phase": {"name": "build[s3atelier]", "workspace": "s3atelier", "target": "s3atelier_avalonia_S3Atelier_Modern"},
            },
        )
        events = [
            {"event_type": "phase-finished", "phase": {"name": "apply-overlay"}, "result": {"status": "ok"}},
            {"event_type": "phase-finished", "phase": {"name": "restore[s3atelier]", "workspace": "s3atelier"}, "result": {"status": "ok"}},
            {"event_type": "phase-started", "phase": {"name": "build[s3atelier]", "workspace": "s3atelier"}},
            {"event_type": "progress", "phase": {"name": "build[s3atelier]", "workspace": "s3atelier"}, "current": 4, "total": 10},
        ]

        steps = operation_steps(state, events)
        self.assertEqual(
            [step.key for step in steps],
            [
                "extract",
                "restore:s3atelier", "build:s3atelier", "test:s3atelier",
                "restore:s3pi-base", "build:s3pi-base", "test:s3pi-base",
                "commit",
            ],
        )
        build = next(step for step in steps if step.key == "build:s3atelier")
        self.assertEqual(build.status, "running")
        self.assertEqual(build.progress, 40)
        self.assertEqual(build.label, "Build · s3atelier")
        self.assertIn("2 targets", state_context_label(state))

    def test_operation_steps_use_authoritative_live_phase_snapshot_without_events(self) -> None:
        state = HudState(
            status="running",
            command="apply-overlay",
            phase_name="build[s3atelier]",
            repo_name="sims3tools",
            raw={
                "phase": {"name": "build[s3atelier]", "workspace": "s3atelier"},
                "pipeline": [
                    {"name": "restore[s3atelier]", "workspace": "s3atelier"},
                    {"name": "build[s3atelier]", "workspace": "s3atelier"},
                    {"name": "test[s3atelier]", "workspace": "s3atelier"},
                ],
                "phase_progress": [
                    {
                        "name": "restore[s3atelier]",
                        "workspace": "s3atelier",
                        "status": "ok",
                        "completed": 2,
                        "total": 2,
                        "percent": 100.0,
                    },
                    {
                        "name": "build[s3atelier]",
                        "workspace": "s3atelier",
                        "status": "running",
                        "completed": 3,
                        "total": 12,
                        "percent": 25.0,
                    },
                ],
            },
        )

        steps = operation_steps(state, [])
        restore = next(step for step in steps if step.key == "restore:s3atelier")
        build = next(step for step in steps if step.key == "build:s3atelier")
        self.assertEqual(restore.status, "ok")
        self.assertEqual(restore.progress, 100)
        self.assertEqual(build.status, "running")
        self.assertEqual(build.progress, 25)

    def test_operation_steps_add_external_validation_and_rollback(self) -> None:
        state = HudState(status="failed", command="apply-overlay", phase_name="apply-rollback")
        events = [
            {"event_type": "phase-finished", "phase": {"name": "artifact-validate-script"}, "result": {"status": "failed"}},
            {"event_type": "phase-finished", "phase": {"name": "apply-rollback"}, "result": {"status": "ok"}},
        ]
        steps = operation_steps(state, events)
        by_key = {step.key: step for step in steps}
        self.assertEqual(by_key["validate"].status, "failed")
        self.assertEqual(by_key["rollback"].status, "ok")

    def test_live_issue_counts_update_from_unique_diagnostics_and_totals(self) -> None:
        state = HudState(status="running")
        events = [
            {"event_type": "progress", "phase": {"name": "build"}, "message": "A.cs(1): warning CS1000: warning"},
            {"event_type": "progress", "phase": {"name": "build"}, "message": "A.cs(1): warning CS1000: warning"},
            {"event_type": "progress", "phase": {"name": "build"}, "message": "B.cs(1): error CS2000: error"},
            {"event_type": "progress", "phase": {"name": "build"}, "message": "2 Warning(s) 1 Error(s)"},
        ]
        self.assertEqual(live_issue_counts(events, state), (1, 2))

    def test_render_ascii_bar_clamps_progress(self) -> None:
        self.assertEqual(render_ascii_bar(5, 10, width=10), "█████░░░░░")
        self.assertEqual(render_ascii_bar(20, 10, width=10), "██████████")
        self.assertEqual(render_ascii_bar(None, None, width=4), "░░░░")

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

    def test_desktop_hud_starts_compact_by_default(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(desktop_hud_starts_expanded())

    def test_desktop_hud_can_start_expanded_by_environment(self) -> None:
        for value in ("1", "true", "yes", "on", "TRUE"):
            with self.subTest(value=value), patch.dict(
                "os.environ",
                {"DEVTOOL_DESKTOP_HUD_START_EXPANDED": value},
                clear=True,
            ):
                self.assertTrue(desktop_hud_starts_expanded())


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
        args = parser.parse_args(["hud", "--mode", "v5", "--host", "0.0.0.0", "--port", "9999", "--once"])
        self.assertEqual(args.command, "hud")
        self.assertEqual(args.mode, "v5")
        self.assertEqual(args.host, "0.0.0.0")
        self.assertEqual(args.port, 9999)

    def test_choose_desktop_aliases(self) -> None:
        from devtool.hud import choose_hud_mode
        with patch("devtool.hud.QtCompactHud.available", return_value=False), patch("devtool.hud.GtkDesktopHud.available", return_value=False), patch("devtool.hud.DesktopNotificationHud.available", return_value=True):
            self.assertEqual(choose_hud_mode("desktop"), "desktop-notify")
        with patch("devtool.hud.QtCompactHud.available", return_value=True), patch("devtool.hud.GtkDesktopHud.available", return_value=True), patch("devtool.hud.DesktopNotificationHud.available", return_value=True):
            self.assertEqual(choose_hud_mode("v6"), "desktop-window")
        with patch("devtool.hud.QtCompactHud.available", return_value=False), patch("devtool.hud.GtkDesktopHud.available", return_value=True), patch("devtool.hud.DesktopNotificationHud.available", return_value=True):
            self.assertEqual(choose_hud_mode("desktop"), "desktop-notify")
        self.assertEqual(choose_hud_mode("desktop-window"), "desktop-window")
        self.assertEqual(choose_hud_mode("gtk"), "gtk-window")

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


    def test_android_hud_modes_are_app_bridge_only(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["hud", "--mode", "android-gui", "--once"])
        self.assertEqual(args.mode, "android-gui")
        args = parser.parse_args(["hud", "--mode", "android-bridge", "--once"])
        self.assertEqual(args.mode, "android-bridge")
        with self.assertRaises(SystemExit):
            parser.parse_args(["hud", "--mode", "android-notify", "--once"])

    def test_android_hud_aliases_and_termux_auto(self) -> None:
        self.assertEqual(choose_hud_mode("termux-gui"), "android-bridge")
        self.assertEqual(choose_hud_mode("android-overlay"), "android-bridge")
        self.assertEqual(choose_hud_mode("android-gui"), "android-bridge")
        with patch("devtool.hud._is_termux_android", return_value=True), \
             patch("devtool.hud.GtkDesktopHud.available", return_value=True):
            self.assertEqual(choose_hud_mode("desktop"), "android-bridge")
            self.assertEqual(choose_hud_mode("desktop-window"), "android-bridge")
            self.assertEqual(choose_hud_mode("desktop-notify"), "android-bridge")
            self.assertEqual(choose_hud_mode("auto"), "android-bridge")

    def test_termux_gui_backend_class_is_removed(self) -> None:
        import devtool.hud as hud_module
        self.assertFalse(hasattr(hud_module, "TermuxGuiHud"))

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
             patch("devtool.hud.GtkDesktopHud.available", return_value=True):
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
        args = parser.parse_args(["android-hud-bridge", "--state-dir", "/tmp/hud", "--once"])
        self.assertEqual(args.command, "android-hud-bridge")
        self.assertEqual(args.state_dir, "/tmp/hud")
        with self.assertRaises(SystemExit):
            parser.parse_args(["android-hud-bridge", "--mode", "android-notify", "--once"])

class AndroidLegacyFlagTests(unittest.TestCase):
    def test_legacy_gui_flags_remain_hidden_compatibility_noops(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--hud-no-overlay", "build"])
        self.assertTrue(args.hud_no_overlay)
        hud_args = parser.parse_args(["hud", "--mode", "android-gui", "--no-overlay"])
        self.assertTrue(hud_args.no_overlay)
        self.assertEqual(choose_hud_mode(hud_args.mode), "android-bridge")



class AndroidOverlayPresentationTests(unittest.TestCase):
    def test_global_cli_accepts_explicit_overlay_modes(self) -> None:
        parser = build_parser()
        overlay_args = parser.parse_args(["--hud-overlay", "build"])
        self.assertTrue(overlay_args.hud_overlay)
        self.assertFalse(overlay_args.hud_no_overlay)
        no_overlay_args = parser.parse_args(["--hud-no-overlay", "build"])
        self.assertTrue(no_overlay_args.hud_no_overlay)
        with self.assertRaises(SystemExit):
            parser.parse_args(["--hud-overlay", "--hud-no-overlay", "build"])

    def test_native_bridge_payload_contains_overlay_request(self) -> None:
        hud = AndroidAppHud(overlay=True)
        state = HudState(
            title="Build", text="Compiling", current=2, total=4,
            status="running", phase_name="build", repo_name="demo",
            raw={"run_id": "overlay-run"},
        )
        action, payload = hud._payload(state, Path("/tmp/overlay-run"))
        self.assertEqual(action, "update")
        self.assertTrue(payload["overlay"])

class HudCurrentRunPreflightTests(unittest.TestCase):
    def test_prepare_hud_replaces_stale_global_state(self) -> None:
        from devtool.tasker import TaskerProgressSink
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "state.json").write_text(json.dumps({"run_id": "old", "status": "ok", "message": "old"}), encoding="utf-8")
            sink = TaskerProgressSink(
                directory=directory,
                command="apply-overlay",
                repo_root="/repo",
                repo_name="repo",
                run_id="current-run",
            )
            sink.prepare_hud()
            payload = json.loads((directory / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["run_id"], "current-run")
            self.assertEqual(payload["status"], "waiting")
            self.assertIn("Preparing", payload["message"])


def test_devtool_no_hud_skips_direct_hud_renderer(monkeypatch) -> None:
    import argparse
    from devtool.hud import run_hud

    monkeypatch.setenv("DEVTOOL_NO_HUD", "true")
    args = argparse.Namespace()
    assert run_hud(args) == 0


def test_android_payload_uses_overall_pipeline_progress_and_live_test_metrics() -> None:
    hud = AndroidAppHud()
    state = HudState(
        title="Test", text="2/3 test assemblies", current=2, total=3,
        status="running", phase_name="test", command="apply-overlay",
        repo_name="demo", target="demo_target",
        raw={
            "run_id": "live-run",
            "sequence": 17,
            "updated_at": "2026-07-13T00:00:00+00:00",
            "pipeline": [
                {"name": "extract"}, {"name": "restore", "target": "demo_target"},
                {"name": "build", "target": "demo_target"},
                {"name": "test", "target": "demo_target"}, {"name": "commit"},
            ],
            "phase": {"name": "test", "target": "demo_target"},
            "phase_progress": [
                {"name": "extract", "status": "ok", "percent": 100},
                {"name": "restore", "target": "demo_target", "status": "ok", "percent": 100},
                {"name": "build", "target": "demo_target", "status": "ok", "percent": 100},
                {"name": "test", "target": "demo_target", "status": "running", "percent": 66,
                 "tests_passed": 225, "tests_failed": 0, "tests_skipped": 1,
                 "tests_total": 226, "test_assemblies": 2},
                {"name": "commit", "status": "pending", "percent": 0},
            ],
        },
    )
    action, payload = hud._payload(state, Path("/tmp/live-run"))
    assert action == "update"
    assert payload["progress"] == 73
    assert payload["phase_progress"] == 67
    assert payload["passed"] == 225
    assert payload["skipped"] == 1
    assert payload["test_assemblies"] == 2
    assert payload["sequence"] == 17


def test_android_forwarder_retries_failed_broadcast() -> None:
    hud = AndroidAppHud()
    state = HudState(status="running", phase_name="build", repo_name="demo", raw={"run_id": "retry", "sequence": 1})
    calls = []
    def send(action, payload):
        calls.append((action, payload))
        return len(calls) > 1
    hud.bridge.send = send
    hud.update(state, directory=Path("/tmp/retry"))
    assert hud.last_signature is None
    hud.update(state, directory=Path("/tmp/retry"))
    assert hud.last_signature is not None
    assert len(calls) == 2



def test_state_reader_prefers_newer_sequence_over_higher_stale_phase_percentage(tmp_path: Path) -> None:
    import json

    # Old build phase is complete, but the newer test phase has restarted its
    # local counter at 1/3. The reader must follow sequence 12, not 100%.
    (tmp_path / "notification.json").write_text(json.dumps({
        "run_id": "phase-reset",
        "sequence": 11,
        "updated_at": "2026-07-13T12:00:00Z",
        "title": "Build complete",
        "text": "17/17 projects",
        "current": 17,
        "total": 17,
    }), encoding="utf-8")
    (tmp_path / "state.json").write_text(json.dumps({
        "run_id": "phase-reset",
        "sequence": 12,
        "updated_at": "2026-07-13T12:00:01Z",
        "status": "running",
        "command": "apply-overlay",
        "phase": {"name": "test", "index": 3, "total": 4},
        "message": "Running test assembly 1 of 3",
        "current": 1,
        "total": 3,
    }), encoding="utf-8")

    state = state_from_directory(tmp_path)
    assert state.phase_name == "test"
    assert state.current == 1
    assert state.total == 3
    assert state.seq == 12


def test_notification_snapshot_contains_authoritative_sequence(tmp_path: Path) -> None:
    import json
    from devtool.tasker import TaskerProgressSink

    sink = TaskerProgressSink(
        directory=tmp_path,
        command="build",
        repo_root="/repo",
        repo_name="repo",
        run_id="android-sequence",
    )
    sink.prepare_hud()
    sink.phase_started("build", index=1, total=1)
    sink.progress(completed=2, total=5, detail="built 2 projects", metrics={"unit_label": "projects"})

    notification = json.loads((tmp_path / "notification.json").read_text(encoding="utf-8"))
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert notification["run_id"] == "android-sequence"
    assert notification["sequence"] == state["sequence"]
    assert notification["updated_at"] == state["updated_at"]
