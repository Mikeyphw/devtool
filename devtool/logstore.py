from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re


def state_dir() -> Path:
    return Path.home() / ".local" / "state" / "devtool"


def logs_dir() -> Path:
    path = state_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def new_log_path() -> Path:
    return logs_dir() / f"{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.log"


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
    return target


def latest_log() -> Path | None:
    files = sorted(logs_dir().glob("*.log"))
    return files[-1] if files else None
