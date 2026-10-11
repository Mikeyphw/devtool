"""ACTIONS-00/01/02/03 command adapter; mutation gated by explicit --yes."""
from __future__ import annotations
import json
import sys
from pathlib import Path
from typing import Any
from .github_actions import GitHubActionsError, actions_cli


def run_actions_command(args: Any) -> int:
    try:
        payload = actions_cli(args, Path(str(getattr(args, "repo", "") or Path.cwd())))
    except GitHubActionsError as exc:
        print(f"GitHub Actions: {exc}", file=sys.stderr)
        return 1
    if bool(getattr(args, "json", False)) or bool(getattr(args, "_devtool_json", False)):
        print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
    elif (str(getattr(args, 'actions_command', '')) == 'release'
          and str(getattr(args, 'release_command', '')) in ('setup', 'readiness')
          and sys.stdout.isatty()):
        from .actions_presentation import release_setup_text
        print(release_setup_text(payload))
    elif str(getattr(args, "actions_command", "")) == "pipeline" and sys.stdout.isatty() and "nodes" in payload:
        from .actions_presentation import pipeline_text
        print(pipeline_text(payload))
    else:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    if (str(getattr(args, 'actions_command', '')) == 'release'
            and str(getattr(args, 'release_command', '')) == 'readiness'
            and not payload.get('ready', False)):
        return 1
    if (str(getattr(args, "actions_command", "")) == "pipeline"
            and payload.get("state") == "blocked"):
        return 1
    return 0
