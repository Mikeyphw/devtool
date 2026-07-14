#!/usr/bin/env python3
from __future__ import annotations

from io import StringIO
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rich.console import Console

from devtool.models import Issue, OperationResult
from devtool.summary import render_summary
from devtool.ui_regression import normalize_terminal_snapshot, render_visual_svg, visual_scenarios

SNAPSHOTS = ROOT / "tests" / "snapshots" / "ui"


def terminal_snapshot(results: list[OperationResult]) -> str:
    stream = StringIO()
    render_summary(results, console_obj=Console(file=stream, force_terminal=False, width=88))
    return normalize_terminal_snapshot(stream.getvalue())


def main() -> int:
    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    success = [
        OperationResult("restore", ["dotnet", "restore"], 0, 1.2, "", []),
        OperationResult("build", ["dotnet", "build"], 0, 2.4, "", []),
        OperationResult("test", ["dotnet", "test"], 0, 1.8, "Passed! - Failed: 0, Passed: 12, Skipped: 1, Total: 13", []),
        OperationResult("git-commit", ["git", "commit"], 0, 0.2, "[main abc1234] Apply UI slice", []),
    ]
    rollback = [
        OperationResult(
            "build",
            ["dotnet", "build"],
            1,
            1.4,
            "",
            [Issue("error", "build", "src/App.cs", "CS1002", "; expected", line=42)],
        ),
        OperationResult("apply-rollback", ["devtool", "rollback"], 0, 0.4, "Changes rolled back", []),
    ]
    (SNAPSHOTS / "terminal-success.txt").write_text(terminal_snapshot(success), encoding="utf-8")
    (SNAPSHOTS / "terminal-rollback.txt").write_text(terminal_snapshot(rollback), encoding="utf-8")
    for scenario in visual_scenarios():
        (SNAPSHOTS / f"{scenario.id}.svg").write_text(render_visual_svg(scenario), encoding="utf-8")
    print(f"Updated {2 + len(visual_scenarios())} UI snapshots in {SNAPSHOTS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
