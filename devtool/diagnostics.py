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
_ANDROID_LINT_DIAGNOSTIC = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+)(?::(?P<col>\d+))?:\s*"
    r"(?P<severity>Error|Warning):\s*(?P<message>.+?)\s+\[(?P<code>[A-Za-z][A-Za-z0-9_.-]*)\]$",
    re.IGNORECASE,
)
_GRADLE_JAVA_TOOLCHAIN = re.compile(
    r"Cannot find a Java installation on your machine .* matching: \{languageVersion=(?P<version>\d+),.*\}\.",
    re.IGNORECASE,
)
_GRADLE_METASPACE_EXHAUSTED = re.compile(
    r"running out of JVM Metaspace",
    re.IGNORECASE,
)
_GRADLE_METASPACE_OOM = re.compile(
    r"(?:OutOfMemoryError:\s*Metaspace|>\s*Metaspace\s*$)",
    re.IGNORECASE | re.MULTILINE,
)
_GRADLE_CONFIGURED_MEMORY = re.compile(
    r"currently configured max heap space is '(?P<heap>[^']+)' and the configured max metaspace is '(?P<meta>[^']+)'",
    re.IGNORECASE,
)
_LOW_STORAGE_WARNING = re.compile(
    r"^warning:\s*(?P<message>(?:Low storage before|Storage remains low after).+)$",
    re.IGNORECASE,
)
_ADB_PROBE_WARNING = re.compile(
    r"^warning:\s*(?P<message>ADB probe failed.+)$",
    re.IGNORECASE,
)
_GRADLE_MEMORY_GUARD = re.compile(
    r"(?:Emergency memory guard stopped Gradle|Devtool stopped validation) after "
    r"(?P<reason>[^:]+):\s*(?P<detail>.+)$",
    re.IGNORECASE,
)
_GRADLE_DAEMON_DISAPPEARED = re.compile(
    r"(Gradle build daemon disappeared unexpectedly|"
    r"daemon disappeared unexpectedly|"
    r"daemon has disappeared|"
    r"daemon process has disappeared|"
    r"daemon process was terminated|"
    r"daemon was killed|"
    r"daemon has been killed|"
    r"daemon unexpectedly stopped)",
    re.IGNORECASE,
)
_GRADLE_FAILED_TASK = re.compile(
    r"^>\s*Task\s+(?P<task>\S+)\s+FAILED$",
    re.IGNORECASE | re.MULTILINE,
)
_ANDROIDX_AAR_METADATA = re.compile(
    r"Dependency '(?P<dependency>[^']+)' requires libraries and applications that depend on it to compile against version (?P<version>\d+) or later of the Android APIs\.",
    re.IGNORECASE,
)
_ANDROIDX_AAR_CURRENT = re.compile(
    r"(?P<project>:[A-Za-z0-9._-]+)\s+is currently compiled against android-(?P<version>\d+)\.",
    re.IGNORECASE,
)
_GCC_CLANG = re.compile(
    r"^(?P<file>[^:\s][^:]*)[:](?P<line>\d+)(?::(?P<col>\d+))?:\s+"
    r"(?P<severity>warning|error|fatal error):\s+(?P<message>.+)$",
    re.IGNORECASE,
)
_PYTEST_FAILURE = re.compile(r"^FAILED\s+(?P<file>[^:]+)(?:::(?P<node>.+))?\s+-\s+(?P<message>.+)$")
_PYTEST_WARNING = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+):\s+"
    r"(?P<category>[A-Za-z_][A-Za-z0-9_.]*Warning):\s+(?P<message>.+)$"
)
_UNITTEST_FAILURE = re.compile(r"^(?P<kind>FAIL|ERROR):\s+(?P<label>.+?)\s+\((?P<test_id>[^)]+)\)$")
_XUNIT_FAILED = re.compile(r"^\s*Failed\s+(?P<test_name>.+?)\s+\[(?P<duration_ms>\d+)\s+ms\]\s*$")
_XUNIT_ERROR_MESSAGE = re.compile(r"^\s*Error Message:\s*$", re.IGNORECASE)
_XUNIT_STACK_TRACE = re.compile(r"^\s*Stack Trace:\s*$", re.IGNORECASE)
_XUNIT_STACK_FRAME = re.compile(r"^\s*at\s+.+?\s+in\s+(?P<file>/.+?):line\s+(?P<line>\d+)\s*$")
_PY_STYLE = re.compile(
    r"^(?P<file>[^:]+):(?P<line>\d+):(?:(?P<col>\d+):)?\s+"
    r"(?P<code>[A-Z]+\d+)?\s*(?P<message>.+)$"
)
_RUFF_FINDING = re.compile(
    r"^(?P<code>[A-Z]{1,5}\d{0,4})(?:\s+\[[^\]]+\])?\s+(?P<message>.+)$"
)
_RUFF_LOCATION = re.compile(r"^-->\s+(?P<file>.+?):(?P<line>\d+):(?P<col>\d+)$")
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
_GENERIC_WARN_ERR = re.compile(r"^(?P<severity>warning|error)\s*:\s*(?P<message>.+)$", re.IGNORECASE)

# Successful Python package builds can emit these informational messages.
# They are not actionable validation findings and should not become GENERIC
# warning cards.
_GENERIC_BUILD_NOISE_MESSAGES = {
    "Failed to hardlink files; falling back to full copy. This may lead to degraded performance.",
    "build_py: byte-compiling is disabled, skipping.",
    "install_lib: byte-compiling is disabled, skipping.",
}
_GRADLE_BENIGN_TERMUX_WARNING_PREFIXES = (
    "Warning: Unable to set daemon's environment variables to match the client because:",
)
_GRADLE_INFRA_WARNING_PREFIXES = (
    "WARNING: The option setting 'android.aapt2FromMavenOverride=",
)
_DOTNET_TEST_RESULT_LINE = re.compile(
    r"\b(?P<status>Passed|Failed)!\s+-\s+Failed:\s*(?P<failed>\d+),\s*Passed:\s*(?P<passed>\d+),\s*Skipped:\s*(?P<skipped>\d+),\s*Total:\s*(?P<total>\d+)",
    re.IGNORECASE,
)
_TEST_RUN_FAILED = re.compile(r"\bTest Run Failed\b", re.IGNORECASE)


def _make_relativizer(repo_root: Path):
    """Return a cheap lexical path formatter for one diagnostics batch.

    ``parse_issues`` may process tens of thousands of unique paths. Rebuilding
    ``Path`` objects and the normalized repository root for every match is
    needlessly expensive on Android storage, so the batch parser keeps one
    string-only closure. Paths outside the repository retain their original
    spelling, matching the historical public behavior.
    """

    root = os.path.normpath(os.path.abspath(os.path.expanduser(os.fspath(repo_root))))
    prefix = root.rstrip(os.sep) + os.sep

    def format_path(path_text: str) -> str:
        if not path_text or path_text == "-":
            return "-"
        try:
            expanded = os.path.expanduser(path_text)
            if os.path.isabs(expanded):
                normalized = os.path.normpath(expanded)
                if normalized == root:
                    return "."
                if normalized.startswith(prefix):
                    return normalized[len(prefix):]
                return path_text

            normalized = os.path.normpath(expanded)
            if normalized == ".." or normalized.startswith(".." + os.sep):
                return path_text
            return normalized
        except (OSError, ValueError):
            return path_text

    return format_path


def relativize(repo_root: Path, path_text: str) -> str:
    return _make_relativizer(repo_root)(path_text)


def go_test_issues(repo_root: Path, command_name: str, report: object) -> list[Issue]:
    """Convert an authoritative/fallback Go test report into diagnostics."""
    relative_path = _make_relativizer(repo_root)
    failures = getattr(report, "failures", [])
    issues: list[Issue] = []
    for failure in failures if isinstance(failures, list) else []:
        test_label = str(getattr(failure, "test", "") or "").strip()
        message = str(getattr(failure, "message", "") or "Go test failed").strip()
        if test_label:
            message = f"{test_label}: {message}"
        issues.append(Issue(
            "error",
            command_name,
            relative_path(str(getattr(failure, "file", "-") or "-")),
            "GO_TEST" if test_label else "GO_TEST_PACKAGE",
            message,
            line=getattr(failure, "line", None),
        ))
    return issues


def parse_issues(repo_root: Path, command_name: str, output: str) -> list[Issue]:
    issues: list[Issue] = []
    cargo_pending: Issue | None = None
    dotnet_failed_summary: tuple[int, int, int, int] | None = None
    dotnet_run_failed = False
    aar_dependencies: list[tuple[str, int]] = []
    aar_current: tuple[str, int] | None = None
    lines = output.splitlines()
    relative_path = _make_relativizer(repo_root)
    metaspace_oom = bool(_GRADLE_METASPACE_OOM.search(output))
    configured_memory = _GRADLE_CONFIGURED_MEMORY.search(output)
    if metaspace_oom:
        detail = "Gradle JVM metaspace was exhausted"
        if configured_memory:
            detail += (
                f" at the effective {configured_memory.group('meta')} max metaspace "
                f"(effective max heap {configured_memory.group('heap')})"
            )
        if "-Dorg.gradle.jvmargs=" not in output:
            detail += "; Devtool did not pass org.gradle.jvmargs, so the effective cap came from Gradle/project/user configuration or Gradle defaults"
        failed_task = _GRADLE_FAILED_TASK.search(output)
        if failed_task:
            detail += f"; affected task {failed_task.group('task')}"
        issues.append(Issue(
            "error",
            command_name,
            "-",
            "GRADLE_METASPACE_OOM",
            detail + ".",
        ))
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
                    relative_path(match.group("file")),
                    "PYTEST_FAILURE",
                    match.group("message").strip(),
                ))
                index += 1
                continue

        # Pytest's warnings summary uses a source-location form rather than a
        # literal ``warning:`` prefix.  Parse it into the same structured issue
        # stream used by the HUD, JSON, summary and clipboard so warning counts
        # do not diverge between surfaces.
        pytest_warning = _PYTEST_WARNING.match(stripped)
        if pytest_warning:
            issues.append(Issue(
                "warning",
                command_name,
                relative_path(pytest_warning.group("file")),
                "PYTEST_WARNING",
                f"{pytest_warning.group('category')}: {pytest_warning.group('message').strip()}",
                line=int(pytest_warning.group("line")),
            ))
            index += 1
            continue

        if stripped.upper().startswith(("ERROR: ", "WARNING: ")):
            match = _ANDROID_TOOL_DIAGNOSTIC.match(stripped)
            if match:
                issues.append(Issue(
                    match.group("severity").lower(),
                    command_name,
                    relative_path(match.group("file")),
                    "ANDROID",
                    match.group("message").strip(),
                    line=int(match.group("line")),
                ))
                index += 1
                continue

        match = _RUFF_FINDING.match(stripped)
        if match and index + 1 < len(lines):
            location = _RUFF_LOCATION.match(lines[index + 1].strip())
            if location:
                issues.append(Issue(
                    "warning",
                    command_name,
                    relative_path(location.group("file")),
                    match.group("code").upper(),
                    match.group("message").strip(),
                    line=int(location.group("line")),
                ))
                index += 2
                continue

        if stripped.startswith("/") and "): " in stripped:
            match = _DOTNET_DIAGNOSTIC.match(stripped)
            if match:
                issues.append(Issue(
                    severity=match.group("severity").lower(),
                    command=command_name,
                    file=relative_path(match.group("file")),
                    code=match.group("code"),
                    message=match.group("message").strip(),
                    line=int(match.group("line")),
                ))
                index += 1
                continue

        if _GRADLE_METASPACE_EXHAUSTED.search(stripped):
            if not metaspace_oom:
                message = "Gradle reports JVM metaspace pressure"
                if configured_memory:
                    message += f" with effective max metaspace {configured_memory.group('meta')} and max heap {configured_memory.group('heap')}"
                message += "; the limit may come from Devtool, org.gradle.jvmargs, project/user Gradle properties, environment options, or Gradle defaults."
                issues.append(Issue(
                    "warning",
                    command_name,
                    "-",
                    "GRADLE_METASPACE_PRESSURE",
                    message,
                ))
            index += 1
            continue

        match = _LOW_STORAGE_WARNING.match(stripped)
        if match:
            issues.append(Issue(
                "warning", command_name, "-", "LOW_STORAGE", match.group("message").strip()
            ))
            index += 1
            continue

        match = _ADB_PROBE_WARNING.match(stripped)
        if match:
            issues.append(Issue(
                "warning", command_name, "-", "ADB_PROBE", match.group("message").strip()
            ))
            index += 1
            continue

        match = _GRADLE_MEMORY_GUARD.search(stripped)
        if match:
            issues.append(Issue(
                "error",
                command_name,
                "-",
                "GRADLE_MEMORY_GUARD",
                (
                    "Gradle was stopped by devtool memory guard after "
                    f"{match.group('reason').strip()}: {match.group('detail').strip()}"
                ),
            ))
            index += 1
            continue

        if _GRADLE_DAEMON_DISAPPEARED.search(stripped):
            if metaspace_oom:
                index += 1
                continue
            issues.append(Issue(
                "error",
                command_name,
                "-",
                "GRADLE_DAEMON_DISAPPEARED",
                (
                    "Gradle daemon disappeared or was killed before the build completed. "
                    "This is an infrastructure/resource interruption, not a source compile error."
                ),
            ))
            index += 1
            continue

        match = _GRADLE_FAILED_TASK.match(stripped)
        if match:
            if metaspace_oom:
                index += 1
                continue
            issues.append(Issue(
                "error",
                command_name,
                "-",
                "GRADLE_TASK",
                f"Gradle task failed: {match.group('task')}",
            ))
            index += 1
            continue

        match = _GRADLE_JAVA_TOOLCHAIN.search(stripped)
        if match:
            issues.append(Issue(
                "error",
                command_name,
                "-",
                "JAVA_TOOLCHAIN",
                f"Gradle could not find a Java {match.group('version')} toolchain.",
            ))
            index += 1
            continue

        match = _ANDROIDX_AAR_METADATA.search(stripped)
        if match:
            dependency = match.group("dependency")
            version = int(match.group("version"))
            aar_dependencies.append((dependency, version))
            issues.append(Issue(
                "error",
                command_name,
                "-",
                "AAR_METADATA",
                f"{dependency} requires compileSdk {version} or later.",
            ))
            index += 1
            continue

        match = _ANDROIDX_AAR_CURRENT.search(stripped)
        if match:
            aar_current = (match.group("project"), int(match.group("version")))
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
                    relative_path(match.group("file")),
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
                    relative_path(match.group("file")),
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
                file=relative_path(match.group("file")),
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
                relative_path(match.group("file")),
                "PYTEST_FAILURE",
                match.group("message").strip(),
            ))
            index += 1
            continue

        match = _UNITTEST_FAILURE.match(stripped)
        if match:
            test_id = match.group("test_id").strip()
            label = match.group("label").strip()
            issues.append(Issue(
                "error", command_name, "-", "UNITTEST",
                f"{test_id}: {label}" if label else test_id,
            ))
            index += 1
            continue

        match = _KOTLIN_DIAGNOSTIC.match(stripped)
        if match:
            line_number = int(match.group("line") or match.group("line_paren"))
            severity = "error" if match.group("prefix").lower() == "e" else "warning"
            issues.append(Issue(
                severity,
                command_name,
                relative_path(match.group("file")),
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
                relative_path(match.group("file")),
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
                relative_path(match.group("file")),
                "MANIFEST",
                match.group("message").strip(),
                line=int(match.group("line")),
            ))
            index += 1
            continue

        match = _ANDROID_LINT_DIAGNOSTIC.match(stripped)
        if match:
            issues.append(Issue(
                match.group("severity").lower(),
                command_name,
                relative_path(match.group("file")),
                match.group("code"),
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
                relative_path(match.group("file")),
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
                relative_path(match.group("file")),
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
                relative_path(match.group("file")),
                code,
                match.group("message").strip(),
                line=int(match.group("line")),
            ))
            index += 1
            continue

        if any(
            prefix in stripped
            for prefix in _GRADLE_BENIGN_TERMUX_WARNING_PREFIXES
        ):
            # This warning is often harmless on Termux, but it is still real
            # Gradle output. Devtool's summary/clipboard contract is to retain
            # every warning/error rather than silently discarding diagnostics.
            warning_start = min(
                stripped.index(prefix)
                for prefix in _GRADLE_BENIGN_TERMUX_WARNING_PREFIXES
                if prefix in stripped
            )
            warning_lines = [stripped[warning_start:].strip()]
            lookahead = index + 1
            while lookahead < len(lines):
                continuation = lines[lookahead].strip()
                if not continuation:
                    break
                if lines[lookahead][:1].isspace():
                    warning_lines.append(continuation)
                    lookahead += 1
                    continue
                break
            issues.append(Issue(
                "warning",
                command_name,
                "-",
                "GRADLE_ENVIRONMENT",
                " ".join(warning_lines),
            ))
            index = lookahead
            continue

        if any(stripped.startswith(prefix) for prefix in _GRADLE_INFRA_WARNING_PREFIXES):
            issues.append(Issue(
                "warning",
                command_name,
                "-",
                "GRADLE_INFRA",
                stripped.split(":", 1)[-1].strip(),
            ))
            index += 1
            continue

        match = _CARGO_RUSTC.match(stripped)
        rust_context = bool(
            match
            and (
                "cargo" in command_name.lower()
                or "rust" in command_name.lower()
                or any(
                    _CARGO_FILE.match(lines[lookahead].rstrip())
                    for lookahead in range(index + 1, min(index + 4, len(lines)))
                )
            )
        )
        if match and rust_context:
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
            cargo_pending.file = relative_path(match.group("file"))
            cargo_pending.line = int(match.group("line"))
            index += 1
            continue

        # Gradle deprecation notices commonly contain wording such as
        # "This will fail with an error in Gradle 10". The word "error"
        # describes a future Gradle release: it is not the severity of the
        # current invocation. Route these before the generic error matcher.
        gradle_deprecation_text = stripped.lower()

        if (
            command_name in {"build", "test", "restore", "package", "lint"}
            and "has been deprecated" in gradle_deprecation_text
            and (
                "gradle 10" in gradle_deprecation_text
                or "will fail with an error in gradle" in gradle_deprecation_text
            )
        ):
            issues.append(
                Issue(
                    "warning",
                    command_name,
                    "-",
                    "GRADLE_DEPRECATION",
                    stripped,
                )
            )
            index += 1
            continue

        match = _GENERIC_WARN_ERR.search(stripped)
        if match and command_name in {"build", "test", "restore", "package", "lint"}:
            message = match.group("message").strip()
            if (
                message not in _GENERIC_BUILD_NOISE_MESSAGES
                and stripped.strip().lower() not in {"error message:", "warning message:", "stack trace:"}
            ):
                issues.append(Issue(
                    match.group("severity").lower(),
                    command_name,
                    "-",
                    "GENERIC",
                    message,
                ))

        index += 1

    # Go's normal test stream does not contain the word "error" for an
    # assertion failure, so generic diagnostic parsing cannot classify it.
    # Devtool-owned Go tests use a JSON transport rendered back into the human
    # stream; custom plain ``go test`` output is supported by the same parser.
    if (
        "go-test package" in output
        or "--- FAIL:" in output
        or command_name.lower() in {"go-validation", "go-test", "test"} and re.search(r"(?m)^FAIL\s+\S+", output)
    ):
        from .go_test import parse_go_test_report
        go_report = parse_go_test_report(output)
        issues.extend(go_test_issues(repo_root, command_name, go_report))

    has_error = any(issue.severity == "error" for issue in issues)
    command_is_test = "test" in command_name.lower() or command_name.lower() == "go-validation"
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

    if aar_dependencies and aar_current:
        current_project, current_compile_sdk = aar_current
        required = max(version for _, version in aar_dependencies)
        if current_compile_sdk < required:
            dependency_list = ", ".join(name for name, version in aar_dependencies if version == required)
            issues.append(Issue(
                "error",
                command_name,
                current_project.lstrip(":") or "-",
                "AAR_METADATA",
                (
                    f"{current_project} is compiled against android-{current_compile_sdk}, "
                    f"but {dependency_list or 'AndroidX dependencies'} require compileSdk {required} or later."
                ),
            ))

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
    if returncode == 130 or any(marker in text for marker in (
        "interrupted by user",
        "cancelled from devtool control center",
        "canceled from devtool control center",
        "devtool validation supervisor stopped process: interrupted:",
    )):
        return "interrupted"
    compile_signal = (
        "compile" in name
        or "compile" in text
        or " compilation failed" in text
        or "compilation error" in text
        or "compiledebugkotlin" in text
        or "compiledebugjava" in text
        or "argument type mismatch" in text
        or "unresolved reference" in text
    )
    explicit_sdk_failure = (
        "failed to find package" in text
        or "package installation failed" in text
        or "sdk package" in text and ("not found" in text or "not installed" in text)
        or "android sdk" in text and ("not found" in text or "not installed" in text)
        or "sdkmanager" in text and ("not found" in text or "could not execute" in text)
    )
    if explicit_sdk_failure and not compile_signal:
        return "missing_sdk"
    if "nuget" in text or "unable to load the service index" in text or "failed to restore" in text:
        return "nuget_failure"
    if "wine" in text or name.startswith("wine"):
        return "wine_issue"
    if "exec format error" in text or "wrong architecture" in text or "bad cpu type" in text or "arm64" in text and "x64" in text:
        return "arm_mismatch"
    if "outofmemoryerror: metaspace" in text or "running out of jvm metaspace" in text:
        return "metaspace_oom"
    if (
        "emergency memory guard stopped gradle after" in text
        or "devtool stopped validation after hard emergency floor" in text
        or "devtool stopped validation after corroborated sustained memory pressure" in text
    ):
        return "resource_interrupted"
    if (
        "gradle build daemon disappeared unexpectedly" in text
        or "daemon disappeared unexpectedly" in text
        or "daemon has disappeared" in text
        or "daemon process has disappeared" in text
        or "daemon process was terminated" in text
        or "daemon was killed" in text
        or "daemon has been killed" in text
        or "daemon unexpectedly stopped" in text
    ):
        return "resource_interrupted"
    if "restore" in name:
        return "restore_failure"
    if (
        "test" in name
        or name == "go-validation"
        or "test run failed" in text
        or "--- fail:" in text
        or "<== go-test package" in text and " fail" in text
        or re.search(r"\bfailed:\s*[1-9]\d*\b", text)
    ):
        return "test_failure"
    if "build" in name or compile_signal:
        return "compile_failure"
    if "lint" in name:
        return "lint_failure"
    if "run" in name:
        return "runtime_failure"
    if "apply" in name or "artifact" in text or "archive" in text:
        return "artifact_failure"
    return "unknown_failure"
