from __future__ import annotations

__test__ = False  # Prevent pytest from collecting production TSYS modules.

"""Canonical adapters between Devtool's legacy test evidence and TSYS results.

TSYS-02 keeps the current runner/progress parsers as compatibility fallbacks,
but promotes structured protocol/report evidence to the canonical TestResult
contract introduced by TSYS-01.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
import json
import re

from .test_counts import TestCountAccumulator, TestCounts
from .test_execution import TestExecutionContext, TestExecutionError, TestRunner, TestRunnerRegistry
from .test_model import (
    TestArtifact,
    TestCaseResult,
    TestFailure,
    TestResult,
    TestSource,
    TestSpec,
    TestStatus,
    TestSuiteResult,
)

_EVIDENCE_RANK = {"exit": 0, "stdout": 1, "report": 2, "protocol": 3}
_SAFE_ID = re.compile(r"[^A-Za-z0-9._:/#-]+")
_PYTEST_SUMMARY_RE = re.compile(r"(?P<count>\d+)\s+(?P<kind>passed|failed|skipped|xfailed|xpassed|errors?)\b", re.I)
_DOTNET_SUMMARY_RE = re.compile(
    r"\b(?:Passed|Failed)!\s+-\s+Failed:\s*(?P<failed>\d+),\s*Passed:\s*(?P<passed>\d+),\s*Skipped:\s*(?P<skipped>\d+),\s*Total:\s*(?P<total>\d+)",
    re.I,
)


def safe_test_id(value: object, fallback: str = "test") -> str:
    text = _SAFE_ID.sub("-", str(value or "").strip()).strip("-")
    text = text.lstrip("._:/#-")
    return text or fallback


def compatibility_counts(result: TestResult) -> dict[str, int]:
    """Project a canonical result into Devtool's legacy four counters."""
    return {
        "passed": result.tests_passed,
        "failed": result.tests_failed,
        "skipped": result.tests_skipped,
        "total": result.tests_total,
    }


def _aggregate_cases(prefix: str, counts: TestCounts, *, evidence: str) -> tuple[TestCaseResult, ...]:
    cases: list[TestCaseResult] = []
    for status_name, count, status in (
        ("passed", counts.passed, TestStatus.PASSED),
        ("failed", counts.failed, TestStatus.FAILED),
        ("skipped", counts.skipped, TestStatus.SKIPPED),
    ):
        for index in range(max(0, int(count))):
            cases.append(TestCaseResult(
                safe_test_id(f"{prefix}/{status_name}/{index + 1}"),
                f"{status_name} #{index + 1}",
                status,
                metadata={"evidence": evidence, "aggregate": True},
            ))
    return tuple(cases)


def result_from_counts(
    result_id: str,
    *,
    framework: str,
    counts: TestCounts,
    evidence: str,
    suite_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> TestResult:
    sid = safe_test_id(suite_id or framework or "tests")
    suite = TestSuiteResult(
        sid,
        framework or "tests",
        _aggregate_cases(sid, counts, evidence=evidence),
        metadata={"evidence": evidence, "framework": framework},
    )
    merged = {"evidence": evidence, "framework": framework, **dict(metadata or {})}
    return TestResult(safe_test_id(result_id), (suite,), metadata=merged)


def result_from_stdout(result_id: str, argv: Sequence[str], output: str, *, framework: str = "") -> TestResult | None:
    normalized = str(framework or "").strip().lower()
    text = str(output or "")
    if normalized == "pytest" or (not normalized and "pytest" in " ".join(argv).lower()):
        # Pytest's terminal summary is a compatibility fallback when JUnit XML
        # is unavailable. The final summary line is authoritative over progress
        # marks because reruns/plugins can redraw intermediate progress.
        values: dict[str, int] = {}
        for line in reversed(text.splitlines()):
            pairs = list(_PYTEST_SUMMARY_RE.finditer(line))
            if not pairs:
                continue
            values = {m.group("kind").lower(): int(m.group("count")) for m in pairs}
            if values:
                break
        if values:
            counts = TestCounts(
                passed=values.get("passed", 0),
                failed=values.get("failed", 0) + values.get("error", 0) + values.get("errors", 0) + values.get("xpassed", 0),
                skipped=values.get("skipped", 0) + values.get("xfailed", 0),
                total=0,
            )
            counts.total = counts.passed + counts.failed + counts.skipped
            return result_from_counts(result_id, framework="pytest", counts=counts, evidence="stdout")
    if normalized == "dotnet":
        passed = failed = skipped = total = 0
        seen: set[str] = set()
        for line in text.splitlines():
            match = _DOTNET_SUMMARY_RE.search(line)
            if not match:
                continue
            identity = " ".join(line.split())
            if identity in seen:
                continue
            seen.add(identity)
            passed += int(match.group("passed")); failed += int(match.group("failed")); skipped += int(match.group("skipped")); total += int(match.group("total"))
        if total:
            return result_from_counts(result_id, framework="dotnet", counts=TestCounts(passed, failed, skipped, total), evidence="stdout")
    accumulator = TestCountAccumulator(framework=normalized) if normalized else TestCountAccumulator.for_argv(list(argv))
    for line in text.splitlines():
        accumulator.feed(line)
    counts = accumulator.counts
    if not (counts.total or counts.passed or counts.failed or counts.skipped):
        return None
    detected = accumulator.framework or normalized or "stdout"
    return result_from_counts(result_id, framework=detected, counts=counts, evidence="stdout")


def result_from_progress_snapshot(result_id: str, snapshot: Mapping[str, Any], *, framework: str = "") -> TestResult | None:
    try:
        counts = TestCounts(
            passed=max(0, int(snapshot.get("tests_passed") or 0)),
            failed=max(0, int(snapshot.get("tests_failed") or 0)),
            skipped=max(0, int(snapshot.get("tests_skipped") or 0)),
            total=max(0, int(snapshot.get("tests_total") or 0)),
        )
    except (TypeError, ValueError):
        return None
    if not counts.total:
        counts.total = counts.passed + counts.failed + counts.skipped
    if not counts.total:
        return None
    return result_from_counts(result_id, framework=framework or "progress", counts=counts, evidence="stdout")


def result_from_go_report(result_id: str, report: Any, *, evidence: str = "protocol") -> TestResult:
    """Convert Go's protocol report, retaining stable package/test identities."""
    cases: list[TestCaseResult] = []
    raw_cases = list(getattr(report, "cases", []) or [])
    failures = list(getattr(report, "failures", []) or [])
    failure_by_key = {(str(getattr(item, "package", "")), str(getattr(item, "test", ""))): item for item in failures}
    failure_by_test = {str(getattr(item, "test", "")): item for item in failures if str(getattr(item, "test", ""))}
    if raw_cases:
        for item in raw_cases:
            package = str(getattr(item, "package", "") or "")
            test = str(getattr(item, "test", "") or "")
            status_text = str(getattr(item, "status", "") or "")
            status = {"pass": TestStatus.PASSED, "fail": TestStatus.FAILED, "skip": TestStatus.SKIPPED}.get(status_text, TestStatus.ERROR)
            failure_obj = failure_by_key.get((package, test)) or failure_by_test.get(test)
            if not package and failure_obj is not None:
                package = str(getattr(failure_obj, "package", "") or "")
            failure = None
            if failure_obj is not None:
                source = None
                file_text = str(getattr(failure_obj, "file", "") or "")
                line = getattr(failure_obj, "line", None)
                if file_text and file_text != "-":
                    source = TestSource("file", file_text, line)
                failure = TestFailure(
                    str(getattr(failure_obj, "message", "Go test failed") or "Go test failed"),
                    source=source,
                )
            cases.append(TestCaseResult(
                safe_test_id(f"{package}#{test}", "go-test"),
                test or package or "go test",
                status,
                duration_seconds=getattr(item, "elapsed", None),
                failure=failure,
                metadata={"evidence": evidence, "package": package},
            ))
    else:
        counts = TestCounts(
            int(getattr(report, "tests_passed", 0) or 0),
            int(getattr(report, "tests_failed", 0) or 0),
            int(getattr(report, "tests_skipped", 0) or 0),
            int(getattr(report, "tests_total", 0) or 0),
        )
        return result_from_counts(result_id, framework="go", counts=counts, evidence=evidence, suite_id="go")
    suite = TestSuiteResult("go", "Go tests", tuple(cases), metadata={"evidence": evidence, "framework": "go"})
    return TestResult(safe_test_id(result_id), (suite,), metadata={"evidence": evidence, "framework": "go"})


def reconcile_results(result_id: str, results: Iterable[TestResult]) -> TestResult | None:
    """Merge structured results by stable identity with richer evidence winning.

    Aggregate stdout results are compatibility fallbacks. If any report/protocol
    result exists, aggregate fallback suites are dropped wholesale to avoid
    counting the same tests once from a report and again from console totals.
    """
    values = [item for item in results if isinstance(item, TestResult)]
    if not values:
        return None
    structured = [item for item in values if _EVIDENCE_RANK.get(str(item.metadata.get("evidence", "stdout")), 1) >= 2]
    source = structured if structured else values
    suites: dict[str, tuple[int, str, dict[str, tuple[int, TestCaseResult]]]] = {}
    artifacts: list[TestArtifact] = []
    for result in source:
        rank = _EVIDENCE_RANK.get(str(result.metadata.get("evidence", "stdout")), 1)
        artifacts.extend(result.artifacts)
        for suite in result.suites:
            slot = suites.setdefault(suite.id, (rank, suite.name, {}))
            suite_rank, suite_name, cases = slot
            if rank > suite_rank:
                suite_rank, suite_name = rank, suite.name
            for case in suite.cases:
                case_rank = _EVIDENCE_RANK.get(str(case.metadata.get("evidence", result.metadata.get("evidence", "stdout"))), rank)
                previous = cases.get(case.id)
                if previous is None or case_rank >= previous[0]:
                    cases[case.id] = (case_rank, case)
            suites[suite.id] = (suite_rank, suite_name, cases)
    merged_suites = tuple(
        TestSuiteResult(sid, value[1], tuple(item[1] for item in value[2].values()))
        for sid, value in suites.items()
    )
    unique_artifacts: list[TestArtifact] = []
    seen_artifacts: set[tuple[str, str]] = set()
    for artifact in artifacts:
        key = (artifact.kind, artifact.path)
        if key not in seen_artifacts:
            seen_artifacts.add(key); unique_artifacts.append(artifact)
    evidence = max((str(item.metadata.get("evidence", "stdout")) for item in source), key=lambda v: _EVIDENCE_RANK.get(v, 1))
    return TestResult(safe_test_id(result_id), merged_suites, artifacts=tuple(unique_artifacts), metadata={"evidence": evidence, "reconciled": True})


def is_test_operation(name: str, argv: Sequence[str], metadata: Mapping[str, Any]) -> bool:
    if metadata.get("contains_tests") or metadata.get("test_framework") or metadata.get("go_test_json"):
        return True
    lowered = str(name or "").lower()
    if "test" in lowered:
        return True
    joined = " ".join(str(item) for item in argv).lower()
    return any(token in joined for token in ("pytest", "dotnet test", "go test", "cargo test", "mvn test", "mvn verify", "mvnw test", "mvnw verify", "npm test", "npm run test", "vitest", "jest", "node --test"))


class EvidenceTestRunner(TestRunner):
    """TestRunner adapter around Devtool's existing command execution machinery."""
    def __init__(
        self,
        command_runner: Callable[..., Any],
        *,
        framework: str,
        structured_reader: Callable[[TestSpec, TestExecutionContext, Any], Iterable[TestResult]] | None = None,
    ) -> None:
        self.command_runner = command_runner
        self.framework = framework
        self.structured_reader = structured_reader

    def run(self, spec: TestSpec, context: TestExecutionContext) -> TestResult:
        outcome = self.command_runner(
            list(spec.command),
            cwd=spec.cwd or context.repo_root,
            env=dict(spec.env),
            timeout=spec.timeout_seconds,
            executor=context.executor,
            cancel_event=context.cancel_event,
        )
        candidates: list[TestResult] = []
        if self.structured_reader is not None:
            candidates.extend(self.structured_reader(spec, context, outcome) or ())
        stdout = str(getattr(outcome, "stdout", "") or getattr(outcome, "output", "") or "")
        fallback = result_from_stdout(spec.id, spec.command, stdout, framework=self.framework)
        if fallback is not None:
            candidates.append(fallback)
        merged = reconcile_results(spec.id, candidates)
        if merged is not None:
            return merged
        # Exit status is the last-resort evidence. It deliberately does not
        # invent a fake test case when the underlying runner exposed none.
        code = int(getattr(outcome, "exit_code", getattr(outcome, "returncode", 0)) or 0)
        return TestResult(spec.id, metadata={"evidence": "exit", "framework": self.framework, "exit_code": code})


def structured_results_for_spec(spec: TestSpec, context: TestExecutionContext, outcome: Any) -> tuple[TestResult, ...]:
    """Collect authoritative structured evidence declared on a TestSpec.

    Imports are intentionally lazy to avoid coupling the report parser module
    back into the test adapter module during import initialization.
    """
    metadata = dict(spec.metadata)
    framework = str(metadata.get("test_framework") or spec.runner or "")
    stdout = str(getattr(outcome, "stdout", "") or getattr(outcome, "output", "") or "")
    values: list[TestResult] = []
    if framework == "go" or metadata.get("go_test_json"):
        from .go_test import GoTestJSONStream
        stream = GoTestJSONStream()
        saw_json = False
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(event, dict) and event.get("Action"):
                saw_json = True
                stream.feed(line)
        if saw_json:
            values.append(result_from_go_report(spec.id, stream.report()))

    configured = metadata.get("report_parsers")
    if isinstance(configured, (list, tuple)):
        parsers = [str(item).strip() for item in configured if str(item).strip()]
    else:
        parser = str(metadata.get("report_parser") or "").strip()
        parsers = [parser] if parser else []
    if parsers:
        from .gradle_reports import collect_report_metadata
        project_root = Path(spec.cwd or context.repo_root).resolve()
        started_at = None
        raw_started = getattr(outcome, "started_at", None)
        if raw_started is None and isinstance(getattr(outcome, "metadata", None), Mapping):
            raw_started = outcome.metadata.get("started_at")
        try:
            started_at = float(raw_started) if raw_started is not None else None
        except (TypeError, ValueError):
            started_at = None
        for parser in dict.fromkeys(parsers):
            collection = collect_report_metadata(
                project_root,
                repo_root=Path(context.repo_root),
                parser=parser,
                tasks=metadata.get("gradle_tasks") or (),
                started_at=started_at,
                command_name=spec.name,
                output=stdout,
                report_path=str(metadata.get("test_report") or ""),
            )
            if collection.test_result is not None:
                values.append(collection.test_result)
    return tuple(values)


class PytestTestRunner(EvidenceTestRunner):
    def __init__(self, command_runner): super().__init__(command_runner, framework="pytest", structured_reader=structured_results_for_spec)
class GradleTestRunner(EvidenceTestRunner):
    def __init__(self, command_runner): super().__init__(command_runner, framework="gradle", structured_reader=structured_results_for_spec)
class DotnetTestRunner(EvidenceTestRunner):
    def __init__(self, command_runner): super().__init__(command_runner, framework="dotnet", structured_reader=structured_results_for_spec)
class GoTestRunner(EvidenceTestRunner):
    def __init__(self, command_runner): super().__init__(command_runner, framework="go", structured_reader=structured_results_for_spec)
class CargoTestRunner(EvidenceTestRunner):
    def __init__(self, command_runner): super().__init__(command_runner, framework="cargo", structured_reader=structured_results_for_spec)
class MavenTestRunner(EvidenceTestRunner):
    def __init__(self, command_runner): super().__init__(command_runner, framework="maven", structured_reader=structured_results_for_spec)
class NodeTestRunner(EvidenceTestRunner):
    def __init__(self, command_runner): super().__init__(command_runner, framework="node", structured_reader=structured_results_for_spec)


def register_polyglot_test_runners(registry: TestRunnerRegistry, command_runner, *, replace: bool = False) -> TestRunnerRegistry:
    for name, runner_type in (
        ("pytest", PytestTestRunner),
        ("gradle", GradleTestRunner),
        ("dotnet", DotnetTestRunner),
        ("go", GoTestRunner),
        ("cargo", CargoTestRunner),
        ("maven", MavenTestRunner),
        ("node", NodeTestRunner),
    ):
        registry.register(name, runner_type(command_runner), replace=replace)
    return registry
