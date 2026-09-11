from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


UI_SCHEMA_VERSION = 1


@dataclass(slots=True)
class UIWorkspaceState:
    name: str = ""
    root: str = ""
    targets: list[str] = field(default_factory=list)
    selected_target: str = ""
    selected_groups: list[str] = field(default_factory=list)


@dataclass(slots=True)
class UISelectionState:
    targets: list[str] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)
    variant: str = ""
    operation: str = ""


@dataclass(slots=True)
class UIOperationState:
    run_id: str = ""
    command: str = ""
    title: str = "Devtool"
    status: str = "idle"
    token: str = "status.pending"
    detail: str = ""
    phase: str = ""
    target: str = ""
    runner: str = ""
    execution_environment: str = ""
    started_at: str = ""
    updated_at: str = ""
    finished_at: str = ""
    log_path: str = ""


@dataclass(slots=True)
class UIProgressState:
    id: str
    label: str
    status: str = "pending"
    token: str = "status.pending"
    kind: str = "step"
    parent_id: str = ""
    current: int | None = None
    total: int | None = None
    percent: int | None = None
    detail: str = ""
    target: str = ""
    runner: str = ""
    execution_environment: str = ""


@dataclass(slots=True)
class UIDiagnosticState:
    severity: str
    message: str
    token: str = "severity.info"
    code: str = ""
    phase: str = ""
    file: str = ""
    source: str = "runtime"


@dataclass(slots=True)
class UIActionState:
    id: str
    label: str
    short_label: str
    icon: str
    category: str
    intent: str
    enabled: bool = True
    destructive: bool = False
    disabled_reason: str = ""
    notification_label: str = ""
    shortcut: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    token: str = "action.secondary"


@dataclass(slots=True)
class UINavigationState:
    id: str
    label: str
    short_label: str
    icon: str
    route: str
    section: str = "primary"
    order: int = 0


@dataclass(slots=True)
class UIPresentationState:
    profile: str = "standard"
    theme: str = "auto"
    density: str = "comfortable"
    icons: str = "auto"
    motion: str = "auto"
    verbosity: str = "normal"
    timestamps: str = "relative"
    duration_format: str = "human"
    confirm_destructive: bool = True
    confirm_cancel: bool = False
    show_completed_steps: bool = True
    show_environment: str = "when-relevant"
    show_commands: str = "on-error"
    show_diagnostics: str = "expanded-on-error"
    sidebar: str = "auto"
    mouse: bool = True
    max_steps: int = 0
    notification_detail: str = "normal"
    provenance: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class UISnapshot:
    workspace: UIWorkspaceState = field(default_factory=UIWorkspaceState)
    selection: UISelectionState = field(default_factory=UISelectionState)
    operation: UIOperationState = field(default_factory=UIOperationState)
    progress: list[UIProgressState] = field(default_factory=list)
    diagnostics: list[UIDiagnosticState] = field(default_factory=list)
    actions: list[UIActionState] = field(default_factory=list)
    navigation: list[UINavigationState] = field(default_factory=list)
    presentation: UIPresentationState = field(default_factory=UIPresentationState)
    schema_version: int = UI_SCHEMA_VERSION
    surface: str = "machine"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
