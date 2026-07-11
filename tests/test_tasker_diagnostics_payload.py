from __future__ import annotations

import unittest

from devtool.models import Issue, OperationResult
from devtool.tasker import _completion_payload


class TaskerDiagnosticsPayloadTests(unittest.TestCase):
    def test_completion_contains_deduplicated_diagnostics(self) -> None:
        issue = Issue("error", "dotnet build", "src/App.cs", "CS1001", "Identifier expected", 12)
        result = OperationResult("build", ["dotnet", "build"], 1, 1.0, "", [issue, issue])
        payload = _completion_payload(ok=False, totals={"errors": 1, "warnings": 0, "failed": 1}, results=[result])
        self.assertEqual(len(payload["diagnostics"]), 1)
        self.assertEqual(payload["diagnostics"][0]["code"], "CS1001")
        self.assertEqual(payload["diagnostics"][0]["phase"], "build")
