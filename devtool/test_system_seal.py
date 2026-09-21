from __future__ import annotations

"""Executable release-seal checks for Devtool's first-class test system."""

from dataclasses import dataclass
from typing import Callable

from .test_adapters import EVIDENCE_PRECEDENCE, register_polyglot_test_runners
from .test_execution import TestRunnerRegistry
from .test_model import TestStatus
from .test_protocol import PROTOCOL
from .test_runstore import SCHEMA_VERSION

REQUIRED_RUNNERS = ("pytest", "gradle", "dotnet", "go", "cargo", "maven", "node")
REQUIRED_STATUSES = (
    TestStatus.PASSED, TestStatus.FAILED, TestStatus.ERROR, TestStatus.SKIPPED,
    TestStatus.CANCELLED, TestStatus.XFAIL, TestStatus.XPASS,
)

@dataclass(frozen=True)
class SealCheck:
    name: str
    ok: bool
    detail: str


def verify_test_system(command_runner: Callable[..., object]) -> tuple[SealCheck, ...]:
    registry = TestRunnerRegistry()
    register_polyglot_test_runners(registry, command_runner)
    names = set(registry.names())
    checks = [
        SealCheck("protocol", PROTOCOL == "devtool.test/v1", PROTOCOL),
        SealCheck("history-schema", SCHEMA_VERSION >= 1, str(SCHEMA_VERSION)),
        SealCheck(
            "evidence-precedence",
            EVIDENCE_PRECEDENCE == {"exit": 0, "stdout": 1, "report": 2, "protocol": 3},
            repr(EVIDENCE_PRECEDENCE),
        ),
        SealCheck("runner-coverage", set(REQUIRED_RUNNERS) <= names, ",".join(sorted(names))),
        SealCheck(
            "rich-statuses",
            all(status in set(TestStatus) for status in REQUIRED_STATUSES),
            ",".join(status.value for status in REQUIRED_STATUSES),
        ),
    ]
    return tuple(checks)


def assert_test_system_sealed(command_runner: Callable[..., object]) -> tuple[SealCheck, ...]:
    checks = verify_test_system(command_runner)
    failed = [item for item in checks if not item.ok]
    if failed:
        detail = "; ".join(f"{item.name}: {item.detail}" for item in failed)
        raise RuntimeError(f"test-system release seal failed: {detail}")
    return checks
