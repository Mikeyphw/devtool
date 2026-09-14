from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


State = tuple[str, int, str]
Backup = tuple[State, bytes | None]


def path_state(repo_root: Path, rel: str) -> State:
    path = repo_root / rel
    try:
        if path.is_symlink():
            return ("symlink", 0, os.readlink(path))
        if not path.exists():
            return ("missing", 0, "")
        if path.is_file():
            return ("file", path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest())
        if path.is_dir():
            return ("directory", 0, "")
    except OSError as exc:
        return ("error", 0, f"{type(exc).__name__}:{exc}")
    return ("other", 0, "")


def _path_backup(repo_root: Path, rel: str) -> Backup:
    state = path_state(repo_root, rel)
    path = repo_root / rel
    data: bytes | None = None
    if state[0] == "file":
        try:
            data = path.read_bytes()
        except OSError:
            data = None
    return state, data


def _restore_backup(repo_root: Path, rel: str, backup: Backup) -> None:
    state, data = backup
    path = repo_root / rel
    if path.is_symlink() or path.is_file():
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    elif path.is_dir() and state[0] != "directory":
        shutil.rmtree(path)
    kind = state[0]
    if kind == "missing":
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists() or path.is_symlink():
            path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if kind == "file":
        path.write_bytes(data or b"")
    elif kind == "symlink":
        os.symlink(state[2], path)
    elif kind == "directory":
        path.mkdir(parents=True, exist_ok=True)


def git_changed_paths(repo_root: Path) -> set[str] | None:
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    paths: set[str] = set()
    records = proc.stdout.split("\0")
    i = 0
    while i < len(records):
        record = records[i]
        i += 1
        if len(record) < 4:
            continue
        status = record[:2]
        raw = record[3:].strip().replace("\\", "/")
        if ("R" in status or "C" in status) and i < len(records) and records[i]:
            raw = records[i].strip().replace("\\", "/")
            i += 1
        if raw:
            paths.add(raw)
    return paths


def _git_tracked(repo_root: Path, rel: str) -> bool:
    try:
        proc = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", rel],
            cwd=repo_root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def repo_file_states(repo_root: Path) -> dict[str, State]:
    states: dict[str, State] = {}
    for path in repo_root.rglob("*"):
        try:
            rel_path = path.relative_to(repo_root)
        except ValueError:
            continue
        if ".git" in rel_path.parts:
            continue
        if path.is_file() or path.is_symlink() or path.is_dir():
            rel = rel_path.as_posix()
            states[rel] = path_state(repo_root, rel)
    return states


def path_is_declared(rel: str, declared: Iterable[str]) -> bool:
    value = rel.strip().replace("\\", "/").strip("/")
    for item in declared:
        root = str(item).strip().replace("\\", "/").strip("/")
        if root and (value == root or value.startswith(root + "/")):
            return True
    return False


@dataclass(slots=True)
class RepositoryMutationSnapshot:
    git_paths: set[str] | None
    dirty_states: dict[str, State]
    fallback_states: dict[str, State] | None
    dirty_backups: dict[str, Backup] = field(default_factory=dict)

    @classmethod
    def capture(cls, repo_root: Path, *, backup_dirty: bool = False) -> "RepositoryMutationSnapshot":
        changed = git_changed_paths(repo_root)
        if changed is None:
            states = repo_file_states(repo_root)
            backups = {path: _path_backup(repo_root, path) for path in states} if backup_dirty else {}
            return cls(None, {}, states, backups)
        fallback = repo_file_states(repo_root)
        backups = {path: _path_backup(repo_root, path) for path in set(changed) | set(fallback)} if backup_dirty else {}
        return cls(changed, {path: path_state(repo_root, path) for path in changed}, fallback, backups)

    def changes(self, repo_root: Path) -> list[str]:
        if self.fallback_states is not None:
            after = repo_file_states(repo_root)
            candidates = set(self.fallback_states) | set(after)
            fallback_changed = {
                path for path in candidates
                if self.fallback_states.get(path, ("missing", 0, ""))
                != after.get(path, ("missing", 0, ""))
            }
            if self.git_paths is not None:
                after_paths = git_changed_paths(repo_root)
                if after_paths is not None:
                    fallback_changed |= set(self.git_paths or set()) | set(after_paths)
            return sorted(fallback_changed)
        after_paths = git_changed_paths(repo_root)
        if after_paths is None:
            after = repo_file_states(repo_root)
            candidates = set(self.dirty_states) | set(after)
            return sorted(
                path for path in candidates
                if self.dirty_states.get(path, ("missing", 0, "")) != after.get(path, ("missing", 0, ""))
            )
        candidates = set(self.git_paths or set()) | after_paths
        return sorted(
            path for path in candidates
            if path_state(repo_root, path) != self.dirty_states.get(path, ("missing", 0, ""))
        )

    def undeclared_changes(self, repo_root: Path, declared: Iterable[str]) -> tuple[list[str], list[str]]:
        changed = self.changes(repo_root)
        violations = [path for path in changed if not path_is_declared(path, declared)]
        return changed, violations

    def restore(self, repo_root: Path, paths: Iterable[str]) -> list[str]:
        """Best-effort restoration used by observational hooks after a Git commit."""
        errors: list[str] = []
        for rel in paths:
            try:
                if rel in self.dirty_backups:
                    _restore_backup(repo_root, rel, self.dirty_backups[rel])
                    continue
                if self.git_paths is not None and _git_tracked(repo_root, rel):
                    proc = subprocess.run(
                        ["git", "restore", "--worktree", "--source=HEAD", "--", rel],
                        cwd=repo_root,
                        capture_output=True,
                        text=True,
                        timeout=15,
                        check=False,
                    )
                    if proc.returncode != 0:
                        errors.append(f"{rel}: {proc.stderr.strip() or proc.stdout.strip() or 'git restore failed'}")
                    continue
                path = repo_root / rel
                if path.is_symlink() or path.is_file():
                    path.unlink(missing_ok=True)
                elif path.is_dir():
                    shutil.rmtree(path)
            except Exception as exc:
                errors.append(f"{rel}: {type(exc).__name__}: {exc}")
        return errors
