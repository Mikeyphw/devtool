"""G3R v3 exact-source and native evidence consumer, not a release seal."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import stat
import sys
import tempfile
from typing import Any, Iterator
from zipfile import ZipFile, BadZipFile

from .g3r_independent_verify import verify
from .remed_r04b_fixed_point import G3R_REAL_SUBSTRATE_CARRY

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_NATIVE = frozenset({
    "android_g1_gate", "android_g2_gate", "life_g3_fresh_lifecycle_chain",
})
REQUIRED_SEMANTIC = frozenset({
    "tests/test_android_g2_instrumentation_device_gate.py",
    "tests/test_android_instrumentation.py",
    "tests/test_android_x02_execution_snapshot_device_fabric.py",
    "tests/test_android_x03_instrumentation_lifecycle.py",
    "tests/test_android_x04_advanced_execution.py",
    "tests/test_aops03_android_signing_instrumentation_operations.py",
    "tests/test_dist_x04_operational_adoption.py",
    "tests/test_life_x02_canonical_artifact_trust_pack.py",
    "tests/test_tui_result_retention.py",
    "tests/test_ui07_ui08_confirmation_notification_terminal.py",
})
REQUIRED_LANES = REQUIRED_NATIVE | REQUIRED_SEMANTIC
MAX_MEMBER_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_MEMBERS = 128

class G3REvidenceError(ValueError):
    """Versioned candidate was not eligible for current-source qualification."""


def _require(ok: bool, message: str) -> None:
    if not ok:
        raise G3REvidenceError(message)


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_bytes())
    _require(isinstance(payload, dict), f"Invalid object: {path.name}")
    return payload


@contextmanager
def _candidate_dir(candidate: Path) -> Iterator[Path]:
    """Use existing directories or inspect ZIPs without path traversal/symlinks."""
    if candidate.is_dir():
        yield candidate.resolve(strict=True)
        return
    _require(candidate.is_file(), "Evidence ZIP/directory missing")
    with tempfile.TemporaryDirectory(prefix="devtool-g3r-verify-") as td:
        out = Path(td).resolve()
        try:
            with ZipFile(candidate) as archive:
                items = archive.infolist()
                _require(0 < len(items) <= MAX_MEMBERS, "Invalid ZIP member count")
                names: set[str] = set()
                total = 0
                for entry in items:
                    name = entry.filename
                    path = PurePosixPath(name)
                    _require(
                        name and "\\" not in name and "\x00" not in name
                        and not name.startswith("/") and ".." not in name.split("/")
                        and "." not in name.split("/") and not path.is_absolute(),
                        f"Unsafe evidence member: {name}",
                    )
                    normalized = path.as_posix().rstrip("/")
                    _require(normalized not in names, "Duplicate archive member")
                    names.add(normalized)
                    mode = (entry.external_attr >> 16) & 0o170000
                    _require(mode in (0, stat.S_IFREG, stat.S_IFDIR), "Symlink or special archive member forbidden")
                    _require(entry.file_size <= MAX_MEMBER_BYTES, "Oversized evidence member")
                    total += entry.file_size
                    _require(total <= MAX_TOTAL_BYTES, "Oversized evidence archive")
                for entry in items:
                    dst = out / PurePosixPath(entry.filename)
                    if entry.is_dir():
                        dst.mkdir(parents=True, exist_ok=True)
                    else:
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        # Streaming limit applies even if ZIP metadata is misleading.
                        with archive.open(entry) as src, dst.open("xb") as target:
                            read = 0
                            while block := src.read(65536):
                                read += len(block)
                                _require(read <= MAX_MEMBER_BYTES, "Inflated ZIP member too large")
                                target.write(block)
        except (BadZipFile, OSError) as exc:
            raise G3REvidenceError(f"Invalid evidence ZIP: {exc}") from exc
        yield out


def evaluate(candidate: Path, repo: Path = ROOT, *, require_native: bool = True) -> dict[str, Any]:
    """Verify G3R v3 against clean HEAD and 13 machine-recorded lanes.

    V2 historical candidates are intentionally rejected after Phase A.
    """
    with _candidate_dir(Path(candidate)) as evidence:
        independent = verify(evidence, Path(repo))
        record = _read_json(evidence / "EVIDENCE_RECORD.json")
        runtime = _read_json(evidence / "INDEPENDENT_SEMANTIC_AND_NATIVE_RUNS.json")
        _require(record.get("schema_version") == 3 and record.get("format") == "G3R-CURRENT-SOURCE-V3",
                 "Unsupported candidate generation (v2 retired)")
        _require(record.get("current_source_truth_only") is True, "Missing candidate scope boundary")
        _require(record.get("formal_release_qualified") is False, "Release qualification may not be asserted")
        _require(runtime.get("qualification") == "NOT_QUALIFIED", "Native receipts cannot declare release")
        checks = runtime.get("checks")
        _require(isinstance(checks, dict), "Missing execution lane inventory")
        present = frozenset(checks)
        successful = frozenset(k for k, v in checks.items()
                               if isinstance(v, dict) and v.get("status") == "PASS"
                               and v.get("exit_code") == 0 and isinstance(v.get("output_file"), str)
                               and isinstance(v.get("output_sha256"), str))
        native = runtime.get("native_termux") is True and runtime.get("host_os") == "Android"
        complete = present == REQUIRED_LANES and successful == REQUIRED_LANES
        if require_native:
            _require(native, "Evidence was not executed on native Termux")
            _require(complete, f"Native/semantic lanes incomplete: {sorted(REQUIRED_LANES - successful)}")
        return {
            "schema_version": 3, "gate": "G3R-CURRENT-SOURCE-EVIDENCE",
            "status": "PASS_CURRENT_SOURCE_AND_NATIVE_LANES" if native and complete else "PASS_CURRENT_SOURCE_ONLY",
            "source_commit": independent["source_commit"], "source_tree": independent["source_tree"],
            "source_files": independent["source_files"],
            "source_root_sha256": independent["source_root_sha256"],
            "independent_verification": independent["verification"],
            "historical_artifacts_retired": independent["historical_artifacts_retired"],
            "native_termux_runs": bool(native),
            "passed_lanes": len(successful & REQUIRED_LANES), "required_lanes": len(REQUIRED_LANES),
            "historical_g3_requalified": False, "formal_release_qualified": False,
            "open_real_substrate_obligations": list(G3R_REAL_SUBSTRATE_CARRY),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, type=Path, help="Native evidence ZIP or extracted directory")
    parser.add_argument("--repo", type=Path, default=ROOT, help="Clean Git checkout at candidate commit")
    parser.add_argument("--source-only", action="store_true", help="Verify source without requiring all 13 native lanes")
    args = parser.parse_args(argv)
    try:
        result = evaluate(args.candidate, args.repo, require_native=not args.source_only)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (ValueError, OSError, KeyError, RuntimeError) as exc:
        print(f"G3R verification blocked: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
