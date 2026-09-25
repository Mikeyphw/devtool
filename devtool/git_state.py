from __future__ import annotations

"""Authoritative, read-mostly Git repository state for Devtool transactions.

GIT-FRX-01 makes Git source state a first-class execution input.  The model is
intentionally independent from mutation/journaling policy; later roadmap items
consume it to create private refs, transaction worktrees, amend guards, remote
attestation, and federated Git plans.
"""

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any, Mapping, Sequence

from .repository_identity import RepositoryIdentity, build_repository_identity

GIT_STATE_SCHEMA_VERSION = 1


class GitStateError(RuntimeError):
    """Raised when authoritative Git state cannot be collected."""


@dataclass(frozen=True, slots=True)
class GitChange:
    status: str
    path: str
    old_path: str = ""

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"status": self.status, "path": self.path}
        if self.old_path:
            payload["old_path"] = self.old_path
        return payload


@dataclass(frozen=True, slots=True)
class GitRefState:
    name: str
    sha: str = ""
    contains_head: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "sha": self.sha,
            "contains_head": self.contains_head,
        }


@dataclass(frozen=True, slots=True)
class GitWorktreeState:
    root: str
    head: str = ""
    branch: str = ""
    detached: bool = False
    bare: bool = False
    locked: bool = False
    lock_reason: str = ""
    prunable: bool = False
    prune_reason: str = ""
    current: bool = False
    worktree_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "head": self.head,
            "branch": self.branch,
            "detached": self.detached,
            "bare": self.bare,
            "locked": self.locked,
            "lock_reason": self.lock_reason,
            "prunable": self.prunable,
            "prune_reason": self.prune_reason,
            "current": self.current,
            "worktree_id": self.worktree_id,
        }


@dataclass(frozen=True, slots=True)
class GitSubmoduleState:
    path: str
    sha: str = ""
    status: str = "unknown"
    description: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sha": self.sha,
            "status": self.status,
            "description": self.description,
        }


@dataclass(frozen=True, slots=True)
class GitRepositoryState:
    schema_version: int
    kind: str
    available: bool
    repository_id: str
    repository_identity: dict[str, Any]
    requested_root: str
    root: str
    git_dir: str
    git_common_dir: str
    object_format: str
    head: dict[str, Any]
    upstream: dict[str, Any]
    protected_remote_refs: tuple[GitRefState, ...]
    index: dict[str, Any]
    staged: tuple[GitChange, ...]
    unstaged: tuple[GitChange, ...]
    untracked: tuple[str, ...]
    unmerged: tuple[str, ...]
    operations: dict[str, Any]
    worktree: dict[str, Any]
    worktrees: tuple[GitWorktreeState, ...]
    shallow: bool
    partial_clone: bool
    partial_clone_remote: str
    promisor_remotes: tuple[str, ...]
    submodules: tuple[GitSubmoduleState, ...]
    capabilities: dict[str, Any]
    dirty: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "ok": self.available,
            "available": self.available,
            "repository_id": self.repository_id,
            "repository_identity": dict(self.repository_identity),
            "requested_root": self.requested_root,
            "root": self.root,
            "git_dir": self.git_dir,
            "git_common_dir": self.git_common_dir,
            "object_format": self.object_format,
            "head": dict(self.head),
            "upstream": dict(self.upstream),
            "protected_remote_refs": [item.as_dict() for item in self.protected_remote_refs],
            "index": dict(self.index),
            "staged": [item.as_dict() for item in self.staged],
            "unstaged": [item.as_dict() for item in self.unstaged],
            "untracked": list(self.untracked),
            "unmerged": list(self.unmerged),
            "operations": dict(self.operations),
            "worktree": dict(self.worktree),
            "worktrees": [item.as_dict() for item in self.worktrees],
            "shallow": self.shallow,
            "partial_clone": self.partial_clone,
            "partial_clone_remote": self.partial_clone_remote,
            "promisor_remotes": list(self.promisor_remotes),
            "submodules": [item.as_dict() for item in self.submodules],
            "capabilities": dict(self.capabilities),
            "dirty": self.dirty,
        }


def _canonical(path: str | Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _run_git(
    root: Path,
    *args: str,
    timeout: float = 8.0,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    merged_env = None
    if env:
        merged_env = os.environ.copy()
        merged_env.update({str(k): str(v) for k, v in env.items()})
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            capture_output=True,
            check=False,
            timeout=timeout,
            env=merged_env,
        )
    except FileNotFoundError as exc:
        raise GitStateError("Git executable is not available on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitStateError(f"Git state probe timed out: git {' '.join(args)}") from exc


def _git_text(root: Path, *args: str, required: bool = False) -> str:
    result = _run_git(root, *args)
    if result.returncode == 0:
        return result.stdout.strip()
    if required:
        detail = (result.stderr or result.stdout or "Git command failed").strip()
        raise GitStateError(detail)
    return ""


def _require_git_result(result: subprocess.CompletedProcess[str], *, context: str) -> str:
    if result.returncode == 0:
        return result.stdout
    detail = (result.stderr or result.stdout or "Git command failed").strip()
    raise GitStateError(f"{context}: {detail}")


def _optional_git_value(root: Path, *args: str, context: str) -> str:
    """Return an optional Git value while distinguishing absence from failure."""

    result = _run_git(root, *args)
    if result.returncode == 0:
        return result.stdout.strip()
    if result.returncode == 1:
        return ""
    detail = (result.stderr or result.stdout or "Git command failed").strip()
    raise GitStateError(f"{context}: {detail}")


def _resolve_git_path(root: Path, name: str) -> Path | None:
    raw = _git_text(root, "rev-parse", "--git-path", name, required=True)
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve(strict=False)


def _parse_name_status(raw: str) -> tuple[GitChange, ...]:
    if not raw:
        return ()
    tokens = raw.split("\0")
    if tokens and tokens[-1] == "":
        tokens.pop()
    entries: list[GitChange] = []
    i = 0
    while i < len(tokens):
        status = tokens[i]
        i += 1
        if not status or i >= len(tokens):
            break
        code = status[:1]
        if code in {"R", "C"} and i + 1 < len(tokens):
            old_path = tokens[i]
            path = tokens[i + 1]
            i += 2
            entries.append(GitChange(status=status, path=path, old_path=old_path))
        else:
            path = tokens[i]
            i += 1
            entries.append(GitChange(status=status, path=path))
    return tuple(entries)


def _collect_changes(root: Path) -> tuple[tuple[GitChange, ...], tuple[GitChange, ...], tuple[str, ...], tuple[str, ...]]:
    staged_raw = _require_git_result(
        _run_git(root, "diff", "--cached", "--name-status", "-z", "--find-renames"),
        context="Unable to inspect staged Git state",
    )
    staged = _parse_name_status(staged_raw)

    unstaged_raw = _require_git_result(
        _run_git(root, "diff", "--name-status", "-z", "--find-renames"),
        context="Unable to inspect unstaged Git state",
    )
    unstaged = _parse_name_status(unstaged_raw)

    untracked_raw = _require_git_result(
        _run_git(root, "ls-files", "--others", "--exclude-standard", "-z"),
        context="Unable to inspect untracked Git state",
    )
    untracked = tuple(item for item in untracked_raw.split("\0") if item)

    unmerged_raw = _require_git_result(
        _run_git(root, "diff", "--name-only", "--diff-filter=U", "-z"),
        context="Unable to inspect unmerged Git state",
    )
    unmerged = tuple(item for item in unmerged_raw.split("\0") if item)
    return staged, unstaged, untracked, unmerged


def _collect_head(root: Path) -> dict[str, Any]:
    symbolic_result = _run_git(root, "symbolic-ref", "--quiet", "HEAD")
    if symbolic_result.returncode == 0:
        symbolic = symbolic_result.stdout.strip()
        branch = symbolic.removeprefix("refs/heads/") if symbolic.startswith("refs/heads/") else symbolic
        sha_result = _run_git(root, "rev-parse", "--verify", "HEAD")
        if sha_result.returncode == 0:
            sha = sha_result.stdout.strip()
            unborn = False
        else:
            # A symbolic HEAD whose target ref does not exist is the normal
            # unborn-branch state. If the ref exists but HEAD still cannot be
            # resolved, repository state is inconsistent and must fail closed.
            ref_result = _run_git(root, "show-ref", "--verify", "--quiet", symbolic)
            if ref_result.returncode == 1:
                sha = ""
                unborn = True
            elif ref_result.returncode == 0:
                detail = (sha_result.stderr or sha_result.stdout or "HEAD could not be resolved").strip()
                raise GitStateError(f"Unable to resolve Git HEAD: {detail}")
            else:
                detail = (ref_result.stderr or ref_result.stdout or "HEAD ref could not be inspected").strip()
                raise GitStateError(f"Unable to inspect Git HEAD ref: {detail}")
        return {
            "sha": sha,
            "symbolic_ref": symbolic,
            "branch": branch,
            "detached": False,
            "unborn": unborn,
        }

    if symbolic_result.returncode != 1:
        detail = (symbolic_result.stderr or symbolic_result.stdout or "HEAD symbolic state could not be inspected").strip()
        raise GitStateError(f"Unable to inspect Git HEAD: {detail}")

    sha = _require_git_result(
        _run_git(root, "rev-parse", "--verify", "HEAD"),
        context="Unable to resolve detached Git HEAD",
    ).strip()
    return {
        "sha": sha,
        "symbolic_ref": "",
        "branch": "",
        "detached": True,
        "unborn": False,
    }


def _collect_upstream(root: Path, head: Mapping[str, Any]) -> dict[str, Any]:
    branch = str(head.get("branch") or "")
    if not branch:
        return {
            "configured": False,
            "partial_configuration": False,
            "resolved": False,
            "name": "",
            "short_name": "",
            "sha": "",
            "remote": "",
            "merge_ref": "",
        }

    remote = _optional_git_value(
        root, "config", "--get", f"branch.{branch}.remote",
        context="Unable to inspect Git upstream remote configuration",
    )
    merge_ref = _optional_git_value(
        root, "config", "--get", f"branch.{branch}.merge",
        context="Unable to inspect Git upstream merge configuration",
    )
    configured = bool(remote and merge_ref)
    partial_configuration = bool(remote) != bool(merge_ref)
    full = _git_text(root, "rev-parse", "--symbolic-full-name", "@{upstream}") if configured else ""
    short_name = _git_text(root, "rev-parse", "--abbrev-ref", "@{upstream}") if full else ""
    sha = _git_text(root, "rev-parse", "--verify", "@{upstream}") if full else ""
    return {
        "configured": configured,
        "partial_configuration": partial_configuration,
        "resolved": bool(full and sha),
        "name": full,
        "short_name": short_name,
        "sha": sha,
        "remote": remote,
        "merge_ref": merge_ref,
    }


def _collect_protected_remote_refs(root: Path, head: Mapping[str, Any], upstream: Mapping[str, Any]) -> tuple[GitRefState, ...]:
    refs: dict[str, GitRefState] = {}
    upstream_name = str(upstream.get("name") or "")
    if upstream_name:
        refs[upstream_name] = GitRefState(
            name=upstream_name,
            sha=str(upstream.get("sha") or ""),
            contains_head=bool(head.get("sha")),
        )
    if not head.get("sha"):
        return tuple(refs.values())
    result = _run_git(
        root,
        "for-each-ref",
        "--contains",
        str(head["sha"]),
        "--format=%(refname)%00%(objectname)",
        "refs/remotes",
    )
    remote_ref_raw = _require_git_result(result, context="Unable to inspect remote-tracking Git refs")
    for line in remote_ref_raw.splitlines():
        if "\0" not in line:
            continue
        name, sha = line.split("\0", 1)
        if name:
            refs[name] = GitRefState(name=name, sha=sha, contains_head=True)
    return tuple(refs[name] for name in sorted(refs))


def _index_state(
    root: Path,
    unmerged: Sequence[str],
    *,
    source_object_dir: Path,
    source_index_file: Path,
) -> dict[str, Any]:
    tree_sha = ""
    tree_error = ""

    # ``git write-tree`` is the canonical way to obtain the exact tree object
    # represented by the index. It can mutate *both* the object database and
    # the index's cache-tree extension, so isolate both surfaces. Existing
    # blobs/trees remain readable through the real object store as an alternate.
    with tempfile.TemporaryDirectory(prefix="devtool-git-index-tree-") as tmp:
        tmp_root = Path(tmp)
        isolated_objects = tmp_root / "objects"
        isolated_index = tmp_root / "index"
        isolated_objects.mkdir(parents=True, exist_ok=True)
        alternates = [str(source_object_dir)]
        inherited_alternates = str(os.environ.get("GIT_ALTERNATE_OBJECT_DIRECTORIES", "") or "").strip()
        if inherited_alternates:
            alternates.append(inherited_alternates)
        isolated_env = {
            "GIT_OBJECT_DIRECTORY": str(isolated_objects),
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": os.pathsep.join(alternates),
            "GIT_INDEX_FILE": str(isolated_index),
        }
        if source_index_file.is_file():
            shutil.copyfile(source_index_file, isolated_index)
        else:
            initialize = _run_git(root, "read-tree", "--empty", env=isolated_env)
            if initialize.returncode != 0:
                tree_error = (
                    initialize.stderr or initialize.stdout or "unable to initialize isolated empty index"
                ).strip()

        result = (
            _run_git(root, "write-tree", env=isolated_env)
            if not tree_error
            else None
        )
    if result is not None and result.returncode == 0:
        tree_sha = result.stdout.strip()
    elif result is not None:
        tree_error = (result.stderr or result.stdout or "unable to materialize index tree").strip()

    entries = _run_git(root, "ls-files", "--stage", "-z")
    raw = _require_git_result(entries, context="Unable to inspect Git index entries")
    entries_fingerprint = hashlib.sha256(raw.encode("utf-8", errors="surrogateescape")).hexdigest()
    return {
        "tree_sha": tree_sha,
        "tree_error": tree_error,
        "entries_fingerprint": entries_fingerprint,
        "unmerged": bool(unmerged),
    }


def _operation_state(root: Path) -> dict[str, Any]:
    probes: dict[str, tuple[str, ...]] = {
        "merge": ("MERGE_HEAD",),
        "rebase": ("rebase-merge", "rebase-apply"),
        "cherry_pick": ("CHERRY_PICK_HEAD",),
        "revert": ("REVERT_HEAD",),
        "bisect": ("BISECT_LOG", "BISECT_START"),
    }
    payload: dict[str, Any] = {}
    active: list[str] = []
    for name, candidates in probes.items():
        paths: list[str] = []
        is_active = False
        for candidate in candidates:
            path = _resolve_git_path(root, candidate)
            if path is not None and path.exists():
                is_active = True
                paths.append(str(path))
        payload[name] = {"active": is_active, "paths": paths}
        if is_active:
            active.append(name)
    payload["active"] = active
    return payload


def _worktree_admin_token(root: Path, common_dir: Path) -> str:
    dotgit = root / ".git"
    if dotgit.is_dir():
        return "main"
    if dotgit.is_file():
        try:
            first_line = dotgit.read_text(encoding="utf-8", errors="surrogateescape").splitlines()[0]
        except (OSError, IndexError):
            first_line = ""
        if first_line.lower().startswith("gitdir:"):
            raw = first_line.split(":", 1)[1].strip()
            admin = Path(raw).expanduser()
            if not admin.is_absolute():
                admin = root / admin
            admin = admin.resolve(strict=False)
            if admin == common_dir:
                return "main"
            try:
                relative = admin.relative_to(common_dir)
                return f"linked:{relative.as_posix()}"
            except ValueError:
                return f"linked:{admin}"
    return f"path:{root}"


def _worktree_id(repository_id: str, root: Path, common_dir: Path) -> str:
    token = _worktree_admin_token(root, common_dir)
    return hashlib.sha256(f"{repository_id}\n{token}".encode("utf-8")).hexdigest()


def _parse_worktree_porcelain(
    raw: str, *, current_root: Path, repository_id: str, common_dir: Path
) -> tuple[GitWorktreeState, ...]:
    blocks = [block for block in raw.strip().split("\n\n") if block.strip()]
    states: list[GitWorktreeState] = []
    for block in blocks:
        values: dict[str, str] = {}
        flags: set[str] = set()
        for line in block.splitlines():
            if not line:
                continue
            key, sep, value = line.partition(" ")
            if sep:
                values[key] = value
            else:
                flags.add(key)
        root_text = values.get("worktree", "")
        if not root_text:
            continue
        root = _canonical(root_text)
        branch = values.get("branch", "")
        if branch.startswith("refs/heads/"):
            branch = branch.removeprefix("refs/heads/")
        worktree_id = _worktree_id(repository_id, root, common_dir)
        states.append(
            GitWorktreeState(
                root=str(root),
                head=values.get("HEAD", ""),
                branch=branch,
                detached="detached" in flags,
                bare="bare" in flags,
                locked="locked" in flags or "locked" in values,
                lock_reason=values.get("locked", ""),
                prunable="prunable" in flags or "prunable" in values,
                prune_reason=values.get("prunable", ""),
                current=root == current_root,
                worktree_id=worktree_id,
            )
        )
    return tuple(states)


def _collect_worktrees(
    root: Path, repository_id: str, *, git_dir: Path, common_dir: Path
) -> tuple[tuple[GitWorktreeState, ...], bool]:
    result = _run_git(root, "worktree", "list", "--porcelain")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "Git worktree probe failed").strip()
        raise GitStateError(f"Unable to inspect Git worktrees: {detail}")

    states = list(
        _parse_worktree_porcelain(
            result.stdout, current_root=root, repository_id=repository_id, common_dir=common_dir
        )
    )

    # Submodule checkouts use a gitfile whose target is also the common Git
    # directory. Git's `worktree list --porcelain` reports that administrative
    # directory as the primary worktree path even though commands execute in
    # `core.worktree` (the checked-out submodule path). Normalize only that
    # specific representation so Devtool's worktree model describes the actual
    # checkout while retaining Git's own HEAD/branch metadata.
    if git_dir == common_dir and (root / ".git").is_file():
        replacement_index = next(
            (idx for idx, item in enumerate(states) if _canonical(item.root) == common_dir),
            None,
        )
        if replacement_index is not None:
            item = states[replacement_index]
            states[replacement_index] = GitWorktreeState(
                root=str(root),
                head=item.head,
                branch=item.branch,
                detached=item.detached,
                bare=item.bare,
                locked=item.locked,
                lock_reason=item.lock_reason,
                prunable=item.prunable,
                prune_reason=item.prune_reason,
                current=True,
                worktree_id=_worktree_id(repository_id, root, common_dir),
            )

    return tuple(states), True


def _collect_submodules(root: Path) -> tuple[GitSubmoduleState, ...]:
    if not (root / ".gitmodules").is_file():
        return ()
    result = _run_git(root, "submodule", "status", "--recursive", timeout=20)
    if result.returncode not in {0, 1}:
        detail = (result.stderr or result.stdout or "Git submodule probe failed").strip()
        raise GitStateError(f"Unable to inspect Git submodules: {detail}")
    states: list[GitSubmoduleState] = []
    status_names = {
        "-": "uninitialized",
        "+": "different",
        "U": "conflict",
        " ": "initialized",
    }
    for line in result.stdout.splitlines():
        if not line:
            continue
        marker = line[0]
        rest = line[1:].strip()
        match = re.match(r"(?P<sha>[0-9a-fA-F]{40,64})\s+(?P<tail>.+)$", rest)
        if not match:
            continue
        tail = match.group("tail")
        description = ""
        path_text = tail
        if tail.endswith(")") and " (" in tail:
            path_text, description = tail.rsplit(" (", 1)
            description = description[:-1]
        states.append(
            GitSubmoduleState(
                path=path_text,
                sha=match.group("sha"),
                status=status_names.get(marker, "unknown"),
                description=description,
            )
        )
    return tuple(states)


def _collect_partial_clone(root: Path) -> tuple[bool, str, tuple[str, ...]]:
    extension = _optional_git_value(
        root, "config", "--get", "extensions.partialClone",
        context="Unable to inspect partial-clone configuration",
    )
    result = _run_git(root, "config", "--get-regexp", r"^remote\..*\.promisor$")
    promisor: list[str] = []
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            key, _, value = line.partition(" ")
            if str(value).strip().lower() not in {"true", "1", "yes", "on"}:
                continue
            match = re.match(r"remote\.(.+)\.promisor$", key)
            if match:
                promisor.append(match.group(1))
    elif result.returncode != 1:
        detail = (result.stderr or result.stdout or "Git promisor configuration probe failed").strip()
        raise GitStateError(f"Unable to inspect partial-clone promisor remotes: {detail}")
    promisor_tuple = tuple(sorted(set(promisor)))
    return bool(extension or promisor_tuple), extension, promisor_tuple


def _object_format(root: Path) -> tuple[str, bool]:
    result = _run_git(root, "rev-parse", "--show-object-format=storage")
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip(), True
    result = _run_git(root, "rev-parse", "--show-object-format")
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip().split()[0], True

    # Older Git versions predate the rev-parse object-format probe. SHA-1 is
    # the default unless the repository explicitly declares another format.
    configured = _optional_git_value(
        root, "config", "--get", "extensions.objectFormat",
        context="Unable to inspect Git object format",
    )
    return (configured or "sha1"), False


def _repository_id(identity: RepositoryIdentity, object_format: str) -> str:
    # git_common_fingerprint is based on the shared Git common-dir identity and
    # therefore remains stable across linked worktrees of the same local
    # repository.  Fall back to the FRX identity for non-standard repositories.
    common = identity.git_common_fingerprint or identity.fingerprint
    basis = "\n".join(
        [
            f"schema={GIT_STATE_SCHEMA_VERSION}",
            f"common={common}",
            f"object_format={object_format}",
        ]
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def collect_git_state(repo_root: str | Path) -> GitRepositoryState:
    """Collect one authoritative Git-state snapshot for ``repo_root``.

    The probe does not move refs, branches, HEAD, the index, or repository
    objects. The canonical index tree SHA is computed with ``git write-tree``
    against an isolated temporary index and object database while the real
    object store is mounted read-only as an alternate.
    """

    if not shutil.which("git"):
        raise GitStateError("Git executable is not available on PATH.")

    requested_root = _canonical(repo_root)
    probe = _run_git(requested_root, "rev-parse", "--show-toplevel")
    if probe.returncode != 0 or not probe.stdout.strip():
        detail = (probe.stderr or probe.stdout or "not a Git working tree").strip()
        raise GitStateError(f"Git repository unavailable at {requested_root}: {detail}")
    root = _canonical(probe.stdout.strip())

    identity = build_repository_identity(root)
    object_format, object_format_supported = _object_format(root)
    repository_id = _repository_id(identity, object_format)

    git_dir_text = _git_text(root, "rev-parse", "--absolute-git-dir", required=True)
    git_dir = _canonical(git_dir_text)
    common_text = _git_text(root, "rev-parse", "--git-common-dir", required=True)
    common_path = Path(common_text).expanduser()
    if not common_path.is_absolute():
        common_path = root / common_path
    git_common_dir = common_path.resolve(strict=False)

    head = _collect_head(root)
    upstream = _collect_upstream(root, head)
    protected_remote_refs = _collect_protected_remote_refs(root, head, upstream)
    staged, unstaged, untracked, unmerged = _collect_changes(root)
    index_path = _resolve_git_path(root, "index")
    if index_path is None:
        raise GitStateError("Unable to resolve Git index path")
    index = _index_state(
        root,
        unmerged,
        source_object_dir=git_common_dir / "objects",
        source_index_file=index_path,
    )
    operations = _operation_state(root)
    worktrees, worktree_porcelain = _collect_worktrees(
        root, repository_id, git_dir=git_dir, common_dir=git_common_dir
    )

    is_main_worktree = git_dir == git_common_dir
    current_worktree_id = _worktree_id(repository_id, root, git_common_dir)
    current_from_list = next((item for item in worktrees if item.current), None)
    worktree = {
        "id": current_from_list.worktree_id if current_from_list else current_worktree_id,
        "root": str(root),
        "is_main": is_main_worktree,
        "is_linked": not is_main_worktree,
        "git_dir": str(git_dir),
        "git_common_dir": str(git_common_dir),
    }

    shallow_result = _run_git(root, "rev-parse", "--is-shallow-repository")
    shallow_text = _require_git_result(shallow_result, context="Unable to inspect shallow-repository state").strip()
    shallow = shallow_text.lower() == "true"
    partial_clone, partial_clone_remote, promisor_remotes = _collect_partial_clone(root)
    submodules = _collect_submodules(root)

    git_version = _git_text(root, "version", required=True)
    porcelain_v2 = _run_git(root, "status", "--porcelain=v2", "--untracked-files=no").returncode == 0
    capabilities = {
        "git_executable": shutil.which("git") or "",
        "git_version": git_version,
        "object_format_probe": object_format_supported,
        "porcelain_v2": porcelain_v2,
        "worktree_porcelain": worktree_porcelain,
    }

    dirty = bool(staged or unstaged or untracked or unmerged)
    return GitRepositoryState(
        schema_version=GIT_STATE_SCHEMA_VERSION,
        kind="git-state",
        available=True,
        repository_id=repository_id,
        repository_identity=identity.as_dict(),
        requested_root=str(requested_root),
        root=str(root),
        git_dir=str(git_dir),
        git_common_dir=str(git_common_dir),
        object_format=object_format,
        head=head,
        upstream=upstream,
        protected_remote_refs=protected_remote_refs,
        index=index,
        staged=staged,
        unstaged=unstaged,
        untracked=untracked,
        unmerged=unmerged,
        operations=operations,
        worktree=worktree,
        worktrees=worktrees,
        shallow=shallow,
        partial_clone=partial_clone,
        partial_clone_remote=partial_clone_remote,
        promisor_remotes=promisor_remotes,
        submodules=submodules,
        capabilities=capabilities,
        dirty=dirty,
    )


def render_git_state_text(state: GitRepositoryState) -> list[tuple[str, str]]:
    head = state.head
    upstream = state.upstream
    operations = state.operations
    active_ops = operations.get("active") if isinstance(operations.get("active"), list) else []
    if upstream.get("resolved"):
        upstream_text = str(upstream.get("short_name") or upstream.get("name") or "none")
    elif upstream.get("configured"):
        remote = str(upstream.get("remote") or "?")
        merge_ref = str(upstream.get("merge_ref") or "?")
        upstream_text = f"{remote}:{merge_ref} (unresolved)"
    else:
        upstream_text = "none"
    return [
        ("repository_id", state.repository_id),
        ("root", state.root),
        ("head", str(head.get("sha") or "<unborn>")),
        ("branch", str(head.get("branch") or ("<detached>" if head.get("detached") else "-"))),
        ("upstream", upstream_text),
        ("index_tree", str(state.index.get("tree_sha") or "unavailable")),
        ("staged", str(len(state.staged))),
        ("unstaged", str(len(state.unstaged))),
        ("untracked", str(len(state.untracked))),
        ("unmerged", str(len(state.unmerged))),
        ("dirty", "yes" if state.dirty else "no"),
        ("operations", ", ".join(str(item) for item in active_ops) or "none"),
        ("worktree", "linked" if state.worktree.get("is_linked") else "main"),
        ("worktrees", str(len(state.worktrees))),
        ("shallow", "yes" if state.shallow else "no"),
        ("partial_clone", "yes" if state.partial_clone else "no"),
        ("object_format", state.object_format),
    ]


__all__ = [
    "GIT_STATE_SCHEMA_VERSION",
    "GitChange",
    "GitRefState",
    "GitRepositoryState",
    "GitStateError",
    "GitSubmoduleState",
    "GitWorktreeState",
    "collect_git_state",
    "render_git_state_text",
]
