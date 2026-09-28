from __future__ import annotations

"""Concise structured test diagnostics derived from canonical test evidence."""

__test__ = False

from dataclasses import dataclass
import difflib
import hashlib
import json
import reprlib
import re
from typing import Any, Iterable, Mapping, Sequence

from .models import Issue
from .test_model import TestResult, TestStatus


_GENERATED_HINTS = (
    "completion",
    "generated",
    "golden",
    "cli help",
    "cli-help",
    "guide",
    "man page",
    "parser",
)
_PROCESS_LEAK_HINTS = (
    "leaked process state",
    "process-state",
    "devtool_validation_state_repo_root",
    "devtool_transaction_repo_root",
    "devtool_transaction_primary_repo_root",
)
_CASCADE_HINTS = (
    "cascade",
    "downstream casualty",
    "blocked by root failure",
    "shared state contamination",
)
_STALE_BRITTLE_HINTS = (
    "stale test",
    "stale expectation",
    "brittle test",
    "obsolete expectation",
    "selector no longer exists",
)


def concise_repr(value: Any, *, max_chars: int = 800) -> str:
    """Return a deterministic bounded repr suitable for assertion summaries."""

    helper = reprlib.Repr()
    helper.maxstring = max(80, min(max_chars, 600))
    helper.maxother = max(80, min(max_chars, 600))
    helper.maxlist = helper.maxtuple = helper.maxset = helper.maxfrozenset = 12
    helper.maxdict = 12
    text = helper.repr(value)
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 18)].rstrip() + " …<truncated>"


def concise_text_diff(expected: str, actual: str, *, max_lines: int = 80) -> tuple[str, str]:
    """Return ``(bounded, full)`` unified diff text.

    Callers can render ``bounded`` in the terminal while retaining ``full`` as
    an attachment/evidence payload.
    """

    full = "\n".join(difflib.unified_diff(
        str(expected).splitlines(),
        str(actual).splitlines(),
        fromfile="expected",
        tofile="actual",
        lineterm="",
    ))
    lines = full.splitlines()
    if len(lines) <= max_lines:
        return full, full
    bounded = "\n".join(lines[:max_lines]) + f"\n... diff truncated after {max_lines} line(s); full diff retained"
    return bounded, full


@dataclass(frozen=True, slots=True)
class FailureCluster:
    cluster_id: str
    classification: str
    root_index: int
    member_indices: tuple[int, ...]
    root_diagnostic_key: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "classification": self.classification,
            "root_index": self.root_index,
            "member_indices": list(self.member_indices),
            "root_diagnostic_key": self.root_diagnostic_key,
            "reason": self.reason,
        }


def _normalized_issue_text(issue: Issue) -> str:
    return " ".join(
        part for part in (
            str(issue.code or ""),
            str(issue.file or ""),
            str(issue.message or ""),
        ) if part
    ).casefold()


def _classification(issue: Issue) -> tuple[str, str, str]:
    text = _normalized_issue_text(issue)
    if any(hint in text for hint in _PROCESS_LEAK_HINTS):
        return (
            "process_state_leak",
            "process-state",
            "Process-global state drift is the root diagnostic; later failures must not be merged into it without evidence.",
        )
    if any(hint in text for hint in _CASCADE_HINTS):
        return (
            "cascade",
            "cascade:reported-root",
            "The diagnostic explicitly identifies downstream/cascade behavior; raw failures remain preserved.",
        )
    if any(hint in text for hint in _STALE_BRITTLE_HINTS):
        return (
            "stale_brittle",
            "stale-brittle:test-contract",
            "The diagnostic explicitly identifies a stale or brittle test contract rather than a product regression.",
        )
    if any(hint in text for hint in _GENERATED_HINTS):
        # Generated parser/help/completion/docs parity failures often fan out
        # from one source drift. Keep one stable root key while preserving every
        # raw failure in the canonical result.
        return (
            "generated_surface_drift",
            "generated-surface:cli-contract",
            "Generated CLI/parser/completion/documentation drift shares one root-diagnostic family.",
        )
    key = f"{str(issue.code or 'failure').casefold()}:{str(issue.file or '-').casefold()}"
    return (
        "independent",
        key,
        "No supported shared-root signal was found; keep the failure independently actionable.",
    )


def cluster_failure_issues(issues: Iterable[Issue]) -> tuple[FailureCluster, ...]:
    rows = [issue for issue in issues if str(issue.severity or "").casefold() == "error"]
    grouped: dict[tuple[str, str], list[int]] = {}
    reasons: dict[tuple[str, str], str] = {}
    for index, issue in enumerate(rows):
        classification, key, reason = _classification(issue)
        group_key = (classification, key)
        grouped.setdefault(group_key, []).append(index)
        reasons[group_key] = reason

    clusters: list[FailureCluster] = []
    for (classification, root_key), indices in grouped.items():
        # Independent findings intentionally remain one cluster each. Grouping
        # unrelated same-file failures would create the same cascade-hiding bug
        # this foundation is designed to prevent.
        groups = [[index] for index in indices] if classification == "independent" else [indices]
        for members in groups:
            digest = hashlib.sha256(
                f"{classification}\0{root_key}\0{members[0]}".encode("utf-8")
            ).hexdigest()[:16]
            clusters.append(FailureCluster(
                cluster_id=f"fc-{digest}",
                classification=classification,
                root_index=members[0],
                member_indices=tuple(members),
                root_diagnostic_key=root_key,
                reason=reasons[(classification, root_key)],
            ))
    return tuple(clusters)


def _failure_rows(result: TestResult, *, limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    bad = {TestStatus.FAILED, TestStatus.ERROR, TestStatus.CANCELLED, TestStatus.XPASS}
    for suite in result.suites:
        for case in suite.cases:
            if case.status not in bad:
                continue
            failure = case.failure
            rows.append({
                "id": case.id,
                "name": case.name,
                "status": case.status.value,
                "message": concise_repr(failure.message if failure else case.name, max_chars=240),
            })
            if len(rows) >= limit:
                return rows
    return rows


def structured_junit_summary(
    result: TestResult | None,
    *,
    issues: Sequence[Issue] = (),
    max_failures: int = 12,
) -> dict[str, Any]:
    """Build the compact machine-readable tail summary for a JUnit-backed run."""

    clusters = cluster_failure_issues(issues)
    if result is None:
        counts = {"passed": 0, "failed": 0, "skipped": 0, "errors": 0, "cancelled": 0, "xfailed": 0, "xpassed": 0, "total": 0}
        failures: list[dict[str, Any]] = []
        result_id = ""
    else:
        summary = result.summary
        counts = {
            "passed": summary.passed,
            "failed": summary.failed,
            "skipped": summary.skipped,
            "errors": summary.errors,
            "cancelled": summary.cancelled,
            "xfailed": summary.xfailed,
            "xpassed": summary.xpassed,
            "total": summary.total,
        }
        failures = _failure_rows(result, limit=max(1, int(max_failures)))
        result_id = result.id
    return {
        "schema_version": 1,
        "source": "junit",
        "result_id": result_id,
        "counts": counts,
        "failure_count_total": int(counts.get("failed", 0)) + int(counts.get("errors", 0)) + int(counts.get("cancelled", 0)) + int(counts.get("xpassed", 0)),
        "failure_count_shown": len(failures),
        "failures": failures,
        "failure_clusters": [cluster.to_dict() for cluster in clusters],
        "generated_surface_root_diagnostics": [
            cluster.root_diagnostic_key
            for cluster in clusters
            if cluster.classification == "generated_surface_drift"
        ],
    }


def render_structured_test_summary(payload: Mapping[str, Any]) -> str:
    return "DEVTOOL_TEST_SUMMARY " + json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
