from __future__ import annotations

from io import StringIO
import os
from pathlib import Path
import sys
import threading
from unittest.mock import patch

from rich.console import Console

from devtool.execution_presentation import (
    ExecutionPresentationController,
    select_execution_presentation,
)
from devtool.runners import _run_process_live


ROOT = Path(__file__).resolve().parents[1]
TRACKERS = (
    ROOT / "devtool/generic_progress.py",
    ROOT / "devtool/gradle_progress.py",
    ROOT / "devtool/npm_progress.py",
    ROOT / "devtool/dotnet_progress.py",
)


class _TTY:
    def isatty(self) -> bool:
        return True


class _NotTTY:
    def isatty(self) -> bool:
        return False


def _console(*, terminal: bool = True) -> Console:
    return Console(file=StringIO(), force_terminal=terminal, color_system=None, width=90)


def test_progress_trackers_no_longer_own_rich_live_or_terminal_printing() -> None:
    for path in TRACKERS:
        source = path.read_text(encoding="utf-8")
        assert "rich.live" not in source, path.name
        assert "Live(" not in source, path.name
        assert "self.live" not in source, path.name
        assert "console.print(" not in source, path.name


def test_non_tty_ci_verbose_and_external_surfaces_are_plain(monkeypatch) -> None:
    console = _console()
    monkeypatch.setattr("devtool.execution_presentation.textual_available", lambda: True)

    assert select_execution_presentation(console, stdin=_NotTTY()).backend == "plain"
    assert select_execution_presentation(console, verbose=True, stdin=_TTY()).backend == "plain"
    assert select_execution_presentation(console, external_surface=True, stdin=_TTY()).backend == "plain"
    assert select_execution_presentation(console, env={"CI": "1"}, stdin=_TTY()).backend == "plain"
    explicit = select_execution_presentation(
        console,
        env={"DEVTOOL_EXECUTION_PRESENTATION": "plain"},
        stdin=_TTY(),
    )
    assert explicit.backend == "plain"
    assert explicit.hides_raw_output is False


def test_interactive_tty_prefers_textual_without_importing_it_eagerly(monkeypatch) -> None:
    monkeypatch.setattr("devtool.execution_presentation.textual_available", lambda: True)
    decision = select_execution_presentation(_console(), env={}, stdin=_TTY())
    assert decision.backend == "textual"
    assert decision.interactive is True
    assert decision.hides_raw_output is True


def test_controller_routes_snapshots_lines_and_cancel_callback(monkeypatch) -> None:
    monkeypatch.setattr("devtool.execution_presentation.textual_available", lambda: True)
    seen: list[tuple[str, object]] = []

    class Presenter:
        interactive = True
        hides_raw_output = True

        def start(self, snapshot=None):
            seen.append(("start", dict(snapshot or {})))

        def update(self, snapshot):
            seen.append(("update", dict(snapshot)))

        def output_line(self, line):
            seen.append(("line", line))

        def set_stop_callback(self, callback):
            seen.append(("cancel", callback))

        def stop(self):
            seen.append(("stop", None))

    controller = ExecutionPresentationController(
        _console(),
        env={},
        stdin=_TTY(),
        presenter_factory=lambda decision: Presenter(),
    )
    cancelled: list[str] = []
    controller.start({"command": "test", "completed": 0, "total": 1})
    controller.update({"command": "test", "completed": 1, "total": 1})
    controller.output_line("hello")
    controller.set_stop_callback(lambda: cancelled.append("cancel"))
    callback = next(payload for kind, payload in seen if kind == "cancel")
    assert callable(callback)
    callback()
    controller.stop()

    assert cancelled == ["cancel"]
    assert controller.hides_raw_output is True
    assert [kind for kind, _ in seen] == ["start", "update", "line", "cancel", "stop"]


def test_sync_controller_never_starts_textual_terminal_driver_in_background(monkeypatch) -> None:
    """Regression for Termux/Linux signal registration from the R01 presenter thread."""
    monkeypatch.setattr("devtool.execution_presentation.textual_available", lambda: True)
    with patch("devtool.execution_presentation.build_textual_execution_app") as build_app:
        controller = ExecutionPresentationController(_console(), env={}, stdin=_TTY())
        controller.start({"command": "artifact validation", "detail": "running"})
        assert controller.decision.backend == "plain"
        assert controller.hides_raw_output is False
        assert "main-thread presentation host" in controller.decision.reason
        build_app.assert_not_called()
        controller.stop()


def test_worker_thread_presentation_fails_closed_to_plain_without_traceback(monkeypatch) -> None:
    monkeypatch.setattr("devtool.execution_presentation.textual_available", lambda: True)
    results: dict[str, object] = {}

    def worker() -> None:
        try:
            controller = ExecutionPresentationController(_console(), env={}, stdin=_TTY())
            controller.start({"command": "validator", "detail": "worker thread"})
            results["backend"] = controller.decision.backend
            results["reason"] = controller.decision.reason
            results["hidden"] = controller.hides_raw_output
            controller.stop()
        except BaseException as exc:  # pragma: no cover - assertion below exposes it
            results["error"] = exc

    thread = threading.Thread(target=worker, name="pres-h01-artifact-validation")
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert "error" not in results
    assert results["backend"] == "plain"
    assert results["hidden"] is False
    assert "main-thread presentation host" in str(results["reason"])
    assert not any(t.name == "devtool-textual-execution" for t in threading.enumerate())


def test_controller_failure_falls_back_to_plain_without_failing_execution(monkeypatch) -> None:
    monkeypatch.setattr("devtool.execution_presentation.textual_available", lambda: True)

    class BrokenPresenter:
        interactive = True
        hides_raw_output = True

        def start(self, snapshot=None):
            raise RuntimeError("renderer exploded")

        def update(self, snapshot):
            raise AssertionError("must be replaced")

        def output_line(self, line):
            raise AssertionError("must be replaced")

        def set_stop_callback(self, callback):
            raise AssertionError("must be replaced")

        def stop(self):
            return None

    controller = ExecutionPresentationController(
        _console(),
        env={},
        stdin=_TTY(),
        presenter_factory=lambda decision: BrokenPresenter(),
    )
    controller.start({"command": "test"})
    assert controller.decision.backend == "plain"
    assert controller.hides_raw_output is False
    controller.update({"detail": "still running"})
    controller.output_line("still visible through runner")
    controller.stop()


def test_runner_routes_normal_execution_through_presentation_controller(tmp_path: Path, capsys) -> None:
    instances = []

    class FakeController:
        def __init__(self, *args, **kwargs):
            self.hides_raw_output = True
            self.events: list[tuple[str, object]] = []
            instances.append(self)

        def start(self, snapshot=None):
            self.events.append(("start", dict(snapshot or {})))

        def update(self, snapshot):
            self.events.append(("update", dict(snapshot)))

        def output_line(self, line):
            self.events.append(("line", line))

        def set_stop_callback(self, callback):
            self.events.append(("cancel", callback))

        def stop(self):
            self.events.append(("stop", None))

    with patch("devtool.runners.ExecutionPresentationController", FakeController):
        code, output = _run_process_live(
            [sys.executable, "-c", "print('pres-r01-runner-line')"],
            cwd=str(tmp_path),
            env=os.environ.copy(),
            verbose=False,
            command_name="audit",
            progress_parser="generic",
        )

    assert code == 0
    assert "pres-r01-runner-line" in output
    assert "pres-r01-runner-line" not in capsys.readouterr().out
    assert len(instances) == 1
    events = instances[0].events
    assert any(kind == "line" and "pres-r01-runner-line" in str(payload) for kind, payload in events)
    assert any(kind == "update" for kind, _ in events)
    assert callable(next(payload for kind, payload in events if kind == "cancel"))
    assert events[-1][0] == "stop"


def test_plain_runner_keeps_redirected_output_observable(tmp_path: Path, capsys) -> None:
    code, output = _run_process_live(
        [sys.executable, "-c", "print('pres-r01-plain-line')"],
        cwd=str(tmp_path),
        env={**os.environ, "DEVTOOL_EXECUTION_PRESENTATION": "plain"},
        verbose=False,
        command_name="audit",
        progress_parser="generic",
    )
    assert code == 0
    assert "pres-r01-plain-line" in output
    assert "pres-r01-plain-line" in capsys.readouterr().out



def test_tasker_hud_projection_does_not_preempt_local_textual_presentation(tmp_path: Path) -> None:
    """Tasker/HUD is a projection subscriber, not the terminal renderer owner."""
    from devtool.tasker import TaskerProgressSink

    instances = []

    class FakeController:
        def __init__(self, *args, **kwargs):
            self.external_surface = bool(kwargs.get("external_surface"))
            self.hides_raw_output = True
            instances.append(self)

        def start(self, snapshot=None):
            return None

        def update(self, snapshot):
            return None

        def output_line(self, line):
            return None

        def set_stop_callback(self, callback):
            return None

        def stop(self):
            return None

    sink = TaskerProgressSink(
        directory=tmp_path / "hud",
        command="validate",
        repo_root=str(tmp_path),
        repo_name="demo",
        enabled=True,
    )
    with patch("devtool.runners.ExecutionPresentationController", FakeController):
        code, output = _run_process_live(
            [sys.executable, "-c", "print('parallel-projection')"],
            cwd=str(tmp_path),
            env=os.environ.copy(),
            verbose=False,
            command_name="validate",
            progress_parser="generic",
            tasker_progress=sink,
        )

    assert code == 0
    assert "parallel-projection" in output
    assert len(instances) == 1
    assert instances[0].external_surface is False

def test_real_textual_execution_app_updates_and_confirms_cancel() -> None:
    import asyncio
    import pytest

    from devtool.execution_presentation import (
        _TextualExecutionPresenter,
        build_textual_execution_app,
        textual_available,
    )

    if not textual_available():
        if os.environ.get("DEVTOOL_PRES_R01_REQUIRE_TEXTUAL") == "1":
            pytest.fail("PRES-R01 native qualification requires Textual to exercise the real execution app")
        pytest.skip("Textual is not installed on this construction host")

    from textual.widgets import ProgressBar, Static

    presenter = _TextualExecutionPresenter(max_log_lines=80)
    cancelled: list[str] = []
    presenter.set_stop_callback(lambda: cancelled.append("cancel"))
    app = build_textual_execution_app(presenter)

    async def exercise() -> None:
        async with app.run_test(size=(58, 18)) as pilot:
            presenter.update(
                {
                    "command": "assembleDebug",
                    "phase_name": "Gradle",
                    "target": "XDM.Android",
                    "detail": "47/100 tasks",
                    "completed": 47,
                    "total": 100,
                }
            )
            presenter.output_line("Gradle task graph ready: 100 tasks")
            await pilot.pause(0.15)

            status = app.query_one("#execution-status", Static)
            detail = app.query_one("#execution-detail", Static)
            status_text = str(status.content if hasattr(status, "content") else status.render())
            detail_text = str(detail.content if hasattr(detail, "content") else detail.render())
            assert "assembleDebug" in status_text
            assert "XDM.Android" in status_text
            assert "47/100 tasks" in detail_text
            bar = app.query_one("#execution-progress", ProgressBar)
            assert float(bar.progress or 0) == 47.0

            await pilot.press("ctrl+c")
            await pilot.pause()
            assert cancelled == []
            notice = app.query_one("#execution-cancel", Static)
            notice_text = str(notice.content if hasattr(notice, "content") else notice.render())
            assert "Press Ctrl+C again" in notice_text

            await pilot.press("ctrl+c")
            await pilot.pause()
            assert cancelled == ["cancel"]

            await pilot.press("l")
            await pilot.pause()
            assert app.query_one("#execution-log").display is False
            await pilot.press("l")
            await pilot.pause()
            assert app.query_one("#execution-log").display is True

    asyncio.run(exercise())


def test_pres_r01_campaign_refresh_and_reopened_ledger_are_consistent() -> None:
    import json

    plan = (ROOT / "PRES_REMEDIATION_IMPLEMENTATION_ORDER_20261001.md").read_text(encoding="utf-8")
    ledger = json.loads((ROOT / "PRES_REMEDIATION_PROMISE_LEDGER_20261001.json").read_text(encoding="utf-8"))
    report = (ROOT / "PRES_R01_EXECUTION_PRESENTATION_OWNERSHIP_REPORT.md").read_text(encoding="utf-8")

    assert ledger["active_denominator"] == 76
    assert ledger["historical_labels_immutable"] is True
    rows = {row["id"]: row for row in ledger["remediation"]}
    assert rows["PRES-R01"]["position"] == "61/76"
    assert rows["PRES-R02"]["position"] == "62/76"
    assert rows["PRES-R03"]["position"] == "63/76"
    assert rows["PRES-G2R"]["position"] == "64/76"
    assert rows["PRES-G2R"]["status"] == "open_gate"
    assert "65/76 DIST-R01" in plan
    assert "66/76 DIST-G3" in plan
    assert "67/76 TEST-G3" in plan
    assert "68/76 EXEC-G3" in plan
    assert "69/76 PRES-G3" in plan
    assert "76/76 RMP-G3 / GRAND DEVTOOL" in plan
    assert "gate-window overlay 1 of 4; implementation 1 of 3; full-plan 61/76" in report

    reopened = {row["overlay"] for row in ledger["reopened_history"]}
    assert {"PRES-X04", "PRES-X05", "PRES-X06", "PRES-X07", "PRES-X08", "PRES-G1", "PRES-G2"} <= reopened
    r01 = json.loads((ROOT / "PRES_R01_PROMISE_LEDGER.json").read_text(encoding="utf-8"))
    assert r01["position"] == "61/76"
    assert r01["gate_window_position"] == "1/4"
    assert r01["next"] == "PRES-G2R 64/76 after PRES-R03 native qualification"
    promises = {row["id"] for row in r01["promises"]}
    assert "PRES-R01-P013" in promises

    # There is exactly one canonical PRES remediation-order authority.  The
    # refresh is intentionally not duplicated into a parallel roadmap file.
    assert not (ROOT / "PRES_TEXTUAL_RUNTIME_REMEDIATION_ROADMAP_20261001.json").exists()
    assert "The next three PRES remediation items are intentionally **not merged**" in plan
    assert "1. **PRES-R01**" in plan
    assert "2. **PRES-R02**" in plan
    assert "3. **PRES-R03**" in plan


def test_artifact_hook_progress_uses_shared_execution_presentation_controller() -> None:
    source = (ROOT / "devtool/hooks/engine.py").read_text(encoding="utf-8")
    assert "ExecutionPresentationController" in source
    assert "tracker.inspection" not in source
    assert "Rich Live" not in source
    assert "presentation.output_line" in source
    assert "tracker.update_from_line" in source


def test_machine_json_quiet_and_explicit_payload_modes_force_plain_execution() -> None:
    from argparse import Namespace
    from devtool.cli import _machine_safe_execution_presentation

    assert _machine_safe_execution_presentation(Namespace(quiet=True, json=False, _devtool_json=False, machine="", format="", stdout_payload="auto"))
    assert _machine_safe_execution_presentation(Namespace(quiet=False, json=True, _devtool_json=False, machine="", format="", stdout_payload="auto"))
    assert _machine_safe_execution_presentation(Namespace(quiet=False, json=False, _devtool_json=False, machine="compact", format="", stdout_payload="auto"))
    assert _machine_safe_execution_presentation(Namespace(quiet=False, json=False, _devtool_json=False, machine="", format="json", stdout_payload="auto"))
    assert _machine_safe_execution_presentation(Namespace(quiet=False, json=False, _devtool_json=False, machine="", format="", stdout_payload="raw"))
    assert not _machine_safe_execution_presentation(Namespace(quiet=False, json=False, _devtool_json=False, machine="", format="", stdout_payload="auto"))
