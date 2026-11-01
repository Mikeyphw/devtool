from __future__ import annotations

"""R39-R2 read-only comparison of legacy and native serial execution contracts.

Both lanes execute only fixed Python commands inside disposable, separate roots.
This is a bounded migration *evidence* harness, not a lane switch or proof that
parallel, remote, Android or interrupted-host execution is equivalent.
"""

import argparse
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any
from unittest.mock import patch

from .models import CommandSpec, ProjectConfig, WorkspaceConfig, WorkflowSchedulerConfig
from .runners import run_specs
from .workflow_scheduler import run_workflow_specs


class WorkflowParityError(RuntimeError):
    """A proven mismatch, unsafe invocation or incomplete comparison."""


CASES = (
    "pass_chain",
    "dependency_block",
    "independent_fail_fast",
    "continue_on_error",
    "pre_cancelled",
)
TERMINAL_EVENTS = {"NodeDiscovered", "TaskStarted", "TaskFinished", "NodeFinished"}


class _CancelledProgress:
    current_phase: dict[str, object] | None = None

    def cancel_requested(self) -> bool:
        return True

    def plan_phases(self, _phases: object, *, append: bool = False) -> None:
        return None

    def phase_started(self, *_args: object, **_kwargs: object) -> None:
        return None

    def phase_finished(self, _result: object) -> None:
        return None

    def progress(self, **_kwargs: object) -> None:
        return None

    def clear_cancel(self) -> None:
        return None


def _git_identity(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for label, ref in (("commit", "HEAD"), ("tree", "HEAD^{tree}")):
        try:
            result[label] = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "--verify", ref],
                capture_output=True, text=True, check=True, timeout=5,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            result[label] = ""
    return result


def _specs(root: Path, case: str) -> list[CommandSpec]:
    if case not in CASES:
        raise WorkflowParityError("R39_PARITY_UNKNOWN_CASE")
    fail = case in {"dependency_block", "independent_fail_fast", "continue_on_error"}
    dependent = case in {"pass_chain", "dependency_block"}
    specs: list[CommandSpec] = []
    for name in ("a", "b"):
        argv = [
            sys.executable, "-c",
            "from pathlib import Path; "
            + f'Path("spawned-{name}.txt").write_text("{name}\\n"); '
            + f'print("output-{name}"); raise SystemExit({7 if name == "a" and fail else 0})',
        ]
        specs.append(CommandSpec(name, argv, cwd=str(root), metadata={
            "workflow_node_key": name,
            "workflow_target": "parity",
            "workflow_name": "r39-r2",
            "workflow_depends_on": ["a"] if name == "b" and dependent else [],
            "workflow_graph": True,
            "workflow_read_only": False,
            "workflow_write_scope_known": True,
            "workflow_write_scopes": [f"spawned-{name}.txt"],
        }))
    return specs


def _event_trace(events: list[object]) -> list[dict[str, object]]:
    trace: list[dict[str, object]] = []
    for event in events:
        kind = type(event).__name__
        if kind not in TERMINAL_EVENTS:
            continue
        trace.append({
            "type": kind,
            "node": str(getattr(event, "node_id", "") or ""),
            "status": str(getattr(event, "status", "") or ""),
            "exit_code": getattr(event, "exit_code", None),
        })
    return trace


def _outcomes(results: list[Any]) -> list[dict[str, object]]:
    return [{
        "name": str(result.name),
        "returncode": int(result.returncode),
        "skipped": bool(result.skipped),
        "failure_kind": str(result.failure_kind or ""),
        "node_state": str(result.metadata.get("workflow_node_state") or ""),
        "output": str(result.output or ""),
    } for result in results]


def _execute(root: Path, *, lane: str, case: str) -> dict[str, object]:
    if lane not in {"legacy", "native"}:
        raise WorkflowParityError("R39_PARITY_UNKNOWN_LANE")
    repo = root / lane
    repo.mkdir(parents=True, exist_ok=False)
    state_home = root / f"{lane}-state"
    config = ProjectConfig(
        repo_root=repo, kind="command", runner="command", project_root=str(repo),
        workspace=WorkspaceConfig(scheduler=WorkflowSchedulerConfig(
            parallel=True, max_parallel_nodes=1,
        )),
    )
    specs = _specs(repo, case)
    events: list[object] = []
    with patch.dict(os.environ, {
        "XDG_STATE_HOME": str(state_home),
        "DEVTOOL_R38_WRITE_AUDIT": "off",
        "DEVTOOL_R38_SCRATCH_ISOLATION": "off",
        "DEVTOOL_GIT_TIMING": "off",
    }), patch("devtool.workflow_retry.capture_start", return_value=None), patch(
        "devtool.runners.publish_event", side_effect=events.append,
    ), redirect_stdout(io.StringIO()):
        if lane == "legacy":
            results, _raw = run_specs(
                config, specs, verbose=False, dry_run=False,
                continue_on_error=case == "continue_on_error", log_path=None,
                workflow_initial_cancelled=case == "pre_cancelled",
            )
        else:
            results, _raw, plan = run_workflow_specs(
                config, specs, verbose=False, dry_run=False,
                continue_on_error=case == "continue_on_error", log_path=None,
                tasker_progress=_CancelledProgress() if case == "pre_cancelled" else None,
                parallel_requested=True, max_parallel_nodes=1,
                write_audit_mode="off", scratch_isolation_mode="off",
            )
            if plan is None or plan.max_parallel_nodes != 1:
                raise WorkflowParityError("R39_PARITY_NATIVE_NOT_SERIAL")
    launches = {
        name: (repo / f"spawned-{name}.txt").read_text(encoding="utf-8")
        for name in ("a", "b") if (repo / f"spawned-{name}.txt").is_file()
    }
    return {
        "outcomes": _outcomes(results),
        "events": _event_trace(events),
        "executed_side_effects": launches,
    }


def compare_evidence(legacy: dict[str, object], native: dict[str, object]) -> list[str]:
    """Compare observed effects and ordered terminal events; no loose sorting."""
    if not isinstance(legacy, dict) or not isinstance(native, dict):
        raise WorkflowParityError("R39_PARITY_INVALID_EVIDENCE")
    return [name for name in ("outcomes", "events", "executed_side_effects")
            if legacy.get(name) != native.get(name)]


def verify_matrix(result: dict[str, object]) -> None:
    """Fail closed when cases/lanes are missing, or any evidence differs."""
    if not isinstance(result, dict) or result.get("schema_version") != 1:
        raise WorkflowParityError("R39_PARITY_INVALID_SCHEMA")
    cases = result.get("cases")
    if not isinstance(cases, list) or [row.get("name") for row in cases if isinstance(row, dict)] != list(CASES):
        raise WorkflowParityError("R39_PARITY_INCOMPLETE_MATRIX")
    expected = {
        "pass_chain": (("passed", "passed"), ("a", "b")),
        "dependency_block": (("failed", "blocked"), ("a",)),
        "independent_fail_fast": (("failed", "skipped"), ("a",)),
        "continue_on_error": (("failed", "passed"), ("a", "b")),
        "pre_cancelled": (("cancelled", "cancelled"), ()),
    }
    for row in cases:
        if not isinstance(row, dict) or not isinstance(row.get("legacy"), dict) or not isinstance(row.get("native"), dict):
            raise WorkflowParityError("R39_PARITY_MISSING_LANE")
        mismatches = compare_evidence(row["legacy"], row["native"])
        if mismatches or row.get("mismatches") != [] or row.get("equivalent") is not True:
            raise WorkflowParityError("R39_PARITY_MISMATCH:" + str(row.get("name")))
        if len(row["legacy"].get("outcomes", [])) != 2:
            raise WorkflowParityError("R39_PARITY_INCOMPLETE_OUTCOMES")
        events = row["legacy"].get("events", [])
        if len(events) != 6 or [e.get("type") for e in events] != [
            "NodeDiscovered", "NodeDiscovered", "TaskStarted", "TaskFinished",
            "TaskStarted", "TaskFinished",
        ]:
            raise WorkflowParityError("R39_PARITY_INCOMPLETE_EVENTS")
        states, launches = expected[row["name"]]
        outcomes = row["legacy"]["outcomes"]
        if [(item["name"], item["node_state"]) for item in outcomes] != list(zip(("a", "b"), states)):
            raise WorkflowParityError("R39_PARITY_UNEXPECTED_OUTCOME")
        if set(row["legacy"]["executed_side_effects"]) != set(launches):
            raise WorkflowParityError("R39_PARITY_UNEXPECTED_PROCESS_SPAWNS")
        if [e["node"] for e in events] != ["a", "b", "a", "a", "b", "b"]:
            raise WorkflowParityError("R39_PARITY_INVALID_NODE_ORDER")
        expected_terminal = tuple("succeeded" if state == "passed" else state for state in states)
        if (events[3]["status"], events[5]["status"]) != expected_terminal:
            raise WorkflowParityError("R39_PARITY_INVALID_TERMINAL_EVENTS")
    if result.get("qualification") != {
        "bounded_serial_subprocess_parity": True,
        "parallel_worker_parity": False,
        "remote_or_android_parity": False,
        "device_acceptance": False,
        "historical_reuse_parity": False,
        "release": False,
        "legacy_retirement_authorized": False,
    }:
        raise WorkflowParityError("R39_PARITY_UNSAFE_QUALIFICATION")


def matrix(*, source_root: Path | None = None) -> dict[str, object]:
    source = (source_root or Path(__file__).resolve().parents[1]).resolve()
    rows: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="devtool-r39-r2-parity-") as temp:
        for case in CASES:
            root = Path(temp) / case
            root.mkdir()
            legacy = _execute(root, lane="legacy", case=case)
            native = _execute(root, lane="native", case=case)
            diff = compare_evidence(legacy, native)
            rows.append({"name": case, "equivalent": not diff, "mismatches": diff,
                         "legacy": legacy, "native": native})
    receipt: dict[str, object] = {
        "schema_version": 1,
        "matrix": "r39-r2-legacy-native-bounded-serial-subprocess",
        "source": _git_identity(source),
        "cases": rows,
        "qualification": {
            "bounded_serial_subprocess_parity": all(not row["mismatches"] for row in rows),
            "parallel_worker_parity": False,
            "remote_or_android_parity": False,
            "device_acceptance": False,
            "historical_reuse_parity": False,
            "release": False,
            "legacy_retirement_authorized": False,
        },
    }
    verify_matrix(receipt)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="R39-R2 real-subprocess serial execution parity (non-release)")
    parser.add_argument("--json", action="store_true", help="print machine-verifiable evidence")
    args = parser.parse_args(argv)
    try:
        receipt = matrix()
    except (WorkflowParityError, OSError, subprocess.SubprocessError) as exc:
        print(f"R39_PARITY_REFUSED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(receipt, indent=2, sort_keys=True))
    else:
        print("R39_R2_BOUNDED_SERIAL_PARITY_PASS: " + ", ".join(CASES))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
