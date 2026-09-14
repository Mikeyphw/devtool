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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .ui_core.machine import IPC_PROTOCOL_VERSION, MACHINE_SCHEMA_VERSION, machine_state_from_runtime
from .xdg import state_dir


def default_ipc_socket_path() -> Path:
    override = os.environ.get("DEVTOOL_IPC_SOCKET", "").strip()
    if override:
        return Path(override).expanduser()
    runtime = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if runtime:
        return Path(runtime).expanduser() / "devtool" / "control-center.sock"
    return state_dir() / "control-center.sock"


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.hex()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    return str(value)


def _json_line(payload: dict[str, Any]) -> bytes:
    safe_payload = _json_safe(payload)
    return (json.dumps(safe_payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _terminal_status(state: dict[str, Any]) -> bool:
    return str(state.get("status") or "").lower() in {"ok", "failed", "cancelled", "interrupted", "skipped"}


def _state_sequence(state: dict[str, Any]) -> int:
    try:
        sequence_raw = state.get("sequence")
        if isinstance(sequence_raw, bool):
            return 0
        sequence = int(sequence_raw or 0)
    except (TypeError, ValueError):
        sequence = 0
    if sequence > 0:
        return sequence
    event_id = str(state.get("event_id") or "")
    try:
        return int(event_id.rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return 0


def _parse_state_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _state_is_newer(candidate: dict[str, Any], current: dict[str, Any] | None) -> bool:
    if not current:
        return True
    if _terminal_status(current) and not _terminal_status(candidate):
        return False
    candidate_sequence = _state_sequence(candidate)
    current_sequence = _state_sequence(current)
    if candidate_sequence or current_sequence:
        if candidate_sequence != current_sequence:
            return candidate_sequence > current_sequence
        # Equal sequence is not newer. This prevents stale replay from replacing
        # an authoritative state with the same monotonic identity.
        return False
    candidate_time = _parse_state_time(candidate.get("updated_at"))
    current_time = _parse_state_time(current.get("updated_at"))
    if candidate_time and current_time:
        return candidate_time > current_time
    candidate_updated = str(candidate.get("updated_at") or "")
    current_updated = str(current.get("updated_at") or "")
    if candidate_updated and current_updated:
        return candidate_updated > current_updated
    return False


def _state_run_id_conflict(state: dict[str, Any], run_id: str) -> bool:
    embedded = str(state.get("run_id") or "").strip()
    return bool(embedded and embedded != run_id)


def _with_machine_state(state: dict[str, Any]) -> dict[str, Any]:
    value = dict(state)
    if not isinstance(value.get("machine"), dict):
        try:
            value["machine"] = machine_state_from_runtime(value).as_dict()
        except Exception:
            pass
    return value

def _ipc_meta() -> dict[str, Any]:
    return {"protocol_version": IPC_PROTOCOL_VERSION, "machine_schema_version": MACHINE_SCHEMA_VERSION}


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
                value = _with_machine_state(value)
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

    def _peer_owns_run(self, peer: _Peer, run_id: str) -> bool:
        """Return True only for the currently registered producer of ``run_id``.

        A peer's historical ``role``/``run_id`` fields are not authority: a later
        registration can supersede that connection.  The producer registry is the
        single ownership source used by every mutating IPC message.
        """
        return bool(
            run_id
            and peer.role == "producer"
            and peer.run_id == run_id
            and self.producers.get(run_id) is peer
        )

    @staticmethod
    def _reject_peer_mutation(peer: _Peer, *, kind: str, run_id: str) -> None:
        peer.send({
            "type": "error",
            "code": "producer_ownership_required",
            "message": f"{kind} requires ownership of run {run_id!r}",
            "run_id": run_id,
            **_ipc_meta(),
        })

    def _snapshot(self) -> list[dict[str, Any]]:
        return sorted(self.runs.values(), key=lambda value: str(value.get("updated_at") or ""), reverse=True)

    def _handle(self, peer: _Peer, message: dict[str, Any]) -> None:
        kind = str(message.get("type") or "").strip().lower()
        if kind in {"subscribe", "hello"}:
            peer.role = "consumer"
            peer.subscribed = True
            peer.send({"type": "snapshot", "runs": self._snapshot(), "socket": str(self.socket_path), **_ipc_meta()})
            return
        if kind == "register":
            run_id = str(message.get("run_id") or "").strip()
            if not run_id:
                peer.send({"type": "error", "message": "register requires run_id", **_ipc_meta()})
                return
            state = message.get("state")
            if isinstance(state, dict) and _state_run_id_conflict(state, run_id):
                peer.send({"type": "error", "code": "run_id_mismatch", "message": "registered run_id and state.run_id must match", "run_id": run_id, **_ipc_meta()})
                return
            if isinstance(state, dict):
                state = dict(state)
                state["run_id"] = run_id
                state = _with_machine_state(state)
            previous = self.producers.get(run_id)
            if previous is not None and previous is not peer:
                previous.role = "superseded"
            peer.role = "producer"
            peer.run_id = run_id
            self.producers[run_id] = peer
            if isinstance(state, dict) and _state_is_newer(state, self.runs.get(run_id)):
                self.runs[run_id] = state
                self._broadcast({"type": "run_update", "run": state, **_ipc_meta()})
            peer.send({"type": "registered", "run_id": run_id, **_ipc_meta()})
            return
        if kind in {"publish", "run_update"}:
            state = message.get("state") or message.get("run")
            if not isinstance(state, dict):
                return
            run_id = str(message.get("run_id") or peer.run_id or state.get("run_id") or "").strip()
            if not run_id:
                return
            if not self._peer_owns_run(peer, run_id):
                self._reject_peer_mutation(peer, kind=kind, run_id=run_id)
                return
            if _state_run_id_conflict(state, run_id):
                peer.send({"type": "error", "code": "run_id_mismatch", "message": "published run_id and state.run_id must match", "run_id": run_id, **_ipc_meta()})
                return
            state = dict(state)
            state["run_id"] = run_id
            state = _with_machine_state(state)
            if not _state_is_newer(state, self.runs.get(run_id)):
                return
            self.runs[run_id] = state
            self._broadcast({"type": "run_update", "run": state, **_ipc_meta()})
            return
        if kind == "unregister":
            run_id = str(message.get("run_id") or peer.run_id or "").strip()
            if run_id and self.producers.get(run_id) is peer:
                self.producers.pop(run_id, None)
            peer.send({"type": "unregistered", "run_id": run_id})
            return
        if kind == "action":
            run_id = str(message.get("run_id") or "").strip()
            action_id = str(message.get("action_id") or "").strip()
            payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
            producer = self.producers.get(run_id)
            if action_id == "operation.cancel":
                accepted = bool(producer and producer.send({"type": "action", "run_id": run_id, "action_id": action_id, "payload": payload, "reason": str(message.get("reason") or "UI request"), **_ipc_meta()}))
            else:
                accepted = bool(producer and producer.send({"type": "action", "run_id": run_id, "action_id": action_id, "payload": payload, **_ipc_meta()}))
            peer.send({"type": "action_ack", "run_id": run_id, "action_id": action_id, "accepted": accepted, **_ipc_meta()})
            if accepted:
                self._broadcast({"type": "action_requested", "run_id": run_id, "action_id": action_id, **_ipc_meta()})
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
            if not self._peer_owns_run(peer, run_id):
                self._reject_peer_mutation(peer, kind=kind, run_id=run_id)
                return
            removed = self.runs.pop(run_id, None) is not None
            if removed:
                self._broadcast({"type": "run_removed", "run_id": run_id, **_ipc_meta()})
            return
        if kind == "ping":
            peer.send({"type": "pong", "time": time.time(), **_ipc_meta()})

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

    def __init__(self, run_id: str, *, socket_path: Path | None = None, on_cancel: Callable[[], None] | None = None, on_action: Callable[[str, dict[str, Any]], None] | None = None) -> None:
        self.run_id = run_id
        self.socket_path = socket_path or default_ipc_socket_path()
        self.on_cancel = on_cancel
        self.on_action = on_action
        self.sock: socket.socket | None = None
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.listener: threading.Thread | None = None
        self.buffer = bytearray()
        self._next_sequence = 0

    def _prepare_state(self, state: dict[str, Any] | None) -> dict[str, Any]:
        prepared = dict(state or {})
        prepared["run_id"] = self.run_id
        if "sequence" not in prepared and "event_id" not in prepared:
            self._next_sequence += 1
            prepared["sequence"] = self._next_sequence
        else:
            sequence = _state_sequence(prepared)
            if sequence > self._next_sequence:
                self._next_sequence = sequence
        return prepared

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
        initial_state = self._prepare_state(state)
        self._send({"type": "register", "run_id": self.run_id, "pid": os.getpid(), "state": _with_machine_state(initial_state), **_ipc_meta()})
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
        publish_state = self._prepare_state(state)
        if self.sock is None and not self.connect(publish_state):
            return False
        return self._send({"type": "publish", "run_id": self.run_id, "state": _with_machine_state(publish_state), **_ipc_meta()})

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
                    if not isinstance(message, dict) or str(message.get("run_id") or "") != self.run_id:
                        continue
                    if message.get("type") == "cancel":
                        if self.on_cancel is not None:
                            self.on_cancel()
                    elif message.get("type") == "action":
                        action_id = str(message.get("action_id") or "")
                        payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
                        if action_id == "operation.cancel" and self.on_cancel is not None:
                            self.on_cancel()
                        if self.on_action is not None:
                            self.on_action(action_id, payload)
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



def request_run_action(run_id: str, action_id: str, *, payload: dict[str, Any] | None = None, reason: str = "UI request", socket_path: Path | None = None, timeout: float = 0.8) -> tuple[bool, str]:
    run_id = str(run_id or "").strip()
    action_id = str(action_id or "").strip()
    if not run_id or not action_id:
        return False, "A run identity and action ID are required."
    path = socket_path or default_ipc_socket_path()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(str(path))
        sock.sendall(_json_line({"type": "action", "run_id": run_id, "action_id": action_id, "payload": dict(payload or {}), "reason": reason, **_ipc_meta()}))
        buffer = bytearray()
        while b"\n" not in buffer:
            chunk = sock.recv(8192)
            if not chunk:
                break
            buffer.extend(chunk)
        line = bytes(buffer).split(b"\n", 1)[0]
        response = json.loads(line.decode("utf-8")) if line else {}
        accepted = bool(isinstance(response, dict) and response.get("type") == "action_ack" and response.get("accepted"))
        if accepted:
            return True, f"Action {action_id} requested for {run_id}."
        return False, f"Action {action_id} was not accepted for {run_id}."
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return False, f"Action request failed: {exc}"
    finally:
        with contextlib.suppress(OSError):
            sock.close()


def request_run_cancel(run_id: str, *, reason: str = "UI request", socket_path: Path | None = None, timeout: float = 0.8) -> tuple[bool, str]:
    """Request graceful cancellation through the shared IPC broker.

    Frontends should call this helper rather than reimplementing the wire
    protocol. The producer remains authoritative about whether cancellation is
    accepted.
    """
    run_id = str(run_id or "").strip()
    if not run_id:
        return False, "No active run identity is available for cancellation."
    path = socket_path or default_ipc_socket_path()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(str(path))
        sock.sendall(_json_line({"type": "cancel", "run_id": run_id, "reason": reason}))
        buffer = bytearray()
        while b"\n" not in buffer:
            chunk = sock.recv(8192)
            if not chunk:
                break
            buffer.extend(chunk)
        line = bytes(buffer).split(b"\n", 1)[0]
        response = json.loads(line.decode("utf-8")) if line else {}
        accepted = bool(isinstance(response, dict) and response.get("type") == "cancel_ack" and response.get("accepted"))
        if accepted:
            return True, f"Cancellation requested for {run_id}."
        return False, f"The run {run_id} is not connected to the IPC broker; cancellation was not sent."
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return False, f"Cancellation request failed: {exc}"
    finally:
        with contextlib.suppress(OSError):
            sock.close()

def broker_is_running(socket_path: Path | None = None) -> bool:
    path = socket_path or default_ipc_socket_path()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(0.2)
    try:
        sock.connect(str(path))
        sock.sendall(_json_line({"type": "ping", **_ipc_meta()}))
        buffer = bytearray()
        while b"\n" not in buffer and len(buffer) < 65536:
            chunk = sock.recv(8192)
            if not chunk:
                break
            buffer.extend(chunk)
        line = bytes(buffer).split(b"\n", 1)[0]
        response = json.loads(line.decode("utf-8")) if line else {}
        return bool(
            isinstance(response, dict)
            and response.get("type") == "pong"
            and int(response.get("protocol_version") or 0) == IPC_PROTOCOL_VERSION
            and int(response.get("machine_schema_version") or 0) == MACHINE_SCHEMA_VERSION
        )
    except (OSError, ValueError, json.JSONDecodeError):
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
