from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from devtool.dist_g3_gate import (
    audit_dist_promises,
    audit_historical_substrate,
    audit_source_dispositions,
    run_end_to_end_proof,
    run_fresh_process_proof,
)


def test_dist_g3_source_promise_and_regression_ledger_is_complete() -> None:
    result = audit_source_dispositions(Path.cwd())
    assert result == {
        "schema_version": 1,
        "kind": "dist-g3-source-disposition-audit",
        "promise_count": 84,
        "regression_count": 157,
        "unmapped": 0,
        "ok": True,
    }


def test_dist_g3_historical_ssh_frx_substrate_is_retained_not_reimplemented() -> None:
    result = audit_historical_substrate(Path.cwd())
    assert result["ok"] is True
    assert set(result["authorities"]) == {"SSH-01", "SSH-02", "SSH-03", "FRX", "GIT-FRX"}


def test_dist_g3_dist_promise_ledger_covers_p001_through_p104_and_preserves_p100_history() -> None:
    result = audit_dist_promises(Path.cwd())
    assert result["promise_count"] == 104
    assert result["p100"] == "REMEDIATED_AFTER_HISTORICAL_DEVIATION"
    assert result["ok"] is True


def test_dist_g3_end_to_end_event_identity_failure_truth_hierarchy_and_projection() -> None:
    result = run_end_to_end_proof()
    assert result["ok"] is True
    assert result["origin_event_identity"] == result["ingested_event_identity"]
    assert result["receipt"] == {"status": "failed", "exit_code": 7, "reconciled": True}
    assert result["hierarchy"]["child_count"] == 1
    assert result["hierarchy"]["flattened_child_identity"] is False
    child = result["child_run_id"]
    assert result["surface_child_ids"] == {"machine": child, "tui": child, "desktop": child}
    assert result["historical_live_replay"] is False
    assert result["doctor_child"] == f"run://{child}"


def test_dist_g3_final_trace_runs_from_fresh_python_process() -> None:
    result = run_fresh_process_proof()
    assert result["ok"] is True
    assert result["origin_event_identity"] == result["ingested_event_identity"]


def test_dist_g3_module_cli_runs_complete_final_seal_in_fresh_process() -> None:
    proc = subprocess.run([sys.executable, "-m", "devtool.dist_g3_gate"], text=True, capture_output=True, check=False)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["roadmap_id"] == "DIST-G3"
    assert payload["position"] == "66/76"
    assert payload["provider_parity"]["provider_count"] == 8
    assert payload["fresh_process"]["ok"] is True


def test_dist_g3_wrapper_is_five_stage_authoritative_gate() -> None:
    import tomllib
    cfg = tomllib.loads((Path.cwd() / ".devtool.toml").read_text(encoding="utf-8"))
    command = cfg["wrapper"]["commands"]["dist-g3"]
    assert command == {
        "description": "Run the DIST-G3 final distributed-execution seal",
        "workflow": "roadmap-dist-g3",
        "target": "devtool",
    }
    workflow = cfg["targets"]["devtool"]["workflows"]["roadmap-dist-g3"]
    assert [node["id"] for node in workflow] == [
        "syntax", "source-closure", "retained-dist", "retained-substrate", "final-proof"
    ]


def test_dist_g3_generated_core_surfaces_are_current_after_wrapper_addition() -> None:
    from devtool.cli import build_parser
    from devtool.core_surfaces import build_generated_surfaces, check_generated_surfaces
    result = check_generated_surfaces(build_generated_surfaces(build_parser()), Path.cwd() / "share/core-generated")
    assert result["status"] == "pass"


def test_dist_g3_records_successful_pres_g2r_prerequisite() -> None:
    payload = json.loads((Path.cwd() / "PRES_G2R_PROMISE_LEDGER.json").read_text(encoding="utf-8"))
    assert payload["status"] == "qualified_applied"
    assert payload["native_qualification"] == {
        "run_id": "c96acfc4493f40fc818f7086e6a68611",
        "commit": "3ffbf98",
        "tests": {"passed": 302, "failed": 0, "skipped": 0},
        "diagnostics": {"warnings": 0, "errors": 0},
    }
