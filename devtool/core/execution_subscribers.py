from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any, Callable
import json, os, tempfile
from .events.listeners import event_to_dict
from .execution_state import ExecutionSnapshot

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
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)
    _last_sequence: int = field(default=-1, init=False)
    _closed: bool = field(default=False, init=False)
    @property
    def snapshot_file(self): return self.directory/"execution.json"
    @property
    def journal_file(self): return self.directory/"execution-events.jsonl"
    @property
    def history_file(self): return (self.history_directory or self.directory/"history")/self.run_id/"execution.json"
    def __call__(self,event:Any)->None:
        if self._closed or str(getattr(event,"run_id",""))!=self.run_id: return
        snapshot=self.store.snapshot(self.run_id)
        if snapshot is None: return
        with self._lock:
            if snapshot.last_sequence<=self._last_sequence: return
            self.directory.mkdir(parents=True,exist_ok=True)
            with self.journal_file.open("a",encoding="utf-8") as h:
                h.write(json.dumps(event_to_dict(event),ensure_ascii=False,sort_keys=True)+"\n")
            doc=canonical_execution_document(snapshot); _atomic_json(self.snapshot_file,doc)
            if self.ipc_publish:
                payload=legacy_execution_projection(snapshot); payload["execution"]=doc["execution"]; self.ipc_publish(payload)
            if snapshot.status in {"succeeded","failed","blocked","skipped","cancelled"}: _atomic_json(self.history_file,doc)
            self._last_sequence=snapshot.last_sequence
    def close(self)->None: self._closed=True
