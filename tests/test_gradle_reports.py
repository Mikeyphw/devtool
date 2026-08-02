from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from devtool.gradle_reports import (
    collect_android_lint_reports,
    collect_gradle_test_reports,
    parse_android_lint_sarif,
    parse_android_lint_xml,
    parse_junit_reports,
)
from devtool.models import CommandSpec, Issue, ProjectConfig
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

    def test_deduplicates_android_lint_xml_and_sarif_for_same_finding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_dir = root / "app/build/reports"
            report_dir.mkdir(parents=True)
            (report_dir / "lint-results-debug.xml").write_text(
                """<?xml version='1.0' encoding='UTF-8'?>
<issues format='6' by='lint 8'>
  <issue id='OldTargetApi' severity='Error' message='Not targeting latest Android'>
    <location file='app/build.gradle.kts' line='14' column='9'/>
  </issue>
</issues>
""",
                encoding="utf-8",
            )
            (report_dir / "lint-results-debug.sarif").write_text(json.dumps({
                "runs": [{
                    "results": [{
                        "ruleId": "OldTargetApi",
                        "level": "error",
                        "message": {"text": "Not targeting the latest versions of Android; compatibility modes apply."},
                        "locations": [{"physicalLocation": {
                            "artifactLocation": {"uri": "app/build.gradle.kts"},
                            "region": {"startLine": 14},
                        }}],
                    }],
                }],
            }), encoding="utf-8")

            result = collect_android_lint_reports(root, tasks=["lintDebug"])

        self.assertEqual(result.metadata["errors"], 1)
        self.assertEqual(result.metadata["warnings"], 0)
        self.assertEqual(result.metadata["total"], 1)
        self.assertEqual(len(result.issues), 1)

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
            saved_log = Path(results[0].log_path).read_text(encoding="utf-8")

        self.assertEqual(results[0].returncode, 1)
        self.assertEqual(results[0].failure_kind, "test_failure")
        self.assertEqual(results[0].metadata["tests"]["total"], 3)
        self.assertIn("Devtool Gradle tests", results[0].output)
        self.assertIn("Devtool Gradle test failures", results[0].output)
        self.assertIn("demo.Tests.fails: expected true", results[0].output)
        self.assertIn("src/test/DemoTest.kt:42", results[0].output)
        self.assertIn("Devtool Gradle test failures", saved_log)
        self.assertIn("demo.Tests.fails: expected true", saved_log)

    def test_run_specs_classifies_memory_guard_as_resource_interruption(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "memory_guard.py"
            script.write_text(
                "print('warning: Emergency memory guard stopped Gradle after hard emergency floor: 165 MiB available; configured warning guard 256 MiB.')\n"
                "raise SystemExit(75)\n",
                encoding="utf-8",
            )
            config = ProjectConfig(repo_root=root, project_root=str(root), kind="android")
            spec = CommandSpec("android-validation-build", ["python3", str(script)], cwd=str(root))
            results, _ = run_specs(config, [spec], verbose=False, shutdown_dotnet=False)

        self.assertEqual(results[0].returncode, 75)
        self.assertEqual(results[0].issues[0].code, "GRADLE_MEMORY_GUARD")
        self.assertEqual(results[0].failure_kind, "resource_interrupted")

    def test_run_specs_classifies_gradle_daemon_disappearance_as_resource_interruption(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "daemon_disappeared.py"
            script.write_text(
                "print('Gradle build daemon disappeared unexpectedly (it may have been killed or may have crashed)')\n"
                "raise SystemExit(1)\n",
                encoding="utf-8",
            )
            config = ProjectConfig(repo_root=root, project_root=str(root), kind="android")
            spec = CommandSpec("android-validation-build", ["python3", str(script)], cwd=str(root))
            results, _ = run_specs(config, [spec], verbose=False, shutdown_dotnet=False)

        self.assertEqual(results[0].returncode, 1)
        self.assertEqual(results[0].issues[0].code, "GRADLE_DAEMON_DISAPPEARED")
        self.assertEqual(results[0].failure_kind, "resource_interrupted")

    def test_run_specs_keeps_successful_android_lint_report_errors_nonfatal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "app/build/reports/lint-results-debug.xml"
            script = root / "make_lint_report.py"
            script.write_text(
                "from pathlib import Path\n"
                f"p=Path({str(report)!r}); p.parent.mkdir(parents=True, exist_ok=True); p.write_text({LINT!r})\n",
                encoding="utf-8",
            )
            config = ProjectConfig(repo_root=root, project_root=str(root), kind="android")
            spec = CommandSpec(
                "build",
                ["python3", str(script)],
                cwd=str(root),
                metadata={
                    "project_root": str(root),
                    "gradle_tasks": ["lintDebug"],
                    "report_parser": "android-lint",
                },
            )
            results, raw = run_specs(config, [spec], verbose=False, shutdown_dotnet=False)

        self.assertEqual(results[0].returncode, 0)
        self.assertEqual(results[0].metadata["lint"]["errors"], 1)
        self.assertEqual(results[0].metadata["lint"]["warnings"], 1)
        self.assertEqual({issue.code for issue in results[0].issues}, {"NewApi", "ManifestTypo"})
        self.assertIn("Devtool Android Lint: 1 errors, 1 warnings.", results[0].output)
        self.assertNotIn("detected error diagnostics", raw)

    def test_chroot_built_apk_does_not_fail_on_host_verifier_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            apk = root / "app/build/outputs/apk/debug/app-debug.apk"
            apk.parent.mkdir(parents=True)
            import zipfile
            with zipfile.ZipFile(apk, "w") as archive:
                archive.writestr("AndroidManifest.xml", b"manifest")
                archive.writestr("classes.dex", b"dex")
            script = root / "success.py"
            script.write_text("print('BUILD SUCCESSFUL')\n", encoding="utf-8")
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="android",
                execution_environment="chroot",
            )
            spec = CommandSpec(
                "build",
                ["python3", str(script)],
                cwd=str(root),
                metadata={
                    "project_root": str(root),
                    "gradle_tasks": ["assembleDebug"],
                    "collect_artifacts": True,
                    "verify_artifacts": True,
                },
            )
            with patch("devtool.android_artifacts._tool_from_sdk") as tool_lookup:
                results, raw = run_specs(
                    config, [spec], verbose=False, shutdown_dotnet=False
                )

        self.assertEqual(results[0].returncode, 0)
        self.assertIn("Devtool artifacts: 1 valid, 0 invalid, 1 total.", raw)
        self.assertNotIn("detected error diagnostics", raw)
        tool_lookup.assert_not_called()

    def test_run_specs_prints_android_artifact_failure_details(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "success.py"
            script.write_text("print('BUILD SUCCESSFUL')\n", encoding="utf-8")
            config = ProjectConfig(repo_root=root, project_root=str(root), kind="android")
            spec = CommandSpec(
                "build",
                ["python3", str(script)],
                cwd=str(root),
                metadata={
                    "project_root": str(root),
                    "gradle_tasks": ["assembleDebug"],
                    "collect_artifacts": True,
                    "verify_artifacts": True,
                },
            )
            metadata = {"valid": 0, "invalid": 1, "count": 1, "items": []}
            artifact_issue = Issue(
                "error",
                "artifact-verify",
                "app/build/outputs/apk/debug/app-debug.apk",
                "SIGNATURE",
                "verification tool failed",
            )
            with patch(
                "devtool.android_artifacts.collect_and_verify_android_artifacts",
                return_value=(metadata, [artifact_issue]),
            ):
                results, raw = run_specs(
                    config, [spec], verbose=False, shutdown_dotnet=False
                )

        self.assertEqual(results[0].returncode, 1)
        self.assertIn("Devtool artifact verification failures:", results[0].output)
        self.assertIn("[SIGNATURE]: verification tool failed", results[0].output)
        self.assertIn("detected error diagnostics", raw)


if __name__ == "__main__":
    unittest.main()
