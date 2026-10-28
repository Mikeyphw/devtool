"""Windows-hosted FC08 IPC regression: missing AF_UNIX is explicit, nonfatal.

Does not claim Windows IPC control delivery, run-source host qualification, or
emulate a working Windows socket. Existing Unix broker tests cover the supported
path independently.
"""
from __future__ import annotations

import argparse
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from devtool import ipc
from devtool.clipboard import _copy_via_socket
from devtool.desktop.ipc_client import DesktopIpcClient


class UnsupportedUnixSocketHost(unittest.TestCase):
    def test_runtime_capability_refuses_without_invoking_socket(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(socket, 'AF_UNIX', None), patch.object(socket, 'socket', side_effect=AssertionError('no socket may be created')):
            path = Path(tmp) / 'not-created.sock'
            self.assertFalse(ipc.unix_socket_available())
            self.assertFalse(ipc.broker_is_running(path))
            self.assertFalse(ipc.ensure_ipc_server(socket_path=path))
            self.assertFalse(path.exists())
            pub = ipc.IpcPublisher('windows-sample',socket_path=path)
            self.assertFalse(pub.publish({'run_id':'windows-sample','status':'running'}))
            self.assertIsNone(pub.sock)
            self.assertIsNone(pub.listener)
            pub.close()

    def test_control_requests_are_rejected_not_acknowledged(self):
        with patch.object(socket, 'AF_UNIX', None), patch.object(socket, 'socket', side_effect=AssertionError('no socket may be created')):
            ok, why=ipc.request_run_action('run-id', 'pause')
            self.assertFalse(ok)
            self.assertIn('AF_UNIX',why)
            ok, why=ipc.request_run_cancel('run-id')
            self.assertFalse(ok)
            self.assertIn('AF_UNIX',why)

    def test_broker_refuses_start_without_touching_files(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(socket,'AF_UNIX',None):
            path=Path(tmp)/'IPC'/'pipe.sock'
            broker=ipc.IpcBroker(path)
            with self.assertRaisesRegex(RuntimeError,'AF_UNIX'):
                broker.start()
            self.assertFalse(path.parent.exists())
            self.assertIsNone(broker.server)
            broker.close()

    def test_cli_broker_refuses_missing_transport(self):
        with patch.object(socket,'AF_UNIX',None), patch('devtool.ipc.subprocess.Popen',side_effect=AssertionError('must not spawn')):
            args=argparse.Namespace(socket=None,state_dir=None,foreground=False)
            self.assertEqual(ipc.run_ipc_server(args),1)
            args.foreground=True
            self.assertEqual(ipc.run_ipc_server(args),1)

    def test_desktop_status_and_clipboard_socket_do_not_crash(self):
        with patch.object(socket,'AF_UNIX',None),patch.object(socket,'socket',side_effect=AssertionError('no socket may be created')):
            result=DesktopIpcClient(socket_path=Path('missing.sock')).snapshot()
            self.assertFalse(result['ok'])
            self.assertIn('unsupported',result['error'])
            with patch.dict('os.environ', {'DEVTOOL_CLIPBOARD_SOCKET':'missing.sock'}):
                result=_copy_via_socket('content')
            self.assertFalse(result.success)
            self.assertEqual(result.backend,'socket')
            self.assertIn('unsupported',result.reason)

    def test_supported_platform_capability_remains_available(self):
        # The actual Unix IPC integration test must remain the authority for
        # a usable socket; we only check this gate's non-interference here.
        self.assertEqual(ipc.unix_socket_available(),getattr(socket,'AF_UNIX',None) is not None)


if __name__=='__main__':
    unittest.main()
