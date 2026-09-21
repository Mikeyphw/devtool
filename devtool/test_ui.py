"""Canonical test UI state shared by terminal/desktop/android/IPC projections."""
from __future__ import annotations
from dataclasses import dataclass, field
from .test_model import TestResult, TestStatus

@dataclass(frozen=True)
class TestCaseState:
    id: str; name: str; status: str; message: str = ""
@dataclass(frozen=True)
class TestSuiteState:
    id: str; name: str; cases: tuple[TestCaseState,...] = ()
@dataclass(frozen=True)
class TestProgressState:
    suites: tuple[TestSuiteState,...] = (); passed:int=0; failed:int=0; errors:int=0; skipped:int=0; cancelled:int=0; total:int=0
    @classmethod
    def from_result(cls, result: TestResult):
        suites=tuple(TestSuiteState(s.id,s.name,tuple(TestCaseState(c.id,c.name,c.status.value,c.failure.message if c.failure else "") for c in s.cases)) for s in result.suites)
        x=result.summary
        return cls(suites,x.passed,x.failed+x.xpassed,x.errors,x.skipped+x.xfailed,x.cancelled,x.total)
