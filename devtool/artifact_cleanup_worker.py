from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Sequence

from .repository_identity import build_repository_identity
from .cleanup_lifecycle import (
    CleanupActionResult,
    CleanupReceipt,
    CleanupScope,
    GeneratedOutputDescriptor,
    apply_cleanup_plan,
    build_cleanup_ownership_view,
    compile_cleanup_plan,
    persist_cleanup_receipt,
)


def _canonical(path: str | Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def _safe_cleanup_error_label(exc: BaseException) -> str:
    """Return only fixed, reviewed labels, never arbitrary exception bodies.

    Preserve legacy refusal families required by callers/tests while refusing
    to echo path fragments and untrusted payloads from OS/library errors.
    """
    if not isinstance(exc, ValueError):
        return "filesystem or worker failure" if isinstance(exc, OSError) else "worker refusal"
    message = str(exc).lower()
    for fragment, label in (
        ("durable execution provenance", "durable execution provenance unavailable"),
        ("durable consumed-source inventory", "durable consumed-source inventory mismatch"),
        ("attachment integrity", "attachment integrity mismatch"),
        ("authority", "apply authority refused"),
    ):
        if fragment in message:
            return label
    return "source identity or provenance refused"


def _literal_path(value: str | Path) -> Path:
    """Keep the exact file name: resolving the leaf follows symlinks to victims."""
    return Path(os.path.abspath(os.path.expanduser(str(value))))


def cleanup_source_snapshot(path: str | Path) -> dict[str, Any]:
    """Capture the exact consumed source identity; never follow a leaf symlink."""
    target = _literal_path(path)
    if target.is_symlink():
        raise ValueError("source symlink is not an authorized cleanup target")
    st = target.lstat()
    if not stat.S_ISREG(st.st_mode):
        raise ValueError("only a regular consumed artifact file may be removed")
    parent = target.parent.resolve(strict=True)
    parent_st = parent.stat()
    return {
        "path": str(target), "parent": str(parent),
        "parent_dev": parent_st.st_dev, "parent_ino": parent_st.st_ino,
        "dev": st.st_dev, "ino": st.st_ino, "size": st.st_size,
        "mtime_ns": st.st_mtime_ns, "ctime_ns": st.st_ctime_ns,
    }


def _unlink_owned_source(path: Path, expected: dict[str, Any] | None) -> int:
    """Unlink by an anchored directory fd after revalidating file identity."""
    current = cleanup_source_snapshot(path)
    if expected is not None and current != expected:
        raise ValueError("consumed source identity changed after cleanup authorization")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    parent_fd = os.open(current["parent"], flags)
    try:
        pst = os.fstat(parent_fd)
        if (pst.st_dev, pst.st_ino) != (current["parent_dev"], current["parent_ino"]):
            raise ValueError("consumed source parent changed before cleanup")
        st = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISREG(st.st_mode):
            raise ValueError("consumed source is not a regular file")
        if (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns) != (
            current["dev"], current["ino"], current["size"], current["mtime_ns"], current["ctime_ns"]
        ):
            raise ValueError("consumed source identity changed before unlink")
        os.unlink(path.name, dir_fd=parent_fd)
        return int(st.st_size)
    finally:
        os.close(parent_fd)


def cleanup_source_failure_diagnostic(path: Path, expected: dict[str, Any] | None,
                                      exc: BaseException) -> dict[str, Any]:
    """Redacted failure classification; never mutate the consumed artifact.

    Android emulated storage may not implement stable inode/ctime or dir-fd
    unlink semantics. Record which identity fields drifted, not their values,
    and never downgrade the exact-source deletion authority.
    """
    target = _literal_path(path)
    fields = ('parent_dev', 'parent_ino', 'dev', 'ino', 'size', 'mtime_ns', 'ctime_ns')
    mismatch: list[str] = []
    observation = 'unavailable'
    try:
        actual = cleanup_source_snapshot(target)
        observation = 'regular-file-observed'
        if expected is not None:
            mismatch = [name for name in fields if expected.get(name) != actual.get(name)]
            if expected.get('parent') != actual.get('parent'):
                mismatch.append('parent')
    except (OSError, ValueError):
        observation = 'source-snapshot-refused'
    code = getattr(exc, 'errno', None)
    return {
        'category': 'identity-drift' if mismatch else (
            'filesystem-error' if isinstance(exc, OSError) else 'authority-refusal'),
        'exception_type': type(exc).__name__ if type(exc) in (OSError, ValueError, PermissionError, FileNotFoundError, NotImplementedError) else 'Other',
        'errno': int(code) if type(code) is int and 0 < code < 256 else None,
        'source_name_sha256': hashlib.sha256(str(target).encode()).hexdigest(),
        'shared_storage_path': str(target).startswith(('/storage/emulated/', '/sdcard/')),
        'observed': observation,
        'mismatched_identity_fields': mismatch,
        'source_deleted': not (target.exists() or target.is_symlink()),
        'safe_to_retry_unlink': False,
        'policy_relaxed': False,
    }


def _validate_persisted_apply_evidence(
    root: Path, run_id: str, record_id: str, reason: str,
    requested_sources: dict[str, dict[str, Any]],
) -> None:
    """Validate a runner-persisted RUN-V3 record before deleting its sources.

    This prevents unbacked OperationResult metadata from being treated as a
    completed apply. It is an identity/consistency check, not authorization
    against another process with the same filesystem privileges.
    """
    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise ValueError("invalid durable apply run identity")
    if not re.fullmatch(r"artifact-execution:[0-9a-f]{24}", record_id):
        raise ValueError("invalid durable artifact execution identity")
    from .core_runtime import private_run_root
    from .run_store import RunCatalog

    store = private_run_root(root)
    catalog = RunCatalog.open(store, create=False)
    record_path = store / run_id / "artifacts" / (record_id.replace(":", "-") + ".json")
    if record_path.is_symlink() or not record_path.is_file():
        raise ValueError("durable artifact execution record is unavailable")
    data_bytes = record_path.read_bytes()
    if len(data_bytes) > 65536:
        raise ValueError("durable artifact execution record is oversized")
    record = json.loads(data_bytes)
    if not isinstance(record, dict) or record.get("record_id") != record_id or record.get("run_id") != run_id:
        raise ValueError("durable artifact execution identity mismatch")
    if record.get("durability") != "durable" or not str(record.get("artifact_id") or "") or not str(record.get("plan_id") or ""):
        raise ValueError("durable artifact execution record is incomplete")
    row = catalog.runs.get(run_id)
    if not isinstance(row, dict) or any((
        str(row.get(field) or "") != str(record.get(expected) or "")
        for field, expected in (("artifact_id", "artifact_id"), ("artifact_plan_id", "plan_id"),
                                ("artifact_qualification_receipt_id", "qualification_receipt_id"))
    )):
        raise ValueError("durable run catalog does not attest this artifact execution")
    status = str(record.get("execution_status") or "")
    allowed = {"succeeded", "applied-pending-validation"}
    if reason == "after failed apply":
        allowed.update({"failed", "rolled-back", "rollback-failed"})
    if status not in allowed:
        raise ValueError("durable artifact execution status is not eligible for cleanup")
    # The catalog seals the attachment digest when the runner saves the record.
    # A file substituted after the apply is not acceptable evidence.
    matching = [item for item in catalog.attachments.values()
                if item.run_id == run_id and item.kind == "artifact-execution-record"
                and item.path == str(record_path) and item.state != "tombstoned"]
    if not any(item.sha256 == hashlib.sha256(data_bytes).hexdigest() for item in matching):
        raise ValueError("durable artifact execution attachment integrity mismatch")
    # A valid record for *another* apply must not authorize arbitrary deletion.
    # The runner separately persists source identities as a catalog-attested
    # attachment; both the list and the inode/stat snapshots must agree.
    evidence_path = record_path.with_name(record_path.stem + "-sources.json")
    if evidence_path.is_symlink() or not evidence_path.is_file():
        raise ValueError("durable consumed-source provenance is unavailable")
    evidence_bytes = evidence_path.read_bytes()
    if len(evidence_bytes) > 65536:
        raise ValueError("durable consumed-source provenance is oversized")
    source_evidence = json.loads(evidence_bytes)
    if (not isinstance(source_evidence, dict)
        or source_evidence.get("schema_version") != 1
        or source_evidence.get("repository") != str(root)
        or source_evidence.get("run_id") != run_id
        or source_evidence.get("artifact_execution_record_id") != record_id):
        raise ValueError("durable consumed-source provenance identity mismatch")
    source_rows = source_evidence.get("sources")
    if not isinstance(source_rows, list) or not source_rows:
        raise ValueError("durable consumed-source inventory is missing")
    source_by_path = {row.get("path"): row for row in source_rows if isinstance(row, dict)}
    if len(source_by_path) != len(source_rows) or source_by_path != requested_sources:
        raise ValueError("durable consumed-source inventory does not match the requested source")
    matching_provenance = [item for item in catalog.attachments.values()
                           if item.run_id == run_id and item.kind == "artifact-apply-source-provenance"
                           and item.path == str(evidence_path) and item.state != "tombstoned"]
    if not any(item.sha256 == hashlib.sha256(evidence_bytes).hexdigest() for item in matching_provenance):
        raise ValueError("durable consumed-source provenance attachment integrity mismatch")


def _read_authority(fd: int, *, root: Path, reason: str, run_id: str, paths: Sequence[str]) -> dict[str, dict[str, Any]]:
    if fd < 3:
        raise ValueError("fresh cleanup worker requires parent-issued apply authority")
    with os.fdopen(os.dup(fd), "rb") as stream:
        data = stream.read(65537)
    if len(data) > 65536:
        raise ValueError("apply cleanup authority exceeds bounded limit")
    payload = json.loads(data)
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("invalid apply cleanup authority")
    if payload.get("repository") != str(root) or payload.get("reason") != reason or payload.get("run_id") != run_id:
        raise ValueError("apply cleanup authority context mismatch")
    if not payload.get("apply_completed"):
        raise ValueError("apply cleanup authority lacks a terminal apply result")
    rows = payload.get("sources")
    if not isinstance(rows, list) or not rows or len(rows) > 32:
        raise ValueError("apply cleanup authority source count invalid")
    sources = {row["path"]: row for row in rows if isinstance(row, dict) and isinstance(row.get("path"), str)}
    if len(sources) != len(rows) or sorted(sources) != sorted(str(_literal_path(path)) for path in paths):
        raise ValueError("apply cleanup authority sources differ from requested paths")
    evidence = payload.get("execution_records")
    if not isinstance(evidence, list) or not evidence or len(evidence) > 32:
        raise ValueError("cleanup authority lacks bounded durable execution provenance")
    covered: list[str] = []
    inspected: set[tuple[str, str]] = set()
    for item in evidence:
        if not isinstance(item, dict) or not isinstance(item.get("sources"), list) or not item["sources"]:
            raise ValueError("invalid durable execution source mapping")
        record_run = str(item.get("run_id") or "")
        record_id = str(item.get("record_id") or "")
        key = (record_run, record_id)
        item_sources = item["sources"]
        if not all(isinstance(source, str) and source in sources for source in item_sources):
            raise ValueError("durable execution source mapping contains an unrelated path")
        if key not in inspected:
            _validate_persisted_apply_evidence(
                root, record_run, record_id, reason,
                {source: sources[source] for source in item_sources},
            )
            inspected.add(key)
        covered.extend(item_sources)
    if sorted(covered) != sorted(sources) or len(covered) != len(set(covered)):
        raise ValueError("durable execution provenance does not cover exact consumed sources")
    return sources



def external_apply_cleanup_plan_id(
    root: Path, paths: Sequence[Path], *, reason: str,
    source_snapshots: dict[str, dict[str, Any]],
) -> str:
    """A deletion receipt must identify the *specific files*, not only their names.

    Inode/time/parent snapshots are already protected by phase-2 RUN evidence.
    Include them in the receipt plan so stale receipts for recycled download
    filenames cannot be used to reconcile a later timed-out cleanup.
    """
    repository_id = build_repository_identity(root).fingerprint
    names = sorted(str(_literal_path(path)) for path in paths)
    if len(names) != len(set(names)) or any(name not in source_snapshots for name in names):
        raise ValueError("cleanup plan requires unique authorized source identities")
    return "cleanup-plan:" + _digest({
        "repository_id": repository_id,
        "policy": "life-apply-source-external-source-identity-v2",
        "sources": [source_snapshots[name] for name in names],
        "reason": reason,
    })[:32]


def verified_external_cleanup_receipt(
    root: Path, paths: Sequence[Path], *, reason: str, run_id: str,
    source_snapshots: dict[str, dict[str, Any]],
) -> dict[str, str] | None:
    """Read-only reconciliation of a terminated worker; never repeat unlink.

    A missing file proves only absence, *not* a completed cleanup. Accept a
    persisted receipt only when it matches the exact consumed-source identity,
    current repository, terminal actions and (when set) RUN attachment digest.
    """
    if not paths or any(_path_is_within(_literal_path(path), root) for path in paths):
        return None
    names = sorted(str(_literal_path(path)) for path in paths)
    try:
        plan_id = external_apply_cleanup_plan_id(
            root, paths, reason=reason, source_snapshots=source_snapshots,
        )
        repo_id = build_repository_identity(root).fingerprint
        directory = root / ".devtool" / "private" / "cleanup" / "receipts"
        if directory.is_symlink() or not directory.is_dir():
            return None
        # Non-mutating bounded scan; an unbounded or unavailable inventory is
        # UNKNOWN, never a successful recovery.
        paths_to_check = []
        for index, candidate in enumerate(directory.glob("cleanup-receipt-*.json")):
            if index >= 4096:
                return None
            paths_to_check.append(candidate)
        catalog = None
        if run_id:
            from .core_runtime import private_run_root
            from .run_store import RunCatalog
            catalog = RunCatalog.open(private_run_root(root), create=False)
        for candidate in paths_to_check:
            if candidate.is_symlink() or not candidate.is_file() or candidate.stat().st_size > 65536:
                continue
            raw = candidate.read_bytes()
            row = json.loads(raw)
            if not isinstance(row, dict) or row.get("plan_id") != plan_id:
                continue
            if (row.get("repository_id") != repo_id or row.get("status") != "applied"
                or row.get("scope") != "project" or row.get("destination") != "local"
                or candidate.name != str(row.get("receipt_id", "")).replace(":", "-") + ".json"):
                continue
            results = row.get("results")
            if (not isinstance(results, list) or len(results) != len(names)
                or sorted(str(item.get("path")) for item in results if isinstance(item, dict)) != names
                or any(not isinstance(item, dict) or item.get("status") != "applied"
                       or item.get("postcondition") != "absent" for item in results)):
                continue
            if any(_literal_path(path).exists() or _literal_path(path).is_symlink() for path in paths):
                continue
            if catalog is not None and not any(
                item.run_id == run_id and item.kind == "cleanup-receipt"
                and item.state != "tombstoned" and item.path == str(candidate)
                and item.sha256 == hashlib.sha256(raw).hexdigest()
                for item in catalog.attachments.values()
            ):
                continue
            return {"cleanup_receipt_id": str(row["receipt_id"]),
                    "cleanup_receipt_path": str(candidate), "cleanup_status": "applied"}
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None
    return None


def _cleanup_external_apply_sources(
    root: Path,
    existing: Sequence[Path],
    descriptors: Sequence[GeneratedOutputDescriptor],
    *,
    reason: str,
    run_id: str,
    authorized_sources: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Delete explicitly-consumed external source artifacts without a global rebuild.

    These paths are outside the repository and are already authorized by the
    current apply command's delete-on-success policy.  Rebuilding every RUN,
    execution-history, provider and validation ownership projection merely to
    unlink `/sdcard/Download/...zip` is both unrelated and unbounded. Repository
    internal paths still use the full LIFE ownership/revalidation path below.
    """

    repo_id = build_repository_identity(root).fingerprint
    descriptor_by_path = {str(_canonical(item.path)): item for item in descriptors}
    try:
        snapshots = authorized_sources if authorized_sources is not None else {
            str(path): cleanup_source_snapshot(path) for path in existing
        }
        plan_id = external_apply_cleanup_plan_id(root, existing, reason=reason, source_snapshots=snapshots)
    except (OSError, ValueError) as exc:
        # The exception may contain a user ZIP name or sensitive directory.
        # Keep the machine classification, never echo raw exception text.
        return {"returncode": 1,
                "output": "Refused consumed source cleanup; authorization or source identity invalid.",
                "failure_kind": "artifact_cleanup_authority_missing",
                "metadata": {"cleanup_status": "protected",
                             "error_category": "SOURCE_AUTHORITY_REFUSED",
                             "exception_type": type(exc).__name__}}
    rows: list[CleanupActionResult] = []
    reclaimed = 0
    failed = False
    source_failures: list[dict[str, Any]] = []
    for path in existing:
        descriptor = descriptor_by_path.get(str(path))
        resource_id = descriptor.resource_id if descriptor is not None else f"apply-source:{_digest(str(path))[:24]}"
        action_id = "cleanup:" + _digest({"plan": plan_id, "path": str(path), "resource": resource_id})[:32]
        if _path_is_within(path, root):
            rows.append(CleanupActionResult(action_id, resource_id, str(path), "protected", reason="repository-internal source requires full LIFE authority"))
            failed = True
            continue
        if path.exists() and path.is_dir() and not path.is_symlink():
            rows.append(CleanupActionResult(action_id, resource_id, str(path), "failed", reason="apply source cleanup only authorizes files/symlinks"))
            failed = True
            continue
        try:
            size = _unlink_owned_source(path, authorized_sources.get(str(path)) if authorized_sources is not None else None)
        except (OSError, ValueError) as exc:
            source_failures.append(cleanup_source_failure_diagnostic(
                path, authorized_sources.get(str(path)) if authorized_sources is not None else None, exc))
            rows.append(CleanupActionResult(action_id, resource_id, str(path), "failed", reason=type(exc).__name__))
            failed = True
            continue
        if path.exists() or path.is_symlink():
            rows.append(CleanupActionResult(action_id, resource_id, str(path), "partial", reason="unlink postcondition failed"))
            failed = True
            continue
        reclaimed += size
        rows.append(CleanupActionResult(action_id, resource_id, str(path), "applied", reclaimed_bytes=size, postcondition="absent"))

    status = "failed" if failed else "applied"
    receipt_id = "cleanup-receipt:" + _digest({
        "plan": plan_id,
        "status": status,
        "results": [item.as_dict() for item in rows],
    })[:32]
    receipt = CleanupReceipt(
        receipt_id=receipt_id,
        plan_id=plan_id,
        repository_id=repo_id,
        scope=CleanupScope.PROJECT,
        destination="local",
        status=status,
        results=tuple(rows),
        reclaimed_bytes=reclaimed,
    )
    receipt_path = persist_cleanup_receipt(root, receipt, run_id=run_id)
    removed = [item.path for item in rows if item.status == "applied"]
    problems = [f"{item.path}: {item.reason or item.status}" for item in rows if item.status != "applied"]
    metadata = {
        "cleanup_receipt_id": receipt.receipt_id,
        "cleanup_receipt_path": str(receipt_path),
        "cleanup_status": receipt.status,
        "cleanup_worker": "fresh-process",
        "cleanup_mode": "bounded-external-apply-source",
        "source_failure_diagnostics": source_failures,
        "cleanup_policy_relaxed": False,
    }
    if failed:
        return {
            "returncode": 1,
            "output": f"Bounded LIFE cleanup failed to delete original artifact(s) {reason}.\n" + "\n".join(problems),
            "failure_kind": "artifact_cleanup_failure",
            "metadata": metadata,
        }
    return {
        "returncode": 0,
        "output": f"Deleted original artifact(s) {reason} through bounded LIFE apply-source cleanup.\n" + "\n".join(f"removed: {item}" for item in removed),
        "failure_kind": "",
        "metadata": metadata,
    }


def cleanup_apply_source_artifacts(
    repo_root: str | Path,
    artifact_paths: Sequence[str | Path],
    *,
    reason: str,
    run_id: str = "",
    authorized_sources: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    root = _canonical(repo_root)
    candidates: list[Path] = []
    # RMP-C02: deleting an artifact family is not the same as deleting the
    # consumed artifact. Sibling downloads and sidecars require their own
    # explicit, source-bound apply authorization; do not infer ownership by name.
    for raw_path in artifact_paths:
        literal = _literal_path(raw_path)
        if literal not in candidates:
            candidates.append(literal)

    existing = [path for path in candidates if path.exists() or path.is_symlink()]
    if not existing:
        return {
            "returncode": 0,
            "output": "Original artifact cleanup skipped; artifact(s) were already absent.",
            "failure_kind": "",
            "metadata": {"cleanup_status": "no-op"},
        }

    descriptors = [
        GeneratedOutputDescriptor.create(
            candidate,
            owner="artifact-apply-source",
            provider="artifact-apply",
            category="consumed-artifact-source",
            producer_id=f"apply-source:{candidate.name}",
            lifecycle="transient",
            rebuild_cost="unknown",
            scope=CleanupScope.PROJECT,
            managed=True,
            published=False,
            provenance={
                "delete_reason": reason,
                "explicit_apply_cleanup": True,
                "source_artifact": str(candidate),
            },
        )
        for candidate in existing
    ]
    if existing and all(not _path_is_within(candidate, root) for candidate in existing):
        return _cleanup_external_apply_sources(
            root, existing, descriptors, reason=reason, run_id=run_id,
            authorized_sources=authorized_sources,
        )

    ownership = build_cleanup_ownership_view(
        root,
        descriptors,
        policy_generation="life-r04b-fresh-process-artifact-source-cleanup-v1",
        strict_authorities=True,
    )
    plan = compile_cleanup_plan(ownership, scope=CleanupScope.PROJECT, destination="local")
    receipt = apply_cleanup_plan(plan, ownership=ownership, confirm=True)
    receipt_path = persist_cleanup_receipt(root, receipt, run_id=run_id)

    removed = [row.path for row in receipt.results if row.status == "applied"]
    protected = [
        f"{row.path}: {row.reason or row.status}"
        for row in receipt.results
        if row.status not in {"applied", "planned"}
    ]
    failures = [row for row in receipt.results if row.status == "failed"]
    expected_existing = {str(path) for path in existing}
    deleted_existing = set(removed)
    incomplete = sorted(expected_existing - deleted_existing)
    metadata = {
        "cleanup_receipt_id": receipt.receipt_id,
        "cleanup_receipt_path": str(receipt_path),
        "cleanup_status": receipt.status,
        "cleanup_worker": "fresh-process",
    }
    if failures or incomplete:
        details = protected or [str(path) for path in incomplete]
        return {
            "returncode": 1,
            "output": (
                f"Canonical LIFE cleanup refused or failed to delete original artifact(s) {reason}.\n"
                + "\n".join(details)
            ),
            "failure_kind": "artifact_cleanup_failure",
            "metadata": metadata,
        }

    if not removed:
        output = "Original artifact cleanup skipped; artifact(s) were already absent."
    else:
        output = f"Deleted original artifact(s) {reason} through canonical LIFE cleanup.\n" + "\n".join(
            f"removed: {item}" for item in removed
        )
    return {
        "returncode": 0,
        "output": output,
        "failure_kind": "",
        "metadata": metadata,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--authority-fd", type=int, default=-1)
    parser.add_argument("artifact", nargs="+")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        root = _canonical(args.repo_root)
        authority = _read_authority(
            args.authority_fd, root=root, reason=args.reason,
            run_id=args.run_id, paths=args.artifact,
        )
        payload = cleanup_apply_source_artifacts(
            root, args.artifact, reason=args.reason, run_id=args.run_id,
            authorized_sources=authority,
        )
    except BaseException as exc:
        # Never echo exception strings: injected errors, OS errors and malformed
        # authority JSON can contain arbitrary user paths or secret values.
        # Keep the refusal observable without laundering it into qualification.
        category = (
            "APPLY_AUTHORITY_REFUSED" if isinstance(exc, (ValueError, TypeError, KeyError, json.JSONDecodeError))
            else "FILESYSTEM_ERROR" if isinstance(exc, OSError)
            else "UNEXPECTED_WORKER_FAILURE"
        )
        payload = {
            "returncode": 1,
            "output": "Canonical LIFE cleanup refused: " + _safe_cleanup_error_label(exc) + ".",
            "failure_kind": "artifact_cleanup_failure",
            "metadata": {"cleanup_worker": "fresh-process", "cleanup_status": "protected",
                         "error_category": category, "release_qualified": False,
                         "safe_to_retry_unlink": False},
        }
    print(json.dumps(payload, sort_keys=True))
    return int(payload.get("returncode") or 0)


if __name__ == "__main__":
    raise SystemExit(main())
