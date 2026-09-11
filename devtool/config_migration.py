from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import re
import tomllib
from typing import Any

CONFIG_MIGRATION_SCHEMA_VERSION = 2

_PLATFORM_BY_RUNNER = {
    "python": "server", "node": "web", "android": "android", "gradle": "jvm",
    "dotnet": "desktop", "rust": "desktop", "go": "server", "maven": "jvm",
    "cmake": "desktop", "meson": "desktop", "make": "desktop", "command": "shared",
}


@dataclass(slots=True)
class ConfigMigrationFinding:
    section: str
    key: str
    value: str
    replacement_key: str
    line: int
    message: str
    compatibility_id: str = ""
    safe: bool = True

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sections(lines: list[str]) -> list[tuple[int, int, str]]:
    starts: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        match = re.match(r"^\s*\[([^]]+)]\s*(?:#.*)?$", line)
        if match:
            starts.append((index, match.group(1).strip()))
    result: list[tuple[int, int, str]] = []
    for offset, (start, name) in enumerate(starts):
        end = starts[offset + 1][0] if offset + 1 < len(starts) else len(lines)
        result.append((start, end, name))
    return result


def _line_value(line: str, key: str) -> str | None:
    match = re.match(rf'^\s*{re.escape(key)}\s*=\s*["\']([^"\']*)["\']', line)
    return match.group(1).strip() if match else None


def _bool_value(line: str, key: str) -> bool | None:
    match = re.match(rf"^\s*{re.escape(key)}\s*=\s*(true|false)\b", line, re.I)
    if not match:
        return None
    return match.group(1).lower() == "true"


def _table_value(text: str, section: str, key: str) -> Any:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None
    current: Any = data
    for part in section.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current.get(key) if isinstance(current, dict) else None


def analyze_config_migration_text(text: str) -> list[ConfigMigrationFinding]:
    lines = text.splitlines()
    findings: list[ConfigMigrationFinding] = []
    canonical_workspace_default = _table_value(text, "workspace", "default_target")
    for start, end, section in _sections(lines):
        body = lines[start + 1:end]
        if section == "project" or section.startswith("targets."):
            canonical_runner = any(re.match(r"^\s*runner\s*=", line) for line in body)
            for rel, line in enumerate(body, start=1):
                match = re.match(r'^\s*(kind|type)\s*=\s*["\']([^"\']+)["\']', line)
                if not match:
                    continue
                key, value = match.group(1), match.group(2).strip().lower()
                if section == "project" and key != "kind":
                    continue
                compat_id = "config.project.kind" if section == "project" else f"config.target.{key}"
                message = (
                    f"Legacy {key} is redundant because runner is already declared."
                    if canonical_runner else
                    f"Replace legacy {key} with canonical runner; platform remains independent."
                )
                findings.append(ConfigMigrationFinding(
                    section, key, value, "runner", start + rel + 1, message,
                    compatibility_id=compat_id,
                ))
            if section == "project":
                for rel, line in enumerate(body, start=1):
                    value = _line_value(line, "default_target")
                    if value is None:
                        continue
                    safe = canonical_workspace_default in (None, "", value)
                    message = (
                        "Move the legacy project default target to [workspace].default_target."
                        if safe else
                        "Legacy [project].default_target conflicts with [workspace].default_target; reconcile the values manually."
                    )
                    findings.append(ConfigMigrationFinding(
                        section, "default_target", value, "workspace.default_target", start + rel + 1,
                        message, compatibility_id="config.project.default_target", safe=safe,
                    ))
        if section == "ui":
            for rel, line in enumerate(body, start=1):
                value = _bool_value(line, "confirm_destructive")
                if value is not None:
                    findings.append(ConfigMigrationFinding(
                        section, "confirm_destructive", str(value).lower(), "ui.confirmations", start + rel + 1,
                        "Move the legacy destructive-confirmation boolean to [ui.confirmations].",
                        compatibility_id="config.ui.confirm_destructive",
                    ))
                value = _bool_value(line, "confirm_cancel")
                if value is not None:
                    findings.append(ConfigMigrationFinding(
                        section, "confirm_cancel", str(value).lower(), "ui.confirmations.cancel", start + rel + 1,
                        "Move the legacy cancel-confirmation boolean to [ui.confirmations].cancel.",
                        compatibility_id="config.ui.confirm_cancel",
                    ))
    return findings


def analyze_config_migration(path: Path) -> list[ConfigMigrationFinding]:
    return analyze_config_migration_text(path.read_text(encoding="utf-8")) if path.exists() else []


def _section_range(lines: list[str], section: str) -> tuple[int, int] | None:
    plain = [line.rstrip("\n") for line in lines]
    return next(((start, end) for start, end, name in _sections(plain) if name == section), None)


def _remove_table_key(lines: list[str], section: str, key: str) -> None:
    bounds = _section_range(lines, section)
    if bounds is None:
        return
    start, end = bounds
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for index in range(start + 1, min(end, len(lines))):
        if pattern.match(lines[index]):
            lines[index] = ""


def _set_table_key(lines: list[str], section: str, key: str, rendered_value: str, *, overwrite: bool = True, comment: str = "") -> None:
    bounds = _section_range(lines, section)
    rendered = f"{key} = {rendered_value}{(' ' + comment) if comment else ''}\n"
    if bounds is None:
        if lines and lines[-1] and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        if lines and any(line.strip() for line in lines):
            lines.append("\n")
        lines.extend([f"[{section}]\n", rendered])
        return
    start, end = bounds
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for index in range(start + 1, min(end, len(lines))):
        if pattern.match(lines[index]):
            if overwrite:
                indent = re.match(r"^(\s*)", lines[index]).group(1)
                lines[index] = indent + rendered
            return
    lines.insert(start + 1, rendered)


def _migrate_runner_aliases(text: str) -> str:
    findings = [f for f in analyze_config_migration_text(text) if f.replacement_key == "runner" and f.safe]
    if not findings:
        return text
    lines = text.splitlines(keepends=True)
    for finding in reversed(findings):
        bounds = _section_range(lines, finding.section)
        if bounds is None:
            continue
        start, end = bounds
        legacy_pattern = re.compile(rf"^\s*{re.escape(finding.key)}\s*=")
        index = next((i for i in range(start + 1, min(end, len(lines))) if legacy_pattern.match(lines[i])), None)
        if index is None:
            continue
        runner_pattern = re.compile(r"^\s*runner\s*=")
        runner_line = next((i for i in range(start + 1, min(end, len(lines))) if i != index and runner_pattern.match(lines[i])), None)
        if runner_line is not None:
            lines[index] = ""
            runner_value = _line_value(lines[runner_line], "runner") or finding.value
        else:
            lines[index] = re.sub(r"^(\s*)(kind|type)(\s*=)", r"\1runner\3", lines[index], count=1)
            runner_value = finding.value
        bounds = _section_range(lines, finding.section)
        start, end = bounds if bounds is not None else (0, len(lines))
        if not any(re.match(r"^\s*platform\s*=", lines[i]) for i in range(start + 1, min(end, len(lines)))):
            platform = _PLATFORM_BY_RUNNER.get(runner_value, runner_value if runner_value not in {"auto", "unknown"} else "shared")
            if platform:
                lines.insert((runner_line if runner_line is not None else index) + 1, f'platform = "{platform}" # added by devtool config migrate\n')
    return "".join(lines)


def migrate_config_text(text: str) -> tuple[str, list[ConfigMigrationFinding]]:
    findings = analyze_config_migration_text(text)
    if not findings:
        return text, []
    unsafe = [item for item in findings if not item.safe]
    migrated = _migrate_runner_aliases(text)
    lines = migrated.splitlines(keepends=True)

    default_finding = next((f for f in findings if f.compatibility_id == "config.project.default_target" and f.safe), None)
    if default_finding is not None:
        current = _table_value("".join(lines), "workspace", "default_target")
        if not current:
            _set_table_key(lines, "workspace", "default_target", repr(default_finding.value).replace("'", '"'), overwrite=False, comment="# moved by devtool config migrate")
        _remove_table_key(lines, "project", "default_target")

    destructive = next((f for f in findings if f.compatibility_id == "config.ui.confirm_destructive"), None)
    cancel = next((f for f in findings if f.compatibility_id == "config.ui.confirm_cancel"), None)
    if destructive is not None:
        if destructive.value == "false":
            for key in ("rollback", "delete_artifact", "snapshot_overwrite"):
                _set_table_key(lines, "ui.confirmations", key, '"never"', overwrite=True, comment="# migrated from confirm_destructive = false")
        _remove_table_key(lines, "ui", "confirm_destructive")
    if cancel is not None:
        if cancel.value == "true":
            _set_table_key(lines, "ui.confirmations", "cancel", '"always"', overwrite=True, comment="# migrated from confirm_cancel = true")
        _remove_table_key(lines, "ui", "confirm_cancel")

    # Unsafe findings are intentionally left untouched; callers can report and
    # require manual reconciliation without corrupting a valid canonical value.
    return "".join(lines), findings


def migration_report(path: Path) -> dict[str, Any]:
    findings = analyze_config_migration(path)
    return {
        "schema_version": CONFIG_MIGRATION_SCHEMA_VERSION,
        "path": str(path),
        "needs_migration": bool(findings),
        "safe_to_write": all(item.safe for item in findings),
        "findings": [item.as_dict() for item in findings],
    }
