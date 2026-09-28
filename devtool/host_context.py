from __future__ import annotations

"""Canonical host identity for ENV-X01.

The host identity deliberately separates the real operating system from the
execution substrate.  A Termux host is still Android, a Linux chroot entered
from Termux is still a Linux guest, and a remote/OCI substrate is represented
as a separate axis by environment qualification.
"""

from dataclasses import dataclass
import hashlib
import json
import os
import platform
import sys
from typing import Mapping


_TERMUX_HOME_PREFIX = "/data/data/com.termux/files/home"
_TERMUX_PREFIX_PREFIX = "/data/data/com.termux/files/usr"
_CHROOT_CONTEXT_VALUES = {"chroot", "android-chroot", "termux-chroot"}


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _stable_digest(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _system_name(*, system: str | None = None, platform_name: str | None = None) -> str:
    value = str(system if system is not None else platform.system()).strip().lower()
    plat = str(platform_name if platform_name is not None else sys.platform).strip().lower()
    if value == "darwin" or plat.startswith("darwin"):
        return "macos"
    if value == "windows" or plat.startswith(("win32", "cygwin", "msys")):
        return "windows"
    if value == "linux" or plat.startswith("linux"):
        return "linux"
    if value == "android" or plat.startswith("android"):
        return "android"
    return value or plat or "unknown"


@dataclass(frozen=True, slots=True)
class HostContext:
    os_family: str
    system: str
    architecture: str
    machine: str
    termux: bool
    inside_chroot: bool
    chroot_name: str = ""
    remote: bool = False
    remote_provider: str = ""
    uid: int | None = None
    euid: int | None = None

    @property
    def host_kind(self) -> str:
        if self.termux:
            return "termux-android"
        return self.os_family

    @property
    def current_substrate(self) -> str:
        if self.remote:
            # Transport remains the substrate identity; provider detail is a
            # separate axis so ssh-proot-oci/OCI does not masquerade as a new
            # local execution environment or trigger recursive delegation.
            return "ssh"
        if self.inside_chroot:
            return "chroot"
        if self.termux:
            return "native-termux"
        if self.os_family == "linux":
            return "desktop-linux"
        if self.os_family == "macos":
            return "desktop-macos"
        if self.os_family == "windows":
            return "desktop-windows"
        return f"host-{self.os_family}" if self.os_family else "host-unknown"

    @property
    def identity_digest(self) -> str:
        return _stable_digest(self.to_dict(include_digest=False))

    def to_dict(self, *, include_digest: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": 1,
            "os_family": self.os_family,
            "system": self.system,
            "architecture": self.architecture,
            "machine": self.machine,
            "termux": self.termux,
            "inside_chroot": self.inside_chroot,
            "chroot_name": self.chroot_name,
            "remote": self.remote,
            "remote_provider": self.remote_provider,
            "uid": self.uid,
            "euid": self.euid,
            "host_kind": self.host_kind,
            "current_substrate": self.current_substrate,
        }
        if include_digest:
            payload["identity_digest"] = self.identity_digest
        return payload


def detect_host_context(
    env: Mapping[str, str] | None = None,
    *,
    system: str | None = None,
    machine: str | None = None,
    platform_name: str | None = None,
    uid: int | None = None,
    euid: int | None = None,
) -> HostContext:
    values = os.environ if env is None else env

    # An explicitly supplied environment mapping is a model of the target
    # process environment, not permission to leak the caller's host OS into
    # that model.  This matters on Termux/Android: tests and delegated-provider
    # planning routinely pass a sparse Linux environment such as
    # {"HOME": "/home/mike"}.  Inheriting platform.system() from the parent
    # Android process would incorrectly turn that synthetic Linux host into
    # ``host-android``.
    #
    # Real calls (env is None / os.environ) still use the actual platform.
    # Explicit mappings may carry portable OS hints; otherwise a POSIX-style
    # sparse mapping retains Devtool's historical desktop-Linux default.
    if system is None and env is not None and env is not os.environ:
        host_hint = str(
            values.get("DEVTOOL_HOST_OS")
            or values.get("DEVTOOL_HOST_OS_FAMILY")
            or values.get("OS")
            or values.get("OSTYPE")
            or ""
        ).strip().lower()
        if host_hint in {"windows", "windows_nt", "win32"} or values.get("SystemRoot") or values.get("ComSpec"):
            system_raw = "Windows"
        elif host_hint.startswith(("darwin", "mac", "osx")):
            system_raw = "Darwin"
        elif host_hint in {"android"}:
            system_raw = "Android"
        else:
            system_raw = "Linux"
    else:
        system_raw = str(system if system is not None else platform.system()).strip() or "unknown"
    machine_raw = str(machine if machine is not None else platform.machine()).strip() or "unknown"
    os_family = _system_name(system=system_raw, platform_name=platform_name)

    execution_context = str(values.get("DEVTOOL_EXECUTION_CONTEXT", "")).strip().lower()
    inside_chroot = execution_context in _CHROOT_CONTEXT_VALUES or _truthy(values.get("DEVTOOL_INSIDE_TERMUX_CHROOT"))
    prefix = str(values.get("PREFIX", ""))
    home = str(values.get("HOME", ""))
    termux_markers = bool(
        values.get("TERMUX_VERSION")
        or prefix == _TERMUX_PREFIX_PREFIX
        or prefix.startswith(_TERMUX_PREFIX_PREFIX + "/")
        or home == _TERMUX_HOME_PREFIX
        or home.startswith(_TERMUX_HOME_PREFIX + "/")
    )
    # A chroot entered from Termux deliberately stops looking like native
    # Termux for substrate selection, but the host relationship remains useful.
    termux_host = termux_markers and not inside_chroot
    if termux_markers and os_family == "linux" and not inside_chroot:
        # Python inside Termux reports Linux even though the host OS is Android.
        os_family = "android"

    remote = _truthy(values.get("DEVTOOL_INSIDE_SSH")) or bool(values.get("SSH_CONNECTION"))
    remote_provider = str(values.get("DEVTOOL_EXECUTION_PROVIDER") or values.get("DEVTOOL_SSH_EXECUTION_PROVIDER") or "").strip()

    if uid is None and hasattr(os, "getuid"):
        try:
            uid = int(os.getuid())
        except OSError:
            uid = None
    if euid is None and hasattr(os, "geteuid"):
        try:
            euid = int(os.geteuid())
        except OSError:
            euid = None

    return HostContext(
        os_family=os_family,
        system=system_raw,
        architecture=machine_raw.lower(),
        machine=machine_raw,
        termux=termux_host,
        inside_chroot=inside_chroot,
        chroot_name=str(values.get("DEVTOOL_CHROOT_CONTEXT", "") or "").strip(),
        remote=remote,
        remote_provider=remote_provider,
        uid=uid,
        euid=euid,
    )
