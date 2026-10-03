from __future__ import annotations

"""FRX-Y11 lock-driven federation repository bootstrap and sync.

Normal federation planning/execution never calls this module.  Repository
source mutation is an explicit user operation that consumes the existing
GIT-FRX-08/FRX-Y03 frozen lock and GIT-FRX-02 recovery primitives.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid
from typing import Any, Iterable, Mapping

from .federation_lock import (
    FederationSourceLock,
    FederationSourceRepository,
    git_remote_locator,
    read_federation_source_lock,
)
from .git_state import GitStateError, collect_git_state
from .git_transaction_journal import (
    GitTransactionRecoveryError,
    establish_git_transaction_recovery,
    verify_git_transaction_recovery,
)
from .xdg import state_dir
from .reservations import repo_key


class FederationSyncError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_git(root: Path, *args: str, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args], text=True, capture_output=True,
            check=False, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FederationSyncError(f"git {' '.join(args)} failed for {root}: {exc}") from exc


def _git(root: Path, *args: str, timeout: float = 30.0) -> str:
    result = _run_git(root, *args, timeout=timeout)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "Git command failed").strip()
        raise FederationSyncError(f"git {' '.join(args)} failed for {root}: {detail}")
    return result.stdout.strip()


def _raw_remote_fingerprint(root: Path) -> str:
    result = _run_git(root, "config", "--get", "remote.origin.url")
    if result.returncode != 0:
        return ""
    raw = result.stdout.strip()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest() if raw else ""


def _sha256_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _locator_digest(locator: str) -> str:
    return hashlib.sha256(locator.encode("utf-8")).hexdigest() if locator else ""


def _locked_by_name(lock: FederationSourceLock) -> dict[str, FederationSourceRepository]:
    return {item.repository: item for item in lock.repositories}


def _selected_names(parent: Any, lock: FederationSourceLock, repositories: Iterable[str]) -> tuple[str, ...]:
    requested = tuple(dict.fromkeys(str(value).strip() for value in repositories if str(value).strip()))
    known = _locked_by_name(lock)
    if requested:
        unknown = [name for name in requested if name not in known]
        if unknown:
            raise FederationSyncError(
                "Frozen federation lock has no repository: " + ", ".join(unknown)
            )
        return requested
    # The lock, not currently available filesystem state, is the source-management set.
    return tuple(item.repository for item in lock.repositories)


def _destination(parent: Any, name: str) -> Path:
    config = parent.federation.repositories.get(name)
    if config is None:
        raise FederationSyncError(f"Repository {name!r} from frozen lock is no longer enrolled")
    root = Path(config.root).expanduser().resolve(strict=False)
    owner = Path(parent.repo_root).expanduser().resolve(strict=False)
    if root == owner:
        raise FederationSyncError(f"Repository {name!r} resolves to the federation owner root")
    return root


def _remote_matches(root: Path, locked: FederationSourceRepository) -> tuple[bool, str]:
    locator = git_remote_locator(root)
    if locked.remote_locator_digest:
        actual = _locator_digest(locator)
        return actual == locked.remote_locator_digest, actual
    actual = _raw_remote_fingerprint(root)
    return actual == locked.repository_remote_fingerprint, actual


def _checkout_identity(root: Path, locked: FederationSourceRepository) -> dict[str, Any]:
    try:
        state = collect_git_state(root)
    except GitStateError as exc:
        return {"ok": False, "reason": f"git-state:{exc}"}
    head = str(state.head.get("sha") or "")
    branch = str(state.head.get("branch") or "")
    head_ref = str(state.head.get("symbolic_ref") or "")
    remote_match, remote_actual = _remote_matches(root, locked)
    config_digest = _sha256_file(root / ".devtool.toml")
    try:
        head_tree = _git(root, "rev-parse", "HEAD^{tree}") if head else ""
    except FederationSyncError:
        head_tree = ""
    reasons: list[str] = []
    if head != locked.start_commit:
        reasons.append("commit")
    if branch != locked.branch or head_ref != locked.head_ref:
        reasons.append("head-ref")
    if locked.config_digest and config_digest != locked.config_digest:
        reasons.append("config")
    if not remote_match:
        reasons.append("remote")
    if locked.head_tree and head_tree != locked.head_tree:
        reasons.append("tree")
    if bool(state.dirty) != bool(locked.dirty):
        reasons.append("dirty-state")
    return {
        "ok": not reasons,
        "reasons": reasons,
        "head": head,
        "branch": branch,
        "head_ref": head_ref,
        "dirty": bool(state.dirty),
        "config_digest": config_digest,
        "head_tree": head_tree,
        "remote_match": remote_match,
        "remote_identity": remote_actual,
    }


def _recovery_journal_path(parent: Any, operation_id: str, repository: str) -> Path:
    return (
        state_dir() / "federation-sync" / repo_key(Path(parent.repo_root)) /
        operation_id / f"{repository}.json"
    )


def _establish_recovery(parent: Any, name: str, root: Path, operation_id: str) -> dict[str, Any]:
    state = collect_git_state(root)
    captured = {
        "is_git_repo": bool(state.available),
        "initial_head": str(state.head.get("sha") or ""),
        "head_ref": str(state.head.get("symbolic_ref") or ""),
    }
    journal, _created = establish_git_transaction_recovery(
        root,
        transaction_id=f"federation-sync-{operation_id}-{name}",
        run_id=f"federation-sync-{operation_id}-{name}",
        intended_mutation="federation-lock-sync",
        worktree_path=root,
        commit_mode="none",
        captured_git_state=captured,
    )
    path = _recovery_journal_path(parent, operation_id, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    journal["federation_sync"] = {"repository": name, "operation_id": operation_id, "created_at": _now()}
    path.write_text(json.dumps(journal, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    evidence = verify_git_transaction_recovery(root, journal)
    return {"path": str(path), "journal": journal, "verification": evidence}


def _commit_exists(root: Path, sha: str) -> bool:
    if not sha:
        return False
    result = _run_git(root, "cat-file", "-e", f"{sha}^{{commit}}")
    return result.returncode == 0


def _is_ancestor(root: Path, older: str, newer: str) -> bool:
    result = _run_git(root, "merge-base", "--is-ancestor", older, newer)
    return result.returncode == 0


@dataclass(frozen=True, slots=True)
class FederationSyncAction:
    repository: str
    root: str
    operation: str
    state: str
    mutation: str = "none"
    reason: str = ""
    commands: tuple[tuple[str, ...], ...] = ()
    applied: bool = False
    verified: bool = False
    recovery: dict[str, Any] = field(default_factory=dict)
    identity: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "root": self.root,
            "operation": self.operation,
            "state": self.state,
            "mutation": self.mutation,
            "reason": self.reason,
            "commands": [list(item) for item in self.commands],
            "applied": self.applied,
            "verified": self.verified,
            "recovery": dict(self.recovery),
            "identity": dict(self.identity),
        }


@dataclass(frozen=True, slots=True)
class FederationSyncReport:
    operation: str
    apply: bool
    lock_digest: str
    lock_path: str
    actions: tuple[FederationSyncAction, ...]
    operation_id: str

    @property
    def ok(self) -> bool:
        terminal_ok = {"verified", "already-exact", "planned", "bootstrapped", "synced"}
        return bool(self.actions) and all(item.state in terminal_ok for item in self.actions)

    @property
    def partial(self) -> bool:
        succeeded = [item for item in self.actions if item.state in {"verified", "already-exact", "bootstrapped", "synced", "planned"}]
        return bool(succeeded) and len(succeeded) != len(self.actions)

    @property
    def status(self) -> str:
        if self.ok:
            return "planned" if not self.apply else "succeeded"
        if self.partial:
            return "partial"
        return "blocked" if any(item.state.startswith("blocked") or item.state == "needs-bootstrap" for item in self.actions) else "failed"

    @property
    def lock_updated(self) -> bool:
        # FRX-Y11 source management never owns lock mutation.
        return False

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "kind": "federation-source-management",
            "operation": self.operation,
            "apply": self.apply,
            "ok": self.ok,
            "partial": self.partial,
            "lock_digest": self.lock_digest,
            "lock_path": self.lock_path,
            "lock_updated": self.lock_updated,
            "operation_id": self.operation_id,
            "actions": [item.as_dict() for item in self.actions],
        }


def _plan_bootstrap(parent: Any, locked: FederationSourceRepository, *, apply: bool, operation_id: str) -> FederationSyncAction:
    name = locked.repository
    root = _destination(parent, name)
    if root.exists():
        try:
            state = collect_git_state(root)
            identity = _checkout_identity(root, locked) if state.available else {"ok": False, "reason": "not-git"}
        except GitStateError as exc:
            identity = {"ok": False, "reason": f"git-state:{exc}"}
        if identity.get("ok"):
            return FederationSyncAction(name, str(root), "bootstrap", "already-exact", verified=True, identity=identity)
        return FederationSyncAction(
            name, str(root), "bootstrap", "blocked-existing",
            reason="destination already exists and does not exactly match the frozen lock",
            identity=identity,
        )
    if locked.source_kind != "git" or locked.dirty or not locked.start_commit:
        return FederationSyncAction(
            name, str(root), "bootstrap", "blocked-unmaterializable",
            reason="frozen source is not a clean Git commit and cannot be reconstructed by clone",
        )
    locator = str(locked.remote_locator or "")
    if not locator or not locked.remote_locator_digest or _locator_digest(locator) != locked.remote_locator_digest:
        return FederationSyncAction(
            name, str(root), "bootstrap", "blocked-missing-source-locator",
            reason="frozen lock predates FRX-Y11 safe clone locator evidence; refreeze while the source checkout is available",
        )
    commands = (
        ("git", "init", str(root)),
        ("git", "-C", str(root), "remote", "add", "origin", locator),
        ("git", "-C", str(root), "fetch", "--prune", "origin"),
        (("git", "-C", str(root), "checkout", "-b", locked.branch, locked.start_commit)
         if locked.branch else ("git", "-C", str(root), "checkout", "--detach", locked.start_commit)),
    )
    if not apply:
        return FederationSyncAction(name, str(root), "bootstrap", "planned", mutation="clone-exact", commands=commands)

    root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".devtool-{name}-bootstrap-", dir=str(root.parent)))
    try:
        _git(staging, "init", "-q")
        _git(staging, "remote", "add", "origin", locator)
        _git(staging, "fetch", "--prune", "origin", timeout=120)
        if not _commit_exists(staging, locked.start_commit):
            raise FederationSyncError(f"locked commit {locked.start_commit} is unavailable from origin")
        if locked.branch:
            _git(staging, "checkout", "-q", "-b", locked.branch, locked.start_commit)
        else:
            _git(staging, "checkout", "-q", "--detach", locked.start_commit)
        if root.exists():
            raise FederationSyncError(f"destination appeared during bootstrap: {root}")
        os.replace(staging, root)
    except Exception as exc:
        # Retain the failed bootstrap staging checkout for recovery/audit instead
        # of deleting provider-owned source evidence from the sync path.
        return FederationSyncAction(
            name, str(root), "bootstrap", "failed", mutation="clone-exact",
            reason=str(exc), commands=commands,
        )
    identity = _checkout_identity(root, locked)
    if not identity.get("ok"):
        return FederationSyncAction(
            name, str(root), "bootstrap", "failed-verification", mutation="clone-exact",
            reason="bootstrapped checkout does not match frozen source identity", commands=commands,
            applied=True, identity=identity,
        )
    return FederationSyncAction(
        name, str(root), "bootstrap", "bootstrapped", mutation="clone-exact",
        commands=commands, applied=True, verified=True, identity=identity,
    )


def _plan_sync(parent: Any, locked: FederationSourceRepository, *, apply: bool, operation_id: str) -> FederationSyncAction:
    name = locked.repository
    root = _destination(parent, name)
    if not root.is_dir():
        return FederationSyncAction(
            name, str(root), "sync", "needs-bootstrap", reason="repository checkout is unavailable"
        )
    try:
        state = collect_git_state(root)
    except GitStateError as exc:
        return FederationSyncAction(name, str(root), "sync", "blocked-not-git", reason=str(exc))
    if locked.dirty:
        return FederationSyncAction(
            name, str(root), "sync", "blocked-frozen-dirty",
            reason="the frozen lock contains dirty worktree state; Git sync cannot reconstruct local dirty bytes",
        )
    if state.dirty:
        return FederationSyncAction(
            name, str(root), "sync", "blocked-dirty",
            reason="repository has staged/unstaged/untracked changes; sync never switches a dirty checkout",
            identity=_checkout_identity(root, locked),
        )
    remote_match, _remote_actual = _remote_matches(root, locked)
    if not remote_match:
        return FederationSyncAction(
            name, str(root), "sync", "blocked-remote-mismatch",
            reason="remote.origin identity differs from the frozen lock",
            identity=_checkout_identity(root, locked),
        )
    current = str(state.head.get("sha") or "")
    branch = str(state.head.get("branch") or "")
    if locked.branch and branch != locked.branch:
        return FederationSyncAction(
            name, str(root), "sync", "blocked-branch-mismatch",
            reason=f"current branch {branch or '<detached>'!r} differs from frozen branch {locked.branch!r}; sync will not switch it",
            identity=_checkout_identity(root, locked),
        )
    if current == locked.start_commit:
        identity = _checkout_identity(root, locked)
        state_name = "already-exact" if identity.get("ok") else "failed-verification"
        return FederationSyncAction(
            name, str(root), "sync", state_name,
            reason="" if identity.get("ok") else "checkout commit matches but frozen source identity does not",
            verified=bool(identity.get("ok")), identity=identity,
        )
    if locked.branch and _commit_exists(root, locked.start_commit) and not _is_ancestor(root, current, locked.start_commit):
        return FederationSyncAction(
            name, str(root), "sync", "blocked-non-fast-forward",
            reason=f"locked commit {locked.start_commit} is not a fast-forward from current HEAD {current}; sync will not reset or rewrite the branch",
            identity=_checkout_identity(root, locked),
        )

    mutation = "fast-forward-to-lock" if locked.branch else "detach-to-lock"
    commands = (
        ("git", "-C", str(root), "fetch", "--prune", "origin"),
        (("git", "-C", str(root), "merge", "--ff-only", locked.start_commit)
         if locked.branch else ("git", "-C", str(root), "checkout", "--detach", locked.start_commit)),
    )
    if not apply:
        return FederationSyncAction(name, str(root), "sync", "planned", mutation=mutation, commands=commands)

    try:
        recovery = _establish_recovery(parent, name, root, operation_id)
        _git(root, "fetch", "--prune", "origin", timeout=120)
        if not _commit_exists(root, locked.start_commit):
            raise FederationSyncError(f"locked commit {locked.start_commit} is unavailable after fetch")
        if locked.branch:
            refreshed = _git(root, "rev-parse", "HEAD")
            if not _is_ancestor(root, refreshed, locked.start_commit):
                raise FederationSyncError(
                    f"locked commit {locked.start_commit} is not a fast-forward from current HEAD {refreshed}; refusing reset"
                )
            _git(root, "merge", "--ff-only", locked.start_commit)
        else:
            _git(root, "checkout", "--detach", locked.start_commit)
        recovery["verification_after"] = verify_git_transaction_recovery(root, recovery["journal"])
    except (FederationSyncError, GitTransactionRecoveryError, GitStateError) as exc:
        return FederationSyncAction(
            name, str(root), "sync", "failed", mutation=mutation,
            reason=str(exc), commands=commands,
            recovery=(recovery if 'recovery' in locals() else {}),
        )
    identity = _checkout_identity(root, locked)
    if not identity.get("ok"):
        return FederationSyncAction(
            name, str(root), "sync", "failed-verification", mutation=mutation,
            reason="updated checkout does not exactly match the frozen lock", commands=commands,
            applied=True, recovery=recovery, identity=identity,
        )
    return FederationSyncAction(
        name, str(root), "sync", "synced", mutation=mutation, commands=commands,
        applied=True, verified=True, recovery=recovery, identity=identity,
    )


def federation_source_management(
    parent: Any,
    *,
    operation: str,
    repositories: Iterable[str] = (),
    apply: bool = False,
) -> FederationSyncReport:
    selected_operation = str(operation or "").strip().lower()
    if selected_operation not in {"bootstrap", "sync"}:
        raise FederationSyncError("operation must be 'bootstrap' or 'sync'")
    lock = read_federation_source_lock(parent)
    if lock is None:
        raise FederationSyncError(
            "No persisted federation source lock; freeze a workflow/group plan before bootstrap/sync"
        )
    names = _selected_names(parent, lock, repositories)
    operation_id = uuid.uuid4().hex[:16]
    by_name = _locked_by_name(lock)
    actions: list[FederationSyncAction] = []
    for name in names:
        locked = by_name[name]
        action = (
            _plan_bootstrap(parent, locked, apply=apply, operation_id=operation_id)
            if selected_operation == "bootstrap"
            else _plan_sync(parent, locked, apply=apply, operation_id=operation_id)
        )
        actions.append(action)
    from .federation_lock import source_lock_path
    return FederationSyncReport(
        selected_operation, bool(apply), lock.lock_digest, str(source_lock_path(parent)),
        tuple(actions), operation_id,
    )


__all__ = [
    "FederationSyncError",
    "FederationSyncAction",
    "FederationSyncReport",
    "federation_source_management",
]
