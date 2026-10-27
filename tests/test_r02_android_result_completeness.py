"""R02: required Android testcase and release phases must fail closed."""
from __future__ import annotations

import hashlib
from pathlib import Path
from unittest import mock

import pytest

from devtool.android.instrumentation import _execution_classification
from devtool.android.release import AndroidReleaseOrchestrator
from devtool.models import CommandSpec, OperationResult, ProjectConfig


def _config(root: Path) -> ProjectConfig:
    return ProjectConfig(
        repo_root=root, project_root=str(root), kind="android", runner="android",
        selected_target="app", variant="release", build_target=":app",
        runner_options={"android": {
            "release": {
                "variant": "release", "device_tests": "required",
                "install_smoke": False, "upgrade_smoke": False,
                "cold_start_smoke": False, "restart_smoke": False,
                "verify_signature": False, "verify_certificate_identity": False,
            },
            "signing": {"release": {"mode": "project", "policy": "optional"}},
        }},
    )


@pytest.mark.parametrize("empty_phase", ["unit-tests", "lint", "instrumentation"])
def test_release_does_not_pass_required_phase_without_results(tmp_path, monkeypatch, empty_phase):
    apk = tmp_path / "app-release.apk"
    apk.write_bytes(b"signed-fixture")
    digest = hashlib.sha256(apk.read_bytes()).hexdigest()
    monkeypatch.setattr(
        "devtool.project_runners.AndroidGradleRunner.build_specs",
        lambda self, config, action: [CommandSpec(action, ["fake-gradle", action])],
    )
    fake_context = mock.Mock()
    fake_context.as_dict.return_value = {"tools": {}}
    monkeypatch.setattr("devtool.android.release.resolve_android_execution_context", lambda *a, **kw: fake_context)

    def executor(config, specs):
        phase = specs[0].name
        if phase == ("test" if empty_phase == "unit-tests" else empty_phase):
            return [], ""
        item = OperationResult(phase, specs[0].argv, 0, 0.01, "ok", [], metadata={})
        if phase == "package":
            item.metadata["artifacts"] = {"items": [{
                "valid": True, "type": "apk", "variant": "release",
                "path": str(apk), "sha256": digest,
            }]}
        return [item], ""

    result = AndroidReleaseOrchestrator(_config(tmp_path), executor=executor).run(continue_on_error=True)
    gates = {gate["name"]: gate for gate in result.evidence["gates"]}
    assert not result.passed
    assert gates[empty_phase]["status"] == "failed"
    assert "0 operation results" in gates[empty_phase]["detail"]
    assert result.evidence["verdict"] != "passed"


@pytest.mark.parametrize("stream", [
    "INSTRUMENTATION_STATUS: numtests=1\nINSTRUMENTATION_STATUS_CODE: 1\n",
    "INSTRUMENTATION_STATUS: numtests=1\nINSTRUMENTATION_STATUS_CODE: 0\n",
    "INSTRUMENTATION_STATUS: numtests=1\nINSTRUMENTATION_STATUS_CODE: 1\nINSTRUMENTATION_STATUS_CODE: 0\nINSTRUMENTATION_STATUS_CODE: 0\n",
    "INSTRUMENTATION_STATUS: numtests=2\nINSTRUMENTATION_STATUS_CODE: 1\nINSTRUMENTATION_STATUS_CODE: 0\n",
    "INSTRUMENTATION_STATUS: numtests=1\nINSTRUMENTATION_STATUS: numtests=2\nINSTRUMENTATION_STATUS_CODE: 1\nINSTRUMENTATION_STATUS_CODE: 0\n",
])
def test_instrumentation_incomplete_or_orphaned_stream_fails(stream):
    counts, ids, kind, reason = _execution_classification(stream, 0)
    assert kind == "incomplete-results"
    assert reason
    assert counts["failed"] == 0  # infrastructure failure must not fabricate failed test cases


def test_instrumentation_valid_completed_stream_preserves_positive_verdict():
    counts, _ids, kind, reason = _execution_classification(
        "INSTRUMENTATION_STATUS: numtests=2\n"
        "INSTRUMENTATION_STATUS_CODE: 1\nINSTRUMENTATION_STATUS_CODE: 0\n"
        "INSTRUMENTATION_STATUS_CODE: 1\nINSTRUMENTATION_STATUS_CODE: -3\n"
        "INSTRUMENTATION_CODE: -1\n", 0,
    )
    assert kind == "" and reason == ""
    assert counts["executed"] == 2 and counts["skipped"] == 1


def test_explicit_test_failure_remains_test_failure():
    counts, _ids, kind, _reason = _execution_classification(
        "INSTRUMENTATION_STATUS: numtests=1\nINSTRUMENTATION_STATUS_CODE: 1\nINSTRUMENTATION_STATUS_CODE: -2\n", 0,
    )
    assert kind == "test-failure" and counts["failed"] == 1

@pytest.mark.parametrize("result_names, skipped, expected", [
    (("test-a", "test-b"), False, "passed"),
    (("test-a", "test-a"), False, "failed"),
    (("test-b", "test-a"), False, "failed"),
    (("test-a", "unrelated"), False, "failed"),
    (("test-a", "test-b"), True, "failed"),
])
def test_multiple_android_results_bind_exact_command_order(tmp_path, monkeypatch, result_names, skipped, expected):
    from devtool.project_runners import AndroidGradleRunner
    from devtool.android.release import AndroidReleaseOrchestrator

    def specs(self, config, action):
        if action == "test":
            return [CommandSpec("test-a", ["fake", "a"]), CommandSpec("test-b", ["fake", "b"])]
        return [CommandSpec(action, ["fake", action])]
    monkeypatch.setattr(AndroidGradleRunner, "build_specs", specs)
    fake_context = mock.Mock()
    fake_context.as_dict.return_value = {"tools": {}}
    monkeypatch.setattr("devtool.android.release.resolve_android_execution_context", lambda *a, **kw: fake_context)

    def executor(config, planned):
        if planned[0].name == "test-a":
            return [OperationResult(name, ["fake"], 0, 0.01, "ok", [], skipped=skipped)
                    for name in result_names], ""
        return [OperationResult(planned[0].name, planned[0].argv, 0, 0.01, "ok", [])], ""

    result = AndroidReleaseOrchestrator(_config(tmp_path), executor=executor).run(continue_on_error=True)
    phase = next(item for item in result.evidence["gates"] if item["name"] == "unit-tests")
    assert phase["status"] == expected
    if expected == "failed":
        assert result.evidence["verdict"] != "passed"
