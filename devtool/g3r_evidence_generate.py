"""Generate G3R v3 source-bound evidence on native Termux (no historical ledgers).

Use `--run-tests --native-gates` for the full 13-lane candidate. This is a
read-only producer; its output is outside the checked-out Git repository.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import time
from zipfile import ZIP_DEFLATED, ZipFile

from .g3r_evidence_gate import REQUIRED_NATIVE, REQUIRED_SEMANTIC
from .g3r_independent_verify import RETIRED, confined, git, sha, tracked_files, verify
from .g3r_obligation_coverage import evaluate as coverage
from .remed_r04b_fixed_point import G3R_REAL_SUBSTRATE_CARRY


def dump(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def execute(argv: list[str], root: Path, timeout: int, log: Path) -> dict:
    started = time.monotonic()
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        child = subprocess.Popen(argv, cwd=root, start_new_session=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            output, _ = child.communicate(timeout=timeout)
            status = "PASS" if child.returncode == 0 else "FAIL"
            code = child.returncode
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            output, _ = child.communicate()
            status, code = "BLOCKED_TIMEOUT", None
        log.write_bytes(output)
        return {"status": status, "exit_code": code, "output_file": log.name,
                "output_sha256": sha(output), "duration_seconds": round(time.monotonic()-started,3)}
    except Exception as exc:
        return {"status": "BLOCKED_ERROR", "exit_code": None, "reason": str(exc)}


def native_termux_environment() -> bool:
    """Recognize native Termux Python on Android as well as Linux builds.

    CPython on Android may return ``Android`` from platform.system(), whereas
    older Termux Python returned ``Linux``. Both must still have the expected
    Termux installation prefix; neither a generic Android interpreter nor a
    Linux CI runner is considered native Termux by this check.
    """
    return (platform.system() in {"Linux", "Android"}
            and os.environ.get("PREFIX", "").startswith("/data/data/com.termux/files/usr"))


def generate(repo: Path, output_root: Path, *, run_tests: bool = False,
             native_gates: bool = False, timeout: int = 600) -> Path:
    root = repo.resolve(strict=True)
    check_clean = git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if check_clean.strip():
        raise ValueError("Dirty source checkout; refusing evidence generation")
    if any((root / rel).exists() for rel in RETIRED):
        raise ValueError("Historical attestations/ledgers still present")
    head = git(root, "rev-parse", "HEAD").decode().strip()
    tree = git(root, "rev-parse", "HEAD^{tree}").decode().strip()
    dst_root = output_root.resolve()
    if dst_root.is_relative_to(root):
        raise ValueError("Evidence output must be outside repository")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = dst_root / f"candidate-v3-{stamp}-{head[:12]}"
    out.mkdir(parents=True, exist_ok=False)
    paths = tracked_files(root)
    entries = [{"path": path, "sha256": sha(confined(root, path).read_bytes())} for path in paths]
    digest = sha(("\n".join(f"{r['path']}\0{r['sha256']}" for r in entries) + "\n").encode())
    dump(out / "SOURCE_MANIFEST.json", {"schema_version": 3, "source_commit": head,
         "source_tree": tree, "root_sha256": digest, "files": entries})
    structure = coverage(root)
    if structure["result"] != "PASS_CURRENT_AUTHORITY_STRUCTURE":
        raise ValueError("Required current executable authority is missing")
    dump(out / "COVERAGE.json", structure)
    native = native_termux_environment()
    checks = {}
    if run_tests:
        for name in sorted(REQUIRED_SEMANTIC):
            checks[name] = execute([sys.executable, "-m", "pytest", "-q", "--disable-warnings", "--tb=short", name],
                                   root, timeout, out/"runs"/("semantic-"+Path(name).stem+".log"))
            print(name, checks[name]["status"], flush=True)
    if native_gates:
        if not native:
            checks.update({name: {"status": "BLOCKED_NOT_NATIVE_TERMUX"} for name in REQUIRED_NATIVE})
        else:
            overrides = [key for key in os.environ if key.startswith("DEVTOOL_ANDROID_G1_") or key.startswith("DEVTOOL_ANDROID_G2_ALLOW_TEST_HOST")]
            if overrides:
                raise ValueError("Native qualification override present: " + ",".join(overrides))
            for name in sorted(REQUIRED_NATIVE):
                argv = ([sys.executable,"-c","import json; from devtool.life_g3_gate import _run_fresh_lifecycle_chain; print(json.dumps(_run_fresh_lifecycle_chain(),sort_keys=True))"]
                        if name == "life_g3_fresh_lifecycle_chain" else [sys.executable,"-m","devtool."+name])
                checks[name] = execute(argv,root,timeout,out/"runs"/("native-"+name+".log"))
                print(name,checks[name]["status"],flush=True)
    dump(out/"INDEPENDENT_SEMANTIC_AND_NATIVE_RUNS.json", {
        "schema_version": 3, "host_os": "Android" if native else platform.system(),
        "native_termux": native, "qualification": "NOT_QUALIFIED", "checks": checks})
    if git(root, "status", "--porcelain=v1", "--untracked-files=all").strip():
        raise ValueError("Source tree changed during qualification; candidate not completed")
    dump(out/"EVIDENCE_RECORD.json", {"schema_version": 3, "format": "G3R-CURRENT-SOURCE-V3",
        "source_commit": head, "source_tree": tree, "status": "SOURCE_RECONCILED_CANDIDATE",
        "current_source_truth_only": True, "formal_release_qualified": False,
        "historical_g3_requalified": False, "retired_artifacts": list(RETIRED),
        "open_real_substrate_obligations": list(G3R_REAL_SUBSTRATE_CARRY)})
    paths_in_evidence = sorted(p for p in out.rglob("*") if p.is_file())
    (out/"SHA256SUMS").write_text("".join(f"{sha(p.read_bytes())}  {p.relative_to(out).as_posix()}\n" for p in paths_in_evidence))
    verify(out,root)  # independent re-evaluation before offering evidence
    archive = out.with_suffix(".zip")
    with ZipFile(archive,"w",ZIP_DEFLATED,compresslevel=8) as z:
        for p in sorted(out.rglob("*")):
            if p.is_file(): z.write(p,p.relative_to(out))
    return archive


def main() -> int:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo",type=Path,default=Path.home()/"Code/devtool")
    ap.add_argument("--output-root",type=Path,default=Path.home()/".local/state/devtool/current-evidence-v3")
    ap.add_argument("--run-tests",action="store_true")
    ap.add_argument("--native-gates",action="store_true")
    ap.add_argument("--timeout",type=int,default=600)
    args=ap.parse_args()
    try:
        print("EVIDENCE_ZIP:",generate(args.repo,args.output_root,run_tests=args.run_tests,native_gates=args.native_gates,timeout=args.timeout))
        return 0
    except Exception as e:
        print("G3R v3 evidence generation blocked:",type(e).__name__,str(e),file=sys.stderr)
        return 2

if __name__=="__main__":raise SystemExit(main())
