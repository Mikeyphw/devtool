from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from devtool.config import load_project_config
from devtool.core.events import EventBus, RunFinished, RunStarted
from devtool.federation_bridge import (
    FederationBridgeError,
    execute_federated_job,
    execute_federated_wrapper,
    plan_federated_job,
    plan_federated_wrapper,
)
from devtool.invocation_origin import invocation_metadata_for_args
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
        '''[project]\nrunner="command"\n[federation]\nschema=1\n[federation.repositories.child]\npath="../child"\n''',
        encoding="utf-8",
    )
    (child / ".devtool.toml").write_text(
        '''[project]\nrunner="command"\n[workspace]\ndefault_target="android"\n[targets.android]\nrunner="command"\nroot="."\n[targets.android.jobs.lint]\nrunner="command"\ncommand=["echo","lint"]\n[wrapper.commands.build]\ndevtool="build"\ntarget="android"\n[wrapper.commands.secure]\ndevtool="build"\ntarget="android"\n[wrapper.commands.secure.options.token]\ntype="string"\nrequired=true\ncommand_argv=["--tag={value}"]\n''',
        encoding="utf-8",
    )
    return parent, child


def _wrapper_reenter(bus: EventBus, seen: list[list[str]]):
    def callback(argv: list[str]) -> int:
        seen.append(list(argv))
        run_id = os.environ["DEVTOOL_RUN_ID"]
        root = Path(argv[argv.index("--repo") + 1])
        source = argv[argv.index("--internal-invocation-source") + 1]
        command = argv[argv.index("--internal-invocation-command") + 1]
        schema = argv[argv.index("--internal-wrapper-schema") + 1]
        metadata = invocation_metadata_for_args(SimpleNamespace(
            _devtool_invocation_source=source,
            _devtool_invocation_command=command,
            _devtool_wrapper_schema=schema,
            command="build",
        ))
        metadata["repository_identity"] = build_repository_identity(root, root / ".devtool.toml").as_dict()
        bus.publish(RunStarted(run_id=run_id, command="build", project=str(root), source=source, metadata=metadata))
        bus.publish(RunFinished(run_id=run_id, status="succeeded", exit_code=0, source=source))
        return 0
    return callback


def test_wrapper_bridge_preserves_wrapper_provenance_and_federation_lineage(tmp_path: Path) -> None:
    parent_root, child_root = _fixture(tmp_path)
    parent = _load(parent_root)
    bus = EventBus(); seen: list[list[str]] = []
    child = execute_federated_wrapper(
        parent,
        "child",
        ["build"],
        reenter=_wrapper_reenter(bus, seen),
        publish=bus.publish,
        snapshot_lookup=bus.execution_store.snapshot,
    )
    assert child.status == "succeeded"
    assert len(seen) == 1
    argv = seen[0]
    assert argv[argv.index("--repo") + 1] == str(child_root.resolve())
    assert argv[argv.index("--target") + 1] == "android"
    assert argv[argv.index("--internal-invocation-source") + 1] == "wrapper"
    snap = bus.execution_store.snapshot(child.run_id)
    assert snap is not None
    assert snap.invocation_source == "wrapper"
    assert snap.invocation_command == "build"
    assert snap.run_lineage["relationship"] == "child"
    assert snap.run_lineage["repository"] == "child"
    assert snap.run_lineage["parent_run_id"]


def test_wrapper_bridge_plan_redacts_sensitive_values(tmp_path: Path) -> None:
    parent_root, _ = _fixture(tmp_path)
    parent = _load(parent_root)
    plan, _, _ = plan_federated_wrapper(parent, "child", ["secure", "--token", "super-secret-value"])
    text = json.dumps(plan.as_dict(), sort_keys=True)
    assert "super-secret-value" not in text
    assert "<redacted>" in text


def test_target_local_job_bridge_uses_child_target_contract_and_exo_lineage(tmp_path: Path) -> None:
    parent_root, _ = _fixture(tmp_path)
    parent = _load(parent_root)
    bus = EventBus(); seen_specs = []

    def fake_run_specs(config, specs, **kwargs):
        seen_specs.extend(specs)
        return ([OperationResult(
            name=specs[0].name,
            argv=list(specs[0].argv),
            returncode=0,
            duration_seconds=0.01,
            output="",
            issues=[],
            metadata=dict(specs[0].metadata),
        )], "")

    child = execute_federated_job(
        parent,
        "child",
        "lint",
        publish=bus.publish,
        snapshot_lookup=bus.execution_store.snapshot,
        run_specs_fn=fake_run_specs,
    )
    assert child.status == "succeeded"
    assert len(seen_specs) == 1
    spec = seen_specs[0]
    assert spec.metadata["direct_job_scope"] == "target"
    assert spec.metadata["direct_job_target"] == "android"
    assert spec.argv == ["echo", "lint"]
    snap = bus.execution_store.snapshot(child.run_id)
    assert snap is not None
    assert snap.invocation_source == "federation-job"
    assert snap.run_lineage["relationship"] == "child"
    assert snap.repository_identity["root"].endswith("/child")


def test_target_qualified_job_never_falls_back_to_repository_scope(tmp_path: Path) -> None:
    parent_root, child_root = _fixture(tmp_path)
    with (child_root / ".devtool.toml").open("a", encoding="utf-8") as handle:
        handle.write('\n[jobs.missing]\nrunner="command"\ncommand=["echo","repo"]\n')
    parent = _load(parent_root)
    with pytest.raises(FederationBridgeError, match="has no target-local job"):
        plan_federated_job(parent, "child", "android:job:missing")


def test_job_bridge_failure_propagates_child_exit_code(tmp_path: Path) -> None:
    parent_root, _ = _fixture(tmp_path)
    parent = _load(parent_root)
    bus = EventBus()

    def failed(_config, specs, **_kwargs):
        return ([OperationResult(
            name=specs[0].name,
            argv=list(specs[0].argv),
            returncode=9,
            duration_seconds=0.01,
            output="bad",
            issues=[],
        )], "")

    child = execute_federated_job(
        parent, "child", "lint",
        publish=bus.publish,
        snapshot_lookup=bus.execution_store.snapshot,
        run_specs_fn=failed,
    )
    assert child.exit_code == 9
    assert child.status == "failed"
    parent_snap = bus.execution_store.snapshot(child.run_lineage["parent_run_id"])
    assert parent_snap is not None and parent_snap.status == "failed"


def test_direct_job_plan_is_non_executing(tmp_path: Path) -> None:
    parent_root, _ = _fixture(tmp_path)
    parent = _load(parent_root)
    plan, _, specs = plan_federated_job(parent, "child", "lint")
    assert plan.contract == "job"
    assert plan.target == "android"
    assert plan.spec_count == 1
    assert specs[0].argv == ["echo", "lint"]


def test_cli_exec_plan_accepts_repository_then_plan_separator(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from devtool import cli
    parent_root, _ = _fixture(tmp_path)
    with mock.patch.dict(os.environ, {"DEVTOOL_GLOBAL_CONFIG": str(parent_root / "missing-global.toml")}, clear=False):
        assert cli.main([
            "--repo", str(parent_root),
            "federation", "exec", "child", "--plan", "--", "job", "lint",
        ]) == 0
    out = capsys.readouterr().out
    assert "repository" in out
    assert "child" in out
    assert "planned" in out
