from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest

from devtool import cli
from devtool.config import load_project_config
from devtool.core.events import EventBus, RunFinished, RunStarted
from devtool.federation_execution import run_federated_workflow
from devtool.federation_config import FederationConfigError, federation_as_dict, load_federation_config
from devtool.federation_plan import FederationPlanError, build_federated_plan
from devtool.repository_identity import build_repository_identity


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


def _write_child(root: Path, *, default_target: str | None = "app") -> None:
    workspace = ""
    if default_target:
        workspace = f'\n[workspace]\ndefault_target = "{default_target}"\n'
    (root / ".devtool.toml").write_text(
        f'''[project]
runner = "command"
{workspace}
[jobs.one]
runner = "command"
command = ["echo", "one"]

[jobs.two]
runner = "command"
command = ["echo", "two"]

[targets.app]
runner = "command"
root = "."

[targets.app.workflows]
build = ["job:one"]
validate = ["job:one"]

[targets.root_module]
runner = "command"
root = "."

[targets.root_module.workflows]
build = ["job:two"]
package = ["job:two"]
''',
        encoding="utf-8",
    )


def _write_parent(root: Path, child: Path, body: str) -> None:
    (root / ".devtool.toml").write_text(
        f'''[project]
runner = "command"

[federation]
schema = 1

[federation.repositories.child]
path = "{child.as_posix()}"

{body}
''',
        encoding="utf-8",
    )


def test_parser_accepts_legacy_and_target_qualified_refs_and_serializes_intent(tmp_path: Path) -> None:
    owner = tmp_path / "owner"
    child = tmp_path / "child"
    owner.mkdir()
    child.mkdir()
    config = load_federation_config(
        {
            "schema": 1,
            "repositories": {"child": {"path": "../child"}},
            "workflows": {
                "release": {
                    "depends_on": [
                        " child :: build ",
                        " child :: root_module :: package ",
                    ]
                }
            },
        },
        owner_root=owner,
    )
    legacy, qualified = config.workflows["release"].steps
    assert legacy.ref == "child::build"
    assert legacy.repository == "child"
    assert legacy.target == ""
    assert legacy.workflow == "build"
    assert legacy.target_qualified is False
    assert legacy.canonical_ref() == "child::build"

    assert qualified.ref == "child::root_module::package"
    assert qualified.repository == "child"
    assert qualified.target == "root_module"
    assert qualified.workflow == "package"
    assert qualified.target_qualified is True
    assert qualified.canonical_ref() == "child::root_module::package"

    payload = federation_as_dict(config)
    steps = payload["workflows"]["release"]["steps"]
    assert steps[0]["target"] == ""
    assert steps[0]["target_qualified"] is False
    assert steps[1]["target"] == "root_module"
    assert steps[1]["target_qualified"] is True


@pytest.mark.parametrize(
    "ref",
    [
        "child",
        "child::",
        "child::::build",
        "child::root_module::",
        "child::root_module::build::extra",
    ],
)
def test_parser_rejects_malformed_reference_arity_or_empty_components(tmp_path: Path, ref: str) -> None:
    child = tmp_path / "child"
    child.mkdir()
    with pytest.raises(FederationConfigError, match="repository::workflow|non-empty"):
        load_federation_config(
            {
                "schema": 1,
                "repositories": {"child": {"path": "child"}},
                "workflows": {"release": {"depends_on": [ref]}},
            },
            owner_root=tmp_path,
        )


def test_target_qualified_ref_selects_non_default_child_target(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    child = tmp_path / "child"
    parent.mkdir()
    child.mkdir()
    _write_child(child, default_target="app")
    _write_parent(
        parent,
        child,
        '''[federation.workflows.release]
depends_on = ["child::root_module::package"]''',
    )

    config = _load(parent)
    graph = build_federated_plan(config, "release")
    executable = [node for node in graph.nodes if not node.synthetic]
    assert executable
    assert {node.target for node in executable} == {"root_module"}
    assert {node.workflow for node in executable} == {"package"}
    assert {node.federation_ref for node in executable} == {"child::root_module::package"}
    assert {node.metadata["federation_ref"] for node in executable} == {"child::root_module::package"}
    assert all(node.metadata["target_explicit"] is True for node in executable)
    assert all(node.metadata["requested_ref"] == "child::root_module::package" for node in executable)
    machine = graph.as_dict()
    assert {node["federation_ref"] for node in machine["nodes"] if not node["synthetic"]} == {
        "child::root_module::package"
    }


def test_legacy_ref_keeps_selected_default_target_semantics_but_plan_is_canonical(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    child = tmp_path / "child"
    parent.mkdir()
    child.mkdir()
    _write_child(child, default_target="app")
    _write_parent(
        parent,
        child,
        '''[federation.workflows.release]
depends_on = ["child::build"]''',
    )

    graph = build_federated_plan(_load(parent), "release")
    executable = [node for node in graph.nodes if not node.synthetic]
    assert {node.target for node in executable} == {"app"}
    assert {node.federation_ref for node in executable} == {"child::app::build"}
    assert all(node.metadata["target_explicit"] is False for node in executable)
    assert all(node.metadata["requested_ref"] == "child::build" for node in executable)


def test_explicit_target_resolves_an_otherwise_ambiguous_child(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    child = tmp_path / "child"
    parent.mkdir()
    child.mkdir()
    _write_child(child, default_target=None)

    _write_parent(
        parent,
        child,
        '''[federation.workflows.release]
depends_on = ["child::build"]''',
    )
    with pytest.raises(FederationPlanError, match="no unambiguous selected target"):
        build_federated_plan(_load(parent), "release")

    _write_parent(
        parent,
        child,
        '''[federation.workflows.release]
depends_on = ["child::root_module::build"]''',
    )
    graph = build_federated_plan(_load(parent), "release")
    assert {node.target for node in graph.nodes if not node.synthetic} == {"root_module"}


def test_target_qualified_ref_fails_closed_for_unknown_target(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    child = tmp_path / "child"
    parent.mkdir()
    child.mkdir()
    _write_child(child)
    _write_parent(
        parent,
        child,
        '''[federation.workflows.release]
depends_on = ["child::missing::build"]''',
    )
    with pytest.raises(FederationPlanError, match="no target 'missing'.*Available targets: app, root_module"):
        build_federated_plan(_load(parent), "release")


def test_target_qualified_ref_fails_closed_when_workflow_is_not_on_selected_target(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    child = tmp_path / "child"
    parent.mkdir()
    child.mkdir()
    _write_child(child)
    _write_parent(
        parent,
        child,
        '''[federation.workflows.release]
depends_on = ["child::root_module::validate"]''',
    )
    with pytest.raises(FederationPlanError, match="target 'root_module' has no workflow 'validate'"):
        build_federated_plan(_load(parent), "release")


def test_same_repository_same_workflow_name_on_two_targets_has_distinct_canonical_graph_identity(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    child = tmp_path / "child"
    parent.mkdir()
    child.mkdir()
    _write_child(child)
    _write_parent(
        parent,
        child,
        '''[federation.workflows.dual]
steps = [
  { id = "app", ref = "child::app::build" },
  { id = "root", ref = "child::root_module::build" },
]''',
    )

    graph = build_federated_plan(_load(parent), "dual")
    executable = [node for node in graph.nodes if not node.synthetic]
    assert {node.federation_ref for node in executable} == {
        "child::app::build",
        "child::root_module::build",
    }
    keys = [node.key for node in executable]
    assert len(keys) == len(set(keys))
    assert any("app.build" in key for key in keys)
    assert any("root_module.build" in key for key in keys)


def test_target_qualified_ref_drives_existing_execution_bridge_to_explicit_target(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    child = tmp_path / "child"
    parent.mkdir()
    child.mkdir()
    _write_child(child, default_target="app")
    _write_parent(
        parent,
        child,
        '''[federation.workflows.release]
depends_on = ["child::root_module::package"]''',
    )
    config = _load(parent)
    bus = EventBus()
    seen_argv: list[tuple[str, ...]] = []

    def reenter(argv: list[str]) -> int:
        seen_argv.append(tuple(argv))
        run_id = os.environ["DEVTOOL_RUN_ID"]
        root = Path(argv[argv.index("--repo") + 1])
        lineage = {
            "kind": "federation",
            "relationship": "child",
            "parent_run_id": os.environ["DEVTOOL_FEDERATION_PARENT_RUN_ID"],
            "parent_node_id": os.environ["DEVTOOL_FEDERATION_PARENT_NODE_ID"],
            "child_run_id": run_id,
        }
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

    report = run_federated_workflow(
        config,
        "release",
        reenter=reenter,
        publish=bus.publish,
        snapshot_lookup=bus.execution_store.snapshot,
        parent_run_id="frx-x01-parent",
    )
    assert report.ok
    assert report.children[0].target == "root_module"
    assert len(seen_argv) == 1
    argv = seen_argv[0]
    assert argv[argv.index("--target") + 1] == "root_module"
    assert argv[argv.index("invoke") + 1] == "package"


def test_cli_plan_json_exposes_canonical_target_qualified_identity(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    parent = tmp_path / "parent"
    child = tmp_path / "child"
    parent.mkdir()
    child.mkdir()
    _write_child(child, default_target="app")
    _write_parent(
        parent,
        child,
        '''[federation.workflows.release]
depends_on = ["child::root_module::package"]''',
    )
    with mock.patch.dict(
        os.environ,
        {"DEVTOOL_GLOBAL_CONFIG": str(parent / "missing-global.toml")},
        clear=False,
    ):
        assert cli.main(["--repo", str(parent), "federation", "plan", "release", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    payload = payload.get("data", payload)
    executable = [node for node in payload["nodes"] if not node["synthetic"]]
    assert executable
    assert {node["federation_ref"] for node in executable} == {"child::root_module::package"}
    assert {node["target"] for node in executable} == {"root_module"}
