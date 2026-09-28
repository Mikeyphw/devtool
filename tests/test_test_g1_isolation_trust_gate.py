from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from devtool.models import Issue
from devtool.test_model import (
    TestCaseResult as CaseResult,
    TestFailure as FailureDetail,
    TestResult as ParsedTestResult,
    TestStatus as ResultStatus,
    TestSuiteResult as SuiteResult,
)
from devtool.test_reporting import cluster_failure_issues, structured_junit_summary


ROOT = Path(__file__).resolve().parents[1]


def _run_pytest(*nodeids: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--tb=short", *nodeids],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )


def test_deliberate_process_state_leak_is_attributed_and_restored_before_next_test() -> None:
    tests_root = ROOT / "tests"
    with tempfile.TemporaryDirectory(prefix=".test-g1-leak-", dir=tests_root) as raw:
        probe = Path(raw) / "test_gate_leak_probe.py"
        probe.write_text(
            """
import os


def test_deliberate_process_state_leak():
    os.environ['DEVTOOL_G1_DELIBERATE_LEAK'] = 'leaked'


def test_followup_observes_restored_state():
    assert 'DEVTOOL_G1_DELIBERATE_LEAK' not in os.environ
""".lstrip(),
            encoding="utf-8",
        )
        result = _run_pytest(str(probe.relative_to(ROOT)))

    assert result.returncode == 1, result.stdout
    assert "test_deliberate_process_state_leak" in result.stdout
    assert "DEVTOOL_G1_DELIBERATE_LEAK" in result.stdout
    assert "leaked process state" in result.stdout
    assert "test_followup_observes_restored_state" not in result.stdout or "passed" in result.stdout
    # The follow-up test must have run successfully even though teardown of the
    # first test was deliberately converted into a gate-visible error.
    assert "2 passed" in result.stdout, result.stdout
    assert "1 error" in result.stdout, result.stdout


def test_validation_family_is_order_independent_across_fresh_processes() -> None:
    forward = _run_pytest(
        "tests/test_rem07_validation_truth_sessions.py",
        "tests/test_validation_reports_phase32.py",
    )
    reverse = _run_pytest(
        "tests/test_validation_reports_phase32.py",
        "tests/test_rem07_validation_truth_sessions.py",
    )
    assert forward.returncode == 0, forward.stdout
    assert reverse.returncode == 0, reverse.stdout
    assert "24 passed" in forward.stdout, forward.stdout
    assert "24 passed" in reverse.stdout, reverse.stdout


def test_cli_and_artifact_sequence_cannot_poison_later_validation_tests() -> None:
    result = _run_pytest(
        "tests/test_test_x01_failure_trustworthiness.py::test_canonical_devtool_cli_fixture_allows_repeated_clean_invocations",
        "tests/test_test_x01_failure_trustworthiness.py::test_snapshot_runner_restores_transaction_repository_roots",
        "tests/test_test_x01_failure_trustworthiness.py::test_repeated_in_process_control_calls_match_fresh_process_semantics",
        "tests/test_git_frx03_isolated_worktree_transactions.py::test_runner_applies_in_worktree_and_primary_changes_only_at_completion",
        "tests/test_rem07_validation_truth_sessions.py::ValidationTruthSessionTests::test_events_and_session_context_are_json_safe",
        "tests/test_rem07_validation_truth_sessions.py::ValidationTruthSessionTests::test_malformed_event_limit_is_bounded",
        "tests/test_rem07_validation_truth_sessions.py::ValidationTruthSessionTests::test_malformed_pending_control_request_does_not_crash",
        "tests/test_validation_reports_phase32.py",
    )
    assert result.returncode == 0, result.stdout
    assert "passed" in result.stdout


def test_generated_surface_drift_has_one_root_cluster_without_hiding_raw_outcomes() -> None:
    cases = (
        CaseResult("golden", "golden", ResultStatus.FAILED, failure=FailureDetail("CLI help golden differs")),
        CaseResult("completion", "completion", ResultStatus.FAILED, failure=FailureDetail("completion drift")),
        CaseResult("control", "control", ResultStatus.FAILED, failure=FailureDetail("typed control regression")),
    )
    parsed = ParsedTestResult("pytest", suites=(SuiteResult("suite", "suite", cases=cases),))
    issues = [
        Issue("error", "test", "tests/test_arch_final_seal.py", "PYTEST_FAILURE", "CLI help golden differs from generated parser output"),
        Issue("error", "test", "tests/test_wrapper_hardening_hotfix.py", "PYTEST_FAILURE", "completion generated surface drift"),
        Issue("error", "test", "tests/test_validation_control_api.py", "PYTEST_FAILURE", "stop then pause typed control regression"),
    ]

    clusters = cluster_failure_issues(issues)
    generated = [item for item in clusters if item.classification == "generated_surface_drift"]
    independent = [item for item in clusters if item.classification == "independent"]
    assert len(generated) == 1
    assert generated[0].member_indices == (0, 1)
    assert generated[0].root_diagnostic_key == "generated-surface:cli-contract"
    assert len(independent) == 1
    assert independent[0].member_indices == (2,)

    summary = structured_junit_summary(parsed, issues=issues)
    assert summary["failure_count_total"] == 3
    assert [row["id"] for row in summary["failures"]] == ["golden", "completion", "control"]
    assert summary["generated_surface_root_diagnostics"] == ["generated-surface:cli-contract"]
    # The independently tracked control regression remains visible even when a
    # generated-surface root cluster exists beside it.
    assert any(item["classification"] == "independent" for item in summary["failure_clusters"])


def test_stop_then_pause_regression_is_a_standalone_typed_control_check() -> None:
    result = _run_pytest(
        "tests/test_test_x01_failure_trustworthiness.py::test_repeated_in_process_control_calls_match_fresh_process_semantics"
    )
    assert result.returncode == 0, result.stdout
    assert "1 passed" in result.stdout, result.stdout
