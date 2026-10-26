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
from urllib.parse import unquote, urlparse
import xml.etree.ElementTree as ET

from .diagnostics import relativize
from .models import Issue
from .test_model import TestArtifact, TestCaseResult, TestFailure, TestResult, TestSource, TestStatus, TestSuiteResult
from .test_adapters import safe_test_id, reconcile_results, compatibility_counts


@dataclass(slots=True)
class ReportCollection:
    metadata: dict[str, Any]
    issues: list[Issue]
    test_result: TestResult | None = None


def _task_leaf(task: str) -> str:
    return str(task or "").strip().split(":")[-1]


def _recent(path: Path, started_at: float | None) -> bool:
    if started_at is None:
        return True
    try:
        # Report truth is invocation-bound. Allow only 1 ms of filesystem/clock
        # granularity around process start: newly-created reports can otherwise
        # appear a few microseconds older than time.time() on real filesystems.
        # This is intentionally tiny; genuinely stale reports remain excluded.
        tolerance_ns = 1_000_000
        return path.stat().st_mtime_ns + tolerance_ns >= int(float(started_at) * 1_000_000_000)
    except (OSError, TypeError, ValueError):
        return False


def _task_module_leaf(task: str) -> tuple[str, str]:
    text = str(task or "").strip()
    parts = [part for part in text.split(":") if part]
    if not parts:
        return "", ""
    leaf = parts[-1]
    module = "/".join(parts[:-1])
    return module, leaf


def _matches_task_report(path: Path, task: str, marker: str) -> bool:
    module, leaf = _task_module_leaf(task)
    if not leaf:
        return False
    normalized = "/" + path.resolve().as_posix().lstrip("/")
    needle = f"/{marker}/{leaf}/"
    if needle not in normalized:
        return False
    if not module:
        return True
    return f"/{module}/build/{marker}/{leaf}/" in normalized


def _choose_task_reports(
    files: Iterable[Path],
    *,
    tasks: Iterable[str],
    marker: str,
    started_at: float | None,
) -> list[Path]:
    candidates = sorted({path.resolve() for path in files if path.is_file()})
    requested = [str(task).strip() for task in tasks if str(task).strip()]
    if requested:
        # When task identity is known, both identity and invocation freshness are
        # mandatory. Falling back to another module's report (or a stale report
        # with the right leaf name) can convert a successful current task into a
        # false failure.
        return [
            path for path in candidates
            if _recent(path, started_at) and any(_matches_task_report(path, task, marker) for task in requested)
        ]
    return [path for path in candidates if _recent(path, started_at)] if started_at is not None else candidates


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
    # A JUnit classname is test identity, not a filesystem path. Keep it in the
    # diagnostic message and leave source unknown unless the report declares a
    # real file attribute.
    return "-", line


def parse_junit_reports(
    repo_root: Path,
    files: Iterable[Path],
    *,
    command_name: str = "test",
    framework: str = "gradle-junit",
    issue_code: str = "GRADLE_TEST",
    report_label: str = "Gradle test report",
    report_error_code: str = "JUNIT_XML",
) -> ReportCollection:
    passed = failed = skipped = total = 0
    duration = 0.0
    suites = 0
    parsed_files: list[str] = []
    issues: list[Issue] = []
    canonical_suites: list[TestSuiteResult] = []

    for path in sorted({Path(item).resolve() for item in files}):
        try:
            root = ET.parse(path).getroot()
        except (OSError, ET.ParseError) as exc:
            issues.append(Issue(
                "error", command_name, relativize(repo_root, str(path)), report_error_code,
                f"Could not parse {report_label}: {exc}",
            ))
            continue

        report_rel = relativize(repo_root, str(path))
        parsed_files.append(report_rel)
        roots = [root] if root.tag.rsplit("}", 1)[-1] == "testsuite" else [
            node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "testsuite"
        ]
        if not roots:
            roots = [root]

        for suite_index, suite in enumerate(roots):
            cases = [node for node in suite if node.tag.rsplit("}", 1)[-1] == "testcase"]
            if not cases:
                has_nested_suites = any(child.tag.rsplit("}", 1)[-1] == "testsuite" for child in suite)
                if has_nested_suites:
                    continue
                suite_total = _int(suite.attrib.get("tests"))
                suite_failed = _int(suite.attrib.get("failures")) + _int(suite.attrib.get("errors"))
                suite_skipped = _int(suite.attrib.get("skipped"))
                suite_passed = max(0, suite_total - suite_failed - suite_skipped)
                total += suite_total
                failed += suite_failed
                skipped += suite_skipped
                passed += suite_passed
                duration += _float(suite.attrib.get("time"))
                if suite_total:
                    suite_name = str(suite.attrib.get("name") or f"suite-{suite_index}")
                    sid = safe_test_id(f"{framework}:{report_rel}:{suite_name}")
                    aggregate_cases: list[TestCaseResult] = []
                    for label, count, status in (
                        ("passed", suite_passed, TestStatus.PASSED),
                        ("failed", suite_failed, TestStatus.FAILED),
                        ("skipped", suite_skipped, TestStatus.SKIPPED),
                    ):
                        for index in range(count):
                            aggregate_cases.append(TestCaseResult(
                                safe_test_id(f"{sid}/{label}/{index + 1}"), f"{label} #{index + 1}", status,
                                metadata={"evidence": "report", "aggregate": True, "framework": framework},
                            ))
                    canonical_suites.append(TestSuiteResult(
                        sid, suite_name, tuple(aggregate_cases),
                        duration_seconds=_float(suite.attrib.get("time")) or None,
                        metadata={"evidence": "report", "framework": framework, "report": report_rel},
                    ))
                suites += 1
                continue

            suites += 1
            suite_name = str(suite.attrib.get("name") or f"suite-{suite_index}")
            sid = safe_test_id(f"{framework}:{report_rel}:{suite_name}")
            canonical_cases: list[TestCaseResult] = []
            seen_case_ids: set[str] = set()
            for case_index, case in enumerate(cases):
                total += 1
                case_duration = _float(case.attrib.get("time"))
                duration += case_duration
                failure_node = next((child for child in case if child.tag.rsplit("}", 1)[-1] in {"failure", "error"}), None)
                skipped_node = next((child for child in case if child.tag.rsplit("}", 1)[-1] == "skipped"), None)
                classname = str(case.attrib.get("classname") or "").strip()
                case_name = str(case.attrib.get("name") or f"case-{case_index}").strip()
                base_case_id = safe_test_id("#".join(item for item in (classname, case_name) if item) or f"{sid}#case-{case_index}")
                case_id = base_case_id
                suffix = 2
                while case_id in seen_case_ids:
                    case_id = safe_test_id(f"{base_case_id}#{suffix}")
                    suffix += 1
                seen_case_ids.add(case_id)
                status = TestStatus.PASSED
                canonical_failure = None
                if failure_node is not None:
                    failed += 1
                    tag = failure_node.tag.rsplit("}", 1)[-1]
                    status = TestStatus.ERROR if tag == "error" else TestStatus.FAILED
                    file_text, line = _testcase_location(repo_root, case)
                    test_name = ".".join(item for item in (classname, case_name) if item) or "test"
                    message = str(failure_node.attrib.get("message") or "").strip()
                    body = str(failure_node.text or "").strip()
                    detail = message or (body.splitlines()[0] if body else "Test failed")
                    issues.append(Issue("error", command_name, file_text, issue_code, f"{test_name}: {detail}", line=line))
                    source = TestSource("file", file_text, line) if file_text and file_text != "-" else None
                    canonical_failure = TestFailure(detail, details=body or None, source=source)
                elif skipped_node is not None:
                    skipped += 1
                    status = TestStatus.SKIPPED
                else:
                    passed += 1
                canonical_cases.append(TestCaseResult(
                    case_id, case_name, status,
                    duration_seconds=case_duration or None,
                    failure=canonical_failure,
                    metadata={"evidence": "report", "framework": framework, "classname": classname},
                ))
            canonical_suites.append(TestSuiteResult(
                sid, suite_name, tuple(canonical_cases),
                duration_seconds=_float(suite.attrib.get("time")) or None,
                metadata={"evidence": "report", "framework": framework, "report": report_rel},
            ))

    metadata: dict[str, Any] = {
        "framework": framework,
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
    canonical = TestResult(
        safe_test_id(command_name), tuple(canonical_suites),
        duration_seconds=round(duration, 6) if duration else None,
        artifacts=tuple(TestArtifact("junit-xml", item) for item in parsed_files),
        metadata={"evidence": "report", "framework": framework},
    ) if parsed_files else None
    return ReportCollection(metadata, issues, canonical)

def _report_module(project_root: Path, path: Path) -> str:
    try:
        parts = path.resolve().relative_to(project_root.resolve()).parts
    except (OSError, ValueError):
        parts = path.parts
    if "build" not in parts:
        return ""
    return "/".join(parts[:parts.index("build")])


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
    source_root = repo_root or project_root
    totals: dict[str, int | float] = {
        "suites": 0, "discovered": 0, "selected": 0, "executed": 0,
        "passed": 0, "failed": 0, "skipped": 0, "total": 0,
        "duration_seconds": 0.0,
    }
    parsed_files: list[str] = []
    issues: list[Issue] = []
    canonical_parts: list[TestResult] = []
    report_modules: dict[str, str] = {}
    for path in files:
        module = _report_module(project_root, path)
        qualified_command = f"{command_name}[{module}]" if module else command_name
        parsed = parse_junit_reports(source_root, [path], command_name=qualified_command)
        parsed_files.extend(str(item) for item in parsed.metadata.get("report_files", []))
        for key in ("suites", "discovered", "selected", "executed", "passed", "failed", "skipped", "total"):
            totals[key] = int(totals[key]) + int(parsed.metadata.get(key) or 0)
        totals["duration_seconds"] = float(totals["duration_seconds"]) + float(parsed.metadata.get("duration_seconds") or 0.0)
        issues.extend(parsed.issues)
        if parsed.test_result is not None:
            canonical_parts.append(parsed.test_result)
        for report_file in parsed.metadata.get("report_files", []):
            report_modules[str(report_file)] = module
    metadata: dict[str, Any] = {
        "framework": "gradle-junit",
        "report_files": parsed_files,
        **totals,
        "duration_seconds": round(float(totals["duration_seconds"]), 6),
        "tasks": [str(task) for task in tasks],
        "report_modules": report_modules,
    }
    return ReportCollection(metadata, issues, reconcile_results(command_name, canonical_parts))


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
    if uri != "-":
        parsed = urlparse(uri)
        if parsed.scheme == "file":
            uri = parsed.path
        uri = unquote(uri)
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
    requested_tasks = [str(task).strip() for task in tasks if str(task).strip()]

    def lint_task_matches(path: Path) -> bool:
        if not requested_tasks:
            return True
        normalized = "/" + path.resolve().as_posix().lstrip("/")
        name = path.name.lower()
        for task in requested_tasks:
            module, leaf = _task_module_leaf(task)
            if module and f"/{module}/build/reports/" not in normalized:
                continue
            match = re.match(r"lint(.+)$", leaf, re.IGNORECASE)
            variant = match.group(1).lower() if match else ""
            if variant and variant not in name:
                continue
            return True
        return False

    selected = [
        path for path in candidates
        if lint_task_matches(path) and _recent(path, started_at)
    ] if (requested_tasks or started_at is not None) else [path for path in candidates if lint_task_matches(path)]

    issues_with_module: list[tuple[str, Issue]] = []
    parsed_files: list[str] = []
    source_root = repo_root or project_root
    for path in selected:
        parsed_files.append(relativize(source_root, str(path)))
        try:
            relative_parts = path.resolve().relative_to(project_root.resolve()).parts
        except ValueError:
            relative_parts = path.parts
        module = ""
        if "build" in relative_parts:
            build_index = relative_parts.index("build")
            module = "/".join(relative_parts[:build_index])
        suffix = path.suffix.lower()
        parsed = (
            parse_android_lint_xml(source_root, path, command_name=command_name)
            if suffix == ".xml"
            else parse_android_lint_sarif(source_root, path, command_name=command_name)
        )
        issues_with_module.extend((module, issue) for issue in parsed)

    # Module identity is part of lint finding identity: two modules may report
    # the same rule/location/message independently. Only exact duplicates from
    # multiple formats within the same module collapse.
    unique: list[Issue] = []
    seen: set[tuple[str, str, str, str, int | None, str]] = set()
    for module, issue in issues_with_module:
        key = (module, issue.severity, issue.file, issue.code, issue.line, issue.message)
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
    started_at: float | None = None,
    command_name: str = "test",
) -> ReportCollection:
    path = Path(report_path) if report_path else project_root / ".devtool" / "reports" / "pytest.xml"
    files = [path] if path.is_file() else list(project_root.glob("**/pytest*.xml"))
    if started_at is not None:
        files = [item for item in files if _recent(item, started_at)]
    collection = parse_junit_reports(
        repo_root or project_root,
        files,
        command_name=command_name,
        framework="pytest-junit",
        issue_code="PYTEST_FAILURE",
        report_label="pytest JUnit report",
        report_error_code="PYTEST_JUNIT_XML",
    )

    def classify_failure(issue: Issue) -> str:
        text = str(issue.message or "").casefold()
        if (
            "modulenotfounderror" in text
            or "no module named" in text
            or "install it with: pip install" in text
            or "pyyaml is required" in text
        ):
            return "PYTHON_DEPENDENCY"
        if (
            "dlopen failed" in text
            or "libc.so.6" in text
            or "cannot open shared object file" in text
            or "wrong elf class" in text
        ):
            return "PYTHON_PLATFORM"
        if "importerror" in text:
            return "PYTHON_IMPORT"
        return "PYTEST_FAILURE"

    category_names = {
        "PYTHON_DEPENDENCY": "dependency",
        "PYTHON_PLATFORM": "platform",
        "PYTHON_IMPORT": "import",
        "PYTEST_FAILURE": "test",
    }
    category_counts: dict[str, int] = {}
    for issue in collection.issues:
        if issue.code == "PYTEST_FAILURE":
            issue.code = classify_failure(issue)
        if issue.severity == "error":
            category = category_names.get(issue.code, "test")
            category_counts[category] = category_counts.get(category, 0) + 1
    collection.metadata["failure_categories"] = category_counts
    failed_ids: list[str] = []
    for file in files:
        try:
            tree = ET.parse(file)
        except (OSError, ET.ParseError):
            continue
        for case in tree.iter():
            if case.tag.rsplit("}", 1)[-1] != "testcase":
                continue
            if not any(child.tag.rsplit("}", 1)[-1] in {"failure", "error"} for child in case):
                continue
            name = str(case.attrib.get("name") or "").strip()
            classname = str(case.attrib.get("classname") or "").strip()
            file_name = str(case.attrib.get("file") or "").strip().replace("\\", "/")
            class_part = ""
            if file_name:
                module_stem = Path(file_name).stem
                class_tokens = classname.split(".") if classname else []
                if class_tokens and class_tokens[-1] != module_stem and class_tokens[-1][:1].isupper():
                    class_part = class_tokens[-1]
            elif classname:
                tokens = classname.split(".")
                if tokens and tokens[-1][:1].isupper():
                    class_part = tokens.pop()
                file_name = "/".join(tokens) + ".py"
            parts = [file_name, class_part, name]
            node_id = "::".join(item for item in parts if item)
            if node_id:
                failed_ids.append(node_id)
    collection.metadata["failed_ids"] = list(dict.fromkeys(failed_ids))
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
        return ReportCollection({"framework": "android-instrumentation", "total": 0, "passed": 0, "failed": 0, "skipped": 0, "failure_kind": "runner-error"}, [issue])
    try:
        payload = json.loads(matches[-1])
    except json.JSONDecodeError as exc:
        issue = Issue("error", command_name, "-", "INSTRUMENTATION_JSON", f"Could not parse instrumentation result: {exc}")
        return ReportCollection({}, [issue])
    issues: list[Issue] = []
    if int(payload.get("failed") or 0) > 0 or int(payload.get("returncode") or 0) != 0:
        setup_error = str(payload.get("error") or payload.get("reason") or "").strip()
        failure_kind = str(payload.get("failure_kind") or "").strip()
        if failure_kind in {"setup-crash", "setup-error", "device-readiness", "runner-error", "snapshot", "receipt", "cleanup"}:
            message = f"Instrumentation {failure_kind.replace('-', ' ')}: {setup_error or 'execution did not reach a successful test result'}"
        elif setup_error and int(payload.get("failed") or 0) == 0:
            message = f"Instrumentation setup failed: {setup_error}"
        else:
            message = f"Instrumentation reported {payload.get('failed', 0)} failed test(s)."
        issues.append(
            Issue(
                "error",
                command_name,
                "-",
                "ANDROID_INSTRUMENTATION",
                message,
            )
        )
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
        message = str(
            item.get("message")
            or "Python tool reported an issue"
        )

        severity = str(
            item.get("severity")
            or "error"
        ).strip().lower()

        if severity not in {
            "error",
            "warning",
        }:
            severity = "error"

        issues.append(
            Issue(
                severity,
                command_name,
                "-",
                f"PYTHON_{str(payload.get('tool') or 'TOOL').upper()}",
                message,
            )
        )

    if (
        int(payload.get("returncode") or 0) != 0
        and not any(
            issue.severity == "error"
            for issue in issues
        )
    ):
        issues.append(
            Issue(
                "error",
                command_name,
                "-",
                "PYTHON_TOOL",
                f"{payload.get('tool', 'Python tool')} exited with code {payload.get('returncode')}",
            )
        )
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


def _duration_seconds(value: object) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    match = re.match(r"^(?:(\d+)\.)?(\d+):(\d+):(\d+(?:\.\d+)?)$", text)
    if not match:
        return None
    days = int(match.group(1) or 0)
    hours = int(match.group(2)); minutes = int(match.group(3)); seconds = float(match.group(4))
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def _duration_milliseconds(value: object) -> float | None:
    try:
        return float(value) / 1000.0 if value is not None else None
    except (TypeError, ValueError):
        return None


def collect_dotnet_trx_report(
    project_root: Path,
    *,
    repo_root: Path | None = None,
    report_path: str = "",
    started_at: float | None = None,
    command_name: str = "test",
) -> ReportCollection:
    root = Path(report_path).expanduser() if report_path else project_root / ".devtool" / "reports" / "dotnet"
    if not root.is_absolute():
        root = project_root / root
    files = [root] if root.is_file() else sorted(root.glob("*.trx")) if root.is_dir() else []
    if started_at is not None:
        files = [path for path in files if _recent(path, started_at)]
    cases: list[TestCaseResult] = []
    parsed_files: list[str] = []
    issues: list[Issue] = []
    for path in files:
        try:
            doc = ET.parse(path).getroot()
        except (OSError, ET.ParseError) as exc:
            issues.append(Issue("error", command_name, relativize(repo_root or project_root, path), "DOTNET_TRX", f"Could not parse .NET TRX report: {exc}"))
            continue
        rel = relativize(repo_root or project_root, path)
        parsed_files.append(rel)
        for index, node in enumerate(doc.iter()):
            if node.tag.rsplit("}", 1)[-1] != "UnitTestResult":
                continue
            name = str(node.attrib.get("testName") or f"test-{index}")
            cid = safe_test_id(str(node.attrib.get("testId") or name))
            outcome = str(node.attrib.get("outcome") or "Error")
            status = {
                "Passed": TestStatus.PASSED,
                "Failed": TestStatus.FAILED,
                "NotExecuted": TestStatus.SKIPPED,
                "Aborted": TestStatus.CANCELLED,
                "Timeout": TestStatus.ERROR,
            }.get(outcome, TestStatus.ERROR)
            message = ""
            stack = ""
            for child in node.iter():
                tag = child.tag.rsplit("}", 1)[-1]
                if tag == "Message" and child.text: message = child.text.strip()
                elif tag == "StackTrace" and child.text: stack = child.text.strip()
            failure = None
            if status in {TestStatus.FAILED, TestStatus.ERROR, TestStatus.CANCELLED}:
                failure = TestFailure(message or outcome or ".NET test failed", details=stack or None)
                issues.append(Issue("error", command_name, "-", "DOTNET_TEST", f"{name}: {message or outcome}"))
            cases.append(TestCaseResult(
                cid, name, status,
                duration_seconds=_duration_seconds(node.attrib.get("duration")),
                failure=failure,
                metadata={"evidence": "report", "framework": "dotnet-trx"},
            ))
    suite = TestSuiteResult("dotnet", ".NET tests", tuple(cases), metadata={"evidence": "report", "framework": "dotnet-trx"})
    result = TestResult(
        safe_test_id(command_name), (suite,),
        artifacts=tuple(TestArtifact("trx", item) for item in parsed_files),
        metadata={"evidence": "report", "framework": "dotnet-trx"},
    ) if parsed_files else None
    counts = compatibility_counts(result) if result is not None else {"passed": 0, "failed": 0, "skipped": 0, "total": 0}
    metadata = {"framework": "dotnet-trx", "report_files": parsed_files, **counts}
    return ReportCollection(metadata, issues, result)



def snapshot_maven_report_state(project_root: Path) -> dict[str, tuple[int, int, int, int, int]]:
    """Capture invocation-entry XML identities without relying on wall clocks.

    A report is current if it did not exist at command entry, or its inode,
    size, mtime or ctime changed while the command ran.  On Android/Termux,
    filesystem clock granularity and clocks can differ from Python's wall
    clock; comparing *only* mtime to time.time() drops genuine current XML.
    """
    result: dict[str, tuple[int, int, int, int, int]] = {}
    for pattern in ("**/target/surefire-reports/TEST-*.xml", "**/target/failsafe-reports/TEST-*.xml"):
        for path in project_root.glob(pattern):
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                st = path.stat()
                result[str(path.absolute())] = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
            except OSError:
                continue
    return result


def collect_maven_test_reports(
    project_root: Path,
    *,
    repo_root: Path | None = None,
    started_at: float | None = None,
    command_name: str = "test",
    initial_report_state: dict[str, tuple[int, ...]] | None = None,
) -> ReportCollection:
    files = [
        *project_root.glob("**/target/surefire-reports/TEST-*.xml"),
        *project_root.glob("**/target/failsafe-reports/TEST-*.xml"),
    ]
    current_files: set[Path] = set()
    for path in files:
        try:
            if path.is_symlink() or not path.is_file():
                continue
            if initial_report_state is not None:
                st = path.stat()
                state = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
                if initial_report_state.get(str(path.absolute())) == state:
                    continue  # unchanged report from an earlier invocation
            elif not _recent(path, started_at):
                continue
            current_files.add(path.resolve())
        except OSError:
            continue
    files = sorted(current_files)
    return parse_junit_reports(
        repo_root or project_root,
        files,
        command_name=command_name,
        framework="maven-surefire",
        issue_code="MAVEN_TEST",
        report_label="Maven Surefire/Failsafe report",
        report_error_code="MAVEN_TEST_XML",
    )


def collect_node_junit_report(
    project_root: Path,
    *,
    repo_root: Path | None = None,
    report_path: str = "",
    started_at: float | None = None,
    command_name: str = "test",
) -> ReportCollection:
    path = Path(report_path).expanduser() if report_path else project_root / ".devtool" / "reports" / "node-tests.xml"
    if not path.is_absolute():
        path = project_root / path
    files = [path] if path.is_file() else []
    if started_at is not None:
        files = [item for item in files if _recent(item, started_at)]
    return parse_junit_reports(
        repo_root or project_root, files, command_name=command_name,
        framework="node-junit", issue_code="NODE_TEST",
        report_label="Node JUnit report", report_error_code="NODE_JUNIT_XML",
    )


def collect_node_json_report(
    project_root: Path,
    *,
    repo_root: Path | None = None,
    report_path: str = "",
    started_at: float | None = None,
    command_name: str = "test",
) -> ReportCollection:
    path = Path(report_path).expanduser() if report_path else project_root / ".devtool" / "reports" / "node-tests.json"
    if not path.is_absolute(): path = project_root / path
    if not path.is_file() or not _recent(path, started_at):
        return ReportCollection({"framework": "node-json", "report_files": [], "passed": 0, "failed": 0, "skipped": 0, "total": 0}, [])
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError) as exc:
        return ReportCollection({}, [Issue("error", command_name, relativize(repo_root or project_root, path), "NODE_TEST_JSON", f"Could not parse Node test report: {exc}")])
    suites: list[TestSuiteResult] = []
    issues: list[Issue] = []
    raw_suites = payload.get("suites") if isinstance(payload, dict) else None
    if isinstance(raw_suites, list):
        for si, raw_suite in enumerate(raw_suites):
            if not isinstance(raw_suite, dict): continue
            name = str(raw_suite.get("name") or raw_suite.get("id") or f"suite-{si}")
            sid = safe_test_id(raw_suite.get("id") or name)
            items = raw_suite.get("cases") if isinstance(raw_suite.get("cases"), list) else []
            suite_cases=[]
            for ci, raw_case in enumerate(items):
                if not isinstance(raw_case, dict): continue
                cname=str(raw_case.get("name") or raw_case.get("title") or f"case-{ci}")
                cid=safe_test_id(raw_case.get("id") or f"{sid}#{cname}")
                status={"passed":TestStatus.PASSED,"failed":TestStatus.FAILED,"skipped":TestStatus.SKIPPED,"pending":TestStatus.SKIPPED,"todo":TestStatus.SKIPPED,"cancelled":TestStatus.CANCELLED,"canceled":TestStatus.CANCELLED,"error":TestStatus.ERROR}.get(str(raw_case.get("status") or "").lower(),TestStatus.ERROR)
                message = str(raw_case.get("message") or raw_case.get("failure") or "").strip()
                failure = TestFailure(message or "Node test failed") if status in {TestStatus.FAILED, TestStatus.ERROR, TestStatus.CANCELLED} else None
                if failure is not None:
                    issues.append(Issue("error", command_name, "-", "NODE_TEST", f"{cname}: {failure.message}"))
                duration_value = _duration_seconds(raw_case.get("duration_seconds"))
                if duration_value is None:
                    duration_value = _duration_milliseconds(raw_case.get("duration_ms"))
                suite_cases.append(TestCaseResult(cid,cname,status,duration_seconds=duration_value,failure=failure,metadata={"evidence":"report","framework":"node-json"}))
            suites.append(TestSuiteResult(sid,name,tuple(suite_cases),metadata={"evidence":"report","framework":"node-json"}))
    elif isinstance(payload, dict) and isinstance(payload.get("testResults"), list):
        # Jest and Vitest JSON reporters expose testResults/assertionResults.
        for si, raw_suite in enumerate(payload["testResults"]):
            if not isinstance(raw_suite, dict): continue
            name=str(raw_suite.get("name") or raw_suite.get("testFilePath") or f"suite-{si}")
            sid=safe_test_id(name)
            assertions=raw_suite.get("assertionResults") if isinstance(raw_suite.get("assertionResults"), list) else []
            suite_cases=[]
            for ci, raw_case in enumerate(assertions):
                if not isinstance(raw_case, dict): continue
                cname=str(raw_case.get("fullName") or raw_case.get("title") or f"case-{ci}")
                cid=safe_test_id(f"{sid}#{cname}")
                status={"passed":TestStatus.PASSED,"failed":TestStatus.FAILED,"pending":TestStatus.SKIPPED,"skipped":TestStatus.SKIPPED,"todo":TestStatus.SKIPPED,"disabled":TestStatus.SKIPPED}.get(str(raw_case.get("status") or "").lower(),TestStatus.ERROR)
                failure_msgs=raw_case.get("failureMessages") if isinstance(raw_case.get("failureMessages"), list) else []
                failure=TestFailure(str(failure_msgs[0]), details="\n".join(str(x) for x in failure_msgs)) if status==TestStatus.FAILED and failure_msgs else None
                if status == TestStatus.FAILED:
                    issues.append(Issue("error", command_name, "-", "NODE_TEST", f"{cname}: {failure.message if failure else 'Node test failed'}"))
                suite_cases.append(TestCaseResult(cid,cname,status,duration_seconds=_duration_milliseconds(raw_case.get("duration")),failure=failure,metadata={"evidence":"report","framework":"node-json"}))
            suites.append(TestSuiteResult(sid,name,tuple(suite_cases),metadata={"evidence":"report","framework":"node-json"}))
    result=TestResult(safe_test_id(command_name),tuple(suites),artifacts=(TestArtifact("node-json",relativize(repo_root or project_root,path)),),metadata={"evidence":"report","framework":"node-json"})
    counts=compatibility_counts(result)
    return ReportCollection({"framework":"node-json","report_files":[relativize(repo_root or project_root,path)],**counts},issues,result)


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
    initial_report_state: dict[str, tuple[int, ...]] | None = None,
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
            project_root, repo_root=repo_root, report_path=report_path, started_at=started_at, command_name=command_name
        )
    if parser == "dotnet-trx":
        return collect_dotnet_trx_report(
            project_root, repo_root=repo_root, report_path=report_path, started_at=started_at, command_name=command_name
        )
    if parser == "maven-surefire":
        return collect_maven_test_reports(
            project_root, repo_root=repo_root, started_at=started_at, command_name=command_name,
            initial_report_state=initial_report_state,
        )
    if parser == "node-junit":
        return collect_node_junit_report(
            project_root, repo_root=repo_root, report_path=report_path, started_at=started_at, command_name=command_name
        )
    if parser == "node-json":
        return collect_node_json_report(
            project_root, repo_root=repo_root, report_path=report_path, started_at=started_at, command_name=command_name
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
