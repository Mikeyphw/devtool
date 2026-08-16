#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from devtool.release_qualification import qualify, repository_root, write_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Devtool release qualification gates.")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--output", type=Path, default=Path("dist/release-qualification.json"))
    parser.add_argument("--core-only", action="store_true")
    parser.add_argument("--require-qt", action="store_true", help="Deprecated compatibility alias for --require-desktop")
    parser.add_argument("--require-desktop", action="store_true", help="Require GTK4 desktop smoke/package gates")
    parser.add_argument("--require-android", action="store_true")
    parser.add_argument("--device-tests", action="store_true")
    parser.add_argument("--final", action="store_true", help="Write a content-hashed, write-once .final.json evidence report")
    args = parser.parse_args()

    root = (args.root or repository_root()).resolve()
    output = args.output if args.output.is_absolute() else root / args.output
    report = qualify(
        root,
        include_qt=not args.core_only,
        include_android=not args.core_only,
        require_qt=args.require_qt or args.require_desktop,
        require_android=args.require_android,
        run_device_tests=args.device_tests,
    )
    output = write_report(report, output, final=args.final)
    for result in report.results:
        print(f"{result.status.upper():7} {result.gate:24} {result.detail}")
    print(f"Report: {output}")
    print(f"Evidence SHA-256: {report.release_evidence_hash}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
