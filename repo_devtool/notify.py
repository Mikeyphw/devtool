from __future__ import annotations

import os
import platform
import shutil
import subprocess

from .console_utils import is_termux_environment


def _termux_gui_available() -> bool:
    return any(
        os.environ.get(name)
        for name in ("DISPLAY", "WAYLAND_DISPLAY", "TERMUX_X11")
    )


def available_notifier() -> str | None:
    if is_termux_environment():
        if shutil.which("termux-notification"):
            return "termux-notification"
        if _termux_gui_available() and shutil.which("notify-send"):
            return "notify-send"
        return None

    if shutil.which("notify-send"):
        return "notify-send"
    if shutil.which("terminal-notifier"):
        return "terminal-notifier"
    if platform.system() == "Darwin" and shutil.which("osascript"):
        return "osascript"
    return None


def send_notification(title: str, body: str) -> bool:
    notifier = available_notifier()
    if notifier == "termux-notification":
        subprocess.run(
            ["termux-notification", "--title", title, "--content", body],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    if notifier == "notify-send":
        subprocess.run(["notify-send", title, body], check=False)
        return True
    if notifier == "terminal-notifier":
        subprocess.run(["terminal-notifier", "-title", title, "-message", body], check=False)
        return True
    if notifier == "osascript":
        subprocess.run(["osascript", "-e", f'display notification "{body}" with title "{title}"'], check=False)
        return True
    return False
