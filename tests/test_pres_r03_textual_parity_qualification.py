from __future__ import annotations

import asyncio
import importlib.util
import inspect
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from devtool.cli import build_parser
from devtool.textual_control_center import ACTION_SPECS, NAV_KEYS, PAGE_ACTIONS
from devtool.textual_control_center_runtime import (
    CONTROL_CENTER_PAGES,
    InMemoryControlCenterProvider,
    RecordingControlActionDispatcher,
    create_textual_control_center_app,
)
from devtool.textual_execution_host import select_execution_textual_host
from devtool.ui_core.navigation import navigation_shortcut_map
from devtool.ui_core.shortcuts import (
    TUI_NAV_SHORTCUTS,
    TUI_PAGE_ALIASES,
    TUI_PAGE_CHOICES,
    TUI_PAGES,
    assert_tui_shortcuts_collision_free,
    normalize_tui_page,
    shortcut_collisions,
)


class _TTY(io.StringIO):
    def isatty(self) -> bool:
        return True


class _Console:
    def __init__(self) -> None:
        self.items: list[object] = []

    def print(self, value: object) -> None:
        self.items.append(value)


def _payload() -> dict:
    return {
        "runs": [
            {"run_id": "run-a", "status": "running", "command": "validate", "project": "demo"},
            {"run_id": "run-b", "status": "failed", "command": "test", "project": "demo"},
        ],
        "nodes_by_run": {
            "run-a": [
                {"node_id": "root", "label": "Validate", "status": "running", "parent_id": ""},
                {"node_id": "gradle", "label": "Gradle", "status": "running", "parent_id": "root"},
            ]
        },
        "logs": {"path": "/tmp/run-a.log", "lines": ["alpha", "beta"], "bounded": True},
        "diagnostics": [{
            "diagnostic_id": "diag-1", "severity": "error", "summary": "Compile failed",
            "message": "Compiler returned exit 1", "failure_kind": "test_failure",
            "file": "src/Main.kt", "remediation": "Fix compile error", "evidence": ["run-a.log"],
            "source": "execution-store", "run_id": "run-a", "node_id": "gradle",
        }],
        "history": [],
        "targets": [{"name": "devtool", "kind": "python", "runner": "command", "platform": "termux"}],
        "workers": [{"profile": "tablet", "status": "ready", "provider": "ssh-native", "target": "devtool"}],
        "validation": {"active": True, "session_id": "v1", "revision": 1, "state": "running", "phases": [{"key": "lint", "label": "Lint", "status": "running", "attempts": 1}]},
        "artifacts": [{"artifact_id": "a1", "path": "build/app.apk", "artifact_type": "apk", "verification_status": "verified", "run_id": "run-a"}],
        "overlays": [{"history_id": "o1", "artifact": "fix.zip", "status": "ok", "operation": "apply-overlay", "applied_at": "now"}],
        "evidence": [{"evidence_id": "e1", "run_id": "run-a", "file_count": 2, "created_at": "now", "path": "/tmp/e1"}],
    }


def test_r03_shortcuts_are_one_collision_free_authority() -> None:
    assert TUI_PAGES == CONTROL_CENTER_PAGES
    assert NAV_KEYS == TUI_NAV_SHORTCUTS
    assert navigation_shortcut_map("tui") == TUI_NAV_SHORTCUTS
    assert shortcut_collisions() == {}
    assert_tui_shortcuts_collision_free()
    assert TUI_NAV_SHORTCUTS["w"] == "workers"
    assert set(TUI_NAV_SHORTCUTS.values()) == set(TUI_PAGES)


def test_r03_cli_accepts_all_pages_aliases_and_normalizes_build() -> None:
    parser = build_parser()
    for page in TUI_PAGE_CHOICES:
        args = parser.parse_args(["tui", "--page", page])
        assert args.page == page
    assert normalize_tui_page("build") == ("runs", "Legacy TUI page 'build' is now 'runs'.")
    assert normalize_tui_page("ssh")[0] == "workers"
    assert TUI_PAGE_ALIASES["artifact"] == "artifacts"


def test_r03_tui_artifact_argument_is_consumed_by_once_projection(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import devtool.textual_control_center_runtime as runtime
    from devtool.tui_textual import run_textual_tui_runtime

    class Result:
        artifact = "/tmp/fix.zip"
        ok = True
        structural_ok = True
        source_state_ok = True
        conflicts = []
        rebaseable = False
        messages = ["preflight ok"]
        preview = {"files": 2}
        manifest = {"type": "overlay"}

    monkeypatch.setattr(runtime, "check_overlay", lambda repo, artifact: Result())
    console = _Console()
    rc = run_textual_tui_runtime(
        SimpleNamespace(once=True, page="dashboard", width=80, artifact="/tmp/fix.zip"),
        repo_root=tmp_path,
        console=console,
    )
    assert rc == 0
    payload = console.items[0]
    assert isinstance(payload, dict)
    assert payload["page"] == "overlays"
    assert payload["artifact"] == "/tmp/fix.zip"
    assert "Opened Overlays" in payload["compatibility_notice"]
    assert payload["canonical"]["overlays"][0]["kind"] == "startup-overlay-preflight"
    assert payload["canonical"]["overlays"][0]["structural_ok"] is True


def test_r03_static_completion_matches_cli_page_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    expected = "dashboard runs logs diagnostics history targets workers validation artifacts overlays evidence build run log diagnostic worker ssh artifact overlay"
    for rel in ("devtool/completion.py", "completions/_devtool"):
        text = (root / rel).read_text(encoding="utf-8")
        assert f"({expected})" in text


def test_r03_fake_model_only_mutations_are_no_longer_advertised() -> None:
    unsupported = {"target.launch", "artifact.apply", "worker.cancel", "worker.resume"}
    assert unsupported.isdisjoint(ACTION_SPECS)
    assert {"overlay.apply", "worker.recover"}.issubset(ACTION_SPECS)
    assert PAGE_ACTIONS["targets"] == ()
    assert PAGE_ACTIONS["workers"] == ("worker.recover",)
    assert PAGE_ACTIONS["artifacts"] == ()
    assert set(PAGE_ACTIONS["overlays"]) == {"overlay.apply", "rollback", "recover"}
    assert set(PAGE_ACTIONS["validation"]) == {"validation.pause", "validation.stop"}


def test_r03_h02_confirmation_css_uses_textual_supported_sizing() -> None:
    source = Path(__file__).resolve().parents[1] / "devtool" / "textual_apply_host.py"
    text = source.read_text(encoding="utf-8")
    assert "width: min(" not in text
    # Compact Termux UI V2 uses bounded, scrollable phone-safe modal sizing.
    assert "width: 96%; max-width: 72; height: 85%; max-height: 92%;" in text
    assert 'with VerticalScroll(id="apply-confirm-body"):' in text
    assert 'with Horizontal(id="apply-confirm-actions"):' in text


def test_r03_devtool_palette_exclusively_owns_ctrl_p() -> None:
    source = Path(__file__).resolve().parents[1] / "devtool" / "textual_control_center_runtime.py"
    text = source.read_text(encoding="utf-8")
    assert "ENABLE_COMMAND_PALETTE = False" in text
    assert '"ctrl+p": ("show_command_palette", "Commands")' in (
        Path(__file__).resolve().parents[1] / "devtool" / "ui_core" / "shortcuts.py"
    ).read_text(encoding="utf-8")


def test_r03_control_center_projection_lifecycle_is_shutdown_safe() -> None:
    source = Path(__file__).resolve().parents[1] / "devtool" / "textual_control_center_runtime.py"
    text = source.read_text(encoding="utf-8")
    assert "from textual.css.query import NoMatches" in text
    assert "self._projection_live = False" in text
    assert "self._projection_live = True" in text
    assert "self._refresh_timer = self.set_interval" in text
    assert "def _stop_refresh(self)" in text
    assert "def _detach_projection(self)" in text
    assert "if not self._projection_live:" in text
    assert "except NoMatches:" in text
    assert "def on_unmount(self)" in text
    assert "self._detach_projection()" in text
    assert "def on_data_table_row_highlighted" in text
    assert "def on_tree_node_selected" in text
    assert "def action_quit_control_center" in text


def test_r03_normal_execution_host_selects_only_human_interactive_work() -> None:
    args = SimpleNamespace(command="invoke", quiet=False, verbose=False, trace=False, stdout_payload="auto", json=False)
    yes = select_execution_textual_host(args, stdin=_TTY(), stdout=_TTY(), textual_probe=lambda: True, env={})
    assert yes.enabled is True
    assert select_execution_textual_host(args, stdin=io.StringIO(), stdout=_TTY(), textual_probe=lambda: True, env={}).enabled is False
    assert select_execution_textual_host(args, stdin=_TTY(), stdout=_TTY(), textual_probe=lambda: True, env={"CI": "1"}).enabled is False
    assert select_execution_textual_host(args, stdin=_TTY(), stdout=_TTY(), textual_probe=lambda: True, env={"DEVTOOL_EXECUTION_PRESENTATION": "plain"}).enabled is False
    assert select_execution_textual_host(args, stdin=_TTY(), stdout=_TTY(), textual_probe=lambda: True, env={"DEVTOOL_TEXTUAL_EXECUTION_CHILD": "1"}).enabled is False
    machine = SimpleNamespace(command="invoke", quiet=False, verbose=False, trace=False, stdout_payload="auto", json=True)
    assert select_execution_textual_host(machine, stdin=_TTY(), stdout=_TTY(), textual_probe=lambda: True, env={}).enabled is False
    apply_args = SimpleNamespace(command="apply-overlay", quiet=False, verbose=False, trace=False, stdout_payload="auto", json=False)
    assert select_execution_textual_host(apply_args, stdin=_TTY(), stdout=_TTY(), textual_probe=lambda: True, env={}).enabled is False



def test_r03_execution_host_maps_canonical_tasker_state_without_owning_truth() -> None:
    from devtool.textual_execution_host import _phase_snapshot

    snapshot = _phase_snapshot({
        "status": "running",
        "message": "27/90 tasks",
        "phase": {"name": "Gradle", "detail": "assembleDebug", "target": "xdm_android"},
        "progress": {"percent": 30.0, "completed": 27, "total": 90},
    }, command="invoke")
    assert snapshot == {
        "command": "invoke", "phase_name": "Gradle", "target": "xdm_android",
        "detail": "27/90 tasks", "completion_status": "running",
        "percent": 30.0, "completed": 27, "total": 90,
    }


def test_r03_cli_wires_generic_main_thread_host_after_h02_apply_host() -> None:
    import devtool.cli as cli
    source = inspect.getsource(cli._main_impl)
    assert "select_apply_textual_host" in source
    assert "select_execution_textual_host" in source
    assert source.index("select_apply_textual_host") < source.index("select_execution_textual_host")
    assert "run_execution_textual_host(raw_argv, args)" in source


def test_r03_no_prototype_debug_echo_or_hardcoded_app_binding_registry() -> None:
    import devtool.textual_control_center_runtime as runtime
    source = inspect.getsource(runtime.create_textual_control_center_app)
    assert "last_control_result" not in source
    assert "Last control:" not in source
    assert "BINDINGS = textual_control_center_bindings()" in source
    assert "CommandPaletteModal" in source
    assert "shortcut_rows()" in source


def test_r03_campaign_ledger_and_workflow_are_last_implementation_before_gate() -> None:
    root = Path(__file__).resolve().parents[1]
    ledger = json.loads((root / "PRES_R03_PROMISE_LEDGER.json").read_text(encoding="utf-8"))
    assert ledger["overlay"] == "PRES-R03"
    assert ledger["position"] == "63/76"
    assert ledger["gate_window_position"] == "3/4"
    assert ledger["implementation_position"] == "3/3"
    assert ledger["status"] == "qualified_native"
    assert ledger["native_qualification"]["run"] == "9b5fff37ecd14b9c81d294a00540183b"
    assert ledger["native_qualification"]["git_commit"] == "0fd7d0c"
    assert ledger["remaining_before_gate"] == 0
    assert ledger["next_gate"] == "PRES-G2R 64/76"
    assert all(row["status"] == "delivered" for row in ledger["promises"])

    import tomllib
    config = tomllib.loads((root / ".devtool.toml").read_text(encoding="utf-8"))
    assert config["wrapper"]["commands"]["pres-r03"]["workflow"] == "roadmap-pres-r03"
    refs = [row["ref"] for row in config["targets"]["devtool"]["workflows"]["roadmap-pres-r03"]]
    assert refs == [
        "job:source-syntax",
        "test:roadmap-pres-r03-parity-contracts",
        "test:roadmap-pres-r03-pres-regression",
        "test:roadmap-pres-r03-real-textual-apps",
    ]


def _require_textual() -> None:
    if importlib.util.find_spec("textual") is not None:
        return
    if os.environ.get("DEVTOOL_PRES_R03_REQUIRE_TEXTUAL") == "1":
        pytest.fail("PRES-R03 requires Textual for concrete App.run_test()/Pilot qualification")
    pytest.skip("Textual is optional on this construction host")


def test_real_textual_r03_navigation_help_palette_failure_live_resize_and_shutdown(tmp_path: Path) -> None:
    _require_textual()
    from textual.widgets import ContentSwitcher
    from textual.widgets import Button, DataTable, ListView, RichLog

    provider = InMemoryControlCenterProvider(_payload())
    dispatcher = RecordingControlActionDispatcher(responses={"retry": (False, "retry rejected")})
    app = create_textual_control_center_app(
        repo_root=tmp_path,
        page="dashboard",
        provider=provider,
        dispatcher=dispatcher,
        refresh_interval=0.04,
    )
    assert app.ENABLE_COMMAND_PALETTE is False
    assert app.use_command_palette is False

    async def exercise() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            switcher = app.query_one("#page-switcher", ContentSwitcher)
            # Every canonical navigation shortcut changes a concrete visible page.
            for key, page in TUI_NAV_SHORTCUTS.items():
                await pilot.press(key)
                await pilot.pause()
                assert switcher.current == page
                assert app.current_page == page

            # Mouse navigation is claimed on the wide layout and must work.
            provider.update(workers=[{"profile": "tablet", "status": "stale", "provider": "ssh-native", "target": "devtool"}])
            await pilot.click("#nav-workers")
            await pilot.pause(0.08)
            assert switcher.current == "workers"
            worker_table = app.query_one("#workers-table", DataTable)
            worker_table.move_cursor(row=0, column=0, animate=False)
            await pilot.pause()
            await pilot.click("#action-worker-recover")
            await pilot.pause()
            assert app.screen.__class__.__name__ == "ConfirmationModal"
            await pilot.click("#confirm-yes")
            await pilot.pause()
            assert any(call["action_id"] == "worker.recover" and call["payload"].get("profile") == "tablet" for call in dispatcher.calls)

            await pilot.press("o")
            await pilot.pause()
            overlay_table = app.query_one("#overlays-table", DataTable)
            overlay_table.move_cursor(row=0, column=0, animate=False)
            await pilot.pause()
            overlay_apply = app.query_one("#action-overlay-apply", Button)
            overlay_apply.scroll_visible()
            await pilot.pause()
            await pilot.click("#action-overlay-apply")
            await pilot.pause()
            assert app.screen.__class__.__name__ == "ConfirmationModal"
            await pilot.click("#confirm-yes")
            await pilot.pause()
            assert any(call["action_id"] == "overlay.apply" and call["payload"].get("artifact") == "fix.zip" for call in dispatcher.calls)

            # Full help map is a real modal with workers and command-palette rows.
            await pilot.press("?")
            await pilot.pause()
            assert app.screen.__class__.__name__ == "HelpModal"
            table = app.screen.query_one("#shortcut-table", DataTable)
            assert table.row_count >= len(TUI_NAV_SHORTCUTS)
            await pilot.click("#help-close")
            await pilot.pause()

            await pilot.press("ctrl+p")
            await pilot.pause()
            assert app.screen.__class__.__name__ == "CommandPaletteModal"
            assert app.screen.query_one("#palette-list", ListView) is not None
            await pilot.press("escape")
            await pilot.pause()

            # Run selection + action failure goes through the real widget tree.
            await pilot.press("u")
            await pilot.pause()
            run_table = app.query_one("#run-table", DataTable)
            run_table.move_cursor(row=0, column=0, animate=False)
            await pilot.pause()
            assert app.selected_run in {"run-a", "run-b"}
            app.selected_run = "run-a"
            await pilot.press("y")
            await pilot.pause()
            assert any(call["action_id"] == "retry" and call["run_id"] == "run-a" for call in dispatcher.calls)

            # Logs are actual contents and update live from the provider.
            await pilot.press("l")
            await pilot.pause()
            assert app.query_one("#logs-view", RichLog) is not None
            assert app._last_log_lines == ["alpha", "beta"]
            provider.update(logs={"path": "/tmp/run-a.log", "lines": ["gamma"], "bounded": True})
            await pilot.pause(0.12)
            assert app._last_log_lines == ["gamma"]

            # Diagnostics are concrete rows, not a placeholder page.
            await pilot.press("g")
            await pilot.pause()
            assert app.query_one("#diagnostics-table", DataTable).row_count == 1

            # Narrow Termux-like resize hides the rail but keeps keyboard nav alive.
            await pilot.resize_terminal(52, 18)
            await pilot.pause()
            assert app.query_one("#navigation", ListView).has_class("-narrow")
            await pilot.press("w")
            await pilot.pause()
            assert switcher.current == "workers"
            await pilot.resize_terminal(120, 40)
            await pilot.pause()
            assert not app.query_one("#navigation", ListView).has_class("-narrow")

            # Escape is an explicit back operation; Ctrl+C without a run is not quit.
            await pilot.press("escape")
            await pilot.pause()
            assert switcher.current == "dashboard"
            app.selected_run = ""
            await pilot.press("ctrl+c")
            await pilot.pause()
            assert app.is_running

            await pilot.press("q")
            await pilot.pause()

    asyncio.run(exercise())
