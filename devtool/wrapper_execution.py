from __future__ import annotations

"""Canonical repository-wrapper execution bridge.

The public wrapper resolver stays pure.  This module owns the one permitted
transition from a resolved wrapper command back into canonical Devtool argv.
Keeping the bridge outside ``cli.py`` lets WRAP-09 probe the exact production
bridge with an in-memory callback instead of executing repository work.
"""

from dataclasses import dataclass
from typing import Callable, Sequence

from .wrapper_resolution import WrapperResolution, WrapperResolutionError
from .invocation_origin import trusted_invocation_context, current_trusted_invocation

_INTERNAL_FLAGS: tuple[str, ...] = ()


def wrapper_resolution_command_token(resolution: WrapperResolution) -> str:
    if resolution.devtool_command:
        return resolution.devtool_command
    if resolution.workflow_action:
        return "invoke"
    return ""


def build_wrapper_canonical_argv_with_provenance(
    resolution: WrapperResolution,
    *,
    outer_global_argv: Sequence[str] | None = None,
) -> list[str]:
    """Return the one canonical Devtool argv for a wrapper execution."""

    if resolution.mode != "execute":
        raise WrapperResolutionError(f"Wrapper resolution mode {resolution.mode!r} is not executable")
    command_token = wrapper_resolution_command_token(resolution)
    if not command_token or command_token == "wrapper":
        raise WrapperResolutionError("Wrapper resolution did not produce a safe canonical Devtool command")

    argv: list[str] = list(resolution.global_argv)
    if outer_global_argv:
        argv.extend(str(item) for item in outer_global_argv if str(item))
    argv.extend([command_token, *resolution.command_argv])
    if resolution.passthrough_argv:
        argv.extend(["--", *resolution.passthrough_argv])
    return argv


def execute_wrapper_resolution(
    resolution: WrapperResolution,
    *,
    reenter: Callable[[list[str]], int],
    outer_global_argv: Sequence[str] | None = None,
) -> int:
    """Re-enter canonical Devtool exactly once and return its exit code."""

    canonical = build_wrapper_canonical_argv_with_provenance(
        resolution,
        outer_global_argv=outer_global_argv,
    )
    with trusted_invocation_context(
        source="wrapper",
        command=resolution.command or resolution.command_spelling or wrapper_resolution_command_token(resolution),
        wrapper_schema=str(resolution.schema),
    ):
        return int(reenter(canonical))


@dataclass(frozen=True, slots=True)
class WrapperExecutionBridgeProbe:
    command_token: str
    reentry_count: int
    callback_exit_code: int
    invocation_source: str
    invocation_command: str
    wrapper_schema: str
    internal_control_counts: tuple[tuple[str, int], ...]
    nested_wrapper_dispatch: bool
    canonical_argv: tuple[str, ...]

    @property
    def controls_once(self) -> bool:
        return all(count == 1 for _, count in self.internal_control_counts)

    @property
    def ok(self) -> bool:
        return (
            self.reentry_count == 1
            and self.callback_exit_code == 73
            and self.invocation_source == "wrapper"
            and bool(self.invocation_command)
            and bool(self.wrapper_schema)
            and self.controls_once
            and not self.nested_wrapper_dispatch
            and bool(self.command_token)
        )


def probe_wrapper_execution_bridge(resolution: WrapperResolution) -> WrapperExecutionBridgeProbe:
    """Exercise the production bridge without executing a repository command."""

    calls: list[tuple[str, ...]] = []
    contexts: list[object] = []

    def probe_reentry(argv: list[str]) -> int:
        calls.append(tuple(argv))
        contexts.append(current_trusted_invocation())
        return 73

    callback_exit_code = execute_wrapper_resolution(resolution, reenter=probe_reentry)
    canonical = calls[0] if len(calls) == 1 else tuple(
        build_wrapper_canonical_argv_with_provenance(resolution)
    )
    command_token = wrapper_resolution_command_token(resolution)
    return WrapperExecutionBridgeProbe(
        command_token=command_token,
        reentry_count=len(calls),
        callback_exit_code=callback_exit_code,
        invocation_source=str(getattr(contexts[0], "source", "")) if contexts else "",
        invocation_command=str(getattr(contexts[0], "command", "")) if contexts else "",
        wrapper_schema=str(getattr(contexts[0], "wrapper_schema", "")) if contexts else "",
        internal_control_counts=(),
        nested_wrapper_dispatch=command_token == "wrapper",
        canonical_argv=canonical,
    )
