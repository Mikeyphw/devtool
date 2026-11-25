"""Read-only status projects through the existing M6 interface."""
from __future__ import annotations
import pytest
from devtool.delivery_base_reconcile import BasePinRefused, main


def test_approval_required_and_no_remote_mutation(capsys):
    rc=main(['advance','--id','a'*32,'--old-base-sha','a'*40,
             '--new-base-sha','b'*40,'--pr-number','48','--plain'])
    assert rc==2
    assert 'BASE_PIN_EXPLICIT_APPROVAL_REQUIRED' in capsys.readouterr().err


def test_cli_refuses_bad_source_before_any_git_request(capsys):
    rc=main(['advance','--id','a'*32,'--old-base-sha','bad',
             '--new-base-sha','b'*40,'--pr-number','48','--yes','--json'])
    assert rc==2
    assert 'BASE_PIN_OLD_SHA_INVALID' in capsys.readouterr().err


def test_source_view_uses_existing_operator_status_and_no_second_live():
    import inspect
    from devtool import delivery_base_reconcile as b
    src=inspect.getsource(b._show_status)
    assert 'from .operator_status import project_delivery_status, render_status_text' in src
    assert 'from .operator_rich import render_operator' in src
    assert 'Live(' not in src and 'Console(' not in src
