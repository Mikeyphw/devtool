"""Embedded artifact hook runtime.

All artifact-controlled scripts (preflight, validator, finalize) execute through
this package so integrity, payloads, capabilities, results, write boundaries,
and history metadata stay consistent.
"""
from .engine import EmbeddedHookEngine
from .models import HookExecutionRequest, HookExecutionContext, HookExecutionResult

__all__ = [
    "EmbeddedHookEngine",
    "HookExecutionRequest",
    "HookExecutionContext",
    "HookExecutionResult",
]
