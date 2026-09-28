from __future__ import annotations

from dataclasses import FrozenInstanceError
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from devtool.models import CommandSpec
from devtool.test_inventory import (
    EXECUTION_LANES,
    TestCheckGroup,
    TestInventory,
    compile_pytest_inventory,
    lane_for_pytest_item,
    lanes_for_pytest_item,
)
from devtool.test_model import TestSpec

ROOT = Path(__file__).resolve().parents[1]


def _spec(check_id: str, selector: str, *, lanes=("fast",), aliases=(), resources=(), safety="parallel-safe") -> TestSpec:
    return TestSpec(
        id=check_id,
        name=check_id,
        runner="pytest",
        selector=selector,
        aliases=aliases,
        behavior="test-system.demo",
        lanes=lanes,
        resources=resources,
        parallel_safety=safety,
    )


def test_testspec_separates_durable_identity_from_current_selector() -> None:
    spec = _spec("check.parser", "tests/test_parser.py::test_parse", aliases=("OLD-PARSER",))
    moved = _spec("check.parser", "tests/parser/test_contract.py::test_parse", aliases=("OLD-PARSER",))
    assert spec.id == moved.id == "check.parser"
    assert spec.selector != moved.selector
    assert spec.aliases == moved.aliases == ("OLD-PARSER",)
    with pytest.raises(FrozenInstanceError):
        spec.selector = "other"  # type: ignore[misc]


def test_inventory_alias_groups_and_explanations_are_stable() -> None:
    inventory = TestInventory(
        (_spec("check.fast", "tests/test_fast.py", aliases=("FAST-OLD",)), _spec("check.sys", "tests/test_seal.py", lanes=("system",))),
        (TestCheckGroup("roadmap.demo", ("check.fast", "check.sys"), aliases=("DEMO-GATE",)),),
        source="unit",
    )
    assert inventory.resolve_check_id("FAST-OLD") == "check.fast"
    assert inventory.resolve_group_id("DEMO-GATE") == "roadmap.demo"
    explained = inventory.explain("check.fast")
    assert explained["identity_kind"] == "durable"
    assert explained["selector"] == "tests/test_fast.py"


def test_semantic_lanes_are_selections_not_duplicate_commands() -> None:
    inventory = TestInventory((
        _spec("fast", "tests/test_fast.py", lanes=("fast",)),
        _spec("integration", "tests/test_git.py", lanes=("integration",)),
        _spec("external", "tests/test_compiler.py", lanes=("external",)),
        _spec("system", "tests/test_final_seal.py", lanes=("system",)),
    ))
    assert inventory.select(lanes=("fast",)).resolved_check_ids == ("fast",)
    assert set(inventory.select(lanes=("full",)).resolved_check_ids) == {"fast", "integration", "external", "system"}
    assert set(EXECUTION_LANES) == {"fast", "integration", "external", "system"}
    with pytest.raises(ValueError, match="unknown test lane"):
        inventory.select(lanes=("mystery",))


def test_selection_is_immutable_deterministic_and_explainable() -> None:
    inventory = TestInventory((
        _spec("a", "tests/a.py", resources=("requires-git",)),
        _spec("b", "tests/b.py", resources=("serial-repository",), safety="serial-repository"),
    ))
    first = inventory.select(lanes=("fast",), provenance={"cfg": "unit"}, shard_count=3)
    second = inventory.select(lanes=("fast",), provenance={"cfg": "unit"}, shard_count=3)
    assert first.selection_id == second.selection_id
    assert first.shard_plan is not None and second.shard_plan is not None
    assert first.shard_plan.to_dict() == second.shard_plan.to_dict()
    ok, detail = first.shard_plan.verify_complete(first.resolved_check_ids)
    assert (ok, detail) == (True, "complete")
    assert first.resources == ("requires-git", "serial-repository")
    assert first.serial_constraints == ("serial-repository",)
    with pytest.raises(FrozenInstanceError):
        first.selection_id = "mutated"  # type: ignore[misc]


def test_manifest_compilation_preserves_registered_checks_and_marks_fallback_identity(tmp_path: Path) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_demo.py").write_text("def test_registered(): pass\n\ndef test_unregistered(): pass\n", encoding="utf-8")
    (tmp_path / "devtool-tests.toml").write_text(
        "schema_version=1\n"
        "[[checks]]\n"
        "id='demo.registered'\n"
        "selector='tests/test_demo.py::test_registered'\n"
        "aliases=['OLD-DEMO']\n"
        "lanes=['fast']\n",
        encoding="utf-8",
    )
    inventory = compile_pytest_inventory(tmp_path)
    assert inventory.resolve_check_id("OLD-DEMO") == "demo.registered"
    fallback = [spec for spec in inventory.specs if spec.metadata.get("selector_derived")]
    assert len(fallback) == 1
    assert fallback[0].selector.endswith("::test_unregistered")
    assert inventory.explain("demo.registered")["identity_kind"] == "durable"


def test_explicit_pytest_metadata_supplies_durable_id_tool_and_resource_policy(tmp_path: Path) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_tool.py").write_text(
        "import pytest\n"
        "@pytest.mark.devtool_check('demo.external-tool')\n"
        "@pytest.mark.behavior('demo.tool-contract')\n"
        "@pytest.mark.external\n"
        "@pytest.mark.requires_tool('git')\n"
        "@pytest.mark.public_contract\n"
        "def test_tool(): pass\n",
        encoding="utf-8",
    )
    inventory = compile_pytest_inventory(tmp_path)
    spec = inventory.spec("demo.external-tool")
    assert spec.selector == "tests/test_tool.py::test_tool"
    assert spec.required_tools == ("git",)
    assert spec.lanes == ("external",)
    assert "requires-tool:git" in spec.resources
    assert spec.behavior == "demo.tool-contract"
    assert spec.contract_level == "public-contract"
    assert inventory.explain(spec.id)["identity_kind"] == "durable"


def test_lane_classifier_keeps_fast_external_integration_and_system_distinct() -> None:
    assert lane_for_pytest_item(marker_names=(), path="tests/test_small.py") == "fast"
    assert lane_for_pytest_item(marker_names=("requires_git",), path="tests/test_repo.py") == "integration"
    assert lane_for_pytest_item(marker_names=("requires_tool",), path="tests/test_compiler.py") == "external"
    assert lane_for_pytest_item(marker_names=(), path="tests/test_final_seal.py") == "system"
    assert lanes_for_pytest_item(
        root=ROOT,
        nodeid="tests/test_test_g1_isolation_trust_gate.py::test_validation_family_is_order_independent_across_fresh_processes",
        marker_names=(),
        path=str(ROOT / "tests/test_test_g1_isolation_trust_gate.py"),
    ) == ("system",)


def test_repo_and_git_factories_remove_repeated_repository_boilerplate(repo_factory, git_repo_factory) -> None:
    plain = repo_factory.create(files={"src/app.py": "VALUE=1\n"})
    assert (plain / "src/app.py").read_text() == "VALUE=1\n"
    repo = git_repo_factory.create(files={"README.md": "demo\n"})
    before = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    (repo / "README.md").write_text("changed\n", encoding="utf-8")
    after = git_repo_factory.commit(repo, "change")
    assert before != after


def test_validation_session_factory_uses_unique_repository_roots(validation_session_factory) -> None:
    one = validation_session_factory.create(persist=False)
    two = validation_session_factory.create(persist=False)
    assert one.session_id != two.session_id
    assert one.repo_root != two.repo_root
    assert Path(one.repo_root).name.startswith("validation-repo-")


def test_artifact_fixture_builder_is_deterministic_and_manifest_hashes_payload(artifact_fixture_builder) -> None:
    one = artifact_fixture_builder.overlay({"demo.txt": "hello\n"}, name="same")
    two = artifact_fixture_builder.overlay({"demo.txt": "hello\n"}, name="same")
    assert one.read_bytes() == two.read_bytes()
    with zipfile.ZipFile(one) as archive:
        manifest = json.loads(archive.read(".devtool-artifact.json"))
        assert manifest["expected_post_sha256"]["demo.txt"] == hashlib.sha256(b"hello\n").hexdigest()


def test_stable_command_boundary_records_public_inputs_not_internal_kwargs(stable_command_boundary) -> None:
    result = stable_command_boundary.run(["tool", "arg"], cwd="/tmp/demo", env={"A": "1"})
    assert result.returncode == 0
    assert stable_command_boundary.calls == [{"argv": ("tool", "arg"), "cwd": "/tmp/demo", "env": {"A": "1"}}]


def test_repository_inventory_declares_durable_roadmap_groups() -> None:
    inventory = compile_pytest_inventory(ROOT)
    g2 = inventory.select(groups=("TEST-G2-group",))
    assert "test.x02.inventory-selection" in g2.resolved_check_ids
    assert "test.g1.isolation-trust" not in g2.resolved_check_ids
    assert all(inventory.explain(check)["identity_kind"] == "durable" for check in g2.resolved_check_ids)


def test_cli_inventory_and_selection_emit_canonical_json(devtool_cli) -> None:
    inventory = devtool_cli(["-r", str(ROOT), "pytest", "inventory", "--group", "roadmap.test.g2", "--json"])
    assert inventory.returncode == 0, inventory.stderr
    payload = json.loads(inventory.stdout)
    data = payload.get("data", payload)
    ids = {row["check_id"] for row in data["specs"]}
    assert "test.x02.inventory-selection" in ids

    selected = devtool_cli(["-r", str(ROOT), "pytest", "select", "--group", "roadmap.test.g2", "--shards", "3", "--json"])
    assert selected.returncode == 0, selected.stderr
    selection_payload = json.loads(selected.stdout)
    selection = selection_payload.get("data", selection_payload)
    assert selection["selection_id"].startswith("tsel-")
    assert selection["shard_plan"]["shard_count"] == 3
    flattened = [check for shard in selection["shard_plan"]["shards"] for check in shard["check_ids"]]
    assert sorted(flattened) == sorted(selection["resolved_check_ids"])


def test_declared_testspec_inventory_policy_survives_config_and_command_bridge(tmp_path: Path) -> None:
    from devtool.config import load_project_config
    from devtool.test_declarations import command_specs

    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n", encoding="utf-8")
    (tmp_path / ".devtool.toml").write_text(
        "[project]\nrunner='python'\n"
        "[[test]]\n"
        "id='contract.demo'\nrunner='pytest'\ncommand=['python','-m','pytest','-q']\n"
        "selector='tests/test_demo.py'\naliases=['OLD-DEMO']\nbehavior='demo.contract'\n"
        "lanes=['fast']\nresources=['requires-git']\nparallel_safety='serial-repository'\n"
        "required_tools=['git']\nrequired_capabilities=['posix']\nbudget_class='fast'\ncontract_level='public-contract'\n"
        "groups=['roadmap.demo']\ndependencies=['fixture.demo']\n",
        encoding="utf-8",
    )
    config = load_project_config(str(tmp_path), None, allow_ambiguous_target=True)
    declared = config.tests["contract.demo"]
    assert declared.selector == "tests/test_demo.py"
    assert declared.aliases == ("OLD-DEMO",)
    spec = command_specs(config, ["contract.demo"])[0]
    assert spec.metadata["test_lanes"] == ["fast"]
    assert spec.metadata["test_required_tools"] == ["git"]
    assert spec.metadata["test_parallel_safety"] == "serial-repository"
