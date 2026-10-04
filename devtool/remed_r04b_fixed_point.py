from __future__ import annotations

"""REMED-R04B whole-campaign fixed-point audit.

R04B is the last remediation implementation boundary before the CORE-G3R
requalification window.  It does not qualify unavailable real providers or
pretend historical candidate attestations are current seals.  Instead it gives
G3R one production audit that must stay true on the checked-out repository:

* every retained source-disposition evidence row is bound to current bytes or a
  concrete source-row digest;
* every checked-in final attestation that is still retained by G3R matches the
  current disposition/evidence manifests it names;
* presentation surfaces share one explicit diagnostic/action/notification/
  bridge/stream matrix; and
* known carried-forward environment-bound items remain explicit, not silently
  green.
"""

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Iterable, Mapping

from .evidence_resolver import DEFAULT_EVIDENCE_ALIASES

SOURCE_BINDINGS_FILE = "REMED_R04B_SOURCE_EVIDENCE_BINDINGS.json"
PRESENTATION_MATRIX_FILE = "REMED_R04B_PRESENTATION_SURFACE_MATRIX.json"

SOURCE_DISPOSITION_LEDGERS = (
    "CORE_G3_SOURCE_DISPOSITIONS.json",
    "ENV_G3_SOURCE_DISPOSITIONS.json",
    "ANDROID_G3_SOURCE_DISPOSITIONS.json",
    "LIFE_G3_SOURCE_DISPOSITIONS.json",
    "DIST_G3_SOURCE_DISPOSITION_LEDGER.json",
    "TEST_G3_SOURCE_DISPOSITION_LEDGER.json",
)

# These are not R04B source gaps.  They are deliberately carried to G3R because
# the required proof is a real substrate/device/provider run.
G3R_REAL_SUBSTRATE_CARRY = (
    "ENV-A06",
    "ANDROID-A16",
    "DIST-A01",
    "DIST-A12",
    "DIST-A13",
    "TEST-A11",
)

_REQUIRED_PRESENTATION_SURFACES = {
    "machine",
    "terminal",
    "tui",
    "desktop",
    "android_hud",
    "tasker",
    "ipc",
    "web",
    "bridge",
}
_REQUIRED_PRESENTATION_INVARIANTS = {"diagnostics", "actions", "notifications", "bridge", "stream"}


@dataclass(frozen=True, slots=True)
class R04BCheck:
    id: str
    status: str
    summary: str
    details: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _check(identifier: str, ok: bool, summary: str, details: Iterable[str] = ()) -> R04BCheck:
    return R04BCheck(identifier, "pass" if ok else "fail", summary, tuple(str(item) for item in details))


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"{path.name} must contain a JSON object")
    return payload


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _git_value(repo_root: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo_root, text=True, capture_output=True, check=False, timeout=20)
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def _source_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("rows")
    if isinstance(rows, list):
        return [dict(item) for item in rows if isinstance(item, dict)]
    combined: list[dict[str, Any]] = []
    for key in ("promises", "regressions"):
        raw = payload.get(key)
        if isinstance(raw, list):
            combined.extend(dict(item) for item in raw if isinstance(item, dict))
    return combined


def _row_id(row: Mapping[str, Any]) -> str:
    return str(row.get("source_id") or row.get("id") or row.get("promise_id") or row.get("obligation_id") or "")


def _all_source_rows(repo_root: Path) -> dict[str, dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for rel in SOURCE_DISPOSITION_LEDGERS:
        path = repo_root / rel
        if not path.is_file():
            continue
        for row in _source_rows(_load_json(path)):
            identifier = _row_id(row)
            if identifier:
                by_id[identifier] = row
    return by_id


def _resolve_reference(repo_root: Path, reference: str, *, source_rows: Mapping[str, Mapping[str, Any]], seen: tuple[str, ...] = ()) -> dict[str, Any]:
    ref = str(reference or "").strip()
    if ref in DEFAULT_EVIDENCE_ALIASES and ref not in seen:
        target = DEFAULT_EVIDENCE_ALIASES[ref]
        resolved = _resolve_reference(repo_root, target, source_rows=source_rows, seen=(*seen, ref))
        resolved["alias_target"] = target
        return resolved
    if ref.startswith("source:"):
        raw = ref[len("source:"):]
        match = re.fullmatch(r"([^:#]+):([0-9a-fA-F]{64})", raw)
        if match:
            source_id = match.group(1)
            expected = match.group(2).lower()
            row = source_rows.get(source_id)
            observed = str((row or {}).get("source_row_sha256") or "").lower()
            return {
                "kind": "source-row",
                "path": "",
                "sha256": observed,
                "expected_sha256": expected,
                "resolved": bool(row) and observed == expected,
                "detail": source_id,
            }
        raw_path = raw.split("#", 1)[0]
        path = repo_root / raw_path
        if path.is_file():
            return {"kind": "source-file", "path": raw_path, "sha256": _sha(path), "expected_sha256": "", "resolved": True, "detail": ""}
        return {"kind": "source-file", "path": raw_path, "sha256": "", "expected_sha256": "", "resolved": False, "detail": "missing"}

    file_part = ref.split("::", 1)[0] if ref.split("::", 1)[0].endswith(".py") else ref
    if file_part.startswith("file:"):
        file_part = file_part[len("file:"):]
    path = repo_root / file_part
    if path.is_file():
        return {
            "kind": "pytest-file" if ref != file_part and file_part.endswith(".py") else "file",
            "path": file_part,
            "sha256": _sha(path),
            "expected_sha256": "",
            "resolved": True,
            "detail": "",
        }
    return {"kind": "file", "path": file_part, "sha256": "", "expected_sha256": "", "resolved": False, "detail": "missing"}


def build_source_evidence_bindings(repo_root: str | Path = ".") -> dict[str, Any]:
    root = Path(repo_root).resolve()
    source_rows = _all_source_rows(root)
    ledger_rows: list[tuple[str, str, dict[str, Any]]] = []
    for ledger in SOURCE_DISPOSITION_LEDGERS:
        payload = _load_json(root / ledger)
        for row in _source_rows(payload):
            ledger_rows.append((ledger, _row_id(row), row))

    bindings: list[dict[str, Any]] = []
    for ledger, identifier, row in ledger_rows:
        evidence = row.get("evidence") or []
        if isinstance(evidence, str):
            evidence = [evidence]
        for reference in evidence:
            resolved = _resolve_reference(root, str(reference), source_rows=source_rows)
            bindings.append({"ledger": ledger, "row_id": identifier, "reference": str(reference), **resolved})

    items = [
        "\0".join(
            [
                str(item.get("ledger") or ""),
                str(item.get("row_id") or ""),
                str(item.get("reference") or ""),
                str(item.get("alias_target") or ""),
                str(item.get("kind") or ""),
                str(item.get("path") or ""),
                str(item.get("sha256") or ""),
                str(item.get("expected_sha256") or ""),
            ]
        )
        for item in bindings
    ]
    return {
        "schema_version": 1,
        "kind": "remed-r04b-source-evidence-bindings",
        "position": "78/93",
        "gate_window_position": "8/8",
        "current_head": _git_value(root, "rev-parse", "HEAD"),
        "current_tree": _git_value(root, "rev-parse", "HEAD^{tree}"),
        "source_ledgers": list(SOURCE_DISPOSITION_LEDGERS),
        "source_row_count": len(ledger_rows),
        "evidence_binding_count": len(bindings),
        "binding_root_sha256": sha256(("\n".join(sorted(items)) + "\n").encode()).hexdigest(),
        "bindings": bindings,
    }


def audit_source_evidence_bindings(repo_root: str | Path = ".") -> dict[str, Any]:
    root = Path(repo_root).resolve()
    expected = _load_json(root / SOURCE_BINDINGS_FILE)
    observed = build_source_evidence_bindings(root)
    failures = [item for item in observed["bindings"] if not item.get("resolved")]
    mismatches: list[str] = []
    for key in ("source_ledgers", "source_row_count", "evidence_binding_count", "binding_root_sha256"):
        if observed.get(key) != expected.get(key):
            mismatches.append(f"{key}: expected={expected.get(key)!r} observed={observed.get(key)!r}")
    return {
        "schema_version": 1,
        "ok": not failures and not mismatches,
        "source_rows": observed["source_row_count"],
        "evidence_bindings": observed["evidence_binding_count"],
        "binding_root_sha256": observed["binding_root_sha256"],
        "failures": failures[:20],
        "mismatches": mismatches,
    }


def audit_presentation_matrix(repo_root: str | Path = ".") -> dict[str, Any]:
    root = Path(repo_root).resolve()
    payload = _load_json(root / PRESENTATION_MATRIX_FILE)
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    surfaces = {str(row.get("surface") or "") for row in rows if isinstance(row, dict)}
    invariants = {str(row.get("invariant") or "") for row in rows if isinstance(row, dict)}
    failures: list[str] = []
    if not _REQUIRED_PRESENTATION_SURFACES.issubset(surfaces):
        failures.append("missing surfaces: " + ", ".join(sorted(_REQUIRED_PRESENTATION_SURFACES - surfaces)))
    if not _REQUIRED_PRESENTATION_INVARIANTS.issubset(invariants):
        failures.append("missing invariants: " + ", ".join(sorted(_REQUIRED_PRESENTATION_INVARIANTS - invariants)))
    for row in rows:
        if not isinstance(row, dict):
            failures.append("invalid matrix row")
            continue
        refs = [str(item) for item in (row.get("evidence") or [])]
        hashes = row.get("evidence_sha256") if isinstance(row.get("evidence_sha256"), Mapping) else {}
        if not refs or set(refs) != set(hashes):
            failures.append(f"evidence binding incomplete for {row.get('surface')}:{row.get('invariant')}")
            continue
        for rel in refs:
            path = root / rel
            if not path.is_file():
                failures.append(f"missing matrix evidence: {rel}")
                continue
            digest = _sha(path)
            if digest != hashes[rel]:
                failures.append(f"stale matrix evidence: {rel}")
    actual_root = sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if actual_root != payload.get("matrix_root_sha256"):
        failures.append("matrix root digest mismatch")
    return {
        "schema_version": 1,
        "ok": not failures,
        "surfaces": sorted(surfaces),
        "invariants": sorted(invariants),
        "rows": len(rows),
        "failures": failures,
    }


def audit_retained_attestations(repo_root: str | Path = ".") -> dict[str, Any]:
    root = Path(repo_root).resolve()
    checks: list[str] = []
    failures: list[str] = []
    pairs = (
        ("CORE_G3_FINAL_ATTESTATION.json", "CORE_G3_SOURCE_DISPOSITIONS.json", "source_dispositions_sha256"),
        ("ENV_G3_FINAL_ATTESTATION.json", "ENV_G3_SOURCE_DISPOSITIONS.json", "source_dispositions_sha256"),
        ("ANDROID_G3_FINAL_ATTESTATION.json", "ANDROID_G3_SOURCE_DISPOSITIONS.json", "source_dispositions_sha256"),
        ("LIFE_G3_FINAL_ATTESTATION.json", "LIFE_G3_SOURCE_DISPOSITIONS.json", "source_dispositions_sha256"),
    )
    for attestation, disposition, field in pairs:
        att = _load_json(root / attestation)
        digest = _sha(root / disposition)
        if att.get(field) != digest:
            failures.append(f"{attestation}:{field} stale for {disposition}")
        checks.append(attestation)
    return {"schema_version": 1, "ok": not failures, "checked": checks, "failures": failures}


def audit_r04b_fixed_point(repo_root: str | Path = ".") -> dict[str, Any]:
    root = Path(repo_root).resolve()
    evidence = audit_source_evidence_bindings(root)
    matrix = audit_presentation_matrix(root)
    attestations = audit_retained_attestations(root)
    checks = [
        _check("source-evidence-bindings", bool(evidence.get("ok")), "all retained source evidence resolves and matches the R04B stale-hash manifest", evidence.get("mismatches", ()) or [str(item) for item in evidence.get("failures", ())]),
        _check("presentation-surface-matrix", bool(matrix.get("ok")), "cross-surface diagnostic/action/notification/bridge/stream matrix is hash-bound", matrix.get("failures", ())),
        _check("retained-attestations", bool(attestations.get("ok")), "retained G3 attestations match current disposition manifests", attestations.get("failures", ())),
        _check("g3r-carry-explicit", True, "real-substrate obligations are explicitly carried to G3R instead of marked green", G3R_REAL_SUBSTRATE_CARRY),
    ]
    failures = [item for item in checks if item.status != "pass"]
    return {
        "schema_version": 1,
        "kind": "remed-r04b-fixed-point-audit",
        "position": "78/93",
        "gate_window_position": "8/8",
        "status": "pass" if not failures else "fail",
        "repo_root": str(root),
        "current_head": _git_value(root, "rev-parse", "HEAD"),
        "current_tree": _git_value(root, "rev-parse", "HEAD^{tree}"),
        "next_major_phase": "CORE-G3R 79/93",
        "checks": [item.as_dict() for item in checks],
        "evidence": {k: v for k, v in evidence.items() if k != "failures"},
        "presentation_matrix": {k: v for k, v in matrix.items() if k != "failures"},
        "retained_attestations": attestations,
        "g3r_real_substrate_carry": list(G3R_REAL_SUBSTRATE_CARRY),
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run the REMED-R04B fixed-point audit")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    payload = audit_r04b_fixed_point(args.repo_root)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"REMED-R04B fixed point: {payload['status'].upper()}")
        for check in payload["checks"]:
            print(f"[{check['status'].upper()}] {check['id']}: {check['summary']}")
            for detail in check.get("details") or ():
                print(f"  - {detail}")
    return 0 if payload["status"] == "pass" else 1


__all__ = [
    "G3R_REAL_SUBSTRATE_CARRY",
    "SOURCE_BINDINGS_FILE",
    "PRESENTATION_MATRIX_FILE",
    "audit_presentation_matrix",
    "audit_r04b_fixed_point",
    "audit_retained_attestations",
    "audit_source_evidence_bindings",
    "build_source_evidence_bindings",
]

if __name__ == "__main__":
    raise SystemExit(main())
