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
class RunRelation:
    relation_id: str
    parent_run_id: str
    child_run_id: str
    parent_node_id: str = ""
    relationship: str = "delegated-child"
    delegation_id: str = ""
    provider_identity: Mapping[str, Any] = field(default_factory=dict)
    delivery_health: str = "unknown"
    pinned: bool = False

    def __post_init__(self) -> None:
        if not self.parent_run_id or not self.child_run_id:
            raise ValueError("run relation requires parent_run_id and child_run_id")
        if self.parent_run_id == self.child_run_id:
            raise ValueError("run relation cannot point to itself")
        if not self.relation_id:
            object.__setattr__(self, "relation_id", _sha({
                "parent_run_id": self.parent_run_id,
                "child_run_id": self.child_run_id,
                "parent_node_id": self.parent_node_id,
                "relationship": self.relationship,
                "delegation_id": self.delegation_id,
            })[:32])

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RunRelation":
        return cls(
            relation_id=str(payload.get("relation_id") or ""),
            parent_run_id=str(payload.get("parent_run_id") or ""),
            child_run_id=str(payload.get("child_run_id") or ""),
            parent_node_id=str(payload.get("parent_node_id") or ""),
            relationship=str(payload.get("relationship") or "delegated-child"),
            delegation_id=str(payload.get("delegation_id") or ""),
            provider_identity=dict(payload.get("provider_identity") or {}),
            delivery_health=str(payload.get("delivery_health") or "unknown"),
            pinned=bool(payload.get("pinned")),
        )

    def pin(self) -> "RunRelation":
        return replace(self, pinned=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "relation_id": self.relation_id,
            "parent_run_id": self.parent_run_id,
            "child_run_id": self.child_run_id,
            "parent_node_id": self.parent_node_id,
            "relationship": self.relationship,
            "delegation_id": self.delegation_id,
            "provider_identity": dict(self.provider_identity),
            "delivery_health": self.delivery_health,
            "pinned": self.pinned,
        }


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
    status: str | None = None
    provider: str | None = None
    name_contains: str | None = None
    since: float | None = None
    until: float | None = None
    include_tombstoned: bool = False
    offset: int = 0
    limit: int = 100

    def __post_init__(self) -> None:
        if self.offset < 0:
            raise ValueError("RunQuery offset must be non-negative")
        if self.limit < 1 or self.limit > 1000:
            raise ValueError("RunQuery limit must be between 1 and 1000")
        if self.since is not None and self.until is not None and self.since > self.until:
            raise ValueError("RunQuery since must not be after until")


@dataclass(slots=True)
class RunCatalog:
    root: Path
    runs: dict[str, Mapping[str, Any]] = field(default_factory=dict)
    attachments: dict[str, AttachmentDescriptor] = field(default_factory=dict)
    streams: dict[str, StreamDescriptor] = field(default_factory=dict)
    events: dict[str, CoalescedEvent] = field(default_factory=dict)
    references: dict[str, RunReference] = field(default_factory=dict)
    relations: dict[str, RunRelation] = field(default_factory=dict)

    @classmethod
    def open(cls, root: str | Path, *, create: bool = True) -> "RunCatalog":
        """Open a run catalog.

        ``create=False`` is the observation/query path: it never creates the
        catalog directory merely because a user asked for history/help/status.
        Mutation owners call the default ``create=True`` path and ``save()``
        still creates the directory at the durability boundary.
        """
        root_path = Path(root)
        if create:
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
            catalog.relations = {k: RunRelation.from_dict(v) for k, v in raw.get("relations", {}).items()}
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

    def add_relation(self, relation: RunRelation) -> RunRelation:
        existing = self.relations.get(relation.relation_id)
        if existing is not None and existing != relation:
            raise ValueError(f"run relation id collision: {relation.relation_id}")
        for prior in self.relations.values():
            if prior.child_run_id == relation.child_run_id and prior.relation_id != relation.relation_id:
                raise ValueError(
                    f"run {relation.child_run_id!r} already has canonical parent relation {prior.relation_id!r}"
                )
        self.relations[relation.relation_id] = relation
        self.runs.setdefault(relation.parent_run_id, {"run_id": relation.parent_run_id})
        self.runs.setdefault(relation.child_run_id, {"run_id": relation.child_run_id})
        return relation

    def pin_relation(self, relation_id: str) -> RunRelation:
        updated = self.relations[relation_id].pin()
        self.relations[relation_id] = updated
        return updated

    def ancestors(self, run_id: str) -> list[Mapping[str, Any]]:
        current = str(run_id or "")
        rows: list[Mapping[str, Any]] = []
        seen: set[str] = set()
        while current:
            relation = next((item for item in self.relations.values() if item.child_run_id == current), None)
            if relation is None:
                break
            if relation.relation_id in seen:
                raise ValueError("run hierarchy cycle detected while resolving ancestors")
            seen.add(relation.relation_id)
            rows.append({
                "relation": relation.to_dict(),
                "run": dict(self.runs.get(relation.parent_run_id, {"run_id": relation.parent_run_id})),
            })
            current = relation.parent_run_id
        return rows

    def descendants(self, run_id: str) -> list[Mapping[str, Any]]:
        root = str(run_id or "")
        rows: list[Mapping[str, Any]] = []
        queue = [root]
        seen_runs = {root}
        while queue:
            parent = queue.pop(0)
            children = sorted(
                (item for item in self.relations.values() if item.parent_run_id == parent),
                key=lambda item: (item.child_run_id, item.relation_id),
            )
            for relation in children:
                if relation.child_run_id in seen_runs:
                    raise ValueError("run hierarchy cycle detected while resolving descendants")
                seen_runs.add(relation.child_run_id)
                rows.append({
                    "relation": relation.to_dict(),
                    "run": dict(self.runs.get(relation.child_run_id, {"run_id": relation.child_run_id})),
                })
                queue.append(relation.child_run_id)
        return rows

    def subtree(self, run_id: str) -> dict[str, Any]:
        root = dict(self.runs.get(run_id, {"run_id": run_id}))
        return {
            "root": root,
            "ancestors": self.ancestors(run_id),
            "descendants": self.descendants(run_id),
        }

    def parent_progress(self, run_id: str) -> dict[str, Any]:
        direct = [item for item in self.relations.values() if item.parent_run_id == run_id]
        statuses: dict[str, int] = {}
        for relation in direct:
            status = str(self.runs.get(relation.child_run_id, {}).get("status") or "unknown")
            statuses[status] = statuses.get(status, 0) + 1
        terminal = sum(count for status, count in statuses.items() if status in {"succeeded", "failed", "cancelled", "blocked", "skipped"})
        return {
            "parent_run_id": run_id,
            "child_count": len(direct),
            "terminal_count": terminal,
            "active_count": max(0, len(direct) - terminal),
            "statuses": dict(sorted(statuses.items())),
            "flattened_child_identity": False,
        }

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
        ordered = sorted(
            self.runs.items(),
            key=lambda item: (float(item[1].get("created_at") or item[1].get("started_at") or 0.0), item[0]),
            reverse=True,
        )
        for run_id, descriptor in ordered:
            if query.run_id and run_id != query.run_id:
                continue
            if query.command_id and descriptor.get("command_id") != query.command_id:
                continue
            if query.status and str(descriptor.get("status") or "") != query.status:
                continue
            provider = str(descriptor.get("provider") or descriptor.get("execution_provider") or "")
            if query.provider and provider != query.provider:
                continue
            name = str(descriptor.get("name") or descriptor.get("label") or descriptor.get("command_id") or run_id)
            if query.name_contains and query.name_contains.lower() not in name.lower():
                continue
            timestamp = float(descriptor.get("created_at") or descriptor.get("started_at") or 0.0)
            if query.since is not None and timestamp < query.since:
                continue
            if query.until is not None and timestamp > query.until:
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
        return results[query.offset : query.offset + query.limit]

    def gc_plan(self) -> GcPlan:
        delete: list[str] = []
        keep: list[str] = []
        dangling: list[str] = []
        pinned_relation_runs: set[str] = set()
        for relation in self.relations.values():
            if not relation.pinned:
                continue
            pinned_relation_runs.add(relation.child_run_id)
            pinned_relation_runs.update(
                str(item.get("run", {}).get("run_id") or "")
                for item in self.descendants(relation.child_run_id)
            )
        for ref in sorted(self.references.values(), key=lambda r: r.ref_id):
            attachment = self.attachments.get(ref.attachment_id or "")
            if attachment is None:
                dangling.append(ref.ref_id)
                continue
            if attachment.state == "pinned" or ref.pinned or attachment.run_id in pinned_relation_runs:
                keep.append(ref.ref_id if attachment.run_id not in pinned_relation_runs else f"relation-retention:{attachment.run_id}:{ref.ref_id}")
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
        return {
            "schema_version": 2,
            "status": status,
            "gc_plan": plan.to_dict(),
            "run_count": len(self.runs),
            "attachment_count": len(self.attachments),
            "stream_count": len(self.streams),
            "reference_count": len(self.references),
            "relation_count": len(self.relations),
            "root_classification": "private-state",
            "index_rebuildable": True,
        }

    def to_dict(self, *, include_private_paths: bool = False) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "runs": dict(sorted(self.runs.items())),
            "attachments": {key: value.to_dict(include_private_path=include_private_paths) for key, value in sorted(self.attachments.items())},
            "streams": {key: value.to_dict(include_private_path=include_private_paths) for key, value in sorted(self.streams.items())},
            "events": {key: value.to_dict() for key, value in sorted(self.events.items())},
            "references": {key: value.to_dict() for key, value in sorted(self.references.items())},
            "relations": {key: value.to_dict() for key, value in sorted(self.relations.items())},
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


def legacy_run_storage_plan(repo_root: str | Path, *, catalog_root: str | Path) -> dict[str, Any]:
    """Describe legacy run/log families without mutating them.

    CORE owns the migration *classification* and destination envelope.  LIFE
    remains responsible for recovery-sensitive transaction state and DIST owns
    published artifacts/caches, so those families are explicitly external.
    """
    repo = Path(repo_root).resolve(strict=False)
    catalog = Path(catalog_root).resolve(strict=False)
    candidates = (
        ("legacy-logs", repo / ".devtool" / "logs", "migrate-as-run-attachments"),
        ("legacy-test-runs", repo / ".devtool" / "test-runs", "migrate-as-test-attachments"),
        ("legacy-evidence", repo / ".devtool" / "evidence", "migrate-as-evidence-attachments"),
        ("legacy-desktop-history", repo / ".devtool" / "desktop" / "history", "migrate-as-run-metadata"),
        ("transactions", repo / ".devtool" / "transactions", "external-life-owned"),
        ("published-artifacts", repo / ".devtool" / "build-artifacts", "external-dist-owned"),
    )
    rows = []
    for family, path, action in candidates:
        exists = path.exists()
        rows.append({"family": family, "path": str(path), "exists": exists, "action": action})
    return {
        "schema_version": 1,
        "kind": "devtool.run-store-migration-plan",
        "repository": str(repo),
        "catalog_root": str(catalog),
        "families": rows,
        "migration_candidates": sum(1 for row in rows if row["exists"] and str(row["action"]).startswith("migrate")),
        "mutates": False,
    }


def migrate_legacy_run_storage(
    repo_root: str | Path,
    catalog: RunCatalog,
    *,
    max_files: int = 10_000,
) -> dict[str, Any]:
    """Adopt readable legacy run/log/history files into RUN V3 metadata.

    Migration is explicit and non-destructive: legacy bytes remain in place and
    become private attachments in the canonical catalog.  Re-running the
    migration is idempotent because run and attachment identities are derived
    from stable source family/path/content identity.  LIFE-owned transaction
    state and DIST-owned published artifacts are never imported here.
    """
    if max_files < 1:
        raise ValueError("max_files must be positive")
    repo = Path(repo_root).resolve(strict=False)
    plan = legacy_run_storage_plan(repo, catalog_root=catalog.root)
    action_to_kind = {
        "migrate-as-run-attachments": "legacy-log",
        "migrate-as-test-attachments": "legacy-test-evidence",
        "migrate-as-evidence-attachments": "legacy-evidence",
        "migrate-as-run-metadata": "legacy-history",
    }
    migrated = 0
    families: list[dict[str, Any]] = []
    truncated = False
    for row in plan["families"]:
        action = str(row.get("action") or "")
        kind = action_to_kind.get(action)
        if kind is None or not row.get("exists"):
            continue
        source_root = Path(str(row["path"]))
        files: list[Path]
        if source_root.is_file():
            files = [source_root]
        else:
            files = sorted(path for path in source_root.rglob("*") if path.is_file() and not path.is_symlink())
        run_id = "legacy-" + _sha({"repository": str(repo), "family": row["family"]})[:24]
        family_count = 0
        earliest: float | None = None
        for source in files:
            if migrated >= max_files:
                truncated = True
                break
            try:
                stat = source.stat()
                attachment = AttachmentDescriptor.from_file(run_id=run_id, path=source, kind=kind, privacy="private")
            except OSError:
                continue
            catalog.add_attachment(attachment)
            timestamp = float(stat.st_mtime)
            earliest = timestamp if earliest is None else min(earliest, timestamp)
            family_count += 1
            migrated += 1
        if family_count:
            catalog.add_run(
                {
                    "run_id": run_id,
                    "command_id": "legacy.import",
                    "status": "legacy",
                    "provider": "legacy-local",
                    "name": f"Legacy {row['family']} import",
                    "created_at": earliest or 0.0,
                    "legacy_source_family": row["family"],
                    "legacy_source_root": str(source_root),
                }
            )
            families.append({"family": row["family"], "run_id": run_id, "attachments": family_count})
        if truncated:
            break
    if migrated:
        catalog.save()
    return {
        "schema_version": 1,
        "kind": "devtool.run-store-migration-result",
        "repository": str(repo),
        "catalog_root": str(catalog.root),
        "migrated_attachments": migrated,
        "families": families,
        "truncated": truncated,
        "max_files": max_files,
        "mutated": bool(migrated),
        "source_bytes_preserved": True,
    }


def repository_storage_inventory(repo_root: str | Path, catalog_root: str | Path) -> dict[str, Any]:
    """Classify known durable repository-local storage families.

    The inventory is intentionally ownership-oriented rather than a deletion
    plan. Unknown top-level ``.devtool`` families are surfaced as unclassified
    so CORE cannot silently acquire another durable state universe.
    """
    repo = Path(repo_root).resolve(strict=False)
    devtool_root = repo / ".devtool"
    known = {
        "private": ("state", "CORE"),
        "logs": ("legacy", "CORE-migration"),
        "test-runs": ("legacy", "CORE-migration"),
        "evidence": ("legacy", "CORE-migration"),
        "desktop": ("legacy", "CORE-migration"),
        "transactions": ("state", "LIFE"),
        "build-artifacts": ("published", "DIST"),
        "cache": ("cache", "external/provider"),
    }
    rows: list[dict[str, Any]] = [
        {
            "path": str(Path(catalog_root).resolve(strict=False)),
            "classification": "state",
            "owner": "CORE",
            "family": "run-v3",
            "exists": Path(catalog_root).exists(),
        },
        {
            "path": str(repo / "share" / "core-generated"),
            "classification": "projection",
            "owner": "CORE",
            "family": "core-generated",
            "exists": (repo / "share" / "core-generated").exists(),
        },
    ]
    if devtool_root.exists():
        for child in sorted(devtool_root.iterdir(), key=lambda item: item.name):
            classification, owner = known.get(child.name, ("unclassified", "unknown"))
            rows.append(
                {
                    "path": str(child),
                    "classification": classification,
                    "owner": owner,
                    "family": child.name,
                    "exists": True,
                }
            )
    unclassified = [row for row in rows if row["classification"] == "unclassified"]
    return {
        "schema_version": 1,
        "kind": "devtool.repository-storage-inventory",
        "paths": rows,
        "unclassified": unclassified,
        "status": "pass" if not unclassified else "fail",
    }


def run_storage_status(repo_root: str | Path, catalog: RunCatalog) -> dict[str, Any]:
    doctor = catalog.doctor()
    migration = legacy_run_storage_plan(repo_root, catalog_root=catalog.root)
    inventory = repository_storage_inventory(repo_root, catalog.root)
    status = "pass" if doctor["status"] == "pass" and inventory["status"] == "pass" else "fail"
    return {
        "schema_version": 1,
        "kind": "devtool.run-storage-status",
        "status": status,
        "repository": str(Path(repo_root).resolve(strict=False)),
        "catalog": doctor,
        "migration": migration,
        "inventory": inventory,
        "ownership": {
            "catalog": "CORE",
            "transactions": "LIFE",
            "published_artifacts": "DIST",
            "execution_status": "EXO",
        },
    }
