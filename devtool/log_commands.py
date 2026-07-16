from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Iterable

from .logstore import latest_log, logs_dir, state_dir


@dataclass(frozen=True, slots=True)
class LogEntry:
    path: Path
    date: str
    time: str
    size: int


@dataclass(frozen=True, slots=True)
class ActiveRun:
    run_id: str
    pid: int
    command: str
    repo: str
    status: str
    phase: str
    updated_at: str
    log_path: Path | None


def parse_log_filename(path: Path) -> tuple[str, str]:
    """Return ISO date and HH:MM:SS time for devtool log filenames."""
    stem = path.stem
    parts = stem.split("-")
    if len(parts) < 2:
        return "", ""
    try:
        parsed = datetime.strptime(f"{parts[0]}-{parts[1]}", "%Y%m%d-%H%M%S")
    except ValueError:
        return "", ""
    return parsed.strftime("%Y-%m-%d"), parsed.strftime("%H:%M:%S")


def list_log_entries() -> list[LogEntry]:
    entries: list[LogEntry] = []
    for path in sorted(logs_dir().glob("*.log")):
        date, time = parse_log_filename(path)
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        entries.append(LogEntry(path=path, date=date, time=time, size=size))
    return entries


def _runs_dir() -> Path:
    return state_dir() / "runs"


def _phase_name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or "").strip()
    return ""


def _log_path_from_state(state: dict[str, Any]) -> Path | None:
    if state.get("log_path"):
        return Path(str(state["log_path"])).expanduser()
    result = state.get("result")
    if isinstance(result, dict) and result.get("log_path"):
        return Path(str(result["log_path"])).expanduser()
    results = state.get("results")
    if isinstance(results, list):
        for item in reversed(results):
            if isinstance(item, dict) and item.get("log_path"):
                return Path(str(item["log_path"])).expanduser()
    return None


def list_active_runs() -> list[ActiveRun]:
    runs: list[ActiveRun] = []
    for path in sorted(_runs_dir().glob("*.json")):
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(state, dict):
            continue
        run_id = str(state.get("run_id") or path.stem).strip()
        if not run_id:
            continue
        pid = int(state.get("pid") or 0)
        if pid:
            try:
                os.kill(pid, 0)
            except OSError:
                continue
        runs.append(
            ActiveRun(
                run_id=run_id,
                pid=pid,
                command=str(state.get("command") or "").strip(),
                repo=str(state.get("repo_root") or state.get("repo_name") or "").strip(),
                status=str(state.get("status") or "").strip(),
                phase=_phase_name(state.get("phase")),
                updated_at=str(state.get("updated_at") or state.get("heartbeat_at") or "").strip(),
                log_path=_log_path_from_state(state),
            )
        )
    return runs


def _format_entry(entry: LogEntry, index: int) -> str:
    stamp = " ".join(part for part in (entry.date, entry.time) if part) or "unknown-time"
    return f"{index:3d}  {stamp}  {entry.size:9d}  {entry.path}"


def _format_run(run: ActiveRun, index: int, fallback_log: Path | None = None) -> str:
    log_path = run.log_path or fallback_log
    log_display = str(log_path) if log_path else "-"
    pid = str(run.pid) if run.pid else "-"
    return f"{index:3d}  pid={pid}  {run.command or '-'}  {run.status or '-'}  {run.phase or '-'}  {run.repo or '-'}  log={log_display}"


def _choose_with_fzf(lines: list[str]) -> int | None:
    if not lines or shutil.which("fzf") is None or not sys.stdin.isatty():
        return None
    result = subprocess.run(["fzf", "--prompt=devtool logs> "], input="\n".join(lines), text=True, capture_output=True, check=False)
    if result.returncode != 0:
        return None
    selected = result.stdout.strip()
    if not selected:
        return None
    try:
        return int(selected.split(None, 1)[0])
    except (ValueError, IndexError):
        return None


def choose_index(lines: list[str], *, use_fzf: bool = True) -> int | None:
    if not lines:
        return None
    if len(lines) == 1:
        return 1
    if use_fzf:
        selected = _choose_with_fzf(lines)
        if selected is not None:
            return selected
    if not sys.stdin.isatty():
        return None
    for line in lines:
        print(line, file=sys.stderr)
    raw = input("Choose log number: ").strip()
    try:
        return int(raw)
    except ValueError:
        return None


def resolve_log_path(selector: str = "", *, use_fzf: bool = True) -> Path | None:
    selector = selector.strip()
    if selector and selector != "latest":
        path = Path(selector).expanduser()
        return path if path.exists() else None
    if selector == "latest":
        return latest_log()
    entries = list_log_entries()
    lines = [_format_entry(entry, index) for index, entry in enumerate(entries, start=1)]
    selected = choose_index(lines, use_fzf=use_fzf)
    if selected is None or selected < 1 or selected > len(entries):
        return None
    return entries[selected - 1].path


def open_log(path: Path, *, pager: str = "auto") -> int:
    if pager == "auto":
        viewer = "bat" if shutil.which("bat") else "cat"
    else:
        viewer = pager
    argv = [viewer, str(path)]
    if viewer == "bat":
        argv = [viewer, "--paging=always", str(path)]
    return subprocess.call(argv)


def tail_log(path: Path, *, lines: int = 200, follow: bool = True) -> int:
    argv = ["tail", f"-n{max(0, int(lines))}"]
    if follow:
        argv.append("-f")
    argv.append(str(path))
    return subprocess.call(argv)


def newest_logs(limit: int = 10) -> list[Path]:
    return [entry.path for entry in list_log_entries()[-max(1, int(limit)):]]


def choose_active_log(*, use_fzf: bool = True) -> Path | None:
    runs = list_active_runs()
    recent_logs = newest_logs(max(1, len(runs)))
    if not runs:
        return None
    lines = []
    fallback_by_index: dict[int, Path | None] = {}
    for index, run in enumerate(runs, start=1):
        fallback = recent_logs[index - 1] if index - 1 < len(recent_logs) else (recent_logs[-1] if recent_logs else None)
        fallback_by_index[index] = fallback
        lines.append(_format_run(run, index, fallback))
    selected = choose_index(lines, use_fzf=use_fzf)
    if selected is None or selected < 1 or selected > len(runs):
        return None
    return runs[selected - 1].log_path or fallback_by_index.get(selected)


def format_log_entries(entries: Iterable[LogEntry]) -> list[str]:
    return [_format_entry(entry, index) for index, entry in enumerate(entries, start=1)]
