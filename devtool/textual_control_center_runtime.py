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
import time

from .artifacts import load_history
from .clipboard import copy_text
from .core.events import get_execution_store, tui_event_snapshot
from .core_runtime import private_run_root
from .ipc import request_run_action, request_run_cancel
from .logstore import logs_dir
from .run_store import RunCatalog, RunQuery
from .ssh_provider_evidence import load_all_provider_evidence
from .validation_control import status_api_payload
from .validation_sessions import load_active_session, write_control_request
from .transactions import list_active_transaction_records, rollback_transaction, resume_transaction_result_publication
from .xdg import state_dir

CONTROL_CENTER_PAGES: tuple[str, ...] = (
    "dashboard",
    "runs",
    "logs",
    "diagnostics",
    "history",
    "targets",
    "workers",
    "validation",
    "artifacts",
    "overlays",
    "evidence",
)

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


class ControlCenterProvider(Protocol):
    def snapshot(self, *, selected_run: str = "", selected_node: str = "") -> dict[str, Any]: ...


@dataclass(slots=True)
class CanonicalControlCenterProvider:
    repo_root: Path
    history_limit: int = 100
    log_line_limit: int = 1200

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
    provider: ControlCenterProvider | None = None,
    dispatcher: ControlActionDispatcher | None = None,
    refresh_interval: float = 0.5,
):
    """Build the real Textual control-center App.

    Textual is imported lazily so non-TUI and machine-safe Devtool paths do not
    gain an import-time dependency on the optional package.
    """

    from textual import events  # type: ignore
    from textual.app import App, ComposeResult  # type: ignore
    from textual.containers import Container, Horizontal, HorizontalScroll, Vertical  # type: ignore
    from textual.screen import ModalScreen  # type: ignore
    from textual.widgets import Button, ContentSwitcher, DataTable, Footer, Header, Input, Label, ListItem, ListView, RichLog, Static, Tree  # type: ignore

    root = Path(repo_root).expanduser().resolve(strict=False)
    source = provider or CanonicalControlCenterProvider(root)
    actions = dispatcher or CanonicalControlActionDispatcher(root)
    initial_page = page if page in CONTROL_CENTER_PAGES else "dashboard"

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
        #help-box { width: 86%; max-width: 100; height: 80%; border: round $accent; padding: 1 2; background: $surface; }
        #help-list { height: 1fr; }
        """

        def compose(self) -> ComposeResult:
            with Vertical(id="help-box"):
                yield Label("Devtool Textual Control Center", id="help-title")
                yield Static(
                    "Navigation: d dashboard · u runs · l logs · g diagnostics · h history · t targets · w workers · v validation · s artifacts · o overlays · e evidence\n"
                    "Run actions: c cancel · y retry · r rerun · m resume · ? help · q quit · Esc back/quit\n"
                    "Selection follows the focused table/tree row. Destructive actions require a modal confirmation.",
                    id="help-list",
                )
                yield Button("Close", id="help-close")

        def on_button_pressed(self, event: Button.Pressed) -> None:
            self.dismiss()

    class DevtoolTextualControlCenter(App):
        TITLE = "Devtool"
        SUB_TITLE = "Textual Control Center"
        CSS = """
        #body { height: 1fr; }
        #navigation { width: 20; min-width: 15; border-right: solid $primary; }
        #content { width: 1fr; height: 1fr; }
        #page-switcher { height: 1fr; min-height: 0; }
        .page { height: 1fr; min-height: 0; padding: 0 1; }
        .page-title { text-style: bold; height: 1; margin-bottom: 1; }
        .table { height: 1fr; }
        .detail { height: auto; max-height: 12; border-top: solid $panel; padding: 0 1; }
        #runs-main { height: 1fr; }
        #run-table { width: 2fr; height: 1fr; }
        #node-tree { width: 1fr; height: 1fr; border-left: solid $panel; }
        #logs-controls { height: auto; }
        #log-filter { width: 1fr; }
        #logs-controls Button { margin-left: 1; }
        #logs-view { height: 1fr; border: round $panel; }
        #action-bar { height: 4; min-height: 4; padding: 0 1; border-top: solid $panel; overflow-x: auto; overflow-y: hidden; }
        #action-bar Button { margin-right: 1; }
        #status-line { height: 1; padding: 0 1; color: $text-muted; }
        #navigation.-narrow { display: none; }
        """
        BINDINGS = [
            ("q", "quit_control_center", "Quit"),
            ("escape", "back_or_quit", "Back"),
            ("?", "show_help", "Help"),
            ("d", "page('dashboard')", "Dashboard"),
            ("u", "page('runs')", "Runs"),
            ("l", "page('logs')", "Logs"),
            ("g", "page('diagnostics')", "Diagnostics"),
            ("h", "page('history')", "History"),
            ("t", "page('targets')", "Targets"),
            ("w", "page('workers')", "Workers"),
            ("v", "page('validation')", "Validation"),
            ("s", "page('artifacts')", "Artifacts"),
            ("o", "page('overlays')", "Overlays"),
            ("e", "page('evidence')", "Evidence"),
            ("c", "run_action('cancel')", "Cancel"),
            ("y", "run_action('retry')", "Retry"),
            ("r", "run_action('rerun')", "Rerun"),
            ("m", "run_action('resume')", "Resume"),
            ("ctrl+c", "cancel_selected", "Cancel selected run"),
        ]

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

        def compose(self) -> ComposeResult:
            yield Header()
            with Horizontal(id="body"):
                yield ListView(
                    *[ListItem(Label(name.title()), id=f"nav-{name}") for name in CONTROL_CENTER_PAGES],
                    id="navigation",
                )
                with Vertical(id="content"):
                    with ContentSwitcher(initial=self.current_page, id="page-switcher"):
                        with Vertical(id="dashboard", classes="page"):
                            yield Label("Dashboard", classes="page-title")
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
                        yield Button("Pause validation", id="action-validation-pause")
                        yield Button("Stop validation", id="action-validation-stop", variant="warning")
                        yield Button("Rollback transaction", id="action-rollback", variant="error")
                        yield Button("Recover transaction", id="action-recover", variant="warning")
                    yield Static("Connecting to canonical Devtool state…", id="status-line")
            yield Footer()

        def on_mount(self) -> None:
            self._configure_tables()
            self.action_page(self.current_page)
            self.refresh_from_provider()
            self.set_interval(self._refresh_interval, self.refresh_from_provider)

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

        def refresh_from_provider(self) -> None:
            try:
                payload = self.provider.snapshot(selected_run=self.selected_run, selected_node=self.selected_node)
                self.snapshot_data = dict(payload)
                self._render_snapshot()
                self.query_one("#status-line", Static).update(
                    f"Live · page={self.current_page} · run={self.selected_run or '-'} · node={self.selected_node or '-'} · owns_truth=false"
                )
            except Exception as exc:
                self.query_one("#status-line", Static).update(f"Projection degraded: {exc}")

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
            tree = self.query_one("#node-tree", Tree)
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
            log = self.query_one("#logs-view", RichLog)
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
            self.query_one("#logs-status", Static).update(
                f"{payload.get('path') or 'No log'} · {len(raw_lines)} bounded lines · {len(lines)} visible · {follow} · {context}{filter_note}"
            )

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

        @staticmethod
        def _detail_text(row: Mapping[str, Any]) -> str:
            if not row:
                return ""
            important = ("run_id", "status", "state", "command", "project", "phase", "path", "name", "kind", "runner", "platform", "message", "error", "session_id", "revision")
            parts = [f"{key}: {row.get(key)}" for key in important if row.get(key) not in (None, "", [], {})]
            if not parts:
                parts = [json.dumps(_safe(row), ensure_ascii=False, sort_keys=True, default=str)[:1200]]
            return " · ".join(parts)

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
            has_run = bool(self.selected_run)
            for action in ("cancel", "retry", "rerun", "resume"):
                self.query_one(f"#action-{action}", Button).disabled = not has_run
            validation = self.snapshot_data.get("validation") if isinstance(self.snapshot_data.get("validation"), Mapping) else {}
            active = bool(validation.get("active"))
            self.query_one("#action-validation-pause", Button).disabled = not active
            self.query_one("#action-validation-stop", Button).disabled = not active
            overlay = self._selected_row("overlays", ("history_id", "operation_id", "sha256"))
            has_transaction = bool(overlay.get("transaction_path"))
            self.query_one("#action-rollback", Button).disabled = not has_transaction
            self.query_one("#action-recover", Button).disabled = not has_transaction

        def _focus_page_primary(self, target: str) -> None:
            selectors = {
                "dashboard": "#dashboard-runs",
                "runs": "#run-table",
                "logs": "#log-filter",
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
            if target not in CONTROL_CENTER_PAGES:
                return
            self.current_page = target
            switcher = self.query_one("#page-switcher", ContentSwitcher)
            switcher.current = target
            nav = self.query_one("#navigation", ListView)
            try:
                nav.index = CONTROL_CENTER_PAGES.index(target)
            except Exception:
                pass
            self.query_one("#status-line", Static).update(f"Page: {target}")
            # A hidden widget must never retain keyboard ownership after navigation.
            # Defer until ContentSwitcher has refreshed so the newly visible page is
            # eligible for focus on the native Textual runtime.
            self.call_after_refresh(self._focus_page_primary, target)

        def action_show_help(self) -> None:
            self.push_screen(HelpModal())

        def action_quit_control_center(self) -> None:
            self.exit()

        def action_back_or_quit(self) -> None:
            if self.current_page != "dashboard":
                self.action_page("dashboard")
            else:
                self.exit()

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

        def on_button_pressed(self, event: Button.Pressed) -> None:
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
                elif action_id in {"rollback", "recover"}:
                    self._request_transaction_action(action_id)
                else:
                    self._request_action(action_id)

        def on_input_changed(self, event: Input.Changed) -> None:
            if (event.input.id or "") == "log-filter":
                self._log_filter = str(event.value or "")
                self._render_logs(force=True)

        def on_list_view_selected(self, event: ListView.Selected) -> None:
            item_id = event.item.id or ""
            if item_id.startswith("nav-"):
                self.action_page(item_id[len("nav-"):])

        def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
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

        def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
            data = getattr(event.node, "data", None)
            if isinstance(data, Mapping):
                node_id = _text(data.get("node_id")).strip()
                if node_id:
                    self.selected_node = node_id
                    self._render_details()

        def on_resize(self, event: events.Resize) -> None:
            try:
                nav = self.query_one("#navigation", ListView)
                if int(event.size.width) < 72:
                    nav.add_class("-narrow")
                else:
                    nav.remove_class("-narrow")
            except Exception:
                pass

    return DevtoolTextualControlCenter()


__all__ = [
    "CONTROL_CENTER_PAGES",
    "CanonicalControlCenterProvider",
    "InMemoryControlCenterProvider",
    "CanonicalControlActionDispatcher",
    "RecordingControlActionDispatcher",
    "create_textual_control_center_app",
]
