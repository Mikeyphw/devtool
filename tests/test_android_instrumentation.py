from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from devtool import android_instrumentation
from devtool.android_instrumentation import _instrument_command, _instrumentation_aapt2, _local_pm_prefix, _parse_counts
from devtool.models import ProjectConfig
from devtool.runners import build_specs


class AndroidInstrumentationTests(unittest.TestCase):
    def test_parses_android_junit_runner_status(self) -> None:
        output = """
INSTRUMENTATION_STATUS: numtests=2
INSTRUMENTATION_STATUS_CODE: 1
INSTRUMENTATION_STATUS_CODE: 0
INSTRUMENTATION_STATUS: numtests=2
INSTRUMENTATION_STATUS_CODE: 1
INSTRUMENTATION_STATUS_CODE: -2
"""
        counts = _parse_counts(output)
        self.assertEqual(counts["total"], 2)
        self.assertEqual(counts["failed"], 1)
        self.assertEqual(counts["passed"], 1)
        self.assertEqual(counts["skipped"], 0)
        self.assertEqual(counts["executed"], 2)


    def test_android_junit_runner_ignored_and_assumption_are_skips(self) -> None:
        output = """
INSTRUMENTATION_STATUS: numtests=3
INSTRUMENTATION_STATUS: class=com.example.ParserTest
INSTRUMENTATION_STATUS: test=passes
INSTRUMENTATION_STATUS_CODE: 1
INSTRUMENTATION_STATUS_CODE: 0
INSTRUMENTATION_STATUS: class=com.example.ParserTest
INSTRUMENTATION_STATUS: test=ignored
INSTRUMENTATION_STATUS_CODE: 1
INSTRUMENTATION_STATUS_CODE: -3
INSTRUMENTATION_STATUS: class=com.example.ParserTest
INSTRUMENTATION_STATUS: test=assumption
INSTRUMENTATION_STATUS_CODE: 1
INSTRUMENTATION_STATUS_CODE: -4
"""
        counts = android_instrumentation._parse_counts(output)
        self.assertEqual(counts["total"], 3)
        self.assertEqual(counts["executed"], 3)
        self.assertEqual(counts["passed"], 1)
        self.assertEqual(counts["failed"], 0)
        self.assertEqual(counts["skipped"], 2)
        self.assertEqual(android_instrumentation._parse_failed_ids(output), [])

    def test_android_junit_runner_process_crash_is_not_a_failed_testcase(self) -> None:
        output = """
INSTRUMENTATION_STATUS: numtests=1
INSTRUMENTATION_STATUS_CODE: -2
INSTRUMENTATION_RESULT: shortMsg=Process crashed.
"""
        from devtool.android.instrumentation import _execution_classification
        counts, failed_ids, failure_kind, _reason = _execution_classification(output, 1)
        self.assertEqual(failure_kind, "setup-crash")
        self.assertEqual(counts["executed"], 0)
        self.assertEqual(counts["failed"], 0)
        self.assertEqual(failed_ids, [])

    def test_android_junit_runner_real_failure_remains_failed(self) -> None:
        output = """
INSTRUMENTATION_STATUS: numtests=1
INSTRUMENTATION_STATUS: class=com.example.ParserTest
INSTRUMENTATION_STATUS: test=fails
INSTRUMENTATION_STATUS_CODE: 1
INSTRUMENTATION_STATUS_CODE: -2
"""
        counts = android_instrumentation._parse_counts(output)
        self.assertEqual(counts["passed"], 0)
        self.assertEqual(counts["failed"], 1)
        self.assertEqual(counts["skipped"], 0)
        self.assertEqual(android_instrumentation._parse_failed_ids(output), ["com.example.ParserTest.fails"])

    def test_rooted_termux_commands_reuse_configured_privilege_prefix(self) -> None:
        self.assertEqual(_local_pm_prefix("sudo pm install -r -t"), ["sudo", "pm"])
        self.assertEqual(
            _instrument_command("", am_command="sudo am instrument"),
            ["sudo", "am", "instrument"],
        )

    def test_native_termux_instrumentation_auto_uses_termux_aapt2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sdk = root / "sdk"
            sdk.mkdir()
            aapt2 = root / "aapt2"
            aapt2.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            aapt2.chmod(0o755)
            info = mock.Mock(compile_sdk=36)
            with mock.patch("devtool.android_instrumentation.android_build.running_under_termux_python", return_value=True), mock.patch(
                "devtool.android_instrumentation.android_build.termux_aapt2_path", return_value=aapt2
            ), mock.patch(
                "devtool.android_instrumentation.android_build.inspect_project", return_value=info
            ), mock.patch(
                "devtool.android_instrumentation.android_build.discover_sdk_root", return_value=sdk
            ), mock.patch(
                "devtool.android_instrumentation.android_build.termux_aapt2_supports_platform", return_value=True
            ):
                selected = _instrumentation_aapt2(root, provider="auto")

            self.assertEqual(selected, aapt2)

    def test_non_termux_instrumentation_does_not_force_termux_aapt2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch(
                "devtool.android_instrumentation.android_build.running_under_termux_python", return_value=False
            ):
                self.assertIsNone(_instrumentation_aapt2(root, provider="auto"))

    def test_android_runner_builds_device_instrumentation_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "settings.gradle").write_text("rootProject.name='instrumentation-fixture'\n", encoding="utf-8")
            (root / "build.gradle").write_text("tasks.register('noop')\n", encoding="utf-8")
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="android",
                device_serial="serial-1",
                runner_options={
                    "android": {
                        "variant": "release",
                        "test_runner": "demo.test/Runner",
                        "aapt2_provider": "termux",
                    },
                    "gradle": {"args": ["-Dorg.gradle.parallel=true", "-Pdemo.flag=true"]},
                    "test_selection": {"instrumented": True, "classes": ["demo.UiTest"]},
                },
            )
            specs = build_specs(config, "test")
            spec = next(item for item in specs if "devtool.android_instrumentation" in item.argv)

        self.assertTrue(any(item.metadata.get("instrumentation_build_owned_by_gradle_planner") for item in specs))
        self.assertEqual(spec.name, "instrumentation")
        self.assertIn("devtool.android_instrumentation", spec.argv)
        self.assertIn("serial-1", spec.argv)
        self.assertIn("demo.UiTest", spec.argv)
        self.assertIn("sudo am instrument", spec.argv)
        self.assertIn("--aapt2-provider", spec.argv)
        self.assertIn("termux", spec.argv)
        build_spec = next(item for item in specs if item.name == "instrumentation-build")
        self.assertIn("-Dorg.gradle.parallel=true", build_spec.argv)
        self.assertIn("-Pdemo.flag=true", build_spec.argv)
        self.assertNotIn("--gradle-arg=-Dorg.gradle.parallel=true", spec.argv)
        self.assertNotIn("--gradle-arg=-Pdemo.flag=true", spec.argv)
        self.assertTrue(spec.metadata["instrumentation_skip_build"])

        # Prove the exact generated device argv is accepted by the instrumentation CLI.
        # Gradle arguments belong to the planner-owned build spec, not the device spec.
        parser = android_instrumentation.build_parser()
        parsed = parser.parse_args(spec.argv[3:])
        self.assertEqual(parsed.gradle_arg, [])
        self.assertEqual(spec.metadata["report_parser"], "android-instrumentation")

    def test_release_pipeline_preserves_configured_debug_instrumentation_lane(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "settings.gradle").write_text("rootProject.name='instrumentation-fixture'\n", encoding="utf-8")
            (root / "build.gradle").write_text("tasks.register('noop')\n", encoding="utf-8")
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="android",
                variant="release",
                runner_options={
                    "android": {
                        "variant": "release",
                        "instrumentation": {"policy": "required"},
                    },
                    "tasks": {
                        "instrumentation": [":app:connectedDebugAndroidTest"],
                    },
                },
            )
            specs = build_specs(config, "instrumentation")
            spec = next(item for item in specs if "devtool.android_instrumentation" in item.argv)

        self.assertTrue(any(item.metadata.get("instrumentation_build_owned_by_gradle_planner") for item in specs))
        parser = android_instrumentation.build_parser()
        parsed = parser.parse_args(spec.argv[3:])
        self.assertEqual(parsed.variant, "debug")
        self.assertEqual(
            spec.metadata["gradle_tasks"],
            [":app:assembleDebug", ":app:assembleDebugAndroidTest"],
        )
        self.assertEqual(
            spec.metadata["configured_instrumentation_tasks"],
            [":app:connectedDebugAndroidTest"],
        )
        self.assertEqual(spec.metadata["instrumentation_variant"], "debug")
        self.assertEqual(spec.metadata["release_variant"], "release")

    def test_explicit_instrumentation_variant_rejects_conflicting_configured_task_lane(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "settings.gradle").write_text("rootProject.name='instrumentation-fixture'\n", encoding="utf-8")
            (root / "build.gradle").write_text("tasks.register('noop')\n", encoding="utf-8")
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="android",
                variant="release",
                runner_options={
                    "android": {
                        "variant": "release",
                        "instrumentation": {
                            "policy": "required",
                            "variant": "benchmark",
                        },
                    },
                    "tasks": {
                        "instrumentation": [":app:connectedDebugAndroidTest"],
                    },
                },
            )
            with self.assertRaisesRegex(ValueError, "conflicts with variant-bound task"):
                build_specs(config, "instrumentation")

    def test_direct_module_without_canonical_flag_lowers_to_canonical_orchestrator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seen: dict[str, object] = {}

            class FakeOps:
                @classmethod
                def from_config(cls, config):
                    seen["config"] = config
                    return object()

            class FakeResult:
                status = "passed"
                reason = ""
                failed = 0
                returncode = 0
                ok = True

                def as_dict(self):
                    return {"status": self.status, "ok": True, "failed": 0, "returncode": 0}

            class FakeOrchestrator:
                def __init__(self, ops, *, config, gradle_receipts):
                    seen["ops"] = ops
                    seen["orchestrator_config"] = config
                    seen["gradle_receipts"] = gradle_receipts

                def run(self, request):
                    seen["request"] = request
                    return FakeResult()

            with mock.patch("devtool.android.device_ops.AndroidDeviceOperations", FakeOps), mock.patch(
                "devtool.android.instrumentation.AndroidInstrumentationOrchestrator", FakeOrchestrator
            ), mock.patch(
                "devtool.android.instrumentation.gradle_receipts_from_current_run", return_value=()
            ), mock.patch(
                "devtool.android_instrumentation.run", side_effect=AssertionError("legacy raw command path executed")
            ):
                rc = android_instrumentation.main([str(root), "--skip-build"])

            self.assertEqual(rc, 0)
            self.assertIn("request", seen)
            self.assertIs(seen["config"], seen["orchestrator_config"])

    def test_canonical_compatibility_flag_is_not_required_for_direct_entrypoint(self) -> None:
        parser = android_instrumentation.build_parser()
        parsed = parser.parse_args(["."])
        self.assertFalse(parsed.canonical)
        source = Path(android_instrumentation.__file__).read_text(encoding="utf-8")
        main_source = source[source.index("def main("):source.index("\n\nif __name__ == \"__main__\":")]
        self.assertNotIn("if args.canonical:", main_source)
        self.assertNotIn("_resolve_execution_serial(", main_source)
        self.assertNotIn("_install(app_apk", main_source)
        self.assertNotIn("run(command, check=False)", main_source)

    def test_direct_entrypoint_failure_uses_orchestrator_failure_dimension(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            class FakeOps:
                @classmethod
                def from_config(cls, config):
                    return object()

            class FakeResult:
                status = "failed"
                reason = "device readiness failed"
                failed = 0
                returncode = 7
                ok = False

                def as_dict(self):
                    return {
                        "status": self.status, "reason": self.reason, "failed": 0,
                        "executed": 0, "returncode": self.returncode, "ok": False,
                    }

            class FakeOrchestrator:
                def __init__(self, *args, **kwargs):
                    pass

                def run(self, request):
                    return FakeResult()

            with mock.patch("devtool.android.device_ops.AndroidDeviceOperations", FakeOps), mock.patch(
                "devtool.android.instrumentation.AndroidInstrumentationOrchestrator", FakeOrchestrator
            ), mock.patch(
                "devtool.android.instrumentation.gradle_receipts_from_current_run", return_value=()
            ):
                rc = android_instrumentation.main([str(root), "--skip-build", "--policy", "required"])

            self.assertEqual(rc, 7)
