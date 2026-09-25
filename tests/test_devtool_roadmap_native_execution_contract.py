from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

from devtool.cli import _jobs, _plan, _workflows, build_parser
from devtool.config import load_project_config
from devtool.workflow_runtime import plan_selected_workflow


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TESTS = {
    "roadmap-artifact-validation",
    "roadmap-frx-baseline",
    "roadmap-frx-y01",
    "roadmap-frx-y04",
    "roadmap-frx-y05",
    "roadmap-frx-y06",
    "roadmap-frx-y07",
    "roadmap-git-frx01",
    "roadmap-git-frx02",
    "roadmap-git-frx03",
    "roadmap-git-frx04",
    "roadmap-git-frx05",
    "roadmap-git-frx06",
    "roadmap-gate1-regressions",
    "roadmap-workflow-exo-baseline",
}
EXPECTED_WORKFLOWS = {"roadmap-smoke", "roadmap-frx-y01", "roadmap-frx-y04", "roadmap-frx-y05", "roadmap-frx-y06", "roadmap-frx-y07", "roadmap-git-frx02", "roadmap-git-frx03", "roadmap-git-frx04", "roadmap-git-frx05", "roadmap-gate1", "roadmap-current"}


def _config():
    return load_project_config(str(ROOT), None, target_name="devtool")


def test_roadmap00_declares_truthful_target_local_non_test_jobs() -> None:
    cfg = _config()
    target = cfg.targets["devtool"]

    assert set(target.jobs) == {"source-syntax", "transaction-worktree-fixture", "self-refresh-root-probe", "commit-policy-fixture", "signing-policy-probe", "overlay-base-fixture", "overlay-rebase-fixture", "interface-conformance-fixture", "public-contract-dag-fixture"}
    syntax = target.jobs["source-syntax"]
    assert syntax.name == "devtool.source-syntax"
    assert syntax.runner == "command"
    assert syntax.command == ("python", "-m", "compileall", "-q", "devtool", "tests")
    assert syntax.inputs == ("devtool/**", "tests/**")
    assert syntax.outputs == (".devtool/cache/roadmap-pyc/**",)
    assert syntax.env["PYTHONPYCACHEPREFIX"] == ".devtool/cache/roadmap-pyc"
    assert syntax.metadata["target_local"] is True

    fixture = target.jobs["transaction-worktree-fixture"]
    assert fixture.command == ("python", "-m", "devtool.transaction_worktree", "fixture", "--repo", ".")
    assert "read_only" not in fixture.metadata
    assert fixture.metadata["roadmap_id"] == "GIT-FRX-03"

    refresh = target.jobs["self-refresh-root-probe"]
    assert refresh.command == ("python", "-m", "devtool.transaction_worktree", "self-refresh-root", "--fallback", ".")
    assert refresh.metadata["read_only"] is True
    assert refresh.metadata["roadmap_id"] == "GIT-FRX-03"

    policy_fixture = target.jobs["commit-policy-fixture"]
    assert policy_fixture.command == ("python", "-m", "devtool.git_commit_policy", "fixture")
    assert policy_fixture.metadata["read_only"] is True
    assert policy_fixture.metadata["roadmap_id"] == "GIT-FRX-04"

    signing_probe = target.jobs["signing-policy-probe"]
    assert signing_probe.command == ("python", "-m", "devtool.git_commit_policy", "probe", "--repo", ".")
    assert signing_probe.metadata["read_only"] is True
    assert signing_probe.metadata["roadmap_id"] == "GIT-FRX-04"

    base_fixture = target.jobs["overlay-base-fixture"]
    assert base_fixture.command == ("python", "-m", "devtool.artifact_base_contract", "fixture")
    assert base_fixture.metadata["read_only"] is True
    assert base_fixture.metadata["roadmap_id"] == "GIT-FRX-05"

    rebase_fixture = target.jobs["overlay-rebase-fixture"]
    assert rebase_fixture.command == ("python", "-m", "devtool.overlay_gate_fixture")
    assert rebase_fixture.metadata["read_only"] is True
    assert rebase_fixture.metadata["roadmap_id"] == "GIT-FRX-06"

    interface_fixture = target.jobs["interface-conformance-fixture"]
    assert interface_fixture.command == ("python", "-m", "devtool.federation_roadmap_fixtures", "interface")
    assert interface_fixture.metadata["read_only"] is True
    assert interface_fixture.metadata["roadmap_id"] == "FRX-Y06"

    contract_fixture = target.jobs["public-contract-dag-fixture"]
    assert contract_fixture.command == ("python", "-m", "devtool.federation_roadmap_fixtures", "public-contract")
    assert contract_fixture.metadata["read_only"] is True
    assert contract_fixture.metadata["roadmap_id"] == "FRX-Y07"

    interface_fixture = target.jobs["interface-conformance-fixture"]
    assert interface_fixture.command == ("python", "-m", "devtool.federation_roadmap_fixtures", "interface")
    assert interface_fixture.metadata["read_only"] is True
    assert interface_fixture.metadata["roadmap_id"] == "FRX-Y06"

    contract_fixture = target.jobs["public-contract-dag-fixture"]
    assert contract_fixture.command == ("python", "-m", "devtool.federation_roadmap_fixtures", "public-contract")
    assert contract_fixture.metadata["read_only"] is True
    assert contract_fixture.metadata["roadmap_id"] == "FRX-Y07"


def test_roadmap00_declared_tests_use_unique_structured_junit_evidence() -> None:
    cfg = _config()

    assert set(cfg.tests) == EXPECTED_TESTS
    junit_paths: set[str] = set()
    for test_id in sorted(EXPECTED_TESTS):
        spec = cfg.tests[test_id]
        assert spec.runner == "pytest"
        junit_args = [arg for arg in spec.command if arg.startswith("--junitxml=")]
        assert len(junit_args) == 1
        report = junit_args[0].split("=", 1)[1]
        assert report == f".devtool/reports/roadmap/{test_id}.xml"
        assert report not in junit_paths
        junit_paths.add(report)
        selected_files = [arg for arg in spec.command if arg.startswith("tests/")]
        assert selected_files
        assert all((ROOT / path).is_file() for path in selected_files)


def test_roadmap00_workflows_compose_only_existing_native_contracts() -> None:
    cfg = _config()
    target = cfg.targets["devtool"]

    assert set(target.workflows) == EXPECTED_WORKFLOWS

    smoke = plan_selected_workflow(cfg, "roadmap-smoke")
    assert [node.ref.raw for node in smoke.topological_order()] == [
        "job:source-syntax",
        "test:roadmap-workflow-exo-baseline",
    ]

    frx_y01 = plan_selected_workflow(cfg, "roadmap-frx-y01")
    assert {node.ref.raw for node in frx_y01.nodes} == {
        "job:source-syntax",
        "test:roadmap-frx-baseline",
        "test:roadmap-frx-y01",
        "test:roadmap-workflow-exo-baseline",
    }

    git_frx02 = plan_selected_workflow(cfg, "roadmap-git-frx02")
    assert [node.ref.raw for node in git_frx02.topological_order()] == [
        "job:source-syntax",
        "test:roadmap-git-frx01",
        "test:roadmap-git-frx02",
        "test:roadmap-workflow-exo-baseline",
    ]

    git_frx03 = plan_selected_workflow(cfg, "roadmap-git-frx03")
    assert {node.ref.raw for node in git_frx03.nodes} == {
        "job:source-syntax",
        "job:transaction-worktree-fixture",
        "job:self-refresh-root-probe",
        "test:roadmap-git-frx02",
        "test:roadmap-git-frx03",
        "test:roadmap-workflow-exo-baseline",
    }

    git_frx04 = plan_selected_workflow(cfg, "roadmap-git-frx04")
    assert {node.ref.raw for node in git_frx04.nodes} == {
        "job:source-syntax",
        "job:commit-policy-fixture",
        "job:signing-policy-probe",
        "test:roadmap-git-frx03",
        "test:roadmap-git-frx04",
        "test:roadmap-workflow-exo-baseline",
    }

    git_frx05 = plan_selected_workflow(cfg, "roadmap-git-frx05")
    assert {node.ref.raw for node in git_frx05.nodes} == {
        "job:source-syntax",
        "job:overlay-base-fixture",
        "test:roadmap-git-frx04",
        "test:roadmap-git-frx05",
        "test:roadmap-workflow-exo-baseline",
    }

    frx_y04 = plan_selected_workflow(cfg, "roadmap-frx-y04")
    assert {node.ref.raw for node in frx_y04.nodes} == {
        "job:source-syntax",
        "test:roadmap-frx-baseline",
        "test:roadmap-frx-y04",
        "test:roadmap-workflow-exo-baseline",
    }

    frx_y05 = plan_selected_workflow(cfg, "roadmap-frx-y05")
    assert {node.ref.raw for node in frx_y05.nodes} == {
        "job:source-syntax",
        "test:roadmap-frx-y04",
        "test:roadmap-frx-y05",
        "test:roadmap-workflow-exo-baseline",
    }

    frx_y06 = plan_selected_workflow(cfg, "roadmap-frx-y06")
    assert {node.ref.raw for node in frx_y06.nodes} == {
        "job:source-syntax",
        "job:interface-conformance-fixture",
        "test:roadmap-frx-y05",
        "test:roadmap-frx-y06",
        "test:roadmap-workflow-exo-baseline",
    }

    frx_y07 = plan_selected_workflow(cfg, "roadmap-frx-y07")
    assert {node.ref.raw for node in frx_y07.nodes} == {
        "job:source-syntax",
        "job:public-contract-dag-fixture",
        "test:roadmap-frx-y06",
        "test:roadmap-frx-y07",
        "test:roadmap-workflow-exo-baseline",
    }

    gate1 = plan_selected_workflow(cfg, "roadmap-gate1")
    assert {node.ref.raw for node in gate1.nodes} == {
        "job:source-syntax",
        "job:overlay-rebase-fixture",
        "test:roadmap-artifact-validation",
        "test:roadmap-frx-baseline",
        "test:roadmap-frx-y01",
        "test:roadmap-git-frx01",
        "test:roadmap-git-frx02",
        "test:roadmap-git-frx03",
        "test:roadmap-git-frx04",
        "test:roadmap-git-frx05",
        "test:roadmap-git-frx06",
        "test:roadmap-workflow-exo-baseline",
        "test:roadmap-gate1-regressions",
    }

    current = plan_selected_workflow(cfg, "roadmap-current")
    assert {node.ref.raw for node in current.nodes} == {
        "job:source-syntax",
        *(f"test:{test_id}" for test_id in EXPECTED_TESTS),
    }
    assert len(current.nodes) == 16


def test_roadmap00_machine_surfaces_export_stable_identity_and_schedule_reasons() -> None:
    cfg = _config()

    jobs_out = io.StringIO()
    with redirect_stdout(jobs_out):
        _jobs(cfg, as_json=True, selected_target="devtool")
    jobs = json.loads(jobs_out.getvalue())
    assert jobs["summary"]["target_local_jobs"] == 9
    assert jobs["completion"]["target_job_keys"] == [
        "devtool.commit-policy-fixture",
        "devtool.interface-conformance-fixture",
        "devtool.overlay-base-fixture",
        "devtool.overlay-rebase-fixture",
        "devtool.public-contract-dag-fixture",
        "devtool.self-refresh-root-probe",
        "devtool.signing-policy-probe",
        "devtool.source-syntax",
        "devtool.transaction-worktree-fixture",
    ]

    workflows_out = io.StringIO()
    with redirect_stdout(workflows_out):
        _workflows(cfg, as_json=True)
    workflows = json.loads(workflows_out.getvalue())["workflows"]["devtool"]
    assert EXPECTED_WORKFLOWS <= set(workflows)
    assert workflows["roadmap-current"]["schedule"] is not None

    args = build_parser().parse_args(["--target", "devtool", "plan", "roadmap-current", "--json"])
    plan_out = io.StringIO()
    with redirect_stdout(plan_out):
        assert _plan(cfg, args) == 0
    payload = json.loads(plan_out.getvalue())

    assert payload["action"] == "roadmap-current"
    assert payload["targets"] == ["devtool"]
    assert payload["summary"]["node_count"] == 16
    assert payload["summary"]["target_local_job_count"] == 1
    assert payload["summary"]["target_local_jobs"][0]["key"] == "devtool.source-syntax"
    assert payload["schedule"]["nodes"]
    by_ref = {node["ref"]: node for node in payload["graph"]["nodes"]}
    assert by_ref["job:source-syntax"]["target"] == "devtool"
    assert by_ref["test:roadmap-git-frx01"]["target"] == "devtool"
    assert by_ref["test:roadmap-git-frx02"]["target"] == "devtool"
    assert by_ref["test:roadmap-git-frx03"]["target"] == "devtool"
    assert by_ref["test:roadmap-git-frx04"]["target"] == "devtool"
    assert by_ref["test:roadmap-git-frx05"]["target"] == "devtool"
    assert by_ref["test:roadmap-git-frx06"]["target"] == "devtool"
    assert by_ref["test:roadmap-gate1-regressions"]["target"] == "devtool"
    assert by_ref["test:roadmap-frx-y01"]["target"] == "devtool"
    assert by_ref["test:roadmap-frx-y04"]["target"] == "devtool"
    assert by_ref["test:roadmap-frx-y05"]["target"] == "devtool"
    assert by_ref["test:roadmap-frx-y06"]["target"] == "devtool"
    assert by_ref["test:roadmap-frx-y07"]["target"] == "devtool"
    source_node = next(node for node in payload["schedule"]["nodes"] if node["job_key"] == "devtool.source-syntax")
    assert source_node["writes_known"] is True
    assert source_node["scope_reason"]
    test_nodes = [node for node in payload["schedule"]["nodes"] if node["runner"] == "pytest"]
    assert test_nodes
    assert all(node["scope_reason"] for node in test_nodes)
