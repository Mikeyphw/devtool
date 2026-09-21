"""Test execution core and runner registry."""
from __future__ import annotations

__test__ = False  # Prevent pytest from collecting production TSYS modules.
from dataclasses import dataclass, field
from typing import Protocol, Callable, Mapping, Any
import threading
from .test_model import TestSpec, TestResult

class TestExecutionError(RuntimeError): pass

@dataclass(frozen=True)
class TestExecutionContext:
    repo_root: str
    executor: str | None = None
    cancel_event: threading.Event | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

class TestRunner(Protocol):
    def run(self, spec: TestSpec, context: TestExecutionContext) -> TestResult: ...

class TestRunnerRegistry:
    def __init__(self): self._runners={}
    def register(self, name: str, runner: TestRunner, *, replace: bool=False):
        if name in self._runners and not replace: raise ValueError(f"runner already registered: {name}")
        self._runners[name]=runner
    def get(self, name: str):
        try: return self._runners[name]
        except KeyError: raise TestExecutionError(f"unknown test runner: {name}") from None
    def names(self): return tuple(sorted(self._runners))

class TestExecutor:
    def __init__(self, registry: TestRunnerRegistry): self.registry=registry
    def execute(self, spec: TestSpec, context: TestExecutionContext) -> TestResult:
        if context.cancel_event is not None and context.cancel_event.is_set():
            raise TestExecutionError(f"test execution cancelled before start: {spec.id}")
        runner=self.registry.get(spec.runner)
        result=runner.run(spec, context)
        if not isinstance(result, TestResult):
            raise TestExecutionError(f"runner {spec.runner!r} returned invalid result")
        if result.id != spec.id:
            raise TestExecutionError(f"runner result id {result.id!r} does not match spec {spec.id!r}")
        return result

class CommandTestRunner:
    """Bridge runner: delegates process ownership to an injected existing Devtool command runner."""
    def __init__(self, command_runner: Callable[..., Any], result_builder: Callable[[TestSpec, Any], TestResult]):
        self.command_runner=command_runner; self.result_builder=result_builder
    def run(self, spec: TestSpec, context: TestExecutionContext) -> TestResult:
        if not spec.command: raise TestExecutionError(f"test {spec.id!r} has no command")
        outcome=self.command_runner(
            list(spec.command), cwd=spec.cwd or context.repo_root, env=dict(spec.env),
            timeout=spec.timeout_seconds, executor=context.executor, cancel_event=context.cancel_event,
        )
        result=self.result_builder(spec, outcome)
        if not isinstance(result, TestResult): raise TestExecutionError("result builder returned invalid result")
        return result
