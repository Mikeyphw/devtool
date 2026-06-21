from __future__ import annotations

import platform
import shutil
import subprocess


def available_notifier() -> str | None:
    if shutil.which("notify-send"):
        return "notify-send"
    if shutil.which("terminal-notifier"):
        return "terminal-notifier"
    if platform.system() == "Darwin" and shutil.which("osascript"):
        return "osascript"
    return None


def send_notification(title: str, body: str) -> bool:
    notifier = available_notifier()
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
