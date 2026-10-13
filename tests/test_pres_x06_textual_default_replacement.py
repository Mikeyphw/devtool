from __future__ import annotations

from argparse import Namespace
import pytest
from devtool.cli import build_parser
from devtool.tui_textual import (TextualAvailability, assert_tui_backend_selection_is_safe,
    default_tui_backend_contract, resolve_tui_backend_selection, run_tui_entrypoint)


def available(): return TextualAvailability(True)
def missing(): return TextualAvailability(False, reason="textual package is not importable")


def test_auto_selects_textual_without_rich_fallback():
    choice = resolve_tui_backend_selection(requested_backend="auto", availability=available(), session_transport="ssh")
    assert choice.effective_backend == "textual"
    assert choice.fallback_backend is None
    assert choice.command == ("python", "-m", "devtool.tui_textual_runtime")
    assert choice.session_transport == "ssh"
    assert_tui_backend_selection_is_safe(choice)


def test_absent_textual_fails_closed_in_auto_and_explicit_modes():
    for backend in ("auto", "textual"):
        choice = resolve_tui_backend_selection(requested_backend=backend, availability=missing())
        assert choice.effective_backend == "unavailable"
        assert choice.fallback_backend is None
        assert not choice.command


def test_legacy_backend_and_flag_rejected():
    with pytest.raises(ValueError, match="removed"):
        resolve_tui_backend_selection(requested_backend="legacy", availability=available())
    with pytest.raises(ValueError, match="removed"):
        resolve_tui_backend_selection(legacy_tui=True, availability=available())


def test_contract_preserves_renderer_only_truth():
    contract = default_tui_backend_contract()
    assert contract["auto_policy"] == "textual-only-fail-closed"
    assert contract["legacy_override"] is None
    assert contract["textual_owns_truth"] is False
    assert contract["machine_output_changed"] is False


def test_cli_parser_removes_old_backend_entrypoint():
    parser = build_parser()
    args = parser.parse_args(["tui"])
    assert args.backend == "auto"
    assert not hasattr(args, "legacy_tui")
    assert parser.parse_args(["tui", "--backend", "textual"]).backend == "textual"
    with pytest.raises(SystemExit):
        parser.parse_args(["tui", "--backend", "legacy"])
    with pytest.raises(SystemExit):
        parser.parse_args(["tui", "--legacy-tui"])


def test_tui_entrypoint_reports_missing_textual(tmp_path):
    args=Namespace(backend="auto",require_textual=False,session_transport="local")
    assert run_tui_entrypoint(args, repo_root=tmp_path, availability=missing()) == 2


def test_tui_entrypoint_invokes_textual(tmp_path):
    calls=[]
    args=Namespace(backend="auto",require_textual=False,session_transport="ssh")
    def textual_runner(_args, *, repo_root, console=None):
        calls.append(repo_root)
        return 0
    assert run_tui_entrypoint(args, repo_root=tmp_path, availability=available(), textual_runner=textual_runner) == 0
    assert calls == [tmp_path]


def test_textual_runtime_parser_remains_import_safe():
    from devtool.tui_textual_runtime import build_parser
    args=build_parser().parse_args(["--once", "--page", "workers", "--width", "90"])
    assert (args.once,args.page,args.width)==(True,"workers",90)
