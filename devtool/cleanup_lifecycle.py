from __future__ import annotations

"""Canonical generated-output ownership and cleanup planning (LIFE-X03).

This module deliberately separates *discovery* from deletion authority.  A name,
suffix, ignore rule, or age can make a resource interesting, but automatic
cleanup requires reconstructable producer ownership plus a fresh protection view.
The durable authorities remain RUN, artifact registry, RER/execution metadata,
transactions, validation, seals and provider-specific owners; LIFE only projects
those facts into a cleanup decision.
"""

from dataclasses import asdict, dataclass, field
from enum import StrEnum
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any, Callable, Iterable, Mapping, Sequence

from .repository_identity import build_repository_identity


class CleanupScope(StrEnum):
    PROJECT = "project"
    REPOSITORY_CACHE = "repository-cache"
    DURABLE_HISTORY = "durable-history"
    TOOLCHAIN_GLOBAL = "toolchain-global"
    USER_GLOBAL = "user-global"
    REMOTE = "remote"


class CleanupSafetyClass(StrEnum):
    DISPOSABLE = "disposable"
    REBUILDABLE = "rebuildable"
    DURABLE_EVIDENCE = "durable-evidence"
    RECOVERY_CRITICAL = "recovery-critical"
    USER_GLOBAL = "user-global"
    OPAQUE_EXTERNAL = "opaque-external"
    PROTECTED_UNKNOWN = "protected-unknown"


class CleanupActionKind(StrEnum):
    DELETE = "delete"
    ARCHIVE = "archive"
    PROVIDER = "provider"


class CleanupAuthorityUnavailableError(RuntimeError):
    """Raised when cleanup cannot read a required protection authority.

    Destructive cleanup is only safe when RUN/RER, artifact registry, transaction,
    and validation protection roots can be observed.  Missing stores are valid for
    fresh repositories; unreadable/corrupt stores are not.
    """



def _stable(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_stable(value).encode("utf-8", "surrogatepass")).hexdigest()


def _sha256_path(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _directory_digest(path: Path) -> str:
    rows: list[dict[str, Any]] = []
    for current, dirs, files in os.walk(path, followlinks=False):
        root = Path(current)
        dirs[:] = sorted(dirs)
        for name in sorted(files):
            item = root / name
            try:
                stat = item.lstat()
                rel = item.relative_to(path).as_posix()
                rows.append({
                    "path": rel,
                    "size": int(stat.st_size),
                    "mtime_ns": int(stat.st_mtime_ns),
                    "sha256": _sha256_path(item) if item.is_file() and not item.is_symlink() else "",
                    "symlink": os.readlink(item) if item.is_symlink() else "",
                })
            except OSError:
                rows.append({"path": str(item), "error": "unreadable"})
    return _digest(rows)


def _path_snapshot(path: Path) -> dict[str, Any]:
    try:
        stat = path.lstat()
    except OSError:
        return {"exists": False, "kind": "missing", "size": 0, "mtime_ns": 0, "sha256": "", "tree_sha256": ""}
    kind = "symlink" if path.is_symlink() else "directory" if path.is_dir() else "file"
    digest = _sha256_path(path) if kind == "file" else ""
    tree_digest = _directory_digest(path) if kind == "directory" else ""
    return {
        "exists": True,
        "kind": kind,
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
        "sha256": digest,
        "tree_sha256": tree_digest,
    }


def _tree_bytes(path: Path) -> int:
    if not path.exists() and not path.is_symlink():
        return 0
    if path.is_file() or path.is_symlink():
        try:
            return int(path.lstat().st_size)
        except OSError:
            return 0
    total = 0
    for current, dirs, files in os.walk(path, followlinks=False):
        root = Path(current)
        for name in files:
            try:
                total += int((root / name).lstat().st_size)
            except OSError:
                pass
        # Directory metadata is not reclaimable payload; symlinked dirs are.
        for name in list(dirs):
            candidate = root / name
            if candidate.is_symlink():
                try:
                    total += int(candidate.lstat().st_size)
                except OSError:
                    pass
    return total


def _filesystem_identity(path: Path) -> str:
    probe = path if path.exists() else path.parent
    try:
        stat = probe.stat()
        return f"dev:{stat.st_dev}"
    except OSError:
        return "unknown"


def _canonical_path(path: str | Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _git_state(repo_root: Path, path: Path) -> str:
    try:
        rel = path.resolve(strict=False).relative_to(repo_root.resolve(strict=False)).as_posix()
    except ValueError:
        return "outside-repository"
    try:
        inside = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"], cwd=repo_root,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
    except OSError:
        return "unknown"
    if inside.returncode != 0:
        return "not-git"
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", rel], cwd=repo_root,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    )
    if tracked.returncode == 0:
        return "tracked"
    if path.is_dir():
        descendants = subprocess.run(
            ["git", "ls-files", "-z", "--", rel.rstrip("/") + "/"], cwd=repo_root,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
        )
        if descendants.returncode == 0 and descendants.stdout:
            return "contains-tracked"
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", "--", rel], cwd=repo_root,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    )
    return "ignored" if ignored.returncode == 0 else "untracked"


@dataclass(frozen=True, slots=True)
class GeneratedOutputDescriptor:
    resource_id: str
    path: str
    owner: str
    provider: str
    category: str
    producer_id: str
    lifecycle: str
    rebuild_cost: str
    scope: CleanupScope = CleanupScope.PROJECT
    destination: str = "local"
    network_cost: str = "none"
    provenance: Mapping[str, Any] = field(default_factory=dict)
    managed: bool = True
    published: bool = False
    tracked_deletion_authorized: bool = False

    @classmethod
    def create(
        cls,
        path: str | Path,
        *,
        owner: str,
        provider: str,
        category: str,
        producer_id: str,
        lifecycle: str = "rebuildable",
        rebuild_cost: str = "low",
        scope: CleanupScope = CleanupScope.PROJECT,
        destination: str = "local",
        network_cost: str = "none",
        provenance: Mapping[str, Any] | None = None,
        managed: bool = True,
        published: bool = False,
        tracked_deletion_authorized: bool = False,
    ) -> "GeneratedOutputDescriptor":
        canonical = str(_canonical_path(path))
        identity = _digest({
            "path": canonical,
            "owner": owner,
            "provider": provider,
            "category": category,
            "producer_id": producer_id,
            "scope": scope.value,
            "destination": destination,
        })[:32]
        return cls(
            resource_id=f"generated:{identity}", path=canonical, owner=owner, provider=provider,
            category=category, producer_id=producer_id, lifecycle=lifecycle,
            rebuild_cost=rebuild_cost, scope=scope, destination=destination,
            network_cost=network_cost, provenance=dict(provenance or {}), managed=managed,
            published=published, tracked_deletion_authorized=tracked_deletion_authorized,
        )

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["scope"] = self.scope.value
        return data


@dataclass(frozen=True, slots=True)
class CleanupReference:
    reference_id: str
    source: str
    resource_id: str = ""
    path: str = ""
    state: str = "active"
    protective: bool = True
    reason: str = "referenced"
    destination: str = "local"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CleanupOwnershipView:
    repository_id: str
    repository_root: str
    policy_generation: str
    descriptors: tuple[GeneratedOutputDescriptor, ...]
    references: tuple[CleanupReference, ...]
    global_blockers: tuple[str, ...] = ()
    authority_sources: tuple[str, ...] = ()

    def references_for(self, descriptor: GeneratedOutputDescriptor) -> tuple[CleanupReference, ...]:
        target = _canonical_path(descriptor.path)
        rows: list[CleanupReference] = []
        for ref in self.references:
            if ref.resource_id and ref.resource_id == descriptor.resource_id:
                rows.append(ref)
                continue
            if not ref.path:
                continue
            candidate = _canonical_path(ref.path)
            if candidate == target:
                rows.append(ref)
                continue
            try:
                target.relative_to(candidate)
            except ValueError:
                try:
                    candidate.relative_to(target)
                except ValueError:
                    pass
                else:
                    rows.append(ref)
            else:
                rows.append(ref)
        return tuple(rows)

    def as_dict(self) -> dict[str, Any]:
        return {
            "repository_id": self.repository_id,
            "repository_root": self.repository_root,
            "policy_generation": self.policy_generation,
            "descriptors": [item.as_dict() for item in self.descriptors],
            "references": [item.as_dict() for item in self.references],
            "global_blockers": list(self.global_blockers),
            "authority_sources": list(self.authority_sources),
        }


@dataclass(frozen=True, slots=True)
class CleanupCandidate:
    candidate_id: str
    resource_id: str
    path: str
    owner: str
    provider: str
    category: str
    scope: CleanupScope
    destination: str
    safety_class: CleanupSafetyClass
    git_state: str
    retention_state: str
    reference_ids: tuple[str, ...]
    reclaimable_bytes: int
    rebuild_cost: str
    network_cost: str
    filesystem_id: str
    source_snapshot: Mapping[str, Any]
    selected: bool
    protection_reason: str = ""
    confirmation_required: bool = False

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["scope"] = self.scope.value
        data["safety_class"] = self.safety_class.value
        return data


@dataclass(frozen=True, slots=True)
class OpaqueCleanupAction:
    action_id: str
    provider: str
    argv: tuple[str, ...]
    cwd: str
    scope: CleanupScope
    destination: str
    safety_class: CleanupSafetyClass = CleanupSafetyClass.OPAQUE_EXTERNAL
    confirmation_required: bool = True
    detail: str = "provider-owned cleanup; exact deleted paths are not known before execution"

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["scope"] = self.scope.value
        data["safety_class"] = self.safety_class.value
        data["argv"] = list(self.argv)
        return data


@dataclass(frozen=True, slots=True)
class CleanupPlan:
    plan_id: str
    repository_id: str
    repository_root: str
    policy_generation: str
    scope: CleanupScope
    destination: str
    candidates: tuple[CleanupCandidate, ...]
    opaque_actions: tuple[OpaqueCleanupAction, ...] = ()
    confirmation_required: bool = False
    global_scope_authorized: bool = False

    @property
    def selected(self) -> tuple[CleanupCandidate, ...]:
        return tuple(item for item in self.candidates if item.selected)

    @property
    def protected(self) -> tuple[CleanupCandidate, ...]:
        return tuple(item for item in self.candidates if not item.selected)

    def as_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "repository_id": self.repository_id,
            "repository_root": self.repository_root,
            "policy_generation": self.policy_generation,
            "scope": self.scope.value,
            "destination": self.destination,
            "candidates": [item.as_dict() for item in self.candidates],
            "selected": [item.candidate_id for item in self.selected],
            "protected": [item.candidate_id for item in self.protected],
            "opaque_actions": [item.as_dict() for item in self.opaque_actions],
            "confirmation_required": self.confirmation_required,
            "global_scope_authorized": self.global_scope_authorized,
        }


@dataclass(frozen=True, slots=True)
class CleanupActionResult:
    action_id: str
    resource_id: str
    path: str
    status: str
    reclaimed_bytes: int = 0
    reason: str = ""
    postcondition: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CleanupReceipt:
    receipt_id: str
    plan_id: str
    repository_id: str
    scope: CleanupScope
    destination: str
    status: str
    results: tuple[CleanupActionResult, ...]
    reclaimed_bytes: int
    invalidated_evidence_ids: tuple[str, ...] = ()
    tombstones: tuple[Mapping[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "plan_id": self.plan_id,
            "repository_id": self.repository_id,
            "scope": self.scope.value,
            "destination": self.destination,
            "status": self.status,
            "results": [item.as_dict() for item in self.results],
            "reclaimed_bytes": self.reclaimed_bytes,
            "invalidated_evidence_ids": list(self.invalidated_evidence_ids),
            "tombstones": [dict(item) for item in self.tombstones],
        }


@dataclass(frozen=True, slots=True)
class DestinationCleanupResult:
    status: str
    revalidated: bool
    reclaimed_bytes: int = 0
    detail: str = ""
    postcondition: str = ""

    def __post_init__(self) -> None:
        if self.status not in {"applied", "protected", "partial", "failed"}:
            raise ValueError(f"Unsupported destination cleanup status: {self.status}")
        if self.reclaimed_bytes < 0:
            raise ValueError("reclaimed_bytes must be non-negative")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RepositoryCacheMigrationPlan:
    old_repository_key: str
    new_repository_key: str
    backend: str
    source_paths: tuple[str, ...]
    destination_paths: tuple[str, ...]
    mutates: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def plan_repository_cache_relocation(
    old_repo_root: str | Path,
    new_repo_root: str | Path,
    *,
    backend: str,
    cache_root: str | Path | None = None,
) -> RepositoryCacheMigrationPlan:
    """Describe orphaned repository-cache adoption after checkout relocation.

    The plan is intentionally side-effect free. LIFE-X04 owns the user-facing
    migrate/apply UX; X03 makes the ownership discontinuity explicit instead of
    silently losing the old repository-scoped cache tree.
    """
    from .cache_cleanup import default_cache_root, repository_cache_key

    root = _canonical_path(cache_root or default_cache_root())
    old_key = repository_cache_key(old_repo_root)
    new_key = repository_cache_key(new_repo_root)
    safe_backend = str(backend or "host").strip() or "host"
    families = (
        ("build",),
        ("host-termux", "build"),
        ("python",),
        ("repos",),
    )
    sources: list[str] = []
    destinations: list[str] = []
    for prefix in families:
        source = root.joinpath(*prefix, old_key, safe_backend)
        destination = root.joinpath(*prefix, new_key, safe_backend)
        if source.exists():
            sources.append(str(source))
            destinations.append(str(destination))
    return RepositoryCacheMigrationPlan(
        old_repository_key=old_key, new_repository_key=new_key, backend=safe_backend,
        source_paths=tuple(sources), destination_paths=tuple(destinations), mutates=False,
    )


def persist_cleanup_receipt(
    repo_root: str | Path,
    receipt: CleanupReceipt,
    *,
    run_id: str = "",
) -> Path:
    """Persist non-secret cleanup provenance and optionally attach it to RUN.

    RUN remains the attachment/tombstone authority. LIFE writes its own receipt
    bytes, then asks RUN to reference them rather than inventing a second run
    history database.
    """
    root = _canonical_path(repo_root)
    directory = root / ".devtool" / "private" / "cleanup" / "receipts"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (receipt.receipt_id.replace(":", "-") + ".json")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(receipt.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if os.name == "posix":
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
    os.replace(tmp, path)
    resolved_run = str(run_id or os.environ.get("DEVTOOL_RUN_ID") or "").strip()
    if resolved_run:
        from .core_runtime import private_run_root
        from .run_store import AttachmentDescriptor, RunCatalog
        catalog = RunCatalog.open(private_run_root(root))
        attachment = AttachmentDescriptor.from_file(
            run_id=resolved_run, path=path, kind="cleanup-receipt", privacy="private"
        )
        catalog.add_attachment(attachment)
        catalog.save()
    return path


def _safety_for(descriptor: GeneratedOutputDescriptor, refs: Sequence[CleanupReference]) -> CleanupSafetyClass:
    if descriptor.scope in {CleanupScope.TOOLCHAIN_GLOBAL, CleanupScope.USER_GLOBAL}:
        return CleanupSafetyClass.USER_GLOBAL
    if descriptor.published:
        return CleanupSafetyClass.DURABLE_EVIDENCE
    if any(ref.protective and ref.source in {"transaction", "validation", "recovery"} for ref in refs):
        return CleanupSafetyClass.RECOVERY_CRITICAL
    if any(ref.protective for ref in refs):
        return CleanupSafetyClass.DURABLE_EVIDENCE
    if not descriptor.managed:
        return CleanupSafetyClass.PROTECTED_UNKNOWN
    if descriptor.lifecycle in {"disposable", "transient"}:
        return CleanupSafetyClass.DISPOSABLE
    return CleanupSafetyClass.REBUILDABLE


def compile_cleanup_plan(
    ownership: CleanupOwnershipView,
    *,
    scope: CleanupScope = CleanupScope.PROJECT,
    destination: str = "local",
    include_user_global: bool = False,
    opaque_actions: Iterable[OpaqueCleanupAction] = (),
) -> CleanupPlan:
    root = Path(ownership.repository_root)
    candidates: list[CleanupCandidate] = []
    for descriptor in ownership.descriptors:
        if descriptor.scope != scope or descriptor.destination != destination:
            continue
        path = _canonical_path(descriptor.path)
        refs = ownership.references_for(descriptor)
        protective = tuple(ref for ref in refs if ref.protective)
        git_state = _git_state(root, path)
        safety = _safety_for(descriptor, refs)
        reason = ""
        selected = True
        if ownership.global_blockers:
            selected = False
            reason = ownership.global_blockers[0]
        elif safety in {CleanupSafetyClass.DURABLE_EVIDENCE, CleanupSafetyClass.RECOVERY_CRITICAL, CleanupSafetyClass.PROTECTED_UNKNOWN}:
            selected = False
            reason = protective[0].reason if protective else "resource ownership is not sufficient for automatic deletion"
        elif git_state in {"tracked", "contains-tracked"} and not descriptor.tracked_deletion_authorized:
            selected = False
            reason = "tracked source is protected; managed ownership did not explicitly authorize tracked deletion"
        elif descriptor.published:
            selected = False
            reason = "published artifact is outside ordinary automatic cleanup"
        elif scope in {CleanupScope.TOOLCHAIN_GLOBAL, CleanupScope.USER_GLOBAL} and not include_user_global:
            selected = False
            reason = "global cleanup requires explicit global scope authorization"
        snapshot = _path_snapshot(path)
        reference_ids = tuple(sorted(ref.reference_id for ref in refs if ref.protective))
        retention = "protected" if protective else "eligible"
        candidate_id = "cleanup:" + _digest({
            "resource": descriptor.resource_id,
            "snapshot": snapshot,
            "references": reference_ids,
            "policy": ownership.policy_generation,
            "destination": descriptor.destination,
        })[:32]
        candidates.append(CleanupCandidate(
            candidate_id=candidate_id,
            resource_id=descriptor.resource_id,
            path=str(path), owner=descriptor.owner, provider=descriptor.provider,
            category=descriptor.category, scope=descriptor.scope,
            destination=descriptor.destination, safety_class=safety, git_state=git_state,
            retention_state=retention, reference_ids=reference_ids,
            reclaimable_bytes=_tree_bytes(path), rebuild_cost=descriptor.rebuild_cost,
            network_cost=descriptor.network_cost, filesystem_id=_filesystem_identity(path),
            source_snapshot=snapshot, selected=selected, protection_reason=reason,
            confirmation_required=safety in {CleanupSafetyClass.USER_GLOBAL, CleanupSafetyClass.OPAQUE_EXTERNAL},
        ))
    opaque = tuple(opaque_actions)
    confirmation = any(item.confirmation_required for item in candidates) or any(item.confirmation_required for item in opaque)
    payload = {
        "repository_id": ownership.repository_id,
        "policy_generation": ownership.policy_generation,
        "scope": scope.value,
        "destination": destination,
        "candidates": [item.as_dict() for item in candidates],
        "opaque_actions": [item.as_dict() for item in opaque],
        "global_scope_authorized": bool(include_user_global),
    }
    return CleanupPlan(
        plan_id="cleanup-plan:" + _digest(payload)[:32], repository_id=ownership.repository_id,
        repository_root=ownership.repository_root, policy_generation=ownership.policy_generation,
        scope=scope, destination=destination, candidates=tuple(candidates), opaque_actions=opaque,
        confirmation_required=confirmation, global_scope_authorized=bool(include_user_global),
    )


def _same_snapshot(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return all(left.get(key) == right.get(key) for key in ("exists", "kind", "size", "mtime_ns", "sha256", "tree_sha256"))



def _read_existing_json_authority(path: Path, *, label: str) -> Mapping[str, Any]:
    """Read an existing lifecycle authority JSON file without mutating it."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CleanupAuthorityUnavailableError(
            f"Cannot read {label} cleanup authority {path}: {type(exc).__name__}: {exc}"
        ) from exc
    if not isinstance(data, Mapping):
        raise CleanupAuthorityUnavailableError(
            f"Cannot read {label} cleanup authority {path}: expected JSON object."
        )
    return data


def _verify_active_transaction_authority_markers(repo_root: Path) -> None:
    """Fail closed on corrupt active transaction pointers before cleanup mutates.

    transactions.find_active_transactions() intentionally prunes malformed legacy
    markers for ordinary rollback/recovery UX.  Destructive cleanup has a stricter
    contract: an existing active transaction pointer is itself cleanup-critical
    authority and must be readable, point at an existing transaction record, and
    resolve to a readable transaction before any deletion can proceed.
    """
    try:
        from .transactions import transaction_root, read_transaction
    except Exception as exc:
        raise CleanupAuthorityUnavailableError(
            f"Cannot load transaction cleanup authority helpers: {type(exc).__name__}: {exc}"
        ) from exc

    root = transaction_root(repo_root)
    markers: list[Path] = []
    legacy = root / "active.json"
    if legacy.exists():
        markers.append(legacy)
    active_dir = root / "active"
    if active_dir.exists():
        try:
            markers.extend(sorted(active_dir.glob("*.json")))
        except OSError as exc:
            raise CleanupAuthorityUnavailableError(
                f"Cannot enumerate transaction cleanup authority {active_dir}: {type(exc).__name__}: {exc}"
            ) from exc

    for marker in markers:
        data = _read_existing_json_authority(marker, label="transaction")
        transaction_path = Path(str(data.get("path") or ""))
        if not transaction_path.is_absolute():
            raise CleanupAuthorityUnavailableError(
                f"Cannot read transaction cleanup authority {marker}: missing absolute transaction path."
            )
        if not transaction_path.exists():
            raise CleanupAuthorityUnavailableError(
                f"Cannot read transaction cleanup authority {marker}: transaction path is missing: {transaction_path}"
            )
        try:
            tx = read_transaction(marker)
        except Exception as exc:
            raise CleanupAuthorityUnavailableError(
                f"Cannot read transaction cleanup authority {marker}: {type(exc).__name__}: {exc}"
            ) from exc
        if not isinstance(tx, Mapping):
            raise CleanupAuthorityUnavailableError(
                f"Cannot read transaction cleanup authority {marker}: expected transaction JSON object."
            )

def refresh_cleanup_ownership_view(ownership: CleanupOwnershipView, *, strict_authorities: bool = False) -> CleanupOwnershipView:
    """Re-read live lifecycle protection roots immediately before mutation.

    Caller-supplied descriptors/references remain conservative inputs, while RUN,
    artifact, transaction and validation authorities are re-projected from their
    canonical stores.  A newly-active protection root therefore wins even when
    the original plan was compiled before that reference existed.
    """
    return build_cleanup_ownership_view(
        ownership.repository_root,
        ownership.descriptors,
        policy_generation=ownership.policy_generation,
        references=ownership.references,
        strict_authorities=strict_authorities,
    )


def preserve_primary_lifecycle_failure(results: Sequence[Any], cleanup_result: Any) -> Any:
    """Demote a secondary cleanup failure when a primary failure already exists.

    Cleanup evidence remains attached in metadata/output, but it cannot replace
    the failure that caused lifecycle termination.  Successful primary work is
    still allowed to fail closed on cleanup when cleanup itself is required.
    """
    if cleanup_result is None or int(getattr(cleanup_result, "returncode", 0) or 0) == 0:
        return cleanup_result
    primary = next((item for item in results if int(getattr(item, "returncode", 0) or 0) != 0), None)
    if primary is None:
        return cleanup_result
    metadata = dict(getattr(cleanup_result, "metadata", {}) or {})
    metadata.update({
        "secondary_lifecycle_failure": True,
        "secondary_original_returncode": int(getattr(cleanup_result, "returncode", 0) or 0),
        "secondary_original_failure_kind": str(getattr(cleanup_result, "failure_kind", "") or ""),
        "primary_failure_name": str(getattr(primary, "name", "") or ""),
        "primary_failure_kind": str(getattr(primary, "failure_kind", "") or ""),
    })
    cleanup_result.metadata = metadata
    cleanup_result.returncode = 0
    cleanup_result.failure_kind = ""
    cleanup_result.skipped = True
    return cleanup_result


def apply_cleanup_plan(
    plan: CleanupPlan,
    *,
    ownership: CleanupOwnershipView,
    dry_run: bool = False,
    confirm: bool = False,
    provider_executor: Callable[[OpaqueCleanupAction], tuple[bool, str]] | None = None,
    destination_executor: Callable[[CleanupCandidate], DestinationCleanupResult] | None = None,
) -> CleanupReceipt:
    if plan.repository_id != ownership.repository_id:
        raise RuntimeError("Cleanup plan repository identity drifted; re-plan before deletion")
    if plan.policy_generation != ownership.policy_generation:
        raise RuntimeError("Cleanup policy generation drifted; re-plan before deletion")
    if plan.scope in {CleanupScope.TOOLCHAIN_GLOBAL, CleanupScope.USER_GLOBAL} and not plan.global_scope_authorized and not dry_run:
        raise RuntimeError("Global cleanup requires explicit global scope authorization")
    if plan.destination != "local" and plan.candidates and destination_executor is None and not dry_run:
        raise RuntimeError("Remote/provider cleanup requires a destination-local provider executor")
    if plan.confirmation_required and not confirm and not dry_run:
        raise RuntimeError("Cleanup plan requires explicit confirmation")

    live_ownership = refresh_cleanup_ownership_view(ownership, strict_authorities=True)
    if live_ownership.repository_id != plan.repository_id:
        raise RuntimeError("Cleanup repository identity drifted during revalidation")
    refreshed = compile_cleanup_plan(
        live_ownership, scope=plan.scope, destination=plan.destination,
        include_user_global=plan.global_scope_authorized,
        opaque_actions=plan.opaque_actions,
    )
    fresh_by_resource = {item.resource_id: item for item in refreshed.candidates}
    results: list[CleanupActionResult] = []
    tombstones: list[Mapping[str, Any]] = []
    reclaimed = 0
    any_failed = False
    any_partial = False

    for original in plan.candidates:
        fresh = fresh_by_resource.get(original.resource_id)
        if fresh is None:
            results.append(CleanupActionResult(original.candidate_id, original.resource_id, original.path, "protected", reason="ownership disappeared during revalidation"))
            any_partial = True
            continue
        if not original.selected or not fresh.selected:
            reason = fresh.protection_reason or original.protection_reason or "resource is protected"
            results.append(CleanupActionResult(original.candidate_id, original.resource_id, original.path, "protected", reason=reason))
            any_partial = True
            continue

        if plan.destination != "local":
            if dry_run:
                results.append(CleanupActionResult(original.candidate_id, original.resource_id, original.path, "planned", reclaimed_bytes=original.reclaimable_bytes, reason="destination-local revalidation required at apply"))
                continue
            assert destination_executor is not None
            try:
                outcome = destination_executor(original)
            except Exception as exc:
                outcome = DestinationCleanupResult(status="failed", revalidated=False, detail=f"destination executor failed: {type(exc).__name__}: {exc}")
            if not outcome.revalidated:
                results.append(CleanupActionResult(original.candidate_id, original.resource_id, original.path, "failed", reason=outcome.detail or "destination executor did not revalidate resource identity/content before deletion"))
                any_failed = True
                continue
            results.append(CleanupActionResult(
                original.candidate_id, original.resource_id, original.path, outcome.status,
                reclaimed_bytes=int(outcome.reclaimed_bytes), reason=outcome.detail, postcondition=outcome.postcondition,
            ))
            reclaimed += int(outcome.reclaimed_bytes)
            any_failed = any_failed or outcome.status == "failed"
            any_partial = any_partial or outcome.status in {"partial", "protected"}
            if outcome.status == "applied" and outcome.postcondition == "absent":
                tombstones.append({
                    "resource_id": original.resource_id, "candidate_id": original.candidate_id,
                    "path": original.path, "owner": original.owner, "provider": original.provider,
                    "destination": original.destination, "reason": "destination-cleanup-plan-applied",
                    "source_snapshot": dict(original.source_snapshot),
                })
            continue

        current_snapshot = _path_snapshot(Path(original.path))
        if not _same_snapshot(original.source_snapshot, current_snapshot):
            results.append(CleanupActionResult(original.candidate_id, original.resource_id, original.path, "protected", reason="resource changed after cleanup plan was created"))
            any_partial = True
            continue
        path = Path(original.path)
        before = _tree_bytes(path)
        if dry_run:
            results.append(CleanupActionResult(original.candidate_id, original.resource_id, original.path, "planned", reclaimed_bytes=before))
            continue
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        except OSError as exc:
            any_failed = True
            results.append(CleanupActionResult(original.candidate_id, original.resource_id, original.path, "failed", reason=str(exc)))
            continue
        after = _path_snapshot(path)
        deleted = not bool(after.get("exists"))
        actual = before if deleted else max(0, before - _tree_bytes(path))
        reclaimed += actual
        status = "applied" if deleted else "partial"
        any_partial = any_partial or not deleted
        results.append(CleanupActionResult(original.candidate_id, original.resource_id, original.path, status, reclaimed_bytes=actual, postcondition="absent" if deleted else "still-present"))
        if deleted:
            tombstones.append({
                "resource_id": original.resource_id,
                "candidate_id": original.candidate_id,
                "path": original.path,
                "owner": original.owner,
                "provider": original.provider,
                "destination": original.destination,
                "reason": "cleanup-plan-applied",
                "source_snapshot": dict(original.source_snapshot),
            })

    for action in plan.opaque_actions:
        if dry_run:
            results.append(CleanupActionResult(action.action_id, action.action_id, action.cwd, "planned", reason=action.detail))
            continue
        if not confirm:
            results.append(CleanupActionResult(action.action_id, action.action_id, action.cwd, "protected", reason="opaque provider cleanup requires confirmation"))
            any_partial = True
            continue
        if provider_executor is None:
            results.append(CleanupActionResult(action.action_id, action.action_id, action.cwd, "failed", reason="provider executor unavailable"))
            any_failed = True
            continue
        ok, detail = provider_executor(action)
        results.append(CleanupActionResult(action.action_id, action.action_id, action.cwd, "applied" if ok else "failed", reason=detail))
        any_failed = any_failed or not ok

    status = "failed" if any_failed and not any(item.status == "applied" for item in results) else "partial" if any_failed or any_partial else "planned" if dry_run else "applied"
    receipt_payload = {
        "plan_id": plan.plan_id,
        "status": status,
        "results": [item.as_dict() for item in results],
        "reclaimed_bytes": reclaimed,
    }
    return CleanupReceipt(
        receipt_id="cleanup-receipt:" + _digest(receipt_payload)[:32], plan_id=plan.plan_id,
        repository_id=plan.repository_id, scope=plan.scope, destination=plan.destination,
        status=status, results=tuple(results), reclaimed_bytes=reclaimed, tombstones=tuple(tombstones),
    )


def provider_cleanup_receipt_from_results(
    plan: CleanupPlan,
    actions: Sequence[OpaqueCleanupAction],
    results: Sequence[Any],
) -> CleanupReceipt:
    """Project provider-clean results -> canonical LIFE receipt, without replay.

    Provider cleans are intentionally opaque: Devtool records the action and
    outcome but does not invent a pre-execution deleted-file list or reclaim
    count. This function observes already-executed results; it never executes
    the provider command a second time.
    """
    rows: list[CleanupActionResult] = []
    failed = False
    partial = False
    for index, action in enumerate(actions):
        result = results[index] if index < len(results) else None
        if result is None:
            rows.append(CleanupActionResult(
                action.action_id, action.action_id, action.cwd, "failed",
                reason="provider cleanup result missing",
            ))
            failed = True
            continue
        returncode = int(getattr(result, "returncode", 1) or 0)
        skipped = bool(getattr(result, "skipped", False))
        status = "skipped" if skipped else "applied" if returncode == 0 else "failed"
        if status == "failed":
            failed = True
        if status == "skipped":
            partial = True
        reason = str(getattr(result, "failure_kind", "") or "") if returncode else ""
        rows.append(CleanupActionResult(
            action.action_id, action.action_id, action.cwd, status,
            reclaimed_bytes=0,
            reason=reason,
            postcondition="provider-reported-success" if status == "applied" else "provider-result-recorded",
        ))
    status = "failed" if failed and not any(item.status == "applied" for item in rows) else "partial" if failed or partial else "applied"
    payload = {"plan_id": plan.plan_id, "status": status, "results": [item.as_dict() for item in rows]}
    return CleanupReceipt(
        receipt_id="cleanup-receipt:" + _digest(payload)[:32],
        plan_id=plan.plan_id,
        repository_id=plan.repository_id,
        scope=plan.scope,
        destination=plan.destination,
        status=status,
        results=tuple(rows),
        reclaimed_bytes=0,
    )


def _run_catalog_references(repo_root: Path) -> tuple[list[GeneratedOutputDescriptor], list[CleanupReference]]:
    from .core_runtime import private_run_root
    from .run_store import RunCatalog

    descriptors: list[GeneratedOutputDescriptor] = []
    refs: list[CleanupReference] = []
    catalog = RunCatalog.open(private_run_root(repo_root), create=False)
    for attachment in catalog.attachments.values():
        if not attachment.path:
            continue
        descriptor = GeneratedOutputDescriptor.create(
            attachment.path, owner="CORE/RUN", provider="run-store", category=f"run-attachment:{attachment.kind}",
            producer_id=attachment.run_id, lifecycle="durable", rebuild_cost="unknown",
            provenance={"attachment_id": attachment.attachment_id, "state": attachment.state},
            managed=True, published=True,
        )
        descriptors.append(descriptor)
        refs.append(CleanupReference(
            reference_id=f"run:{attachment.run_id}:attachment:{attachment.attachment_id}", source="RUN",
            resource_id=descriptor.resource_id, path=attachment.path, state=attachment.state,
            protective=attachment.state != "tombstoned", reason="RUN attachment remains referenced" if attachment.state != "tombstoned" else "RUN attachment tombstoned",
        ))
    # Canonical RUN/process streams are protection roots too.  LIFE does not
    # decide when stream payloads become tombstoneable; RUN owns that lifecycle.
    # Protecting all catalogued streams is deliberately conservative and avoids
    # racing active output/debug writers during cleanup.
    for stream in catalog.streams.values():
        if not stream.path:
            continue
        refs.append(CleanupReference(
            reference_id=f"run:{stream.run_id}:stream:{stream.stream_id}", source="RUN",
            path=stream.path, state=str((catalog.runs.get(stream.run_id) or {}).get("status") or "active"),
            protective=True, reason="canonical RUN/process stream is RUN-owned evidence",
        ))
    # RER/reusable output materialization refs are stored in canonical RUN/node metadata.
    for run_id, row in catalog.runs.items():
        if not isinstance(row, Mapping):
            continue
        for key in ("reuse_materialization_refs", "reuse_outputs", "rer_output_paths", "seal_paths"):
            values = row.get(key) or ()
            if isinstance(values, str):
                values = (values,)
            if not isinstance(values, (list, tuple, set)):
                continue
            for value in values:
                text = str(value or "").strip()
                if text:
                    refs.append(CleanupReference(
                        reference_id=f"run:{run_id}:{key}:{_digest(text)[:12]}", source="RER" if "reuse" in key or "rer" in key else "seal",
                        path=text, reason="reusable/seal evidence protects this resource",
                    ))
    return descriptors, refs


def _artifact_registry_projection(repo_root: Path) -> tuple[list[GeneratedOutputDescriptor], list[CleanupReference]]:
    from .artifact_registry import registry_path, _load_registry_unlocked

    path = registry_path(repo_root)
    if not path.is_file():
        return [], []
    data = _load_registry_unlocked(repo_root, preserve_corrupt=False)
    if data.get("warning") or data.get("warnings"):
        raise ValueError(str(data.get("warning") or data.get("warnings")))
    descriptors: list[GeneratedOutputDescriptor] = []
    refs: list[CleanupReference] = []
    for item in data.get("items", []):
        if not isinstance(item, Mapping):
            continue
        raw_path = str(item.get("path") or item.get("artifact") or "").strip()
        if not raw_path:
            continue
        artifact_path = Path(raw_path).expanduser()
        if not artifact_path.is_absolute():
            artifact_path = repo_root / artifact_path
        record_id = str(item.get("id") or item.get("event_key") or item.get("sha256") or _digest(item)[:20])
        descriptor = GeneratedOutputDescriptor.create(
            artifact_path, owner="artifact-registry", provider=str(item.get("provider") or item.get("runner") or "artifact"),
            category="produced-artifact", producer_id=record_id, lifecycle="published", rebuild_cost="high",
            provenance={"registry_record_id": record_id}, managed=True, published=True,
        )
        descriptors.append(descriptor)
        refs.append(CleanupReference(
            reference_id=f"artifact:{record_id}", source="artifact-registry", resource_id=descriptor.resource_id,
            path=str(artifact_path), reason="produced artifact registry record protects published output",
        ))
    return descriptors, refs


def build_cleanup_ownership_view(
    repo_root: str | Path,
    descriptors: Iterable[GeneratedOutputDescriptor] = (),
    *,
    policy_generation: str = "default",
    references: Iterable[CleanupReference] = (),
    strict_authorities: bool = False,
) -> CleanupOwnershipView:
    root = _canonical_path(repo_root)
    repo_identity = build_repository_identity(root)
    owned = list(descriptors)
    refs = list(references)
    authority_sources: set[str] = set()
    blockers: list[str] = []

    authority_errors: list[str] = []

    try:
        run_descriptors, run_refs = _run_catalog_references(root)
        owned.extend(run_descriptors)
        refs.extend(run_refs)
        if run_descriptors or run_refs:
            authority_sources.add("RUN/RER")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        if (root / ".devtool" / "private" / "run-v3" / "catalog.json").exists():
            authority_errors.append(f"RUN/RER: {type(exc).__name__}: {exc}")

    try:
        artifact_descriptors, artifact_refs = _artifact_registry_projection(root)
        owned.extend(artifact_descriptors)
        refs.extend(artifact_refs)
        if artifact_descriptors:
            authority_sources.add("artifact-registry")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        from .artifact_registry import registry_path
        if registry_path(root).exists():
            authority_errors.append(f"artifact-registry: {type(exc).__name__}: {exc}")

    try:
        from .transactions import find_active_transactions, read_transaction
        if strict_authorities:
            _verify_active_transaction_authority_markers(root)
        active = find_active_transactions(root, include_live=True)
        for transaction_path in active:
            authority_sources.add("transactions")
            try:
                data = read_transaction(transaction_path)
            except Exception as exc:
                if strict_authorities:
                    raise CleanupAuthorityUnavailableError(
                        f"Cannot read active transaction cleanup authority {transaction_path}: {type(exc).__name__}: {exc}"
                    ) from exc
                data = {}
            transaction_id = str(data.get("transaction_id") or transaction_path.name)
            blockers.append(f"active artifact transaction {transaction_id} protects repository cleanup")
            refs.append(CleanupReference(
                reference_id=f"transaction:{transaction_id}", source="transaction", path=str(transaction_path),
                state=str(data.get("status") or "active"), reason="active transaction/recovery state is cleanup-critical",
            ))
    except CleanupAuthorityUnavailableError:
        raise
    except (OSError, ValueError, RuntimeError) as exc:
        authority_errors.append(f"transactions: {type(exc).__name__}: {exc}")

    try:
        from .validation_sessions import ValidationSessionError, load_active_session
        session = load_active_session(root)
        if session is not None and str(getattr(session, "status", "")) not in {"", "completed", "failed", "aborted"}:
            authority_sources.add("validation")
            blockers.append(f"active validation session {session.session_id} protects repository cleanup")
            refs.append(CleanupReference(
                reference_id=f"validation:{session.session_id}", source="validation",
                path=str(getattr(session, "transaction_path", "") or ""), state=str(session.status),
                reason="active/resumable validation is a cleanup protection root",
            ))
    except (OSError, ValueError, json.JSONDecodeError, ValidationSessionError) as exc:
        authority_errors.append(f"validation: {type(exc).__name__}: {exc}")

    if authority_errors and strict_authorities:
        raise CleanupAuthorityUnavailableError(
            "Cleanup protection authority is unreadable; refusing destructive cleanup: "
            + "; ".join(authority_errors)
        )

    # Stable order + de-dup by resource/reference identity.
    desc_map = {item.resource_id: item for item in owned}
    ref_map = {item.reference_id: item for item in refs}
    return CleanupOwnershipView(
        repository_id=repo_identity.fingerprint,
        repository_root=str(root),
        policy_generation=str(policy_generation or "default"),
        descriptors=tuple(desc_map[key] for key in sorted(desc_map)),
        references=tuple(ref_map[key] for key in sorted(ref_map)),
        global_blockers=tuple(dict.fromkeys(blockers)),
        authority_sources=tuple(sorted(authority_sources)),
    )


def descriptor_from_path(
    path: str | Path,
    *, owner: str,
    provider: str,
    category: str,
    producer_id: str,
    repo_root: str | Path | None = None,
    heuristic: bool = False,
    scope: CleanupScope = CleanupScope.PROJECT,
    destination: str = "local",
    lifecycle: str = "rebuildable",
) -> GeneratedOutputDescriptor:
    # A filename/suffix/ignore match is discovery evidence only.  It never
    # becomes deletion authority by itself (LIFE-G2).  Callers that can prove
    # producer ownership must pass ``heuristic=False`` or construct an explicit
    # GeneratedOutputDescriptor.
    managed = not heuristic
    git_state = _git_state(_canonical_path(repo_root), _canonical_path(path)) if repo_root is not None else "unknown"
    return GeneratedOutputDescriptor.create(
        path, owner=owner, provider=provider, category=category, producer_id=producer_id,
        lifecycle=lifecycle, rebuild_cost="low", scope=scope, destination=destination,
        provenance={
            "discovery": "heuristic" if heuristic else "declared-provider-output",
            "git_state_at_discovery": git_state,
        }, managed=managed,
    )


def remote_staging_descriptor(
    path: str | Path,
    *,
    provider: str,
    destination: str,
    producer_id: str,
    lifecycle: str = "disposable",
    rebuild_cost: str = "low",
    provenance: Mapping[str, Any] | None = None,
) -> GeneratedOutputDescriptor:
    """Declare provider staging that is owned by a specific destination.

    The path is descriptive controller-side metadata only.  ``CleanupScope.REMOTE``
    plus ``destination`` ensures application requires a destination-local executor
    and prevents a same-shaped path on another host from being treated as the same
    cleanup authority.
    """
    destination_id = str(destination or "").strip()
    if not destination_id or destination_id == "local":
        raise ValueError("remote staging requires an explicit non-local destination")
    provider_id = str(provider or "remote").strip() or "remote"
    return GeneratedOutputDescriptor.create(
        path, owner=f"provider:{provider_id}", provider=provider_id, category="remote-staging",
        producer_id=producer_id, lifecycle=lifecycle, rebuild_cost=rebuild_cost,
        scope=CleanupScope.REMOTE, destination=destination_id,
        provenance={"destination_local": True, **dict(provenance or {})}, managed=True,
    )


def explain_cleanup_candidate(plan: CleanupPlan, candidate_id: str) -> dict[str, Any]:
    """Canonical projection for later CLI/TUI explain surfaces.

    X03 owns the semantics; LIFE-X04 can render this without inventing separate
    ownership, protection, recreation, destination, or invalidation logic.
    """
    candidate = next((item for item in plan.candidates if item.candidate_id == candidate_id), None)
    if candidate is None:
        raise KeyError(candidate_id)
    return {
        "plan_id": plan.plan_id,
        "candidate_id": candidate.candidate_id,
        "resource_id": candidate.resource_id,
        "path": candidate.path,
        "owner": candidate.owner,
        "provider": candidate.provider,
        "scope": candidate.scope.value,
        "destination": candidate.destination,
        "safety_class": candidate.safety_class.value,
        "selected": candidate.selected,
        "protection_reason": candidate.protection_reason,
        "reference_ids": list(candidate.reference_ids),
        "recreation": {
            "rebuild_cost": candidate.rebuild_cost,
            "network_cost": candidate.network_cost,
        },
        "invalidation": {
            "requires_fresh_snapshot": True,
            "filesystem_id": candidate.filesystem_id,
            "source_snapshot": dict(candidate.source_snapshot),
        },
        "reclaimable_bytes": candidate.reclaimable_bytes,
        "confirmation_required": candidate.confirmation_required,
    }


def opaque_action_from_spec(spec: Any, *, destination: str = "local") -> OpaqueCleanupAction:
    metadata = dict(getattr(spec, "metadata", {}) or {})
    provider = str(metadata.get("runner") or metadata.get("project_type") or "provider")
    scope_text = str(metadata.get("cleanup_scope") or "project")
    try:
        scope = CleanupScope(scope_text)
    except ValueError:
        scope = CleanupScope.PROJECT
    argv = tuple(str(item) for item in (getattr(spec, "argv", []) or []))
    cwd = str(getattr(spec, "cwd", "") or metadata.get("project_root") or "")
    return OpaqueCleanupAction(
        action_id="cleanup-action:" + _digest({"provider": provider, "argv": argv, "cwd": cwd, "destination": destination})[:32],
        provider=provider, argv=argv, cwd=cwd, scope=scope, destination=destination,
    )


def annotate_cleanup_specs(specs: Sequence[Any], *, destination: str = "local") -> tuple[OpaqueCleanupAction, ...]:
    actions: list[OpaqueCleanupAction] = []
    for spec in specs:
        if not bool(getattr(spec, "destructive", False)):
            continue
        action = opaque_action_from_spec(spec, destination=destination)
        metadata = dict(getattr(spec, "metadata", {}) or {})
        metadata["cleanup_action"] = action.as_dict()
        metadata["cleanup_semantics"] = "opaque-provider-action"
        spec.metadata = metadata
        actions.append(action)
    return tuple(actions)


__all__ = [
    "CleanupActionKind", "CleanupActionResult", "CleanupCandidate", "CleanupOwnershipView",
    "CleanupPlan", "CleanupReceipt", "CleanupReference", "CleanupSafetyClass", "CleanupScope",
    "DestinationCleanupResult",
    "GeneratedOutputDescriptor", "OpaqueCleanupAction", "RepositoryCacheMigrationPlan",
    "CleanupAuthorityUnavailableError", "annotate_cleanup_specs", "apply_cleanup_plan", "build_cleanup_ownership_view",
    "compile_cleanup_plan", "descriptor_from_path", "explain_cleanup_candidate",
    "preserve_primary_lifecycle_failure", "refresh_cleanup_ownership_view",
    "opaque_action_from_spec", "persist_cleanup_receipt", "plan_repository_cache_relocation",
    "provider_cleanup_receipt_from_results", "remote_staging_descriptor",
]
