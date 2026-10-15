"""Active R04B path-confinement helper; historical fixed-point manifests retired.

Current cross-surface source trust belongs to G3R v3. No missing ledger or
historical attestation can be interpreted as qualification.
"""
from __future__ import annotations
from hashlib import sha256
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping
from .evidence_resolver import DEFAULT_EVIDENCE_ALIASES

G3R_REAL_SUBSTRATE_CARRY = (
    "ENV-A06", "ANDROID-A16", "DIST-A01", "DIST-A12", "DIST-A13", "TEST-A11",
)

def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()

def _resolve_reference(repo_root: Path, reference: str, *, source_rows: Mapping[str, Mapping[str, Any]], seen: tuple[str, ...] = ()) -> dict[str, Any]:
    ref = str(reference or "").strip()
    if ref in DEFAULT_EVIDENCE_ALIASES and ref not in seen:
        target = DEFAULT_EVIDENCE_ALIASES[ref]
        resolved = _resolve_reference(repo_root, target, source_rows=source_rows, seen=(*seen, ref))
        resolved["alias_target"] = target
        return resolved
    # Untrusted historical references must never escape the repository, even
    # when a file exists at the destination of a symlink or an absolute path.
    def confined_file(raw_path: str) -> tuple[Path | None, str]:
        if not raw_path or "\x00" in raw_path:
            return None, "empty-or-invalid"
        candidate = (repo_root / raw_path).resolve(strict=False)
        if not candidate.is_relative_to(repo_root.resolve()) or not candidate.is_file():
            return None, "missing-or-escaped"
        return candidate, ""

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
        path, issue = confined_file(raw_path)
        if path is not None:
            return {"kind": "source-file", "path": raw_path, "sha256": _sha(path), "expected_sha256": "", "resolved": True, "detail": ""}
        return {"kind": "source-file", "path": raw_path, "sha256": "", "expected_sha256": "", "resolved": False, "detail": issue}

    file_part = ref.split("::", 1)[0] if ref.split("::", 1)[0].endswith(".py") else ref
    if file_part.startswith("file:"):
        file_part = file_part[len("file:"):]
    path, issue = confined_file(file_part)
    if path is not None:
        return {
            "kind": "pytest-file" if ref != file_part and file_part.endswith(".py") else "file",
            "path": file_part,
            "sha256": _sha(path),
            "expected_sha256": "",
            "resolved": True,
            "detail": "",
        }
    return {"kind": "file", "path": file_part, "sha256": "", "expected_sha256": "", "resolved": False, "detail": issue}


def _git_value(repo_root: Path, *args: str) -> str:
    root = repo_root.resolve(strict=True)
    proc = subprocess.run(["git", "-c", f"safe.directory={root}", *args],
                          cwd=root, text=True, capture_output=True, check=False, timeout=20)
    if proc.returncode or not proc.stdout.strip():
        raise RuntimeError("R04B Git authority lookup failed: " + (proc.stderr.strip() or "empty Git response"))
    return proc.stdout.strip()


def audit_r04b_fixed_point(repo_root: str | Path = ".") -> dict[str, Any]:
    """Explicit retirement, not an automatic fixed-point pass."""
    return {"schema_version": 2, "status": "not_qualified", "historical_ledgers_retired": True,
            "g3r_real_substrate_carry": list(G3R_REAL_SUBSTRATE_CARRY),
            "reason": "Current-source qualification requires a verified G3R v3 candidate"}

def main(argv=None) -> int:
    import argparse, json
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo-root", default=".")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    print(json.dumps(audit_r04b_fixed_point(args.repo_root), sort_keys=True))
    return 2

if __name__ == "__main__":
    raise SystemExit(main())
