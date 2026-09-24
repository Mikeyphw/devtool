from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from devtool import cli
from devtool.config import load_project_config
from devtool.core.events import EventBus, RunFinished, RunStarted
from devtool.federation_execution import FederationExecutionError, run_federated_workflow
from devtool.federation_gate2 import build_federation_gate2_report, federation_gate2_as_dict
from devtool.federation_plan import FederatedPlanGraph, FederatedPlanNode, FederationPlanError, build_federated_plan
from devtool.federation_bridge import FederationBridgeError, plan_federated_wrapper
from devtool.invocation_origin import invocation_metadata_for_args
from devtool.repository_identity import RepositoryIdentity, build_repository_identity


def _load(root: Path):
    with mock.patch.dict(os.environ, {"DEVTOOL_GLOBAL_CONFIG": str(root / "missing-global.toml")}, clear=False):
        return load_project_config(str(root), None, allow_ambiguous_target=True, allow_ambiguous_project_kind=True)


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    parent = tmp_path / "parent"; backend = tmp_path / "backend"; android = tmp_path / "android"
    parent.mkdir(); backend.mkdir(); android.mkdir()
    (backend / ".devtool.toml").write_text(
        '''[project]\nrunner="command"\n[workspace]\ndefault_target="app"\n[targets.app]\nrunner="command"\n[targets.app.jobs.prepare]\nrunner="command"\ncommand=["echo","prepare"]\n[targets.app.workflows]\nprepare=["job:prepare"]\n''', encoding="utf-8")
    (android / ".devtool.toml").write_text(
        '''[project]\nrunner="command"\n[workspace]\ndefault_target="android"\n[targets.android]\nrunner="command"\n[targets.android.jobs.release]\nrunner="command"\ncommand=["echo","release"]\n[targets.android.workflows]\nrelease=["job:release"]\n[wrapper.commands.build]\ndevtool="build"\ntarget="android"\n''', encoding="utf-8")
    (parent / ".devtool.toml").write_text(
        '''[project]\nrunner="command"\n[federation]\nschema=1\n[federation.repositories.backend]\npath="../backend"\n[federation.repositories.android]\npath="../android"\n[federation.workflows.release]\nsteps=[\n {id="prepare",ref="backend::prepare"},\n {id="android",ref="android::release",depends_on=["prepare"]}\n]\n''', encoding="utf-8")
    return parent, backend, android


def _reenter(bus: EventBus, *, wrong_parent: bool = False, wrong_identity: bool = False):
    def callback(argv: list[str]) -> int:
        run_id = os.environ["DEVTOOL_RUN_ID"]
        root = Path(argv[argv.index("--repo") + 1])
        metadata = invocation_metadata_for_args(SimpleNamespace(
            _devtool_invocation_source="federation",
            _devtool_invocation_command="gate2",
            _devtool_wrapper_schema="",
            command="invoke",
        ))
        if wrong_parent:
            metadata["run_lineage"] = dict(metadata.get("run_lineage") or {}, parent_run_id="poisoned-parent")
        identity = build_repository_identity(root, root / ".devtool.toml").as_dict()
        if wrong_identity:
            identity = build_repository_identity(root.parent, None).as_dict()
        metadata["repository_identity"] = identity
        bus.publish(RunStarted(run_id=run_id, command="invoke", project=str(root), source="federation", metadata=metadata))
        bus.publish(RunFinished(run_id=run_id, status="succeeded", exit_code=0, source="federation"))
        return 0
    return callback


def test_gate2_standalone_is_side_effect_free_and_machine_readable(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / ".devtool.toml").write_text('[project]\nrunner="command"\n', encoding="utf-8")
    report = build_federation_gate2_report(_load(tmp_path))
    assert report.ok is True
    assert report.mode == "standalone"
    payload = federation_gate2_as_dict(report)
    assert payload["gate"] == "FRX-08"
    assert payload["summary"]["errors"] == 0
    with mock.patch.dict(os.environ, {"DEVTOOL_GLOBAL_CONFIG": str(tmp_path / "missing-global.toml")}, clear=False):
        assert cli.main(["--repo", str(tmp_path), "federation", "gate2", "--json"]) == 0
    cli_payload = json.loads(capsys.readouterr().out)
    cli_payload = cli_payload.get("data", cli_payload)
    assert cli_payload["gate"] == "FRX-08"
    assert cli_payload["ok"] is True


def test_gate2_qualifies_configured_cross_repo_dag(tmp_path: Path) -> None:
    parent, _, _ = _fixture(tmp_path)
    report = build_federation_gate2_report(_load(parent))
    assert report.ok is True
    check = next(item for item in report.checks if item.section == "planning" and item.name == "workflow:release")
    assert check.status == "ok"
    assert check.data["node_count"] >= 3


def test_execution_fails_closed_if_repository_identity_changes_after_planning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    parent_root, _, _ = _fixture(tmp_path)
    parent = _load(parent_root)
    import devtool.federation_execution as execution
    real = execution.resolve_federated_repository

    def poisoned(config, name):
        resolution = real(config, name)
        if name != "backend":
            return resolution
        actual = resolution.repository_identity
        assert actual is not None
        bad = RepositoryIdentity.from_dict({**actual.as_dict(), "fingerprint": "f" * 64})
        return replace(resolution, repository_identity=bad)

    monkeypatch.setattr(execution, "resolve_federated_repository", poisoned)
    bus = EventBus()
    with pytest.raises(FederationExecutionError, match="identity changed between planning and execution"):
        run_federated_workflow(parent, "release", reenter=_reenter(bus), publish=bus.publish, snapshot_lookup=bus.execution_store.snapshot)


@pytest.mark.parametrize("mode", ["wrong_parent", "wrong_identity"])
def test_execution_rejects_corrupt_child_snapshot_authority(tmp_path: Path, mode: str) -> None:
    parent_root, _, _ = _fixture(tmp_path)
    parent = _load(parent_root)
    bus = EventBus()
    callback = _reenter(bus, wrong_parent=mode == "wrong_parent", wrong_identity=mode == "wrong_identity")
    with pytest.raises(FederationExecutionError, match="lineage|identity mismatch"):
        run_federated_workflow(parent, "release", reenter=callback, publish=bus.publish, snapshot_lookup=bus.execution_store.snapshot)


def test_mutated_graph_cycle_fails_closed() -> None:
    graph = FederatedPlanGraph(
        workflow="bad",
        repositories=("a", "b"),
        nodes=(
            FederatedPlanNode("a::one", "a", "t", "w", "one", depends_on=("b::two",), index=1),
            FederatedPlanNode("b::two", "b", "t", "w", "two", depends_on=("a::one",), index=2),
        ),
    )
    with pytest.raises(FederationPlanError, match="cycle"):
        graph.topological_order()


def test_missing_child_workflow_fails_closed(tmp_path: Path) -> None:
    parent, _, android = _fixture(tmp_path)
    text = (parent / ".devtool.toml").read_text(encoding="utf-8").replace('ref="android::release"', 'ref="android::missing"')
    (parent / ".devtool.toml").write_text(text, encoding="utf-8")
    config = _load(parent)
    with pytest.raises(FederationPlanError, match="has no workflow"):
        build_federated_plan(config, "release")


def test_wrapper_cannot_inject_protected_target_routing(tmp_path: Path) -> None:
    parent, _, _ = _fixture(tmp_path)
    config = _load(parent)
    with pytest.raises(FederationBridgeError):
        plan_federated_wrapper(config, "android", ["build", "--target", "backend"])
