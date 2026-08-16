from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any



def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    tmp_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            tmp_name = handle.name
            os.chmod(tmp_name, 0o600)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        os.chmod(path, 0o600)
        _fsync_directory(path.parent)
    finally:
        if tmp_name:
            try:
                Path(tmp_name).unlink(missing_ok=True)
            except Exception:
                pass

def _desktop_state_dir() -> Path:
    explicit = str(os.environ.get("DEVTOOL_DESKTOP_STATE_DIR", "") or "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_absolute():
            raise ValueError("DEVTOOL_DESKTOP_STATE_DIR must be an absolute path")
        return path.resolve(strict=False)
    from ..xdg import state_dir
    return state_dir() / "desktop"

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
        _write_json_atomic(path, entries[:50])
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
