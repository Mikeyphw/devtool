from __future__ import annotations
import json
from pathlib import Path

from devtool.core.events import (
    DiagnosticEmitted, EventBus, GraphSealed, NodeDiscovered, NodeFinished,
    NodeProgressChanged, NodeStarted, RunFinished, RunStarted,
)
from devtool.core.execution_subscribers import ExecutionProjectionSubscriber
from devtool.hud import AndroidAppHud, state_from_directory


def _canonical_run(root: Path):
    bus = EventBus()
    sub = ExecutionProjectionSubscriber(store=bus.execution_store, run_id="android-exo10", directory=root)
    bus.subscribe_all(sub)
    bus.publish(RunStarted(run_id="android-exo10", command="build", project="demo"))
    for node_id, label, kind in [
        ("download", "Download SDK", "download"),
        ("compile", "Compile", "gradle-task"),
        ("zip", "Create ZIP", "artifact"),
    ]:
        bus.publish(NodeDiscovered(run_id="android-exo10", node_id=node_id, label=label, kind=kind))
    bus.publish(GraphSealed(run_id="android-exo10"))
    bus.publish(NodeStarted(run_id="android-exo10", node_id="download", label="Download SDK", kind="download"))
    bus.publish(NodeProgressChanged(run_id="android-exo10", node_id="download", percent=100, current=1, total=1, unit="file"))
    bus.publish(NodeFinished(run_id="android-exo10", node_id="download", status="succeeded"))
    bus.publish(NodeStarted(run_id="android-exo10", node_id="compile", label="Compile", kind="gradle-task"))
    bus.publish(NodeProgressChanged(run_id="android-exo10", node_id="compile", percent=29, current=29, total=100, unit="percent"))
    bus.publish(DiagnosticEmitted(run_id="android-exo10", node_id="compile", diagnostic_id="diag-compile", severity="error",
                                  code="COMPILE", message="Compilation failed", process_id="process-compile",
                                  failure_kind="process_exit", log_path="/tmp/compile.log", evidence=("/tmp/compile.log",)))
    bus.publish(NodeFinished(run_id="android-exo10", node_id="compile", status="failed", exit_code=1,
                             process_id="process-compile", diagnostic_ids=("diag-compile",)))
    bus.publish(NodeStarted(run_id="android-exo10", node_id="zip", label="Create ZIP", kind="artifact"))
    bus.publish(NodeProgressChanged(run_id="android-exo10", node_id="zip", percent=100, current=1, total=1, unit="archive"))
    bus.publish(NodeFinished(run_id="android-exo10", node_id="zip", status="succeeded"))
    bus.publish(RunFinished(run_id="android-exo10", status="failed", exit_code=1))


def test_android_payload_prefers_canonical_execution_over_legacy_state(tmp_path):
    _canonical_run(tmp_path)
    (tmp_path / "state.json").write_text(json.dumps({"run_id":"android-exo10","status":"ok","percent":100,"phase_name":"Legacy success"}))
    legacy = state_from_directory(tmp_path)
    assert legacy.status == "ok"

    sink = AndroidAppHud()
    action, payload = sink._payload(legacy, tmp_path)
    execution = json.loads(payload["execution_json"])
    assert action == "fail"
    assert payload["status"] == "failed"
    assert payload["execution_truth"] == "canonical"
    assert execution["run_id"] == "android-exo10"
    assert execution["diagnostics"][0]["node_id"] == "compile"
    assert payload["progress"] != 100


def test_android_java_contract_persists_execution_graph():
    root = Path("android/devtool-live-hud/app/src/main/java/io/github/mikeceead/devtoolhud")
    graph = (root / "ExecutionGraphState.java").read_text()
    update = (root / "HudUpdate.java").read_text()
    record = (root / "RunRecord.java").read_text()
    store = (root / "RunStore.java").read_text()
    assert 'class ExecutionGraphState' in graph
    assert 'intent.getStringExtra("execution_json")' in update
    assert 'executionJson' in record and '"execution_json"' in record
    assert 'record.executionJson = update.execution.raw.toString()' in store
    assert 'record.executionSequence = update.execution.sequence' in store
