from __future__ import annotations

from argparse import Namespace

from devtool.cli import build_parser
from devtool.tui_textual import (
    TextualAvailability,
    assert_tui_backend_selection_is_safe,
    default_tui_backend_contract,
    resolve_tui_backend_selection,
    run_tui_entrypoint,
)


def available() -> TextualAvailability:
    return TextualAvailability(True)


def missing() -> TextualAvailability:
    return TextualAvailability(False, reason="textual package is not importable")


def test_auto_backend_prefers_textual_when_available() -> None:
    selection = resolve_tui_backend_selection(requested_backend="auto", availability=available(), session_transport="ssh")
    assert selection.default_backend == "textual"
    assert selection.effective_backend == "textual"
    assert selection.fallback_backend == "legacy"
    assert selection.command == ("python", "-m", "devtool.tui_textual_runtime")
    assert selection.session_transport == "ssh"
    assert_tui_backend_selection_is_safe(selection)


def test_auto_backend_falls_back_to_legacy_when_textual_missing() -> None:
    selection = resolve_tui_backend_selection(requested_backend="auto", availability=missing())
    assert selection.default_backend == "textual"
    assert selection.effective_backend == "legacy"
    assert selection.fallback_backend == "legacy"
    assert "prefers Textual" in selection.reason
    assert selection.command == ("devtool", "tui", "--backend", "legacy")


def test_explicit_textual_missing_fails_closed() -> None:
    selection = resolve_tui_backend_selection(requested_backend="textual", availability=missing())
    assert selection.effective_backend == "unavailable"
    assert selection.fallback_backend is None
    assert selection.command == ()
    assert selection.require_textual is True


def test_require_textual_in_auto_fails_closed_when_missing() -> None:
    selection = resolve_tui_backend_selection(requested_backend="auto", require_textual=True, availability=missing())
    assert selection.effective_backend == "unavailable"
    assert selection.fallback_backend is None
    assert selection.require_textual is True


def test_legacy_flag_wins_over_auto_default() -> None:
    selection = resolve_tui_backend_selection(requested_backend="auto", legacy_tui=True, availability=available())
    assert selection.effective_backend == "legacy"
    assert selection.legacy_flag is True
    assert selection.default_backend == "textual"


def test_default_backend_contract_documents_no_truth_transfer() -> None:
    contract = default_tui_backend_contract()
    assert contract["default_backend"] == "textual"
    assert contract["auto_policy"] == "prefer-textual-fallback-legacy"
    assert contract["textual_owns_truth"] is False
    assert contract["machine_output_changed"] is False


def test_cli_parser_exposes_default_replacement_flags() -> None:
    args = build_parser().parse_args(["tui"])
    assert args.backend == "auto"
    assert args.legacy_tui is False
    assert args.require_textual is False

    explicit = build_parser().parse_args(["tui", "--backend", "textual", "--require-textual"])
    assert explicit.backend == "textual"
    assert explicit.require_textual is True

    legacy = build_parser().parse_args(["tui", "--legacy-tui"])
    assert legacy.legacy_tui is True


def test_run_entrypoint_calls_legacy_runner_for_auto_missing_textual(tmp_path) -> None:
    calls: list[tuple[str, object]] = []

    def legacy_runner(args: Namespace, *, repo_root, console=None) -> int:
        calls.append((args.backend, repo_root))
        return 17

    args = Namespace(backend="auto", legacy_tui=False, require_textual=False, session_transport="local")
    assert run_tui_entrypoint(args, repo_root=tmp_path, availability=missing(), legacy_runner=legacy_runner) == 17
    assert calls == [("auto", tmp_path)]


def test_run_entrypoint_calls_textual_runner_for_auto_available(tmp_path) -> None:
    calls: list[tuple[str, object]] = []

    def textual_runner(args: Namespace, *, repo_root, console=None) -> int:
        calls.append((args.backend, repo_root))
        return 0

    args = Namespace(backend="auto", legacy_tui=False, require_textual=False, session_transport="ssh")
    assert run_tui_entrypoint(args, repo_root=tmp_path, availability=available(), textual_runner=textual_runner) == 0
    assert calls == [("auto", tmp_path)]


def test_run_entrypoint_fails_for_explicit_textual_when_missing(tmp_path) -> None:
    args = Namespace(backend="textual", legacy_tui=False, require_textual=False, session_transport="local")
    assert run_tui_entrypoint(args, repo_root=tmp_path, availability=missing()) == 2


def test_textual_runtime_module_is_import_safe() -> None:
    import devtool.tui_textual_runtime as runtime

    args = runtime.build_parser().parse_args(["--once", "--page", "workers", "--width", "90"])
    assert args.once is True
    assert args.page == "workers"
    assert args.width == 90
