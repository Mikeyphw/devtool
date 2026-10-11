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
from .single_run import active_run_lock_records


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


def _candidate_state_roots() -> list[Path]:
    """Return every state root that can own the active producer record.

    The active run lock lives in the normal XDG runtime/state tree, while HUD
    producers may publish their richer run state to a Tasker or Android bridge
    directory. ``logs tail`` must consult both rather than assuming the HUD
    always writes to ``state_dir()``.
    """
    roots: list[Path] = []

    def add(value: str | Path | None) -> None:
        if value is None:
            return
        text = str(value).strip()
        if not text:
            return
        path = Path(text).expanduser()
        if path not in roots:
            roots.append(path)

    explicit_xdg = bool(os.environ.get("DEVTOOL_XDG_DIR", "").strip())
    add(state_dir())
    add(os.environ.get("DEVTOOL_TASKER_DIR"))
    add(os.environ.get("DEVTOOL_HUD_BRIDGE_DIR"))
    add(os.environ.get("DEVTOOL_ANDROID_HUD_STATE_DIR"))

    try:
        from .tasker import default_tasker_dir, read_active_hud_directory

        # DEVTOOL_XDG_DIR is an explicit isolation boundary. Explicit bridge
        # directories above may opt back into shared state, but inferred Android
        # defaults (/sdcard/Tasker/devtool) and the global HUD registry must not
        # escape the caller-selected state root.
        if not explicit_xdg:
            add(default_tasker_dir())
            if os.environ.get("DEVTOOL_NO_HUD", "").strip().lower() not in {"1", "true", "yes", "on"}:
                add(read_active_hud_directory(default=None))
    except Exception:
        pass
    return roots


def _path_text(path: Path) -> str:
    # Purely lexical: resolving an Android shared-storage path can itself block.
    return os.path.abspath(os.fspath(path.expanduser()))


def _is_android_shared_storage(path: Path) -> bool:
    text = _path_text(path)
    return text == "/sdcard" or text.startswith("/sdcard/") or text == "/storage" or text.startswith("/storage/")


def _explicit_state_root_keys() -> set[str]:
    values: list[str | Path] = [state_dir()]
    for name in ("DEVTOOL_TASKER_DIR", "DEVTOOL_HUD_BRIDGE_DIR", "DEVTOOL_ANDROID_HUD_STATE_DIR"):
        value = os.environ.get(name, "").strip()
        if value:
            values.append(value)
    return {_path_text(Path(value)) for value in values}


def _partition_state_roots(roots: Iterable[Path]) -> tuple[list[Path], list[Path]]:
    """Keep inferred /sdcard roots off the hot path on Termux."""
    explicit = _explicit_state_root_keys()
    fast: list[Path] = []
    slow: list[Path] = []
    for root in roots:
        bucket = slow if _is_android_shared_storage(root) and _path_text(root) not in explicit else fast
        if root not in bucket:
            bucket.append(root)
    return fast, slow


def _log_paths_in_dir(directory: Path) -> list[Path]:
    paths: list[Path] = []
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.name.endswith(".log"):
                    try:
                        if entry.is_file(follow_symlinks=False):
                            paths.append(Path(entry.path))
                    except OSError:
                        continue
    except OSError:
        pass
    return paths


def _all_log_paths() -> list[Path]:
    # Local/XDG and explicitly configured roots are authoritative. Inferred
    # Android shared storage is fallback-only because it can block for seconds.
    paths: list[Path] = []
    seen: set[Path] = set()
    roots = _candidate_state_roots()
    fast_roots, slow_roots = _partition_state_roots(roots)

    fast_dirs: list[Path] = [logs_dir()]
    for root in fast_roots:
        candidate = root / "logs"
        if candidate not in fast_dirs:
            fast_dirs.append(candidate)

    def collect(directories: Iterable[Path]) -> None:
        for directory in directories:
            for path in _log_paths_in_dir(directory):
                if path not in seen:
                    seen.add(path)
                    paths.append(path)

    collect(fast_dirs)
    if not paths:
        collect(root / "logs" for root in slow_roots)
    return sorted(paths, key=lambda item: (_safe_mtime(item), str(item)))


def list_log_entries() -> list[LogEntry]:
    entries: list[LogEntry] = []
    for path in _all_log_paths():
        date, time = parse_log_filename(path)
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        entries.append(LogEntry(path=path, date=date, time=time, size=size))
    return entries


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


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Cross-user Termux/chroot readers cannot signal the producer, but the
        # permission failure proves that the PID exists.
        return True
    except OSError:
        return False
    return True


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _state_timestamp(state: dict[str, Any], path: Path | None = None) -> str:
    value = str(
        state.get("updated_at")
        or state.get("heartbeat_at")
        or state.get("started_at")
        or ""
    ).strip()
    if value:
        return value
    if path is not None:
        timestamp = _safe_mtime(path)
        if timestamp:
            return datetime.fromtimestamp(timestamp).isoformat()
    return ""


def _active_run_from_payload(state: dict[str, Any], *, fallback_id: str, source_path: Path | None = None) -> ActiveRun | None:
    run_id = str(state.get("run_id") or fallback_id).strip()
    if not run_id:
        return None
    pid = int(state.get("pid") or 0)
    if pid and not _pid_is_alive(pid):
        return None
    return ActiveRun(
        run_id=run_id,
        pid=pid,
        command=str(state.get("command") or "").strip(),
        repo=str(state.get("repo_root") or state.get("repo_name") or "").strip(),
        status=str(state.get("status") or "running").strip(),
        phase=_phase_name(state.get("phase")),
        updated_at=_state_timestamp(state, source_path),
        log_path=_log_path_from_state(state),
    )


def _merge_active_runs(authoritative: ActiveRun, richer: ActiveRun) -> ActiveRun:
    """Merge HUD detail without letting it override lock authority.

    The single-run lock decides whether an operation is active and what its
    terminal status is. HUD JSON is richer but can be stale; it may contribute
    descriptive fields, never contradict the authoritative status/identity.
    """
    authoritative_status = authoritative.status or "running"
    richer_phase = richer.phase if authoritative_status in {"running", "starting", "pending"} else ""
    return ActiveRun(
        run_id=authoritative.run_id,
        pid=authoritative.pid or richer.pid,
        command=authoritative.command or richer.command,
        repo=authoritative.repo or richer.repo,
        status=authoritative_status,
        phase=richer_phase or authoritative.phase,
        updated_at=authoritative.updated_at or richer.updated_at,
        log_path=authoritative.log_path or richer.log_path,
    )


def list_active_runs() -> list[ActiveRun]:
    by_id: dict[str, ActiveRun] = {}
    roots = _candidate_state_roots()
    fast_roots, slow_roots = _partition_state_roots(roots)

    # Repository locks are authoritative. Avoid inferred /sdcard state when a
    # local or explicitly configured producer is already discoverable.
    lock_states = active_run_lock_records(fast_roots)
    if not lock_states and slow_roots:
        lock_states = active_run_lock_records(slow_roots)
    for state in lock_states:
        run = _active_run_from_payload(state, fallback_id="")
        if run is not None:
            by_id[run.run_id] = run

    def merge_hud_roots(scan_roots: Iterable[Path]) -> None:
        for root in scan_roots:
            runs_dir = root / "runs"
            try:
                with os.scandir(runs_dir) as entries:
                    paths = sorted(
                        (Path(entry.path) for entry in entries if entry.name.endswith(".json")),
                        key=str,
                    )
            except OSError:
                continue
            for path in paths:
                try:
                    state = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(state, dict):
                    continue
                run = _active_run_from_payload(state, fallback_id=path.stem, source_path=path)
                if run is None:
                    continue
                existing = by_id.get(run.run_id)
                by_id[run.run_id] = _merge_active_runs(existing, run) if existing else run

    merge_hud_roots(fast_roots)
    if not by_id and slow_roots:
        merge_hud_roots(slow_roots)

    return sorted(by_id.values(), key=lambda run: run.updated_at or "", reverse=True)

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
    result = subprocess.run(
        ["fzf", "--prompt=devtool logs> "],
        input="\n".join(lines),
        text=True,
        stdout=subprocess.PIPE,
        check=False,
    )
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
        paths = _all_log_paths()
        return paths[-1] if paths else latest_log()
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
        argv = [viewer, "--paging=auto", str(path)]
    return subprocess.call(argv)


def tail_log(path: Path, *, lines: int = 200, follow: bool = True) -> int:
    argv = ["tail", f"-n{max(0, int(lines))}"]
    if follow:
        argv.append("-f")
    argv.append(str(path))
    return subprocess.call(argv)


def newest_logs(limit: int = 10) -> list[Path]:
    return _all_log_paths()[-max(1, int(limit)):]


def _accessible_log_path(path: Path | None, fallbacks: Iterable[Path]) -> Path | None:
    if path is not None and path.exists():
        return path
    if path is not None:
        basename = path.name
        for candidate in fallbacks:
            if candidate.name == basename and candidate.exists():
                return candidate
        for root in _candidate_state_roots():
            candidate = root / "logs" / basename
            if candidate.exists():
                return candidate
    return None


def choose_active_log(*, use_fzf: bool = True) -> Path | None:
    runs = list_active_runs()
    recent_logs = newest_logs(max(1, len(runs)))
    if not runs:
        return None
    lines: list[str] = []
    log_by_index: dict[int, Path | None] = {}
    for index, run in enumerate(runs, start=1):
        # The active-run record is the authority for run identity. Never pair a
        # run that lacks log_path evidence with an arbitrary recent log merely
        # because timestamps happen to be close; concurrent operations can make
        # that association wrong.
        resolved = _accessible_log_path(run.log_path, recent_logs)
        log_by_index[index] = resolved
        lines.append(_format_run(run, index, resolved))
    selected = choose_index(lines, use_fzf=use_fzf)
    if selected is None and not sys.stdin.isatty():
        # Termux RunCommand and app integrations are non-interactive. Select
        # the newest active run rather than falsely reporting that none exists.
        selected = 1
    if selected is None or selected < 1 or selected > len(runs):
        return None
    return log_by_index.get(selected)


def format_log_entries(entries: Iterable[LogEntry]) -> list[str]:
    return [_format_entry(entry, index) for index, entry in enumerate(entries, start=1)]
