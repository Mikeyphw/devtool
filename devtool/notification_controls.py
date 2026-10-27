from __future__ import annotations

"""User-global controls for Devtool-generated OS notifications and toasts.

This does not disable the Android companion's own independently managed
progress notifications, nor event persistence or application HUD updates.
"""

import os
import subprocess
from typing import Mapping

from .config import load_global_config

_TRUTHY = {"1", "true", "on", "yes", "enabled"}
_FALSEY = {"0", "false", "off", "no", "disabled"}


def _mode(value: object) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    text = str(value or "auto").strip().lower()
    if text in _TRUTHY:
        return "on"
    if text in _FALSEY:
        return "off"
    return text if text in {"auto", "on", "off"} else "off"  # Invalid env is fail-silent.


def output_mode(kind: str, *, env: Mapping[str, str] | None = None) -> str:
    """Resolve off/on/auto; explicit env overrides user-global TOML.

    Repository .devtool.toml cannot reactivate globally silenced OS output.
    """
    if kind not in {"notifications", "toasts"}:
        raise ValueError(f"Unknown OS output kind: {kind}")
    environment = os.environ if env is None else env
    key = "DEVTOOL_NOTIFICATIONS" if kind == "notifications" else "DEVTOOL_TOASTS"
    explicit = str(environment.get(key, "")).strip()
    if explicit:
        return _mode(explicit)
    try:
        # Respect the exact global-config override without repository influence.
        config, _ = load_global_config()
        cli = config.get("cli", {})
        notifications = cli.get("notifications", {}) if isinstance(cli, dict) else {}
        value = notifications.get("native_" + kind, "auto") if isinstance(notifications, dict) else "auto"
        return _mode(value)
    except (OSError, ValueError, SystemExit):
        # Notification failure must not turn a successful apply into failure.
        return "off"


def companion_installed() -> bool:
    """Positive, privilege-passive Android package-manager proof only.

    A token, marker or unqueryable package is not evidence of installation.
    Avoid Android root prompts and do not use the bridge's optimistic fallback.
    """
    from .android_live_hud import AndroidLiveHudBridge, DEBUG_PACKAGE, PACKAGE, configured_package
    candidates = list(dict.fromkeys(filter(None, (configured_package(), DEBUG_PACKAGE, PACKAGE))))
    for package in candidates:
        for command in AndroidLiveHudBridge._package_commands(package):
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=1.0, check=False)
            except (OSError, subprocess.SubprocessError):
                continue
            if result.returncode == 0 and any(line.startswith("package:") for line in (result.stdout or "").splitlines()):
                return True
    return False


def os_output_enabled(kind: str, *, android: bool, app_detected: bool | None = None) -> bool:
    mode = output_mode(kind)
    if mode == "off":
        return False
    if mode == "on":
        return True
    if android:
        return not (companion_installed() if app_detected is None else app_detected)
    return True
