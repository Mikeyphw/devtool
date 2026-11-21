"""M6: archived native log paging and fail-closed local read-only viewer."""
from __future__ import annotations

import os
from pathlib import Path
from types import ModuleType
import sys

import pytest

from devtool.m6_receipt_logs import receipt_log_refs, read_native_log_page
from devtool.m6_operation_view import credential_health_rows, render_inspection


def test_bound_sources_only_without_arbitrary_recursive_search():
    token = 'ghp_' + 'S' * 40
    rows = receipt_log_refs({
        'log_path': '/valid/logs/out.log',
        'token': token,
        'validation_steps': [{'log_file': '/valid/logs/stage.log'}, {'other': '/ignored'}],
        'nested': {'log_path': '/private/deeper/file'},
        'events': [{'evidence_log_file': '/valid/logs/step.log'}]})
    assert rows == ('/valid/logs/out.log', '/valid/logs/stage.log', '/valid/logs/step.log')
    assert token not in repr(rows)


def test_m4_health_is_truthful_and_secret_free():
    health = {'auth_mode':'app', 'authenticated': True, 'authorized_for_operation':False,
              'repository_coverage':'unverified', 'token':'ghp_'+'F'*40}
    rows = credential_health_rows(health)
    values = repr(rows)
    assert 'authorized for operation' in values
    assert 'False' in values
    assert 'UNVERIFIED' in values
    assert 'ghp_' not in values
    assert 'required_ci_qualified' in values


def test_section_title_and_correct_paging_hint():
    frame, idx = render_inspection({4:[('log','available')]}, 4, 0,
                                    width=60,height=17,title='M6 · NATIVE TRANSACTION')
    from rich.console import Console
    from io import StringIO
    out=StringIO()
    con=Console(file=out,width=60,color_system=None,force_terminal=False)
    con.print(frame)
    text=out.getvalue()
    assert 'NATIVE TRANSACTION' in text
    assert '[ previous · ] next' in text
    assert '[/] page' not in text
    assert idx==0


def test_archived_log_paging_redaction_immutability(tmp_path,monkeypatch):
    repo=tmp_path/'repo';repo.mkdir()
    root=tmp_path/'state'/'devtool'/'logs';root.mkdir(parents=True)
    source=root/'20261121-123456-000001-stage.log'
    secret='ghp_'+'Z'*40
    source.write_text('\n'.join(f'line {i}' for i in range(80))+'\nTOKEN='+secret+'\n',encoding='utf-8')
    monkeypatch.setenv('XDG_STATE_HOME',str(tmp_path/'state'))
    # Stand-in for the canonical native redactor: prevent success if absent.
    redact=ModuleType('devtool.logstore')
    redact.redact_sensitive_text=lambda value: value.replace(secret,'<redacted>')
    monkeypatch.setitem(sys.modules,'devtool.logstore',redact)
    before=source.read_bytes()
    a=read_native_log_page(repo,str(source),0)
    b=read_native_log_page(repo,str(source),2)
    c=read_native_log_page(repo,str(source),9999)
    assert (a['page'],a['pages'])==(0,3)
    assert b['page']==2 and c['page']==2
    assert secret not in repr((a,b,c))
    assert source.read_bytes()==before


def test_outside_path_symlinks_and_oversized_refused(tmp_path,monkeypatch):
    repo=tmp_path/'repo';repo.mkdir()
    root=tmp_path/'state'/'devtool'/'logs';root.mkdir(parents=True)
    monkeypatch.setenv('XDG_STATE_HOME',str(tmp_path/'state'))
    bad=tmp_path/'private.log';bad.write_text('private')
    link=root/'link.log';link.symlink_to(bad)
    for path in (str(bad),str(link),'/not/such/path.log'):
        with pytest.raises((ValueError,OSError)):
            read_native_log_page(repo,path)
    giant=root/'oversized.log'
    with giant.open('wb') as f:
        f.truncate(16*1024*1024+1)
    with pytest.raises(ValueError,match='limit'):
        read_native_log_page(repo,str(giant))


def test_fail_closed_without_native_redactor(tmp_path,monkeypatch):
    repo=tmp_path/'repo';repo.mkdir()
    root=tmp_path/'state'/'devtool'/'logs';root.mkdir(parents=True)
    source=root/'valid.log';source.write_text('SECRET=shouldnotdisplay\n')
    monkeypatch.setenv('XDG_STATE_HOME',str(tmp_path/'state'))
    monkeypatch.setitem(sys.modules,'devtool.logstore',ModuleType('devtool.logstore'))
    with pytest.raises(ImportError):
        read_native_log_page(repo,str(source))
