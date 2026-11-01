from __future__ import annotations

"""R38-R1 opt-in tracked-write observations for serial workflow nodes.

This is *not* a filesystem sandbox or a continuous write monitor. It compares
Git-tracked working-tree bytes at two stable observation points. It neither
reads ignored/untracked writes nor attributes concurrent writes to a process.
Refuse unsupported layouts instead of recording an unearned isolation PASS.
"""

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
import subprocess
from typing import Sequence


class WriteAuditError(ValueError):
    """A tracked write could not be verified conservatively."""


_MAX_PATHS = 2048
_MAX_FILE_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class TrackedSnapshot:
    head: str
    changed: tuple[tuple[str, str], ...]

    def files(self) -> dict[str, str]:
        return dict(self.changed)


def _git(root: Path, *args: str) -> bytes:
    try:
        process = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True,
            timeout=15, check=False, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WriteAuditError("R38_GIT_PROOF_UNAVAILABLE") from exc
    if process.returncode:
        raise WriteAuditError("R38_GIT_PROOF_UNAVAILABLE")
    return process.stdout


def _file_digest(root: Path, relative: str) -> str:
    target = root / relative
    # Each parent is inspected without following symlinks. Treat concurrent
    # replacement or unusual filesystem objects as unverifiable.
    for parent in list(target.parents)[:-1]:
        if parent == root.parent:
            break
        if parent == root or not parent.is_relative_to(root):
            continue
        try:
            mode = parent.lstat().st_mode
        except OSError as exc:
            raise WriteAuditError("R38_PATH_STATE_UNAVAILABLE") from exc
        if not stat.S_ISDIR(mode):
            raise WriteAuditError("R38_UNSAFE_PATH_TYPE")
    try:
        info = target.lstat()
    except FileNotFoundError:
        return "deleted"
    except OSError as exc:
        raise WriteAuditError("R38_PATH_STATE_UNAVAILABLE") from exc
    if stat.S_ISLNK(info.st_mode):
        try:
            return "symlink:" + hashlib.sha256(os.readlink(target).encode("utf-8", "surrogateescape")).hexdigest()
        except OSError as exc:
            raise WriteAuditError("R38_PATH_STATE_UNAVAILABLE") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_FILE_BYTES:
        raise WriteAuditError("R38_UNSUPPORTED_FILE_TYPE_OR_SIZE")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(target, flags)
        with os.fdopen(fd, "rb") as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > _MAX_FILE_BYTES:
                raise WriteAuditError("R38_UNSUPPORTED_FILE_TYPE_OR_SIZE")
            digest = hashlib.sha256()
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
            after = os.fstat(source.fileno())
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                raise WriteAuditError("R38_CONCURRENT_FILE_MUTATION")
            return "file:" + digest.hexdigest() + ":" + str(info.st_mode & 0o777)
    except WriteAuditError:
        raise
    except OSError as exc:
        raise WriteAuditError("R38_PATH_STATE_UNAVAILABLE") from exc


def snapshot(root: Path) -> TrackedSnapshot:
    root = Path(root).absolute()
    if root.is_symlink() or not root.is_dir():
        raise WriteAuditError("R38_UNSAFE_REPOSITORY_ROOT")
    top = _git(root, "rev-parse", "--show-toplevel").decode("utf-8", "surrogateescape").strip()
    if Path(top).absolute() != root:
        raise WriteAuditError("R38_REPOSITORY_SCOPE_MISMATCH")
    head = _git(root, "rev-parse", "--verify", "HEAD").decode("ascii").strip()
    output = _git(root, "diff", "--no-ext-diff", "--no-textconv", "--no-renames", "--name-only", "-z", "HEAD", "--")
    raw_paths = output.split(b"\0")
    if raw_paths[-1:] == [b""]:
        raw_paths.pop()
    if len(raw_paths) > _MAX_PATHS:
        raise WriteAuditError("R38_TOO_MANY_CHANGED_FILES")
    changed = []
    for raw in raw_paths:
        relative = raw.decode("utf-8", "surrogateescape")
        path = Path(relative)
        if not relative or path.is_absolute() or ".." in path.parts or ".git" in path.parts:
            raise WriteAuditError("R38_UNSAFE_TRACKED_PATH")
        changed.append((relative, _file_digest(root, relative)))
    return TrackedSnapshot(head=head, changed=tuple(sorted(changed)))


def verify_node(before: TrackedSnapshot, after: TrackedSnapshot, *, node_key: str,
                writes: Sequence[str], writes_known: bool, read_only: bool) -> dict[str, object]:
    """Classify observed changes without equating declared scopes to a sandbox."""
    if before.head != after.head:
        return {"status": "fail", "code": "R38_SOURCE_HEAD_CHANGED", "node": node_key, "observed": [], "outside_scope": []}
    old, new = before.files(), after.files()
    observed = sorted(path for path in old.keys() | new.keys() if old.get(path) != new.get(path))
    allowed = [p.rstrip("/") for p in writes if p and not p.startswith("@") and p not in {".", "/"} and ".." not in Path(p).parts]
    if not writes_known:
        return {"status": "fail", "code": "R38_UNKNOWN_WRITE_SCOPE", "node": node_key, "observed": observed, "outside_scope": observed}
    outside = [path for path in observed if read_only or not any(path == p or path.startswith(p + "/") for p in allowed)]
    return {"status": "fail" if outside else "pass", "code": "R38_OUT_OF_SCOPE_TRACKED_WRITE" if outside else "R38_TRACKED_WRITE_CHECK_PASSED",
            "node": node_key, "observed": observed, "outside_scope": outside,
            "limits": "git-tracked stable pre/post snapshots only; not an OS sandbox or continuous monitor"}
