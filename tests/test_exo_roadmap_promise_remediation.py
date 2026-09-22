from __future__ import annotations

import json
from pathlib import Path

from devtool.core.events import (
    DiagnosticEmitted,
    EventBus,
    NodeFinished,
    NodeStarted,
    TaskFinished,
    ProcessExited,
    ProcessStarted,
)
from devtool.core.execution_limits import (
    MAX_EVIDENCE_REF_CHARS,
    MAX_EVIDENCE_REFS,
    MAX_LOG_REF_CHARS,
)
from devtool.core.execution_subscribers import ExecutionProjectionSubscriber
from devtool.core.events.events import event_from_dict
from devtool.core.events.listeners import event_to_dict


ROOT = Path(__file__).resolve().parents[1]
ANDROID = ROOT / "android/devtool-live-hud/app/src/main/java/io/github/mikeceead/devtoolhud"


def test_evidence_and_log_references_are_bounded_in_snapshot_and_journal(tmp_path):
    bus = EventBus()
    sub = ExecutionProjectionSubscriber(store=bus.execution_store, run_id="bounds", directory=tmp_path)
    bus.subscribe_all(sub)

    refs = tuple(f"/evidence/{index}-" + ("x" * 3000) for index in range(100))
    huge_log = "/logs/" + ("l" * 6000)

    bus.publish(NodeStarted(run_id="bounds", node_id="node", label="Node"))
    bus.publish(ProcessStarted(
        run_id="bounds",
        node_id="node",
        process_id="process",
        pid=7,
        argv=("tool",),
        cwd="/repo",
    ))
    bus.publish(ProcessExited(
        run_id="bounds",
        node_id="node",
        process_id="process",
        exit_code=1,
        log_path=huge_log,
        evidence=refs,
    ))
    bus.publish(DiagnosticEmitted(
        run_id="bounds",
        node_id="node",
        diagnostic_id="diag",
        severity="error",
        message="failed",
        process_id="process",
        log_path=huge_log,
        evidence=refs,
    ))
    bus.publish(NodeFinished(
        run_id="bounds",
        node_id="node",
        status="failed",
        exit_code=1,
        process_id="process",
        diagnostic_ids=("diag",),
        evidence=refs,
    ))

    snapshot = bus.execution_store.snapshot("bounds")
    node = snapshot.node("node")
    process = snapshot.processes[0]
    diagnostic = snapshot.diagnostics[0]

    assert len(node.evidence_refs) <= MAX_EVIDENCE_REFS
    assert len(process.evidence) <= MAX_EVIDENCE_REFS
    assert len(diagnostic.evidence) <= MAX_EVIDENCE_REFS
    assert all(len(value) <= MAX_EVIDENCE_REF_CHARS for value in node.evidence_refs)
    assert all(len(value) <= MAX_EVIDENCE_REF_CHARS for value in process.evidence)
    assert all(len(value) <= MAX_EVIDENCE_REF_CHARS for value in diagnostic.evidence)
    assert len(process.log_path) <= MAX_LOG_REF_CHARS
    assert len(diagnostic.log_path) <= MAX_LOG_REF_CHARS

    journal = [
        json.loads(line)
        for line in (tmp_path / "execution-events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    for payload in journal:
        if isinstance(payload.get("evidence"), list):
            assert len(payload["evidence"]) <= MAX_EVIDENCE_REFS
            assert all(len(value) <= MAX_EVIDENCE_REF_CHARS for value in payload["evidence"])
        if "log_path" in payload:
            assert len(str(payload["log_path"])) <= MAX_LOG_REF_CHARS


def test_android_sqlite_persists_canonical_execution_graph():
    db = (ANDROID / "DevtoolDatabase.java").read_text()
    assert "private static final int VERSION = 11;" in db
    assert "execution_json TEXT NOT NULL DEFAULT '{}'" in db
    assert "execution_schema_version INTEGER NOT NULL DEFAULT 0" in db
    assert "execution_sequence INTEGER NOT NULL DEFAULT -1" in db
    assert "execution_event_id TEXT NOT NULL DEFAULT ''" in db
    assert 'if (oldVersion < 11)' in db
    assert 'value.put("execution_json", run.executionJson)' in db
    assert 'run.executionJson = string(cursor, "execution_json")' in db
    assert 'run.executionSequence = number(cursor, "execution_sequence")' in db


def test_android_persistent_graph_keeps_dependencies_blocked_status_and_partial_failure_progress():
    graph = (ANDROID / "ExecutionGraphState.java").read_text()
    update = (ANDROID / "HudUpdate.java").read_text()
    main = (ANDROID / "MainActivity.java").read_text()
    overlay = (ANDROID / "OverlayHudService.java").read_text()
    record = (ANDROID / "RunRecord.java").read_text()

    assert 'dependsOn = strings(value.optJSONArray("depends_on"))' in graph
    assert 'blockedBy = strings(value.optJSONArray("blocked_by"))' in graph
    assert 'if (status.equals("blocked")) return "failed";' not in graph
    assert 'status.equals("blocked")' in update
    assert 'status.equals("blocked")' in record

    assert 'ExecutionGraphState.parse(record.executionJson)' in main
    assert 'step.relationshipText()' in main
    assert 'step.relationshipText()' in overlay

    forced_terminal_100 = '(step.succeeded() || step.failed() || step.skipped()) ? 100 : step.progress'
    assert forced_terminal_100 not in main
    assert forced_terminal_100 not in overlay
    assert 'step.progressKnown() ? step.progress : 0' in main
    assert 'step.progressKnown() ? step.progress : 0' in overlay

    assert 'item.put("depends_on", new JSONArray(step.dependsOn))' in update
    assert 'item.put("blocked_by", new JSONArray(step.blockedBy))' in update


def test_all_original_exo_contract_modules_remain_present():
    for number in range(1, 12):
        matches = list((ROOT / "tests").glob(f"test_exo{number:02d}_*.py"))
        assert matches, f"missing EXO-{number:02d} acceptance contract"


def test_synthetic_transition_provenance_is_first_class_and_round_trips():
    bus = EventBus()
    bus.publish(NodeStarted(run_id="provenance", node_id="node", label="Node"))
    event = NodeFinished(
        run_id="provenance",
        node_id="node",
        status="blocked",
        exit_code=0,
        transition_origin="scheduler_inferred",
        transition_reason="dependency compile failed",
    )
    encoded = event_to_dict(event)
    decoded = event_from_dict(encoded)
    assert decoded.transition_origin == "scheduler_inferred"
    assert decoded.transition_reason == "dependency compile failed"

    bus.publish(event)
    snapshot = bus.execution_store.snapshot("provenance")
    node = snapshot.node("node")
    assert snapshot.schema_version == 5
    assert node.status == "blocked"
    assert node.transition_origin == "scheduler_inferred"
    assert node.transition_reason == "dependency compile failed"
    assert node.to_dict()["transition_origin"] == "scheduler_inferred"

    compatibility = TaskFinished(
        run_id="compat-provenance",
        node_id="node",
        task="Node",
        success=True,
        status="skipped",
        metadata={
            "workflow_state": "skipped",
            "workflow_transition_origin": "scheduler_inferred",
            "workflow_skip_reason": "fail-fast stopped independent node",
        },
    )
    assert compatibility.transition_origin == "scheduler_inferred"
    assert compatibility.transition_reason == "fail-fast stopped independent node"


def test_workflow_synthetic_transitions_publish_explicit_provenance():
    runners = (ROOT / "devtool/runners.py").read_text()
    assert 'transition_origin="scheduler_inferred"' in runners
    assert 'transition_reason=synthetic_reason' in runners
    assert '"workflow_transition_origin": "scheduler_inferred"' in runners


def test_android_compatibility_projection_preserves_transition_provenance():
    graph = (ANDROID / "ExecutionGraphState.java").read_text()
    update = (ANDROID / "HudUpdate.java").read_text()
    assert 'transitionOrigin = value.optString("transition_origin", "")' in graph
    assert 'transitionReason = value.optString("transition_reason", "")' in graph
    assert 'item.put("transition_origin", step.transitionOrigin)' in update
    assert 'item.put("transition_reason", step.transitionReason)' in update
