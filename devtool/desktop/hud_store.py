from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class DesktopHudSnapshot:
    state: dict[str, Any]
    events: list[dict[str, Any]]
    phase_highlights: list[dict[str, Any]]
    directory: Path
    ui: dict[str, Any]
    execution: dict[str, Any] | None = None
    runtime_state: dict[str, Any] | None = None


def load_hud_snapshot(directory: Path | str, *, follow_active: bool = False, recent_events: int = 8) -> DesktopHudSnapshot:
    """Load the HUD snapshot through the canonical HUD state readers."""
    from ..hud import last_phase_highlights, resolve_hud_directory, snapshot_from_directory
    from ..ui_core.viewmodels import runtime_state_to_ui_snapshot
    from .execution_truth import desktop_runtime_payload

    resolved = resolve_hud_directory(Path(directory).expanduser(), follow_active=follow_active)
    payload = snapshot_from_directory(resolved, recent_events=recent_events, prefer_execution=True)
    events = payload.get("events") if isinstance(payload.get("events"), list) else []
    runtime = desktop_runtime_payload(payload.get("runtime_state") if isinstance(payload.get("runtime_state"), dict) else payload.get("state"))
    presentations = runtime.get("ui_presentations") if isinstance(runtime.get("ui_presentations"), dict) else {}
    desktop_preferences = presentations.get("desktop") if isinstance(presentations.get("desktop"), dict) else None
    desktop_ui = runtime_state_to_ui_snapshot(runtime, surface="desktop", preferences=desktop_preferences).as_dict()
    return DesktopHudSnapshot(
        state=runtime,
        events=[dict(item) for item in events if isinstance(item, dict)],
        phase_highlights=last_phase_highlights(events),
        directory=resolved,
        ui=desktop_ui,
        execution=dict(payload.get("execution")) if isinstance(payload.get("execution"), dict) else None,
        runtime_state=runtime,
    )
