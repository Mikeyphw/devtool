from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any


class DesktopIpcClient:
    """Thin desktop wrapper around Devtool's Unix-socket IPC broker."""

    def __init__(self, *, socket_path: Path | None = None, timeout_s: float = 0.8) -> None:
        self.socket_path = socket_path
        self.timeout_s = timeout_s

    def socket(self) -> Path:
        if self.socket_path is not None:
            return self.socket_path
        from ..ipc import default_ipc_socket_path

        return default_ipc_socket_path()

    def snapshot(self) -> dict[str, Any]:
        path = self.socket()
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(self.timeout_s)
                sock.connect(str(path))
                sock.sendall(b'{"type":"subscribe"}\n')
                data = sock.recv(1024 * 512)
        except OSError:
            return {"ok": False, "runs": [], "socket": str(path), "error": "ipc unavailable"}
        try:
            payload = json.loads(data.decode("utf-8", errors="replace").splitlines()[0])
        except (IndexError, json.JSONDecodeError):
            return {"ok": False, "runs": [], "socket": str(path), "error": "invalid ipc payload"}
        if isinstance(payload, dict):
            payload.setdefault("ok", True)
            payload.setdefault("socket", str(path))
            return payload
        return {"ok": False, "runs": [], "socket": str(path), "error": "unexpected ipc payload"}
