from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .console_utils import is_termux_environment

CLIPBOARD_STARTUP_GRACE_SECONDS = 0.15
CLIPBOARD_COMMAND_TIMEOUT_SECONDS = float(os.environ.get("DEVTOOL_CLIPBOARD_TIMEOUT", "4.0"))


@dataclass(frozen=True)
class ClipboardResult:
    success: bool
    backend: str | None = None
    reason: str | None = None


def _socket_clipboard_path() -> Path | None:
    text = os.environ.get("DEVTOOL_CLIPBOARD_SOCKET", "").strip()
    return Path(text).expanduser() if text else None


def _fifo_clipboard_path() -> Path | None:
    text = os.environ.get("DEVTOOL_CLIPBOARD_FIFO", "").strip()
    return Path(text).expanduser() if text else None


def _copy_via_socket(text: str) -> ClipboardResult:
    path = _socket_clipboard_path()
    if path is None:
        return ClipboardResult(False, backend="socket", reason="DEVTOOL_CLIPBOARD_SOCKET is not set.")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(2.0)
            client.connect(str(path))
            client.sendall(text.encode("utf-8"))
        return ClipboardResult(True, backend="socket")
    except OSError as exc:
        return ClipboardResult(False, backend="socket", reason=str(exc))


def _copy_via_fifo(text: str) -> ClipboardResult:
    path = _fifo_clipboard_path()
    if path is None:
        return ClipboardResult(False, backend="fifo", reason="DEVTOOL_CLIPBOARD_FIFO is not set.")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
        try:
            os.write(fd, text.encode("utf-8"))
        finally:
            os.close(fd)
        return ClipboardResult(True, backend="fifo")
    except OSError as exc:
        return ClipboardResult(False, backend="fifo", reason=str(exc))


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
    # Termux and pbcopy should consume stdin and exit.  Use communicate() with a
    # timeout so devtool never appears to hang after setting the clipboard.
    if backend in {"termux-clipboard-set", "pbcopy"}:
        try:
            completed = subprocess.run(
                command,
                input=text,
                text=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=CLIPBOARD_COMMAND_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return ClipboardResult(False, backend=backend, reason=f"{backend} timed out after {CLIPBOARD_COMMAND_TIMEOUT_SECONDS:.1f}s")
        except OSError as exc:
            return ClipboardResult(False, backend=backend, reason=str(exc))
        if completed.returncode == 0:
            return ClipboardResult(True, backend=backend)
        return ClipboardResult(False, backend=backend, reason=f"{backend} exited with status {completed.returncode}")

    # X11/Wayland clipboard owners may intentionally stay alive.  Start them in
    # a new session, close stdin, and return promptly instead of waiting for the
    # long-lived owner process.
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
    if returncode is None:
        return ClipboardResult(True, backend=backend)
    if returncode == 0:
        return ClipboardResult(True, backend=backend)

    return ClipboardResult(
        False,
        backend=backend,
        reason=f"{backend} exited early with status {returncode}",
    )


def _can_use_termux_clipboard() -> bool:
    return is_termux_environment() and bool(shutil.which("termux-clipboard-set"))


def _candidate_backends(preferred: str = "auto") -> list[tuple[list[str], str]]:
    candidates: list[tuple[list[str], str]] = []

    include_termux = preferred in {"auto", "termux"}
    include_x11 = preferred in {"auto", "x11"}
    include_wayland = preferred in {"auto", "wayland"}

    if include_termux and _can_use_termux_clipboard():
        candidates.append((["termux-clipboard-set"], "termux-clipboard-set"))

    if include_x11 and _can_use_x11():
        if shutil.which("xclip"):
            candidates.append((["xclip", "-selection", "clipboard"], "xclip"))
        if shutil.which("xsel"):
            candidates.append((["xsel", "--clipboard", "--input"], "xsel"))

    if include_wayland and _can_use_wl_copy():
        candidates.append((["wl-copy"], "wl-copy"))

    if preferred in {"auto", "x11", "wayland"} and shutil.which("pbcopy"):
        candidates.append((["pbcopy"], "pbcopy"))

    return candidates


def copy_text(text: str, preferred: str = "auto") -> ClipboardResult:
    last_failure: ClipboardResult | None = None

    if preferred in {"auto", "socket"} and _socket_clipboard_path() is not None:
        result = _copy_via_socket(text)
        if result.success or preferred == "socket":
            return result
        last_failure = result

    if preferred in {"auto", "fifo"} and _fifo_clipboard_path() is not None:
        result = _copy_via_fifo(text)
        if result.success or preferred == "fifo":
            return result
        last_failure = result

    for command, backend in _candidate_backends(preferred):
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
