from __future__ import annotations

"""GIT-FRX-08 + FRX-Y03 frozen multi-repository source identity.

The lock is deliberately split into three identities:

* ``lock_digest``: portable strict source identity.  It freezes commit/branch/
  upstream, exact index/worktree content, child config content and graph shape
  without embedding local filesystem paths.
* ``local_digest``: adds local repository/worktree identity for same-host
  execution drift detection.
* ``equivalence_digest``: ignores commit/ref placement but requires equivalent
  source/config content and graph semantics.  This is the only identity used by
  the explicit ``allow-if-equivalent`` policy.

The lock never clones, fetches, checks out, resets, or otherwise updates a
repository.  FRX-Y11 owns bootstrap/sync later in the roadmap.
"""

from dataclasses import dataclass, field, replace
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from urllib.parse import urlsplit, urlunsplit
from typing import Any, Iterable, Mapping, TYPE_CHECKING

from .federation_discovery import resolve_federated_repository
from .git_state import GitStateError, collect_git_state
from .reservations import repo_key
from .xdg import state_dir

if TYPE_CHECKING:
    from .federation_plan import FederatedPlanGraph
    from .models import ProjectConfig


FEDERATION_SOURCE_LOCK_SCHEMA_VERSION = 1
DRIFT_POLICIES = ("fail", "replan", "allow-if-equivalent")


class FederationSourceLockError(RuntimeError):
    pass


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sanitize_git_remote_locator(value: str) -> str:
    """Return a clone-capable locator without embedded credentials/query secrets."""
    raw = str(value or "").strip()
    if not raw or any(ch in raw for ch in ("\x00", "\n", "\r")):
        return ""
    # SCP-style SSH locators are already credential-token free.
    if "://" not in raw and ":" in raw and not raw.startswith(("/", "./", "../")):
        return raw
    if "://" not in raw:
        return raw
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https", "ssh", "git", "file"}:
        return ""
    host = parsed.hostname or ""
    if scheme == "file":
        netloc = parsed.netloc
    else:
        if not host:
            return ""
        user = parsed.username or ""
        # Passwords/tokens are never persisted. HTTPS credentials are delegated
        # to Git's normal credential helper, while SSH usernames remain useful.
        if scheme in {"http", "https"}:
            user = ""
        port = f":{parsed.port}" if parsed.port else ""
        netloc = f"{user + '@' if user else ''}{host}{port}"
    return urlunsplit((scheme, netloc, parsed.path, "", ""))


def git_remote_locator(root: str | Path) -> str:
    import subprocess
    try:
        result = subprocess.run(
            ["git", "-C", str(Path(root)), "config", "--get", "remote.origin.url"],
            text=True, capture_output=True, check=False, timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return sanitize_git_remote_locator(result.stdout.strip()) if result.returncode == 0 else ""


def _path_identity(root: Path, relative: str) -> dict[str, Any]:
    path = root / relative
    try:
        metadata = path.lstat()
    except OSError:
        return {"path": relative, "kind": "missing", "mode": "", "sha256": ""}
    mode = stat.S_IMODE(metadata.st_mode)
    if stat.S_ISLNK(metadata.st_mode):
        target = os.readlink(path)
        return {
            "path": relative,
            "kind": "symlink",
            "mode": f"{mode:04o}",
            "sha256": _sha256_bytes(target.encode("utf-8", "surrogatepass")),
            "target": target,
        }
    if stat.S_ISREG(metadata.st_mode):
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return {
            "path": relative,
            "kind": "file",
            "mode": f"{mode:04o}",
            "sha256": digest.hexdigest(),
        }
    if stat.S_ISDIR(metadata.st_mode):
        return {"path": relative, "kind": "directory", "mode": f"{mode:04o}", "sha256": ""}
    return {"path": relative, "kind": "other", "mode": f"{mode:04o}", "sha256": ""}


def _dirty_identity(root: Path, state: Any) -> dict[str, Any]:
    staged = [item.as_dict() for item in state.staged]
    unstaged = [item.as_dict() for item in state.unstaged]
    unmerged = sorted(str(item) for item in state.unmerged)
    untracked = sorted(str(item) for item in state.untracked)
    worktree_paths: set[str] = set(untracked)
    for item in state.unstaged:
        worktree_paths.add(str(item.path))
        if item.old_path:
            worktree_paths.add(str(item.old_path))
    for item in state.unmerged:
        worktree_paths.add(str(item))
    worktree = [_path_identity(root, path) for path in sorted(worktree_paths)]
    payload = {
        "staged": staged,
        "unstaged": unstaged,
        "untracked": untracked,
        "unmerged": unmerged,
        "worktree_paths": worktree,
    }
    payload["digest"] = _digest(payload)
    return payload


def _config_digest(path: str | Path | None) -> str:
    if not path:
        return ""
    candidate = Path(path)
    try:
        data = candidate.read_bytes()
    except OSError:
        return ""
    return _sha256_bytes(data)


def _head_tree(root: Path, *, unborn: bool) -> str:
    if unborn:
        return ""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD^{tree}"],
            text=True,
            capture_output=True,
            check=False,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FederationSourceLockError(f"Unable to resolve HEAD tree for {root}: {exc}") from exc
    if result.returncode != 0 or not result.stdout.strip():
        detail = (result.stderr or result.stdout or "git rev-parse failed").strip()
        raise FederationSourceLockError(f"Unable to resolve HEAD tree for {root}: {detail}")
    return result.stdout.strip()


def _load_child(parent: "ProjectConfig", repository: str):
    from .config import load_project_config

    resolution = resolve_federated_repository(parent, repository)
    if not resolution.available:
        raise FederationSourceLockError(
            f"Federation source lock requires unavailable repository {repository!r}: {resolution.root}"
        )
    try:
        child = load_project_config(
            resolution.root,
            resolution.config_path or None,
            target_name="",
            allow_ambiguous_target=True,
            allow_ambiguous_project_kind=True,
            allow_parent_config=False,
        )
    except SystemExit as exc:
        raise FederationSourceLockError(f"Cannot load federation repository {repository!r}: {exc}") from exc
    return resolution, child


def _repository_dependency_payload(plan: "FederatedPlanGraph", repository: str) -> list[dict[str, Any]]:
    by_key = plan.by_key
    payload: list[dict[str, Any]] = []
    for node in sorted(
        (item for item in plan.nodes if not item.synthetic and item.repository == repository),
        key=lambda item: (item.index, item.key),
    ):
        payload.append(
            {
                "node": node.key,
                "depends_on": list(node.depends_on),
                "cross_repository_dependencies": [
                    dependency
                    for dependency in node.depends_on
                    if dependency in by_key and by_key[dependency].repository != repository
                ],
            }
        )
    return payload


def _expected_result_ref(repository_id: str, repository: str) -> str:
    token = repository_id or hashlib.sha256(repository.encode("utf-8")).hexdigest()
    return f"refs/devtool/federations/{{federation_run_id}}/repos/{token}/result"


def _filesystem_tree_identity(root: Path) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for base, dirs, files in os.walk(root, followlinks=False):
        base_path = Path(base)
        dirs[:] = sorted(name for name in dirs if name not in {".git", ".devtool"})
        for name in sorted(files):
            path = base_path / name
            rel = path.relative_to(root).as_posix()
            entries.append(_path_identity(root, rel))
        for name in sorted(dirs):
            path = base_path / name
            if path.is_symlink():
                rel = path.relative_to(root).as_posix()
                entries.append(_path_identity(root, rel))
    return {"entries": entries, "digest": _digest(entries)}


@dataclass(frozen=True, slots=True)
class FederationSourceRepository:
    repository: str
    source_kind: str
    git_available: bool
    filesystem_tree_digest: str
    repository_root: str
    repository_id: str
    repository_fingerprint: str
    repository_remote_fingerprint: str
    config_digest: str
    start_commit: str
    unborn: bool
    head_ref: str
    branch: str
    upstream: dict[str, Any]
    head_tree: str
    index_tree: str
    index_fingerprint: str
    dirty: bool
    dirty_identity: dict[str, Any]
    worktree_state: dict[str, Any]
    commit_policy: str
    expected_result_ref: str
    node_dependencies: tuple[dict[str, Any], ...] = ()
    source_digest: str = ""
    local_digest: str = ""
    equivalence_digest: str = ""
    remote_locator: str = ""
    remote_locator_digest: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "source_kind": self.source_kind,
            "git_available": self.git_available,
            "filesystem_tree_digest": self.filesystem_tree_digest,
            "repository_root": self.repository_root,
            "repository_id": self.repository_id,
            "repository_fingerprint": self.repository_fingerprint,
            "repository_remote_fingerprint": self.repository_remote_fingerprint,
            "config_digest": self.config_digest,
            "start_commit": self.start_commit,
            "unborn": self.unborn,
            "head_ref": self.head_ref,
            "branch": self.branch,
            "upstream": dict(self.upstream),
            "head_tree": self.head_tree,
            "index_tree": self.index_tree,
            "index_fingerprint": self.index_fingerprint,
            "dirty": self.dirty,
            "dirty_identity": dict(self.dirty_identity),
            "worktree_state": dict(self.worktree_state),
            "commit_policy": self.commit_policy,
            "expected_result_ref": self.expected_result_ref,
            "node_dependencies": [dict(item) for item in self.node_dependencies],
            "source_digest": self.source_digest,
            "local_digest": self.local_digest,
            "equivalence_digest": self.equivalence_digest,
            "remote_locator": self.remote_locator,
            "remote_locator_digest": self.remote_locator_digest,
        }


@dataclass(frozen=True, slots=True)
class FederationSourceLock:
    schema_version: int
    kind: str
    subject_kind: str
    workflow: str
    group: str
    drift_policy: str
    graph_digest: str
    repositories: tuple[FederationSourceRepository, ...]
    lock_digest: str
    local_digest: str
    equivalence_digest: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "subject_kind": self.subject_kind,
            "workflow": self.workflow,
            "group": self.group,
            "drift_policy": self.drift_policy,
            "graph_digest": self.graph_digest,
            "repositories": [item.as_dict() for item in self.repositories],
            "lock_digest": self.lock_digest,
            "local_digest": self.local_digest,
            "equivalence_digest": self.equivalence_digest,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FederationSourceLock":
        repos = []
        for raw in value.get("repositories") or []:
            if not isinstance(raw, Mapping):
                continue
            repos.append(
                FederationSourceRepository(
                    repository=str(raw.get("repository") or ""),
                    source_kind=str(raw.get("source_kind") or ("git" if raw.get("git_available", True) else "filesystem")),
                    git_available=bool(raw.get("git_available", True)),
                    filesystem_tree_digest=str(raw.get("filesystem_tree_digest") or ""),
                    repository_root=str(raw.get("repository_root") or ""),
                    repository_id=str(raw.get("repository_id") or ""),
                    repository_fingerprint=str(raw.get("repository_fingerprint") or ""),
                    repository_remote_fingerprint=str(raw.get("repository_remote_fingerprint") or ""),
                    config_digest=str(raw.get("config_digest") or ""),
                    start_commit=str(raw.get("start_commit") or ""),
                    unborn=bool(raw.get("unborn", False)),
                    head_ref=str(raw.get("head_ref") or ""),
                    branch=str(raw.get("branch") or ""),
                    upstream=dict(raw.get("upstream") or {}),
                    head_tree=str(raw.get("head_tree") or ""),
                    index_tree=str(raw.get("index_tree") or ""),
                    index_fingerprint=str(raw.get("index_fingerprint") or ""),
                    dirty=bool(raw.get("dirty", False)),
                    dirty_identity=dict(raw.get("dirty_identity") or {}),
                    worktree_state=dict(raw.get("worktree_state") or {}),
                    commit_policy=str(raw.get("commit_policy") or "none"),
                    expected_result_ref=str(raw.get("expected_result_ref") or ""),
                    node_dependencies=tuple(dict(item) for item in (raw.get("node_dependencies") or []) if isinstance(item, Mapping)),
                    source_digest=str(raw.get("source_digest") or ""),
                    local_digest=str(raw.get("local_digest") or ""),
                    equivalence_digest=str(raw.get("equivalence_digest") or ""),
                    remote_locator=str(raw.get("remote_locator") or ""),
                    remote_locator_digest=str(raw.get("remote_locator_digest") or ""),
                )
            )
        return cls(
            schema_version=int(value.get("schema_version") or FEDERATION_SOURCE_LOCK_SCHEMA_VERSION),
            kind=str(value.get("kind") or "federation-source-lock"),
            subject_kind=str(value.get("subject_kind") or "workflow"),
            workflow=str(value.get("workflow") or ""),
            group=str(value.get("group") or ""),
            drift_policy=str(value.get("drift_policy") or "fail"),
            graph_digest=str(value.get("graph_digest") or ""),
            repositories=tuple(repos),
            lock_digest=str(value.get("lock_digest") or ""),
            local_digest=str(value.get("local_digest") or ""),
            equivalence_digest=str(value.get("equivalence_digest") or ""),
        )


def _plan_graph_digest(plan: "FederatedPlanGraph") -> str:
    payload = {
        "subject_kind": "group" if plan.group else "workflow",
        "group": plan.group,
        "workflow": plan.workflow,
        "repositories": list(plan.repositories),
        "nodes": [
            {
                "key": node.key,
                "repository": node.repository,
                "target": node.target,
                "contract_type": str(node.metadata.get("contract_type") or "workflow"),
                "contract_name": str(node.metadata.get("contract_name") or node.workflow),
                "depends_on": list(node.depends_on),
                "synthetic": node.synthetic,
            }
            for node in plan.topological_order()
        ],
        "capability_edges": list((plan.capability_graph or {}).get("edges") or []),
    }
    return _digest(payload)


def _build_repository_source(parent: "ProjectConfig", plan: "FederatedPlanGraph", repository: str) -> FederationSourceRepository:
    resolution, child = _load_child(parent, repository)
    root = Path(resolution.root).resolve(strict=False)
    identity = child.repository_identity.as_dict() if child.repository_identity else {}
    config_digest = _config_digest(resolution.config_path or getattr(child, "config_path", None))
    remote_fingerprint = str(identity.get("git_remote_fingerprint") or "")
    remote_locator = git_remote_locator(root)
    remote_locator_digest = hashlib.sha256(remote_locator.encode("utf-8")).hexdigest() if remote_locator else ""
    node_dependencies = tuple(_repository_dependency_payload(plan, repository))

    try:
        state = collect_git_state(root)
    except GitStateError:
        tree = _filesystem_tree_identity(root)
        portable_strict = {
            "repository": repository,
            "source_kind": "filesystem",
            "config_digest": config_digest,
            "filesystem_tree_digest": tree["digest"],
            "commit_policy": "none",
            "expected_result_ref": _expected_result_ref("", repository),
            "node_dependencies": list(node_dependencies),
        }
        local = {
            "portable_source_digest": _digest(portable_strict),
            "repository_root": str(root),
            "repository_fingerprint": str(identity.get("fingerprint") or ""),
        }
        equivalence = {
            "repository": repository,
            "source_kind": "filesystem",
            "config_digest": config_digest,
            "filesystem_tree_digest": tree["digest"],
            "node_dependencies": list(node_dependencies),
        }
        return FederationSourceRepository(
            repository=repository,
            source_kind="filesystem",
            git_available=False,
            filesystem_tree_digest=str(tree["digest"]),
            repository_root=str(root),
            repository_id="",
            repository_fingerprint=str(identity.get("fingerprint") or ""),
            repository_remote_fingerprint=remote_fingerprint,
            config_digest=config_digest,
            start_commit="",
            unborn=False,
            head_ref="",
            branch="",
            upstream={},
            head_tree="",
            index_tree="",
            index_fingerprint="",
            dirty=False,
            dirty_identity={"staged": [], "unstaged": [], "untracked": [], "unmerged": [], "worktree_paths": [], "digest": ""},
            worktree_state={},
            commit_policy="none",
            expected_result_ref=_expected_result_ref("", repository),
            node_dependencies=node_dependencies,
            source_digest=_digest(portable_strict),
            local_digest=_digest(local),
            equivalence_digest=_digest(equivalence),
            remote_locator=remote_locator,
            remote_locator_digest=remote_locator_digest,
        )

    head = dict(state.head)
    upstream = dict(state.upstream)
    dirty = _dirty_identity(root, state)
    worktree = dict(state.worktree)
    branch = str(head.get("branch") or "")
    head_ref = str(head.get("symbolic_ref") or "")
    unborn = bool(head.get("unborn", False))
    start_commit = str(head.get("sha") or "")
    index_tree = str(state.index.get("tree_sha") or "")
    if not index_tree:
        raise FederationSourceLockError(
            f"Frozen federation source cannot materialize index tree for repository {repository!r}: "
            f"{state.index.get('tree_error') or 'unknown error'}"
        )
    head_tree = _head_tree(root, unborn=unborn)

    portable_strict = {
        "repository": repository,
        "source_kind": "git",
        "remote_fingerprint": remote_fingerprint,
        "config_digest": config_digest,
        "start_commit": start_commit,
        "unborn": unborn,
        "head_ref": head_ref,
        "branch": branch,
        "upstream": upstream,
        "head_tree": head_tree,
        "index_tree": index_tree,
        "index_fingerprint": str(state.index.get("entries_fingerprint") or ""),
        "dirty": state.dirty,
        "dirty_digest": str(dirty.get("digest") or ""),
        "commit_policy": "none",
        "expected_result_ref": _expected_result_ref("", repository),
        "node_dependencies": list(node_dependencies),
    }
    equivalence = {
        "repository": repository,
        "source_kind": "git",
        "remote_fingerprint": remote_fingerprint,
        "config_digest": config_digest,
        "head_tree": head_tree,
        "index_tree": index_tree,
        "dirty": state.dirty,
        "dirty_digest": str(dirty.get("digest") or ""),
        "node_dependencies": list(node_dependencies),
    }
    local = {
        "portable_source_digest": _digest(portable_strict),
        "repository_root": str(root),
        "repository_id": state.repository_id,
        "repository_fingerprint": str(identity.get("fingerprint") or ""),
        "worktree": {
            "id": str(worktree.get("id") or ""),
            "is_main": bool(worktree.get("is_main", False)),
            "is_linked": bool(worktree.get("is_linked", False)),
        },
    }
    return FederationSourceRepository(
        repository=repository,
        source_kind="git",
        git_available=True,
        filesystem_tree_digest="",
        repository_root=str(root),
        repository_id=state.repository_id,
        repository_fingerprint=str(identity.get("fingerprint") or ""),
        repository_remote_fingerprint=remote_fingerprint,
        config_digest=config_digest,
        start_commit=start_commit,
        unborn=unborn,
        head_ref=head_ref,
        branch=branch,
        upstream=upstream,
        head_tree=head_tree,
        index_tree=index_tree,
        index_fingerprint=str(state.index.get("entries_fingerprint") or ""),
        dirty=state.dirty,
        dirty_identity=dirty,
        worktree_state=worktree,
        commit_policy="none",
        expected_result_ref=_expected_result_ref("", repository),
        node_dependencies=node_dependencies,
        source_digest=_digest(portable_strict),
        local_digest=_digest(local),
        equivalence_digest=_digest(equivalence),
        remote_locator=remote_locator,
        remote_locator_digest=remote_locator_digest,
    )


def build_federation_source_lock(
    parent: "ProjectConfig",
    plan: "FederatedPlanGraph",
    *,
    drift_policy: str = "fail",
) -> FederationSourceLock:
    policy = str(drift_policy or "fail").strip()
    if policy not in DRIFT_POLICIES:
        raise FederationSourceLockError(
            f"Unsupported federation drift policy {policy!r}; expected one of {', '.join(DRIFT_POLICIES)}"
        )
    graph_digest = _plan_graph_digest(plan)
    repositories = tuple(
        _build_repository_source(parent, plan, repository)
        for repository in sorted(plan.repositories)
    )
    portable = {
        "schema_version": FEDERATION_SOURCE_LOCK_SCHEMA_VERSION,
        "kind": "federation-source-lock",
        "subject_kind": "group" if plan.group else "workflow",
        "workflow": plan.workflow,
        "group": plan.group,
        "graph_digest": graph_digest,
        "repositories": [
            {"repository": item.repository, "source_digest": item.source_digest}
            for item in repositories
        ],
    }
    local = {
        **portable,
        "repositories": [
            {
                "repository": item.repository,
                "source_digest": item.source_digest,
                "local_digest": item.local_digest,
            }
            for item in repositories
        ],
    }
    equivalent = {
        "schema_version": FEDERATION_SOURCE_LOCK_SCHEMA_VERSION,
        "kind": "federation-source-lock-equivalence",
        "subject_kind": portable["subject_kind"],
        "workflow": plan.workflow,
        "group": plan.group,
        "graph_digest": graph_digest,
        "repositories": [
            {"repository": item.repository, "equivalence_digest": item.equivalence_digest}
            for item in repositories
        ],
    }
    return FederationSourceLock(
        schema_version=FEDERATION_SOURCE_LOCK_SCHEMA_VERSION,
        kind="federation-source-lock",
        subject_kind=portable["subject_kind"],
        workflow=plan.workflow,
        group=plan.group,
        drift_policy=policy,
        graph_digest=graph_digest,
        repositories=repositories,
        lock_digest=_digest(portable),
        local_digest=_digest(local),
        equivalence_digest=_digest(equivalent),
    )


def source_lock_path(parent: "ProjectConfig") -> Path:
    return state_dir() / "federation-locks" / repo_key(Path(parent.repo_root)) / "source.lock.json"


def write_federation_source_lock(parent: "ProjectConfig", lock: FederationSourceLock) -> Path:
    path = source_lock_path(parent)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = lock.as_dict()
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)
    return path


def read_federation_source_lock(parent: "ProjectConfig") -> FederationSourceLock | None:
    path = source_lock_path(parent)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FederationSourceLockError(f"Cannot read federation source lock {path}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise FederationSourceLockError(f"Federation source lock {path} is not a JSON object")
    lock = FederationSourceLock.from_dict(payload)
    if lock.schema_version != FEDERATION_SOURCE_LOCK_SCHEMA_VERSION:
        raise FederationSourceLockError(
            f"Unsupported federation source lock schema {lock.schema_version}; expected {FEDERATION_SOURCE_LOCK_SCHEMA_VERSION}"
        )
    return lock


def _subject_matches(left: FederationSourceLock, right: FederationSourceLock) -> bool:
    return (
        left.subject_kind == right.subject_kind
        and left.workflow == right.workflow
        and left.group == right.group
    )


def compare_federation_source_locks(
    frozen: FederationSourceLock,
    current: FederationSourceLock,
    *,
    policy: str = "fail",
) -> dict[str, Any]:
    selected = str(policy or frozen.drift_policy or "fail").strip()
    if selected not in DRIFT_POLICIES:
        raise FederationSourceLockError(f"Unsupported federation drift policy {selected!r}")
    subject_match = _subject_matches(frozen, current)
    if selected == "allow-if-equivalent":
        matched = subject_match and frozen.equivalence_digest == current.equivalence_digest
        expected = frozen.equivalence_digest
        actual = current.equivalence_digest
        mode = "equivalence"
    else:
        matched = subject_match and frozen.local_digest == current.local_digest
        expected = frozen.local_digest
        actual = current.local_digest
        mode = "strict-local"
    frozen_repos = {item.repository: item for item in frozen.repositories}
    current_repos = {item.repository: item for item in current.repositories}
    drift: list[dict[str, Any]] = []
    for repository in sorted(set(frozen_repos) | set(current_repos)):
        before = frozen_repos.get(repository)
        after = current_repos.get(repository)
        if before is None or after is None:
            drift.append({"repository": repository, "kind": "membership", "frozen": bool(before), "current": bool(after)})
            continue
        before_digest = before.equivalence_digest if selected == "allow-if-equivalent" else before.local_digest
        after_digest = after.equivalence_digest if selected == "allow-if-equivalent" else after.local_digest
        if before_digest != after_digest:
            drift.append(
                {
                    "repository": repository,
                    "kind": "source",
                    "frozen_digest": before_digest,
                    "current_digest": after_digest,
                    "frozen_commit": before.start_commit,
                    "current_commit": after.start_commit,
                    "frozen_index_tree": before.index_tree,
                    "current_index_tree": after.index_tree,
                    "frozen_config_digest": before.config_digest,
                    "current_config_digest": after.config_digest,
                }
            )
    if frozen.graph_digest != current.graph_digest:
        drift.append(
            {
                "repository": "@federation",
                "kind": "graph",
                "frozen_digest": frozen.graph_digest,
                "current_digest": current.graph_digest,
            }
        )
    return {
        "schema_version": 1,
        "policy": selected,
        "mode": mode,
        "subject_match": subject_match,
        "matched": matched,
        "frozen_digest": expected,
        "current_digest": actual,
        "drift": drift,
    }


def resolve_execution_source_lock(
    parent: "ProjectConfig",
    plan: "FederatedPlanGraph",
    *,
    drift_policy: str = "fail",
) -> tuple[FederationSourceLock, dict[str, Any]]:
    # Every plan is itself a frozen source statement, even when the caller did
    # not persist a lock.  Re-snapshot immediately before execution and compare
    # against that planned identity so a build-plan -> execute race cannot
    # silently advance a child repository.
    planned = FederationSourceLock.from_dict(plan.source_lock or {})
    if not planned.repositories:
        planned = build_federation_source_lock(parent, plan, drift_policy=drift_policy)
    current = build_federation_source_lock(parent, plan, drift_policy=drift_policy)
    frozen = read_federation_source_lock(parent)
    if frozen is None or not _subject_matches(frozen, current):
        comparison = compare_federation_source_locks(planned, current, policy=drift_policy)
        if comparison["matched"]:
            comparison["source"] = "ephemeral-plan"
            return planned, comparison
        if drift_policy == "replan":
            comparison.update({"matched": True, "source": "ephemeral-replanned"})
            return current, comparison
        details = "; ".join(
            f"{item.get('repository')}:{item.get('kind')}" for item in comparison.get("drift") or []
        ) or "source identity changed"
        raise FederationSourceLockError(
            f"Federation source changed after planning under policy {drift_policy!r}: {details}. "
            "Use --drift-policy replan to explicitly rebuild the execution source plan, or "
            "--drift-policy allow-if-equivalent only when source/config content is equivalent."
        )
    comparison = compare_federation_source_locks(frozen, current, policy=drift_policy)
    if comparison["matched"]:
        comparison["source"] = "persisted-lock"
        return frozen, comparison
    if drift_policy == "replan":
        refreshed = replace(current, drift_policy="replan")
        path = write_federation_source_lock(parent, refreshed)
        comparison.update({"matched": True, "source": "replanned", "replanned_lock": str(path)})
        return refreshed, comparison
    details = "; ".join(
        f"{item.get('repository')}:{item.get('kind')}" for item in comparison.get("drift") or []
    ) or "source identity changed"
    raise FederationSourceLockError(
        f"Frozen federation source drift detected under policy {drift_policy!r}: {details}. "
        "Use --drift-policy replan to explicitly create a new source plan, or "
        "--drift-policy allow-if-equivalent only when source/config content is equivalent."
    )


def update_federation_source_lock_repository(
    parent: "ProjectConfig",
    current_plan: "FederatedPlanGraph",
    repository: str,
) -> FederationSourceLock:
    frozen = read_federation_source_lock(parent)
    if frozen is None:
        raise FederationSourceLockError("No persisted federation source lock; run 'devtool federation plan ... --freeze' first")
    fresh = build_federation_source_lock(parent, current_plan, drift_policy=frozen.drift_policy)
    if not _subject_matches(frozen, fresh):
        raise FederationSourceLockError(
            "Persisted federation source lock belongs to a different workflow/group; freeze the intended plan first"
        )
    if frozen.graph_digest != fresh.graph_digest:
        raise FederationSourceLockError(
            "Federation graph changed since the lock was frozen; --update may refresh one repository source only. "
            "Run 'devtool federation plan ... --freeze' to create a new graph/source lock."
        )
    if repository not in {item.repository for item in fresh.repositories}:
        raise FederationSourceLockError(f"Repository {repository!r} does not participate in the frozen plan")
    by_name = {item.repository: item for item in frozen.repositories}
    fresh_by_name = {item.repository: item for item in fresh.repositories}
    by_name[repository] = fresh_by_name[repository]
    merged_repositories = tuple(by_name[name] for name in sorted(by_name))

    # Recalculate all top-level digests without changing unaffected entries.
    proxy = replace(fresh, repositories=merged_repositories)
    portable = {
        "schema_version": proxy.schema_version,
        "kind": proxy.kind,
        "subject_kind": proxy.subject_kind,
        "workflow": proxy.workflow,
        "group": proxy.group,
        "graph_digest": proxy.graph_digest,
        "repositories": [{"repository": item.repository, "source_digest": item.source_digest} for item in merged_repositories],
    }
    local = {
        **portable,
        "repositories": [
            {"repository": item.repository, "source_digest": item.source_digest, "local_digest": item.local_digest}
            for item in merged_repositories
        ],
    }
    equivalent = {
        "schema_version": proxy.schema_version,
        "kind": "federation-source-lock-equivalence",
        "subject_kind": proxy.subject_kind,
        "workflow": proxy.workflow,
        "group": proxy.group,
        "graph_digest": proxy.graph_digest,
        "repositories": [
            {"repository": item.repository, "equivalence_digest": item.equivalence_digest}
            for item in merged_repositories
        ],
    }
    updated = replace(
        proxy,
        lock_digest=_digest(portable),
        local_digest=_digest(local),
        equivalence_digest=_digest(equivalent),
    )
    write_federation_source_lock(parent, updated)
    return updated


__all__ = [
    "DRIFT_POLICIES",
    "FEDERATION_SOURCE_LOCK_SCHEMA_VERSION",
    "FederationSourceLock",
    "FederationSourceLockError",
    "FederationSourceRepository",
    "build_federation_source_lock",
    "compare_federation_source_locks",
    "read_federation_source_lock",
    "resolve_execution_source_lock",
    "source_lock_path",
    "sanitize_git_remote_locator",
    "git_remote_locator",
    "update_federation_source_lock_repository",
    "write_federation_source_lock",
]
