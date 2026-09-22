from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
import json

from .events.bus import EventBus
from .events.events import UnknownExecutionEvent, event_from_dict


@dataclass(frozen=True, slots=True)
class JournalReplayReport:
    run_id: str
    events_replayed: int
    unknown_events: int
    skipped_lines: int
    last_sequence: int
    source: str

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "events_replayed": self.events_replayed,
            "unknown_events": self.unknown_events,
            "skipped_lines": self.skipped_lines,
            "last_sequence": self.last_sequence,
            "source": self.source,
        }


def _candidate_run_id(directory: Path) -> str:
    for name in ("execution-checkpoint.json", "execution.json"):
        path = directory / name
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        value = str(payload.get("run_id") or "").strip()
        if not value and isinstance(payload.get("execution"), dict):
            value = str(payload["execution"].get("run_id") or "").strip()
        if value:
            return value
    return ""


def load_execution_journal(
    directory: Path | str,
    *,
    run_id: str = "",
    strict: bool = True,
) -> tuple[list[object], int]:
    """Decode a compact EXO journal, retaining unknown future event types."""
    root = Path(directory)
    path = root / "execution-events.jsonl"
    wanted = str(run_id or _candidate_run_id(root)).strip()
    events: list[object] = []
    skipped = 0
    if not path.is_file():
        if strict:
            raise FileNotFoundError(path)
        return events, skipped

    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
            event = event_from_dict(payload, allow_unknown=True)
        except Exception:
            if strict:
                raise ValueError(f"Invalid execution journal line {line_number}: {path}")
            skipped += 1
            continue
        if wanted and str(getattr(event, "run_id", "")) != wanted:
            continue
        events.append(event)
    return events, skipped


def recover_event_bus(
    directory: Path | str,
    *,
    bus: EventBus | None = None,
    run_id: str = "",
    strict: bool = True,
) -> tuple[EventBus, JournalReplayReport]:
    """Recover authoritative execution state and the next live sequence cursor."""
    root = Path(directory)
    resolved_run = str(run_id or _candidate_run_id(root)).strip()
    events, skipped = load_execution_journal(root, run_id=resolved_run, strict=strict)
    target = bus or EventBus()
    unknown = 0
    last_sequence = -1
    replayed = 0
    for event in events:
        if isinstance(event, UnknownExecutionEvent):
            unknown += 1
        target.replay_event(event)
        replayed += 1
        last_sequence = max(last_sequence, int(getattr(event, "sequence", -1)))
        if not resolved_run:
            resolved_run = str(getattr(event, "run_id", "") or "")
    return target, JournalReplayReport(
        run_id=resolved_run,
        events_replayed=replayed,
        unknown_events=unknown,
        skipped_lines=skipped,
        last_sequence=last_sequence,
        source=str(root / "execution-events.jsonl"),
    )


__all__ = ["JournalReplayReport", "load_execution_journal", "recover_event_bus"]
