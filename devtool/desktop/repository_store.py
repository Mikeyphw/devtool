from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _desktop_state_dir() -> Path:
    override = os.environ.get("DEVTOOL_DESKTOP_STATE_DIR") or os.environ.get("XDG_STATE_HOME")
    if override and override == os.environ.get("XDG_STATE_HOME"):
        return Path(override).expanduser() / "devtool"
    if override:
        return Path(override).expanduser()
    return Path.home() / ".local" / "state" / "devtool"


@dataclass(slots=True)
class RepositorySummary:
    path: Path
    branch: str = ""
    head: str = ""
    dirty: bool = False
    exists: bool = False


class DesktopRepositoryStore:
    def __init__(self, repository: Path | str) -> None:
        self.repository = Path(repository).expanduser()

    def summary(self) -> RepositorySummary:
        path = self.repository
        if not path.is_dir():
            return RepositorySummary(path=path, exists=False)
        branch = self._git(["rev-parse", "--abbrev-ref", "HEAD"])
        head = self._git(["rev-parse", "--short", "HEAD"])
        dirty = bool(self._git(["status", "--porcelain"]))
        return RepositorySummary(path=path, branch=branch, head=head, dirty=dirty, exists=True)

    def remember(self, *, state_dir: Path | str | None = None) -> Path:
        """Add the current repository to the desktop recent repository list."""
        destination = Path(state_dir).expanduser() if state_dir else _desktop_state_dir()
        destination.mkdir(parents=True, exist_ok=True)
        path = destination / "repositories.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            raw = []
        entries = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
        resolved = str(self.repository.resolve() if self.repository.exists() else self.repository)
        entries = [item for item in entries if str(item.get("path") or "") != resolved]
        summary = self.summary()
        entries.insert(0, {
            "path": resolved,
            "branch": summary.branch,
            "head": summary.head,
            "dirty": summary.dirty,
            "remembered_at": _timestamp(),
        })
        path.write_text(json.dumps(entries[:50], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def git_status(self) -> str:
        if not self.repository.is_dir():
            return f"Repository does not exist: {self.repository}"
        try:
            completed = subprocess.run(
                ["git", "status", "--short", "--branch"],
                cwd=self.repository,
                text=True,
                capture_output=True,
                check=False,
                timeout=8,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"git status failed: {exc}"
        output = (completed.stdout or completed.stderr or "").strip()
        if completed.returncode != 0:
            return output or f"git status exited with {completed.returncode}"
        return output or "## clean working tree"

    def _git(self, args: list[str]) -> str:
        try:
            completed = subprocess.run(["git", *args], cwd=self.repository, text=True, capture_output=True, check=False, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return completed.stdout.strip() if completed.returncode == 0 else ""


def _timestamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
