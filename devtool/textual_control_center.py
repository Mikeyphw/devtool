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

PAGES = ("dashboard", "runs", "logs", "diagnostics", "history", "targets", "workers", "validation", "artifacts", "overlays", "evidence")
NAV_KEYS = {"d":"dashboard", "u":"runs", "l":"logs", "g":"diagnostics", "h":"history", "t":"targets", "w":"workers", "v":"validation", "s":"artifacts", "o":"overlays", "e":"evidence"}
TERMINAL_STATES = {"passed", "failed", "cancelled", "blocked", "error", "complete", "completed"}

PANEL_AUTHORITIES: dict[str, str] = {
    "dashboard": "RUN/PRES",
    "runs": "RUN/EXO",
    "logs": "RUN",
    "diagnostics": "EXO",
    "history": "RUN",
    "targets": "EXEC/WRAP/TEST",
    "workers": "DIST/SSH",
    "validation": "validation",
    "artifacts": "LIFE",
    "overlays": "LIFE",
    "evidence": "RUN/CORE",
}

@dataclass(frozen=True)
class OperatorActionSpec:
    id: str
    authority: str
    destructive: bool = False
    requires_run: bool = False
    requires_node: bool = False

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


ACTION_SPECS: dict[str, OperatorActionSpec] = {
    "cancel": OperatorActionSpec("cancel", "EXO/RUN", destructive=True, requires_run=True),
    "retry": OperatorActionSpec("retry", "EXO/RUN", requires_run=True),
    "rerun": OperatorActionSpec("rerun", "EXO/RUN", requires_run=True),
    "resume": OperatorActionSpec("resume", "EXO/RUN", requires_run=True),
    "logs.open": OperatorActionSpec("logs.open", "RUN", requires_run=True),
    "workflow.inspect": OperatorActionSpec("workflow.inspect", "EXEC/WRAP"),
    "target.launch": OperatorActionSpec("target.launch", "EXEC/WRAP/TEST"),
    "artifact.apply": OperatorActionSpec("artifact.apply", "LIFE", destructive=True),
    "overlay.apply": OperatorActionSpec("overlay.apply", "LIFE", destructive=True),
    "rollback": OperatorActionSpec("rollback", "LIFE", destructive=True),
    "recover": OperatorActionSpec("recover", "LIFE", destructive=True),
    "worker.cancel": OperatorActionSpec("worker.cancel", "DIST/SSH", destructive=True),
    "worker.resume": OperatorActionSpec("worker.resume", "DIST/SSH"),
    "worker.recover": OperatorActionSpec("worker.recover", "DIST/SSH", destructive=True),
}

PAGE_ACTIONS: dict[str, tuple[str, ...]] = {
    "dashboard": (),
    "runs": ("cancel", "retry", "rerun", "resume"),
    "logs": ("logs.open",),
    "diagnostics": ("logs.open",),
    "history": ("rerun",),
    "targets": ("workflow.inspect", "target.launch"),
    "workers": ("worker.cancel", "worker.resume", "worker.recover"),
    "validation": (),
    "artifacts": ("artifact.apply",),
    "overlays": ("overlay.apply", "rollback", "recover"),
    "evidence": (),
}

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
class TerminalRestorationGuard:
    """Small explicit lifetime guard for renderer-owned terminal state.

    Textual remains responsible for the actual terminal driver.  This guard makes
    Devtool's ownership boundary observable and guarantees our runtime always
    records restoration through normal return and exceptions.
    """

    active: bool = False
    restored: bool = False

    def __enter__(self) -> "TerminalRestorationGuard":
        self.active = True
        self.restored = False
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        self.active = False
        self.restored = True
        return False


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
    panel_failures: dict[str, str] = field(default_factory=dict)
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

    def degrade_panel(self, page: str, reason: str) -> None:
        if page not in PAGES:
            raise ValueError(f"unknown control-center page: {page}")
        self.panel_failures[page] = str(reason)

    def recover_panel(self, page: str) -> None:
        self.panel_failures.pop(page, None)

    def action_availability(self, action_id: str) -> tuple[bool, str | None]:
        spec = ACTION_SPECS.get(action_id)
        if spec is None:
            return False, "unsupported-action"
        if spec.requires_run and not self.selected_run:
            return False, "select-a-run"
        if spec.requires_node and not self.selected_node:
            return False, "select-a-node"
        return True, None

    def visible_actions(self, page: str | None = None) -> list[dict[str, Any]]:
        current = page or self.page
        if current not in PAGES:
            raise ValueError(f"unknown control-center page: {current}")
        rows: list[dict[str, Any]] = []
        for action_id in PAGE_ACTIONS[current]:
            spec = ACTION_SPECS[action_id]
            enabled, disabled_reason = self.action_availability(action_id)
            row = spec.as_dict()
            row.update({"enabled": enabled, "disabled_reason": disabled_reason})
            rows.append(row)
        return rows

    def action_request(self, action_id: str, *, confirmed: bool = False) -> ControlActionRequest:
        spec = ACTION_SPECS.get(action_id)
        if spec is None:
            raise KeyError(f"unsupported control-center action: {action_id}")
        enabled, reason = self.action_availability(action_id)
        if not enabled:
            raise RuntimeError(f"{action_id} disabled: {reason}")
        if spec.destructive and not confirmed:
            raise PermissionError(f"{action_id} requires canonical confirmation")
        payload = f"{action_id}\0{self.selected_run or ''}\0{self.selected_node or ''}\0{len(self.events)}"
        return ControlActionRequest(sha256(payload.encode()).hexdigest()[:20], action_id, self.selected_run, self.selected_node, spec.destructive, spec.authority)

    def handle_key(self, key: str, *, confirmed: bool = False) -> dict[str, Any]:
        normalized = str(key).strip().lower()
        if normalized in {"q", "esc", "escape"}:
            return {"kind": "exit", "cancel_requested": False, "key": normalized}
        if normalized in {"ctrl+c", "^c"}:
            enabled, reason = self.action_availability("cancel")
            if not enabled:
                return {"kind": "disabled", "action_id": "cancel", "reason": reason}
            if not confirmed:
                return {"kind": "confirmation-required", "action_id": "cancel"}
            return {"kind": "action-request", "request": self.action_request("cancel", confirmed=True).as_dict()}
        if normalized in NAV_KEYS:
            self.navigate(NAV_KEYS[normalized])
            return {"kind": "navigate", "page": self.page}
        return {"kind": "unhandled", "key": normalized}

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
            "panel_health": {page: {"status": "degraded" if page in self.panel_failures else "healthy", "reason": self.panel_failures.get(page), "authority": PANEL_AUTHORITIES[page]} for page in PAGES},
            "visible_actions": self.visible_actions(),
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
    spec = ACTION_SPECS.get(action_id)
    if spec is None:
        raise KeyError(f"unsupported control-center action: {action_id}")
    return spec.authority


def render_control_center_text(snapshot: Mapping[str, Any]) -> str:
    """Render a bounded plain-text view from the canonical control-center snapshot."""
    page = str(snapshot.get("page") or "dashboard")
    health = dict(snapshot.get("panel_health") or {}).get(page, {})
    lines = [
        "Devtool Textual Control Center",
        f"Page: {page}",
        f"Panel: {health.get('status', 'unknown')} · authority={health.get('authority', 'unknown')}",
    ]
    if health.get("reason"):
        lines.append(f"Panel issue: {health['reason']}")
    selected_run = snapshot.get("selected_run")
    selected_node = snapshot.get("selected_node")
    lines.append(f"Selection: run={selected_run or '-'} node={selected_node or '-'}")
    lines.append(f"Runs: {len(snapshot.get('runs') or [])} · attention={len(snapshot.get('attention') or [])}")
    if page == "logs":
        lines.append(f"Log lines: {len(snapshot.get('log_lines') or [])} · paused={bool(snapshot.get('paused'))} · follow={bool(snapshot.get('follow_logs'))}")
    actions = list(snapshot.get("visible_actions") or [])
    if actions:
        rendered=[]
        for action in actions:
            state="enabled" if action.get("enabled") else f"disabled:{action.get('disabled_reason')}"
            rendered.append(f"{action.get('id')}[{state}]@{action.get('authority')}")
        lines.append("Actions: " + ", ".join(rendered))
    lines.append("Keys: " + " ".join(f"{key}={page_name}" for key,page_name in NAV_KEYS.items()) + " ctrl+c=cancel q/esc=exit ?=help")
    return "\n".join(lines)

def _digest(payload: Mapping[str, Any]) -> str:
    return sha256(json.dumps(payload,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()
