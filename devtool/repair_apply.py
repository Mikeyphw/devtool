from __future__ import annotations

import re
from pathlib import Path

from .models import OperationResult
from .repair_policy import RepairFinding, build_repair_report


_CS_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def apply_auto_source_repairs(repo_root: Path, results: list[OperationResult]) -> tuple[list[RepairFinding], dict[Path, str]]:
    """Apply the narrow deterministic repairs that are safe to rewrite in place."""
    report = build_repair_report(results)
    backups: dict[Path, str] = {}
    applied: list[RepairFinding] = []
    cache: dict[Path, str] = {}

    for finding in report.auto_fix:
        path = (repo_root / finding.file).resolve()
        if not path.exists() or not path.is_file():
            continue
        current = cache.get(path)
        if current is None:
            try:
                current = path.read_text(encoding="utf-8")
            except Exception:
                continue
            cache[path] = current
        updated, changed = _repair_source_text(current, finding)
        if not changed:
            continue
        if path not in backups:
            backups[path] = current
        path.write_text(updated, encoding="utf-8")
        cache[path] = updated
        applied.append(finding)

    return applied, backups


def restore_source_backups(backups: dict[Path, str]) -> None:
    for path, text in backups.items():
        path.write_text(text, encoding="utf-8")


def _repair_source_text(text: str, finding: RepairFinding) -> tuple[str, bool]:
    if not text:
        return text, False
    lines = text.splitlines(keepends=True)
    index = max(0, (finding.line or 1) - 1)
    methods = {
        "CS1061": _repair_string_path_api,
        "CS0121": _repair_split_ambiguity,
        "CS0136": _repair_shadowed_local,
        "CS8076": _repair_interpolation_ternary,
        "CS8361": _repair_interpolation_ternary,
        "CS8514": _repair_interpolation_ternary,
        "CS8516": _repair_interpolation_ternary,
    }
    repair = methods.get(finding.code.upper())
    if repair is None:
        return text, False

    for offset in (0, -1, 1, -2, 2):
        line_index = index + offset
        if line_index < 0 or line_index >= len(lines):
            continue
        updated = repair(lines[line_index])
        if updated is None or updated == lines[line_index]:
            continue
        lines[line_index] = updated
        return "".join(lines), True

    if finding.code.upper() == "CS0136":
        updated = _repair_shadowed_local_block(lines, finding)
        if updated is not None:
            return "".join(updated), True

    return text, False


def _repair_string_path_api(line: str) -> str | None:
    pattern = re.compile(r"\b(?P<receiver>[A-Za-z_][A-Za-z0-9_\.]*)\.(?P<method>GetFullPath|GetDirectoryName)\(\s*\)")

    def _replace(match: re.Match[str]) -> str:
        receiver = match.group("receiver")
        method = match.group("method")
        return f"System.IO.Path.{method}({receiver})"

    updated, count = pattern.subn(_replace, line, count=1)
    return updated if count else None


def _repair_split_ambiguity(line: str) -> str | None:
    pattern = re.compile(r"\.Split\(\s*(?P<arg>[^()]+?)\s*\)")
    match = pattern.search(line)
    if not match:
        return None
    arg = match.group("arg").strip()
    if arg.startswith("new[]") or arg.startswith("new []") or arg.startswith("new char") or arg.startswith("new string"):
        return None
    replacement = f".Split(new[] {{ {arg} }})"
    return line[: match.start()] + replacement + line[match.end() :]


def _repair_interpolation_ternary(line: str) -> str | None:
    if "$\"" not in line or "{" not in line or "}" not in line or "?" not in line or ":" not in line:
        return None

    def _wrap(match: re.Match[str]) -> str:
        body = match.group("body")
        if body.startswith("(") and body.endswith(")"):
            return match.group(0)
        return "{(" + body + ")}"

    pattern = re.compile(r"\{(?P<body>[^{}]*\?[^{}:]+:[^{}]+)\}")
    updated, count = pattern.subn(_wrap, line, count=1)
    return updated if count else None


def _repair_shadowed_local(line: str) -> str | None:
    if "var " not in line and "string " not in line and "int " not in line and "bool " not in line and "object " not in line:
        return None
    pattern = re.compile(r"\b(?P<type>var|string|int|bool|object|char|byte|short|long|float|double|decimal|dynamic)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\b")
    match = pattern.search(line)
    if not match:
        return None
    name = match.group("name")
    new_name = _next_rename(name, line)
    return line.replace(f" {name}", f" {new_name}", 1)


def _repair_shadowed_local_block(lines: list[str], finding: RepairFinding) -> list[str] | None:
    name = _shadow_name_from_message(finding.message)
    if not name:
        return None
    index = max(0, (finding.line or 1) - 1)
    start, end = _find_brace_block(lines, index)
    if start is None or end is None:
        return None

    declaration_index = None
    declaration_pattern = re.compile(rf"\b(?P<type>var|string|int|bool|object|char|byte|short|long|float|double|decimal|dynamic|[A-Za-z_][A-Za-z0-9_<>\.\[\]\?]*)\s+{re.escape(name)}\b")
    for line_index in range(start, min(len(lines), index + 1)):
        if declaration_pattern.search(lines[line_index]):
            declaration_index = line_index
            break
    if declaration_index is None:
        return None

    new_name = _next_rename(name, "".join(lines[start : end + 1]))
    updated = list(lines)
    word_re = re.compile(rf"\b{re.escape(name)}\b")
    for line_index in range(declaration_index, end + 1):
        updated[line_index] = word_re.sub(new_name, updated[line_index])
    return updated


def _find_brace_block(lines: list[str], index: int) -> tuple[int | None, int | None]:
    depth = 0
    start = None
    for line_index in range(index, -1, -1):
        line = lines[line_index]
        depth += line.count("}")
        depth -= line.count("{")
        if depth <= 0 and "{" in line:
            start = line_index
            break
    if start is None:
        return None, None
    depth = 0
    for line_index in range(start, len(lines)):
        line = lines[line_index]
        depth += line.count("{")
        depth -= line.count("}")
        if depth <= 0 and line_index > start:
            return start, line_index
    return start, None


def _shadow_name_from_message(message: str) -> str | None:
    match = re.search(r"named '([^']+)'", message)
    if match:
        return match.group(1)
    match = re.search(r"parameter named '([^']+)'", message)
    if match:
        return match.group(1)
    return None


def _next_rename(name: str, text: str) -> str:
    base = f"{name}_1"
    candidate = base
    counter = 1
    while re.search(rf"\b{re.escape(candidate)}\b", text):
        counter += 1
        candidate = f"{name}_{counter}"
    return candidate
