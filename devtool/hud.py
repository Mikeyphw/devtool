from __future__ import annotations

import argparse
import contextlib
import html
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .tasker import DEFAULT_NOTIFICATION_ID, DEFAULT_TASKER_DIR, read_active_hud_directory


DEFAULT_HUD_POLL_INTERVAL = 0.10
DEFAULT_RECENT_EVENTS = 8
DEFAULT_HUD_HOST = "127.0.0.1"
DEFAULT_HUD_PORT = 8765
DESKTOP_HUD_APP_ID = "net.mph.devtool.HUD"
DESKTOP_HUD_ICON_NAME = "devtool-hud"
DESKTOP_HUD_WM_CLASS = "devtool-hud"


def _is_termux_android() -> bool:
    """Return True when running in the Android Termux userspace."""
    prefix = os.environ.get("PREFIX", "")
    return bool(
        os.environ.get("TERMUX_VERSION")
        or "/com.termux/" in prefix
        or prefix.startswith("/data/data/com.termux/")
        or (os.environ.get("ANDROID_ROOT") and os.environ.get("ANDROID_DATA"))
    )


def default_android_bridge_dir() -> Path:
    """Return the shared HUD bridge directory visible to Termux and chroots.

    Direct Termux runs use the Termux home. Ubuntu chroots that expose the
    Termux home as /home/termux use that path. DEVTOOL_HUD_BRIDGE_DIR wins for
    custom bind mounts.
    """
    override = os.environ.get("DEVTOOL_HUD_BRIDGE_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    if _is_termux_android():
        return Path.home() / ".local" / "state" / "devtool-android-hud"
    termux_home = Path(os.environ.get("DEVTOOL_TERMUX_HOME", "/home/termux")).expanduser()
    if termux_home.exists():
        return termux_home / ".local" / "state" / "devtool-android-hud"
    return Path.home() / ".local" / "state" / "devtool-android-hud"


def _is_termux_chroot_bridge() -> bool:
    """Return True for a Linux/chroot process that can see Termux home."""
    if os.environ.get("DEVTOOL_ANDROID_BRIDGE", "").strip().lower() in {"0", "false", "no", "off"}:
        return False
    if _is_termux_android():
        return False
    if os.environ.get("DEVTOOL_HUD_BRIDGE_DIR", "").strip():
        return True
    return Path(os.environ.get("DEVTOOL_TERMUX_HOME", "/home/termux")).expanduser().exists()


def _atomic_write_bridge_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _android_argb(value: int) -> int:
    """Return a signed Android ARGB color integer."""
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value > 0x7FFFFFFF else value


@dataclass(slots=True)
class HudState:
    title: str = "devtool"
    text: str = "waiting for devtool progress"
    expanded_title: str = "devtool"
    expanded_text: str = "waiting for devtool progress"
    current: int | None = None
    total: int | None = None
    status: str = "idle"
    event_type: str = "idle"
    phase_name: str = ""
    command: str = ""
    repo_name: str = ""
    repo_root: str = ""
    target: str = ""
    updated_at: str = ""
    seq: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def has_progress(self) -> bool:
        return self.current is not None and self.total is not None and self.total > 0

    @property
    def percent(self) -> int | None:
        if not self.has_progress:
            return None
        assert self.current is not None and self.total is not None
        if self.total <= 0:
            return None
        return max(0, min(100, int(round(self.current / self.total * 100))))

    @property
    def display_title(self) -> str:
        if self.percent is None:
            return self.title
        return f"{self.title} ({self.percent}%)"

    @property
    def display_text(self) -> str:
        bits: list[str] = []
        if self.repo_name:
            bits.append(self.repo_name)
        phase = human_phase_name(self.phase_name or self.command) if (self.phase_name or self.command) else ""
        if phase:
            bits.append(phase)
        detail = human_detail(self.text or self.expanded_text)
        if detail and detail.lower() not in {phase.lower(), self.repo_name.lower()} :
            bits.append(detail)
        return " • ".join(bit for bit in bits if bit) or "Waiting for progress"

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "text": self.text,
            "expanded_title": self.expanded_title,
            "expanded_text": self.expanded_text,
            "current": self.current,
            "total": self.total,
            "percent": self.percent,
            "status": self.status,
            "event_type": self.event_type,
            "phase_name": self.phase_name,
            "command": self.command,
            "repo_name": self.repo_name,
            "repo_root": self.repo_root,
            "target": self.target,
            "updated_at": self.updated_at,
            "seq": self.seq,
            "display_title": self.display_title,
            "display_text": self.display_text,
            "human_status": human_status(self.status),
            "human_event_type": human_event_type(self.event_type),
            "human_phase_name": human_phase_name(self.phase_name),
        }


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _read_json_file(path: Path) -> dict[str, Any] | None:
    try:
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return None
        data = json.loads(text)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _tail_lines(path: Path, limit: int, *, block_size: int = 8192) -> list[str]:
    if limit <= 0:
        return []
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            end = fh.tell()
            if end <= 0:
                return []
            chunks: list[bytes] = []
            pos = end
            newlines = 0
            while pos > 0 and newlines <= limit:
                read_size = min(block_size, pos)
                pos -= read_size
                fh.seek(pos)
                chunk = fh.read(read_size)
                chunks.append(chunk)
                newlines += chunk.count(b"\n")
            data = b"".join(reversed(chunks))
    except OSError:
        return []
    return data.decode("utf-8", errors="replace").splitlines()[-limit:]


def _last_jsonl_events(path: Path, limit: int) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    events: list[dict[str, Any]] = []
    # Read a few extra lines because malformed/partial lines are skipped.
    for line in _tail_lines(path, max(limit * 3, limit)):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events[-limit:]


def _first_dict(*values: Any) -> dict[str, Any]:
    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def parse_hud_state(payload: dict[str, Any] | None) -> HudState:
    if not payload:
        return HudState()

    # notification.json uses simple top-level fields. latest_event/state.json use
    # a richer envelope. Accept both so HUD survives partial upgrades.
    notification = _first_dict(payload.get("tasker_notification"), payload.get("notification"), payload)
    progress = _first_dict(payload.get("progress"))
    phase = _first_dict(payload.get("phase"))

    current = _coerce_int(payload.get("current"))
    total = _coerce_int(payload.get("total"))
    if current is None:
        current = _coerce_int(notification.get("current"))
    if total is None:
        total = _coerce_int(notification.get("total"))
    if current is None:
        current = _coerce_int(progress.get("current") or progress.get("completed"))
    if total is None:
        total = _coerce_int(progress.get("total"))

    seq = _coerce_int(payload.get("seq") or notification.get("seq"))
    title = str(notification.get("title") or payload.get("title") or "devtool")
    text = str(notification.get("text") or notification.get("body") or payload.get("message") or payload.get("text") or "")
    expanded_title = str(notification.get("expanded_title") or title)
    expanded_text = str(notification.get("expanded_text") or notification.get("body") or text)

    target = str(payload.get("target") or progress.get("target") or notification.get("target") or "")

    return HudState(
        title=title,
        text=text,
        expanded_title=expanded_title,
        expanded_text=expanded_text,
        current=current,
        total=total,
        status=str(payload.get("status") or "running"),
        event_type=str(payload.get("event_type") or payload.get("type") or "progress"),
        phase_name=str(phase.get("name") or payload.get("phase_name") or ""),
        command=str(payload.get("command") or ""),
        repo_name=str(payload.get("repo_name") or ""),
        repo_root=str(payload.get("repo_root") or ""),
        target=target,
        updated_at=str(payload.get("updated_at") or payload.get("seq") or ""),
        seq=seq,
        raw=payload,
    )


def render_ascii_bar(current: int | None, total: int | None, *, width: int = 28) -> str:
    if current is None:
        return "░" * width
    if total is None or total <= 0:
        # Indeterminate but active: show movement/progress without pretending
        # we know the final denominator.
        filled = min(width, max(1, current % (width + 1)))
        return "█" * filled + "░" * (width - filled)
    current = max(0, min(current, total))
    filled = int(round((current / total) * width))
    return "█" * filled + "░" * (width - filled)




def human_phase_name(value: str) -> str:
    key = (value or "").strip().lower().replace("_", "-")
    mapping = {
        "restore": "Restore",
        "build": "Build",
        "test": "Tests",
        "validate": "Validation",
        "apply-overlay": "Apply overlay",
        "apply-snapshot": "Apply snapshot",
        "commit": "Commit",
        "started": "Started",
        "progress": "Progress",
        "phase-started": "Started",
        "phase_started": "Started",
        "phase-finished": "Finished",
        "phase_finished": "Finished",
        "finished": "Finished",
    }
    return mapping.get(key, (value or "devtool").strip().replace("-", " ").replace("_", " ").title())


def human_detail(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    replacements = {
        "waiting for devtool progress": "Waiting for progress",
        "restore: determining projects": "Finding projects to restore",
        "restore: all projects up-to-date": "All projects are already restored",
        "restore completed": "Restore completed",
        "build: msbuild starting": "Starting MSBuild",
        "test: starting execution": "Starting tests",
        "finished": "Finished",
        "failed": "Failed",
        "started": "Started",
    }
    if lowered in replacements:
        return replacements[lowered]
    for prefix, pretty in (("restored ", "Restored "), ("up-to-date ", "Already up to date: "), ("built ", "Built "), ("testing ", "Testing ")):
        if lowered.startswith(prefix):
            remainder = text[len(prefix):]
            if " -> " in remainder:
                remainder = remainder.split(" -> ", 1)[0]
            return pretty + remainder
    if lowered.startswith("tests:"):
        return text[0].upper() + text[1:]
    return text


def human_status(value: str) -> str:
    key = (value or "").strip().lower()
    return {
        "idle": "Waiting",
        "running": "Running",
        "ok": "Complete",
        "failed": "Failed",
        "skipped": "Skipped",
    }.get(key, human_phase_name(value))


def human_event_type(value: str) -> str:
    return human_phase_name(value)



def _coerce_str(value: Any) -> str:
    return str(value or "").strip()


def _progress_pair(event: dict[str, Any]) -> tuple[int | None, int | None]:
    progress = event.get("progress") if isinstance(event.get("progress"), dict) else {}
    current = _coerce_int(event.get("current"))
    total = _coerce_int(event.get("total"))
    if current is None:
        current = _coerce_int(progress.get("current") or progress.get("completed"))
    if total is None:
        total = _coerce_int(progress.get("total"))
    return current, total


def _result_status_from_event(event: dict[str, Any]) -> str:
    result = event.get("result") if isinstance(event.get("result"), dict) else {}
    return _coerce_str(result.get("status") or event.get("status"))


def _phase_from_event(event: dict[str, Any]) -> str:
    phase = event.get("phase") if isinstance(event.get("phase"), dict) else {}
    return _coerce_str(phase.get("name") or event.get("phase_name") or event.get("command"))


def _event_message(event: dict[str, Any]) -> str:
    progress = event.get("progress") if isinstance(event.get("progress"), dict) else {}
    return _coerce_str(event.get("message") or progress.get("detail") or event.get("text"))


def _is_noisy_test_line(text: str) -> bool:
    lower = text.strip().lower()
    if not lower:
        return False
    return (
        lower.startswith("a total of ")
        or " test files matched the specified pattern" in lower
        or lower.startswith("test run for ")
        or lower.startswith("starting test execution")
    )


_DEVTOOL_TEST_SUMMARY_RE = re.compile(r"(\d+)\s+passed,\s*(\d+)\s+failed,\s*(\d+)\s+skipped", re.I)
_VSTEST_RESULT_RE = re.compile(
    r"\b(?P<status>Passed|Failed)!\s+-\s+Failed:\s*(?P<failed>\d+),\s*Passed:\s*(?P<passed>\d+),\s*Skipped:\s*(?P<skipped>\d+)",
    re.I,
)
_TEST_RUN_SUCCESS_RE = re.compile(r"\bTest Run Successful\b", re.I)
_TEST_RUN_FAILED_RE = re.compile(r"\bTest Run Failed\b", re.I)


def _parse_test_counts(text: str) -> tuple[int, int, int] | None:
    """Return ``(passed, failed, skipped)`` for known .NET/devtool test summaries."""
    clean = text.strip()
    if not clean:
        return None
    match = _DEVTOOL_TEST_SUMMARY_RE.search(clean)
    if match:
        return tuple(map(int, match.groups()))  # type: ignore[return-value]
    match = _VSTEST_RESULT_RE.search(clean)
    if match:
        return (int(match.group("passed")), int(match.group("failed")), int(match.group("skipped")))
    return None


def _is_test_summary(text: str) -> bool:
    clean = text.strip()
    return bool(_parse_test_counts(clean) or _TEST_RUN_SUCCESS_RE.search(clean) or _TEST_RUN_FAILED_RE.search(clean))


def _parse_test_summary(text: str) -> str | None:
    counts = _parse_test_counts(text)
    if counts is None:
        if _TEST_RUN_SUCCESS_RE.search(text):
            return "Tests passing"
        if _TEST_RUN_FAILED_RE.search(text):
            return "Tests failing"
        return None
    passed, failed, skipped = counts
    parts = [f"{passed} passed"]
    if failed:
        parts.append(f"{failed} failed")
    if skipped:
        parts.append(f"{skipped} skipped")
    if failed:
        return "Tests failing: " + ", ".join(parts)
    return "Tests passing: " + ", ".join(parts)


def _test_summary_failed(text: str) -> bool:
    counts = _parse_test_counts(text)
    if counts is not None:
        return counts[1] > 0
    return bool(_TEST_RUN_FAILED_RE.search(text))


def friendly_event_line(event: dict[str, Any]) -> str | None:
    event_type = _coerce_str(event.get("event_type") or event.get("type") or "event").lower().replace("_", "-")
    phase = human_phase_name(_phase_from_event(event))
    message = _event_message(event)
    detail = human_detail(message)
    current, total = _progress_pair(event)
    status = _result_status_from_event(event).lower()

    # Hide low-value test runner boilerplate in the recent list.
    if event_type == "progress" and _is_noisy_test_line(message):
        return None

    if _is_test_summary(message):
        parsed = _parse_test_summary(message)
        if parsed:
            return parsed

    if event_type == "phase-started":
        return f"Starting {phase}" if phase else "Starting next step"

    if event_type == "phase-finished":
        if status in {"ok", "complete", "completed", "success"}:
            return f"{phase} completed" if phase else "Step completed"
        if status == "failed":
            return f"{phase} failed" if phase else "Step failed"
        if status == "skipped":
            return f"{phase} skipped" if phase else "Step skipped"
        return f"{phase}: {human_status(status)}" if phase else human_status(status)

    if event_type == "finished":
        if status in {"ok", "complete", "completed", "success"}:
            return "Devtool finished successfully"
        if status == "failed":
            return "Devtool finished with errors"
        return "Devtool finished"

    if event_type == "progress":
        if detail:
            # For progress lists, do not repeat 9/9 on every line unless it is the only useful info.
            return detail
        if current is not None and total is not None:
            if current <= total:
                return f"{phase}: {current}/{total}" if phase else f"Progress {current}/{total}"
            return f"{phase}: {current} done" if phase else f"{current} done"
        if current is not None:
            return f"{phase}: {current} done" if phase else f"{current} done"
        return f"{phase} is running" if phase else "Running"

    if detail and detail.lower() not in {event_type, phase.lower()}:
        return detail
    return human_event_type(event_type)


def event_is_error(event: dict[str, Any]) -> bool:
    message = _event_message(event).lower()
    # Test summaries must win over generic status/substring checks.  VSTest
    # success lines intentionally contain text like ``Failed: 0``.
    if _is_test_summary(message):
        return _test_summary_failed(message)
    status = _result_status_from_event(event).lower()
    if status in {"failed", "error"}:
        return True
    notification = event.get("notification") if isinstance(event.get("notification"), dict) else {}
    if str(notification.get("level") or "").lower() == "error":
        return True
    totals = event.get("totals") if isinstance(event.get("totals"), dict) else {}
    if _coerce_int(totals.get("errors")) or _coerce_int(totals.get("failed")):
        return True
    return bool(message and (" error " in f" {message} " or " failed" in message or message.startswith("failed")))


def recent_error_lines(events: Iterable[dict[str, Any]], *, limit: int = 3) -> list[str]:
    lines: list[str] = []
    for event in events:
        if not event_is_error(event):
            continue
        line = friendly_event_line(event)
        if line and line not in lines:
            lines.append(line)
    return lines[-limit:]


def event_is_warning(event: dict[str, Any]) -> bool:
    if event_is_error(event):
        return False
    notification = event.get("notification") if isinstance(event.get("notification"), dict) else {}
    if str(notification.get("level") or "").lower() == "warning":
        return True
    totals = event.get("totals") if isinstance(event.get("totals"), dict) else {}
    if _coerce_int(totals.get("warnings")):
        return True
    message = _event_message(event).lower()
    return bool(message and (" warning " in f" {message} " or " warning:" in message or " warning(" in message))


def event_severity(event: dict[str, Any]) -> str:
    if event_is_error(event):
        return "error"
    if event_is_warning(event):
        return "warning"
    status = _result_status_from_event(event).lower()
    message = _event_message(event)
    if status in {"ok", "complete", "completed", "success"}:
        return "success"
    if _is_test_summary(message) and not _test_summary_failed(message):
        return "success"
    event_type = _coerce_str(event.get("event_type") or event.get("type")).lower().replace("_", "-")
    if event_type in {"phase-finished", "finished"} and status != "failed":
        return "success"
    if event_type in {"phase-started", "progress"}:
        return "running"
    return "neutral"


def event_icon(event: dict[str, Any]) -> str:
    return {
        "error": "❌",
        "warning": "⚠",
        "success": "✓",
        "running": "→",
        "neutral": "•",
    }.get(event_severity(event), "•")


def hud_event_summary(events: Iterable[dict[str, Any]]) -> dict[str, int]:
    summary = {"errors": 0, "warnings": 0, "success": 0, "running": 0, "events": 0}
    for event in events:
        if friendly_event_line(event) is None:
            continue
        summary["events"] += 1
        severity = event_severity(event)
        if severity in summary:
            summary[severity] += 1
    return summary


def completion_from_state(state: HudState) -> dict[str, Any]:
    completion = state.raw.get("completion") if isinstance(state.raw, dict) else None
    return completion if isinstance(completion, dict) else {}


def completion_copy_text(state: HudState) -> str:
    completion = completion_from_state(state)
    if not completion:
        return ""
    lines = [str(completion.get("headline") or "Devtool completed") + "."]
    detail = str(completion.get("detail") or "").strip()
    if detail:
        lines.extend(("", detail))
    actions = completion.get("actions") if isinstance(completion.get("actions"), list) else []
    if actions:
        labels = {
            "apply-overlay": "Applied overlay",
            "apply-overlays": "Applied overlay bundle",
            "apply-snapshot": "Applied snapshot",
            "restore": "Restored dependencies",
            "build": "Built project",
            "test": "Ran test suite",
            "wine-test": "Ran Wine test suite",
            "git-commit": "Committed repository changes",
            "artifact-cleanup": "Cleaned applied artifact",
        }
        lines.extend(("", "Actions completed:"))
        for action in actions:
            if not isinstance(action, dict):
                continue
            name = str(action.get("name") or "step")
            label = labels.get(name, name.replace("-", " ").title())
            duration = action.get("duration_s")
            suffix = f" ({float(duration):.2f}s)" if isinstance(duration, (int, float)) else ""
            status = str(action.get("status") or "ok")
            lines.append(f"- {label}{suffix}" if status == "ok" else f"- {label}: {status}{suffix}")
    warning_details = completion.get("warning_details") if isinstance(completion.get("warning_details"), list) else []
    if warning_details:
        lines.extend(("", "Warnings:"))
        lines.extend(f"- {item}" for item in warning_details if item)
    commit_hash = str(completion.get("commit_hash") or "").strip()
    if commit_hash:
        lines.extend(("", f"Git commit: {commit_hash}"))
    return "\n".join(lines).strip()


def pipeline_label(state: HudState, highlights: dict[str, str]) -> str:
    phase_key = (state.phase_name or state.command or "").strip().lower().replace("_", "-")
    steps = [("validate", "Validate"), ("apply", "Apply"), ("test", "Test"), ("commit", "Commit")]
    if state.status == "ok":
        actions = completion_from_state(state).get("actions")
        action_names = {str(item.get("name") or "") for item in actions if isinstance(item, dict)} if isinstance(actions, list) else set()
        performed = {
            "apply": any("apply" in name for name in action_names),
            "test": any(name in {"test", "tests", "wine-test"} for name in action_names),
            "commit": any(name in {"git-commit", "commit"} for name in action_names),
            "validate": any(name in {"restore", "build", "test", "tests", "wine-test"} for name in action_names),
        }
        return " → ".join(f"✓ {label}" if performed.get(key) else label for key, label in steps)
    pieces: list[str] = []
    for key, label in steps:
        active = key in phase_key or (key == "apply" and "overlay" in phase_key)
        done = key in highlights and key != "validate"
        if active:
            pieces.append(f"● {label}")
        elif done:
            pieces.append(f"✓ {label}")
        else:
            pieces.append(label)
    return " → ".join(pieces)


def state_context_label(state: HudState) -> str:
    bits: list[str] = []
    if state.repo_name:
        bits.append(state.repo_name)
    elif state.repo_root:
        bits.append(Path(state.repo_root).name)
    if state.target:
        bits.append(state.target)
    return " · ".join(bits) if bits else "Waiting for target"


def state_metric_text(state: HudState, events: Iterable[dict[str, Any]]) -> str:
    completion = completion_from_state(state)
    if completion:
        return str(completion.get("detail") or completion.get("headline") or "Completed")
    summary = hud_event_summary(events)
    errors = summary.get("errors", 0)
    warnings = summary.get("warnings", 0)
    if state.status == "failed" and errors == 0:
        errors = 1
    parts = [f"{errors} errors", f"{warnings} warnings"]
    if state.percent is not None:
        parts.append(f"{state.percent}%")
    elif summary.get("events", 0):
        parts.append(f"{summary['events']} events")
    return " · ".join(parts)


def state_window_title(state: HudState) -> str:
    status_label = "Complete" if state.status == "ok" else human_status(state.status)
    pieces = ["Devtool HUD", status_label]
    target = state.target or state.repo_name or (Path(state.repo_root).name if state.repo_root else "")
    if target:
        pieces.append(target)
    return " — ".join(pieces)


def state_target_label(state: HudState) -> str:
    target = (state.target or "").strip()
    if target:
        return f"Target: {target}"
    repo = (state.repo_name or "").strip()
    if repo:
        return f"Target: {repo}"
    return "Target: waiting"


def compact_operation_title(state: HudState) -> str:
    """Return the one-line headline for the compact GTK HUD."""
    phase = human_phase_name(state.phase_name or state.command or state.event_type)
    status = (state.status or "").strip().lower()
    if status == "failed":
        return f"Failed during {phase}" if phase else "Devtool failed"
    if status == "ok":
        return str(completion_from_state(state).get("headline") or "Completed successfully")
    if phase and phase.lower() not in {"progress", "idle"}:
        return f"Finished {phase}" if state.event_type in {"phase_finished", "phase-finished"} else f"Running {phase}"
    title = human_detail(state.title)
    return title if title and title.lower() != "devtool" else "Devtool is running"


def compact_detail_text(state: HudState) -> str:
    completion = completion_from_state(state)
    if completion:
        return str(completion.get("detail") or "Completed")
    bits: list[str] = []
    detail = human_detail(state.text or state.expanded_text)
    if detail and detail.lower() not in {"devtool", "progress"}:
        bits.append(detail)
    if state.repo_name:
        bits.append(state.repo_name)
    if state.target:
        bits.append(state.target)
    return " • ".join(bits) or "Waiting for progress"


def _phase_highlight_from_event(event: dict[str, Any]) -> tuple[str, str] | None:
    phase_key = _phase_from_event(event).lower().replace("_", "-")
    key = {"rs":"restore", "restore":"restore", "b":"build", "build":"build", "t":"test", "test":"test", "tests":"test"}.get(phase_key)
    if not key:
        return None
    event_type = _coerce_str(event.get("event_type") or event.get("type")).lower().replace("_", "-")
    message = _event_message(event)
    if event_type == "progress" and _is_noisy_test_line(message):
        return None
    if _is_test_summary(message):
        parsed = _parse_test_summary(message)
        if parsed:
            return key, parsed
    detail = human_detail(message)
    if detail and detail.lower() not in {"started", "running", "progress"}:
        return key, detail
    if event_type == "phase-finished":
        status = _result_status_from_event(event).lower()
        if status in {"ok", "complete", "completed", "success"}:
            return key, f"{human_phase_name(key)} completed"
        if status == "failed":
            return key, f"{human_phase_name(key)} failed"
        if status == "skipped":
            return key, f"{human_phase_name(key)} skipped"
    return None


def last_phase_highlights(events: Iterable[dict[str, Any]]) -> dict[str, str]:
    """Return the last useful restore/build/test detail for the HUD."""
    highlights: dict[str, str] = {}
    for event in events:
        parsed = _phase_highlight_from_event(event)
        if parsed is not None:
            highlights[parsed[0]] = parsed[1]
    return highlights


def latest_log_path(events: Iterable[dict[str, Any]]) -> str:
    """Find the newest log_path embedded in Tasker/HUD events."""
    latest = ""
    for event in events:
        result = event.get("result") if isinstance(event.get("result"), dict) else {}
        if result.get("log_path"):
            latest = str(result.get("log_path"))
        results = event.get("results") if isinstance(event.get("results"), list) else []
        for item in results:
            if isinstance(item, dict) and item.get("log_path"):
                latest = str(item.get("log_path"))
    return latest

def _newest_existing(paths: list[Path]) -> Path | None:
    existing = []
    for path in paths:
        try:
            existing.append((path.stat().st_mtime_ns, path))
        except OSError:
            pass
    if not existing:
        return None
    return max(existing, key=lambda item: item[0])[1]


def resolve_hud_directory(directory: Path, *, follow_active: bool = False) -> Path:
    if not follow_active:
        return directory
    return read_active_hud_directory(directory) or directory


def snapshot_from_runtime_directory(directory: Path, *, follow_active: bool = False, recent_events: int = DEFAULT_RECENT_EVENTS) -> dict[str, Any]:
    current = resolve_hud_directory(directory, follow_active=follow_active)
    snapshot = snapshot_from_directory(current, recent_events=recent_events)
    snapshot["active_directory"] = str(current)
    snapshot["follow_active"] = bool(follow_active)
    snapshot["runs"] = _run_payloads(current)
    return snapshot


def _run_payloads(directory: Path) -> list[dict[str, Any]]:
    runs_dir = directory / "runs"
    payloads: list[dict[str, Any]] = []
    if not runs_dir.exists():
        return payloads
    for path in runs_dir.glob("*.json"):
        payload = _read_json_file(path)
        if not isinstance(payload, dict):
            continue
        pid = int(payload.get("pid", 0) or 0)
        if str(payload.get("status") or "") == "running" and pid > 0:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                with contextlib.suppress(OSError):
                    path.unlink(missing_ok=True)
                continue
            except PermissionError:
                pass
            except OSError:
                continue
        payloads.append(payload)
    payloads.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    return payloads


def _aggregate_active_runs(directory: Path) -> dict[str, Any] | None:
    active = [item for item in _run_payloads(directory) if str(item.get("status") or "") == "running"]
    if not active:
        return None
    if len(active) == 1:
        return active[0]
    lines: list[str] = []
    for item in active[:6]:
        phase = item.get("phase") if isinstance(item.get("phase"), dict) else {}
        phase_name = str(phase.get("name") or item.get("command") or "run")
        progress = item.get("progress") if isinstance(item.get("progress"), dict) else {}
        percent = progress.get("percent")
        suffix = f" {float(percent):.0f}%" if isinstance(percent, (int, float)) else ""
        repo = str(item.get("repo_name") or "repo")
        lines.append(f"{repo}: {phase_name}{suffix}")
    if len(active) > 6:
        lines.append(f"…and {len(active) - 6} more")
    newest = dict(active[0])
    newest.update({
        "title": f"Devtool — {len(active)} active runs",
        "text": "\n".join(lines),
        "expanded_title": f"Devtool — {len(active)} active runs",
        "expanded_text": "\n".join(lines),
        "current": None,
        "total": None,
        "target": "multiple concurrent tasks",
        "repo_name": f"{len(active)} active runs",
        "command": "concurrent",
        "phase": {"name": "concurrent", "index": 0, "total": len(active)},
        "progress": {"detail": "\n".join(lines), "percent": None},
        "notification": {
            "title": f"Devtool — {len(active)} active runs",
            "text": "\n".join(lines),
            "expanded_title": f"Devtool — {len(active)} active runs",
            "expanded_text": "\n".join(lines),
        },
        "active_runs": active,
    })
    return newest


def state_from_directory(directory: Path) -> HudState:
    aggregate = _aggregate_active_runs(directory)
    if aggregate is not None:
        return parse_hud_state(aggregate)
    # Keep notification.json as the simple primary state file, but fall back to
    # newer rich state/event files when notification.json is missing or clearly
    # stale.  The stale threshold avoids flicker in tests/tools that write a
    # static notification file and then append historical events immediately.
    notification_path = directory / "notification.json"
    notification = _read_json_file(notification_path)
    if notification is not None:
        try:
            notification_mtime = notification_path.stat().st_mtime_ns
            events_mtime = (directory / "events.jsonl").stat().st_mtime_ns
        except OSError:
            events_mtime = 0
            notification_mtime = 0
        if events_mtime <= notification_mtime + 250_000_000:
            # Preserve the richer repo/phase/target fields from state.json while
            # still letting notification.json win for title/text/progress.
            rich_payload = _read_json_file(directory / "state.json") or _read_json_file(directory / "latest_event.json") or {}
            if rich_payload:
                merged = dict(rich_payload)
                merged["notification"] = notification
                merged["tasker_notification"] = notification
                for key in ("title", "text", "expanded_title", "expanded_text", "current", "total", "target"):
                    if key in notification:
                        merged[key] = notification[key]
                return parse_hud_state(merged)
            return parse_hud_state(notification)

    candidates = [directory / "latest_event.json", directory / "state.json"]
    newest = _newest_existing(candidates)
    payload = _read_json_file(newest) if newest is not None else notification
    events_path = directory / "events.jsonl"
    try:
        events_newer = newest is None or events_path.stat().st_mtime_ns > newest.stat().st_mtime_ns
    except OSError:
        events_newer = False
    if events_newer:
        events = _last_jsonl_events(events_path, 1)
        if events:
            payload = events[-1]
    return parse_hud_state(payload)


def snapshot_from_directory(directory: Path, *, recent_events: int = DEFAULT_RECENT_EVENTS) -> dict[str, Any]:
    events = _last_jsonl_events(directory / "events.jsonl", recent_events)
    state = state_from_directory(directory)
    if state.raw == {} and events:
        state = parse_hud_state(events[-1])
    return {
        "version": 5,
        "state": state.to_dict(),
        "events": events,
        "paths": {
            "directory": str(directory),
            "notification": str(directory / "notification.json"),
            "events": str(directory / "events.jsonl"),
            "latest_event": str(directory / "latest_event.json"),
            "state": str(directory / "state.json"),
        },
        "generated_at": time.time(),
    }


def _event_summary(event: dict[str, Any]) -> tuple[str, str, Any, Any]:
    event_type = str(event.get("event_type") or event.get("type") or "event")
    progress = event.get("progress") if isinstance(event.get("progress"), dict) else {}
    message = str(event.get("message") or progress.get("detail") or event.get("text") or "")
    current = event.get("current")
    total = event.get("total")
    if current is None:
        current = progress.get("current") or progress.get("completed")
    if total is None:
        total = progress.get("total")
    return event_type, message, current, total


class TerminalHud:
    def __init__(self, *, console: Console | None = None, recent_events: int = DEFAULT_RECENT_EVENTS, title: str = "devtool HUD v3") -> None:
        self.console = console or Console()
        self.recent_events = recent_events
        self.title = title

    def render(self, state: HudState, events: list[dict[str, Any]]) -> Panel:
        table = Table.grid(expand=True)
        table.add_column(ratio=1)
        bar = render_ascii_bar(state.current, state.total, width=34)
        progress_text = "unknown"
        if state.current is not None and state.total is not None:
            progress_text = f"{state.current}/{state.total}"
            if state.percent is not None:
                progress_text += f"  {state.percent}%"
        title = Text(state.display_title, style="bold")
        detail = Text(state.display_text or "waiting")
        table.add_row(title)
        table.add_row(Text(f"{bar}  {progress_text}"))
        table.add_row(detail)
        summary_bits = [human_status(state.status)]
        if state.phase_name:
            summary_bits.append(human_phase_name(state.phase_name))
        elif state.event_type:
            summary_bits.append(human_event_type(state.event_type))
        if state.repo_name:
            summary_bits.append(state.repo_name)
        table.add_row(Text(" • ".join(bit for bit in summary_bits if bit), style="dim"))
        recent = events[-self.recent_events :]
        if recent:
            lines = [line for event in recent if (line := friendly_event_line(event))]
            if lines:
                table.add_row(Text("recent", style="bold dim"))
                for line in lines[-self.recent_events :]:
                    table.add_row(Text(f"• {line}"[:180], style="dim"))
        border_style = "green" if state.status in {"ok", "running", "idle"} else "red"
        return Panel(table, title=self.title, border_style=border_style)

    def run(self, *, directory: Path, interval_s: float, once: bool = False) -> int:
        events_path = directory / "events.jsonl"
        refresh = max(1, min(20, int(1 / max(interval_s, 0.05))))
        with Live(self.render(state_from_directory(directory), _last_jsonl_events(events_path, self.recent_events)), console=self.console, refresh_per_second=refresh) as live:
            while True:
                state = state_from_directory(directory)
                events = _last_jsonl_events(events_path, self.recent_events)
                live.update(self.render(state, events))
                if once:
                    return 0
                time.sleep(interval_s)


class TermuxNotificationHud:
    def __init__(self, *, notification_id: str = DEFAULT_NOTIFICATION_ID) -> None:
        self.notification_id = str(notification_id)
        self.last_signature: tuple[Any, ...] | None = None

    def _build_argv(self, state: HudState) -> list[str]:
        argv = [
            "termux-notification",
            "--id",
            self.notification_id,
            "--title",
            state.display_title,
            "--content",
            state.display_text,
            "--ongoing",
            "--priority",
            "high",
        ]
        # Termux:API builds have not always exposed the same progress argument
        # shape. Keep current/total visible in text by default and allow opt-in
        # to either known argument form via env.
        progress_mode = os.environ.get("DEVTOOL_TERMUX_NOTIFICATION_PROGRESS", "").strip().lower()
        if progress_mode and state.current is not None and state.total is not None:
            current = max(0, min(state.current, state.total)) if state.total > 0 else max(0, state.current)
            total = max(1, state.total or 1)
            if progress_mode in {"csv", "android"}:
                argv += ["--progress", f"{total},{current},false"]
            elif progress_mode in {"split", "legacy"}:
                argv += ["--progress", str(current), "--max", str(total)]
        return argv

    def update(self, state: HudState) -> None:
        signature = (state.display_title, state.display_text, state.current, state.total, state.status)
        if signature == self.last_signature:
            return
        self.last_signature = signature
        if not shutil.which("termux-notification"):
            return
        try:
            subprocess.run(self._build_argv(state), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        except OSError:
            return

    def run(self, *, directory: Path, interval_s: float, once: bool = False) -> int:
        while True:
            self.update(state_from_directory(directory))
            if once:
                return 0
            time.sleep(interval_s)


class TermuxGuiHud:
    """Android-native floating/dashboard HUD through the Termux:GUI plugin.

    Termux:GUI supports Android Activity overlays via Activity(..., overlay=True)
    and setposition().  Overlay mode requires the Termux:GUI Android app to
    have the system "Display over other apps" permission.  When overlay creation
    fails, this backend falls back to a normal Termux:GUI Activity unless the
    user explicitly disables fallback with DEVTOOL_ANDROID_GUI_FALLBACK=0.
    """

    def __init__(self, *, recent_events: int = DEFAULT_RECENT_EVENTS, overlay: bool = True, notification_id: str = DEFAULT_NOTIFICATION_ID) -> None:
        self.recent_events = recent_events
        self.overlay = overlay
        self.notification_id = str(notification_id)

    @staticmethod
    def available() -> bool:
        return importlib.util.find_spec("termuxgui") is not None

    @staticmethod
    def install_hint() -> str:
        return (
            "Termux:GUI HUD requires the Termux:GUI Android app and Python bindings. "
            "Install the app, then run: pip install termuxgui. "
            "For a floating HUD, enable Android Settings → Apps → Termux:GUI → Display over other apps."
        )

    @staticmethod
    def _safe_call(obj: Any, method: str, *args: Any) -> Any:
        fn = getattr(obj, method, None)
        if fn is None:
            return None
        with contextlib.suppress(Exception):
            return fn(*args)
        return None

    @staticmethod
    def _make_activity(tg: Any, connection: Any, *, overlay: bool) -> tuple[Any, bool, str]:
        fallback_enabled = os.environ.get("DEVTOOL_ANDROID_GUI_FALLBACK", "1").strip().lower() not in {"0", "false", "no"}
        if overlay:
            try:
                activity = tg.Activity(connection, overlay=True, intercept=True)
                with contextlib.suppress(Exception):
                    activity.sendoverlayevents(True)
                x = int(os.environ.get("DEVTOOL_ANDROID_HUD_X", "12"))
                y = int(os.environ.get("DEVTOOL_ANDROID_HUD_Y", "56"))
                with contextlib.suppress(Exception):
                    activity.setposition(x, y)
                return activity, True, "floating overlay"
            except Exception as exc:
                if not fallback_enabled:
                    raise RuntimeError(
                        "Could not start Termux:GUI overlay HUD. Enable Android 'Display over other apps' for Termux:GUI, "
                        "or set DEVTOOL_ANDROID_GUI_OVERLAY=0 to use a normal Activity."
                    ) from exc
                with contextlib.suppress(Exception):
                    connection.toast("Devtool HUD: overlay permission missing; using normal window", True)
        activity = tg.Activity(connection, intercept=True)
        return activity, False, "normal activity"

    def _format_recent_lines(self, events: list[dict[str, Any]]) -> str:
        pairs = [(event, line) for event in events if (line := friendly_event_line(event))]
        lines = [f"{event_icon(event)} {line}" for event, line in pairs[-self.recent_events :]]
        return "\n".join(lines) or "No events yet"

    def run(self, *, directory: Path, interval_s: float, once: bool = False, follow_active: bool = False) -> int:
        try:
            import termuxgui as tg  # type: ignore[import-not-found]
        except ModuleNotFoundError:
            raise RuntimeError(self.install_hint()) from None

        if once:
            snapshot = snapshot_from_runtime_directory(directory, follow_active=follow_active, recent_events=self.recent_events)
            print(json.dumps({"state": snapshot["state"], "events": snapshot["events"], "mode": "android-gui"}, ensure_ascii=False, indent=2))
            return 0

        overlay_requested = self.overlay and os.environ.get("DEVTOOL_ANDROID_GUI_OVERLAY", "1").strip().lower() not in {"0", "false", "no"}
        events_path = directory / "events.jsonl"
        poll_ms = max(100, int(interval_s * 1000))

        with tg.Connection() as connection:
            activity, overlay_active, presentation = self._make_activity(tg, connection, overlay=overlay_requested)
            with contextlib.suppress(Exception):
                activity.settaskdescription("Devtool HUD", "")
            with contextlib.suppress(Exception):
                activity.settheme(
                    _android_argb(0xFF0B0F14),
                    _android_argb(0xFF121923),
                    _android_argb(0xFF0B0F14),
                    _android_argb(0xFFE8EEF6),
                    _android_argb(0xFF5EE49B),
                )
            with contextlib.suppress(Exception):
                activity.keepscreenon(False)
            with contextlib.suppress(Exception):
                activity.interceptbackbutton(True)

            root = tg.LinearLayout(activity)
            self._safe_call(root, "setbackgroundcolor", _android_argb(0xFF0B0F14))
            self._safe_call(root, "setdimensions", 380 if overlay_active else getattr(tg.View, "MATCH_PARENT", "MATCH_PARENT"), getattr(tg.View, "WRAP_CONTENT", "WRAP_CONTENT"))
            self._safe_call(root, "setmargin", 8)

            header = tg.LinearLayout(activity, root, False)
            self._safe_call(header, "setlinearlayoutparams", 0)
            app_label = tg.TextView(activity, "DEVTOOL", header)
            status_badge = tg.TextView(activity, "WAITING", header)
            title = tg.TextView(activity, "Waiting for progress", root)
            context_label = tg.TextView(activity, "Waiting for target", root)
            detail = tg.TextView(activity, "No devtool task has reported progress yet.", root)
            error_label = tg.TextView(activity, "", root)
            warning_label = tg.TextView(activity, "", root)
            pipeline = tg.TextView(activity, "Validate → Apply → Test → Commit", root)
            progress_bar = tg.ProgressBar(activity, root) if hasattr(tg, "ProgressBar") else None
            progress_text = tg.TextView(activity, "waiting", root)
            metrics = tg.TextView(activity, "0 errors · 0 warnings", root)
            recent_heading = tg.TextView(activity, "RECENT", root)
            recent = tg.TextView(activity, "No events yet", root)
            footer = tg.TextView(activity, f"android-gui · {presentation}", root)

            labels = [app_label, status_badge, title, context_label, detail, error_label, warning_label, pipeline, progress_text, metrics, recent_heading, recent, footer]
            for widget in labels:
                self._safe_call(widget, "setheight", getattr(tg.View, "WRAP_CONTENT", "WRAP_CONTENT"))
                self._safe_call(widget, "setmargin", 3)
            for widget, size in ((app_label, 11), (status_badge, 11), (title, 20), (context_label, 14), (detail, 12), (error_label, 14), (warning_label, 13), (pipeline, 13), (progress_text, 13), (metrics, 16), (recent_heading, 11), (recent, 12), (footer, 10)):
                self._safe_call(widget, "settextsize", size)
            color_map = {
                app_label: 0xFF8EA0B4,
                status_badge: 0xFFFFD166,
                title: 0xFFF8FBFF,
                context_label: 0xFFCFE0F5,
                detail: 0xFF91A3B8,
                error_label: 0xFFFF6978,
                warning_label: 0xFFFFD166,
                pipeline: 0xFFD8E5F6,
                progress_text: 0xFF5EE49B,
                metrics: 0xFFF6F8FB,
                recent_heading: 0xFF8EA0B4,
                recent: 0xFFD6E2F0,
                footer: 0xFF64748B,
            }
            for widget, color in color_map.items():
                self._safe_call(widget, "settextcolor", _android_argb(color))
            for widget in (error_label, warning_label):
                self._safe_call(widget, "setvisibility", getattr(tg.View, "GONE", 0))
            if progress_bar is not None:
                self._safe_call(progress_bar, "setheight", 14)
                self._safe_call(progress_bar, "setprogress", 0)

            last_signature: tuple[Any, ...] | None = None
            running = True

            def pump_events() -> None:
                nonlocal running
                checkevent = getattr(connection, "checkevent", None)
                if checkevent is None:
                    return
                while True:
                    try:
                        event = checkevent()
                    except Exception:
                        return
                    if event is None:
                        return
                    event_type = str(getattr(event, "type", ""))
                    value = getattr(event, "value", {}) if isinstance(getattr(event, "value", {}), dict) else {}
                    if event_type in {getattr(tg.Event, "destroy", "destroy"), getattr(tg.Event, "back", "back")}:
                        if event_type != getattr(tg.Event, "destroy", "destroy") or value.get("finishing", True):
                            running = False
                            return

            def refresh() -> None:
                nonlocal last_signature
                current_dir = resolve_hud_directory(directory, follow_active=follow_active)
                current_events_path = current_dir / "events.jsonl"
                state = state_from_directory(current_dir)
                events = _last_jsonl_events(current_events_path, max(self.recent_events, 14))
                highlights = last_phase_highlights(events)
                errors = recent_error_lines(events, limit=2)
                if state.status == "failed" and not errors:
                    errors = [compact_detail_text(state)]
                warnings: list[str] = []
                for event in events:
                    if event_is_warning(event):
                        line = friendly_event_line(event)
                        if line and line not in warnings:
                            warnings.append(line)
                warnings = warnings[-2:]
                summary = hud_event_summary(events)
                error_count = max(summary.get("errors", 0), 1 if state.status == "failed" else 0)
                warning_count = summary.get("warnings", 0)
                progress_label = f"{state.current}/{state.total}  {state.percent or 0}%" if state.current is not None and state.total is not None and state.total > 0 else human_status(state.status).lower()
                if state.current is not None and (state.total is None or state.total <= 0):
                    progress_label = f"{state.current} done"
                signature = (state.seq, state.status, state.current, state.total, state.text, state.target, len(events), error_count, warning_count)
                if signature == last_signature:
                    return
                last_signature = signature
                status = human_status(state.status).upper()
                self._safe_call(status_badge, "settext", status)
                self._safe_call(title, "settext", compact_operation_title(state))
                self._safe_call(context_label, "settext", state_context_label(state))
                self._safe_call(detail, "settext", compact_detail_text(state))
                self._safe_call(pipeline, "settext", pipeline_label(state, highlights))
                self._safe_call(progress_text, "settext", progress_label)
                if progress_bar is not None:
                    self._safe_call(progress_bar, "setprogress", state.percent or 0)
                self._safe_call(metrics, "settext", f"{error_count} errors · {warning_count} warnings" + (f" · {state.percent}%" if state.percent is not None else ""))
                if errors:
                    self._safe_call(error_label, "settext", "❌ " + "\n❌ ".join(errors))
                    self._safe_call(error_label, "setvisibility", getattr(tg.View, "VISIBLE", 2))
                else:
                    self._safe_call(error_label, "setvisibility", getattr(tg.View, "GONE", 0))
                if warnings and not errors:
                    self._safe_call(warning_label, "settext", "⚠ " + "\n⚠ ".join(warnings))
                    self._safe_call(warning_label, "setvisibility", getattr(tg.View, "VISIBLE", 2))
                else:
                    self._safe_call(warning_label, "setvisibility", getattr(tg.View, "GONE", 0))
                self._safe_call(recent, "settext", self._format_recent_lines(events))
                self._safe_call(footer, "settext", f"android-gui · {presentation} · {current_dir}")

            while running:
                pump_events()
                refresh()
                time.sleep(poll_ms / 1000)
        return 0


class AndroidBridgeHud:
    """Chroot/proot-safe Android HUD bridge writer.

    This backend does not import Termux:GUI. It mirrors the current HUD state
    into a shared directory, normally /home/termux/.local/state/devtool-android-hud
    inside an Ubuntu chroot. A Termux host helper can then render that directory
    using the android-gui or android-notify backend.
    """

    def __init__(self, *, recent_events: int = DEFAULT_RECENT_EVENTS, bridge_dir: Path | None = None) -> None:
        self.recent_events = recent_events
        self.bridge_dir = (bridge_dir or default_android_bridge_dir()).expanduser()
        self.last_signature: tuple[Any, ...] | None = None

    def _mirror_once(self, source_dir: Path, *, follow_active: bool = False) -> dict[str, Any]:
        current_dir = resolve_hud_directory(source_dir, follow_active=follow_active)
        snapshot = snapshot_from_directory(current_dir, recent_events=max(self.recent_events, 1))
        snapshot["active_directory"] = str(current_dir)
        snapshot["bridge_directory"] = str(self.bridge_dir)
        snapshot["mode"] = "android-bridge"
        state = snapshot.get("state") if isinstance(snapshot.get("state"), dict) else {}
        events = snapshot.get("events") if isinstance(snapshot.get("events"), list) else []
        self.bridge_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_bridge_json(self.bridge_dir / "state.json", dict(state))
        notification = {
            "title": str(state.get("display_title") or state.get("title") or "devtool"),
            "text": str(state.get("display_text") or state.get("text") or "Waiting for progress"),
            "expanded_title": str(state.get("expanded_title") or state.get("title") or "devtool"),
            "expanded_text": str(state.get("expanded_text") or state.get("text") or "Waiting for progress"),
            "current": state.get("current"),
            "total": state.get("total"),
            "target": state.get("target") or "",
        }
        _atomic_write_bridge_json(self.bridge_dir / "notification.json", notification)
        if events:
            events_text = "\n".join(json.dumps(event, ensure_ascii=False) for event in events if isinstance(event, dict)) + "\n"
            (self.bridge_dir / "events.jsonl").write_text(events_text, encoding="utf-8")
            if isinstance(events[-1], dict):
                _atomic_write_bridge_json(self.bridge_dir / "latest_event.json", events[-1])
        _atomic_write_bridge_json(
            self.bridge_dir / "bridge-status.json",
            {
                "mode": "android-bridge",
                "source_directory": str(current_dir),
                "bridge_directory": str(self.bridge_dir),
                "updated_at": time.time(),
                "pid": os.getpid(),
            },
        )
        return snapshot

    def run(self, *, directory: Path, interval_s: float, once: bool = False, follow_active: bool = False) -> int:
        while True:
            snapshot = self._mirror_once(directory, follow_active=follow_active)
            signature = (
                snapshot.get("generated_at"),
                (snapshot.get("state") or {}).get("seq") if isinstance(snapshot.get("state"), dict) else None,
            )
            if once:
                print(json.dumps(snapshot, ensure_ascii=False, indent=2))
                return 0
            if signature != self.last_signature:
                self.last_signature = signature
            time.sleep(interval_s)



class DesktopNotificationHud:
    """Unix desktop notification HUD using notify-send/libnotify.

    GNOME Shell can show the text reliably. Some notification daemons also
    honor the `value` hint as a progress bar; when they do not, the title still
    includes current/total and percent.
    """

    def __init__(self, *, notification_id: str = DEFAULT_NOTIFICATION_ID) -> None:
        self.notification_id = str(notification_id)
        self.last_signature: tuple[Any, ...] | None = None

    @staticmethod
    def available() -> bool:
        return shutil.which("notify-send") is not None

    def _build_argv(self, state: HudState) -> list[str]:
        percent = state.percent or 0
        argv = [
            "notify-send",
            "--app-name=devtool",
            "--urgency=normal",
            "--expire-time=1000",
            "--hint=string:x-canonical-private-synchronous:devtool-hud",
            f"--hint=int:value:{percent}",
            state.display_title,
            state.display_text,
        ]
        return argv

    def update(self, state: HudState) -> None:
        signature = (state.display_title, state.display_text, state.current, state.total, state.status)
        if signature == self.last_signature:
            return
        self.last_signature = signature
        if not self.available():
            return
        try:
            subprocess.run(self._build_argv(state), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        except OSError:
            return

    def run(self, *, directory: Path, interval_s: float, once: bool = False, follow_active: bool = False) -> int:
        while True:
            current_dir = resolve_hud_directory(directory, follow_active=follow_active)
            self.update(state_from_directory(current_dir))
            if once:
                return 0
            time.sleep(interval_s)


def _xdg_data_home() -> Path:
    raw = os.environ.get("XDG_DATA_HOME", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".local" / "share"


def _install_desktop_hud_identity() -> Path | None:
    """Install a lightweight desktop/icon identity so GNOME can group the HUD."""
    data_home = _xdg_data_home()
    icon_path = data_home / "icons" / "hicolor" / "scalable" / "apps" / f"{DESKTOP_HUD_ICON_NAME}.svg"
    desktop_path = data_home / "applications" / f"{DESKTOP_HUD_WM_CLASS}.desktop"
    appid_desktop_path = data_home / "applications" / f"{DESKTOP_HUD_APP_ID}.desktop"
    icon_svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">
  <rect width="128" height="128" rx="28" fill="#111827"/>
  <path d="M24 78h80" stroke="#5ee49b" stroke-width="12" stroke-linecap="round"/>
  <path d="M24 52h48" stroke="#68d8ff" stroke-width="12" stroke-linecap="round"/>
  <circle cx="94" cy="52" r="10" fill="#ffd166"/>
</svg>
"""
    desktop_text = f"""[Desktop Entry]
Type=Application
Name=Devtool HUD
Comment=Live devtool progress HUD
Exec=devtool hud --mode desktop-window --follow-active
Icon={DESKTOP_HUD_ICON_NAME}
Terminal=false
NoDisplay=true
Categories=Development;Utility;
StartupWMClass={DESKTOP_HUD_WM_CLASS}
"""
    try:
        icon_path.parent.mkdir(parents=True, exist_ok=True)
        desktop_path.parent.mkdir(parents=True, exist_ok=True)
        if not icon_path.exists() or icon_path.read_text(encoding="utf-8", errors="ignore") != icon_svg:
            icon_path.write_text(icon_svg, encoding="utf-8")
        for path in (desktop_path, appid_desktop_path):
            if not path.exists() or path.read_text(encoding="utf-8", errors="ignore") != desktop_text:
                path.write_text(desktop_text, encoding="utf-8")
        return icon_path
    except OSError:
        return None


class GtkDesktopHud:
    """Compact GTK floating desktop HUD for GNOME/Unix desktops."""

    def __init__(self, *, recent_events: int = DEFAULT_RECENT_EVENTS) -> None:
        self.recent_events = recent_events

    @staticmethod
    def _require_gtk3() -> Any:
        import gi  # type: ignore[import-not-found]

        # Pin every GTK namespace before importing from gi.repository.
        # On GNOME systems with GTK 4 installed, importing Gdk without this
        # can load Gdk 4 first; Gtk 3 then crashes with:
        # "Requiring namespace 'Gdk' version '3.0', but '4.0' is already loaded".
        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
        gi.require_version("Pango", "1.0")
        from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # type: ignore[import-not-found]

        return Gdk, Gio, GLib, Gtk, Pango

    @staticmethod
    def available() -> bool:
        if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            return False
        if importlib.util.find_spec("gi") is None:
            return False
        try:
            GtkDesktopHud._require_gtk3()
        except Exception:
            return False
        return True

    def run(self, *, directory: Path, interval_s: float, once: bool = False, follow_active: bool = False) -> int:
        if once:
            snapshot = snapshot_from_runtime_directory(directory, follow_active=follow_active, recent_events=self.recent_events)
            print(json.dumps({"state": snapshot["state"], "events": snapshot["events"], "phase_highlights": last_phase_highlights(snapshot["events"])}, ensure_ascii=False, indent=2))
            return 0
        try:
            Gdk, Gio, GLib, Gtk, Pango = self._require_gtk3()
        except Exception as exc:
            raise RuntimeError("GTK HUD requires PyGObject/GTK 3. On Ubuntu: sudo apt install python3-gi gir1.2-gtk-3.0") from exc

        icon_path = _install_desktop_hud_identity()
        with contextlib.suppress(Exception):
            GLib.set_application_name("Devtool HUD")
        with contextlib.suppress(Exception):
            GLib.set_prgname(DESKTOP_HUD_WM_CLASS)
        with contextlib.suppress(Exception):
            Gdk.set_program_class(DESKTOP_HUD_WM_CLASS)
        with contextlib.suppress(Exception):
            Gtk.Window.set_default_icon_name(DESKTOP_HUD_ICON_NAME)

        window = Gtk.Window(title="Devtool HUD")
        window.set_name(DESKTOP_HUD_WM_CLASS)
        window.set_default_size(520, 460)
        window.set_keep_above(True)
        window.set_resizable(True)
        with contextlib.suppress(Exception):
            window.set_wmclass(DESKTOP_HUD_WM_CLASS, DESKTOP_HUD_WM_CLASS)
        with contextlib.suppress(Exception):
            window.set_role(DESKTOP_HUD_WM_CLASS)
        with contextlib.suppress(Exception):
            window.set_icon_name(DESKTOP_HUD_ICON_NAME)
        if icon_path is not None:
            with contextlib.suppress(Exception):
                window.set_icon_from_file(str(icon_path))
        window.connect("destroy", Gtk.main_quit)

        def _move_window_top_right() -> bool:
            """Place the desktop HUD near the top-right of the active monitor."""
            try:
                margin = int(os.environ.get("DEVTOOL_DESKTOP_HUD_MARGIN", "24"))
            except ValueError:
                margin = 24
            with contextlib.suppress(Exception):
                screen = window.get_screen()
                gdk_window = window.get_window()
                if gdk_window is not None:
                    monitor = screen.get_monitor_at_window(gdk_window)
                else:
                    monitor = screen.get_primary_monitor()
                geometry = screen.get_monitor_workarea(monitor)
                width, _height = window.get_size()
                x = geometry.x + geometry.width - width - margin
                y = geometry.y + margin
                window.move(max(geometry.x, x), max(geometry.y, y))
            return False

        window.connect("realize", lambda *_args: GLib.idle_add(_move_window_top_right))

        css = b"""
        .hud-root { background: #0b0f14; color: #eef4ff; }
        .hud-card { background: #121923; border-radius: 18px; padding: 14px; }
        .hud-header { color: #8ea0b4; font-size: 11px; letter-spacing: 0.08em; font-weight: 800; }
        .hud-badge { color: #10151d; background: #ffd166; border-radius: 999px; padding: 4px 9px; font-size: 11px; font-weight: 900; }
        .hud-title { color: #f8fbff; font-size: 21px; font-weight: 900; }
        .hud-context { color: #cfe0f5; font-size: 14px; font-weight: 700; }
        .hud-muted { color: #91a3b8; font-size: 12px; }
        .hud-pipeline { color: #d8e5f6; font-size: 13px; font-weight: 700; }
        .hud-metric { color: #f6f8fb; font-size: 17px; font-weight: 900; }
        .hud-metric-label { color: #8ea0b4; font-size: 10px; letter-spacing: 0.05em; font-weight: 800; }
        .hud-event { color: #d6e2f0; font-size: 13px; font-weight: 600; }
        .hud-error-banner { color: #ffffff; background: #3b1219; border-radius: 14px; padding: 10px; font-size: 14px; font-weight: 900; }
        .hud-warning-banner { color: #fff4c2; background: #30240c; border-radius: 14px; padding: 10px; font-size: 13px; font-weight: 800; }
        .hud-debug { color: #8ea0b4; font-family: monospace; font-size: 11px; }
        .hud-ok { color: #5ee49b; }
        .hud-failed { color: #ff6978; font-weight: 900; }
        .hud-running { color: #ffd166; font-weight: 800; }
        .hud-neutral { color: #91a3b8; }
        progressbar.hud-progress trough { min-height: 14px; border-radius: 999px; background: #071018; }
        progressbar.hud-progress progress { min-height: 14px; border-radius: 999px; background: #ffd166; }
        progressbar.hud-progress-ok progress { background: #5ee49b; }
        progressbar.hud-progress-failed progress { background: #ff6978; }
        progressbar.hud-progress-running progress { background: #ffd166; }
        progressbar.hud-progress-neutral progress { background: #415064; }
        button { border-radius: 10px; padding: 5px 9px; background: #1b2634; color: #e8eef6; }
        expander { color: #91a3b8; }
        """
        provider = Gtk.CssProvider(); provider.load_from_data(css)
        screen = Gdk.Screen.get_default()
        if screen is not None:
            Gtk.StyleContext.add_provider_for_screen(screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        def _label(text: str = "", css_class: str = "", *, xalign: float = 0.0, wrap: bool = False, selectable: bool = False) -> Any:
            label = Gtk.Label(label=text)
            label.set_xalign(xalign)
            label.set_yalign(0)
            label.set_line_wrap(wrap)
            label.set_selectable(selectable)
            if css_class:
                label.get_style_context().add_class(css_class)
            return label

        window.set_default_size(500, 430)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.get_style_context().add_class("hud-root")
        outer.set_margin_top(10); outer.set_margin_bottom(10); outer.set_margin_start(10); outer.set_margin_end(10)
        window.add(outer)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9)
        card.get_style_context().add_class("hud-card")
        outer.pack_start(card, True, True, 0)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8); card.pack_start(header, False, False, 0)
        header_label = _label("DEVTOOL", "hud-header"); header.pack_start(header_label, True, True, 0)
        status_badge = _label("WAITING", "hud-badge", xalign=1.0); header.pack_end(status_badge, False, False, 0)

        title = _label("Waiting for progress", "hud-title", wrap=True); card.pack_start(title, False, False, 0)
        context_label = _label("Waiting for target", "hud-context"); context_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE); card.pack_start(context_label, False, False, 0)
        detail = _label("No devtool task has reported progress yet.", "hud-muted", wrap=True); card.pack_start(detail, False, False, 0)

        error_label = _label("", "hud-error-banner", wrap=True); error_label.set_no_show_all(True); card.pack_start(error_label, False, False, 0)
        warning_label = _label("", "hud-warning-banner", wrap=True); warning_label.set_no_show_all(True); card.pack_start(warning_label, False, False, 0)

        pipeline = _label("Validate → Apply → Test → Commit", "hud-pipeline"); pipeline.set_ellipsize(Pango.EllipsizeMode.END); card.pack_start(pipeline, False, False, 0)
        progress = Gtk.ProgressBar(); progress.set_show_text(True); progress.get_style_context().add_class("hud-progress"); card.pack_start(progress, False, False, 0)

        metrics = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12); card.pack_start(metrics, False, False, 0)
        error_metric = _label("0\nERRORS", "hud-metric", wrap=True)
        warning_metric = _label("0\nWARNINGS", "hud-metric", wrap=True)
        progress_metric = _label("WAITING\nSTATUS", "hud-metric", wrap=True)
        for metric in (error_metric, warning_metric, progress_metric):
            metric.set_justify(Gtk.Justification.CENTER)
            metrics.pack_start(metric, True, True, 0)

        recent_heading = _label("RECENT", "hud-header"); card.pack_start(recent_heading, False, False, 0)
        events_scroller = Gtk.ScrolledWindow(); events_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC); events_scroller.set_min_content_height(118)
        events_label = _label("No events yet", "hud-event", wrap=True, selectable=True); events_scroller.add(events_label); card.pack_start(events_scroller, True, True, 0)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6); card.pack_start(actions, False, False, 0)
        copy_button = Gtk.Button(label="Copy summary"); log_button = Gtk.Button(label="Open log"); close_button = Gtk.Button(label="Close")
        actions.pack_start(copy_button, False, False, 0); actions.pack_start(log_button, False, False, 0); actions.pack_end(close_button, False, False, 0)
        debug = Gtk.Expander(label="Details"); debug.set_expanded(False)
        debug_label = _label("", "hud-debug", wrap=True, selectable=True); debug.add(debug_label); card.pack_start(debug, False, False, 0)
        last_summary = {"text": "Devtool HUD: waiting", "log_path": ""}

        def _copy_summary(_button: Any) -> None:
            clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD); clipboard.set_text(str(last_summary.get("text") or ""), -1); clipboard.store()
        def _open_log(_button: Any) -> None:
            log_path = str(last_summary.get("log_path") or "")
            if log_path:
                with contextlib.suppress(Exception):
                    subprocess.Popen(["xdg-open", log_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        copy_button.connect("clicked", _copy_summary); log_button.connect("clicked", _open_log); close_button.connect("clicked", lambda _button: Gtk.main_quit())

        def _set_status_classes(widget: Any, status: str) -> None:
            ctx = widget.get_style_context()
            for cls in ("hud-ok", "hud-failed", "hud-running", "hud-neutral"):
                ctx.remove_class(cls)
            if status == "failed":
                ctx.add_class("hud-failed")
            elif status == "ok":
                ctx.add_class("hud-ok")
            elif status == "running":
                ctx.add_class("hud-running")
            else:
                ctx.add_class("hud-neutral")

        def _set_progress_classes(status: str) -> None:
            ctx = progress.get_style_context()
            for cls in ("hud-progress-ok", "hud-progress-failed", "hud-progress-running", "hud-progress-neutral"):
                ctx.remove_class(cls)
            if status == "ok":
                ctx.add_class("hud-progress-ok")
            elif status == "failed":
                ctx.add_class("hud-progress-failed")
            elif status == "running":
                ctx.add_class("hud-progress-running")
            else:
                ctx.add_class("hud-progress-neutral")

        def refresh() -> bool:
            current_dir = resolve_hud_directory(directory, follow_active=follow_active)
            snapshot = snapshot_from_directory(current_dir, recent_events=max(self.recent_events, 14))
            state = state_from_directory(current_dir)
            events = snapshot.get("events") if isinstance(snapshot.get("events"), list) else []
            highlights = last_phase_highlights(events)
            operation = compact_operation_title(state)
            errors = recent_error_lines(events, limit=3)
            if state.status == "failed" and not errors:
                errors = [compact_detail_text(state)]
            warnings = []
            for event in events:
                if event_is_warning(event):
                    line = friendly_event_line(event)
                    if line and line not in warnings:
                        warnings.append(line)
            warnings = warnings[-2:]
            summary = hud_event_summary(events)
            error_count = max(summary.get("errors", 0), 1 if state.status == "failed" else 0)
            warning_count = summary.get("warnings", 0)

            title.set_text(operation)
            context_label.set_text(state_context_label(state))
            detail.set_text(compact_detail_text(state))
            window.set_title(state_window_title(state))
            status_badge.set_text(human_status(state.status).upper())
            _set_status_classes(status_badge, state.status)
            pipeline.set_text(pipeline_label(state, highlights))

            completion = completion_from_state(state)
            recent_heading.set_text("RESULT" if state.status in {"ok", "failed"} else "RECENT")

            if state.current is not None and state.total is not None and state.total > 0:
                fraction = max(0.0, min(1.0, state.current / state.total))
                if state.status == "ok":
                    progress.set_fraction(1.0)
                    duration = completion.get("duration_s")
                    progress.set_text(f"Complete · {float(duration):.1f}s" if isinstance(duration, (int, float)) else "Complete")
                elif state.status == "failed":
                    progress.set_fraction(1.0)
                    progress.set_text("failed")
                else:
                    progress.set_fraction(fraction)
                    progress.set_text(f"{state.current}/{state.total}  {state.percent or 0}%")
            elif state.status == "ok":
                progress.set_fraction(1.0)
                duration = completion.get("duration_s")
                progress.set_text(f"Complete · {float(duration):.1f}s" if isinstance(duration, (int, float)) else "Complete")
            elif state.status == "failed":
                progress.set_fraction(1.0)
                progress.set_text("failed")
            elif state.current is not None:
                progress.pulse()
                progress.set_text(f"{state.current} done")
            else:
                progress.pulse()
                progress.set_text(human_status(state.status).lower())
            _set_progress_classes(state.status)

            if state.status == "ok" and completion:
                tests = completion.get("tests") if isinstance(completion.get("tests"), dict) else {}
                test_total = int(tests.get("total", 0) or 0)
                duration = completion.get("duration_s")
                error_metric.set_text(f"{test_total}\nTESTS" if test_total else "PASS\nRESULT")
                warning_metric.set_text(f"{int(completion.get('warnings', warning_count) or 0)}\nWARNINGS")
                progress_metric.set_text(f"{float(duration):.1f}s\nDURATION" if isinstance(duration, (int, float)) else "100%\nCOMPLETE")
                _set_status_classes(error_metric, "ok")
                _set_status_classes(warning_metric, "running" if int(completion.get('warnings', 0) or 0) else "ok")
                _set_status_classes(progress_metric, "ok")
            else:
                error_metric.set_text(f"{error_count}\nERRORS")
                warning_metric.set_text(f"{warning_count}\nWARNINGS")
                if state.status == "failed":
                    progress_metric.set_text("FAILED\nSTATUS")
                else:
                    progress_metric.set_text((f"{state.percent}%\nPROGRESS" if state.percent is not None else f"{human_status(state.status).upper()}\nSTATUS"))
                _set_status_classes(error_metric, "failed" if error_count else "ok")
                _set_status_classes(warning_metric, "running" if warning_count else "ok")
                _set_status_classes(progress_metric, state.status)

            if errors:
                error_label.set_text("❌ " + "\n❌ ".join(errors))
                error_label.show()
            else:
                error_label.hide()
            if warnings and not errors:
                warning_label.set_text("⚠ " + "\n⚠ ".join(warnings))
                warning_label.show()
            else:
                warning_label.hide()

            event_pairs = [(event, line) for event in events if (line := friendly_event_line(event))]
            shown = event_pairs[-7:]
            formatted_lines = [f"{event_icon(event)} {line}" for event, line in shown]
            events_label.set_text("\n".join(formatted_lines) or "No events yet")

            phase_lines = []
            for key in ("restore", "build", "test"):
                if key in highlights:
                    phase_lines.append(f"{human_phase_name(key)}: {highlights[key]}")
            meta_lines = []
            if state.phase_name:
                meta_lines.append(f"Phase: {human_phase_name(state.phase_name)}")
            elif state.command:
                meta_lines.append(f"Command: {human_phase_name(state.command)}")
            if state.repo_name:
                meta_lines.append(f"Repo: {state.repo_name}")
            if state.target:
                meta_lines.append(f"Target: {state.target}")
            if follow_active:
                meta_lines.append("Following active HUD")

            log_path = latest_log_path(events)
            log_button.set_sensitive(bool(log_path) and shutil.which("xdg-open") is not None)
            summary_lines = [
                operation,
                state_context_label(state),
                compact_detail_text(state),
                state_metric_text(state, events),
                pipeline.get_text(),
                *(errors or []),
                *(warnings or []),
                *phase_lines,
                *[line for _event, line in shown],
            ]
            final_copy = completion_copy_text(state)
            last_summary["text"] = final_copy or "\n".join(line for line in summary_lines if line)
            last_summary["log_path"] = log_path or ""
            debug_label.set_text("\n".join([f"active_dir={current_dir}", f"state={current_dir / 'state.json'}", f"events={current_dir / 'events.jsonl'}", f"seq={state.seq if state.seq is not None else '-'} status={state.status} event={state.event_type}", *meta_lines, f"log={log_path or '-'}"]))
            return True
        GLib.timeout_add(max(100, int(interval_s * 1000)), refresh); refresh(); window.show_all(); Gtk.main(); return 0


DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>devtool HUD v5</title>
<style>
:root{color-scheme:dark;--bg:#0b0f14;--card:#121923;--muted:#8ea0b4;--text:#e8eef6;--line:#243244;--ok:#5ee49b;--bad:#ff6978;--warn:#ffd166}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;padding:16px}.wrap{max-width:980px;margin:0 auto;display:grid;gap:14px}.card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:16px;box-shadow:0 16px 40px rgba(0,0,0,.25)}.top{display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap}.title{font-size:22px;font-weight:800}.pill{font-size:12px;color:var(--muted);border:1px solid var(--line);border-radius:999px;padding:4px 9px}.bar{height:18px;background:#071018;border:1px solid var(--line);border-radius:999px;overflow:hidden}.fill{height:100%;width:0;background:linear-gradient(90deg,var(--ok),#68d8ff);transition:width .18s ease}.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}.kv{background:#0c131c;border:1px solid var(--line);border-radius:14px;padding:10px}.k{color:var(--muted);font-size:12px}.v{font-weight:700;overflow-wrap:anywhere}.events{display:grid;gap:8px;max-height:50vh;overflow:auto}.event{border:1px solid var(--line);border-radius:14px;padding:10px;background:#0c131c}.event .meta{color:var(--muted);font-size:12px;margin-bottom:4px}pre{white-space:pre-wrap;word-break:break-word;margin:0;color:#cfe3ff}.bad{color:var(--bad)}.ok{color:var(--ok)}@media(max-width:720px){body{padding:10px}.grid{grid-template-columns:repeat(2,minmax(0,1fr))}.title{font-size:18px}}
</style>
</head>
<body>
<div class="wrap">
  <section class="card top"><div><div class="title" id="title">devtool HUD</div><div id="text" class="pill">waiting</div></div><div class="pill" id="connected">connecting</div></section>
  <section class="card"><div class="bar"><div class="fill" id="fill"></div></div><p><b id="pct">0%</b> <span id="count" class="pill">-/-</span></p></section>
  <section class="grid"><div class="kv"><div class="k">State</div><div class="v" id="status">idle</div></div><div class="kv"><div class="k">Update</div><div class="v" id="event">idle</div></div><div class="kv"><div class="k">Phase</div><div class="v" id="phase">-</div></div><div class="kv"><div class="k">Project</div><div class="v" id="repo">-</div></div></section>
  <section class="card"><h3>Recent events</h3><div id="events" class="events"></div></section>
</div>
<script>
const $=id=>document.getElementById(id);
function esc(s){return String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function eventMsg(e){const p=e.progress||{}; return e.message || p.detail || e.text || '';}
function render(data){const s=data.state||{}; const pct=s.percent ?? 0; $('title').textContent=s.display_title||s.title||'devtool HUD'; $('text').textContent=s.display_text||s.text||'waiting'; $('fill').style.width=Math.max(0,Math.min(100,pct))+'%'; $('pct').textContent=pct+'%'; $('count').textContent=(s.current ?? '-')+'/'+(s.total ?? '-'); $('status').textContent=s.human_status||s.status||'Waiting'; $('event').textContent=s.human_event_type||s.event_type||'Update'; $('phase').textContent=s.human_phase_name||s.phase_name||'-'; $('repo').textContent=s.repo_name||'-'; const events=(data.events||[]).slice().reverse(); $('events').innerHTML=events.map(e=>`<div class="event"><div class="meta">${esc(e.event_type||e.type||'event')} ${esc(e.current??((e.progress||{}).completed)??'')}/${esc(e.total??((e.progress||{}).total)??'')}</div><pre>${esc(eventMsg(e))}</pre></div>`).join('')||'<div class="pill">No events yet</div>';}
async function poll(){try{const r=await fetch('/api/state',{cache:'no-store'}); render(await r.json()); $('connected').textContent='connected'; $('connected').className='pill ok';}catch(e){$('connected').textContent='offline'; $('connected').className='pill bad';}}
if (!!window.EventSource){const es=new EventSource('/stream'); es.onmessage=ev=>{render(JSON.parse(ev.data)); $('connected').textContent='live'; $('connected').className='pill ok';}; es.onerror=()=>{ $('connected').textContent='reconnecting'; $('connected').className='pill';};} else {setInterval(poll,500);} poll();
</script>
</body>
</html>
"""


class WebDashboardHud:
    def __init__(self, *, host: str = DEFAULT_HUD_HOST, port: int = DEFAULT_HUD_PORT, recent_events: int = DEFAULT_RECENT_EVENTS, interval_s: float = DEFAULT_HUD_POLL_INTERVAL, follow_active: bool = False) -> None:
        self.host = host
        self.port = int(port)
        self.recent_events = recent_events
        self.interval_s = interval_s
        self.follow_active = follow_active
        self.httpd: ThreadingHTTPServer | None = None

    @staticmethod
    def _json_response(handler: BaseHTTPRequestHandler, payload: Any, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)

    def _make_handler(self, directory: Path) -> type[BaseHTTPRequestHandler]:
        dashboard = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "devtool-hud/5"

            def log_message(self, fmt: str, *args: Any) -> None:
                if os.environ.get("DEVTOOL_HUD_HTTP_LOG"):
                    super().log_message(fmt, *args)

            def do_GET(self) -> None:  # noqa: N802 - http.server API
                path = self.path.split("?", 1)[0]
                if path in {"/", "/index.html"}:
                    data = DASHBOARD_HTML.encode("utf-8")
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if path in {"/api/state", "/state.json"}:
                    WebDashboardHud._json_response(self, snapshot_from_runtime_directory(directory, follow_active=dashboard.follow_active, recent_events=dashboard.recent_events))
                    return
                if path in {"/api/events"}:
                    WebDashboardHud._json_response(self, _last_jsonl_events(resolve_hud_directory(directory, follow_active=dashboard.follow_active) / "events.jsonl", dashboard.recent_events))
                    return
                if path == "/events.jsonl":
                    text = "\n".join(_tail_lines(resolve_hud_directory(directory, follow_active=dashboard.follow_active) / "events.jsonl", dashboard.recent_events)) + "\n"
                    data = text.encode("utf-8")
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if path == "/health":
                    WebDashboardHud._json_response(self, {"ok": True})
                    return
                if path == "/stream":
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Connection", "keep-alive")
                    self.end_headers()
                    last_signature = ""
                    while True:
                        snapshot = snapshot_from_runtime_directory(directory, follow_active=dashboard.follow_active, recent_events=dashboard.recent_events)
                        signature = json.dumps(snapshot.get("state", {}), sort_keys=True, separators=(",", ":"))
                        if signature != last_signature:
                            payload = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
                            try:
                                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                                self.wfile.flush()
                            except (BrokenPipeError, ConnectionResetError, OSError):
                                return
                            last_signature = signature
                        time.sleep(max(0.1, dashboard.interval_s))
                    return
                self.send_error(HTTPStatus.NOT_FOUND)

        return Handler

    def serve_forever(self, *, directory: Path) -> None:
        handler = self._make_handler(directory)
        self.httpd = ThreadingHTTPServer((self.host, self.port), handler)
        self.httpd.serve_forever(poll_interval=max(0.1, self.interval_s))

    def start_background(self, *, directory: Path) -> tuple[ThreadingHTTPServer, threading.Thread]:
        handler = self._make_handler(directory)
        httpd = ThreadingHTTPServer((self.host, self.port), handler)
        self.httpd = httpd
        thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": max(0.1, self.interval_s)}, daemon=True)
        thread.start()
        return httpd, thread

    def run(self, *, directory: Path, once: bool = False, console: Console | None = None) -> int:
        console = console or Console()
        if once:
            snapshot = snapshot_from_directory(directory, recent_events=self.recent_events)
            print(json.dumps(snapshot, ensure_ascii=False, indent=2))
            return 0
        console.print(f"[green]devtool HUD v4/v5 web dashboard[/green] listening on http://{self.host}:{self.port}")
        self.serve_forever(directory=directory)
        return 0


class StudioHud:
    """HUD v5: web dashboard + local console + optional notification mirroring."""

    def __init__(self, *, host: str, port: int, recent_events: int, notification_id: str, notify: bool) -> None:
        self.host = host
        self.port = port
        self.recent_events = recent_events
        self.notification_id = notification_id
        self.notify = notify

    def run(self, *, directory: Path, interval_s: float, once: bool = False, console: Console | None = None, follow_active: bool = False) -> int:
        console = console or Console()
        web = WebDashboardHud(host=self.host, port=self.port, recent_events=self.recent_events, interval_s=interval_s, follow_active=follow_active)
        if once:
            snapshot = snapshot_from_runtime_directory(directory, follow_active=follow_active, recent_events=self.recent_events)
            print(json.dumps(snapshot, ensure_ascii=False, indent=2))
            return 0
        httpd, _thread = web.start_background(directory=directory)
        host, port = httpd.server_address[:2]
        console.print(f"[green]devtool HUD v5 studio[/green] web=http://{host}:{port} dir={directory}")
        notifier = TermuxNotificationHud(notification_id=self.notification_id) if self.notify else None
        terminal = TerminalHud(console=console, recent_events=self.recent_events, title="devtool HUD v5 studio")
        refresh = max(1, min(20, int(1 / max(interval_s, 0.05))))
        current_dir = resolve_hud_directory(directory, follow_active=follow_active)
        with Live(terminal.render(state_from_directory(current_dir), _last_jsonl_events(current_dir / "events.jsonl", self.recent_events)), console=console, refresh_per_second=refresh) as live:
            while True:
                current_dir = resolve_hud_directory(directory, follow_active=follow_active)
                state = state_from_directory(current_dir)
                events = _last_jsonl_events(current_dir / "events.jsonl", self.recent_events)
                live.update(terminal.render(state, events))
                if notifier is not None:
                    notifier.update(state)
                time.sleep(interval_s)


def choose_hud_mode(requested: str) -> str:
    requested = requested.lower().strip()
    aliases = {
        "v3": "terminal",
        "console": "terminal",
        "rich": "terminal",
        "v4": "web",
        "server": "web",
        "http": "web",
        "dashboard": "web",
        "v5": "studio",
        "all": "studio",
        "v6": "desktop",
        "unix": "desktop",
        "gnome": "desktop",
        "desktop-gtk": "desktop-window",
        "gtk": "desktop-window",
        "notify-send": "desktop-notify",
        "libnotify": "desktop-notify",
        "termux-gui": "android-gui",
        "termuxgui": "android-gui",
        "android": "android-gui",
        "android-overlay": "android-gui",
        "android-chroot": "android-bridge",
        "termux-bridge": "android-bridge",
        "termux-chroot": "android-bridge",
        "android-hud-bridge": "android-bridge",
        "termux-notification": "android-notify",
        "termux-notify": "android-notify",
        "android-notification": "android-notify",
        "notification": "android-notify" if _is_termux_android() else "notification",
    }
    requested = aliases.get(requested, requested)
    if requested == "desktop":
        if _is_termux_android():
            if TermuxGuiHud.available():
                return "android-gui"
            if shutil.which("termux-notification"):
                return "android-notify"
        if _is_termux_chroot_bridge():
            return "android-bridge"
        if GtkDesktopHud.available():
            return "desktop-window"
        if DesktopNotificationHud.available():
            return "desktop-notify"
        return "terminal"
    if requested != "auto":
        return requested
    if _is_termux_android():
        if TermuxGuiHud.available():
            return "android-gui"
        if shutil.which("termux-notification"):
            return "android-notify"
    if _is_termux_chroot_bridge():
        return "android-bridge"

    # Prefer native Termux:GUI whenever it is available, even if a desktop
    # DISPLAY/WAYLAND_DISPLAY exists. This keeps Android/Termux auto-HUD from
    # being stolen by GTK/X11 and preserves the documented priority:
    # android-gui → android-notify → desktop-window → desktop-notify → terminal.
    if TermuxGuiHud.available():
        return "android-gui"
    if shutil.which("termux-notification"):
        return "android-notify"

    if os.name == "posix" and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        if GtkDesktopHud.available():
            return "desktop-window"
        if DesktopNotificationHud.available():
            return "desktop-notify"
    return "terminal"


def build_hud_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="devtool hud")
    parser.add_argument("--tasker-dir", default=DEFAULT_TASKER_DIR, help="Directory containing devtool HUD/Tasker JSON files (default: platform state dir)")
    parser.add_argument(
        "--mode",
        choices=["auto", "android-gui", "android-bridge", "android-notify", "termux-gui", "terminal", "notification", "web", "studio", "desktop", "desktop-window", "desktop-notify", "v3", "v4", "v5", "v6", "console", "dashboard", "all", "unix", "gnome"],
        default="auto",
        help="HUD renderer: android-gui/android-bridge, terminal/v3, web/v4, studio/v5, or Unix desktop/v6",
    )
    parser.add_argument("--host", default=DEFAULT_HUD_HOST, help="Host/IP for HUD web/studio modes")
    parser.add_argument("--port", type=int, default=DEFAULT_HUD_PORT, help="Port for HUD web/studio modes")
    parser.add_argument("--poll-interval", type=float, default=0.1, help="Seconds between HUD refreshes")
    parser.add_argument("--recent-events", type=int, default=DEFAULT_RECENT_EVENTS, help="Recent JSONL events to show/serve")
    parser.add_argument("--notify-id", default=DEFAULT_NOTIFICATION_ID, help="Notification id for notification/studio modes")
    parser.add_argument("--studio-notify", action="store_true", help="In studio/v5 mode, also mirror state into termux-notification when available")
    parser.add_argument("--once", action="store_true", help="Render/update once and exit")
    parser.add_argument("--follow-active", action="store_true", help="Follow the global active HUD directory written by auto-HUD")
    parser.add_argument("--no-overlay", action="store_true", help="For android-gui, use a normal Activity instead of a floating overlay")
    return parser


def run_hud(args: argparse.Namespace) -> int:
    directory = Path(args.tasker_dir).expanduser()
    mode = choose_hud_mode(str(args.mode))
    interval_s = max(0.05, float(args.poll_interval))
    recent_events = max(0, int(args.recent_events))
    follow_active = bool(getattr(args, "follow_active", False))
    if mode == "android-gui":
        use_overlay = not bool(getattr(args, "no_overlay", False))
        return TermuxGuiHud(recent_events=recent_events, overlay=use_overlay, notification_id=str(args.notify_id)).run(directory=directory, interval_s=interval_s, once=bool(args.once), follow_active=follow_active)
    if mode == "android-bridge":
        return AndroidBridgeHud(recent_events=recent_events).run(directory=directory, interval_s=interval_s, once=bool(args.once), follow_active=follow_active)
    if mode in {"android-notify", "notification"}:
        return TermuxNotificationHud(notification_id=str(args.notify_id)).run(directory=directory, interval_s=interval_s, once=bool(args.once))
    if mode == "web":
        return WebDashboardHud(host=str(args.host), port=int(args.port), recent_events=recent_events, interval_s=interval_s, follow_active=follow_active).run(directory=directory, once=bool(args.once))
    if mode == "studio":
        return StudioHud(host=str(args.host), port=int(args.port), recent_events=recent_events, notification_id=str(args.notify_id), notify=bool(args.studio_notify)).run(directory=directory, interval_s=interval_s, once=bool(args.once), follow_active=follow_active)
    if mode == "desktop-window":
        return GtkDesktopHud(recent_events=recent_events).run(directory=directory, interval_s=interval_s, once=bool(args.once), follow_active=follow_active)
    if mode == "desktop-notify":
        return DesktopNotificationHud(notification_id=str(args.notify_id)).run(directory=directory, interval_s=interval_s, once=bool(args.once), follow_active=follow_active)
    return TerminalHud(recent_events=recent_events).run(directory=directory, interval_s=interval_s, once=bool(args.once))


def main(argv: list[str] | None = None) -> int:
    parser = build_hud_parser()
    args = parser.parse_args(argv)
    return run_hud(args)


if __name__ == "__main__":
    raise SystemExit(main())
