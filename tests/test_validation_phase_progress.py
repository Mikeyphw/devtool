from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import call, patch

from devtool.cli import (
    _announce_validation_phase_finished,
    _announce_validation_phase_started,
    _announce_validation_session_completed,
)
from devtool.hud import friendly_event_line
from devtool.tasker import _human_event_message
from devtool.validation_sessions import completion_result, pause_result


def _args(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {"json": False, "machine": ""}
    values.update(overrides)
    return SimpleNamespace(**values)


def _phase(index: int, total: int, label: str, status: str) -> SimpleNamespace:
    return SimpleNamespace(index=index, total=total, label=label, name=label, status=status)


def test_terminal_announces_phase_start_and_success_once() -> None:
    phase = _phase(3, 9, "compile", "succeeded")
    session = SimpleNamespace(phases=[
        _phase(1, 9, "preflight", "succeeded"),
        _phase(2, 9, "dependencies", "succeeded"),
        phase,
        *[_phase(index, 9, f"phase-{index}", "pending") for index in range(4, 10)],
    ])

    with patch("devtool.cli.console.print") as printer:
        _announce_validation_phase_started(_args(), phase)
        _announce_validation_phase_finished(_args(), session, phase)

    messages = [str(item.args[0]) for item in printer.call_args_list if item.args]
    assert messages.count("[bold green]✓ VALIDATION PHASE 3 OF 9 COMPLETED[/bold green]") == 1
    assert "[bold cyan]▶ VALIDATION PHASE 3 OF 9[/bold cyan]" in messages
    assert any("Overall 3 of 9 phases complete" in message for message in messages)


def test_terminal_completion_banner_is_unmistakable() -> None:
    session = SimpleNamespace(session_id="validation-demo", phases=[
        _phase(index, 4, f"phase-{index}", "succeeded") for index in range(1, 5)
    ])

    with patch("devtool.cli.console.print") as printer:
        _announce_validation_session_completed(_args(), session)

    printer.assert_has_calls([
        call(),
        call("[bold green]════════ VALIDATION COMPLETE ════════[/bold green]"),
        call("[bold green]✓ 4 of 4 phases completed successfully[/bold green]"),
        call("[dim]Session validation-demo is finalized. No validation phases remain.[/dim]"),
    ])


def test_machine_modes_do_not_pollute_structured_stdout() -> None:
    phase = _phase(1, 2, "preflight", "succeeded")
    session = SimpleNamespace(phases=[phase, _phase(2, 2, "compile", "pending")])
    with patch("devtool.cli.console.print") as printer:
        _announce_validation_phase_started(_args(json=True), phase)
        _announce_validation_phase_finished(_args(machine="tasker"), session, phase)
    printer.assert_not_called()


def test_pause_and_completion_results_explain_boundaries() -> None:
    phases = [
        _phase(1, 3, "preflight", "succeeded"),
        _phase(2, 3, "compile", "pending"),
        _phase(3, 3, "finalize", "pending"),
    ]
    session = SimpleNamespace(
        session_id="validation-demo",
        phases=phases,
        next_pending=lambda: phases[1],
        apply_context={},
        transaction_path="",
        options={},
    )

    paused = pause_result(session, "requested phase boundary reached")
    assert "Overall progress: 1 of 3 phases completed" in paused.output
    assert "Last completed: phase 1 of 3 · preflight" in paused.output
    assert "Next phase: 2 of 3 · compile" in paused.output

    complete_session = SimpleNamespace(
        session_id="validation-done",
        phases=[_phase(index, 3, f"phase-{index}", "succeeded") for index in range(1, 4)],
        apply_context={},
        options={},
    )
    completed = completion_result(complete_session)
    assert completed.name == "validation-session-complete"
    assert "VALIDATION COMPLETE" in completed.output
    assert "Completed: 3 of 3 phases" in completed.output
    assert "No pending validation phases remain" in completed.output


def test_tasker_and_hud_messages_include_validation_counter() -> None:
    metadata = {"validation_phase_index": 4, "validation_phase_total": 7}
    assert _human_event_message(
        "phase_started", phase_name="unit-test", metadata=metadata
    ) == "Phase 4 of 7 · Starting Unit Test"
    assert _human_event_message(
        "phase_finished", phase_name="unit-test", status="ok", metadata=metadata
    ) == "Phase 4 of 7 · Unit Test completed"

    started = friendly_event_line({
        "event_type": "phase-started",
        "phase": {"name": "unit-test", "index": 4, "total": 7},
        "status": "running",
    })
    completed = friendly_event_line({
        "event_type": "phase-finished",
        "phase": {"name": "unit-test", "index": 4, "total": 7},
        "result": {"status": "ok"},
    })
    assert started == "Phase 4 of 7 · Starting Unit Test"
    assert completed == "Phase 4 of 7 · Unit Test completed"
