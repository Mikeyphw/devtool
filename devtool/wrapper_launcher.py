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
import os
from pathlib import Path
from typing import Mapping

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


def install_launchers(repo_root: str | Path, *, force: bool = False, update: bool = False) -> WrapperLauncherStatus:
    """Create or update repository launcher files.

    ``init`` callers should use ``update=False`` so unmanaged/drifted files are
    protected unless ``force`` is supplied.  ``update`` callers intentionally
    refresh generated launchers in place.
    """

    root = _repo(repo_root)
    expected = expected_launcher_texts()
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

    for name, text in expected.items():
        path = root / name
        if path.is_symlink():
            # ``force`` was required above. Remove the link itself rather than
            # writing through it to an arbitrary target outside the repository.
            path.unlink()
        path.write_text(text, encoding="utf-8", newline="")
        if name == POSIX_LAUNCHER:
            _make_executable(path)
    return launcher_status(root)


def verify_launchers(repo_root: str | Path) -> WrapperLauncherStatus:
    return launcher_status(repo_root)
