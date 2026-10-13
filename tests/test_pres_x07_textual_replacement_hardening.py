from __future__ import annotations
from argparse import Namespace
import pytest
from devtool.cli import build_parser
from devtool.tui_textual import (TextualAvailability, assert_legacy_tui_is_fenced,
    assert_tui_backend_notice_is_safe, build_tui_backend_notice,
    resolve_tui_backend_selection, run_tui_entrypoint,
    summarize_legacy_cleanup_assumptions, textual_replacement_hardening_contract)


def available(): return TextualAvailability(True)
def missing(): return TextualAvailability(False, reason="textual package is not importable")


def test_contract_explicitly_removes_legacy_and_preserves_truth():
    contract=textual_replacement_hardening_contract()
    assert contract["primary_backend"]=="textual"
    assert contract["legacy_backend_status"]=="removed"
    assert not contract["legacy_allowed_when"]
    assert contract["textual_owns_truth"] is False
    assert contract["machine_output_changed"] is False


def test_missing_textual_produces_error_not_fallback():
    selection=resolve_tui_backend_selection(requested_backend="auto",availability=missing())
    notice=build_tui_backend_notice(selection)
    assert notice.severity=="error"
    assert "no legacy fallback" in notice.message
    assert_tui_backend_notice_is_safe(notice)


def test_explicit_legacy_selection_is_rejected():
    with pytest.raises(ValueError,match="removed"):
        resolve_tui_backend_selection(requested_backend="legacy",availability=available())


def test_cleanup_summary_reports_no_legacy_paths():
    choices=[resolve_tui_backend_selection(requested_backend="auto",availability=available()),
             resolve_tui_backend_selection(requested_backend="auto",availability=missing())]
    for item in choices: assert_legacy_tui_is_fenced(item)
    summary=summarize_legacy_cleanup_assumptions(choices)
    assert summary["legacy_path_count"]==0
    assert summary["cleanup_digest"]


def test_parser_explain_backend_and_required_flags():
    args=build_parser().parse_args(["tui","--no-tui-fallback","--explain-backend"])
    assert args.require_textual and args.explain_backend


def test_explain_does_not_launch(tmp_path):
    calls=[]
    args=Namespace(backend="auto",require_textual=False,explain_backend=True,session_transport="ssh")
    assert run_tui_entrypoint(args,repo_root=tmp_path,availability=available(),textual_runner=lambda *a,**kw: calls.append(1))==0
    assert not calls


def test_required_textual_fails_closed(tmp_path):
    args=Namespace(backend="auto",require_textual=True,explain_backend=False,session_transport="local")
    assert run_tui_entrypoint(args,repo_root=tmp_path,availability=missing())==2
