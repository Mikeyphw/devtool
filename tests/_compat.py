from __future__ import annotations

import os
import shutil
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
