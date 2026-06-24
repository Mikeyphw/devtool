from __future__ import annotations

import unittest
from io import StringIO

from repo_devtool.models import Issue, OperationResult
from repo_devtool.summary import render_status_table, render_summary
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


if __name__ == "__main__":
    unittest.main()
