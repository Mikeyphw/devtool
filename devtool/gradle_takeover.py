from __future__ import annotations

"""ENV-X05 Gradle takeover broker and real-launcher authority.

This module deliberately owns *environment/launcher/policy truth only*.  It
reuses :mod:`devtool.gradle_planning` for canonical Gradle planning.  EXEC-X05
will later route every Devtool-owned ingress through this broker.
"""

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys
from typing import Iterable, Mapping, Sequence

from .gradle_planning import GradleInvocationPlan, GradleInvocationRequest, GradleLauncherIdentity, plan_gradle_invocation
from .models import ProjectConfig
from .xdg import state_dir


class GradleTakeoverError(RuntimeError):
    pass


class TakeoverMode(str, Enum):
    OFF = "off"
    OBSERVE = "observe"
    MANAGE = "manage"
    STRICT = "strict"


class TakeoverScope(str, Enum):
    DEVTOOL = "devtool"
    SHELL = "shell"
    BOTH = "both"


class GradleIntent(str, Enum):
    WORK = "work"
    PROBE = "probe"
    CONTROL = "control"


class GradleOrigin(str, Enum):
    SHELL = "shell"
    DEVTOOL = "devtool"
    WRAPPER = "wrapper"
    INVOKE = "invoke"
    WORKFLOW = "workflow"
    VALIDATION = "validation"
    TESTSPEC = "testspec"
    ARTIFACT = "artifact"
    ANDROID = "android"
    SSH = "ssh"
    FEDERATION = "federation"
    REAL_ESCAPE = "real-escape"


def _digest(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _repo_key(root: Path | str) -> str:
    return hashlib.sha256(str(Path(root).expanduser().resolve(strict=False)).encode("utf-8")).hexdigest()[:20]


def _takeover_root() -> Path:
    return state_dir() / "gradle-takeover"


def _runtime_state_path(repo_root: Path | str) -> Path:
    return _takeover_root() / "repositories" / _repo_key(repo_root) / "state.json"


def shell_root() -> Path:
    return _takeover_root() / "shell"


def shell_bin_dir() -> Path:
    return shell_root() / "bin"


def shell_adapter_path() -> Path:
    return shell_root() / "adapter.sh"


def _capability_path() -> Path:
    return _takeover_root() / "internal-capability"


@dataclass(frozen=True, slots=True)
class GradleTakeoverConfig:
    enabled: bool = False
    mode: TakeoverMode = TakeoverMode.OBSERVE
    scope: TakeoverScope = TakeoverScope.DEVTOOL
    shell_adapter: bool = True
    requested_launcher: str = "auto"
    source: str = "default"
    runtime_override: bool = False
    identity_digest: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "mode": self.mode.value,
            "scope": self.scope.value,
            "shell_adapter": self.shell_adapter,
            "requested_launcher": self.requested_launcher,
            "source": self.source,
            "runtime_override": self.runtime_override,
            "identity_digest": self.identity_digest,
        }


@dataclass(frozen=True, slots=True)
class GradleDestination:
    kind: str
    identity: str
    local: bool = True
    path_value: str = ""
    cwd: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "identity": self.identity,
            "local": self.local,
            "path_present": bool(self.path_value),
            "cwd": self.cwd,
        }


@dataclass(frozen=True, slots=True)
class GradleLauncherResolution:
    requested: str
    executable: str
    source: str
    destination: GradleDestination
    launcher: GradleLauncherIdentity
    stripped_shim_dirs: tuple[str, ...] = ()
    deferred: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "requested": self.requested,
            "executable": self.executable,
            "source": self.source,
            "destination": self.destination.as_dict(),
            "launcher": self.launcher.as_dict(),
            "stripped_shim_dirs": list(self.stripped_shim_dirs),
            "deferred": self.deferred,
        }


@dataclass(frozen=True, slots=True)
class GradleTakeoverCoverage:
    enabled: bool
    devtool_owned: bool
    bare_gradle_shell: bool
    direct_project_wrapper_shell: bool
    arbitrary_nested_processes: bool
    explanation: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "devtool_owned": self.devtool_owned,
            "bare_gradle_shell": self.bare_gradle_shell,
            "direct_project_wrapper_shell": self.direct_project_wrapper_shell,
            "arbitrary_nested_processes": self.arbitrary_nested_processes,
            "explanation": list(self.explanation),
        }


@dataclass(frozen=True, slots=True)
class GradleTakeoverReceipt:
    receipt_id: str
    enabled: bool
    mode: str
    scope: str
    origin: str
    intent: str
    requested_launcher: str
    real_launcher: str
    launcher_identity: str
    destination_identity: str
    configuration_identity: str
    canonicalized: bool
    plan_digest: str = ""
    bypass_reason: str = ""
    coverage: Mapping[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "receipt_id": self.receipt_id,
            "enabled": self.enabled,
            "mode": self.mode,
            "scope": self.scope,
            "origin": self.origin,
            "intent": self.intent,
            "requested_launcher": self.requested_launcher,
            "real_launcher": self.real_launcher,
            "launcher_identity": self.launcher_identity,
            "destination_identity": self.destination_identity,
            "configuration_identity": self.configuration_identity,
            "canonicalized": self.canonicalized,
            "plan_digest": self.plan_digest,
            "bypass_reason": self.bypass_reason,
            "coverage": dict(self.coverage),
        }


@dataclass(frozen=True, slots=True)
class GradleTakeoverDecision:
    config: GradleTakeoverConfig
    launcher: GradleLauncherResolution
    receipt: GradleTakeoverReceipt
    plan: GradleInvocationPlan | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "config": self.config.as_dict(),
            "launcher": self.launcher.as_dict(),
            "receipt": self.receipt.as_dict(),
            "plan": self.plan.as_dict() if self.plan is not None else None,
        }


def _coerce_mode(value: object, *, enabled: bool) -> TakeoverMode:
    text = str(value or ("observe" if enabled else "off")).strip().lower()
    aliases = {"disabled": "off", "disabled-by-default": "off", "on": "manage"}
    text = aliases.get(text, text)
    try:
        return TakeoverMode(text)
    except ValueError as exc:
        raise GradleTakeoverError(f"Unsupported Gradle takeover mode: {text!r}") from exc


def _coerce_scope(value: object) -> TakeoverScope:
    text = str(value or "devtool").strip().lower()
    try:
        return TakeoverScope(text)
    except ValueError as exc:
        raise GradleTakeoverError(f"Unsupported Gradle takeover scope: {text!r}") from exc


def _read_runtime_state(repo_root: Path | str) -> dict[str, object]:
    path = _runtime_state_path(repo_root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return dict(data) if isinstance(data, dict) else {}


def _write_runtime_state(repo_root: Path | str, payload: Mapping[str, object]) -> Path:
    path = _runtime_state_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(dict(payload), sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.chmod(temp, stat.S_IRUSR | stat.S_IWUSR)
    os.replace(temp, path)
    return path


def resolve_takeover_config(config: ProjectConfig, *, include_runtime_state: bool = True) -> GradleTakeoverConfig:
    gradle = config.runner_options.get("gradle", {}) if isinstance(config.runner_options, dict) else {}
    takeover = gradle.get("takeover", {}) if isinstance(gradle, dict) else {}
    if not isinstance(takeover, dict):
        raise GradleTakeoverError("[gradle.takeover] must be a TOML table")
    configured_enabled = bool(takeover.get("enabled", False))
    payload: dict[str, object] = {
        "enabled": configured_enabled,
        "mode": takeover.get("mode", "observe" if configured_enabled else "off"),
        "scope": takeover.get("scope", "devtool"),
        "shell_adapter": bool(takeover.get("shell_adapter", True)),
        "requested_launcher": str(takeover.get("launcher", "auto") or "auto"),
        "source": "repository-or-global-config" if takeover else "default",
        "runtime_override": False,
    }
    runtime = _read_runtime_state(config.repo_root) if include_runtime_state else {}
    if runtime:
        for key in ("enabled", "mode", "scope", "shell_adapter", "requested_launcher"):
            if key in runtime:
                payload[key] = runtime[key]
        payload["source"] = "runtime-state"
        payload["runtime_override"] = True
    enabled = bool(payload["enabled"])
    mode = _coerce_mode(payload.get("mode"), enabled=enabled)
    if not enabled:
        mode = TakeoverMode.OFF
    elif mode is TakeoverMode.OFF:
        enabled = False
    scope = _coerce_scope(payload.get("scope"))
    identity_payload = {
        "enabled": enabled,
        "mode": mode.value,
        "scope": scope.value,
        "shell_adapter": bool(payload.get("shell_adapter", True)),
        "requested_launcher": str(payload.get("requested_launcher", "auto") or "auto"),
        "runtime_override": bool(payload.get("runtime_override", False)),
    }
    return GradleTakeoverConfig(
        enabled=enabled,
        mode=mode,
        scope=scope,
        shell_adapter=bool(payload.get("shell_adapter", True)),
        requested_launcher=str(payload.get("requested_launcher", "auto") or "auto"),
        source=str(payload.get("source", "default")),
        runtime_override=bool(payload.get("runtime_override", False)),
        identity_digest=_digest(identity_payload),
    )


def destination_from_config(config: ProjectConfig, *, env: Mapping[str, str] | None = None) -> GradleDestination:
    values = dict(os.environ if env is None else env)
    kind = str(config.execution_environment or "auto").strip().lower().replace("_", "-") or "auto"
    remote = kind in {"ssh", "external-device", "chroot", "proot-distro"} or kind.startswith("federat")
    if kind == "auto":
        kind = "native-termux" if values.get("PREFIX", "").startswith("/data/data/com.termux/") else "host"
    destination_key = {
        "kind": kind,
        "repo": str(config.repo_root),
        "target": str(config.selected_target or ""),
        "ssh_host": str((config.runner_options.get("ssh", {}) or {}).get("host", "")) if isinstance(config.runner_options.get("ssh", {}), dict) else "",
        "chroot": str(config.chroot_name or ""),
    }
    return GradleDestination(
        kind=kind,
        identity=_digest(destination_key),
        local=not remote,
        path_value=str(values.get("PATH", "")),
        cwd=str(config.project_root or config.repo_root),
    )


def _is_takeover_shim_dir(path: Path) -> bool:
    try:
        return (path / ".devtool-gradle-takeover-shim").is_file()
    except OSError:
        return False


def strip_takeover_shims(path_value: str, *, extra_dirs: Iterable[Path | str] = ()) -> tuple[str, tuple[str, ...]]:
    explicit = {str(Path(item).expanduser().resolve(strict=False)) for item in extra_dirs if str(item).strip()}
    kept: list[str] = []
    stripped: list[str] = []
    for raw in str(path_value or "").split(os.pathsep):
        if not raw:
            continue
        resolved = str(Path(raw).expanduser().resolve(strict=False))
        if resolved in explicit or _is_takeover_shim_dir(Path(raw).expanduser()):
            stripped.append(resolved)
            continue
        kept.append(raw)
    return os.pathsep.join(kept), tuple(dict.fromkeys(stripped))


def _wrapper_metadata(project_root: Path) -> tuple[str, str]:
    props = project_root / "gradle" / "wrapper" / "gradle-wrapper.properties"
    distribution = ""
    if props.is_file():
        try:
            for line in props.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.strip().startswith("distributionUrl="):
                    distribution = line.split("=", 1)[1].strip()
                    break
        except OSError:
            distribution = ""
    match = re.search(r"gradle-([0-9]+(?:\.[0-9]+){1,3})-(?:bin|all)\.", distribution)
    return distribution, (match.group(1) if match else "")


def _qualify_launcher_destination(base: GradleLauncherIdentity, destination: GradleDestination) -> GradleLauncherIdentity:
    payload = base.as_dict()
    payload["destination_identity"] = destination.identity
    values = {name: getattr(base, name) for name in base.__dataclass_fields__}
    values["identity"] = _digest(payload)
    return GradleLauncherIdentity(**values)


def _logical_launcher_identity(*, executable: str, source: str, destination: GradleDestination, version: str = "", distribution: str = "") -> GradleLauncherIdentity:
    payload = {
        "executable": executable,
        "source": source,
        "required_version": version,
        "effective_version": version,
        "wrapper_distribution": distribution,
        "destination_identity": destination.identity,
    }
    return GradleLauncherIdentity(
        executable=executable,
        source=source,
        required_version=version,
        effective_version=version,
        wrapper_distribution=distribution,
        identity=_digest(payload),
    )


def resolve_real_launcher(
    config: ProjectConfig,
    *,
    requested: str = "auto",
    destination: GradleDestination | None = None,
    env: Mapping[str, str] | None = None,
    project_root: Path | str | None = None,
) -> GradleLauncherResolution:
    destination = destination or destination_from_config(config, env=env)
    root = Path(project_root or config.project_root or config.repo_root).expanduser().resolve(strict=False)
    requested_text = str(requested or "auto").strip() or "auto"
    wrapper = root / "gradlew"
    wants_wrapper = requested_text in {"wrapper", "gradlew", "./gradlew"} or (requested_text == "auto" and destination.local and wrapper.is_file())
    if wants_wrapper:
        distribution, version = _wrapper_metadata(root) if destination.local else ("", "")
        if destination.local and wrapper.is_file():
            launcher = _qualify_launcher_destination(GradleLauncherIdentity.create(
                executable=str(wrapper), source="project-wrapper", required_version=version,
                effective_version=version, wrapper_distribution=distribution, project_root=root,
            ), destination)
            return GradleLauncherResolution(requested_text, str(wrapper), "project-wrapper", destination, launcher)
        launcher = _logical_launcher_identity(
            executable="./gradlew", source=f"project-wrapper:{destination.kind}", destination=destination,
            version=version, distribution=distribution,
        )
        return GradleLauncherResolution(requested_text, "./gradlew", launcher.source, destination, launcher, deferred=True)

    if requested_text not in {"auto", "system", "gradle"} and ("/" in requested_text or "\\" in requested_text):
        candidate = Path(requested_text).expanduser()
        if destination.local:
            resolved = candidate.resolve(strict=False)
            if resolved.parent == shell_bin_dir().resolve(strict=False) or _is_takeover_shim_dir(resolved.parent):
                raise GradleTakeoverError(f"Requested Gradle launcher resolves to the Devtool takeover shim: {resolved}")
            if not resolved.exists():
                raise GradleTakeoverError(f"Requested Gradle launcher does not exist: {resolved}")
            launcher = _qualify_launcher_destination(
                GradleLauncherIdentity.create(executable=str(resolved), source="explicit-system", project_root=root), destination
            )
            return GradleLauncherResolution(requested_text, str(resolved), "explicit-system", destination, launcher)
        launcher = _logical_launcher_identity(executable=requested_text, source=f"explicit-system:{destination.kind}", destination=destination)
        return GradleLauncherResolution(requested_text, requested_text, launcher.source, destination, launcher, deferred=True)

    if not destination.local:
        launcher = _logical_launcher_identity(executable="gradle", source=f"system:{destination.kind}", destination=destination)
        return GradleLauncherResolution(requested_text, "gradle", launcher.source, destination, launcher, deferred=True)

    path_value, stripped = strip_takeover_shims(destination.path_value, extra_dirs=(shell_bin_dir(),))
    for entry in path_value.split(os.pathsep):
        if not entry:
            continue
        candidate = Path(entry).expanduser() / "gradle"
        try:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                resolved = candidate.resolve(strict=False)
                if _is_takeover_shim_dir(resolved.parent) or resolved.parent == shell_bin_dir().resolve(strict=False):
                    continue
                launcher = _qualify_launcher_destination(
                    GradleLauncherIdentity.create(executable=str(resolved), source="system", project_root=root), destination
                )
                return GradleLauncherResolution(requested_text, str(resolved), "system", destination, launcher, stripped)
        except OSError:
            continue
    raise GradleTakeoverError("No real Gradle launcher found after removing Devtool takeover shims from PATH")


def takeover_coverage(config: GradleTakeoverConfig) -> GradleTakeoverCoverage:
    shell_claimed = config.enabled and config.scope in {TakeoverScope.SHELL, TakeoverScope.BOTH}
    devtool_claimed = config.enabled and config.scope in {TakeoverScope.DEVTOOL, TakeoverScope.BOTH}
    reasons = [
        "bare `gradle` is intercepted only when the generated shim directory is active in PATH",
        "direct `./gradlew` is not claimed by the shell adapter because a slash-qualified executable bypasses PATH lookup",
        "arbitrary Gradle started by nested scripts is not claimed as intercepted",
    ]
    return GradleTakeoverCoverage(
        enabled=config.enabled,
        devtool_owned=devtool_claimed,
        bare_gradle_shell=shell_claimed,
        direct_project_wrapper_shell=False,
        arbitrary_nested_processes=False,
        explanation=tuple(reasons),
    )


def _receipt(
    *, cfg: GradleTakeoverConfig, origin: GradleOrigin, intent: GradleIntent,
    launcher: GradleLauncherResolution, canonicalized: bool, plan_digest: str = "", bypass_reason: str = "",
) -> GradleTakeoverReceipt:
    coverage = takeover_coverage(cfg).as_dict()
    payload = {
        "enabled": cfg.enabled, "mode": cfg.mode.value, "scope": cfg.scope.value,
        "origin": origin.value, "intent": intent.value,
        "requested_launcher": launcher.requested, "real_launcher": launcher.executable,
        "launcher_identity": launcher.launcher.identity,
        "destination_identity": launcher.destination.identity,
        "configuration_identity": cfg.identity_digest,
        "canonicalized": canonicalized, "plan_digest": plan_digest,
        "bypass_reason": bypass_reason, "coverage": coverage,
    }
    return GradleTakeoverReceipt(receipt_id=_digest(payload), **payload)


def broker_gradle_invocation(
    config: ProjectConfig,
    request: GradleInvocationRequest,
    *,
    origin: GradleOrigin | str = GradleOrigin.DEVTOOL,
    intent: GradleIntent | str = GradleIntent.WORK,
    requested_launcher: str = "auto",
    destination: GradleDestination | None = None,
    env: Mapping[str, str] | None = None,
    require_plan: bool = False,
    fallback_launcher: GradleLauncherIdentity | None = None,
    planner_kwargs: Mapping[str, object] | None = None,
) -> GradleTakeoverDecision:
    """Broker one Gradle request without forcing callers to plan twice.

    ``require_plan`` is the EXEC-X05 bridge for already-typed Devtool work. When
    takeover is disabled or out of scope, the canonical planner still runs once
    with the caller's existing launcher identity. When takeover applies, that
    same single planning pass uses the broker-resolved real launcher instead.
    Probe/control intents never become work merely because a plan was requested.
    """
    cfg = resolve_takeover_config(config)
    origin_value = origin if isinstance(origin, GradleOrigin) else GradleOrigin(str(origin))
    intent_value = intent if isinstance(intent, GradleIntent) else GradleIntent(str(intent))
    configured_launcher = str(cfg.requested_launcher or "auto")
    requested = str(requested_launcher or "auto")
    if requested == "auto" and configured_launcher not in {"", "auto"}:
        requested = configured_launcher
    shell_origin = origin_value is GradleOrigin.SHELL
    scope_allows = (
        cfg.scope is TakeoverScope.BOTH
        or (shell_origin and cfg.scope is TakeoverScope.SHELL)
        or ((not shell_origin) and cfg.scope is TakeoverScope.DEVTOOL)
    )
    destination_value = destination or destination_from_config(config, env=env)
    preserve_fallback = bool(
        fallback_launcher is not None
        and (not cfg.enabled or not scope_allows or (require_plan and cfg.mode is TakeoverMode.OBSERVE))
    )
    if preserve_fallback:
        launcher = GradleLauncherResolution(
            requested=requested, executable=fallback_launcher.executable, source=fallback_launcher.source,
            destination=destination_value, launcher=fallback_launcher, deferred=not destination_value.local,
        )
    else:
        launcher = resolve_real_launcher(
            config, requested=requested, destination=destination_value, env=env, project_root=request.project_root,
        )
    bypass_reason = ""
    if not cfg.enabled:
        bypass_reason = "takeover-disabled"
    elif not scope_allows:
        bypass_reason = "origin-outside-takeover-scope"
    elif intent_value in {GradleIntent.PROBE, GradleIntent.CONTROL}:
        bypass_reason = f"typed-{intent_value.value}-bypass"
    elif cfg.mode is TakeoverMode.OBSERVE:
        bypass_reason = "observe-only"

    plan = None
    kwargs = dict(planner_kwargs or {})
    if intent_value is GradleIntent.WORK and (not bypass_reason or require_plan):
        selected_launcher = launcher.launcher if not bypass_reason else (fallback_launcher or launcher.launcher)
        plan = plan_gradle_invocation(config=config, request=request, launcher=selected_launcher, **kwargs)

    canonicalized = bool(plan is not None and not bypass_reason)
    receipt = _receipt(
        cfg=cfg, origin=origin_value, intent=intent_value, launcher=launcher,
        canonicalized=canonicalized, plan_digest=(plan.plan_digest if plan is not None else ""),
        bypass_reason=bypass_reason,
    )
    return GradleTakeoverDecision(cfg, launcher, receipt, plan)


def takeover_semantic_identity(
    receipt: GradleTakeoverReceipt | Mapping[str, object],
    plan: GradleInvocationPlan | Mapping[str, object] | None = None,
) -> str:
    """Return origin-neutral identity for equivalent Gradle work.

    The canonical plan digest intentionally contains caller/evidence fields, so
    it is not itself suitable for cross-surface RER convergence. This identity
    keeps physical Gradle semantics, launcher/destination and effective policy
    while excluding wrapper/invoke/workflow provenance.
    """
    payload = receipt.as_dict() if isinstance(receipt, GradleTakeoverReceipt) else dict(receipt)
    if isinstance(plan, GradleInvocationPlan):
        plan_data: Mapping[str, object] = plan.as_dict()
    elif isinstance(plan, Mapping):
        plan_data = plan
    else:
        plan_data = {}
    context = dict(plan_data.get("context") or {}) if isinstance(plan_data, Mapping) else {}
    policy = dict(plan_data.get("policy") or {}) if isinstance(plan_data, Mapping) else {}
    physical = {
        "requested_roots": list(plan_data.get("requested_roots") or ()),
        "optimized_roots": list(plan_data.get("optimized_roots") or ()),
        "arguments": dict(plan_data.get("arguments") or {}),
        "semantic_request_digest": str(plan_data.get("semantic_request_digest") or ""),
        "invocations": list(plan_data.get("invocations") or ()),
        "context_digest": str(context.get("context_digest") or ""),
        "policy_digest": str(policy.get("policy_digest") or ""),
    } if plan_data else {"plan_digest": str(payload.get("plan_digest") or "")}
    return _digest({
        "intent": str(payload.get("intent") or "work"),
        "real_launcher": str(payload.get("real_launcher") or ""),
        "launcher_identity": str(payload.get("launcher_identity") or ""),
        "destination_identity": str(payload.get("destination_identity") or ""),
        "configuration_identity": str(payload.get("configuration_identity") or ""),
        "bypass_reason": str(payload.get("bypass_reason") or ""),
        "physical": physical,
    })


def takeover_metadata(decision: GradleTakeoverDecision) -> dict[str, object]:
    """Identity/evidence projection for RUN/EXO/RER CommandSpec metadata."""
    receipt = decision.receipt.as_dict()
    return {
        "gradle_takeover": {
            "config": decision.config.as_dict(),
            "launcher": decision.launcher.as_dict(),
            "receipt": receipt,
        },
        "gradle_takeover_receipt_id": decision.receipt.receipt_id,
        "gradle_takeover_semantic_identity": takeover_semantic_identity(decision.receipt, decision.plan),
        "gradle_takeover_origin": decision.receipt.origin,
        "gradle_takeover_destination_identity": decision.receipt.destination_identity,
        "gradle_takeover_launcher_identity": decision.receipt.launcher_identity,
    }


def retag_takeover_metadata(
    metadata: Mapping[str, object],
    *,
    origin: GradleOrigin | str,
) -> dict[str, object]:
    """Retag evidence origin without re-planning already canonical Gradle work.

    Nested Devtool surfaces (typed runner -> TestSpec -> workflow -> SSH) may
    discover a more precise ingress origin after the canonical Gradle plan was
    produced. Rebuild only the evidence receipt and keep the origin-neutral
    semantic identity stable.
    """
    takeover = metadata.get("gradle_takeover")
    if not isinstance(takeover, Mapping):
        return {}
    nested = dict(takeover)
    receipt_raw = nested.get("receipt")
    if not isinstance(receipt_raw, Mapping):
        return {}
    receipt = dict(receipt_raw)
    origin_value = origin if isinstance(origin, GradleOrigin) else GradleOrigin(str(origin))
    existing = str(receipt.get("origin") or "")
    if existing == origin_value.value:
        return {}
    payload = {key: value for key, value in receipt.items() if key != "receipt_id"}
    payload["origin"] = origin_value.value
    receipt_id = _digest(payload)
    receipt["origin"] = origin_value.value
    receipt["receipt_id"] = receipt_id
    nested["receipt"] = receipt
    chain = [str(item) for item in metadata.get("gradle_takeover_origin_chain", ()) or () if str(item)]
    if not chain:
        previous = str(metadata.get("gradle_takeover_origin") or existing)
        if previous:
            chain.append(previous)
    if not chain or chain[-1] != origin_value.value:
        chain.append(origin_value.value)
    return {
        "gradle_takeover": nested,
        "gradle_takeover_receipt_id": receipt_id,
        "gradle_takeover_semantic_identity": takeover_semantic_identity(receipt, metadata.get("gradle_execution_plan") if isinstance(metadata, Mapping) else None),
        "gradle_takeover_origin": origin_value.value,
        "gradle_takeover_origin_chain": chain,
        "gradle_takeover_destination_identity": str(receipt.get("destination_identity") or ""),
        "gradle_takeover_launcher_identity": str(receipt.get("launcher_identity") or ""),
    }


def takeover_metadata_for_plan(
    config: ProjectConfig,
    plan: GradleInvocationPlan,
    *,
    origin: GradleOrigin | str = GradleOrigin.DEVTOOL,
    requested_launcher: str = "auto",
    destination: GradleDestination | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Project a broker receipt for a plan already produced through this broker.

    This is evidence-only: it never calls the Gradle planner and therefore does
    not re-plan typed Android/JVM work.
    """
    cfg = resolve_takeover_config(config)
    origin_value = origin if isinstance(origin, GradleOrigin) else GradleOrigin(str(origin))
    dest = destination or destination_from_config(config, env=env)
    requested = str(requested_launcher or "auto")
    if requested == "auto" and str(cfg.requested_launcher or "auto") not in {"", "auto"}:
        requested = str(cfg.requested_launcher)
    launcher = GradleLauncherResolution(
        requested=requested,
        executable=plan.context.launcher.executable,
        source=plan.context.launcher.source,
        destination=dest,
        launcher=plan.context.launcher,
        deferred=not dest.local,
    )
    shell_origin = origin_value is GradleOrigin.SHELL
    scope_allows = (
        cfg.scope is TakeoverScope.BOTH
        or (shell_origin and cfg.scope is TakeoverScope.SHELL)
        or ((not shell_origin) and cfg.scope is TakeoverScope.DEVTOOL)
    )
    bypass_reason = "" if cfg.enabled and scope_allows else (
        "takeover-disabled" if not cfg.enabled else "origin-outside-takeover-scope"
    )
    receipt = _receipt(
        cfg=cfg, origin=origin_value, intent=GradleIntent.WORK, launcher=launcher,
        canonicalized=not bool(bypass_reason), plan_digest=plan.plan_digest, bypass_reason=bypass_reason,
    )
    return takeover_metadata(GradleTakeoverDecision(cfg, launcher, receipt, plan))


def _ensure_capability() -> str:
    path = _capability_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        try:
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value
        except OSError:
            pass
    value = secrets.token_urlsafe(32)
    temp = path.with_suffix(".tmp")
    temp.write_text(value + "\n", encoding="utf-8")
    os.chmod(temp, stat.S_IRUSR | stat.S_IWUSR)
    os.replace(temp, path)
    return value


def validate_internal_capability(value: str) -> bool:
    if not value:
        return False
    try:
        expected = _capability_path().read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return secrets.compare_digest(value, expected)


def write_shell_assets() -> dict[str, str]:
    root = shell_root()
    bindir = shell_bin_dir()
    bindir.mkdir(parents=True, exist_ok=True)
    (bindir / ".devtool-gradle-takeover-shim").write_text("env-x05\n", encoding="utf-8")
    shim = bindir / "gradle"
    shim.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        "exec \"${DEVTOOL_GRADLE_TAKEOVER_DEVTOOL:-devtool}\" gradle takeover intercept --origin shell --intent work --requested-launcher gradle -- \"$@\"\n",
        encoding="utf-8",
    )
    os.chmod(shim, 0o755)
    adapter = shell_adapter_path()
    adapter.write_text(
        "# Generated by Devtool ENV-X05. Compatible with POSIX sh/bash/zsh.\n"
        f"DEVTOOL_GRADLE_TAKEOVER_SHIM_DIR={str(bindir)!r}\n"
        "case \"${PATH:-}\" in\n"
        "  \"$DEVTOOL_GRADLE_TAKEOVER_SHIM_DIR\"|\"$DEVTOOL_GRADLE_TAKEOVER_SHIM_DIR\":*) ;;\n"
        "  *) export PATH=\"$DEVTOOL_GRADLE_TAKEOVER_SHIM_DIR${PATH:+:$PATH}\" ;;\n"
        "esac\n"
        "export DEVTOOL_GRADLE_TAKEOVER_SHIM_DIR\n",
        encoding="utf-8",
    )
    os.chmod(adapter, 0o600)
    _ensure_capability()
    return {"shim": str(shim), "adapter": str(adapter), "bin": str(bindir)}


def enable_takeover(config: ProjectConfig, *, mode: str = "manage", scope: str = "both") -> dict[str, object]:
    mode_value = _coerce_mode(mode, enabled=True)
    if mode_value is TakeoverMode.OFF:
        raise GradleTakeoverError("enable requires observe, manage, or strict mode")
    scope_value = _coerce_scope(scope)
    state = {
        "enabled": True,
        "mode": mode_value.value,
        "scope": scope_value.value,
        "shell_adapter": True,
        "requested_launcher": "auto",
        "schema": 1,
    }
    path = _write_runtime_state(config.repo_root, state)
    assets = write_shell_assets() if scope_value in {TakeoverScope.SHELL, TakeoverScope.BOTH} else {}
    return {"state_path": str(path), "state": state, "shell": assets}


def _other_shell_takeover_enabled(repo_root: Path | str) -> bool:
    current = _runtime_state_path(repo_root).resolve(strict=False)
    repositories = _takeover_root() / "repositories"
    if not repositories.is_dir():
        return False
    for state_path in repositories.glob("*/state.json"):
        if state_path.resolve(strict=False) == current:
            continue
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if not isinstance(payload, dict) or not bool(payload.get("enabled", False)):
            continue
        if str(payload.get("scope", "devtool")).strip().lower() in {"shell", "both"}:
            return True
    return False


def disable_takeover(config: ProjectConfig) -> dict[str, object]:
    state = {"enabled": False, "mode": "off", "scope": "devtool", "shell_adapter": False, "schema": 1}
    path = _write_runtime_state(config.repo_root, state)
    removed: list[str] = []
    retained_for_other_repositories = _other_shell_takeover_enabled(config.repo_root)
    if not retained_for_other_repositories:
        for candidate in (shell_bin_dir() / "gradle", shell_bin_dir() / ".devtool-gradle-takeover-shim", shell_adapter_path()):
            try:
                candidate.unlink()
                removed.append(str(candidate))
            except FileNotFoundError:
                pass
    return {
        "state_path": str(path), "state": state, "removed": removed,
        "shell_assets_retained_for_other_repositories": retained_for_other_repositories,
    }


def takeover_status(config: ProjectConfig, *, env: Mapping[str, str] | None = None) -> dict[str, object]:
    cfg = resolve_takeover_config(config)
    coverage = takeover_coverage(cfg)
    destination = destination_from_config(config, env=env)
    launcher: dict[str, object]
    try:
        launcher = resolve_real_launcher(config, requested=cfg.requested_launcher, destination=destination, env=env).as_dict()
    except GradleTakeoverError as exc:
        launcher = {"error": str(exc)}
    path_entries = str((os.environ if env is None else env).get("PATH", "")).split(os.pathsep)
    shim_active = str(shell_bin_dir()) in path_entries
    return {
        "config": cfg.as_dict(),
        "coverage": coverage.as_dict(),
        "destination": destination.as_dict(),
        "real_launcher": launcher,
        "shell": {
            "shim_dir": str(shell_bin_dir()),
            "shim_installed": (shell_bin_dir() / "gradle").is_file(),
            "adapter": str(shell_adapter_path()),
            "adapter_installed": shell_adapter_path().is_file(),
            "shim_active_in_path": shim_active,
            "activation_command": f". {shell_adapter_path()}",
        },
    }


def takeover_doctor(config: ProjectConfig, *, env: Mapping[str, str] | None = None) -> dict[str, object]:
    status = takeover_status(config, env=env)
    issues: list[str] = []
    cfg = status["config"]
    shell = status["shell"]
    if bool(cfg.get("enabled")) and cfg.get("scope") in {"shell", "both"}:
        if not shell.get("shim_installed"):
            issues.append("shell takeover is enabled but the Gradle shim is not installed")
        elif not shell.get("shim_active_in_path"):
            issues.append("shell shim is installed but not active in PATH; source the generated adapter")
    if isinstance(status.get("real_launcher"), dict) and status["real_launcher"].get("error"):
        issues.append(str(status["real_launcher"]["error"]))
    return {**status, "ok": not issues, "issues": issues}


def takeover_explain(config: ProjectConfig, *, env: Mapping[str, str] | None = None) -> dict[str, object]:
    status = takeover_status(config, env=env)
    cfg = status["config"]
    coverage = status["coverage"]
    return {
        **status,
        "summary": (
            "Gradle takeover is disabled; existing Gradle behavior is preserved."
            if not cfg.get("enabled")
            else f"Gradle takeover is {cfg.get('mode')} for {cfg.get('scope')} scope."
        ),
        "truth": {
            "planner_authority": "devtool.gradle_planning.plan_gradle_invocation",
            "bare_gradle_intercepted": bool(coverage.get("bare_gradle_shell")),
            "direct_dot_gradlew_intercepted": bool(coverage.get("direct_project_wrapper_shell")),
            "nested_script_intercepted": bool(coverage.get("arbitrary_nested_processes")),
            "note": "ENV-X05 owns launcher/policy truth; EXEC-X05 owns universal Devtool ingress convergence.",
        },
    }


def parse_shell_gradle_args(argv: Sequence[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    roots: list[str] = []
    raw: list[str] = []
    takes_value = {"-p", "--project-dir", "-g", "--gradle-user-home", "-I", "--init-script", "-c", "--settings-file", "--max-workers"}
    index = 0
    values = list(argv)
    while index < len(values):
        arg = values[index]
        if arg in takes_value:
            raw.append(arg)
            if index + 1 < len(values):
                raw.append(values[index + 1])
                index += 2
                continue
        elif arg.startswith("-"):
            raw.append(arg)
        else:
            roots.append(arg)
        index += 1
    return tuple(roots), tuple(raw)


def execute_intercepted_gradle(
    config: ProjectConfig,
    argv: Sequence[str],
    *,
    origin: str = "shell",
    intent: str = "work",
    requested_launcher: str = "gradle",
    env: Mapping[str, str] | None = None,
) -> int:
    values = dict(os.environ if env is None else env)
    capability = str(values.get("DEVTOOL_GRADLE_TAKEOVER_CAPABILITY", "") or "")
    if capability:
        if not validate_internal_capability(capability):
            raise GradleTakeoverError("Invalid protected Gradle takeover bypass capability")
        return execute_real_gradle(config, argv, requested_launcher=requested_launcher, env=values)
    roots, raw = parse_shell_gradle_args(argv)
    request = GradleInvocationRequest(
        caller=f"takeover:{origin}", action="takeover", project_root=str(config.project_root or config.repo_root),
        requested_roots=roots, raw_args=raw,
    )
    decision = broker_gradle_invocation(
        config, request, origin=GradleOrigin(origin), intent=GradleIntent(intent),
        requested_launcher=requested_launcher, env=env,
    )
    process_env = dict(values)
    process_env["DEVTOOL_GRADLE_TAKEOVER_CAPABILITY"] = _ensure_capability()
    if decision.plan is None or decision.config.mode is TakeoverMode.OBSERVE:
        cmd = [decision.launcher.executable, *argv]
        return subprocess.run(cmd, cwd=request.project_root, env=process_env, check=False).returncode
    for invocation in decision.plan.invocations:
        cmd = [decision.launcher.executable, *invocation.roots, *invocation.args]
        rc = subprocess.run(cmd, cwd=request.project_root, env=process_env, check=False).returncode
        if rc:
            return int(rc)
    return 0


def execute_real_gradle(config: ProjectConfig, argv: Sequence[str], *, requested_launcher: str = "auto", env: Mapping[str, str] | None = None) -> int:
    resolution = resolve_real_launcher(config, requested=requested_launcher, env=env)
    process_env = dict(os.environ if env is None else env)
    process_env["DEVTOOL_GRADLE_TAKEOVER_CAPABILITY"] = _ensure_capability()
    return subprocess.run([resolution.executable, *argv], cwd=str(config.project_root or config.repo_root), env=process_env, check=False).returncode
