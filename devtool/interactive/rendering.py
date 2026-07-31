from __future__ import annotations

from dataclasses import asdict
import json
import shlex
from typing import Any, Iterable


def shell_join(argv: Iterable[str]) -> str:
    return shlex.join([str(item) for item in argv])


def shell_join_multiline(argv: Iterable[str], *, indent: str = "  ") -> str:
    values = [str(item) for item in argv]
    if not values:
        return ""
    if len(values) <= 5:
        return shell_join(values)
    lines = [shlex.quote(values[0]) + " \\"]
    for index, value in enumerate(values[1:], start=1):
        suffix = " \\" if index < len(values) - 1 else ""
        lines.append(f"{indent}{shlex.quote(value)}{suffix}")
    return "\n".join(lines)


def format_value(value: Any) -> str:
    if value is None or value == "" or value == [] or value == {}:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(item) for item in value) or "-"
    return str(value)


def markdown_table(rows: Iterable[tuple[str, Any]]) -> str:
    output = ["| Setting | Value |", "|---|---|"]
    for key, value in rows:
        output.append(f"| {key} | {format_value(value).replace('|', '\\|')} |")
    return "\n".join(output)


def json_preview(payload: Any) -> str:
    try:
        if hasattr(payload, "__dataclass_fields__"):
            payload = asdict(payload)
        return json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(payload)
