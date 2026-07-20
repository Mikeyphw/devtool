from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile

from devtool.android_integration import android_validation_change_scope
from devtool.apply_notifications import build_apply_notification
from devtool.cli import build_parser, main
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
    create_session,
    load_active_session,
    load_session,
    reconcile_inputs,
    record_phase_result,
    retry_failed,
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
        for action in ("start", "next", "resume", "status", "finalize", "abort"):
            args = parser.parse_args(["validate", action])
            self.assertEqual(args.validate_scope, action)

    def test_cli_runs_one_durable_phase_per_invocation_and_resumes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            make_android_project(root)
            (root / ".devtool.toml").write_text('[project]\nkind="android"\nproject="."\n', encoding="utf-8")
            with patch.dict("os.environ", {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}, clear=False):
                with patch("devtool.cli.render_summary"):
                    first_rc = main(["--repo", str(root), "--dry-run", "--no-clipboard", "validate", "start", "--next"])
                active = load_active_session(root, required=True)
                self.assertEqual(first_rc, 0)
                self.assertEqual(active.phases[0].status, "skipped")
                self.assertEqual(active.phases[1].status, "pending")
                with patch("devtool.cli.render_summary"):
                    second_rc = main(["--repo", str(root), "--dry-run", "--no-clipboard", "validate", "resume", "--next"])
                resumed = load_active_session(root, required=True)
                self.assertEqual(second_rc, 0)
                self.assertEqual(resumed.session_id, active.session_id)
                self.assertEqual(resumed.phases[1].status, "skipped")

    def test_changed_file_scope_selects_minimum_safe_phase_plan(self) -> None:
        expected = {
            ("README.md",): ("docs", ["finalize"]),
            ("app/src/main/kotlin/Main.kt",): ("source", ["compile", "unit-test", "package", "artifact-check", "lint", "finalize"]),
            ("app/src/main/res/layout/main.xml",): ("resources", ["resources", "package", "artifact-check", "lint", "finalize"]),
            ("app/src/test/kotlin/MainTest.kt",): ("tests", ["unit-test", "lint", "finalize"]),
            ("build.gradle.kts",): ("full", ["preflight", "dependencies", "compile", "resources", "unit-test", "package", "artifact-check", "lint", "finalize"]),
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
            self.assertEqual(loaded.phases[0].status, "succeeded")
            self.assertEqual(load_active_session(root).session_id, session.session_id)

            (root / "app" / "src" / "main" / "kotlin" / "Main.kt").write_text("class Changed\n", encoding="utf-8")
            invalidated = reconcile_inputs(loaded, config)
            self.assertEqual(invalidated, "compile")
            self.assertEqual(loaded.phases[0].status, "succeeded")
            self.assertEqual([phase.status for phase in loaded.phases[1:]], ["pending", "pending"])

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
            self.assertEqual([phase.name for phase in session.phases], ["compile", "unit-test", "package", "artifact-check", "lint", "finalize"])
            self.assertEqual(session.phases[0].status, "succeeded")
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
