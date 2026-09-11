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


def load_hud_snapshot(directory: Path | str, *, follow_active: bool = False, recent_events: int = 8) -> DesktopHudSnapshot:
    """Load the HUD snapshot through the canonical HUD state readers."""
    from ..hud import last_phase_highlights, resolve_hud_directory, snapshot_from_runtime_directory

    resolved = resolve_hud_directory(Path(directory).expanduser(), follow_active=follow_active)
    payload = snapshot_from_runtime_directory(resolved, follow_active=False, recent_events=recent_events)
    events = payload.get("events") if isinstance(payload.get("events"), list) else []
    return DesktopHudSnapshot(
        state=dict(payload.get("state") or {}),
        events=[dict(item) for item in events if isinstance(item, dict)],
        phase_highlights=last_phase_highlights(events),
        directory=resolved,
        ui=dict(payload.get("ui") or {}),
    )
