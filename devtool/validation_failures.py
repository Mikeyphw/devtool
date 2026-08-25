from __future__ import annotations

"""Validation-attempt failure classification.

The generic command runner exposes process return codes, parsed output, issues,
and lightweight metadata.  This module converts those signals into the durable
validation verdicts used by sequential sessions without treating Android,
Gradle, chroot, or resource failures as bad source code.
"""

from dataclasses import dataclass, field
import re
from typing import Any

from .models import OperationResult


@dataclass(slots=True)
class FailureClassification:
    verdict: str
    retryable: bool = False
    category: str = "code"
    evidence: list[str] = field(default_factory=list)
    signal: int | None = None
    recommended_profile: str = ""


_RESOURCE_MARKERS = (
    "out of memory",
    "memory pressure",
    "memory guard",
    "killed by earlyoom",
    "low memory",
    "thermal guard",
    "temperature is",
    "battery is",
    "low disk space",
    "no space left on device",
    "devtool validation supervisor stopped process: resource:",
    "devtool stopped validation after hard emergency floor",
    "devtool stopped validation after corroborated sustained memory pressure",
    "gradle build daemon disappeared unexpectedly",
    "daemon disappeared unexpectedly",
    "daemon has disappeared",
    "daemon process has disappeared",
    "daemon process was terminated",
    "daemon was killed",
    "daemon has been killed",
    "daemon unexpectedly stopped",
)
_ENVIRONMENT_MARKERS = (
    "chroot bridge disconnected",
    "connection to the build service was lost",
    "connection reset by peer",
    "temporary failure in name resolution",
    "network is unreachable",
    "could not resolve host",
    "unable to load the service index",
    "remote host terminated the handshake",
)
_TOOLCHAIN_MARKERS = (
    "command not found",
    "sdk location not found",
    "could not find sdk",
    "failed to find package",
    "java installation on your machine",
    "no matching toolchains found",
    "exec format error",
    "wrong architecture",
)
_TEST_MARKERS = (
    "tests failed",
    "test failed",
    "there were failing tests",
    "test run failed",
    "failed tests:",
)
_TIMEOUT_MARKERS = (
    "timed out",
    "command timeout",
    "devtool validation supervisor stopped process: timeout:",
    "stopped a quiet child process",
)
_INTERRUPT_MARKERS = (
    "interrupted by user",
    "cancelled from devtool control center",
    "canceled from devtool control center",
    "devtool validation supervisor stopped process: interrupted:",
)


def _normalized_failure_kind(result: OperationResult) -> str:
    return str(result.failure_kind or "").strip().lower().replace("_", "-")


def _process_signal(returncode: int) -> int | None:
    if returncode < 0:
        return abs(returncode)
    if 128 < returncode <= 192:
        return returncode - 128
    return None


def _metadata_event_strings(metadata: dict[str, Any]) -> list[str]:
    evidence: list[str] = []
    for key in (
        "supervisor_reason",
        "resource_guard_reason",
        "termination_reason",
        "bridge_failure",
    ):
        value = str(metadata.get(key) or "").strip()
        if value:
            evidence.append(f"{key}={value}")
    events = metadata.get("supervisor_events")
    if isinstance(events, list):
        for event in events:
            if isinstance(event, dict):
                kind = str(event.get("kind") or "event")
                reason = str(event.get("reason") or "").strip()
                evidence.append(f"supervisor:{kind}: {reason}".rstrip())
            elif str(event).strip():
                evidence.append(f"supervisor: {str(event).strip()}")
    return evidence


def _contains_any(text: str, markers: tuple[str, ...]) -> str:
    return next((marker for marker in markers if marker in text), "")


def classify_validation_failure(result: OperationResult) -> FailureClassification:
    """Classify one operation result for durable validation state.

    Return-code-only guesses are deliberately conservative.  Exit 137 is a
    resource failure only when corroborated by output, metadata, or a known
    guard failure kind; otherwise it is an environment failure requiring review.
    """

    returncode = int(result.returncode or 0)
    if returncode == 0:
        return FailureClassification("passed", category="success")

    failure_kind = _normalized_failure_kind(result)
    text = str(result.output or "").lower()
    metadata = dict(result.metadata or {})
    signal = _process_signal(returncode)
    evidence = _metadata_event_strings(metadata)
    if failure_kind:
        evidence.append(f"failure_kind={failure_kind}")
    if signal is not None:
        evidence.append(f"signal={signal}")

    marker = _contains_any(text, _INTERRUPT_MARKERS)
    if (
        failure_kind in {"cancelled", "canceled", "interrupted", "user-interrupt", "user-interrupted"}
        or returncode == 130
        or (signal in {2, 15} and marker)
        or marker
    ):
        if marker:
            evidence.append(f"output={marker}")
        return FailureClassification(
            "interrupted", retryable=True, category="control", evidence=evidence, signal=signal
        )

    marker = _contains_any(text, _TIMEOUT_MARKERS)
    if (
        failure_kind in {"timeout", "timed-out", "command-timeout", "idle-timeout"}
        or bool(metadata.get("timed_out"))
        or marker
        or returncode == 124
    ):
        if marker:
            evidence.append(f"output={marker}")
        return FailureClassification(
            "timed-out", retryable=True, category="timeout", evidence=evidence, signal=signal
        )

    marker = _contains_any(text, _RESOURCE_MARKERS)
    resource_kind = failure_kind in {
        "memory-guard",
        "memory-pressure",
        "resource-failure",
        "resource-interrupted",
        "metaspace-oom",
        "thermal-guard",
        "battery-guard",
        "low-disk-space",
        "no-space-left",
    }
    if resource_kind or bool(metadata.get("resource_guard_triggered")) or marker:
        if marker:
            evidence.append(f"output={marker}")
        return FailureClassification(
            "resource-failed",
            retryable=True,
            category="resource",
            evidence=evidence,
            signal=signal,
            recommended_profile="android-low-power",
        )

    marker = _contains_any(text, _ENVIRONMENT_MARKERS)
    environment_kind = failure_kind in {
        "environment-failure",
        "bridge-failure",
        "chroot-failure",
        "gradle-daemon-disappeared",
        "nuget-failure",
        "restore-failure",
        "network-failure",
    }
    if environment_kind or marker or (returncode == 137 and signal == 9):
        if marker:
            evidence.append(f"output={marker}")
        if returncode == 137 and not marker:
            evidence.append("exit 137 was not corroborated as memory pressure")
        return FailureClassification(
            "environment-failed",
            retryable=True,
            category="environment",
            evidence=evidence,
            signal=signal,
            recommended_profile="android-low-power" if returncode == 137 else "",
        )

    marker = _contains_any(text, _TOOLCHAIN_MARKERS)
    if failure_kind in {
        "toolchain-missing",
        "missing-tool",
        "missing-sdk",
        "missing-sdk-package",
        "arm-mismatch",
    } or marker:
        if marker:
            evidence.append(f"output={marker}")
        return FailureClassification(
            "toolchain-missing", category="toolchain", evidence=evidence, signal=signal
        )

    if failure_kind in {
        "internal-error",
        "validation-session-error",
        "rollback-failure",
        "artifact-cleanup-failure",
    }:
        return FailureClassification(
            "internal-error", category="internal", evidence=evidence, signal=signal
        )

    marker = _contains_any(text, _TEST_MARKERS)
    tests_metadata = metadata.get("tests")
    failed_tests = 0
    if isinstance(tests_metadata, dict):
        try:
            failed_tests = int(tests_metadata.get("failed", 0) or 0)
        except (TypeError, ValueError):
            failed_tests = 0
    if (
        failure_kind in {"test-failure", "tests-failed", "test-failed"}
        or marker
        or failed_tests > 0
        or re.search(r"\bfailed:\s*[1-9]\d*\b", text)
    ):
        if marker:
            evidence.append(f"output={marker}")
        if failed_tests:
            evidence.append(f"failed_tests={failed_tests}")
        return FailureClassification(
            "test-failed", category="test", evidence=evidence, signal=signal
        )

    return FailureClassification(
        "code-failed", category="code", evidence=evidence, signal=signal
    )
