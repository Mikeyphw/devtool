from __future__ import annotations

"""RMP-C03 source-bound, local-only asynchronous acknowledgement acceptance."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

TESTS: tuple[str, ...] = (
    "tests/test_dist_x01_delegation_ingest.py",
    "tests/test_dist_x02_async_transport.py",
    "tests/test_dist_x03_provider_control_hierarchy.py",
    "tests/test_dist_x04_operational_adoption.py",
    "tests/test_dist_g1_identity_delivery_recovery_gate.py",
    "tests/test_dist_g2_provider_parity_gate.py",
    "tests/test_dist_g3_final_seal.py",
    "tests/test_r03_distributed_ack_binding.py",
    "tests/test_rmp_c03_ack_context.py",
)
MINIMUM_CASES = 89


def sha(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def _git(repo: Path, *args: str) -> bytes:
    run = subprocess.run(("git", "-C", str(repo), *args), capture_output=True, check=False)
    if run.returncode:
        raise RuntimeError("Git source verification failed: " + run.stderr.decode(errors="replace").strip())
    return run.stdout


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def execute(repo: Path, output: Path, *, timeout: int = 300) -> dict:
    start = datetime.now(timezone.utc).isoformat()
    report: dict = {
        "schema_version": 1,
        "producer": "devtool-rmp-c03-local-acceptance",
        "scope": "RMP-C03-source-bound-async-ack-local-only",
        "overall": "BLOCKED_C03_LOCAL_ACCEPTANCE",
        "formal_c03_closed": False,
        "release_qualified": False,
        "guardrails": {
            "automatic_push": False,
            "automatic_release": False,
            "automatic_requalification_of_historical_campaigns": False,
            "test_owned_disposable_artifacts_only": True,
            "other_rmp_findings_closed": False,
        },
        "started_at_utc": start,
    }
    try:
        if _git(repo,"branch","--show-current").decode().strip() != "feature/rmp01-security-remediation":
            raise RuntimeError("expected RMP security branch")
        head = _git(repo,"rev-parse","HEAD").decode().strip()
        filenames = (*TESTS, "devtool/distributed_transport.py", "devtool/rmp_c03_acceptance.py")
        inventory: dict[str,str] = {}
        for name in filenames:
            file = repo/name
            if file.is_symlink() or not file.is_file():
                raise RuntimeError("missing or symlinked required source: " + name)
            contents = file.read_bytes()
            if _git(repo,"show","HEAD:"+name) != contents:
                raise RuntimeError("uncommitted source mutation: " + name)
            inventory[name] = sha(contents)
        report.update(source_commit=head, source_sha256=inventory, lanes={name:inventory[name] for name in TESTS})
        env = dict(os.environ)
        env["PYTHONPATH"] = str(repo) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        try:
            completed = subprocess.run(
                (sys.executable, "-m", "pytest", "-q", *TESTS),
                cwd=repo, env=env, capture_output=True, timeout=timeout, check=False,
            )
            combined=completed.stdout + b"\n" + completed.stderr
            report["pytest_returncode"] = completed.returncode
            report["output_sha256"] = sha(combined)
            summary = re.search(rb"(?<!\d)(\d+) passed\b", combined)
            report["pytest_passed"] = int(summary.group(1)) if summary else 0
            report["timed_out"] = False
            if completed.returncode == 0 and report["pytest_passed"] >= MINIMUM_CASES:
                report["overall"] = "PASS_C03_LOCAL_ACCEPTANCE"
            else:
                report["reason"] = "focused C03 DIST regression suite failed or incomplete"
        except subprocess.TimeoutExpired as exc:
            report.update(timed_out=True, pytest_returncode=None, pytest_passed=0,
                          reason="focused C03 suite exceeded timeout", output_sha256=sha((exc.stdout or b"")+(exc.stderr or b"")))
    except (RuntimeError, OSError, ValueError) as exc:
        report["reason"] = str(exc)
    report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    output.parent.mkdir(parents=True, exist_ok=True)
    # Close the file before atomic publication; never replace an existing report.
    fd,name=tempfile.mkstemp(prefix=".rmp-c03-",dir=str(output.parent))
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as handle:
            json.dump(report,handle,sort_keys=True,indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if output.exists() or output.is_symlink():
            raise RuntimeError("acceptance receipt already exists")
        os.replace(name,output)
    finally:
        try: os.unlink(name)
        except FileNotFoundError: pass
    return report


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo",type=Path,default=Path("."))
    parser.add_argument("--output",type=Path)
    parser.add_argument("--timeout",type=int,default=300)
    args=parser.parse_args(argv)
    repo=args.repo.expanduser().resolve(strict=True)
    output=args.output or (Path.home()/".local"/"state"/"devtool"/"rmp-c03"/("acceptance-"+_stamp()+".json"))
    result=execute(repo,output,timeout=max(1,args.timeout))
    print(json.dumps({"overall":result["overall"],"receipt":str(output),"pytest_passed":result.get("pytest_passed",0),"formal_c03_closed":False,"reason":result.get("reason")},sort_keys=True))
    return 0 if result["overall"]=="PASS_C03_LOCAL_ACCEPTANCE" else 1

if __name__ == "__main__":raise SystemExit(main())
