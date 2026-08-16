from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from devtool import android_instrumentation
from devtool.android_instrumentation import _instrument_command, _local_pm_prefix, _parse_counts
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
        self.assertEqual(counts["passed"], 0)


    def test_rooted_termux_commands_reuse_configured_privilege_prefix(self) -> None:
        self.assertEqual(_local_pm_prefix("sudo pm install -r -t"), ["sudo", "pm"])
        self.assertEqual(
            _instrument_command("", am_command="sudo am instrument"),
            ["sudo", "am", "instrument"],
        )

    def test_android_runner_builds_device_instrumentation_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="android",
                device_serial="serial-1",
                runner_options={
                    "android": {"variant": "release", "test_runner": "demo.test/Runner"},
                    "test_selection": {"instrumented": True, "classes": ["demo.UiTest"]},
                },
            )
            spec = build_specs(config, "test")[0]

        self.assertEqual(spec.name, "instrumentation")
        self.assertIn("devtool.android_instrumentation", spec.argv)
        self.assertIn("serial-1", spec.argv)
        self.assertIn("demo.UiTest", spec.argv)
        self.assertIn("sudo am instrument", spec.argv)
        self.assertEqual(spec.metadata["report_parser"], "android-instrumentation")

    def test_failed_instrumentation_uninstalls_test_package_and_writes_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
            test = root / "app" / "build" / "outputs" / "apk" / "androidTest" / "debug" / "app-debug-androidTest.apk"
            app.parent.mkdir(parents=True)
            test.parent.mkdir(parents=True)
            app.write_bytes(b"app")
            test.write_bytes(b"test")
            report = root / "instrumentation.json"
            calls: list[list[str]] = []

            def fake_run(argv: list[str], *, check: bool = True):
                calls.append(list(argv))
                if "list" in argv and "instrumentation" in argv:
                    return mock.Mock(returncode=0, stdout="instrumentation:demo.test/Runner (target=demo)\n", stderr="")
                if "instrument" in argv:
                    return mock.Mock(
                        returncode=1,
                        stdout=(
                            "INSTRUMENTATION_STATUS: numtests=1\n"
                            "INSTRUMENTATION_STATUS: class=demo.UiTest\n"
                            "INSTRUMENTATION_STATUS: test=fails\n"
                            "INSTRUMENTATION_STATUS_CODE: -2\n"
                        ),
                        stderr="",
                    )
                return mock.Mock(returncode=0, stdout="", stderr="")

            with mock.patch("devtool.android_instrumentation.run", side_effect=fake_run):
                rc = android_instrumentation.main([str(root), "--skip-build", "--report", str(report)])

            self.assertEqual(rc, 1)
            self.assertTrue(report.is_file())
            self.assertTrue(any(call[-2:] == ["uninstall", "demo.test"] for call in calls))

    def test_instrumentation_error_writes_structured_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "instrumentation.json"
            rc = android_instrumentation.main([str(root), "--skip-build", "--report", str(report)])

            self.assertEqual(rc, 1)
            self.assertTrue(report.is_file())
            self.assertIn('"error"', report.read_text(encoding="utf-8"))

    def test_configured_runner_cleanup_runs_when_setup_fails_after_install(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
            test = root / "app" / "build" / "outputs" / "apk" / "androidTest" / "debug" / "app-debug-androidTest.apk"
            app.parent.mkdir(parents=True)
            test.parent.mkdir(parents=True)
            app.write_bytes(b"app")
            test.write_bytes(b"test")
            report = root / "instrumentation.json"
            calls: list[list[str]] = []

            def fake_run(argv: list[str], *, check: bool = True):
                calls.append(list(argv))
                if argv and str(argv[-1]).endswith("app-debug-androidTest.apk"):
                    raise android_instrumentation.InstrumentationError("test install failed")
                return mock.Mock(returncode=0, stdout="", stderr="")

            with mock.patch("devtool.android_instrumentation.run", side_effect=fake_run):
                rc = android_instrumentation.main([
                    str(root),
                    "--skip-build",
                    "--report",
                    str(report),
                    "--runner",
                    "demo.test/Runner",
                ])

            self.assertEqual(rc, 1)
            self.assertTrue(report.is_file())
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(payload["test_package"], "demo.test")
            self.assertTrue(any(call[-2:] == ["uninstall", "demo.test"] for call in calls))
