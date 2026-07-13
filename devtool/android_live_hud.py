from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


PACKAGE = "io.github.mikeceead.devtoolhud"
COMPONENT = f"{PACKAGE}/.BridgeReceiver"
ACTION_PREFIX = PACKAGE
DEFAULT_TOKEN_FILE = Path.home() / ".config" / "devtool" / "android-live-hud.token"


class AndroidLiveHudBridge:
    """Send devtool HUD updates to the native Android companion app."""

    def __init__(self, *, token_file: Path | None = None) -> None:
        override = os.environ.get("DEVTOOL_ANDROID_HUD_TOKEN_FILE", "").strip()
        self.token_file = Path(override).expanduser() if override else (token_file or DEFAULT_TOKEN_FILE)

    @staticmethod
    def _am_binary() -> str | None:
        override = os.environ.get("DEVTOOL_ANDROID_AM", "").strip()
        if override:
            return override
        return shutil.which("am") or ("/system/bin/am" if Path("/system/bin/am").exists() else None)

    def token(self) -> str:
        inline = os.environ.get("DEVTOOL_ANDROID_HUD_TOKEN", "").strip()
        if inline:
            return inline
        try:
            return self.token_file.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    @staticmethod
    def _package_installed() -> bool:
        if os.environ.get("DEVTOOL_ANDROID_HUD_ASSUME_INSTALLED", "").strip().lower() in {"1", "true", "yes", "on"}:
            return True
        candidates = [shutil.which("pm"), "/system/bin/pm"]
        checked = False
        for candidate in candidates:
            if not candidate or not Path(candidate).exists():
                continue
            checked = True
            try:
                result = subprocess.run(
                    [candidate, "path", PACKAGE],
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
        # On some Termux setups pm is inaccessible. A configured token is a
        # deliberate opt-in, so allow the broadcast attempt in that case.
        return not checked

    def available(self) -> bool:
        return bool(self._am_binary() and self.token() and self._package_installed())

    @staticmethod
    def install_hint() -> str:
        return (
            "Install Devtool Live HUD, open it once, and save its token to "
            "~/.config/devtool/android-live-hud.token"
        )

    def send(self, action: str, payload: dict[str, Any]) -> bool:
        am = self._am_binary()
        token = self.token()
        if not am or not token:
            return False

        argv = [
            am,
            "broadcast",
            "--user",
            "0",
            "-n",
            COMPONENT,
            "-a",
            f"{ACTION_PREFIX}.{action.upper()}",
            "--es",
            "token",
            token,
        ]
        for key, value in payload.items():
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
        except (OSError, subprocess.SubprocessError):
            return False
        output = (result.stdout or "").lower()
        return result.returncode == 0 and "exception" not in output and "error:" not in output
