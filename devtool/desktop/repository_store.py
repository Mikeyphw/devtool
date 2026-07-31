from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


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

    def _git(self, args: list[str]) -> str:
        try:
            completed = subprocess.run(["git", *args], cwd=self.repository, text=True, capture_output=True, check=False, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return completed.stdout.strip() if completed.returncode == 0 else ""
