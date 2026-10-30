"""R1R16: Windows hosted .NET TRX / coverage must be authentic, not inferred."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

SRC = Path(__file__).resolve().parents[1] / 'tools' / 'fc08_host_workloads.py'
spec = importlib.util.spec_from_file_location('_fc08_r1r16', SRC)
assert spec is not None and spec.loader is not None
fc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fc)


def _evidence(tmp_path: Path, *, total: int = 1, executed: int = 1, passed: int = 1,
              failed: int = 0, cover: bool = True, valid_lines: int = 1):
    root=tmp_path/'test-results';root.mkdir(exist_ok=True)
    (root/'fc08.trx').write_text('''<?xml version="1.0" encoding="utf-8"?>
<TestRun xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010">
  <ResultSummary outcome="Completed">
    <Counters total="%d" executed="%d" passed="%d" failed="%d" />
  </ResultSummary>
</TestRun>''' % (total,executed,passed,failed))
    if cover:
        (root/'coverage.cobertura.xml').write_text(
            '<coverage line-rate="1" lines-valid="%d" lines-covered="%d"></coverage>' % (valid_lines,valid_lines))
    return root


def _testrow():
    return {'stdout_sha256':'f'*64,'stdout_tail':'test discovery summary: passed 1', 'stderr_tail':''}


def test_vstest_trx_and_real_cobertura_yield_only_digests(tmp_path):
    d=_evidence(tmp_path)
    got=fc.validate_dotnet_test_evidence(d,_testrow())
    assert set(got)=={'result','trx','coverage'}
    assert got['result']=='f'*64
    assert got['trx']==fc.sha_file(d/'fc08.trx')
    assert got['coverage']==fc.sha_file(d/'coverage.cobertura.xml')


@pytest.mark.parametrize('which', ['missing-trx','missing-coverage','duplicate-trx'])
def test_missing_or_ambiguous_artifact_never_passes(tmp_path,which):
    d=_evidence(tmp_path)
    if which=='missing-trx':(d/'fc08.trx').unlink()
    if which=='missing-coverage':(d/'coverage.cobertura.xml').unlink()
    if which=='duplicate-trx':(d/'other.trx').write_bytes((d/'fc08.trx').read_bytes())
    with pytest.raises(RuntimeError,match='MISSING_TRX_OR_COVERAGE') as exc:
        fc.validate_dotnet_test_evidence(d,_testrow())
    assert 'trx_count=' in str(exc.value) and 'coverage_count=' in str(exc.value)
    assert 'test discovery summary' in str(exc.value)


@pytest.mark.parametrize('counts',[(0,0,0,0),(1,0,0,0),(2,2,1,1)])
def test_zero_missing_or_failed_tests_do_not_qualify(tmp_path,counts):
    d=_evidence(tmp_path,total=counts[0],executed=counts[1],passed=counts[2],failed=counts[3])
    with pytest.raises(RuntimeError,match='INVALID_OR_ZERO_TEST_TRX'):
        fc.validate_dotnet_test_evidence(d,_testrow())


def test_invalid_and_empty_coverage_rejected(tmp_path):
    d=_evidence(tmp_path,valid_lines=0)
    with pytest.raises(RuntimeError,match='INVALID_OR_EMPTY_COVERAGE'):
        fc.validate_dotnet_test_evidence(d,_testrow())
    (d/'coverage.cobertura.xml').write_text('not XML')
    with pytest.raises(RuntimeError,match='INVALID_OR_EMPTY_COVERAGE'):
        fc.validate_dotnet_test_evidence(d,_testrow())


def test_capture_success_output_only_when_explicitly_requested(tmp_path):
    argv=[sys.executable,'-c','print("TEST_DISCOVERY_OK")']
    regular=fc.run(argv,tmp_path)
    assert regular['code']==0 and regular['stdout_tail']==''
    debug=fc.run(argv,tmp_path,capture_success_tail=True)
    assert debug['code']==0 and 'TEST_DISCOVERY_OK' in debug['stdout_tail']
    assert debug['stdout_sha256']==regular['stdout_sha256']


def test_fixture_pins_sdk8_and_explicitly_rejects_zero_tests():
    content=SRC.read_text()
    assert "'global.json':'{\"sdk\":{\"version\":\"8.0.100\",\"rollForward\":\"latestFeature\"}}\\n'" in content
    assert '<TreatNoTestsAsError>true</TreatNoTestsAsError>' in content
    assert "'--settings','fc08.runsettings'" in content
    assert "capture_success_tail=True" in content
    assert "must(['dotnet','new','sln','-n','FC08'],repo,timeout=120)" in content
    assert "'--format','sln'" not in content
