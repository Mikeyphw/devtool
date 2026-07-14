from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

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
