from __future__ import annotations

"""Structured Go test output support.

Devtool executes ``go test -json`` so package/test identity remains authoritative
when Go runs packages in parallel.  The JSON transport is rendered back into a
compact human-readable stream for the durable log, while an in-memory report is
built directly from the original events.  Plain ``go test`` output remains
supported as a fallback for user-supplied commands.
"""

from dataclasses import dataclass, field
import json
import re
import shlex

_PACKAGE_START_RE = re.compile(r"^==> go-test package (?P<package>\S+)$")
_PACKAGE_END_RE = re.compile(r"^<== go-test package (?P<package>\S+) (?P<status>pass|fail|skip)$")
_TEST_RESULT_RE = re.compile(r"^---\s+(?P<status>PASS|FAIL|SKIP):\s+(?P<test>\S+)")
_NO_TESTS_RE = re.compile(r"^\?\s+(?P<package>\S+)\s+\[no test files\]")
_GO_LOCATION_RE = re.compile(
    r"^\s*(?P<file>(?:[A-Za-z]:)?[^\n:]*?\.go):(?P<line>\d+)(?::(?P<column>\d+))?:\s*(?P<message>.+?)\s*$"
)
_NOISE_PREFIXES = (
    "=== RUN ",
    "--- PASS:",
    "--- FAIL:",
    "--- SKIP:",
    "PASS",
    "FAIL",
    "ok\t",
    "?\t",
)


@dataclass(slots=True)
class GoTestFailure:
    package: str
    test: str = ""
    file: str = "-"
    line: int | None = None
    column: int | None = None
    message: str = "Go test failed"
    rerun: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "package": self.package,
            "test": self.test,
            "file": self.file,
            "line": self.line,
            "column": self.column,
            "message": self.message,
            "rerun": self.rerun,
        }


@dataclass(slots=True)
class GoTestCase:
    package: str
    test: str
    status: str
    elapsed: float | None = None

    def to_dict(self) -> dict[str, object]:
        return {"package": self.package, "test": self.test, "status": self.status, "elapsed": self.elapsed}


@dataclass(slots=True)
class GoTestReport:
    tests_passed: int = 0
    tests_failed: int = 0
    tests_skipped: int = 0
    packages_passed: int = 0
    packages_failed: int = 0
    packages_skipped: int = 0
    packages_no_tests: int = 0
    failures: list[GoTestFailure] = field(default_factory=list)
    cases: list[GoTestCase] = field(default_factory=list)

    @property
    def tests_total(self) -> int:
        return self.tests_passed + self.tests_failed + self.tests_skipped

    @property
    def packages_total(self) -> int:
        return self.packages_passed + self.packages_failed + self.packages_skipped + self.packages_no_tests

    def to_metadata(self) -> dict[str, object]:
        return {
            "tests": {
                "passed": self.tests_passed,
                "failed": self.tests_failed,
                "skipped": self.tests_skipped,
                "total": self.tests_total,
            },
            "packages": {
                "passed": self.packages_passed,
                "failed": self.packages_failed,
                "skipped": self.packages_skipped,
                "no_tests": self.packages_no_tests,
                "total": self.packages_total,
            },
            "failures": [item.to_dict() for item in self.failures],
            "cases": [item.to_dict() for item in self.cases],
            "rerun": next((item.rerun for item in self.failures if item.rerun), ""),
        }


def _rerun_command(package: str, test: str) -> str:
    if not package or not test:
        return ""
    # Go treats slash-delimited subtest names as independently matched regexes.
    pattern = "/".join(f"^{re.escape(part)}$" for part in test.split("/") if part)
    if not pattern:
        return ""
    return f"go test -run {shlex.quote(pattern)} {shlex.quote(package)}"


def _best_failure_detail(lines: list[str]) -> tuple[str, int | None, int | None, str]:
    for candidate in reversed(lines[-40:]):
        match = _GO_LOCATION_RE.match(candidate.rstrip())
        if match:
            return (
                match.group("file").strip(),
                int(match.group("line")),
                int(match.group("column")) if match.group("column") else None,
                match.group("message").strip(),
            )
    for candidate in reversed(lines[-30:]):
        text = candidate.strip()
        if not text or text.startswith(_NOISE_PREFIXES) or text.startswith(("==>", "<==")):
            continue
        if re.match(r"^(?:ok|\?)\s+\S+", text):
            continue
        if text.startswith("$ ") or text.startswith("# "):
            continue
        return "-", None, None, text
    return "-", None, None, "Go test failed"


class GoTestJSONStream:
    """Render ``go test -json`` while retaining authoritative event metadata."""

    def __init__(self) -> None:
        self._started_packages: set[str] = set()
        self._finished_packages: set[str] = set()
        self._package_status: dict[str, str] = {}
        self._test_status: dict[tuple[str, str], str] = {}
        self._test_elapsed: dict[tuple[str, str], float | None] = {}
        self._no_test_packages: set[str] = set()
        self._package_output: dict[str, list[str]] = {}
        self._test_output: dict[tuple[str, str], list[str]] = {}
        self._failures: list[GoTestFailure] = []
        self._failure_keys: set[tuple[str, str]] = set()

    def _record_output(self, package: str, test: str, output: str) -> None:
        if package:
            self._package_output.setdefault(package, []).extend(output.splitlines())
        if package and test:
            self._test_output.setdefault((package, test), []).extend(output.splitlines())
        no_tests = _NO_TESTS_RE.match(output.strip())
        if no_tests:
            self._no_test_packages.add(no_tests.group("package"))

    def _record_test_result(self, package: str, test: str, status: str, elapsed: float | None = None) -> None:
        if not package or not test:
            return
        self._test_status[(package, test)] = status
        self._test_elapsed[(package, test)] = elapsed
        if status != "fail" or (package, test) in self._failure_keys:
            return
        file_text, line_no, column, message = _best_failure_detail(self._test_output.get((package, test), []))
        self._failures.append(GoTestFailure(
            package=package,
            test=test,
            file=file_text,
            line=line_no,
            column=column,
            message=message,
            rerun=_rerun_command(package, test),
        ))
        self._failure_keys.add((package, test))

    def _record_package_result(self, package: str, status: str) -> None:
        if not package:
            return
        self._package_status[package] = status
        if status != "fail":
            return
        if any(failure.package == package for failure in self._failures):
            return
        file_text, line_no, column, message = _best_failure_detail(self._package_output.get(package, []))
        self._failures.append(GoTestFailure(
            package=package,
            file=file_text,
            line=line_no,
            column=column,
            message=message,
        ))
        self._failure_keys.add((package, ""))

    def feed(self, raw_line: str) -> str:
        stripped = raw_line.strip()
        if not stripped:
            return raw_line
        try:
            event = json.loads(stripped)
        except (json.JSONDecodeError, TypeError):
            return raw_line
        if not isinstance(event, dict):
            return raw_line

        action = str(event.get("Action") or "").strip().lower()
        package = str(event.get("Package") or "").strip()
        test = str(event.get("Test") or "").strip()

        if action == "start" and package and not test:
            if package in self._started_packages:
                return ""
            self._started_packages.add(package)
            return f"==> go-test package {package}\n"

        if action == "output":
            output = str(event.get("Output") or "")
            self._record_output(package, test, output)
            return output

        if action in {"pass", "fail", "skip"} and test:
            elapsed_value = event.get("Elapsed")
            try:
                elapsed = float(elapsed_value) if elapsed_value is not None else None
            except (TypeError, ValueError):
                elapsed = None
            self._record_test_result(package, test, action, elapsed)
            return ""

        if action in {"pass", "fail", "skip"} and package:
            self._record_package_result(package, action)
            key = f"{package}\0{action}"
            if key in self._finished_packages:
                return ""
            self._finished_packages.add(key)
            return f"<== go-test package {package} {action}\n"

        return ""

    def report(self) -> GoTestReport:
        report = GoTestReport(failures=[GoTestFailure(**failure.to_dict()) for failure in self._failures])
        report.tests_passed = sum(status == "pass" for status in self._test_status.values())
        report.tests_failed = sum(status == "fail" for status in self._test_status.values())
        report.tests_skipped = sum(status == "skip" for status in self._test_status.values())
        report.cases = [
            GoTestCase(package=package, test=test, status=status, elapsed=self._test_elapsed.get((package, test)))
            for (package, test), status in sorted(self._test_status.items())
        ]

        for package in set(self._package_status) | self._no_test_packages:
            if package in self._no_test_packages:
                report.packages_no_tests += 1
                continue
            status = self._package_status.get(package, "")
            if status == "pass":
                report.packages_passed += 1
            elif status == "fail":
                report.packages_failed += 1
            elif status == "skip":
                report.packages_skipped += 1
        return report


def parse_go_test_report(output: str) -> GoTestReport:
    """Parse a rendered/plain Go test stream as a compatibility fallback.

    Devtool-owned ``go test -json`` runs use :class:`GoTestJSONStream.report`
    directly so package identity is not inferred from interleaved text.
    """

    report = GoTestReport()
    current_package = ""
    current_block: list[str] = []
    package_blocks: dict[str, list[str]] = {}
    no_test_packages: set[str] = set()
    package_status: dict[str, str] = {}
    test_status: dict[tuple[str, str], str] = {}
    pending_failures: list[GoTestFailure] = []
    failure_keys: set[tuple[str, str, str, int | None, str]] = set()

    lines = output.splitlines()
    for line in lines:
        start = _PACKAGE_START_RE.match(line.strip())
        if start:
            current_package = start.group("package")
            current_block = []
            package_blocks.setdefault(current_package, [])
            continue

        no_tests = _NO_TESTS_RE.match(line.strip())
        if no_tests:
            no_test_packages.add(no_tests.group("package"))

        if current_package:
            package_blocks.setdefault(current_package, []).append(line)

        # Go may print ``file_test.go:line`` immediately after ``--- FAIL:``.
        location = _GO_LOCATION_RE.match(line.rstrip())
        if location and pending_failures:
            failure = pending_failures[-1]
            if failure.file in {"", "-"}:
                failure.file = location.group("file").strip()
                failure.line = int(location.group("line"))
                failure.column = int(location.group("column")) if location.group("column") else None
                failure.message = location.group("message").strip()

        test_match = _TEST_RESULT_RE.match(line.strip())
        if test_match:
            status = test_match.group("status").lower()
            test = test_match.group("test")
            package = current_package
            test_status[(package, test)] = status
            if status == "fail":
                file_text, line_no, column, message = _best_failure_detail(current_block)
                pending_failures.append(GoTestFailure(
                    package=package,
                    test=test,
                    file=file_text,
                    line=line_no,
                    column=column,
                    message=message,
                    rerun=_rerun_command(package, test),
                ))
            current_block.append(line)
            continue

        end = _PACKAGE_END_RE.match(line.strip())
        if end:
            package = end.group("package")
            status = end.group("status")
            package_status[package] = status
            for failure in pending_failures:
                if not failure.package:
                    failure.package = package
                    failure.rerun = _rerun_command(package, failure.test)
                key = (failure.package, failure.test, failure.file, failure.line, failure.message)
                if key not in failure_keys:
                    failure_keys.add(key)
                    report.failures.append(failure)
            pending_failures.clear()
            current_package = ""
            current_block = []
            continue

        # Plain go test fallback: package result lines carry the package name.
        plain_fail = re.match(r"^FAIL\s+(?P<package>\S+)(?:\s|$)", line.strip())
        if plain_fail:
            package = plain_fail.group("package")
            package_status[package] = "fail"
            package_blocks.setdefault(package, []).extend(current_block)
            for failure in pending_failures:
                if not failure.package:
                    failure.package = package
                    failure.rerun = _rerun_command(package, failure.test)
                key = (failure.package, failure.test, failure.file, failure.line, failure.message)
                if key not in failure_keys:
                    failure_keys.add(key)
                    report.failures.append(failure)
            pending_failures.clear()
        else:
            plain_pass = re.match(r"^ok\s+(?P<package>\S+)(?:\s|$)", line.strip())
            if plain_pass:
                package_status[plain_pass.group("package")] = "pass"

        current_block.append(line)

    for failure in pending_failures:
        key = (failure.package, failure.test, failure.file, failure.line, failure.message)
        if key not in failure_keys:
            failure_keys.add(key)
            report.failures.append(failure)

    report.tests_passed = sum(status == "pass" for status in test_status.values())
    report.tests_failed = sum(status == "fail" for status in test_status.values())
    report.tests_skipped = sum(status == "skip" for status in test_status.values())
    report.cases = [
        GoTestCase(package=package, test=test, status=status)
        for (package, test), status in sorted(test_status.items())
    ]

    for package in set(package_status) | no_test_packages:
        if package in no_test_packages:
            report.packages_no_tests += 1
            continue
        status = package_status.get(package, "")
        if status == "pass":
            report.packages_passed += 1
        elif status == "fail":
            report.packages_failed += 1
        elif status == "skip":
            report.packages_skipped += 1

    failed_packages_with_test = {item.package for item in report.failures if item.package}
    for package, status in package_status.items():
        if status != "fail" or package in failed_packages_with_test:
            continue
        detail_lines = package_blocks.get(package) or lines
        file_text, line_no, column, message = _best_failure_detail(detail_lines)
        report.failures.append(GoTestFailure(
            package=package,
            file=file_text,
            line=line_no,
            column=column,
            message=message,
        ))

    return report
