from __future__ import annotations

import os
from pathlib import Path


def _override_root() -> Path | None:
    raw = os.environ.get("DEVTOOL_XDG_DIR", "").strip()
    return Path(raw).expanduser() if raw else None


def state_dir() -> Path:
    override = _override_root()
    if override is not None:
        return override
    raw = os.environ.get("XDG_STATE_HOME", "").strip()
    if raw:
        return Path(raw).expanduser() / "devtool"
    return Path.home() / ".local" / "state" / "devtool"


def data_dir() -> Path:
    override = _override_root()
    if override is not None:
        return override
    raw = os.environ.get("XDG_DATA_HOME", "").strip()
    if raw:
        return Path(raw).expanduser() / "devtool"
    return Path.home() / ".local" / "share" / "devtool"


def cache_dir() -> Path:
    override = _override_root()
    if override is not None:
        return override
    raw = os.environ.get("XDG_CACHE_HOME", "").strip()
    if raw:
        return Path(raw).expanduser() / "devtool"
    return Path.home() / ".cache" / "devtool"
