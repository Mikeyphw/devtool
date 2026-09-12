from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from rich.console import Console

from tests._compat import portable_shell_argv

from devtool.cli import _load_or_create_validation_session, build_parser
from devtool import android_build
from devtool.android_integration import android_project_inspection_cache, inspect_android_project
from devtool.gradle_progress import GradleProgressTracker
from devtool.models import CommandSpec, ProjectConfig
from devtool.project_runners import AndroidGradleRunner
from devtool.runners import _run_process_live
from devtool.termux_toolchains import active_gradle_config_text, classify_native_requirement
from devtool.validation_sessions import (
    SESSION_SCHEMA_VERSION,
    load_active_session,
    save_session,
)


class DurableResumePlanTests(unittest.TestCase):
    @staticmethod
    def _specs(root: Path) -> list[CommandSpec]:
        phases = ["preflight", "dependencies", "compile", "resources", "unit-test", "package", "lint", "finalize"]
        return [
            CommandSpec(
                f"android-validation-{phase}",
                ["fake-gradle", f":app:{phase}"],
                cwd=str(root),
                env={"PLAN_TOKEN": "stable"},
                metadata={
                    "validation_phase_name": phase,
                    "progress_parser": "gradle",
                    "gradle_tasks": [f":app:{phase}"],
                },
            )
            for phase in phases
        ]

    def test_resume_uses_durable_specs_without_replanning_and_keeps_phase_5_next(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "settings.gradle.kts").write_text('rootProject.name = "demo"\n', encoding="utf-8")
            devtool_config = root / ".devtool.toml"
            devtool_config.write_text('[project]\nkind="android"\nproject="."\n', encoding="utf-8")
            config = ProjectConfig(repo_root=root, project_root=str(root), kind="android")
            parser = build_parser()
            first_args = parser.parse_args(["--repo", str(root), "validate", "--sequential"])
            planned = self._specs(root)

            with patch("devtool.cli._validation_specs_for_args", return_value=planned) as planner:
                session, first_specs = _load_or_create_validation_session(config, first_args, parser)
            self.assertEqual(planner.call_count, 1)
            self.assertEqual(SESSION_SCHEMA_VERSION, 6)
            self.assertEqual(len(session.plan_specs), 8)

            for phase in session.phases[:4]:
                phase.status = "passed"
                phase.returncode = 0
                phase.last_verdict = "passed"
            session.status = "paused"
            save_session(session, active=True)

            # Reproduce the field report: validation reached phase 4, then the
            # user edited .devtool.toml while paused. A durable resume must keep
            # the original command plan and continue at phase 5, not preflight.
            devtool_config.write_text(
                '[project]\nkind="android"\nproject="."\n# edited while paused\n',
                encoding="utf-8",
            )

            resume_args = parser.parse_args(["--repo", str(root), "validate", "--resume"])
            with patch(
                "devtool.cli._validation_specs_for_args",
                side_effect=AssertionError("resume must not rebuild validation specs"),
            ):
                resumed, resumed_specs = _load_or_create_validation_session(config, resume_args, parser)

            self.assertEqual(resumed.session_id, session.session_id)
            self.assertEqual([phase.status for phase in resumed.phases[:4]], ["passed"] * 4)
            self.assertIsNotNone(resumed.next_pending())
            self.assertEqual(resumed.next_pending().index, 5)
            self.assertEqual([spec.argv for spec in resumed_specs], [spec.argv for spec in first_specs])
            self.assertEqual([spec.env for spec in resumed_specs], [spec.env for spec in first_specs])
            self.assertEqual([spec.cwd for spec in resumed_specs], [spec.cwd for spec in first_specs])

            durable = load_active_session(root, required=True)
            self.assertEqual(len(durable.plan_specs), 8)
            self.assertEqual(durable.next_pending().index, 5)

    def test_durable_resume_still_invalidates_for_source_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "app" / "src" / "main" / "kotlin" / "Main.kt"
            src.parent.mkdir(parents=True)
            src.write_text("class Main\n", encoding="utf-8")
            (root / "settings.gradle.kts").write_text('rootProject.name = "demo"\n', encoding="utf-8")
            config = ProjectConfig(repo_root=root, project_root=str(root), kind="android")
            parser = build_parser()
            args = parser.parse_args(["--repo", str(root), "validate", "--sequential"])
            planned = self._specs(root)
            with patch("devtool.cli._validation_specs_for_args", return_value=planned):
                session, _ = _load_or_create_validation_session(config, args, parser)
            for phase in session.phases[:4]:
                phase.status = "passed"
                phase.returncode = 0
                phase.last_verdict = "passed"
            session.status = "paused"
            save_session(session, active=True)

            src.write_text("class Changed\n", encoding="utf-8")
            resume_args = parser.parse_args(["--repo", str(root), "validate", "--resume"])
            with patch(
                "devtool.cli._validation_specs_for_args",
                side_effect=AssertionError("durable resume must not replan for source invalidation"),
            ):
                resumed, _ = _load_or_create_validation_session(config, resume_args, parser)

            self.assertEqual(resumed.phases[0].status, "passed")
            self.assertEqual(resumed.phases[1].status, "passed")
            self.assertEqual(resumed.phases[2].status, "pending")
            self.assertEqual(resumed.next_pending().index, 3)

    def test_schema_5_session_migrates_with_empty_plan_specs_for_one_time_backfill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = ProjectConfig(repo_root=root, project_root=str(root), kind="android")
            parser = build_parser()
            args = parser.parse_args(["--repo", str(root), "validate", "--sequential"])
            with patch("devtool.cli._validation_specs_for_args", return_value=self._specs(root)[:2]):
                session, _ = _load_or_create_validation_session(config, args, parser)
            state_path = root / ".devtool" / "state" / "android-validation" / session.session_id / "state.json"
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            payload["schema_version"] = 5
            payload.pop("plan_specs", None)
            state_path.write_text(json.dumps(payload), encoding="utf-8")

            loaded = load_active_session(root, required=True)
            self.assertEqual(loaded.plan_specs, [])


class AndroidInspectionCacheTests(unittest.TestCase):
    def test_validation_planning_cache_reuses_project_info_only_inside_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "settings.gradle.kts").write_text('rootProject.name = "demo"\n', encoding="utf-8")
            config = ProjectConfig(repo_root=root, project_root=str(root), kind="android")
            info = android_build.ProjectInfo(
                root=root,
                name="demo",
                android=True,
                android_application=True,
                compile_sdk=35,
                build_tools=None,
                agp_major=8,
                native_build=False,
                ndk_version=None,
                cmake_version=None,
                agp_version="8.8.0",
            )
            with patch("devtool.android_integration.android_build.inspect_project", return_value=info) as inspect:
                with android_project_inspection_cache(config):
                    first = inspect_android_project(config)
                    second = inspect_android_project(config)
                    self.assertIs(first, second)
                    self.assertEqual(inspect.call_count, 1)
                inspect_android_project(config)
                self.assertEqual(inspect.call_count, 2)
            self.assertNotIn("_devtool_android_inspection_cache", config.runner_options)

    def test_resumable_android_plan_inspects_project_once_for_all_eight_phases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app" / "src" / "main").mkdir(parents=True)
            (root / "app" / "src" / "main" / "AndroidManifest.xml").write_text(
                '<manifest package="demo"/>', encoding="utf-8"
            )
            (root / "settings.gradle.kts").write_text(
                'rootProject.name = "demo"\ninclude(":app")\n', encoding="utf-8"
            )
            (root / "build.gradle.kts").write_text(
                'plugins { id("com.android.application") version "8.8.0" apply false }\n', encoding="utf-8"
            )
            (root / "app" / "build.gradle.kts").write_text(
                'plugins { id("com.android.application") }\nandroid { compileSdk = 35 }\n', encoding="utf-8"
            )
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="android",
                variant="debug",
                runner_options={
                    "validation": {"resumable": True},
                    "tasks": {
                        "build": ["assembleDebug"],
                        "test": ["testDebugUnitTest"],
                        "lint": ["lintDebug"],
                    },
                },
            )
            real_inspect = android_build.inspect_project
            with patch(
                "devtool.android_integration.android_build.inspect_project",
                wraps=real_inspect,
            ) as inspect:
                specs = AndroidGradleRunner().build_validation_specs(config)

            self.assertEqual(len(specs), 8)
            self.assertEqual(inspect.call_count, 1)
            self.assertNotIn("_devtool_android_inspection_cache", config.runner_options)


class GradleConfigScanTests(unittest.TestCase):
    def test_active_gradle_scan_prunes_generated_cache_and_node_trees(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app").mkdir()
            (root / "app" / "build.gradle.kts").write_text("android { compileSdk = 35 }\n", encoding="utf-8")
            ignored = [
                root / ".gradle" / "cache",
                root / "app" / "build" / "generated",
                root / "node_modules" / "pkg",
                root / ".devtool" / "scratch",
            ]
            for directory in ignored:
                directory.mkdir(parents=True)
                (directory / "build.gradle.kts").write_text("SHOULD_NOT_BE_SCANNED ndkVersion = 'bad'\n", encoding="utf-8")

            with patch.object(Path, "rglob", side_effect=AssertionError("active Gradle scan must not use rglob")):
                text = active_gradle_config_text(root)

            self.assertIn("compileSdk = 35", text)
            self.assertNotIn("SHOULD_NOT_BE_SCANNED", text)
            self.assertNotIn("ndkVersion = 'bad'", text)

    def test_native_requirement_scan_also_prunes_generated_trees_without_rglob(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            generated = root / "build" / "generated"
            generated.mkdir(parents=True)
            (generated / "generated.cpp").write_text("int generated;\n", encoding="utf-8")
            with patch.object(Path, "rglob", side_effect=AssertionError("native scan must not use rglob")):
                requirement = classify_native_requirement(root, android=False)
            self.assertEqual(requirement.capability, "none")


class GradleStartupProgressTests(unittest.TestCase):
    def test_synthetic_states_advance_build_heading_does_not_freeze_and_real_events_take_over(self) -> None:
        tracker = GradleProgressTracker(
            ["./gradlew", ":app:compileDebugKotlin"],
            "/repo",
            "Build",
            Console(force_terminal=False),
        )
        self.assertEqual(tracker.detail, "Preparing Gradle environment")
        self.assertEqual(tracker._synthetic_detail_for_elapsed(None), "Preparing Gradle environment")
        self.assertEqual(tracker._synthetic_detail_for_elapsed(0.5), "Starting Gradle daemon")
        self.assertEqual(tracker._synthetic_detail_for_elapsed(3.0), "Configuring Gradle project")

        tracker.mark_process_started()
        self.assertEqual(tracker.detail, "Starting Gradle daemon")
        tracker.update_from_line("==> Build")
        self.assertFalse(tracker.real_progress_seen)
        self.assertNotEqual(tracker.detail, "Build")
        tracker._set_synthetic_detail("Configuring Gradle project")
        self.assertEqual(tracker.detail, "Configuring Gradle project")

        tracker.update_from_line('DEVTOOL_EVENT {"sequence":1,"kind":"task-graph","total":2}')
        self.assertTrue(tracker.real_progress_seen)
        self.assertEqual(tracker.detail, "Gradle task graph ready: 2 tasks")
        tracker._set_synthetic_detail("Starting Gradle daemon")
        self.assertEqual(tracker.detail, "Gradle task graph ready: 2 tasks")

    def test_no_daemon_synthetic_state_does_not_claim_daemon_startup(self) -> None:
        tracker = GradleProgressTracker(
            ["./gradlew", "test", "--no-daemon"],
            "/repo",
            "Build",
            Console(force_terminal=False),
        )
        tracker.mark_process_started()
        self.assertEqual(tracker.detail, "Starting Gradle process")
        self.assertEqual(tracker._synthetic_detail_for_elapsed(0.5), "Starting Gradle process")

    def test_gradle_tracker_enters_before_child_popen(self) -> None:
        order: list[str] = []
        real_popen = subprocess.Popen

        class FakeTracker:
            def __init__(self, *args, **kwargs):
                self.enabled = True
                self.update_callback = None

            def __enter__(self):
                order.append("tracker-enter")
                return self

            def __exit__(self, exc_type, exc, tb):
                order.append("tracker-exit")

            def mark_process_started(self):
                order.append("process-started")

            def update_from_line(self, line: str):
                order.append("line")

            def finish(self, returncode: int):
                order.append(f"finish-{returncode}")

        def popen(*args, **kwargs):
            order.append("popen")
            return real_popen(*args, **kwargs)

        with patch("devtool.runners.GradleProgressTracker", FakeTracker), patch(
            "devtool.runners.subprocess.Popen", side_effect=popen
        ):
            code, output = _run_process_live(
                portable_shell_argv("printf '> Task :app:compileDebugKotlin\n'"),
                cwd=os.getcwd(),
                env=os.environ.copy(),
                verbose=False,
                command_name="Build",
                progress_parser="gradle",
            )

        self.assertEqual(code, 0)
        self.assertIn("> Task :app:compileDebugKotlin", output)
        self.assertLess(order.index("tracker-enter"), order.index("popen"))
        self.assertLess(order.index("popen"), order.index("process-started"))
        self.assertIn("tracker-exit", order)


if __name__ == "__main__":
    unittest.main()
