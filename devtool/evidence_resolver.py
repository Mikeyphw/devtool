from __future__ import annotations

"""Resolvable evidence references for campaign gates and source dispositions.

A non-empty evidence string is not evidence.  This module gives gates one
small fail-closed resolver for executable pytest nodes, concrete source/report
files, and canonical RUN-V3 attachments.  Callers may additionally bind an
expected SHA-256; stale evidence then fails qualification instead of silently
remaining "satisfied" after source changes.
"""

from dataclasses import asdict, dataclass
import hashlib
from pathlib import Path
import subprocess
import sys
from typing import Any, Iterable, Mapping

from .core_runtime import private_run_root
from .run_store import RunCatalog


DEFAULT_EVIDENCE_ALIASES: dict[str, str] = {
    # TEST-G3 source/merged disposition symbolic rows.  These aliases are
    # retained for human readability in the historical ledgers, but gates must
    # resolve them to concrete source or executable evidence instead of treating
    # any non-empty string as proof.
    "roadmap-test-g3-trust": "tests/test_test_x01_failure_trustworthiness.py",
    "roadmap-test-g3-inventory": "tests/test_test_x02_inventory_selection.py",
    "roadmap-test-g3-runtime": "tests/test_test_x03_runtime_optimization.py",
    "roadmap-test-g3-convergence": "tests/test_test_x04_convergence.py",
    "roadmap-test-g3-final-proof": "tests/test_test_g3_final_seal.py",
    "roadmap-test-g3-runner-semantics": "tests/test_test_g3_final_seal.py",
    "TEST-X01/G1": "tests/test_test_g1_isolation_trust_gate.py",
    "TEST-X02/G2": "tests/test_test_g2_lane_selection_gate.py",
    "TEST-X03": "tests/test_test_x03_runtime_optimization.py",
    "TEST-X04/G3": "tests/test_test_x04_convergence.py",
    # DIST-G3 retained-substrate and final-proof aliases.
    "roadmap-dist-g3-retained-dist": "tests/test_dist_g3_final_seal.py",
    "roadmap-dist-g3-retained-substrate": "DIST_G3_HISTORICAL_SUBSTRATE_CROSSWALK.json",
    "roadmap-dist-g3-final-proof": "tests/test_dist_g3_final_seal.py",
    "DIST-X01/X02/G1": "tests/test_dist_g1_identity_delivery_recovery_gate.py",
    "DIST-X03/G2": "tests/test_dist_g2_provider_parity_gate.py",
    "DIST-X04": "tests/test_dist_x04_operational_adoption.py",
    "DIST-G3": "tests/test_dist_g3_final_seal.py",
}


@dataclass(frozen=True, slots=True)
class EvidenceResolution:
    reference: str
    resolved: bool
    kind: str
    path: str = ""
    sha256: str = ""
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class EvidenceResolver:
    def __init__(self, repo_root: str | Path, *, aliases: Mapping[str, str] | None = None) -> None:
        self.repo_root = Path(repo_root).expanduser().resolve()
        self.aliases = {**DEFAULT_EVIDENCE_ALIASES, **dict(aliases or {})}

    def _finish(self, reference: str, kind: str, path: Path, *, expected_sha256: str = "") -> EvidenceResolution:
        try:
            resolved = path.expanduser().resolve(strict=True)
        except OSError as exc:
            return EvidenceResolution(reference, False, kind, str(path), "", f"evidence path is unavailable: {exc}")
        if not resolved.is_file():
            return EvidenceResolution(reference, False, kind, str(resolved), "", "evidence path is not a file")
        digest = sha256_file(resolved)
        expected = str(expected_sha256 or "").strip().lower()
        if expected and digest != expected:
            return EvidenceResolution(reference, False, kind, str(resolved), digest, f"stale evidence hash: expected {expected}, observed {digest}")
        return EvidenceResolution(reference, True, kind, str(resolved), digest, "resolved")

    def _pytest(self, reference: str, *, expected_sha256: str = "", execute: bool = True) -> EvidenceResolution:
        file_part = reference.split("::", 1)[0]
        path = (self.repo_root / file_part).resolve(strict=False)
        base = self._finish(reference, "pytest-node", path, expected_sha256=expected_sha256)
        if not base.resolved or not execute:
            return base
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "--collect-only", "-q", reference],
                cwd=self.repo_root,
                text=True,
                capture_output=True,
                check=False,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return EvidenceResolution(reference, False, "pytest-node", base.path, base.sha256, f"pytest collection failed: {exc}")
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().splitlines()
            return EvidenceResolution(reference, False, "pytest-node", base.path, base.sha256, detail[-1] if detail else f"pytest collect exit {result.returncode}")
        return EvidenceResolution(reference, True, "pytest-node", base.path, base.sha256, "executable pytest node resolved")

    def _run_attachment(self, reference: str, *, expected_sha256: str = "") -> EvidenceResolution:
        # Canonical form: run:<run_id>:attachment:<attachment_id>
        parts = reference.split(":", 3)
        if len(parts) != 4 or parts[0] != "run" or parts[2] != "attachment":
            return EvidenceResolution(reference, False, "run-attachment", detail="malformed RUN attachment reference")
        run_id, attachment_id = parts[1], parts[3]
        catalog = RunCatalog.open(private_run_root(self.repo_root), create=False)
        attachment = catalog.attachments.get(attachment_id)
        if attachment is None or attachment.run_id != run_id:
            return EvidenceResolution(reference, False, "run-attachment", detail="RUN attachment is not registered")
        if attachment.state == "tombstoned":
            return EvidenceResolution(reference, False, "run-attachment", attachment.path, attachment.sha256, "RUN attachment is tombstoned")
        if not attachment.materialized:
            expected = str(expected_sha256 or "").strip().lower()
            if expected and attachment.sha256 != expected:
                return EvidenceResolution(reference, False, "run-attachment", "", attachment.sha256, "remote attachment hash does not match expected hash")
            # A locator is provenance, not executable evidence. Campaign seals
            # require bytes that can be hashed now; callers must materialize a
            # remote attachment before it can qualify as evidence.
            detail = "RUN attachment is not materialized"
            if attachment.remote_locator:
                detail += f" (remote locator: {attachment.remote_locator})"
            return EvidenceResolution(reference, False, "run-attachment", "", attachment.sha256, detail)
        result = self._finish(reference, "run-attachment", Path(attachment.path), expected_sha256=expected_sha256 or attachment.sha256)
        if result.resolved and attachment.sha256 and result.sha256 != attachment.sha256:
            return EvidenceResolution(reference, False, "run-attachment", result.path, result.sha256, "materialized attachment no longer matches RUN catalog hash")
        return result

    def resolve(self, reference: str, *, expected_sha256: str = "", execute_pytest: bool = True) -> EvidenceResolution:
        ref = str(reference or "").strip()
        if not ref:
            return EvidenceResolution(ref, False, "unknown", detail="empty evidence reference")
        alias_target = self.aliases.get(ref)
        if alias_target:
            resolved = self.resolve(alias_target, expected_sha256=expected_sha256, execute_pytest=execute_pytest)
            if resolved.resolved:
                return EvidenceResolution(ref, True, f"alias:{resolved.kind}", resolved.path, resolved.sha256, f"alias resolved to {alias_target}")
            return EvidenceResolution(ref, False, f"alias:{resolved.kind}", resolved.path, resolved.sha256, f"alias target {alias_target!r} failed: {resolved.detail}")
        if ref.startswith("run:") and ":attachment:" in ref:
            return self._run_attachment(ref, expected_sha256=expected_sha256)
        if "::" in ref and ref.split("::", 1)[0].endswith(".py"):
            return self._pytest(ref, expected_sha256=expected_sha256, execute=execute_pytest)
        if ref.startswith("source:"):
            raw = ref[len("source:"):]
            path_text, _, symbol = raw.partition("#")
            result = self._finish(ref, "source", self.repo_root / path_text, expected_sha256=expected_sha256)
            if result.resolved and symbol:
                try:
                    text = Path(result.path).read_text(encoding="utf-8", errors="replace")
                except OSError as exc:
                    return EvidenceResolution(ref, False, "source", result.path, result.sha256, str(exc))
                if symbol not in text:
                    return EvidenceResolution(ref, False, "source", result.path, result.sha256, f"source symbol/text {symbol!r} was not found")
            return result
        raw_path = ref[len("file:"):] if ref.startswith("file:") else ref
        return self._finish(ref, "file", self.repo_root / raw_path, expected_sha256=expected_sha256)

    def resolve_many(self, rows: Iterable[Mapping[str, Any]], *, execute_pytest: bool = True) -> tuple[EvidenceResolution, ...]:
        results: list[EvidenceResolution] = []
        for row in rows:
            raw_reference = row.get("reference")
            if raw_reference is None:
                raw_reference = row.get("evidence")
            expected = str(row.get("sha256") or row.get("expected_sha256") or "")
            if isinstance(raw_reference, (list, tuple)):
                for item in raw_reference:
                    results.append(self.resolve(str(item), expected_sha256=expected, execute_pytest=execute_pytest))
            else:
                results.append(self.resolve(str(raw_reference or ""), expected_sha256=expected, execute_pytest=execute_pytest))
        return tuple(results)

    def disposition_evidence_rows(self, rows: Iterable[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
        expanded: list[dict[str, Any]] = []
        for row in rows:
            evidence = row.get("evidence")
            row_id = str(row.get("id") or row.get("source_id") or row.get("promise_id") or "<unknown>")
            if not isinstance(evidence, (list, tuple)) or not evidence:
                expanded.append({"reference": "", "source_row_id": row_id})
                continue
            hashes = row.get("evidence_sha256") if isinstance(row.get("evidence_sha256"), Mapping) else {}
            for reference in evidence:
                ref = str(reference)
                expanded.append({"reference": ref, "expected_sha256": str(hashes.get(ref) or ""), "source_row_id": row_id})
        return tuple(expanded)

    def require_all(self, rows: Iterable[Mapping[str, Any]], *, execute_pytest: bool = True) -> tuple[EvidenceResolution, ...]:
        results = self.resolve_many(rows, execute_pytest=execute_pytest)
        failures = [item for item in results if not item.resolved]
        if failures:
            details = "; ".join(f"{item.reference}: {item.detail}" for item in failures)
            raise RuntimeError(f"Evidence qualification failed: {details}")
        return results


__all__ = ["DEFAULT_EVIDENCE_ALIASES", "EvidenceResolution", "EvidenceResolver", "sha256_file"]
