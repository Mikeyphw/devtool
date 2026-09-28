from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import subprocess
import sys
import tomllib

import pytest

from devtool.config import load_project_config
from devtool.test_declarations import command_specs
from devtool.test_inventory import (
    EXECUTION_LANES,
    TestCheckGroup,
    TestInventory,
    compile_pytest_inventory,
    lane_for_pytest_item,
    policy_for_pytest_item,
    qualify_selection_environment,
)
from devtool.test_model import TestSpec

ROOT = Path(__file__).resolve().parents[1]


def _spec(check_id: str, selector: str, *, lane: str = "fast") -> TestSpec:
    return TestSpec(
        id=check_id,
        name=check_id,
        runner="pytest",
        selector=selector,
        lanes=(lane,),
        parallel_safety="parallel-safe",
        budget_class=lane,
        contract_level="public-contract",
    )


def test_fast_lane_excludes_measured_campaigns_and_broad_seals() -> None:
    inventory = compile_pytest_inventory(ROOT)
    fast = set(inventory.select(lanes=("fast",)).resolved_pytest_nodes)
    heavy = (
        "tests/test_frx_x04_real_repo_adoption_reseal.py",
        "tests/test_frx_y12_shared_library_final_reseal.py",
        "tests/test_git_frx15_git_federated_bisect.py",
        "tests/test_git_frx12_campaign_stacks_range_diff.py",
        "tests/test_sshw06_final_worker_seal.py",
        "tests/test_ssh03_lifecycle_hardening_final_qualification.py",
    )
    for prefix in heavy:
        assert not any(node == prefix or node.startswith(prefix + "::") for node in fast), prefix
    assert any(rule.id == "campaign.frx-system" for rule in inventory.classification_rules)
    assert any(rule.id == "campaign.git-frx-system" for rule in inventory.classification_rules)
    assert any(rule.id == "campaign.any-seal-system" for rule in inventory.classification_rules)


def test_integration_lane_keeps_real_git_process_and_pty_boundaries() -> None:
    inventory = compile_pytest_inventory(ROOT)
    integration = inventory.select(lanes=("integration",))
    assert "test.git.transaction-isolation" in integration.resolved_check_ids
    policy = policy_for_pytest_item(
        root=ROOT,
        nodeid="tests/test_validation_interactive_menu.py::test_fallback_dashboard_is_navigable_in_a_pseudo_tty",
        marker_names=("slow_external",),
        path=str(ROOT / "tests/test_validation_interactive_menu.py"),
    )
    assert policy["lanes"] == ["integration"]
    assert "posix" in policy["required_capabilities"]
    # Legacy timing exemption alone must not redefine a test as external-tool work.
    assert lane_for_pytest_item(marker_names=("slow_external",), path="tests/test_small.py") == "fast"


def test_external_lane_has_exact_requirements_and_environment_identity() -> None:
    inventory = compile_pytest_inventory(ROOT)
    selection = inventory.select(groups=("roadmap.test.g2.external",))
    expected = {"test.external.gradle-smoke", "test.external.go-smoke", "test.external.javac-smoke"}
    assert set(selection.resolved_check_ids) == expected
    for check_id in selection.resolved_check_ids:
        spec = inventory.spec(check_id)
        assert spec.lanes == ("external",)
        assert spec.required_tools or spec.required_capabilities
    qualification = qualify_selection_environment(selection, inventory, probe_versions=False)
    environment = qualification.host["test_environment"]
    assert environment["declared"] is True
    assert environment["ready"] is True
    assert environment["manager"] == "uv"
    assert environment["manager_available"] is True
    assert Path(environment["manager_path"]).is_absolute()
    assert environment["requirements_lock_exists"] is True
    assert len(environment["requirements_lock_sha256"]) == 64
    assert environment["python_satisfied"] is True
    rows = {tool.name: tool for tool in qualification.tools}
    assert set(rows) == {"go", "gradle", "java", "javac"}
    for tool in rows.values():
        if tool.available:
            assert Path(tool.path).is_absolute()
        else:
            assert tool.name in qualification.missing_tools


def test_system_lane_contains_broad_qualification_and_full_is_exact_union() -> None:
    inventory = compile_pytest_inventory(ROOT)
    system_nodes = set(inventory.select(lanes=("system",)).resolved_pytest_nodes)
    assert any(node.startswith("tests/test_frx_x04_real_repo_adoption_reseal.py") for node in system_nodes)
    assert any(node.startswith("tests/test_sshw06_final_worker_seal.py") for node in system_nodes)
    full = set(inventory.select(lanes=("full",)).resolved_check_ids)
    union: set[str] = set()
    for lane in EXECUTION_LANES:
        union.update(inventory.select(lanes=(lane,)).resolved_check_ids)
    assert full == union


def test_stable_check_group_survives_selector_move_and_declared_job_consumes_group(tmp_path: Path) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_old.py").write_text("def test_contract(): pass\n", encoding="utf-8")
    manifest = tmp_path / "devtool-tests.toml"
    manifest.write_text(
        "schema_version=1\n"
        "[environment]\nmanager='uv'\nrequirements_lock='test.lock'\npython='>=3.11'\n"
        "[[checks]]\nid='stable.contract'\nselector='tests/test_old.py::test_contract'\nlanes=['fast']\nparallel_safety='parallel-safe'\ngroups=['roadmap.demo']\n"
        "[[groups]]\nid='roadmap.demo'\nchecks=['stable.contract']\n",
        encoding="utf-8",
    )
    (tmp_path / "test.lock").write_text("pytest==9.0.2\n", encoding="utf-8")
    before = compile_pytest_inventory(tmp_path)
    assert before.select(groups=("roadmap.demo",)).resolved_check_ids == ("stable.contract",)
    (tests / "test_old.py").rename(tests / "test_new.py")
    manifest.write_text(manifest.read_text().replace("test_old.py", "test_new.py"), encoding="utf-8")
    after = compile_pytest_inventory(tmp_path)
    assert after.select(groups=("roadmap.demo",)).resolved_check_ids == ("stable.contract",)
    assert after.spec("stable.contract").selector == "tests/test_new.py::test_contract"

    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\nversion='0'\n", encoding="utf-8")
    (tmp_path / ".devtool.toml").write_text(
        "[project]\nrunner='python'\n"
        "[[test]]\nid='roadmap-demo'\nrunner='pytest'\ncommand=['python','-m','pytest','-q']\nselection_groups=['roadmap.demo']\n",
        encoding="utf-8",
    )
    config = load_project_config(str(tmp_path), None, allow_ambiguous_target=True)
    spec = command_specs(config, ["roadmap-demo"])[0]
    assert spec.argv[-1] == "tests/test_new.py::test_contract"
    assert spec.metadata["test_selection_groups"] == ["roadmap.demo"]
    assert spec.metadata["test_selection"]["resolved_check_ids"] == ["stable.contract"]


def test_unknown_marker_fails_real_collection(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\naddopts='--strict-markers --strict-config'\ntestpaths=['tests']\n",
        encoding="utf-8",
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_unknown.py").write_text(
        "import pytest\n@pytest.mark.this_marker_must_not_exist\ndef test_demo(): pass\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--strict-markers", "--strict-config", "--collect-only", "-q"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert result.returncode != 0
    assert "this_marker_must_not_exist" in (result.stdout + result.stderr)


def test_selection_and_shards_are_deterministic_explainable_and_immutable() -> None:
    inventory = compile_pytest_inventory(ROOT)
    first = inventory.select(groups=("roadmap.test.g2",), shard_count=4, provenance={"gate": "TEST-G2"})
    second = inventory.select(groups=("roadmap.test.g2",), shard_count=4, provenance={"gate": "TEST-G2"})
    assert first.to_dict() == second.to_dict()
    assert first.shard_plan is not None
    assert first.shard_plan.verify_complete(first.resolved_check_ids) == (True, "complete")
    assert all(inventory.explain(check)["identity_kind"] == "durable" for check in first.resolved_check_ids)
    with pytest.raises(FrozenInstanceError):
        first.selection_id = "changed"  # type: ignore[misc]


def test_inventory_rejects_unknown_dependencies_and_false_external_contracts() -> None:
    with pytest.raises(ValueError, match="unknown dependencies"):
        TestInventory((_spec("a", "tests/a.py"), TestSpec(id="b", name="b", runner="pytest", selector="tests/b.py", lanes=("fast",), dependencies=("missing",))))
    with pytest.raises(ValueError, match="exact tool or host capability"):
        TestInventory((TestSpec(id="external", name="external", runner="pytest", selector="tests/e.py", lanes=("external",), parallel_safety="serial-process-global"),))


def test_dependency_requirements_are_validated_and_selected_as_stable_closure() -> None:
    dependency = _spec("fixture.base", "tests/base.py")
    dependent = TestSpec(
        id="contract.child",
        name="contract.child",
        runner="pytest",
        selector="tests/child.py",
        lanes=("fast",),
        parallel_safety="parallel-safe",
        dependencies=("fixture.base",),
    )
    inventory = TestInventory((dependency, dependent))
    selection = inventory.select(checks=("contract.child",))
    assert selection.resolved_check_ids == ("contract.child", "fixture.base") or set(selection.resolved_check_ids) == {"contract.child", "fixture.base"}
    with pytest.raises(ValueError, match="dependency cycle"):
        TestInventory((
            TestSpec(id="a", name="a", runner="pytest", selector="tests/a.py", lanes=("fast",), dependencies=("b",)),
            TestSpec(id="b", name="b", runner="pytest", selector="tests/b.py", lanes=("fast",), dependencies=("a",)),
        ))


def test_lane_filtered_dependency_cannot_silently_widen_fast_feedback() -> None:
    inventory = TestInventory((
        _spec("fast.child", "tests/fast.py"),
        TestSpec(id="system.dep", name="system.dep", runner="pytest", selector="tests/system.py", lanes=("system",), dependencies=()),
        TestSpec(id="fast.with-system-dep", name="fast.with-system-dep", runner="pytest", selector="tests/child.py", lanes=("fast",), dependencies=("system.dep",)),
    ))
    with pytest.raises(ValueError, match="not eligible for requested lane"):
        inventory.select(lanes=("fast",), checks=("fast.with-system-dep",))


def test_unclassified_discovery_is_conservatively_serial(tmp_path: Path) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_plain.py").write_text("def test_plain(): pass\n", encoding="utf-8")
    inventory = compile_pytest_inventory(tmp_path)
    spec = inventory.specs[0]
    assert spec.parallel_safety == "serial-process-global"
    assert "serial-process-global" in spec.resources


def test_artifact_builder_covers_overlay_snapshot_and_bundle(artifact_fixture_builder) -> None:
    overlay = artifact_fixture_builder.overlay({"a.txt": "a\n"}, name="a")
    snapshot = artifact_fixture_builder.snapshot({"b.txt": "b\n"}, name="b")
    bundle = artifact_fixture_builder.bundle([overlay], name="bundle")
    import zipfile
    with zipfile.ZipFile(overlay) as zf:
        assert json.loads(zf.read(".devtool-artifact.json"))["type"] == "overlay"
    with zipfile.ZipFile(snapshot) as zf:
        assert json.loads(zf.read(".devtool-artifact.json"))["type"] == "snapshot"
    with zipfile.ZipFile(bundle) as zf:
        manifest = json.loads(zf.read("devtool-bundle.json"))
        assert manifest["type"] == "overlay_bundle"
        assert manifest["apply_order"][0]["sha256"]


def test_repository_boundary_fake_avoids_git_for_unit_contract(stable_repository_boundary) -> None:
    stable_repository_boundary.head = "abc123"
    stable_repository_boundary.tracked_paths = ("src/app.py",)
    assert stable_repository_boundary.snapshot() == {
        "head": "abc123",
        "branch": "main",
        "dirty": False,
        "tracked_paths": ["src/app.py"],
    }
    assert stable_repository_boundary.calls == ["snapshot"]


def test_repository_test_environment_is_explicit_and_pinned() -> None:
    raw = tomllib.loads((ROOT / "devtool-tests.toml").read_text(encoding="utf-8"))
    env = raw["environment"]
    lock = ROOT / env["requirements_lock"]
    assert env["manager"] == "uv"
    assert env["python"] == ">=3.11"
    assert lock.read_text(encoding="utf-8").strip().splitlines()[-1] == "pytest==9.0.2"


def test_declared_test_without_parallel_qualification_defaults_serial(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\nversion='0'\n", encoding="utf-8")
    (tmp_path / ".devtool.toml").write_text(
        "[project]\nrunner='python'\n[[test]]\nid='plain'\nrunner='pytest'\ncommand=['python','-m','pytest','-q']\n",
        encoding="utf-8",
    )
    config = load_project_config(str(tmp_path), None, allow_ambiguous_target=True)
    assert config.tests["plain"].parallel_safety == "serial-process-global"


def test_repository_declared_g2_job_uses_stable_group_not_pytest_paths() -> None:
    config = load_project_config(str(ROOT), None, target_name="devtool", allow_ambiguous_target=True)
    declared = config.tests["roadmap-test-g2"]
    assert declared.selection_groups == ("roadmap.test.g2",)
    assert not any(token.startswith("tests/") for token in declared.command)
    spec = command_specs(config, ["roadmap-test-g2"])[0]
    assert spec.metadata["test_selection_groups"] == ["roadmap.test.g2"]
    assert "test.g2.lane-selection" in spec.metadata["test_selection"]["resolved_check_ids"]
    assert any(token == "tests/test_test_g2_lane_selection_gate.py" for token in spec.argv)


def test_multi_validation_pytest_summaries_are_aggregated_for_gate_reporting() -> None:
    from devtool.models import OperationResult
    from devtool.summary import build_json_summary

    results = [
        OperationResult(
            "test:g2-fast",
            ["python", "-m", "pytest"],
            0,
            1.0,
            "39 passed in 1.00s",
            [],
            metadata={"overlay_index": 1, "overlay_total": 1, "overlay_label": "g2", "overlay_target": "devtool"},
        ),
        OperationResult(
            "test:g2-integration",
            ["python", "-m", "pytest"],
            0,
            1.0,
            "28 passed, 1 skipped in 2.00s",
            [],
            metadata={"overlay_index": 1, "overlay_total": 1, "overlay_label": "g2", "overlay_target": "devtool"},
        ),
        OperationResult(
            "test:g2-system",
            ["python", "-m", "pytest"],
            0,
            1.0,
            "5 passed in 3.00s",
            [],
            metadata={"overlay_index": 1, "overlay_total": 1, "overlay_label": "g2", "overlay_target": "devtool"},
        ),
    ]
    summary = build_json_summary(results, repo_root=str(ROOT))
    assert summary["tests"] == {"passed": 72, "failed": 0, "skipped": 1, "total": 73}
    assert summary["overlays"][0]["tests"] == {"passed": 72, "failed": 0, "skipped": 1, "total": 73}
