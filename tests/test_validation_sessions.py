from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile

from devtool.android_integration import android_validation_change_scope
from devtool.apply_notifications import build_apply_notification
from devtool.cli import _run_validation_session, build_parser, main
from devtool.models import CommandSpec, OperationResult, ProjectConfig
from devtool.project_runners import AndroidGradleRunner
from devtool.transactions import (
    create_apply_transaction,
    mark_transaction_pending_validation,
    mark_transaction_validation_running,
    read_transaction,
    transaction_pending_validation,
)
from devtool.validation_sessions import (
    ValidationSessionError,
    active_session_marker,
    create_session,
    load_active_session,
    load_session,
    reconcile_inputs,
    record_phase_result,
    recover_interrupted_phases,
    resolve_resumable_session,
    mark_phase_running,
    retry_failed,
    select_phase_keys,
    checkpoint_text,
    save_session,
    status_payload,
    sync_plan,
    validation_plan_path,
)


def make_android_project(root: Path) -> None:
    (root / "app" / "src" / "main" / "kotlin").mkdir(parents=True)
    (root / "app" / "src" / "main" / "res" / "layout").mkdir(parents=True)
    (root / "app" / "src" / "test" / "kotlin").mkdir(parents=True)
    (root / "app" / "src" / "main" / "AndroidManifest.xml").write_text("<manifest package='demo'/>", encoding="utf-8")
    (root / "app" / "src" / "main" / "kotlin" / "Main.kt").write_text("class Main\n", encoding="utf-8")
    (root / "app" / "src" / "main" / "res" / "layout" / "main.xml").write_text("<FrameLayout/>", encoding="utf-8")
    (root / "app" / "src" / "test" / "kotlin" / "MainTest.kt").write_text("class MainTest\n", encoding="utf-8")
    (root / "settings.gradle.kts").write_text('rootProject.name="Demo"\ninclude(":app")\n', encoding="utf-8")
    (root / "build.gradle.kts").write_text('plugins { id("com.android.application") version "8.8.0" apply false }\n', encoding="utf-8")
    (root / "app" / "build.gradle.kts").write_text('plugins { id("com.android.application") }\nandroid { compileSdk=35 }\n', encoding="utf-8")
    (root / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")
    (root / "gradlew").chmod(0o755)


def validation_config(root: Path, changed: list[str] | None = None) -> ProjectConfig:
    options: dict[str, object] = {"resumable": True}
    if changed is not None:
        options["changed_paths"] = changed
    return ProjectConfig(
        repo_root=root,
        project_root=str(root),
        kind="android",
        runner_options={"validation": options},
    )


class ResumableValidationTests(unittest.TestCase):

    def test_plan_sync_discards_legacy_artifact_check_without_replaying_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = validation_config(root)
            old_specs = [
                CommandSpec("package", ["gradle", "assembleDebug"], metadata={"validation_phase_name": "package"}),
                CommandSpec("artifact-check", ["python", "check.py"], metadata={"validation_phase_name": "artifact-check"}),
                CommandSpec("lint", ["gradle", "lintDebug"], metadata={"validation_phase_name": "lint"}),
            ]
            session = create_session(config, old_specs)
            session.phases[0].status = "passed"
            session.phases[0].returncode = 0
            session.phases[1].status = "failed"
            session.phases[1].returncode = 127
            session.status = "failed"
            new_specs = [
                CommandSpec("package", ["gradle", "assembleDebug"], metadata={"validation_phase_name": "package"}),
                CommandSpec("lint", ["gradle", "lintDebug"], metadata={"validation_phase_name": "lint"}),
            ]
            sync_plan(session, new_specs)
            self.assertEqual([phase.name for phase in session.phases], ["package", "lint"])
            self.assertEqual(session.phases[0].status, "passed")
            self.assertEqual(session.phases[1].status, "pending")
            self.assertEqual(session.next_pending().name, "lint")

    def test_plan_sync_tail_addition_preserves_completed_phases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = validation_config(root)
            base = [
                CommandSpec("compile", ["gradle", "compile"], metadata={"validation_phase_name": "compile"}),
                CommandSpec("unit-test", ["gradle", "test"], metadata={"validation_phase_name": "unit-test"}),
            ]
            session = create_session(config, base)
            for phase in session.phases:
                phase.status = "passed"
                phase.returncode = 0
            sync_plan(session, [*base, CommandSpec("lint", ["gradle", "lint"], metadata={"validation_phase_name": "lint"})])
            self.assertEqual([phase.status for phase in session.phases], ["passed", "passed", "pending"])

    def test_plan_sync_command_change_invalidates_changed_phase_and_downstream_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = validation_config(root)
            base = [
                CommandSpec("compile", ["gradle", "compile"], metadata={"validation_phase_name": "compile"}),
                CommandSpec("unit-test", ["gradle", "testDebug"], metadata={"validation_phase_name": "unit-test"}),
                CommandSpec("lint", ["gradle", "lint"], metadata={"validation_phase_name": "lint"}),
            ]
            session = create_session(config, base)
            for phase in session.phases:
                phase.status = "passed"
                phase.returncode = 0
            changed = [
                base[0],
                CommandSpec("unit-test", ["gradle", "testRelease"], metadata={"validation_phase_name": "unit-test"}),
                base[2],
            ]
            sync_plan(session, changed)
            self.assertEqual([phase.status for phase in session.phases], ["passed", "pending", "passed"])
            self.assertEqual(session.invalidated_from, "unit-test")

    def test_android_resumable_validation_has_no_artifact_phase_or_artifact_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = validation_config(root)
            specs = AndroidGradleRunner().build_validation_specs(config)
            phases = [str(spec.metadata.get("validation_phase_name") or "") for spec in specs]
            self.assertNotIn("artifact-check", phases)
            for spec in specs:
                self.assertFalse(spec.metadata.get("collect_artifacts"))
                self.assertFalse(spec.metadata.get("verify_artifacts"))
                self.assertNotIn("--scan-artifacts", spec.argv)

    def test_parser_exposes_complete_sequential_controls(self) -> None:
        args = build_parser().parse_args([
            "validate", "--sequential", "--through", "package", "--max-runtime", "15m",
            "--stop-below-battery", "25", "--require-charging", "--stop-above-temperature", "44",
            "--stop-gradle-after-phase", "--retry-failed",
        ])
        self.assertTrue(args.sequential)
        self.assertEqual(args.through, "package")
        self.assertEqual(args.max_runtime, "15m")
        self.assertEqual(args.stop_below_battery, 25)
        self.assertTrue(args.require_charging)
        self.assertEqual(args.stop_above_temperature, 44.0)
        self.assertTrue(args.stop_gradle_after_phase)
        self.assertTrue(args.retry_failed)

    def test_validate_nested_actions_are_supported(self) -> None:
        parser = build_parser()
        for action in ("start", "next", "resume", "continue", "status", "checkpoint", "finalize", "abort", "recover"):
            args = parser.parse_args(["validate", action])
            self.assertEqual(args.validate_scope, action)

    def test_parser_exposes_deferred_validation_and_low_power_profile(self) -> None:
        args = build_parser().parse_args([
            "apply-overlay", "--defer-validate", "--profile", "android-low-power", "demo.zip",
        ])
        self.assertTrue(args.defer_validate)
        self.assertEqual(args.validation_resource_profile, "android-low-power")

    def test_named_phase_includes_pending_prerequisites_and_phase_only_does_not(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = validation_config(root)
            specs = [
                CommandSpec("preflight", ["true"], metadata={"validation_phase_name": "preflight"}),
                CommandSpec("dependencies", ["true"], metadata={"validation_phase_name": "dependencies"}),
                CommandSpec("compile", ["true"], metadata={"validation_phase_name": "compile"}),
            ]
            session = create_session(config, specs)
            self.assertEqual(len(select_phase_keys(session, phase="compile")), 3)
            with self.assertRaisesRegex(Exception, "unsatisfied dependencies"):
                select_phase_keys(session, phase="compile", exact_phase=True)

    def test_checkpoint_text_reports_recovery_and_safe_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = validation_config(root)
            session = create_session(
                config,
                [CommandSpec("compile", ["true"], metadata={"validation_phase_name": "compile"})],
                transaction_path="/tmp/devtool-transaction.json",
                apply_context={"validation_mode": "deferred", "artifact": "patch.zip"},
                options={"validation_resource_profile": "android-low-power"},
            )
            text = checkpoint_text(session)
            self.assertIn("Recovery available: yes", text)
            self.assertIn("Safe checkpoint: yes", text)

    def test_cli_runs_one_durable_phase_per_invocation_and_resumes(self) -> None:
        def fake_run(_config, specs, _args, **_kwargs):
            results = [
                OperationResult(spec.name, list(spec.argv), 0, 0.01, "ok", [], metadata=dict(spec.metadata))
                for spec in specs
            ]
            return results, "\n".join(result.name for result in results)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            make_android_project(root)
            (root / ".devtool.toml").write_text('[project]\nkind="android"\nproject="."\n', encoding="utf-8")
            env = {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}
            with patch.dict("os.environ", env, clear=False), patch("devtool.cli.render_summary"), patch("devtool.cli._run_specs_for_args", side_effect=fake_run):
                first_rc = main(["--repo", str(root), "--no-clipboard", "validate", "start", "--next"])
            active = load_active_session(root, required=True)
            self.assertEqual(first_rc, 0)
            self.assertEqual(active.phases[0].status, "passed")
            self.assertEqual(active.phases[1].status, "pending")
            with patch.dict("os.environ", env, clear=False), patch("devtool.cli.render_summary"), patch("devtool.cli._run_specs_for_args", side_effect=fake_run):
                second_rc = main(["--repo", str(root), "--no-clipboard", "validate", "resume", "--next"])
            resumed = load_active_session(root, required=True)
            self.assertEqual(second_rc, 0)
            self.assertEqual(resumed.session_id, active.session_id)
            self.assertEqual(resumed.phases[1].status, "passed")


    def test_resume_uses_stored_plan_and_does_not_replay_passed_phase_after_config_edit(self) -> None:
        def fake_run(_config, specs, _args, **_kwargs):
            results = [
                OperationResult(spec.name, list(spec.argv), 0, 0.01, "ok", [], metadata=dict(spec.metadata))
                for spec in specs
            ]
            return results, "\n".join(result.name for result in results)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            make_android_project(root)
            config_path = root / ".devtool.toml"
            config_path.write_text('[project]\nkind="android"\nproject="."\n', encoding="utf-8")
            env = {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}
            with patch.dict("os.environ", env, clear=False), patch("devtool.cli.render_summary"), patch(
                "devtool.cli._run_specs_for_args", side_effect=fake_run
            ):
                self.assertEqual(main(["--repo", str(root), "--no-clipboard", "validate", "start", "--next"]), 0)

            first = load_active_session(root, required=True)
            self.assertEqual(first.phases[0].status, "passed")
            self.assertEqual(first.phases[1].status, "pending")
            self.assertTrue(first.options.get("validation_spec_plan_v1"))

            # A session resume is intentionally pinned to the plan that started
            # it. Editing validation config must not force a fresh Android
            # inspection or replay an already-passed phase; restart-session is
            # the explicit way to adopt a new plan.
            config_path.write_text(
                '[project]\nkind="android"\nproject="."\n# changed while validation is paused\n',
                encoding="utf-8",
            )
            with patch.dict("os.environ", env, clear=False), patch("devtool.cli.render_summary"), patch(
                "devtool.cli._run_specs_for_args", side_effect=fake_run
            ), patch(
                "devtool.cli._validation_specs_for_args",
                side_effect=AssertionError("resume regenerated the Android validation plan"),
            ):
                self.assertEqual(main(["--repo", str(root), "--no-clipboard", "validate", "resume", "--next"]), 0)

            resumed = load_active_session(root, required=True)
            self.assertEqual(resumed.session_id, first.session_id)
            self.assertEqual(resumed.phases[0].status, "passed")
            self.assertEqual(resumed.phases[1].status, "passed")

    def test_plain_sequential_recovers_missing_active_marker_and_keeps_session_progress(self) -> None:
        def fake_run(_config, specs, _args, **_kwargs):
            results = [
                OperationResult(spec.name, list(spec.argv), 0, 0.01, "ok", [], metadata=dict(spec.metadata))
                for spec in specs
            ]
            return results, "\n".join(result.name for result in results)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            make_android_project(root)
            (root / ".devtool.toml").write_text('[project]\nkind="android"\nproject="."\n', encoding="utf-8")
            env = {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}
            with patch.dict("os.environ", env, clear=False), patch("devtool.cli.render_summary"), patch("devtool.cli._run_specs_for_args", side_effect=fake_run):
                self.assertEqual(main(["--repo", str(root), "--no-clipboard", "validate", "start", "--next"]), 0)

            before = load_active_session(root, required=True)
            self.assertEqual(before.phases[0].status, "passed")
            active_session_marker(root).unlink()

            with patch.dict("os.environ", env, clear=False), patch("devtool.cli.render_summary"), patch("devtool.cli._run_specs_for_args", side_effect=fake_run):
                self.assertEqual(main(["--repo", str(root), "--no-clipboard", "validate", "--sequential"]), 0)

            resumed = load_active_session(root, required=True)
            self.assertEqual(resumed.session_id, before.session_id)
            self.assertEqual(resumed.phases[0].status, "passed")
            self.assertEqual(resumed.phases[1].status, "passed")

    def test_orphaned_validation_plan_refuses_to_create_replacement_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = validation_config(root)
            session = create_session(
                config,
                [CommandSpec("preflight", ["true"], metadata={"validation_phase_name": "preflight"})],
            )
            active_session_marker(root).unlink()
            Path(root / ".devtool" / "state" / "android-validation" / session.session_id / "state.json").unlink()

            with self.assertRaisesRegex(ValidationSessionError, "No new validation session was created"):
                resolve_resumable_session(root)

    def test_multiple_unfinished_sessions_require_explicit_session_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = validation_config(root)
            specs = [CommandSpec("preflight", ["true"], metadata={"validation_phase_name": "preflight"})]
            first = create_session(config, specs)
            second = create_session(config, specs, replace_active=True)
            self.assertNotEqual(first.session_id, second.session_id)
            active_session_marker(root).unlink()
            validation_plan_path(root).unlink()

            with self.assertRaisesRegex(ValidationSessionError, "Multiple unfinished validation sessions"):
                resolve_resumable_session(root)

    def test_validation_plan_contains_recovery_identity_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = validation_config(root)
            config.selected_target = "mobile"
            config.variant = "debug"
            session = create_session(
                config,
                [CommandSpec("preflight", ["true"], metadata={"validation_phase_name": "preflight"})],
            )

            payload = json.loads(validation_plan_path(root).read_text(encoding="utf-8"))
            self.assertEqual(payload["session_id"], session.session_id)
            self.assertEqual(payload["repo_root"], str(root.resolve()))
            self.assertEqual(payload["project_root"], str(root.resolve()))
            self.assertEqual(payload["project_type"], "android")
            self.assertEqual(payload["target"], "mobile")
            self.assertEqual(payload["variant"], "debug")

    def test_omitted_target_inherits_active_validation_session_target(self) -> None:
        observed_targets: list[str] = []

        def fake_run(config, specs, _args, **_kwargs):
            observed_targets.append(config.selected_target)
            results = [
                OperationResult(spec.name, list(spec.argv), 0, 0.01, "ok", [], metadata=dict(spec.metadata))
                for spec in specs
            ]
            return results, "\n".join(result.name for result in results)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            make_android_project(root)
            (root / ".devtool.toml").write_text(
                "[project]\nkind='android'\ndefault_target='one'\n"
                "\n[targets.one]\ntype='android'\nroot='.'\n"
                "\n[targets.two]\ntype='android'\nroot='.'\n",
                encoding="utf-8",
            )
            env = {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}
            with patch.dict("os.environ", env, clear=False), patch("devtool.cli.render_summary"), patch("devtool.cli._run_specs_for_args", side_effect=fake_run):
                self.assertEqual(main(["--repo", str(root), "--target", "two", "--no-clipboard", "validate", "start", "--next"]), 0)
                self.assertEqual(main(["--repo", str(root), "--no-clipboard", "validate", "continue"]), 0)

            resumed = load_active_session(root, required=True)
            self.assertEqual(resumed.target, "two")
            self.assertEqual(observed_targets, ["two", "two"])

    def test_second_validation_process_refuses_live_phase_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = validation_config(root)
            specs = [CommandSpec("package", ["true"], metadata={"validation_phase_name": "package"})]
            session = create_session(config, specs)
            mark_phase_running(session, session.phases[0].key)
            parser = build_parser()
            args = parser.parse_args(["validate", "continue"])

            with self.assertRaisesRegex(ValidationSessionError, "already running.*phase 1 of 1"):
                _run_validation_session(
                    config,
                    args,
                    parser,
                    log_path=None,
                    tasker_progress=None,
                    continue_on_error=False,
                )

    def test_cli_recovers_stale_running_phase_and_retries_it_with_plain_continue(self) -> None:
        def fake_run(_config, specs, _args, **_kwargs):
            results = [
                OperationResult(spec.name, list(spec.argv), 0, 0.01, "ok", [], metadata=dict(spec.metadata))
                for spec in specs
            ]
            return results, "\n".join(result.name for result in results)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            make_android_project(root)
            (root / ".devtool.toml").write_text('[project]\nkind="android"\nproject="."\n', encoding="utf-8")
            env = {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}
            with patch.dict("os.environ", env, clear=False), patch("devtool.cli.render_summary"), patch("devtool.cli._run_specs_for_args", side_effect=fake_run):
                self.assertEqual(main(["--repo", str(root), "--no-clipboard", "validate", "start"]), 0)
            session = load_active_session(root, required=True)
            stale = next(phase for phase in session.phases if phase.status == "pending")
            mark_phase_running(session, stale.key)
            stale.owner_pid = 99999999
            stale.owner_process_start = "dead"
            stale.attempt_history[-1].owner_pid = 99999999
            stale.attempt_history[-1].owner_process_start = "dead"
            stale.heartbeat_at = "2000-01-01T00:00:00Z"
            stale.attempt_history[-1].heartbeat_at = stale.heartbeat_at
            save_session(session, active=True)

            with patch.dict("os.environ", env, clear=False), patch("devtool.cli.render_summary"), patch("devtool.cli._run_specs_for_args", side_effect=fake_run):
                rc = main(["--repo", str(root), "--no-clipboard", "validate", "continue"])

            resumed = load_active_session(root, required=True)
            recovered_phase = resumed.phase_by_key(stale.key)
            self.assertEqual(rc, 0)
            self.assertIsNotNone(recovered_phase)
            self.assertEqual(recovered_phase.status, "passed")
            self.assertEqual(recovered_phase.interruption_count, 1)
            self.assertEqual([attempt.verdict for attempt in recovered_phase.attempt_history[-2:]], ["interrupted", "passed"])

    def test_checkpoint_is_read_only_for_a_live_attached_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            make_android_project(root)
            changed = root / "app" / "src" / "main" / "kotlin" / "Main.kt"
            changed.write_text("class Before\n", encoding="utf-8")
            artifact = Path(tmp) / "overlay.zip"
            with ZipFile(artifact, "w") as archive:
                archive.writestr("app/src/main/kotlin/Main.kt", "class After\n")
            tx = create_apply_transaction(
                root,
                artifact,
                artifact_type="overlay",
                preview={"changes": [{"kind": "modify", "path": "app/src/main/kotlin/Main.kt"}]},
            )
            changed.write_text("class After\n", encoding="utf-8")
            config = validation_config(root)
            session = create_session(
                config,
                [CommandSpec("preflight", ["true"], metadata={"validation_phase_name": "preflight"})],
                transaction_path=tx["path"],
                apply_context={"validation_mode": "deferred", "artifact": str(artifact)},
            )
            mark_phase_running(session, session.phases[0].key)
            mark_transaction_validation_running(tx["path"], session_id=session.session_id, phase="preflight")
            before_revision = load_active_session(root, required=True).revision

            env = {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}
            with patch.dict("os.environ", env, clear=False), patch("devtool.cli.render_summary"):
                rc = main(["--repo", str(root), "--no-clipboard", "validate", "checkpoint"])

            observed = load_active_session(root, required=True)
            transaction = read_transaction(tx["path"])
            self.assertEqual(rc, 0)
            self.assertEqual(observed.phases[0].status, "running")
            self.assertEqual(observed.revision, before_revision)
            self.assertTrue(status_payload(observed)["live_attempts"][0]["owner_alive"])
            self.assertEqual(transaction["status"], "validation-running")
            self.assertTrue(Path(tx["path"]).exists())
            self.assertEqual(changed.read_text(encoding="utf-8"), "class After\n")

    def test_explicit_recover_reclaims_only_a_dead_phase_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            make_android_project(root)
            (root / ".devtool.toml").write_text('[project]\nkind="android"\nproject="."\n', encoding="utf-8")
            config = validation_config(root)
            specs = [CommandSpec("compile", ["true"], metadata={"validation_phase_name": "compile"})]
            session = create_session(config, specs)
            phase = mark_phase_running(session, session.phases[0].key)
            phase.owner_pid = 99999999
            phase.owner_process_start = "dead"
            phase.attempt_history[-1].owner_pid = 99999999
            phase.attempt_history[-1].owner_process_start = "dead"
            phase.heartbeat_at = "2000-01-01T00:00:00Z"
            phase.attempt_history[-1].heartbeat_at = phase.heartbeat_at
            save_session(session, active=True)

            env = {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}
            with patch.dict("os.environ", env, clear=False), patch("devtool.cli.render_summary"):
                rc = main(["--repo", str(root), "--no-clipboard", "validate", "recover"])

            recovered = load_active_session(root, required=True)
            self.assertEqual(rc, 0)
            self.assertEqual(recovered.phases[0].status, "interrupted")
            self.assertEqual(recovered.phases[0].interruption_count, 1)

    def test_explicit_recover_does_not_reclaim_a_live_phase_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            make_android_project(root)
            (root / ".devtool.toml").write_text('[project]\nkind="android"\nproject="."\n', encoding="utf-8")
            config = validation_config(root)
            session = create_session(
                config,
                [CommandSpec("compile", ["true"], metadata={"validation_phase_name": "compile"})],
            )
            mark_phase_running(session, session.phases[0].key)
            before = load_active_session(root, required=True)

            env = {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}
            with patch.dict("os.environ", env, clear=False), patch("devtool.cli.render_summary"):
                rc = main(["--repo", str(root), "--no-clipboard", "validate", "recover"])

            observed = load_active_session(root, required=True)
            self.assertEqual(rc, 0)
            self.assertEqual(observed.phases[0].status, "running")
            self.assertEqual(observed.revision, before.revision)

    def test_deferred_apply_preserves_transaction_and_runs_embedded_validator_as_static_phase(self) -> None:
        import hashlib
        import json
        import os
        import subprocess

        def digest(payload: bytes) -> str:
            return hashlib.sha256(payload).hexdigest()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            make_android_project(repo)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "devtool@example.invalid"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Devtool Tests"], cwd=repo, check=True)
            (repo / ".devtool.toml").write_text('[project]\nkind="android"\nproject="."\n', encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "Base"], cwd=repo, check=True)
            base_head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo, check=True, text=True, stdout=subprocess.PIPE
            ).stdout.strip()

            payload = b"class DeferredPatched\n"
            script = b'#!/usr/bin/env bash\nset -euo pipefail\nprintf "ok\n" > "$DEVTOOL_REPO_ROOT/static-ran.txt"\n'
            manifest = {
                "schema_version": 2,
                "type": "overlay",
                "repository": {"name": "repo"},
                "inventory": {"format": 1, "entries": [{
                    "path": "app/src/main/kotlin/Deferred.kt",
                    "action": "add",
                    "sha256": digest(payload),
                    "size": len(payload),
                }]},
                "validation": {
                    "mode": "required",
                    "allow_deferred": True,
                    "minimum_phase": "finalize",
                    "restore": False,
                    "build": False,
                    "test": False,
                    "rollback_on_failure": True,
                    "script": {
                        "path": ".devtool-artifact/validate.sh",
                        "runner": "bash",
                        "sha256": digest(script),
                        "timeout_seconds": 30,
                    },
                },
                "apply": {"commit": {"enabled": True, "message": "Apply deferred overlay"}},
            }
            artifact = root / "deferred.zip"
            with ZipFile(artifact, "w") as archive:
                archive.writestr(".devtool-artifact.json", json.dumps(manifest))
                archive.writestr(".devtool-artifact/validate.sh", script)
                archive.writestr("app/src/main/kotlin/Deferred.kt", payload)

            env = {"DEVTOOL_XDG_DIR": str(root / "xdg")}
            with patch.dict(os.environ, env, clear=False), patch("devtool.cli.render_summary"):
                apply_rc = main([
                    "--repo", str(repo), "--yes", "--no-clipboard",
                    "apply-overlay", "--defer-validate", "--profile", "android-low-power",
                    "--keep-artifact", str(artifact),
                ])
            self.assertEqual(apply_rc, 0)
            self.assertTrue((repo / "app/src/main/kotlin/Deferred.kt").exists())
            self.assertEqual(
                subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, text=True, stdout=subprocess.PIPE).stdout.strip(),
                base_head,
            )
            session = load_active_session(repo, required=True)
            self.assertEqual([phase.name for phase in session.phases], ["static"])
            self.assertEqual(session.options["validation_resource_profile"], "android-low-power")
            self.assertEqual(read_transaction(session.transaction_path)["status"], "applied-validation-deferred")

            with patch.dict(os.environ, env, clear=False), patch("devtool.cli.render_summary"):
                resume_rc = main([
                    "--repo", str(repo), "--no-clipboard",
                    "validate", "continue", "--phase", "static",
                ])
            self.assertEqual(resume_rc, 0)
            self.assertTrue((repo / "static-ran.txt").exists())
            self.assertIsNone(load_active_session(repo))
            self.assertFalse(Path(session.transaction_path).exists())
            self.assertTrue(artifact.exists())
            subject = subprocess.run(
                ["git", "log", "-1", "--pretty=%s"], cwd=repo, check=True, text=True, stdout=subprocess.PIPE
            ).stdout.strip()
            self.assertEqual(subject, "Apply deferred overlay")

    def test_changed_file_scope_selects_minimum_safe_phase_plan(self) -> None:
        expected = {
            ("README.md",): ("docs", ["finalize"]),
            ("app/src/main/kotlin/Main.kt",): ("source", ["compile", "unit-test", "package", "lint", "finalize"]),
            ("app/src/main/res/layout/main.xml",): ("resources", ["resources", "package", "lint", "finalize"]),
            ("app/src/test/kotlin/MainTest.kt",): ("tests", ["unit-test", "lint", "finalize"]),
            ("build.gradle.kts",): ("full", ["preflight", "dependencies", "compile", "resources", "unit-test", "package", "lint", "finalize"]),
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            for paths, (scope, phases) in expected.items():
                config = validation_config(root, list(paths))
                self.assertEqual(android_validation_change_scope(config), scope)
                specs = AndroidGradleRunner().build_validation_specs(config)
                self.assertEqual([spec.metadata["validation_phase_name"] for spec in specs], phases)
                self.assertTrue(all(spec.metadata["validation_change_scope"] == scope for spec in specs))

    def test_deferred_android_resume_rehydrates_preview_changed_paths_before_plan_sync(self) -> None:
        import os

        def fake_run(_config, specs, _args, **_kwargs):
            results = [
                OperationResult(spec.name, list(spec.argv), 0, 0.01, "ok", [], metadata=dict(spec.metadata))
                for spec in specs
            ]
            return results, "\n".join(result.name for result in results)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            make_android_project(root)
            (root / ".devtool.toml").write_text('[project]\nkind="android"\nproject="."\n', encoding="utf-8")
            preview = {"changes": [{"kind": "modify", "path": "app/src/main/kotlin/Main.kt"}]}
            scoped_config = validation_config(root, ["app/src/main/kotlin/Main.kt"])
            specs = AndroidGradleRunner().build_validation_specs(scoped_config)
            self.assertEqual(
                [str(spec.metadata.get("validation_phase_name") or "") for spec in specs],
                ["compile", "unit-test", "package", "lint", "finalize"],
            )
            create_session(
                scoped_config,
                specs,
                apply_context={
                    "validation_mode": "deferred",
                    "manifest": {},
                    "preview": preview,
                    "minimum_phase": "finalize",
                },
            )

            env = {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}
            with patch.dict("os.environ", env, clear=False), \
                    patch("devtool.cli.render_summary"), \
                    patch("devtool.cli._run_specs_for_args", side_effect=fake_run):
                rc = main(["--repo", str(root), "--no-clipboard", "validate", "continue"])

            self.assertEqual(rc, 0)
            resumed = load_active_session(root, required=True)
            self.assertNotEqual(resumed.plan_drift_status, "acceptance-required")
            self.assertEqual([phase.name for phase in resumed.phases], ["compile", "unit-test", "package", "lint", "finalize"])

    def test_sequential_android_resume_rehydrates_preview_changed_paths_before_plan_sync(self) -> None:
        def fake_run(_config, specs, _args, **_kwargs):
            results = [
                OperationResult(spec.name, list(spec.argv), 0, 0.01, "ok", [], metadata=dict(spec.metadata))
                for spec in specs
            ]
            return results, "\n".join(result.name for result in results)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            make_android_project(root)
            (root / ".devtool.toml").write_text('[project]\nkind="android"\nproject="."\n', encoding="utf-8")
            preview = {"changes": [{"kind": "modify", "path": "app/src/main/kotlin/Main.kt"}]}
            scoped_config = validation_config(root, ["app/src/main/kotlin/Main.kt"])
            specs = AndroidGradleRunner().build_validation_specs(scoped_config)
            create_session(
                scoped_config,
                specs,
                apply_context={
                    "validation_mode": "sequential",
                    "manifest": {},
                    "preview": preview,
                    "minimum_phase": "finalize",
                },
            )

            env = {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}
            with patch.dict("os.environ", env, clear=False), \
                    patch("devtool.cli.render_summary"), \
                    patch("devtool.cli._run_specs_for_args", side_effect=fake_run):
                rc = main(["--repo", str(root), "--no-clipboard", "validate", "continue"])

            self.assertEqual(rc, 0)
            resumed = load_active_session(root, required=True)
            self.assertEqual([phase.name for phase in resumed.phases], ["compile", "unit-test", "package", "lint", "finalize"])

    def test_accept_plan_change_rehydrates_preview_changed_paths_before_sync(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            make_android_project(root)
            (root / ".devtool.toml").write_text('[project]\nkind="android"\nproject="."\n', encoding="utf-8")
            preview = {"changes": [{"kind": "modify", "path": "app/src/main/kotlin/Main.kt"}]}
            scoped_config = validation_config(root, ["app/src/main/kotlin/Main.kt"])
            specs = AndroidGradleRunner().build_validation_specs(scoped_config)
            create_session(
                scoped_config,
                specs,
                apply_context={
                    "validation_mode": "deferred",
                    "manifest": {},
                    "preview": preview,
                    "minimum_phase": "finalize",
                },
            )

            env = {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}
            with patch.dict("os.environ", env, clear=False), patch("devtool.cli.render_summary"):
                rc = main([
                    "--repo", str(root), "--no-clipboard",
                    "validation-accept-plan-change", "--reason", "reviewed",
                ])

            self.assertEqual(rc, 0)
            session = load_active_session(root, required=True)
            self.assertEqual([phase.name for phase in session.phases], ["compile", "unit-test", "package", "lint", "finalize"])

    def test_dry_run_resume_reports_plan_drift_without_mutating_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            make_android_project(root)
            config = validation_config(root)
            scoped_config = validation_config(root, ["app/src/main/kotlin/Main.kt"])
            specs = AndroidGradleRunner().build_validation_specs(scoped_config)
            session = create_session(config, specs)
            parser = build_parser()
            args = parser.parse_args(["--dry-run", "validate", "continue"])

            results, raw_output, returned = _run_validation_session(
                config,
                args,
                parser,
                log_path=None,
                tasker_progress=None,
                continue_on_error=False,
            )

            self.assertEqual(results[0].returncode, 0)
            self.assertIn("Plan drift: safe-change", raw_output)
            self.assertEqual(returned.session_id, session.session_id)
            saved = load_active_session(root, required=True)
            self.assertEqual(saved.session_id, session.session_id)
            self.assertEqual(saved.plan_drift_status, "")
            self.assertEqual([phase.name for phase in saved.phases], ["compile", "unit-test", "package", "lint", "finalize"])

    def test_session_persists_checkpoints_and_invalidates_from_changed_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = validation_config(root)
            specs = [
                CommandSpec("preflight", ["true"], metadata={"validation_phase_name": "preflight"}),
                CommandSpec("compile", ["true"], metadata={"validation_phase_name": "compile"}),
                CommandSpec("package", ["true"], metadata={"validation_phase_name": "package"}),
            ]
            session = create_session(config, specs)
            first = session.phases[0]
            record_phase_result(session, first.key, OperationResult("preflight", ["true"], 0, 1.0, "ok", []))
            loaded = load_session(root, session.session_id)
            self.assertEqual(loaded.phases[0].status, "passed")
            self.assertEqual(load_active_session(root).session_id, session.session_id)

            (root / "app" / "src" / "main" / "kotlin" / "Main.kt").write_text("class Changed\n", encoding="utf-8")
            invalidated = reconcile_inputs(loaded, config)
            self.assertEqual(invalidated, "source")
            self.assertEqual(loaded.phases[0].status, "passed")
            self.assertEqual([phase.status for phase in loaded.phases[1:]], ["pending", "pending"])

    def test_control_c_marks_phase_interrupted_without_validation_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = validation_config(root)
            specs = [
                CommandSpec("preflight", ["true"], metadata={"validation_phase_name": "preflight"}),
                CommandSpec("dependencies", ["true"], metadata={"validation_phase_name": "dependencies"}),
            ]
            session = create_session(config, specs)
            phase = mark_phase_running(session, session.phases[0].key)
            recorded = record_phase_result(
                session,
                phase.key,
                OperationResult(
                    "preflight", ["gradle", "help"], 130, 0.3,
                    "Interrupted by user. Child process was terminated.", [],
                ),
            )
            self.assertEqual(recorded.status, "interrupted")
            self.assertEqual(recorded.last_verdict, "interrupted")
            self.assertEqual(recorded.interruption_count, 1)
            self.assertEqual(recorded.last_interrupted_returncode, 130)
            self.assertFalse(session.failed)
            self.assertEqual(session.status, "paused")
            self.assertEqual(select_phase_keys(session), [recorded.key])

    def test_dead_owner_running_phase_is_recovered_as_interrupted_and_retryable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = validation_config(root)
            specs = [
                CommandSpec("preflight", ["true"], metadata={"validation_phase_name": "preflight"}),
                CommandSpec("dependencies", ["true"], metadata={"validation_phase_name": "dependencies"}),
            ]
            session = create_session(config, specs)
            phase = mark_phase_running(session, session.phases[0].key)
            phase.owner_pid = 99999999
            phase.owner_process_start = "dead"
            phase.attempt_history[-1].owner_pid = 99999999
            phase.attempt_history[-1].owner_process_start = "dead"
            save_session(session, active=True)

            reloaded = load_session(root, session.session_id)
            recovered = recover_interrupted_phases(reloaded)

            self.assertEqual(recovered, ["preflight"])
            self.assertEqual(reloaded.phases[0].status, "interrupted")
            self.assertEqual(reloaded.phases[0].interruption_count, 1)
            self.assertEqual(select_phase_keys(reloaded), [reloaded.phases[0].key])
            self.assertIn("recovered interrupted", reloaded.paused_reason)

    def test_validate_continue_defaults_to_exactly_one_next_phase(self) -> None:
        def fake_run(_config, specs, _args, **_kwargs):
            results = [
                OperationResult(spec.name, list(spec.argv), 0, 0.01, "ok", [], metadata=dict(spec.metadata))
                for spec in specs
            ]
            return results, "\n".join(result.name for result in results)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            make_android_project(root)
            (root / ".devtool.toml").write_text('[project]\nkind="android"\nproject="."\n', encoding="utf-8")
            env = {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}
            with patch.dict("os.environ", env, clear=False), patch("devtool.cli.render_summary"), patch("devtool.cli._run_specs_for_args", side_effect=fake_run):
                first_rc = main(["--repo", str(root), "--no-clipboard", "validate", "start"])
            first = load_active_session(root, required=True)
            self.assertEqual(first_rc, 0)
            self.assertEqual(sum(phase.status == "passed" for phase in first.phases), 1)
            with patch.dict("os.environ", env, clear=False), patch("devtool.cli.render_summary"), patch("devtool.cli._run_specs_for_args", side_effect=fake_run):
                second_rc = main(["--repo", str(root), "--no-clipboard", "validate", "continue"])
            second = load_active_session(root, required=True)
            self.assertEqual(second_rc, 0)
            self.assertEqual(sum(phase.status == "passed" for phase in second.phases), 2)

    def test_failed_phase_is_cached_and_explicitly_retryable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = validation_config(root)
            specs = [
                CommandSpec("compile", ["false"], metadata={"validation_phase_name": "compile"}),
                CommandSpec("package", ["true"], metadata={"validation_phase_name": "package"}),
            ]
            session = create_session(config, specs)
            record_phase_result(session, session.phases[0].key, OperationResult("compile", ["false"], 1, 0.2, "boom", []))
            self.assertTrue(session.failed)
            self.assertEqual(session.phases[0].attempts, 0)
            self.assertEqual(retry_failed(session), "compile")
            self.assertFalse(session.failed)
            self.assertEqual([phase.status for phase in session.phases], ["pending", "pending"])
            self.assertIn("explicit retry", session.phases[0].invalidated_reason)

    def test_attached_deferred_failure_blocks_and_preserves_pending_phases_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = validation_config(root)
            artifact = Path(tmp) / "overlay.zip"
            with ZipFile(artifact, "w") as archive:
                archive.writestr("app/src/main/kotlin/Main.kt", "class Patched\n")
            tx = create_apply_transaction(
                root,
                artifact,
                artifact_type="overlay",
                preview={"changes": [{"kind": "modify", "path": "app/src/main/kotlin/Main.kt"}]},
            )
            specs = [
                CommandSpec("compile", ["false"], metadata={"validation_phase_name": "compile"}),
                CommandSpec("package", ["true"], metadata={"validation_phase_name": "package"}),
            ]
            session = create_session(
                config,
                specs,
                transaction_path=tx["path"],
                apply_context={"validation_mode": "deferred", "artifact": "repair.zip"},
            )
            parser = build_parser()
            args = parser.parse_args(["validate", "continue"])
            failure = OperationResult("compile", ["gradle", "compile"], 1, 0.2, "compile failed", [])

            with patch("devtool.cli._load_or_create_validation_session", return_value=(session, specs)), \
                    patch("devtool.cli._run_specs_for_args", return_value=([failure], "compile failed")), \
                    patch("devtool.cli._run_transaction_rollback") as rollback_call:
                results, raw_output, returned = _run_validation_session(
                    config,
                    args,
                    parser,
                    log_path=None,
                    tasker_progress=None,
                    continue_on_error=False,
                )

            rollback_call.assert_not_called()
            self.assertIs(returned, session)
            self.assertEqual([phase.status for phase in session.phases], ["failed", "pending"])
            self.assertEqual(session.status, "blocked")
            self.assertEqual(session.transaction_path, tx["path"])
            self.assertEqual(read_transaction(tx["path"])["status"], "validation-blocked")
            self.assertTrue(Path(tx["path"]).exists())
            self.assertIsNotNone(load_active_session(root))
            self.assertIn("Applied changes: retained", raw_output)
            self.assertEqual(results[-1].name, "validation-blocked")

    def test_explicit_rollback_policy_seals_remaining_phases_and_keeps_failed_recovery_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = validation_config(root)
            specs = [
                CommandSpec("compile", ["false"], metadata={"validation_phase_name": "compile"}),
                CommandSpec("package", ["true"], metadata={"validation_phase_name": "package"}),
            ]
            transaction_path = str(root / ".devtool" / "transactions" / "tx" / "transaction.json")
            session = create_session(
                config,
                specs,
                transaction_path=transaction_path,
                apply_context={"validation_mode": "deferred"},
            )
            parser = build_parser()
            args = parser.parse_args(["validate", "continue", "--failure-action", "rollback"])
            failure = OperationResult("compile", ["gradle", "compile"], 1, 0.2, "compile failed", [])
            rollback = OperationResult("apply-rollback", ["rsync", "rollback"], 1, 0.1, "rollback failed", [])

            with patch("devtool.cli._load_or_create_validation_session", return_value=(session, specs)), \
                    patch("devtool.cli._run_specs_for_args", return_value=([failure], "compile failed")), \
                    patch("devtool.cli._run_transaction_rollback", return_value=(rollback, "rollback failed")):
                _run_validation_session(
                    config,
                    args,
                    parser,
                    log_path=None,
                    tasker_progress=None,
                    continue_on_error=False,
                )

            self.assertEqual([phase.status for phase in session.phases], ["failed", "skipped"])
            self.assertEqual(session.status, "rollback-failed")
            self.assertEqual(session.transaction_path, transaction_path)
            active = load_active_session(root, required=True)
            self.assertEqual(active.session_id, session.session_id)
            self.assertEqual(active.status, "rollback-failed")
            self.assertIsNone(active.next_pending())
            self.assertFalse(active.apply_context["rollback_succeeded"])

    def test_overlay_apply_pauses_transaction_and_finalizes_later(self) -> None:
        def fake_run(_config, specs, _args, **_kwargs):
            results = [
                OperationResult(spec.name, list(spec.argv), 0, 0.01, "ok", [], metadata=dict(spec.metadata))
                for spec in specs
            ]
            return results, "\n".join(result.name for result in results)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            make_android_project(root)
            (root / ".git").mkdir()
            (root / ".devtool.toml").write_text('[project]\nkind="android"\nproject="."\n', encoding="utf-8")
            artifact = Path(tmp) / "source_overlay.zip"
            with ZipFile(artifact, "w") as archive:
                archive.writestr("app/src/main/kotlin/Main.kt", "class Patched\n")
            env = {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}
            with patch.dict("os.environ", env, clear=False), patch("devtool.cli.render_summary"), patch("devtool.cli._run_specs_for_args", side_effect=fake_run):
                first_rc = main([
                    "--repo", str(root), "--yes", "--force", "--no-clipboard",
                    "apply-overlay", "--validate", "--sequential", "--through", "compile",
                    "--keep-artifact", str(artifact),
                ])
            session = load_active_session(root, required=True)
            transaction = Path(session.transaction_path)
            self.assertEqual(first_rc, 0)
            self.assertEqual((root / "app" / "src" / "main" / "kotlin" / "Main.kt").read_text(encoding="utf-8"), "class Patched\n")
            self.assertEqual([phase.name for phase in session.phases], ["compile", "unit-test", "package", "lint", "finalize"])
            self.assertEqual(session.phases[0].status, "passed")
            self.assertTrue(transaction.exists())
            self.assertTrue(transaction_pending_validation(transaction))

            with patch.dict("os.environ", env, clear=False), patch("devtool.cli.render_summary"), patch("devtool.cli._run_specs_for_args", side_effect=fake_run):
                final_rc = main([
                    "--repo", str(root), "--yes", "--force", "--no-clipboard",
                    "validate", "resume", "--complete",
                ])
            self.assertEqual(final_rc, 0)
            self.assertIsNone(load_active_session(root))
            self.assertFalse(transaction.exists())
            self.assertTrue(artifact.exists())

    def test_pending_transaction_survives_phase_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            (root / "file.txt").write_text("old\n", encoding="utf-8")
            artifact = Path(tmp) / "overlay.zip"
            with ZipFile(artifact, "w") as archive:
                archive.writestr("file.txt", "new\n")
            with patch("devtool.transactions._rsync", return_value="rsync"):
                with patch("devtool.transactions._run_rsync", return_value=""):
                    tx = create_apply_transaction(
                        root,
                        artifact,
                        artifact_type="overlay",
                        preview={"changes": [{"kind": "modify", "path": "file.txt"}]},
                    )
            path = Path(tx["path"])
            mark_transaction_validation_running(path, session_id="session-1", phase="compile")
            self.assertEqual(read_transaction(path)["status"], "validation-running")
            mark_transaction_pending_validation(path, session_id="session-1", next_phase="resources", completed_phases=3, phase_total=9)
            state = read_transaction(path)
            self.assertTrue(transaction_pending_validation(path))
            self.assertEqual(state["status"], "partially-validated")
            self.assertEqual(state["pid"], 0)
            self.assertEqual(state["next_validation_phase"], "resources")

    def test_dry_run_preview_never_creates_or_advances_a_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            make_android_project(root)
            (root / ".devtool.toml").write_text('[project]\nkind="android"\nproject="."\n', encoding="utf-8")
            env = {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}
            with patch.dict("os.environ", env, clear=False), patch("devtool.cli.render_summary"):
                rc = main(["--repo", str(root), "--dry-run", "--no-clipboard", "validate", "start", "--complete"])
            self.assertEqual(rc, 0)
            self.assertIsNone(load_active_session(root))

    def test_schema_v1_failure_migrates_to_blocked_with_attempt_history(self) -> None:
        import json

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            session_id = "legacy-session"
            state_path = root / ".devtool" / "state" / "android-validation" / session_id / "state.json"
            state_path.parent.mkdir(parents=True)
            state_path.write_text(json.dumps({
                "schema_version": 1,
                "session_id": session_id,
                "repo_root": str(root.resolve()),
                "project_root": str(root.resolve()),
                "project_type": "android",
                "target": "",
                "variant": "",
                "status": "failed",
                "created_at": "2026-07-27T00:00:00Z",
                "updated_at": "2026-07-27T00:01:00Z",
                "phases": [{
                    "key": "compile",
                    "name": "compile",
                    "label": "compile",
                    "spec_name": "compile",
                    "index": 1,
                    "total": 1,
                    "status": "failed",
                    "returncode": 1,
                    "output_tail": "compile failed",
                }],
                "fingerprints": {},
            }), encoding="utf-8")
            session = load_session(root, session_id)
            self.assertEqual(session.status, "blocked")
            self.assertEqual(session.phases[0].status, "failed")
            self.assertEqual(session.phases[0].attempt_history[0].verdict, "code-failed")
            self.assertEqual(session.failure_action, "pause")

    def test_validation_abort_relocates_stale_session_repo_root_before_save(self) -> None:
        import json

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            (root / ".devtool.toml").write_text('[project]\nkind="android"\nproject="."\n', encoding="utf-8")
            config = validation_config(root)
            session = create_session(
                config,
                [CommandSpec("compile", ["gradle", "compile"], metadata={"validation_phase_name": "compile"})],
            )
            state_path = root / ".devtool" / "state" / "android-validation" / session.session_id / "state.json"
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            stale_root = "/data/data/com.termux/files/home/Code/dustui"
            payload["repo_root"] = stale_root
            payload["project_root"] = stale_root
            state_path.write_text(json.dumps(payload), encoding="utf-8")

            relocated = load_session(root, session.session_id)
            self.assertEqual(relocated.repo_root, str(root.resolve()))
            self.assertEqual(relocated.project_root, str(root.resolve()))

            abort_rc = main(["--repo", str(root), "validation-abort", "--keep-changes"])
            self.assertEqual(abort_rc, 0)
            self.assertIsNone(load_active_session(root))
            persisted = load_session(root, session.session_id)
            self.assertEqual(persisted.status, "aborted-kept-changes")
            self.assertEqual(persisted.repo_root, str(root.resolve()))

    def test_resource_failure_pauses_and_keeps_phase_retryable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = validation_config(root)
            session = create_session(
                config,
                [CommandSpec("compile", ["gradle", "compile"], metadata={"validation_phase_name": "compile"})],
            )
            phase = mark_phase_running(session, session.phases[0].key)
            recorded = record_phase_result(
                session,
                phase.key,
                OperationResult(
                    "compile",
                    ["gradle", "compile"],
                    137,
                    2.0,
                    "Process killed by earlyoom after memory pressure",
                    [],
                    failure_kind="memory-pressure",
                ),
            )
            self.assertEqual(recorded.status, "interrupted")
            self.assertEqual(recorded.last_verdict, "resource-failed")
            self.assertEqual(session.status, "paused")
            self.assertFalse(session.failed)
            self.assertEqual(select_phase_keys(session), [recorded.key])

    def test_missing_deferred_validator_blocks_and_preserves_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = validation_config(root)
            artifact = Path(tmp) / "overlay.zip"
            with ZipFile(artifact, "w") as archive:
                archive.writestr("app/src/main/kotlin/Main.kt", "class Patched\n")
            tx = create_apply_transaction(
                root,
                artifact,
                artifact_type="overlay",
                preview={"changes": [{"kind": "modify", "path": "app/src/main/kotlin/Main.kt"}]},
            )
            artifact.unlink()
            spec = CommandSpec(
                "artifact-validate-script",
                ["artifact-validate-script", "missing.zip"],
                metadata={
                    "validation_phase_name": "static",
                    "devtool_internal_validation": "artifact-script",
                },
            )
            session = create_session(
                config,
                [spec],
                transaction_path=tx["path"],
                apply_context={
                    "validation_mode": "deferred",
                    "artifacts": [str(artifact)],
                    "artifact_script_in_plan": True,
                },
            )
            parser = build_parser()
            args = parser.parse_args(["validate", "continue"])
            with patch("devtool.cli._load_or_create_validation_session", return_value=(session, [spec])):
                results, raw_output, returned = _run_validation_session(
                    config,
                    args,
                    parser,
                    log_path=None,
                    tasker_progress=None,
                    continue_on_error=False,
                )
            self.assertIs(returned, session)
            self.assertEqual(session.status, "blocked")
            self.assertEqual(session.phases[0].last_verdict, "code-failed")
            self.assertEqual(read_transaction(tx["path"])["status"], "validation-blocked")
            self.assertTrue(Path(tx["path"]).exists())
            self.assertIn("source artifact is missing", raw_output)
            self.assertEqual(results[-1].name, "validation-blocked")

    def test_explicit_rollback_policy_restores_tree_and_closes_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            target = root / "app" / "src" / "main" / "kotlin" / "Main.kt"
            target.write_text("class Old\n", encoding="utf-8")
            artifact = Path(tmp) / "overlay.zip"
            with ZipFile(artifact, "w") as archive:
                archive.writestr("app/src/main/kotlin/Main.kt", "class New\n")
            tx = create_apply_transaction(
                root,
                artifact,
                artifact_type="overlay",
                preview={"changes": [{"kind": "modify", "path": "app/src/main/kotlin/Main.kt"}]},
            )
            target.write_text("class New\n", encoding="utf-8")
            config = validation_config(root)
            specs = [
                CommandSpec("compile", ["false"], metadata={"validation_phase_name": "compile"}),
                CommandSpec("package", ["true"], metadata={"validation_phase_name": "package"}),
            ]
            session = create_session(
                config,
                specs,
                transaction_path=tx["path"],
                apply_context={"validation_mode": "deferred"},
            )
            parser = build_parser()
            args = parser.parse_args(["validate", "continue", "--failure-action", "rollback"])
            failure = OperationResult("compile", ["false"], 1, 0.1, "compile failed", [])
            with patch("devtool.cli._load_or_create_validation_session", return_value=(session, specs)), \
                    patch("devtool.cli._run_specs_for_args", return_value=([failure], "compile failed")):
                results, _raw, returned = _run_validation_session(
                    config,
                    args,
                    parser,
                    log_path=None,
                    tasker_progress=None,
                    continue_on_error=False,
                )
            self.assertIs(returned, session)
            self.assertEqual(target.read_text(encoding="utf-8"), "class Old\n")
            self.assertEqual(session.status, "aborted-rolled-back")
            self.assertEqual([phase.status for phase in session.phases], ["failed", "skipped"])
            self.assertFalse(Path(tx["path"]).exists())
            self.assertIsNone(load_active_session(root))
            self.assertEqual(results[-1].name, "apply-rollback")

    def test_abort_keep_changes_detaches_transaction_without_trapping_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            make_android_project(root)
            (root / ".devtool.toml").write_text('[project]\nkind="android"\nproject="."\n', encoding="utf-8")
            target = root / "app" / "src" / "main" / "kotlin" / "Main.kt"
            artifact = Path(tmp) / "overlay.zip"
            with ZipFile(artifact, "w") as archive:
                archive.writestr("app/src/main/kotlin/Main.kt", "class Kept\n")
            tx = create_apply_transaction(
                root,
                artifact,
                artifact_type="overlay",
                preview={"changes": [{"kind": "modify", "path": "app/src/main/kotlin/Main.kt"}]},
            )
            target.write_text("class Kept\n", encoding="utf-8")
            session = create_session(
                validation_config(root),
                [CommandSpec("compile", ["true"], metadata={"validation_phase_name": "compile"})],
                transaction_path=tx["path"],
                apply_context={"validation_mode": "deferred"},
            )
            env = {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}
            with patch.dict("os.environ", env, clear=False), patch("devtool.cli.render_summary"):
                rc = main(["--repo", str(root), "--no-clipboard", "validate", "abort", "--keep-changes"])
            persisted = load_session(root, session.session_id)
            self.assertEqual(rc, 0)
            self.assertEqual(target.read_text(encoding="utf-8"), "class Kept\n")
            self.assertEqual(persisted.status, "aborted-kept-changes")
            self.assertEqual(persisted.transaction_path, "")
            self.assertFalse(Path(tx["path"]).exists())
            self.assertIsNone(load_active_session(root))

    def test_post_validation_commit_failure_keeps_validated_tree_and_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = validation_config(root)
            artifact = Path(tmp) / "overlay.zip"
            with ZipFile(artifact, "w") as archive:
                archive.writestr("app/src/main/kotlin/Main.kt", "class Patched\n")
            tx = create_apply_transaction(
                root,
                artifact,
                artifact_type="overlay",
                preview={"changes": [{"kind": "modify", "path": "app/src/main/kotlin/Main.kt"}]},
            )
            spec = CommandSpec("compile", ["true"], metadata={"validation_phase_name": "compile"})
            session = create_session(
                config,
                [spec],
                transaction_path=tx["path"],
                apply_context={
                    "validation_mode": "deferred",
                    "command": "apply-overlay",
                    "artifact": str(artifact),
                    "artifacts": [str(artifact)],
                    "commit": "Apply validated overlay",
                    "keep_artifact": True,
                    "manifest": {},
                },
            )
            parser = build_parser()
            args = parser.parse_args(["validate", "continue"])
            success = OperationResult("compile", ["true"], 0, 0.1, "ok", [], metadata=dict(spec.metadata))
            commit_failure = OperationResult("git-commit", ["git", "commit"], 1, 0.1, "signing failed", [])

            def fail_commit(_config, _args, results, raw_output, **_kwargs):
                return [*results, commit_failure], raw_output + "\n\nsigning failed"

            with patch("devtool.cli._load_or_create_validation_session", return_value=(session, [spec])), \
                    patch("devtool.cli._run_specs_for_args", return_value=([success], "ok")), \
                    patch("devtool.cli._maybe_commit_after_apply", side_effect=fail_commit), \
                    patch("devtool.cli._run_transaction_rollback") as rollback_call:
                results, _raw, returned = _run_validation_session(
                    config,
                    args,
                    parser,
                    log_path=None,
                    tasker_progress=None,
                    continue_on_error=False,
                )
            rollback_call.assert_not_called()
            self.assertIs(returned, session)
            self.assertEqual(session.status, "validated-uncommitted")
            self.assertEqual(session.phases[0].status, "passed")
            self.assertEqual(read_transaction(tx["path"])["status"], "validated-uncommitted")
            self.assertTrue(Path(tx["path"]).exists())
            self.assertIsNotNone(load_active_session(root))
            self.assertEqual(results[-1].name, "git-commit")

    def test_apply_notification_reports_pause_instead_of_success(self) -> None:
        args = SimpleNamespace(command="apply-overlay", artifact="demo.zip")
        config = ProjectConfig(repo_root=Path("/tmp/demo"), kind="android")
        paused = OperationResult(
            "validation-paused", ["devtool", "validate"], 0, 0.0, "Validation paused", [],
            metadata={"validation_complete": False, "completed_phases": 3, "phase_total": 9, "next_phase": "resources"},
        )
        notification = build_apply_notification(args, config, [paused], {"failed": 0, "warnings": 0, "errors": 0})
        self.assertEqual(notification.title, "Devtool overlay validation paused")
        self.assertIn("phase 3/9", notification.body)
        self.assertIn("next resources", notification.body)
        self.assertEqual(notification.toast, "Validation paused: demo.zip")


if __name__ == "__main__":
    unittest.main()
