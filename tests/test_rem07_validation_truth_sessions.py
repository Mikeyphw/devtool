from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from devtool.models import CommandSpec, Issue, OperationResult, ProjectConfig
from devtool.test_selection import changed_paths, resolve_test_selection
from devtool.validation_control import require_revision, status_api_payload as control_status_payload
from devtool.validation_profiles import apply_profile
from devtool.validation_reports import load_validation_report, report_path, write_validation_report
from devtool.validation_sessions import (
    ValidationSessionError,
    append_event,
    create_session,
    input_fingerprints,
    load_session,
    phase_records,
    read_events,
    record_phase_result,
    required_checkpoint_satisfied,
    retry_failed,
    save_session,
    session_path,
    status_payload,
    update_phase_progress,
    write_control_request,
    validation_control_path,
    mark_phase_running,
    completion_result,
)


def make_project(root: Path) -> None:
    (root / "src" / "main").mkdir(parents=True)
    (root / "tests").mkdir(parents=True)
    (root / "src" / "main" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (root / "tests" / "test_app.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    (root / ".devtool.toml").write_text("[project]\nkind='python'\n", encoding="utf-8")


def config(root: Path, *, project_root: Path | None = None, runner_options: dict | None = None) -> ProjectConfig:
    return ProjectConfig(
        repo_root=root,
        project_root=str(project_root or root),
        kind="python",
        runner_options=runner_options or {},
    )


class ValidationTruthSessionTests(unittest.TestCase):
    def test_duplicate_validation_phase_keys_fail_closed(self) -> None:
        specs = [
            CommandSpec("a", ["true"], metadata={"validation_phase_key": "same", "validation_phase_name": "lint"}),
            CommandSpec("b", ["true"], metadata={"validation_phase_key": "same", "validation_phase_name": "unit-test"}),
        ]
        with self.assertRaisesRegex(ValidationSessionError, "Duplicate validation phase key"):
            phase_records(specs)

    def test_error_diagnostic_with_zero_exit_blocks_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_project(root)
            session = create_session(config(root), [CommandSpec("lint", ["true"], metadata={"validation_phase_name": "lint"})], persist=False)
            phase = mark_phase_running(session, session.phases[0].key)
            record_phase_result(
                session,
                phase.key,
                OperationResult("lint", ["true"], 0, 0.1, "lint reported an error", [Issue("error", "lint", "x.py", "E1", "broken")]),
            )
            self.assertEqual(session.status, "blocked")
            self.assertEqual(session.phases[0].status, "failed")
            self.assertEqual(session.phases[0].returncode, 1)

    def test_narrow_domain_success_does_not_refresh_unvalidated_fingerprints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_project(root)
            session = create_session(config(root), [CommandSpec("compile", ["true"], metadata={"validation_phase_name": "compile"})], persist=False)
            before_tests = session.fingerprints["tests"]
            phase = mark_phase_running(session, session.phases[0].key)
            (root / "tests" / "test_app.py").write_text("def test_changed(): assert True\n", encoding="utf-8")
            record_phase_result(session, phase.key, OperationResult("compile", ["true"], 0, 0.1, "ok", []))
            current = input_fingerprints(root)
            self.assertNotEqual(current["tests"], before_tests)
            self.assertEqual(session.fingerprints["tests"], before_tests)

    def test_retry_failed_resets_failed_key_not_every_same_named_phase(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_project(root)
            specs = [
                CommandSpec("unit-a", ["true"], metadata={"validation_phase_name": "unit-test"}),
                CommandSpec("unit-b", ["true"], metadata={"validation_phase_name": "unit-test"}),
            ]
            session = create_session(config(root), specs, persist=False)
            session.phases[0].status = "failed"
            session.phases[0].returncode = 1
            session.phases[1].status = "passed"
            session.phases[1].returncode = 0
            failed_key = session.phases[0].key
            kept_key = session.phases[1].key
            returned = retry_failed(session)
            self.assertEqual(returned, "unit-test")
            by_key = {phase.key: phase for phase in session.phases}
            self.assertEqual(by_key[failed_key].status, "pending")
            self.assertEqual(by_key[kept_key].status, "passed")

    def test_validation_required_string_false_is_optional(self) -> None:
        record = phase_records([
            CommandSpec("docs", ["true"], metadata={"validation_phase_name": "lint", "validation_required": "false"})
        ])[0]
        self.assertFalse(record.required)

    def test_workflow_profile_string_false_does_not_enable_skips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".devtool").mkdir()
            (root / ".devtool" / "validation.toml").write_text(
                "[profiles.demo]\nskip_build = \"false\"\nskip_test = \"true\"\n",
                encoding="utf-8",
            )
            args = SimpleNamespace(validation_workflow_profile="demo", skip_restore=False, skip_build=False, skip_test=False, validation_resource_profile="automatic")
            apply_profile(config(root), args)
            self.assertFalse(args.skip_build)
            self.assertTrue(args.skip_test)

    def test_input_fingerprints_ignore_symlink_resolving_outside_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root = Path(tmp)
            make_project(root)
            external = Path(outside) / "external.py"
            external.write_text("print('outside')\n", encoding="utf-8")
            os.symlink(external, root / "outside_link.py")
            fingerprints = input_fingerprints(root)
            self.assertIn("source", fingerprints)

    def test_stale_invalid_sealed_report_is_regenerated_for_current_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_project(root)
            session = create_session(config(root), [CommandSpec("lint", ["true"], metadata={"validation_phase_name": "lint"})], persist=False)
            path = report_path(root, session.session_id, sealed=True)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"session_id": session.session_id, "sealed": True, "session_state": "stale", "report_sha256": "0" * 64}), encoding="utf-8")
            session.status = "completed"
            session.finalized_at = "2030-01-01T00:00:00Z"
            second = write_validation_report(session, sealed=True)
            second_payload = json.loads(second.read_text(encoding="utf-8"))
            self.assertEqual(path, second)
            self.assertEqual(second_payload["session_state"], "completed")
            self.assertNotEqual(second_payload["report_sha256"], "0" * 64)

    def test_malformed_session_schema_and_non_object_state_raise_session_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = session_path(root, "bad")
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"schema_version": "not-int"}), encoding="utf-8")
            with self.assertRaisesRegex(ValidationSessionError, "Unsupported validation session schema"):
                load_session(root, "bad")
            path.write_text(json.dumps(["not", "object"]), encoding="utf-8")
            with self.assertRaisesRegex(ValidationSessionError, "expected JSON object"):
                load_session(root, "bad")

    def test_malformed_pending_control_request_does_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_project(root)
            session = create_session(config(root), [CommandSpec("lint", ["true"], metadata={"validation_phase_name": "lint"})])
            control = validation_control_path(root, session.session_id)
            control.write_text(json.dumps({"session_id": session.session_id, "action": "stop-now", "expected_revision": {"bad": True}}), encoding="utf-8")
            with self.assertRaisesRegex(Exception, "pending|unavailable"):
                write_control_request(session, "pause-after-phase", expected_revision=session.revision)

    def test_invalid_control_revision_inputs_are_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_project(root)
            session = create_session(config(root), [CommandSpec("lint", ["true"], metadata={"validation_phase_name": "lint"})], persist=False)
            session.revision = "bad"  # simulate corrupted but in-memory API input
            self.assertEqual(require_revision(session, "also-bad"), 0)

    def test_malformed_event_limit_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_project(root)
            session = create_session(config(root), [CommandSpec("lint", ["true"], metadata={"validation_phase_name": "lint"})])
            marker = append_event(session, "demo", data={"ok": True})
            events = read_events(session, limit="not-int")
            self.assertGreaterEqual(len(events), 1)
            self.assertLessEqual(len(events), 1000)
            self.assertEqual(events[-1]["event_id"], marker["event_id"])
            payload = control_status_payload(session, include_events=True, event_limit="not-int")
            self.assertGreaterEqual(payload["event_cursor"]["returned"], 1)

    def test_events_and_session_context_are_json_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_project(root)
            session = create_session(
                config(root),
                [CommandSpec("lint", ["true"], metadata={"validation_phase_name": "lint"})],
                apply_context={"path": root, "values": {"a", "b"}},
            )
            event = append_event(session, "json-safe", data={"path": root, "items": {"x", "y"}, "nan": math.nan})
            self.assertIsNone(event["data"]["nan"])
            loaded = load_session(root, session.session_id)
            self.assertIsInstance(loaded.apply_context["path"], str)
            self.assertIsInstance(loaded.apply_context["values"], list)

    def test_non_finite_progress_updates_are_ignored_not_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_project(root)
            session = create_session(config(root), [CommandSpec("lint", ["true"], metadata={"validation_phase_name": "lint"})], persist=False)
            phase = mark_phase_running(session, session.phases[0].key)
            self.assertFalse(update_phase_progress(session, phase.key, {"percent": float("nan")}))
            self.assertFalse(update_phase_progress(session, phase.key, {"completed": float("inf"), "total": 10}))
            self.assertEqual(phase.progress_percent, 0)

    def test_skipped_required_checkpoint_does_not_satisfy_completion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_project(root)
            session = create_session(config(root), [CommandSpec("final", ["true"], metadata={"validation_phase_name": "finalize"})], persist=False)
            session.phases[0].status = "skipped"
            self.assertFalse(required_checkpoint_satisfied(session))
            with self.assertRaisesRegex(ValidationSessionError, "cannot complete"):
                completion_result(session)

    def test_legacy_rollback_false_string_keeps_pause_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_project(root)
            session = create_session(
                config(root),
                [CommandSpec("lint", ["true"], metadata={"validation_phase_name": "lint"})],
                apply_context={"legacy_rollback_on_failure_explicit": True, "rollback_on_failure": "false"},
                persist=False,
            )
            self.assertEqual(session.failure_action, "pause")

    def test_repository_identity_is_stable_across_reader_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_project(root)
            session = create_session(config(root), [CommandSpec("lint", ["true"], metadata={"validation_phase_name": "lint"})], persist=False)
            session.options = {"execution_environment": "native-termux", "linux_user": "u1"}
            first = status_payload(session)["repository_id"]
            session.options = {"execution_environment": "chroot", "chroot_name": "debian", "linux_user": "root"}
            second = status_payload(session)["repository_id"]
            self.assertEqual(first, second)

    def test_changed_paths_are_project_root_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / "apps" / "app1"
            other = root / "apps" / "app2"
            (app / "tests").mkdir(parents=True)
            (other / "tests").mkdir(parents=True)
            subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.PIPE)
            subprocess.run(["git", "config", "user.email", "a@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "A"], cwd=root, check=True)
            (app / "tests" / "test_app.py").write_text("def test_a(): assert True\n", encoding="utf-8")
            (other / "tests" / "test_other.py").write_text("def test_b(): assert True\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "base"], cwd=root, check=True, stdout=subprocess.PIPE)
            (app / "tests" / "test_app.py").write_text("def test_a2(): assert True\n", encoding="utf-8")
            (other / "tests" / "test_other.py").write_text("def test_b2(): assert True\n", encoding="utf-8")
            selected = changed_paths(root, "HEAD", project_root=app)
            self.assertEqual(selected, ["tests/test_app.py"])
            resolved = resolve_test_selection(config(root, project_root=app, runner_options={"test_selection": {"changed": True, "changed_from": "HEAD"}}))
            self.assertEqual(resolved.paths, ["tests/test_app.py"])


if __name__ == "__main__":
    unittest.main()
