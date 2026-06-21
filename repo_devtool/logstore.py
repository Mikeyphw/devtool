from __future__ import annotations

from datetime import datetime
from pathlib import Path


def state_dir() -> Path:
    return Path.home() / ".local" / "state" / "devtool"


def logs_dir() -> Path:
    path = state_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_log(text: str) -> Path:
    path = logs_dir() / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
    path.write_text(text, encoding="utf-8")
    return path


def latest_log() -> Path | None:
    files = sorted(logs_dir().glob("*.log"))
    return files[-1] if files else None
