from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import re
import shutil

from .xdg import state_dir as _xdg_state_dir


def state_dir() -> Path:
    return _xdg_state_dir()


def logs_dir() -> Path:
    path = state_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def new_log_path() -> Path:
    return logs_dir() / f"{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.log"


def _is_phone_termux() -> bool:
    prefix = os.environ.get("PREFIX", "")
    home = os.environ.get("HOME", "")
    return bool(
        os.environ.get("TERMUX_VERSION")
        or os.environ.get("TERMUX_APP_PID")
        or prefix.startswith("/data/data/com.termux/")
        or home.startswith("/data/data/com.termux/files/home")
    )


def _mirror_log_to_phone(path: Path) -> None:
    if not _is_phone_termux() or path.suffix != ".log":
        return
    mirror_root = Path(os.environ.get("DEVTOOL_PHONE_LOG_MIRROR_DIR", "/sdcard/.logs/devtool")).expanduser()
    try:
        resolved = path.resolve(strict=False)
        logs_root = logs_dir().resolve(strict=False)
        if not (resolved == logs_root or logs_root in resolved.parents):
            return
        mirror_root.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, mirror_root / path.name)
    except OSError:
        pass


_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(?P<name>(?:[A-Z0-9_]*(?:TOKEN|PASSWORD|PASSWD|SECRET|API_KEY|AUTH)[A-Z0-9_]*))(?P<sep>\s*=\s*)(?P<value>[^\s;]+)"
)
_BEARER = re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[A-Za-z0-9._~+/-]+")
_CLI_SECRET = re.compile(r"(?i)(--(?:token|password|secret|api-key|auth-token)(?:=|\s+))([^\s]+)")


def redact_sensitive_text(text: str) -> str:
    """Redact common credentials before persistent logs are written."""
    value = _SECRET_ASSIGNMENT.sub(lambda m: f"{m.group('name')}{m.group('sep')}<redacted>", str(text))
    value = _BEARER.sub(r"\1<redacted>", value)
    return _CLI_SECRET.sub(r"\1<redacted>", value)


def write_log(text: str, path: Path | str | None = None, *, append: bool = False) -> Path:
    target = Path(path) if path is not None else new_log_path()
    text = redact_sensitive_text(text)
    target.parent.mkdir(parents=True, exist_ok=True)
    if append:
        with target.open("a", encoding="utf-8") as handle:
            if target.exists() and target.stat().st_size > 0:
                handle.write("\n\n")
            handle.write(text)
    else:
        target.write_text(text, encoding="utf-8")
    _mirror_log_to_phone(target)
    return target


def latest_log() -> Path | None:
    files = sorted(logs_dir().glob("*.log"))
    return files[-1] if files else None
