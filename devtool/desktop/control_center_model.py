from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any

from ..config import load_project_config
from ..models import ProjectConfig
from ..tasker import DEFAULT_TASKER_DIR
from .history_store import DesktopHistoryStore
from .hud_store import load_hud_snapshot
from .ipc_client import DesktopIpcClient
from .repository_store import DesktopRepositoryStore, RepositorySummary


@dataclass(slots=True)
class DesktopTargetSummary:
    name: str
    kind: str = ""
    root: str = ""
    build: str = ""
    test: str = ""
    run: str = ""
    environment: str = "auto"
    chroot: str = ""


@dataclass(slots=True)
class DesktopDiagnosticSummary:
    severity: str
    message: str
    code: str = ""
    phase: str = ""
    file: str = ""


@dataclass(slots=True)
class DesktopRunSummary:
    run_id: str
    title: str
    status: str
    text: str = ""
    percent: int | None = None
    target: str = ""
    phase: str = ""
    updated_at: str = ""
    log_path: str = ""


@dataclass(slots=True)
class DesktopControlCenterSnapshot:
    repository: dict[str, Any]
    project: dict[str, Any]
    active_runs: list[dict[str, Any]] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    targets: list[dict[str, Any]] = field(default_factory=list)
    hud: dict[str, Any] = field(default_factory=dict)
    ipc: dict[str, Any] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "project": self.project,
            "active_runs": self.active_runs,
            "history": self.history,
            "diagnostics": self.diagnostics,
            "targets": self.targets,
            "hud": self.hud,
            "ipc": self.ipc,
            "settings": self.settings,
        }


class DesktopControlCenterModel:
    """Read-only model used by the GTK4 Control Center shell.

    The model deliberately reuses Devtool's existing stores and state readers so
    the GTK4 migration does not grow a second truth source beside the CLI,
    Control Center IPC broker, artifact history, or HUD runtime files.
    """

    def __init__(
        self,
        *,
        repository: Path | str,
        config_path: str | None = None,
        target: str = "",
        variant: str = "",
        state_dir: Path | str = DEFAULT_TASKER_DIR,
        follow_active: bool = False,
        history_limit: int = 25,
        recent_events: int = 8,
        ipc_socket: Path | None = None,
    ) -> None:
        self.repository = Path(repository).expanduser().resolve()
        self.config_path = config_path
        self.target = target
        self.variant = variant
        self.state_dir = Path(state_dir).expanduser()
        self.follow_active = follow_active
        self.history_limit = max(0, int(history_limit))
        self.recent_events = max(0, int(recent_events))
        self.ipc_socket = ipc_socket

    def load_config(self) -> ProjectConfig:
        return load_project_config(
            str(self.repository),
            self.config_path,
            target_name=self.target,
            variant_name=self.variant,
        )

    def snapshot(self) -> DesktopControlCenterSnapshot:
        config = self.load_config()
        repo = DesktopRepositoryStore(config.repo_root).summary()
        history = DesktopHistoryStore(config.repo_root).load(limit=self.history_limit)
        hud = load_hud_snapshot(self.state_dir, follow_active=self.follow_active, recent_events=self.recent_events)
        ipc = DesktopIpcClient(socket_path=self.ipc_socket).snapshot()
        raw_state = _read_raw_hud_state(hud.directory) or hud.state
        if raw_state and not self.follow_active:
            raw_runs = [dict(raw_state)]
            diagnostic_runs = [dict(raw_state)]
        else:
            raw_runs = _raw_runs_from_ipc_or_hud(ipc, hud.state)
            diagnostic_runs = _raw_runs_from_ipc_or_hud(ipc, raw_state)
        active_runs = [_run_summary_dict(item) for item in raw_runs]
        diagnostics = _diagnostics_from_runs_and_events(diagnostic_runs, hud.events)
        targets = [_target_summary_dict(name, target) for name, target in sorted(config.targets.items())]
        return DesktopControlCenterSnapshot(
            repository=_repository_dict(repo),
            project={
                "kind": config.kind,
                "selected_target": config.selected_target,
                "variant": config.variant,
                "execution_environment": config.execution_environment,
                "chroot_name": config.chroot_name,
                "config_path": str(config.config_path or ""),
                "global_config_path": str(config.global_config_path or ""),
            },
            active_runs=active_runs,
            history=history,
            diagnostics=diagnostics,
            targets=targets,
            hud={
                "directory": str(hud.directory),
                "state": hud.state,
                "events": hud.events,
                "phase_highlights": hud.phase_highlights,
            },
            ipc=ipc,
            settings={
                "history_limit": self.history_limit,
                "recent_events": self.recent_events,
                "follow_active": self.follow_active,
            },
        )


def _read_raw_hud_state(directory: Path) -> dict[str, Any]:
    try:
        data = json.loads((directory / "state.json").read_text(encoding="utf-8"))
    except Exception:
        return {}
    return dict(data) if isinstance(data, dict) else {}


def _repository_dict(summary: RepositorySummary) -> dict[str, Any]:
    return {
        "path": str(summary.path),
        "branch": summary.branch,
        "head": summary.head,
        "dirty": summary.dirty,
        "exists": summary.exists,
    }


def _target_summary_dict(name: str, target: Any) -> dict[str, Any]:
    return asdict(DesktopTargetSummary(
        name=name,
        kind=str(getattr(target, "kind", "") or ""),
        root=str(getattr(target, "root", "") or ""),
        build=str(getattr(target, "build_target", "") or getattr(target, "build_command", "") or ""),
        test=str(getattr(target, "test_target", "") or getattr(target, "test_command", "") or ""),
        run=str(getattr(target, "run_project", "") or getattr(target, "run_command", "") or ""),
        environment=str(getattr(target, "execution_environment", "") or "auto"),
        chroot=str(getattr(target, "chroot_name", "") or ""),
    ))


def _raw_runs_from_ipc_or_hud(ipc: dict[str, Any], hud_state: dict[str, Any]) -> list[dict[str, Any]]:
    raw_runs = ipc.get("runs") if isinstance(ipc.get("runs"), list) else []
    runs = [dict(item) for item in raw_runs if isinstance(item, dict)]
    if runs:
        return runs
    if hud_state:
        return [dict(hud_state)]
    return []


def _field_text(value: Any, *keys: str) -> str:
    if isinstance(value, dict):
        for key in keys:
            if str(value.get(key) or ""):
                return str(value.get(key))
        return ""
    if value is None:
        return ""
    return str(value)


def _run_summary_dict(item: dict[str, Any]) -> dict[str, Any]:
    percent = item.get("percent")
    try:
        percent_value = int(percent) if percent is not None and str(percent) != "" else None
    except (TypeError, ValueError):
        percent_value = None
    return asdict(DesktopRunSummary(
        run_id=str(item.get("run_id") or item.get("id") or ""),
        title=str(item.get("display_title") or item.get("title") or item.get("artifact_name") or item.get("command") or "devtool"),
        status=str(item.get("status") or "idle"),
        text=str(item.get("display_text") or item.get("text") or item.get("summary") or ""),
        percent=percent_value,
        target=_field_text(item.get("target_summary") or item.get("target"), "name", "target"),
        phase=_field_text(item.get("phase_name") or item.get("phase"), "name", "phase"),
        updated_at=str(item.get("updated_at") or ""),
        log_path=_field_text(item.get("log_path") or item.get("result"), "log_path"),
    ))


def _diagnostics_from_runs_and_events(runs: list[dict[str, Any]], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()

    def append(raw: dict[str, Any], *, phase: str = "") -> None:
        message = str(raw.get("detail") or raw.get("message") or raw.get("text") or "").strip()
        if not message:
            return
        severity = str(raw.get("severity") or raw.get("level") or raw.get("type") or "info").strip().lower() or "info"
        item = DesktopDiagnosticSummary(
            severity=severity,
            message=message,
            code=str(raw.get("code") or ""),
            phase=str(raw.get("phase") or phase or ""),
            file=str(raw.get("file") or raw.get("path") or ""),
        )
        key = (item.severity, item.code, item.phase, item.file, item.message)
        if key in seen:
            return
        seen.add(key)
        diagnostics.append(asdict(item))

    for run in runs:
        state = run if isinstance(run, dict) else {}
        for issue in state.get("diagnostics", []) if isinstance(state.get("diagnostics"), list) else []:
            if isinstance(issue, dict):
                append(issue, phase=str(state.get("phase") or ""))
        for result in state.get("results", []) if isinstance(state.get("results"), list) else []:
            if not isinstance(result, dict):
                continue
            phase = str(result.get("name") or result.get("phase") or "")
            for issue in result.get("issues", []) if isinstance(result.get("issues"), list) else []:
                if isinstance(issue, dict):
                    append(issue, phase=phase)
    for event in events:
        if not isinstance(event, dict):
            continue
        severity = str(event.get("severity") or event.get("level") or event.get("type") or "").lower()
        if severity in {"warning", "warn", "error", "failed", "failure"}:
            append(event)
    return diagnostics
