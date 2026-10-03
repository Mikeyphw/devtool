from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any, Callable
import json, os, tempfile
from .events.listeners import event_to_dict
from ..logstore import redact_sensitive_value
from .execution_state import ExecutionSnapshot
from .execution_limits import (
    MAX_JOURNAL_PROCESS_OUTPUT_CHARS,
    bound_event_reference_fields,
)

def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as h:
            json.dump(payload, h, ensure_ascii=False, sort_keys=True); h.write("\n"); h.flush(); os.fsync(h.fileno())
        os.replace(tmp, path)
    finally:
        try: os.unlink(tmp)
        except FileNotFoundError: pass

def canonical_execution_document(snapshot: ExecutionSnapshot) -> dict[str, Any]:
    return {"schema_version": 1, "kind": "devtool.execution.snapshot", "run_id": snapshot.run_id,
            "sequence": snapshot.last_sequence, "event_id": snapshot.last_event_id,
            "status": snapshot.status, "execution": snapshot.to_dict()}

def compact_event_document(
    event: Any,
    *,
    max_output_chars: int = MAX_JOURNAL_PROCESS_OUTPUT_CHARS,
) -> dict[str, Any]:
    """Bound and redact the durable journal representation of one event.

    A federated RUN produced by a real remote SSH Devtool advertises the
    transport protocol implemented by *that remote process* in its canonical
    RunStarted journal entry.  The parent can then negotiate against actual
    peer bytes rather than assuming its own local version is present remotely.
    """
    payload = bound_event_reference_fields(redact_sensitive_value(event_to_dict(event)))
    if (
        str(payload.get("event_type") or "") == "run.started"
        and str(os.environ.get("DEVTOOL_INSIDE_SSH") or "").strip().lower() in {"1", "true", "yes", "on"}
        and str(os.environ.get("DEVTOOL_FEDERATION_RELATIONSHIP") or "").strip() == "child"
    ):
        from ..distributed_transport import TransportProtocolCapabilities
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
            payload["metadata"] = metadata
        metadata["dist_transport_peer"] = "ssh-remote-devtool"
        metadata["dist_transport_capabilities"] = TransportProtocolCapabilities().as_dict()
    if str(payload.get("event_type") or "") == "process.output":
        text = str(payload.get("text") or "")
        if len(text) > max_output_chars:
            payload["text"] = text[-max_output_chars:]
            payload["output_truncated"] = True
            payload["original_length"] = len(text)
    return payload

def legacy_execution_projection(snapshot: ExecutionSnapshot) -> dict[str, Any]:
    accounting = snapshot.accounting.leaf
    measured = snapshot.accounting.leaf.progress
    failed=[n for n in snapshot.nodes if n.status=="failed"]; running=[n for n in snapshot.nodes if n.status=="running"]
    phase=running[-1] if running else (failed[-1] if failed else None)
    return {"run_id":snapshot.run_id,"status":snapshot.status,"sequence":snapshot.last_sequence,"event_id":snapshot.last_event_id,
            "progress":{"semantics_version":2,"percent":measured.measured_percent,"measured_percent":measured.measured_percent,
                        "resolution_percent":measured.resolution_percent,
                        "measurement_coverage_percent":measured.coverage_percent,"determinate":measured.determinate},
            "operation_counts":accounting.counts.to_dict(),
            "phase":({"node_id":phase.node_id,"name":phase.label,"status":phase.status,"kind":phase.kind,
                      "progress":phase.progress.to_dict()} if phase else None),
            "failed_node_id":failed[-1].node_id if failed else "",
            "execution_schema_version":snapshot.schema_version}

@dataclass(slots=True)
class ExecutionProjectionSubscriber:
    store: Any
    run_id: str
    directory: Path
    ipc_publish: Callable[[dict[str, Any]], Any] | None = None
    history_directory: Path | None = None
    checkpoint_interval: int = 50
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)
    _events_written: int = field(default=0, init=False)
    _last_sequence: int = field(default=-1, init=False)
    _closed: bool = field(default=False, init=False)
    def __post_init__(self) -> None:
        try:
            if self.journal_file.is_file():
                self._events_written = sum(
                    1 for line in self.journal_file.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                )
        except OSError:
            self._events_written = 0

    @property
    def snapshot_file(self): return self.directory/"execution.json"
    @property
    def journal_file(self): return self.directory/"execution-events.jsonl"
    @property
    def history_file(self): return (self.history_directory or self.directory/"history")/self.run_id/"execution.json"
    @property
    def checkpoint_file(self): return self.directory/"execution-checkpoint.json"
    def __call__(self,event:Any)->None:
        if self._closed or str(getattr(event,"run_id",""))!=self.run_id: return
        snapshot=self.store.snapshot(self.run_id)
        if snapshot is None: return
        with self._lock:
            if snapshot.last_sequence<=self._last_sequence: return
            self.directory.mkdir(parents=True,exist_ok=True)
            with self.journal_file.open("a",encoding="utf-8") as h:
                h.write(json.dumps(compact_event_document(event),ensure_ascii=False,sort_keys=True)+"\n")
            self._events_written += 1
            doc=canonical_execution_document(snapshot); _atomic_json(self.snapshot_file,doc)
            terminal = snapshot.status in {"succeeded","failed","blocked","skipped","cancelled"}
            interval = max(1, int(self.checkpoint_interval or 1))
            if terminal or self._events_written % interval == 0:
                _atomic_json(self.checkpoint_file, {
                    "schema_version": 1,
                    "kind": "devtool.execution.checkpoint",
                    "run_id": snapshot.run_id,
                    "sequence": snapshot.last_sequence,
                    "event_id": snapshot.last_event_id,
                    "journal_events": self._events_written,
                    "execution": doc["execution"],
                })
            if self.ipc_publish:
                payload=legacy_execution_projection(snapshot); payload["execution"]=doc["execution"]; self.ipc_publish(payload)
            if terminal: _atomic_json(self.history_file,doc)
            self._last_sequence=snapshot.last_sequence
    def close(self)->None: self._closed=True
