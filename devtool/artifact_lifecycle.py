from __future__ import annotations

"""Canonical delivery artifact, qualification, mutation, and recovery planning models.

LIFE-X02 centralizes delivery semantics without replacing existing overlay/bundle/
snapshot applicators. Raw artifacts compile into these immutable models before an
execution boundary consumes them.
"""

from dataclasses import asdict, dataclass, field
from enum import StrEnum
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


class ArtifactSubtype(StrEnum):
    OVERLAY = "overlay"
    BUNDLE = "bundle"
    SNAPSHOT = "snapshot"


class AssuranceLevel(StrEnum):
    LEGACY = "legacy"
    QUALIFIED = "qualified"
    STRICT = "strict"


class MutationKind(StrEnum):
    ADD = "add"
    MODIFY = "modify"
    DELETE = "delete"
    PRESERVE = "preserve"


class GenerationOwner(StrEnum):
    BASELINE = "baseline"
    PAYLOAD = "payload"
    VALIDATION = "validation"
    REPAIR = "repair"
    FINALIZE = "finalize"
    INTEGRATION = "integration"


_GENERATION_ORDER = {
    GenerationOwner.BASELINE: 0,
    GenerationOwner.PAYLOAD: 1,
    GenerationOwner.VALIDATION: 2,
    GenerationOwner.REPAIR: 3,
    GenerationOwner.FINALIZE: 4,
    GenerationOwner.INTEGRATION: 5,
}


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _sha256_path(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _normal_text(value: Any) -> str:
    return str(value or "").strip()


def _repo_manifest_identity(manifest: Mapping[str, Any]) -> dict[str, Any]:
    raw = manifest.get("repository")
    if isinstance(raw, Mapping):
        return {str(k): v for k, v in raw.items() if v not in (None, "")}
    legacy = _normal_text(manifest.get("repo_name") or manifest.get("repo"))
    return {"name": legacy} if legacy else {}


@dataclass(frozen=True, slots=True)
class ArtifactIdentity:
    semantic_sha256: str
    content_sha256: str
    transport_sha256: str
    trust_identity: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ArtifactEnvelope:
    artifact_id: str
    revision: str
    subtype: ArtifactSubtype
    schema_version: int
    assurance: AssuranceLevel
    name: str
    identities: ArtifactIdentity
    repository: Mapping[str, Any]
    source: Mapping[str, Any]
    manifest_source: str
    manifest: Mapping[str, Any] = field(repr=False)

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["subtype"] = self.subtype.value
        payload["assurance"] = self.assurance.value
        return payload


@dataclass(frozen=True, slots=True)
class QualificationIssue:
    code: str
    message: str
    category: str
    blocking: bool = True

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class UnsafeAuthorization:
    code: str
    reason: str
    provenance: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ArtifactQualificationReceipt:
    receipt_id: str
    artifact_id: str
    artifact_semantic_sha256: str
    repository_identity: Mapping[str, Any]
    base_identity: Mapping[str, Any]
    effective_policy_identity: str
    applicator_capabilities: tuple[str, ...]
    hook_readiness: bool
    validation_executable: bool
    isolation_assurance: str
    rollback_assurance: str
    external_effect_policy: str
    trust_ready: bool
    commit_ready: bool
    blockers: tuple[QualificationIssue, ...]
    warnings: tuple[QualificationIssue, ...]
    unsafe_authorizations: tuple[UnsafeAuthorization, ...]
    trust_evidence_ids: tuple[str, ...] = ()
    trust_evidence: tuple[Mapping[str, Any], ...] = ()

    @property
    def qualified(self) -> bool:
        return not any(item.blocking for item in self.blockers)

    def as_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "artifact_id": self.artifact_id,
            "artifact_semantic_sha256": self.artifact_semantic_sha256,
            "repository_identity": dict(self.repository_identity),
            "base_identity": dict(self.base_identity),
            "effective_policy_identity": self.effective_policy_identity,
            "applicator_capabilities": list(self.applicator_capabilities),
            "hook_readiness": self.hook_readiness,
            "validation_executable": self.validation_executable,
            "isolation_assurance": self.isolation_assurance,
            "rollback_assurance": self.rollback_assurance,
            "external_effect_policy": self.external_effect_policy,
            "trust_ready": self.trust_ready,
            "commit_ready": self.commit_ready,
            "qualified": self.qualified,
            "blockers": [item.as_dict() for item in self.blockers],
            "warnings": [item.as_dict() for item in self.warnings],
            "unsafe_authorizations": [item.as_dict() for item in self.unsafe_authorizations],
            "trust_evidence_ids": list(self.trust_evidence_ids),
            "trust_evidence": [dict(item) for item in self.trust_evidence],
        }


@dataclass(frozen=True, slots=True)
class ValidationExpectation:
    key: str
    outcome: str


@dataclass(frozen=True, slots=True)
class ResolvedValidationContract:
    contract_id: str
    required: bool
    tasks: tuple[str, ...]
    test_refs: tuple[str, ...]
    expected: tuple[ValidationExpectation, ...]
    maximum_warnings: int | None
    write_paths: tuple[str, ...]
    failure_action: str
    allow_deferred: bool
    required_checkpoints: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "required": self.required,
            "tasks": list(self.tasks),
            "test_refs": list(self.test_refs),
            "expected": [asdict(item) for item in self.expected],
            "maximum_warnings": self.maximum_warnings,
            "write_paths": list(self.write_paths),
            "failure_action": self.failure_action,
            "allow_deferred": self.allow_deferred,
            "required_checkpoints": list(self.required_checkpoints),
        }


@dataclass(frozen=True, slots=True)
class ResolvedBundleEntry:
    entry_id: str
    bundle_path: str
    target: str
    label: str
    required: bool
    sha256: str
    size: int | None
    index: int
    validation_tasks: tuple[str, ...] = ()
    validation_tasks_declared: bool = False
    manifest_json: str = "{}"

    @property
    def manifest(self) -> dict[str, Any]:
        value = json.loads(self.manifest_json)
        if not isinstance(value, dict):
            raise ValueError("Resolved bundle entry manifest must be an object")
        return value

    @property
    def source_owner(self) -> str:
        return self.entry_id or self.bundle_path

    def as_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "bundle_path": self.bundle_path,
            "target": self.target,
            "label": self.label,
            "required": self.required,
            "sha256": self.sha256,
            "size": self.size,
            "index": self.index,
            "validation_tasks": list(self.validation_tasks),
            "validation_tasks_declared": self.validation_tasks_declared,
            "manifest": self.manifest,
        }


@dataclass(frozen=True, slots=True)
class Mutation:
    path: str
    kind: MutationKind
    source_sha256: str | None = None
    expected_sha256: str | None = None
    source_identity: str = ""
    order: int = 0

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["kind"] = self.kind.value
        return payload


@dataclass(frozen=True, slots=True)
class MutationSet:
    mutations: tuple[Mutation, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"mutations": [item.as_dict() for item in self.mutations]}


@dataclass(frozen=True, slots=True)
class ExpectedPath:
    path: str
    state: str
    sha256: str | None


@dataclass(frozen=True, slots=True)
class ExpectedFinalTree:
    entries: tuple[ExpectedPath, ...]
    exact: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"exact": self.exact, "entries": [asdict(item) for item in self.entries]}


@dataclass(frozen=True, slots=True)
class WriteLease:
    path: str
    owner: str
    generations: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class PathGeneration:
    path: str
    generation: int
    owner: GenerationOwner
    sha256: str | None

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["owner"] = self.owner.value
        return payload


@dataclass(slots=True)
class LifecycleGenerationLedger:
    """In-memory G0..Gn ownership ledger persisted by callers as transaction evidence."""

    paths: dict[str, PathGeneration] = field(default_factory=dict)

    def baseline(self, path: str, sha256: str | None) -> PathGeneration:
        record = PathGeneration(path, 0, GenerationOwner.BASELINE, sha256)
        self.paths[path] = record
        return record

    def advance(self, path: str, owner: GenerationOwner, sha256: str | None) -> PathGeneration:
        current = self.paths.get(path)
        if current is None:
            raise ValueError(f"No G0 baseline is registered for {path!r}.")
        target = _GENERATION_ORDER[owner]
        if target <= current.generation:
            if target == current.generation and current.owner == owner and current.sha256 == sha256:
                return current
            raise ValueError(f"Generation for {path!r} cannot move backward from G{current.generation} to G{target}.")
        record = PathGeneration(path, target, owner, sha256)
        self.paths[path] = record
        return record

    def as_dict(self) -> dict[str, Any]:
        return {path: value.as_dict() for path, value in sorted(self.paths.items())}


@dataclass(frozen=True, slots=True)
class ExternalEffect:
    effect_id: str
    kind: str
    target: str
    compensator: str = ""
    reversible: bool = False
    evidence: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExternalEffectLedger:
    effects: tuple[ExternalEffect, ...] = ()

    @property
    def fully_compensatable(self) -> bool:
        return all(item.reversible and bool(item.compensator) for item in self.effects)

    def as_dict(self) -> dict[str, Any]:
        return {"effects": [asdict(item) for item in self.effects], "fully_compensatable": self.fully_compensatable}


@dataclass(frozen=True, slots=True)
class ArtifactExecutionRecord:
    record_id: str
    run_id: str
    artifact_id: str
    plan_id: str
    qualification_receipt_id: str
    validation_evidence_id: str = ""
    transaction_id: str = ""
    rollback_receipt_id: str = ""
    trust_evidence_ids: tuple[str, ...] = ()
    final_state_identity: str = ""
    execution_status: str = "applied-pending-validation"
    durability: str = "durable"

    def __post_init__(self) -> None:
        if self.durability not in {"durable", "retained", "ephemeral"}:
            raise ValueError("Artifact execution durability must be durable, retained, or ephemeral.")

    @classmethod
    def create(
        cls,
        *,
        artifact_id: str,
        plan_id: str,
        run_id: str = "",
        qualification_receipt_id: str,
        validation_evidence_id: str = "",
        transaction_id: str = "",
        rollback_receipt_id: str = "",
        trust_evidence_ids: Sequence[str] = (),
        final_state_identity: str = "",
        execution_status: str = "applied-pending-validation",
        durability: str = "durable",
    ) -> "ArtifactExecutionRecord":
        basis = {
            "run_id": run_id, "artifact_id": artifact_id, "plan_id": plan_id,
            "qualification_receipt_id": qualification_receipt_id,
            "validation_evidence_id": validation_evidence_id, "transaction_id": transaction_id,
            "rollback_receipt_id": rollback_receipt_id, "trust_evidence_ids": list(trust_evidence_ids),
            "final_state_identity": final_state_identity, "execution_status": execution_status, "durability": durability,
        }
        return cls(record_id=f"artifact-execution:{_digest(basis)[:24]}", **basis)

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["trust_evidence_ids"] = list(self.trust_evidence_ids)
        return payload


@dataclass(frozen=True, slots=True)
class ResolvedArtifactPlan:
    plan_id: str
    artifact: ArtifactEnvelope
    qualification: ArtifactQualificationReceipt
    mutations: MutationSet
    bundle_entries: tuple[ResolvedBundleEntry, ...]
    expected_final_tree: ExpectedFinalTree
    validation: ResolvedValidationContract
    write_leases: tuple[WriteLease, ...]
    postconditions: tuple[str, ...]
    isolation: str
    transaction_mode: str
    external_effects: ExternalEffectLedger
    remote_requirements: tuple[str, ...]
    unsafe_overrides: tuple[UnsafeAuthorization, ...]

    @property
    def executable(self) -> bool:
        return self.qualification.qualified

    def as_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "artifact": self.artifact.as_dict(),
            "qualification": self.qualification.as_dict(),
            "mutations": self.mutations.as_dict(),
            "bundle_entries": [item.as_dict() for item in self.bundle_entries],
            "expected_final_tree": self.expected_final_tree.as_dict(),
            "validation": self.validation.as_dict(),
            "write_leases": [asdict(item) for item in self.write_leases],
            "postconditions": list(self.postconditions),
            "isolation": self.isolation,
            "transaction_mode": self.transaction_mode,
            "external_effects": self.external_effects.as_dict(),
            "remote_requirements": list(self.remote_requirements),
            "unsafe_overrides": [item.as_dict() for item in self.unsafe_overrides],
            "executable": self.executable,
        }


def _artifact_subtype(manifest: Mapping[str, Any], *, bundle: bool = False) -> ArtifactSubtype:
    raw = _normal_text(manifest.get("type") or manifest.get("kind") or manifest.get("artifact_type")).lower().replace("_", "-")
    if bundle or raw in {"bundle", "overlay-bundle", "devtool.overlay-bundle", "devtool.overlay_bundle"}:
        return ArtifactSubtype.BUNDLE
    if raw in {"snapshot", "devtool.snapshot"} or bool(manifest.get("snapshot")):
        return ArtifactSubtype.SNAPSHOT
    return ArtifactSubtype.OVERLAY


def _assurance(manifest: Mapping[str, Any]) -> AssuranceLevel:
    raw = _normal_text(manifest.get("assurance") or manifest.get("assurance_level")).lower()
    if raw == AssuranceLevel.STRICT.value:
        return AssuranceLevel.STRICT
    if raw in {AssuranceLevel.QUALIFIED.value, "modern"}:
        return AssuranceLevel.QUALIFIED
    # Schema number alone does not imply strict assurance.
    return AssuranceLevel.LEGACY


def _trust_identity(manifest: Mapping[str, Any]) -> str:
    trust = manifest.get("trust") if isinstance(manifest.get("trust"), Mapping) else {}
    signing = manifest.get("signing") if isinstance(manifest.get("signing"), Mapping) else {}
    return _normal_text(trust.get("identity_id") or signing.get("identity_id"))


def _content_identity(manifest: Mapping[str, Any]) -> str:
    inventory = manifest.get("inventory")
    entries: Sequence[Any]
    if isinstance(inventory, Mapping) and isinstance(inventory.get("entries"), list):
        entries = inventory["entries"]
    else:
        entries = []
    normalized = []
    for index, raw in enumerate(entries):
        if not isinstance(raw, Mapping):
            continue
        normalized.append({
            "path": _normal_text(raw.get("path")),
            "action": _normal_text(raw.get("action") or raw.get("kind") or "payload").lower(),
            "sha256": _normal_text(raw.get("sha256")) or None,
            "source_sha256": _normal_text(raw.get("source_sha256")) or None,
            "order": index,
        })
    return _digest(normalized)


def _semantic_identity(manifest: Mapping[str, Any], subtype: ArtifactSubtype, content_sha256: str) -> str:
    transport_keys = {"transport", "download", "archive_path", "source_path", "signature_sidecar"}
    normalized = {k: v for k, v in manifest.items() if k not in transport_keys}
    normalized["subtype"] = subtype.value
    normalized["content_sha256"] = content_sha256
    return _digest(normalized)


def _validation_contract(manifest: Mapping[str, Any]) -> ResolvedValidationContract:
    raw = manifest.get("validation") if isinstance(manifest.get("validation"), Mapping) else {}
    tasks_raw = raw.get("tasks") or raw.get("gradle_tasks") or raw.get("test_tasks") or raw.get("task") or []
    if isinstance(tasks_raw, str):
        tasks = tuple(dict.fromkeys(x.strip() for x in tasks_raw.split(",") if x.strip()))
    elif isinstance(tasks_raw, Sequence):
        tasks = tuple(dict.fromkeys(str(x).strip() for x in tasks_raw if str(x).strip()))
    else:
        tasks = ()
    tests = raw.get("tests") if isinstance(raw.get("tests"), list) else []
    refs = tuple(
        dict.fromkeys(
            _normal_text(item.get("ref"))
            for item in tests
            if isinstance(item, Mapping) and _normal_text(item.get("ref"))
        )
    )
    expected_raw = raw.get("expected") if isinstance(raw.get("expected"), Mapping) else {}
    expected = tuple(ValidationExpectation(str(k), str(v)) for k, v in sorted(expected_raw.items()))
    writes = raw.get("write_paths") if isinstance(raw.get("write_paths"), list) else []
    checkpoints = raw.get("required_checkpoints") if isinstance(raw.get("required_checkpoints"), list) else []
    required = bool(raw.get("required") or str(raw.get("mode") or "").lower() == "required")
    maximum_warnings = raw.get("maximum_warnings")
    if maximum_warnings is not None:
        maximum_warnings = int(maximum_warnings)
    payload = {
        "required": required,
        "tasks": tasks,
        "refs": refs,
        "expected": [asdict(item) for item in expected],
        "maximum_warnings": maximum_warnings,
        "write_paths": list(writes),
        "failure_action": _normal_text(raw.get("failure_action") or "rollback"),
        "allow_deferred": bool(raw.get("allow_deferred", False)),
        "required_checkpoints": list(checkpoints),
    }
    return ResolvedValidationContract(
        contract_id=f"validation:{_digest(payload)[:24]}",
        required=required,
        tasks=tasks,
        test_refs=refs,
        expected=expected,
        maximum_warnings=maximum_warnings,
        write_paths=tuple(str(x) for x in writes),
        failure_action=payload["failure_action"],
        allow_deferred=payload["allow_deferred"],
        required_checkpoints=tuple(str(x) for x in checkpoints),
    )



def _compile_bundle_execution(artifact_path: Path) -> tuple[MutationSet, tuple[ResolvedBundleEntry, ...]]:
    """Compile bundle execution order and nested semantics into the canonical plan.

    The applicator may later read archive bytes, but it must not reparse bundle or
    nested manifests to decide mutation/config/hook semantics after this point.
    """
    from .artifacts import materialize_overlay_bundle_entries, read_artifact_manifest

    sequence: list[Mutation] = []
    resolved_entries: list[ResolvedBundleEntry] = []
    with tempfile.TemporaryDirectory(prefix="devtool-life-x02-bundle-") as tmp:
        _bundle_manifest, entries = materialize_overlay_bundle_entries(artifact_path, tmp)
        for entry in entries:
            nested_manifest = read_artifact_manifest(entry.path)
            nested_subtype = _artifact_subtype(nested_manifest, bundle=False)
            nested = _compile_mutations(nested_manifest, nested_subtype, artifact_path=entry.path)
            source_owner = entry.id or entry.bundle_path
            for nested_order, mutation in enumerate(nested.mutations):
                sequence.append(Mutation(
                    path=mutation.path,
                    kind=mutation.kind,
                    source_sha256=mutation.source_sha256,
                    expected_sha256=mutation.expected_sha256,
                    source_identity=(
                        f"{source_owner}::{mutation.source_identity}"
                        if mutation.source_identity.startswith("devtool:")
                        else source_owner
                    ),
                    order=(entry.index * 100000) + nested_order,
                ))
            resolved_entries.append(ResolvedBundleEntry(
                entry_id=str(entry.id or ""),
                bundle_path=str(entry.bundle_path),
                target=str(entry.target or ""),
                label=str(entry.label or ""),
                required=bool(entry.required),
                sha256=str(entry.sha256 or ""),
                size=entry.size,
                index=int(entry.index),
                validation_tasks=tuple(str(item) for item in (entry.validation_tasks or ())),
                validation_tasks_declared=bool(entry.validation_tasks_declared),
                manifest_json=json.dumps(nested_manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
            ))
    return MutationSet(tuple(sequence)), tuple(resolved_entries)


def _compile_mutations(
    manifest: Mapping[str, Any],
    subtype: ArtifactSubtype,
    *,
    artifact_path: Path | None = None,
) -> MutationSet:
    if subtype == ArtifactSubtype.BUNDLE and artifact_path is not None:
        return _compile_bundle_execution(artifact_path)[0]

    inventory = manifest.get("inventory")
    entries = inventory.get("entries") if isinstance(inventory, Mapping) else None
    raw_entries = entries if isinstance(entries, list) else []
    # Legacy/minimal artifacts (notably snapshots) may not carry an inventory.
    # Canonical execution still needs a complete mutation contract, so compile
    # the actual archive payload into immutable mutation rows here instead of
    # letting the runner rediscover it later.
    if not raw_entries and artifact_path is not None:
        from .artifacts import (
            artifact_inventory_metadata_payload_paths,
            is_skipped_artifact_payload_member,
            list_archive_members,
        )
        allowed_metadata = artifact_inventory_metadata_payload_paths(dict(manifest))
        synthesized: list[dict[str, Any]] = []
        for member in list_archive_members(artifact_path, include_data=True):
            if member.is_dir:
                continue
            # Explicitly permitted metadata payloads are repository payload; all
            # ordinary Devtool metadata remains outside the mutation contract.
            if is_skipped_artifact_payload_member(member.name) and member.name not in allowed_metadata:
                continue
            data = member.data or b""
            synthesized.append({
                "path": member.name,
                "action": "modify",
                "sha256": hashlib.sha256(data).hexdigest(),
                "source": "archive-payload",
            })
        raw_entries = synthesized
    mutations: list[Mutation] = []
    final_by_path: dict[str, int] = {}
    for index, raw in enumerate(raw_entries):
        if not isinstance(raw, Mapping):
            continue
        path = _normal_text(raw.get("path")).replace("\\", "/")
        if not path:
            continue
        action = _normal_text(raw.get("action") or raw.get("kind") or "modify").lower()
        try:
            kind = MutationKind(action)
        except ValueError:
            kind = MutationKind.MODIFY
        item = Mutation(
            path=path,
            kind=kind,
            source_sha256=_normal_text(raw.get("source_sha256")) or None,
            expected_sha256=_normal_text(raw.get("sha256")) or None,
            source_identity=_normal_text(raw.get("source") or raw.get("source_artifact")),
            order=index,
        )
        if path in final_by_path:
            mutations[final_by_path[path]] = item
        else:
            final_by_path[path] = len(mutations)
            mutations.append(item)

    solution = _normal_text(manifest.get("solution"))
    project = _normal_text(manifest.get("run_project") or manifest.get("project"))
    if (solution or project) and ".devtool.toml" not in final_by_path:
        final_by_path[".devtool.toml"] = len(mutations)
        mutations.append(Mutation(
            path=".devtool.toml", kind=MutationKind.MODIFY,
            source_identity="devtool:artifact-config-registration",
            order=len(mutations),
        ))

    if subtype == ArtifactSubtype.OVERLAY and artifact_path is not None:
        # Legacy .devtool-delete is parsed exactly once by the canonical planner
        # and lowered into ordinary DELETE mutations. The runner never reads it.
        from .artifacts import read_delete_manifest
        for value in read_delete_manifest(artifact_path):
            path = _normal_text(value).replace("\\", "/")
            if not path:
                continue
            mutations.append(Mutation(
                path=path, kind=MutationKind.DELETE,
                source_identity="devtool:delete-manifest", order=len(mutations),
            ))

    if subtype == ArtifactSubtype.SNAPSHOT:
        deletes = []
        for key in ("delete", "deleted_paths", "deletions"):
            value = manifest.get(key)
            if isinstance(value, list):
                deletes.extend(value)
        for value in deletes:
            path = _normal_text(value).replace("\\", "/")
            if path and path not in final_by_path:
                final_by_path[path] = len(mutations)
                mutations.append(Mutation(path=path, kind=MutationKind.DELETE, order=len(mutations)))
        preserve = manifest.get("preserve") if isinstance(manifest.get("preserve"), list) else []
        for value in preserve:
            path = _normal_text(value).replace("\\", "/")
            if path and path not in final_by_path:
                mutations.append(Mutation(path=path, kind=MutationKind.PRESERVE, order=len(mutations)))
    return MutationSet(tuple(mutations))


def _expected_tree(mutations: MutationSet, subtype: ArtifactSubtype) -> ExpectedFinalTree:
    # MutationSet preserves execution order (including repeated writes in a
    # bundle). ExpectedFinalTree is instead a final-state projection, therefore
    # only the last authoritative state for each path belongs here.
    final: dict[str, ExpectedPath] = {}
    order: list[str] = []
    for item in mutations.mutations:
        if item.kind == MutationKind.DELETE:
            expected = ExpectedPath(item.path, "missing", None)
        elif item.kind == MutationKind.PRESERVE:
            expected = ExpectedPath(item.path, "preserve", None)
        else:
            expected = ExpectedPath(item.path, "present", item.expected_sha256)
        if item.path not in final:
            order.append(item.path)
        final[item.path] = expected
    return ExpectedFinalTree(tuple(final[path] for path in order), exact=subtype == ArtifactSubtype.SNAPSHOT)


class ArtifactEnvelopeResolver:
    """Compile raw delivery input into one canonical envelope/qualification/plan."""

    def resolve(self, artifact: str | Path, *, repo_root: str | Path | None = None) -> ArtifactEnvelope:
        from .artifacts import is_overlay_bundle, read_artifact_manifest, resolve_artifact_manifest, resolve_artifact_path

        path = resolve_artifact_path(artifact)
        resolution = resolve_artifact_manifest(path)
        manifest = read_artifact_manifest(path)
        subtype = _artifact_subtype(manifest, bundle=is_overlay_bundle(path))
        content_sha256 = _content_identity(manifest)
        semantic_sha256 = _semantic_identity(manifest, subtype, content_sha256)
        transport_sha256 = _sha256_path(path)
        revision = _normal_text(manifest.get("revision") or manifest.get("version") or manifest.get("schema_version") or "1")
        artifact_id = _normal_text(manifest.get("artifact_id")) or f"artifact:{semantic_sha256[:24]}"
        source = {
            "path": str(path),
            "base_commit": _normal_text(manifest.get("base_commit")),
            "base_tree": _normal_text(manifest.get("base_tree")),
        }
        return ArtifactEnvelope(
            artifact_id=artifact_id,
            revision=revision,
            subtype=subtype,
            schema_version=int(manifest.get("schema_version") or 1),
            assurance=_assurance(manifest),
            name=_normal_text(manifest.get("name") or path.stem),
            identities=ArtifactIdentity(semantic_sha256, content_sha256, transport_sha256, _trust_identity(manifest)),
            repository=_repo_manifest_identity(manifest),
            source=source,
            manifest_source=resolution.source,
            manifest=manifest,
        )

    def qualify(
        self,
        envelope: ArtifactEnvelope,
        *,
        repo_root: str | Path,
        public_runner: bool = True,
        isolation: str = "worktree",
        unsafe_authorizations: Iterable[UnsafeAuthorization] = (),
        ignore_checksum: bool = False,
    ) -> ArtifactQualificationReceipt:
        from .artifacts import validate_artifact, validate_overlay_bundle
        from .repository_identity import build_repository_identity

        repo = Path(repo_root).expanduser().resolve()
        blockers: list[QualificationIssue] = []
        warnings: list[QualificationIssue] = []
        authorizations = tuple(unsafe_authorizations)
        authorized = {item.code for item in authorizations}
        artifact_path = Path(str(envelope.source.get("path") or ""))
        validation = (
            validate_overlay_bundle(repo, artifact_path, ignore_checksum=ignore_checksum)
            if envelope.subtype == ArtifactSubtype.BUNDLE
            else validate_artifact(
                repo, artifact_path, artifact_type=envelope.subtype.value,
                ignore_checksum=ignore_checksum,
            )
        )
        for message in validation.messages:
            issue = QualificationIssue("artifact-validation", str(message), "artifact", not bool(validation.ok))
            (blockers if issue.blocking else warnings).append(issue)
        if public_runner and envelope.assurance == AssuranceLevel.LEGACY and "allow-legacy-assurance" not in authorized:
            blockers.append(QualificationIssue(
                "legacy-assurance-public-runner",
                "Public runners require qualified/strict assurance or an explicit typed unsafe authorization.",
                "assurance",
                True,
            ))
        repository_identity = build_repository_identity(repo).as_dict()
        declared_repo = dict(envelope.repository)
        declared_name = _normal_text(declared_repo.get("name"))
        if declared_name and declared_name != repo.name:
            blockers.append(QualificationIssue("repository-name-mismatch", f"Artifact targets repository {declared_name!r}, not {repo.name!r}.", "repository", True))
        validation_contract = _validation_contract(envelope.manifest)
        raw_validation = envelope.manifest.get("validation") if isinstance(envelope.manifest.get("validation"), Mapping) else {}
        validation_script = raw_validation.get("script")
        validation_executable = bool(
            validation_contract.tasks
            or validation_contract.test_refs
            or validation_script
            or not validation_contract.required
        )
        if validation_contract.required and not validation_executable:
            blockers.append(QualificationIssue(
                "required-validation-unresolved",
                "Required artifact validation has no executable task/ref/script contract.",
                "validation", True,
            ))
        hooks = [envelope.manifest.get(name) for name in ("preflight", "validation", "finalize")]
        hook_readiness = all(not isinstance(item, Mapping) or bool(item) for item in hooks)
        trust = envelope.manifest.get("trust") if isinstance(envelope.manifest.get("trust"), Mapping) else {}
        trust_evidence_ids: list[str] = []
        trust_evidence: list[Mapping[str, Any]] = []
        try:
            from .artifact_trust import (
                SigningIdentityRegistry, assert_certificate_continuity,
                effective_artifact_trust_policy, trust_policy_allows_assurance,
                verify_artifact_signature,
            )
            effective_trust = effective_artifact_trust_policy(repo, trust)
            if not trust_policy_allows_assurance(effective_trust, envelope.assurance.value):
                raise ValueError(
                    f"artifact assurance {envelope.assurance.value!r} is below required "
                    f"{effective_trust.minimum_assurance!r}"
                )
            identity_id = _normal_text(trust.get("identity_id") or envelope.identities.trust_identity)
            identity = None
            if effective_trust.require_known_identity or effective_trust.require_signature or identity_id:
                if not identity_id:
                    raise ValueError("effective trust policy requires a registered signing identity")
                identity = SigningIdentityRegistry().get(identity_id)
                if identity is None:
                    raise ValueError(f"signing identity {identity_id!r} is not registered")
                if effective_trust.allowed_identity_ids and identity_id not in effective_trust.allowed_identity_ids:
                    raise ValueError(f"signing identity {identity_id!r} is not allowed by effective trust policy")
                expected_cert = _normal_text(trust.get("certificate_sha256"))
                if expected_cert:
                    assert_certificate_continuity(identity, expected_cert, allow_change=False)
            if effective_trust.require_signature:
                if identity is None:
                    raise ValueError("effective trust policy requires signature verification but no identity resolved")
                sidecar_raw = _normal_text(trust.get("signature_sidecar"))
                sidecar = Path(sidecar_raw).expanduser() if sidecar_raw else artifact_path.with_name(artifact_path.name + ".sig")
                if not sidecar.is_absolute():
                    sidecar = artifact_path.parent / sidecar
                try:
                    signature = sidecar.read_text(encoding="utf-8").strip()
                except OSError as exc:
                    raise ValueError(f"detached signature sidecar is unavailable: {sidecar}: {exc}") from exc
                evidence = verify_artifact_signature(identity, artifact_path, signature)
                trust_evidence_ids.append(evidence.evidence_id)
                trust_evidence.append(evidence.as_dict())
            trust_ready = True
        except Exception as exc:
            trust_ready = False
            blockers.append(QualificationIssue("trust-verification-failed", str(exc), "trust", True))
        apply = envelope.manifest.get("apply") if isinstance(envelope.manifest.get("apply"), Mapping) else {}
        commit = apply.get("commit") if isinstance(apply.get("commit"), Mapping) else {}
        commit_ready = not bool(commit.get("enabled")) or bool(_normal_text(commit.get("message")))
        if bool(commit.get("enabled")) and not commit_ready:
            blockers.append(QualificationIssue("commit-message-missing", "Artifact commit is enabled but has no commit message.", "commit", True))
        policy_basis = {
            "assurance": envelope.assurance.value,
            "validation": validation_contract.as_dict(),
            "trust": trust,
            "effective_trust": (effective_trust.as_dict() if "effective_trust" in locals() else {}),
            "isolation": isolation,
            "unsafe": [item.as_dict() for item in authorizations],
            "trust_evidence_ids": list(trust_evidence_ids),
        }
        base_identity = {k: envelope.source.get(k) for k in ("base_commit", "base_tree") if envelope.source.get(k)}
        receipt_payload = {
            "artifact": envelope.identities.semantic_sha256,
            "repository": repository_identity.get("fingerprint"),
            "policy": _digest(policy_basis),
            "blockers": [item.as_dict() for item in blockers],
            "warnings": [item.as_dict() for item in warnings],
            "unsafe": [item.as_dict() for item in authorizations],
        }
        return ArtifactQualificationReceipt(
            receipt_id=f"qualification:{_digest(receipt_payload)[:24]}",
            artifact_id=envelope.artifact_id,
            artifact_semantic_sha256=envelope.identities.semantic_sha256,
            repository_identity=repository_identity,
            base_identity=base_identity,
            effective_policy_identity=_digest(policy_basis),
            applicator_capabilities=("atomic-apply", "rollback", "hook-runtime", "validation-contract"),
            hook_readiness=hook_readiness,
            validation_executable=validation_executable,
            isolation_assurance=isolation,
            rollback_assurance="transactional" if isolation in {"worktree", "direct"} else "reduced",
            external_effect_policy="record-and-compensate",
            trust_ready=trust_ready,
            commit_ready=commit_ready,
            blockers=tuple(blockers),
            warnings=tuple(warnings),
            unsafe_authorizations=authorizations,
            trust_evidence_ids=tuple(trust_evidence_ids),
            trust_evidence=tuple(dict(item) for item in trust_evidence),
        )

    def plan(
        self,
        envelope: ArtifactEnvelope,
        qualification: ArtifactQualificationReceipt,
        *,
        isolation: str = "worktree",
    ) -> ResolvedArtifactPlan:
        artifact_path = Path(str(envelope.source.get("path") or ""))
        if envelope.subtype == ArtifactSubtype.BUNDLE:
            mutations, bundle_entries = _compile_bundle_execution(artifact_path)
        else:
            mutations = _compile_mutations(envelope.manifest, envelope.subtype, artifact_path=artifact_path)
            bundle_entries = ()
        validation = _validation_contract(envelope.manifest)
        from .artifacts import artifact_finalize_write_paths, artifact_preflight_write_paths, artifact_validation_write_paths
        lease_rows: list[WriteLease] = []
        seen_leases: set[str] = set()
        for item in mutations.mutations:
            if item.kind == MutationKind.PRESERVE or item.path in seen_leases:
                continue
            seen_leases.add(item.path)
            lease_rows.append(WriteLease(item.path, "artifact", (1, 2, 3, 4, 5)))
        for owner, values in (
            ("preflight", artifact_preflight_write_paths(dict(envelope.manifest))),
            ("validation", artifact_validation_write_paths(dict(envelope.manifest))),
            ("finalize", artifact_finalize_write_paths(dict(envelope.manifest))),
        ):
            for raw in values:
                path = _normal_text(raw).replace("\\", "/")
                if not path or path in seen_leases:
                    continue
                seen_leases.add(path)
                lease_rows.append(WriteLease(path, owner, (1, 2, 3, 4, 5)))
        leases = tuple(lease_rows)
        external_raw = envelope.manifest.get("external_effects") if isinstance(envelope.manifest.get("external_effects"), list) else []
        effects: list[ExternalEffect] = []
        for index, item in enumerate(external_raw):
            if not isinstance(item, Mapping):
                continue
            effects.append(ExternalEffect(
                effect_id=_normal_text(item.get("id")) or f"effect-{index+1}",
                kind=_normal_text(item.get("kind") or "external"),
                target=_normal_text(item.get("target")),
                compensator=_normal_text(item.get("compensator")),
                reversible=bool(item.get("reversible")),
                evidence={str(k): v for k, v in item.items() if str(k) not in {"secret", "password", "token"}},
            ))
        postconditions = tuple(
            _normal_text(value)
            for value in (envelope.manifest.get("postconditions") if isinstance(envelope.manifest.get("postconditions"), list) else [])
            if _normal_text(value)
        )
        remote = envelope.manifest.get("remote") if isinstance(envelope.manifest.get("remote"), Mapping) else {}
        remote_requirements = tuple(
            _normal_text(value)
            for value in (remote.get("requirements") if isinstance(remote.get("requirements"), list) else [])
            if _normal_text(value)
        )
        unsafe = qualification.unsafe_authorizations
        plan_basis = {
            "artifact": envelope.identities.semantic_sha256,
            "qualification": qualification.receipt_id,
            "mutations": mutations.as_dict(),
            "bundle_entries": [item.as_dict() for item in bundle_entries],
            "validation": validation.as_dict(),
            "isolation": isolation,
            "external_effects": [asdict(item) for item in effects],
        }
        return ResolvedArtifactPlan(
            plan_id=f"artifact-plan:{_digest(plan_basis)[:24]}",
            artifact=envelope,
            qualification=qualification,
            mutations=mutations,
            bundle_entries=bundle_entries,
            expected_final_tree=_expected_tree(mutations, envelope.subtype),
            validation=validation,
            write_leases=leases,
            postconditions=postconditions,
            isolation=isolation,
            transaction_mode="atomic" if bool((envelope.manifest.get("apply") if isinstance(envelope.manifest.get("apply"), Mapping) else {}).get("atomic", True)) else "best-effort",
            external_effects=ExternalEffectLedger(tuple(effects)),
            remote_requirements=remote_requirements,
            unsafe_overrides=unsafe,
        )

    def resolve_qualified_plan(
        self,
        artifact: str | Path,
        *,
        repo_root: str | Path,
        public_runner: bool = True,
        isolation: str = "worktree",
        unsafe_authorizations: Iterable[UnsafeAuthorization] = (),
        ignore_checksum: bool = False,
    ) -> ResolvedArtifactPlan:
        envelope = self.resolve(artifact, repo_root=repo_root)
        qualification = self.qualify(
            envelope,
            repo_root=repo_root,
            public_runner=public_runner,
            isolation=isolation,
            unsafe_authorizations=unsafe_authorizations,
            ignore_checksum=ignore_checksum,
        )
        return self.plan(envelope, qualification, isolation=isolation)


__all__ = [
    "ArtifactEnvelope",
    "ArtifactEnvelopeResolver",
    "ArtifactExecutionRecord",
    "ArtifactIdentity",
    "ArtifactQualificationReceipt",
    "ArtifactSubtype",
    "AssuranceLevel",
    "ExternalEffect",
    "ExternalEffectLedger",
    "GenerationOwner",
    "LifecycleGenerationLedger",
    "Mutation",
    "MutationKind",
    "MutationSet",
    "QualificationIssue",
    "ResolvedArtifactPlan",
    "ResolvedBundleEntry",
    "ResolvedValidationContract",
    "UnsafeAuthorization",
    "WriteLease",
]
