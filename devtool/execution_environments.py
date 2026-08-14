from __future__ import annotations

"""Shared execution-environment selection and path translation.

This module describes *where* a runner command executes independently from
*what* project runner planned it.  It deliberately has no CLI or process
execution dependencies, which keeps Termux/chroot selection testable and lets
future Android-device and external-device backends reuse the same contract.
"""

from dataclasses import dataclass, field
import os
from pathlib import Path
import shutil
from typing import Iterable, Mapping

AUTO = "auto"
NATIVE_TERMUX = "native-termux"
CHROOT = "chroot"
DESKTOP_LINUX = "desktop-linux"
ANDROID_DEVICE = "android-device"
EXTERNAL_DEVICE = "external-device"
SSH = "ssh"
PROOT_DISTRO = "proot-distro"

EXECUTION_ENVIRONMENTS = (
    AUTO,
    NATIVE_TERMUX,
    CHROOT,
    DESKTOP_LINUX,
    ANDROID_DEVICE,
    EXTERNAL_DEVICE,
    SSH,
    PROOT_DISTRO,
)

_CHROOT_CONTEXT_VALUES = {"chroot", "android-chroot", "termux-chroot"}
_TERMUX_ROOT_PREFIX = "/data/data/com.termux/"
_TERMUX_HOME_PREFIX = "/data/data/com.termux/files/home"
_TERMUX_PREFIX_PREFIX = "/data/data/com.termux/files/usr"


@dataclass(frozen=True, slots=True)
class PathMapping:
    host: str
    guest: str

    def as_tuple(self) -> tuple[str, str]:
        return self.host, self.guest


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    requested: str
    current: str
    effective: str
    chroot_name: str = ""
    device_serial: str = ""
    delegated: bool = False
    reason: str = ""
    path_map: tuple[PathMapping, ...] = field(default_factory=tuple)

    @property
    def uses_chroot(self) -> bool:
        return self.effective == CHROOT

    @property
    def is_current_environment(self) -> bool:
        return self.effective == self.current

    def metadata(self) -> dict[str, object]:
        return {
            "execution_environment_requested": self.requested,
            "execution_environment_current": self.current,
            "execution_environment": self.effective,
            "execution_environment_reason": self.reason,
            "execution_delegated": self.delegated,
            "chroot": self.chroot_name,
            "device_serial": self.device_serial,
        }


@dataclass(frozen=True, slots=True)
class PreflightCheck:
    name: str
    status: str
    detail: str


@dataclass(frozen=True, slots=True)
class ExecutionPreflight:
    plan: ExecutionPlan
    checks: tuple[PreflightCheck, ...]

    @property
    def ok(self) -> bool:
        return all(check.status not in {"missing", "incompatible", "error"} for check in self.checks)

    def failures(self) -> tuple[PreflightCheck, ...]:
        return tuple(check for check in self.checks if check.status in {"missing", "incompatible", "error"})


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def inside_chroot(env: Mapping[str, str] | None = None) -> bool:
    env = env or os.environ
    context = str(env.get("DEVTOOL_EXECUTION_CONTEXT", "")).strip().lower()
    return context in _CHROOT_CONTEXT_VALUES or _truthy(env.get("DEVTOOL_INSIDE_TERMUX_CHROOT"))


def is_termux_host(env: Mapping[str, str] | None = None) -> bool:
    env = env or os.environ
    if inside_chroot(env):
        return False
    prefix = str(env.get("PREFIX", ""))
    home = str(env.get("HOME", ""))
    return bool(
        env.get("TERMUX_VERSION")
        or prefix == _TERMUX_PREFIX_PREFIX
        or prefix.startswith(_TERMUX_PREFIX_PREFIX + "/")
        or home == _TERMUX_HOME_PREFIX
        or home.startswith(_TERMUX_HOME_PREFIX + "/")
    )


def detect_current_environment(env: Mapping[str, str] | None = None) -> str:
    env = env or os.environ
    if inside_chroot(env):
        return CHROOT
    if is_termux_host(env):
        return NATIVE_TERMUX
    return DESKTOP_LINUX


def normalize_execution_environment(value: str | None) -> str:
    normalized = str(value or AUTO).strip().lower().replace("_", "-") or AUTO
    aliases = {
        "host": DESKTOP_LINUX,
        "desktop": DESKTOP_LINUX,
        "linux": DESKTOP_LINUX,
        "termux": NATIVE_TERMUX,
        "native": NATIVE_TERMUX,
        "android": ANDROID_DEVICE,
        "device": ANDROID_DEVICE,
        "external": EXTERNAL_DEVICE,
        "remote": SSH,
        "proot": PROOT_DISTRO,
        "proot-distro": PROOT_DISTRO,
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in EXECUTION_ENVIRONMENTS:
        valid = ", ".join(EXECUTION_ENVIRONMENTS)
        raise ValueError(f"Unknown execution environment '{value}'. Expected one of: {valid}")
    return normalized


def _split_path_map(raw: str) -> list[PathMapping]:
    mappings: list[PathMapping] = []
    for chunk in str(raw or "").replace(";", ",").split(","):
        item = chunk.strip()
        if not item or "=" not in item:
            continue
        host, guest = item.split("=", 1)
        host = os.path.expanduser(host.strip()).rstrip("/")
        guest = guest.strip().rstrip("/") or "/"
        if host:
            mappings.append(PathMapping(host, guest))
    return sorted(mappings, key=lambda item: len(item.host), reverse=True)


def default_termux_chroot_path_map(env: Mapping[str, str] | None = None) -> tuple[PathMapping, ...]:
    env = env or os.environ
    mappings = list(_split_path_map(env.get("DEVTOOL_TERMUX_CHROOT_PATH_MAP", "")))

    # Automatic host→guest mappings are meaningful only on the native Termux
    # host. Desktop Linux must never invent a chroot home mapping from its own
    # HOME, because that can make ordinary PC paths look chroot-compatible.
    termux_home = str(env.get("HOME", "")).strip() if is_termux_host(env) else ""
    chroot_home = str(env.get("DEVTOOL_TERMUX_CHROOT_HOME", "")).strip()
    if not chroot_home:
        explicit_user = str(env.get("DEVTOOL_TERMUX_CHROOT_USER", "")).strip()
        if explicit_user:
            chroot_home = f"/home/{explicit_user}"
        elif termux_home.startswith("/data/data/com.termux/files/home"):
            chroot_home = "/home/termux"
        else:
            chroot_user = str(env.get("DEVTOOL_TERMUX_CHROOT_USER", "mike")).strip() or "mike"
            chroot_home = f"/home/{chroot_user}"
    if termux_home and chroot_home:
        mappings.append(PathMapping(os.path.expanduser(termux_home).rstrip("/"), chroot_home.rstrip("/")))

    for identity in ("/sdcard", "/storage", "/mnt", "/tmp"):
        mappings.append(PathMapping(identity, identity))

    seen: set[str] = set()
    unique: list[PathMapping] = []
    for mapping in sorted(mappings, key=lambda item: len(item.host), reverse=True):
        if mapping.host in seen:
            continue
        seen.add(mapping.host)
        unique.append(mapping)
    return tuple(unique)


def coerce_path_map(mappings: Iterable[PathMapping | tuple[str, str]]) -> tuple[PathMapping, ...]:
    result: list[PathMapping] = []
    for mapping in mappings:
        if isinstance(mapping, PathMapping):
            result.append(mapping)
        else:
            result.append(PathMapping(str(mapping[0]), str(mapping[1])))
    return tuple(sorted(result, key=lambda item: len(item.host), reverse=True))


def translate_host_path(
    value: str,
    mappings: Iterable[PathMapping | tuple[str, str]],
    *,
    cwd: str | None = None,
) -> str:
    if not value:
        return value
    normalized = coerce_path_map(mappings)
    original = value
    expanded = os.path.expanduser(value)
    if not os.path.isabs(expanded):
        expanded = os.path.abspath(os.path.join(cwd or os.getcwd(), expanded))
    for mapping in normalized:
        if expanded == mapping.host:
            return mapping.guest or "/"
        if expanded.startswith(mapping.host + "/"):
            suffix = expanded[len(mapping.host):]
            return (mapping.guest.rstrip("/") or "/") + suffix
    return expanded if os.path.isabs(original) or not os.path.isabs(original) else original


def translate_guest_path(
    value: str,
    mappings: Iterable[PathMapping | tuple[str, str]],
) -> str | None:
    if not value:
        return None
    normalized = sorted(coerce_path_map(mappings), key=lambda item: len(item.guest), reverse=True)
    for mapping in normalized:
        host = mapping.host.rstrip("/") or "/"
        guest = mapping.guest.rstrip("/") or "/"
        if value == guest:
            return host
        if value.startswith(guest + "/"):
            return (host.rstrip("/") or "/") + value[len(guest):]
    return None


def resolve_execution_plan(
    *,
    requested: str = AUTO,
    configured: str = AUTO,
    chroot_name: str = "androidos",
    device_serial: str = "",
    env: Mapping[str, str] | None = None,
    path_map: Iterable[PathMapping | tuple[str, str]] | None = None,
) -> ExecutionPlan:
    env = env or os.environ
    current = detect_current_environment(env)
    request = normalize_execution_environment(requested if requested and requested != AUTO else configured)
    mappings = coerce_path_map(
        default_termux_chroot_path_map(env) if path_map is None else path_map
    )

    if request == AUTO:
        effective = current
        reason = "auto selected the current execution environment"
    else:
        effective = request
        reason = "explicit execution environment"

    if effective == CHROOT and current == CHROOT:
        return ExecutionPlan(
            requested=request,
            current=current,
            effective=CHROOT,
            chroot_name=str(env.get("DEVTOOL_CHROOT_CONTEXT", chroot_name) or chroot_name),
            device_serial=device_serial,
            delegated=False,
            reason="already inside the requested chroot; recursive delegation suppressed",
            path_map=mappings,
        )

    return ExecutionPlan(
        requested=request,
        current=current,
        effective=effective,
        chroot_name=chroot_name if effective == CHROOT else "",
        device_serial=device_serial,
        delegated=effective != current,
        reason=reason,
        path_map=mappings,
    )


def preflight_execution(
    repo_root: Path,
    plan: ExecutionPlan,
    *,
    expected_paths: Iterable[str] = (),
) -> ExecutionPreflight:
    checks: list[PreflightCheck] = []
    repo = Path(repo_root).expanduser()
    checks.append(PreflightCheck("repository", "ready" if repo.is_dir() else "missing", str(repo)))

    seen_targets: set[str] = set()
    for raw in expected_paths:
        if not raw:
            continue
        path = Path(raw)
        identity = str(path.expanduser().absolute())
        if identity in seen_targets:
            continue
        seen_targets.add(identity)
        # Targets may intentionally be introduced by an overlay, so missing
        # paths are diagnostic rather than fatal at this shared layer.
        checks.append(PreflightCheck("target", "ready" if path.exists() else "optional", str(path)))

    if plan.effective == CHROOT:
        if plan.current == CHROOT:
            checks.append(PreflightCheck("chroot-context", "ready", "already inside chroot"))
        elif plan.current != NATIVE_TERMUX:
            checks.append(PreflightCheck(
                "chroot-host",
                "incompatible",
                f"chroot execution is available only from native Termux; current environment is {plan.current}",
            ))
        else:
            executable = shutil.which(os.environ.get("DEVTOOL_CHROOTCTL", "chrootctl"))
            checks.append(PreflightCheck("chrootctl", "ready" if executable else "missing", executable or "chrootctl not found"))
            mapped = translate_host_path(str(repo.resolve()), plan.path_map, cwd=str(repo))
            changed = mapped != str(repo.resolve()) or str(repo).startswith(("/sdcard", "/storage", "/mnt", "/tmp"))
            if changed:
                mapping_status = "ready"
                mapping_detail = mapped
            else:
                mapping_status = "incompatible"
                mapping_detail = f"no Termux-to-chroot mapping for {repo.resolve()}"
            checks.append(PreflightCheck("path-map", mapping_status, mapping_detail))
    elif plan.effective == NATIVE_TERMUX:
        checks.append(PreflightCheck("termux", "ready" if plan.current == NATIVE_TERMUX else "incompatible", plan.current))
    elif plan.effective == DESKTOP_LINUX:
        checks.append(PreflightCheck("desktop-linux", "ready" if plan.current == DESKTOP_LINUX else "incompatible", plan.current))
    elif plan.effective == PROOT_DISTRO:
        executable = shutil.which(os.environ.get("DEVTOOL_PROOT_DISTRO_COMMAND", "proot-distro"))
        checks.append(PreflightCheck(
            "proot-distro",
            "ready" if executable else "missing",
            executable or "proot-distro executable not found",
        ))
    elif plan.effective == SSH:
        executable = shutil.which(os.environ.get("DEVTOOL_SSH_COMMAND", "ssh"))
        checks.append(PreflightCheck(
            "ssh-transport",
            "ready" if executable else "missing",
            executable or "ssh executable not found",
        ))
    elif plan.effective in {ANDROID_DEVICE, EXTERNAL_DEVICE}:
        # The environment is modelled now, while runner-specific device command
        # execution arrives with Android instrumentation support.
        checks.append(PreflightCheck("device-backend", "incompatible", "device command backend is not implemented in this phase"))

    return ExecutionPreflight(plan=plan, checks=tuple(checks))



def termux_path_leaks(values: Iterable[str]) -> tuple[str, ...]:
    """Return unique native-Termux paths still present in command material."""

    leaks: list[str] = []
    for raw in values:
        text = str(raw or "")
        if _TERMUX_ROOT_PREFIX not in text:
            continue
        for token in text.replace("=", " ").replace(":", " ").split():
            start = token.find(_TERMUX_ROOT_PREFIX)
            if start < 0:
                continue
            candidate = token[start:].strip("'\";,()[]{}")
            if candidate and candidate not in leaks:
                leaks.append(candidate)
    return tuple(leaks)


def assert_chroot_path_safe(values: Iterable[str], *, context: str = "command") -> None:
    """Reject native-Termux paths before a command executes inside a chroot."""

    leaks = termux_path_leaks(values)
    if not leaks:
        return
    rendered = ", ".join(leaks[:5])
    suffix = f" (+{len(leaks) - 5} more)" if len(leaks) > 5 else ""
    raise ValueError(
        f"{context} contains native Termux path(s) that are not valid inside the chroot: "
        f"{rendered}{suffix}. Configure guest paths or select native-termux execution."
    )

def chroot_environment_assignments(chroot_name: str) -> tuple[str, ...]:
    return (
        "DEVTOOL_EXECUTION_CONTEXT=chroot",
        "DEVTOOL_INSIDE_TERMUX_CHROOT=1",
        f"DEVTOOL_CHROOT_CONTEXT={chroot_name}",
    )
