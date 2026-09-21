from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Any

_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_CARGO_RE = re.compile(r"test result:\s+\w+\.\s+(\d+) passed;\s+(\d+) failed;\s+(\d+) ignored(?:;\s+(\d+) measured)?(?:;\s+(\d+) filtered out)?", re.I)
_MAVEN_RE = re.compile(r"Tests run:\s*(\d+)\s*,\s*Failures:\s*(\d+)\s*,\s*Errors:\s*(\d+)\s*,\s*Skipped:\s*(\d+)", re.I)
_JEST_RE = re.compile(r"^Tests:\s*(.*)$", re.I)
_VITEST_RE = re.compile(r"^Tests\s+(.*?)(?:\((\d+)\))?\s*$", re.I)
_COUNT_WORD_RE = re.compile(r"(\d+)\s+(passed|failed|skipped|pending|todo|ignored|total)\b", re.I)
_TAP_RE = re.compile(r"^#\s*(tests|pass|fail|skip|skipped|todo|cancelled)\s+(\d+)\s*$", re.I)

@dataclass(slots=True)
class TestCounts:
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    total: int = 0

@dataclass(slots=True)
class TestCountAccumulator:
    """Normalize common test-runner output into Devtool's shared counters.

    Summary formats replace the current framework total. Per-test Go JSON is
    keyed by package/test identity so duplicate/replayed lines cannot inflate it.
    """
    framework: str = ""
    counts: TestCounts = field(default_factory=TestCounts)
    _go_status: dict[tuple[str, str], str] = field(default_factory=dict)
    _tap: dict[str, int] = field(default_factory=dict)
    _maven_results_pending: bool = False
    _maven_aggregate: TestCounts = field(default_factory=TestCounts)
    _maven_saw_aggregate: bool = False

    @classmethod
    def for_argv(cls, argv: list[str]) -> "TestCountAccumulator":
        joined = " ".join(argv).lower()
        framework = ""
        if "pytest" in joined:
            framework = "pytest"
        elif "cargo" in joined and "test" in joined:
            framework = "cargo"
        elif "mvn" in joined or "maven" in joined:
            framework = "maven"
        elif any(x in joined for x in ("jest", "vitest", "node --test", "npm test", "npm run test", "pnpm test", "yarn test")):
            framework = "node"
        elif "go test" in joined:
            framework = "go"
        return cls(framework=framework)

    def feed(self, line: str) -> bool:
        text = _ANSI_RE.sub("", line).strip()
        if not text or self.framework == "pytest":
            return False
        if self._feed_go(text):
            return True
        m = _CARGO_RE.search(text)
        if m:
            passed, failed, ignored = map(int, m.group(1, 2, 3))
            self.counts = TestCounts(
                self.counts.passed + passed, self.counts.failed + failed,
                self.counts.skipped + ignored, self.counts.total + passed + failed + ignored,
            )
            return True
        if self.framework == "maven" and re.search(r"\bResults:\s*$", text, re.I):
            self._maven_results_pending = True
            return False
        m = _MAVEN_RE.search(text)
        if m:
            total, failures, errors, skipped = map(int, m.groups())
            current = TestCounts(max(0, total - failures - errors - skipped), failures + errors, skipped, total)
            if self.framework == "maven" and self._maven_results_pending:
                self._maven_results_pending = False
                self._maven_saw_aggregate = True
                self._maven_aggregate = TestCounts(
                    self._maven_aggregate.passed + current.passed,
                    self._maven_aggregate.failed + current.failed,
                    self._maven_aggregate.skipped + current.skipped,
                    self._maven_aggregate.total + current.total,
                )
                self.counts = TestCounts(
                    self._maven_aggregate.passed, self._maven_aggregate.failed,
                    self._maven_aggregate.skipped, self._maven_aggregate.total,
                )
            elif not self._maven_saw_aggregate:
                # Fallback for runners/log formats that emit only one summary.
                # Once Surefire's `Results:` aggregate appears, class-level
                # summaries are ignored to avoid double-counting.
                self.counts = current
            return True
        if self.framework == "node":
            if self._feed_node(text):
                return True
        return False

    def _feed_go(self, text: str) -> bool:
        if self.framework != "go" or not text.startswith("{"):
            return False
        try:
            event: dict[str, Any] = json.loads(text)
        except (ValueError, TypeError):
            return False
        test = str(event.get("Test") or "")
        action = str(event.get("Action") or "")
        if not test or action not in {"pass", "fail", "skip"}:
            return False
        key = (str(event.get("Package") or ""), test)
        self._go_status[key] = action
        values = list(self._go_status.values())
        self.counts = TestCounts(values.count("pass"), values.count("fail"), values.count("skip"), len(values))
        return True

    def _feed_node(self, text: str) -> bool:
        tap = _TAP_RE.match(text)
        if tap:
            self._tap[tap.group(1).lower()] = int(tap.group(2))
            total = self._tap.get("tests", 0)
            passed = self._tap.get("pass", 0)
            # Node TAP can report cancelled tests separately. The shared model
            # has no cancelled bucket, so treat them as unsuccessful/failed
            # rather than silently losing them from accounting.
            failed = self._tap.get("fail", 0) + self._tap.get("cancelled", 0)
            skipped = (
                self._tap.get("skip", 0)
                + self._tap.get("skipped", 0)
                + self._tap.get("todo", 0)
            )
            if total or passed or failed or skipped:
                self.counts = TestCounts(passed, failed, skipped, total or passed + failed + skipped)
            return True
        m = _JEST_RE.match(text) or _VITEST_RE.match(text)
        if not m:
            return False
        body = m.group(1)
        values = {name.lower(): int(value) for value, name in _COUNT_WORD_RE.findall(body)}
        passed = values.get("passed", 0)
        failed = values.get("failed", 0)
        skipped = values.get("skipped", 0) + values.get("pending", 0) + values.get("todo", 0) + values.get("ignored", 0)
        total = values.get("total", 0)
        if len(m.groups()) > 1 and m.group(2):
            total = int(m.group(2))
        if not total:
            total = passed + failed + skipped
        if total:
            self.counts = TestCounts(passed, failed, skipped, total)
            return True
        return False
