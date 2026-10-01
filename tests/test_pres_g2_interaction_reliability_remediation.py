from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import hashlib

import pytest

from devtool.textual_control_center import (
    ACTION_SPECS,
    NAV_KEYS,
    PAGE_ACTIONS,
    PANEL_AUTHORITIES,
    PAGES,
    TerminalRestorationGuard,
    TextualControlCenter,
    render_control_center_text,
)
from devtool.tui_textual import run_textual_tui_runtime


class _Console:
    def __init__(self) -> None:
        self.items: list[object] = []

    def print(self, value: object) -> None:
        self.items.append(value)


def _digest_tree(root: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        h.update(path.relative_to(root).as_posix().encode())
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def test_overlay_page_and_collision_free_navigation_are_explicit() -> None:
    assert "overlays" in PAGES
    assert NAV_KEYS["o"] == "overlays"
    assert len(NAV_KEYS) == len(set(NAV_KEYS))
    assert len(NAV_KEYS.values()) == len(set(NAV_KEYS.values()))
    assert set(NAV_KEYS.values()) <= set(PAGES)


def test_panel_authorities_cover_every_page_without_guessing_commands() -> None:
    assert set(PANEL_AUTHORITIES) == set(PAGES)
    assert PANEL_AUTHORITIES["workers"] == "DIST/SSH"
    assert PANEL_AUTHORITIES["artifacts"] == "LIFE"
    assert PANEL_AUTHORITIES["overlays"] == "LIFE"
    assert PANEL_AUTHORITIES["targets"] == "EXEC/WRAP/TEST"


def test_one_source_failure_degrades_only_its_panel() -> None:
    center = TextualControlCenter(page="workers")
    center.degrade_panel("workers", "ssh-source-unavailable")
    snapshot = center.snapshot()
    assert snapshot["panel_health"]["workers"]["status"] == "degraded"
    assert snapshot["panel_health"]["workers"]["reason"] == "ssh-source-unavailable"
    assert snapshot["panel_health"]["runs"]["status"] == "healthy"
    center.recover_panel("workers")
    assert center.snapshot()["panel_health"]["workers"]["status"] == "healthy"


def test_multi_run_selection_survives_attention_reordering() -> None:
    center = TextualControlCenter()
    center.ingest({"kind": "node", "run_id": "run-b", "node_id": "b1", "state": "running"})
    center.ingest({"kind": "node", "run_id": "run-a", "node_id": "a1", "state": "passed"})
    center.select(run_id="run-a", node_id="a1")
    before = center.snapshot()
    center.ingest({"kind": "node", "run_id": "run-b", "node_id": "b2", "state": "failed"})
    after = center.snapshot()
    assert before["selected_run"] == after["selected_run"] == "run-a"
    assert after["runs"][0]["run_id"] == "run-b"


def test_logs_and_diagnostics_are_distinct_semantic_surfaces() -> None:
    center = TextualControlCenter()
    center.ingest({"kind": "log", "run_id": "r", "text": "hello"})
    center.ingest({"kind": "diagnostic", "run_id": "r", "state": "failed", "code": "E1"})
    snapshot = center.snapshot()
    assert len(snapshot["log_lines"]) == 1
    assert any(event.get("kind") == "diagnostic" for event in center.events)
    assert all(event.get("kind") != "log" for event in center.events)
    assert NAV_KEYS["l"] == "logs"
    assert NAV_KEYS["g"] == "diagnostics"


def test_visible_actions_are_registry_backed_and_disabled_reasons_are_explicit() -> None:
    center = TextualControlCenter(page="runs")
    rows = center.visible_actions()
    assert {row["id"] for row in rows} == set(PAGE_ACTIONS["runs"])
    assert all(row["id"] in ACTION_SPECS for row in rows)
    assert all(row["enabled"] is False and row["disabled_reason"] == "select-a-run" for row in rows)
    center.select(run_id="run-1")
    assert all(row["enabled"] for row in center.visible_actions())


def test_unknown_action_is_rejected_instead_of_becoming_guessed_presentation_action() -> None:
    center = TextualControlCenter()
    with pytest.raises(KeyError, match="unsupported control-center action"):
        center.action_request("shell:rm -rf")


def test_domain_actions_retain_owner_and_confirmation_policy() -> None:
    center = TextualControlCenter(page="runs", selected_run="run-1")
    retry = center.action_request("retry")
    assert retry.authority == "EXO/RUN" and retry.confirmation_required is False
    center.navigate("overlays")
    # PRES-R03 adopts overlay apply as a concrete delegated action while keeping
    # lifecycle ownership in LIFE and requiring canonical confirmation.
    with pytest.raises(PermissionError, match="requires canonical confirmation"):
        center.action_request("overlay.apply")
    apply = center.action_request("overlay.apply", confirmed=True)
    assert apply.authority == "LIFE" and apply.confirmation_required is True
    assert {row["id"] for row in center.visible_actions()} == {"overlay.apply", "rollback", "recover"}


def test_ctrl_c_q_and_escape_have_truthful_non_overlapping_semantics() -> None:
    center = TextualControlCenter(selected_run="run-1")
    assert center.handle_key("ctrl+c")["kind"] == "confirmation-required"
    cancel = center.handle_key("ctrl+c", confirmed=True)
    assert cancel["kind"] == "action-request"
    assert cancel["request"]["action_id"] == "cancel"
    assert center.handle_key("q") == {"kind": "exit", "cancel_requested": False, "key": "q"}
    assert center.handle_key("escape") == {"kind": "exit", "cancel_requested": False, "key": "escape"}


def test_ctrl_c_without_selected_run_is_explicitly_disabled() -> None:
    result = TextualControlCenter().handle_key("ctrl+c", confirmed=True)
    assert result == {"kind": "disabled", "action_id": "cancel", "reason": "select-a-run"}


def test_terminal_guard_restores_after_normal_exit_and_exception() -> None:
    guard = TerminalRestorationGuard()
    with guard:
        assert guard.active is True
    assert guard.restored is True and guard.active is False

    failing = TerminalRestorationGuard()
    with pytest.raises(RuntimeError):
        with failing:
            raise RuntimeError("renderer boom")
    assert failing.restored is True and failing.active is False


def test_plain_text_projection_exposes_authority_and_disabled_actions() -> None:
    center = TextualControlCenter(page="workers")
    text = render_control_center_text(center.snapshot(term="dumb"))
    assert "Page: workers" in text
    assert "authority=DIST/SSH" in text
    assert "worker.cancel" not in text
    assert "ctrl+c=cancel" in text


def test_once_runtime_is_read_only_and_renderer_independent(tmp_path: Path) -> None:
    (tmp_path / "tracked.txt").write_text("stable\n")
    before = _digest_tree(tmp_path)
    console = _Console()
    rc = run_textual_tui_runtime(
        SimpleNamespace(once=True, page="runs", width=80),
        repo_root=tmp_path,
        console=console,
    )
    after = _digest_tree(tmp_path)
    assert rc == 0
    assert before == after
    assert console.items and isinstance(console.items[0], dict)
    assert console.items[0]["backend"] == "textual"


def test_snapshot_has_no_shell_command_layer() -> None:
    center = TextualControlCenter(page="targets")
    encoded = repr(center.snapshot()).lower()
    assert "shell_command" not in encoded
    assert "command_string" not in encoded
    assert center.visible_actions() == []
