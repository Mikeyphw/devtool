from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping


def _env_path(
    name: str,
    *,
    env: Mapping[str, str] | None = None,
    custom: bool = False,
) -> Path | None:
    values = env or os.environ
    raw = str(values.get(name, "") or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        if custom:
            raise ValueError(f"{name} must be an absolute path, got {raw!r}")
        # XDG Base Directory variables with relative values are invalid and
        # must be ignored rather than being resolved relative to the caller's
        # current working directory.
        return None
    return path.resolve(strict=False)


def override_root(env: Mapping[str, str] | None = None) -> Path | None:
    return _env_path("DEVTOOL_XDG_DIR", env=env, custom=True)


def _home(env: Mapping[str, str] | None = None) -> Path:
    values = env or os.environ
    raw = str(values.get("HOME", "") or "").strip()
    path = Path(raw).expanduser() if raw else Path.home()
    return path.resolve(strict=False) if path.is_absolute() else Path.home().resolve(strict=False)


def state_home(env: Mapping[str, str] | None = None) -> Path:
    values = env or os.environ
    return _env_path("XDG_STATE_HOME", env=values) or (_home(values) / ".local" / "state")


def data_home(env: Mapping[str, str] | None = None) -> Path:
    values = env or os.environ
    return _env_path("XDG_DATA_HOME", env=values) or (_home(values) / ".local" / "share")


def cache_home(env: Mapping[str, str] | None = None) -> Path:
    values = env or os.environ
    return _env_path("XDG_CACHE_HOME", env=values) or (_home(values) / ".cache")


def state_dir(env: Mapping[str, str] | None = None) -> Path:
    values = env or os.environ
    override = override_root(values)
    return override if override is not None else state_home(values) / "devtool"


def data_dir(env: Mapping[str, str] | None = None) -> Path:
    values = env or os.environ
    override = override_root(values)
    return override if override is not None else data_home(values) / "devtool"


def cache_dir(env: Mapping[str, str] | None = None) -> Path:
    values = env or os.environ
    override = override_root(values)
    return override if override is not None else cache_home(values) / "devtool"
