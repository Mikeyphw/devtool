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
    "roadmap-git-frx01",
    "roadmap-workflow-exo-baseline",
}
EXPECTED_WORKFLOWS = {"roadmap-smoke", "roadmap-frx-y01", "roadmap-current"}


def _config():
    return load_project_config(str(ROOT), None, target_name="devtool")


def test_roadmap00_declares_one_truthful_target_local_non_test_job() -> None:
    cfg = _config()
    target = cfg.targets["devtool"]

    assert set(target.jobs) == {"source-syntax"}
    job = target.jobs["source-syntax"]
    assert job.name == "devtool.source-syntax"
    assert job.runner == "command"
    assert job.command == ("python", "-m", "compileall", "-q", "devtool", "tests")
    assert job.inputs == ("devtool/**", "tests/**")
    assert job.outputs == (".devtool/cache/roadmap-pyc/**",)
    assert job.env["PYTHONPYCACHEPREFIX"] == ".devtool/cache/roadmap-pyc"
    assert job.metadata["target_local"] is True
    assert job.metadata["target"] == "devtool"
    assert job.metadata["local_name"] == "source-syntax"


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
        "test:roadmap-workflow-exo-baseline",
    }

    current = plan_selected_workflow(cfg, "roadmap-current")
    assert {node.ref.raw for node in current.nodes} == {
        "job:source-syntax",
        *(f"test:{test_id}" for test_id in EXPECTED_TESTS),
    }
    assert len(current.nodes) == 5


def test_roadmap00_machine_surfaces_export_stable_identity_and_schedule_reasons() -> None:
    cfg = _config()

    jobs_out = io.StringIO()
    with redirect_stdout(jobs_out):
        _jobs(cfg, as_json=True, selected_target="devtool")
    jobs = json.loads(jobs_out.getvalue())
    assert jobs["summary"]["target_local_jobs"] == 1
    assert jobs["completion"]["target_job_keys"] == ["devtool.source-syntax"]

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
    assert payload["summary"]["node_count"] == 5
    assert payload["summary"]["target_local_job_count"] == 1
    assert payload["summary"]["target_local_jobs"][0]["key"] == "devtool.source-syntax"
    assert payload["schedule"]["nodes"]
    by_ref = {node["ref"]: node for node in payload["graph"]["nodes"]}
    assert by_ref["job:source-syntax"]["target"] == "devtool"
    assert by_ref["test:roadmap-git-frx01"]["target"] == "devtool"
    source_node = next(node for node in payload["schedule"]["nodes"] if node["job_key"] == "devtool.source-syntax")
    assert source_node["writes_known"] is True
    assert source_node["scope_reason"]
    test_nodes = [node for node in payload["schedule"]["nodes"] if node["runner"] == "pytest"]
    assert test_nodes
    assert all(node["scope_reason"] for node in test_nodes)
