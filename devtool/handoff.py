from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .artifacts import load_history
from .config import resolve_config_dict
from .models import ProjectConfig


def _git_status(repo_root: Path) -> str:
    proc = subprocess.run(["git", "status", "--short"], cwd=repo_root, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def render_handoff(config: ProjectConfig) -> str:
    cfg = resolve_config_dict(config)
    repo = config.repo_root
    checked_candidates = [
        "AI_AGENT_DEVELOPER_PROMPT.md",
        "s3pe_avalonia/docs/final-parity-report.md",
        "s3pe_avalonia/docs/migration-map.md",
        "s3ow_avalonia/docs/texturetweaker3-modernization-plan.md",
        "README.md",
    ]
    checked = [path for path in checked_candidates if (repo / path).exists()]
    history = load_history(repo)[-5:]
    lines = [
        "# devtool handoff",
        "",
        "## Repo",
        f"- root: `{repo}`",
        f"- kind: `{config.kind}`",
        f"- selected target: `{config.selected_target or '-'}`",
        f"- config: `{config.config_path or '-'}`",
        "",
        "## Checked files present",
    ]
    lines.extend(f"- `{path}`" for path in checked) if checked else lines.append("- none detected")
    lines.extend([
        "",
        "## Current targets",
        f"- restore: `{config.restore_target or '-'}`",
        f"- build: `{config.build_target or '-'}`",
        f"- test: `{config.test_target or '-'}`",
        f"- run: `{config.run_target or '-'}`",
        "",
        "## Exact commands",
        f"```bash\ndevtool -r {str(repo)!r} validate\ndevtool -r {str(repo)!r} status\ndevtool -r {str(repo)!r} detect\n```",
        "",
        "## Named targets",
    ])
    if cfg.get("targets"):
        for name, target in cfg["targets"].items():
            lines.append(f"- `{name}`: solution=`{target.get('solution') or '-'}` run=`{target.get('run_project') or '-'}`")
    else:
        lines.append("- none")
    lines.extend(["", "## Recent artifact history"])
    if history:
        for entry in history:
            lines.append(f"- `{entry.get('artifact_name')}` status={entry.get('status')} validation={entry.get('validation_status')} commit={entry.get('commit_hash') or '-'}")
    else:
        lines.append("- none")
    status = _git_status(repo)
    lines.extend(["", "## Git status", "```", status or "clean or unavailable", "```", ""])
    return "\n".join(lines)
