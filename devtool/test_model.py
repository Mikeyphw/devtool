"""Canonical framework-independent test domain model."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Sequence
import json
import re

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#-]*$")

def _id(value: str, what: str) -> str:
    value = str(value).strip()
    if not value or not _ID.fullmatch(value):
        raise ValueError(f"invalid {what}: {value!r}")
    return value

def _frozen_map(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return MappingProxyType(dict(value or {}))

class TestStatus(str, Enum):
    PENDING="pending"; RUNNING="running"; PASSED="passed"; FAILED="failed"
    ERROR="error"; SKIPPED="skipped"; CANCELLED="cancelled"; XFAIL="xfail"; XPASS="xpass"

@dataclass(frozen=True)
class TestSource:
    kind: str
    value: str
    line: int | None = None
    def __post_init__(self):
        if self.line is not None and self.line < 1: raise ValueError("line must be >= 1")

@dataclass(frozen=True)
class TestFailure:
    message: str
    details: str | None = None
    source: TestSource | None = None

@dataclass(frozen=True)
class TestArtifact:
    kind: str
    path: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
    def __post_init__(self): object.__setattr__(self, "metadata", _frozen_map(self.metadata))

@dataclass(frozen=True)
class TestCaseResult:
    id: str
    name: str
    status: TestStatus
    duration_seconds: float | None = None
    failure: TestFailure | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    def __post_init__(self):
        object.__setattr__(self, "id", _id(self.id, "test case id"))
        if self.duration_seconds is not None and self.duration_seconds < 0: raise ValueError("duration must be >= 0")
        object.__setattr__(self, "metadata", _frozen_map(self.metadata))

@dataclass(frozen=True)
class TestSuiteResult:
    id: str
    name: str
    cases: tuple[TestCaseResult, ...] = ()
    duration_seconds: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    def __post_init__(self):
        object.__setattr__(self, "id", _id(self.id, "test suite id"))
        object.__setattr__(self, "cases", tuple(self.cases))
        if len({c.id for c in self.cases}) != len(self.cases): raise ValueError("duplicate test case id")
        if self.duration_seconds is not None and self.duration_seconds < 0: raise ValueError("duration must be >= 0")
        object.__setattr__(self, "metadata", _frozen_map(self.metadata))

@dataclass(frozen=True)
class TestSummary:
    passed:int=0; failed:int=0; skipped:int=0; errors:int=0; cancelled:int=0; xfailed:int=0; xpassed:int=0
    @property
    def total(self): return self.passed+self.failed+self.skipped+self.errors+self.cancelled+self.xfailed+self.xpassed
    @classmethod
    def from_suites(cls, suites):
        vals={k:0 for k in ("passed","failed","skipped","errors","cancelled","xfailed","xpassed")}
        m={TestStatus.PASSED:"passed",TestStatus.FAILED:"failed",TestStatus.SKIPPED:"skipped",TestStatus.ERROR:"errors",
           TestStatus.CANCELLED:"cancelled",TestStatus.XFAIL:"xfailed",TestStatus.XPASS:"xpassed"}
        for s in suites:
            for c in s.cases:
                if c.status in m: vals[m[c.status]] += 1
        return cls(**vals)

@dataclass(frozen=True)
class TestResult:
    id: str
    suites: tuple[TestSuiteResult, ...] = ()
    duration_seconds: float | None = None
    artifacts: tuple[TestArtifact, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = 1
    def __post_init__(self):
        object.__setattr__(self, "id", _id(self.id, "test result id"))
        object.__setattr__(self, "suites", tuple(self.suites)); object.__setattr__(self, "artifacts", tuple(self.artifacts))
        if len({s.id for s in self.suites}) != len(self.suites): raise ValueError("duplicate test suite id")
        if self.duration_seconds is not None and self.duration_seconds < 0: raise ValueError("duration must be >= 0")
        object.__setattr__(self, "metadata", _frozen_map(self.metadata))
    @property
    def summary(self): return TestSummary.from_suites(self.suites)
    @property
    def tests_passed(self): return self.summary.passed
    @property
    def tests_failed(self): return self.summary.failed + self.summary.errors + self.summary.cancelled + self.summary.xpassed
    @property
    def tests_skipped(self): return self.summary.skipped + self.summary.xfailed
    @property
    def tests_total(self): return self.summary.total
    def to_dict(self):
        def conv(x):
            if isinstance(x, Enum): return x.value
            if isinstance(x, Mapping): return {k:conv(v) for k,v in x.items()}
            if isinstance(x, tuple): return [conv(v) for v in x]
            if hasattr(x, "__dataclass_fields__"): return {k:conv(getattr(x,k)) for k in x.__dataclass_fields__}
            return x
        return conv(self)
    def to_json(self): return json.dumps(self.to_dict(), sort_keys=True)

@dataclass(frozen=True)
class TestSpec:
    id: str
    name: str
    runner: str
    command: tuple[str, ...] = ()
    cwd: str | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    timeout_seconds: float | None = None
    required: bool = True
    requires_device: bool = False
    requires_network: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)
    def __post_init__(self):
        object.__setattr__(self, "id", _id(self.id, "test spec id"))
        object.__setattr__(self, "runner", _id(self.runner, "test runner id"))
        object.__setattr__(self, "command", tuple(str(x) for x in self.command))
        if self.timeout_seconds is not None and self.timeout_seconds <= 0: raise ValueError("timeout must be > 0")
        object.__setattr__(self, "env", _frozen_map(self.env)); object.__setattr__(self, "metadata", _frozen_map(self.metadata))
