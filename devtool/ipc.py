from __future__ import annotations

import argparse
import contextlib
import json
import os
import selectors
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .xdg import state_dir


def default_ipc_socket_path() -> Path:
    override = os.environ.get("DEVTOOL_IPC_SOCKET", "").strip()
    if override:
        return Path(override).expanduser()
    runtime = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if runtime:
        return Path(runtime).expanduser() / "devtool" / "control-center.sock"
    return state_dir() / "control-center.sock"


def _json_line(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _terminal_status(state: dict[str, Any]) -> bool:
    return str(state.get("status") or "").lower() in {"ok", "failed", "cancelled", "interrupted", "skipped"}


def _state_sequence(state: dict[str, Any]) -> int:
    try:
        sequence = int(state.get("sequence") or 0)
    except (TypeError, ValueError):
        sequence = 0
    if sequence > 0:
        return sequence
    event_id = str(state.get("event_id") or "")
    try:
        return int(event_id.rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return 0


def _state_is_newer(candidate: dict[str, Any], current: dict[str, Any] | None) -> bool:
    if not current:
        return True
    candidate_sequence = _state_sequence(candidate)
    current_sequence = _state_sequence(current)
    if candidate_sequence and current_sequence and candidate_sequence != current_sequence:
        return candidate_sequence > current_sequence
    candidate_updated = str(candidate.get("updated_at") or "")
    current_updated = str(current.get("updated_at") or "")
    if candidate_updated and current_updated:
        return candidate_updated >= current_updated
    return True


@dataclass(eq=False)
class _Peer:
    sock: socket.socket
    address: str = ""
    buffer: bytearray = field(default_factory=bytearray)
    role: str = ""
    run_id: str = ""
    subscribed: bool = False
    write_lock: threading.Lock = field(default_factory=threading.Lock)

    def send(self, payload: dict[str, Any]) -> bool:
        try:
            with self.write_lock:
                self.sock.sendall(_json_line(payload))
            return True
        except OSError:
            return False


class IpcBroker:
    """Small newline-delimited JSON broker for local Control Center clients."""

    def __init__(self, socket_path: Path | None = None, *, state_dir: Path | None = None) -> None:
        self.socket_path = (socket_path or default_ipc_socket_path()).expanduser()
        self.state_dir = state_dir.expanduser() if state_dir else None
        self.selector = selectors.DefaultSelector()
        self.server: socket.socket | None = None
        self.peers: set[_Peer] = set()
        self.producers: dict[str, _Peer] = {}
        self.runs: dict[str, dict[str, Any]] = {}
        self.stop_event = threading.Event()

    def _load_snapshot_files(self) -> None:
        if self.state_dir is None:
            return
        candidates: list[Path] = []
        runs = self.state_dir / "runs"
        if runs.is_dir():
            candidates.extend(sorted(runs.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True))
        candidates.append(self.state_dir / "state.json")
        for path in candidates:
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(value, dict) and value.get("run_id"):
                self.runs[str(value["run_id"])] = value

    def start(self) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists():
            try:
                probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                probe.settimeout(0.1)
                probe.connect(str(self.socket_path))
                probe.close()
                raise RuntimeError(f"IPC broker already active at {self.socket_path}")
            except (ConnectionRefusedError, FileNotFoundError, socket.timeout, OSError):
                with contextlib.suppress(OSError):
                    self.socket_path.unlink()
        self._load_snapshot_files()
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(self.socket_path))
        os.chmod(self.socket_path, 0o600)
        server.listen(32)
        server.setblocking(False)
        self.server = server
        self.selector.register(server, selectors.EVENT_READ, None)

    def close(self) -> None:
        self.stop_event.set()
        for peer in list(self.peers):
            self._drop(peer)
        if self.server is not None:
            with contextlib.suppress(Exception):
                self.selector.unregister(self.server)
            with contextlib.suppress(OSError):
                self.server.close()
            self.server = None
        with contextlib.suppress(OSError):
            self.socket_path.unlink()
        with contextlib.suppress(Exception):
            self.selector.close()

    def _accept(self) -> None:
        assert self.server is not None
        try:
            conn, _ = self.server.accept()
        except OSError:
            return
        conn.setblocking(False)
        peer = _Peer(conn)
        self.peers.add(peer)
        self.selector.register(conn, selectors.EVENT_READ, peer)

    def _drop(self, peer: _Peer) -> None:
        if peer not in self.peers:
            return
        self.peers.discard(peer)
        if peer.run_id and self.producers.get(peer.run_id) is peer:
            self.producers.pop(peer.run_id, None)
        with contextlib.suppress(Exception):
            self.selector.unregister(peer.sock)
        with contextlib.suppress(OSError):
            peer.sock.close()

    def _broadcast(self, payload: dict[str, Any]) -> None:
        for peer in list(self.peers):
            if peer.subscribed and not peer.send(payload):
                self._drop(peer)

    def _snapshot(self) -> list[dict[str, Any]]:
        return sorted(self.runs.values(), key=lambda value: str(value.get("updated_at") or ""), reverse=True)

    def _handle(self, peer: _Peer, message: dict[str, Any]) -> None:
        kind = str(message.get("type") or "").strip().lower()
        if kind in {"subscribe", "hello"}:
            peer.role = "consumer"
            peer.subscribed = True
            peer.send({"type": "snapshot", "runs": self._snapshot(), "socket": str(self.socket_path)})
            return
        if kind == "register":
            run_id = str(message.get("run_id") or "").strip()
            if not run_id:
                peer.send({"type": "error", "message": "register requires run_id"})
                return
            peer.role = "producer"
            peer.run_id = run_id
            self.producers[run_id] = peer
            state = message.get("state")
            if isinstance(state, dict) and _state_is_newer(state, self.runs.get(run_id)):
                self.runs[run_id] = state
                self._broadcast({"type": "run_update", "run": state})
            peer.send({"type": "registered", "run_id": run_id})
            return
        if kind in {"publish", "run_update"}:
            state = message.get("state") or message.get("run")
            if not isinstance(state, dict):
                return
            run_id = str(state.get("run_id") or peer.run_id or "").strip()
            if not run_id:
                return
            state["run_id"] = run_id
            if not _state_is_newer(state, self.runs.get(run_id)):
                return
            self.runs[run_id] = state
            self._broadcast({"type": "run_update", "run": state})
            return
        if kind == "unregister":
            run_id = str(message.get("run_id") or peer.run_id or "").strip()
            if run_id and self.producers.get(run_id) is peer:
                self.producers.pop(run_id, None)
            peer.send({"type": "unregistered", "run_id": run_id})
            return
        if kind == "cancel":
            run_id = str(message.get("run_id") or "").strip()
            producer = self.producers.get(run_id)
            accepted = bool(producer and producer.send({"type": "cancel", "run_id": run_id, "reason": str(message.get("reason") or "Control Center request")}))
            peer.send({"type": "cancel_ack", "run_id": run_id, "accepted": accepted})
            if accepted:
                self._broadcast({"type": "cancel_requested", "run_id": run_id})
            return
        if kind == "remove_run":
            run_id = str(message.get("run_id") or "").strip()
            self.runs.pop(run_id, None)
            self._broadcast({"type": "run_removed", "run_id": run_id})
            return
        if kind == "ping":
            peer.send({"type": "pong", "time": time.time()})

    def _read(self, peer: _Peer) -> None:
        try:
            data = peer.sock.recv(65536)
        except BlockingIOError:
            return
        except OSError:
            self._drop(peer)
            return
        if not data:
            self._drop(peer)
            return
        peer.buffer.extend(data)
        while b"\n" in peer.buffer:
            line, _, remainder = peer.buffer.partition(b"\n")
            peer.buffer = bytearray(remainder)
            if not line.strip():
                continue
            try:
                message = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                peer.send({"type": "error", "message": "invalid JSON message"})
                continue
            if isinstance(message, dict):
                self._handle(peer, message)

    def serve_forever(self) -> None:
        self.start()
        try:
            while not self.stop_event.is_set():
                for key, _ in self.selector.select(timeout=0.5):
                    if key.data is None:
                        self._accept()
                    else:
                        self._read(key.data)
        finally:
            self.close()


class IpcPublisher:
    """Persistent run producer with a cancellation listener."""

    def __init__(self, run_id: str, *, socket_path: Path | None = None, on_cancel: Callable[[], None] | None = None) -> None:
        self.run_id = run_id
        self.socket_path = socket_path or default_ipc_socket_path()
        self.on_cancel = on_cancel
        self.sock: socket.socket | None = None
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.listener: threading.Thread | None = None
        self.buffer = bytearray()

    def connect(self, state: dict[str, Any] | None = None) -> bool:
        if self.sock is not None:
            return True
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(0.12)
        try:
            sock.connect(str(self.socket_path))
        except OSError:
            sock.close()
            return False
        sock.settimeout(None)
        self.sock = sock
        self._send({"type": "register", "run_id": self.run_id, "pid": os.getpid(), "state": state or {}})
        self.listener = threading.Thread(target=self._listen, name=f"devtool-ipc-{self.run_id[:8]}", daemon=True)
        self.listener.start()
        return True

    def _send(self, payload: dict[str, Any]) -> bool:
        sock = self.sock
        if sock is None:
            return False
        try:
            with self.lock:
                sock.sendall(_json_line(payload))
            return True
        except OSError:
            self.close()
            return False

    def publish(self, state: dict[str, Any]) -> bool:
        if self.sock is None and not self.connect(state):
            return False
        return self._send({"type": "publish", "run_id": self.run_id, "state": state})

    def _listen(self) -> None:
        sock = self.sock
        if sock is None:
            return
        try:
            while not self.stop_event.is_set():
                data = sock.recv(8192)
                if not data:
                    break
                self.buffer.extend(data)
                while b"\n" in self.buffer:
                    line, _, remainder = self.buffer.partition(b"\n")
                    self.buffer = bytearray(remainder)
                    try:
                        message = json.loads(line.decode("utf-8"))
                    except Exception:
                        continue
                    if isinstance(message, dict) and message.get("type") == "cancel" and str(message.get("run_id") or "") == self.run_id:
                        if self.on_cancel is not None:
                            self.on_cancel()
        except OSError:
            pass
        finally:
            self.close()

    def close(self, *, unregister: bool = False) -> None:
        sock, self.sock = self.sock, None
        self.stop_event.set()
        if sock is None:
            return
        if unregister:
            with contextlib.suppress(OSError):
                sock.sendall(_json_line({"type": "unregister", "run_id": self.run_id}))
        with contextlib.suppress(OSError):
            sock.shutdown(socket.SHUT_RDWR)
        with contextlib.suppress(OSError):
            sock.close()


def broker_is_running(socket_path: Path | None = None) -> bool:
    path = socket_path or default_ipc_socket_path()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(0.1)
    try:
        sock.connect(str(path))
        sock.sendall(_json_line({"type": "ping"}))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def ensure_ipc_server(*, socket_path: Path | None = None, state_dir: Path | None = None) -> bool:
    path = socket_path or default_ipc_socket_path()
    if broker_is_running(path):
        return True
    argv = [sys.executable, "-m", "devtool", "ipc-server", "--foreground", "--socket", str(path)]
    if state_dir is not None:
        argv += ["--state-dir", str(state_dir)]
    try:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
    except OSError:
        return False
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if broker_is_running(path):
            return True
        time.sleep(0.05)
    return False


def run_ipc_server(args: argparse.Namespace) -> int:
    path = Path(args.socket).expanduser() if args.socket else default_ipc_socket_path()
    state_dir = Path(args.state_dir).expanduser() if args.state_dir else None
    if not args.foreground:
        return 0 if ensure_ipc_server(socket_path=path, state_dir=state_dir) else 1
    broker = IpcBroker(path, state_dir=state_dir)
    stopping = threading.Event()

    def stop(_signum: int, _frame: Any) -> None:
        stopping.set()
        broker.stop_event.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        broker.serve_forever()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0
