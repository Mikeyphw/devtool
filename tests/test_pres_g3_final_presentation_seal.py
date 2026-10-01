from __future__ import annotations

import asyncio
from io import StringIO
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from rich.console import Console
from rich.text import Text

from devtool.hud import StudioHud, TerminalHud, build_textual_terminal_hud_app, choose_hud_mode
from devtool.pres_g3_gate import run_gate

ROOT = Path(__file__).resolve().parents[1]


def _json(name: str) -> dict:
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def test_pres_g3_campaign_position_and_exec_prerequisite_are_current() -> None:
    ledger = _json("PRES_G3_PROMISE_LEDGER.json")
    assert ledger["position"] == "70/77"
    assert ledger["status"] == "candidate_pending_native_qualification"
    assert ledger["prerequisite"]["EXEC-G3"] == {
        "run": "b9bff06189b04df6b6f7ac4336feb94b",
        "commit": "87baef9",
        "tests": "341 passed, 0 failed, 896 skipped (1237 total)",
        "diagnostics": "0 warnings, 0 errors",
    }
    assert ledger["next_on_success"] == "RMP-X01 71/77"


def test_pres_g3_removes_rich_live_from_all_runtime_presentation_owners() -> None:
    refs: list[str] = []
    for path in (ROOT / "devtool").rglob("*.py"):
        if path.name == "pres_g3_gate.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "rich.live" in text.lower() or "Live(" in text:
            refs.append(path.relative_to(ROOT).as_posix())
    assert refs == []
    hud = (ROOT / "devtool" / "hud.py").read_text(encoding="utf-8")
    assert "build_textual_terminal_hud_app" in hud
    assert "Static rendering is deliberately non-live" in hud


def test_pres_g3_terminal_and_studio_aliases_remain_compatible() -> None:
    assert choose_hud_mode("terminal") == "terminal"
    assert choose_hud_mode("v3") == "terminal"
    assert choose_hud_mode("studio") == "studio"
    assert choose_hud_mode("v5") == "studio"
    assert choose_hud_mode("all") == "studio"


def test_pres_g3_static_terminal_once_is_safe_without_term(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TERM", raising=False)
    out = StringIO()
    hud = TerminalHud(console=Console(file=out, force_terminal=False, width=80))
    assert hud.run(directory=tmp_path, interval_s=0.05, once=True) == 0
    rendered = out.getvalue()
    assert "Devtool" in rendered
    assert "TERM environment variable not set" not in rendered


def test_pres_g3_studio_owns_web_cleanup_but_delegates_terminal_to_textual(tmp_path: Path) -> None:
    calls: dict[str, object] = {}

    class Httpd:
        server_address = ("127.0.0.1", 8765)
        def shutdown(self) -> None:
            calls["shutdown"] = True
        def server_close(self) -> None:
            calls["close"] = True

    httpd = Httpd()
    with patch("devtool.hud.WebDashboardHud.start_background", return_value=(httpd, object())), patch(
        "devtool.hud.TerminalHud.run", return_value=0
    ) as run_terminal:
        rc = StudioHud(host="127.0.0.1", port=0, recent_events=4).run(
            directory=tmp_path,
            interval_s=0.05,
            once=False,
            console=Console(file=StringIO(), force_terminal=False),
            follow_active=True,
        )
    assert rc == 0
    assert calls == {"shutdown": True, "close": True}
    assert run_terminal.call_args.kwargs["follow_active"] is True
    assert run_terminal.call_args.kwargs["once"] is False


def test_pres_g3_fresh_process_gate_closes_noninteractive_terminal_boundary() -> None:
    proof = run_gate(ROOT)
    assert proof["position"] == "70/77"
    assert proof["rich_live_runtime_refs"] == 0
    assert proof["static_terminal_without_term"] == "pass"
    assert proof["next"] == "RMP-X01 71/77"


def test_real_pres_g3_textual_terminal_hud_refreshes_and_quits(tmp_path: Path) -> None:
    try:
        from textual.widgets import Static
    except ImportError:
        if os.environ.get("DEVTOOL_PRES_G3_REQUIRE_TEXTUAL") == "1":
            pytest.fail("PRES-G3 requires Textual for final terminal HUD qualification")
        pytest.skip("Textual unavailable on construction host")

    class Renderer:
        def __init__(self) -> None:
            self.calls = 0
        def render(self, state, events):
            self.calls += 1
            return Text(f"textual-hud-tick-{self.calls}")

    async def exercise() -> None:
        renderer = Renderer()
        app = build_textual_terminal_hud_app(
            directory=tmp_path,
            interval_s=60,
            recent_events=2,
            title="PRES-G3 HUD",
            follow_active=False,
            renderer=renderer,
        )
        async with app.run_test(size=(48, 14)) as pilot:
            await pilot.pause()
            body = app.query_one("#hud-body", Static)
            assert "textual-hud-tick-" in str(body.render())
            before = renderer.calls
            await pilot.press("r")
            await pilot.pause()
            assert renderer.calls > before
            await pilot.press("q")
            await pilot.pause()

    asyncio.run(exercise())
