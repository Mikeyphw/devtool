"""Current-source structural obligation coverage, without obsolete G3 ledgers."""
from pathlib import Path
from .remed_r04b_fixed_point import G3R_REAL_SUBSTRATE_CARRY

CURRENT_AUTHORITIES = (
 "devtool/android_g1_gate.py", "devtool/android_g2_gate.py",
 "devtool/android/instrumentation.py", "devtool/life_g3_gate.py",
 "devtool/artifact_trust.py",
 "devtool/remed_r04b_fixed_point.py", "devtool/g3r_evidence_gate.py",
 "devtool/g3r_independent_verify.py", "devtool/g3r_evidence_generate.py",
)
PRESENTATION_SURFACES = frozenset(("machine","terminal","tui","desktop","android_hud","tasker","ipc","web","bridge"))

def evaluate(root: Path) -> dict:
    root = Path(root).resolve()
    checks = {f"active:{path}": (root/path).is_file() for path in CURRENT_AUTHORITIES}
    checks["six-real-substrate-obligations-are-explicit"] = len(G3R_REAL_SUBSTRATE_CARRY) == 6 and len(set(G3R_REAL_SUBSTRATE_CARRY)) == 6
    return {"schema_version": 3, "scope": "CURRENT_EXECUTABLE_AUTHORITIES_ONLY",
        "checks": checks, "passed": sum(checks.values()), "total": len(checks),
        "result": "PASS_CURRENT_AUTHORITY_STRUCTURE" if all(checks.values()) else "FAIL_CURRENT_AUTHORITY_STRUCTURE",
        "open_real_substrate_obligations": list(G3R_REAL_SUBSTRATE_CARRY),
        "release_qualified": False, "native_device_qualified": False}
