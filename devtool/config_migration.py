from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import re
from typing import Any

CONFIG_MIGRATION_SCHEMA_VERSION = 1

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

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sections(lines: list[str]) -> list[tuple[int, int, str]]:
    starts: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        match = re.match(r"^\s*\[([^]]+)\]\s*(?:#.*)?$", line)
        if match:
            starts.append((index, match.group(1).strip()))
    result: list[tuple[int, int, str]] = []
    for offset, (start, name) in enumerate(starts):
        end = starts[offset + 1][0] if offset + 1 < len(starts) else len(lines)
        result.append((start, end, name))
    return result


def analyze_config_migration_text(text: str) -> list[ConfigMigrationFinding]:
    lines = text.splitlines()
    findings: list[ConfigMigrationFinding] = []
    for start, end, section in _sections(lines):
        if section != "project" and not section.startswith("targets."):
            continue
        body = lines[start + 1:end]
        canonical_runner = any(re.match(r"^\s*runner\s*=", line) for line in body)
        for rel, line in enumerate(body, start=1):
            match = re.match(r'^\s*(kind|type)\s*=\s*["\']([^"\']+)["\']', line)
            if not match:
                continue
            key, value = match.group(1), match.group(2).strip().lower()
            if section == "project" and key != "kind":
                continue
            if canonical_runner:
                message = f"Legacy {key} is redundant because runner is already declared."
            else:
                message = f"Replace legacy {key} with canonical runner; platform remains independent."
            findings.append(ConfigMigrationFinding(section, key, value, "runner", start + rel + 1, message))
    return findings


def analyze_config_migration(path: Path) -> list[ConfigMigrationFinding]:
    return analyze_config_migration_text(path.read_text(encoding="utf-8")) if path.exists() else []


def migrate_config_text(text: str) -> tuple[str, list[ConfigMigrationFinding]]:
    findings = analyze_config_migration_text(text)
    if not findings:
        return text, []
    lines = text.splitlines(keepends=True)
    plain = [line.rstrip("\n") for line in lines]
    section_ranges = _sections(plain)
    inserts: list[tuple[int, str]] = []
    for finding in findings:
        index = finding.line - 1
        target_range = next(((start, end) for start, end, name in section_ranges if name == finding.section), None)
        if not target_range:
            continue
        start, end = target_range
        runner_line = next((
            i for i in range(start + 1, min(end, len(lines)))
            if i != index and re.match(r"^\s*runner\s*=", lines[i])
        ), None)
        if runner_line is not None:
            # Canonical runner already exists: remove the redundant legacy alias
            # instead of creating a duplicate runner key.
            lines[index] = ""
            runner_match = re.match(r'^\s*runner\s*=\s*["\']([^"\']+)["\']', lines[runner_line])
            runner_value = runner_match.group(1).strip().lower() if runner_match else finding.value
        else:
            lines[index] = re.sub(r"^(\s*)(kind|type)(\s*=)", r"\1runner\3", lines[index], count=1)
            runner_value = finding.value
        has_platform = any(re.match(r"^\s*platform\s*=", lines[i]) for i in range(start + 1, min(end, len(lines))))
        platform = _PLATFORM_BY_RUNNER.get(runner_value, runner_value if runner_value not in {"auto", "unknown"} else "shared")
        if not has_platform and platform:
            insert_at = (runner_line + 1) if runner_line is not None else index + 1
            inserts.append((insert_at, f'platform = "{platform}" # added by devtool config migrate\n'))
    # Section line indexes are from the original file, so apply additions from
    # bottom to top to keep every insertion location stable.
    for index, value in sorted(dict(inserts).items(), reverse=True):
        lines.insert(index, value)
    return "".join(lines), findings


def migration_report(path: Path) -> dict[str, Any]:
    findings = analyze_config_migration(path)
    return {
        "schema_version": CONFIG_MIGRATION_SCHEMA_VERSION,
        "path": str(path),
        "needs_migration": bool(findings),
        "findings": [item.as_dict() for item in findings],
    }
