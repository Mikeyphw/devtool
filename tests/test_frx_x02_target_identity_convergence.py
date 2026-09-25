from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from devtool.config import load_project_config
from devtool.core.events import EventBus, RunFinished, RunStarted
from devtool.federation_bridge import (
    execute_federated_job, execute_federated_wrapper, plan_federated_job, plan_federated_wrapper,
)
from devtool.federation_execution import run_federated_workflow
from devtool.federation_observability import build_federation_observability, runtime_federation_projection
from devtool.federation_transport import FederationTransportError, build_federation_transport_metadata
from devtool.invocation_origin import federation_lineage_from_environment, invocation_metadata_for_args
from devtool.models import OperationResult
from devtool.repository_identity import build_repository_identity


def _load(root: Path):
    with mock.patch.dict(os.environ, {"DEVTOOL_GLOBAL_CONFIG": str(root / "missing-global.toml")}, clear=False):
        return load_project_config(str(root), None, allow_ambiguous_target=True, allow_ambiguous_project_kind=True)


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    parent = tmp_path / "parent"
    child = tmp_path / "child"
    parent.mkdir(); child.mkdir()
    (parent / ".devtool.toml").write_text(
        '''[project]\nrunner="command"\n[federation]\nschema=1\n[federation.repositories.child]\npath="../child"\n[federation.workflows.release]\ndepends_on=["child::root_module::package"]\n''',
        encoding="utf-8",
    )
    (child / ".devtool.toml").write_text(
        '''[project]\nrunner="command"\n[workspace]\ndefault_target="app"\n[jobs.one]\nrunner="command"\ncommand=["echo","one"]\n[targets.app]\nrunner="command"\nroot="."\n[targets.app.workflows]\nbuild=["job:one"]\n[targets.root_module]\nrunner="command"\nroot="."\n[targets.root_module.workflows]\npackage=["job:one"]\n[targets.root_module.jobs.lint]\nrunner="command"\ncommand=["echo","lint"]\n[wrapper.commands.package]\ndevtool="build"\ntarget="root_module"\n''',
        encoding="utf-8",
    )
    return parent, child


def _workflow_reenter(bus: EventBus):
    seen: list[dict[str, str]] = []
    def callback(argv: list[str]) -> int:
        run_id = os.environ["DEVTOOL_RUN_ID"]
        root = Path(argv[argv.index("--repo") + 1])
        lineage = federation_lineage_from_environment()
        seen.append({str(k): str(v) for k, v in lineage.items()})
        metadata = invocation_metadata_for_args(SimpleNamespace(
            _devtool_invocation_source="federation",
            _devtool_invocation_command=os.environ["DEVTOOL_FEDERATION_STEP"],
            _devtool_wrapper_schema="",
            command="invoke",
        ))
        metadata["repository_identity"] = build_repository_identity(root, root / ".devtool.toml").as_dict()
        bus.publish(RunStarted(run_id=run_id, command="invoke", project=str(root), source="federation", metadata=metadata))
        bus.publish(RunFinished(run_id=run_id, status="succeeded", exit_code=0, source="federation"))
        return 0
    return callback, seen


def test_workflow_child_persists_canonical_target_identity_across_exo_and_report(tmp_path: Path) -> None:
    parent_root, _ = _fixture(tmp_path)
    config = _load(parent_root)
    bus = EventBus()
    callback, seen = _workflow_reenter(bus)
    report = run_federated_workflow(
        config, "release", reenter=callback, publish=bus.publish,
        snapshot_lookup=bus.execution_store.snapshot, parent_run_id="x02-parent",
    )
    assert report.ok
    child = report.children[0]
    assert child.target == "root_module"
    assert child.workflow == "package"
    assert child.federation_ref == "child::root_module::package"
    assert child.requested_ref == "child::root_module::package"
    assert child.as_dict()["federation_ref"] == "child::root_module::package"
    assert seen == [{
        **{k: v for k, v in seen[0].items() if k not in {"child_run_id"}},
        "child_run_id": child.run_id,
    }]
    assert seen[0]["target"] == "root_module"
    assert seen[0]["child_workflow"] == "package"
    assert seen[0]["federation_ref"] == "child::root_module::package"
    assert seen[0]["requested_ref"] == "child::root_module::package"
    snap = bus.execution_store.snapshot(child.run_id)
    assert snap is not None
    assert snap.run_lineage["target"] == "root_module"
    assert snap.run_lineage["child_workflow"] == "package"
    assert snap.run_lineage["federation_ref"] == "child::root_module::package"
    parent = bus.execution_store.snapshot("x02-parent")
    assert parent is not None
    node = parent.node(child.parent_node_id)
    assert node is not None
    assert node.label == "child::root_module::package"
    assert node.metadata["target"] == "root_module"
    assert node.metadata["federation_ref"] == "child::root_module::package"


def test_legacy_reference_is_observed_as_resolved_canonical_ref(tmp_path: Path) -> None:
    parent_root, _ = _fixture(tmp_path)
    text = (parent_root / ".devtool.toml").read_text(encoding="utf-8")
    (parent_root / ".devtool.toml").write_text(text.replace("child::root_module::package", "child::build"), encoding="utf-8")
    config = _load(parent_root)
    observation = build_federation_observability(config, execution_snapshots=()).as_dict()
    workflow = observation["workflows"][0]
    assert workflow["steps"][0]["ref"] == "child::build"
    assert workflow["steps"][0]["canonical_ref"] == "child::app::build"
    assert workflow["federation_refs"] == ["child::app::build"]


def test_runtime_projection_surfaces_target_ref_and_requested_ref(tmp_path: Path) -> None:
    parent_root, _ = _fixture(tmp_path)
    config = _load(parent_root)
    bus = EventBus(); callback, _ = _workflow_reenter(bus)
    report = run_federated_workflow(config, "release", reenter=callback, publish=bus.publish, snapshot_lookup=bus.execution_store.snapshot, parent_run_id="proj-parent")
    child_snap = bus.execution_store.snapshot(report.children[0].run_id)
    projection = runtime_federation_projection(child_snap)
    assert projection["repository"] == "child"
    assert projection["target"] == "root_module"
    assert projection["child_workflow"] == "package"
    assert projection["federation_ref"] == "child::root_module::package"
    assert projection["requested_ref"] == "child::root_module::package"
    parent_projection = runtime_federation_projection(bus.execution_store.snapshot("proj-parent"))
    child = parent_projection["children"][0]
    assert child["target"] == "root_module"
    assert child["workflow"] == "package"
    assert child["federation_ref"] == "child::root_module::package"


def test_transport_metadata_carries_canonical_target_identity_and_fails_closed_on_mismatch(tmp_path: Path) -> None:
    _, child_root = _fixture(tmp_path)
    identity = build_repository_identity(child_root, child_root / ".devtool.toml").as_dict()
    lineage = {
        "kind": "federation", "relationship": "child", "parent_run_id": "p", "parent_node_id": "n", "child_run_id": "c",
        "federation_workflow": "release", "federation_step": "step-01", "repository": "child", "target": "root_module",
        "child_workflow": "package", "federation_ref": "child::root_module::package", "requested_ref": "child::root_module::package",
        "effective_repository": identity["fingerprint"],
    }
    meta = build_federation_transport_metadata(
        {"run_lineage": lineage, "repository_identity": identity},
        local_root=child_root, execution_environment="local",
    )
    assert meta["federation_child_target"] == "root_module"
    assert meta["federation_child_workflow"] == "package"
    assert meta["federation_ref"] == "child::root_module::package"
    assert meta["federation_requested_ref"] == "child::root_module::package"
    bad = dict(lineage); bad["federation_ref"] = "child::app::package"
    with pytest.raises(FederationTransportError, match="ref mismatch"):
        build_federation_transport_metadata(
            {"run_lineage": bad, "repository_identity": identity},
            local_root=child_root, execution_environment="local",
        )


def test_wrapper_and_tlj_direct_bridges_preserve_selected_target_contract_identity(tmp_path: Path) -> None:
    parent_root, child_root = _fixture(tmp_path)
    parent = _load(parent_root)
    bus = EventBus()

    def wrapper_reenter(argv: list[str]) -> int:
        run_id = os.environ["DEVTOOL_RUN_ID"]
        metadata = invocation_metadata_for_args(SimpleNamespace(
            _devtool_invocation_source="wrapper", _devtool_invocation_command="package", _devtool_wrapper_schema="1", command="build"
        ))
        metadata["repository_identity"] = build_repository_identity(child_root, child_root / ".devtool.toml").as_dict()
        bus.publish(RunStarted(run_id=run_id, command="build", project=str(child_root), source="wrapper", metadata=metadata))
        bus.publish(RunFinished(run_id=run_id, status="succeeded", exit_code=0, source="wrapper"))
        return 0

    wrapper = execute_federated_wrapper(parent, "child", ["package"], reenter=wrapper_reenter, publish=bus.publish, snapshot_lookup=bus.execution_store.snapshot)
    assert wrapper.target == "root_module"
    assert wrapper.child_contract == "wrapper:package"
    assert wrapper.contract_ref == "child::root_module::wrapper:package"
    wsnap = bus.execution_store.snapshot(wrapper.run_id)
    assert wsnap is not None
    assert wsnap.run_lineage["target"] == "root_module"
    assert wsnap.run_lineage["contract_ref"] == "child::root_module::wrapper:package"

    def fake_run_specs(config, specs, **kwargs):
        return ([OperationResult(name=specs[0].name, argv=list(specs[0].argv), returncode=0, duration_seconds=0.01, output="", issues=[], metadata=dict(specs[0].metadata))], "")

    job = execute_federated_job(parent, "child", "root_module:job:lint", publish=bus.publish, snapshot_lookup=bus.execution_store.snapshot, run_specs_fn=fake_run_specs)
    assert job.target == "root_module"
    assert job.child_contract == "job:lint"
    assert job.contract_ref == "child::root_module::job:lint"
    jsnap = bus.execution_store.snapshot(job.run_id)
    assert jsnap is not None
    assert jsnap.run_lineage["target"] == "root_module"
    assert jsnap.run_lineage["contract_ref"] == "child::root_module::job:lint"


def test_whole_run_remote_delegation_forwards_canonical_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    from devtool.ssh_tmux_runner import SSHRunnerConfig, build_remote_tmux_script
    from devtool.termux_chroot_runner import RunnerConfig, build_inner_command

    values = {
        "DEVTOOL_RUN_ID": "child-run",
        "DEVTOOL_FEDERATION_RELATIONSHIP": "child",
        "DEVTOOL_FEDERATION_PARENT_RUN_ID": "parent-run",
        "DEVTOOL_FEDERATION_PARENT_NODE_ID": "federation:release:step-01",
        "DEVTOOL_FEDERATION_WORKFLOW": "release",
        "DEVTOOL_FEDERATION_STEP": "step-01",
        "DEVTOOL_FEDERATION_REPOSITORY": "child",
        "DEVTOOL_FEDERATION_TARGET": "root_module",
        "DEVTOOL_FEDERATION_CHILD_WORKFLOW": "package",
        "DEVTOOL_FEDERATION_REF": "child::root_module::package",
        "DEVTOOL_FEDERATION_REQUESTED_REF": "child::root_module::package",
        "DEVTOOL_FEDERATION_ATTESTATION_SCHEMA_VERSION": "1",
        "DEVTOOL_FEDERATION_ATTESTATION_AVAILABLE": "1",
        "DEVTOOL_FEDERATION_ATTESTATION_CONFIG_SHA256": "a" * 64,
    }
    with mock.patch.dict(os.environ, values, clear=False):
        ssh = build_remote_tmux_script(
            SSHRunnerConfig(host="worker.example", user="mike", cwd="/srv/child"),
            ["status"],
        )
        chroot = build_inner_command(
            RunnerConfig(chroot_name="androidos", cwd="/home/mike/child", tool="devtool"),
            ["status"],
            identity=SimpleNamespace(user="mike", home="/home/mike", shell="/bin/bash", source="test"),
        )
    for rendered in (ssh, chroot):
        assert "DEVTOOL_FEDERATION_TARGET=root_module" in rendered
        assert "DEVTOOL_FEDERATION_CHILD_WORKFLOW=package" in rendered
        assert "DEVTOOL_FEDERATION_REF=child::root_module::package" in rendered
        assert "DEVTOOL_FEDERATION_REQUESTED_REF=child::root_module::package" in rendered


def test_doctor_machine_payload_exposes_resolved_canonical_refs(tmp_path: Path) -> None:
    from devtool.doctor import doctor_payload

    parent_root, _ = _fixture(tmp_path)
    payload = doctor_payload(_load(parent_root))
    workflow = payload["federation"]["workflows"][0]
    assert workflow["steps"][0]["canonical_ref"] == "child::root_module::package"
    assert workflow["federation_refs"] == ["child::root_module::package"]


def test_return_producer_identity_retains_target_workflow_attribution() -> None:
    from devtool.oci_resilience import producer_identity

    producer = producer_identity(
        profile="tablet",
        destination="mike@worker",
        run_id="child-run",
        session="session",
        source_sync_id="sync",
        metadata={
            "execution_provider": "ssh-native",
            "federation_child_repository": "child",
            "federation_child_target": "root_module",
            "federation_child_workflow": "package",
            "federation_ref": "child::root_module::package",
            "federation_requested_ref": "child::root_module::package",
            "federation_parent_run_id": "parent-run",
            "federation_child_run_id": "child-run",
        },
    )
    federation = producer["federation"]
    assert federation["child_repository"] == "child"
    assert federation["child_target"] == "root_module"
    assert federation["child_workflow"] == "package"
    assert federation["ref"] == "child::root_module::package"
    assert federation["requested_ref"] == "child::root_module::package"


def test_direct_bridge_plans_publish_canonical_contract_refs(tmp_path: Path) -> None:
    parent_root, _ = _fixture(tmp_path)
    parent = _load(parent_root)
    wrapper, _, _ = plan_federated_wrapper(parent, "child", ["package"])
    assert wrapper.target == "root_module"
    assert wrapper.child_contract == "wrapper:package"
    assert wrapper.contract_ref == "child::root_module::wrapper:package"
    assert wrapper.as_dict()["contract_ref"] == "child::root_module::wrapper:package"

    job, _, _ = plan_federated_job(parent, "child", "root_module:job:lint")
    assert job.target == "root_module"
    assert job.child_contract == "job:lint"
    assert job.contract_ref == "child::root_module::job:lint"
    assert job.as_dict()["contract_ref"] == "child::root_module::job:lint"
