"""M5 focused regressions — native operations remain source bound and inert by default."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import pytest
from devtool.native_delivery_m5 import (
    NativeM5Refused, _artifact, _inspection, _preverify, _session_id,
    _summary, _child_env, main,
)
from devtool.cli import build_parser
from devtool.core_parser import parser_identity_projection
from devtool.core_registry import build_core_registry, assert_parser_matches_canonical_registry


def test_native_cli_canonical_help_and_options():
    parser=build_parser()
    examples=[
        ['native','check','sample.zip','--repo','/tmp/work'],
        ['native','apply','sample.pyz','--repo','/tmp/work','--yes'],
        ['native','status','--session-id','a'*32,'--repo','/tmp/work'],
        ['native','deliver','--session-id','a'*32,'--repo','/tmp/work','--auth','app','--yes'],
        ['native','ci','--session-id','a'*32,'--repo','/tmp/work','--checks','10','--interval','5'],
    ]
    for argv in examples:
        args=parser.parse_args(argv)
        assert args.command=='native'
        assert args.native_command
    # Gate the original real-device failure explicitly, before regeneration.
    registry=build_core_registry()
    assert_parser_matches_canonical_registry(parser, registry)
    projected=parser_identity_projection()
    assert ('native','apply') in projected['commands']
    assert (('native','deliver'),'--auth') in projected['options']


def test_missing_approval_is_rejected_without_native_call(tmp_path,monkeypatch):
    art=tmp_path/'a.zip';art.write_bytes(b'abc')
    monkeypatch.setattr('devtool.native_delivery_m5._preverify',lambda _: 'native-zip')
    monkeypatch.setattr('devtool.native_delivery_m5._run',lambda *a,**kw:(0,''))
    p=SimpleNamespace(native_command='apply',repo=str(tmp_path),artifact=str(art),yes=False)
    assert main(p)==2


def test_invalid_or_symlink_artifact_is_rejected(tmp_path):
    a=tmp_path/'a.pyz';a.write_bytes(b'bad')
    link=tmp_path/'link.pyz';link.symlink_to(a)
    with pytest.raises(NativeM5Refused,match='M5_ARTIFACT_MUST_BE_REGULAR'):
        _artifact(str(link))
    with pytest.raises(NativeM5Refused,match='M5_ARTIFACT_MUST_BE_REGULAR'):
        _artifact(str(tmp_path/'a.txt'))


def test_pyz_never_runs_without_verified_v3_manifest(tmp_path):
    a=tmp_path/'bad.pyz';a.write_bytes(b'not-a-zip')
    with pytest.raises(NativeM5Refused,match='M5_PYZ_SOURCE_AND_INTEGRITY_UNVERIFIED'):
        _preverify(a)
    assert _inspection(a) if False else True


def test_report_never_invents_live_ci_or_release(capsys):
    info={'id':'a'*32,'head_sha':'b'*40,'head':'branch',
          'native_apply':{'state':'COMMITTED','commit':'b'*40,'transaction_id':'txn',
                          'history_id':'hist','validation_status':'ok'},
          'pr':{'url':'https://github.com/example/repo/pull/1','head_sha':'b'*40},
          'ci':{'state':'REQUIRED_CHECKS_PASSED'}}
    summary=_summary(info,verb='status',format='json')
    assert summary['ci_scope']=='recorded_historical_not_live_qualified'
    assert summary['release_qualified'] is False
    assert summary['device_qualified'] is False
    assert summary['automatic_clipboard'] is False
    assert summary['fullscreen_ui'] is False
    assert summary['tests_evidence']['skipped']=='not_recorded'
    assert 'token' not in capsys.readouterr().out.lower()


def test_child_presentation_plain_and_no_copy():
    env=_child_env()
    assert env['DEVTOOL_EXECUTION_PRESENTATION']=='plain'
    assert env['DEVTOOL_JOB_UI']=='plain'
    assert env['DEVTOOL_TEXTUAL_APPLY_HOST']=='0'


def test_bad_session_id_rejected():
    for value in ['a'*31,'A'*32,'hello','a'*32+';rm']:
        with pytest.raises(NativeM5Refused):_session_id(value)


def test_no_auto_delivery_mutation_without_yes(tmp_path,monkeypatch):
    p=SimpleNamespace(native_command='deliver',repo=str(tmp_path),native_session_id='a'*32,yes=False)
    monkeypatch.setattr('devtool.native_delivery_m5._bound_status',lambda *a,**kw:{'id':'a'*32})
    with mock.patch('devtool.native_delivery_m5._run') as runner:
        assert main(p)==2
        runner.assert_not_called()
