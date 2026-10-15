"""Versioned, executable PRES/EXEC qualification receipts.

Historical Markdown is audit documentation, never an input to a live gate.
The probes exercise loaded production APIs and bind each receipt to the exact
imported implementation files. A missing/substituted implementation fails
closed; a source hash is provenance, not a hard-coded approval allowlist.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from importlib import import_module
import json
from pathlib import Path
from typing import Callable

SCHEMA_VERSION = 1
PRES_G1_IDS = tuple(f"PRES-X{i:02d}" for i in range(1, 8))
PRES_G2_IDS = PRES_G1_IDS + ("PRES-G1", "PRES-X08", "PRES-G2")


@dataclass(frozen=True)
class SourceProof:
    id: str
    status: str
    source_paths: tuple[str, ...]
    source_digest: str
    contract: str
    schema_version: int = SCHEMA_VERSION

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "status": self.status,
            "source_paths": list(self.source_paths),
            "source_digest": self.source_digest,
            "contract": self.contract,
        }


def _bind_source(repo_root: Path, modules: tuple[str, ...]) -> tuple[tuple[str, ...], str]:
    root = repo_root.resolve(strict=True)
    if not (root / "devtool" / "__init__.py").is_file():
        raise RuntimeError("source-proof: missing repository package root")
    parts: list[bytes] = []
    paths: list[str] = []
    for module_name in modules:
        module = import_module(module_name)
        path = Path(str(getattr(module, "__file__", ""))).resolve(strict=True)
        rel = Path(*module_name.split(".")).with_suffix(".py").as_posix()
        expected = (root / rel).resolve(strict=True)
        if path != expected or not expected.is_file():
            raise RuntimeError(f"source-proof: substituted or missing implementation {rel}")
        paths.append(rel)
        parts.extend((rel.encode("utf-8"), b"\0", path.read_bytes(), b"\0"))
    return tuple(paths), sha256(b"".join(parts)).hexdigest()


def _require(condition: bool, proof: str) -> None:
    if not condition:
        raise RuntimeError(f"source-proof: {proof} behavior failed")


def _pres_core_checks() -> dict[str, tuple[tuple[str, ...], str, Callable[[], None]]]:
    from . import presentation_surfaces as s
    from . import tui_textual as t

    records = (
        {"id": "node-1", "ref": "exo:proof:1", "authority": "EXO", "status": "failed", "label": "proof failure"},
    )

    def x01() -> None:
        projection = s.status_from_exo(records)
        view = s.render_projection_view(projection)
        s.assert_view_is_reference_only(view)
        _require(projection.status == "failed" and not projection.generated_truth and bool(view.refs), "PRES-X01")

    def x02() -> None:
        compact = s.resolve_layout_intent("tui", width=44)
        wide = s.resolve_layout_intent("tui", width=150, profile="detailed")
        _require(compact.panes == 1 and wide.panes > 1 and compact.density == "compact", "PRES-X02")

    def x03() -> None:
        bundle = s.build_presentation_bundle(s.status_from_exo(records))
        s.assert_bundle_is_reference_only(bundle)
        _require(bundle.parity["consistent"] is True and len(bundle.renders) == 6, "PRES-X03")

    def x04() -> None:
        spec = t.build_textual_spec_from_projection(records, width=44, height=16)
        t.assert_textual_spec_is_reference_only(spec)
        _require(spec.backend == "textual" and not spec.owns_truth and len(spec.screens) >= 3, "PRES-X04")

    def x05() -> None:
        plan = t.build_textual_feature_parity_from_projection(
            (
                {"id": "syntax", "ref": "exo:pres:syntax", "status": "passed", "label": "Source syntax", "progress": 1.0},
                {"id": "textual", "ref": "exo:pres:textual", "status": "running", "label": "Textual replacement", "progress": 0.62},
                {"id": "gate", "ref": "exo:pres:g1", "status": "passed", "label": "PRES-G1 proof", "progress": 1.0},
            ),
            width=44, height=16,
        )
        t.assert_textual_feature_parity_is_reference_only(plan)
        _require(not plan.as_dict()["missing_features"] and not plan.owns_truth, "PRES-X05")

    def x06() -> None:
        contract = t.default_tui_backend_contract()
        yes = t.resolve_tui_backend_selection(requested_backend="auto", availability=t.TextualAvailability(True))
        no = t.resolve_tui_backend_selection(requested_backend="auto", availability=t.TextualAvailability(False, reason="missing"))
        _require(contract["default_backend"] == "textual" and yes.effective_backend == "textual" and no.effective_backend == "unavailable", "PRES-X06")

    def x07() -> None:
        hardening = t.textual_replacement_hardening_contract()
        unavailable = t.resolve_tui_backend_selection(requested_backend="textual", availability=t.TextualAvailability(False, reason="missing"))
        notice = t.build_tui_backend_notice(unavailable)
        t.assert_tui_backend_notice_is_safe(notice)
        _require(hardening["legacy_backend_status"] == "removed" and notice.severity == "error" and not hardening["textual_owns_truth"], "PRES-X07")

    source = ("devtool.presentation_surfaces", "devtool.tui_textual")
    return {
        "PRES-X01": (("devtool.presentation_surfaces",), "Canonical status remains reference-only", x01),
        "PRES-X02": (("devtool.presentation_surfaces",), "Responsive layout capabilities remain canonical", x02),
        "PRES-X03": (("devtool.presentation_surfaces",), "Six consumers share a reference-only parity key", x03),
        "PRES-X04": (source, "Textual spec remains a renderer", x04),
        "PRES-X05": (source, "Textual feature parity and authority are maintained", x05),
        "PRES-X06": (("devtool.tui_textual",), "Textual is the sole available default", x06),
        "PRES-X07": (("devtool.tui_textual",), "Legacy fallback is fenced when Textual is missing", x07),
    }


def _pres_g2_checks() -> dict[str, tuple[tuple[str, ...], str, Callable[[], None]]]:
    from . import textual_control_center as c
    from . import tui_textual as t

    def g1() -> None:
        gate = t.build_textual_replacement_proof_gate()
        t.assert_textual_replacement_gate_passes(gate)
        _require(gate.passed and gate.owns_truth is False, "PRES-G1")

    def x08() -> None:
        center = c.TextualControlCenter(page="runs", selected_run="run-1")
        center.ingest({"kind": "node", "run_id": "run-1", "node_id": "check", "state": "running"})
        result = center.handle_key("ctrl+c", confirmed=True)
        leave = center.handle_key("q")
        _require(result.get("kind") == "action-request" and leave.get("kind") == "exit" and leave.get("cancel_requested") is False, "PRES-X08")

    def g2() -> None:
        center = c.TextualControlCenter(page="workers")
        center.degrade_panel("workers", "provider-down")
        snapshot = center.snapshot()
        _require(snapshot["panel_health"]["workers"]["status"] == "degraded" and snapshot["panel_health"]["runs"]["status"] == "healthy", "PRES-G2")

    return {
        "PRES-G1": (("devtool.tui_textual", "devtool.presentation_surfaces"), "G1 gate is executable and fail-closed", g1),
        "PRES-X08": (("devtool.textual_control_center",), "Live controls request cancel separately from exit", x08),
        "PRES-G2": (("devtool.pres_g2_gate", "devtool.textual_control_center"), "G2 interaction invariants execute", g2),
    }


def pres_source_proofs(repo_root: Path, *, stage: str) -> tuple[SourceProof, ...]:
    """Run all required stage probes; errors raise rather than issuing false PASS."""
    if stage not in {"PRES-G1", "PRES-G2"}:
        raise ValueError(f"unsupported presentation gate: {stage}")
    probes = _pres_core_checks()
    if stage == "PRES-G2":
        probes.update(_pres_g2_checks())
    ids = PRES_G1_IDS if stage == "PRES-G1" else PRES_G2_IDS
    result: list[SourceProof] = []
    for proof_id in ids:
        modules, contract, probe = probes[proof_id]
        paths, digest = _bind_source(repo_root, ("devtool.source_bound_proofs",) + modules)
        probe()  # Never manufacture PASS from a file's presence or a string marker.
        result.append(SourceProof(proof_id, "passed", paths, digest, contract))
    return tuple(result)


def proof_digest(proofs: tuple[SourceProof, ...]) -> str:
    payload = [item.as_dict() for item in proofs]
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def exec_p082_source_proof(repo_root: Path) -> SourceProof:
    """Prove the X01 P082 boundary through a bounded non-Gradle validation.

    A historical report mentioning P082 is intentionally not consulted.
    This does not claim to qualify any Gradle, device or external substrate.
    """
    import subprocess
    import sys

    from .core.execution_semantics import (
        ApplicabilityPolicy,
        CheckpointPolicy,
        ResolvedValidationContract,
        ValidationCheck,
        ValidationOutcome,
        ValidationOutcomeState,
        resolved_validation_contract_from_specs,
    )
    from .models import CommandSpec

    paths, digest = _bind_source(repo_root, (
        "devtool.source_bound_proofs", "devtool.exec_g3_gate",
        "devtool.core.execution_semantics", "devtool.models",
    ))
    check_id = "exec-p082:non-gradle-smoke"
    spec = CommandSpec(
        "bounded-python-smoke", (sys.executable, "-c", "print('exec-p082-ok')"),
        metadata={"validation_check_id": check_id, "validation_required": True},
    )
    contract = resolved_validation_contract_from_specs((spec,), source="exec-p082-source-bound")
    _require(
        len(contract.checks) == 1
        and contract.checks[0].check_id == check_id
        and contract.checkpoint_policy.required_check_ids == (check_id,),
        "EXEC-P082-typed-contract",
    )
    process = subprocess.run(
        list(spec.argv), capture_output=True, text=True, timeout=8, check=False,
    )
    passed = ValidationOutcome(check_id, ValidationOutcomeState.PASSED, evidence_refs=("bounded:python",))
    failed = ValidationOutcome(check_id, ValidationOutcomeState.FAILED)
    skipped = ValidationOutcome(check_id, ValidationOutcomeState.SKIPPED)
    _require(
        process.returncode == 0 and process.stdout.strip() == "exec-p082-ok"
        and contract.evaluate((passed,)).satisfied
        and not contract.evaluate((failed,)).satisfied
        and not contract.evaluate((skipped,)).satisfied
        and not contract.evaluate(()).satisfied,
        "EXEC-P082-non-gradle-required-validation",
    )
    conditional = ResolvedValidationContract(
        (ValidationCheck("conditional", "conditional", applicability_policy=ApplicabilityPolicy.EXPLICIT),),
        CheckpointPolicy(required_check_ids=("conditional",)),
    )
    _require(
        not conditional.evaluate((ValidationOutcome("conditional", ValidationOutcomeState.NOT_APPLICABLE),)).satisfied
        and conditional.evaluate((ValidationOutcome(
            "conditional", ValidationOutcomeState.NOT_APPLICABLE,
            applicability_evidence=("substate:not-applicable",),
        ),)).satisfied,
        "EXEC-P082-explicit-applicability",
    )
    return SourceProof(
        "EXEC-P082", "passed", paths, digest,
        "Bounded Python validation; required, skipped, failure and applicability policies fail closed; no Gradle authority",
    )
