from __future__ import annotations

import unittest

from devtool.models import Issue, OperationResult
from devtool.repair_policy import build_repair_report, repair_summary_lines_from_report


class RepairPolicyTests(unittest.TestCase):
    def test_classifies_common_auto_fixable_diagnostics(self) -> None:
        results = [
            OperationResult(
                name="build",
                argv=["dotnet", "build"],
                returncode=1,
                duration_seconds=0.0,
                output="",
                issues=[
                    Issue("error", "build", "src/PathHelpers.cs", "CS1061", "string does not contain a definition for GetFullPath"),
                    Issue("error", "build", "src/Strings.cs", "CS0121", "The call is ambiguous between the following methods or properties: Split"),
                    Issue("error", "build", "src/Shadowing.cs", "CS0136", "A local or parameter named 'value' cannot be declared in this scope"),
                    Issue("error", "build", "src/Interpolation.cs", "CS8361", "A ternary operator in interpolation is ambiguous"),
                ],
            )
        ]

        report = build_repair_report(results)
        self.assertEqual(len(report.auto_fix), 4)
        self.assertIn("System.IO.Path.GetFullPath", report.auto_fix[0].fix)
        self.assertIn("Split", report.auto_fix[1].fix)
        self.assertIn("shadow", report.auto_fix[2].fix.lower())
        self.assertIn("interpol", report.auto_fix[3].fix.lower())

    def test_classifies_confirm_and_manual_buckets(self) -> None:
        results = [
            OperationResult(
                name="build",
                argv=["dotnet", "build"],
                returncode=1,
                duration_seconds=0.0,
                output="",
                issues=[
                    Issue("error", "build", "src/Missing.cs", "CS0246", "The type or namespace name 'RenamedType' could not be found"),
                    Issue("error", "build", "src/Deleted.cs", "CS0535", "The deleted interface has no obvious replacement."),
                    Issue("error", "build", "src/Ui.axaml", "AVLN2002", "Cannot assign to non-existent property 'Foo'"),
                ],
            )
        ]

        report = build_repair_report(results)
        self.assertGreaterEqual(len(report.confirm_fix), 1)
        self.assertGreaterEqual(len(report.manual_review), 1)
        lines = repair_summary_lines_from_report(report)
        self.assertIn("Repair summary:", lines[0])
        self.assertTrue(any("Needs confirmation" in line for line in lines))
        self.assertTrue(any("Manual review" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
