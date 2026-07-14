from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from devtool.gradle_reports import (
    collect_android_lint_reports,
    collect_gradle_test_reports,
    parse_android_lint_sarif,
    parse_android_lint_xml,
    parse_junit_reports,
)
from devtool.models import CommandSpec, ProjectConfig
from devtool.runners import run_specs


JUNIT = """<?xml version='1.0' encoding='UTF-8'?>
<testsuite name='demo.Tests' tests='3' failures='1' errors='0' skipped='1' time='0.75'>
  <testcase name='passes' classname='demo.Tests' time='0.10'/>
  <testcase name='fails' classname='demo.Tests' file='src/test/DemoTest.kt' line='42' time='0.20'>
    <failure message='expected true'>stack trace</failure>
  </testcase>
  <testcase name='skips' classname='demo.Tests' time='0.05'><skipped/></testcase>
</testsuite>
"""

LINT = """<?xml version='1.0' encoding='UTF-8'?>
<issues format='6' by='lint 8'>
  <issue id='NewApi' severity='Warning' message='Requires API 34'>
    <location file='app/src/main/java/demo/Main.kt' line='12' column='5'/>
  </issue>
  <issue id='ManifestTypo' severity='Error' message='Invalid manifest value'>
    <location file='app/src/main/AndroidManifest.xml' line='3' column='2'/>
  </issue>
</issues>
"""


class GradleReportTests(unittest.TestCase):
    def test_parses_junit_xml_as_authoritative_totals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "app/build/test-results/testDebugUnitTest/TEST-demo.xml"
            report.parent.mkdir(parents=True)
            report.write_text(JUNIT, encoding="utf-8")
            result = collect_gradle_test_reports(root, tasks=["testDebugUnitTest"])

        self.assertEqual(result.metadata["total"], 3)
        self.assertEqual(result.metadata["passed"], 1)
        self.assertEqual(result.metadata["failed"], 1)
        self.assertEqual(result.metadata["skipped"], 1)
        self.assertEqual(len(result.issues), 1)
        self.assertEqual(result.issues[0].code, "GRADLE_TEST")
        self.assertEqual(result.issues[0].line, 42)

    def test_parses_android_lint_xml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "app/build/reports/lint-results-debug.xml"
            report.parent.mkdir(parents=True)
            report.write_text(LINT, encoding="utf-8")
            result = collect_android_lint_reports(root, tasks=["lintDebug"])

        self.assertEqual(result.metadata["warnings"], 1)
        self.assertEqual(result.metadata["errors"], 1)
        self.assertEqual({issue.code for issue in result.issues}, {"NewApi", "ManifestTypo"})

    def test_parses_android_lint_sarif(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "lint.sarif"
            report.write_text(json.dumps({
                "runs": [{
                    "results": [{
                        "ruleId": "UnusedResources",
                        "level": "warning",
                        "message": {"text": "Resource is unused"},
                        "locations": [{"physicalLocation": {
                            "artifactLocation": {"uri": "app/src/main/res/values/strings.xml"},
                            "region": {"startLine": 8},
                        }}],
                    }],
                }],
            }), encoding="utf-8")
            issues = parse_android_lint_sarif(root, report)

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "UnusedResources")
        self.assertEqual(issues[0].line, 8)

    def test_run_specs_attaches_junit_metadata_and_fails_on_report_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "app/build/test-results/testDebugUnitTest/TEST-demo.xml"
            script = root / "make_report.py"
            script.write_text(
                "from pathlib import Path\n"
                f"p=Path({str(report)!r}); p.parent.mkdir(parents=True, exist_ok=True); p.write_text({JUNIT!r})\n",
                encoding="utf-8",
            )
            config = ProjectConfig(repo_root=root, project_root=str(root), kind="android")
            spec = CommandSpec(
                "test",
                ["python3", str(script)],
                cwd=str(root),
                metadata={
                    "project_root": str(root),
                    "gradle_tasks": ["testDebugUnitTest"],
                    "report_parser": "gradle-junit",
                },
            )
            results, _ = run_specs(config, [spec], verbose=False, shutdown_dotnet=False)

        self.assertEqual(results[0].returncode, 1)
        self.assertEqual(results[0].metadata["tests"]["total"], 3)
        self.assertIn("Devtool Gradle tests", results[0].output)


if __name__ == "__main__":
    unittest.main()
