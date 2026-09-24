from __future__ import annotations

import json
from pathlib import Path

import pytest

from devtool import cli
from devtool.core.events import EventBus, NodeDiscovered, NodeFinished, NodeStarted, ProcessExited, ProcessStarted, RunStarted
from devtool.core.execution_lifecycle import ExecutionLifecycleError, finalize_execution_run
from devtool.core.execution_subscribers import ExecutionProjectionSubscriber


def _workflow_repo(root: Path, *, first_exit: int = 0, nodes: int = 3) -> None:
    jobs: list[str] = []
    steps: list[str] = []
    previous = ""
    for index in range(nodes):
        name = chr(ord("a") + index)
        exit_code = first_exit if index == 0 else 0
        jobs.append(
            f'[jobs.{name}]\nrunner = "command"\n'
            f'command = ["python3", "-c", "import sys; print(\\\"{name}\\\"); sys.exit({exit_code})"]\n'
        )
        depends = f'["{previous}"]' if previous else "[]"
        steps.append(f'  {{ id = "{name}", ref = "job:{name}", depends_on = {depends} }}')
        previous = name
    text = (
        '[project]\nrunner = "command"\ndefault_target = "demo"\n\n'
        + "\n".join(jobs)
        + '\n[targets.demo]\nrunner = "command"\nroot = "."\n\n'
        + '[targets.demo.workflows]\nbuild = [\n'
        + ",\n".join(steps)
        + "\n]\n"
    )
    (root / ".devtool.toml").write_text(text, encoding="utf-8")


def _run_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, first_exit: int = 0, nodes: int = 3):
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    repo.mkdir()
    state.mkdir()
    _workflow_repo(repo, first_exit=first_exit, nodes=nodes)
    monkeypatch.setattr(cli, "DEFAULT_TASKER_DIR", str(state))
    monkeypatch.setenv("DEVTOOL_NO_AUTO_HUD", "1")
    monkeypatch.delenv("DEVTOOL_NO_HUD", raising=False)
    monkeypatch.delenv("DEVTOOL_RUN_ID", raising=False)
    rc = cli.main(["-r", str(repo), "--target", "demo", "build"])
    document = json.loads((state / "execution.json").read_text(encoding="utf-8"))
    run_id = document["run_id"]
    history = state / "history" / run_id / "execution.json"
    journal = [json.loads(line) for line in (state / "execution-events.jsonl").read_text(encoding="utf-8").splitlines()]
    return rc, document, history, journal


def test_generic_three_node_cli_workflow_seals_and_persists_terminal_success(tmp_path, monkeypatch):
    rc, document, history, journal = _run_cli(tmp_path, monkeypatch)
    execution = document["execution"]
    assert rc == 0
    assert execution["status"] == "succeeded"
    assert execution["graph_sealed"] is True
    assert all(node["status"] == "succeeded" for node in execution["nodes"])
    assert execution["nodes"][1]["depends_on"] == [execution["nodes"][0]["node_id"]]
    assert execution["nodes"][2]["depends_on"] == [execution["nodes"][1]["node_id"]]
    assert history.is_file()
    terminal = json.loads(history.read_text(encoding="utf-8"))
    assert terminal["status"] == "succeeded"
    assert terminal["execution"]["graph_sealed"] is True
    assert [item["event_type"] for item in journal[-2:]] == ["graph.sealed", "run.finished"]


def test_failed_dependency_workflow_is_failed_sealed_and_blocked_downstream(tmp_path, monkeypatch):
    rc, document, history, journal = _run_cli(tmp_path, monkeypatch, first_exit=7)
    execution = document["execution"]
    assert rc == 1
    assert execution["status"] == "failed"
    assert execution["graph_sealed"] is True
    assert [node["status"] for node in execution["nodes"]] == ["failed", "blocked", "blocked"]
    assert history.is_file()
    assert [item["event_type"] for item in journal[-2:]] == ["graph.sealed", "run.finished"]


def test_cancelled_workflow_returns_130_and_persists_cancelled_terminal_truth(tmp_path, monkeypatch):
    rc, document, history, journal = _run_cli(tmp_path, monkeypatch, first_exit=130, nodes=2)
    execution = document["execution"]
    assert rc == 130
    assert execution["status"] == "cancelled"
    assert execution["graph_sealed"] is True
    assert [node["status"] for node in execution["nodes"]] == ["cancelled", "cancelled"]
    assert history.is_file()
    assert [item["event_type"] for item in journal[-2:]] == ["graph.sealed", "run.finished"]


def test_finalizer_rejects_nonterminal_nodes_and_active_processes(tmp_path):
    bus = EventBus()
    sub = ExecutionProjectionSubscriber(store=bus.execution_store, run_id="unfinished", directory=tmp_path)
    bus.subscribe_all(sub)
    bus.publish(RunStarted(run_id="unfinished", command="build", project="demo"))
    bus.publish(NodeDiscovered(run_id="unfinished", node_id="a", label="A"))
    bus.publish(NodeStarted(run_id="unfinished", node_id="a", label="A"))
    bus.publish(ProcessStarted(run_id="unfinished", node_id="a", process_id="p", pid=123, argv=("tool",), cwd="/repo"))
    with pytest.raises(ExecutionLifecycleError, match="nonterminal node"):
        finalize_execution_run("unfinished", status="succeeded", exit_code=0, bus=bus)

    bus.publish(ProcessExited(run_id="unfinished", node_id="a", process_id="p", exit_code=0))
    bus.publish(NodeFinished(run_id="unfinished", node_id="a", status="succeeded"))
    snapshot = finalize_execution_run("unfinished", status="succeeded", exit_code=0, bus=bus)
    assert snapshot.status == "succeeded"
    assert snapshot.graph_sealed is True
