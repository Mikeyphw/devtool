"""ACTIONS-00/01/02 command adapter; mutation gated by explicit --yes."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from .github_actions import GitHubActionsError, actions_cli


def run_actions_command(args: Any) -> int:
    try:
        payload = actions_cli(args, Path(str(getattr(args, "repo", "") or Path.cwd())))
    except GitHubActionsError as exc:
        import sys
        print(f"GitHub Actions: {exc}", file=sys.stderr)
        return 1
    if bool(getattr(args, "json", False)) or bool(getattr(args, "_devtool_json", False)):
        print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0
