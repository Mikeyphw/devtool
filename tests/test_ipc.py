from __future__ import annotations

import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from devtool.ipc import IpcBroker, IpcPublisher, default_ipc_socket_path


def recv_message(sock: socket.socket, timeout: float = 2.0) -> dict:
    sock.settimeout(timeout)
    data = bytearray()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        chunk = sock.recv(8192)
        if not chunk:
            break
        data.extend(chunk)
        if b"\n" in data:
            line = bytes(data).split(b"\n", 1)[0]
            return json.loads(line)
    raise TimeoutError("no IPC message")


class IpcBrokerTests(unittest.TestCase):
    def test_publish_snapshot_and_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "control.sock"
            broker = IpcBroker(path)
            thread = threading.Thread(target=broker.serve_forever, daemon=True)
            thread.start()
            deadline = time.monotonic() + 2
            while not path.exists() and time.monotonic() < deadline:
                time.sleep(0.01)

            cancelled = threading.Event()
            publisher = IpcPublisher("run-1", socket_path=path, on_cancel=cancelled.set)
            state = {"run_id": "run-1", "status": "running", "updated_at": "2026-07-11T00:00:00Z"}
            self.assertTrue(publisher.publish(state))

            consumer = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            consumer.connect(str(path))
            consumer.sendall(b'{"type":"subscribe"}\n')
            snapshot = recv_message(consumer)
            self.assertEqual(snapshot["type"], "snapshot")
            self.assertEqual(snapshot["runs"][0]["run_id"], "run-1")

            updated = dict(state, message="halfway", current=1, total=2)
            self.assertTrue(publisher.publish(updated))
            event = recv_message(consumer)
            self.assertEqual(event["type"], "run_update")
            self.assertEqual(event["run"]["message"], "halfway")

            consumer.sendall(b'{"type":"cancel","run_id":"run-1"}\n')
            ack = recv_message(consumer)
            while ack.get("type") != "cancel_ack":
                ack = recv_message(consumer)
            self.assertTrue(ack["accepted"])
            self.assertTrue(cancelled.wait(1.0))

            publisher.close(unregister=True)
            consumer.close()
            broker.stop_event.set()
            thread.join(timeout=2)
            broker.close()

    def test_default_socket_honors_override(self) -> None:
        import os
        old = os.environ.get("DEVTOOL_IPC_SOCKET")
        try:
            os.environ["DEVTOOL_IPC_SOCKET"] = "/tmp/devtool-test.sock"
            self.assertEqual(default_ipc_socket_path(), Path("/tmp/devtool-test.sock"))
        finally:
            if old is None:
                os.environ.pop("DEVTOOL_IPC_SOCKET", None)
            else:
                os.environ["DEVTOOL_IPC_SOCKET"] = old

class IpcCliTests(unittest.TestCase):
    def test_cli_exposes_ipc_server(self) -> None:
        from devtool.cli import build_parser
        args = build_parser().parse_args(["ipc-server", "--foreground"])
        self.assertEqual(args.command, "ipc-server")
        self.assertTrue(args.foreground)

class IpcMonotonicStateTests(unittest.TestCase):
    def test_sequence_rejects_delayed_state(self) -> None:
        from devtool.ipc import _state_is_newer
        current = {"run_id": "run", "sequence": 8, "updated_at": "2026-07-12T04:00:08Z"}
        delayed = {"run_id": "run", "sequence": 7, "updated_at": "2026-07-12T04:00:09Z"}
        self.assertFalse(_state_is_newer(delayed, current))
        self.assertTrue(_state_is_newer(dict(delayed, sequence=9), current))

    def test_event_id_sequence_is_supported_for_older_producers(self) -> None:
        from devtool.ipc import _state_is_newer
        current = {"event_id": "abc-00012", "updated_at": "2026-07-12T04:00:12Z"}
        delayed = {"event_id": "abc-00011", "updated_at": "2026-07-12T04:00:13Z"}
        self.assertFalse(_state_is_newer(delayed, current))
