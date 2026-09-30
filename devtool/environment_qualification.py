from __future__ import annotations

"""Capability-first execution-substrate qualification for ENV-X01."""

from dataclasses import dataclass, field
import hashlib
import json
import os
import shutil
import time
from typing import Iterable, Mapping, Sequence

from .host_context import HostContext, detect_host_context
from .models import CommandSpec
from .privilege import (
    HOST_CHROOT_ENTER,
    PrivilegePolicy,
    PrivilegeRequest,
    load_global_privilege_policy,
    qualify_privilege_providers,
)


PROTECTED_ENV_NAMES = frozenset({
    "DEVTOOL_REPO_ROOT",
    "DEVTOOL_RUN_ID",
    "DEVTOOL_IPC_SOCKET",
    "DEVTOOL_INTERNAL_INVOCATION_SOURCE",
    "DEVTOOL_ANDROID_SU",
    "DEVTOOL_ROOT_BRIDGE_COMMAND",
    "DEVTOOL_INSIDE_SSH",
    "DEVTOOL_VALIDATION_STATE_REPO_ROOT",
    "DEVTOOL_TRANSACTION_ROOT",
    "DEVTOOL_TRANSACTION_REPO_ROOT",
    "DEVTOOL_TRANSACTION_PRIMARY_REPO_ROOT",
    "DEVTOOL_TRANSACTION_COMMIT_MODE",
    "DEVTOOL_TRANSACTION_ISOLATION",
})
PROTECTED_ENV_PREFIXES = (
    "DEVTOOL_FEDERATION_",
    "DEVTOOL_TRANSACTION_",
    "DEVTOOL_IPC_",
    "DEVTOOL_EXECUTION_",
    "DEVTOOL_PRIVILEGE_",
    "DEVTOOL_CHROOT_",
    "DEVTOOL_TERMUX_CHROOT_",
    "DEVTOOL_ROOT_BRIDGE_",
    "DEVTOOL_SSH_",
    "DEVTOOL_AUTO_CHROOT_",
    "DEVTOOL_GRADLE_TAKEOVER_",
)


def _stable_digest(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def is_protected_environment_name(name: str) -> bool:
    key = str(name or "").strip().upper()
    return key in PROTECTED_ENV_NAMES or any(key.startswith(prefix) for prefix in PROTECTED_ENV_PREFIXES)


def assert_environment_overlay_safe(values: Mapping[str, object] | None, *, source: str) -> None:
    for key in (values or {}):
        if is_protected_environment_name(str(key)):
            raise ValueError(f"{source} may not override protected Devtool environment variable {key}")


def merge_environment_overlay(base: Mapping[str, str], overlay: Mapping[str, object] | None, *, source: str) -> dict[str, str]:
    assert_environment_overlay_safe(overlay, source=source)
    merged = dict(base)
    merged.update({str(key): str(value) for key, value in (overlay or {}).items()})
    return merged


@dataclass(frozen=True, slots=True)
class CapabilityRequirements:
    tools: tuple[str, ...] = ()
    privilege_capabilities: tuple[str, ...] = ()
    requires_remote: bool = False
    network: str = "optional"
    features: tuple[str, ...] = ()

    @classmethod
    def from_specs(cls, specs: Sequence[CommandSpec]) -> "CapabilityRequirements":
        tools: set[str] = set()
        privilege: set[str] = set()
        features: set[str] = set()
        requires_remote = False
        network = "optional"
        for spec in specs:
            if spec.argv:
                executable = str(spec.argv[0]).strip()
                if executable and "/" not in executable and "\\" not in executable:
                    tools.add(executable)
            meta = spec.metadata or {}
            for value in meta.get("required_tools", ()) or ():
                if str(value).strip():
                    tools.add(str(value).strip())
            for value in meta.get("required_capabilities", ()) or ():
                if str(value).strip():
                    privilege.add(str(value).strip())
            for value in meta.get("required_features", ()) or ():
                if str(value).strip():
                    features.add(str(value).strip())
            requires_remote = requires_remote or bool(meta.get("requires_remote"))
            if str(meta.get("network") or "").strip().lower() in {"required", "offline"}:
                network = str(meta.get("network")).strip().lower()
        return cls(tuple(sorted(tools)), tuple(sorted(privilege)), requires_remote, network, tuple(sorted(features)))

    def to_dict(self) -> dict[str, object]:
        return {
            "tools": list(self.tools),
            "privilege_capabilities": list(self.privilege_capabilities),
            "requires_remote": self.requires_remote,
            "network": self.network,
            "features": list(self.features),
        }


@dataclass(frozen=True, slots=True)
class EnvironmentCandidate:
    kind: str
    name: str = ""
    provider: str = ""
    guest_user: str = ""
    remote: bool = False
    metadata: Mapping[str, object] = field(default_factory=dict)

    @property
    def identity(self) -> str:
        suffix = self.name or self.provider
        return f"{self.kind}:{suffix}" if suffix else self.kind

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "name": self.name,
            "provider": self.provider,
            "guest_user": self.guest_user,
            "remote": self.remote,
            "metadata": dict(self.metadata),
            "identity": self.identity,
        }


@dataclass(frozen=True, slots=True)
class CandidateQualification:
    candidate: EnvironmentCandidate
    accepted: bool
    checks: tuple[dict[str, str], ...]
    rejection_reasons: tuple[str, ...] = ()
    missing_tools: tuple[str, ...] = ()
    privilege: tuple[dict[str, object], ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate": self.candidate.to_dict(),
            "accepted": self.accepted,
            "checks": list(self.checks),
            "rejection_reasons": list(self.rejection_reasons),
            "missing_tools": list(self.missing_tools),
            "privilege": list(self.privilege),
        }


@dataclass(frozen=True, slots=True)
class EnvironmentQualificationReceipt:
    host: HostContext
    requirements: CapabilityRequirements
    requested: str
    preference: str
    candidates: tuple[CandidateQualification, ...]
    selected_identity: str
    cache_key: str
    created_monotonic: float
    ttl_seconds: float

    @property
    def selected(self) -> CandidateQualification | None:
        return next((item for item in self.candidates if item.candidate.identity == self.selected_identity), None)

    @property
    def identity_digest(self) -> str:
        return _stable_digest(self.to_dict(include_runtime=False, include_digest=False))

    def to_dict(self, *, include_runtime: bool = True, include_digest: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": 1,
            "host": self.host.to_dict(),
            "requirements": self.requirements.to_dict(),
            "requested": self.requested,
            "preference": self.preference,
            "candidates": [item.to_dict() for item in self.candidates],
            "selected_identity": self.selected_identity,
            "cache_key": self.cache_key,
            "ttl_seconds": self.ttl_seconds,
        }
        if include_runtime:
            payload["created_monotonic"] = self.created_monotonic
        if include_digest:
            payload["identity_digest"] = self.identity_digest
        return payload


class EnvironmentQualificationCache:
    def __init__(self, *, ttl_seconds: float = 15.0) -> None:
        self.ttl_seconds = max(0.0, float(ttl_seconds))
        self._entries: dict[str, EnvironmentQualificationReceipt] = {}

    def get(self, key: str) -> EnvironmentQualificationReceipt | None:
        receipt = self._entries.get(key)
        if receipt is None:
            return None
        effective_ttl = min(self.ttl_seconds, receipt.ttl_seconds)
        if effective_ttl <= 0 or time.monotonic() - receipt.created_monotonic > effective_ttl:
            self._entries.pop(key, None)
            return None
        return receipt

    def put(self, receipt: EnvironmentQualificationReceipt) -> None:
        self._entries[receipt.cache_key] = receipt

    def invalidate(self) -> None:
        self._entries.clear()


_ENVIRONMENT_QUALIFICATION_CACHE = EnvironmentQualificationCache(ttl_seconds=15.0)

def invalidate_environment_qualification_cache() -> None:
    _ENVIRONMENT_QUALIFICATION_CACHE.invalidate()

def default_candidates(host: HostContext, *, chroot_name: str = "androidos") -> tuple[EnvironmentCandidate, ...]:
    candidates: list[EnvironmentCandidate] = []
    current = host.current_substrate
    candidates.append(EnvironmentCandidate(current, name="current", provider="native", remote=host.remote))
    if host.termux:
        candidates.append(EnvironmentCandidate("chroot", name=chroot_name, provider="chrootctl", guest_user=str(os.environ.get("DEVTOOL_CHROOT_GUEST_USER", "") or "")))
        candidates.append(EnvironmentCandidate("proot-distro", name=str(os.environ.get("DEVTOOL_PROOT_DISTRO", "") or ""), provider="proot-distro"))
    if shutil.which("ssh"):
        candidates.append(EnvironmentCandidate("ssh", provider="ssh", remote=True))
    if shutil.which("proot-distro") and not any(item.kind == "proot-distro" for item in candidates):
        candidates.append(EnvironmentCandidate("proot-distro", provider="proot-distro"))
    return tuple(candidates)


def _native_tool_path(tool: str) -> str:
    return shutil.which(tool) or ""


def qualify_candidate(
    candidate: EnvironmentCandidate,
    requirements: CapabilityRequirements,
    *,
    host: HostContext,
    policy: PrivilegePolicy,
    probe_privilege: bool = False,
) -> CandidateQualification:
    checks: list[dict[str, str]] = []
    reasons: list[str] = []
    missing_tools: list[str] = []
    privilege_rows: list[dict[str, object]] = []

    if requirements.requires_remote and not candidate.remote:
        reasons.append("planned work requires a remote substrate")

    if candidate.kind in {host.current_substrate, "desktop-linux", "desktop-macos", "desktop-windows", "native-termux"} and not candidate.remote:
        for tool in requirements.tools:
            path = _native_tool_path(tool)
            checks.append({"name": f"tool:{tool}", "status": "ready" if path else "missing", "detail": path or "not found on PATH"})
            if not path:
                missing_tools.append(tool)
    elif candidate.kind == "chroot":
        chrootctl = shutil.which(str(os.environ.get("DEVTOOL_CHROOTCTL", "chrootctl") or "chrootctl")) or ""
        checks.append({"name": "chrootctl", "status": "ready" if chrootctl else "missing", "detail": chrootctl or "chrootctl not found"})
        if not host.termux:
            reasons.append("chroot candidate requires the native Termux host")
        if not chrootctl:
            reasons.append("chrootctl is unavailable")
        request = PrivilegeRequest(HOST_CHROOT_ENTER, reason=f"enter chroot {candidate.name}", requested_by="environment-qualification")
        allowed, policy_reason = policy.authorizes(request)
        if not allowed:
            reasons.append(policy_reason)
        qualifications = qualify_privilege_providers(
            request, host=host, probe=probe_privilege,
            allow_interactive_authorization=policy.allow_interactive_authorization,
        )
        privilege_rows.extend(item.to_dict() for item in qualifications)
        if allowed and not any(item.ready for item in qualifications):
            pending = any(item.state == "action-required" for item in qualifications)
            reasons.append(
                "chroot-entry privilege requires explicit authorization"
                if pending else "no chroot-entry privilege provider is qualified"
            )
        if requirements.tools:
            # Passive qualification never guesses about guest contents. A
            # caller may attach a previously proven tool receipt in metadata.
            supplied = {str(item) for item in candidate.metadata.get("tools", ()) or ()}
            unknown = [tool for tool in requirements.tools if tool not in supplied]
            if unknown:
                checks.append({"name": "guest-tools", "status": "unknown", "detail": "no guest tool qualification receipt for: " + ", ".join(unknown)})
                reasons.append("guest tool requirements are unqualified: " + ", ".join(unknown))
    elif candidate.kind == "proot-distro":
        executable = shutil.which("proot-distro") or ""
        checks.append({"name": "proot-distro", "status": "ready" if executable else "missing", "detail": executable or "not found"})
        if not executable:
            reasons.append("proot-distro is unavailable")
        if requirements.privilege_capabilities:
            reasons.append("PRoot emulated uid 0 cannot satisfy real host privilege capabilities")
        if requirements.tools:
            supplied = {str(item) for item in candidate.metadata.get("tools", ()) or ()}
            unknown = [tool for tool in requirements.tools if tool not in supplied]
            if unknown:
                checks.append({"name": "guest-tools", "status": "unknown", "detail": "no PRoot tool qualification receipt for: " + ", ".join(unknown)})
                reasons.append("PRoot tool requirements are unqualified: " + ", ".join(unknown))
    elif candidate.kind == "ssh":
        executable = shutil.which("ssh") or ""
        checks.append({"name": "ssh", "status": "ready" if executable else "missing", "detail": executable or "not found"})
        if not executable:
            reasons.append("ssh transport is unavailable")
        if requirements.tools:
            supplied = {str(item) for item in candidate.metadata.get("tools", ()) or ()}
            unknown = [tool for tool in requirements.tools if tool not in supplied]
            if unknown:
                checks.append({"name": "remote-tools", "status": "unknown", "detail": "no SSH worker tool qualification receipt for: " + ", ".join(unknown)})
                reasons.append("remote tool requirements are unqualified: " + ", ".join(unknown))
    elif candidate.kind in {"oci", "ssh-proot-oci"}:
        # Provider-specific readiness is attached by OCI/SSH ownership; X01
        # consumes explicit receipts and never treats declaration as readiness.
        ready = bool(candidate.metadata.get("qualified", False))
        checks.append({"name": "provider", "status": "ready" if ready else "unknown", "detail": candidate.provider or candidate.kind})
        if not ready:
            reasons.append("provider has no qualification receipt")
        if requirements.tools:
            supplied = {str(item) for item in candidate.metadata.get("tools", ()) or ()}
            unknown = [tool for tool in requirements.tools if tool not in supplied]
            if unknown:
                reasons.append("provider tool requirements are unqualified: " + ", ".join(unknown))
    else:
        checks.append({"name": "substrate", "status": "unknown", "detail": candidate.kind})

    if requirements.privilege_capabilities and candidate.kind == "proot-distro":
        pass
    elif requirements.privilege_capabilities and candidate.kind != "chroot":
        for capability in requirements.privilege_capabilities:
            request = PrivilegeRequest(capability, reason="planned capability", requested_by="environment-qualification")
            allowed, policy_reason = policy.authorizes(request)
            qualifications = qualify_privilege_providers(request, host=host, probe=probe_privilege, allow_interactive_authorization=policy.allow_interactive_authorization)
            privilege_rows.extend(item.to_dict() for item in qualifications)
            if not allowed:
                reasons.append(policy_reason)
            elif not any(item.ready for item in qualifications):
                if any(item.state == "action-required" for item in qualifications):
                    reasons.append(f"privilege capability {capability} requires explicit authorization")
                else:
                    reasons.append(f"no privilege provider can satisfy {capability}")

    accepted = not reasons and not missing_tools
    return CandidateQualification(candidate, accepted, tuple(checks), tuple(reasons), tuple(missing_tools), tuple(privilege_rows))


def qualify_environment(
    requirements: CapabilityRequirements,
    *,
    requested: str = "auto",
    preference: str = "least-privilege",
    candidates: Iterable[EnvironmentCandidate] | None = None,
    host: HostContext | None = None,
    policy: PrivilegePolicy | None = None,
    chroot_name: str = "androidos",
    probe_privilege: bool = False,
    ttl_seconds: float = 15.0,
) -> EnvironmentQualificationReceipt:
    host = host or detect_host_context()
    policy = policy or load_global_privilege_policy()
    candidate_list = tuple(candidates or default_candidates(host, chroot_name=chroot_name))
    requested_norm = str(requested or "auto").strip().lower().replace("_", "-")
    payload = {
        "host": host.identity_digest,
        "requirements": requirements.to_dict(),
        "requested": requested_norm,
        "preference": preference,
        "candidates": [item.to_dict() for item in candidate_list],
        "policy": policy.to_dict(),
        "probe_privilege": bool(probe_privilege),
        "ttl_seconds": float(ttl_seconds),
        "provider_environment": {
            key: str(os.environ.get(key, "") or "")
            for key in ("PATH", "DEVTOOL_CHROOTCTL", "DEVTOOL_PROOT_DISTRO", "DEVTOOL_EXECUTION_PROVIDER")
        },
    }
    cache_key = _stable_digest(payload)
    cached = _ENVIRONMENT_QUALIFICATION_CACHE.get(cache_key)
    if cached is not None:
        return cached
    qualified = tuple(qualify_candidate(candidate, requirements, host=host, policy=policy, probe_privilege=probe_privilege) for candidate in candidate_list)

    selected = ""
    eligible = [item for item in qualified if item.accepted]
    if requested_norm not in {"", "auto"}:
        match = next((item for item in eligible if item.candidate.kind == requested_norm or item.candidate.identity == requested_norm), None)
        selected = match.candidate.identity if match else ""
    else:
        # least privilege + locality: current/native first, then rootless PRoot,
        # then privileged chroot, then remote provider.
        order = {host.current_substrate: 0, "native-termux": 0, "desktop-linux": 0, "desktop-macos": 0, "desktop-windows": 0, "proot-distro": 1, "chroot": 2, "ssh": 3, "oci": 4, "ssh-proot-oci": 4}
        eligible.sort(key=lambda item: (order.get(item.candidate.kind, 99), item.candidate.identity))
        selected = eligible[0].candidate.identity if eligible else ""

    receipt = EnvironmentQualificationReceipt(
        host, requirements, requested_norm, preference, qualified, selected,
        cache_key, time.monotonic(), float(ttl_seconds),
    )
    _ENVIRONMENT_QUALIFICATION_CACHE.put(receipt)
    return receipt


def render_environment_explain(receipt: EnvironmentQualificationReceipt) -> str:
    lines = [
        f"Host: {receipt.host.host_kind} ({receipt.host.current_substrate})",
        f"Requested: {receipt.requested or 'auto'}",
        f"Selected: {receipt.selected_identity or '<none>'}",
        "Candidates:",
    ]
    for item in receipt.candidates:
        status = "accepted" if item.accepted else "rejected"
        lines.append(f"  {item.candidate.identity}: {status}")
        for reason in item.rejection_reasons:
            lines.append(f"    - {reason}")
        for tool in item.missing_tools:
            lines.append(f"    - missing tool: {tool}")
    return "\n".join(lines)
