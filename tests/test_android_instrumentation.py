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
        self.assertEqual(counts["passed"], 0)


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
            spec = build_specs(config, "test")[0]

        self.assertEqual(spec.name, "instrumentation")
        self.assertIn("devtool.android_instrumentation", spec.argv)
        self.assertIn("serial-1", spec.argv)
        self.assertIn("demo.UiTest", spec.argv)
        self.assertIn("sudo am instrument", spec.argv)
        self.assertIn("--aapt2-provider", spec.argv)
        self.assertIn("termux", spec.argv)
        self.assertIn("--gradle-arg=-Dorg.gradle.parallel=true", spec.argv)
        self.assertIn("--gradle-arg=-Pdemo.flag=true", spec.argv)

        # Prove the exact generated argv is accepted by the instrumentation CLI.
        parser = android_instrumentation.build_parser()
        parsed = parser.parse_args(spec.argv[3:])
        self.assertEqual(
            parsed.gradle_arg,
            ["-Dorg.gradle.parallel=true", "-Pdemo.flag=true"],
        )
        self.assertEqual(spec.metadata["report_parser"], "android-instrumentation")

    def test_instrumentation_build_injects_native_aapt2_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gradlew = root / "gradlew"
            gradlew.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            gradlew.chmod(0o755)
            app = root / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
            test = root / "app" / "build" / "outputs" / "apk" / "androidTest" / "debug" / "app-debug-androidTest.apk"
            app.parent.mkdir(parents=True)
            test.parent.mkdir(parents=True)
            app.write_bytes(b"app")
            test.write_bytes(b"test")
            calls: list[list[str]] = []

            def fake_run(argv: list[str], *, check: bool = True):
                calls.append(list(argv))
                if "list" in argv and "instrumentation" in argv:
                    return mock.Mock(returncode=0, stdout="instrumentation:demo.test/Runner (target=demo)\n", stderr="")
                if "instrument" in argv:
                    return mock.Mock(
                        returncode=0,
                        stdout="INSTRUMENTATION_STATUS: numtests=1\nINSTRUMENTATION_STATUS_CODE: 1\nINSTRUMENTATION_STATUS_CODE: 0\n",
                        stderr="",
                    )
                return mock.Mock(returncode=0, stdout="", stderr="")

            aapt2 = Path("/data/data/com.termux/files/usr/bin/aapt2")
            with mock.patch("devtool.android_instrumentation._instrumentation_aapt2", return_value=aapt2), mock.patch(
                "devtool.android_instrumentation._resolve_execution_serial", return_value=""
            ), mock.patch(
                "devtool.android_instrumentation._resolve_local_command", side_effect=lambda argv: list(argv)
            ), mock.patch(
                "devtool.android_instrumentation.run", side_effect=fake_run
            ):
                rc = android_instrumentation.main([str(root)])

            self.assertEqual(rc, 0)
            build_command = calls[0]
            self.assertIn(f"-Pandroid.aapt2FromMavenOverride={aapt2}", build_command)
            self.assertIn(
                "-Pandroid.sync.suppressAgpWarnings=UNSUPPORTED_PROJECT_OPTION_USE",
                build_command,
            )
            self.assertEqual(sum(item.startswith("--console=") for item in build_command), 1)

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

            with mock.patch(
                "devtool.android_instrumentation._resolve_execution_serial", return_value=""
            ), mock.patch(
                "devtool.android_instrumentation._resolve_local_command", side_effect=lambda argv: list(argv)
            ), mock.patch("devtool.android_instrumentation.run", side_effect=fake_run):
                rc = android_instrumentation.main([str(root), "--skip-build", "--report", str(report)])

            self.assertEqual(rc, 1)
            self.assertTrue(report.is_file())
            self.assertTrue(any(call[-2:] == ["uninstall", "demo.test"] for call in calls))

    def test_instrumentation_error_writes_structured_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "instrumentation.json"
            with mock.patch("devtool.android_instrumentation._resolve_execution_serial", return_value=""):
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

            with mock.patch(
                "devtool.android_instrumentation._resolve_execution_serial", return_value=""
            ), mock.patch(
                "devtool.android_instrumentation._resolve_local_command", side_effect=lambda argv: list(argv)
            ), mock.patch("devtool.android_instrumentation.run", side_effect=fake_run):
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
