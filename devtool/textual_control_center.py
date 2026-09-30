"""PRES-X08 live Textual control-center projection and interaction contracts.

This module is intentionally renderer/control-client only. It consumes canonical
records and emits bounded presentation state plus typed action requests; it does
not execute domain actions or own RUN/EXO/LIFE/DIST/TEST truth.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
from typing import Any, Iterable, Mapping, Sequence

PAGES = ("dashboard", "runs", "logs", "diagnostics", "history", "targets", "workers", "validation", "artifacts", "evidence")
NAV_KEYS = {"d":"dashboard", "u":"runs", "l":"logs", "g":"diagnostics", "h":"history", "t":"targets", "w":"workers", "v":"validation", "s":"artifacts", "e":"evidence"}
TERMINAL_STATES = {"passed", "failed", "cancelled", "blocked", "error", "complete", "completed"}

@dataclass(frozen=True)
class ControlCenterCapabilities:
    width: int
    height: int
    mode: str
    mouse: bool
    hyperlink: bool
    clipboard: bool
    resize: bool
    multi_pane: bool
    tree: bool
    reduced_motion: bool
    screen_reader: bool

    def as_dict(self) -> dict[str, Any]: return self.__dict__.copy()

@dataclass(frozen=True)
class ControlActionRequest:
    request_id: str
    action_id: str
    run_id: str | None
    node_id: str | None
    confirmation_required: bool
    authority: str
    status: str = "requested"

    def as_dict(self) -> dict[str, Any]: return self.__dict__.copy()

@dataclass(frozen=True)
class ControlActionAck:
    request_id: str
    accepted: bool
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"request_id": self.request_id, "accepted": self.accepted, "status": "accepted" if self.accepted else "rejected", "reason": self.reason}

@dataclass
class TextualControlCenter:
    max_events: int = 2048
    max_log_lines: int = 4000
    render_hz: int = 12
    page: str = "dashboard"
    selected_run: str | None = None
    selected_node: str | None = None
    follow_logs: bool = True
    paused: bool = False
    events: list[dict[str, Any]] = field(default_factory=list)
    logs: list[dict[str, Any]] = field(default_factory=list)
    dropped_progress_updates: int = 0
    _progress_slots: dict[tuple[str | None, str | None], int] = field(default_factory=dict, repr=False)

    def navigate(self, page: str) -> None:
        if page not in PAGES: raise ValueError(f"unknown control-center page: {page}")
        self.page = page

    def ingest(self, record: Mapping[str, Any]) -> None:
        item = dict(record)
        kind = str(item.get("kind") or item.get("type") or "event")
        run_id = _identity(item, "run_id", "run")
        node_id = _identity(item, "node_id", "node")
        state = str(item.get("state") or item.get("status") or "").lower()
        if kind in {"stdout", "stderr", "log", "process-output"}:
            safe = {k:v for k,v in item.items() if k not in {"secret", "token", "password", "raw_secret"}}
            self.logs.append(safe)
            if len(self.logs) > self.max_log_lines: del self.logs[:len(self.logs)-self.max_log_lines]
            return
        # Coalesce noisy progress by run/node, but never terminal/error transitions.
        if kind in {"progress", "heartbeat"} and state not in TERMINAL_STATES:
            key = (run_id, node_id)
            previous = self._progress_slots.get(key)
            if previous is not None and previous < len(self.events):
                self.events[previous] = item
                self.dropped_progress_updates += 1
                return
            self._progress_slots[key] = len(self.events)
        self.events.append(item)
        if len(self.events) > self.max_events:
            trim = len(self.events)-self.max_events
            del self.events[:trim]
            self._progress_slots = {k:max(0,v-trim) for k,v in self._progress_slots.items() if v >= trim}

    def select(self, *, run_id: str | None = None, node_id: str | None = None) -> None:
        if run_id is not None: self.selected_run = run_id
        if node_id is not None: self.selected_node = node_id

    def action_request(self, action_id: str, *, confirmed: bool = False) -> ControlActionRequest:
        authority = _action_authority(action_id)
        destructive = action_id in {"cancel", "artifact.apply", "overlay.apply", "rollback", "recover"}
        if destructive and not confirmed:
            raise PermissionError(f"{action_id} requires canonical confirmation")
        payload = f"{action_id}\0{self.selected_run or ''}\0{self.selected_node or ''}\0{len(self.events)}"
        return ControlActionRequest(sha256(payload.encode()).hexdigest()[:20], action_id, self.selected_run, self.selected_node, destructive, authority)

    def snapshot(self, *, width: int = 100, height: int = 28, term: str = "xterm", clipboard_available: bool = False, hyperlinks: bool = True, screen_reader: bool = False, reduced_motion: bool = False) -> dict[str, Any]:
        caps = capabilities(width=width, height=height, term=term, clipboard_available=clipboard_available, hyperlinks=hyperlinks, screen_reader=screen_reader, reduced_motion=reduced_motion)
        runs = _runs(self.events)
        attention = [r for r in runs if r.get("state") in {"failed", "blocked", "error", "running"}]
        payload = {
            "schema_version": 1, "backend": "textual", "page": self.page,
            "pages": list(PAGES), "navigation": dict(NAV_KEYS),
            "selected_run": self.selected_run, "selected_node": self.selected_node,
            "runs": runs, "attention": attention[:50],
            "log_lines": [] if self.paused else self.logs[-min(self.max_log_lines, 500):],
            "follow_logs": self.follow_logs, "paused": self.paused,
            "capabilities": caps.as_dict(), "dropped_progress_updates": self.dropped_progress_updates,
            "source_authorities": ["RUN", "EXO", "RER", "validation", "ENV", "LIFE", "DIST", "TEST", "RMP", "CFG"],
            "owns_truth": False,
        }
        payload["snapshot_digest"] = _digest(payload)
        return payload


def capabilities(*, width:int, height:int, term:str="xterm", clipboard_available:bool=False, hyperlinks:bool=True, screen_reader:bool=False, reduced_motion:bool=False) -> ControlCenterCapabilities:
    width=max(20,int(width)); height=max(8,int(height)); dumb=term.lower() in {"dumb","unknown",""}
    mode = "plain" if dumb or screen_reader else "ultra-narrow" if width < 52 else "narrow" if width < 88 else "wide"
    return ControlCenterCapabilities(width,height,mode,not dumb and width>=52,not dumb and hyperlinks,clipboard_available,not dumb,not dumb and width>=120 and height>=24,not dumb and width>=72,reduced_motion,screen_reader)


def replay(records: Iterable[Mapping[str, Any]], **kwargs: Any) -> dict[str, Any]:
    center=TextualControlCenter()
    for record in records: center.ingest(record)
    return center.snapshot(**kwargs)


def assert_reference_only(snapshot: Mapping[str, Any]) -> None:
    if snapshot.get("owns_truth") is not False: raise AssertionError("Textual control center attempted to own canonical truth")
    encoded=json.dumps(snapshot, sort_keys=True).lower()
    for forbidden in ("raw_secret", "validation_truth", "heartbeat_owner", "process_attempt"):
        if forbidden in encoded: raise AssertionError(f"renderer leaked execution-owned field: {forbidden}")


def _identity(item: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value=item.get(key)
        if value is not None: return str(value)
    return None


def _runs(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]]={}
    for e in events:
        rid=_identity(e,"run_id","run")
        if not rid: continue
        row=by_id.setdefault(rid,{"run_id":rid,"state":"unknown","nodes":[]})
        state=e.get("state") or e.get("status")
        if state is not None: row["state"]=str(state).lower()
        node=_identity(e,"node_id","node")
        if node and node not in row["nodes"]: row["nodes"].append(node)
    rank={"failed":0,"error":0,"blocked":1,"running":2,"queued":3,"passed":4,"complete":4,"completed":4}
    return sorted(by_id.values(), key=lambda r:(rank.get(r["state"],3),r["run_id"]))


def _action_authority(action_id: str) -> str:
    if action_id in {"cancel","retry","rerun","resume"}: return "EXO/RUN"
    if action_id.startswith(("artifact.","overlay.")) or action_id in {"rollback","recover"}: return "LIFE"
    if action_id.startswith("worker."): return "DIST/SSH"
    if action_id.startswith("test."): return "TEST"
    return "PRES-navigation"


def _digest(payload: Mapping[str, Any]) -> str:
    return sha256(json.dumps(payload,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()
