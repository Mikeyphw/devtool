from __future__ import annotations

import os
from pathlib import Path

import pytest

from devtool.config import load_project_config
from devtool.federation_execution import run_federated_workflow
from devtool.federation_plan import build_federated_plan


def _load(root: Path):
    return load_project_config(
        root, root / ".devtool.toml", target_name="",
        allow_ambiguous_target=True, allow_ambiguous_project_kind=True,
    )


def _leaf(root: Path) -> None:
    root.mkdir(parents=True)
    (root / ".devtool.toml").write_text(
        '''[project]\nrunner="command"\n[workspace]\ndefault_target="app"\n[targets.app]\nrunner="command"\n[targets.app.jobs.check]\nrunner="command"\ncommand=["true"]\n[targets.app.workflows]\nvalidate=["job:check"]\n''',
        encoding="utf-8",
    )


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    master = tmp_path / "master"; platform = tmp_path / "platform"; leaf = tmp_path / "leaf"
    master.mkdir(); platform.mkdir(); _leaf(leaf)
    (platform / ".devtool.toml").write_text(
        '''[project]\nrunner="command"\n[federation]\nschema=1\n[federation.repositories.app]\npath="../leaf"\n[federation.workflows.release]\nsteps=[{id="app",ref="app::app::validate"}]\n[federation.groups.release-ready]\nmembers=["release"]\n''',
        encoding="utf-8",
    )
    (master / ".devtool.toml").write_text(
        '''[project]\nrunner="command"\n[federation]\nschema=1\n[federation.repositories.platform]\npath="../platform"\n[federation.workflows.ship]\nsteps=[{id="nested",ref="platform::@federation::release"}]\n[federation.workflows.grouped]\nsteps=[{id="nested",ref="platform::@group::release-ready"}]\n''',
        encoding="utf-8",
    )
    return master, platform, leaf


def test_hierarchical_refs_parse_and_parent_plan_does_not_flatten_grandchildren(tmp_path: Path) -> None:
    master, _platform, _leaf_root = _fixture(tmp_path)
    config = _load(master)
    step = config.federation.workflows["ship"].steps[0]
    assert step.hierarchical and step.contract_type == "federation"
    assert step.canonical_ref() == "platform::@federation::release"
    graph = build_federated_plan(config, "ship")
    children = [node for node in graph.nodes if not node.synthetic]
    assert len(children) == 1
    node = children[0]
    assert node.key == "platform::@federation::release"
    assert node.metadata["hierarchical_federation"]["flattened_grandchildren"] is False
    assert node.metadata["hierarchical_federation"]["ownership_depth"] == 1
    assert "app::" not in " ".join(item.key for item in graph.nodes)


def test_nested_group_is_one_parent_node_and_reentry_uses_child_federation_cli(tmp_path: Path) -> None:
    master, platform, _leaf_root = _fixture(tmp_path)
    config = _load(master)
    calls: list[list[str]] = []
    def reenter(argv: list[str]) -> int:
        calls.append(list(argv))
        assert Path(argv[argv.index("-r") + 1]).resolve() == platform.resolve()
        assert argv[-4:] == ["federation", "run", "--group", "release-ready"]
        assert os.environ["DEVTOOL_FEDERATION_PARENT_RUN_ID"] == "master-run"
        assert os.environ["DEVTOOL_FEDERATION_CHILD_CONTRACT"] == "group:release-ready"
        return 0
    report = run_federated_workflow(
        config, "grouped", reenter=reenter, snapshot_lookup=lambda _run_id: None,
        parent_run_id="master-run",
    )
    assert report.ok and len(calls) == 1
    child = report.children[0]
    assert child.contract_ref == "platform::@group::release-ready"
    assert child.workflow == "@group:release-ready"


def test_hierarchical_cycle_detection_spans_repository_boundaries(tmp_path: Path) -> None:
    a = tmp_path / "a"; b = tmp_path / "b"; c = tmp_path / "c"
    a.mkdir(); b.mkdir(); c.mkdir()
    (a / ".devtool.toml").write_text(
        '''[project]\nrunner="command"\n[federation]\nschema=1\n[federation.repositories.b]\npath="../b"\n[federation.workflows.loop]\nsteps=[{id="b",ref="b::@federation::loop"}]\n''', encoding="utf-8")
    (b / ".devtool.toml").write_text(
        '''[project]\nrunner="command"\n[federation]\nschema=1\n[federation.repositories.c]\npath="../c"\n[federation.workflows.loop]\nsteps=[{id="c",ref="c::@federation::loop"}]\n''', encoding="utf-8")
    (c / ".devtool.toml").write_text(
        '''[project]\nrunner="command"\n[federation]\nschema=1\n[federation.repositories.a]\npath="../a"\n[federation.workflows.loop]\nsteps=[{id="a",ref="a::@federation::loop"}]\n''', encoding="utf-8")
    config = _load(a)
    with pytest.raises(Exception, match="hierarchical federation cycle"):
        build_federated_plan(config, "loop")

def test_hierarchical_child_is_local_reentry_boundary_not_remote_provider_contract(tmp_path: Path) -> None:
    master, _platform, _leaf_root = _fixture(tmp_path)
    config = _load(master)
    graph = build_federated_plan(config, "ship")
    node = next(node for node in graph.nodes if not node.synthetic)
    contract = node.metadata["execution_contract"]
    assert contract["allowed_execution_environments"] == ["auto"]
    assert node.metadata["hierarchical_federation"]["reentry_boundary"] == "direct-child-federation"


def test_parent_resume_reuses_nested_federation_as_one_opaque_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from devtool.federation_resume import build_resume_plan, checkpoint_report, load_checkpoint

    state = tmp_path / "state"
    monkeypatch.setattr("devtool.federation_resume.state_dir", lambda: state)
    monkeypatch.setattr("devtool.federation_git_lineage.state_dir", lambda: state)
    master, _platform, _leaf_root = _fixture(tmp_path)
    config = _load(master)
    calls: list[list[str]] = []
    run_id = f"hierarchy-resume-{tmp_path.name}"
    report = run_federated_workflow(
        config, "ship", reenter=lambda argv: calls.append(list(argv)) or 0,
        publish=lambda _event: None, snapshot_lookup=lambda _run_id: None, parent_run_id=run_id,
    )
    assert report.ok and len(report.children) == 1 and len(calls) == 1
    checkpoint_report(config, report)
    resume = build_resume_plan(config, load_checkpoint(config, run_id), mode="resume")
    assert len(resume.decisions) == 1
    assert resume.decisions[0].step_id == "nested"
    assert resume.decisions[0].action == "reused"
    assert resume.decisions[0].prior_child["contract_ref"] == "platform::@federation::release"


def test_nested_cli_handoff_uses_outer_child_run_as_parent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from types import SimpleNamespace
    from devtool import cli

    master, _platform, _leaf_root = _fixture(tmp_path)
    config = _load(master)
    observed: dict[str, str] = {}

    class _Report:
        workflow = "ship"
        parent_run_id = "nested-run"
        status = "succeeded"
        exit_code = 0
        children = ()
        schedule = {}
        def as_dict(self):
            return {"workflow": self.workflow, "parent_run_id": self.parent_run_id, "status": self.status, "exit_code": 0, "children": []}

    def fake_run(parent, workflow_name, **kwargs):
        assert parent.repo_root == config.repo_root
        assert workflow_name == "ship"
        observed["parent_run_id"] = str(kwargs.get("parent_run_id") or "")
        return _Report()

    monkeypatch.setattr(cli, "_federation_parent_config", lambda _args: config)
    monkeypatch.setattr(cli, "run_federated_workflow", fake_run)
    monkeypatch.setenv("DEVTOOL_FEDERATION_RELATIONSHIP", "child")
    monkeypatch.setenv("DEVTOOL_RUN_ID", "outer-direct-child-run")
    args = cli.build_parser().parse_args(["--repo", str(master), "federation", "run", "ship", "--json"])
    assert cli._federation_command(args) == 0
    assert observed["parent_run_id"] == "outer-direct-child-run"


def test_hierarchical_cancellation_stays_at_direct_child_boundary(tmp_path: Path) -> None:
    master, _platform, _leaf_root = _fixture(tmp_path)
    config = _load(master)
    report = run_federated_workflow(
        config, "ship", reenter=lambda _argv: 130,
        publish=lambda _event: None, snapshot_lookup=lambda _run_id: None,
        parent_run_id="hierarchy-cancel-parent",
    )
    assert report.status == "cancelled"
    assert report.exit_code == 130
    assert len(report.children) == 1
    child = report.children[0]
    assert child.contract_ref == "platform::@federation::release"
    assert child.lifecycle == "cancelled"
    assert "app::" not in child.contract_ref


def test_parallel_hierarchical_reentry_propagates_hierarchy_depth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    master, _platform, _leaf_root = _fixture(tmp_path)
    config = _load(master)
    observed: list[dict[str, str]] = []

    def executor(_argv: list[str], env: dict[str, str]) -> int:
        observed.append(dict(env))
        return 0

    monkeypatch.setenv("DEVTOOL_FEDERATION_HIERARCHY_DEPTH", "2")
    report = run_federated_workflow(
        config, "ship", reenter=lambda _argv: 99,
        child_executor=executor, parallel=True, max_parallel=2,
        publish=lambda _event: None, snapshot_lookup=lambda _run_id: None,
        parent_run_id="hierarchy-parallel-parent",
    )
    assert report.ok
    assert len(observed) == 1
    assert observed[0]["DEVTOOL_FEDERATION_HIERARCHY_DEPTH"] == "3"
    assert observed[0]["DEVTOOL_FEDERATION_CONTRACT_REF"] == "platform::@federation::release"
