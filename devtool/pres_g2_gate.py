"""PRES-G2 interaction/reliability milestone proof.

The gate consumes PRES presentation contracts and records proof only. It does not
own execution, run storage, artifact transactions, SSH workers, or test truth.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterable

from .textual_control_center import (
    ACTION_SPECS,
    NAV_KEYS,
    PAGE_ACTIONS,
    PANEL_AUTHORITIES,
    PAGES,
    TerminalRestorationGuard,
    TextualControlCenter,
)

from .source_bound_proofs import (PRES_G2_IDS, SourceProof, pres_source_proofs, proof_digest)


@dataclass(frozen=True)
class PresG2ProofItem:
    id: str
    passed: bool
    evidence: str
    owner: str = "PRES"

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "status": "passed" if self.passed else "failed", "evidence": self.evidence, "owner": self.owner}


@dataclass(frozen=True)
class PresG2GateReport:
    schema_version: int
    gate_id: str
    items: tuple[PresG2ProofItem, ...]
    required_reports: tuple[str, ...]
    present_reports: tuple[str, ...]
    owns_truth: bool = False
    source_proofs: tuple[SourceProof, ...] = ()

    @property
    def missing_reports(self) -> tuple[str, ...]:
        """Retained compatibility field; historical Markdown never gates closure."""
        return ()

    @property
    def missing_proofs(self) -> tuple[str, ...]:
        ids = {proof.id for proof in self.source_proofs if proof.status == "passed"}
        return tuple(proof_id for proof_id in PRES_G2_IDS if proof_id not in ids)

    @property
    def passed(self) -> bool:
        return not self.missing_proofs and all(item.passed for item in self.items)

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "gate_id": self.gate_id,
            "passed": self.passed,
            "owns_truth": self.owns_truth,
            "missing_reports": [],
            "source_proof_schema_version": 1,
            "source_proofs": [proof.as_dict() for proof in self.source_proofs],
            "missing_proofs": list(self.missing_proofs),
            "source_proof_digest": proof_digest(self.source_proofs),
            "items": [item.as_dict() for item in self.items],
        }
        payload["gate_digest"] = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return payload


def required_pres_g2_reports() -> tuple[str, ...]:
    return ()  # Deprecated API retained for callers; no Markdown dependency.


def build_pres_g2_gate(*, repo_root: Path | None = None, present_reports: Iterable[str] | None = None) -> PresG2GateReport:
    root = repo_root or Path(__file__).resolve().parents[1]
    _ = present_reports  # Legacy argument cannot create or bypass executable proof.
    source_proofs = pres_source_proofs(root, stage="PRES-G2")

    center = TextualControlCenter(page="runs")
    center.ingest({"kind": "node", "run_id": "run-b", "node_id": "b", "state": "running"})
    center.ingest({"kind": "node", "run_id": "run-a", "node_id": "a", "state": "passed"})
    center.select(run_id="run-a", node_id="a")
    selected_before = center.snapshot()["selected_run"]
    center.ingest({"kind": "node", "run_id": "run-b", "node_id": "b2", "state": "failed"})
    selected_after = center.snapshot()["selected_run"]

    center.degrade_panel("workers", "ssh-source-unavailable")
    health = center.snapshot()["panel_health"]
    panel_local = health["workers"]["status"] == "degraded" and health["runs"]["status"] == "healthy"

    center.ingest({"kind": "log", "run_id": "run-a", "text": "safe log"})
    center.ingest({"kind": "diagnostic", "run_id": "run-a", "state": "failed", "code": "E-PRES-G2"})
    semantic_split = bool(center.logs) and any(event.get("kind") == "diagnostic" for event in center.events) and all(event.get("kind") != "log" for event in center.events)

    action_rows = []
    for page in PAGES:
        action_rows.extend(TextualControlCenter(page=page, selected_run="run-a").visible_actions())
    action_registry_ok = all(row["id"] in ACTION_SPECS and row["authority"] == ACTION_SPECS[row["id"]].authority for row in action_rows)
    disabled_reason_ok = TextualControlCenter(page="runs").visible_actions()[0]["disabled_reason"] == "select-a-run"

    control = TextualControlCenter(selected_run="run-a")
    cancel = control.handle_key("ctrl+c", confirmed=True)
    exits = [control.handle_key("q"), control.handle_key("escape")]
    control_ok = cancel.get("kind") == "action-request" and all(item.get("kind") == "exit" and item.get("cancel_requested") is False for item in exits)

    normal_guard = TerminalRestorationGuard()
    with normal_guard:
        pass
    exceptional_guard = TerminalRestorationGuard()
    try:
        with exceptional_guard:
            raise KeyboardInterrupt()
    except KeyboardInterrupt:
        pass
    restoration_ok = normal_guard.restored and exceptional_guard.restored

    collisions_ok = len(NAV_KEYS) == len(set(NAV_KEYS)) and len(NAV_KEYS.values()) == len(set(NAV_KEYS.values()))
    authorities_ok = set(PANEL_AUTHORITIES) == set(PAGES) and PANEL_AUTHORITIES["workers"] == "DIST/SSH" and PANEL_AUTHORITIES["overlays"] == "LIFE"
    no_shell_commands = "shell_command" not in json.dumps(center.snapshot(), sort_keys=True).lower() and "command_string" not in json.dumps(center.snapshot(), sort_keys=True).lower()
    page_actions_ok = set(PAGE_ACTIONS) == set(PAGES) and all(action in ACTION_SPECS for actions in PAGE_ACTIONS.values() for action in actions)

    items = (
        PresG2ProofItem("panel-local-degradation", panel_local, "workers degradation leaves runs panel healthy"),
        PresG2ProofItem("stable-multi-run-selection", selected_before == selected_after == "run-a", "selected run survives attention/list reorder"),
        PresG2ProofItem("logs-diagnostics-separation", semantic_split, "log records and diagnostic events remain distinct"),
        PresG2ProofItem("shared-action-registry", action_registry_ok and page_actions_ok, "every visible action is registry-backed with canonical owner"),
        PresG2ProofItem("disabled-reasons", disabled_reason_ok, "unavailable run action reports select-a-run"),
        PresG2ProofItem("truthful-control-keys", control_ok, "Ctrl+C requests cancel; q/Esc exit without implicit cancel"),
        PresG2ProofItem("terminal-restoration", restoration_ok, "renderer lifetime restores on normal and interrupt paths"),
        PresG2ProofItem("domain-authority-pages", authorities_ok, "Artifacts/Overlays/Workers/History/Run pages map to domain authorities"),
        PresG2ProofItem("collision-free-keymap", collisions_ok, "navigation bindings are unique"),
        PresG2ProofItem("no-guessed-shell-commands", no_shell_commands, "operator snapshot/actions contain typed IDs rather than shell strings"),
    )
    return PresG2GateReport(1, "PRES-G2", items, (), (), owns_truth=False, source_proofs=source_proofs)


def assert_pres_g2_gate_passes(report: PresG2GateReport) -> None:
    if report.missing_proofs or any(p.status != "passed" for p in report.source_proofs):
        raise AssertionError(f"PRES-G2 missing or failed current-source executable proof: {', '.join(report.missing_proofs)}")
    failed = [item.id for item in report.items if not item.passed]
    if failed:
        raise AssertionError(f"PRES-G2 proof gate failed: {', '.join(failed)}")
    if report.owns_truth:
        raise AssertionError("PRES-G2 presentation gate attempted to own execution truth")
