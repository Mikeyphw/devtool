from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from devtool.core.events import EventBus
from devtool.gradle_planning import GradleExecutionReceipt, GradleInvocationRequest, GradleLauncherIdentity, plan_gradle_invocation
from devtool.models import CommandSpec, ProjectConfig
from devtool.runners import run_specs


def _plan(tmp_path: Path):
    config = ProjectConfig(
        repo_root=tmp_path,
        project_root=str(tmp_path),
        kind="gradle",
        runner="gradle",
        execution_environment="desktop-linux",
        runner_options={"gradle": {"configuration_cache": "on", "max_workers": 2}},
    )
    launcher = GradleLauncherIdentity.create(executable="gradle", source="system", effective_version="9.7")
    request = GradleInvocationRequest(
        caller="receipt-test",
        action="build",
        project_root=str(tmp_path),
        requested_roots=("assemble", "test"),
        selection_identity="selection-123",
        expected_artifact_producers=("assemble",),
        expected_report_producers=("test",),
    )
    return config, plan_gradle_invocation(config=config, request=request, launcher=launcher, cpu_count=4, memory_mb=4096)


def test_receipt_captures_requested_optimized_observed_and_existing_run_refs(tmp_path: Path) -> None:
    _config, plan = _plan(tmp_path)
    receipt = GradleExecutionReceipt.from_plan_metadata(
        plan.as_dict(),
        progress={
            "observed_tasks": [":app:compile", ":app:test"],
            "task_outcomes": {":app:compile": "SUCCESS", ":app:test": "FROM-CACHE"},
            "configuration_cache_state": "reused",
            "daemon_state": "started",
        },
        success=True,
        process_executed=True,
        artifact_refs=("artifact:apk",),
        producer_artifact_refs={":app:assemble": ("artifact:apk",)},
        report_refs=("report:junit",),
        diagnostic_ids=("diag:1",),
        duration_seconds=12.5,
        exo_run_ref="process:7",
        rer_ref="rer:8",
        run_ref="run:9",
    )
    payload = receipt.as_dict()

    assert payload["requested_roots"] == ["assemble", "test"]
    assert payload["optimized_roots"] == ["assemble", "test"]
    assert payload["observed_tasks"] == [":app:compile", ":app:test"]
    assert payload["task_outcomes"][":app:test"] == "FROM-CACHE"
    assert payload["selection_identity"] == "selection-123"
    assert payload["jdk_identity"] == plan.context.jdk_identity
    assert payload["resource_profile_provenance"] == plan.policy.resource_profile_provenance
    assert payload["resource_profile_reason"] == plan.policy.resource_profile_reason
    assert payload["resource_profile_evidence"] == dict(plan.policy.resource_profile_evidence)
    assert payload["expected_artifact_producers"] == ["assemble"]
    assert payload["expected_report_producers"] == ["test"]
    assert payload["exo_run_ref"] == "process:7"
    assert payload["rer_ref"] == "rer:8"
    assert payload["run_ref"] == "run:9"
    assert payload["configuration_cache"]["effective"] == "on"
    assert payload["configuration_cache"]["observed_state"] == "reused"
    assert payload["configuration_cache_qualification"]["qualification_id"]
    assert payload["daemon"]["observed_state"] == "started"
    assert payload["vfs_qualification"]["qualification_id"]
    assert payload["failure_policy"]["effective"] == "fail-fast"
    assert payload["producer_artifact_refs"] == {":app:assemble": ["artifact:apk"]}
    assert payload["arguments"]["raw"] == []
    assert payload["arguments"]["managed"] == []
    assert payload["arguments"]["protected"] == []
    assert payload["arguments"]["passthrough"] == []
    assert payload["arguments"]["semantic_properties"] == []
    assert payload["arguments"]["argument_digest"]


def test_gradle_progress_observes_configuration_cache_hit_and_store_state() -> None:
    from rich.console import Console
    from devtool.gradle_progress import GradleProgressTracker

    hit = GradleProgressTracker(["gradle", "check"], "/repo", "check", Console(force_terminal=False))
    hit._update_from_plain_gradle_line("Reusing configuration cache.")
    assert hit.snapshot()["configuration_cache_state"] == "reused"

    miss = GradleProgressTracker(["gradle", "check"], "/repo", "check", Console(force_terminal=False))
    miss._update_from_plain_gradle_line(
        "Calculating task graph as no cached configuration is available for tasks: check"
    )
    miss._update_from_plain_gradle_line("Configuration cache entry stored.")
    assert miss.snapshot()["configuration_cache_state"] == "stored"


def test_run_specs_projects_gradle_receipt_into_existing_task_finished_event(tmp_path: Path, monkeypatch) -> None:
    config, plan = _plan(tmp_path)
    bus = EventBus()
    monkeypatch.setattr("devtool.runners.publish_event", bus.publish)
    monkeypatch.setattr("devtool.progress_events.publish_event", bus.publish)
    spec = CommandSpec(
        "gradle-receipt",
        ["gradle", "assemble"],
        cwd=str(tmp_path),
        metadata={
            "progress_parser": "gradle",
            "gradle_tasks": ["assemble"],
            "gradle_execution_plan": plan.as_dict(),
            "gradle_plan_digest": plan.plan_digest,
        },
    )
    with patch("devtool.runners._run_process_live", return_value=(0, "> Task :app:compile UP-TO-DATE\nBUILD SUCCESSFUL")):
        results, _ = run_specs(config, [spec], verbose=False, event_run_id="env-x03-run")

    payload = results[0].metadata["gradle_execution_receipt"]
    assert payload["success"] is True
    assert payload["process_executed"] is True
    assert payload["run_ref"] == "env-x03-run"
    assert results[0].metadata["gradle_receipt_id"] == payload["receipt_id"]
    snapshot = bus.execution_store.snapshot("env-x03-run")
    # The receipt is projected through the existing EXO TaskFinished reducer,
    # not written into an ENV-owned history database.
    assert any(
        node.metadata.get("gradle_receipt_id") == payload["receipt_id"]
        for node in snapshot.nodes
    )


def test_structured_suite_metrics_keep_suite_local_and_invocation_totals_distinct() -> None:
    import json
    from rich.console import Console
    from devtool.gradle_progress import GradleProgressTracker

    tracker = GradleProgressTracker(["gradle", "test"], "/repo", "test", Console(force_terminal=False))
    payload = {
        "sequence": 1,
        "kind": "test-suite-finish",
        "suite": "module-a",
        "passed": 2,
        "failed": 1,
        "skipped": 0,
        "total": 3,
        "invocationPassed": 7,
        "invocationFailed": 1,
        "invocationSkipped": 2,
        "invocationTotal": 10,
    }
    tracker.update_from_line("DEVTOOL_EVENT " + json.dumps(payload))
    snapshot = tracker.snapshot()
    assert snapshot["suite_tests_passed"] == 2
    assert snapshot["suite_tests_total"] == 3
    assert snapshot["tests_passed"] == 7
    assert snapshot["tests_total"] == 10


def test_standard_telemetry_uses_cc_compatible_build_events_service_and_full_detail_is_bounded() -> None:
    standard = Path("devtool/resources/gradle/devtool-events.init.gradle").read_text(encoding="utf-8")
    assert "BuildEventsListenerRegistry" in standard
    assert "OperationCompletionListener" in standard
    assert "BuildService" in standard
    assert "onTaskCompletion" in standard
    assert "TaskExecutionListener" not in standard
    assert "TestListener" not in standard
    assert "task.outputs" not in standard

    source = Path("devtool/resources/gradle/devtool-events-full.init.gradle").read_text(encoding="utf-8")
    assert "DEVTOOL_GRADLE_TEST_EVENT_LIMIT" in source
    assert "DEVTOOL_GRADLE_ARTIFACT_PRODUCERS" in source
    assert "devtoolArtifactProducers.contains(task.path)" in source
    assert "Math.min(10000L" in source
    assert "visitLimit = 2000" in source
    assert "found.size() < 200" in source
    assert "eachFileRecurse" not in source
    assert "invocationPassed" in source
    assert "invocationTotal" in source
