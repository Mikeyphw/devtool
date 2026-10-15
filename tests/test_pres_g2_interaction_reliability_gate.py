from __future__ import annotations

import ast
import hashlib
import os
from pathlib import Path
import subprocess
import sys

import pytest

from devtool.pres_g2_gate import (
    PresG2ProofItem,
    assert_pres_g2_gate_passes,
    build_pres_g2_gate,
    required_pres_g2_reports,
)
from devtool.textual_control_center import TerminalRestorationGuard, TextualControlCenter


def _tree_digest(root: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        h.update(path.relative_to(root).as_posix().encode())
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def test_required_gate_window_uses_current_source_proofs() -> None:
    root = Path(__file__).resolve().parents[1]
    report = build_pres_g2_gate(repo_root=root, present_reports=[])
    assert report.missing_proofs == ()
    assert report.missing_reports == ()
    assert len(report.source_proofs) == 10
    assert {proof.id for proof in report.source_proofs} >= {"PRES-G1", "PRES-X08", "PRES-G2"}
    assert required_pres_g2_reports() == ()


def test_gate_machine_report_passes_every_interaction_reliability_item() -> None:
    report = build_pres_g2_gate(repo_root=Path(__file__).resolve().parents[1])
    assert_pres_g2_gate_passes(report)
    machine = report.as_dict()
    assert machine["gate_id"] == "PRES-G2"
    assert machine["passed"] is True
    assert machine["owns_truth"] is False
    assert machine["gate_digest"]
    assert {item["id"] for item in machine["items"]} == {
        "panel-local-degradation",
        "stable-multi-run-selection",
        "logs-diagnostics-separation",
        "shared-action-registry",
        "disabled-reasons",
        "truthful-control-keys",
        "terminal-restoration",
        "domain-authority-pages",
        "collision-free-keymap",
        "no-guessed-shell-commands",
    }


def test_fresh_process_once_is_read_only_and_renderer_independent(tmp_path: Path) -> None:
    (tmp_path / "tracked.txt").write_text("stable\n")
    before = _tree_digest(tmp_path)
    repo_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo_root) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, "-m", "devtool.tui_textual_runtime", "--once", "--page", "workers", "--width", "44"],
        cwd=tmp_path,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    payload = ast.literal_eval(proc.stdout.strip())
    assert payload["page"] == "workers"
    assert payload["capabilities"]["mode"] == "ultra-narrow"
    assert payload["owns_truth"] is False
    assert _tree_digest(tmp_path) == before


def test_panel_failure_does_not_terminate_or_poison_other_panels() -> None:
    center = TextualControlCenter(page="workers")
    center.degrade_panel("workers", "offline")
    workers = center.snapshot()
    center.navigate("runs")
    runs = center.snapshot()
    assert workers["panel_health"]["workers"]["status"] == "degraded"
    assert runs["panel_health"]["runs"]["status"] == "healthy"
    assert runs["page"] == "runs"


def test_control_completion_race_does_not_rewrite_exit_as_cancel() -> None:
    center = TextualControlCenter(selected_run="run-1")
    request = center.handle_key("ctrl+c", confirmed=True)
    center.ingest({"kind": "node", "run_id": "run-1", "node_id": "final", "state": "passed"})
    exit_result = center.handle_key("q")
    assert request["request"]["status"] == "requested"
    assert exit_result["cancel_requested"] is False
    assert center.snapshot()["runs"][0]["state"] == "passed"


def test_terminal_restoration_guard_covers_keyboard_interrupt() -> None:
    guard = TerminalRestorationGuard()
    with pytest.raises(KeyboardInterrupt):
        with guard:
            raise KeyboardInterrupt()
    assert guard.restored is True
    assert guard.active is False


def test_gate_fails_closed_on_missing_source_proof() -> None:
    from dataclasses import replace

    report = build_pres_g2_gate()
    broken = replace(report, source_proofs=report.source_proofs[:-1])
    with pytest.raises(AssertionError, match="missing or failed current-source"):
        assert_pres_g2_gate_passes(broken)


def test_gate_fails_closed_on_failed_proof_item() -> None:
    report = build_pres_g2_gate(repo_root=Path(__file__).resolve().parents[1])
    items = list(report.items)
    items[0] = PresG2ProofItem(items[0].id, False, items[0].evidence, items[0].owner)
    broken = type(report)(report.schema_version, report.gate_id, tuple(items), report.required_reports, report.present_reports, report.owns_truth, report.source_proofs)
    with pytest.raises(AssertionError, match="proof gate failed"):
        assert_pres_g2_gate_passes(broken)
