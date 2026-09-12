from __future__ import annotations

"""Lightweight pytest profiling helpers for Devtool's own test suite.

The command intentionally delegates execution to pytest instead of importing
pytest internals.  That keeps it usable from Termux and from partially bootstrapped
checkouts while still making slow-test evidence easy to collect and compare.
"""

from dataclasses import asdict, dataclass
from pathlib import Path
import json
import os
import re
import subprocess
import sys
from typing import Iterable, Mapping, Sequence

_DURATION_RE = re.compile(r"^\s*(?P<seconds>\d+(?:\.\d+)?)s\s+(?:(?P<phase>setup|call|teardown)\s+)?(?P<nodeid>\S.*)$")


@dataclass(frozen=True, slots=True)
class PytestSlowEntry:
    seconds: float
    nodeid: str
    phase: str = "call"

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PytestProfileResult:
    command: tuple[str, ...]
    returncode: int
    threshold_seconds: float
    entries: tuple[PytestSlowEntry, ...]
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "ok": self.ok,
            "returncode": self.returncode,
            "threshold_seconds": self.threshold_seconds,
            "command": list(self.command),
            "slow_tests": [entry.as_dict() for entry in self.entries],
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


def parse_pytest_duration_output(text: str, *, threshold_seconds: float = 0.0, limit: int | None = None) -> tuple[PytestSlowEntry, ...]:
    """Parse pytest --durations output and Devtool's slow-test summary."""

    entries: list[PytestSlowEntry] = []
    seen: set[tuple[float, str, str]] = set()
    for line in text.splitlines():
        match = _DURATION_RE.match(line)
        if not match:
            continue
        nodeid = match.group("nodeid").strip()
        # Ignore section separators and pytest prose that happen to start with a number.
        if not nodeid or nodeid.startswith("="):
            continue
        seconds = float(match.group("seconds"))
        if seconds < threshold_seconds:
            continue
        phase = match.group("phase") or "call"
        key = (seconds, phase, nodeid)
        if key in seen:
            continue
        seen.add(key)
        entries.append(PytestSlowEntry(seconds=seconds, phase=phase, nodeid=nodeid))
    entries.sort(key=lambda item: item.seconds, reverse=True)
    if limit is not None and limit >= 0:
        entries = entries[:limit]
    return tuple(entries)


def build_pytest_profile_command(
    pytest_args: Sequence[str] | None = None,
    *,
    threshold_seconds: float = 2.0,
    top: int = 30,
    strict_slow: bool = False,
) -> list[str]:
    args = [str(item) for item in (pytest_args or ())]
    if not args:
        args = ["tests"]
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        *args,
        f"--devtool-slow-threshold={threshold_seconds:g}",
        f"--durations={max(0, int(top))}",
        f"--durations-min={threshold_seconds:g}",
    ]
    if strict_slow:
        command.append("--devtool-strict-slow")
    return command


def run_pytest_profile(
    repo_root: str | Path,
    pytest_args: Sequence[str] | None = None,
    *,
    threshold_seconds: float = 2.0,
    top: int = 30,
    strict_slow: bool = False,
    env: Mapping[str, str] | None = None,
) -> PytestProfileResult:
    repo = Path(repo_root).resolve()
    command = build_pytest_profile_command(
        pytest_args,
        threshold_seconds=threshold_seconds,
        top=top,
        strict_slow=strict_slow,
    )
    run_env = dict(os.environ if env is None else env)
    existing = run_env.get("PYTHONPATH", "")
    run_env["PYTHONPATH"] = str(repo) if not existing else str(repo) + os.pathsep + existing
    proc = subprocess.run(command, cwd=repo, env=run_env, text=True, capture_output=True, check=False)
    combined = "\n".join(part for part in (proc.stdout, proc.stderr) if part)
    entries = parse_pytest_duration_output(combined, threshold_seconds=threshold_seconds, limit=max(0, int(top)))
    return PytestProfileResult(
        command=tuple(command),
        returncode=int(proc.returncode),
        threshold_seconds=float(threshold_seconds),
        entries=entries,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


def render_pytest_profile_text(result: PytestProfileResult) -> str:
    lines = [
        f"pytest profile: {'PASS' if result.ok else 'FAIL'} (exit {result.returncode})",
        "command: " + " ".join(result.command),
        f"slow threshold: {result.threshold_seconds:g}s",
    ]
    if result.entries:
        lines.append("Top slow tests:")
        for entry in result.entries:
            lines.append(f"{entry.seconds:7.2f}s  {entry.phase:<8} {entry.nodeid}")
    else:
        lines.append("No tests exceeded the configured threshold.")
    if result.stderr.strip():
        lines.append("stderr:")
        lines.append(result.stderr.rstrip())
    return "\n".join(lines)


def render_pytest_profile_json(result: PytestProfileResult) -> str:
    return json.dumps(result.as_dict(), indent=2, ensure_ascii=False)


__all__ = [
    "PytestProfileResult",
    "PytestSlowEntry",
    "build_pytest_profile_command",
    "parse_pytest_duration_output",
    "render_pytest_profile_json",
    "render_pytest_profile_text",
    "run_pytest_profile",
]
