from __future__ import annotations

"""GIT-FRX-02 private Git recovery refs and durable transaction journal fields.

This module deliberately does not own the artifact transaction lifecycle.  The
existing ``devtool.transactions`` journal remains authoritative; this module
only establishes and verifies the Git recovery anchors embedded in that
journal before an artifact may mutate the worktree.
"""

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping, Sequence

from .git_state import GitStateError, collect_git_state


GIT_TRANSACTION_JOURNAL_SCHEMA_VERSION = 1
_SAFE_REF_COMPONENT = re.compile(r"[^A-Za-z0-9._-]+")


class GitTransactionRecoveryError(RuntimeError):
    """Raised when Devtool cannot establish or verify required recovery state."""


@dataclass(frozen=True, slots=True)
class _PrivateRef:
    role: str
    ref: str
    sha: str

    def as_dict(self) -> dict[str, str]:
        return {"role": self.role, "ref": self.ref, "sha": self.sha}


def _run_git(repo_root: Path, *args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(repo_root), *args],
            input=input_text,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitTransactionRecoveryError(f"Unable to execute git {' '.join(args)}: {exc}") from exc


def _git_error(result: subprocess.CompletedProcess[str], *, context: str) -> GitTransactionRecoveryError:
    detail = (result.stderr or result.stdout or "Git command failed").strip()
    return GitTransactionRecoveryError(f"{context}: {detail}")


def _ref_component(value: str, *, fallback: str) -> str:
    raw = str(value or "").strip()
    cleaned = _SAFE_REF_COMPONENT.sub("-", raw).strip(".-")
    cleaned = cleaned.replace("..", "-")
    if not cleaned or cleaned.endswith(".lock") or "@{" in cleaned:
        cleaned = fallback
    # Preserve readability without allowing arbitrarily large ref names.
    if len(cleaned) > 96:
        digest = hashlib.sha256(raw.encode("utf-8", "surrogatepass")).hexdigest()[:16]
        cleaned = f"{cleaned[:72].rstrip('.-')}-{digest}"
    return cleaned


def _show_ref(repo_root: Path, ref: str) -> str:
    result = _run_git(repo_root, "rev-parse", "--verify", "--quiet", ref)
    if result.returncode == 0:
        return result.stdout.strip()
    if result.returncode == 1:
        return ""
    raise _git_error(result, context=f"Unable to inspect private recovery ref {ref}")


def _delete_created_refs(repo_root: Path, refs: Sequence[_PrivateRef]) -> None:
    for item in reversed(tuple(refs)):
        result = _run_git(repo_root, "update-ref", "-d", item.ref, item.sha)
        if result.returncode not in {0, 1}:
            # Recovery setup is already failing.  Best-effort deletion must not
            # mask the original error, and the surviving private ref remains a
            # safe pointer to the original commit rather than destructive state.
            continue


def _establish_private_refs(repo_root: Path, refs: Sequence[_PrivateRef]) -> tuple[list[_PrivateRef], list[_PrivateRef]]:
    create: list[_PrivateRef] = []
    reused: list[_PrivateRef] = []
    for item in refs:
        check = _run_git(repo_root, "check-ref-format", item.ref)
        if check.returncode != 0:
            raise _git_error(check, context=f"Invalid Devtool private recovery ref {item.ref}")
        current = _show_ref(repo_root, item.ref)
        if current:
            if current != item.sha:
                raise GitTransactionRecoveryError(
                    f"Private recovery ref collision: {item.ref} points to {current}, expected {item.sha}"
                )
            reused.append(item)
        else:
            create.append(item)

    if create:
        script = ["start"]
        script.extend(f"create {item.ref} {item.sha}" for item in create)
        script.extend(["prepare", "commit", ""])
        result = _run_git(repo_root, "update-ref", "--stdin", input_text="\n".join(script))
        if result.returncode != 0:
            raise _git_error(result, context="Unable to create Devtool private recovery refs atomically")

    try:
        for item in refs:
            current = _show_ref(repo_root, item.ref)
            if current != item.sha:
                raise GitTransactionRecoveryError(
                    f"Private recovery ref verification failed: {item.ref} -> {current or '<missing>'}, expected {item.sha}"
                )
    except Exception:
        _delete_created_refs(repo_root, create)
        raise
    return create, reused


def _recovery_refs(
    *,
    run_id: str,
    start_sha: str,
    repository_id: str,
    campaign_id: str,
    federation_id: str,
) -> list[_PrivateRef]:
    run = _ref_component(run_id, fallback="run")
    repo = _ref_component(repository_id, fallback="repo")
    refs = [
        _PrivateRef("run-start", f"refs/devtool/runs/{run}/start", start_sha),
        _PrivateRef("backup-head", f"refs/devtool/backups/{run}/head", start_sha),
    ]
    if campaign_id:
        campaign = _ref_component(campaign_id, fallback="campaign")
        refs.append(_PrivateRef("campaign-start", f"refs/devtool/campaigns/{campaign}/runs/{run}/start", start_sha))
    if federation_id:
        federation = _ref_component(federation_id, fallback="federation")
        refs.append(
            _PrivateRef(
                "federation-start",
                f"refs/devtool/federations/{federation}/repos/{repo}/start",
                start_sha,
            )
        )
    return refs


def establish_git_transaction_recovery(
    repo_root: str | Path,
    *,
    transaction_id: str,
    run_id: str,
    intended_mutation: str,
    worktree_path: str | Path,
    commit_mode: str,
    captured_git_state: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], tuple[dict[str, str], ...]]:
    """Create and verify private refs, returning the journal contract.

    The returned second value contains only refs created by this invocation and
    is used by the caller to undo setup if persisting the durable transaction
    journal itself fails.  Reused refs are never deleted by that cleanup path.
    """

    root = Path(repo_root).resolve()
    captured = dict(captured_git_state or {})
    exo_run_id = str(run_id or "").strip() or f"transaction-{transaction_id}"
    campaign_id = str(os.environ.get("DEVTOOL_CAMPAIGN_ID") or "").strip()
    federation_id = str(os.environ.get("DEVTOOL_FEDERATION_PARENT_RUN_ID") or "").strip()
    federation_workflow = str(os.environ.get("DEVTOOL_FEDERATION_WORKFLOW") or "").strip()
    federation_group = str(os.environ.get("DEVTOOL_FEDERATION_GROUP") or "").strip()

    base: dict[str, Any] = {
        "schema_version": GIT_TRANSACTION_JOURNAL_SCHEMA_VERSION,
        "kind": "git-transaction-recovery",
        "transaction_id": transaction_id,
        "exo_run_id": exo_run_id,
        "repository_id": "",
        "start": {
            "source_ref": str(captured.get("head_ref") or ""),
            "sha": str(captured.get("initial_head") or ""),
            "unborn": not bool(str(captured.get("initial_head") or "").strip()),
        },
        "intended_mutation": str(intended_mutation or "").strip() or "unknown",
        "worktree_path": str(Path(worktree_path).resolve()),
        "commit_mode": str(commit_mode or "none").strip() or "none",
        "campaign_id": campaign_id,
        "federation_id": federation_id,
        "federation_workflow": federation_workflow,
        "federation_group": federation_group,
        "recovery": {
            "required": bool(captured.get("is_git_repo")),
            "state": "not-applicable" if not bool(captured.get("is_git_repo")) else "initializing",
            "refs": [],
            "created_refs": [],
            "reused_refs": [],
        },
        "finalization": {"state": "pending"},
    }

    if not bool(captured.get("is_git_repo")):
        return base, ()

    try:
        authoritative = collect_git_state(root)
    except GitStateError as exc:
        raise GitTransactionRecoveryError(f"Unable to collect authoritative Git state before mutation: {exc}") from exc

    captured_sha = str(captured.get("initial_head") or "")
    authoritative_sha = str(authoritative.head.get("sha") or "")
    captured_ref = str(captured.get("head_ref") or "")
    authoritative_ref = str(authoritative.head.get("symbolic_ref") or "")
    if captured_sha != authoritative_sha or captured_ref != authoritative_ref:
        raise GitTransactionRecoveryError(
            "Git source state changed while establishing transaction recovery; refusing mutation"
        )

    base["repository_id"] = authoritative.repository_id
    base["start"] = {
        "source_ref": authoritative_ref,
        "sha": authoritative_sha,
        "unborn": bool(authoritative.head.get("unborn")),
        "detached": bool(authoritative.head.get("detached")),
        "branch": str(authoritative.head.get("branch") or ""),
    }

    # An unborn repository has no commit object that a ref can point at.  The
    # explicit unborn + symbolic HEAD state, existing index backup, and path
    # journal are therefore the complete recoverable pre-state.
    if bool(authoritative.head.get("unborn")):
        base["recovery"] = {
            "required": True,
            "state": "ready-unborn",
            "refs": [],
            "created_refs": [],
            "reused_refs": [],
        }
        return base, ()

    if not authoritative_sha:
        raise GitTransactionRecoveryError("Git HEAD has no recoverable commit SHA and is not explicitly unborn")

    refs = _recovery_refs(
        run_id=exo_run_id,
        start_sha=authoritative_sha,
        repository_id=authoritative.repository_id,
        campaign_id=campaign_id,
        federation_id=federation_id,
    )
    created, reused = _establish_private_refs(root, refs)
    base["recovery"] = {
        "required": True,
        "state": "ready",
        "refs": [item.as_dict() for item in refs],
        "created_refs": [item.ref for item in created],
        "reused_refs": [item.ref for item in reused],
    }
    return base, tuple(item.as_dict() for item in created)


def cleanup_created_recovery_refs(repo_root: str | Path, created_refs: Sequence[Mapping[str, str]]) -> None:
    refs = [
        _PrivateRef(str(item.get("role") or "cleanup"), str(item.get("ref") or ""), str(item.get("sha") or ""))
        for item in created_refs
        if str(item.get("ref") or "") and str(item.get("sha") or "")
    ]
    _delete_created_refs(Path(repo_root).resolve(), refs)


def verify_git_transaction_recovery(repo_root: str | Path, journal: Mapping[str, Any]) -> dict[str, Any]:
    """Verify that every persisted recovery ref still resolves to its planned SHA."""

    root = Path(repo_root).resolve()
    recovery = journal.get("recovery") if isinstance(journal.get("recovery"), Mapping) else {}
    state = str(recovery.get("state") or "")
    refs = recovery.get("refs") if isinstance(recovery.get("refs"), list) else []
    mismatches: list[dict[str, str]] = []
    for raw in refs:
        if not isinstance(raw, Mapping):
            continue
        ref = str(raw.get("ref") or "")
        expected = str(raw.get("sha") or "")
        current = _show_ref(root, ref) if ref else ""
        if not ref or not expected or current != expected:
            mismatches.append({"ref": ref, "expected": expected, "actual": current})
    ok = state in {"not-applicable", "ready-unborn"} or (state in {"ready", "mutation-applied", "commit-recorded", "rolled-back", "completed", "abandoned"} and not mismatches)
    return {
        "ok": bool(ok),
        "state": state,
        "ref_count": len(refs),
        "mismatches": mismatches,
    }


def update_git_transaction_journal(
    transaction: dict[str, Any],
    *,
    recovery_state: str = "",
    finalization_state: str = "",
    result_commit: str = "",
    error: str = "",
) -> None:
    journal = transaction.get("git_recovery")
    if not isinstance(journal, dict):
        return
    recovery = journal.get("recovery") if isinstance(journal.get("recovery"), dict) else {}
    finalization = journal.get("finalization") if isinstance(journal.get("finalization"), dict) else {}
    if recovery_state:
        recovery["state"] = recovery_state
    if result_commit:
        recovery["result_commit"] = result_commit
    if error:
        recovery["error"] = error
    if finalization_state:
        finalization["state"] = finalization_state
    journal["recovery"] = recovery
    journal["finalization"] = finalization
    transaction["git_recovery"] = journal


__all__ = [
    "GIT_TRANSACTION_JOURNAL_SCHEMA_VERSION",
    "GitTransactionRecoveryError",
    "cleanup_created_recovery_refs",
    "establish_git_transaction_recovery",
    "update_git_transaction_journal",
    "verify_git_transaction_recovery",
]
