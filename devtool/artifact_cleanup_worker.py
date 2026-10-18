from __future__ import annotations

import argparse
import hashlib
import json
import os
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
    return sources


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
    plan_id = "cleanup-plan:" + _digest({
        "repository_id": repo_id,
        "policy": "life-apply-source-external-bounded-v1",
        "paths": [str(item) for item in existing],
        "reason": reason,
    })[:32]
    rows: list[CleanupActionResult] = []
    reclaimed = 0
    failed = False
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
            rows.append(CleanupActionResult(action_id, resource_id, str(path), "failed", reason=str(exc)))
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
        payload = {
            "returncode": 1,
            "output": f"Failed canonical LIFE cleanup of original artifact(s) {args.reason}.\n{type(exc).__name__}: {exc}",
            "failure_kind": "artifact_cleanup_failure",
            "metadata": {"cleanup_worker": "fresh-process"},
        }
    print(json.dumps(payload, sort_keys=True))
    return int(payload.get("returncode") or 0)


if __name__ == "__main__":
    raise SystemExit(main())
