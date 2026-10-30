"""R1R18: VSTest-reported coverage attachment provenance, bounded fail-closed evidence."""
from __future__ import annotations
import importlib.util
from pathlib import Path
import sys
import pytest

SOURCE=Path(__file__).resolve().parents[1]/'tools'/'fc08_host_workloads.py'
spec=importlib.util.spec_from_file_location('fc08_r1r18', SOURCE)
assert spec and spec.loader
fc=importlib.util.module_from_spec(spec)
spec.loader.exec_module(fc)


def root(tmp_path:Path, *, passing:bool=True):
    r=tmp_path/'test-results'
    r.mkdir()
    (r/'fc08.trx').write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<TestRun xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010">'
        '<ResultSummary outcome="Completed"><Counters total="1" executed="1" passed="%d" failed="%d"/>'
        '</ResultSummary></TestRun>'%(int(passing),int(not passing)))
    attached=r/'real'/'coverage.cobertura.xml'
    attached.parent.mkdir()
    attached.write_text('<coverage line-rate="1" lines-valid="3" lines-covered="3"/>')
    extra=r/'extra'/'coverage.cobertura.xml'
    extra.parent.mkdir()
    extra.write_text('<coverage line-rate="0" lines-valid="0" lines-covered="0"/>')
    return r, attached, extra


def test_vstest_attached_coverage_wins_with_extra_parseable_candidate(tmp_path):
    r, attached, extra=root(tmp_path)
    stdout='Passed! - Failed: 0, Passed: 1, Total: 1\nAttachments:\n '+str(attached)+'\n'
    got=fc.validate_dotnet_test_evidence(r, {'stdout_tail':stdout,'stdout_sha256':'1'*64})
    assert got['result']=='1'*64
    assert got['coverage']==fc.sha_file(attached)
    assert got['trx']==fc.sha_file(r/'fc08.trx')
    assert got['coverage_candidate_count']==2
    assert got['coverage_candidates_sha256']==sorted([fc.sha_file(attached),fc.sha_file(extra)])
    assert str(attached) not in str(got)


def test_missing_reported_attachment_stays_fail_closed(tmp_path):
    r,_,_=root(tmp_path)
    with pytest.raises(RuntimeError,match='AMBIGUOUS_OR_MISSING_VSTEST_COVERAGE_ATTACHMENT'):
        fc.validate_dotnet_test_evidence(r,{'stdout_tail':'Passed: 1','stdout_sha256':'1'*64})


def test_multiple_reported_attachments_stay_fail_closed(tmp_path):
    r,a,b=root(tmp_path)
    with pytest.raises(RuntimeError,match='AMBIGUOUS_OR_MISSING_VSTEST_COVERAGE_ATTACHMENT'):
        fc.validate_dotnet_test_evidence(r,{'stdout_tail':str(a)+'\n'+str(b),'stdout_sha256':'1'*64})


def test_reported_attachment_outside_scoped_results_stays_fail_closed(tmp_path):
    r,_,_=root(tmp_path)
    outside=tmp_path/'outside'/'coverage.cobertura.xml'
    outside.parent.mkdir()
    outside.write_text('<coverage lines-valid="12"/>')
    with pytest.raises(RuntimeError,match='REFUSED_VSTEST_COVERAGE_ATTACHMENT_OUTSIDE_RESULTS'):
        fc.validate_dotnet_test_evidence(r,{'stdout_tail':str(outside),'stdout_sha256':'1'*64})


def test_reported_attachment_nonexistent_stays_fail_closed(tmp_path):
    r,a,b=root(tmp_path)
    a.unlink()
    b.write_text('<coverage lines-valid="1"/>')
    with pytest.raises(RuntimeError,match='REFUSED_VSTEST_COVERAGE_ATTACHMENT_OUTSIDE_RESULTS'):
        fc.validate_dotnet_test_evidence(r,{'stdout_tail':str(a),'stdout_sha256':'1'*64})


def test_reported_extra_coverage_must_have_lines(tmp_path):
    r,attached,_=root(tmp_path)
    with pytest.raises(RuntimeError,match='INVALID_OR_EMPTY_COVERAGE'):
        fc.validate_dotnet_test_evidence(r,{'stdout_tail':str(r/'extra'/'coverage.cobertura.xml'), 'stdout_sha256':'1'*64})
    good=fc.validate_dotnet_test_evidence(r,{'stdout_tail':str(attached),'stdout_sha256':'1'*64})
    assert good['coverage']==fc.sha_file(attached)


def test_malformed_extra_coverage_is_not_silently_ignored(tmp_path):
    r,a,b=root(tmp_path)
    b.write_text('garbage')
    with pytest.raises(RuntimeError,match='INVALID_OR_EMPTY_COVERAGE'):
        fc.validate_dotnet_test_evidence(r,{'stdout_tail':str(a),'stdout_sha256':'1'*64})


def test_failed_trx_remains_refused_even_if_attached_coverage_exists(tmp_path):
    r,a,_=root(tmp_path,passing=False)
    with pytest.raises(RuntimeError,match='INVALID_OR_ZERO_TEST_TRX'):
        fc.validate_dotnet_test_evidence(r,{'stdout_tail':str(a),'stdout_sha256':'1'*64})


def test_single_coverage_candidate_retains_previous_receipt_shape(tmp_path):
    r,a,b=root(tmp_path)
    b.unlink()
    value=fc.validate_dotnet_test_evidence(r,{'stdout_tail':'','stdout_sha256':'1'*64})
    assert set(value)=={'result','trx','coverage'}
    assert value['coverage']==fc.sha_file(a)


def test_duplicate_trx_refused_even_with_correct_coverage(tmp_path):
    r,a,_=root(tmp_path)
    (r/'extra.trx').write_text((r/'fc08.trx').read_text())
    with pytest.raises(RuntimeError,match='MISSING_TRX_OR_COVERAGE'):
        fc.validate_dotnet_test_evidence(r,{'stdout_tail':str(a),'stdout_sha256':'1'*64})
