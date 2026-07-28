#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def partition(files: list[Path], group_count: int) -> list[list[Path]]:
    groups: list[list[Path]] = [[] for _ in range(group_count)]
    sizes = [0 for _ in range(group_count)]
    for path in sorted(files, key=lambda item: (-item.stat().st_size, item.as_posix())):
        index = min(range(group_count), key=lambda candidate: (sizes[candidate], candidate))
        groups[index].append(path)
        sizes[index] += path.stat().st_size
    return [sorted(group) for group in groups if group]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the complete Python suite in deterministic balanced groups")
    parser.add_argument("--groups", type=int, default=4, help="number of balanced pytest groups")
    parser.add_argument("--group", type=int, default=0, help="run only this 1-based group")
    parser.add_argument("--continue-on-failure", action="store_true", help="run later groups after a failed group")
    parser.add_argument("--group-timeout", type=int, default=300, help="seconds before a hung pytest group is terminated")
    parser.add_argument("--retries", type=int, default=1, help="retries for a timed-out pytest group")
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER, help="arguments passed after -- to each pytest process")
    args = parser.parse_args()
    if args.groups < 1:
        parser.error("--groups must be at least 1")
    extra = list(args.pytest_args)
    if extra and extra[0] == "--":
        extra = extra[1:]

    files = [path for path in (ROOT / "tests").rglob("test_*.py") if "__pycache__" not in path.parts]
    if not files:
        raise SystemExit("no pytest files found")
    groups = partition(files, min(args.groups, len(files)))
    if args.group and not 1 <= args.group <= len(groups):
        parser.error(f"--group must be between 1 and {len(groups)}")
    selected = [(args.group, groups[args.group - 1])] if args.group else list(enumerate(groups, 1))
    environment = {**os.environ, "PYTHONPATH": str(ROOT)}
    failed = 0
    for index, group in selected:
        relative = [str(path.relative_to(ROOT)) for path in group]
        print(f"=== pytest group {index}/{len(groups)} · {len(group)} files ===", flush=True)
        command = [sys.executable, "-m", "pytest", "-q", *extra, *relative]
        returncode = 1
        for attempt in range(args.retries + 1):
            process = subprocess.Popen(command, cwd=ROOT, env=environment)
            try:
                returncode = process.wait(timeout=max(1, args.group_timeout))
            except subprocess.TimeoutExpired:
                print(
                    f"pytest group {index} timed out after {args.group_timeout}s"
                    + (f"; retrying ({attempt + 1}/{args.retries})" if attempt < args.retries else ""),
                    file=sys.stderr, flush=True,
                )
                try:
                    process.terminate()
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                returncode = 124
                if attempt < args.retries:
                    continue
            break
        if returncode != 0:
            failed = returncode or 1
            if not args.continue_on_failure:
                return failed
    return failed


if __name__ == "__main__":
    raise SystemExit(main())
