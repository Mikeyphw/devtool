from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from devtool.cli import main
from devtool.models import CommandSpec, OperationResult, ProjectConfig
from devtool.validation_sessions import (
    create_session,
    heartbeat_phase,
    load_active_session,
    load_session,
    mark_phase_running,
    read_control_request,
    read_events,
    save_session,
    session_path,
    select_phase_keys,
)


def make_android_project(root: Path) -> None:
    (root / "app" / "src" / "main" / "kotlin").mkdir(parents=True)
    (root / "app" / "src" / "test" / "kotlin").mkdir(parents=True)
    (root / "settings.gradle.kts").write_text('rootProject.name="Demo"\ninclude(":app")\n', encoding="utf-8")
    (root / "build.gradle.kts").write_text("plugins {}\n", encoding="utf-8")
    (root / "app" / "build.gradle.kts").write_text("plugins {}\n", encoding="utf-8")
    (root / "app" / "src" / "main" / "kotlin" / "Main.kt").write_text("class Main\n", encoding="utf-8")
    (root / "app" / "src" / "test" / "kotlin" / "MainTest.kt").write_text("class MainTest\n", encoding="utf-8")
    (root / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")
    (root / "gradlew").chmod(0o755)
    (root / ".devtool.toml").write_text('[project]\nkind="android"\nproject="."\n', encoding="utf-8")


def config(root: Path) -> ProjectConfig:
    return ProjectConfig(repo_root=root, project_root=str(root), kind="android", runner_options={"validation": {"resumable": True}})


def specs() -> list[CommandSpec]:
    return [
        CommandSpec("preflight", ["true"], metadata={"validation_phase_name": "preflight"}),
        CommandSpec("compile", ["true"], metadata={"validation_phase_name": "compile"}),
        CommandSpec("unit-test", ["true"], metadata={"validation_phase_name": "unit-test"}),
        CommandSpec("lint", ["true"], metadata={"validation_phase_name": "lint"}),
    ]


class ValidationControlApiTests(unittest.TestCase):

    def test_schema_v3_session_migrates_to_control_schema_v4(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            make_android_project(root)
            session = create_session(config(root), specs())
            path = session_path(root, session.session_id)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["schema_version"] = 3
            path.write_text(json.dumps(payload), encoding="utf-8")

            migrated = load_session(root, session.session_id)
            self.assertEqual(migrated.session_id, session.session_id)
            self.assertEqual(migrated.revision, session.revision)
            self.assertEqual([phase.name for phase in migrated.phases], ["preflight", "compile", "unit-test", "lint"])
            self.assertEqual(migrated.status, session.status)

    def test_status_and_plan_are_stable_json_envelopes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            make_android_project(root)
            session = create_session(config(root), specs())
            env = {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}

            stdout = io.StringIO()
            with patch.dict("os.environ", env, clear=False), redirect_stdout(stdout):
                rc = main(["--repo", str(root), "--no-clipboard", "validate", "status", "--json"])
            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(payload["api_version"], 1)
            self.assertTrue(payload["accepted"])
            self.assertEqual(payload["session_id"], session.session_id)
            self.assertEqual(payload["data"]["revision"], session.revision)

            stdout = io.StringIO()
            with patch.dict("os.environ", env, clear=False), redirect_stdout(stdout):
                rc = main(["--repo", str(root), "--no-clipboard", "validate", "plan", "--json"])
            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(payload["action"], "plan")
            self.assertEqual(payload["data"]["supported_scopes"], ["next", "through", "selected", "remaining"])
            self.assertEqual([item["name"] for item in payload["data"]["phases"]], ["preflight", "compile", "unit-test", "lint"])

    def test_stale_revision_is_exit_three_and_does_not_write_control(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            make_android_project(root)
            session = create_session(config(root), specs())
            env = {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}
            stdout = io.StringIO()
            with patch.dict("os.environ", env, clear=False), redirect_stdout(stdout):
                rc = main([
                    "--repo", str(root), "--no-clipboard", "validate", "pause",
                    "--if-revision", str(session.revision - 1), "--json",
                ])
            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 3)
            self.assertFalse(payload["accepted"])
            self.assertEqual(payload["reason"], "stale_revision")
            self.assertEqual(read_control_request(session), {})

    def test_pause_and_stop_publish_control_without_mutating_live_revision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            make_android_project(root)
            session = create_session(config(root), specs())
            phase = mark_phase_running(session, session.phases[0].key)
            running_revision = session.revision
            heartbeat_phase(session, phase.key, persist_interval_seconds=0)
            self.assertEqual(session.revision, running_revision)
            env = {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}

            stdout = io.StringIO()
            with patch.dict("os.environ", env, clear=False), redirect_stdout(stdout):
                rc = main([
                    "--repo", str(root), "--no-clipboard", "validate", "pause",
                    "--if-revision", str(running_revision), "--json",
                ])
            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(payload["effective_state"], "pausing")
            self.assertGreater(payload["revision"], running_revision)
            pause_revision = payload["revision"]
            self.assertEqual(read_control_request(session)["action"], "pause-after-phase")

            # A second, conflicting control request is unavailable until the
            # validation worker consumes the first request.
            stdout = io.StringIO()
            with patch.dict("os.environ", env, clear=False), redirect_stdout(stdout):
                rc = main([
                    "--repo", str(root), "--no-clipboard", "validate", "stop",
                    "--if-revision", str(pause_revision), "--json",
                ])
            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 6)
            self.assertEqual(payload["reason"], "control_unavailable")
            self.assertEqual(read_control_request(session)["action"], "pause-after-phase")


    def test_pause_after_phase_request_is_persisted_for_the_live_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            make_android_project(root)
            session = create_session(config(root), specs())
            phase = mark_phase_running(session, session.phases[0].key)
            revision = session.revision
            heartbeat_phase(session, phase.key, persist_interval_seconds=0)
            env = {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}
            stdout = io.StringIO()
            with patch.dict("os.environ", env, clear=False), redirect_stdout(stdout):
                rc = main([
                    "--repo", str(root), "--no-clipboard", "validate", "pause",
                    "--if-revision", str(revision), "--json",
                ])
            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(payload["effective_state"], "pausing")
            self.assertEqual(read_control_request(session)["action"], "pause-after-phase")

    def test_stop_now_request_is_persisted_for_the_live_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            make_android_project(root)
            session = create_session(config(root), specs())
            phase = mark_phase_running(session, session.phases[0].key)
            revision = session.revision
            heartbeat_phase(session, phase.key, persist_interval_seconds=0)
            env = {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}
            stdout = io.StringIO()
            with patch.dict("os.environ", env, clear=False), redirect_stdout(stdout):
                rc = main([
                    "--repo", str(root), "--no-clipboard", "validate", "stop",
                    "--if-revision", str(revision), "--json",
                ])
            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(payload["effective_state"], "stopping")
            self.assertEqual(read_control_request(session)["action"], "stop-now")

    def test_stop_without_running_phase_is_control_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            make_android_project(root)
            session = create_session(config(root), specs())
            env = {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}
            stdout = io.StringIO()
            with patch.dict("os.environ", env, clear=False), redirect_stdout(stdout):
                rc = main([
                    "--repo", str(root), "--no-clipboard", "validate", "stop",
                    "--if-revision", str(session.revision), "--json",
                ])
            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 6)
            self.assertFalse(payload["accepted"])
            self.assertEqual(payload["reason"], "control_unavailable")
            self.assertEqual(load_active_session(root, required=True).revision, session.revision)

    def test_machine_file_does_not_contaminate_json_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            make_android_project(root)
            create_session(config(root), specs())
            output = Path(tmp) / "status.json"
            env = {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}
            stdout = io.StringIO()
            with patch.dict("os.environ", env, clear=False), redirect_stdout(stdout):
                rc = main([
                    "--repo", str(root), "--no-clipboard", "validate", "status",
                    "--json", "--machine-file", str(output),
                ])
            self.assertEqual(rc, 0)
            stdout_payload = json.loads(stdout.getvalue())
            file_payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(stdout_payload, file_payload)
            self.assertNotIn("Wrote JSON payload", stdout.getvalue())

    def test_selected_scope_includes_dependency_closure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            session = create_session(config(root), specs())
            keys = select_phase_keys(session, selected_phases=["unit-test", "lint"])
            names = [session.phase_by_key(key).name for key in keys]
            self.assertEqual(names, ["preflight", "compile", "unit-test", "lint"])

    def test_events_are_append_only_and_cursor_readable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            session = create_session(config(root), specs())
            phase = mark_phase_running(session, session.phases[0].key)
            events = read_events(session)
            self.assertEqual(events[0]["type"], "session-created")
            self.assertEqual(events[-1]["type"], "phase-started")
            cursor = events[0]["event_id"]
            after = read_events(session, after_event_id=cursor)
            self.assertEqual([item["type"] for item in after], ["phase-started"])
            self.assertEqual(after[0]["data"]["attempt_id"], phase.current_attempt_id)

    def test_blocked_status_uses_exit_four(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            make_android_project(root)
            session = create_session(config(root), specs())
            session.phases[0].status = "failed"
            session.phases[0].last_verdict = "test-failed"
            session.status = "blocked"
            save_session(session, active=True)
            env = {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}
            stdout = io.StringIO()
            with patch.dict("os.environ", env, clear=False), redirect_stdout(stdout):
                rc = main(["--repo", str(root), "--no-clipboard", "validate", "status", "--json"])
            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 4)
            self.assertEqual(payload["state"], "blocked")

    def test_run_scope_next_emits_control_response(self) -> None:
        def fake_run(_config, run_specs, _args, **_kwargs):
            results = [
                OperationResult(spec.name, list(spec.argv), 0, 0.01, "ok", [], metadata=dict(spec.metadata))
                for spec in run_specs
            ]
            return results, "ok"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            make_android_project(root)
            env = {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}
            with patch.dict("os.environ", env, clear=False), patch("devtool.cli._run_specs_for_args", side_effect=fake_run), patch("devtool.cli.render_summary"):
                self.assertEqual(main(["--repo", str(root), "--no-clipboard", "validate", "start", "--next"]), 0)
            session = load_active_session(root, required=True)
            stdout = io.StringIO()
            with patch.dict("os.environ", env, clear=False), patch("devtool.cli._run_specs_for_args", side_effect=fake_run), redirect_stdout(stdout):
                rc = main([
                    "--repo", str(root), "--no-clipboard", "validate", "run",
                    "--scope", "next", "--if-revision", str(session.revision), "--json",
                ])
            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertTrue(payload["accepted"])
            self.assertEqual(payload["action"], "run")
            self.assertEqual(payload["data"]["scope"], "next")
            self.assertGreater(payload["revision"], session.revision)


    def test_pending_pause_can_be_cancelled_with_same_revision_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            make_android_project(root)
            session = create_session(config(root), specs())
            mark_phase_running(session, session.phases[0].key)
            running = load_active_session(root, required=True)
            env = {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}

            stdout = io.StringIO()
            with patch.dict("os.environ", env, clear=False), redirect_stdout(stdout):
                rc = main([
                    "--repo", str(root), "--no-clipboard", "validate", "pause",
                    "--if-revision", str(running.revision), "--json",
                ])
            pause_payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(pause_payload["effective_state"], "pausing")
            self.assertEqual(read_control_request(running)["action"], "pause-after-phase")

            stdout = io.StringIO()
            with patch.dict("os.environ", env, clear=False), redirect_stdout(stdout):
                rc = main([
                    "--repo", str(root), "--no-clipboard", "validate", "pause", "--cancel",
                    "--if-revision", str(pause_payload["revision"]), "--json",
                ])
            cancel_payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertTrue(cancel_payload["accepted"])
            self.assertIn("keep running", cancel_payload["message"])
            latest = load_active_session(root, required=True)
            self.assertEqual(read_control_request(latest), {})
            self.assertGreater(cancel_payload["revision"], pause_payload["revision"])



if __name__ == "__main__":
    unittest.main()
