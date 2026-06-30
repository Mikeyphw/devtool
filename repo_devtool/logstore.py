from __future__ import annotations

from datetime import datetime
from pathlib import Path


def state_dir() -> Path:
    return Path.home() / ".local" / "state" / "devtool"


def logs_dir() -> Path:
    path = state_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def new_log_path() -> Path:
    return logs_dir() / f"{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.log"


def write_log(text: str, path: Path | str | None = None, *, append: bool = False) -> Path:
    target = Path(path) if path is not None else new_log_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    if append:
        with target.open("a", encoding="utf-8") as handle:
            if target.exists() and target.stat().st_size > 0:
                handle.write("\n\n")
            handle.write(text)
    else:
        target.write_text(text, encoding="utf-8")
    return target


def latest_log() -> Path | None:
    files = sorted(logs_dir().glob("*.log"))
    return files[-1] if files else None
