from __future__ import annotations

"""Scoped privilege broker introduced by ENV-X01.

Qualification is passive by default.  Merely discovering ``su``/``sudo`` does
not mean a provider is ready, and passive/read-only callers never authorize or
invoke elevation.  Explicit execution paths may request non-interactive probes
or an executable plan after trusted policy has authorized a capability.
"""

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import time
from typing import Iterable, Mapping, Sequence

from .host_context import HostContext, detect_host_context

HOST_CHROOT_ENTER = "host.chroot.enter"
LINUX_PACKAGES_INSTALL = "linux.packages.install"
LINUX_VM_DROP_CACHES = "linux.vm.drop_caches"
ANDROID_HOST_PACKAGE_MANAGER = "android.host.package-manager"
ANDROID_ADBD_CONFIGURE = "android.adbd.configure"
ANDROID_SECURE_SETTINGS_WRITE = "android.secure-settings.write"
SSH_PROVISION_PACKAGES = "ssh.provision.packages"

KNOWN_PRIVILEGE_CAPABILITIES = (
    HOST_CHROOT_ENTER,
    LINUX_PACKAGES_INSTALL,
    LINUX_VM_DROP_CACHES,
    ANDROID_HOST_PACKAGE_MANAGER,
    ANDROID_ADBD_CONFIGURE,
    ANDROID_SECURE_SETTINGS_WRITE,
    SSH_PROVISION_PACKAGES,
)

_READY = "ready"
_ACTION_REQUIRED = "action-required"
_DENIED = "denied"
_UNAVAILABLE = "unavailable"
_ERROR = "error"

PROVIDER_STATES = (_READY, _ACTION_REQUIRED, _DENIED, _UNAVAILABLE, _ERROR)


def _stable_digest(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class PrivilegeRequest:
    capability_id: str
    scope: str = "host"
    reason: str = ""
    requested_by: str = "execution"
    interactive: bool = False
    mutation: bool = False
    reversible: bool | None = None

    def __post_init__(self) -> None:
        capability = str(self.capability_id or "").strip()
        if not capability:
            raise ValueError("PrivilegeRequest requires capability_id")
        object.__setattr__(self, "capability_id", capability)

    def to_dict(self) -> dict[str, object]:
        return {
            "capability_id": self.capability_id,
            "scope": self.scope,
            "reason": self.reason,
            "requested_by": self.requested_by,
            "interactive": self.interactive,
            "mutation": self.mutation,
            "reversible": self.reversible,
        }


@dataclass(frozen=True, slots=True)
class PrivilegePolicy:
    allow: frozenset[str] = field(default_factory=lambda: frozenset({HOST_CHROOT_ENTER}))
    deny: frozenset[str] = field(default_factory=frozenset)
    provider_order: tuple[str, ...] = ("direct-root", "root-bridge", "android-su", "sudo", "doas")
    allow_interactive_authorization: bool = False
    source: str = "builtin-global-default"

    def authorizes(self, request: PrivilegeRequest) -> tuple[bool, str]:
        if request.capability_id in self.deny or "*" in self.deny:
            return False, f"{request.capability_id} is denied by {self.source}"
        if request.capability_id in self.allow or "*" in self.allow:
            return True, f"{request.capability_id} is authorized by {self.source}"
        return False, f"{request.capability_id} is not authorized by {self.source}"

    def to_dict(self) -> dict[str, object]:
        return {
            "allow": sorted(self.allow),
            "deny": sorted(self.deny),
            "provider_order": list(self.provider_order),
            "allow_interactive_authorization": self.allow_interactive_authorization,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class PrivilegeProviderQualification:
    provider: str
    state: str
    detail: str
    executable: str = ""
    capability_id: str = ""
    passive: bool = True
    command_dialect: str = ""

    @property
    def ready(self) -> bool:
        return self.state == _READY

    def to_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "state": self.state,
            "detail": self.detail,
            "executable": self.executable,
            "capability_id": self.capability_id,
            "passive": self.passive,
            "command_dialect": self.command_dialect,
        }


@dataclass(frozen=True, slots=True)
class PrivilegePlan:
    request: PrivilegeRequest
    policy: PrivilegePolicy
    selected_provider: str
    qualification: PrivilegeProviderQualification
    provider_argv_prefix: tuple[str, ...] = ()
    shell_wrapper: bool = False

    def wrap_argv(self, argv: Sequence[str]) -> list[str]:
        command = [str(item) for item in argv]
        if self.selected_provider == "direct-root":
            return command
        if self.selected_provider == "android-su":
            return [*self.provider_argv_prefix, shlex.join(command)]
        if self.selected_provider == "root-bridge":
            return [*self.provider_argv_prefix, "--", *command]
        return [*self.provider_argv_prefix, *command]

    def to_receipt(self) -> "PrivilegeReceipt":
        return PrivilegeReceipt(
            request=self.request,
            policy_source=self.policy.source,
            selected_provider=self.selected_provider,
            qualification=self.qualification,
        )


@dataclass(frozen=True, slots=True)
class PrivilegeReceipt:
    request: PrivilegeRequest
    policy_source: str
    selected_provider: str
    qualification: PrivilegeProviderQualification
    state_change: str = "none"
    restoration: str = "not-required"

    @property
    def identity_digest(self) -> str:
        return _stable_digest(self.to_dict(include_digest=False))

    def to_dict(self, *, include_digest: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": 1,
            "request": self.request.to_dict(),
            "policy_source": self.policy_source,
            "selected_provider": self.selected_provider,
            "qualification": self.qualification.to_dict(),
            "state_change": self.state_change,
            "restoration": self.restoration,
        }
        if include_digest:
            payload["identity_digest"] = self.identity_digest
        return payload


def policy_from_mapping(mapping: Mapping[str, object] | None, *, source: str = "global") -> PrivilegePolicy:
    data = dict(mapping or {})
    allow_raw = data.get("allow", (HOST_CHROOT_ENTER,))
    deny_raw = data.get("deny", ())
    order_raw = data.get("provider_order", ("direct-root", "root-bridge", "android-su", "sudo", "doas"))

    def _strings(value: object) -> tuple[str, ...]:
        if isinstance(value, str):
            return tuple(item.strip() for item in value.split(",") if item.strip())
        if isinstance(value, Iterable):
            return tuple(str(item).strip() for item in value if str(item).strip())
        return ()

    return PrivilegePolicy(
        allow=frozenset(_strings(allow_raw)),
        deny=frozenset(_strings(deny_raw)),
        provider_order=_strings(order_raw) or ("direct-root", "root-bridge", "android-su", "sudo", "doas"),
        allow_interactive_authorization=_truthy(data.get("allow_interactive_authorization", False)),
        source=source,
    )


def load_global_privilege_policy(path: str | Path | None = None) -> PrivilegePolicy:
    # Lazy import prevents config -> execution_environments -> privilege cycles.
    from .config import load_global_config

    data, config_path = load_global_config(path)
    raw = data.get("privilege", {}) if isinstance(data, Mapping) else {}
    if raw is not None and not isinstance(raw, Mapping):
        raise SystemExit(f"Invalid global config {config_path}: [privilege] must be a TOML table")
    source = f"global:{config_path}" if Path(config_path).exists() else "builtin-global-default"
    return policy_from_mapping(raw if isinstance(raw, Mapping) else {}, source=source)


def _run_probe(argv: Sequence[str], *, timeout: float = 3.0) -> tuple[str, str]:
    try:
        proc = subprocess.run(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _ACTION_REQUIRED, "probe timed out; authorization may require user action"
    except OSError as exc:
        return _ERROR, str(exc)
    detail = (proc.stdout or proc.stderr or "").strip() or f"exit {proc.returncode}"
    if proc.returncode == 0:
        return _READY, detail
    if proc.returncode in {1, 126, 127}:
        return _DENIED, detail
    return _ERROR, detail


def qualify_privilege_provider(
    provider: str,
    request: PrivilegeRequest,
    *,
    host: HostContext | None = None,
    env: Mapping[str, str] | None = None,
    probe: bool = False,
    allow_interactive_authorization: bool = False,
) -> PrivilegeProviderQualification:
    host = host or detect_host_context(env)
    values = os.environ if env is None else env
    name = str(provider or "").strip().lower()
    capability = request.capability_id

    def _which(command: str) -> str:
        path = str(values.get("PATH", "") or "")
        return shutil.which(command, path=path or None) or ""

    if name == "direct-root":
        ready = host.euid == 0
        return PrivilegeProviderQualification(name, _READY if ready else _UNAVAILABLE, "effective uid is 0" if ready else "effective uid is not 0", capability_id=capability, passive=True)

    if name == "android-su":
        executable = _which(str(values.get("DEVTOOL_ANDROID_SU", "su") or "su"))
        if not host.termux or not executable:
            return PrivilegeProviderQualification(name, _UNAVAILABLE, "Android su is unavailable on this host", executable=executable, capability_id=capability, passive=not probe, command_dialect="su-c")
        if not probe or not request.interactive or not allow_interactive_authorization:
            detail = "su is present; readiness is not assumed without an explicit privilege action/probe"
            if probe and not request.interactive:
                detail = "su is present, but unattended qualification will not trigger Android root authorization"
            elif probe and request.interactive and not allow_interactive_authorization:
                detail = "su is present, but global policy forbids interactive root authorization"
            return PrivilegeProviderQualification(name, _ACTION_REQUIRED, detail, executable=executable, capability_id=capability, passive=True, command_dialect="su-c")
        state, detail = _run_probe([executable, "-c", "id -u"])
        if state == _READY and detail.splitlines()[:1] != ["0"]:
            state, detail = _ERROR, f"su probe did not return uid 0: {detail}"
        return PrivilegeProviderQualification(name, state, detail, executable=executable, capability_id=capability, passive=False, command_dialect="su-c")

    if name == "root-bridge":
        executable = str(values.get("DEVTOOL_ROOT_BRIDGE_COMMAND", "") or "").strip()
        if not executable:
            return PrivilegeProviderQualification(name, _UNAVAILABLE, "no trusted root bridge command is configured", capability_id=capability, passive=True, command_dialect="typed-bridge")
        resolved = _which(executable) if os.path.sep not in executable else (executable if Path(executable).exists() else "")
        if not resolved:
            return PrivilegeProviderQualification(name, _UNAVAILABLE, f"root bridge executable not found: {executable}", executable=executable, capability_id=capability, passive=True, command_dialect="typed-bridge")
        # The bridge itself is a trusted typed provider. Availability does not
        # authorize its use; policy authorization remains separate.
        return PrivilegeProviderQualification(name, _READY, "trusted root bridge endpoint is configured", executable=str(resolved), capability_id=capability, passive=True, command_dialect="typed-bridge")

    if name in {"sudo", "doas"}:
        executable = _which(name)
        if not executable:
            return PrivilegeProviderQualification(name, _UNAVAILABLE, f"{name} executable not found", capability_id=capability, passive=not probe, command_dialect=f"{name}-noninteractive")
        if not probe:
            return PrivilegeProviderQualification(name, _ACTION_REQUIRED, f"{name} is present; readiness is not inferred from binary presence", executable=executable, capability_id=capability, passive=True, command_dialect=f"{name}-noninteractive")
        probe_argv = [executable, "-n", "true"]
        state, detail = _run_probe(probe_argv)
        return PrivilegeProviderQualification(name, state, detail, executable=executable, capability_id=capability, passive=False, command_dialect=f"{name}-noninteractive")

    return PrivilegeProviderQualification(name or "unknown", _UNAVAILABLE, "unknown privilege provider", capability_id=capability, passive=True)


def qualify_privilege_providers(
    request: PrivilegeRequest,
    *,
    host: HostContext | None = None,
    env: Mapping[str, str] | None = None,
    providers: Iterable[str] = ("direct-root", "root-bridge", "android-su", "sudo", "doas"),
    probe: bool = False,
    allow_interactive_authorization: bool = False,
) -> tuple[PrivilegeProviderQualification, ...]:
    return tuple(
        qualify_privilege_provider(
            provider, request, host=host, env=env, probe=probe,
            allow_interactive_authorization=allow_interactive_authorization,
        )
        for provider in providers
    )


def resolve_privilege_plan(
    request: PrivilegeRequest,
    *,
    policy: PrivilegePolicy | None = None,
    host: HostContext | None = None,
    env: Mapping[str, str] | None = None,
    probe: bool = False,
    allow_action_required: bool = False,
) -> PrivilegePlan:
    policy = policy or load_global_privilege_policy()
    host = host or detect_host_context(env)
    allowed, reason = policy.authorizes(request)
    if not allowed:
        raise PermissionError(reason)

    qualifications = {
        q.provider: q
        for q in _qualify_privilege_providers_cached(
            request, host=host, env=env, providers=policy.provider_order, probe=probe,
            allow_interactive_authorization=policy.allow_interactive_authorization,
        )
    }
    selected: PrivilegeProviderQualification | None = None
    for provider in policy.provider_order:
        candidate = qualifications.get(provider)
        if candidate is None:
            continue
        if candidate.ready or (allow_action_required and policy.allow_interactive_authorization and candidate.state == _ACTION_REQUIRED):
            selected = candidate
            break
    if selected is None:
        summary = "; ".join(f"{name}={q.state}" for name, q in qualifications.items())
        raise PermissionError(f"No qualified privilege provider for {request.capability_id}: {summary}")

    if selected.provider == "direct-root":
        prefix: tuple[str, ...] = ()
    elif selected.provider == "android-su":
        prefix = (selected.executable or "su", "-c")
    elif selected.provider == "root-bridge":
        prefix = (selected.executable, "exec")
    elif selected.provider in {"sudo", "doas"}:
        prefix = (selected.executable or selected.provider, "-n")
    else:
        prefix = ()
    return PrivilegePlan(request=request, policy=policy, selected_provider=selected.provider, qualification=selected, provider_argv_prefix=prefix, shell_wrapper=selected.provider == "android-su")


@dataclass(slots=True)
class QualificationCache:
    ttl_seconds: float = 15.0
    _values: dict[str, tuple[float, object]] = field(default_factory=dict)

    def get(self, key: str) -> object | None:
        item = self._values.get(key)
        if item is None:
            return None
        created, value = item
        if time.monotonic() - created > self.ttl_seconds:
            self._values.pop(key, None)
            return None
        return value

    def put(self, key: str, value: object) -> None:
        self._values[key] = (time.monotonic(), value)

    def invalidate(self, prefix: str = "") -> None:
        if not prefix:
            self._values.clear()
            return
        for key in tuple(self._values):
            if key.startswith(prefix):
                self._values.pop(key, None)

_PRIVILEGE_QUALIFICATION_CACHE = QualificationCache(ttl_seconds=15.0)

def _privilege_cache_key(
    request: PrivilegeRequest, *, host: HostContext, env: Mapping[str, str] | None,
    providers: Iterable[str], probe: bool, allow_interactive_authorization: bool,
) -> str:
    values = os.environ if env is None else env
    payload = {
        "host": host.identity_digest,
        "request": request.to_dict(),
        "providers": list(providers),
        "probe": bool(probe),
        "allow_interactive_authorization": bool(allow_interactive_authorization),
        "provider_environment": {
            key: str(values.get(key, "") or "")
            for key in ("PATH", "DEVTOOL_ANDROID_SU", "DEVTOOL_ROOT_BRIDGE_COMMAND")
        },
    }
    return _stable_digest(payload)

def _qualify_privilege_providers_cached(
    request: PrivilegeRequest, *, host: HostContext, env: Mapping[str, str] | None,
    providers: Iterable[str], probe: bool, allow_interactive_authorization: bool,
) -> tuple[PrivilegeProviderQualification, ...]:
    provider_tuple = tuple(providers)
    key = _privilege_cache_key(
        request, host=host, env=env, providers=provider_tuple, probe=probe,
        allow_interactive_authorization=allow_interactive_authorization,
    )
    cached = _PRIVILEGE_QUALIFICATION_CACHE.get(key)
    if isinstance(cached, tuple) and all(isinstance(item, PrivilegeProviderQualification) for item in cached):
        return cached
    result = qualify_privilege_providers(
        request, host=host, env=env, providers=provider_tuple, probe=probe,
        allow_interactive_authorization=allow_interactive_authorization,
    )
    _PRIVILEGE_QUALIFICATION_CACHE.put(key, result)
    return result

def invalidate_privilege_qualification_cache() -> None:
    _PRIVILEGE_QUALIFICATION_CACHE.invalidate()

