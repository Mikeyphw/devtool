"""Regression tests for host-resolved executable fixture interpreters."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from tests._compat import (fixture_shebang, portable_fixture_path, required_interpreter,
                           short_private_ipc_socket_dir)


@pytest.mark.parametrize('name', ['bash', 'sh'])
def test_non_fhs_fixture_executes_original_body(tmp_path, monkeypatch, name):
    real = required_interpreter(name)
    # Reproduce the native runner's deeply nested pytest scratch path on every
    # host. Older Android/Linux kernels only read 128 bytes of a shebang.
    deep = tmp_path / ("nested" * 30)
    deep.mkdir()
    assert len(os.fsencode(str(deep / "non-fhs-bin" / name))) > 127
    # The private IPC fixture allocator already guarantees a shorter (103-byte)
    # executable alias path, independent of the nested TMPDIR. Keep the script
    # itself in the deep directory; only its interpreter path needs shortening.
    with short_private_ipc_socket_dir() as directory:
        bindir = Path(directory)
        (bindir / name).symlink_to(real)
        monkeypatch.setenv('PATH', str(bindir))
        script = deep / 'fixture'
        script.write_text(fixture_shebang(name) + 'printf "%s\\n" "$@"\n')
        script.chmod(0o700)
        header = script.read_bytes().splitlines(keepends=True)[0]
        assert header == b'#!' + os.fsencode(str(bindir / name)) + b'\n'
        assert len(header) <= 127
        result = subprocess.run([str(script), 'a b', 'c'], capture_output=True, text=True, check=True)
        assert result.stdout == 'a b\nc\n'


def test_missing_fixture_interpreter_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv('PATH', str(tmp_path))
    with pytest.raises(RuntimeError, match='unavailable'):
        fixture_shebang('bash')
    with pytest.raises(ValueError, match='Unsupported'):
        fixture_shebang('python')


def test_non_fhs_wrapper_tools_and_argument_forwarding(tmp_path, monkeypatch):
    tools = {tool: shutil.which(tool) for tool in ('sh', 'dirname', 'readlink')}
    assert all(tools.values()), tools
    with short_private_ipc_socket_dir() as directory:
        bindir = Path(directory)
        for tool, real in tools.items():
            (bindir / tool).symlink_to(real)
        monkeypatch.setenv('PATH', str(bindir))
        assert portable_fixture_path() == str(bindir)
        probe = tmp_path / 'argv-probe'
        header = fixture_shebang()
        assert len(os.fsencode(header)) <= 127
        probe.write_text(header + 'printf "%s\\n" "$@"\n')
        probe.chmod(0o700)
        env = {**os.environ, 'DEVTOOL_WRAPPER_EXECUTABLE': str(probe)}
        wrapper = Path('devtoolw').resolve()
        result = subprocess.run([required_interpreter('sh'), str(wrapper), '--help', '--', 'a b'], env=env,
                                capture_output=True, text=True, check=True)
        assert result.stdout.splitlines() == ['--repo', str(wrapper.parent), 'wrapper', 'exec', '--help', '--', 'a b']


def test_deep_tmpdir_socket_remains_short_and_private(tmp_path, monkeypatch):
    import socket
    import stat
    from tests._compat import short_private_ipc_socket_dir
    deep = tmp_path / ("nested" * 30)
    deep.mkdir()
    monkeypatch.setenv("TMPDIR", str(deep))
    with short_private_ipc_socket_dir() as directory:
        root = Path(directory)
        path = root / "ipc.sock"
        assert len(os.fsencode(str(path))) <= 103
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.bind(str(path))
            sock.listen(1)
    assert not root.exists()
