from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

from devtool import cli, logstore
from devtool.models import CommandSpec, Issue, ProjectConfig
from devtool.process_state import ProcessStateGuard
from devtool.project_runners import PythonRunner
from devtool.runners import apply_snapshot, run_specs
from devtool.validation_control import EXIT_CONTROL_UNAVAILABLE
from devtool.validation_sessions import create_session, mark_phase_running
from devtool.test_model import (
    TestCaseResult as CaseResult,
    TestFailure as FailureDetail,
    TestResult as ParsedTestResult,
    TestStatus as ResultStatus,
    TestSuiteResult as SuiteResult,
)
from devtool.test_reporting import (
    cluster_failure_issues,
    concise_repr,
    concise_text_diff,
    render_structured_test_summary,
    structured_junit_summary,
)


def _python_project(root: Path) -> ProjectConfig:
    (root / "pyproject.toml").write_text(
        "[project]\nname='demo'\nversion='0.1'\ndependencies=[]\n",
        encoding="utf-8",
    )
    return ProjectConfig(
        repo_root=root,
        project_root=str(root),
        kind="python",
        runner_options={"python": {"manager": "venv", "test_framework": "pytest"}},
    )


def test_process_state_guard_reports_and_restores_environment_cwd_and_log_override(tmp_path: Path) -> None:
    original = Path.cwd()
    baseline_value = os.environ.get("DEVTOOL_TEST_X01_SENTINEL")
    logstore.set_log_dir_override(None)
    guard = ProcessStateGuard.capture()
    other = tmp_path / "other"
    other.mkdir()
    os.environ["DEVTOOL_TEST_X01_SENTINEL"] = "leaked"
    os.chdir(other)
    logstore.set_log_dir_override(tmp_path / "logs")

    differences = guard.verify_and_restore()
    rendered = "\n".join(item.render() for item in differences)
    assert "DEVTOOL_TEST_X01_SENTINEL" in rendered
    assert "cwd:cwd" in rendered
    assert "runtime-override:log_dir" in rendered
    assert Path.cwd() == original
    assert logstore.get_log_dir_override() is None
    if baseline_value is None:
        assert "DEVTOOL_TEST_X01_SENTINEL" not in os.environ
    else:
        assert os.environ["DEVTOOL_TEST_X01_SENTINEL"] == baseline_value


def test_cli_main_restores_invocation_repository_roots_even_when_inner_call_changes_them(monkeypatch) -> None:
    keys = (
        "DEVTOOL_TRANSACTION_REPO_ROOT",
        "DEVTOOL_TRANSACTION_PRIMARY_REPO_ROOT",
        "DEVTOOL_VALIDATION_STATE_REPO_ROOT",
    )
    before = {key: os.environ.get(key) for key in keys}

    def fake_after_parse(*_args, **_kwargs):
        for index, key in enumerate(keys):
            os.environ[key] = f"/leaked/{index}"
        return 0

    monkeypatch.setattr(cli, "_main_after_parse", fake_after_parse)
    assert cli.main(["status"]) == 0
    assert {key: os.environ.get(key) for key in keys} == before


def test_canonical_devtool_cli_fixture_allows_repeated_clean_invocations(devtool_cli, monkeypatch) -> None:
    calls: list[int] = []

    def fake_after_parse(*_args, **_kwargs):
        calls.append(1)
        os.environ["DEVTOOL_TRANSACTION_REPO_ROOT"] = f"/transaction/{len(calls)}"
        print(f"invocation {len(calls)}")
        return 0

    monkeypatch.setattr(cli, "_main_after_parse", fake_after_parse)
    first = devtool_cli(["status"])
    second = devtool_cli(["status"])
    assert first.returncode == second.returncode == 0
    assert "invocation 1" in first.stdout
    assert "invocation 2" in second.stdout
    assert "DEVTOOL_TRANSACTION_REPO_ROOT" not in os.environ




def test_nested_pytest_masks_outer_artifact_transaction_roots(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.update({
        "DEVTOOL_TRANSACTION_REPO_ROOT": str(tmp_path / "outer-transaction"),
        "DEVTOOL_TRANSACTION_PRIMARY_REPO_ROOT": str(tmp_path / "outer-primary"),
        "DEVTOOL_VALIDATION_STATE_REPO_ROOT": str(tmp_path / "outer-primary"),
        "DEVTOOL_TRANSACTION_COMMIT_MODE": "single",
        "DEVTOOL_TRANSACTION_ISOLATION": "worktree",
        "DEVTOOL_PRESERVE_DIRTY_REQUESTED": "1",
    })
    result = subprocess.run(
        [
            sys.executable, "-m", "pytest", "-q", "--tb=short",
            "tests/test_rem07_validation_truth_sessions.py::ValidationTruthSessionTests::test_events_and_session_context_are_json_safe",
            "tests/test_rem07_validation_truth_sessions.py::ValidationTruthSessionTests::test_malformed_event_limit_is_bounded",
        ],
        cwd=repo_root,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout
    assert "2 passed" in result.stdout


def test_snapshot_runner_restores_transaction_repository_roots(monkeypatch, tmp_path: Path) -> None:
    from devtool import runners
    from devtool.models import OperationResult

    artifact = tmp_path / "snapshot.zip"
    artifact.write_bytes(b"placeholder")

    def fake_apply_archive(*_args, **_kwargs):
        os.environ["DEVTOOL_TRANSACTION_REPO_ROOT"] = str(tmp_path / "tx")
        os.environ["DEVTOOL_TRANSACTION_PRIMARY_REPO_ROOT"] = str(tmp_path / "primary")
        os.environ["DEVTOOL_VALIDATION_STATE_REPO_ROOT"] = str(tmp_path / "primary")
        return OperationResult("apply-snapshot", ["snapshot"], 0, 0.0, "ok", [])

    monkeypatch.setattr(runners, "resolve_artifact_path", lambda _artifact: artifact)
    monkeypatch.setattr(runners, "_resolve_executable_artifact_plan", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(runners, "_apply_archive", fake_apply_archive)
    assert apply_snapshot(tmp_path, str(artifact)).returncode == 0
    assert "DEVTOOL_TRANSACTION_REPO_ROOT" not in os.environ
    assert "DEVTOOL_TRANSACTION_PRIMARY_REPO_ROOT" not in os.environ
    assert "DEVTOOL_VALIDATION_STATE_REPO_ROOT" not in os.environ

def test_final_log_summary_does_not_create_process_global_run_identity(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("DEVTOOL_RUN_ID", raising=False)
    args = argparse.Namespace(command="validate", artifact="", _devtool_run_id="run-x01")
    now = datetime.now(timezone.utc)

    cli._append_run_log_summary(
        tmp_path / "run.log",
        args,
        None,
        {"warnings": 0, "errors": 0, "failed": 0, "skipped": 0},
        "Diagnostics: 0 warnings, 0 errors",
        started_at=now,
        finished_at=now,
        exit_code=0,
    )

    assert "DEVTOOL_RUN_ID" not in os.environ
    assert "Devtool run id: run-x01" in (tmp_path / "run.log").read_text(encoding="utf-8")


def test_python_runner_uses_concise_default_traceback_and_reason_output(tmp_path: Path) -> None:
    spec = PythonRunner()._pytest_spec(_python_project(tmp_path))
    assert "--tb=short" in spec.argv
    assert "-ra" in spec.argv
    junit_args = [arg for arg in spec.argv if arg.startswith("--junitxml=")]
    assert len(junit_args) == 1


def test_concise_repr_and_diff_bound_terminal_output_but_keep_full_evidence() -> None:
    value = {f"key-{index}": "x" * 200 for index in range(40)}
    rendered = concise_repr(value, max_chars=240)
    assert len(rendered) <= 240

    expected = "\n".join(f"old-{index}" for index in range(200))
    actual = "\n".join(f"new-{index}" for index in range(200))
    bounded, full = concise_text_diff(expected, actual, max_lines=20)
    assert len(bounded.splitlines()) == 21
    assert "diff truncated after 20 line(s)" in bounded
    assert len(full.splitlines()) > 20


def test_failure_clusters_group_generated_surface_drift_without_hiding_independent_failure() -> None:
    issues = [
        Issue("error", "test", "tests/test_arch_final_seal.py", "PYTEST_FAILURE", "CLI help golden differs from generated parser output"),
        Issue("error", "test", "tests/test_wrapper_hardening_hotfix.py", "PYTEST_FAILURE", "completion generated surface drift"),
        Issue("error", "test", "tests/test_validation_control_api.py", "PYTEST_FAILURE", "stop then pause returned generic exit 1 instead of control_unavailable"),
    ]
    clusters = cluster_failure_issues(issues)
    generated = [cluster for cluster in clusters if cluster.classification == "generated_surface_drift"]
    independent = [cluster for cluster in clusters if cluster.classification == "independent"]
    assert len(generated) == 1
    assert generated[0].member_indices == (0, 1)
    assert generated[0].root_diagnostic_key == "generated-surface:cli-contract"
    assert len(independent) == 1
    assert independent[0].member_indices == (2,)


def test_structured_junit_summary_preserves_raw_failure_and_generated_root_linkage() -> None:
    result = ParsedTestResult(
        "pytest",
        suites=(SuiteResult(
            "suite",
            "suite",
            cases=(
                CaseResult("ok", "ok", ResultStatus.PASSED),
                CaseResult("bad", "bad", ResultStatus.FAILED, failure=FailureDetail("cli help golden drift")),
            ),
        ),),
    )
    issues = [Issue("error", "test", "tests/test_arch_final_seal.py", "PYTEST_FAILURE", "generated CLI help golden drift")]
    summary = structured_junit_summary(result, issues=issues)
    assert summary["counts"]["total"] == 2
    assert summary["counts"]["failed"] == 1
    assert summary["failures"][0]["id"] == "bad"
    assert summary["generated_surface_root_diagnostics"] == ["generated-surface:cli-contract"]
    line = render_structured_test_summary(summary)
    assert line.startswith("DEVTOOL_TEST_SUMMARY ")
    assert json.loads(line.split(" ", 1)[1])["counts"]["failed"] == 1



def test_failure_clusters_distinguish_cascade_stale_and_independent_contracts() -> None:
    issues = [
        Issue("error", "test", "tests/test_a.py", "PYTEST_FAILURE", "downstream casualty after shared root failure"),
        Issue("error", "test", "tests/test_b.py", "PYTEST_FAILURE", "stale expectation in brittle test contract"),
        Issue("error", "test", "tests/test_c.py", "PYTEST_FAILURE", "actual product assertion mismatch"),
    ]
    classifications = [cluster.classification for cluster in cluster_failure_issues(issues)]
    assert classifications == ["cascade", "stale_brittle", "independent"]


def test_repeated_in_process_control_calls_match_fresh_process_semantics(tmp_path: Path, devtool_cli) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='demo'\nversion='0.1'\n", encoding="utf-8"
    )
    (tmp_path / ".devtool.toml").write_text(
        '[project]\nkind="python"\nproject="."\n', encoding="utf-8"
    )
    config = ProjectConfig(repo_root=tmp_path, project_root=str(tmp_path), kind="python")
    session = create_session(config, [
        CommandSpec("preflight", ["true"], metadata={"validation_phase_name": "preflight", "validation_depends_on": []}),
    ])
    mark_phase_running(session, session.phases[0].key)

    stop = devtool_cli([
        "--repo", str(tmp_path), "--no-clipboard", "validate", "stop",
        "--if-revision", str(session.revision), "--json",
    ])
    stop_payload = json.loads(stop.stdout)
    assert stop.returncode == 0

    pause = devtool_cli([
        "--repo", str(tmp_path), "--no-clipboard", "validate", "pause",
        "--if-revision", str(stop_payload["revision"]), "--json",
    ])
    pause_payload = json.loads(pause.stdout)
    assert pause.returncode == EXIT_CONTROL_UNAVAILABLE
    assert pause_payload["reason"] == "control_unavailable"
    assert "DEVTOOL_RUN_ID" not in os.environ

def test_runner_appends_machine_readable_junit_tail_after_pytest_output(tmp_path: Path) -> None:
    test_file = tmp_path / "test_sample.py"
    test_file.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    report = tmp_path / ".devtool" / "reports" / "pytest.xml"
    report.parent.mkdir(parents=True)
    config = ProjectConfig(repo_root=tmp_path, project_root=str(tmp_path), kind="python")
    spec = CommandSpec(
        "test",
        [sys.executable, "-m", "pytest", "-q", f"--junitxml={report}", str(test_file)],
        cwd=str(tmp_path),
        metadata={
            "runner": "python",
            "project_type": "python",
            "project_root": str(tmp_path),
            "report_parser": "pytest-junit",
            "report_parsers": ["pytest-junit"],
            "test_report": str(report),
            "test_framework": "pytest",
        },
    )
    results, _raw = run_specs(config, [spec], verbose=False)
    assert len(results) == 1
    assert results[0].returncode == 0
    assert "DEVTOOL_TEST_SUMMARY " in results[0].output
    payload = results[0].metadata["structured_test_summary"]
    assert payload["source"] == "junit"
    assert payload["counts"]["passed"] == 1
    assert payload["counts"]["total"] == 1
