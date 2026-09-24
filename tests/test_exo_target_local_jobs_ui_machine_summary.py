from __future__ import annotations

from datetime import datetime, timezone

from devtool.models import OperationResult
from devtool.summary import _action_label, build_runner_json


def _result(name: str, metadata: dict[str, object]) -> OperationResult:
    return OperationResult(
        name,
        ["echo", name],
        0,
        0.25,
        "",
        [],
        metadata=metadata,
    )


def test_action_labels_show_target_local_job_identity() -> None:
    label = _action_label(
        "workflow-step",
        {
            "workflow_job_scope": "target",
            "workflow_job_target": "dustui_android",
            "workflow_job_local_name": "contracts",
            "workflow_job_key": "dustui_android.contracts",
            "workflow_job_target_local": True,
        },
    )

    assert label == "Workflow Step · target-local job:dustui_android.contracts"


def test_runner_json_exports_workflow_context_for_dashboards_and_huds() -> None:
    results = [
        _result(
            "contracts",
            {
                "workflow_node_key": "dustui_android:fast:contracts",
                "workflow_target": "dustui_android",
                "workflow_name": "fast",
                "workflow_runner": "command",
                "workflow_action": "contracts",
                "workflow_job_scope": "target",
                "workflow_job_target": "dustui_android",
                "workflow_job_local_name": "contracts",
                "workflow_job_key": "dustui_android.contracts",
                "workflow_job_target_local": True,
            },
        ),
        _result(
            "shared-lint",
            {
                "workflow_node_key": "dustui_android:fast:shared-lint",
                "workflow_target": "dustui_android",
                "workflow_name": "fast",
                "workflow_runner": "command",
                "workflow_action": "shared-lint",
                "workflow_job_scope": "repository",
                "workflow_job_key": "shared_lint",
            },
        ),
    ]

    payload = build_runner_json(
        results,
        repo_root="/repo",
        command="devtool workflow dustui_android:fast",
        raw_output="raw",
        summary_text="summary",
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        finished_at=datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc),
    )

    assert payload["schema_version"] == 1
    assert payload["workflow"]["schema_version"] == 2
    assert payload["workflow"]["summary"]["target_local_job_count"] == 1
    assert payload["execution_context"]["target_local_job_count"] == 1
    assert payload["execution_context"]["repository_job_count"] == 1
    assert payload["execution_context"]["targets_with_target_local_jobs"] == ["dustui_android"]
    assert payload["execution_context"]["target_local_jobs"][0]["key"] == "dustui_android.contracts"
    assert payload["execution_context"]["repository_jobs"] == ["shared_lint"]
