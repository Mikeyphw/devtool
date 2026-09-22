from __future__ import annotations

import pytest

from devtool.core.events import (
    EventBus,
    GraphSealed,
    NodeDiscovered,
    NodeFinished,
    NodeProgressChanged,
    NodeStarted,
    RunFinished,
    RunStarted,
    event_from_dict,
)
from devtool.core.events.listeners import event_to_dict
from devtool.core.execution_store import ExecutionTransitionError
from devtool.ui_core.progress import summarize_progress
from devtool.ui_core.state import UIProgressState


def _hierarchy(bus: EventBus, run_id: str = "account") -> None:
    bus.publish(RunStarted(run_id=run_id, command="build"))
    bus.publish(NodeDiscovered(run_id=run_id, node_id="download", label="Download", kind="download"))
    bus.publish(NodeDiscovered(run_id=run_id, node_id="build", label="Build", kind="build"))
    bus.publish(
        NodeDiscovered(
            run_id=run_id,
            node_id="pkg-a",
            parent_id="build",
            label="package/a",
            kind="compile",
            metadata={"progress_weight": 3},
        )
    )
    bus.publish(
        NodeDiscovered(
            run_id=run_id,
            node_id="pkg-b",
            parent_id="build",
            label="package/b",
            kind="compile",
            metadata={"progress_weight": 1},
        )
    )


def test_accounting_scopes_avoid_parent_child_double_counting() -> None:
    bus = EventBus()
    _hierarchy(bus)

    snapshot = bus.execution_store.snapshot("account")
    assert snapshot.accounting.direct.counts.total == 2  # Download + Build
    assert snapshot.accounting.recursive.counts.total == 4  # all graph nodes
    assert snapshot.accounting.leaf.counts.total == 3  # Download + two packages

    build_direct = bus.execution_store.accounting("account", parent_id="build", scope="direct")
    build_leaf = bus.execution_store.accounting("account", parent_id="build", scope="leaf")
    assert build_direct.counts.total == 2
    assert build_leaf.counts.total == 2


def test_measured_work_is_weighted_and_separate_from_resolution() -> None:
    bus = EventBus()
    _hierarchy(bus, "weighted")

    bus.publish(NodeStarted(run_id="weighted", node_id="pkg-a", label="package/a"))
    bus.publish(NodeProgressChanged(run_id="weighted", node_id="pkg-a", percent=25, current=1, total=4))
    bus.publish(NodeFinished(run_id="weighted", node_id="pkg-a", status="failed", exit_code=1))

    bus.publish(NodeStarted(run_id="weighted", node_id="pkg-b", label="package/b"))
    bus.publish(NodeProgressChanged(run_id="weighted", node_id="pkg-b", percent=75, current=3, total=4))

    build = bus.execution_store.accounting("weighted", parent_id="build", scope="leaf")
    assert build.counts.total == 2
    assert build.counts.failed == 1
    assert build.counts.running == 1
    assert build.progress.measured_percent == pytest.approx(37.5)  # (25*3 + 75*1) / 4
    assert build.progress.coverage_percent == pytest.approx(100.0)
    assert build.progress.resolution_percent == pytest.approx(50.0)
    assert build.progress.determinate is True

    snapshot = bus.execution_store.snapshot("weighted")
    assert snapshot.node("pkg-a").progress.percent == 25  # failure is not fake 100%


def test_terminal_without_measurement_stays_unmeasured() -> None:
    bus = EventBus()
    bus.publish(NodeStarted(run_id="unmeasured", node_id="measured", label="Measured"))
    bus.publish(NodeProgressChanged(run_id="unmeasured", node_id="measured", percent=40))
    bus.publish(NodeFinished(run_id="unmeasured", node_id="measured", status="succeeded"))
    bus.publish(NodeStarted(run_id="unmeasured", node_id="plain", label="Plain"))
    bus.publish(NodeFinished(run_id="unmeasured", node_id="plain", status="succeeded"))

    accounting = bus.execution_store.accounting("unmeasured", scope="leaf")
    assert accounting.counts.succeeded == 2
    assert accounting.progress.resolution_percent == 100
    assert accounting.progress.measured_percent == 40
    assert accounting.progress.coverage_percent == 50
    assert accounting.progress.unmeasured_nodes == 1
    assert accounting.progress.determinate is False


def test_graph_seal_marks_total_final_and_rejects_new_nodes() -> None:
    bus = EventBus()
    bus.publish(RunStarted(run_id="sealed", command="build"))
    bus.publish(NodeDiscovered(run_id="sealed", node_id="known", label="Known"))
    before = bus.execution_store.snapshot("sealed")
    assert before.graph_sealed is False
    assert before.accounting.recursive.progress.total_is_final is False

    seal = GraphSealed(run_id="sealed", reason="planner and dynamic discovery complete")
    bus.publish(seal)
    sealed = bus.execution_store.snapshot("sealed")
    assert sealed.graph_sealed is True
    assert sealed.graph_seal_reason == "planner and dynamic discovery complete"
    assert sealed.accounting.recursive.progress.total_is_final is True

    with pytest.raises(ExecutionTransitionError):
        bus.publish(NodeDiscovered(run_id="sealed", node_id="late", label="Late"))
    with pytest.raises(ExecutionTransitionError):
        bus.publish(NodeStarted(run_id="sealed", node_id="late-start", label="Late start"))

    # Existing nodes may continue progressing after discovery is sealed.
    bus.publish(NodeStarted(run_id="sealed", node_id="known", label="Known"))
    bus.publish(NodeProgressChanged(run_id="sealed", node_id="known", percent=43))
    bus.publish(NodeFinished(run_id="sealed", node_id="known", status="failed", exit_code=1))
    bus.publish(RunFinished(run_id="sealed", status="failed", exit_code=1))
    final = bus.execution_store.snapshot("sealed")
    assert final.node("known").progress.percent == 43
    assert final.accounting.recursive.progress.resolution_percent == 100
    assert final.accounting.recursive.progress.measured_percent == 43


def test_graph_sealed_event_round_trips() -> None:
    bus = EventBus()
    event = GraphSealed(run_id="roundtrip", reason="done")
    bus.publish(event)
    decoded = event_from_dict(event_to_dict(event))
    assert isinstance(decoded, GraphSealed)
    assert decoded.reason == "done"
    assert decoded.event_id == event.event_id


def test_ui_progress_summary_no_longer_invents_terminal_100_percent() -> None:
    rows = [
        UIProgressState(id="a", label="A", status="ok", token="status.success"),
        UIProgressState(id="b", label="B", status="failed", token="status.failed", percent=43),
    ]
    summary = summarize_progress(rows)
    assert summary.total_nodes == 2
    assert summary.completed_nodes == 2
    assert summary.succeeded_nodes == 1
    assert summary.failed_nodes == 1
    assert summary.resolution_percent == 100
    assert summary.measured_percent == 43
    assert summary.overall_percent == 43
    assert summary.measurement_coverage_percent == 50
