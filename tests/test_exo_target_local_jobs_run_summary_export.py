from __future__ import annotations

import argparse
from datetime import datetime, timezone

from devtool.cli import _append_run_log_summary
from devtool.models import Issue, OperationResult
from devtool.summary import build_json_summary


def _result(name: str, metadata: dict[str, object], rc: int = 0) -> OperationResult:
    return OperationResult(
        name=name,
        argv=["echo", name],
        returncode=rc,
        duration_seconds=1.25,
        output="",
        issues=[] if rc == 0 else [Issue("error", name, "", "FAILED", "failed")],
        metadata=metadata,
    )


def test_json_summary_exports_target_local_job_identity() -> None:
    results = [
        _result(
            "job:contracts",
            {
                "workflow_node_key": "dustui_android:fast:contracts",
                "workflow_target": "dustui_android",
                "workflow_name": "fast",
                "workflow_runner": "command",
                "workflow_action": "",
                "workflow_depends_on": [],
                "workflow_job_scope": "target",
                "workflow_job_target": "dustui_android",
                "workflow_job_local_name": "contracts",
                "workflow_job_key": "dustui_android.contracts",
                "workflow_job_target_local": True,
            },
        ),
        _result(
            "job:repo_lint",
            {
                "workflow_node_key": "dustui_android:fast:repo_lint",
                "workflow_target": "dustui_android",
                "workflow_name": "fast",
                "workflow_runner": "command",
                "workflow_action": "",
                "workflow_depends_on": ["dustui_android:fast:contracts"],
                "workflow_job_scope": "repository",
                "workflow_job_key": "repo_lint",
            },
        ),
    ]

    payload = build_json_summary(results, repo_root="/repo")

    assert payload["workflow"]["schema_version"] == 2
    assert payload["workflow"]["summary"]["target_local_job_count"] == 1
    assert payload["workflow"]["summary"]["repository_job_count"] == 1
    assert payload["workflow"]["summary"]["targets_with_target_local_jobs"] == ["dustui_android"]
    assert payload["workflow"]["summary"]["target_local_jobs"] == [
        {
            "key": "dustui_android.contracts",
            "target": "dustui_android",
            "local_name": "contracts",
            "result_count": 1,
            "failed": 0,
            "duration_seconds": 1.25,
        }
    ]
    contract_node = payload["workflow"]["nodes"][0]
    assert contract_node["job"] == {
        "scope": "target",
        "target": "dustui_android",
        "local_name": "contracts",
        "key": "dustui_android.contracts",
        "target_local": True,
    }


def test_run_log_summary_includes_target_local_job_counts(tmp_path) -> None:
    log_path = tmp_path / "run.log"
    args = argparse.Namespace(command="validate", repo=str(tmp_path), target="dustui_android")
    results = [
        _result(
            "job:contracts",
            {
                "workflow_node_key": "dustui_android:fast:contracts",
                "workflow_target": "dustui_android",
                "workflow_name": "fast",
                "workflow_job_scope": "target",
                "workflow_job_target": "dustui_android",
                "workflow_job_local_name": "contracts",
                "workflow_job_key": "dustui_android.contracts",
                "workflow_job_target_local": True,
            },
        )
    ]

    _append_run_log_summary(
        log_path,
        args,
        None,
        {"warnings": 0, "errors": 0, "failed": 0, "skipped": 0},
        "Tests: completed successfully",
        started_at=datetime(2026, 9, 24, tzinfo=timezone.utc),
        finished_at=datetime(2026, 9, 24, 0, 0, 1, tzinfo=timezone.utc),
        exit_code=0,
        results=results,
    )

    text = log_path.read_text()
    assert "Devtool target-local jobs: 1" in text
    assert "Devtool repository jobs: 0" in text
    assert "Devtool target-local job details: dustui_android.contracts count=1" in text
