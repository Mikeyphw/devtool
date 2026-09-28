from __future__ import annotations

"""Repository ``devtoolw`` launcher lifecycle helpers.

WRAP-06 keeps launcher lifecycle separate from wrapper execution: this module
only renders, writes, checks, and reports repository-owned launcher files.  The
launchers themselves immediately delegate to ``devtool --repo <repo> wrapper
exec`` so all command resolution and execution semantics remain in the normal
Devtool CLI path.
"""

from dataclasses import asdict, dataclass
from importlib import resources
from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
from typing import Iterator, Mapping

LAUNCHER_MARKER = "DEVTOOL_WRAPPER_LAUNCHER=1"
POSIX_LAUNCHER = "devtoolw"
WINDOWS_LAUNCHER = "devtoolw.cmd"
LAUNCHER_NAMES = (POSIX_LAUNCHER, WINDOWS_LAUNCHER)
_RESOURCE_PACKAGE = "devtool.resources.wrapper"


class WrapperLauncherError(RuntimeError):
    """Raised when repository launcher lifecycle work cannot be completed."""


@dataclass(frozen=True, slots=True)
class WrapperLauncherFileStatus:
    path: str
    name: str
    exists: bool
    current: bool
    executable: bool = False
    marker_present: bool = False
    reason: str = ""


@dataclass(frozen=True, slots=True)
class WrapperLauncherStatus:
    repo_root: str
    files: tuple[WrapperLauncherFileStatus, ...]

    @property
    def ok(self) -> bool:
        return all(item.exists and item.current for item in self.files)

    @property
    def installed(self) -> bool:
        return all(item.exists for item in self.files)

    @property
    def drifted(self) -> bool:
        return any(item.exists and not item.current for item in self.files)


def _repo(root: str | Path) -> Path:
    return Path(root).resolve(strict=False)


def _template_text(name: str) -> str:
    if name not in LAUNCHER_NAMES:
        raise WrapperLauncherError(f"unknown wrapper launcher template {name!r}")
    resource = resources.files(_RESOURCE_PACKAGE).joinpath(name)
    return resource.read_text(encoding="utf-8")


def expected_launcher_texts() -> Mapping[str, str]:
    return {name: _template_text(name) for name in LAUNCHER_NAMES}


def expected_launcher_paths(repo_root: str | Path) -> Mapping[str, Path]:
    root = _repo(repo_root)
    return {name: root / name for name in LAUNCHER_NAMES}


def _has_executable_mode(path: Path) -> bool:
    return os.access(path, os.X_OK)


def _status_for(path: Path, name: str, expected: str) -> WrapperLauncherFileStatus:
    # Never follow repository launcher symlinks. A dangling symlink reports
    # ``exists() == False`` but still represents an occupied path that must not
    # be followed by write_text().
    if path.is_symlink():
        return WrapperLauncherFileStatus(path=str(path), name=name, exists=True, current=False, reason="symlink")
    if not path.exists():
        return WrapperLauncherFileStatus(path=str(path), name=name, exists=False, current=False, reason="missing")
    if not path.is_file():
        return WrapperLauncherFileStatus(path=str(path), name=name, exists=True, current=False, reason="not a regular file")
    try:
        data = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return WrapperLauncherFileStatus(path=str(path), name=name, exists=True, current=False, reason="not UTF-8 text")
    marker_present = LAUNCHER_MARKER in data
    content_current = data == expected
    executable_ok = True if name == WINDOWS_LAUNCHER else _has_executable_mode(path)
    if content_current and executable_ok:
        reason = "current"
    elif content_current and not executable_ok:
        reason = "not executable"
    elif not marker_present:
        reason = "unmanaged file"
    else:
        reason = "drifted"
    return WrapperLauncherFileStatus(
        path=str(path),
        name=name,
        exists=True,
        current=bool(content_current and executable_ok),
        executable=executable_ok,
        marker_present=marker_present,
        reason=reason,
    )


def launcher_status(repo_root: str | Path) -> WrapperLauncherStatus:
    root = _repo(repo_root)
    expected = expected_launcher_texts()
    paths = expected_launcher_paths(root)
    return WrapperLauncherStatus(
        repo_root=str(root),
        files=tuple(_status_for(paths[name], name, expected[name]) for name in LAUNCHER_NAMES),
    )


def launcher_status_as_dict(status: WrapperLauncherStatus) -> dict[str, object]:
    return {
        "repo_root": status.repo_root,
        "installed": status.installed,
        "ok": status.ok,
        "drifted": status.drifted,
        "files": [asdict(item) for item in status.files],
    }


def _make_executable(path: Path) -> None:
    mode = path.stat().st_mode
    path.chmod(mode | 0o755)


@contextmanager
def _repository_launcher_lock(repo_root: Path) -> Iterator[None]:
    """Serialize launcher pair replacement for this repository.

    The lock is advisory but process-safe on the supported local platforms.  It
    intentionally lives beside the generated launchers so older Devtool builds
    and recovery tooling can discover the ownership boundary.
    """
    lock_path = repo_root / ".devtool-wrapper.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _write_launcher_temp(root: Path, name: str, text: str) -> Path:
    fd, raw = tempfile.mkstemp(prefix=f".{name}.", suffix=".tmp", dir=str(root))
    path = Path(raw)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if name == POSIX_LAUNCHER:
            _make_executable(path)
        return path
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _restore_launcher(path: Path, snapshot: tuple[bytes, int] | None) -> None:
    if snapshot is None:
        path.unlink(missing_ok=True)
        return
    data, mode = snapshot
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.restore.", suffix=".tmp", dir=str(path.parent))
    temp = Path(raw)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temp.chmod(mode)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def install_launchers(repo_root: str | Path, *, force: bool = False, update: bool = False) -> WrapperLauncherStatus:
    """Create/update the POSIX+Windows launcher pair under one repository lock.

    Both launchers are staged before either live path changes.  If replacement
    of the second member fails, the first member is restored from its exact
    pre-update bytes/mode so a failed update cannot leave a mixed generation.
    """

    root = _repo(repo_root)
    root.mkdir(parents=True, exist_ok=True)
    expected = expected_launcher_texts()
    with _repository_launcher_lock(root):
        before = launcher_status(root)
        blockers: list[WrapperLauncherFileStatus] = []
        for item in before.files:
            if not item.exists or item.current or force:
                continue
            # ``update`` may refresh only launchers that Devtool owns.  Unmanaged
            # files, symlinks, and non-regular paths still require explicit force.
            if update and item.marker_present and item.reason == "drifted":
                continue
            blockers.append(item)
        if blockers:
            names = ", ".join(f"{item.name} ({item.reason})" for item in blockers)
            hint = "Use --force only if replacing these files is intentional."
            raise WrapperLauncherError(f"Refusing to overwrite existing wrapper launcher file(s): {names}. {hint}")

        paths = expected_launcher_paths(root)
        snapshots: dict[str, tuple[bytes, int] | None] = {}
        staged: dict[str, Path] = {}
        replaced: list[str] = []
        try:
            for name in LAUNCHER_NAMES:
                path = paths[name]
                if path.is_symlink():
                    # ``force`` was required by blocker handling.  Snapshotting
                    # arbitrary symlink targets would violate the no-follow rule.
                    snapshots[name] = None
                elif path.is_file():
                    snapshots[name] = (path.read_bytes(), path.stat().st_mode)
                else:
                    snapshots[name] = None
                staged[name] = _write_launcher_temp(root, name, expected[name])

            for name in LAUNCHER_NAMES:
                path = paths[name]
                if path.is_symlink():
                    path.unlink()
                os.replace(staged[name], path)
                replaced.append(name)
        except Exception:
            for name in reversed(replaced):
                _restore_launcher(paths[name], snapshots[name])
            raise
        finally:
            for path in staged.values():
                path.unlink(missing_ok=True)

    return launcher_status(root)

def verify_launchers(repo_root: str | Path) -> WrapperLauncherStatus:
    return launcher_status(repo_root)
