from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .console_utils import is_termux_environment

CLIPBOARD_STARTUP_GRACE_SECONDS = 0.15


@dataclass(frozen=True)
class ClipboardResult:
    success: bool
    backend: str | None = None
    reason: str | None = None


def _wayland_socket_path() -> Path | None:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime_dir:
        return None

    display = os.environ.get("WAYLAND_DISPLAY") or "wayland-0"
    return Path(runtime_dir) / display


def _can_use_wl_copy() -> bool:
    if not shutil.which("wl-copy"):
        return False

    socket_path = _wayland_socket_path()
    return socket_path is not None and socket_path.exists()


def _can_use_x11() -> bool:
    return bool(os.environ.get("DISPLAY"))


def _run_clipboard_command(command: list[str], text: str, backend: str) -> ClipboardResult:
    try:
        process = subprocess.Popen(
            command,
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        return ClipboardResult(False, backend=backend, reason=str(exc))

    try:
        assert process.stdin is not None
        process.stdin.write(text)
        process.stdin.close()
    except OSError as exc:
        try:
            process.kill()
        except OSError:
            pass
        return ClipboardResult(False, backend=backend, reason=str(exc))

    time.sleep(CLIPBOARD_STARTUP_GRACE_SECONDS)
    returncode = process.poll()
    if returncode is None or returncode == 0:
        return ClipboardResult(True, backend=backend)

    return ClipboardResult(
        False,
        backend=backend,
        reason=f"{backend} exited early with status {returncode}",
    )


def _can_use_termux_clipboard() -> bool:
    return is_termux_environment() and bool(shutil.which("termux-clipboard-set"))


def _candidate_backends() -> list[tuple[list[str], str]]:
    candidates: list[tuple[list[str], str]] = []

    if _can_use_termux_clipboard():
        candidates.append((["termux-clipboard-set"], "termux-clipboard-set"))

    if _can_use_x11():
        if shutil.which("xclip"):
            candidates.append((["xclip", "-selection", "clipboard"], "xclip"))
        if shutil.which("xsel"):
            candidates.append((["xsel", "--clipboard", "--input"], "xsel"))

    if _can_use_wl_copy():
        candidates.append((["wl-copy"], "wl-copy"))

    if shutil.which("pbcopy"):
        candidates.append((["pbcopy"], "pbcopy"))

    return candidates


def copy_text(text: str) -> ClipboardResult:
    last_failure: ClipboardResult | None = None

    for command, backend in _candidate_backends():
        result = _run_clipboard_command(command, text, backend=backend)
        if result.success:
            return result
        last_failure = result

    socket_path = _wayland_socket_path()
    if shutil.which("wl-copy") and (socket_path is None or not socket_path.exists()):
        display_name = os.environ.get("WAYLAND_DISPLAY") or "wayland-0"
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR", "<unset>")
        return ClipboardResult(
            False,
            backend="wl-copy",
            reason=(
                "Wayland clipboard unavailable: "
                f"expected socket {runtime_dir}/{display_name} was not found."
            ),
        )

    if _can_use_x11() and not (shutil.which("xclip") or shutil.which("xsel")):
        return ClipboardResult(
            False,
            reason="X11 display detected but neither xclip nor xsel is installed.",
        )


    if is_termux_environment() and not shutil.which("termux-clipboard-set"):
        return ClipboardResult(
            False,
            reason="Termux environment detected but termux-clipboard-set is not installed.",
        )

    if last_failure is not None:
        return last_failure

    return ClipboardResult(
        False,
        reason="No supported clipboard backend is available.",
    )
