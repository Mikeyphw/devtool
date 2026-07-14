from __future__ import annotations

import os
import re
from pathlib import Path

from .models import Issue

_DOTNET_DIAGNOSTIC = re.compile(
    r"^(?P<file>/.+?)\((?P<line>\d+),(?P<col>\d+)(?:,\d+,\d+)?\): "
    r"(?P<severity>warning|error) (?P<code>[A-Za-z]+\d+): (?P<message>.+?)(?: \[.*\])?$"
)


_KOTLIN_DIAGNOSTIC = re.compile(
    r"^(?P<prefix>[ew]):\s+(?:file://)?(?P<file>.+?):(?:\s*\((?P<line_paren>\d+),\s*(?P<col_paren>\d+)\)|(?P<line>\d+):(?P<col>\d+))[:\s]+(?P<message>.+)$",
    re.IGNORECASE,
)
_ANDROID_TOOL_DIAGNOSTIC = re.compile(
    r"^(?P<severity>ERROR|WARNING):\s+(?P<file>.+?):(?P<line>\d+)(?::(?P<col>\d+))?:\s*(?P<message>.+)$",
    re.IGNORECASE,
)
_ANDROID_MANIFEST_DIAGNOSTIC = re.compile(
    r"^(?P<file>.+?AndroidManifest\.xml):(?P<line>\d+):(?P<col>\d+)(?:-\d+)?\s+(?P<severity>Error|Warning):\s*(?P<message>.+)$",
    re.IGNORECASE,
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
_DOTNET_TEST_RESULT_LINE = re.compile(
    r"\b(?P<status>Passed|Failed)!\s+-\s+Failed:\s*(?P<failed>\d+),\s*Passed:\s*(?P<passed>\d+),\s*Skipped:\s*(?P<skipped>\d+),\s*Total:\s*(?P<total>\d+)",
    re.IGNORECASE,
)
_TEST_RUN_FAILED = re.compile(r"\bTest Run Failed\b", re.IGNORECASE)


def relativize(repo_root: Path, path_text: str) -> str:
    if not path_text or path_text == "-":
        return "-"
    try:
        # Diagnostic paths are display metadata, not filesystem authorities.
        # Normalize lexically instead of resolving every unique path through
        # the filesystem; large builds commonly contain tens of thousands of
        # distinct diagnostics and Path.resolve() dominates parse time.
        root = Path(os.path.abspath(os.path.expanduser(os.fspath(repo_root))))
        expanded = os.path.expanduser(path_text)
        path = Path(os.path.normpath(expanded if os.path.isabs(expanded) else os.path.join(root, expanded)))
        return str(path.relative_to(root))
    except (OSError, ValueError):
        return path_text


def parse_issues(repo_root: Path, command_name: str, output: str) -> list[Issue]:
    issues: list[Issue] = []
    cargo_pending: Issue | None = None
    dotnet_failed_summary: tuple[int, int, int, int] | None = None
    dotnet_run_failed = False
    lines = output.splitlines()
    index = 0

    while index < len(lines):
        stripped = lines[index].rstrip()

        # Route common diagnostic families before the generic matcher chain.
        # Large builds often contain tens of thousands of lines; cheap prefix
        # and extension checks prevent every line from paying for every regex.
        if stripped.startswith("FAILED "):
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

        if stripped.upper().startswith(("ERROR: ", "WARNING: ")):
            match = _ANDROID_TOOL_DIAGNOSTIC.match(stripped)
            if match:
                issues.append(Issue(
                    match.group("severity").lower(),
                    command_name,
                    relativize(repo_root, match.group("file")),
                    "ANDROID",
                    match.group("message").strip(),
                    line=int(match.group("line")),
                ))
                index += 1
                continue

        if stripped.startswith("/") and "): " in stripped:
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

        if ".py:" in stripped and (": error:" in stripped or ": warning:" in stripped or ": note:" in stripped):
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

        if any(ext in stripped for ext in (".c:", ".cc:", ".cpp:", ".cxx:", ".h:", ".hpp:", ".rs:")):
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


        match = _DOTNET_TEST_RESULT_LINE.search(stripped)
        if match:
            failed = int(match.group("failed"))
            if failed > 0 or match.group("status").lower() == "failed":
                dotnet_failed_summary = (
                    failed,
                    int(match.group("passed")),
                    int(match.group("skipped")),
                    int(match.group("total")),
                )
            index += 1
            continue

        if _TEST_RUN_FAILED.search(stripped):
            dotnet_run_failed = True
            index += 1
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

        match = _KOTLIN_DIAGNOSTIC.match(stripped)
        if match:
            line_number = int(match.group("line") or match.group("line_paren"))
            severity = "error" if match.group("prefix").lower() == "e" else "warning"
            issues.append(Issue(
                severity,
                command_name,
                relativize(repo_root, match.group("file")),
                "KOTLIN",
                match.group("message").strip(),
                line=line_number,
            ))
            index += 1
            continue

        match = _ANDROID_TOOL_DIAGNOSTIC.match(stripped)
        if match:
            issues.append(Issue(
                match.group("severity").lower(),
                command_name,
                relativize(repo_root, match.group("file")),
                "ANDROID",
                match.group("message").strip(),
                line=int(match.group("line")),
            ))
            index += 1
            continue

        match = _ANDROID_MANIFEST_DIAGNOSTIC.match(stripped)
        if match:
            issues.append(Issue(
                match.group("severity").lower(),
                command_name,
                relativize(repo_root, match.group("file")),
                "MANIFEST",
                match.group("message").strip(),
                line=int(match.group("line")),
            ))
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
        if match and command_name in {"build", "test", "restore", "package", "lint"}:
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

    has_error = any(issue.severity == "error" for issue in issues)
    command_is_test = "test" in command_name.lower()
    if command_is_test and not has_error:
        if dotnet_failed_summary is not None:
            failed, passed, skipped, total = dotnet_failed_summary
            issues.append(Issue(
                "error",
                command_name,
                "-",
                "VSTEST",
                f"{failed} failed, {passed} passed, {skipped} skipped, {total} total",
            ))
        elif dotnet_run_failed:
            issues.append(Issue("error", command_name, "-", "VSTEST", "Test Run Failed"))

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


def classify_failure(command_name: str, output: str, returncode: int) -> str:
    if returncode == 0:
        return ""
    text = output.lower()
    name = command_name.lower()
    if "sdk" in text and ("not found" in text or "not installed" in text or "could not execute" in text):
        return "missing_sdk"
    if "nuget" in text or "unable to load the service index" in text or "failed to restore" in text:
        return "nuget_failure"
    if "wine" in text or name.startswith("wine"):
        return "wine_issue"
    if "exec format error" in text or "wrong architecture" in text or "bad cpu type" in text or "arm64" in text and "x64" in text:
        return "arm_mismatch"
    if "restore" in name:
        return "restore_failure"
    if "build" in name or "compile" in text or " compilation failed" in text:
        return "compile_failure"
    if "test" in name or "failed!" in text or "failed " in text:
        return "test_failure"
    if "lint" in name:
        return "lint_failure"
    if "run" in name:
        return "runtime_failure"
    if "apply" in name or "artifact" in text or "archive" in text:
        return "artifact_failure"
    return "unknown_failure"
