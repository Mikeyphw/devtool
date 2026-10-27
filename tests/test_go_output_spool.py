"""Go output spool acceptance: no false PASS under backpressure/EOF failure."""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import threading
import time

import pytest

from devtool.go_output_spool import GoOutputSpool
from devtool.runners import _run_process_live


def _invoke(tmp_path: Path, code: str, *, maximum: int = 10485760,
            timeout: float | None = 15.0) -> tuple[int, str, dict]:
    state = {}
    rc, out = _run_process_live(
        [sys.executable, "-u", "-c", code], cwd=str(tmp_path),
        env={**os.environ, "DEVTOOL_GO_OUTPUT_SPOOL_MAX_BYTES":str(maximum)},
        verbose=True, progress_parser="go-test-json", structured_state=state,
        timeout_seconds=timeout,
    )
    return rc, out, state


def _line(action: str, **more) -> str:
    return json.dumps({"Action":action,"Package":"example.org/spool", **more}) + "\n"


def test_private_spool_file_commits_complete_chunks_and_removes_storage(tmp_path):
    with GoOutputSpool(max_bytes=65536, directory=str(tmp_path)) as spool:
        assert stat.S_IMODE(spool.temporary_path.stat().st_mode) == 0o600
        proc = subprocess.Popen([sys.executable,"-c","import sys;sys.stdout.write('A'*8000);sys.stdout.flush()"],stdout=subprocess.PIPE)
        assert proc.stdout is not None
        spool.start(proc.stdout)
        got = bytearray()
        until = time.monotonic()+3
        while time.monotonic()<until:
            got.extend(spool.read_available(wait=0.05))
            state=spool.state()
            if state['pipe_eof_observed'] and state['bytes_consumed']==state['spool_bytes']:
                break
        assert bytes(got)==b'A'*8000
        assert spool.state()['bytes_drained']==8000
        assert spool.state()['transport_error'] is None
        proc.wait(timeout=3)
        proc.stdout.close()
        path=spool.temporary_path
    assert not path.exists()


def test_slow_renderer_does_not_block_pipe_drain(tmp_path, monkeypatch):
    import devtool.runners as module
    original_print = print
    def slow_print(*args, **kwargs):
        time.sleep(.001)
    monkeypatch.setattr(module, "print", slow_print, raising=False)
    events = [_line('start')] + [_line('output', Test='TestBulk',Output=f'line {i}\n') for i in range(800)] + [_line('pass',Test='TestBulk'),_line('pass')]
    code = 'import sys;sys.stdout.write('+repr(''.join(events))+');sys.stdout.flush()'
    try:
        rc, out, state = _invoke(tmp_path, code)
    finally:
        monkeypatch.delattr(module,'print',raising=False)
    assert rc==0
    transport=state['go_output_transport']
    assert transport['pipe_eof_observed'] is True
    assert transport['peak_backlog_bytes'] > 0
    assert transport['bytes_drained']==transport['spool_bytes']==transport['bytes_consumed']
    assert state['go_test_report'].tests_passed==1
    assert state['go_test_report'].packages_passed==1
    assert 'line 799' in out
    assert not any(t.name=='devtool-go-stdout-drain' for t in threading.enumerate())


def test_go_test_nonzero_and_failure_detail_kept(tmp_path):
    lines = ''.join((_line('start'),_line('output',Test='TestBroken',Output='fail_test.go:17: wrong contract\n'),
      _line('fail',Test='TestBroken'),_line('fail')))
    rc, out, state=_invoke(tmp_path,'import sys;sys.stdout.write('+repr(lines)+');sys.exit(1)')
    assert rc==1
    report=state['go_test_report']
    assert report.tests_failed==1 and report.packages_failed==1
    assert report.failures[0].line==17


def test_spool_overflow_fails_closed_and_terminates(tmp_path):
    code = ('import sys,json\n'
            'for i in range(8000):\n'
            ' sys.stdout.write(json.dumps({"Action":"output","Package":"example.org/spool", "Output":"x"*200})+"\\n")\n'
            'sys.stdout.flush()')
    rc, out, state=_invoke(tmp_path,code,maximum=2048)
    assert rc != 0
    transport=state['go_output_transport']
    assert 'capacity_exceeded' in transport['transport_error']
    assert transport['pipe_eof_observed'] is False


def test_incomplete_json_stream_cannot_pass(tmp_path):
    lines=_line('start')+_line('pass',Test='TestOK')+'{"Action":'
    rc,out,state=_invoke(tmp_path,'import sys;sys.stdout.write('+repr(lines)+')')
    assert rc!=0
    assert 'incomplete_go_json' in state['go_output_transport']['transport_error']
    assert state['go_test_report'].tests_passed==1


def test_missing_package_terminal_cannot_pass(tmp_path):
    lines=_line('start')+_line('pass',Test='TestOK')
    rc,out,state=_invoke(tmp_path,'import sys;sys.stdout.write('+repr(lines)+')')
    assert rc != 0
    assert 'unfinished_package_count=1' in state['go_output_transport']['transport_error']


@pytest.mark.skipif(os.name!='posix',reason='POSIX process-group inherited-pipe case')
def test_descendant_holding_stdout_is_diagnosed_and_bounded(tmp_path):
    # Descendant intentionally inherits stdout and remains in the same group.
    code='''import subprocess,sys
subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(20)'],stdout=sys.stdout,stderr=sys.stderr)
print('''+repr(_line('start')+_line('pass'))+''',end='',flush=True)
'''
    t=time.monotonic()
    rc,out,state=_invoke(tmp_path,code,timeout=5)
    assert time.monotonic()-t < 4
    assert rc!=0
    # EOF may become observable *after* group termination; the diagnosis is the missing EOF at exit.
    assert 'pipe_open_after_child_exit' in state['go_output_transport']['transport_error']


def test_consumer_failure_does_not_leave_reader_thread_or_spool(tmp_path,monkeypatch):
    from devtool.go_test import GoTestJSONStream
    def broken(self,raw):
        raise RuntimeError('deliberate renderer failure')
    monkeypatch.setattr(GoTestJSONStream,'feed',broken)
    with pytest.raises(RuntimeError,match='deliberate renderer failure'):
        _invoke(tmp_path,'import sys,time;print('+repr(_line('start'))+',end="",flush=True);time.sleep(10)',timeout=3)
    assert not any(t.name=='devtool-go-stdout-drain' for t in threading.enumerate())


def test_concurrent_spool_read_write_keeps_exact_byte_order(tmp_path):
    payload=b''.join(f'{i:08d}\n'.encode() for i in range(5000))
    with GoOutputSpool(max_bytes=500000,directory=str(tmp_path)) as spool:
        proc=subprocess.Popen([sys.executable,'-c','import sys;sys.stdout.buffer.write('+repr(payload)+');sys.stdout.flush()'],stdout=subprocess.PIPE)
        assert proc.stdout
        spool.start(proc.stdout)
        result=bytearray()
        until=time.monotonic()+4
        while time.monotonic()<until:
            result.extend(spool.read_available(size=91,wait=.02))
            s=spool.state()
            if s['pipe_eof_observed'] and s['bytes_consumed']==s['spool_bytes']:
                break
        assert bytes(result)==payload
        proc.wait(timeout=3)
        proc.stdout.close()


def test_invalid_capacity_fails_early():
    from devtool.go_output_spool import capacity_from_env
    with pytest.raises(ValueError):capacity_from_env({'DEVTOOL_GO_OUTPUT_SPOOL_MAX_BYTES':'999999999999'})
    with pytest.raises(ValueError):capacity_from_env({'DEVTOOL_GO_OUTPUT_SPOOL_MAX_BYTES':'n/a'})


def test_timeout_cancels_heavy_output_without_reader_leak(tmp_path):
    code = ('import sys,time\n'
            'while True:\n'
            ' sys.stdout.write(\'{"Action":"output","Package":"example.org/spool","Output":"x"}\\n\')\n'
            ' sys.stdout.flush()\n'
            ' time.sleep(.008)\n')
    rc, _out, state = _invoke(tmp_path, code, maximum=8 * 1024 * 1024, timeout=.35)
    assert rc == 124
    transport = state['go_output_transport']
    assert transport['spool_bytes'] > 0
    assert transport['transport_error'] is None
    assert transport['child_exit_code'] is not None
    assert not any(t.name == 'devtool-go-stdout-drain' for t in threading.enumerate())

# Go spool P1/P2 boundary regression (post-squash source hotfix).

def test_empty_successful_go_stream_is_not_accepted(tmp_path):
    rc, out, state = _invoke(tmp_path, "import sys;sys.exit(0)")
    assert rc != 0
    assert "completed_package_count=0" in state["go_output_transport"]["transport_error"]
    assert state["go_output_transport"]["pipe_eof_observed"] is True
    assert state["go_test_report"].packages_total == 0


def test_only_output_events_cannot_prove_a_completed_package(tmp_path):
    emitted = _line("output", Output="Go output but no result\n")
    rc, _, state = _invoke(tmp_path, "import sys;sys.stdout.write(" + repr(emitted) + ")")
    assert rc != 0
    assert state["go_test_report"].packages_total == 0
    assert "completed_package_count=0" in state["go_output_transport"]["transport_error"]


def test_genuine_no_test_files_package_is_accepted(tmp_path):
    # `go test -json` still emits the terminal package result for no-tests.
    emitted = (_line("start") + _line("output", Output="?\texample.org/spool\t[no test files]\n")
               + _line("skip"))
    rc, _, state = _invoke(tmp_path, "import sys;sys.stdout.write(" + repr(emitted) + ")")
    assert rc == 0
    report = state["go_test_report"]
    assert report.packages_no_tests == 1
    assert report.packages_total == 1
    assert report.tests_total == 0
    assert state["go_output_transport"]["transport_error"] is None


@pytest.mark.parametrize("bad", ["not-a-number", "1", "-1", "4294967297"])
def test_invalid_capacity_is_refused_before_popen(tmp_path, monkeypatch, bad):
    import devtool.runners as runners
    spawn_attempts = []
    def forbidden_spawn(*args, **kwargs):
        spawn_attempts.append(args)
        raise AssertionError("child must not start with invalid spool capacity")
    monkeypatch.setattr(runners.subprocess, "Popen", forbidden_spawn)
    with pytest.raises(ValueError, match="DEVTOOL_GO_OUTPUT_SPOOL_MAX_BYTES"):
        _run_process_live([sys.executable, "-c", "pass"], cwd=str(tmp_path),
              env={**os.environ, "DEVTOOL_GO_OUTPUT_SPOOL_MAX_BYTES":bad},
              verbose=True, progress_parser="go-test-json")
    assert spawn_attempts == []


def test_spool_initialization_io_error_is_before_spawn(tmp_path, monkeypatch):
    import devtool.runners as runners
    import devtool.go_output_spool as transport
    attempts = []
    def fail_spool(*args, **kwargs):
        raise OSError("private temporary directory unavailable")
    def forbidden_spawn(*args, **kwargs):
        attempts.append(True)
        raise AssertionError("spool error must precede spawn")
    monkeypatch.setattr(transport, "GoOutputSpool", fail_spool)
    monkeypatch.setattr(runners.subprocess, "Popen", forbidden_spawn)
    with pytest.raises(OSError, match="private temporary directory unavailable"):
        _run_process_live([sys.executable, "-c", "pass"], cwd=str(tmp_path),
              env={**os.environ, "DEVTOOL_GO_OUTPUT_SPOOL_MAX_BYTES":"4096"},
              verbose=True, progress_parser="go-test-json")
    assert attempts == []


def test_failed_process_creation_closes_preallocated_spool(tmp_path, monkeypatch):
    import devtool.runners as runners
    import devtool.go_output_spool as transport
    real = transport.GoOutputSpool
    opened = []
    def private_spool(*args, **kwargs):
        instance = real(*args, directory=str(tmp_path), **kwargs)
        opened.append(instance)
        return instance
    def forbidden_spawn(*args, **kwargs):
        raise PermissionError("deliberate process refusal")
    monkeypatch.setattr(transport, "GoOutputSpool", private_spool)
    monkeypatch.setattr(runners.subprocess, "Popen", forbidden_spawn)
    rc, output = _run_process_live(["forbidden"], cwd=str(tmp_path),
        env={**os.environ, "DEVTOOL_GO_OUTPUT_SPOOL_MAX_BYTES":"4096"},
        verbose=True, progress_parser="go-test-json")
    assert rc == 127
    assert "deliberate process refusal" in output
    assert len(opened) == 1
    assert not opened[0].temporary_path.exists()
    assert not list(tmp_path.glob("devtool-go-spool-*.raw"))
