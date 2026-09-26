from __future__ import annotations

"""GIT-FRX-14 federation cancellation and recovery evidence.

Federation owns scheduling and parent resume/retry intent. Child repository
rollback/recovery remains child-owned; this module only projects stable
references already produced by the child Git/EXO authorities.
"""

from typing import Any

from .core.execution_contract import ExecutionStatus


def cancellation_recovery_evidence(report: Any) -> dict[str, Any]:
    children = tuple(getattr(report, "children", ()) or ())
    reusable: list[str] = []
    invalidated: list[dict[str, Any]] = []
    child_recovery: list[dict[str, Any]] = []
    cancelled_run_ids: list[str] = []

    for child in children:
        status = str(getattr(child, "status", "") or "")
        exit_code = int(getattr(child, "exit_code", 0) or 0)
        step_id = str(getattr(child, "step_id", "") or "")
        run_id = str(getattr(child, "run_id", "") or "")
        git_lineage = dict(getattr(child, "git_lineage", {}) or {})
        operational = dict(getattr(child, "operational_ownership", {}) or {})
        if status == ExecutionStatus.SUCCEEDED.value and exit_code == 0:
            reusable.append(step_id)
        else:
            reason = "cancelled-before-scheduling" if exit_code == 130 and not run_id else status or "not-succeeded"
            invalidated.append({"step_id": step_id, "status": status, "exit_code": exit_code, "reason": reason})
        if status == ExecutionStatus.CANCELLED.value or exit_code == 130:
            if run_id:
                cancelled_run_ids.append(run_id)
        if run_id or git_lineage or operational:
            child_recovery.append({
                "step_id": step_id,
                "run_id": run_id,
                "repository": str(getattr(child, "repository", "") or ""),
                "result_ref": str(git_lineage.get("result_ref") or ""),
                "rollback_ref": str(git_lineage.get("rollback_ref") or ""),
                "operational_ownership": operational,
                "rollback_owner": "child-repository",
                "automatic_git_rollback": False,
            })

    return {
        "kind": "federation-cancellation-recovery",
        "terminal_status": str(getattr(report, "status", "") or ""),
        "stopped_new_scheduling": str(getattr(report, "status", "") or "") == ExecutionStatus.CANCELLED.value,
        "reusable_steps": reusable,
        "invalidated_steps": invalidated,
        "cancelled_child_run_ids": cancelled_run_ids,
        "child_recovery": child_recovery,
        "rollback_ownership": "child-repository-owned",
        "automatic_git_rollback": False,
        "recovery_commands": {
            "resume": f"devtool federation resume {getattr(report, 'parent_run_id', '')}",
            "retry": f"devtool federation retry {getattr(report, 'parent_run_id', '')}",
        },
    }


__all__ = ["cancellation_recovery_evidence"]
