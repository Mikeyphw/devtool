from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from devtool.diagnostics import parse_issues


class DiagnosticTests(unittest.TestCase):
    def test_parse_dotnet_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            file_path = root / "src" / "App" / "Main.cs"
            file_path.parent.mkdir(parents=True)
            file_path.write_text("class X {}", encoding="utf-8")

            output = f"{file_path}(10,5): error CS1002: ; expected [{root / 'App.csproj'}]\n"
            issues = parse_issues(root, "build", output)
            self.assertEqual(len(issues), 1)
            self.assertEqual(issues[0].severity, "error")
            self.assertEqual(issues[0].file, "src/App/Main.cs")
            self.assertEqual(issues[0].line, 10)

    def test_parse_pytest_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = "FAILED tests/test_app.py::test_x - assert 1 == 2\n"
            issues = parse_issues(root, "test", output)
            self.assertEqual(len(issues), 1)
            self.assertEqual(issues[0].code, "PYTEST")

    def test_parse_flake8_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = "pkg/app.py:10:2: F401 unused import foo\n"
            issues = parse_issues(root, "test", output)
            self.assertEqual(issues[0].file, "pkg/app.py")
            self.assertEqual(issues[0].code, "F401")

    def test_parse_gcc_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = "src/main.c:12:3: error: boom\n"
            issues = parse_issues(root, "build", output)
            self.assertEqual(issues[0].code, "CC")

    def test_parse_kotlin_android_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "app/src/main/java/demo/Main.kt"
            source.parent.mkdir(parents=True)
            source.write_text("fun main() {}", encoding="utf-8")
            output = f"e: file://{source}:12:5 Unresolved reference: missing\n"
            issues = parse_issues(root, "build", output)

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "KOTLIN")
        self.assertEqual(issues[0].file, "app/src/main/java/demo/Main.kt")
        self.assertEqual(issues[0].line, 12)

    def test_parse_aapt2_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            resource = root / "app/src/main/res/layout/main.xml"
            resource.parent.mkdir(parents=True)
            resource.write_text("<bad>", encoding="utf-8")
            output = f"ERROR: {resource}:7:3: AAPT: error: resource missing.\n"
            issues = parse_issues(root, "build", output)

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "ANDROID")
        self.assertEqual(issues[0].line, 7)

    def test_parse_gradle_metaspace_and_failed_task_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = (
                "The Daemon will expire after the build after running out of JVM Metaspace.\n"
                "> Task :platform:compileDebugKotlin FAILED\n"
            )
            issues = parse_issues(root, "build", output)

        by_code = {issue.code: issue for issue in issues}
        self.assertEqual(by_code["GRADLE_METASPACE"].severity, "warning")
        self.assertIn("metaspace", by_code["GRADLE_METASPACE"].message.lower())
        self.assertEqual(by_code["GRADLE_TASK"].severity, "error")
        self.assertIn(":platform:compileDebugKotlin", by_code["GRADLE_TASK"].message)

    def test_parse_gradle_memory_guard_as_fatal_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = (
                "warning: Emergency memory guard stopped Gradle after hard emergency floor: "
                "165 MiB available; configured warning guard 512 MiB.\n"
            )
            issues = parse_issues(root, "build", output)

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, "error")
        self.assertEqual(issues[0].code, "GRADLE_MEMORY_GUARD")
        self.assertIn("165 MiB available", issues[0].message)

    def test_parse_gradle_toolchain_and_aar_metadata_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = (
                "Could not determine the dependencies of task ':app:compileDebugJavaWithJavac'.\n"
                "> Could not resolve all dependencies for configuration ':app:debugCompileClasspath'.\n"
                "   > Could not resolve project :core-model.\n"
                "      > Failed to calculate the value of task ':core-model:compileJava' property 'javaCompiler'.\n"
                "         > Cannot find a Java installation on your machine (Linux amd64) matching: {languageVersion=17, vendor=any vendor, implementation=vendor-specific, nativeImageCapable=false}. Toolchain download repositories have not been configured.\n"
                "Dependency 'androidx.core:core-ktx:1.19.0' requires libraries and applications that depend on it to compile against version 37 or later of the Android APIs.\n"
                ":app is currently compiled against android-36.\n"
            )
            issues = parse_issues(root, "build", output)

        codes = [issue.code for issue in issues]
        self.assertIn("JAVA_TOOLCHAIN", codes)
        self.assertIn("AAR_METADATA", codes)
        self.assertTrue(any("Java 17 toolchain" in issue.message for issue in issues))
        self.assertTrue(any("compileSdk 37" in issue.message for issue in issues))

    def test_parse_manifest_merger_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "app/src/main/AndroidManifest.xml"
            manifest.parent.mkdir(parents=True)
            manifest.write_text("<manifest/>", encoding="utf-8")
            output = f"{manifest}:4:9-27 Error: Attribute application@name is invalid\n"
            issues = parse_issues(root, "build", output)

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "MANIFEST")
        self.assertEqual(issues[0].line, 4)

    def test_parse_cargo_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = "error[E0425]: cannot find value `x` in this scope\n  --> src/main.rs:2:5\n"
            issues = parse_issues(root, "build", output)
            self.assertEqual(issues[0].file, "src/main.rs")


    def test_parse_xunit_failure_uses_assertion_message_and_stack_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            test_file = root / "tests" / "S3oc.Tests" / "MainWindowViewModelTests.cs"
            test_file.parent.mkdir(parents=True)
            test_file.write_text("// test", encoding="utf-8")

            output = """[xUnit.net 00:00:00.97]     S3oc.Core.Tests.MainWindowViewModelTests.TgiSearchStep_FiltersIndexedContentByTypeGroupAndInstance [FAIL]
  Failed S3oc.Core.Tests.MainWindowViewModelTests.TgiSearchStep_FiltersIndexedContentByTypeGroupAndInstance [3 ms]
  Error Message:
   Assert.Single() Failure: The collection was empty
  Stack Trace:
     at S3oc.Core.Tests.MainWindowViewModelTests.TgiSearchStep_FiltersIndexedContentByTypeGroupAndInstance() in {test_file}:line 2630
--- End of stack trace from previous location ---

Failed!  - Failed:     1, Passed:   113, Skipped:     0, Total:   114, Duration: 847 ms - S3oc.Tests.dll (net8.0)
""".format(test_file=test_file)

            issues = parse_issues(root, "test", output)
            self.assertEqual(len(issues), 1)
            self.assertEqual(issues[0].code, "XUNIT")
            self.assertEqual(issues[0].message, "Assert.Single() Failure: The collection was empty")
            self.assertEqual(issues[0].file, "tests/S3oc.Tests/MainWindowViewModelTests.cs")
            self.assertEqual(issues[0].line, 2630)

    def test_parse_failed_vstest_summary_without_detail_as_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = "Failed! - Failed: 1, Passed: 75, Skipped: 0, Total: 76, Duration: 9 s - App.Tests.dll (net8.0)\n"
            issues = parse_issues(root, "test", output)
            self.assertEqual(len(issues), 1)
            self.assertEqual(issues[0].severity, "error")
            self.assertEqual(issues[0].code, "VSTEST")
            self.assertIn("1 failed", issues[0].message)

    def test_parse_passing_vstest_summary_is_not_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = "Passed! - Failed: 0, Passed: 11, Skipped: 0, Total: 11, Duration: 1 s - App.Tests.dll (net8.0)\n"
            issues = parse_issues(root, "test", output)
            self.assertEqual(issues, [])
