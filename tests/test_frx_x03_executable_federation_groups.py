from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

import pytest

from devtool import cli
from devtool.completion import ZSH_COMPLETION
from devtool.completion_state import build_completion_state, completion_values
from devtool.config import load_project_config
from devtool.core.events import EventBus, RunFinished, RunStarted
from devtool.federation_config import FederationConfigError, federation_as_dict, load_federation_config
from devtool.federation_execution import run_federated_group
from devtool.federation_plan import (
    FederationPlanError,
    build_federated_group_plan,
    materialize_federation_group,
)


def _load(root: Path):
    with mock.patch.dict(
        os.environ,
        {"DEVTOOL_GLOBAL_CONFIG": str(root / "missing-global.toml")},
        clear=False,
    ):
        return load_project_config(
            str(root),
            None,
            allow_ambiguous_target=True,
            allow_ambiguous_project_kind=True,
        )


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    parent = tmp_path / "parent"
    child = tmp_path / "child"
    parent.mkdir()
    child.mkdir()
    (child / ".devtool.toml").write_text(
        '''[project]
runner = "command"

[workspace]
default_target = "app"

[jobs.app]
runner = "command"
command = ["echo", "app"]

[jobs.root]
runner = "command"
command = ["echo", "root"]

[targets.app]
runner = "command"
root = "."

[targets.app.workflows]
validate = ["job:app"]
build = ["job:app"]

[targets.root_module]
runner = "command"
root = "."

[targets.root_module.workflows]
package = ["job:root"]
test = ["job:root"]
''',
        encoding="utf-8",
    )
    (parent / ".devtool.toml").write_text(
        f'''[project]
runner = "command"

[federation]
schema = 1

[federation.repositories.child]
path = "{child.as_posix()}"

[federation.workflows.validate-ready]
depends_on = ["child::validate"]

[federation.workflows.package-ready]
depends_on = ["child::root_module::package"]

[federation.groups]
inner = ["child::root_module::test"]
ready = ["validate-ready", "@inner", "child::root_module::package"]

[federation.groups.metadata]
repositories = ["child"]
''',
        encoding="utf-8",
    )
    return parent, child


def test_group_parser_preserves_legacy_repository_groups_and_normalizes_executable_members(tmp_path: Path) -> None:
    parent, _ = _fixture(tmp_path)
    config = _load(parent).federation
    assert config.groups["metadata"].repositories == ("child",)
    assert config.groups["metadata"].members == ()
    assert config.groups["metadata"].executable is False
    assert config.groups["inner"].members == ("child::root_module::test",)
    assert config.groups["ready"].members == (
        "workflow:validate-ready",
        "group:inner",
        "child::root_module::package",
    )
    assert config.groups["ready"].executable is True
    payload = federation_as_dict(config)
    assert payload["groups"]["metadata"]["executable"] is False
    assert payload["groups"]["ready"]["members"] == [
        "workflow:validate-ready",
        "group:inner",
        "child::root_module::package",
    ]


def test_group_table_members_form_is_supported(tmp_path: Path) -> None:
    child = tmp_path / "child"; child.mkdir()
    config = load_federation_config(
        {
            "schema": 1,
            "repositories": {"child": {"path": "child"}},
            "workflows": {"build-ready": {"depends_on": ["child::build"]}},
            "groups": {"ready": {"repositories": ["child"], "members": ["build-ready"]}},
        },
        owner_root=tmp_path,
    )
    assert config.groups["ready"].repositories == ("child",)
    assert config.groups["ready"].members == ("workflow:build-ready",)


def test_group_unknown_ambiguous_duplicate_and_cycle_fail_closed(tmp_path: Path) -> None:
    child = tmp_path / "child"; child.mkdir()
    base = {
        "schema": 1,
        "repositories": {"child": {"path": "child"}},
        "workflows": {"release": {"depends_on": ["child::build"]}},
    }
    with pytest.raises(FederationConfigError, match="unknown federation workflow/group"):
        load_federation_config({**base, "groups": {"x": ["missing"]}}, owner_root=tmp_path)
    with pytest.raises(FederationConfigError, match="cycle detected"):
        load_federation_config({**base, "groups": {"a": ["@b"], "b": ["@a"]}}, owner_root=tmp_path)
    with pytest.raises(FederationConfigError, match="duplicate executable member"):
        load_federation_config(
            {**base, "groups": {"x": ["release", "workflow:release"]}}, owner_root=tmp_path
        )
    with pytest.raises(FederationConfigError, match="ambiguous"):
        load_federation_config(
            {
                **base,
                "groups": {
                    "release": ["child::build"],
                    "bundle": ["release"],
                },
            },
            owner_root=tmp_path,
        )


def test_explicit_member_kind_disambiguates_same_group_and_workflow_name(tmp_path: Path) -> None:
    child = tmp_path / "child"; child.mkdir()
    config = load_federation_config(
        {
            "schema": 1,
            "repositories": {"child": {"path": "child"}},
            "workflows": {"release": {"depends_on": ["child::build"]}},
            "groups": {
                "release": ["child::test"],
                "bundle": ["workflow:release", "group:release"],
            },
        },
        owner_root=tmp_path,
    )
    assert config.groups["bundle"].members == ("workflow:release", "group:release")


def test_group_materializes_named_workflows_nested_groups_and_direct_refs_through_normal_planner(tmp_path: Path) -> None:
    parent, _ = _fixture(tmp_path)
    config = _load(parent)
    workflow = materialize_federation_group(config, "ready")
    assert workflow.name == "group:ready"
    assert [step.ref for step in workflow.steps] == [
        "child::validate",
        "child::root_module::test",
        "child::root_module::package",
    ]
    graph = build_federated_group_plan(config, "ready")
    executable = [node for node in graph.topological_order() if not node.synthetic]
    assert graph.workflow == "group:ready"
    assert [node.federation_ref for node in executable] == [
        "child::app::validate",
        "child::root_module::test",
        "child::root_module::package",
    ]
    assert graph.nodes[-1].key == "@federation::group:ready"


def test_metadata_only_group_refuses_execution_without_guessing_work(tmp_path: Path) -> None:
    parent, _ = _fixture(tmp_path)
    with pytest.raises(FederationPlanError, match="no executable members"):
        build_federated_group_plan(_load(parent), "metadata")


def test_overlapping_group_members_fail_on_duplicate_child_graph_identity(tmp_path: Path) -> None:
    parent, _ = _fixture(tmp_path)
    text = (parent / ".devtool.toml").read_text(encoding="utf-8")
    text += '\n[federation.groups.duplicate]\nmembers = ["validate-ready", "child::app::validate"]\n'
    (parent / ".devtool.toml").write_text(text, encoding="utf-8")
    with pytest.raises(FederationPlanError, match="duplicate child graph node"):
        build_federated_group_plan(_load(parent), "duplicate")


def test_group_execution_uses_existing_frx_coordinator_and_persists_group_lineage(tmp_path: Path) -> None:
    parent, _ = _fixture(tmp_path)
    config = _load(parent)
    bus = EventBus()
    seen: list[tuple[str, ...]] = []

    def reenter(argv: list[str]) -> int:
        from devtool.invocation_origin import federation_lineage_from_environment
        from devtool.repository_identity import build_repository_identity

        seen.append(tuple(argv))
        run_id = os.environ["DEVTOOL_RUN_ID"]
        root = Path(argv[argv.index("--repo") + 1])
        lineage = federation_lineage_from_environment()
        bus.publish(RunStarted(
            run_id=run_id,
            command="invoke",
            project=str(root),
            source="federation",
            metadata={
                "run_lineage": lineage,
                "repository_identity": build_repository_identity(root, root / ".devtool.toml").as_dict(),
            },
        ))
        bus.publish(RunFinished(run_id=run_id, status="succeeded", exit_code=0, source="federation"))
        return 0

    report = run_federated_group(
        config,
        "ready",
        reenter=reenter,
        publish=bus.publish,
        snapshot_lookup=bus.execution_store.snapshot,
        parent_run_id="group-parent",
    )
    assert report.ok
    assert report.workflow == "group:ready"
    assert [child.federation_ref for child in report.children] == [
        "child::app::validate",
        "child::root_module::test",
        "child::root_module::package",
    ]
    assert len(seen) == 3
    assert any(("--target", "root_module") == (argv[argv.index("--target")], argv[argv.index("--target") + 1]) for argv in seen if "--target" in argv)
    parent_snapshot = bus.execution_store.snapshot("group-parent")
    assert parent_snapshot is not None
    assert parent_snapshot.run_lineage["federation_group"] == "ready"
    assert parent_snapshot.run_lineage["federation_workflow"] == "group:ready"
    for child_run in report.children:
        snapshot = bus.execution_store.snapshot(child_run.run_id)
        assert snapshot is not None
        assert snapshot.run_lineage["federation_group"] == "ready"
        assert snapshot.run_lineage["federation_workflow"] == "group:ready"
    from devtool.federation_observability import runtime_federation_projection
    parent_projection = runtime_federation_projection(parent_snapshot)
    assert parent_projection["group"] == "ready"
    assert all(child["group"] == "ready" for child in parent_projection["children"])


def test_cli_plan_and_run_accept_group_selector_and_require_exactly_one_subject(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    parent, _ = _fixture(tmp_path)
    env = {"DEVTOOL_GLOBAL_CONFIG": str(parent / "missing-global.toml")}
    with mock.patch.dict(os.environ, env, clear=False):
        assert cli.main(["--repo", str(parent), "federation", "plan", "--group", "ready", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    payload = payload.get("data", payload)
    assert payload["subject_kind"] == "group"
    assert payload["group"] == "ready"
    assert payload["workflow"] == "group:ready"

    with mock.patch.dict(os.environ, env, clear=False):
        assert cli.main(["--repo", str(parent), "federation", "plan", "validate-ready", "--group", "ready"]) == 1
    assert "exactly one" in capsys.readouterr().err.lower()


def test_completion_exposes_only_executable_groups_for_group_selector(tmp_path: Path) -> None:
    parent, _ = _fixture(tmp_path)
    config = _load(parent)
    state = build_completion_state(config)
    assert state.federation_groups == ["inner", "metadata", "ready"]
    assert state.federation_executable_groups == ["inner", "ready"]
    assert completion_values(config, "federation-executable-groups") == ["inner", "ready"]
    assert "_devtool_federation_groups()" in ZSH_COMPLETION
    assert "--group[execute/plan a federation group]" in ZSH_COMPLETION

def test_executable_group_cannot_nest_metadata_only_group(tmp_path: Path) -> None:
    child = tmp_path / "child"; child.mkdir()
    with pytest.raises(FederationConfigError, match="metadata-only group"):
        load_federation_config(
            {
                "schema": 1,
                "repositories": {"child": {"path": "child"}},
                "groups": {
                    "metadata": {"repositories": ["child"]},
                    "ready": ["@metadata"],
                },
            },
            owner_root=tmp_path,
        )


def test_group_identity_survives_environment_transport_and_observability(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from devtool.federation_observability import build_federation_observability
    from devtool.federation_transport import build_federation_transport_metadata
    from devtool.invocation_origin import federation_lineage_from_environment
    from devtool.oci_resilience import producer_identity
    from devtool.repository_identity import build_repository_identity

    parent, child = _fixture(tmp_path)
    config = _load(parent)
    observation = build_federation_observability(config, execution_snapshots=()).as_dict()
    assert observation["summary"]["executable_groups"] == 2
    assert observation["group_details"]["ready"]["executable"] is True
    assert observation["group_details"]["metadata"]["executable"] is False

    env = {
        "DEVTOOL_RUN_ID": "child-run",
        "DEVTOOL_FEDERATION_RELATIONSHIP": "child",
        "DEVTOOL_FEDERATION_PARENT_RUN_ID": "parent-run",
        "DEVTOOL_FEDERATION_PARENT_NODE_ID": "node",
        "DEVTOOL_FEDERATION_WORKFLOW": "group:ready",
        "DEVTOOL_FEDERATION_GROUP": "ready",
        "DEVTOOL_FEDERATION_STEP": "step",
        "DEVTOOL_FEDERATION_REPOSITORY": "child",
        "DEVTOOL_FEDERATION_TARGET": "app",
        "DEVTOOL_FEDERATION_CHILD_WORKFLOW": "validate",
        "DEVTOOL_FEDERATION_REF": "child::app::validate",
        "DEVTOOL_FEDERATION_REQUESTED_REF": "child::validate",
    }
    with mock.patch.dict(os.environ, env, clear=False):
        lineage = federation_lineage_from_environment()
    assert lineage["federation_group"] == "ready"

    identity = build_repository_identity(child, child / ".devtool.toml").as_dict()
    metadata = build_federation_transport_metadata(
        {"run_lineage": lineage, "repository_identity": identity},
        local_root=child,
        execution_environment="local",
    )
    assert metadata["federation_group"] == "ready"

    producer = producer_identity(
        profile="tablet",
        destination="mike@worker",
        run_id="child-run",
        session="session",
        source_sync_id="sync",
        metadata={**metadata, "execution_provider": "ssh-native"},
    )
    assert producer["federation"]["group"] == "ready"
