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

REQUIRED_REPORTS: tuple[str, ...] = (
    "PRES_G1_TEXTUAL_REPLACEMENT_PROOF_GATE_REPORT.md",
    "PRES_X04_TEXTUAL_TUI_SUBSTRATE_REPORT.md",
    "PRES_X05_TEXTUAL_FEATURE_PARITY_REPORT.md",
    "PRES_X06_TEXTUAL_DEFAULT_REPLACEMENT_REPORT.md",
    "PRES_X07_TEXTUAL_REPLACEMENT_HARDENING_REPORT.md",
    "PRES_X08_LIVE_TEXTUAL_CONTROL_CENTER_REPORT.md",
    "PRES_G2_INTERACTION_RELIABILITY_REMEDIATION_REPORT.md",
)


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

    @property
    def missing_reports(self) -> tuple[str, ...]:
        present = set(self.present_reports)
        return tuple(name for name in self.required_reports if name not in present)

    @property
    def passed(self) -> bool:
        return not self.missing_reports and all(item.passed for item in self.items)

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "gate_id": self.gate_id,
            "passed": self.passed,
            "owns_truth": self.owns_truth,
            "missing_reports": list(self.missing_reports),
            "items": [item.as_dict() for item in self.items],
        }
        payload["gate_digest"] = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return payload


def required_pres_g2_reports() -> tuple[str, ...]:
    return REQUIRED_REPORTS


def _present_reports(repo_root: Path) -> tuple[str, ...]:
    return tuple(sorted(path.name for path in repo_root.glob("PRES*_REPORT.md")))


def build_pres_g2_gate(*, repo_root: Path | None = None, present_reports: Iterable[str] | None = None) -> PresG2GateReport:
    root = repo_root or Path.cwd()
    reports = tuple(present_reports) if present_reports is not None else _present_reports(root)

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
    return PresG2GateReport(1, "PRES-G2", items, REQUIRED_REPORTS, reports, owns_truth=False)


def assert_pres_g2_gate_passes(report: PresG2GateReport) -> None:
    if report.missing_reports:
        raise AssertionError(f"PRES-G2 missing report evidence: {', '.join(report.missing_reports)}")
    failed = [item.id for item in report.items if not item.passed]
    if failed:
        raise AssertionError(f"PRES-G2 proof gate failed: {', '.join(failed)}")
    if report.owns_truth:
        raise AssertionError("PRES-G2 presentation gate attempted to own execution truth")
