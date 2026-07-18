from __future__ import annotations

"""Structured Gradle/JUnit and Android Lint report collection.

Gradle console output is useful for humans, but XML/SARIF reports are the
source of truth for final test and lint totals.  This module intentionally has
no Gradle process dependencies so it can also parse reports produced in a
Termux chroot after the command returns to the host process.
"""

from dataclasses import dataclass
import json
from pathlib import Path
import re
import time
from typing import Any, Iterable
import xml.etree.ElementTree as ET

from .diagnostics import relativize
from .models import Issue


@dataclass(slots=True)
class ReportCollection:
    metadata: dict[str, Any]
    issues: list[Issue]


def _task_leaf(task: str) -> str:
    return str(task or "").strip().split(":")[-1]


def _recent(path: Path, started_at: float | None) -> bool:
    if started_at is None:
        return True
    try:
        return path.stat().st_mtime + 2.0 >= started_at
    except OSError:
        return False


def _choose_task_reports(
    files: Iterable[Path],
    *,
    tasks: Iterable[str],
    marker: str,
    started_at: float | None,
) -> list[Path]:
    candidates = sorted({path.resolve() for path in files if path.is_file()})
    leaves = {_task_leaf(task) for task in tasks if _task_leaf(task)}
    matching: list[Path] = []
    if leaves:
        for path in candidates:
            normalized = path.as_posix()
            if any(f"/{marker}/{leaf}/" in normalized for leaf in leaves):
                matching.append(path)
    if matching:
        return matching

    recent = [path for path in candidates if _recent(path, started_at)]
    if recent:
        return recent
    return candidates


def _float(value: object) -> float:
    try:
        return float(str(value or "0"))
    except (TypeError, ValueError):
        return 0.0


def _int(value: object) -> int:
    try:
        return int(str(value or "0"))
    except (TypeError, ValueError):
        return 0


def _testcase_location(repo_root: Path, case: ET.Element) -> tuple[str, int | None]:
    file_text = str(case.attrib.get("file") or "").strip()
    line = _int(case.attrib.get("line")) or None
    if file_text:
        return relativize(repo_root, file_text), line
    class_name = str(case.attrib.get("classname") or "").strip()
    return (class_name or "-"), line


def parse_junit_reports(
    repo_root: Path,
    files: Iterable[Path],
    *,
    command_name: str = "test",
) -> ReportCollection:
    passed = failed = skipped = total = 0
    duration = 0.0
    suites = 0
    parsed_files: list[str] = []
    issues: list[Issue] = []

    for path in sorted({Path(item).resolve() for item in files}):
        try:
            root = ET.parse(path).getroot()
        except (OSError, ET.ParseError) as exc:
            issues.append(Issue(
                "error",
                command_name,
                relativize(repo_root, str(path)),
                "JUNIT_XML",
                f"Could not parse Gradle test report: {exc}",
            ))
            continue

        parsed_files.append(relativize(repo_root, str(path)))
        roots = [root] if root.tag.rsplit("}", 1)[-1] == "testsuite" else [
            node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "testsuite"
        ]
        if not roots:
            roots = [root]

        for suite in roots:
            suites += 1
            cases = [node for node in suite if node.tag.rsplit("}", 1)[-1] == "testcase"]
            if not cases:
                suite_total = _int(suite.attrib.get("tests"))
                suite_failed = _int(suite.attrib.get("failures")) + _int(suite.attrib.get("errors"))
                suite_skipped = _int(suite.attrib.get("skipped"))
                total += suite_total
                failed += suite_failed
                skipped += suite_skipped
                passed += max(0, suite_total - suite_failed - suite_skipped)
                duration += _float(suite.attrib.get("time"))
                continue

            for case in cases:
                total += 1
                duration += _float(case.attrib.get("time"))
                failure_node = next(
                    (
                        child
                        for child in case
                        if child.tag.rsplit("}", 1)[-1] in {"failure", "error"}
                    ),
                    None,
                )
                skipped_node = next(
                    (child for child in case if child.tag.rsplit("}", 1)[-1] == "skipped"),
                    None,
                )
                if failure_node is not None:
                    failed += 1
                    file_text, line = _testcase_location(repo_root, case)
                    test_name = ".".join(
                        item
                        for item in (
                            str(case.attrib.get("classname") or "").strip(),
                            str(case.attrib.get("name") or "").strip(),
                        )
                        if item
                    ) or "Gradle test"
                    message = str(failure_node.attrib.get("message") or "").strip()
                    body = str(failure_node.text or "").strip()
                    detail = message or (body.splitlines()[0] if body else "Test failed")
                    issues.append(Issue(
                        "error",
                        command_name,
                        file_text,
                        "GRADLE_TEST",
                        f"{test_name}: {detail}",
                        line=line,
                    ))
                elif skipped_node is not None:
                    skipped += 1
                else:
                    passed += 1

    metadata: dict[str, Any] = {
        "framework": "gradle-junit",
        "report_files": parsed_files,
        "suites": suites,
        "discovered": total,
        "selected": total,
        "executed": total,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "total": total,
        "duration_seconds": round(duration, 6),
    }
    return ReportCollection(metadata, issues)


def collect_gradle_test_reports(
    project_root: Path,
    *,
    repo_root: Path | None = None,
    tasks: Iterable[str] = (),
    started_at: float | None = None,
    command_name: str = "test",
) -> ReportCollection:
    files = _choose_task_reports(
        project_root.glob("**/build/test-results/**/*.xml"),
        tasks=tasks,
        marker="test-results",
        started_at=started_at,
    )
    collection = parse_junit_reports(repo_root or project_root, files, command_name=command_name)
    collection.metadata["tasks"] = [str(task) for task in tasks]
    return collection


def _lint_severity(value: str) -> str | None:
    normalized = str(value or "").strip().lower()
    if normalized in {"fatal", "error"}:
        return "error"
    if normalized in {"warning", "information", "info"}:
        return "warning"
    return None


def parse_android_lint_xml(
    repo_root: Path,
    path: Path,
    *,
    command_name: str = "lint",
) -> list[Issue]:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        return [Issue(
            "error",
            command_name,
            relativize(repo_root, str(path)),
            "LINT_XML",
            f"Could not parse Android Lint report: {exc}",
        )]

    issues: list[Issue] = []
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] != "issue":
            continue
        severity = _lint_severity(str(node.attrib.get("severity") or ""))
        if severity is None:
            continue
        code = str(node.attrib.get("id") or "ANDROID_LINT").strip()
        message = str(
            node.attrib.get("message")
            or node.attrib.get("summary")
            or node.attrib.get("errorLine1")
            or "Android Lint issue"
        ).strip()
        locations = [child for child in node if child.tag.rsplit("}", 1)[-1] == "location"]
        if not locations:
            issues.append(Issue(severity, command_name, "-", code, message))
            continue
        for location in locations:
            file_text = str(location.attrib.get("file") or "-").strip()
            line = _int(location.attrib.get("line")) or None
            issues.append(Issue(
                severity,
                command_name,
                relativize(repo_root, file_text),
                code,
                message,
                line=line,
            ))
    return issues


def _sarif_location(repo_root: Path, result: dict[str, Any]) -> tuple[str, int | None]:
    locations = result.get("locations")
    if not isinstance(locations, list) or not locations:
        return "-", None
    location = locations[0] if isinstance(locations[0], dict) else {}
    physical = location.get("physicalLocation") if isinstance(location, dict) else {}
    physical = physical if isinstance(physical, dict) else {}
    artifact = physical.get("artifactLocation") if isinstance(physical, dict) else {}
    artifact = artifact if isinstance(artifact, dict) else {}
    region = physical.get("region") if isinstance(physical, dict) else {}
    region = region if isinstance(region, dict) else {}
    uri = str(artifact.get("uri") or "-")
    if uri.startswith("file://"):
        uri = uri[7:]
    return relativize(repo_root, uri), (_int(region.get("startLine")) or None)


def parse_android_lint_sarif(
    repo_root: Path,
    path: Path,
    *,
    command_name: str = "lint",
) -> list[Issue]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError) as exc:
        return [Issue(
            "error",
            command_name,
            relativize(repo_root, str(path)),
            "LINT_SARIF",
            f"Could not parse Android Lint SARIF report: {exc}",
        )]

    issues: list[Issue] = []
    for run in payload.get("runs", []) if isinstance(payload, dict) else []:
        if not isinstance(run, dict):
            continue
        for result in run.get("results", []) if isinstance(run.get("results"), list) else []:
            if not isinstance(result, dict):
                continue
            level = str(result.get("level") or "warning")
            severity = "error" if level.lower() == "error" else "warning"
            message_obj = result.get("message")
            if isinstance(message_obj, dict):
                message = str(message_obj.get("text") or message_obj.get("markdown") or "Android Lint issue")
            else:
                message = str(message_obj or "Android Lint issue")
            file_text, line = _sarif_location(repo_root, result)
            issues.append(Issue(
                severity,
                command_name,
                file_text,
                str(result.get("ruleId") or "ANDROID_LINT"),
                message.strip(),
                line=line,
            ))
    return issues


def _lint_variant_tokens(tasks: Iterable[str]) -> set[str]:
    tokens: set[str] = set()
    for task in tasks:
        leaf = _task_leaf(task)
        match = re.match(r"lint(.+)$", leaf, re.IGNORECASE)
        if match:
            tokens.add(match.group(1).lower())
    return tokens


def collect_android_lint_reports(
    project_root: Path,
    *,
    repo_root: Path | None = None,
    tasks: Iterable[str] = (),
    started_at: float | None = None,
    command_name: str = "lint",
) -> ReportCollection:
    candidates = sorted({
        *project_root.glob("**/build/reports/lint-results*.xml"),
        *project_root.glob("**/build/reports/lint-results*.sarif"),
        *project_root.glob("**/build/reports/lint-results*.json"),
    })
    variants = _lint_variant_tokens(tasks)
    selected = [
        path for path in candidates
        if not variants or any(token in path.name.lower() for token in variants)
    ]
    if not selected:
        selected = [path for path in candidates if _recent(path, started_at)] or candidates

    issues: list[Issue] = []
    parsed_files: list[str] = []
    source_root = repo_root or project_root
    for path in selected:
        parsed_files.append(relativize(source_root, str(path)))
        suffix = path.suffix.lower()
        if suffix == ".xml":
            issues.extend(parse_android_lint_xml(source_root, path, command_name=command_name))
        else:
            issues.extend(parse_android_lint_sarif(source_root, path, command_name=command_name))

    # Preserve one finding per source/rule/message. Android Lint can emit both
    # XML and SARIF for the same task.
    unique: list[Issue] = []
    seen: set[tuple[str, str, str, str, int | None]] = set()
    for issue in issues:
        key = (issue.severity, issue.file, issue.code, issue.message, issue.line)
        if key in seen:
            continue
        seen.add(key)
        unique.append(issue)

    errors = sum(1 for issue in unique if issue.severity == "error")
    warnings = sum(1 for issue in unique if issue.severity == "warning")
    return ReportCollection(
        {
            "tool": "android-lint",
            "report_files": parsed_files,
            "tasks": [str(task) for task in tasks],
            "errors": errors,
            "warnings": warnings,
            "total": errors + warnings,
        },
        unique,
    )



def collect_pytest_report(
    project_root: Path,
    *,
    repo_root: Path | None = None,
    report_path: str = "",
    command_name: str = "test",
) -> ReportCollection:
    path = Path(report_path) if report_path else project_root / ".devtool" / "reports" / "pytest.xml"
    files = [path] if path.is_file() else list(project_root.glob("**/pytest*.xml"))
    collection = parse_junit_reports(repo_root or project_root, files, command_name=command_name)
    collection.metadata["framework"] = "pytest-junit"
    return collection


def collect_instrumentation_output(
    output: str,
    *,
    repo_root: Path,
    command_name: str = "instrumentation",
) -> ReportCollection:
    matches = re.findall(r"DEVTOOL_INSTRUMENTATION_RESULT\s+(\{.*\})", output or "")
    if not matches:
        issue = Issue("error", command_name, "-", "INSTRUMENTATION_RESULT", "Instrumentation runner did not emit a structured result.")
        return ReportCollection({"framework": "android-instrumentation", "total": 0, "passed": 0, "failed": 1, "skipped": 0}, [issue])
    try:
        payload = json.loads(matches[-1])
    except json.JSONDecodeError as exc:
        issue = Issue("error", command_name, "-", "INSTRUMENTATION_JSON", f"Could not parse instrumentation result: {exc}")
        return ReportCollection({}, [issue])
    issues: list[Issue] = []
    if int(payload.get("failed") or 0) > 0 or int(payload.get("returncode") or 0) != 0:
        issues.append(Issue("error", command_name, "-", "ANDROID_INSTRUMENTATION", f"Instrumentation reported {payload.get('failed', 0)} failed test(s)."))
    return ReportCollection(dict(payload), issues)


def collect_python_package_output(
    output: str,
    *,
    repo_root: Path,
    command_name: str = "package-check",
) -> ReportCollection:
    matches = re.findall(r"DEVTOOL_PYTHON_PACKAGE_RESULT\s+(\{.*\})", output or "")
    if not matches:
        return ReportCollection({}, [Issue("error", command_name, "dist", "PYTHON_PACKAGE_RESULT", "Package checker did not emit a structured result.")])
    try:
        payload = json.loads(matches[-1])
    except json.JSONDecodeError as exc:
        return ReportCollection({}, [Issue("error", command_name, "dist", "PYTHON_PACKAGE_JSON", str(exc))])
    issues: list[Issue] = []
    for item in payload.get("items", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict) or bool(item.get("valid", False)):
            continue
        detail = "; ".join(str(check.get("detail") or check.get("name") or "invalid") for check in item.get("checks", []) if isinstance(check, dict) and check.get("status") == "failed")
        issues.append(Issue("error", command_name, relativize(repo_root, str(item.get("path") or "dist")), "PYTHON_PACKAGE", detail or "Invalid Python package artifact"))
    if not payload.get("items"):
        issues.append(Issue("error", command_name, "dist", "PYTHON_PACKAGE_MISSING", str(payload.get("error") or "No package artifacts found")))
    return ReportCollection(dict(payload), issues)


def collect_python_audit_output(
    output: str,
    *,
    repo_root: Path,
    command_name: str,
) -> ReportCollection:
    matches = re.findall(r"DEVTOOL_PYTHON_AUDIT_RESULT\s+(\{.*\})", output or "")
    if not matches:
        return ReportCollection({}, [])
    try:
        payload = json.loads(matches[-1])
    except json.JSONDecodeError as exc:
        return ReportCollection({}, [Issue("error", command_name, "-", "PYTHON_AUDIT_JSON", str(exc))])
    issues = [
        Issue("error", command_name, str(item.get("path") or "-"), str(item.get("code") or "PYTHON_AUDIT"), str(item.get("message") or "Python source audit issue"))
        for item in payload.get("issues", []) if isinstance(item, dict)
    ]
    return ReportCollection(dict(payload), issues)


def collect_python_tool_output(
    output: str,
    *,
    repo_root: Path,
    command_name: str,
) -> ReportCollection:
    matches = re.findall(r"DEVTOOL_PYTHON_TOOL_RESULT\s+(\{.*\})", output or "")
    if not matches:
        return ReportCollection({}, [])
    try:
        payload = json.loads(matches[-1])
    except json.JSONDecodeError as exc:
        return ReportCollection({}, [Issue("error", command_name, "-", "PYTHON_TOOL_JSON", str(exc))])
    issues: list[Issue] = []
    for item in payload.get("issues", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        message = str(item.get("message") or "Python tool reported an issue")
        issues.append(Issue("error", command_name, "-", f"PYTHON_{str(payload.get('tool') or 'TOOL').upper()}", message))
    if int(payload.get("returncode") or 0) != 0 and not issues:
        issues.append(Issue("error", command_name, "-", "PYTHON_TOOL", f"{payload.get('tool', 'Python tool')} exited with code {payload.get('returncode')}"))
    return ReportCollection(dict(payload), issues)


def collect_python_coverage_report(
    project_root: Path,
    *,
    repo_root: Path,
    report_path: str,
    command_name: str,
) -> ReportCollection:
    path = Path(report_path).expanduser() if report_path else project_root / ".devtool" / "reports" / "coverage.xml"
    if not path.is_absolute():
        path = project_root / path
    if not path.is_file():
        return ReportCollection({}, [])
    try:
        root = ET.parse(path).getroot()
        line_rate = float(root.attrib.get("line-rate", "0") or 0)
        branch_rate = float(root.attrib.get("branch-rate", "0") or 0)
        lines_valid = int(float(root.attrib.get("lines-valid", "0") or 0))
        lines_covered = int(float(root.attrib.get("lines-covered", "0") or 0))
    except (OSError, ET.ParseError, ValueError) as exc:
        return ReportCollection({}, [Issue("error", command_name, relativize(repo_root, path), "PYTHON_COVERAGE_XML", str(exc))])
    payload = {
        "line_rate": line_rate,
        "line_percent": round(line_rate * 100.0, 2),
        "branch_rate": branch_rate,
        "branch_percent": round(branch_rate * 100.0, 2),
        "lines_valid": lines_valid,
        "lines_covered": lines_covered,
        "report": str(path),
    }
    return ReportCollection(payload, [])

def collect_report_metadata(
    project_root: Path,
    *,
    repo_root: Path | None = None,
    parser: str,
    tasks: Iterable[str] = (),
    started_at: float | None = None,
    command_name: str,
    output: str = "",
    report_path: str = "",
) -> ReportCollection:
    if parser == "gradle-junit":
        return collect_gradle_test_reports(
            project_root,
            repo_root=repo_root,
            tasks=tasks,
            started_at=started_at,
            command_name=command_name,
        )
    if parser == "android-lint":
        return collect_android_lint_reports(
            project_root,
            repo_root=repo_root,
            tasks=tasks,
            started_at=started_at,
            command_name=command_name,
        )
    if parser == "pytest-junit":
        return collect_pytest_report(
            project_root, repo_root=repo_root, report_path=report_path, command_name=command_name
        )
    if parser == "android-instrumentation":
        return collect_instrumentation_output(output, repo_root=repo_root or project_root, command_name=command_name)
    if parser == "python-package":
        return collect_python_package_output(output, repo_root=repo_root or project_root, command_name=command_name)
    if parser == "python-tool":
        return collect_python_tool_output(output, repo_root=repo_root or project_root, command_name=command_name)
    if parser == "python-audit":
        return collect_python_audit_output(output, repo_root=repo_root or project_root, command_name=command_name)
    if parser == "python-coverage":
        return collect_python_coverage_report(
            project_root, repo_root=repo_root or project_root, report_path=report_path, command_name=command_name
        )
    return ReportCollection({}, [])
