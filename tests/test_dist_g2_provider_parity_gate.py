from pathlib import Path
import json
import subprocess
import sys

from devtool.dist_g2_gate import PROVIDERS, audit_window, run_parity_proof


def test_dist_g2_window_audit_closes_x03_export() -> None:
    result = audit_window(Path.cwd())
    assert result["roadmap_id"] == "DIST-G2"
    assert result["position"] == "48/70"
    assert len(result["providers"]) == 8
    assert result["x03_promise_count"] >= 12


def test_all_required_provider_classes_share_semantic_control_hierarchy_truth() -> None:
    result = run_parity_proof()
    assert result["ok"] is True
    assert result["provider_count"] == len(PROVIDERS) == 8
    truth = result["normalized_truth"]
    assert truth["acks"] == [1, 2, 3]
    assert truth["control"] == {"accepted": True, "detail": "accepted"}
    assert truth["hierarchy"] == {"child_count": 1, "terminal_count": 1, "flattened": False}
    assert truth["binding"] is True
    assert truth["route_hops"] == 1


def test_dist_g2_cli_runs_provider_proof_in_fresh_process() -> None:
    proc = subprocess.run([sys.executable, "-m", "devtool.dist_g2_gate"], text=True, capture_output=True, check=False)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["fresh_process"]["ok"] is True
    assert payload["audit"]["status"] == "window-audit-pass"
