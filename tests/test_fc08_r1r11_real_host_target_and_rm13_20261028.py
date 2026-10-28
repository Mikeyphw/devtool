"""R1R11 strict Windows solution selection and bounded native symbol verification."""
from pathlib import Path
import importlib.util
from types import SimpleNamespace
import subprocess
import pytest

from devtool.actions_rm13_cpp import CppError, verify_binary
# The repo's tools/ is a namespace directory. The host may already have a
# regular `tools` package loaded (e.g. Hermes Agent on native Termux), which
# would otherwise hijack the workload module import at collection.
# Load precisely this repository's reviewed fixture, independent of sys.path,
# PYTHONPATH, the CWD, or existing sys.modules["tools"].
_FC08_SOURCE = Path(__file__).resolve().parents[1] / 'tools' / 'fc08_host_workloads.py'
_FC08_SPEC = importlib.util.spec_from_file_location('_devtool_fc08_workloads_r1r12', _FC08_SOURCE)
if _FC08_SPEC is None or _FC08_SPEC.loader is None:
    raise RuntimeError('FC08 R1R12 cannot load reviewed local workload source')
fc08 = importlib.util.module_from_spec(_FC08_SPEC)
_FC08_SPEC.loader.exec_module(fc08)


def test_fc08_regression_uses_exact_local_reviewed_source():
    assert Path(fc08.__file__).resolve() == _FC08_SOURCE.resolve()
    assert callable(fc08.devtool) and callable(fc08.windows)



def _native_elf(path: Path) -> Path:
    data = bytearray(64)
    data[:4] = b'\x7fELF'
    data[4:6] = b'\x02\x01'
    data[16:18] = (2).to_bytes(2, 'little')
    data[18:20] = (62).to_bytes(2, 'little')
    path.write_bytes(data)
    return path


def test_dotnet_fixture_explicitly_selects_tracked_real_solution(tmp_path, monkeypatch):
    root = tmp_path / 'dotnet'
    root.mkdir()
    (root / 'FC08.sln').write_text('fixture solution')
    seen = []
    def fake_must(argv, cwd, *, timeout, env):
        seen.append((argv, cwd, timeout))
        return {'code': 0, 'stdout_sha256': '0' * 64}
    monkeypatch.setattr(fc08, 'must', fake_must)
    monkeypatch.setattr(fc08, 'git', lambda path, *args: 'FC08.sln')
    assert fc08.devtool(root, 'build', timeout=900, solution='FC08.sln')['code'] == 0
    assert seen[0][0][-3:] == ['--solution', 'FC08.sln', 'build']
    assert seen[0][1] == fc08.SOURCE and seen[0][2] == 900
    assert fc08.devtool(root, 'test')['code'] == 0
    assert '--solution' not in seen[1][0]


def test_dotnet_fixture_refuses_untracked_missing_or_ambiguous_solution(tmp_path, monkeypatch):
    root = tmp_path / 'dotnet'
    root.mkdir()
    calls = []
    monkeypatch.setattr(fc08, 'must', lambda *a, **kw: calls.append(1))
    monkeypatch.setattr(fc08, 'git', lambda *a: '')
    with pytest.raises(RuntimeError, match='UNTRACKED'):
        fc08.devtool(root, 'build', solution='FC08.sln')
    (root / 'FC08.sln').write_text('fixture')
    with pytest.raises(RuntimeError, match='UNTRACKED'):
        fc08.devtool(root, 'build', solution='FC08.sln')
    with pytest.raises(RuntimeError, match='UNTRACKED'):
        fc08.devtool(root, 'build', solution='../FC08.sln')
    assert not calls


def test_rm13_symbol_inspection_bounded_success_absent_and_wrong_tool(tmp_path, monkeypatch):
    artifact = _native_elf(tmp_path / 'native')
    seen = []
    def fake_nm(argv, **kwargs):
        seen.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout='000001 T reviewed_symbol\n', stderr='')
    monkeypatch.setattr(subprocess, 'run', fake_nm)
    result = verify_binary(artifact, abi='elf-x86_64', symbols=['reviewed_symbol'])
    assert result['verified_local_bytes'] is True
    assert seen[0][0][:3] == ['nm', '-g', '--defined-only']
    assert seen[0][1]['timeout'] == 30
    with pytest.raises(CppError, match='declared symbol absent'):
        verify_binary(artifact, abi='elf-x86_64', symbols=['absent_symbol'])
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=1, stdout='', stderr='failed'))
    with pytest.raises(CppError, match='uninspectable symbols'):
        verify_binary(artifact, abi='elf-x86_64', symbols=['reviewed_symbol'])


def test_rm13_symbol_inspection_distinguishes_timeout_from_missing_tool(tmp_path, monkeypatch):
    artifact = _native_elf(tmp_path / 'native')
    def timeout(argv, **kw):
        raise subprocess.TimeoutExpired(argv, kw['timeout'])
    monkeypatch.setattr(subprocess, 'run', timeout)
    with pytest.raises(CppError, match='timed out after 30s'):
        verify_binary(artifact, abi='elf-x86_64', symbols=['reviewed_symbol'])
    def missing(argv, **kw):
        raise FileNotFoundError('nm')
    monkeypatch.setattr(subprocess, 'run', missing)
    with pytest.raises(CppError, match='tool unavailable'):
        verify_binary(artifact, abi='elf-x86_64', symbols=['reviewed_symbol'])
