from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from devtool.config import load_project_config
from devtool.core.events import EventBus, RunFinished, RunStarted
from devtool.federation_execution import run_federated_group, run_federated_workflow
from devtool.invocation_origin import invocation_metadata_for_args
from devtool.repository_identity import build_repository_identity
from devtool.federation_plan import build_federated_plan, materialize_federation_group


def _fixture(tmp_path: Path, *, fail_lint: bool = False):
    parent = tmp_path / "parent"; child = tmp_path / "child"
    parent.mkdir(); child.mkdir()
    lint_command = '["python","-c","import sys; sys.exit(7)"]' if fail_lint else '["echo","lint"]'
    (child / ".devtool.toml").write_text(
        f'''[project]\nrunner="command"\n[workspace]\ndefault_target="app"\n[targets.app]\nrunner="command"\n[targets.app.jobs.lint]\nrunner="command"\ncommand={lint_command}\n[targets.app.jobs.package]\nrunner="command"\ncommand=["echo","package"]\n[targets.app.workflows]\nvalidate=["job:lint"]\n[wrapper.commands.build]\ndevtool="build"\ntarget="app"\n''',
        encoding="utf-8",
    )
    (parent / ".devtool.toml").write_text(
        '''[project]\nrunner="command"\n[workspace]\ndefault_target="root"\n[targets.root]\nrunner="command"\n[federation]\nschema=1\n[federation.repositories.child]\npath="../child"\n[federation.workflows.release]\nsteps=[\n {id="lint",ref="child::app::job:lint"},\n {id="package",ref="child::app::job:package",depends_on=["lint"]},\n]\n[federation.groups.public-contracts]\nmembers=["child::app::job:lint","child::app::wrapper:build"]\n''',
        encoding="utf-8",
    )
    config = load_project_config(
        parent,
        parent / ".devtool.toml",
        target_name="",
        allow_ambiguous_target=True,
        allow_ambiguous_project_kind=True,
    )
    return config


def test_y07_parser_accepts_target_qualified_job_and_wrapper_contracts(tmp_path: Path) -> None:
    config = _fixture(tmp_path)
    steps = config.federation.workflows["release"].steps
    assert steps[0].contract_type == "job"
    assert steps[0].contract_name == "lint"
    assert steps[0].child_contract == "job:lint"
    group = materialize_federation_group(config, "public-contracts")
    assert [step.contract_type for step in group.steps] == ["job", "wrapper"]
    assert group.steps[1].child_contract == "wrapper:build"


def test_y07_unqualified_direct_contract_is_not_silently_interpreted_as_public_job(tmp_path: Path) -> None:
    config = _fixture(tmp_path)
    parent = Path(config.repo_root)
    text = (parent / ".devtool.toml").read_text().replace("child::app::job:lint", "child::job:lint", 1)
    (parent / ".devtool.toml").write_text(text, encoding="utf-8")
    with pytest.raises(SystemExit):
        load_project_config(parent, parent / ".devtool.toml", allow_ambiguous_target=True, allow_ambiguous_project_kind=True)


def test_y07_plan_uses_bridge_contract_identity_and_conservative_schedule(tmp_path: Path) -> None:
    config = _fixture(tmp_path)
    plan = build_federated_plan(config, "release")
    lint = next(node for node in plan.nodes if node.metadata.get("contract_name") == "lint")
    package = next(node for node in plan.nodes if node.metadata.get("contract_name") == "package")
    assert lint.key == "child::app::job:lint"
    assert lint.federation_ref == "child::app::job:lint"
    assert lint.metadata["bridge_plan"]["contract"] == "job"
    assert lint.metadata["child_contract"] == "job:lint"
    assert "federation:direct-contract-env" in lint.metadata["child_schedule"]["global_reservations"]
    assert lint.key in package.depends_on


def test_y07_direct_job_executes_inside_federation_dag_with_contract_lineage(tmp_path: Path) -> None:
    config = _fixture(tmp_path)
    bus = EventBus()
    report = run_federated_workflow(
        config,
        "release",
        reenter=lambda _argv: (_ for _ in ()).throw(AssertionError("direct jobs must not require workflow reentry")),
        publish=bus.publish,
        snapshot_lookup=bus.execution_store.snapshot,
    )
    assert report.ok
    assert [child.child_contract for child in report.children] == ["job:lint", "job:package"]
    for child in report.children:
        assert child.contract_ref == f"child::app::{child.child_contract}"
        snap = bus.execution_store.snapshot(child.run_id)
        assert snap is not None
        assert snap.run_lineage["child_contract"] == child.child_contract
        assert snap.run_lineage["contract_ref"] == child.contract_ref
        assert snap.run_lineage["parent_run_id"] == report.parent_run_id


def test_y07_direct_job_failure_blocks_dependent_contract(tmp_path: Path) -> None:
    config = _fixture(tmp_path, fail_lint=True)
    bus = EventBus()
    report = run_federated_workflow(
        config,
        "release",
        reenter=lambda _argv: 0,
        publish=bus.publish,
        snapshot_lookup=bus.execution_store.snapshot,
    )
    assert not report.ok
    assert report.children[0].status == "failed" and report.children[0].exit_code == 7
    assert report.children[1].status == "blocked"
    assert report.children[1].child_contract == "job:package"


def test_y07_parallel_path_preserves_direct_contract_identity(tmp_path: Path) -> None:
    config = _fixture(tmp_path)
    bus = EventBus()
    report = run_federated_workflow(
        config,
        "release",
        reenter=lambda _argv: 0,
        publish=bus.publish,
        snapshot_lookup=bus.execution_store.snapshot,
        parallel=True,
        max_parallel=2,
    )
    assert report.ok
    assert all(child.contract_ref.startswith("child::app::job:") for child in report.children)
    parent = bus.execution_store.snapshot(report.parent_run_id)
    assert parent is not None
    discovered = [node for node in parent.nodes if node.kind == "federation-child"]
    assert {node.metadata.get("contract_type") for node in discovered} == {"job"}


def test_y07_wrapper_contract_executes_through_existing_wrapper_bridge(tmp_path: Path) -> None:
    config = _fixture(tmp_path)
    bus = EventBus()
    seen: list[list[str]] = []

    def reenter(argv: list[str]) -> int:
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

    report = run_federated_group(
        config,
        "public-contracts",
        reenter=reenter,
        publish=bus.publish,
        snapshot_lookup=bus.execution_store.snapshot,
    )
    assert report.ok
    wrapper = next(child for child in report.children if child.child_contract == "wrapper:build")
    assert wrapper.contract_ref == "child::app::wrapper:build"
    snapshot = bus.execution_store.snapshot(wrapper.run_id)
    assert snapshot is not None
    assert snapshot.run_lineage["child_contract"] == "wrapper:build"
    assert snapshot.run_lineage["contract_ref"] == "child::app::wrapper:build"
    assert seen and "--internal-wrapper-schema" in seen[0]


def test_y07_explicit_workflow_contract_ref_is_typed_without_breaking_legacy_workflows(tmp_path: Path) -> None:
    config = _fixture(tmp_path)
    parent = Path(config.repo_root)
    text = (parent / ".devtool.toml").read_text().replace(
        '{id="lint",ref="child::app::job:lint"}',
        '{id="lint",ref="child::app::workflow:validate"}',
        1,
    ).replace(
        '{id="package",ref="child::app::job:package",depends_on=["lint"]}',
        '{id="package",ref="child::app::job:package",depends_on=["lint"]}',
        1,
    )
    (parent / ".devtool.toml").write_text(text, encoding="utf-8")
    typed = load_project_config(parent, parent / ".devtool.toml", allow_ambiguous_target=True, allow_ambiguous_project_kind=True)
    step = typed.federation.workflows["release"].steps[0]
    assert step.contract_type == "workflow"
    assert step.contract_name == "validate"
    assert step.ref == "child::app::workflow:validate"
    plan = build_federated_plan(typed, "release")
    node = next(node for node in plan.nodes if node.metadata.get("federation_step") == "lint")
    assert node.federation_ref == "child::app::validate"
    assert node.metadata["child_contract"] == "workflow:validate"
    assert node.metadata["contract_ref"] == "child::app::workflow:validate"


def test_y07_direct_wrapper_dag_node_owns_child_exo_truth(tmp_path: Path) -> None:
    config = _fixture(tmp_path)
    parent = Path(config.repo_root)
    text = (parent / ".devtool.toml").read_text(encoding="utf-8")
    text = text.replace(
        'steps=[\n {id="lint",ref="child::app::job:lint"},\n {id="package",ref="child::app::job:package",depends_on=["lint"]},\n]',
        'steps=[\n {id="build",ref="child::app::wrapper:build"},\n]',
    )
    (parent / ".devtool.toml").write_text(text, encoding="utf-8")
    config = load_project_config(parent, parent / ".devtool.toml", target_name="", allow_ambiguous_target=True, allow_ambiguous_project_kind=True)
    bus = EventBus()
    calls: list[list[str]] = []
    report = run_federated_workflow(
        config,
        "release",
        reenter=lambda argv: (calls.append(list(argv)) or 0),
        publish=bus.publish,
        snapshot_lookup=bus.execution_store.snapshot,
    )
    assert report.ok and calls
    child = report.children[0]
    assert child.child_contract == "wrapper:build"
    assert child.contract_ref == "child::app::wrapper:build"
    snap = bus.execution_store.snapshot(child.run_id)
    assert snap is not None and snap.status == "succeeded"
    assert snap.run_lineage["child_contract"] == "wrapper:build"
    assert snap.run_lineage["contract_ref"] == child.contract_ref
