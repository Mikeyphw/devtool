from __future__ import annotations

import unittest
from io import StringIO

from devtool.models import Issue, OperationResult
from devtool.summary import build_json_summary, build_runner_json, render_artifact_manifest, render_status_table, render_summary
from rich.console import Console


class SummaryRenderingTests(unittest.TestCase):
    def test_render_summary_compact_escapes_bracketed_paths(self) -> None:
        stream = StringIO()
        console = Console(file=stream, force_terminal=False, width=40)
        results = [
            OperationResult(
                name="build [/data/data/com.termux/files/home/Code/sims3tools/s3pi_base/S3pi.Base.sln]",
                argv=["dotnet", "build"],
                returncode=1,
                duration_seconds=0.1,
                output="",
                issues=[
                    Issue(
                        severity="error",
                        command="build [/data/data/com.termux/files/home/Code/sims3tools/s3pi_base/S3pi.Base.sln]",
                        file="/data/data/com.termux/files/home/Code/sims3tools/s3pi_base/S3pi.Base.sln",
                        line=7,
                        code="MSB1001",
                        message="bad [path]",
                    )
                ],
            )
        ]

        render_summary(results, console_obj=console)
        output = stream.getvalue()
        self.assertIn("[/data", output)
        self.assertIn("MSB10", output)
        self.assertIn("[pat", output)

    def test_render_status_table_compact_escapes_bracketed_values(self) -> None:
        stream = StringIO()
        console = Console(file=stream, force_terminal=False, width=40)
        render_status_table(
            [("solution", "/data/data/com.termux/files/home/Code/[repo]/App.sln")],
            console_obj=console,
        )
        output = stream.getvalue()
        self.assertIn("[repo]", output)
        self.assertIn("solution", output)



    def test_render_artifact_manifest_uses_semantic_sections(self) -> None:
        stream = StringIO()
        console = Console(file=stream, force_terminal=False, width=80)
        hidden_hash = "a" * 64
        render_artifact_manifest(
            {
                "type": "overlay",
                "repo_name": "sims3tools",
                "target": "s3ow_avalonia_TextureTweaker3_Modern",
                "inventory": {
                    "entries": [
                        {"path": "a.cs", "action": "add", "sha256": hidden_hash, "size": 42},
                        {"path": "b.cs", "action": "modify", "source_sha256": "b" * 64},
                    ]
                },
                "validation": {
                    "required": True,
                    "dotnet_available_in_authoring_environment": False,
                    "recommended_commands": ["dotnet test App.sln --no-build"],
                },
            },
            console_obj=console,
        )
        output = stream.getvalue()
        self.assertIn("Artifact summary", output)
        self.assertIn("Artifact contents", output)
        self.assertIn("Change", output)
        self.assertIn("Repository path", output)
        self.assertIn("Execution contract", output)
        self.assertIn("Validation reason", output)
        self.assertIn("auto-enabled by artifact manifest", output)
        self.assertIn("Activation", output)
        self.assertIn("auto-enabled unless", output)
        self.assertIn("--no-validate is", output)
        self.assertIn("a.cs", output)
        self.assertIn("b.cs", output)
        self.assertIn("Recommended Commands", output)
        self.assertIn("dotnet test App.sln", output)
        self.assertIn("--no-build", output)
        self.assertNotIn("sha256", output.lower())
        self.assertNotIn(hidden_hash, output)
        self.assertNotIn("[#]", output)

    def test_render_bundle_manifest_uses_named_rows_without_index_nodes_or_hashes(self) -> None:
        stream = StringIO()
        console = Console(file=stream, force_terminal=False, width=100)
        render_artifact_manifest(
            {
                "type": "overlay_bundle",
                "name": "Modernization bundle",
                "overlays": [
                    {"id": "core", "artifact": "overlays/core.zip", "target": "core", "sha256": "c" * 64},
                    {"id": "ui", "artifact": "overlays/ui.zip", "target": "ui", "after": ["core"]},
                ],
                "inventory": [
                    {"path": "src/Core.cs", "action": "modify", "sha256": "d" * 64},
                ],
            },
            console_obj=console,
        )
        output = stream.getvalue()
        self.assertIn("Bundle composition", output)
        self.assertIn("core", output)
        self.assertIn("ui", output)
        self.assertIn("src/Core.cs", output)
        self.assertNotIn("sha256", output.lower())
        self.assertNotIn("[#]", output)

    def test_build_runner_json_uses_concise_notification_format(self) -> None:
        result = OperationResult(
            name="build",
            argv=["dotnet", "build"],
            returncode=0,
            duration_seconds=0.1,
            output="",
            issues=[Issue(severity="warning", command="build", file="Program.cs", code="CS8604", message="nullable")],
        )

        data = build_runner_json(
            [result],
            repo_root="/tmp/sims3tools",
            command="validate",
            raw_output="",
            summary_text="",
        )

        self.assertEqual(data["notification"]["title"], "⚠️ WARN")
        self.assertEqual(data["notification"]["body"], "⚠️ WARN: sims3tools: validate • 0E 1W")

    def test_render_summary_separates_overlay_groups(self) -> None:
        stream = StringIO()
        console = Console(file=stream, force_terminal=False, width=100)
        first_meta = {"overlay_index": 1, "overlay_total": 2, "overlay_label": "Core", "overlay_target": "core"}
        second_meta = {"overlay_index": 2, "overlay_total": 2, "overlay_label": "UI", "overlay_target": "ui"}
        results = [
            OperationResult("apply-overlay", ["apply"], 0, 0.1, "ok", [], metadata=first_meta),
            OperationResult("test", ["test"], 0, 0.2, "Passed! - Failed: 0, Passed: 4, Skipped: 0, Total: 4", [], metadata=first_meta),
            OperationResult("git-commit", ["git"], 0, 0.1, "[main abc1234] Core", [], metadata=first_meta),
            OperationResult("apply-overlay", ["apply"], 0, 0.1, "ok", [], metadata=second_meta),
            OperationResult("test", ["test"], 0, 0.2, "Passed! - Failed: 0, Passed: 7, Skipped: 1, Total: 8", [], metadata=second_meta),
            OperationResult("git-commit", ["git"], 0, 0.1, "[main def5678] UI", [], metadata=second_meta),
        ]
        render_summary(results, console_obj=console)
        output = stream.getvalue()
        self.assertIn("Overlay 1/2: Core", output)
        self.assertIn("Overlay 2/2: UI", output)
        self.assertIn("4 passed", output)
        self.assertIn("7 passed", output)
        self.assertIn("abc1234", output)
        self.assertIn("def5678", output)

    def test_render_summary_shows_repair_summary_for_fixable_failures(self) -> None:
        stream = StringIO()
        console = Console(file=stream, force_terminal=False, width=90)
        results = [
            OperationResult(
                name="build",
                argv=["dotnet", "build"],
                returncode=1,
                duration_seconds=0.1,
                output="",
                issues=[Issue("error", "build", "src/PathHelpers.cs", "CS1061", "string does not contain a definition for GetFullPath")],
            )
        ]

        render_summary(results, console_obj=console)
        output = stream.getvalue()
        self.assertIn("Repair summary", output)
        self.assertIn("Auto-fix candidates", output)
        self.assertIn("System.IO.Path.GetFullPath", output)

    def test_render_summary_explains_coalesced_dotnet_validation(self) -> None:
        stream = StringIO()
        console = Console(file=stream, force_terminal=False, width=120)
        result = OperationResult(
            name="dotnet-validation",
            argv=["dotnet", "test", "Demo.sln"],
            returncode=0,
            duration_seconds=1.0,
            output="Passed! - Failed: 0, Passed: 4, Skipped: 0, Total: 4",
            issues=[],
            metadata={
                "coalesced_dotnet_validation": True,
                "validation_components": ["restore", "build", "test"],
            },
        )

        render_summary([result], console_obj=console)
        output = stream.getvalue()

        self.assertIn(".NET validation (restore + build", output)
        self.assertIn("test via dotnet test)", output)

    def test_build_json_summary_includes_repairs(self) -> None:
        result = OperationResult(
            name="build",
            argv=["dotnet", "build"],
            returncode=1,
            duration_seconds=0.1,
            output="",
            issues=[Issue("error", "build", "src/Strings.cs", "CS0121", "The call is ambiguous between the following methods or properties: Split")],
        )

        data = build_json_summary([result], repo_root="/tmp/sims3tools")
        self.assertIn("repairs", data)
        self.assertIn("auto_fix", data["repairs"])
        self.assertGreaterEqual(len(data["repairs"]["auto_fix"]), 1)

    def test_render_summary_shows_applied_repair_details(self) -> None:
        stream = StringIO()
        console = Console(file=stream, force_terminal=False, width=90)
        results = [
            OperationResult(
                name="auto-repair",
                argv=["devtool", "repair"],
                returncode=0,
                duration_seconds=0.1,
                output="Auto repair pass:\n- Applied 1 safe fix.\n  - src/Program.cs:12 CS1061: Replace `string.GetFullPath(...)` with `System.IO.Path.GetFullPath(...)`.\n- Validation failures: 1 -> 0",
                issues=[],
                metadata={
                    "applied_repairs": [
                        {
                            "bucket": "applied",
                            "code": "CS1061",
                            "file": "src/Program.cs",
                            "line": 12,
                            "message": "string does not contain a definition for GetFullPath",
                            "fix": "Replace `string.GetFullPath(...)` with `System.IO.Path.GetFullPath(...)`.",
                        }
                    ],
                    "restored": False,
                    "before_failures": 1,
                    "after_failures": 0,
                },
            ),
            OperationResult(
                name="build",
                argv=["dotnet", "build"],
                returncode=0,
                duration_seconds=0.2,
                output="ok",
                issues=[],
            ),
        ]

        render_summary(results, console_obj=console)
        output = stream.getvalue()
        self.assertIn("Applied repairs", output)
        self.assertIn("System.IO.Path.GetFullPath", output)



if __name__ == "__main__":
    unittest.main()

class FailureDetailRenderingTests(unittest.TestCase):
    def test_render_summary_shows_failed_output_details(self) -> None:
        stream = StringIO()
        console = Console(file=stream, force_terminal=False, width=80)
        result = OperationResult(
            name="artifact-check",
            argv=["apply-overlay", "bad.zip"],
            returncode=1,
            duration_seconds=0.0,
            output="Artifact check failed before apply.\nerror_type: RuntimeError\nerror: boom",
            issues=[],
            failure_kind="artifact_failure",
        )
        render_summary([result], console_obj=console)
        output = stream.getvalue()
        self.assertIn("Failure details", output)
        self.assertIn("RuntimeError", output)
        self.assertIn("boom", output)

    def test_render_summary_shows_primary_root_cause_before_artifact_missing(self) -> None:
        stream = StringIO()
        console = Console(file=stream, force_terminal=False, width=100)
        result = OperationResult(
            name="build",
            argv=["gradlew", "assembleDebug"],
            returncode=75,
            duration_seconds=0.0,
            output=(
                "warning: Emergency memory guard stopped Gradle after hard emergency floor: "
                "165 MiB available.\n"
                "Devtool artifact verification failures:\n"
                "- android [ARTIFACT_MISSING]: Build completed without producing a new standard Android/Gradle artifact.\n"
            ),
            issues=[
                Issue(
                    severity="error",
                    command="build",
                    file="-",
                    code="GRADLE_MEMORY_GUARD",
                    message="Gradle was stopped by devtool memory guard after hard emergency floor: 165 MiB available.",
                ),
                Issue(
                    severity="error",
                    command="artifact-verify",
                    file="android",
                    code="ARTIFACT_MISSING",
                    message="Build completed without producing a new standard Android/Gradle artifact.",
                ),
            ],
            failure_kind="compile_failure",
        )
        render_summary([result], console_obj=console)
        output = stream.getvalue()

        self.assertIn("root cause", output)
        self.assertIn("GRADLE_MEMORY_GUARD", output)
        self.assertIn("165 MiB available", output)

    def test_build_json_summary_includes_output(self) -> None:
        result = OperationResult(
            name="artifact-check",
            argv=["apply-overlay", "bad.zip"],
            returncode=1,
            duration_seconds=0.0,
            output='{"error":"boom"}',
            issues=[],
            failure_kind="artifact_failure",
        )
        data = build_json_summary([result], repo_root="/tmp/repo")
        self.assertEqual(data["results"][0]["output"], '{"error":"boom"}')
