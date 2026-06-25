from __future__ import annotations

import unittest
from io import StringIO

from repo_devtool.models import Issue, OperationResult
from repo_devtool.summary import build_runner_json, render_status_table, render_summary
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


if __name__ == "__main__":
    unittest.main()
