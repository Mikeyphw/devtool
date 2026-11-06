"""RM22 source-only regression: preserve the hosted runner OS for native child Gradle.

No Gradle process, network or code from a consumer executes here.
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from devtool.actions_rm21_hosted_native import _child_env


class HostedChildEnvironment(unittest.TestCase):
    def test_linux_runner_os_forwarded_without_sensitive_values(self):
        with patch.dict(os.environ, {
            "RUNNER_OS": "Linux", "GITHUB_TOKEN": "must-not-leak",
            "GH_TOKEN": "must-not-leak", "PYTHONPATH": "/secret",
            "STUDYFLOW_SIGNING_PASSWORD": "must-not-leak",
        }):
            child = _child_env()
        self.assertEqual(child.get("RUNNER_OS"), "Linux")
        self.assertEqual(child["CI"], "true")
        self.assertEqual(child["GITHUB_ACTIONS"], "true")
        for forbidden in ("GITHUB_TOKEN", "GH_TOKEN", "PYTHONPATH", "STUDYFLOW_SIGNING_PASSWORD"):
            self.assertNotIn(forbidden, child)

    def test_unknown_os_not_forwarded(self):
        with patch.dict(os.environ, {"RUNNER_OS": "Linux;curl example.invalid"}):
            self.assertNotIn("RUNNER_OS", _child_env())

    def test_absent_os_is_not_fabricated(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertNotIn("RUNNER_OS", _child_env())

    def test_standard_runners_forwarded(self):
        for name in ("Linux", "Windows", "macOS"):
            with self.subTest(name=name), patch.dict(os.environ, {"RUNNER_OS": name}):
                self.assertEqual(_child_env().get("RUNNER_OS"), name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
