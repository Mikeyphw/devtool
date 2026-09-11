from __future__ import annotations

import atexit
import contextlib
import json
import os
import shutil
import shlex
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .xdg import state_dir

from .ipc import IpcPublisher, ensure_ipc_server

from .console_utils import resolve_cli_path
from .models import OperationResult
from .ui_core.viewmodels import runtime_state_to_ui_snapshot

ANDROID_TASKER_DIR = "/sdcard/Tasker/devtool"


def _is_termux_android() -> bool:
    """Return True only for real Termux/Android, not Linux desktops with /sdcard.

    Some desktop setups can have /sdcard mounted or symlinked, so merely
    checking Path('/sdcard').exists() makes Ubuntu/GNOME incorrectly use the
    Android Tasker directory. Prefer Termux/Android-specific environment and
    filesystem signals instead.
    """
    prefix = os.environ.get("PREFIX", "")
    termux_version = os.environ.get("TERMUX_VERSION")
    termux_app_pid = os.environ.get("TERMUX_APP_PID")
    if termux_version or termux_app_pid:
        return True
    if prefix.startswith("/data/data/com.termux/") or "com.termux" in prefix:
        return True
    if os.environ.get("ANDROID_ROOT") and Path("/system/build.prop").exists():
        return True
    return False


def default_tasker_dir() -> str:
    """Return the default devtool event directory for this platform.

    Android/Termux keeps the original Tasker path. Normal Unix desktops use
    XDG_STATE_HOME so HUDs and desktop notifications work without /sdcard.
    DEVTOOL_TASKER_DIR always wins.
    """
    override = os.environ.get("DEVTOOL_TASKER_DIR")
    if override:
        return override
    if _is_termux_android():
        return ANDROID_TASKER_DIR
    return str(state_dir())


DEFAULT_TASKER_DIR = default_tasker_dir()
DEFAULT_NOTIFICATION_ID = "7771"
DEFAULT_EVENT_HTTP_URL = os.environ.get("DEVTOOL_EVENT_HTTP_URL", "http://127.0.0.1:1821/")
DEFAULT_EVENT_HTTP_TIMEOUT_S = 0.5
DEFAULT_HUD_SINGLETON_MODE = os.environ.get("DEVTOOL_HUD_SINGLETON", "reuse").strip().lower() or "reuse"
DEFAULT_RUN_HEARTBEAT_INTERVAL_S = 5.0
HUD_DISABLE_ENV = "DEVTOOL_NO_HUD"
_TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}


def hud_disabled_by_environment() -> bool:
    """Return whether HUD publication/rendering is hard-disabled.

    DEVTOOL_NO_HUD is intentionally stronger than command-line flags: when it
    is truthy, --auto-hud, --hud-overlay, and direct ``devtool hud`` launches
    are all ignored. DEVTOOL_NO_AUTO_HUD remains supported as the narrower
    legacy switch used by detached HUD children.
    """
    return os.environ.get(HUD_DISABLE_ENV, "").strip().lower() in _TRUTHY_ENV_VALUES


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


def _default_run_id() -> str:
    return os.environ.get("DEVTOOL_RUN_ID", "").strip() or uuid.uuid4().hex


def hud_registry_dir() -> Path:
    override = os.environ.get("DEVTOOL_HUD_REGISTRY_DIR")
    if override:
        return tasker_dir_from_text(override)
    return tasker_dir_from_text(DEFAULT_TASKER_DIR)


def hud_active_file() -> Path:
    return hud_registry_dir() / "hud-active.json"


def hud_singleton_file() -> Path:
    return hud_registry_dir() / "hud-singleton.json"


def _read_json_path(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def write_active_hud_directory(directory: Path, *, mode: str = "desktop") -> None:
    payload = {"directory": str(directory), "mode": mode, "updated_at": utc_now_iso(), "pid": os.getpid()}
    _atomic_write_json(hud_active_file(), payload)


def read_active_hud_directory(default: Path | None = None) -> Path | None:
    payload = _read_json_path(hud_active_file())
    directory = str((payload or {}).get("directory") or "").strip()
    if directory:
        return tasker_dir_from_text(directory)
    return default


def _read_hud_singleton() -> dict[str, Any]:
    return _read_json_path(hud_singleton_file()) or {}


def _write_hud_singleton(*, pid: int, mode: str, directory: Path, follow_active: bool) -> None:
    payload = {"pid": int(pid), "mode": mode, "directory": str(directory), "follow_active": bool(follow_active), "started_at": utc_now_iso()}
    _atomic_write_json(hud_singleton_file(), payload)


def _terminate_pid(pid: int, *, timeout_s: float = 1.5) -> bool:
    if not _pid_is_alive(pid):
        return True
    try:
        os.kill(pid, 15)
    except OSError:
        return not _pid_is_alive(pid)
    deadline = time.monotonic() + max(0.1, timeout_s)
    while time.monotonic() < deadline:
        if not _pid_is_alive(pid):
            return True
        time.sleep(0.05)
    with contextlib.suppress(OSError):
        os.kill(pid, 9)
    return not _pid_is_alive(pid)



def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Atomically write JSON and force it to disk for live HUD readers.

    Desktop HUDs and Tasker file observers can otherwise lag behind terminal
    output because the writer process has closed/replaced the file but the
    payload is still sitting in userspace/kernel buffers.  fsyncing the temp
    file before replace, and best-effort fsyncing the directory after replace,
    makes notification.json/latest_event.json reliable as a live state channel.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    data = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    if os.name == "posix":
        try:
            dir_fd = os.open(str(path.parent), os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(dir_fd)
        except OSError:
            pass
        finally:
            os.close(dir_fd)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _progress_current_total(progress: dict[str, Any] | None) -> tuple[Any, Any]:
    progress = progress or {}
    current = progress.get("current")
    if current is None:
        current = progress.get("completed")
    return current, progress.get("total")


def _simple_notification_payload(notification: dict[str, Any], *, message: str, current: Any, total: Any) -> dict[str, Any]:
    title = str(notification.get("title") or "devtool")
    text = str(notification.get("text") or notification.get("body") or message or "running")
    expanded_title = str(notification.get("expanded_title") or title)
    expanded_text = str(notification.get("expanded_text") or notification.get("body") or text)
    return {
        "title": title,
        "text": text,
        "expanded_title": expanded_title,
        "expanded_text": expanded_text,
        "current": current,
        "total": total,
    }


def send_local_http_event(
    payload: dict[str, Any],
    *,
    url: str = DEFAULT_EVENT_HTTP_URL,
    timeout_s: float = DEFAULT_EVENT_HTTP_TIMEOUT_S,
) -> bool:
    """Best-effort POST of a full progress event to a local receiver.

    This replaces the old Android ``log -t DEVTOOL <json>`` side channel.
    Toybox ``log`` truncates argv messages at 1024 bytes, which can cut JSON
    in the middle of a token. Posting the UTF-8 JSON body to a localhost
    listener preserves the complete payload without adding a third-party
    dependency or making devtool fail when the listener is absent.
    """
    endpoint = (url or "").strip()
    if not endpoint:
        return False
    try:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except Exception:
        body = json.dumps({"message": str(payload)}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    request = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Content-Length": str(len(body)),
            "User-Agent": "devtool",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=max(0.05, float(timeout_s))) as response:
            return 200 <= int(getattr(response, "status", 200)) < 300
    except (OSError, ValueError, urllib.error.URLError):
        return False


def send_logcat_event(tag: str, payload: dict[str, Any]) -> bool:
    """Backward-compatible wrapper for callers using the old function name."""
    return send_local_http_event(payload)


def _safe_name(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in value.strip())
    cleaned = cleaned.strip(".-")
    return cleaned or "event"


def tasker_dir_from_text(path_text: str | None) -> Path:
    return resolve_cli_path(path_text or DEFAULT_TASKER_DIR)

def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def start_auto_hud(directory: Path, *, mode: str = "desktop", poll_interval: float = 0.1, no_overlay: bool = False) -> bool:
    """Start, reuse, or replace the global live HUD process.

    Auto-HUD is a singleton by default. Each new devtool task writes the active
    event directory to hud-active.json; a GTK/web/studio HUD spawned by this
    helper follows that pointer, so one already-open window can switch to the
    newest task instead of spawning duplicate windows.
    """
    if hud_disabled_by_environment() or os.environ.get("DEVTOOL_AUTO_HUD_CHILD") == "1" or os.environ.get("DEVTOOL_NO_AUTO_HUD") == "1":
        return False

    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False

    requested_mode = (mode or "desktop").strip() or "desktop"
    if requested_mode in {"terminal", "console", "v3"} and not sys.stdout.isatty():
        requested_mode = "desktop"

    singleton_mode = (os.environ.get("DEVTOOL_HUD_SINGLETON") or DEFAULT_HUD_SINGLETON_MODE or "reuse").strip().lower()
    singleton_mode = {"true": "reuse", "1": "reuse", "yes": "reuse", "replace": "kill"}.get(singleton_mode, singleton_mode)
    follow_active = singleton_mode not in {"off", "false", "0", "no", "legacy"}

    try:
        if follow_active:
            write_active_hud_directory(directory, mode=requested_mode)
    except OSError:
        follow_active = False

    registry = hud_registry_dir() if follow_active else directory
    try:
        registry.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False

    log_path = registry / "hud-child.log"

    def _append_hud_log(message: str) -> None:
        with contextlib.suppress(OSError):
            registry.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(f"{utc_now_iso()} | {message}\n")

    def _resolve_mode_for_detached_child(value: str) -> str:
        try:
            from .hud import choose_hud_mode
            return choose_hud_mode(value)
        except Exception as exc:
            _append_hud_log(f"failed to resolve HUD mode {value!r}: {exc}")
            return value

    chosen_mode = _resolve_mode_for_detached_child(requested_mode)
    registry_mode = chosen_mode
    # The GTK4 Control Center watches the same state directory. Publish
    # the active pointer, but do not spawn a second renderer process.
    if chosen_mode == "control-center":
        ready = ensure_ipc_server(state_dir=directory)
        _append_hud_log("control-center mode: IPC broker ready." if ready else "control-center mode: IPC broker unavailable; file fallback remains active.")
        return True
    if chosen_mode == "android-bridge":
        try:
            from .hud import _is_termux_android
            termux_host = _is_termux_android()
        except Exception:
            termux_host = False
        if termux_host:
            from .android_live_hud import AndroidLiveHudBridge

            bridge = AndroidLiveHudBridge()
            if not bridge.available():
                detail = bridge.last_error or bridge.install_hint()
                _append_hud_log(f"android bridge mode unavailable: {detail}")
                if "companion package was not found" in detail.lower():
                    warning = "warning: Devtool Live HUD app not found."
                else:
                    warning = "warning: Devtool Live HUD unavailable."
                print(warning, file=sys.stderr)
                return False
            _append_hud_log("android bridge mode: companion endpoint resolved; starting the native app state forwarder.")
        else:
            _append_hud_log(
                "android bridge mode: producer is not the Termux host; state is "
                "mirrored for the host bridge daemon."
            )
            return True
    if chosen_mode in {"terminal", "console", "v3"}:
        _append_hud_log(
            "not starting auto-HUD because the requested/resolved mode is terminal, "
            "which would be invisible when detached. Install devtool-control-center or libnotify-bin, then retry desktop/desktop-window/desktop-notify."
        )
        return False

    legacy_pid_file = directory / "hud.pid"
    singleton = _read_hud_singleton() if follow_active else {}
    try:
        old_pid = int((singleton.get("pid") if singleton else legacy_pid_file.read_text(encoding="utf-8").strip()) or 0)
    except Exception:
        old_pid = 0

    if _pid_is_alive(old_pid):
        old_mode = str((singleton or {}).get("mode") or "")
        should_replace = (
            (follow_active and singleton_mode == "kill")
            or (follow_active and old_mode and old_mode != chosen_mode)
        )
        if should_replace:
            _append_hud_log(f"replacing existing HUD pid={old_pid} mode={old_mode or '-'} with mode={registry_mode}")
            _terminate_pid(old_pid)
        elif follow_active and bool(singleton.get("follow_active")):
            return True
        elif follow_active:
            _terminate_pid(old_pid)
        else:
            return True

    if follow_active:
        try:
            legacy_pid = int(legacy_pid_file.read_text(encoding="utf-8").strip() or "0")
        except Exception:
            legacy_pid = 0
        if legacy_pid and legacy_pid != old_pid and _pid_is_alive(legacy_pid):
            _terminate_pid(legacy_pid)

    argv = [
        sys.executable, "-m", "devtool", "hud",
        "--mode", chosen_mode,
        "--state-dir", str(directory),
        "--poll-interval", str(max(0.05, float(poll_interval))),
    ]
    if follow_active:
        argv.append("--follow-active")

    try:
        env = os.environ.copy()
        env["DEVTOOL_AUTO_HUD_CHILD"] = "1"
        env["DEVTOOL_NO_AUTO_HUD"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        quoted = " ".join(shlex.quote(part) for part in argv)
        log_quoted = shlex.quote(str(log_path))
        _append_hud_log(f"starting auto-HUD requested={requested_mode} resolved={chosen_mode} dir={directory}")
        launcher = f"setsid {quoted} </dev/null >>{log_quoted} 2>&1 & echo $!"
        completed = subprocess.run(["/bin/sh", "-c", launcher], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=False, close_fds=True, env=env)
        pid_text = (completed.stdout or "").strip().splitlines()[-1:]
        if pid_text:
            pid = int(pid_text[0].strip())
            legacy_pid_file.parent.mkdir(parents=True, exist_ok=True)
            legacy_pid_file.write_text(str(pid), encoding="utf-8")
            if follow_active:
                _write_hud_singleton(pid=pid, mode=registry_mode, directory=directory, follow_active=True)
            _append_hud_log(f"spawned auto-HUD pid={pid} mode={chosen_mode}")
        return completed.returncode == 0
    except Exception as exc:
        _append_hud_log(f"failed to start auto-HUD: {exc}")
        return False

def _result_status(result: OperationResult) -> str:
    if result.skipped:
        return "skipped"
    return "ok" if result.returncode == 0 else "failed"


def _result_to_json(result: OperationResult) -> dict[str, Any]:
    diagnostics = []
    for issue in result.issues:
        diagnostics.append({
            "severity": issue.severity,
            "code": issue.code or "",
            "message": issue.message,
            "file": issue.file or "",
            "line": issue.line,
            "phase": result.name,
            "cause": result.failure_kind or "",
            "related_node": str((result.metadata or {}).get("workflow_node_key") or ""),
            "technical_details": f"{result.name} exited {result.returncode}" if result.returncode else "",
            "evidence": {"exit_code": result.returncode, "failure_kind": result.failure_kind or ""},
        })
    return {
        "name": result.name,
        "status": _result_status(result),
        "exit_code": result.returncode,
        "duration_s": result.duration_seconds,
        "log_path": result.log_path or None,
        "failure_kind": result.failure_kind or None,
        "metadata": result.metadata or {},
        "diagnostics": diagnostics,
        "warnings": sum(1 for issue in result.issues if issue.severity == "warning"),
        "errors": sum(1 for issue in result.issues if issue.severity == "error"),
    }


def _completion_test_totals(results: list[OperationResult]) -> dict[str, int]:
    """Aggregate authoritative structured reports before console fallbacks."""
    import re

    structured = [
        result.metadata.get("tests")
        for result in results
        if isinstance(result.metadata, dict) and isinstance(result.metadata.get("tests"), dict)
    ]
    if structured:
        return {
            "failed": sum(int(item.get("failed") or 0) for item in structured),
            "passed": sum(int(item.get("passed") or 0) for item in structured),
            "skipped": sum(int(item.get("skipped") or 0) for item in structured),
            "total": sum(int(item.get("total") or 0) for item in structured),
        }

    text = "\n".join(result.output or "" for result in results if result.name in {"test", "tests", "wine-test"})
    modern = list(re.finditer(
        r"\b(?:Passed|Failed)!\s*-\s*Failed:\s*(\d+),\s*Passed:\s*(\d+),\s*Skipped:\s*(\d+),\s*Total:\s*(\d+)",
        text,
        re.IGNORECASE,
    ))
    if modern:
        return {
            "failed": sum(int(match.group(1)) for match in modern),
            "passed": sum(int(match.group(2)) for match in modern),
            "skipped": sum(int(match.group(3)) for match in modern),
            "total": sum(int(match.group(4)) for match in modern),
        }

    pytest_match = re.search(
        r"(?:(\d+)\s+failed(?:,\s*)?)?(?:(\d+)\s+passed)(?:,\s*(\d+)\s+skipped)?",
        text,
        re.IGNORECASE,
    )
    if pytest_match:
        failed = int(pytest_match.group(1) or 0)
        passed = int(pytest_match.group(2) or 0)
        skipped = int(pytest_match.group(3) or 0)
        return {"passed": passed, "failed": failed, "skipped": skipped, "total": passed + failed + skipped}
    return {}


def _completion_commit_hash(results: list[OperationResult]) -> str:
    """Return the commit created by this run when available."""
    import re

    for result in reversed(results):
        if result.name not in {"git-commit", "commit"}:
            continue
        metadata = result.metadata or {}
        for key in ("commit", "commit_hash", "git_commit", "sha"):
            value = str(metadata.get(key) or "").strip()
            if re.fullmatch(r"[0-9a-f]{7,40}", value, re.IGNORECASE):
                return value
        for pattern in (
            r"^\[[^\]\n]*?\s([0-9a-f]{7,40})\]\s",
            r"^\[detached HEAD\s+([0-9a-f]{7,40})\]\s",
            r"\bcommit(?:ted)?(?:\s+as)?\s+([0-9a-f]{7,40})\b",
        ):
            match = re.search(pattern, result.output or "", re.IGNORECASE | re.MULTILINE)
            if match:
                return match.group(1)
    return ""


def _completion_overlays(results: list[OperationResult]) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, str], dict[str, Any]] = {}
    for result in results:
        metadata = result.metadata or {}
        raw_index = metadata.get("overlay_index")
        if raw_index is None:
            continue
        try:
            index = int(raw_index)
        except (TypeError, ValueError):
            continue
        artifact = str(metadata.get("overlay_artifact") or "").strip()
        key = (index, artifact)
        item = grouped.setdefault(
            key,
            {
                "index": index,
                "total": int(metadata.get("overlay_total") or 1),
                "label": str(metadata.get("overlay_label") or f"Overlay {index}"),
                "target": str(metadata.get("overlay_target") or ""),
                "artifact": artifact,
            },
        )
        if result.returncode != 0 and result.name != "apply-rollback":
            item["failed"] = True
    return [grouped[key] for key in sorted(grouped, key=lambda value: value[0])]


def _completion_payload(*, ok: bool, totals: dict[str, int], results: list[OperationResult]) -> dict[str, Any]:
    """Build a stable, descriptive final result for every HUD renderer."""
    tests = _completion_test_totals(results)
    commit_hash = _completion_commit_hash(results)
    overlays = _completion_overlays(results)
    duration_s = sum(max(0.0, float(result.duration_seconds or 0.0)) for result in results)
    warnings = int(totals.get("warnings", 0) or 0)
    errors = int(totals.get("errors", 0) or 0)
    failed = int(totals.get("failed", 0) or 0)

    warning_details: list[str] = []
    diagnostics: list[dict[str, Any]] = []
    seen: set[str] = set()
    for result in results:
        for issue in result.issues:
            location = issue.file or ""
            if issue.line is not None and location not in {"", "-"}:
                location = f"{location}:{issue.line}"
            code = f" {issue.code}" if issue.code else ""
            prefix = f"{location}: " if location not in {"", "-"} else ""
            detail = f"{prefix}{issue.severity}{code}: {issue.message}".strip()
            key = detail.casefold()
            if key in seen:
                continue
            seen.add(key)
            diagnostics.append({
                "severity": issue.severity,
                "code": issue.code or "",
                "message": issue.message,
                "file": issue.file or "",
                "line": issue.line,
                "phase": result.name,
                "detail": detail,
                "cause": result.failure_kind or str((result.metadata or {}).get("failure_reason") or ""),
                "related_node": str((result.metadata or {}).get("workflow_node_key") or ""),
                "technical_details": f"{result.name} exited {result.returncode}" if result.returncode else detail,
                "evidence": {"exit_code": result.returncode, "failure_kind": result.failure_kind or ""},
            })
            if issue.severity == "warning":
                warning_details.append(detail)

    rollback_results = [result for result in results if result.name == "apply-rollback"]
    stale_recovery = any(result.failure_kind == "stale_apply_rolled_back" for result in rollback_results)
    rollback_succeeded = any(
        result.returncode == 0 and result.failure_kind != "stale_apply_rolled_back"
        for result in rollback_results
    )
    rollback_failed = any(
        result.returncode != 0 and result.failure_kind != "stale_apply_rolled_back"
        for result in rollback_results
    )
    failed_result = next(
        (result for result in results if result.returncode != 0 and result.name != "apply-rollback"),
        None,
    )

    actions: list[dict[str, Any]] = []
    for result in results:
        if result.name == "git-add":
            continue
        action_status = _result_status(result)
        if result.name == "apply-rollback" and result.failure_kind == "stale_apply_rolled_back":
            action_status = "ok"
        actions.append({
            "name": result.name,
            "status": action_status,
            "duration_s": round(float(result.duration_seconds or 0.0), 3),
        })

    if ok:
        headline = "Completed successfully"
    elif stale_recovery:
        headline = "Recovered interrupted apply; rerun command"
    elif rollback_succeeded and not rollback_failed:
        phase_name = _human_phase_name(failed_result.name) if failed_result is not None else "Validation"
        headline = f"{phase_name} failed; changes rolled back"
    elif rollback_failed:
        headline = "Validation failed and rollback failed"
    else:
        headline = "Completed with errors"
    detail_parts: list[str] = []
    if overlays:
        if len(overlays) == 1:
            detail_parts.append(f"overlay {overlays[0]['label']}")
        else:
            detail_parts.append(f"{len(overlays)} overlays applied")
    if tests:
        detail_parts.append(
            f"{tests.get('passed', 0)} passed, {tests.get('failed', 0)} failed, "
            f"{tests.get('skipped', 0)} skipped ({tests.get('total', 0)} tests)"
        )
    detail_parts.append(f"{warnings} warning{'s' if warnings != 1 else ''}")
    if errors:
        detail_parts.append(f"{errors} error{'s' if errors != 1 else ''}")
    elif failed:
        detail_parts.append(f"{failed} failed step{'s' if failed != 1 else ''}")
    if commit_hash:
        detail_parts.append(f"commit {commit_hash[:12]}")
    detail_parts.append(f"{duration_s:.2f}s")

    return {
        "ok": ok,
        "headline": headline,
        "detail": " • ".join(detail_parts),
        "duration_s": round(duration_s, 3),
        "warnings": warnings,
        "errors": errors,
        "failed": failed,
        "tests": tests,
        "commit_hash": commit_hash,
        "overlays": overlays,
        "warning_details": warning_details,
        "diagnostics": diagnostics,
        "actions": actions,
        "rolled_back": rollback_succeeded,
        "rollback_failed": rollback_failed,
        "failure_phase": failed_result.name if failed_result is not None else "",
    }


def _run_quiet(argv: list[str]) -> bool:
    try:
        completed = subprocess.run(
            argv,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except OSError:
        return False
    return completed.returncode == 0


def _human_phase_name(value: str) -> str:
    key = (value or "").strip().lower().replace("_", "-")
    mapping = {
        "restore": "Restore",
        "build": "Build",
        "test": "Tests",
        "lint": "Lint",
        "validate": "Validation",
        "apply-overlay": "Apply overlay",
        "apply-snapshot": "Apply snapshot",
        "commit": "Commit",
    }
    return mapping.get(key, (value or "devtool").strip().replace("-", " ").title())


def _human_detail(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    replacements = {
        "restore: determining projects": "Finding projects to restore",
        "restore: all projects up-to-date": "All projects are already restored",
        "restore completed": "Restore completed",
        "build: msbuild starting": "Starting MSBuild",
        "test: starting execution": "Starting tests",
        "all projects up-to-date": "All projects are already up to date",
    }
    if lowered in replacements:
        return replacements[lowered]
    for prefix, pretty in (("restored ", "Restored "), ("up-to-date ", "Already up to date: "), ("built ", "Built "), ("testing ", "Testing ")):
        if lowered.startswith(prefix):
            remainder = text[len(prefix):]
            # Hide noisy output paths in human UI: ``Project -> bin/...`` becomes ``Project``.
            if " -> " in remainder:
                remainder = remainder.split(" -> ", 1)[0]
            return pretty + remainder
    if lowered.startswith("tests:"):
        import re
        match = re.search(r"(\d+)\s+passed,\s*(\d+)\s+failed,\s*(\d+)\s+skipped", text, re.I)
        if match:
            passed, failed, skipped = map(int, match.groups())
            parts = [f"{passed} passed"]
            if failed:
                parts.append(f"{failed} failed")
            if skipped:
                parts.append(f"{skipped} skipped")
            return ("Tests failing: " if failed else "Tests passing: ") + ", ".join(parts)
        return text[0].upper() + text[1:]
    if lowered.startswith("a total of ") and "test files matched" in lowered:
        return "Found test files"
    return text


def _coalesced_dotnet_validation_detail(metadata: dict[str, Any]) -> str:
    if not bool(metadata.get("coalesced_dotnet_validation")):
        return ""
    components = [str(item).strip() for item in metadata.get("validation_components") or [] if str(item).strip()]
    if not components:
        components = ["restore", "build", "test"]
    component_text = " + ".join(components)
    return f"{component_text} via dotnet test"


def _human_event_message(
    event_type: str,
    *,
    status: str = "",
    phase_name: str = "",
    message: str = "",
    metadata: dict[str, Any] | None = None,
) -> str:
    human_phase = _human_phase_name(phase_name) if phase_name else "devtool"
    clean = _human_detail(message)
    normalized = (event_type or "").replace("_", "-")
    status_key = (status or "").lower()
    event_metadata = metadata or {}
    detail = _coalesced_dotnet_validation_detail(event_metadata)
    phase_index = int(event_metadata.get("validation_phase_index") or 0)
    phase_total = int(event_metadata.get("validation_phase_total") or 0)
    counter = f"Phase {phase_index} of {phase_total} · " if phase_index > 0 and phase_total > 0 else ""
    if normalized == "phase-started":
        if detail:
            return f"{counter}Starting {human_phase}: {detail}"
        return f"{counter}Starting {human_phase}"
    if normalized == "phase-finished":
        if status_key == "failed":
            if detail:
                return f"{counter}{human_phase} failed: {detail}"
            return f"{counter}{human_phase} failed"
        if status_key == "skipped":
            if detail:
                return f"{counter}{human_phase} skipped: {detail}"
            return f"{counter}{human_phase} skipped"
        if detail:
            return f"{counter}{human_phase} completed: {detail}"
        return f"{counter}{human_phase} completed"
    if normalized == "finished":
        return "Devtool finished successfully" if status_key == "ok" else "Devtool finished with errors"
    return clean or f"{human_phase} is running"

@dataclass(slots=True)
class TaskerProgressSink:
    directory: Path
    command: str
    repo_root: str
    repo_name: str
    target: str = ""
    project_type: str = "auto"
    execution_environment: str = "auto"
    log_path: str = ""
    targets: list[str] = field(default_factory=list)
    planned_phases: list[dict[str, Any]] = field(default_factory=list)
    ui_presentations: dict[str, dict[str, Any]] = field(default_factory=dict)
    enabled: bool = True
    notify_progress: bool = False
    notification_id: str = DEFAULT_NOTIFICATION_ID
    overlay: bool = False
    event_http_url: str = DEFAULT_EVENT_HTTP_URL
    update_interval_s: float = 0.10
    notification_interval_s: float = 0.75
    run_id: str = field(default_factory=_default_run_id)
    sequence: int = 0
    current_phase: dict[str, Any] | None = None
    failed_phase: dict[str, Any] | None = None
    started_at: str = field(default_factory=utc_now_iso)
    status: str = "running"
    last_error: str = ""
    _last_update_at: float = 0.0
    _last_notification_at: float = 0.0
    _last_state: dict[str, Any] = field(default_factory=dict)
    _phase_progress: dict[str, dict[str, Any]] = field(default_factory=dict)
    _process_start: str = field(default_factory=lambda: _process_start_token(os.getpid()))
    _boot_id: str = field(default_factory=_boot_id)
    _heartbeat_interval_s: float = DEFAULT_RUN_HEARTBEAT_INTERVAL_S
    _heartbeat_stop: threading.Event = field(default_factory=threading.Event)
    _heartbeat_thread: threading.Thread | None = None
    _cancel_requested: threading.Event = field(default_factory=threading.Event)
    _ipc_publisher: IpcPublisher | None = None

    @property
    def state_file(self) -> Path:
        return self.directory / "state.json"

    @property
    def latest_event_file(self) -> Path:
        return self.directory / "latest_event.json"

    @property
    def notification_file(self) -> Path:
        # Simple Tasker bridge: watch this file, parse title/text/current/total.
        return self.directory / "notification.json"

    @property
    def events_jsonl_file(self) -> Path:
        return self.directory / "events.jsonl"

    @property
    def events_dir(self) -> Path:
        return self.directory / "events"

    @property
    def runs_dir(self) -> Path:
        return self.directory / "runs"

    def reset_run_files(self) -> None:
        """Initialize the one active HUD run and discard stale live state."""
        try:
            for path in (self.state_file, self.latest_event_file, self.notification_file, self.events_jsonl_file):
                path.unlink(missing_ok=True)
            if self.events_dir.exists():
                shutil.rmtree(self.events_dir, ignore_errors=True)
            if self.runs_dir.exists():
                shutil.rmtree(self.runs_dir, ignore_errors=True)
            self.events_dir.mkdir(parents=True, exist_ok=True)
            self.runs_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self.sequence = 0
        self.current_phase = None
        self.failed_phase = None
        self.planned_phases = []
        self.targets = [self.target] if self.target else []
        self._last_state = {}
        self._phase_progress = {}
        self._last_update_at = 0.0
        self._cancel_requested.clear()

    def _run_file(self) -> Path:
        return self.runs_dir / f"{self.run_id}.json"

    def _start_heartbeat(self) -> None:
        if self._heartbeat_thread is not None and self._heartbeat_thread.is_alive():
            return
        self._heartbeat_stop.clear()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name=f"devtool-hud-heartbeat-{self.run_id[:8]}",
            daemon=True,
        )
        self._heartbeat_thread.start()
        atexit.register(self._cleanup_run_file)

    def _heartbeat_loop(self) -> None:
        while not self._heartbeat_stop.wait(max(1.0, float(self._heartbeat_interval_s))):
            if not self._last_state:
                continue
            payload = dict(self._last_state)
            payload["heartbeat_at"] = utc_now_iso()
            payload["pid"] = os.getpid()
            payload["process_start"] = self._process_start
            payload["boot_id"] = self._boot_id
            try:
                _atomic_write_json(self._run_file(), payload)
                self._last_state = payload
            except OSError as exc:
                self.last_error = str(exc)

    def _stop_heartbeat(self) -> None:
        self._heartbeat_stop.set()
        thread = self._heartbeat_thread
        self._heartbeat_thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=0.25)

    def _cleanup_run_file(self) -> None:
        self._stop_heartbeat()
        with contextlib.suppress(OSError):
            self._run_file().unlink(missing_ok=True)

    def request_cancel(self) -> None:
        self._cancel_requested.set()

    def cancel_requested(self) -> bool:
        return self._cancel_requested.is_set()

    def clear_cancel(self) -> None:
        self._cancel_requested.clear()

    def wait_for_cancel(self, timeout: float | None = None) -> bool:
        return self._cancel_requested.wait(timeout)

    def _publish_ipc(self, state: dict[str, Any]) -> None:
        publisher = self._ipc_publisher
        if publisher is None:
            publisher = IpcPublisher(self.run_id, on_cancel=self.request_cancel)
            self._ipc_publisher = publisher
        publisher.publish(state)

    def prepare_hud(self) -> None:
        """Publish the new run identity before a singleton HUD follows it.

        Without this preflight record, the desktop HUD can resolve the shared
        active directory while it still contains the previous run's state and
        briefly render that stale completion before ``started()`` is emitted.
        """
        self.reset_run_files()
        self.emit(
            "preparing",
            status="waiting",
            message=f"Preparing {self.command}",
            progress={"percent": None},
            force=True,
            notify=False,
        )

    def started(self, *, message: str = "started") -> None:
        self.reset_run_files()
        self.emit("started", status="running", message=message, force=True, notify=True)
        self._start_heartbeat()

    def set_targets(self, values: list[str]) -> None:
        normalized: list[str] = []
        for value in values:
            clean = str(value or "").strip()
            if clean and clean not in normalized:
                normalized.append(clean)
        if normalized:
            self.targets = normalized
            self.target = normalized[0]

    def plan_phases(self, phases: list[dict[str, Any]], *, append: bool = True) -> None:
        planned = list(self.planned_phases) if append else []
        existing = {(str(item.get("name") or ""), str(item.get("target") or ""), str(item.get("workspace") or "")) for item in planned}
        for item in phases:
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            target = str(item.get("target") or "").strip()
            workspace = str(item.get("workspace") or "").strip()
            key = (name, target, workspace)
            if key in existing:
                continue
            existing.add(key)
            planned.append({
                "name": name,
                "target": target,
                "workspace": workspace,
                "workspace_root": str(item.get("workspace_root") or "").strip(),
                "workflow": str(item.get("workflow") or item.get("workflow_name") or "").strip(),
                "node_id": str(item.get("node_id") or item.get("workflow_node_key") or "").strip(),
                "parent_id": str(item.get("parent_id") or "").strip(),
                "runner": str(item.get("runner") or item.get("workflow_runner") or "").strip(),
                "execution_environment": str(item.get("execution_environment") or "").strip(),
                "depends_on": [str(value) for value in item.get("depends_on", []) or [] if str(value).strip()],
            })
        self.planned_phases = planned
        self.set_targets([str(item.get("target") or item.get("workspace") or "") for item in planned] + self.targets)

    @staticmethod
    def _phase_progress_key(phase: dict[str, Any] | None) -> str:
        phase = phase or {}
        name = str(phase.get("name") or "").strip()
        workspace = str(phase.get("workspace") or "").strip()
        target = str(phase.get("target") or "").strip()
        return "\x1f".join((name, workspace, target))

    def _record_phase_progress(
        self,
        *,
        phase: dict[str, Any] | None = None,
        status: str | None = None,
        detail: str | None = None,
        completed: int | None = None,
        total: int | None = None,
        percent: float | None = None,
        warnings: int | None = None,
        errors: int | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> None:
        source = dict(phase or self.current_phase or {})
        if not source.get("name"):
            return
        key = self._phase_progress_key(source)
        current = dict(self._phase_progress.get(key) or {})
        current.update({
            "name": str(source.get("name") or ""),
            "target": str(source.get("target") or ""),
            "workspace": str(source.get("workspace") or ""),
            "workspace_root": str(source.get("workspace_root") or ""),
            "workflow": str(source.get("workflow") or ""),
            "node_id": str(source.get("node_id") or ""),
            "parent_id": str(source.get("parent_id") or ""),
            "runner": str(source.get("runner") or ""),
            "execution_environment": str(source.get("execution_environment") or ""),
            "depends_on": [str(value) for value in source.get("depends_on", []) or [] if str(value).strip()],
            "index": source.get("index"),
            "phase_total": source.get("total"),
            "started_at": str(source.get("started_at") or current.get("started_at") or ""),
            "finished_at": str(source.get("finished_at") or current.get("finished_at") or ""),
        })
        if status is not None:
            current["status"] = status
        if detail is not None:
            current["detail"] = detail
        if completed is not None:
            current["completed"] = completed
        if total is not None:
            current["total"] = total
        if percent is None and completed is not None and total:
            percent = max(0.0, min(100.0, completed / total * 100.0))
        if percent is not None:
            current["percent"] = max(0.0, min(100.0, float(percent)))
        if warnings is not None:
            current["warnings"] = max(0, int(warnings))
        if errors is not None:
            current["errors"] = max(0, int(errors))
        if metrics:
            for metric_key in (
                "command",
                "unit_label",
                "tests_passed",
                "tests_failed",
                "tests_skipped",
                "tests_total",
                "test_assemblies",
                "requested_tasks",
                "requested_task_count",
                "dependency_task_count",
            ):
                value = metrics.get(metric_key)
                if value is None:
                    continue
                if metric_key == "requested_tasks":
                    if isinstance(value, list):
                        current[metric_key] = [str(item) for item in value if str(item)]
                    continue
                if metric_key in {
                    "tests_passed",
                    "tests_failed",
                    "tests_skipped",
                    "tests_total",
                    "test_assemblies",
                    "requested_task_count",
                    "dependency_task_count",
                }:
                    current[metric_key] = max(0, int(value))
                else:
                    current[metric_key] = str(value)
        current["updated_at"] = utc_now_iso()
        self._phase_progress[key] = current

    def phase_started(self, name: str, *, index: int, total: int, argv: list[str] | None = None, metadata: dict[str, Any] | None = None) -> None:
        metadata = dict(metadata or {})
        index = int(metadata.get("validation_phase_index") or index)
        total = int(metadata.get("validation_phase_total") or total)
        display_name = str(metadata.get("validation_phase_name") or name).strip() or name
        target = str(metadata.get("bundle_target") or metadata.get("workflow_target") or metadata.get("target") or metadata.get("matrix") or "").strip()
        workspace = str(metadata.get("workspace") or metadata.get("workflow_workspace") or "").strip()
        if not workspace and isinstance(metadata.get("workspace_targets"), (list, tuple)):
            workspace = ",".join(str(value) for value in metadata.get("workspace_targets") or [] if str(value).strip())
        if target:
            self.set_targets(self.targets + [target])
        self.current_phase = {
            "name": display_name,
            "command_name": name,
            "index": index,
            "total": total,
            "argv": argv or [],
            "target": target,
            "workspace": workspace,
            "workspace_root": str(metadata.get("workspace_root") or "").strip(),
            "workflow": str(metadata.get("workflow_name") or "").strip(),
            "node_id": str(metadata.get("workflow_node_key") or "").strip(),
            "parent_id": str(metadata.get("workflow_parent_id") or "").strip(),
            "runner": str(metadata.get("workflow_runner") or metadata.get("runner") or "").strip(),
            "execution_environment": str(metadata.get("execution_environment") or metadata.get("effective_execution_environment") or "").strip(),
            "depends_on": [str(value) for value in metadata.get("workflow_depends_on", []) or [] if str(value).strip()],
            "validation_session_id": str(metadata.get("validation_session_id") or "").strip(),
            "validation_phase_key": str(metadata.get("validation_phase_key") or "").strip(),
            "started_at": utc_now_iso(),
        }
        self._record_phase_progress(phase=self.current_phase, status="running", completed=0)
        self.emit("phase_started", status="running", phase=self.current_phase, progress={"percent": None, "current": 0, "completed": 0, "total": None, "target": target}, message=_human_event_message("phase_started", phase_name=display_name, metadata=metadata), force=True, notify=True)

    def progress(
        self,
        *,
        detail: str = "",
        completed: int | None = None,
        total: int | None = None,
        percent: float | None = None,
        target: str = "",
        warnings: int | None = None,
        errors: int | None = None,
        metrics: dict[str, Any] | None = None,
        force: bool = False,
    ) -> None:
        now = time.monotonic()
        if not force and now - self._last_update_at < self.update_interval_s:
            return
        self._last_update_at = now
        if percent is None and completed is not None and total:
            percent = max(0.0, min(100.0, completed / total * 100.0))
        payload = {
            "percent": percent,
            "current": completed,
            "completed": completed,
            "total": total,
            "detail": detail,
        }
        if target:
            payload["target"] = target
        issue_totals: dict[str, int] = {}
        if warnings is not None:
            issue_totals["warnings"] = max(0, int(warnings))
        if errors is not None:
            issue_totals["errors"] = max(0, int(errors))
        self._record_phase_progress(
            status="running",
            detail=detail,
            completed=completed,
            total=total,
            percent=percent,
            warnings=warnings,
            errors=errors,
            metrics=metrics,
        )
        self.emit("progress", status="running", phase=self.current_phase, progress=payload, totals=issue_totals, message=detail, notify=True)

    def phase_finished(self, result: OperationResult) -> None:
        status = _result_status(result)
        phase = dict(self.current_phase or {"name": result.name})
        phase["finished_at"] = utc_now_iso()
        if result.returncode != 0 and result.name != "apply-rollback":
            self.failed_phase = dict(phase)
        phase_key = self._phase_progress_key(phase)
        previous = self._phase_progress.get(phase_key) or {}
        final_total = previous.get("total")
        final_completed = previous.get("completed")
        if status in {"ok", "failed", "skipped"} and final_total is not None:
            final_completed = final_total
        self._record_phase_progress(
            phase=phase,
            status=status,
            completed=final_completed if isinstance(final_completed, int) else None,
            total=final_total if isinstance(final_total, int) else None,
            percent=100.0 if status in {"ok", "failed", "skipped"} else None,
        )
        self.emit(
            "phase_finished",
            status="running" if result.returncode == 0 else "failed",
            phase=phase,
            result=_result_to_json(result),
            message=_human_event_message("phase_finished", status=status, phase_name=result.name, metadata=result.metadata),
            force=True,
            notify=True,
        )

    def _finalize_successful_pipeline(self) -> None:
        """Make the terminal success snapshot authoritative for every planned phase.

        A few workflows finish after commit/validator work that does not emit a
        normal phase_finished event.  Leaving those planned entries pending made
        native HUDs correctly show SUCCESS but only 80% progress.
        """
        for planned in self.planned_phases:
            phase = dict(planned)
            if not phase.get("name"):
                continue
            key = self._phase_progress_key(phase)
            previous = dict(self._phase_progress.get(key) or {})
            final_total = previous.get("total")
            final_completed = previous.get("completed")
            if isinstance(final_total, int) and final_total >= 0:
                final_completed = final_total
            self._record_phase_progress(
                phase=phase,
                status="ok",
                detail=str(previous.get("detail") or "Completed"),
                completed=final_completed if isinstance(final_completed, int) else None,
                total=final_total if isinstance(final_total, int) else None,
                percent=100.0,
                warnings=int(previous.get("warnings") or 0),
                errors=int(previous.get("errors") or 0),
            )

        # Also finalize any dynamically discovered phase that was not in the
        # original plan (custom validators and commit phases are common).
        for key, previous_value in list(self._phase_progress.items()):
            previous = dict(previous_value)
            if str(previous.get("status") or "").lower() in {"failed", "cancelled", "interrupted"}:
                continue
            final_total = previous.get("total")
            if isinstance(final_total, int) and final_total >= 0:
                previous["completed"] = final_total
            previous["status"] = "ok"
            previous["percent"] = 100.0
            previous["updated_at"] = utc_now_iso()
            self._phase_progress[key] = previous

    def finished(self, *, ok: bool, totals: dict[str, int], results: list[OperationResult], notification: dict[str, Any] | None = None) -> None:
        cancelled = any(int(result.returncode or 0) == 130 for result in results if result.name != "apply-rollback")
        if ok:
            self._finalize_successful_pipeline()
        self.status = "ok" if ok else ("cancelled" if cancelled else "failed")
        completion = _completion_payload(ok=ok, totals=totals, results=results)
        if cancelled:
            completion["headline"] = "Cancelled" + ("; changes rolled back" if completion.get("rolled_back") else "")
        final_notification = dict(notification or {})
        final_notification.update({
            "title": completion["headline"],
            "text": completion["detail"],
            "body": completion["detail"],
            "expanded_title": completion["headline"],
            "expanded_text": completion["detail"],
        })
        self.emit(
            "finished",
            status=self.status,
            phase=(self.failed_phase if not ok and self.failed_phase else self.current_phase),
            totals=totals,
            results=[_result_to_json(result) for result in results],
            completion=completion,
            progress={"percent": 100.0 if ok else None},
            notification=final_notification,
            message=completion["headline"],
            force=True,
            notify=True,
        )
        # Global state files retain the completion result; the heartbeat file
        # is only needed while the single logical run is active.
        self._cleanup_run_file()
        if self._ipc_publisher is not None:
            self._ipc_publisher.close(unregister=True)
            self._ipc_publisher = None

    def emit(
        self,
        event_type: str,
        *,
        status: str,
        message: str = "",
        phase: dict[str, Any] | None = None,
        progress: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        results: list[dict[str, Any]] | None = None,
        totals: dict[str, Any] | None = None,
        completion: dict[str, Any] | None = None,
        notification: dict[str, Any] | None = None,
        force: bool = False,
        notify: bool = False,
    ) -> None:
        if not self.enabled:
            return
        self.sequence += 1
        timestamp = utc_now_iso()
        event_id = f"{self.run_id}-{self.sequence:05d}"
        if phase is not None:
            self.current_phase = phase
        current, total = _progress_current_total(progress)
        # Only progress updates may inherit the previous current/total. Phase
        # changes and final events must not reuse stale counts like 0/9 or 9/31.
        if event_type == "progress" and self._last_state:
            if current is None:
                current = self._last_state.get("current")
            if total is None:
                total = self._last_state.get("total")
        if event_type == "finished" and status == "ok" and total is not None:
            current = total
        progress_target = str((progress or {}).get("target") or "").strip()
        display_target = progress_target or self.target or None
        notification_payload = notification or self._notification_payload(status, message, progress)
        simple_notification = _simple_notification_payload(notification_payload, message=message, current=current, total=total)
        if display_target:
            simple_notification["target"] = display_target
        # Keep the lightweight notification snapshot ordered with the rich
        # state. Android/desktop readers can then select the newest phase by
        # sequence instead of incorrectly preferring a stale phase merely
        # because its local percentage was higher.
        simple_notification.update({
            "run_id": self.run_id,
            "sequence": self.sequence,
            "updated_at": timestamp,
            "status": status,
            "command": self.command,
            "phase": phase if phase is not None else self.current_phase,
        })

        state = {
            "schema_version": 1,
            "producer": "devtool",
            "run_id": self.run_id,
            "pid": os.getpid(),
            "process_start": self._process_start,
            "boot_id": self._boot_id,
            "heartbeat_at": timestamp,
            "event_id": event_id,
            "sequence": self.sequence,
            "event_type": event_type,
            "status": status,
            "command": self.command,
            "repo_root": self.repo_root,
            "repo_name": self.repo_name,
            "target": display_target,
            "project_type": self.project_type,
            "execution_environment": self.execution_environment,
            "targets": list(self.targets),
            "pipeline": list(self.planned_phases),
            "phase_progress": [dict(item) for item in self._phase_progress.values()],
            "overlay": self.overlay,
            "started_at": self.started_at,
            "updated_at": timestamp,
            "finished_at": timestamp if event_type == "finished" else None,
            "message": message,
            "log_path": self.log_path,
            "current": current,
            "total": total,
            "phase": phase if phase is not None else self.current_phase,
            "progress": progress or {},
            "result": result,
            "results": results,
            "totals": totals or {},
            "completion": completion or {},
            "notification": notification_payload,
            "tasker_notification": simple_notification,
            "files": {
                "state": str(self.state_file),
                "latest_event": str(self.latest_event_file),
                "notification": str(self.notification_file),
                "events_jsonl": str(self.events_jsonl_file),
                "events_dir": str(self.events_dir),
            },
        }
        # UI01-UI04: publish one semantic UI snapshot beside the legacy run
        # fields. Resolved per-surface presentation is carried across the
        # process boundary so repo/global/profile overrides stay consistent in
        # separately running HUD/Android consumers without reparsing config.
        if self.ui_presentations:
            state["ui_presentations"] = {key: dict(value) for key, value in self.ui_presentations.items()}
        machine_preferences = self.ui_presentations.get("machine") if self.ui_presentations else None
        state["ui"] = runtime_state_to_ui_snapshot(
            state, surface="machine", preferences=machine_preferences
        ).as_dict()
        event = dict(state)
        event["event_file"] = None
        event_file = self.events_dir / f"{timestamp.replace(':', '').replace('.', '-')}-{self.sequence:05d}-{_safe_name(event_type)}.json"
        event["event_file"] = str(event_file)
        state["event_file"] = str(event_file)
        if self.enabled:
            try:
                _atomic_write_json(self.state_file, state)
                _atomic_write_json(self.latest_event_file, event)
                _atomic_write_json(self.notification_file, simple_notification)
                _atomic_write_json(event_file, event)
                _append_jsonl(self.events_jsonl_file, event)
                send_local_http_event(event, url=self.event_http_url)
                _atomic_write_json(self._run_file(), state)
                self._last_state = state
                self._publish_ipc(state)
            except OSError as exc:
                self.last_error = str(exc)


    def _notification_payload(self, status: str, message: str, progress: dict[str, Any] | None) -> dict[str, Any]:
        phase_name = str((self.current_phase or {}).get("name") or self.command or "work")
        human_phase = _human_phase_name(phase_name)
        if status == "running":
            title = f"{human_phase} in progress"
        elif status == "ok":
            title = f"{human_phase} complete"
        elif status == "failed":
            title = f"{human_phase} failed"
        else:
            title = f"{human_phase}: {status}"

        current, total = _progress_current_total(progress)
        progress_text = ""
        percent = None if not progress else progress.get("percent")
        if current is not None and total is not None:
            progress_text = f"{current}/{total}"
            if isinstance(percent, (int, float)):
                progress_text += f" ({percent:.0f}%)"
        elif isinstance(percent, (int, float)):
            progress_text = f"{percent:.0f}%"

        detail = _human_detail(message)
        bits = []
        if self.repo_name:
            bits.append(self.repo_name)
        progress_target = str((progress or {}).get("target") or self.target or "").strip()
        if progress_target:
            bits.append(progress_target)
        if progress_text:
            bits.append(progress_text)
        if detail:
            bits.append(detail)
        body = " • ".join(bits) or "Waiting for progress"
        return {"title": title, "text": body, "expanded_title": title, "expanded_text": body, "body": body, "id": self.notification_id}


def build_tasker_sink(
    *,
    enabled: bool,
    directory: str,
    command: str,
    repo_root: Path,
    target: str = "",
    project_type: str = "auto",
    execution_environment: str = "auto",
    notify_progress: bool = False,
    notification_id: str = DEFAULT_NOTIFICATION_ID,
    run_id: str = "",
    overlay: bool = False,
    ui_presentations: dict[str, dict[str, Any]] | None = None,
) -> TaskerProgressSink | None:
    if not enabled:
        return None
    repo = repo_root.resolve()
    return TaskerProgressSink(
        directory=tasker_dir_from_text(directory),
        command=command,
        repo_root=str(repo),
        repo_name=repo.name,
        target=target,
        project_type=(project_type or "auto").strip().lower(),
        execution_environment=(execution_environment or "auto").strip().lower(),
        enabled=enabled,
        notify_progress=False,
        notification_id=notification_id,
        run_id=run_id.strip() or _default_run_id(),
        overlay=overlay,
        ui_presentations={key: dict(value) for key, value in (ui_presentations or {}).items()},
    )
