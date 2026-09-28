from __future__ import annotations

"""CORE-X03 durable RUN V3 catalog primitives.

The module is intentionally storage-light: it provides the canonical data model,
state transitions, bounded stream persistence, query/index semantics, and GC /
doctor planning that later EXEC/LIFE/DIST adopters can consume without inventing
parallel history stores.
"""

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
import hashlib
import json
import os
import time

_ATTACHMENT_STATES = {"created", "pinned", "tombstoned"}
_PRIVACY_MODES = {"private", "metadata", "public"}
_STREAM_KINDS = {"stdout", "stderr", "event", "snapshot"}


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha(value: Any) -> str:
    raw = value if isinstance(value, str) else _stable_json(value)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tuple(values: Iterable[str] | None) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(item) for item in (values or ()) if str(item)))


@dataclass(frozen=True, slots=True)
class AttachmentDescriptor:
    schema_version: int
    attachment_id: str
    run_id: str
    kind: str
    path: str
    state: str = "created"
    privacy: str = "private"
    node_id: str = "root"
    process_id: str = "process"
    size: int = 0
    sha256: str = ""
    media_type: str = "application/octet-stream"
    remote_locator: str | None = None
    materialized: bool = True
    created_at: float = field(default_factory=lambda: round(time.time(), 6))

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("AttachmentDescriptor schema_version must be 1")
        if not self.attachment_id or not self.run_id:
            raise ValueError("attachment_id and run_id are required")
        if self.state not in _ATTACHMENT_STATES:
            raise ValueError(f"invalid attachment state: {self.state!r}")
        if self.privacy not in _PRIVACY_MODES:
            raise ValueError(f"invalid attachment privacy: {self.privacy!r}")
        if not self.materialized and not self.remote_locator:
            raise ValueError("lazy remote attachments require remote_locator")

    @classmethod
    def from_file(
        cls,
        *,
        run_id: str,
        path: str | Path,
        kind: str,
        node_id: str = "root",
        process_id: str = "process",
        privacy: str = "private",
        media_type: str = "application/octet-stream",
    ) -> "AttachmentDescriptor":
        p = Path(path)
        digest = _sha_file(p)
        attachment_id = _sha({"run_id": run_id, "path": str(p), "kind": kind, "sha256": digest})[:32]
        return cls(
            schema_version=1,
            attachment_id=attachment_id,
            run_id=run_id,
            kind=kind,
            path=str(p),
            node_id=node_id,
            process_id=process_id,
            privacy=privacy,
            size=p.stat().st_size,
            sha256=digest,
            media_type=media_type,
            materialized=True,
        )

    @classmethod
    def remote(
        cls,
        *,
        run_id: str,
        kind: str,
        remote_locator: str,
        node_id: str = "root",
        process_id: str = "process",
        privacy: str = "metadata",
        media_type: str = "application/octet-stream",
        size: int = 0,
        sha256: str = "",
    ) -> "AttachmentDescriptor":
        attachment_id = _sha({"run_id": run_id, "kind": kind, "remote_locator": remote_locator, "sha256": sha256})[:32]
        return cls(
            schema_version=1,
            attachment_id=attachment_id,
            run_id=run_id,
            kind=kind,
            path="",
            node_id=node_id,
            process_id=process_id,
            privacy=privacy,
            size=size,
            sha256=sha256,
            media_type=media_type,
            remote_locator=remote_locator,
            materialized=False,
        )

    def pin(self) -> "AttachmentDescriptor":
        if self.state == "tombstoned":
            raise ValueError("cannot pin a tombstoned attachment")
        return replace(self, state="pinned")

    def tombstone(self, *, force: bool = False) -> "AttachmentDescriptor":
        if self.state == "pinned" and not force:
            raise ValueError("cannot tombstone a pinned attachment without force")
        return replace(self, state="tombstoned")

    def to_dict(self, *, include_private_path: bool = False) -> dict[str, Any]:
        path = self.path if include_private_path or self.privacy == "public" else ""
        return {
            "schema_version": self.schema_version,
            "attachment_id": self.attachment_id,
            "run_id": self.run_id,
            "kind": self.kind,
            "path": path,
            "state": self.state,
            "privacy": self.privacy,
            "node_id": self.node_id,
            "process_id": self.process_id,
            "size": self.size,
            "sha256": self.sha256,
            "media_type": self.media_type,
            "remote_locator": self.remote_locator,
            "materialized": self.materialized,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class StreamDescriptor:
    schema_version: int
    stream_id: str
    run_id: str
    kind: str
    path: str
    node_id: str
    process_id: str
    byte_limit: int
    bytes_written: int
    truncated: bool
    sha256: str
    privacy: str = "private"

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("StreamDescriptor schema_version must be 1")
        if self.kind not in _STREAM_KINDS:
            raise ValueError(f"invalid stream kind: {self.kind!r}")
        if self.privacy not in _PRIVACY_MODES:
            raise ValueError(f"invalid stream privacy: {self.privacy!r}")
        if self.byte_limit < 0 or self.bytes_written < 0:
            raise ValueError("stream byte counts must be non-negative")

    def to_dict(self, *, include_private_path: bool = False) -> dict[str, Any]:
        path = self.path if include_private_path or self.privacy == "public" else ""
        return {
            "schema_version": self.schema_version,
            "stream_id": self.stream_id,
            "run_id": self.run_id,
            "kind": self.kind,
            "path": path,
            "node_id": self.node_id,
            "process_id": self.process_id,
            "privacy": self.privacy,
            "byte_limit": self.byte_limit,
            "bytes_written": self.bytes_written,
            "truncated": self.truncated,
            "sha256": self.sha256,
        }


def write_bounded_stream(
    *,
    run_root: str | Path,
    run_id: str,
    kind: str,
    content: bytes | str,
    node_id: str = "root",
    process_id: str = "process",
    privacy: str = "private",
    byte_limit: int = 1_048_576,
) -> StreamDescriptor:
    if kind not in _STREAM_KINDS:
        raise ValueError(f"invalid stream kind: {kind!r}")
    if privacy not in _PRIVACY_MODES:
        raise ValueError(f"invalid stream privacy: {privacy!r}")
    if byte_limit < 0:
        raise ValueError("byte_limit must be non-negative")
    data = content.encode("utf-8") if isinstance(content, str) else bytes(content)
    stored = data[:byte_limit]
    truncated = len(data) > byte_limit
    root = Path(run_root)
    stream_dir = root / "streams" / node_id / process_id
    stream_dir.mkdir(parents=True, exist_ok=True)
    stream_id = _sha({"run_id": run_id, "kind": kind, "node_id": node_id, "process_id": process_id, "digest": hashlib.sha256(data).hexdigest()})[:32]
    path = stream_dir / f"{kind}-{stream_id}.log"
    path.write_bytes(stored)
    return StreamDescriptor(
        schema_version=1,
        stream_id=stream_id,
        run_id=run_id,
        kind=kind,
        path=str(path),
        node_id=node_id,
        process_id=process_id,
        privacy=privacy,
        byte_limit=byte_limit,
        bytes_written=len(stored),
        truncated=truncated,
        sha256=hashlib.sha256(stored).hexdigest(),
    )


@dataclass(frozen=True, slots=True)
class CoalescedEvent:
    event_id: str
    run_id: str
    kind: str
    payload: Mapping[str, Any]
    update_count: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {"event_id": self.event_id, "run_id": self.run_id, "kind": self.kind, "payload": dict(self.payload), "update_count": self.update_count}


@dataclass(frozen=True, slots=True)
class RunReference:
    ref_id: str
    run_id: str
    attachment_id: str | None = None
    pinned: bool = False
    tombstoned: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"ref_id": self.ref_id, "run_id": self.run_id, "attachment_id": self.attachment_id, "pinned": self.pinned, "tombstoned": self.tombstoned}


@dataclass(frozen=True, slots=True)
class GcPlan:
    schema_version: int
    delete_paths: tuple[str, ...]
    keep_refs: tuple[str, ...]
    dangling_refs: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "delete_paths": list(self.delete_paths), "keep_refs": list(self.keep_refs), "dangling_refs": list(self.dangling_refs)}


@dataclass(frozen=True, slots=True)
class RunQuery:
    run_id: str | None = None
    command_id: str | None = None
    attachment_kind: str | None = None
    include_tombstoned: bool = False


@dataclass(slots=True)
class RunCatalog:
    root: Path
    runs: dict[str, Mapping[str, Any]] = field(default_factory=dict)
    attachments: dict[str, AttachmentDescriptor] = field(default_factory=dict)
    streams: dict[str, StreamDescriptor] = field(default_factory=dict)
    events: dict[str, CoalescedEvent] = field(default_factory=dict)
    references: dict[str, RunReference] = field(default_factory=dict)

    @classmethod
    def open(cls, root: str | Path) -> "RunCatalog":
        root_path = Path(root)
        root_path.mkdir(parents=True, exist_ok=True)
        catalog = cls(root=root_path)
        index = root_path / "catalog.json"
        if index.exists():
            raw = json.loads(index.read_text())
            catalog.runs = {str(k): v for k, v in raw.get("runs", {}).items()}
            catalog.attachments = {k: AttachmentDescriptor(**v) for k, v in raw.get("attachments", {}).items()}
            catalog.streams = {k: StreamDescriptor(**({"privacy": "private", **v})) for k, v in raw.get("streams", {}).items()}
            catalog.events = {k: CoalescedEvent(**v) for k, v in raw.get("events", {}).items()}
            catalog.references = {k: RunReference(**v) for k, v in raw.get("references", {}).items()}
        return catalog

    def save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = self.to_dict(include_private_paths=True)
        tmp = self.root / "catalog.json.tmp"
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.root / "catalog.json")

    def add_run(self, descriptor: Mapping[str, Any]) -> None:
        run_id = str(descriptor.get("run_id") or "")
        if not run_id:
            raise ValueError("run descriptor requires run_id")
        self.runs[run_id] = dict(descriptor)

    def add_attachment(self, attachment: AttachmentDescriptor) -> RunReference:
        if attachment.run_id not in self.runs:
            self.runs[attachment.run_id] = {"run_id": attachment.run_id}
        self.attachments[attachment.attachment_id] = attachment
        ref_id = f"run:{attachment.run_id}:attachment:{attachment.attachment_id}"
        ref = RunReference(ref_id=ref_id, run_id=attachment.run_id, attachment_id=attachment.attachment_id, pinned=attachment.state == "pinned", tombstoned=attachment.state == "tombstoned")
        self.references[ref_id] = ref
        return ref

    def add_stream(self, stream: StreamDescriptor) -> None:
        if stream.run_id not in self.runs:
            self.runs[stream.run_id] = {"run_id": stream.run_id}
        self.streams[stream.stream_id] = stream

    def coalesce_event(self, *, run_id: str, kind: str, key: str, payload: Mapping[str, Any]) -> CoalescedEvent:
        event_id = _sha({"run_id": run_id, "kind": kind, "key": key})[:32]
        previous = self.events.get(event_id)
        if previous is None:
            event = CoalescedEvent(event_id=event_id, run_id=run_id, kind=kind, payload=dict(payload), update_count=1)
        else:
            event = CoalescedEvent(event_id=event_id, run_id=run_id, kind=kind, payload=dict(payload), update_count=previous.update_count + 1)
        self.events[event_id] = event
        return event

    def pin_attachment(self, attachment_id: str) -> AttachmentDescriptor:
        updated = self.attachments[attachment_id].pin()
        self.attachments[attachment_id] = updated
        self.references[f"run:{updated.run_id}:attachment:{updated.attachment_id}"] = RunReference(
            ref_id=f"run:{updated.run_id}:attachment:{updated.attachment_id}", run_id=updated.run_id, attachment_id=updated.attachment_id, pinned=True, tombstoned=False
        )
        return updated

    def tombstone_attachment(self, attachment_id: str, *, force: bool = False) -> AttachmentDescriptor:
        updated = self.attachments[attachment_id].tombstone(force=force)
        self.attachments[attachment_id] = updated
        self.references[f"run:{updated.run_id}:attachment:{updated.attachment_id}"] = RunReference(
            ref_id=f"run:{updated.run_id}:attachment:{updated.attachment_id}", run_id=updated.run_id, attachment_id=updated.attachment_id, pinned=False, tombstoned=True
        )
        return updated

    def query(self, query: RunQuery) -> list[Mapping[str, Any]]:
        results: list[Mapping[str, Any]] = []
        for run_id, descriptor in sorted(self.runs.items()):
            if query.run_id and run_id != query.run_id:
                continue
            if query.command_id and descriptor.get("command_id") != query.command_id:
                continue
            attachments = [a for a in self.attachments.values() if a.run_id == run_id]
            if query.attachment_kind:
                attachments = [a for a in attachments if a.kind == query.attachment_kind]
                if not attachments:
                    continue
            if not query.include_tombstoned:
                attachments = [a for a in attachments if a.state != "tombstoned"]
                if query.attachment_kind and not attachments:
                    continue
            results.append({"run": dict(descriptor), "attachments": [a.to_dict() for a in attachments], "streams": [s.to_dict() for s in self.streams.values() if s.run_id == run_id]})
        return results

    def gc_plan(self) -> GcPlan:
        delete: list[str] = []
        keep: list[str] = []
        dangling: list[str] = []
        for ref in sorted(self.references.values(), key=lambda r: r.ref_id):
            attachment = self.attachments.get(ref.attachment_id or "")
            if attachment is None:
                dangling.append(ref.ref_id)
                continue
            if attachment.state == "pinned" or ref.pinned:
                keep.append(ref.ref_id)
                continue
            if attachment.state == "tombstoned" or ref.tombstoned:
                if attachment.materialized and attachment.path:
                    delete.append(attachment.path)
                continue
            if attachment.materialized and attachment.path and not Path(attachment.path).exists():
                dangling.append(ref.ref_id)
        return GcPlan(schema_version=1, delete_paths=_tuple(delete), keep_refs=_tuple(keep), dangling_refs=_tuple(dangling))

    def doctor(self) -> dict[str, Any]:
        plan = self.gc_plan()
        status = "pass" if not plan.dangling_refs else "fail"
        return {"schema_version": 1, "status": status, "gc_plan": plan.to_dict(), "run_count": len(self.runs), "attachment_count": len(self.attachments)}

    def to_dict(self, *, include_private_paths: bool = False) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "runs": dict(sorted(self.runs.items())),
            "attachments": {key: value.to_dict(include_private_path=include_private_paths) for key, value in sorted(self.attachments.items())},
            "streams": {key: value.to_dict(include_private_path=include_private_paths) for key, value in sorted(self.streams.items())},
            "events": {key: value.to_dict() for key, value in sorted(self.events.items())},
            "references": {key: value.to_dict() for key, value in sorted(self.references.items())},
        }


@dataclass(frozen=True, slots=True)
class WrapperRuntimeIdentity:
    executable: str
    sha256: str
    trusted: bool
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"executable": self.executable, "sha256": self.sha256, "trusted": self.trusted, "reason": self.reason}


def qualify_wrapper_runtime(executable: str | Path, *, expected_sha256: str | None = None) -> WrapperRuntimeIdentity:
    path = Path(executable)
    if not path.exists() or not path.is_file():
        return WrapperRuntimeIdentity(str(path), "", False, "missing-runtime")
    digest = _sha_file(path)
    if expected_sha256 and digest != expected_sha256:
        return WrapperRuntimeIdentity(str(path), digest, False, "sha256-mismatch")
    executable_bit = os.access(path, os.X_OK)
    return WrapperRuntimeIdentity(str(path), digest, executable_bit, "trusted" if executable_bit else "not-executable")


@dataclass(frozen=True, slots=True)
class WrapperPlanQualification:
    schema_version: int
    plan_id: str
    command_id: str
    required_inputs: tuple[str, ...]
    missing_inputs: tuple[str, ...]
    runtime: WrapperRuntimeIdentity
    machine_envelope: Mapping[str, Any]

    @property
    def ready(self) -> bool:
        return not self.missing_inputs and self.runtime.trusted

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "command_id": self.command_id,
            "ready": self.ready,
            "required_inputs": list(self.required_inputs),
            "missing_inputs": list(self.missing_inputs),
            "runtime": self.runtime.to_dict(),
            "machine_envelope": dict(self.machine_envelope),
        }


def qualify_wrapper_plan(
    *,
    command_id: str,
    executable: str | Path,
    required_inputs: Sequence[str] = (),
    provided_inputs: Mapping[str, Any] | None = None,
    expected_sha256: str | None = None,
) -> WrapperPlanQualification:
    provided = set((provided_inputs or {}).keys())
    required = _tuple(required_inputs)
    missing = tuple(item for item in required if item not in provided or provided_inputs is None or provided_inputs.get(item) in (None, ""))
    runtime = qualify_wrapper_runtime(executable, expected_sha256=expected_sha256)
    plan_id = _sha({"command_id": command_id, "runtime": runtime.to_dict(), "required": required, "missing": missing})[:32]
    return WrapperPlanQualification(
        schema_version=1,
        plan_id=plan_id,
        command_id=command_id,
        required_inputs=required,
        missing_inputs=missing,
        runtime=runtime,
        machine_envelope={"schema_version": 1, "kind": "wrapper-plan-qualification", "command_id": command_id, "plan_id": plan_id},
    )


@dataclass(frozen=True, slots=True)
class TypedRuntimeFailure:
    code: str
    message: str
    retryable: bool = False
    detail: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "retryable": self.retryable, "detail": dict(self.detail)}


@dataclass(frozen=True, slots=True)
class LauncherUpdatePlan:
    source: str
    destination: str
    lock_path: str
    backup_path: str
    atomic: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "destination": self.destination, "lock_path": self.lock_path, "backup_path": self.backup_path, "atomic": self.atomic}


def launcher_update_plan(source: str | Path, destination: str | Path) -> LauncherUpdatePlan:
    dst = Path(destination)
    repo_lock = dst.parent / ".devtool-wrapper.lock"
    return LauncherUpdatePlan(source=str(source), destination=str(destination), lock_path=str(repo_lock), backup_path=str(dst.with_suffix(dst.suffix + ".bak")), atomic=True)
