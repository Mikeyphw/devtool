from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PACKAGE = "io.github.mikeceead.devtoolhud"
DEBUG_PACKAGE = f"{PACKAGE}.debug"
RECEIVER_CLASS = f"{PACKAGE}.BridgeReceiver"
ACTION_PREFIX = PACKAGE
PROTOCOL_VERSION = 1
SUPPORTED_ACTIONS = frozenset({"update", "complete", "fail", "cancel", "toast"})
_ACK_ACCEPTED = frozenset({100, 110})
_ACK_REJECTED = {
    201: "invalid-token",
    202: "unknown-action",
    203: "missing-run-id",
    204: "unsupported-protocol",
}
_BROADCAST_RESULT_RE = re.compile(r"\bresult=(-?\d+)\b", re.IGNORECASE)
_BROADCAST_DATA_RE = re.compile(r"\bdata=\"([^\"]*)\"", re.IGNORECASE)
DEFAULT_TOKEN_FILE = Path.home() / ".config" / "devtool" / "android-live-hud.token"
DEFAULT_PACKAGE_FILE = Path.home() / ".config" / "devtool" / "android-live-hud.package"
DEFAULT_COMPONENT_FILE = Path.home() / ".config" / "devtool" / "android-live-hud.component"
DEFAULT_DIAGNOSTIC_FILE = Path.home() / ".local" / "state" / "devtool" / "android-hud-bridge-error.log"
_PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+$")


@dataclass(frozen=True)
class AndroidHudEndpoint:
    package: str
    component: str
    action_prefix: str


def _clean_package(value: str) -> str:
    clean = str(value or "").strip()
    return clean if _PACKAGE_RE.fullmatch(clean) else ""


def _component_package(value: str) -> str:
    clean = str(value or "").strip()
    if "/" not in clean:
        return ""
    return _clean_package(clean.split("/", 1)[0])


def _read_marker(path: Path) -> str:
    try:
        return path.expanduser().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _package_marker_file() -> Path:
    override = os.environ.get("DEVTOOL_ANDROID_HUD_PACKAGE_FILE", "").strip()
    return Path(override).expanduser() if override else DEFAULT_PACKAGE_FILE


def _component_marker_file() -> Path:
    override = os.environ.get("DEVTOOL_ANDROID_HUD_COMPONENT_FILE", "").strip()
    return Path(override).expanduser() if override else DEFAULT_COMPONENT_FILE


def configured_package() -> str:
    return _clean_package(os.environ.get("DEVTOOL_ANDROID_HUD_PACKAGE", ""))


def configured_component() -> str:
    return os.environ.get("DEVTOOL_ANDROID_HUD_COMPONENT", "").strip()


def configured_action_prefix() -> str:
    return _clean_package(os.environ.get("DEVTOOL_ANDROID_HUD_ACTION_PREFIX", ""))


def active_package() -> str:
    """Return the preferred package without repeatedly probing Android.

    Explicit environment values win, followed by the marker written by the
    companion app. The debug package is the fallback because the Termux
    installer builds and installs the debug APK by default.
    """

    explicit_component_package = _component_package(configured_component())
    if explicit_component_package:
        return explicit_component_package
    explicit = configured_package()
    if explicit:
        return explicit
    marked = _clean_package(_read_marker(_package_marker_file()))
    return marked or DEBUG_PACKAGE


def active_component() -> str:
    explicit = configured_component()
    if explicit:
        return explicit
    marked = _read_marker(_component_marker_file())
    if marked and _component_package(marked):
        return marked
    # The receiver's Java class remains in the release namespace even when
    # applicationIdSuffix changes the installed package to *.debug.
    return f"{active_package()}/{RECEIVER_CLASS}"


class AndroidLiveHudBridge:
    """Send Devtool HUD updates to the native Android companion app."""

    def __init__(self, *, token_file: Path | None = None) -> None:
        override = os.environ.get("DEVTOOL_ANDROID_HUD_TOKEN_FILE", "").strip()
        self.token_file = Path(override).expanduser() if override else (token_file or DEFAULT_TOKEN_FILE)
        diagnostic_override = os.environ.get("DEVTOOL_ANDROID_HUD_DIAGNOSTIC_FILE", "").strip()
        self.diagnostic_file = Path(diagnostic_override).expanduser() if diagnostic_override else DEFAULT_DIAGNOSTIC_FILE
        self._endpoint: AndroidHudEndpoint | None = None
        self.last_error = ""
        self._last_reported_error = ""
        self._last_reported_at = 0.0

    @staticmethod
    def _am_binary() -> str | None:
        override = os.environ.get("DEVTOOL_ANDROID_AM", "").strip()
        if override:
            return override
        return shutil.which("am") or ("/system/bin/am" if Path("/system/bin/am").exists() else None)

    @staticmethod
    def _package_commands(package: str) -> list[list[str]]:
        commands: list[list[str]] = []
        pm_override = os.environ.get("DEVTOOL_ANDROID_PM", "").strip()
        pm = pm_override or shutil.which("pm") or ("/system/bin/pm" if Path("/system/bin/pm").exists() else "")
        if pm:
            commands.append([pm, "path", package])
        cmd = shutil.which("cmd") or ("/system/bin/cmd" if Path("/system/bin/cmd").exists() else "")
        if cmd:
            commands.append([cmd, "package", "path", package])

        # Package-manager visibility can be restricted for the Termux UID on
        # recent Android releases. Root is available in the supported Termux
        # workflow, so use it as a final read-only discovery fallback.
        su = shutil.which("su")
        if su and os.environ.get("DEVTOOL_ANDROID_HUD_NO_ROOT_DISCOVERY", "").strip().lower() not in {
            "1",
            "true",
            "yes",
            "on",
        }:
            quoted = shlex.quote(package)
            commands.append([su, "-c", f"/system/bin/pm path {quoted}"])
            commands.append([su, "-c", f"/system/bin/cmd package path {quoted}"])
        return commands

    def token(self) -> str:
        inline = os.environ.get("DEVTOOL_ANDROID_HUD_TOKEN", "").strip()
        if inline:
            return inline
        try:
            return self.token_file.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    @staticmethod
    def _package_installed(package: str | None = None) -> bool:
        if os.environ.get("DEVTOOL_ANDROID_HUD_ASSUME_INSTALLED", "").strip().lower() in {"1", "true", "yes", "on"}:
            return True
        selected = _clean_package(package or active_package())
        if not selected:
            return False
        commands = AndroidLiveHudBridge._package_commands(selected)
        checked = False
        for argv in commands:
            checked = True
            try:
                result = subprocess.run(
                    argv,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    timeout=2,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                continue
            if result.returncode == 0 and "package:" in (result.stdout or ""):
                return True
        # A token/package marker is a deliberate pairing. Some Android builds
        # deny package-manager queries to Termux, so allow a broadcast attempt
        # when no package command could be executed.
        return not checked

    @staticmethod
    def _candidate_packages() -> list[str]:
        values = [
            _component_package(configured_component()),
            configured_package(),
            _clean_package(_read_marker(_package_marker_file())),
            DEBUG_PACKAGE,
            PACKAGE,
        ]
        result: list[str] = []
        for value in values:
            if value and value not in result:
                result.append(value)
        return result

    def _resolve_endpoint(self, *, force: bool = False) -> AndroidHudEndpoint | None:
        if self._endpoint is not None and not force:
            return self._endpoint

        action_prefix_override = configured_action_prefix()
        explicit_component = configured_component()
        if explicit_component:
            package = _component_package(explicit_component)
            if not package:
                self._set_error(
                    "DEVTOOL_ANDROID_HUD_COMPONENT must use Android's package/class form, "
                    "for example io.github.mikeceead.devtoolhud.debug/"
                    "io.github.mikeceead.devtoolhud.BridgeReceiver"
                )
                return None
            self._endpoint = AndroidHudEndpoint(package, explicit_component, action_prefix_override or package)
            return self._endpoint

        marked_component = _read_marker(_component_marker_file())
        marked_package = _component_package(marked_component)
        if marked_package and self._package_installed(marked_package):
            self._endpoint = AndroidHudEndpoint(
                marked_package, marked_component, action_prefix_override or marked_package
            )
            return self._endpoint

        for package in self._candidate_packages():
            if self._package_installed(package):
                self._endpoint = AndroidHudEndpoint(
                    package, f"{package}/{RECEIVER_CLASS}", action_prefix_override or package
                )
                return self._endpoint

        self._set_error(
            "Devtool Live HUD companion package was not found. Checked "
            + ", ".join(self._candidate_packages())
            + "."
        )
        return None

    def endpoint(self) -> AndroidHudEndpoint | None:
        return self._resolve_endpoint()

    def _set_error(self, message: str) -> None:
        clean = " ".join(str(message or "").split())
        if not clean:
            return
        self.last_error = clean
        now = time.monotonic()
        if clean == self._last_reported_error and now - self._last_reported_at < 30.0:
            return
        self._last_reported_error = clean
        self._last_reported_at = now
        line = f"Devtool Android HUD bridge: {clean}"
        try:
            self.diagnostic_file.parent.mkdir(parents=True, exist_ok=True)
            with self.diagnostic_file.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            pass

    def available(self) -> bool:
        if not self._am_binary():
            self._set_error("Android activity manager command 'am' is unavailable.")
            return False
        if not self.token():
            self._set_error(
                "Bridge token is missing. Open Devtool Live HUD and install or update the Termux bridge token."
            )
            return False
        return self._resolve_endpoint() is not None

    @staticmethod
    def install_hint() -> str:
        return (
            "Install Devtool Live HUD, open it once, and install its Termux bridge token. "
            "Devtool auto-detects io.github.mikeceead.devtoolhud.debug and "
            "io.github.mikeceead.devtoolhud."
        )

    @staticmethod
    def _broadcast_outcome(result: subprocess.CompletedProcess[str]) -> tuple[bool, str]:
        output = " ".join((result.stdout or "").strip().split())
        lowered = output.lower()
        failure_markers = (
            "exception",
            "error:",
            "unable to resolve intent",
            "bad component name",
            "not found",
            "securityexception",
        )
        if result.returncode != 0 or any(marker in lowered for marker in failure_markers):
            return False, output or f"am broadcast exited with status {result.returncode}"

        result_match = _BROADCAST_RESULT_RE.search(output)
        result_code = int(result_match.group(1)) if result_match else None
        data_match = _BROADCAST_DATA_RE.search(output)
        result_data = data_match.group(1) if data_match else ""
        if result_code in _ACK_ACCEPTED:
            return True, result_data
        if result_code in _ACK_REJECTED:
            detail = result_data or _ACK_REJECTED[result_code]
            return False, f"companion rejected broadcast: {detail}"
        if result_code not in {None, 0}:
            return False, f"companion returned unknown result code {result_code}"

        # Older companion releases did not acknowledge ordered broadcasts. Keep
        # them compatible while newer releases provide an authoritative result.
        return True, result_data

    def _send_to_endpoint(
        self,
        *,
        am: str,
        endpoint: AndroidHudEndpoint,
        action: str,
        token: str,
        payload: dict[str, Any],
    ) -> tuple[bool, str]:
        argv = [
            am,
            "broadcast",
            "--user",
            "0",
            "-f",
            "0x20",
            "-n",
            endpoint.component,
            "-a",
            f"{endpoint.action_prefix}.{action.upper()}",
            "--es",
            "token",
            token,
        ]
        wire_payload = dict(payload)
        wire_payload["protocol_version"] = PROTOCOL_VERSION
        for key, value in wire_payload.items():
            if value is None:
                continue
            key = str(key)
            if isinstance(value, bool):
                argv += ["--ez", key, "true" if value else "false"]
            elif isinstance(value, int):
                flag = "--el" if key.endswith("_ms") or key in {"sequence"} else "--ei"
                argv += [flag, key, str(value)]
            else:
                argv += ["--es", key, str(value)]
        try:
            result = subprocess.run(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return False, str(exc)
        return self._broadcast_outcome(result)

    def send(self, action: str, payload: dict[str, Any]) -> bool:
        normalized_action = str(action or "").strip().lower()
        if normalized_action not in SUPPORTED_ACTIONS:
            self._set_error(
                f"Unsupported Android HUD action '{normalized_action or action}'. "
                f"Supported actions: {', '.join(sorted(SUPPORTED_ACTIONS))}."
            )
            return False

        am = self._am_binary()
        token = self.token()
        if not am:
            self._set_error("Android activity manager command 'am' is unavailable.")
            return False
        if not token:
            self._set_error(
                "Bridge token is missing. Open Devtool Live HUD and install or update the Termux bridge token."
            )
            return False

        endpoint = self._resolve_endpoint()
        if endpoint is None:
            return False
        ok, detail = self._send_to_endpoint(
            am=am, endpoint=endpoint, action=normalized_action, token=token, payload=payload
        )
        if ok:
            self.last_error = ""
            return True

        # The app may have been replaced by the other build variant while the
        # detached HUD process was alive. Refresh package discovery and retry
        # once when resolution selects a different endpoint.
        previous = endpoint
        self._endpoint = None
        refreshed = self._resolve_endpoint(force=True)
        if refreshed is not None and refreshed != previous:
            ok, retry_detail = self._send_to_endpoint(
                am=am, endpoint=refreshed, action=normalized_action, token=token, payload=payload
            )
            if ok:
                self.last_error = ""
                return True
            detail = retry_detail or detail

        self._set_error(
            f"broadcast to {previous.component} failed"
            + (f": {detail}" if detail else ".")
            + " Set DEVTOOL_ANDROID_HUD_COMPONENT to override auto-detection."
        )
        return False
