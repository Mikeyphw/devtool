from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Mapping


def host_is_termux(env: Mapping[str, str] | None = None) -> bool:
    values = os.environ if env is None else env
    prefix = str(values.get("PREFIX") or "")
    home = str(values.get("HOME") or "")
    return bool(
        values.get("TERMUX_VERSION")
        or prefix.startswith("/data/data/com.termux/files/")
        or home.startswith("/data/data/com.termux/files/home")
    )


def command_path(name: str) -> str | None:
    return shutil.which(name)


def command_available(name: str) -> bool:
    return command_path(name) is not None


def shell_path() -> str | None:
    return command_path("sh")


def portable_shell_argv(script: str) -> list[str]:
    shell = shell_path()
    if not shell:
        raise RuntimeError("POSIX sh is unavailable")
    return [shell, "-c", script]


def short_private_ipc_socket_dir() -> tempfile.TemporaryDirectory[str]:
    """Allocate only the socket in a private, platform-appropriate short path."""
    roots: list[Path] = []
    prefix = os.environ.get("PREFIX", "").strip()
    if prefix:
        roots.append(Path(prefix) / "tmp")
    runtime_tmp = os.environ.get("TMPDIR", "").strip()
    if runtime_tmp:
        roots.append(Path(runtime_tmp))
    if not host_is_termux():
        roots.append(Path("/tmp"))
    roots.append(Path(tempfile.gettempdir()))
    for root in dict.fromkeys(roots):
        if not root.is_absolute() or not root.is_dir() or str(root).startswith(("/sdcard/", "/storage/", "/mnt/media_rw/")):
            continue
        if len(os.fsencode(str(root))) + len("/dt-ipc-xxxxxxxx/ipc.sock") > 103:
            continue
        try:
            private = tempfile.TemporaryDirectory(prefix="dt-ipc-", dir=root)
        except OSError:
            continue
        if len(os.fsencode(str(Path(private.name) / "ipc.sock"))) <= 103:
            return private
        private.cleanup()
    raise OSError("No writable short private directory available for AF_UNIX IPC test")


def required_interpreter(name: str) -> str:
    """Resolve the actual test-host interpreter; never assume FHS paths."""
    if name not in {"sh", "bash"}:
        raise ValueError(f"Unsupported fixture interpreter: {name}")
    found = command_path(name)
    if not found:
        raise RuntimeError(f"Required fixture interpreter unavailable: {name}")
    path = Path(found).absolute()
    if not path.is_file() or not os.access(path, os.X_OK) or any(c.isspace() for c in str(path)):
        raise RuntimeError(f"Unsafe fixture interpreter: {path}")
    return str(path)


def fixture_shebang(name: str = "sh") -> str:
    return "#!" + required_interpreter(name) + "\n"


def portable_wrapper_argv(wrapper: Path, *args: str) -> list[str]:
    """Execute the checked-in wrapper directly when its POSIX shebang exists.

    Native non-FHS hosts instead execute the same immutable script through
    their real sh; fixture execution does not rewrite repository launchers.
    """
    if Path('/bin/sh').is_file() and os.access('/bin/sh', os.X_OK):
        return [str(wrapper), *args]
    return [required_interpreter('sh'), str(wrapper), *args]


def portable_fixture_path() -> str:
    """Minimal host tool directories needed by the repository wrapper."""
    directories = []
    for name in ("sh", "dirname", "readlink"):
        found = command_path(name)
        if not found:
            raise RuntimeError(f"Required fixture tool unavailable: {name}")
        directory = str(Path(found).absolute().parent)
        if directory not in directories:
            directories.append(directory)
    return os.pathsep.join(directories)


def shared_tool_cache_env(root: Path) -> dict[str, str]:
    """Return private session-scoped caches for heavyweight external tools.

    The paths deliberately live under pytest's own temporary root rather than
    the developer's HOME.  This keeps tests isolated while avoiding a cold Go,
    Gradle, npm, pip, uv, and Cargo cache for every individual test case.
    """

    root = root.resolve()
    mapping = {
        "GOCACHE": root / "go-build",
        "GOMODCACHE": root / "go-mod",
        "GRADLE_USER_HOME": root / "gradle",
        "UV_CACHE_DIR": root / "uv",
        "PIP_CACHE_DIR": root / "pip",
        "npm_config_cache": root / "npm",
        "CARGO_HOME": root / "cargo-home",
        "CARGO_TARGET_DIR": root / "cargo-target",
    }
    for path in mapping.values():
        path.mkdir(parents=True, exist_ok=True)
    return {name: str(path) for name, path in mapping.items()}
