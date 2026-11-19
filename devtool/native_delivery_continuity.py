"""M3: qualified native commit -> existing durable delivery session.

No apply, rollback, commit, delivery journal, or remote engine is implemented
here. The native CLI calls this only *after* its finalized transaction and
persisted applied-commit history; interrupted adoption is replay-safe through
existing delivery_session.adopt_applied, never through another overlay apply.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence


class NativeContinuityError(RuntimeError):
    """Committed source cannot safely be adopted without operator recovery."""


def _options(manifest: Mapping[str, Any]) -> dict[str, str] | None:
    if manifest.get("schema_version") != 3:
        return None
    from .native_delivery_contract_v3 import normalize_delivery_contract
    normalized = normalize_delivery_contract(manifest)
    if normalized is None:
        raise NativeContinuityError("M3_V3_DELIVERY_CONTRACT_MISSING")
    # The data-only M1 contract is authoritative for fork/upstream identity.
    remote = normalized["remote"]
    return {"base": remote["base_branch"],
            "base_repository": remote["base_repository"],
            "base_sha": remote["base_sha"],
            "head_repository": normalized["source"]["repository"]}


def _qualified_history(repo: Path, overlay: Path, *, expected: Mapping[str, Any] | None) -> dict[str, Any]:
    from .artifacts import load_history
    from .delivery_session import _identity
    identity = _identity(repo, head=None, base="main")  # read-only Git identity
    if identity["repo_root"] != str(repo.resolve(strict=True)):
        raise NativeContinuityError("M3_TARGET_NOT_EXACT_ROOT")
    # Do not follow changed/aliased archive paths. A cleaned archive is allowed:
    # qualified native history is the authoritative original digest.
    if overlay.is_symlink():
        raise NativeContinuityError("M3_ARTIFACT_SYMLINK_REFUSED")
    artifact_path = str(overlay.resolve(strict=False))
    matches = [e for e in load_history(repo)
               if e.get("commit_hash") == identity["head_sha"]
               and e.get("artifact") == artifact_path]
    if len(matches) != 1:
        raise NativeContinuityError("M3_UNIQUE_COMMITTED_HISTORY_REQUIRED")
    record = matches[0]
    manifest = record.get("manifest")
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 3:
        raise NativeContinuityError("M3_V3_COMMITTED_MANIFEST_REQUIRED")
    if expected is not None and manifest != expected:
        raise NativeContinuityError("M3_COMMITTED_MANIFEST_DIFFERS_FROM_APPLIED_DECLARATION")
    _options(manifest)  # strict M1 schema, before session mutation
    return record


def reconcile_committed(repo: str | Path, overlay: str | Path, *,
                        expected_manifest: Mapping[str, Any] | None = None,
                        state_root: Path | None = None,
                        requested_base: str | None = None,
                        requested_base_repository: str | None = None,
                        requested_head_repository: str | None = None,
                        requested_base_sha: str | None = None) -> dict[str, Any]:
    """Resume M3 adoption from existing, committed native history only.

    No parsing of an executable archive and no replay of overlay apply/commit.
    The canonical delivery_session enforces unique session and exact evidence.
    """
    from .delivery_session import adopt_applied
    from .overlay.recovery import overlay_recovery_plan
    root = Path(repo).expanduser().absolute()
    archive = Path(overlay).expanduser().absolute()
    if root.is_symlink() or not root.is_dir() or archive.is_symlink():
        raise NativeContinuityError("M3_UNSAFE_TARGET_OR_ARCHIVE")
    plan = overlay_recovery_plan(root)
    if plan.get("marker_issues") or plan.get("active_transactions"):
        raise NativeContinuityError("M3_UNRESOLVED_NATIVE_TRANSACTION; inspect recovery-plan")
    evidence = _qualified_history(root, archive, expected=expected_manifest)
    options = _options(evidence["manifest"])
    assert options is not None
    for requested, key in ((requested_base, "base"),
                           (requested_base_repository, "base_repository"),
                           (requested_head_repository, "head_repository"),
                           (requested_base_sha, "base_sha")):
        if requested is not None and requested.casefold() != options[key].casefold():
            raise NativeContinuityError("M3_EXPLICIT_IDENTITY_CONFLICT " + key)
    # The only durable journal writer is the existing native adoption API.
    result = adopt_applied(root, archive, state_root=state_root, **options)
    if result.get("native_apply", {}).get("state") != "COMMITTED" or (not result.get("reused") and result.get("stage") != "PLANNED"):
        raise NativeContinuityError("M3_SESSION_DID_NOT_ATTEST_COMMITTED_AND_PLANNED_STATES")
    return result


def is_committed_v3(repo: str | Path, overlay: str | Path) -> bool:
    """Classify a committed history receipt without trusting archive execution.

    Used only to keep immutable executable v2 bootstrap's existing adoption call
    compatible with v3 sessions automatically adopted by the native CLI.
    """
    from .artifacts import load_history
    from .delivery_session import _identity
    root = Path(repo).expanduser().absolute()
    archive = Path(overlay).expanduser().absolute()
    if archive.is_symlink():
        raise NativeContinuityError("M3_ARTIFACT_SYMLINK_REFUSED")
    identity = _identity(root, head=None, base="main")
    path = str(archive.resolve(strict=False))
    records = [x for x in load_history(root)
               if x.get("artifact") == path and x.get("commit_hash") == identity["head_sha"]]
    if len(records) > 1:
        raise NativeContinuityError("M3_AMBIGUOUS_COMMITTED_HISTORY")
    return bool(records and isinstance(records[0].get("manifest"), dict)
                and records[0]["manifest"].get("schema_version") == 3)


def adopt_after_apply(repo: str | Path, overlay: str | Path,
                      manifest: Mapping[str, Any], results: Sequence[Any], *,
                      state_root: Path | None = None) -> dict[str, Any] | None:
    """Called after the native engine persists history; never call on an apply replay."""
    if _options(manifest) is None:
        return None  # Preserve existing legacy ZIP/PYZ adoption behavior.
    commits = [r for r in results if getattr(r, "name", "") == "git-commit"
               and getattr(r, "returncode", 1) == 0 and not getattr(r, "skipped", False)]
    if not commits:
        # Deferred validation is not a committed apply, and cannot become one.
        raise NativeContinuityError("M3_QUALIFIED_COMMIT_REQUIRED; no adoption or replay")
    if len(commits) != 1 or any(getattr(r, "returncode", 1) != 0
                                 for r in results):
        raise NativeContinuityError("M3_COMMIT_HAS_UNRESOLVED_OR_SKIPPED_NATIVE_RESULT")
    return reconcile_committed(repo, overlay, expected_manifest=manifest, state_root=state_root)


__all__ = ["NativeContinuityError", "adopt_after_apply", "reconcile_committed", "is_committed_v3"]
