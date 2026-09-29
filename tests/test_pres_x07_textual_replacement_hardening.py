from __future__ import annotations

from argparse import Namespace

from devtool.cli import build_parser
from devtool.tui_textual import (
    TextualAvailability,
    assert_legacy_tui_is_fenced,
    assert_tui_backend_notice_is_safe,
    build_tui_backend_notice,
    resolve_tui_backend_selection,
    run_tui_entrypoint,
    summarize_legacy_cleanup_assumptions,
    textual_replacement_hardening_contract,
)


def available() -> TextualAvailability:
    return TextualAvailability(True)


def missing() -> TextualAvailability:
    return TextualAvailability(False, reason="textual package is not importable")


def test_hardening_contract_keeps_textual_primary_and_truth_external() -> None:
    contract = textual_replacement_hardening_contract()
    assert contract["primary_backend"] == "textual"
    assert contract["legacy_backend_status"] == "fallback-only"
    assert contract["textual_owns_truth"] is False
    assert contract["machine_output_changed"] is False
    assert "--backend textual was requested" in contract["legacy_not_allowed_when"]


def test_backend_notice_explains_auto_legacy_fallback() -> None:
    selection = resolve_tui_backend_selection(requested_backend="auto", availability=missing())
    notice = build_tui_backend_notice(selection)
    assert notice.severity == "warning"
    assert "legacy fallback" in notice.message
    assert any("--require-textual" in item for item in notice.remediations)
    assert notice.fallback_explicit is False
    assert_tui_backend_notice_is_safe(notice)


def test_backend_notice_explains_explicit_legacy_request() -> None:
    selection = resolve_tui_backend_selection(requested_backend="legacy", availability=available())
    notice = build_tui_backend_notice(selection)
    assert notice.severity == "notice"
    assert notice.fallback_explicit is True
    assert "explicitly requested" in notice.message


def test_legacy_fence_allows_only_explicit_or_auto_missing_textual() -> None:
    explicit = resolve_tui_backend_selection(requested_backend="legacy", availability=available())
    auto_fallback = resolve_tui_backend_selection(requested_backend="auto", availability=missing())
    textual = resolve_tui_backend_selection(requested_backend="auto", availability=available())
    assert_legacy_tui_is_fenced(explicit)
    assert_legacy_tui_is_fenced(auto_fallback)
    assert_legacy_tui_is_fenced(textual)


def test_cleanup_summary_counts_only_fenced_legacy_paths() -> None:
    explicit = resolve_tui_backend_selection(requested_backend="legacy", availability=available())
    auto_fallback = resolve_tui_backend_selection(requested_backend="auto", availability=missing())
    textual = resolve_tui_backend_selection(requested_backend="auto", availability=available())
    summary = summarize_legacy_cleanup_assumptions((explicit, auto_fallback, textual))
    assert summary["primary_backend"] == "textual"
    assert summary["legacy_path_count"] == 2
    assert summary["owns_truth"] is False
    assert summary["cleanup_digest"]


def test_cli_parser_exposes_no_fallback_and_explain_backend_flags() -> None:
    args = build_parser().parse_args(["tui", "--no-tui-fallback", "--explain-backend"])
    assert args.require_textual is True
    assert args.explain_backend is True


def test_explain_backend_exits_before_launching_runtime(tmp_path) -> None:
    calls: list[str] = []

    def textual_runner(args: Namespace, *, repo_root, console=None) -> int:
        calls.append("textual")
        return 3

    args = Namespace(
        backend="auto",
        legacy_tui=False,
        require_textual=False,
        explain_backend=True,
        session_transport="ssh",
    )
    assert run_tui_entrypoint(args, repo_root=tmp_path, availability=available(), textual_runner=textual_runner) == 0
    assert calls == []


def test_no_fallback_alias_fails_closed_when_textual_missing(tmp_path) -> None:
    args = Namespace(
        backend="auto",
        legacy_tui=False,
        require_textual=True,
        explain_backend=False,
        session_transport="local",
    )
    assert run_tui_entrypoint(args, repo_root=tmp_path, availability=missing()) == 2
