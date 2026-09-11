from __future__ import annotations

import argparse
import contextlib
import html
import json
import os
import re
import shlex
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .tasker import DEFAULT_NOTIFICATION_ID, DEFAULT_TASKER_DIR, hud_disabled_by_environment, read_active_hud_directory
from .android_live_hud import AndroidLiveHudBridge
from .ui_core import UI_SCHEMA_VERSION
from .ui_core.viewmodels import runtime_state_to_ui_snapshot


DEFAULT_HUD_POLL_INTERVAL = 0.10
DEFAULT_RECENT_EVENTS = 8
DEFAULT_HUD_HOST = "127.0.0.1"
DEFAULT_HUD_PORT = 8765
DESKTOP_HUD_APP_ID = "net.mph.devtool.HUD"
DESKTOP_HUD_ICON_NAME = "devtool-hud"
DESKTOP_HUD_WM_CLASS = "devtool-hud"
DEFAULT_DESKTOP_HUD_COMPACT_SIZE = (520, 330)
DEFAULT_DESKTOP_HUD_EXPANDED_SIZE = (560, 520)
DEFAULT_RUN_HEARTBEAT_TIMEOUT_S = 30.0


def desktop_hud_starts_expanded() -> bool:
    """Return whether the GTK HUD should bypass its compact initial state.

    Compact mode is the default.  The environment override is intentionally
    opt-in so launchers and older workflows can request the full dashboard
    without adding another CLI flag.
    """
    return os.environ.get("DEVTOOL_DESKTOP_HUD_START_EXPANDED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _process_start_token(pid: int) -> str:
    if pid <= 0:
        return ""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace").split()
        return fields[21] if len(fields) > 21 else ""
    except OSError:
        return ""


def _iso_age_seconds(value: object) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds())
    except ValueError:
        return None


def _running_payload_is_stale(payload: dict[str, Any]) -> bool:
    if str(payload.get("status") or "") != "running":
        return False
    payload_boot = str(payload.get("boot_id") or "").strip()
    current_boot = _boot_id()
    if payload_boot and current_boot and payload_boot != current_boot:
        return True
    pid = int(payload.get("pid", 0) or 0)
    expected_start = str(payload.get("process_start") or "").strip()
    if pid <= 0:
        # Legacy HUD records did not include process identity. Keep them
        # readable for compatibility; all newly written records include PID,
        # process start token, boot ID, and heartbeat metadata.
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        pass
    except OSError:
        return True
    current_start = _process_start_token(pid)
    if expected_start and current_start and expected_start != current_start:
        return True
    heartbeat_age = _iso_age_seconds(payload.get("heartbeat_at") or payload.get("updated_at"))
    timeout = float(os.environ.get("DEVTOOL_HUD_RUN_HEARTBEAT_TIMEOUT", DEFAULT_RUN_HEARTBEAT_TIMEOUT_S))
    # New-format records include process_start/boot_id and are heartbeated even
    # during quiet phases. Expire them if the identity cannot be verified and
    # their heartbeat has stopped. Older records keep the PID-only behavior.
    if heartbeat_age is not None and heartbeat_age > max(5.0, timeout):
        if not expected_start or not current_start or (payload_boot and not current_boot):
            return True
    return False


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

    Direct Termux runs use the Termux home. Chroot/PRoot runs must receive an
    explicit DEVTOOL_HUD_BRIDGE_DIR (or DEVTOOL_TERMUX_HOME for compatibility)
    from their execution planner; no guest home path is guessed.
    """
    override = os.environ.get("DEVTOOL_HUD_BRIDGE_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    if _is_termux_android():
        return Path.home() / ".local" / "state" / "devtool-android-hud"
    termux_home_raw = os.environ.get("DEVTOOL_TERMUX_HOME", "").strip()
    if termux_home_raw:
        termux_home = Path(termux_home_raw).expanduser()
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
    termux_home_raw = os.environ.get("DEVTOOL_TERMUX_HOME", "").strip()
    return bool(termux_home_raw and Path(termux_home_raw).expanduser().exists())


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


def _payload_progress_score(payload: dict[str, Any] | None) -> float:
    if not isinstance(payload, dict):
        return -1.0
    progress = _first_dict(payload.get("progress"))
    current = _coerce_int(payload.get("current"))
    total = _coerce_int(payload.get("total"))
    if current is None:
        current = _coerce_int(progress.get("current") or progress.get("completed"))
    if total is None:
        total = _coerce_int(progress.get("total"))
    percent = _coerce_int(payload.get("percent"))
    if percent is None:
        percent = _coerce_int(progress.get("percent"))
    if percent is None and current is not None and total is not None and total > 0:
        percent = int(round(current / total * 100))
    if percent is not None:
        return float(max(0, min(100, percent)))
    if current is not None:
        return float(max(0, current))
    return -1.0


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

    seq = _coerce_int(payload.get("sequence") or payload.get("seq") or notification.get("sequence") or notification.get("seq"))
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
        "dotnet-validation": ".NET validation",
        "android-validation": "Android validation",
        "gradle-validation": "Gradle validation",
        "validation-deferred": "Validation deferred",
        "validation-paused": "Validation paused",
        "validation-session-complete": "Validation complete",
        "apply-overlay": "Apply overlay",
        "apply-snapshot": "Apply snapshot",
        "apply-rollback": "Rollback",
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


def _validation_phase_counter(event: dict[str, Any]) -> str:
    """Return a human phase counter for resumable validation events."""

    phase = event.get("phase") if isinstance(event.get("phase"), dict) else {}
    result = event.get("result") if isinstance(event.get("result"), dict) else {}
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    index = _coerce_int(phase.get("index") or metadata.get("validation_phase_index"))
    total = _coerce_int(phase.get("total") or metadata.get("validation_phase_total"))
    if index is None or total is None or index <= 0 or total <= 0:
        return ""
    return f"Phase {index} of {total} · "


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
        counter = _validation_phase_counter(event)
        return f"{counter}Starting {phase}" if phase else f"{counter}Starting next step"

    if event_type == "phase-finished":
        counter = _validation_phase_counter(event)
        if _phase_from_event(event).strip().lower().replace("_", "-") == "apply-rollback":
            if status in {"ok", "complete", "completed", "success"}:
                return "Changes rolled back"
            if status == "failed":
                return "Rollback failed"
        if status in {"ok", "complete", "completed", "success"}:
            return f"{counter}{phase} completed" if phase else f"{counter}Step completed"
        if status == "failed":
            return f"{counter}{phase} failed" if phase else f"{counter}Step failed"
        if status == "skipped":
            return f"{counter}{phase} skipped" if phase else f"{counter}Step skipped"
        return f"{counter}{phase}: {human_status(status)}" if phase else f"{counter}{human_status(status)}"

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
    phase_key = _phase_from_event(event).strip().lower().replace("_", "-")
    if phase_key == "apply-rollback" and status in {"ok", "complete", "completed", "success"}:
        return False
    if status in {"failed", "error"}:
        return True
    notification = event.get("notification") if isinstance(event.get("notification"), dict) else {}
    if str(notification.get("level") or "").lower() == "error":
        return True
    totals = event.get("totals") if isinstance(event.get("totals"), dict) else {}
    if _coerce_int(totals.get("errors")) or _coerce_int(totals.get("failed")):
        return True
    return bool(message and (" error " in f" {message} " or " failed" in message or message.startswith("failed")))


def recent_error_lines(events: Iterable[dict[str, Any]], *, limit: int = 2) -> list[str]:
    lines: list[str] = []
    generic: list[str] = []
    for event in events:
        if not event_is_error(event):
            continue
        line = friendly_event_line(event)
        if not line:
            continue
        if line in {"Devtool finished with errors", "Step failed"}:
            if line not in generic:
                generic.append(line)
            continue
        if line not in lines:
            lines.append(line)
    selected = lines or generic
    return selected[-limit:]


def events_for_state(events: Iterable[dict[str, Any]], state: HudState) -> list[dict[str, Any]]:
    """Return only events belonging to the state currently shown by the HUD.

    HUD directories keep a compatibility event log across runs.  Without this
    filter, a fresh run can inherit warning/error banners and metrics from the
    previous completion until enough new events have been appended.
    """
    items = list(events)
    run_id = str(state.raw.get("run_id") or "").strip() if isinstance(state.raw, dict) else ""
    if not run_id:
        return items
    return [event for event in items if str(event.get("run_id") or "").strip() == run_id]


def concise_event_pairs(events: Iterable[dict[str, Any]], *, limit: int = 5) -> list[tuple[dict[str, Any], str]]:
    """Collapse duplicate and generic HUD lines into a short recent timeline."""
    pairs: list[tuple[dict[str, Any], str]] = []
    seen: set[str] = set()
    for event in events:
        line = friendly_event_line(event)
        if not line:
            continue
        if line == "Devtool finished with errors" and any(event_is_error(item) for item, _ in pairs):
            continue
        key = line.casefold().strip()
        if key in seen:
            continue
        seen.add(key)
        pairs.append((event, line))
    return pairs[-max(1, limit):]


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
    overlays = completion.get("overlays") if isinstance(completion.get("overlays"), list) else []
    if overlays:
        lines.extend(("", "Applied overlay:" if len(overlays) == 1 else "Applied overlays:"))
        for overlay in overlays:
            if not isinstance(overlay, dict):
                continue
            label = str(overlay.get("label") or "Overlay").strip()
            target = str(overlay.get("target") or "").strip()
            artifact = str(overlay.get("artifact") or "").strip()
            suffix = f" [{target}]" if target else ""
            if artifact and artifact != label:
                lines.append(f"- {label}{suffix} — {artifact}")
            else:
                lines.append(f"- {label}{suffix}")
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
            "validation-deferred": "Created deferred validation checkpoint",
            "validation-paused": "Paused validation at checkpoint",
            "validation-session-complete": "Completed staged validation",
            "apply-rollback": "Rolled back changes",
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
    diagnostics = completion.get("diagnostics") if isinstance(completion.get("diagnostics"), list) else []
    error_details = [
        str(item.get("detail") or item.get("message") or "").strip()
        for item in diagnostics
        if isinstance(item, dict) and str(item.get("severity") or "").lower() == "error"
    ]
    if error_details:
        lines.extend(("", "Errors:"))
        lines.extend(f"- {item}" for item in error_details if item)
    warning_details = completion.get("warning_details") if isinstance(completion.get("warning_details"), list) else []
    if warning_details:
        lines.extend(("", "Warnings:"))
        lines.extend(f"- {item}" for item in warning_details if item)
    commit_hash = str(completion.get("commit_hash") or "").strip()
    if commit_hash:
        lines.extend(("", f"Git commit: {commit_hash}"))
    return "\n".join(lines).strip()




@dataclass(slots=True)
class HudDiagnostic:
    severity: str
    message: str
    code: str = ""
    phase: str = ""
    file: str = ""
    source: str = "runtime"

    def to_dict(self) -> dict[str, str]:
        return {
            "severity": self.severity,
            "message": self.message,
            "code": self.code,
            "phase": self.phase,
            "file": self.file,
            "source": self.source,
        }


def _diagnostic_severity(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace("_", "-")
    if normalized in {"error", "failed", "failure", "fatal"}:
        return "error"
    if normalized in {"warning", "warn"}:
        return "warning"
    if normalized in {"info", "notice", "debug"}:
        return "info"
    return "info"


def _diagnostic_message(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    return str(value.get("detail") or value.get("message") or value.get("text") or value.get("summary") or "").strip()


def canonical_diagnostics(events: Iterable[dict[str, Any]], state: HudState) -> list[dict[str, str]]:
    """Return deduplicated canonical diagnostics for desktop and HUD surfaces.

    The state snapshot is the primary source of truth because runners can emit
    exact diagnostic objects and totals there.  Events are used as a fallback for
    live compiler/progress lines.  Parser-only summary observations are not
    mixed into this list unless no structured diagnostic exists for the same
    message, preventing the HUD from showing one count in the header and another
    in the details pane.
    """
    collected: list[HudDiagnostic] = []
    seen: set[tuple[str, str, str, str, str]] = set()

    def append(raw: Any, *, phase: str = "", source: str = "runtime") -> None:
        if not isinstance(raw, dict):
            return
        message = _diagnostic_message(raw)
        if not message:
            return
        severity = _diagnostic_severity(raw.get("severity") or raw.get("level") or raw.get("type"))
        if severity == "info" and source == "event":
            # Plain progress events are not diagnostics.  They stay in the raw
            # log and parser-observation counters.
            return
        item = HudDiagnostic(
            severity=severity,
            message=message,
            code=str(raw.get("code") or ""),
            phase=str(raw.get("phase") or phase or ""),
            file=str(raw.get("file") or raw.get("path") or ""),
            source=source,
        )
        key = (item.severity, item.code, item.phase, item.file, re.sub(r"\s+", " ", item.message).casefold())
        if key in seen:
            return
        seen.add(key)
        collected.append(item)

    raw = state.raw if isinstance(state.raw, dict) else {}
    for issue in raw.get("diagnostics", []) if isinstance(raw.get("diagnostics"), list) else []:
        append(issue, phase=str(raw.get("phase_name") or raw.get("phase") or ""), source="state")
    for result in raw.get("results", []) if isinstance(raw.get("results"), list) else []:
        if not isinstance(result, dict):
            continue
        phase = str(result.get("name") or result.get("phase") or "")
        for key in ("diagnostics", "issues", "errors", "warnings"):
            for issue in result.get(key, []) if isinstance(result.get(key), list) else []:
                append(issue, phase=phase, source="result")
    completion = completion_from_state(state)
    for issue in completion.get("diagnostics", []) if isinstance(completion.get("diagnostics"), list) else []:
        append(issue, phase=str(completion.get("phase") or "completion"), source="completion")
    for text in completion.get("warning_details", []) if isinstance(completion.get("warning_details"), list) else []:
        append({"severity": "warning", "message": str(text)}, phase="completion", source="completion")

    for event in events:
        if not isinstance(event, dict):
            continue
        if event_is_error(event):
            append({**event, "severity": "error"}, source="event")
        elif event_is_warning(event):
            append({**event, "severity": "warning"}, source="event")

    return [item.to_dict() for item in collected]


def canonical_issue_counts(events: Iterable[dict[str, Any]], state: HudState) -> tuple[int, int]:
    """Return the canonical visible error/warning counts for the current run."""
    events_list = list(events)
    diagnostics = canonical_diagnostics(events_list, state)
    diag_errors = sum(1 for item in diagnostics if item.get("severity") == "error")
    diag_warnings = sum(1 for item in diagnostics if item.get("severity") == "warning")

    explicit_errors = 0
    explicit_warnings = 0
    raw = state.raw if isinstance(state.raw, dict) else {}
    raw_totals = raw.get("totals") if isinstance(raw.get("totals"), dict) else {}
    explicit_errors = max(explicit_errors, int(_coerce_int(raw_totals.get("errors") or raw_totals.get("failed")) or 0))
    explicit_warnings = max(explicit_warnings, int(_coerce_int(raw_totals.get("warnings")) or 0))
    phase_progress = raw.get("phase_progress") if isinstance(raw.get("phase_progress"), list) else []
    for item in phase_progress:
        if not isinstance(item, dict):
            continue
        explicit_errors = max(explicit_errors, int(_coerce_int(item.get("errors")) or 0))
        explicit_warnings = max(explicit_warnings, int(_coerce_int(item.get("warnings")) or 0))
    completion = completion_from_state(state)
    explicit_errors = max(explicit_errors, int(completion.get("errors", 0) or 0), int(completion.get("failed", 0) or 0))
    explicit_warnings = max(explicit_warnings, int(completion.get("warnings", 0) or 0))

    # Fall back to event parsing only when structured diagnostics/totals are
    # absent. This preserves live compiler feedback without contradicting the
    # canonical totals emitted at completion.
    if explicit_errors == 0 and explicit_warnings == 0 and diag_errors == 0 and diag_warnings == 0:
        parsed_errors, parsed_warnings = _parser_observation_counts(events_list, state)
        explicit_errors = max(explicit_errors, parsed_errors)
        explicit_warnings = max(explicit_warnings, parsed_warnings)

    error_count = max(explicit_errors, diag_errors, 1 if state.status == "failed" else 0)
    warning_count = max(explicit_warnings, diag_warnings)
    return error_count, warning_count


def _parser_observation_counts(events: Iterable[dict[str, Any]], state: HudState) -> tuple[int, int]:
    errors: set[str] = set()
    warnings: set[str] = set()
    explicit_errors = 0
    explicit_warnings = 0
    summary_re = re.compile(r"(?P<count>\d+)\s+(?P<kind>warning|error)\(s\)", re.I)
    for event in events:
        totals = event.get("totals") if isinstance(event.get("totals"), dict) else {}
        explicit_errors = max(explicit_errors, int(_coerce_int(totals.get("errors") or totals.get("failed")) or 0))
        explicit_warnings = max(explicit_warnings, int(_coerce_int(totals.get("warnings")) or 0))
        message = _event_message(event).strip()
        for match in summary_re.finditer(message):
            count = int(match.group("count"))
            if match.group("kind").lower().startswith("error"):
                explicit_errors = max(explicit_errors, count)
            else:
                explicit_warnings = max(explicit_warnings, count)
        normalized = re.sub(r"\s+", " ", message).casefold()
        event_type = _coerce_str(event.get("event_type") or event.get("type")).lower().replace("_", "-")
        if normalized and event_type not in {"phase-finished", "finished"}:
            if event_is_error(event) and normalized not in {"devtool finished with errors", "step failed"}:
                errors.add(normalized)
            elif event_is_warning(event):
                warnings.add(normalized)
    return max(explicit_errors, len(errors), 1 if state.status == "failed" else 0), max(explicit_warnings, len(warnings))


def primary_failure_message(events: Iterable[dict[str, Any]], state: HudState) -> str:
    diagnostics = canonical_diagnostics(events, state)
    for item in diagnostics:
        if item.get("severity") == "error" and item.get("message"):
            return str(item.get("message"))
    for event in reversed(list(events)):
        text = str(event.get("message") or event.get("text") or "").strip() if isinstance(event, dict) else ""
        if text and ("error" in text.lower() or "failed" in text.lower()):
            return text
    return compact_detail_text(state) or str(state.raw.get("text") or "The last operation reported a failure.") if isinstance(state.raw, dict) else "The last operation reported a failure."

@dataclass(slots=True)
class HudOperationStep:
    key: str
    label: str
    status: str = "pending"
    progress: int = 0
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "status": self.status,
            "progress": max(0, min(100, int(self.progress))),
            "detail": self.detail,
        }


_STEP_ORDER = ("extract", "restore", "build", "test", "validate", "commit", "rollback")
_STEP_LABELS = {
    "extract": "Extract",
    "restore": "Restore",
    "build": "Build",
    "test": "Test",
    "validate": "Validate",
    "commit": "Commit",
    "rollback": "Rollback",
}


def _split_operation_phase(name: str) -> tuple[str, str]:
    """Return the operation name and optional ``[...]`` target/workspace scope."""
    raw = (name or "").strip()
    match = re.match(r"^(?P<name>.*?)(?:\[(?P<scope>[^\]]+)\])?$", raw)
    if not match:
        return raw, ""
    return (match.group("name") or raw).strip(), (match.group("scope") or "").strip()


def _operation_step_key(name: str) -> str:
    base, _scope = _split_operation_phase(name)
    key = base.strip().lower().replace("_", "-")
    if key in {"apply-overlay", "apply-overlays", "apply-snapshot", "extract-overlay", "extract"}:
        return "extract"
    if key in {"restore", "dependency-restore"} or "restore" in key:
        return "restore"
    if key in {"build", "package"} or key.endswith("-build"):
        return "build"
    if key in {"test", "tests", "wine-test"} or key.endswith("-test"):
        return "test"
    if key in {
        "artifact-validate-script", "artifact-validation-expectations", "external-validation",
        "validation-deferred", "validation-paused", "validation-session-complete",
    } or "validate-script" in key or key.startswith("validation-"):
        return "validate"
    if key in {"git-add", "git-commit", "git-commit-skipped", "commit"}:
        return "commit"
    if key in {"apply-rollback", "rollback"}:
        return "rollback"
    return ""


def _step_status(value: str) -> str:
    status = (value or "").strip().lower().replace("_", "-")
    if status in {"ok", "success", "complete", "completed", "passed"}:
        return "ok"
    if status in {"failed", "error", "failure"}:
        return "failed"
    if status in {"skipped", "skip"}:
        return "skipped"
    if status in {"running", "started", "progress"}:
        return "running"
    return "pending"


def _compact_step_scope(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    # Workspace names are normally already compact. Long generated target IDs
    # remain readable without consuming the entire compact HUD width.
    if len(text) > 24:
        parts = [part for part in re.split(r"[_/\\]+", text) if part]
        if len(parts) >= 2:
            text = "-".join(parts[:2])
        if len(text) > 24:
            text = text[:21] + "…"
    return text


def _phase_scope(event: dict[str, Any]) -> str:
    phase = event.get("phase") if isinstance(event.get("phase"), dict) else {}
    workspace = _coerce_str(phase.get("workspace"))
    target = _coerce_str(phase.get("target"))
    _base, bracket_scope = _split_operation_phase(_phase_from_event(event))
    return workspace or bracket_scope or target


def _step_instance_key(base: str, scope: str = "") -> str:
    clean_scope = re.sub(r"[^a-z0-9]+", "-", (scope or "").casefold()).strip("-")
    return f"{base}:{clean_scope}" if clean_scope else base


def _step_display_label(base: str, scope: str = "") -> str:
    label = _STEP_LABELS[base]
    compact = _compact_step_scope(scope)
    return f"{label} · {compact}" if compact else label


def _planned_pipeline(state: HudState, events: list[dict[str, Any]]) -> list[dict[str, str]]:
    candidates: list[Any] = []
    raw_pipeline = state.raw.get("pipeline") if isinstance(state.raw, dict) else None
    if isinstance(raw_pipeline, list):
        candidates.extend(raw_pipeline)
    if not candidates:
        for event in reversed(events):
            pipeline = event.get("pipeline") if isinstance(event.get("pipeline"), list) else None
            if pipeline:
                candidates.extend(pipeline)
                break
    planned: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in candidates:
        if not isinstance(item, dict):
            continue
        name = _coerce_str(item.get("name"))
        base = _operation_step_key(name)
        if not base:
            continue
        _raw_base, bracket_scope = _split_operation_phase(name)
        scope = _coerce_str(item.get("workspace") or bracket_scope or item.get("target"))
        marker = (base, scope.casefold())
        if marker in seen:
            continue
        seen.add(marker)
        planned.append({"base": base, "scope": scope})
    return planned


def operation_steps(state: HudState, events: Iterable[dict[str, Any]]) -> list[HudOperationStep]:
    """Build a stable pipeline with independent live progress per target.

    Target/workspace suffixes such as ``build[s3atelier]`` are preserved as
    separate steps, so monorepo validation can show each restore/build/test
    sequence instead of collapsing all targets into one finished-only bar.
    """
    events_list = list(events)
    command = (state.command or str(state.raw.get("command") or "")).strip().lower().replace("_", "-")
    apply_run = command in {"apply-overlay", "apply-overlays", "apply-snapshot", "ao", "aos", "as"} or any(
        _operation_step_key(_phase_from_event(event)) == "extract" for event in events_list
    )

    descriptors: list[tuple[str, str]] = []
    descriptor_set: set[tuple[str, str]] = set()

    def add_descriptor(base: str, scope: str = "", *, before_tail: bool = False) -> None:
        if not base:
            return
        marker = (base, scope.casefold())
        if marker in descriptor_set:
            return
        descriptor_set.add(marker)
        if before_tail:
            tail_index = next((i for i, (key, _scope) in enumerate(descriptors) if key in {"commit", "rollback"}), len(descriptors))
            descriptors.insert(tail_index, (base, scope))
        else:
            descriptors.append((base, scope))

    if apply_run:
        add_descriptor("extract")

    planned = _planned_pipeline(state, events_list)
    for item in planned:
        add_descriptor(item["base"], item["scope"])

    if apply_run and not any(base in {"restore", "build", "test"} for base, _scope in descriptors):
        for base in ("restore", "build", "test"):
            add_descriptor(base)
    if apply_run:
        add_descriptor("commit")

    statuses: dict[str, str] = {}
    progresses: dict[str, int] = {}

    for event in events_list:
        base = _operation_step_key(_phase_from_event(event))
        if not base:
            continue
        scope = _phase_scope(event)
        add_descriptor(base, scope, before_tail=True)
        instance = _step_instance_key(base, scope)
        event_type = _coerce_str(event.get("event_type") or event.get("type")).lower().replace("_", "-")
        status = _step_status(_result_status_from_event(event))
        if event_type == "phase-started":
            status = "running"
        elif event_type == "progress" and status == "pending":
            status = "running"
        elif event_type == "phase-finished" and status == "pending":
            status = "ok"
        if status != "pending":
            statuses[instance] = status
        current, total = _progress_pair(event)
        if current is not None and total is not None and total > 0:
            progresses[instance] = max(0, min(100, int(round(current / total * 100))))
        elif status in {"ok", "failed", "skipped"}:
            progresses[instance] = 100

    # The current state carries an authoritative snapshot for every phase.
    # This avoids depending on the HUD poller observing every intermediate
    # JSONL event and keeps target-scoped bars live across atomic file swaps.
    phase_progress = state.raw.get("phase_progress") if isinstance(state.raw, dict) else None
    if isinstance(phase_progress, list):
        for item in phase_progress:
            if not isinstance(item, dict):
                continue
            name = _coerce_str(item.get("name"))
            base = _operation_step_key(name)
            if not base:
                continue
            _raw_base, bracket_scope = _split_operation_phase(name)
            scope = _coerce_str(item.get("workspace") or bracket_scope or item.get("target"))
            add_descriptor(base, scope, before_tail=True)
            instance = _step_instance_key(base, scope)
            status = _step_status(_coerce_str(item.get("status")))
            if status != "pending":
                statuses[instance] = status
            percent = item.get("percent")
            if isinstance(percent, (int, float)):
                progresses[instance] = max(0, min(100, int(round(float(percent)))))
            else:
                current = _coerce_int(item.get("current") if item.get("current") is not None else item.get("completed"))
                total = _coerce_int(item.get("total"))
                if current is not None and total is not None and total > 0:
                    progresses[instance] = max(0, min(100, int(round(current / total * 100))))
                elif status in {"ok", "failed", "skipped"}:
                    progresses[instance] = 100

    completion = completion_from_state(state)
    actions = completion.get("actions") if isinstance(completion.get("actions"), list) else []
    for action in actions:
        if not isinstance(action, dict):
            continue
        base = _operation_step_key(str(action.get("name") or ""))
        if not base:
            continue
        scope = str(action.get("workspace") or action.get("target") or "").strip()
        add_descriptor(base, scope, before_tail=True)
        instance = _step_instance_key(base, scope)
        statuses[instance] = _step_status(str(action.get("status") or "ok"))
        progresses[instance] = 100

    active_base = _operation_step_key(state.phase_name or state.command)
    active_scope = ""
    if isinstance(state.raw, dict):
        active_scope = _phase_scope(state.raw)
    if active_base:
        add_descriptor(active_base, active_scope, before_tail=True)
        instance = _step_instance_key(active_base, active_scope)
        if state.status == "running":
            statuses[instance] = "running"
            if state.percent is not None:
                progresses[instance] = state.percent
        elif state.status == "failed" and statuses.get(instance) not in {"ok", "skipped"}:
            statuses[instance] = "failed"
            progresses[instance] = 100

    terminal = state.status in {"ok", "failed", "skipped", "interrupted", "cancelled"}
    if terminal:
        for base, scope in descriptors:
            instance = _step_instance_key(base, scope)
            if instance not in statuses:
                statuses[instance] = "skipped"
            status = statuses.get(instance, "pending")
            if status == "skipped":
                progresses.setdefault(instance, 0)
            elif status in {"ok", "failed"}:
                progresses.setdefault(instance, 100)
            else:
                progresses.setdefault(instance, 0)

    # Generic placeholders are replaced once target-scoped equivalents exist.
    scoped_bases = {base for base, scope in descriptors if scope}
    descriptors = [(base, scope) for base, scope in descriptors if scope or base not in scoped_bases]

    # Rollback always belongs at the end; commit remains immediately before it.
    order_index = {name: index for index, name in enumerate(_STEP_ORDER)}
    if not planned:
        descriptors.sort(key=lambda item: order_index.get(item[0], len(order_index)))

    return [
        HudOperationStep(
            _step_instance_key(base, scope),
            _step_display_label(base, scope),
            statuses.get(_step_instance_key(base, scope), "pending"),
            progresses.get(_step_instance_key(base, scope), 0),
            "Repository restored" if base == "rollback" and statuses.get(_step_instance_key(base, scope)) in {"ok", "success", "complete", "completed"} else "Not run" if statuses.get(_step_instance_key(base, scope), "pending") == "skipped" else "",
        )
        for base, scope in descriptors
    ]


def live_issue_counts(events: Iterable[dict[str, Any]], state: HudState) -> tuple[int, int]:
    """Return canonical visible warning/error counts for the current run."""
    return canonical_issue_counts(events, state)


def pipeline_label(state: HudState, highlights: dict[str, str]) -> str:
    # Compatibility helper retained for terminal/web callers. GTK and Android
    # render the structured operation_steps() model instead.
    return " → ".join(
        ("✓ " if step.status == "ok" else "● " if step.status == "running" else "✕ " if step.status == "failed" else "") + step.label
        for step in operation_steps(state, [])
    )


def state_context_label(state: HudState) -> str:
    bits: list[str] = []
    if state.repo_name:
        bits.append(state.repo_name)
    elif state.repo_root:
        bits.append(Path(state.repo_root).name)
    raw_targets = state.raw.get("targets") if isinstance(state.raw, dict) else None
    targets = [str(item).strip() for item in raw_targets if str(item).strip()] if isinstance(raw_targets, list) else []
    targets = list(dict.fromkeys(targets))
    if len(targets) > 1:
        bits.append(f"{len(targets)} targets: " + ", ".join(_compact_step_scope(item) for item in targets))
    elif targets:
        bits.append(targets[0])
    elif state.target:
        bits.append(state.target)
    return " · ".join(bits) if bits else "Waiting for target"


def state_metric_text(state: HudState, events: Iterable[dict[str, Any]]) -> str:
    completion = completion_from_state(state)
    errors, warnings = canonical_issue_counts(events, state)
    parts = [f"{errors} errors", f"{warnings} warnings"]
    if completion and str(completion.get("detail") or "").strip():
        parts.append(str(completion.get("detail")))
    elif state.percent is not None:
        parts.append(f"{state.percent}%")
    else:
        summary = hud_event_summary(events)
        if summary.get("events", 0):
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
        completion = completion_from_state(state)
        headline = str(completion.get("headline") or "").strip()
        if headline:
            return headline
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
    return snapshot


def _payload_sequence(payload: dict[str, Any] | None) -> int:
    if not isinstance(payload, dict):
        return -1
    notification = _first_dict(payload.get("tasker_notification"), payload.get("notification"))
    return int(_coerce_int(payload.get("sequence") or payload.get("seq") or notification.get("sequence") or notification.get("seq")) or -1)


def _payload_updated_timestamp(payload: dict[str, Any] | None) -> float:
    if not isinstance(payload, dict):
        return -1.0
    notification = _first_dict(payload.get("tasker_notification"), payload.get("notification"))
    value = payload.get("updated_at") or notification.get("updated_at")
    if not value:
        value = payload.get("updated_at_ms") or notification.get("updated_at_ms")
        try:
            numeric = float(value)
            return numeric / 1000.0 if numeric > 10_000_000_000 else numeric
        except (TypeError, ValueError):
            return -1.0
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (TypeError, ValueError):
        return -1.0


def _payload_order(payload: dict[str, Any] | None, mtime_ns: int = 0) -> tuple[int, float, int]:
    return (_payload_sequence(payload), _payload_updated_timestamp(payload), int(mtime_ns))


def state_from_directory(directory: Path) -> HudState:
    """Read the newest authoritative HUD snapshot without phase rewinds.

    Local phase counters restart at each operation (for example build 17/17,
    then test 1/3). Choosing a file by its percentage therefore preserves stale
    build data. Prefer the producer's monotonic sequence, then timestamp and
    filesystem mtime. notification.json may decorate the matching rich state,
    but may not replace counters from a newer phase.
    """
    paths = {
        "state": directory / "state.json",
        "latest": directory / "latest_event.json",
        "notification": directory / "notification.json",
    }
    loaded: dict[str, tuple[dict[str, Any], int]] = {}
    for name, path in paths.items():
        payload = _read_json_file(path)
        if payload is None:
            continue
        try:
            mtime = path.stat().st_mtime_ns
        except OSError:
            mtime = 0
        loaded[name] = (payload, mtime)

    rich_candidates = [loaded[name] for name in ("state", "latest") if name in loaded]
    rich_payload: dict[str, Any] | None = None
    rich_mtime = 0
    if rich_candidates:
        rich_payload, rich_mtime = max(rich_candidates, key=lambda item: _payload_order(item[0], item[1]))

    notification_entry = loaded.get("notification")
    if rich_payload is not None:
        merged = dict(rich_payload)
        if notification_entry is not None:
            notification, notification_mtime = notification_entry
            rich_seq = _payload_sequence(rich_payload)
            notification_seq = _payload_sequence(notification)
            sequenced = rich_seq >= 0 or notification_seq >= 0
            same_or_newer_event = notification_seq >= 0 and notification_seq >= rich_seq

            # A sequenced notification may decorate/override only its matching
            # or newer rich event. This prevents an old build summary from
            # replacing a newer test phase. Legacy unsequenced snapshots retain
            # the historical behavior: notification supplies friendly text and
            # target, while the higher local progress pair is kept.
            merged["notification"] = notification
            merged["tasker_notification"] = notification
            if same_or_newer_event or not sequenced:
                for key in ("title", "text", "expanded_title", "expanded_text", "target"):
                    if key in notification:
                        merged[key] = notification[key]
            if same_or_newer_event:
                for key in ("current", "total", "percent"):
                    if key in notification:
                        merged[key] = notification[key]
            elif not sequenced and _payload_progress_score(notification) >= _payload_progress_score(rich_payload):
                for key in ("current", "total", "percent"):
                    if key in notification:
                        merged[key] = notification[key]
        selected = merged
        selected_order = _payload_order(rich_payload, rich_mtime)
    elif notification_entry is not None:
        selected, selected_mtime = notification_entry
        selected_order = _payload_order(selected, selected_mtime)
    else:
        selected = None
        selected_order = (-1, -1.0, 0)

    # JSONL is a recovery source only. It wins when it is genuinely newer by
    # sequence/timestamp, never merely because a prior phase was 100% complete.
    events_path = directory / "events.jsonl"
    events = _last_jsonl_events(events_path, 1)
    if events:
        event = events[-1]
        try:
            event_mtime = events_path.stat().st_mtime_ns
        except OSError:
            event_mtime = 0
        event_seq = _payload_sequence(event)
        selected_seq = _payload_sequence(selected)
        if event_seq >= 0 and event_seq > selected_seq:
            selected = event
        elif selected is None:
            selected = event

    return parse_hud_state(selected)


def snapshot_from_directory(directory: Path, *, recent_events: int = DEFAULT_RECENT_EVENTS) -> dict[str, Any]:
    events = _last_jsonl_events(directory / "events.jsonl", recent_events)
    state = state_from_directory(directory)
    if state.raw == {} and events:
        state = parse_hud_state(events[-1])
    return {
        "version": 5,
        "state": state.to_dict(),
        "ui": runtime_state_to_ui_snapshot(state.raw, surface="hud").as_dict(),
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


class AndroidAppHud:
    """Forward the authoritative active state to the Android companion app.

    The forwarder intentionally reads only ``state.json`` for normal live
    updates.  The state already contains the complete pipeline and independent
    per-workspace phase snapshots, so reparsing thousands of JSONL events every
    100 ms only adds lag.  History remains available to the app through logs.
    """

    HEARTBEAT_INTERVAL_SECONDS = 5
    TERMINAL_STATUSES = {"ok", "failed", "skipped", "cancelled", "interrupted"}

    def __init__(self, *, recent_events: int = DEFAULT_RECENT_EVENTS, overlay: bool | None = None) -> None:
        self.recent_events = max(0, int(recent_events))
        self.overlay = overlay
        self.last_signature: tuple[Any, ...] | None = None
        self.bridge = AndroidLiveHudBridge()
        self.run_started_at: dict[str, int] = {}

    @staticmethod
    def _timestamp_ms(value: object) -> int:
        text = str(value or "").strip()
        if not text:
            return int(time.time() * 1000)
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return int(parsed.timestamp() * 1000)
        except ValueError:
            return int(time.time() * 1000)

    @staticmethod
    def _pipeline_percent(steps: list[HudOperationStep], *, terminal_ok: bool) -> int:
        if terminal_ok:
            return 100
        if not steps:
            return 0
        value = 0.0
        for step in steps:
            if step.status in {"ok", "failed", "skipped"}:
                value += 100.0
            else:
                value += max(0, min(100, int(step.progress)))
        return max(0, min(100, int(round(value / len(steps)))))

    @staticmethod
    def _live_test_metrics(state: HudState) -> dict[str, int]:
        raw = state.raw if isinstance(state.raw, dict) else {}
        result = {"passed": 0, "failed": 0, "skipped": 0, "total": 0, "assemblies": 0}
        phase_progress = raw.get("phase_progress") if isinstance(raw.get("phase_progress"), list) else []
        for item in phase_progress:
            if not isinstance(item, dict):
                continue
            name = _operation_step_key(str(item.get("name") or item.get("command") or ""))
            has_metrics = any(item.get(key) is not None for key in (
                "tests_passed", "tests_failed", "tests_skipped", "tests_total", "test_assemblies",
            ))
            if name != "test" and not has_metrics:
                continue
            result["passed"] += max(0, int(_coerce_int(item.get("tests_passed")) or 0))
            result["failed"] += max(0, int(_coerce_int(item.get("tests_failed")) or 0))
            result["skipped"] += max(0, int(_coerce_int(item.get("tests_skipped")) or 0))
            result["total"] += max(0, int(_coerce_int(item.get("tests_total")) or 0))
            result["assemblies"] += max(0, int(_coerce_int(item.get("test_assemblies")) or 0))

        completion = completion_from_state(state) or {}
        tests = completion.get("tests") if isinstance(completion.get("tests"), dict) else {}
        if tests:
            # Completion is the final authority and avoids double counting when
            # a workflow reports aggregate plus per-workspace phase metrics.
            result["passed"] = max(0, int(tests.get("passed", 0) or 0))
            result["failed"] = max(0, int(tests.get("failed", 0) or 0))
            result["skipped"] = max(0, int(tests.get("skipped", 0) or 0))
            result["total"] = max(0, int(tests.get("total", 0) or 0))
        if result["total"] <= 0:
            result["total"] = result["passed"] + result["failed"] + result["skipped"]
        return result

    @staticmethod
    def _workspace_rows(state: HudState) -> list[dict[str, Any]]:
        """Build the compact per-workspace model used by the Android notification.

        The HTML mock showed each workspace independently.  Preserve that model in
        the native payload instead of forcing Android to infer it from one current
        phase and an aggregate percentage.
        """
        raw = state.raw if isinstance(state.raw, dict) else {}
        pipeline = raw.get("pipeline") if isinstance(raw.get("pipeline"), list) else []
        snapshots = raw.get("phase_progress") if isinstance(raw.get("phase_progress"), list) else []
        phase = raw.get("phase") if isinstance(raw.get("phase"), dict) else {}
        active_scope = str(phase.get("workspace") or phase.get("target") or state.target or "").strip()

        ordered: dict[str, dict[str, Any]] = {}

        def ensure(scope: str) -> dict[str, Any] | None:
            clean = str(scope or "").strip()
            if not clean:
                return None
            if clean not in ordered:
                ordered[clean] = {
                    "name": clean,
                    "status": "pending",
                    "meta": "Waiting",
                    "progress": 0,
                    "steps": [],
                }
            return ordered[clean]

        for item in pipeline:
            if not isinstance(item, dict):
                continue
            ensure(str(item.get("workspace") or item.get("target") or ""))

        for item in snapshots:
            if not isinstance(item, dict):
                continue
            row = ensure(str(item.get("workspace") or item.get("target") or ""))
            if row is None:
                continue
            status = _step_status(str(item.get("status") or "pending"))
            name = human_phase_name(str(item.get("name") or item.get("command") or "Working"))
            percent = item.get("percent")
            if not isinstance(percent, (int, float)):
                current = _coerce_int(item.get("current") if item.get("current") is not None else item.get("completed"))
                total = _coerce_int(item.get("total"))
                percent = int(round(current / total * 100)) if current is not None and total and total > 0 else (100 if status in {"ok", "failed", "skipped"} else 0)
            percent = max(0, min(100, int(round(float(percent)))))
            detail = name
            current = _coerce_int(item.get("current") if item.get("current") is not None else item.get("completed"))
            total = _coerce_int(item.get("total"))
            unit = str(item.get("unit_label") or "").strip()
            if current is not None and total is not None and total > 0:
                detail += f" · {current}/{total}" + (f" {unit}" if unit else "")
            elif percent > 0 and status == "running":
                detail += f" · {percent}%"
            row["steps"].append((status, percent, detail))

        if active_scope:
            ensure(active_scope)

        rows: list[dict[str, Any]] = []
        for scope, row in ordered.items():
            steps = list(row.pop("steps", []))
            statuses = [item[0] for item in steps]
            if "failed" in statuses:
                row["status"] = "failed"
            elif "running" in statuses or scope == active_scope and state.status == "running":
                row["status"] = "active"
            elif steps and all(item in {"ok", "skipped"} for item in statuses):
                row["status"] = "done"
            else:
                row["status"] = "pending"

            active_step = next((item for item in steps if item[0] in {"running", "failed"}), None)
            if active_step is not None:
                row["meta"] = active_step[2]
            elif row["status"] == "done":
                row["meta"] = "Complete"
            elif row["status"] == "failed":
                row["meta"] = "Failed"
            else:
                row["meta"] = "Waiting"

            if steps:
                row["progress"] = max(0, min(100, int(round(sum(item[1] for item in steps) / len(steps)))))
            if row["status"] in {"done", "failed"}:
                row["progress"] = 100
            rows.append(row)
        return rows

    @staticmethod
    def _workspace_context(state: HudState) -> tuple[str, int, int]:
        raw = state.raw if isinstance(state.raw, dict) else {}
        pipeline = raw.get("pipeline") if isinstance(raw.get("pipeline"), list) else []
        scopes: list[str] = []
        for item in pipeline:
            if not isinstance(item, dict):
                continue
            scope = str(item.get("workspace") or item.get("target") or "").strip()
            if scope and scope not in scopes:
                scopes.append(scope)
        phase = raw.get("phase") if isinstance(raw.get("phase"), dict) else {}
        active = str(phase.get("workspace") or phase.get("target") or state.target or "").strip()
        if active and active not in scopes:
            scopes.append(active)
        total = max(1, len(scopes))
        index = scopes.index(active) + 1 if active in scopes else 1
        return active, index, total

    def _payload(self, state: HudState, directory: Path) -> tuple[str, dict[str, Any]]:
        completion = completion_from_state(state) or {}
        steps = operation_steps(state, [])
        error_count, warning_count = live_issue_counts([], state)
        tests = self._live_test_metrics(state)
        run_id = str(state.raw.get("run_id") or state.raw.get("task_id") or directory.name or "devtool")
        started_at = self.run_started_at.setdefault(
            run_id,
            self._timestamp_ms(state.raw.get("started_at")) if isinstance(state.raw, dict) else int(time.time() * 1000),
        )
        status = (state.status or "running").strip().lower()
        action = "update"
        if status in {"ok", "skipped"}:
            action = "complete"
        elif status in {"failed", "interrupted", "cancelled"}:
            action = "fail"
        duration_seconds = completion.get("duration_seconds")
        try:
            duration_ms = int(float(duration_seconds) * 1000) if duration_seconds is not None else 0
        except (TypeError, ValueError):
            duration_ms = 0
        now_ms = int(time.time() * 1000)
        raw_updated_at_ms = self._timestamp_ms(state.raw.get("updated_at")) if isinstance(state.raw, dict) else now_ms
        is_terminal = status in self.TERMINAL_STATUSES
        heartbeat_bucket = 0 if is_terminal else int(time.time() // self.HEARTBEAT_INTERVAL_SECONDS)
        # For quiet builds, a heartbeat is the only payload delta.  Advance the
        # timestamp so the Android stale-update gate accepts the repaint even
        # when the build sequence has not changed.
        updated_at_ms = max(raw_updated_at_ms, now_ms) if not is_terminal else raw_updated_at_ms

        raw_targets = state.raw.get("targets") if isinstance(state.raw, dict) else None
        target_values = [str(item).strip() for item in raw_targets if str(item).strip()] if isinstance(raw_targets, list) else []
        target_values = list(dict.fromkeys(target_values))
        android_target = ", ".join(_compact_step_scope(item) for item in target_values) if len(target_values) > 1 else (target_values[0] if target_values else state.target)
        workspace_name, workspace_index, workspace_total = self._workspace_context(state)
        phase_progress = state.raw.get("progress") if isinstance(state.raw.get("progress"), dict) else {}
        raw_phase = state.raw.get("phase") if isinstance(state.raw.get("phase"), dict) else {}
        command_name = str(state.command or state.raw.get("command") or "devtool").strip().lower().replace("_", "-")
        task_titles = {
            "apply-overlay": "Applying overlay",
            "ao": "Applying overlay",
            "apply-overlays": "Applying overlay bundle",
            "aos": "Applying overlay bundle",
            "apply-snapshot": "Applying snapshot",
            "restore": "Restoring dependencies",
            "build": "Building target",
            "test": "Running tests",
            "validate": "Validating target",
            "commit": "Creating commit",
            "rollback": "Rolling back changes",
        }
        task_title = task_titles.get(command_name, human_phase_name(state.phase_name or state.command) or "Running Devtool")
        raw_artifact = str(
            state.raw.get("artifact")
            or state.raw.get("artifact_path")
            or state.raw.get("bundle")
            or ""
        ).strip()
        unit_label = ""
        raw_phase_progress = state.raw.get("phase_progress") if isinstance(state.raw.get("phase_progress"), list) else []
        for item in raw_phase_progress:
            if not isinstance(item, dict):
                continue
            if str(item.get("status") or "").lower() == "running":
                unit_label = str(item.get("unit_label") or "").strip()
                break

        overall_percent = self._pipeline_percent(steps, terminal_ok=status in {"ok", "skipped"})
        semantic_android_ui = runtime_state_to_ui_snapshot(state.raw, surface="android").as_dict()
        payload: dict[str, Any] = {
            "run_id": run_id,
            "sequence": int(_coerce_int(state.raw.get("sequence")) or 0),
            "updated_at_ms": updated_at_ms,
            "heartbeat": heartbeat_bucket,
            "status": status,
            "task": task_title,
            "artifact": raw_artifact,
            "phase": human_phase_name(state.phase_name or state.command) or human_status(status),
            "phase_index": int(_coerce_int(raw_phase.get("index")) or 0),
            "phase_total": int(_coerce_int(raw_phase.get("total")) or 0),
            "detail": human_detail(state.text or state.expanded_text),
            "repo": state.repo_name or (Path(state.repo_root).name if state.repo_root else "devtool"),
            "target": android_target,
            "project_type": str(state.raw.get("project_type") or "auto"),
            "execution_environment": str(state.raw.get("execution_environment") or "auto"),
            "workspace": workspace_name,
            "workspace_index": workspace_index,
            "workspace_total": workspace_total,
            "workspaces_json": json.dumps(self._workspace_rows(state), separators=(",", ":")),
            "progress": overall_percent,
            "phase_progress": state.percent if state.percent is not None else 0,
            "indeterminate": state.percent is None and status not in {"ok", "failed", "skipped"},
            "current": state.current,
            "total": state.total,
            "unit_label": unit_label or str(phase_progress.get("unit_label") or ""),
            "passed": tests["passed"],
            "failed": tests["failed"],
            "skipped": tests["skipped"],
            "test_total": tests["total"],
            "test_assemblies": tests["assemblies"],
            "warnings": warning_count,
            "errors": error_count,
            "steps_json": json.dumps([step.to_dict() for step in steps], separators=(",", ":")),
            "commit": str(completion.get("commit_hash") or ""),
            "summary": completion_copy_text(state),
            "ui_schema_version": UI_SCHEMA_VERSION,
            "ui_actions_json": json.dumps(semantic_android_ui.get("actions", []), separators=(",", ":")),
            "ui_navigation_json": json.dumps(semantic_android_ui.get("navigation", []), separators=(",", ":")),
            "started_at_ms": started_at,
            "duration_ms": duration_ms,
            "overlay": bool(state.raw.get("overlay", False)) if self.overlay is None else bool(self.overlay),
        }
        return action, payload

    def update(self, state: HudState, *, directory: Path) -> None:
        action, payload = self._payload(state, directory)
        signature = (
            payload.get("run_id"), payload.get("sequence"), action, payload.get("status"),
            payload.get("phase"), payload.get("progress"), payload.get("phase_progress"),
            payload.get("current"), payload.get("total"), payload.get("passed"),
            payload.get("failed"), payload.get("skipped"), payload.get("warnings"),
            payload.get("errors"), payload.get("steps_json"), payload.get("workspaces_json"),
            payload.get("heartbeat"),
        )
        if signature == self.last_signature:
            return
        # Failed broadcasts are retried on the next poll.  Previously the
        # signature was committed first, permanently dropping transient sends.
        if self.bridge.send(action, payload):
            self.last_signature = signature

    def run(self, *, directory: Path, interval_s: float, once: bool = False, follow_active: bool = False) -> int:
        while True:
            current_dir = resolve_hud_directory(directory, follow_active=follow_active)
            self.update(state_from_directory(current_dir), directory=current_dir)
            if once:
                return 0
            time.sleep(interval_s)


class AndroidBridgeHud:
    """Chroot/proot-safe Android HUD bridge writer.

    This backend does not import Termux:GUI. It mirrors the current HUD state
    into the shared directory explicitly selected by the execution planner
    inside an Ubuntu chroot. A Termux host helper forwards the state only to the
    Devtool Android app bridge.
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
    from .xdg import data_home
    return data_home()


def _desktop_hud_exec_argv() -> list[str]:
    """Return a launcher command that survives restricted desktop PATH values."""
    configured = os.environ.get("DEVTOOL_EXECUTABLE", "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return [str(candidate), "hud", "--mode", "desktop-window", "--follow-active"]

    located = shutil.which("devtool")
    if located:
        return [str(Path(located).resolve()), "hud", "--mode", "desktop-window", "--follow-active"]

    return [sys.executable, "-m", "devtool", "hud", "--mode", "desktop-window", "--follow-active"]


def _desktop_entry_exec(argv: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in argv)


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
    desktop_exec = _desktop_entry_exec(_desktop_hud_exec_argv())
    desktop_text = f"""[Desktop Entry]
Type=Application
Version=1.0
Name=Devtool HUD
Comment=Live devtool progress HUD
Exec={desktop_exec}
Icon={icon_path}
Terminal=false
NoDisplay=true
DBusActivatable=false
Categories=Development;Utility;
StartupNotify=false
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
        updater = shutil.which("update-desktop-database")
        if updater:
            subprocess.run(
                [updater, str(desktop_path.parent)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        return icon_path
    except OSError:
        return None




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
    """HUD v5: web dashboard plus local console."""

    def __init__(self, *, host: str, port: int, recent_events: int) -> None:
        self.host = host
        self.port = port
        self.recent_events = recent_events

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
        terminal = TerminalHud(console=console, recent_events=self.recent_events, title="devtool HUD v5 studio")
        refresh = max(1, min(20, int(1 / max(interval_s, 0.05))))
        current_dir = resolve_hud_directory(directory, follow_active=follow_active)
        with Live(terminal.render(state_from_directory(current_dir), _last_jsonl_events(current_dir / "events.jsonl", self.recent_events)), console=console, refresh_per_second=refresh) as live:
            while True:
                current_dir = resolve_hud_directory(directory, follow_active=follow_active)
                state = state_from_directory(current_dir)
                events = _last_jsonl_events(current_dir / "events.jsonl", self.recent_events)
                live.update(terminal.render(state, events))
                time.sleep(interval_s)

def legacy_desktop_mode_message(requested: str) -> str:
    requested = (requested or "").strip() or "desktop"
    if requested in {"qt", "qt5", "qt6", "qt-window", "qt-control-center"}:
        return (
            f"Legacy Qt desktop mode {requested!r} was removed in the GTK4 desktop seal. "
            "Use 'desktop-window' for the GTK4 HUD or 'control-center' for the GTK4 Control Center."
        )
    return (
        f"Legacy desktop mode {requested!r} is no longer a separate backend. "
        "Use 'desktop-window' for the GTK4 HUD."
    )


def choose_hud_mode(requested: str) -> str:
    requested = (requested or "auto").strip().lower()
    legacy_qt_modes = {"qt", "qt5", "qt6", "qt-window", "qt-control-center"}
    legacy_gtk_modes = {"gtk-window", "gtk3", "x11-gtk"}
    aliases = {
        "v3": "terminal", "console": "terminal",
        "v4": "web", "server": "web", "http": "web", "dashboard": "web",
        "v5": "studio", "all": "studio",
        "v6": "desktop", "unix": "desktop", "gnome": "desktop",
        "desktop-gtk": "desktop-window", "gtk": "desktop-window", "gtk4": "desktop-window",
        "gtk4-window": "desktop-window", "notify-send": "desktop-notify", "libnotify": "desktop-notify",
        "termux-gui": "android-bridge", "termuxgui": "android-bridge",
        "android": "android-bridge", "android-gui": "android-bridge",
        "android-overlay": "android-bridge",
        "control-centre": "control-center",
        "android-chroot": "android-bridge", "termux-bridge": "android-bridge",
        "termux-chroot": "android-bridge", "android-hud-bridge": "android-bridge",
    }
    if requested in legacy_qt_modes:
        return "legacy-qt-removed"
    if requested in legacy_gtk_modes:
        return "desktop-window"
    requested = aliases.get(requested, requested)
    if requested == "desktop":
        if _is_termux_android() or _is_termux_chroot_bridge():
            return "android-bridge"
        try:
            from .desktop.gtk_hud import Gtk4CompactHud
            if Gtk4CompactHud.available():
                return "desktop-window"
        except Exception:
            pass
        if DesktopNotificationHud.available():
            return "desktop-notify"
        return "terminal"
    if _is_termux_android() and requested in {"desktop-window", "desktop-notify"}:
        return "android-bridge"
    if requested != "auto":
        return requested
    if _is_termux_android() or _is_termux_chroot_bridge():
        return "android-bridge"
    if os.name == "posix" and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        try:
            from .desktop.gtk_hud import Gtk4CompactHud
            if Gtk4CompactHud.available():
                return "desktop-window"
        except Exception:
            pass
        if DesktopNotificationHud.available():
            return "desktop-notify"
    return "terminal"

def build_hud_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="devtool hud")
    parser.add_argument("--state-dir", dest="tasker_dir", default=DEFAULT_TASKER_DIR, help="Directory containing devtool live HUD state")
    parser.add_argument(
        "--mode",
        choices=["auto", "android-bridge", "android-gui", "termux-gui", "terminal", "web", "studio", "desktop", "desktop-window", "gtk4-window", "gtk-window", "desktop-notify", "control-center", "v3", "v4", "v5", "v6", "console", "dashboard", "all", "unix", "gnome"],
        default="auto",
        help="HUD renderer: Android app bridge, terminal/v3, web/v4, studio/v5, or GTK4 desktop/v6",
    )
    parser.add_argument("--host", default=DEFAULT_HUD_HOST, help="Host/IP for HUD web/studio modes")
    parser.add_argument("--port", type=int, default=DEFAULT_HUD_PORT, help="Port for HUD web/studio modes")
    parser.add_argument("--poll-interval", type=float, default=0.1, help="Seconds between HUD refreshes")
    parser.add_argument("--recent-events", type=int, default=DEFAULT_RECENT_EVENTS, help="Recent JSONL events to show/serve")
    parser.add_argument("--once", action="store_true", help="Render/update once and exit")
    parser.add_argument("--follow-active", action="store_true", help="Follow the global active HUD directory written by auto-HUD")
    presentation = parser.add_mutually_exclusive_group()
    presentation.add_argument("--overlay", action="store_true", help="Also publish runs to the enabled Android floating overlay HUD")
    presentation.add_argument("--no-overlay", action="store_true", help="Do not open the floating overlay")
    return parser


def run_hud(args: argparse.Namespace) -> int:
    if hud_disabled_by_environment():
        return 0
    directory = Path(args.tasker_dir).expanduser()
    mode = choose_hud_mode(str(args.mode))
    interval_s = max(0.05, float(args.poll_interval))
    recent_events = max(0, int(args.recent_events))
    follow_active = bool(getattr(args, "follow_active", False))
    if mode == "android-bridge":
        if _is_termux_android():
            overlay_override: bool | None = None
            if bool(getattr(args, "overlay", False)):
                overlay_override = True
            elif bool(getattr(args, "no_overlay", False)):
                overlay_override = False
            return AndroidAppHud(recent_events=recent_events, overlay=overlay_override).run(
                directory=directory, interval_s=interval_s, once=bool(args.once), follow_active=follow_active
            )
        return AndroidBridgeHud(recent_events=recent_events).run(directory=directory, interval_s=interval_s, once=bool(args.once), follow_active=follow_active)
    if mode == "web":
        return WebDashboardHud(host=str(args.host), port=int(args.port), recent_events=recent_events, interval_s=interval_s, follow_active=follow_active).run(directory=directory, once=bool(args.once))
    if mode == "studio":
        return StudioHud(host=str(args.host), port=int(args.port), recent_events=recent_events).run(directory=directory, interval_s=interval_s, once=bool(args.once), follow_active=follow_active)
    if mode == "legacy-qt-removed":
        print(legacy_desktop_mode_message(str(getattr(args, "mode", "qt-window") or "qt-window")), file=sys.stderr)
        return 2
    if str(getattr(args, "mode", "") or "").strip().lower() in {"gtk-window", "gtk3", "x11-gtk"}:
        print("devtool: --mode gtk-window now maps to the GTK4 desktop HUD; use --mode desktop-window.", file=sys.stderr)
    if mode == "desktop-window":
        from .desktop.gtk_hud import Gtk4CompactHud

        if bool(args.once) or Gtk4CompactHud.available():
            return Gtk4CompactHud(recent_events=recent_events).run(
                directory=directory, interval_s=interval_s, once=bool(args.once), follow_active=follow_active
            )
        raise RuntimeError(
            "Desktop HUD requires the GTK4/libadwaita backend. "
            "Install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1."
        )
    if mode == "desktop-notify":
        return DesktopNotificationHud(notification_id=str(args.notify_id)).run(directory=directory, interval_s=interval_s, once=bool(args.once), follow_active=follow_active)
    return TerminalHud(recent_events=recent_events).run(directory=directory, interval_s=interval_s, once=bool(args.once))


def main(argv: list[str] | None = None) -> int:
    parser = build_hud_parser()
    args = parser.parse_args(argv)
    return run_hud(args)


if __name__ == "__main__":
    raise SystemExit(main())
