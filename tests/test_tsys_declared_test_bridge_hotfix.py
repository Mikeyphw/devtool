from __future__ import annotations

import sys
from pathlib import Path

from devtool.models import CommandSpec, ProjectConfig, TargetConfig, WorkflowConfig, WorkflowStepRef
from devtool.runners import run_specs
from devtool.test_declarations import command_specs
from devtool.test_model import TestSpec as CanonicalSpec
from devtool.test_runstore import build_run_payload
from devtool.workflow_runtime import build_workflow_specs


def _config(tmp_path: Path, spec: CanonicalSpec) -> ProjectConfig:
    return ProjectConfig(
        repo_root=tmp_path,
        kind="python",
        project_root=str(tmp_path),
        tests={spec.id: spec},
    )


def test_declared_testspec_policy_fields_survive_command_bridge(tmp_path: Path) -> None:
    declared = CanonicalSpec(
        "unit",
        "Unit",
        "pytest",
        (sys.executable, "-c", "print('1 passed in 0.01s')"),
        required=False,
        timeout_seconds=7.5,
    )
    spec = command_specs(_config(tmp_path, declared), ["unit"])[0]

    assert spec.name == "test:unit"
    assert spec.metadata["test_spec_id"] == "unit"
    assert spec.metadata["required"] is False
    assert spec.metadata["timeout_seconds"] == 7.5


def test_declared_testspec_policy_fields_survive_workflow_bridge(tmp_path: Path) -> None:
    declared = CanonicalSpec(
        "unit",
        "Unit",
        "pytest",
        (sys.executable, "-c", "print('1 passed in 0.01s')"),
        required=False,
        timeout_seconds=9,
    )
    target = TargetConfig(name="app", runner="python")
    target.workflows["validate"] = WorkflowConfig(
        "validate",
        (WorkflowStepRef("test:unit", "test", "unit"),),
    )
    config = ProjectConfig(
        repo_root=tmp_path,
        kind="python",
        selected_target="app",
        targets={"app": target},
        tests={"unit": declared},
        project_root=str(tmp_path),
    )

    spec = build_workflow_specs(config, "validate")[0]
    assert spec.metadata["test_spec_id"] == "unit"
    assert spec.metadata["required"] is False
    assert spec.metadata["timeout_seconds"] == 9


def test_declared_timeout_is_enforced_and_exit_fallback_uses_declared_id(tmp_path: Path) -> None:
    declared = CanonicalSpec(
        "slow",
        "Slow",
        "pytest",
        (sys.executable, "-c", "import time; time.sleep(5)"),
        required=False,
        timeout_seconds=0.15,
    )
    config = _config(tmp_path, declared)
    spec = command_specs(config, ["slow"])[0]

    results, _ = run_specs(config, [spec], verbose=False, shutdown_dotnet=False)
    result = results[0]

    assert result.name == "test:slow"
    assert result.returncode == 124
    assert result.failure_kind == "command_timeout"
    assert result.metadata["required"] is False
    assert result.metadata["timeout_seconds"] == 0.15
    assert result.metadata["timed_out"] is True
    assert result.metadata["test_result"]["id"] == "slow"


def test_stdout_result_identity_uses_declared_testspec_id(tmp_path: Path) -> None:
    declared = CanonicalSpec(
        "unit",
        "Unit",
        "pytest",
        (sys.executable, "-c", "print('1 passed in 0.01s')"),
    )
    config = _config(tmp_path, declared)
    spec = command_specs(config, ["unit"])[0]

    results, _ = run_specs(config, [spec], verbose=False, shutdown_dotnet=False)
    result = results[0]

    assert result.name == "test:unit"
    assert result.metadata["test_result"]["id"] == "unit"
    payload = build_run_payload(tmp_path, results, run_id="identity")
    assert payload is not None
    assert payload["operations"][0]["operation_id"] == "unit"
    assert payload["operations"][0]["test_result"]["id"] == "unit"


def test_structured_report_result_identity_uses_declared_testspec_id(tmp_path: Path) -> None:
    report = tmp_path / "pytest.xml"
    script = tmp_path / "emit_report.py"
    script.write_text(
        "from pathlib import Path\n"
        f"p=Path({str(report)!r})\n"
        "p.write_text('<testsuite name=\"suite\" tests=\"1\" failures=\"0\" skipped=\"0\">'"
        "+'<testcase classname=\"demo\" name=\"test_ok\" />'"
        "+'</testsuite>', encoding='utf-8')\n",
        encoding="utf-8",
    )
    config = ProjectConfig(repo_root=tmp_path, kind="python", project_root=str(tmp_path))
    spec = CommandSpec(
        "test:unit",
        [sys.executable, str(script)],
        cwd=str(tmp_path),
        metadata={
            "canonical_test": True,
            "test_spec_id": "unit",
            "test_runner": "pytest",
            "runner": "pytest",
            "project_root": str(tmp_path),
            "report_parser": "pytest-junit",
            "test_report": str(report),
        },
    )

    results, _ = run_specs(config, [spec], verbose=False, shutdown_dotnet=False)
    assert results[0].metadata["test_result"]["id"] == "unit"
    assert results[0].metadata["test_result"]["metadata"]["evidence"] == "report"
