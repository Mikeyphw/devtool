#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "docs" / "validation-release-scenarios.json"


def run(command: list[str], *, cwd: Path = ROOT) -> None:
    completed = subprocess.run(command, cwd=cwd, env={**os.environ, "PYTHONPATH": str(ROOT)}, check=False)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Phase 32–33 validation release seal")
    parser.add_argument("--require-android", action="store_true", help="Require Android SDK lint, unit, release APK, and instrumentation gates")
    parser.add_argument("--device-tests", action="store_true", help="Run connected Android instrumentation tests")
    args = parser.parse_args()

    payload = json.loads(MATRIX.read_text(encoding="utf-8"))
    scenarios = payload.get("scenarios", [])
    if len(scenarios) != 16 or len({item.get("id") for item in scenarios}) != 16:
        raise SystemExit("validation release matrix must contain 16 unique scenarios")
    nodeids: list[str] = []
    for item in scenarios:
        tests = item.get("automated_tests") or []
        if not item.get("expected_state") or not tests:
            raise SystemExit(f"scenario is incomplete: {item.get('id')}")
        nodeids.extend(str(value) for value in tests)

    run([sys.executable, "-m", "compileall", "-q", "devtool", "tools"])
    run([sys.executable, "-m", "pytest", "-q", *nodeids])
    run([
        sys.executable, "-m", "pytest", "-q",
        "tests/test_validation_reports_phase32.py",
        "tests/test_phase33_validation_release_seal.py",
        "tests/test_android_validation_workbench_phase29_31.py",
        "tests/test_phase31_1_validation_completion.py",
        "tests/test_android_notification_mock.py",
    ])
    qualification = [sys.executable, "tools/release-qualify.py", "--core-only"]
    if args.require_android:
        qualification = [sys.executable, "tools/release-qualify.py", "--require-android"]
        if args.device_tests:
            qualification.append("--device-tests")
    run(qualification)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
