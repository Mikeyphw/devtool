from __future__ import annotations

import os
import shutil
from pathlib import Path

from rich.console import Console

_TERMUX_PREFIX_MARKER = "/data/data/com.termux/files"
_MIN_CONSOLE_WIDTH = 40
_TERMUX_NARROW_WIDTH = 96
_TERMUX_HOME_OVERRIDE_ENV = "DEVTOOL_TERMUX_HOME_OVERRIDE"


def is_termux_environment() -> bool:
    if os.environ.get("TERMUX_VERSION"):
        return True
    prefix = os.environ.get("PREFIX", "")
    home = os.environ.get("HOME", "")
    return _TERMUX_PREFIX_MARKER in prefix or home.startswith(f"{_TERMUX_PREFIX_MARKER}/home")


def detect_console_width(default: int = 100) -> int:
    columns_text = os.environ.get("COLUMNS", "").strip()
    if columns_text.isdigit():
        columns = int(columns_text)
    else:
        columns = shutil.get_terminal_size(fallback=(default, 24)).columns
    return max(_MIN_CONSOLE_WIDTH, columns)


def create_console(*, stderr: bool = False) -> Console:
    kwargs: dict[str, object] = {"stderr": stderr}
    if is_termux_environment():
        kwargs["width"] = detect_console_width()
    return Console(**kwargs)


def is_narrow_console(console: Console) -> bool:
    return is_termux_environment() and console.width <= _TERMUX_NARROW_WIDTH


def syntax_line_numbers(console: Console) -> bool:
    return not is_narrow_console(console)


def termux_home_override() -> str:
    return os.environ.get(_TERMUX_HOME_OVERRIDE_ENV, "").strip()


def resolve_cli_path(path_text: str) -> Path:
    raw = (path_text or "").strip()
    if not raw:
        return Path(raw).resolve()

    if is_termux_environment():
        override = termux_home_override()
        if override:
            if raw == "~":
                raw = override
            elif raw.startswith("~/"):
                raw = f"{override}/{raw[2:]}"
            elif raw == "$HOME":
                raw = override
            elif raw.startswith("$HOME/"):
                raw = f"{override}/{raw[len('$HOME/') :]}"

    expanded = os.path.expandvars(raw)
    return Path(expanded).expanduser().resolve()
