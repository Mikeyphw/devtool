from __future__ import annotations

"""EXEC-X05 universal Gradle ingress convergence.

This boundary converts representable Devtool-owned raw Gradle CommandSpecs into
ENV-X05 broker decisions before EXO/RER fingerprints are finalized. Typed Gradle
jobs already planned through the broker are left intact, so canonical Gradle work
is never planned twice.
"""

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Mapping, Sequence

from .gradle_planning import GradleInvocationRequest, GradleLauncherIdentity, plan_metadata
from .gradle_takeover import (
    GradleIntent,
    GradleOrigin,
    GradleTakeoverError,
    TakeoverMode,
    TakeoverScope,
    broker_gradle_invocation,
    destination_from_config,
    parse_shell_gradle_args,
    resolve_takeover_config,
    retag_takeover_metadata,
    takeover_metadata,
)
from .models import CommandSpec, ProjectConfig


_GRADLE_NAMES = {"gradle", "gradle.bat", "gradlew", "gradlew.bat"}
_SHELL_NAMES = {"sh", "bash", "zsh", "ksh", "dash"}
_SHELL_GRADLE_RE = re.compile(r"(?<![A-Za-z0-9_.-])(?:\./|[^\s;&|()]+/)?gradlew(?:\.bat)?\b|(?<![A-Za-z0-9_.-])gradle(?:\.bat)?\b")


def _digest(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _outer_origin(config: ProjectConfig) -> GradleOrigin | None:
    source = str(getattr(config, "invocation_source", "") or "").strip().lower()
    command = str(getattr(config, "invocation_command", "") or "").strip().lower()
    if source == "wrapper":
        return GradleOrigin.WRAPPER
    if source.startswith("federation"):
        return GradleOrigin.FEDERATION
    if command in {"invoke", "inv"}:
        return GradleOrigin.INVOKE
    return None


def _append_origin_chain(metadata: dict[str, object], *origins: GradleOrigin | str | None) -> None:
    chain = [str(item) for item in metadata.get("gradle_takeover_origin_chain", ()) or () if str(item)]
    for origin in origins:
        if origin is None:
            continue
        value = origin.value if isinstance(origin, GradleOrigin) else str(origin)
        if value and (not chain or chain[-1] != value):
            chain.append(value)
    if chain:
        metadata["gradle_takeover_origin_chain"] = chain


def _origin_for_spec(spec: CommandSpec, default: GradleOrigin | str = GradleOrigin.DEVTOOL) -> GradleOrigin:
    metadata = dict(spec.metadata or {})
    explicit = str(metadata.get("gradle_origin") or "").strip()
    if explicit:
        try:
            return GradleOrigin(explicit)
        except ValueError:
            pass
    execution = str(metadata.get("workflow_execution_environment") or metadata.get("execution_environment") or "").strip().lower()
    if execution == "ssh":
        return GradleOrigin.SSH
    if execution.startswith("federat"):
        return GradleOrigin.FEDERATION
    default_value = default if isinstance(default, GradleOrigin) else GradleOrigin(str(default))
    if default_value is not GradleOrigin.DEVTOOL:
        return default_value
    if metadata.get("artifact_validation_ref") or metadata.get("artifact_validation_test"):
        return GradleOrigin.ARTIFACT
    if metadata.get("validation_pipeline") or metadata.get("validation_phase_key") or metadata.get("validation_session_id"):
        return GradleOrigin.VALIDATION
    if metadata.get("canonical_test") or metadata.get("test_spec_id"):
        return GradleOrigin.TESTSPEC
    if metadata.get("workflow_graph"):
        return GradleOrigin.WORKFLOW
    if str(metadata.get("runner") or "").strip().lower() == "android":
        return GradleOrigin.ANDROID
    previous = str(metadata.get("gradle_takeover_origin") or "").strip()
    if previous:
        try:
            return GradleOrigin(previous)
        except ValueError:
            pass
    return default_value


def _intent_for_spec(spec: CommandSpec) -> GradleIntent:
    raw = str(spec.metadata.get("gradle_intent") or "work").strip().lower()
    try:
        return GradleIntent(raw)
    except ValueError:
        return GradleIntent.WORK


def _scoped_config(config: ProjectConfig, spec: CommandSpec) -> ProjectConfig:
    metadata = dict(spec.metadata or {})
    execution = str(metadata.get("workflow_execution_environment") or metadata.get("execution_environment") or "").strip()
    chroot = str(metadata.get("workflow_chroot_name") or metadata.get("chroot_name") or "").strip()
    device = str(metadata.get("workflow_device_serial") or metadata.get("device_serial") or "").strip()
    updates = {}
    if execution and execution != "auto":
        updates["execution_environment"] = execution
    if chroot:
        updates["chroot_name"] = chroot
    if device:
        updates["device_serial"] = device
    scoped_options = metadata.get("workflow_runner_options")
    if isinstance(scoped_options, Mapping):
        def merge(base: Mapping[str, object], extra: Mapping[str, object]) -> dict[str, object]:
            out = dict(base)
            for key, value in extra.items():
                if isinstance(value, Mapping) and isinstance(out.get(key), Mapping):
                    out[str(key)] = merge(dict(out[key]), value)
                else:
                    out[str(key)] = value
            return out
        updates["runner_options"] = merge(config.runner_options, scoped_options)
    return replace(config, **updates) if updates else config


def _direct_launcher(argv: Sequence[str]) -> tuple[str, tuple[str, ...]] | None:
    if not argv:
        return None
    first = str(argv[0])
    if Path(first).name.lower() not in _GRADLE_NAMES:
        return None
    return first, tuple(str(item) for item in argv[1:])


def _opaque_shell_text(spec: CommandSpec) -> str:
    metadata = dict(spec.metadata or {})
    text = str(metadata.get("shell_command") or "")
    if text and _SHELL_GRADLE_RE.search(text):
        return text
    argv = [str(item) for item in spec.argv]
    if argv and Path(argv[0]).name.lower() in _SHELL_NAMES:
        for index, token in enumerate(argv[:-1]):
            if token in {"-c", "-lc"} and _SHELL_GRADLE_RE.search(argv[index + 1]):
                return argv[index + 1]
    # Wrapper commands such as env/nice/timeout are intentionally not guessed
    # into a canonical argv. They are known Gradle escapes, but not safely
    # representable without owning the wrapper's semantics.
    if len(argv) > 1 and any(Path(token).name.lower() in _GRADLE_NAMES for token in argv[1:]):
        return " ".join(argv)
    return ""


def _requested_launcher(token: str) -> str:
    base = Path(token).name.lower()
    if base in {"gradlew", "gradlew.bat"}:
        return "wrapper"
    if base in {"gradle", "gradle.bat"} and ("/" not in token and "\\" not in token):
        return "system"
    return token


def _fallback_launcher(token: str, root: Path) -> GradleLauncherIdentity:
    source = "project-wrapper" if Path(token).name.lower() in {"gradlew", "gradlew.bat"} else "raw-devtool-ingress"
    return GradleLauncherIdentity.create(executable=token, source=source, project_root=root)


def _mark_identity(metadata: dict[str, object], *, semantic_identity: str, status: str) -> None:
    domains = dict(metadata.get("environment_domains") or {})
    domains["gradle_takeover"] = semantic_identity or _digest({"status": status})
    metadata["environment_domains"] = domains
    metadata["gradle_takeover_ingress_checked"] = True
    metadata["gradle_takeover_ingress_status"] = status


def converge_gradle_spec(
    config: ProjectConfig,
    spec: CommandSpec,
    *,
    default_origin: GradleOrigin | str = GradleOrigin.DEVTOOL,
) -> list[CommandSpec]:
    """Return zero-or-more canonical specs for one Devtool-owned ingress."""

    metadata = dict(spec.metadata or {})
    outer_origin = _outer_origin(config)
    effective_default = default_origin
    default_value = default_origin if isinstance(default_origin, GradleOrigin) else GradleOrigin(str(default_origin))
    if default_value is GradleOrigin.DEVTOOL and outer_origin is not None:
        effective_default = outer_origin
    origin = _origin_for_spec(spec, effective_default)
    if metadata.get("gradle_takeover_ingress_checked"):
        if isinstance(metadata.get("gradle_execution_plan"), Mapping):
            updated = retag_takeover_metadata(metadata, origin=origin)
            if updated:
                metadata.update(updated)
            _append_origin_chain(metadata, outer_origin, origin)
            _mark_identity(
                metadata,
                semantic_identity=str(metadata.get("gradle_takeover_semantic_identity") or ""),
                status=str(metadata.get("gradle_takeover_ingress_status") or "typed-brokered"),
            )
            spec.metadata = metadata
        return [spec]

    # Typed Gradle planners attach this before reaching CommandSpec ingress. If
    # broker metadata is present, this is already converged and must not plan a
    # second time. Legacy/pre-takeover plans remain executable but are marked so
    # strict qualification can detect them rather than silently double-plan.
    if isinstance(metadata.get("gradle_execution_plan"), Mapping):
        takeover = metadata.get("gradle_takeover")
        status = "typed-brokered" if isinstance(takeover, Mapping) else "typed-preplanned-unbrokered"
        updated = retag_takeover_metadata(metadata, origin=origin)
        if updated:
            metadata.update(updated)
        _append_origin_chain(metadata, outer_origin, origin)
        _mark_identity(
            metadata,
            semantic_identity=str(metadata.get("gradle_takeover_semantic_identity") or ""),
            status=status,
        )
        spec.metadata = metadata
        return [spec]

    direct = _direct_launcher(spec.argv)
    scoped = _scoped_config(config, spec)
    takeover_cfg = resolve_takeover_config(scoped)
    intent = _intent_for_spec(spec)

    if direct is None:
        opaque = _opaque_shell_text(spec)
        if not opaque:
            metadata["gradle_takeover_ingress_checked"] = True
            spec.metadata = metadata
            return [spec]
        scope_allows = takeover_cfg.scope in {TakeoverScope.DEVTOOL, TakeoverScope.BOTH}
        if takeover_cfg.enabled and scope_allows and takeover_cfg.mode is TakeoverMode.STRICT:
            raise GradleTakeoverError(
                f"Strict Gradle takeover cannot safely canonicalize opaque Devtool-owned command {spec.name!r}: {opaque}"
            )
        metadata["gradle_takeover_opaque_shell"] = True
        metadata["gradle_takeover_coverage"] = "unrepresentable-devtool-shell"
        metadata["gradle_takeover_origin"] = origin.value
        _append_origin_chain(metadata, outer_origin, origin)
        _mark_identity(metadata, semantic_identity="", status="opaque-unmanaged")
        spec.metadata = metadata
        return [spec]

    launcher_token, gradle_argv = direct
    roots, raw = parse_shell_gradle_args(gradle_argv)
    root = Path(spec.cwd or metadata.get("project_root") or scoped.project_root or scoped.repo_root).expanduser().resolve(strict=False)
    request = GradleInvocationRequest(
        caller=str(metadata.get("workflow_ref") or metadata.get("test_spec_id") or spec.name),
        action=str(metadata.get("workflow_action") or metadata.get("validation_phase_name") or spec.name),
        project_root=str(root),
        requested_roots=roots,
        validation=bool(metadata.get("validation_pipeline") or metadata.get("validation_phase_key")),
        validation_phase=str(metadata.get("validation_phase_name") or ""),
        raw_args=raw,
        selection_identity=str(metadata.get("test_selection_identity") or ""),
        expected_artifact_producers=tuple(str(x) for x in metadata.get("expected_artifact_producers", ()) or ()),
        expected_report_producers=tuple(str(x) for x in metadata.get("expected_report_producers", ()) or ()),
    )
    effective_env = dict(os.environ)
    effective_env.update(scoped.env)
    effective_env.update(spec.env)
    decision = broker_gradle_invocation(
        scoped,
        request,
        origin=origin,
        intent=intent,
        requested_launcher=_requested_launcher(launcher_token),
        destination=destination_from_config(scoped, env=effective_env),
        env=effective_env,
        require_plan=False,
        fallback_launcher=_fallback_launcher(launcher_token, root),
    )
    common = dict(metadata)
    common.update(takeover_metadata(decision))
    common["gradle_takeover_original_argv"] = [str(item) for item in spec.argv]
    common["gradle_takeover_original_cwd"] = str(spec.cwd or "")
    common["gradle_takeover_origin"] = origin.value
    common["gradle_intent"] = intent.value
    _append_origin_chain(common, outer_origin, origin)
    _mark_identity(common, semantic_identity=str(common.get("gradle_takeover_semantic_identity") or ""), status="raw-brokered")

    # Disabled/out-of-scope/observe modes preserve exact execution argv while
    # still carrying the broker receipt into identity/evidence.
    if decision.plan is None or decision.receipt.bypass_reason or decision.config.mode is TakeoverMode.OBSERVE:
        spec.metadata = common
        return [spec]

    common.update(plan_metadata(decision.plan))
    rendered: list[CommandSpec] = []
    total = len(decision.plan.invocations)
    for index, invocation in enumerate(decision.plan.invocations, start=1):
        item_metadata = dict(common)
        item_metadata.update({
            "progress_parser": str(item_metadata.get("progress_parser") or "gradle"),
            "gradle_direct": True,
            "gradle_tasks": list(invocation.roots),
            "requested_gradle_tasks": list(decision.plan.requested_roots),
            "gradle_invocation_index": index,
            "gradle_invocation_total": total,
            "gradle_invocation_barriers": [barrier.as_dict() for barrier in invocation.barriers],
        })
        rendered.append(CommandSpec(
            name=spec.name if total == 1 else f"{spec.name}-{index}",
            argv=[decision.launcher.executable, *invocation.roots, *invocation.args],
            env=dict(spec.env),
            cwd=spec.cwd,
            destructive=spec.destructive or any(barrier.kind == "clean" for barrier in invocation.barriers),
            metadata=item_metadata,
        ))
    return rendered


def converge_gradle_specs(
    config: ProjectConfig,
    specs: Sequence[CommandSpec],
    *,
    default_origin: GradleOrigin | str = GradleOrigin.DEVTOOL,
) -> list[CommandSpec]:
    out: list[CommandSpec] = []
    for spec in specs:
        previous = dict(spec.metadata or {})
        rendered = converge_gradle_spec(config, spec, default_origin=default_origin)
        if isinstance(previous.get("reuse_fingerprint"), Mapping):
            from .core.reuse import annotate_spec_reuse
            definition = str(previous.get("node_definition_id") or "")
            for item in rendered:
                annotate_spec_reuse(item, repo_root=config.repo_root, node_definition_id=definition)
        out.extend(rendered)
    return out
