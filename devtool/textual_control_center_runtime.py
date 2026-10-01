from __future__ import annotations

"""Concrete Textual control-center runtime for PRES-R02.

The control center is deliberately a projection and control client.  Canonical
execution, validation, artifact, worker, log, and evidence truth remains owned
by the existing Devtool authorities.  This module joins those authorities into
one bounded UI snapshot and wires UI requests back through their existing
control contracts.
"""

from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence
import json
import os
import subprocess
import sys
import threading
import time

from .artifacts import load_history
from .clipboard import copy_text
from .core.events import get_execution_store, tui_event_snapshot
from .core_runtime import private_run_root
from .ipc import request_run_action, request_run_cancel
from .logstore import logs_dir
from .overlay import check_overlay
from .run_store import RunCatalog, RunQuery
from .ssh_provider_evidence import load_all_provider_evidence
from .validation_control import status_api_payload
from .validation_sessions import load_active_session, write_control_request
from .transactions import list_active_transaction_records, rollback_transaction, resume_transaction_result_publication
from .xdg import state_dir
from .textual_ux import (
    ArtifactActivityTracker, TEXTUAL_SHARED_CSS, artifact_display_progress,
    artifact_review_text, artifact_running_summary, artifact_stage_compact_line,
    artifact_stage_line, fit_middle, is_compact_width, progress_counter,
    progress_percent, responsive_class_names, status_label,
)
from .ui_core.shortcuts import (
    TUI_NAV_SHORTCUTS, TUI_PAGES, normalize_tui_page, shortcut_rows,
    textual_control_center_bindings,
)

CONTROL_CENTER_PAGES: tuple[str, ...] = TUI_PAGES

RUN_ACTION_MAP: dict[str, str] = {
    "retry": "operation.retry",
    "rerun": "operation.rerun",
    "resume": "validation.resume",
}


def _safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if is_dataclass(value):
        if hasattr(value, "to_dict"):
            try:
                return _safe(value.to_dict())
            except Exception:
                pass
        return _safe(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_safe(item) for item in value]
    enum_value = getattr(value, "value", None)
    if enum_value is not None and enum_value is not value:
        return _safe(enum_value)
    return str(value)


def _text(value: Any) -> str:
    return str(value if value is not None else "")


def _run_id(item: Mapping[str, Any]) -> str:
    return _text(item.get("run_id") or item.get("id") or item.get("run") or "").strip()


def _status(item: Mapping[str, Any]) -> str:
    return _text(item.get("status") or item.get("state") or "unknown").strip().lower() or "unknown"


def _merge_run(target: dict[str, Any], source: Mapping[str, Any]) -> None:
    for key, value in source.items():
        if value not in (None, "", [], {}, ()) or key not in target:
            target[str(key)] = _safe(value)


def _tail_file(path: Path, *, line_limit: int, byte_limit: int = 512 * 1024) -> list[str]:
    """Return a bounded UTF-8 tail without loading an unbounded log into memory."""
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max(1024, byte_limit)))
            data = handle.read(max(1024, byte_limit))
    except OSError:
        return []
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    return lines[-max(1, int(line_limit)) :]


def _latest_log_path() -> Path | None:
    try:
        rows = [item for item in logs_dir().glob("*.log") if item.is_file()]
    except OSError:
        return None
    if not rows:
        return None
    return max(rows, key=lambda item: item.stat().st_mtime if item.exists() else 0.0)


def _history_rows(repo_root: Path, limit: int) -> list[dict[str, Any]]:
    rows = [dict(item) for item in load_history(repo_root) if isinstance(item, dict)]
    rows.reverse()
    return rows[: max(1, int(limit))]


def _artifact_rows(history: Sequence[Mapping[str, Any]], execution: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for run in execution:
        for item in run.get("artifacts", []) if isinstance(run.get("artifacts"), list) else []:
            if not isinstance(item, Mapping):
                continue
            row = dict(item)
            row.setdefault("run_id", run.get("run_id", ""))
            key = _text(row.get("artifact_id") or row.get("path") or json.dumps(row, sort_keys=True, default=str))
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
    for item in history:
        path = _text(item.get("artifact") or item.get("path") or item.get("artifact_path") or "")
        key = _text(item.get("history_id") or item.get("operation_id") or item.get("sha256") or path)
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "artifact_id": key,
            "path": path,
            "artifact_type": item.get("type") or item.get("kind") or item.get("artifact_type") or "artifact",
            "verification_status": item.get("status") or item.get("result") or "recorded",
            "sha256": item.get("sha256") or "",
            "run_id": item.get("run_id") or "",
            "history": dict(item),
        })
    return rows[:200]


def _overlay_rows(history: Sequence[Mapping[str, Any]], repo_root: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in history:
        encoded = " ".join(
            _text(item.get(key)) for key in ("type", "kind", "command", "operation", "artifact", "path", "artifact_path")
        ).lower()
        if "overlay" not in encoded and _text(item.get("format")).lower() != "overlay":
            continue
        result.append(dict(item))
    try:
        transactions = list_active_transaction_records(repo_root)
    except Exception:
        transactions = []
    for tx in transactions:
        path = _text(tx.get("path"))
        result.insert(0, {
            "history_id": _text(tx.get("transaction_id") or path),
            "artifact": _text(tx.get("artifact_path") or tx.get("artifact") or tx.get("transaction_id") or "transaction"),
            "status": _text(tx.get("status") or "active"),
            "operation": "transaction",
            "applied_at": _text(tx.get("updated_at") or tx.get("started_at") or ""),
            "transaction_path": path,
            "kind": "active-transaction",
            "recovery_diagnostics": _safe(tx.get("recovery_diagnostics") or {}),
        })
    return result[:200]


def _evidence_rows(limit: int = 200) -> list[dict[str, Any]]:
    root = state_dir() / "evidence"
    if not root.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    try:
        children = sorted(root.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True)
    except OSError:
        return []
    for item in children[: max(1, int(limit))]:
        if not item.is_dir():
            continue
        summary = item / "evidence.json"
        payload: dict[str, Any] = {}
        try:
            raw = json.loads(summary.read_text(encoding="utf-8")) if summary.is_file() else {}
            payload = raw if isinstance(raw, dict) else {}
        except (OSError, json.JSONDecodeError):
            payload = {}
        rows.append({
            "evidence_id": item.name,
            "path": str(item),
            "run_id": payload.get("run_id") or item.name,
            "created_at": payload.get("created_at") or "",
            "file_count": len([entry for entry in item.iterdir() if entry.is_file()]) if item.is_dir() else 0,
            "summary": payload,
        })
    return rows


def _worker_rows() -> list[dict[str, Any]]:
    try:
        return [dict(item) for item in load_all_provider_evidence() if isinstance(item, Mapping)]
    except Exception:
        return []


def _catalog_rows(repo_root: Path, limit: int) -> list[dict[str, Any]]:
    try:
        catalog = RunCatalog.open(private_run_root(repo_root), create=False)
        return [dict(item) for item in catalog.query(RunQuery(limit=min(1000, max(1, int(limit)))))]
    except Exception:
        return []


def _execution_rows() -> list[dict[str, Any]]:
    try:
        return [dict(snapshot.to_dict()) for snapshot in get_execution_store().snapshots()]
    except Exception:
        return []


def _ipc_snapshot() -> dict[str, Any]:
    try:
        from .desktop.ipc_client import DesktopIpcClient
        payload = DesktopIpcClient(timeout_s=0.08).snapshot()
        return dict(payload) if isinstance(payload, dict) else {}
    except Exception:
        return {"ok": False, "runs": [], "error": "ipc unavailable"}


def _target_rows(repo_root: Path) -> list[dict[str, Any]]:
    try:
        from .config import load_project_config
        config = load_project_config(
            str(repo_root), None, target_name="",
            allow_ambiguous_target=True, allow_ambiguous_project_kind=True,
        )
    except Exception:
        return []
    rows: list[dict[str, Any]] = []
    for name, target in sorted(config.targets.items()):
        rows.append({
            "name": name,
            "kind": target.kind,
            "runner": target.runner,
            "platform": target.platform,
            "root": str(target.root or ""),
            "default": name == config.workspace.default_target,
            "workflows": sorted(target.workflows),
            "jobs": sorted(target.jobs),
        })
    return rows


def _validation_snapshot(repo_root: Path) -> dict[str, Any]:
    try:
        session = load_active_session(repo_root, required=False)
    except Exception as exc:
        return {"active": False, "error": str(exc), "phases": []}
    if session is None:
        return {"active": False, "phases": []}
    try:
        payload = status_api_payload(session, include_events=True, event_limit=50)
    except Exception as exc:
        payload = session.as_dict()
        payload["error"] = str(exc)
    payload["active"] = True
    return _safe(payload)


def _diagnostic_rows(execution: Sequence[Mapping[str, Any]], events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for run in execution:
        for item in run.get("diagnostics", []) if isinstance(run.get("diagnostics"), list) else []:
            if not isinstance(item, Mapping):
                continue
            row = dict(item)
            row.setdefault("run_id", run.get("run_id", ""))
            row.setdefault("summary", row.get("message") or row.get("code") or "diagnostic")
            row.setdefault("source", "execution-store")
            key = _text(row.get("diagnostic_id") or json.dumps(row, sort_keys=True, default=str))
            if key in seen:
                continue
            seen.add(key)
            result.append(row)
    for item in events:
        if not isinstance(item, Mapping):
            continue
        kind = _text(item.get("type") or item.get("event_type")).lower()
        if "diagnostic" not in kind and kind not in {"warningdetected", "diagnosticemitted"}:
            continue
        row = dict(item)
        row.setdefault("summary", row.get("message") or row.get("code") or kind)
        row.setdefault("source", "event-bus")
        key = _text(row.get("diagnostic_id") or row.get("event_id") or json.dumps(row, sort_keys=True, default=str))
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result[-300:]


def _nodes_by_run(runs: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        rid = _run_id(run)
        nodes = run.get("nodes")
        if rid and isinstance(nodes, list):
            result[rid] = [dict(item) for item in nodes if isinstance(item, Mapping)]
    return result


def _log_path_for_run(runs: Sequence[Mapping[str, Any]], selected_run: str) -> Path | None:
    if selected_run:
        for row in runs:
            if _run_id(row) != selected_run:
                continue
            for key in ("log_path", "log", "log_file"):
                raw = _text(row.get(key)).strip()
                if raw:
                    path = Path(raw).expanduser()
                    if path.is_file():
                        return path
            invocation = row.get("invocation_metadata")
            if isinstance(invocation, Mapping):
                raw = _text(invocation.get("log_path")).strip()
                if raw and Path(raw).expanduser().is_file():
                    return Path(raw).expanduser()
    return _latest_log_path()


def _startup_overlay_preflight(repo_root: Path, artifact: str) -> dict[str, Any] | None:
    path = str(artifact or "").strip()
    if not path:
        return None
    try:
        result = check_overlay(repo_root, path)
        return {
            "history_id": "startup-artifact",
            "artifact": result.artifact,
            "status": "ready" if result.ok else "blocked",
            "operation": "preflight",
            "kind": "startup-overlay-preflight",
            "structural_ok": result.structural_ok,
            "source_state_ok": result.source_state_ok,
            "conflict_count": len(result.conflicts),
            "rebaseable": result.rebaseable,
            "message": result.messages[-1] if result.messages else ("Overlay preflight passed." if result.ok else "Overlay preflight requires attention."),
            "messages": list(result.messages),
            "preview": _safe(result.preview),
            "manifest": _safe(result.manifest),
            "startup": True,
        }
    except Exception as exc:
        return {
            "history_id": "startup-artifact",
            "artifact": path,
            "status": "unavailable",
            "operation": "preflight",
            "kind": "startup-overlay-preflight",
            "error": str(exc),
            "message": f"Overlay preflight unavailable: {exc}",
            "startup": True,
        }


class ControlCenterProvider(Protocol):
    def snapshot(self, *, selected_run: str = "", selected_node: str = "") -> dict[str, Any]: ...


@dataclass(slots=True)
class CanonicalControlCenterProvider:
    repo_root: Path
    history_limit: int = 100
    log_line_limit: int = 1200
    startup_artifact: str = ""

    def __post_init__(self) -> None:
        self.repo_root = Path(self.repo_root).expanduser().resolve(strict=False)

    def snapshot(self, *, selected_run: str = "", selected_node: str = "") -> dict[str, Any]:
        execution = _execution_rows()
        ipc = _ipc_snapshot()
        catalog_rows = _catalog_rows(self.repo_root, self.history_limit)
        events = [dict(item) for item in tui_event_snapshot() if isinstance(item, Mapping)]

        by_run: dict[str, dict[str, Any]] = {}
        for item in execution:
            rid = _run_id(item)
            if rid:
                by_run.setdefault(rid, {"run_id": rid})
                _merge_run(by_run[rid], item)
        for item in ipc.get("runs", []) if isinstance(ipc.get("runs"), list) else []:
            if not isinstance(item, Mapping):
                continue
            rid = _run_id(item)
            if rid:
                by_run.setdefault(rid, {"run_id": rid})
                _merge_run(by_run[rid], item)
        for entry in catalog_rows:
            run = entry.get("run") if isinstance(entry.get("run"), Mapping) else {}
            rid = _run_id(run)
            if rid:
                by_run.setdefault(rid, {"run_id": rid})
                _merge_run(by_run[rid], run)
                if entry.get("streams"):
                    by_run[rid].setdefault("streams", entry.get("streams"))
                if entry.get("attachments"):
                    by_run[rid].setdefault("attachments", entry.get("attachments"))
        runs = list(by_run.values())
        runs.sort(key=lambda row: _text(row.get("updated_at") or row.get("finished_at") or row.get("started_at") or row.get("created_at")), reverse=True)

        history = _history_rows(self.repo_root, self.history_limit)
        artifacts = _artifact_rows(history, execution)
        overlays = _overlay_rows(history, self.repo_root)
        startup_overlay = _startup_overlay_preflight(self.repo_root, self.startup_artifact)
        if startup_overlay is not None:
            overlays.insert(0, startup_overlay)
        validation = _validation_snapshot(self.repo_root)
        workers = _worker_rows()
        targets = _target_rows(self.repo_root)
        evidence = _evidence_rows(self.history_limit)
        diagnostics = _diagnostic_rows(execution, events)
        nodes_by_run = _nodes_by_run(runs)

        log_path = _log_path_for_run(runs, selected_run)
        log_lines = _tail_file(log_path, line_limit=self.log_line_limit) if log_path is not None else []

        return {
            "schema_version": 1,
            "generated_at": time.time(),
            "repo_root": str(self.repo_root),
            "runs": runs,
            "nodes_by_run": nodes_by_run,
            "events": events[-500:],
            "logs": {"path": str(log_path or ""), "lines": log_lines, "bounded": True},
            "diagnostics": diagnostics,
            "history": history,
            "targets": targets,
            "workers": workers,
            "validation": validation,
            "artifacts": artifacts,
            "overlays": overlays,
            "evidence": evidence,
            "ipc": {"ok": bool(ipc.get("ok")), "socket": ipc.get("socket", ""), "error": ipc.get("error", "")},
            "selected_run": selected_run,
            "selected_node": selected_node,
            "owns_truth": False,
            "authorities": ["IPC", "EXO", "RUN", "validation", "LIFE", "DIST/SSH", "logstore", "evidence"],
        }


@dataclass(slots=True)
class InMemoryControlCenterProvider:
    payload: dict[str, Any] = field(default_factory=dict)
    version: int = 0

    def snapshot(self, *, selected_run: str = "", selected_node: str = "") -> dict[str, Any]:
        result = json.loads(json.dumps(self.payload, default=str)) if self.payload else {}
        result.setdefault("schema_version", 1)
        result.setdefault("runs", [])
        result.setdefault("nodes_by_run", {})
        result.setdefault("logs", {"path": "", "lines": [], "bounded": True})
        result.setdefault("diagnostics", [])
        result.setdefault("history", [])
        result.setdefault("targets", [])
        result.setdefault("workers", [])
        result.setdefault("validation", {"active": False, "phases": []})
        result.setdefault("artifacts", [])
        result.setdefault("overlays", [])
        result.setdefault("evidence", [])
        result["selected_run"] = selected_run
        result["selected_node"] = selected_node
        result["version"] = self.version
        result["owns_truth"] = False
        return result

    def update(self, **changes: Any) -> None:
        self.payload.update(changes)
        self.version += 1


class ControlActionDispatcher(Protocol):
    def dispatch(self, action_id: str, *, run_id: str = "", node_id: str = "", payload: Mapping[str, Any] | None = None) -> tuple[bool, str]: ...


@dataclass(slots=True)
class CanonicalControlActionDispatcher:
    repo_root: Path
    popen_factory: Callable[..., subprocess.Popen[Any]] = subprocess.Popen
    python_executable: str = sys.executable

    def __post_init__(self) -> None:
        self.repo_root = Path(self.repo_root).expanduser().resolve(strict=False)

    def dispatch(self, action_id: str, *, run_id: str = "", node_id: str = "", payload: Mapping[str, Any] | None = None) -> tuple[bool, str]:
        action = str(action_id or "").strip().lower()
        data = dict(payload or {})
        if node_id:
            data.setdefault("node_id", node_id)
        if action == "cancel":
            return request_run_cancel(run_id, reason="Textual Control Center request")
        if action in RUN_ACTION_MAP:
            return request_run_action(run_id, RUN_ACTION_MAP[action], payload=data, reason="Textual Control Center request")
        if action == "overlay.apply":
            artifact = _text(data.get("artifact") or data.get("path")).strip()
            if not artifact:
                return False, "Select an overlay artifact first."
            return self._spawn_cli(
                ["--yes", "apply-overlay", artifact],
                label=f"overlay apply {artifact}",
                apply_child=True,
            )
        if action == "worker.recover":
            profile = _text(data.get("profile") or data.get("name")).strip()
            if not profile:
                return False, "Select an SSH worker profile first."
            argv = ["ssh", "recover", profile]
            worker_run_id = _text(data.get("run_id")).strip()
            worker_node_id = _text(data.get("node_id")).strip()
            if worker_run_id:
                argv.extend(["--run-id", worker_run_id])
            if worker_node_id:
                argv.extend(["--node-id", worker_node_id])
            return self._spawn_cli(argv, label=f"SSH worker recovery {profile}")
        if action in {"rollback", "recover"}:
            transaction_path = _text(data.get("transaction_path") or data.get("path")).strip()
            if not transaction_path:
                return False, "Select an active transaction with a canonical transaction path first."
            try:
                if action == "rollback":
                    result = rollback_transaction(transaction_path, reason="Textual Control Center rollback request")
                    return int(result.returncode or 0) == 0, _text(result.output or ("Rollback completed." if int(result.returncode or 0) == 0 else "Rollback failed."))
                resumed = resume_transaction_result_publication(transaction_path)
                if resumed is None:
                    # Recovery for a rollback-failed/stale transaction is the same
                    # canonical idempotent rollback authority, not a TUI-owned path.
                    result = rollback_transaction(transaction_path, reason="Textual Control Center recovery request")
                    return int(result.returncode or 0) == 0, _text(result.output or ("Recovery completed." if int(result.returncode or 0) == 0 else "Recovery failed."))
                return int(resumed.returncode or 0) == 0, _text(resumed.output or "Result publication resumed.")
            except Exception as exc:
                return False, f"Transaction {action} failed: {exc}"
        if action == "validation.stop":
            return self._validation_control("stop-now")
        if action == "validation.pause":
            return self._validation_control("pause-after-phase")
        if action == "validation.resume-control":
            return self._validation_control("cancel-pause")
        return False, f"Action {action_id} is not wired to a canonical control authority."

    def _spawn_cli(self, argv: Sequence[str], *, label: str, apply_child: bool = False) -> tuple[bool, str]:
        """Request a domain mutation through the canonical Devtool CLI.

        The control center owns no apply/SSH lifecycle.  It only starts the same
        structured CLI authority a shell user would invoke, then returns to live
        projection.  Child output remains in canonical Devtool logs/evidence and
        Tasker/RUN state rather than being duplicated in a TUI-owned store.
        """

        env = dict(os.environ)
        env["DEVTOOL_TEXTUAL_EXECUTION_CHILD"] = "1"
        env["DEVTOOL_AUTO_HUD_CHILD"] = "1"
        env["DEVTOOL_EXECUTION_PRESENTATION"] = "plain"
        if apply_child:
            env["DEVTOOL_TEXTUAL_APPLY_CHILD"] = "1"
        command = [self.python_executable, "-m", "devtool", "--repo", str(self.repo_root), *[str(item) for item in argv]]
        try:
            proc = self.popen_factory(
                command,
                cwd=str(self.repo_root),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as exc:
            return False, f"Could not request {label}: {exc}"

        # Reap the child without making UI lifetime or truth depend on it.
        try:
            threading.Thread(target=proc.wait, name=f"devtool-tui-request-{getattr(proc, 'pid', 'child')}", daemon=True).start()
        except Exception:
            pass
        return True, f"Requested {label} through canonical Devtool CLI (pid {getattr(proc, 'pid', '?')})."

    def _validation_control(self, control: str) -> tuple[bool, str]:
        try:
            session = load_active_session(self.repo_root, required=False)
            if session is None:
                return False, "No active validation session is available."
            request = write_control_request(session, control, expected_revision=session.revision, requested_by="textual-control-center")
            return True, f"Validation control {request.get('action')} requested for {session.session_id}."
        except Exception as exc:
            return False, f"Validation control request failed: {exc}"


@dataclass(slots=True)
class RecordingControlActionDispatcher:
    responses: dict[str, tuple[bool, str]] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def dispatch(self, action_id: str, *, run_id: str = "", node_id: str = "", payload: Mapping[str, Any] | None = None) -> tuple[bool, str]:
        call = {"action_id": action_id, "run_id": run_id, "node_id": node_id, "payload": dict(payload or {})}
        self.calls.append(call)
        return self.responses.get(action_id, (True, f"{action_id} accepted"))


def _row_id(value: Any, fallback: str) -> str:
    text = _text(value).strip()
    return text or fallback


def create_textual_control_center_app(
    *,
    repo_root: str | Path,
    page: str = "dashboard",
    artifact: str = "",
    compatibility_notice: str = "",
    provider: ControlCenterProvider | None = None,
    dispatcher: ControlActionDispatcher | None = None,
    refresh_interval: float = 0.5,
    inline_overlay_apply: bool | None = None,
    apply_source_factory: Callable[[str], Any] | None = None,
):
    """Build the real Textual control-center App.

    Textual is imported lazily so non-TUI and machine-safe Devtool paths do not
    gain an import-time dependency on the optional package.
    """

    from textual import events  # type: ignore
    from textual.app import App, ComposeResult  # type: ignore
    from textual.containers import Container, Horizontal, HorizontalScroll, Vertical  # type: ignore
    from textual.css.query import NoMatches  # type: ignore
    from textual.screen import ModalScreen  # type: ignore
    from textual.widgets import Button, ContentSwitcher, DataTable, Footer, Header, Input, Label, ListItem, ListView, ProgressBar, RichLog, Static, Tree  # type: ignore

    root = Path(repo_root).expanduser().resolve(strict=False)
    normalized_page, alias_notice = normalize_tui_page(page)
    startup_artifact = str(artifact or "").strip()
    if startup_artifact and normalized_page == "dashboard":
        normalized_page = "overlays"
        artifact_notice = "Opened Overlays for --artifact preflight."
    else:
        artifact_notice = ""
    source = provider or CanonicalControlCenterProvider(root, startup_artifact=startup_artifact)
    actions = dispatcher or CanonicalControlActionDispatcher(root)
    use_inline_overlay_apply = (dispatcher is None) if inline_overlay_apply is None else bool(inline_overlay_apply)

    def make_apply_source(artifact_path: str) -> Any:
        if apply_source_factory is not None:
            return apply_source_factory(artifact_path)
        from .textual_apply_host import create_overlay_apply_source
        return create_overlay_apply_source(root, artifact_path)

    initial_page = normalized_page
    startup_notice = " ".join(value for value in (str(compatibility_notice or "").strip(), alias_notice, artifact_notice) if value).strip()

    class ConfirmationModal(ModalScreen):
        DEFAULT_CSS = """
        ConfirmationModal { align: center middle; }
        #confirm-box { width: 70%; max-width: 76; height: auto; border: round $warning; padding: 1 2; background: $surface; }
        #confirm-buttons { height: auto; align-horizontal: right; margin-top: 1; }
        #confirm-buttons Button { margin-left: 1; }
        """

        def __init__(self, prompt: str) -> None:
            super().__init__()
            self.prompt = str(prompt)

        def compose(self) -> ComposeResult:
            with Vertical(id="confirm-box"):
                yield Label(self.prompt, id="confirm-prompt")
                with Horizontal(id="confirm-buttons"):
                    yield Button("Cancel", id="confirm-no")
                    yield Button("Confirm", id="confirm-yes", variant="error")

        def on_button_pressed(self, event: Button.Pressed) -> None:
            self.dismiss(event.button.id == "confirm-yes")

    class HelpModal(ModalScreen):
        DEFAULT_CSS = """
        HelpModal { align: center middle; }
        #help-box { width: 90%; max-width: 110; height: 86%; border: round $accent; padding: 1 2; background: $surface; }
        #shortcut-table { height: 1fr; }
        #help-note { height: auto; margin-top: 1; }
        """

        def compose(self) -> ComposeResult:
            with Vertical(id="help-box"):
                yield Label("Devtool Textual Control Center shortcuts", id="help-title")
                yield DataTable(id="shortcut-table")
                yield Static(
                    "Selection follows the focused table/tree row. Consequential actions use real confirmation modals. "
                    "Ctrl+P opens the command palette.",
                    id="help-note",
                )
                yield Button("Close", id="help-close")

        def on_mount(self) -> None:
            table = self.query_one("#shortcut-table", DataTable)
            table.cursor_type = "row"
            table.zebra_stripes = True
            table.add_columns("Key", "Action", "Scope")
            for row in shortcut_rows():
                table.add_row(row.key, row.label, row.scope)

        def on_button_pressed(self, event: Button.Pressed) -> None:
            self.dismiss()

    class CommandPaletteModal(ModalScreen[str | None]):
        DEFAULT_CSS = """
        CommandPaletteModal { align: center middle; }
        #palette-box { width: 82%; max-width: 90; height: 78%; border: round $accent; padding: 1 2; background: $surface; }
        #palette-list { height: 1fr; }
        #palette-note { height: auto; color: $text-muted; }
        """
        BINDINGS = [("escape", "dismiss_palette", "Close")]

        def __init__(self) -> None:
            super().__init__()
            self.commands: list[tuple[str, str]] = [
                *[(f"page:{page}", f"Open {page.title()}") for page in CONTROL_CENTER_PAGES],
                ("run:cancel", "Cancel selected run"),
                ("run:retry", "Retry selected run"),
                ("run:rerun", "Rerun selected run"),
                ("run:resume", "Resume selected run"),
                ("help", "Open shortcut help"),
            ]

        def compose(self) -> ComposeResult:
            with Vertical(id="palette-box"):
                yield Label("Command palette", id="palette-title")
                yield ListView(
                    *[ListItem(Label(label), id=f"palette-{index}") for index, (_intent, label) in enumerate(self.commands)],
                    id="palette-list",
                )
                yield Static("↑/↓ select · Enter run · Esc close", id="palette-note")

        def on_list_view_selected(self, event: ListView.Selected) -> None:
            item_id = event.item.id or ""
            if not item_id.startswith("palette-"):
                return
            try:
                index = int(item_id[len("palette-"):])
                intent = self.commands[index][0]
            except (ValueError, IndexError):
                return
            self.dismiss(intent)

        def action_dismiss_palette(self) -> None:
            self.dismiss(None)

    class ApplyWorkspaceScreen(ModalScreen[int | None]):
        """Full-screen artifact workspace hosted by the control center.

        It reuses the H02 managed-child source and the same mobile-first
        presentation contract as shell apply: compact running summary, semantic
        validation-child progress, activity-filtered logs, and full-height flex.
        """

        BINDINGS = [
            ("escape", "back", "Back"),
            ("q", "back", "Back"),
            ("l", "toggle_logs", "Logs"),
            ("d", "toggle_details", "Details"),
            ("r", "toggle_raw", "Raw log"),
            ("ctrl+c", "request_cancel", "Cancel"),
        ]
        DEFAULT_CSS = TEXTUAL_SHARED_CSS + """
        ApplyWorkspaceScreen { background: $surface; }
        #inline-apply-root { width: 100%; height: 100%; min-height: 0; }
        #inline-apply-context { height: auto; min-height: 3; }
        #inline-apply-title { height: 1; text-style: bold; overflow-x: hidden; overflow-y: hidden; }
        #inline-apply-meta { height: 1; color: $text-muted; overflow-x: hidden; overflow-y: hidden; }
        #inline-apply-review { height: auto; max-height: 8; margin: 0 1; }
        #inline-apply-stage { height: 1; padding: 0 1; color: $text-muted; overflow-x: hidden; overflow-y: hidden; }
        #inline-apply-progress-row { height: 1; min-height: 1; padding: 0 1; }
        #inline-apply-progress { width: 1fr; height: 1; margin-right: 1; }
        #inline-apply-progress-meta { width: auto; min-width: 8; height: 1; content-align: right middle; color: $text-muted; }
        #inline-apply-phase { height: auto; min-height: 1; max-height: 2; padding: 0 1; overflow-x: hidden; overflow-y: hidden; }
        #inline-apply-activity { height: auto; min-height: 0; max-height: 3; padding: 0 1; color: $text-muted; overflow-x: hidden; overflow-y: hidden; }
        #inline-apply-notice { height: auto; min-height: 0; max-height: 2; padding: 0 1; color: $warning; overflow-x: hidden; overflow-y: hidden; }
        #inline-apply-log-toolbar { height: 3; min-height: 3; padding: 0 1; border-top: solid $panel; }
        #inline-apply-log-title { width: 1fr; height: 1; content-align: left middle; text-style: bold; overflow-x: hidden; overflow-y: hidden; }
        #inline-apply-log-toggle, #inline-apply-raw-toggle, #inline-apply-close { width: auto; min-width: 8; margin-left: 1; }
        #inline-apply-log { height: 1fr; min-height: 1; border-top: solid $surface-lighten-2; }
        .compact #inline-apply-review { margin-left: 0; margin-right: 0; border-left: none; border-right: none; }
        .compact #inline-apply-stage,
        .compact #inline-apply-progress-row,
        .compact #inline-apply-phase,
        .compact #inline-apply-activity,
        .compact #inline-apply-notice,
        .compact #inline-apply-log-toolbar { padding-left: 0; padding-right: 0; }
        .compact #inline-apply-progress-meta { min-width: 6; }
        .short #inline-apply-context { min-height: 2; }
        .short #inline-apply-review { display: none; }
        .short #inline-apply-phase { max-height: 1; }
        .short #inline-apply-activity { max-height: 1; }
        .short #inline-apply-log-toolbar { height: 1; min-height: 1; }
        .short #inline-apply-log-toggle, .short #inline-apply-raw-toggle { display: none; }
        .tiny #inline-apply-meta { display: none; }
        .tiny #inline-apply-notice { max-height: 1; }
        """

        def __init__(self, apply_source: Any) -> None:
            super().__init__()
            self.apply_source = apply_source
            self._timer: Any | None = None
            self._logs_visible = True
            self._show_raw = False
            self._details_visible = False
            self._returncode: int | None = None
            self._started_at = 0.0
            self._last_state: dict[str, Any] = {}
            self._activity = ArtifactActivityTracker()
            self._raw_lines: list[str] = []

        def compose(self) -> ComposeResult:
            review = self.apply_source.review
            with Vertical(id="inline-apply-root"):
                with Vertical(id="inline-apply-context", classes="ux-context"):
                    yield Static("DEVTOOL · ARTIFACT APPLY", classes="ux-kicker")
                    yield Static(str(review.get("artifact") or review.get("path") or "Artifact"), id="inline-apply-title", classes="ux-title")
                    yield Static("STARTING · canonical LIFE/RUN child", id="inline-apply-meta")
                yield Static(artifact_review_text(review), id="inline-apply-review", classes="ux-card")
                yield Static(artifact_stage_line({}), id="inline-apply-stage")
                with Horizontal(id="inline-apply-progress-row"):
                    yield ProgressBar(total=100, show_eta=False, show_percentage=False, id="inline-apply-progress")
                    yield Static("working", id="inline-apply-progress-meta")
                yield Static("Canonical artifact qualification pending", id="inline-apply-phase")
                yield Static("", id="inline-apply-activity")
                yield Static("", id="inline-apply-notice")
                with Horizontal(id="inline-apply-log-toolbar"):
                    yield Static("Activity log · l hide · r raw · d details", id="inline-apply-log-title")
                    yield Button("Hide", id="inline-apply-log-toggle")
                    yield Button("Raw", id="inline-apply-raw-toggle")
                    yield Button("Back", id="inline-apply-close", disabled=True)
                yield RichLog(id="inline-apply-log", wrap=True, highlight=False, markup=False, max_lines=self.apply_source.max_log_lines)

        def _apply_responsive_geometry(self, width: int, height: int) -> None:
            desired = set(responsive_class_names(width, height))
            for name in ("compact", "ultra-compact", "short", "tiny"):
                if name in desired:
                    self.add_class(name)
                else:
                    self.remove_class(name)
            self._sync_details_visibility()
            self._refresh_stage_line()
            self._refresh_context_summary()
            self._refresh_activity()
            try:
                self.refresh(layout=True)
            except Exception:
                pass

        def _sync_details_visibility(self) -> None:
            try:
                review = self.query_one("#inline-apply-review", Static)
            except NoMatches:
                return
            constrained = self.has_class("short") or self.has_class("tiny")
            review.display = bool(self._details_visible and not constrained)

        def _refresh_context_summary(self) -> None:
            try:
                self.query_one("#inline-apply-title", Static).update(
                    artifact_running_summary(self.apply_source.review, width=max(24, self.size.width - 2))
                )
            except NoMatches:
                pass

        def _refresh_stage_line(self) -> None:
            try:
                if self.has_class("compact") or self.has_class("short"):
                    text = artifact_stage_compact_line(self._last_state, returncode=self._returncode, tiny=self.has_class("tiny"))
                else:
                    text = artifact_stage_line(self._last_state, returncode=self._returncode)
                self.query_one("#inline-apply-stage", Static).update(text)
            except NoMatches:
                pass

        def _elapsed(self) -> float:
            value = getattr(self.apply_source, "elapsed_seconds", None)
            if isinstance(value, (int, float)):
                return max(0.0, float(value))
            if self._started_at:
                return max(0.0, time.monotonic() - self._started_at)
            return 0.0

        def _refresh_activity(self) -> None:
            try:
                max_lines = 1 if self.has_class("short") else 2
                text = self._activity.activity_text(
                    elapsed_seconds=self._elapsed(), max_lines=max_lines, width=max(28, self.size.width - 2)
                )
                self.query_one("#inline-apply-activity", Static).update(text)
                mode = "raw log" if self._show_raw else "activity log"
                self.query_one("#inline-apply-log-title", Static).update(
                    f"{mode} · {len(self._raw_lines)} lines · l hide · r {'activity' if self._show_raw else 'raw'} · d details"
                )
            except NoMatches:
                pass

        def on_mount(self) -> None:
            self._started_at = time.monotonic()
            self._apply_responsive_geometry(self.size.width, self.size.height)
            self._timer = self.set_interval(0.10, self._poll_apply)
            try:
                self.apply_source.start(force_yes=True)
            except Exception as exc:
                self._returncode = 1
                self.query_one("#inline-apply-meta", Static).update("FAILED · could not start canonical child")
                self.query_one("#inline-apply-phase", Static).update(f"{type(exc).__name__}: {exc}")
                self.query_one("#inline-apply-close", Button).disabled = False

        def on_resize(self, event: events.Resize) -> None:
            self._apply_responsive_geometry(event.size.width, event.size.height)

        def _stop_timer(self) -> None:
            if self._timer is not None:
                try:
                    self._timer.stop()
                finally:
                    self._timer = None

        def _remember_line(self, line: str) -> bool:
            text = str(line or "").rstrip("\r\n")
            if not text:
                return False
            self._raw_lines.append(text)
            limit = int(getattr(self.apply_source, "max_log_lines", 1200))
            if len(self._raw_lines) > limit:
                del self._raw_lines[: len(self._raw_lines) - limit]
            return self._activity.observe(text)

        def _drain_source_lines(self) -> None:
            log = self.query_one("#inline-apply-log", RichLog)
            for line in self.apply_source.drain_lines():
                surfaced = self._remember_line(line)
                if self._logs_visible and (self._show_raw or surfaced):
                    log.write(line)
            self._refresh_activity()

        def _apply_state(self, state: Mapping[str, Any], rc: int | None) -> None:
            self._last_state = dict(state)
            percent, counter, scope = artifact_display_progress(state, self._activity, returncode=rc)
            status = status_label(state.get("status") or "running")
            meta_scope = "canonical LIFE/RUN" if scope == "overall" else ("validation child" if scope == "validation child" else "canonical LIFE/RUN · progress indeterminate")
            self.query_one("#inline-apply-meta", Static).update(f"{status} · {meta_scope}")
            phase = state.get("phase")
            if isinstance(phase, Mapping):
                phase_name = _text(phase.get("name") or phase.get("label"))
                detail = _text(phase.get("detail"))
            else:
                phase_name, detail = _text(phase), ""
            progress = state.get("progress") if isinstance(state.get("progress"), Mapping) else {}
            if not detail:
                detail = _text(progress.get("detail") or state.get("message"))
            phase_text = " · ".join(value for value in (phase_name, detail) if value) or "Working…"
            self.query_one("#inline-apply-phase", Static).update(fit_middle(phase_text, max(24, self.size.width - 2)))
            progress_widget = self.query_one("#inline-apply-progress", ProgressBar)
            progress_widget.display = percent is not None
            if percent is not None:
                progress_widget.update(progress=percent)
            self.query_one("#inline-apply-progress-meta", Static).update(counter)
            self._refresh_stage_line()
            self._refresh_activity()

        def _poll_apply(self) -> None:
            try:
                self._drain_source_lines()
                state = self.apply_source.read_state()
                rc = self.apply_source.returncode
                if state:
                    self._apply_state(state, rc)
                elif rc is None:
                    percent, counter, _scope = artifact_display_progress({}, self._activity)
                    progress_widget = self.query_one("#inline-apply-progress", ProgressBar)
                    progress_widget.display = percent is not None
                    if percent is not None:
                        progress_widget.update(progress=percent)
                    self.query_one("#inline-apply-progress-meta", Static).update(counter)
                if rc is None:
                    return
                self._returncode = int(rc)
                self._drain_source_lines()
                outcome = "COMPLETED" if rc == 0 else "CANCELLED" if rc == 130 else "FAILED"
                self.query_one("#inline-apply-meta", Static).update(f"{outcome} · canonical lifecycle finished")
                self._refresh_stage_line()
                self.query_one("#inline-apply-phase", Static).update(
                    "Artifact applied successfully." if rc == 0 else ("Artifact apply cancelled." if rc == 130 else f"Artifact apply failed · exit {rc}")
                )
                if rc == 0:
                    self.query_one("#inline-apply-progress", ProgressBar).display = True
                    self.query_one("#inline-apply-progress", ProgressBar).update(progress=100.0)
                    self.query_one("#inline-apply-progress-meta", Static).update("100%")
                self.query_one("#inline-apply-close", Button).disabled = False
                self._stop_timer()
            except NoMatches:
                self._stop_timer()

        def action_toggle_logs(self) -> None:
            log = self.query_one("#inline-apply-log", RichLog)
            self._logs_visible = not self._logs_visible
            log.display = self._logs_visible
            self.query_one("#inline-apply-log-toggle", Button).label = "Hide" if self._logs_visible else "Show"

        def action_toggle_details(self) -> None:
            self._details_visible = not self._details_visible
            self._sync_details_visibility()
            try:
                self.refresh(layout=True)
            except Exception:
                pass

        def action_toggle_raw(self) -> None:
            self._show_raw = not self._show_raw
            log = self.query_one("#inline-apply-log", RichLog)
            log.clear()
            if self._logs_visible:
                if self._show_raw:
                    for line in self._raw_lines:
                        log.write(line)
                else:
                    replay = ArtifactActivityTracker()
                    for line in self._raw_lines:
                        if replay.observe(line):
                            log.write(line)
            self.query_one("#inline-apply-raw-toggle", Button).label = "Activity" if self._show_raw else "Raw"
            self._refresh_activity()

        def action_request_cancel(self) -> None:
            if not self.apply_source.running:
                return
            self.app.push_screen(
                ConfirmationModal("Cancel this artifact apply? Devtool will preserve canonical rollback/recovery behavior."),
                lambda confirmed: self._confirm_cancel() if confirmed else None,
            )

        def _confirm_cancel(self) -> None:
            self.apply_source.request_cancel()
            self.query_one("#inline-apply-notice", Static).update(
                "Cancellation requested · waiting for Devtool recovery/rollback…"
            )

        def action_back(self) -> None:
            if self.apply_source.running:
                self.query_one("#inline-apply-notice", Static).update(
                    "Artifact apply is still running. Use Ctrl+C to cancel, or wait for completion before returning."
                )
                return
            self.dismiss(self._returncode)

        def on_button_pressed(self, event: Button.Pressed) -> None:
            button_id = event.button.id or ""
            if button_id == "inline-apply-log-toggle":
                self.action_toggle_logs()
            elif button_id == "inline-apply-raw-toggle":
                self.action_toggle_raw()
            elif button_id == "inline-apply-close":
                self.action_back()

        def on_unmount(self) -> None:
            self._stop_timer()
            if self.apply_source.running:
                self.apply_source.terminate()

    class DevtoolTextualControlCenter(App):
        TITLE = "Devtool"
        SUB_TITLE = "Textual Control Center"
        # Devtool owns Ctrl+P through the canonical R03 shortcut registry.
        # Textual 8.x otherwise injects its own priority Ctrl+P command-palette
        # binding during App initialization, preempting show_command_palette.
        ENABLE_COMMAND_PALETTE = False
        CSS = TEXTUAL_SHARED_CSS + """
        #body { height: 1fr; min-height: 0; }
        #navigation { width: 18; min-width: 15; border-right: solid $primary; }
        #content { width: 1fr; height: 1fr; min-height: 0; }
        #page-context { height: auto; min-height: 3; }
        #page-switcher { height: 1fr; min-height: 0; }
        .page { height: 1fr; min-height: 0; padding: 0 1; }
        .page-title { text-style: bold; height: 1; margin-bottom: 0; }
        .table { height: 1fr; min-height: 0; }
        .detail { height: auto; max-height: 10; border-top: solid $panel; padding: 0 1; }
        #dashboard-summary { min-height: 3; margin-bottom: 1; }
        #runs-main { height: 1fr; min-height: 0; }
        #run-table { width: 2fr; height: 1fr; }
        #node-tree { width: 1fr; height: 1fr; border-left: solid $panel; }
        #logs-controls { height: auto; }
        #log-filter { width: 1fr; }
        #logs-controls Button { margin-left: 1; }
        #logs-view { height: 1fr; min-height: 0; border: round $panel; }
        #action-bar { height: auto; min-height: 3; padding: 0 1; border-top: solid $panel; overflow-x: auto; overflow-y: hidden; }
        #action-bar.-empty { display: none; }
        #action-bar Button { margin-right: 1; }
        #status-line { height: auto; min-height: 1; padding: 0 1; color: $text-muted; }
        #navigation.-narrow { display: none; }
        .compact .page { padding-left: 0; padding-right: 0; }
        .compact .detail { padding-left: 0; padding-right: 0; }
        .compact #action-bar, .compact #status-line { padding-left: 0; padding-right: 0; }
        .short #page-context { min-height: 1; max-height: 1; overflow-x: hidden; overflow-y: hidden; }
        .short .page-title { display: none; }
        .short .detail { max-height: 3; }
        .short #dashboard-summary { min-height: 1; max-height: 1; margin-bottom: 0; overflow-x: hidden; overflow-y: hidden; }
        .short #action-bar { height: 3; min-height: 3; max-height: 3; }
        .short #status-line { display: none; }
        .tiny .detail { display: none; }
        .tiny #action-bar { display: none; }
        """
        PAGE_CONTEXT = {
            "dashboard": "Overview · live runs, validation and worker health",
            "runs": "Runs · select a run, inspect nodes and request canonical controls",
            "logs": "Logs · bounded canonical output for the selected run",
            "diagnostics": "Diagnostics · actionable failures and remediation evidence",
            "history": "History · recent artifact and operation lineage",
            "targets": "Targets · configured execution surfaces and workflows",
            "workers": "Workers · distributed/SSH provider state and recovery",
            "validation": "Validation · active session phases and controls",
            "artifacts": "Artifacts · produced and verified outputs",
            "overlays": "Overlays · preflight, apply and transaction recovery",
            "evidence": "Evidence · durable run and qualification material",
        }
        ACTIONS_BY_PAGE = {
            "dashboard": ("cancel", "retry", "rerun", "resume"),
            "runs": ("cancel", "retry", "rerun", "resume"),
            # Logs and diagnostics are still selected-run inspection surfaces.
            # Contextual actions must not make canonical run controls disappear
            # merely because the operator moved from Runs to investigate output.
            "logs": ("cancel", "retry", "rerun", "resume"),
            "diagnostics": ("cancel", "retry", "rerun", "resume"),
            "workers": ("worker-recover",),
            "validation": ("validation-pause", "validation-stop"),
            "overlays": ("overlay-apply", "rollback", "recover"),
        }

        # One canonical shortcut registry owns the actual Textual bindings.
        BINDINGS = textual_control_center_bindings()

        def __init__(self) -> None:
            super().__init__()
            self.provider = source
            self.dispatcher = actions
            self.current_page = initial_page
            self.selected_run = ""
            self.selected_node = ""
            self.selected_items: dict[str, str] = {}
            self.snapshot_data: dict[str, Any] = {}
            self._last_log_lines: list[str] = []
            self._log_follow = True
            self._log_filter = ""
            self._refresh_interval = max(0.1, float(refresh_interval))
            self._refresh_timer: Any | None = None
            # True only while the control-center projection tree is mounted.
            # Timer ticks and queued Textual events may outlive the default
            # Screen during App.exit()/run_test() teardown, so every render
            # boundary gates itself on this lifecycle bit.
            self._projection_live = False

        def compose(self) -> ComposeResult:
            yield Header()
            with Horizontal(id="body"):
                yield ListView(
                    *[ListItem(Label(name.title()), id=f"nav-{name}") for name in CONTROL_CENTER_PAGES],
                    id="navigation",
                )
                with Vertical(id="content"):
                    yield Static("Dashboard · canonical overview", id="page-context", classes="ux-context")
                    with ContentSwitcher(initial=self.current_page, id="page-switcher"):
                        with Vertical(id="dashboard", classes="page"):
                            yield Label("Dashboard", classes="page-title")
                            yield Static("Runs — · Attention — · Validation — · Workers —", id="dashboard-summary", classes="ux-summary")
                            yield DataTable(id="dashboard-runs", classes="table")
                            yield Static("No run selected.", id="dashboard-detail", classes="detail")
                        with Vertical(id="runs", classes="page"):
                            yield Label("Runs", classes="page-title")
                            with Horizontal(id="runs-main"):
                                yield DataTable(id="run-table", classes="table")
                                yield Tree("Nodes", id="node-tree")
                            yield Static("Select a run or node.", id="run-detail", classes="detail")
                        with Vertical(id="logs", classes="page"):
                            yield Label("Logs", classes="page-title")
                            with Horizontal(id="logs-controls"):
                                yield Input(placeholder="Filter visible log lines…", id="log-filter")
                                yield Button("Follow / pause", id="log-follow-toggle")
                                yield Button("Copy visible", id="log-copy")
                            yield RichLog(id="logs-view", wrap=False, highlight=True, markup=False, max_lines=1200, auto_scroll=True)
                            yield Static("", id="logs-status", classes="detail")
                        with Vertical(id="diagnostics", classes="page"):
                            yield Label("Diagnostics", classes="page-title")
                            yield DataTable(id="diagnostics-table", classes="table")
                            yield Static("No diagnostic selected.", id="diagnostic-detail", classes="detail")
                        with Vertical(id="history", classes="page"):
                            yield Label("History", classes="page-title")
                            yield DataTable(id="history-table", classes="table")
                            yield Static("", id="history-detail", classes="detail")
                        with Vertical(id="targets", classes="page"):
                            yield Label("Targets", classes="page-title")
                            yield DataTable(id="targets-table", classes="table")
                            yield Static("", id="target-detail", classes="detail")
                        with Vertical(id="workers", classes="page"):
                            yield Label("Workers", classes="page-title")
                            yield DataTable(id="workers-table", classes="table")
                            yield Static("", id="worker-detail", classes="detail")
                        with Vertical(id="validation", classes="page"):
                            yield Label("Validation", classes="page-title")
                            yield DataTable(id="validation-table", classes="table")
                            yield Static("", id="validation-detail", classes="detail")
                        with Vertical(id="artifacts", classes="page"):
                            yield Label("Artifacts", classes="page-title")
                            yield DataTable(id="artifacts-table", classes="table")
                            yield Static("", id="artifact-detail", classes="detail")
                        with Vertical(id="overlays", classes="page"):
                            yield Label("Overlays", classes="page-title")
                            yield DataTable(id="overlays-table", classes="table")
                            yield Static("", id="overlay-detail", classes="detail")
                        with Vertical(id="evidence", classes="page"):
                            yield Label("Evidence", classes="page-title")
                            yield DataTable(id="evidence-table", classes="table")
                            yield Static("", id="evidence-detail", classes="detail")
                    with HorizontalScroll(id="action-bar"):
                        yield Button("Cancel", id="action-cancel", variant="error")
                        yield Button("Retry", id="action-retry")
                        yield Button("Rerun", id="action-rerun")
                        yield Button("Resume", id="action-resume")
                        yield Button("Recover worker", id="action-worker-recover", variant="warning")
                        yield Button("Pause validation", id="action-validation-pause")
                        yield Button("Stop validation", id="action-validation-stop", variant="warning")
                        yield Button("Apply overlay", id="action-overlay-apply", variant="primary")
                        yield Button("Rollback transaction", id="action-rollback", variant="error")
                        yield Button("Recover transaction", id="action-recover", variant="warning")
                    yield Static("Connecting to canonical Devtool state…", id="status-line")
            yield Footer()

        def on_mount(self) -> None:
            self._projection_live = True
            self._apply_responsive_geometry(self.size.width, self.size.height)
            self._configure_tables()
            self.action_page(self.current_page)
            self.refresh_from_provider()
            if self._projection_live:
                self._refresh_timer = self.set_interval(self._refresh_interval, self.refresh_from_provider)
            if startup_notice:
                self.notify(startup_notice, severity="information")

        def _apply_responsive_geometry(self, width: int, height: int) -> None:
            try:
                nav = self.query_one("#navigation", ListView)
                desired = set(responsive_class_names(width, height))
                for name in ("compact", "ultra-compact", "short", "tiny"):
                    if name in desired:
                        self.screen.add_class(name)
                    else:
                        self.screen.remove_class(name)
                if "compact" in desired:
                    nav.add_class("-narrow")
                else:
                    nav.remove_class("-narrow")
                self.screen.refresh(layout=True)
            except Exception:
                pass

        def _render_page_context(self) -> None:
            if not self._projection_live:
                return
            try:
                text = self.PAGE_CONTEXT.get(self.current_page, self.current_page.title())
                compact_hint = " · keys stay active · ? help" if self.screen.has_class("compact") else " · Ctrl+P commands · ? help"
                self.query_one("#page-context", Static).update(text + compact_hint)
            except NoMatches:
                self._detach_projection()

        def _render_dashboard_summary(self) -> None:
            runs = self._rows(self.snapshot_data.get("runs"))
            active = sum(1 for row in runs if _status(row) in {"running", "queued", "starting", "pending"})
            attention = sum(1 for row in runs if _status(row) in {"failed", "error", "blocked", "cancelled"})
            validation = self.snapshot_data.get("validation") if isinstance(self.snapshot_data.get("validation"), Mapping) else {}
            validation_state = _text(validation.get("state") or ("active" if validation.get("active") else "idle"))
            workers = self._rows(self.snapshot_data.get("workers"))
            worker_attention = sum(1 for row in workers if _status(row) in {"failed", "error", "blocked", "stale", "disconnected"})
            worker_text = f"{len(workers)}" + (f" / {worker_attention} attention" if worker_attention else "")
            try:
                self.query_one("#dashboard-summary", Static).update(
                    f"Runs {len(runs)} · Active {active} · Attention {attention} · Validation {validation_state} · Workers {worker_text}"
                )
            except NoMatches:
                self._detach_projection()

        def _apply_contextual_action_visibility(self) -> None:
            if not self._projection_live:
                return
            visible = set(self.ACTIONS_BY_PAGE.get(self.current_page, ()))
            try:
                bar = self.query_one("#action-bar", HorizontalScroll)
                all_actions = (
                    "cancel", "retry", "rerun", "resume", "worker-recover",
                    "validation-pause", "validation-stop", "overlay-apply", "rollback", "recover",
                )
                for action in all_actions:
                    self.query_one(f"#action-{action}", Button).display = action in visible
                if visible:
                    bar.remove_class("-empty")
                else:
                    bar.add_class("-empty")
            except NoMatches:
                self._detach_projection()

        def _configure_tables(self) -> None:
            specs = {
                "dashboard-runs": ("Run", "Status", "Command", "Project"),
                "run-table": ("Run", "Status", "Command", "Project"),
                "diagnostics-table": ("Severity", "Summary", "Run", "Source"),
                "history-table": ("When", "Status", "Artifact", "Operation"),
                "targets-table": ("Target", "Kind", "Runner", "Platform"),
                "workers-table": ("Worker", "Status", "Provider", "Target"),
                "validation-table": ("Phase", "Status", "Attempts", "Progress"),
                "artifacts-table": ("Artifact", "Type", "Status", "Run"),
                "overlays-table": ("Overlay", "Status", "Operation", "When"),
                "evidence-table": ("Evidence", "Run", "Files", "Created"),
            }
            for widget_id, columns in specs.items():
                table = self.query_one(f"#{widget_id}", DataTable)
                table.cursor_type = "row"
                table.zebra_stripes = True
                table.add_columns(*columns)

        @staticmethod
        def _rows(payload: Any) -> list[dict[str, Any]]:
            return [dict(item) for item in payload if isinstance(item, Mapping)] if isinstance(payload, list) else []

        def _stop_refresh(self) -> None:
            timer = self._refresh_timer
            if timer is not None:
                try:
                    timer.stop()
                finally:
                    self._refresh_timer = None

        def _detach_projection(self) -> None:
            # Idempotent shutdown barrier shared by timer callbacks and queued
            # widget events. Textual may still deliver RowHighlighted / input /
            # tree events after the default Screen begins unmounting.
            self._projection_live = False
            self._stop_refresh()

        def refresh_from_provider(self) -> None:
            # Textual can deliver one final interval tick while run_test()/App.exit()
            # is tearing down the default Screen. Table replacement can also queue
            # RowHighlighted events which run after this callback returns. Gate the
            # whole projection lifecycle, not only the interval callback.
            if not self._projection_live:
                return
            try:
                payload = self.provider.snapshot(selected_run=self.selected_run, selected_node=self.selected_node)
                self.snapshot_data = dict(payload)
                self._render_snapshot()
                if not self._projection_live:
                    return
                ipc = self.snapshot_data.get("ipc") if isinstance(self.snapshot_data.get("ipc"), Mapping) else {}
                ipc_state = "IPC connected" if ipc.get("ok") else "IPC degraded"
                self.query_one("#status-line", Static).update(
                    f"LIVE · {ipc_state} · run {self.selected_run or '-'} · node {self.selected_node or '-'} · renderer only"
                )
            except NoMatches:
                self._detach_projection()
                return
            except Exception as exc:
                if not self._projection_live:
                    return
                try:
                    self.query_one("#status-line", Static).update(f"Projection degraded: {exc}")
                except NoMatches:
                    self._detach_projection()
                    return

        def on_unmount(self) -> None:
            self._detach_projection()

        def _replace_table(self, widget_id: str, rows: Sequence[Sequence[Any]], keys: Sequence[str]) -> None:
            table = self.query_one(f"#{widget_id}", DataTable)
            cursor = int(getattr(table, "cursor_row", 0) or 0)
            table.clear(columns=False)
            for index, row in enumerate(rows):
                key = keys[index] if index < len(keys) else str(index)
                try:
                    table.add_row(*[_text(value) for value in row], key=key)
                except Exception:
                    table.add_row(*[_text(value) for value in row])
            if rows:
                try:
                    table.move_cursor(row=min(cursor, len(rows) - 1), column=0, animate=False)
                except Exception:
                    pass

        def _render_snapshot(self) -> None:
            runs = self._rows(self.snapshot_data.get("runs"))
            run_rows = [(
                _run_id(item), _status(item), item.get("command") or item.get("invocation_command") or "-", item.get("project") or "-"
            ) for item in runs]
            run_keys = [_run_id(item) or f"run-{i}" for i, item in enumerate(runs)]
            self._replace_table("dashboard-runs", run_rows[:25], run_keys[:25])
            self._replace_table("run-table", run_rows, run_keys)
            self._render_dashboard_summary()
            self._render_node_tree()

            diagnostics = self._rows(self.snapshot_data.get("diagnostics"))
            self._replace_table(
                "diagnostics-table",
                [(item.get("severity") or "-", item.get("summary") or item.get("message") or "-", item.get("run_id") or "-", item.get("source") or "-") for item in diagnostics],
                [_row_id(item.get("diagnostic_id") or item.get("event_id"), f"diagnostic-{i}") for i, item in enumerate(diagnostics)],
            )
            history = self._rows(self.snapshot_data.get("history"))
            self._replace_table(
                "history-table",
                [(item.get("applied_at") or item.get("created_at") or "-", item.get("status") or item.get("result") or "-", item.get("artifact") or item.get("path") or "-", item.get("operation") or item.get("command") or "-") for item in history],
                [_row_id(item.get("history_id") or item.get("operation_id") or item.get("sha256"), f"history-{i}") for i, item in enumerate(history)],
            )
            targets = self._rows(self.snapshot_data.get("targets"))
            self._replace_table("targets-table", [(item.get("name"), item.get("kind"), item.get("runner"), item.get("platform")) for item in targets], [_row_id(item.get("name"), f"target-{i}") for i, item in enumerate(targets)])
            workers = self._rows(self.snapshot_data.get("workers"))
            self._replace_table(
                "workers-table",
                [(item.get("profile") or item.get("name") or "-", item.get("status") or item.get("state") or item.get("ready") or "-", item.get("provider") or item.get("execution_provider") or "ssh", item.get("target") or "-") for item in workers],
                [_row_id(item.get("profile") or item.get("name"), f"worker-{i}") for i, item in enumerate(workers)],
            )
            validation = self.snapshot_data.get("validation") if isinstance(self.snapshot_data.get("validation"), Mapping) else {}
            phases = self._rows(validation.get("phases"))
            self._replace_table(
                "validation-table",
                [(item.get("label") or item.get("name") or item.get("key"), item.get("status") or "-", item.get("attempts") or 0, self._phase_progress(item)) for item in phases],
                [_row_id(item.get("key") or item.get("name"), f"phase-{i}") for i, item in enumerate(phases)],
            )
            artifacts = self._rows(self.snapshot_data.get("artifacts"))
            self._replace_table(
                "artifacts-table",
                [(item.get("path") or item.get("artifact_id") or "-", item.get("artifact_type") or item.get("kind") or "-", item.get("verification_status") or item.get("status") or "-", item.get("run_id") or "-") for item in artifacts],
                [_row_id(item.get("artifact_id") or item.get("path"), f"artifact-{i}") for i, item in enumerate(artifacts)],
            )
            overlays = self._rows(self.snapshot_data.get("overlays"))
            self._replace_table(
                "overlays-table",
                [(item.get("artifact") or item.get("path") or item.get("artifact_path") or "-", item.get("status") or item.get("result") or "-", item.get("operation") or item.get("command") or "apply-overlay", item.get("applied_at") or item.get("created_at") or "-") for item in overlays],
                [_row_id(item.get("history_id") or item.get("operation_id") or item.get("sha256"), f"overlay-{i}") for i, item in enumerate(overlays)],
            )
            evidence = self._rows(self.snapshot_data.get("evidence"))
            self._replace_table(
                "evidence-table",
                [(item.get("evidence_id") or "-", item.get("run_id") or "-", item.get("file_count") or 0, item.get("created_at") or "-") for item in evidence],
                [_row_id(item.get("evidence_id"), f"evidence-{i}") for i, item in enumerate(evidence)],
            )
            self._render_logs()
            self._render_details()
            self._update_action_state()

        @staticmethod
        def _phase_progress(item: Mapping[str, Any]) -> str:
            progress = item.get("progress")
            if isinstance(progress, Mapping):
                percent = progress.get("percent")
                if percent not in (None, ""):
                    return f"{percent}%"
                current, total = progress.get("current"), progress.get("total")
                if current not in (None, "") or total not in (None, ""):
                    return f"{current or 0}/{total or '?'}"
            return "-"

        def _render_node_tree(self) -> None:
            if not self._projection_live:
                return
            try:
                tree = self.query_one("#node-tree", Tree)
            except NoMatches:
                self._detach_projection()
                return
            tree.clear()
            tree.root.set_label(f"Nodes · {self.selected_run or 'select a run'}")
            nodes_by_run = self.snapshot_data.get("nodes_by_run") if isinstance(self.snapshot_data.get("nodes_by_run"), Mapping) else {}
            nodes = nodes_by_run.get(self.selected_run, []) if self.selected_run else []
            if isinstance(nodes, list):
                by_id = {str(item.get("node_id") or ""): item for item in nodes if isinstance(item, Mapping) and item.get("node_id")}
                tree_nodes: dict[str, Any] = {}
                pending = list(by_id.values())
                for _ in range(len(pending) + 1):
                    if not pending:
                        break
                    next_pending = []
                    for item in pending:
                        node_id = str(item.get("node_id"))
                        parent_id = str(item.get("parent_id") or "")
                        parent = tree_nodes.get(parent_id) if parent_id else tree.root
                        if parent is None:
                            next_pending.append(item)
                            continue
                        label = f"{item.get('label') or node_id} [{item.get('status') or 'unknown'}]"
                        tree_nodes[node_id] = parent.add(label, data={"node_id": node_id, "run_id": self.selected_run, "row": dict(item)})
                    if len(next_pending) == len(pending):
                        for item in next_pending:
                            node_id = str(item.get("node_id"))
                            tree_nodes[node_id] = tree.root.add(f"{item.get('label') or node_id} [{item.get('status') or 'unknown'}]", data={"node_id": node_id, "run_id": self.selected_run, "row": dict(item)})
                        break
                    pending = next_pending
            tree.root.expand()

        def _render_logs(self, *, force: bool = False) -> None:
            if not self._projection_live:
                return
            try:
                log = self.query_one("#logs-view", RichLog)
            except NoMatches:
                self._detach_projection()
                return
            payload = self.snapshot_data.get("logs") if isinstance(self.snapshot_data.get("logs"), Mapping) else {}
            raw_lines = [_text(item) for item in payload.get("lines", [])] if isinstance(payload.get("lines"), list) else []
            needle = self._log_filter.casefold().strip()
            lines = [line for line in raw_lines if not needle or needle in line.casefold()]
            should_replace = force or (self._log_follow and lines != self._last_log_lines)
            if should_replace:
                log.clear()
                for line in lines:
                    log.write(line)
                self._last_log_lines = lines
                if self._log_follow:
                    try:
                        log.scroll_end(animate=False)
                    except Exception:
                        pass
            follow = "following" if self._log_follow else "paused"
            context = f"run={self.selected_run or '-'} · node={self.selected_node or '-'}"
            filter_note = f" · filter={self._log_filter!r}" if self._log_filter else ""
            try:
                self.query_one("#logs-status", Static).update(
                    f"{payload.get('path') or 'No log'} · {len(raw_lines)} bounded lines · {len(lines)} visible · {follow} · {context}{filter_note}"
                )
            except NoMatches:
                self._detach_projection()

        def _toggle_log_follow(self) -> None:
            self._log_follow = not self._log_follow
            if self._log_follow:
                self._render_logs(force=True)
            else:
                self._render_logs()

        def _copy_visible_logs(self) -> None:
            text = "\n".join(self._last_log_lines)
            if not text:
                self.notify("There are no visible log lines to copy.", severity="warning")
                return
            result = copy_text(text)
            if result.success:
                self.notify(f"Visible log lines copied via {result.backend}.")
            else:
                self.notify(result.reason or "Clipboard is unavailable.", severity="warning")

        def _selected_row(self, collection: str, identity_keys: Sequence[str]) -> dict[str, Any]:
            selected = self.selected_items.get(collection, "")
            rows = self._rows(self.snapshot_data.get(collection))
            for row in rows:
                if any(_text(row.get(key)) == selected for key in identity_keys):
                    return row
            return {}

        def _render_details(self) -> None:
            if not self._projection_live:
                return
            try:
                runs = self._rows(self.snapshot_data.get("runs"))
                run = next((item for item in runs if _run_id(item) == self.selected_run), {})
                run_text = self._detail_text(run) if run else "Select a run."
                self.query_one("#dashboard-detail", Static).update(run_text)
                self.query_one("#run-detail", Static).update((run_text + (f"\nSelected node: {self.selected_node}" if self.selected_node else "")))
                diag = self._selected_row("diagnostics", ("diagnostic_id", "event_id"))
                self.query_one("#diagnostic-detail", Static).update(self._diagnostic_text(diag) if diag else "Select a diagnostic.")
                mappings = (
                    ("history", "#history-detail", ("history_id", "operation_id", "sha256")),
                    ("targets", "#target-detail", ("name",)),
                    ("workers", "#worker-detail", ("profile", "name")),
                    ("artifacts", "#artifact-detail", ("artifact_id", "path")),
                    ("overlays", "#overlay-detail", ("history_id", "operation_id", "sha256")),
                    ("evidence", "#evidence-detail", ("evidence_id",)),
                )
                for collection, widget_id, keys in mappings:
                    row = self._selected_row(collection, keys)
                    self.query_one(widget_id, Static).update(self._detail_text(row) if row else f"Select a {collection.rstrip('s')} item.")
                validation = self.snapshot_data.get("validation") if isinstance(self.snapshot_data.get("validation"), Mapping) else {}
                self.query_one("#validation-detail", Static).update(self._detail_text(validation) if validation else "No active validation session.")
            except NoMatches:
                self._detach_projection()

        @staticmethod
        def _detail_text(row: Mapping[str, Any]) -> str:
            if not row:
                return ""
            if row.get("kind") == "startup-overlay-preflight":
                return "\n".join([
                    f"Artifact: {_text(row.get('artifact'))}",
                    f"Status: {_text(row.get('status'))}",
                    f"Structural: {'ok' if row.get('structural_ok') else 'blocked'}",
                    f"Source state: {'ok' if row.get('source_state_ok') else 'blocked'}",
                    f"Conflicts: {_text(row.get('conflict_count'))}",
                    f"Rebaseable: {_text(row.get('rebaseable'))}",
                    f"Message: {_text(row.get('message'))}",
                ])
            important = ("run_id", "status", "state", "command", "project", "phase", "path", "name", "kind", "runner", "platform", "message", "error", "session_id", "revision")
            parts = [f"{key}: {row.get(key)}" for key in important if row.get(key) not in (None, "", [], {})]
            if not parts:
                parts = [json.dumps(_safe(row), ensure_ascii=False, sort_keys=True, default=str)[:1200]]
            return "\n".join(parts)

        @staticmethod
        def _diagnostic_text(row: Mapping[str, Any]) -> str:
            fields = (
                ("severity", "Severity"), ("summary", "Summary"), ("message", "What happened"),
                ("failure_kind", "Why"), ("file", "Where"), ("remediation", "Remediation"),
                ("evidence", "Evidence"), ("source", "Source"), ("run_id", "Run"), ("node_id", "Node"),
                ("log_path", "Log"),
            )
            return "\n".join(f"{label}: {row.get(key)}" for key, label in fields if row.get(key) not in (None, "", [], {})) or "Diagnostic has no detail fields."

        def _update_action_state(self) -> None:
            if not self._projection_live:
                return
            try:
                has_run = bool(self.selected_run)
                for action in ("cancel", "retry", "rerun", "resume"):
                    self.query_one(f"#action-{action}", Button).disabled = not has_run
                validation = self.snapshot_data.get("validation") if isinstance(self.snapshot_data.get("validation"), Mapping) else {}
                active = bool(validation.get("active"))
                self.query_one("#action-validation-pause", Button).disabled = not active
                self.query_one("#action-validation-stop", Button).disabled = not active
                worker = self._selected_row("workers", ("profile", "name"))
                worker_name = _text(worker.get("profile") or worker.get("name")).strip()
                worker_status = _text(worker.get("status") or worker.get("state")).strip().lower()
                worker_recoverable = bool(worker.get("recoverable")) or worker_status in {"stale", "failed", "error", "blocked", "disconnected", "recoverable", "unknown"}
                self.query_one("#action-worker-recover", Button).disabled = not bool(worker_name and worker_recoverable)
                overlay = self._selected_row("overlays", ("history_id", "operation_id", "sha256"))
                artifact = _text(overlay.get("artifact") or overlay.get("path") or overlay.get("artifact_path")).strip()
                overlay_ready = _text(overlay.get("status")).strip().lower() in {"ready", "ok", "verified", "qualified", "pass", "passed"}
                self.query_one("#action-overlay-apply", Button).disabled = not bool(artifact and overlay_ready)
                has_transaction = bool(overlay.get("transaction_path"))
                self.query_one("#action-rollback", Button).disabled = not has_transaction
                self.query_one("#action-recover", Button).disabled = not has_transaction
            except NoMatches:
                self._detach_projection()

        def _focus_page_primary(self, target: str) -> None:
            if not self._projection_live:
                return
            selectors = {
                "dashboard": "#dashboard-runs",
                "runs": "#run-table",
                "logs": "#log-follow-toggle",
                "diagnostics": "#diagnostics-table",
                "history": "#history-table",
                "targets": "#targets-table",
                "workers": "#workers-table",
                "validation": "#validation-table",
                "artifacts": "#artifacts-table",
                "overlays": "#overlays-table",
                "evidence": "#evidence-table",
            }
            selector = selectors.get(target)
            if not selector:
                return
            try:
                self.set_focus(self.query_one(selector), scroll_visible=False)
            except Exception:
                # Focus is presentation state only; projection/action truth must not
                # fail if a widget is being mounted or resized at this instant.
                pass

        def action_page(self, target: str) -> None:
            if not self._projection_live:
                return
            target, notice = normalize_tui_page(target)
            if notice:
                self.notify(notice, severity="information")
            if target not in CONTROL_CENTER_PAGES:
                return
            self.current_page = target
            try:
                switcher = self.query_one("#page-switcher", ContentSwitcher)
                switcher.current = target
                nav = self.query_one("#navigation", ListView)
                try:
                    nav.index = CONTROL_CENTER_PAGES.index(target)
                except Exception:
                    pass
                self.query_one("#status-line", Static).update(f"Page · {target}")
                self._render_page_context()
                self._apply_contextual_action_visibility()
                self._update_action_state()
            except NoMatches:
                self._detach_projection()
                return
            # A hidden widget must never retain keyboard ownership after navigation.
            # Defer until ContentSwitcher has refreshed so the newly visible page is
            # eligible for focus on the native Textual runtime.
            self.call_after_refresh(self._focus_page_primary, target)

        def action_show_help(self) -> None:
            self.push_screen(HelpModal())

        def action_show_command_palette(self) -> None:
            self.push_screen(CommandPaletteModal(), self._handle_palette_intent)

        def _handle_palette_intent(self, intent: str | None) -> None:
            value = str(intent or "").strip()
            if not value:
                return
            if value == "help":
                self.action_show_help()
                return
            if value.startswith("page:"):
                self.action_page(value.split(":", 1)[1])
                return
            if value.startswith("run:"):
                self._request_action(value.split(":", 1)[1])

        def action_quit_control_center(self) -> None:
            self._detach_projection()
            self.exit()

        def action_back_or_quit(self) -> None:
            if self.current_page != "dashboard":
                self.action_page("dashboard")
            else:
                self.action_quit_control_center()

        def action_cancel_selected(self) -> None:
            self._request_action("cancel")

        def action_run_action(self, action_id: str) -> None:
            self._request_action(str(action_id))

        def _request_action(self, action_id: str) -> None:
            if not self.selected_run:
                self.notify("Select a run first.", severity="warning")
                return
            destructive = action_id == "cancel"
            if destructive:
                self.push_screen(ConfirmationModal(f"Cancel run {self.selected_run}?"), lambda confirmed: self._dispatch_confirmed(action_id) if confirmed else None)
            else:
                self._dispatch_confirmed(action_id)

        def _dispatch_confirmed(self, action_id: str) -> None:
            ok, message = self.dispatcher.dispatch(action_id, run_id=self.selected_run, node_id=self.selected_node)
            self.notify(message, severity="information" if ok else "error")
            self.refresh_from_provider()

        def _request_validation_action(self, action_id: str, *, destructive: bool) -> None:
            if destructive:
                self.push_screen(ConfirmationModal("Stop the active validation session?"), lambda confirmed: self._dispatch_validation(action_id) if confirmed else None)
            else:
                self._dispatch_validation(action_id)

        def _dispatch_validation(self, action_id: str) -> None:
            ok, message = self.dispatcher.dispatch(action_id)
            self.notify(message, severity="information" if ok else "error")
            self.refresh_from_provider()


        def _request_transaction_action(self, action_id: str) -> None:
            overlay = self._selected_row("overlays", ("history_id", "operation_id", "sha256"))
            transaction_path = _text(overlay.get("transaction_path")).strip()
            if not transaction_path:
                self.notify("Select an active transaction first.", severity="warning")
                return
            prompt = f"{action_id.title()} transaction {overlay.get('history_id') or transaction_path}?"
            self.push_screen(
                ConfirmationModal(prompt),
                lambda confirmed: self._dispatch_transaction(action_id, overlay) if confirmed else None,
            )

        def _dispatch_transaction(self, action_id: str, overlay: Mapping[str, Any]) -> None:
            ok, message = self.dispatcher.dispatch(action_id, payload={"transaction_path": overlay.get("transaction_path", "")})
            self.notify(message, severity="information" if ok else "error")
            self.refresh_from_provider()

        def _request_overlay_apply(self) -> None:
            overlay = self._selected_row("overlays", ("history_id", "operation_id", "sha256"))
            artifact = _text(overlay.get("artifact") or overlay.get("path") or overlay.get("artifact_path")).strip()
            if not artifact:
                self.notify("Select an overlay artifact first.", severity="warning")
                return
            self.push_screen(
                ConfirmationModal(f"Apply overlay {artifact}?"),
                lambda confirmed: self._dispatch_overlay_apply(artifact) if confirmed else None,
            )

        def _dispatch_overlay_apply(self, artifact: str) -> None:
            if use_inline_overlay_apply:
                try:
                    apply_source = make_apply_source(artifact)
                except Exception as exc:
                    self.notify(f"Could not prepare artifact workspace: {exc}", severity="error")
                    return
                self.push_screen(
                    ApplyWorkspaceScreen(apply_source),
                    lambda rc: self._after_inline_overlay_apply(apply_source, rc),
                )
                return
            ok, message = self.dispatcher.dispatch("overlay.apply", payload={"artifact": artifact})
            self.notify(message, severity="information" if ok else "error")
            self.refresh_from_provider()

        def _after_inline_overlay_apply(self, apply_source: Any, returncode: int | None) -> None:
            rc = int(returncode) if isinstance(returncode, int) else apply_source.returncode
            if rc == 0:
                self.notify("Artifact apply completed successfully.", severity="information")
            elif rc == 130:
                self.notify("Artifact apply cancelled; canonical recovery state is preserved.", severity="warning")
            elif rc is not None:
                self.notify(f"Artifact apply failed with exit {rc}. Open Runs or Diagnostics for evidence.", severity="error")
            self.refresh_from_provider()

        def _request_worker_recovery(self) -> None:
            worker = self._selected_row("workers", ("profile", "name"))
            profile = _text(worker.get("profile") or worker.get("name")).strip()
            if not profile:
                self.notify("Select an SSH worker first.", severity="warning")
                return
            self.push_screen(
                ConfirmationModal(f"Recover stale SSH worker state for {profile}?"),
                lambda confirmed: self._dispatch_worker_recovery(worker) if confirmed else None,
            )

        def _dispatch_worker_recovery(self, worker: Mapping[str, Any]) -> None:
            ok, message = self.dispatcher.dispatch(
                "worker.recover",
                payload={
                    "profile": worker.get("profile") or worker.get("name") or "",
                    "run_id": worker.get("run_id") or "",
                    "node_id": worker.get("node_id") or "",
                },
            )
            self.notify(message, severity="information" if ok else "error")
            self.refresh_from_provider()

        def on_button_pressed(self, event: Button.Pressed) -> None:
            if not self._projection_live:
                return
            button_id = event.button.id or ""
            if button_id == "log-follow-toggle":
                self._toggle_log_follow()
                return
            if button_id == "log-copy":
                self._copy_visible_logs()
                return
            if button_id.startswith("action-"):
                action_id = button_id[len("action-"):]
                if action_id == "validation-pause":
                    self._request_validation_action("validation.pause", destructive=False)
                elif action_id == "validation-stop":
                    self._request_validation_action("validation.stop", destructive=True)
                elif action_id == "overlay-apply":
                    self._request_overlay_apply()
                elif action_id == "worker-recover":
                    self._request_worker_recovery()
                elif action_id in {"rollback", "recover"}:
                    self._request_transaction_action(action_id)
                else:
                    self._request_action(action_id)

        def on_input_changed(self, event: Input.Changed) -> None:
            if not self._projection_live:
                return
            if (event.input.id or "") == "log-filter":
                self._log_filter = str(event.value or "")
                self._render_logs(force=True)

        def on_list_view_selected(self, event: ListView.Selected) -> None:
            if not self._projection_live:
                return
            item_id = event.item.id or ""
            if item_id.startswith("nav-"):
                self.action_page(item_id[len("nav-"):])

        def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
            if not self._projection_live:
                return
            table_id = event.data_table.id or ""
            key = _text(getattr(event.row_key, "value", event.row_key))
            if table_id in {"run-table", "dashboard-runs"}:
                if key and key != self.selected_run:
                    self.selected_run = key
                    self.selected_node = ""
                    self._render_node_tree()
                    self._render_details()
                    self._update_action_state()
                return
            mapping = {
                "diagnostics-table": "diagnostics",
                "history-table": "history",
                "targets-table": "targets",
                "workers-table": "workers",
                "artifacts-table": "artifacts",
                "overlays-table": "overlays",
                "evidence-table": "evidence",
                "validation-table": "validation-phases",
            }
            collection = mapping.get(table_id)
            if collection:
                self.selected_items[collection] = key
                self._render_details()
                # Selection is part of action authority: overlay/worker/
                # transaction controls derive enabled-state from the selected
                # canonical row. Recompute immediately rather than waiting for
                # the next provider refresh tick.
                self._update_action_state()

        def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
            if not self._projection_live:
                return
            data = getattr(event.node, "data", None)
            if isinstance(data, Mapping):
                node_id = _text(data.get("node_id")).strip()
                if node_id:
                    self.selected_node = node_id
                    self._render_details()

        def on_resize(self, event: events.Resize) -> None:
            self._apply_responsive_geometry(event.size.width, event.size.height)
            self._render_page_context()

    return DevtoolTextualControlCenter()


__all__ = [
    "CONTROL_CENTER_PAGES",
    "CanonicalControlCenterProvider",
    "InMemoryControlCenterProvider",
    "CanonicalControlActionDispatcher",
    "RecordingControlActionDispatcher",
    "create_textual_control_center_app",
]
