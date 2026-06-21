from __future__ import annotations

import re
from pathlib import Path

from .models import Issue

_DOTNET_DIAGNOSTIC = re.compile(
    r"^(?P<file>/.+?)\((?P<line>\d+),(?P<col>\d+)(?:,\d+,\d+)?\): "
    r"(?P<severity>warning|error) (?P<code>[A-Za-z]+\d+): (?P<message>.+?)(?: \[.*\])?$"
)

_GCC_CLANG = re.compile(
    r"^(?P<file>[^:\s][^:]*)[:](?P<line>\d+)(?::(?P<col>\d+))?:\s+"
    r"(?P<severity>warning|error|fatal error):\s+(?P<message>.+)$",
    re.IGNORECASE,
)
_PYTEST_FAILURE = re.compile(r"^FAILED\s+(?P<file>[^:]+)(?:::(?P<node>.+))?\s+-\s+(?P<message>.+)$")
_UNITTEST_FAILURE = re.compile(r"^(FAIL|ERROR):\s+.+?\((?P<file>[^)]+)\)$")
_XUNIT_FAILED = re.compile(r"^\s*Failed\s+(?P<test_name>.+?)\s+\[(?P<duration_ms>\d+)\s+ms\]\s*$")
_XUNIT_ERROR_MESSAGE = re.compile(r"^\s*Error Message:\s*$", re.IGNORECASE)
_XUNIT_STACK_TRACE = re.compile(r"^\s*Stack Trace:\s*$", re.IGNORECASE)
_XUNIT_STACK_FRAME = re.compile(r"^\s*at\s+.+?\s+in\s+(?P<file>/.+?):line\s+(?P<line>\d+)\s*$")
_PY_STYLE = re.compile(
    r"^(?P<file>[^:]+):(?P<line>\d+):(?:(?P<col>\d+):)?\s+"
    r"(?P<code>[A-Z]+\d+)?\s*(?P<message>.+)$"
)
_MYPY = re.compile(
    r"^(?P<file>[^:]+):(?P<line>\d+):(?:(?P<col>\d+):)?\s+"
    r"(?P<severity>error|note|warning):\s+(?P<message>.+?)(?:\s+\[(?P<code>[^\]]+)\])?$",
    re.IGNORECASE,
)
_CARGO_RUSTC = re.compile(
    r"^(?P<severity>warning|error)(?:\[(?P<code>E\d+)\])?:\s+(?P<message>.+)$",
    re.IGNORECASE,
)
_CARGO_FILE = re.compile(r"^\s*-->\s+(?P<file>[^:]+):(?P<line>\d+):(?P<col>\d+)$")
_GENERIC_WARN_ERR = re.compile(r"\b(?P<severity>warning|error)\b[: ]+(?P<message>.+)", re.IGNORECASE)


def relativize(repo_root: Path, path_text: str) -> str:
    if not path_text or path_text == "-":
        return "-"
    try:
        path = Path(path_text).expanduser()
        if not path.is_absolute():
            path = (repo_root / path).resolve()
        else:
            path = path.resolve()
        return str(path.relative_to(repo_root))
    except Exception:
        return path_text


def parse_issues(repo_root: Path, command_name: str, output: str) -> list[Issue]:
    issues: list[Issue] = []
    cargo_pending: Issue | None = None
    lines = output.splitlines()
    index = 0

    while index < len(lines):
        stripped = lines[index].rstrip()

        match = _DOTNET_DIAGNOSTIC.match(stripped)
        if match:
            issues.append(Issue(
                severity=match.group("severity").lower(),
                command=command_name,
                file=relativize(repo_root, match.group("file")),
                code=match.group("code"),
                message=match.group("message").strip(),
                line=int(match.group("line")),
            ))
            index += 1
            continue

        match = _XUNIT_FAILED.match(stripped)
        if match:
            issue, next_index = _parse_xunit_failure(repo_root, command_name, lines, index)
            if issue is not None:
                issues.append(issue)
                index = next_index
                continue

        match = _PYTEST_FAILURE.match(stripped)
        if match:
            issues.append(Issue(
                "error",
                command_name,
                relativize(repo_root, match.group("file")),
                "PYTEST",
                match.group("message").strip(),
            ))
            index += 1
            continue

        match = _UNITTEST_FAILURE.match(stripped)
        if match:
            issues.append(Issue("error", command_name, relativize(repo_root, match.group("file")), "UNITTEST", stripped))
            index += 1
            continue

        match = _GCC_CLANG.match(stripped)
        if match:
            severity = match.group("severity").lower()
            if severity == "fatal error":
                severity = "error"
            issues.append(Issue(
                severity,
                command_name,
                relativize(repo_root, match.group("file")),
                "CC",
                match.group("message").strip(),
                line=int(match.group("line")),
            ))
            index += 1
            continue

        match = _MYPY.match(stripped)
        if match:
            severity = match.group("severity").lower()
            if severity == "note":
                severity = "warning"
            issues.append(Issue(
                severity,
                command_name,
                relativize(repo_root, match.group("file")),
                (match.group("code") or "MYPY").upper(),
                match.group("message").strip(),
                line=int(match.group("line")),
            ))
            index += 1
            continue

        match = _PY_STYLE.match(stripped)
        if match and match.group("code"):
            code = match.group("code").upper()
            severity = "warning"
            if code.startswith(("F", "E9")):
                severity = "error"
            issues.append(Issue(
                severity,
                command_name,
                relativize(repo_root, match.group("file")),
                code,
                match.group("message").strip(),
                line=int(match.group("line")),
            ))
            index += 1
            continue

        match = _CARGO_RUSTC.match(stripped)
        if match:
            cargo_pending = Issue(
                match.group("severity").lower(),
                command_name,
                "-",
                match.group("code") or "RUSTC",
                match.group("message").strip(),
            )
            issues.append(cargo_pending)
            index += 1
            continue

        match = _CARGO_FILE.match(stripped)
        if match and cargo_pending is not None:
            cargo_pending.file = relativize(repo_root, match.group("file"))
            cargo_pending.line = int(match.group("line"))
            index += 1
            continue

        match = _GENERIC_WARN_ERR.search(stripped)
        if match and command_name in {"build", "test", "restore", "package"}:
            message = match.group("message").strip()
            if stripped.strip().lower() not in {"error message:", "warning message:", "stack trace:"}:
                issues.append(Issue(
                    match.group("severity").lower(),
                    command_name,
                    "-",
                    "GENERIC",
                    message,
                ))

        index += 1

    return _dedupe_issues(issues)


def _parse_xunit_failure(
    repo_root: Path,
    command_name: str,
    lines: list[str],
    start_index: int,
) -> tuple[Issue | None, int]:
    index = start_index + 1
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index >= len(lines) or not _XUNIT_ERROR_MESSAGE.match(lines[index].rstrip()):
        return None, start_index + 1

    index += 1
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index >= len(lines):
        return None, start_index + 1

    message = lines[index].strip()
    file_text = "-"
    line_number: int | None = None
    index += 1

    while index < len(lines):
        stripped = lines[index].rstrip()
        if _XUNIT_STACK_TRACE.match(stripped):
            index += 1
            continue
        if not stripped:
            index += 1
            break
        match = _XUNIT_STACK_FRAME.match(stripped)
        if match:
            file_text = relativize(repo_root, match.group("file"))
            line_number = int(match.group("line"))
            index += 1
            continue
        if stripped.startswith("Failed!"):
            break
        index += 1

    return Issue("error", command_name, file_text, "XUNIT", message, line=line_number), index


def _dedupe_issues(issues: list[Issue]) -> list[Issue]:
    seen: set[tuple[str, str, str, str, str, int | None]] = set()
    unique: list[Issue] = []
    for issue in issues:
        key = (issue.severity, issue.command, issue.file, issue.code, issue.message, issue.line)
        if key in seen:
            continue
        seen.add(key)
        unique.append(issue)
    return unique
