"""Canonical Android device inventory, selection, and transport diagnostics."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import os
import re
import subprocess
from typing import Any, Mapping

from .capabilities import AndroidToolResolution, probe_termux_usb

DEVICE_POLICIES = {"exactly-one", "serial", "preferred", "first-physical", "first-emulator", "all"}
KNOWN_STATES = {"device", "offline", "unauthorized", "recovery", "sideload", "bootloader", "host", "no", "unknown"}


class AndroidDeviceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AndroidDevice:
    serial: str
    state: str
    product: str = ""
    model: str = ""
    device: str = ""
    transport_id: str = ""
    usb: str = ""
    connection_type: str = "unknown"
    emulator: bool = False
    physical: bool = True
    api_level: str = ""
    abi: str = ""
    manufacturer: str = ""
    battery_level: str = ""
    screen_state: str = ""
    boot_completed: str = ""
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def usable(self) -> bool:
        return self.state == "device"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AndroidDeviceDoctor:
    status: str
    code: str
    detail: str
    provider: str
    adb_path: str
    devices: tuple[AndroidDevice, ...] = ()
    termux_usb: dict[str, Any] = field(default_factory=dict)

    @property
    def healthy(self) -> bool:
        return self.status == "ok"

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status, "code": self.code, "detail": self.detail,
            "provider": self.provider, "adb_path": self.adb_path,
            "healthy": self.healthy,
            "devices": [item.as_dict() for item in self.devices],
            "termux_usb": self.termux_usb,
        }


def parse_adb_devices(output: str) -> list[AndroidDevice]:
    devices: list[AndroidDevice] = []
    for raw in str(output or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("List of devices attached") or line.startswith("* daemon"):
            continue
        if line.lower().startswith(("adb:", "error:")):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1].lower()
        if state not in KNOWN_STATES:
            state = "unknown"
        metadata: dict[str, str] = {}
        for token in parts[2:]:
            if ":" in token:
                key, value = token.split(":", 1)
                metadata[key] = value
        emulator = serial.startswith("emulator-") or metadata.get("product", "").startswith("sdk") or "emulator" in metadata.get("model", "").lower()
        if emulator:
            connection = "emulator"
        elif metadata.get("usb"):
            connection = "usb"
        elif re.match(r"^[^:]+:\d+$", serial):
            connection = "tcp"
        else:
            connection = "unknown"
        devices.append(AndroidDevice(
            serial=serial, state=state,
            product=metadata.get("product", ""), model=metadata.get("model", "").replace("_", " "),
            device=metadata.get("device", ""), transport_id=metadata.get("transport_id", ""),
            usb=metadata.get("usb", ""), connection_type=connection,
            emulator=emulator, physical=not emulator, metadata=metadata,
        ))
    return devices


class AndroidDeviceManager:
    def __init__(self, adb: AndroidToolResolution, *, env: Mapping[str, str] | None = None) -> None:
        self.adb = adb
        self.env = dict(os.environ if env is None else env)

    def _run(self, *args: str, timeout: float = 8.0) -> tuple[int, str]:
        if not self.adb.path or self.adb.compatible is False:
            return 127, self.adb.rejection_reason or "ADB unavailable"
        try:
            proc = subprocess.run([self.adb.path, *args], text=True, capture_output=True, check=False, timeout=timeout, env=self.env)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return 127, str(exc)
        return proc.returncode, (proc.stdout + "\n" + proc.stderr).strip()

    def command_prefix(self, serial: str = "") -> list[str]:
        if not self.adb.path:
            raise AndroidDeviceError(self.adb.rejection_reason or "ADB unavailable")
        prefix = [self.adb.path]
        if serial:
            prefix += ["-s", serial]
        return prefix

    def list_devices(self, *, enrich: bool = False) -> list[AndroidDevice]:
        rc, text = self._run("devices", "-l")
        if rc != 0:
            raise AndroidDeviceError(text or f"ADB devices probe failed with exit {rc}")
        devices = parse_adb_devices(text)
        if not enrich:
            return devices
        return [self._enrich(item) if item.usable else item for item in devices]

    def _shell_value(self, serial: str, *argv: str) -> str:
        rc, text = self._run("-s", serial, "shell", *argv, timeout=5)
        return text.splitlines()[0].strip() if rc == 0 and text.strip() else ""

    def _enrich(self, item: AndroidDevice) -> AndroidDevice:
        api = self._shell_value(item.serial, "getprop", "ro.build.version.sdk")
        abi = self._shell_value(item.serial, "getprop", "ro.product.cpu.abi")
        manufacturer = self._shell_value(item.serial, "getprop", "ro.product.manufacturer")
        boot = self._shell_value(item.serial, "getprop", "sys.boot_completed")
        rc, battery = self._run("-s", item.serial, "shell", "dumpsys", "battery", timeout=5)
        battery_match = re.search(r"level:\s*(\d+)", battery) if rc == 0 else None
        rc, power = self._run("-s", item.serial, "shell", "dumpsys", "power", timeout=5)
        screen = ""
        if rc == 0:
            match = re.search(r"mWakefulness=(\w+)", power)
            if match:
                screen = match.group(1).lower()
        data = item.as_dict()
        data.update(api_level=api, abi=abi, manufacturer=manufacturer, battery_level=battery_match.group(1) if battery_match else "", screen_state=screen, boot_completed=boot)
        return AndroidDevice(**data)

    def select(
        self,
        policy: str = "exactly-one",
        *,
        serial: str = "",
        preferred: str = "",
        include_unusable: bool = False,
    ) -> list[AndroidDevice]:
        policy = str(policy or "exactly-one").strip().lower()
        if policy not in DEVICE_POLICIES:
            raise AndroidDeviceError(f"Unsupported device policy: {policy}. Expected one of: {', '.join(sorted(DEVICE_POLICIES))}")
        devices = self.list_devices()
        candidates = devices if include_unusable else [item for item in devices if item.usable]
        effective_serial = str(serial or self.env.get("ANDROID_SERIAL") or "").strip()
        if policy == "serial" or effective_serial:
            wanted = effective_serial or serial
            matches = [item for item in devices if item.serial == wanted or item.transport_id == wanted]
            if not matches:
                raise AndroidDeviceError(f"Requested Android device {wanted!r} is not connected")
            if not include_unusable and not matches[0].usable:
                raise AndroidDeviceError(f"Requested Android device {wanted!r} is {matches[0].state}")
            return matches[:1]
        if policy == "all":
            return candidates
        if policy == "preferred":
            wanted_preferred = str(preferred or "").strip()
            if not wanted_preferred:
                raise AndroidDeviceError("Device policy 'preferred' requires android.adb.preferred or --preferred")
            matches = [item for item in candidates if item.serial == wanted_preferred or item.model == wanted_preferred or item.device == wanted_preferred]
            if matches:
                return matches[:1]
            raise AndroidDeviceError(f"Preferred Android device {wanted_preferred!r} is not connected or usable")
        if policy == "first-physical":
            matches = [item for item in candidates if item.physical]
            if not matches:
                raise AndroidDeviceError("No usable physical Android device is connected")
            return matches[:1]
        if policy == "first-emulator":
            matches = [item for item in candidates if item.emulator]
            if not matches:
                raise AndroidDeviceError("No usable Android emulator is connected")
            return matches[:1]
        if len(candidates) == 1:
            return candidates
        if not candidates:
            raise AndroidDeviceError("No usable Android device is connected")
        raise AndroidDeviceError("Multiple usable Android devices are connected; select a serial or deterministic device policy")

    def doctor(self) -> AndroidDeviceDoctor:
        if not self.adb.available:
            code = "termux-adb-not-installed" if self.adb.provider == "termux-adb" else "adb-not-installed"
            return AndroidDeviceDoctor("error", code, self.adb.rejection_reason or "ADB executable is unavailable", self.adb.provider, self.adb.path)
        termux_usb: dict[str, Any] = {}
        if self.adb.provider == "termux-adb":
            termux_usb = dict(self.adb.metadata.get("termux_usb") or probe_termux_usb(env=self.env))
            if termux_usb.get("termux_usb") == "":
                return AndroidDeviceDoctor("error", "termux-usb-unavailable", str(termux_usb.get("detail") or "termux-usb unavailable"), self.adb.provider, self.adb.path, termux_usb=termux_usb)
            if termux_usb.get("termux_api_app") is False:
                return AndroidDeviceDoctor("error", "termux-api-missing", str(termux_usb.get("api_detail") or "Termux:API app missing"), self.adb.provider, self.adb.path, termux_usb=termux_usb)
            if termux_usb.get("list_status") != "ready":
                return AndroidDeviceDoctor("error", "termux-usb-failed", str(termux_usb.get("detail") or "termux-usb failed"), self.adb.provider, self.adb.path, termux_usb=termux_usb)
        if self.adb.compatible is False:
            reason = self.adb.rejection_reason or "ADB provider failed probes"
            lowered = reason.lower()
            if self.adb.provider == "termux-adb" and "permission" in lowered and ("denied" in lowered or "grant" in lowered or "not granted" in lowered):
                return AndroidDeviceDoctor("error", "usb-permission-not-granted", reason, self.adb.provider, self.adb.path, termux_usb=termux_usb)
            return AndroidDeviceDoctor("error", "adb-provider-unusable", reason, self.adb.provider, self.adb.path, termux_usb=termux_usb)
        try:
            devices = self.list_devices()
        except AndroidDeviceError as exc:
            return AndroidDeviceDoctor("error", "adb-daemon-unavailable", str(exc), self.adb.provider, self.adb.path, termux_usb=termux_usb)
        usable = [item for item in devices if item.usable]
        unauthorized = [item for item in devices if item.state == "unauthorized"]
        offline = [item for item in devices if item.state == "offline"]
        if usable:
            return AndroidDeviceDoctor("ok", "device-ready", f"{len(usable)} usable Android device(s)", self.adb.provider, self.adb.path, tuple(devices), termux_usb)
        if unauthorized:
            return AndroidDeviceDoctor("warning", "device-unauthorized", "Android device is visible but ADB authorization has not been granted", self.adb.provider, self.adb.path, tuple(devices), termux_usb)
        if offline:
            return AndroidDeviceDoctor("warning", "device-offline", "Android device is visible but offline", self.adb.provider, self.adb.path, tuple(devices), termux_usb)
        if self.adb.provider == "termux-adb" and termux_usb.get("devices"):
            return AndroidDeviceDoctor("warning", "usb-visible-adb-missing", "USB device is visible to Termux:API but is not currently available through ADB; grant USB permission/ADB authorization and retry", self.adb.provider, self.adb.path, tuple(devices), termux_usb)
        if self.adb.provider == "termux-adb":
            detail = str(termux_usb.get("detail") or termux_usb.get("api_detail") or "")
            lowered = detail.lower()
            if "permission" in lowered and ("denied" in lowered or "grant" in lowered or "not granted" in lowered):
                return AndroidDeviceDoctor("error", "usb-permission-not-granted", detail or "Termux USB permission has not been granted", self.adb.provider, self.adb.path, tuple(devices), termux_usb)
        return AndroidDeviceDoctor("warning", "no-device", "ADB provider is healthy but no Android device is connected", self.adb.provider, self.adb.path, tuple(devices), termux_usb)


__all__ = [
    "AndroidDevice", "AndroidDeviceDoctor", "AndroidDeviceError", "AndroidDeviceManager",
    "DEVICE_POLICIES", "KNOWN_STATES", "parse_adb_devices",
]
