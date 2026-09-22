import json
from devtool.core.events.bus import EventBus
from devtool.core.events.events import RunStarted,RunFinished,NodeDiscovered,NodeStarted,NodeProgressChanged,NodeFinished
from devtool.core.execution_subscribers import ExecutionProjectionSubscriber,legacy_execution_projection

def test_projection_persists_journal_history_and_ipc(tmp_path):
    bus=EventBus(); published=[]
    sub=ExecutionProjectionSubscriber(store=bus.execution_store,run_id="run-exo07",directory=tmp_path,ipc_publish=published.append)
    bus.subscribe_all(sub)
    bus.publish(RunStarted(run_id="run-exo07",command="build",project="demo"))
    bus.publish(NodeDiscovered(run_id="run-exo07",node_id="compile",label="Compile",kind="build"))
    bus.publish(NodeStarted(run_id="run-exo07",node_id="compile",label="Compile",kind="build"))
    bus.publish(NodeProgressChanged(run_id="run-exo07",node_id="compile",percent=42.0,current=42,total=100,unit="items"))
    bus.publish(NodeFinished(run_id="run-exo07",node_id="compile",status="succeeded"))
    bus.publish(RunFinished(run_id="run-exo07",status="succeeded",exit_code=0))
    doc=json.loads((tmp_path/"execution.json").read_text()); assert doc["execution"]["counts"]["succeeded"]==1
    assert len((tmp_path/"execution-events.jsonl").read_text().splitlines())==6
    assert json.loads((tmp_path/"history/run-exo07/execution.json").read_text())["status"]=="succeeded"
    assert published[-1]["operation_counts"]["succeeded"]==1

def test_legacy_projection_never_fabricates_unobserved_success():
    bus=EventBus(); bus.publish(RunStarted(run_id="truth",command="build"))
    bus.publish(NodeDiscovered(run_id="truth",node_id="a",label="A")); bus.publish(NodeDiscovered(run_id="truth",node_id="b",label="B"))
    bus.publish(NodeStarted(run_id="truth",node_id="a",label="A")); bus.publish(NodeFinished(run_id="truth",node_id="a",status="succeeded"))
    bus.publish(RunFinished(run_id="truth",status="succeeded",exit_code=0))
    p=legacy_execution_projection(bus.execution_store.snapshot("truth"))
    assert p["operation_counts"]["succeeded"]==1 and p["operation_counts"]["planned"]==1

def test_subscriber_filters_other_runs(tmp_path):
    bus=EventBus(); sub=ExecutionProjectionSubscriber(store=bus.execution_store,run_id="wanted",directory=tmp_path); bus.subscribe_all(sub)
    bus.publish(RunStarted(run_id="other",command="noop")); assert not (tmp_path/"execution.json").exists()
