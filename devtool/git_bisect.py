from __future__ import annotations

"""GIT-FRX-15 managed Git and federated bisect.

The implementation deliberately never enters Git's global ``git bisect`` state.
Candidate revisions are evaluated from Devtool-managed detached worktrees and
all candidate combinations/results are persisted under the Devtool state root.

Federated bisect keeps the roadmap-v1 default: exactly one enrolled repository
varies while every other participating repository remains pinned.  The pinned
baseline comes from the persisted frozen federation source lock unless an
explicit ``--pin`` overrides that repository.  Candidate identity also records
the frozen-lock identity, per-candidate capability graph generation and
artifact-input contract identity.
"""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from typing import Any, Callable, Mapping, Sequence

from .federation_capabilities import build_capability_graph
from .federation_lock import FederationSourceLock, read_federation_source_lock
from .federation_plan import build_federated_group_plan, build_federated_plan
from .git_state import GitStateError, collect_git_state
from .models import ProjectConfig
from .xdg import state_dir

BISECT_SCHEMA_VERSION = 1
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class GitBisectError(RuntimeError):
    """Raised when a managed bisect cannot prove a safe deterministic state."""


@dataclass(frozen=True, slots=True)
class BisectTaskOutcome:
    exit_code: int
    duration_seconds: float = 0.0
    output_digest: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def classification(self) -> str:
        return "good" if int(self.exit_code) == 0 else "bad"

    def as_dict(self) -> dict[str, Any]:
        return {
            "exit_code": int(self.exit_code),
            "classification": self.classification,
            "duration_seconds": float(self.duration_seconds),
            "output_digest": self.output_digest,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class BisectReport:
    session_id: str
    mode: str
    task: str
    variable_repository: str
    initial_good: str
    initial_bad: str
    first_bad_commit: str
    candidates: tuple[dict[str, Any], ...]
    source_identity: dict[str, Any]
    state_path: str
    history_path: str
    status: str = "succeeded"

    @property
    def ok(self) -> bool:
        return self.status == "succeeded" and bool(self.first_bad_commit)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": BISECT_SCHEMA_VERSION,
            "kind": "devtool.git-bisect",
            "session_id": self.session_id,
            "mode": self.mode,
            "task": self.task,
            "variable_repository": self.variable_repository,
            "initial_good": self.initial_good,
            "initial_bad": self.initial_bad,
            "first_bad_commit": self.first_bad_commit,
            "candidate_count": len(self.candidates),
            "candidates": [dict(item) for item in self.candidates],
            "source_identity": dict(self.source_identity),
            "state_path": self.state_path,
            "history_path": self.history_path,
            "managed_git_bisect_state": "unused",
            "status": self.status,
        }


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _safe(value: str, fallback: str = "bisect") -> str:
    raw = str(value or "").strip()
    cleaned = _SAFE.sub("-", raw).strip(".-")
    if not cleaned or cleaned.endswith(".lock") or "@{" in cleaned:
        cleaned = fallback
    if len(cleaned) > 96:
        cleaned = cleaned[:72].rstrip(".-") + "-" + hashlib.sha256(raw.encode()).hexdigest()[:16]
    return cleaned


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _session_id(mode: str, task: str, identity: Mapping[str, Any]) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    entropy = {"mode": mode, "task": task, "identity": dict(identity), "pid": os.getpid(), "ns": time.time_ns()}
    return f"{_safe(mode)}-{stamp}-{_digest(entropy)[:12]}"


def bisect_session_root(session_id: str) -> Path:
    return state_dir() / "git-bisect" / _safe(session_id)


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(dict(payload), indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _append_history(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(_canonical_json(dict(payload)) + "\n")
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:
            pass


def _run_git(root: Path, *args: str, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitBisectError(f"Unable to execute git {' '.join(args)}: {exc}") from exc


def _require(proc: subprocess.CompletedProcess[str], context: str) -> str:
    if proc.returncode == 0:
        return (proc.stdout or "").strip()
    detail = (proc.stderr or proc.stdout or "git command failed").strip()
    raise GitBisectError(f"{context}: {detail}")


def _resolve_commit(root: Path, revision: str) -> str:
    raw = str(revision or "").strip()
    if not raw:
        raise GitBisectError("Bisect revision cannot be empty")
    return _require(_run_git(root, "rev-parse", "--verify", f"{raw}^{{commit}}"), f"Unable to resolve revision {raw!r}")


def _assert_repo_safe_for_worktrees(root: Path) -> dict[str, Any]:
    try:
        state = collect_git_state(root)
    except GitStateError as exc:
        raise GitBisectError(f"Bisect source is not a Git worktree: {exc}") from exc
    active = []
    operations = state.operations if isinstance(state.operations, Mapping) else {}
    for item in operations.get("active", []) if isinstance(operations.get("active"), list) else []:
        if str(item).strip():
            active.append(str(item))
    if active:
        raise GitBisectError(
            "Managed bisect refuses a repository with an active Git operation: " + ", ".join(sorted(active))
        )
    sha = str(state.head.get("sha") or "")
    if not sha:
        raise GitBisectError("Managed bisect requires a committed Git HEAD")
    return {
        "repository_id": str(state.repository_id or ""),
        "repository_fingerprint": str((state.repository_identity or {}).get("fingerprint") or ""),
        "head": sha,
        "head_ref": str(state.head.get("symbolic_ref") or ""),
        "dirty": bool(state.staged or state.unstaged or state.untracked or state.unmerged),
    }


def _assert_ancestor(root: Path, good: str, bad: str) -> None:
    proc = _run_git(root, "merge-base", "--is-ancestor", good, bad)
    if proc.returncode == 0:
        return
    if proc.returncode == 1:
        raise GitBisectError(
            f"Default bisect strategy requires --good {good[:12]} to be an ancestor of --bad {bad[:12]}"
        )
    _require(proc, "Unable to verify bisect ancestry")


def _suspects(root: Path, bad: str, good_commits: Sequence[str]) -> tuple[str, ...]:
    argv = ["rev-list", bad, "--not", *good_commits]
    text = _require(_run_git(root, *argv), "Unable to enumerate bisect candidates")
    return tuple(line.strip() for line in text.splitlines() if line.strip())


def _next_candidate(root: Path, bad: str, good_commits: Sequence[str]) -> str:
    text = _require(
        _run_git(root, "rev-list", "--bisect", bad, "--not", *good_commits),
        "Unable to choose next bisect candidate",
    )
    candidate = text.splitlines()[0].strip() if text.strip() else ""
    if not candidate:
        raise GitBisectError("Git produced no bisect candidate while unresolved suspects remain")
    return candidate


class _ManagedWorktrees:
    """Detached disposable worktrees; user branches/index/worktree remain untouched."""

    def __init__(self, repositories: Mapping[str, Path], initial_commits: Mapping[str, str]) -> None:
        self.repositories = {str(name): Path(root).resolve() for name, root in repositories.items()}
        self.initial_commits = dict(initial_commits)
        self._tmp = Path(tempfile.mkdtemp(prefix="devtool-bisect-"))
        self.paths: dict[str, Path] = {}
        self._created: list[tuple[Path, Path]] = []

    def __enter__(self) -> "_ManagedWorktrees":
        try:
            for name in sorted(self.repositories):
                root = self.repositories[name]
                commit = self.initial_commits[name]
                destination = self._tmp / _safe(name, "repo")
                _require(
                    _run_git(root, "worktree", "add", "--detach", str(destination), commit, timeout=60.0),
                    f"Unable to create managed bisect worktree for {name}",
                )
                self.paths[name] = destination
                self._created.append((root, destination))
            return self
        except Exception:
            self.close()
            raise

    def checkout(self, commits: Mapping[str, str]) -> dict[str, str]:
        actual: dict[str, str] = {}
        for name in sorted(self.paths):
            destination = self.paths[name]
            commit = commits[name]
            _require(
                _run_git(destination, "checkout", "--detach", "--force", commit, timeout=60.0),
                f"Unable to checkout managed bisect candidate {name}={commit}",
            )
            _require(_run_git(destination, "reset", "--hard", commit, timeout=60.0), f"Unable to reset bisect worktree {name}")
            clean = _run_git(destination, "clean", "-ffdx", timeout=60.0)
            _require(clean, f"Unable to clean disposable bisect worktree {name}")
            actual[name] = _resolve_commit(destination, "HEAD")
            if actual[name] != commit:
                raise GitBisectError(
                    f"Managed bisect worktree identity mismatch for {name}: expected {commit}, got {actual[name]}"
                )
        return actual

    def close(self) -> None:
        for root, destination in reversed(self._created):
            try:
                _run_git(root, "worktree", "remove", "--force", str(destination), timeout=60.0)
            except Exception:
                pass
            try:
                _run_git(root, "worktree", "prune", timeout=30.0)
            except Exception:
                pass
        shutil.rmtree(self._tmp, ignore_errors=True)
        self._created.clear()
        self.paths.clear()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def _normalize_outcome(value: Any, *, started: float) -> BisectTaskOutcome:
    elapsed = max(0.0, time.monotonic() - started)
    if isinstance(value, BisectTaskOutcome):
        if value.duration_seconds > 0:
            return value
        return BisectTaskOutcome(value.exit_code, elapsed, value.output_digest, dict(value.metadata))
    if isinstance(value, bool):
        return BisectTaskOutcome(0 if value else 1, elapsed)
    if isinstance(value, int):
        return BisectTaskOutcome(int(value), elapsed)
    if isinstance(value, tuple) and value:
        code = int(value[0])
        metadata = dict(value[1]) if len(value) > 1 and isinstance(value[1], Mapping) else {}
        return BisectTaskOutcome(code, elapsed, metadata=metadata)
    raise GitBisectError(f"Bisect evaluator returned unsupported result {type(value).__name__}")


def _task_argv(task: str, *, target: str = "") -> list[str]:
    raw = str(task or "").strip()
    if not raw:
        raise GitBisectError("--task is required")
    prefix, sep, value = raw.partition(":")
    command: list[str]
    if sep and prefix == "workflow":
        if not value.strip():
            raise GitBisectError("workflow: bisect task requires a workflow name")
        command = ["invoke", value.strip()]
    elif sep and prefix == "test":
        if not value.strip():
            raise GitBisectError("test: bisect task requires a declared test id")
        command = ["test", value.strip()]
    elif sep and prefix in {"job", "shell", "cmd", "command"}:
        raise GitBisectError(
            "Bisect tasks must use a Devtool command/workflow/test identity; raw shell/job execution is not a bisect contract"
        )
    elif raw in {"build", "test", "validate", "package", "run", "restore", "lint", "typecheck"}:
        command = [raw]
    else:
        command = ["invoke", raw]
    argv = ["-r", "{repo}"]
    if target:
        argv.extend(["--target", str(target)])
    argv.extend(command)
    return argv


def _default_single_evaluator(root: Path, *, task: str, target: str = "") -> BisectTaskOutcome:
    import sys

    argv = [part if part != "{repo}" else str(root) for part in _task_argv(task, target=target)]
    started = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "devtool", *argv],
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=60.0 * 60.0,
        )
    except subprocess.TimeoutExpired as exc:
        output = str(exc.stdout or "")
        return BisectTaskOutcome(
            124,
            max(0.0, time.monotonic() - started),
            hashlib.sha256(output.encode("utf-8", "surrogatepass")).hexdigest(),
            {"reason": "task-timeout"},
        )
    output = proc.stdout or ""
    return BisectTaskOutcome(
        int(proc.returncode),
        max(0.0, time.monotonic() - started),
        hashlib.sha256(output.encode("utf-8", "surrogatepass")).hexdigest(),
        {"argv": argv},
    )


def _persist_progress(
    *,
    session_id: str,
    mode: str,
    task: str,
    variable_repository: str,
    initial_good: str,
    initial_bad: str,
    source_identity: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    status: str,
    first_bad_commit: str = "",
) -> tuple[Path, Path]:
    root = bisect_session_root(session_id)
    state_path = root / "state.json"
    history_path = root / "history.jsonl"
    payload = {
        "schema_version": BISECT_SCHEMA_VERSION,
        "kind": "devtool.git-bisect",
        "session_id": session_id,
        "mode": mode,
        "task": task,
        "variable_repository": variable_repository,
        "initial_good": initial_good,
        "initial_bad": initial_bad,
        "first_bad_commit": first_bad_commit,
        "candidate_count": len(candidates),
        "candidates": [dict(item) for item in candidates],
        "source_identity": dict(source_identity),
        "managed_git_bisect_state": "unused",
        "status": status,
        "updated_at": _now(),
    }
    _atomic_write(state_path, payload)
    return state_path, history_path


def _persist_failure(
    *,
    session_id: str,
    mode: str,
    task: str,
    variable_repository: str,
    initial_good: str,
    initial_bad: str,
    source_identity: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    history_path: Path,
    error: BaseException,
) -> None:
    _persist_progress(
        session_id=session_id,
        mode=mode,
        task=task,
        variable_repository=variable_repository,
        initial_good=initial_good,
        initial_bad=initial_bad,
        source_identity=source_identity,
        candidates=candidates,
        status="failed",
    )
    _append_history(
        history_path,
        {
            "event": "failed",
            "recorded_at": _now(),
            "status": "failed",
            "error_type": type(error).__name__,
            "error": str(error),
            "candidate_count": len(candidates),
        },
    )


def _run_bisect_search(
    *,
    repository_root: Path,
    good: str,
    bad: str,
    evaluate: Callable[[str, int], tuple[BisectTaskOutcome, dict[str, Any]]],
    on_progress: Callable[[Sequence[Mapping[str, Any]], str, str], None],
) -> tuple[str, list[dict[str, Any]]]:
    good_commits = [good]
    current_bad = bad
    candidates: list[dict[str, Any]] = []
    previous_count: int | None = None
    for index in range(1, 513):
        suspects = _suspects(repository_root, current_bad, good_commits)
        if not suspects:
            raise GitBisectError("Bisect search exhausted all suspects without retaining a bad boundary")
        if len(suspects) == 1:
            if suspects[0] != current_bad:
                raise GitBisectError("Bisect suspect graph lost the current bad boundary")
            on_progress(candidates, "succeeded", current_bad)
            return current_bad, candidates
        if previous_count is not None and len(suspects) >= previous_count:
            raise GitBisectError("Bisect search made no progress; refusing an ambiguous or cyclic candidate loop")
        previous_count = len(suspects)
        candidate = _next_candidate(repository_root, current_bad, good_commits)
        if candidate == current_bad:
            alternate = next((sha for sha in suspects if sha != current_bad), "")
            if not alternate:
                on_progress(candidates, "succeeded", current_bad)
                return current_bad, candidates
            candidate = alternate
        started = time.monotonic()
        outcome, identity = evaluate(candidate, index)
        outcome = _normalize_outcome(outcome, started=started)
        if outcome.exit_code == 125:
            raise GitBisectError(
                "Bisect task returned exit 125 (skip). GIT-FRX-15 default strategy is single-axis deterministic bisect and does not guess across skipped candidates"
            )
        row = {
            "index": index,
            "commit": candidate,
            "combination": dict(identity.get("combination") or {}),
            "candidate_identity": dict(identity),
            "candidate_identity_digest": _digest(identity),
            "result": outcome.as_dict(),
            "classified_as": outcome.classification,
            "recorded_at": _now(),
        }
        candidates.append(row)
        if outcome.exit_code == 0:
            good_commits.append(candidate)
        else:
            current_bad = candidate
        on_progress(candidates, "running", "")
    raise GitBisectError("Bisect exceeded the safety limit of 512 evaluated candidates")


def run_git_bisect(
    repo_root: str | Path,
    *,
    good: str,
    bad: str,
    task: str,
    target: str = "",
    evaluator: Callable[[Path, str, Mapping[str, Any]], Any] | None = None,
) -> BisectReport:
    root = Path(repo_root).expanduser().resolve()
    repository_identity = _assert_repo_safe_for_worktrees(root)
    good_sha = _resolve_commit(root, good)
    bad_sha = _resolve_commit(root, bad)
    if good_sha == bad_sha:
        raise GitBisectError("--good and --bad resolve to the same commit")
    _assert_ancestor(root, good_sha, bad_sha)
    source_identity = {
        "repository_id": repository_identity["repository_id"],
        "repository_fingerprint": repository_identity["repository_fingerprint"],
        "initial_head": repository_identity["head"],
        "initial_head_ref": repository_identity["head_ref"],
        "initial_dirty": repository_identity["dirty"],
        "good": good_sha,
        "bad": bad_sha,
        "task": task,
        "target": target,
    }
    session_id = _session_id("single", task, source_identity)
    candidates: list[dict[str, Any]] = []
    state_path, history_path = _persist_progress(
        session_id=session_id,
        mode="single",
        task=task,
        variable_repository="@repository",
        initial_good=good_sha,
        initial_bad=bad_sha,
        source_identity=source_identity,
        candidates=candidates,
        status="running",
    )
    _append_history(history_path, {
        "event": "boundaries",
        "recorded_at": _now(),
        "good": good_sha,
        "bad": bad_sha,
        "source_identity": source_identity,
    })

    try:
        with _ManagedWorktrees({"@repository": root}, {"@repository": good_sha}) as worktrees:
            def evaluate(candidate: str, index: int) -> tuple[BisectTaskOutcome, dict[str, Any]]:
                actual = worktrees.checkout({"@repository": candidate})
                identity = {
                    "schema_version": 1,
                    "mode": "single",
                    "repository": "@repository",
                    "combination": {"@repository": actual["@repository"]},
                    "task": task,
                    "target": target,
                    "source_repository_id": repository_identity["repository_id"],
                }
                started = time.monotonic()
                raw = (
                    evaluator(worktrees.paths["@repository"], task, identity)
                    if evaluator is not None
                    else _default_single_evaluator(worktrees.paths["@repository"], task=task, target=target)
                )
                return _normalize_outcome(raw, started=started), identity

            def progress(rows: Sequence[Mapping[str, Any]], status: str, first_bad: str) -> None:
                nonlocal state_path, history_path
                if len(rows) > len(candidates):
                    for row in rows[len(candidates):]:
                        _append_history(history_path, {"event": "candidate", **dict(row)})
                candidates[:] = [dict(row) for row in rows]
                state_path, history_path = _persist_progress(
                    session_id=session_id,
                    mode="single",
                    task=task,
                    variable_repository="@repository",
                    initial_good=good_sha,
                    initial_bad=bad_sha,
                    source_identity=source_identity,
                    candidates=candidates,
                    status=status,
                    first_bad_commit=first_bad,
                )

            first_bad, final_candidates = _run_bisect_search(
                repository_root=root,
                good=good_sha,
                bad=bad_sha,
                evaluate=evaluate,
                on_progress=progress,
            )
    except Exception as exc:
        _persist_failure(
            session_id=session_id,
            mode="single",
            task=task,
            variable_repository="@repository",
            initial_good=good_sha,
            initial_bad=bad_sha,
            source_identity=source_identity,
            candidates=candidates,
            history_path=history_path,
            error=exc,
        )
        raise

    _append_history(history_path, {
        "event": "finished",
        "recorded_at": _now(),
        "status": "succeeded",
        "first_bad_commit": first_bad,
        "candidate_count": len(final_candidates),
    })
    return BisectReport(
        session_id=session_id,
        mode="single",
        task=task,
        variable_repository="@repository",
        initial_good=good_sha,
        initial_bad=bad_sha,
        first_bad_commit=first_bad,
        candidates=tuple(final_candidates),
        source_identity=source_identity,
        state_path=str(state_path),
        history_path=str(history_path),
    )


def _parse_assignment(value: str, *, option: str) -> tuple[str, str]:
    raw = str(value or "").strip()
    if "=" not in raw:
        raise GitBisectError(f"{option} expects NAME=VALUE; got {raw!r}")
    name, payload = raw.split("=", 1)
    name = name.strip()
    payload = payload.strip()
    if not name or not payload:
        raise GitBisectError(f"{option} expects non-empty NAME=VALUE")
    return name, payload


def parse_federated_range(value: str) -> tuple[str, str, str]:
    name, payload = _parse_assignment(value, option="--repo")
    if "..." in payload or payload.count("..") != 1:
        raise GitBisectError("--repo expects NAME=GOOD..BAD using exactly two dots")
    good, bad = (part.strip() for part in payload.split("..", 1))
    if not good or not bad:
        raise GitBisectError("--repo expects NAME=GOOD..BAD with both revisions present")
    return name, good, bad


def parse_pin(value: str) -> tuple[str, str]:
    return _parse_assignment(value, option="--pin")


def _federated_subject(task: str) -> tuple[str, str]:
    raw = str(task or "").strip()
    if not raw:
        raise GitBisectError("--task is required")
    if raw.startswith("group:"):
        name = raw.split(":", 1)[1].strip()
        if not name:
            raise GitBisectError("group: bisect task requires a group name")
        return "group", name
    if raw.startswith("workflow:"):
        name = raw.split(":", 1)[1].strip()
        if not name:
            raise GitBisectError("workflow: bisect task requires a workflow name")
        return "workflow", name
    return "workflow", raw


def _subject_plan(parent: ProjectConfig, task: str):
    kind, name = _federated_subject(task)
    if kind == "group":
        return kind, name, build_federated_group_plan(parent, name)
    return kind, name, build_federated_plan(parent, name)


def _artifact_contract_identity(plan: Any) -> dict[str, Any]:
    rows = []
    for node in plan.topological_order():
        if node.synthetic:
            continue
        artifact_inputs = node.metadata.get("artifact_inputs") if isinstance(node.metadata, Mapping) else []
        rows.append({
            "node": node.key,
            "repository": node.repository,
            "contract_ref": str(node.metadata.get("contract_ref") or node.metadata.get("federation_ref") or ""),
            "artifact_inputs": list(artifact_inputs or []),
        })
    payload = {"nodes": rows}
    payload["digest"] = _digest(payload)
    return payload


def _capability_generation(parent: ProjectConfig, plan: Any) -> dict[str, Any]:
    graph = build_capability_graph(parent, strict=True).as_dict()
    # Plan projection is authoritative for the task DAG; retain both the full
    # declared graph and its projected edges in the generation identity.
    payload = {
        "declared": graph,
        "projected": dict(getattr(plan, "capability_graph", {}) or {}),
    }
    payload["digest"] = _digest(payload)
    return payload


def _lock_subject_matches(lock: FederationSourceLock, subject_kind: str, subject_name: str) -> bool:
    if subject_kind == "group":
        return lock.subject_kind == "group" and lock.group == subject_name
    return lock.subject_kind == "workflow" and lock.workflow == subject_name


def _scoped_candidate_config(parent: ProjectConfig, roots: Mapping[str, Path], temp_parent_root: Path) -> ProjectConfig:
    scoped = deepcopy(parent)
    scoped.repo_root = temp_parent_root
    scoped.config_path = None
    updated = dict(scoped.federation.repositories)
    for name, root in roots.items():
        enrollment = updated.get(name)
        if enrollment is None:
            raise GitBisectError(f"Candidate repository {name!r} is no longer enrolled")
        updated[name] = replace(
            enrollment,
            declared_path=str(root),
            root=str(root.resolve()),
            available=True,
        )
    scoped.federation.repositories = updated
    return scoped


def _resolved_artifact_result_identity(report: Any) -> dict[str, Any]:
    children = []
    for child in getattr(report, "children", ()):
        raw_inputs = dict(getattr(child, "artifact_inputs", {}) or {})
        public_inputs = {key: value for key, value in raw_inputs.items() if key != "__manifest__"}
        children.append({
            "step_id": str(getattr(child, "step_id", "") or ""),
            "repository": str(getattr(child, "repository", "") or ""),
            "run_id": str(getattr(child, "run_id", "") or ""),
            "inputs": public_inputs,
        })
    payload = {"children": children}
    payload["digest"] = _digest(payload)
    return payload


def _default_federated_evaluator(
    parent: ProjectConfig,
    *,
    subject_kind: str,
    subject_name: str,
    reenter: Callable[[list[str] | None], int],
) -> BisectTaskOutcome:
    from .federation_execution import run_federated_group, run_federated_workflow

    started = time.monotonic()
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        report = (
            run_federated_group(parent, subject_name, reenter=reenter, parallel=False, drift_policy="fail")
            if subject_kind == "group"
            else run_federated_workflow(parent, subject_name, reenter=reenter, parallel=False, drift_policy="fail")
        )
    output = stdout.getvalue() + stderr.getvalue()
    return BisectTaskOutcome(
        int(report.exit_code),
        max(0.0, time.monotonic() - started),
        hashlib.sha256(output.encode("utf-8", "surrogatepass")).hexdigest(),
        {
            "parent_run_id": str(report.parent_run_id or ""),
            "status": str(report.status or ""),
            "child_runs": [
                {
                    "step_id": child.step_id,
                    "repository": child.repository,
                    "run_id": child.run_id,
                    "status": child.status,
                    "exit_code": child.exit_code,
                }
                for child in report.children
            ],
            "resolved_artifact_inputs": _resolved_artifact_result_identity(report),
        },
    )


def run_federated_bisect(
    parent: ProjectConfig,
    *,
    repository_ranges: Sequence[str],
    pins: Sequence[str],
    task: str,
    evaluator: Callable[[ProjectConfig, Mapping[str, str], Mapping[str, Any]], Any] | None = None,
    reenter: Callable[[list[str] | None], int] | None = None,
) -> BisectReport:
    if len(tuple(repository_ranges)) != 1:
        raise GitBisectError(
            "GIT-FRX-15 default strategy varies exactly one repository; coordinated/Cartesian multi-repository search is not enabled"
        )
    variable_repository, good_raw, bad_raw = parse_federated_range(repository_ranges[0])
    subject_kind, subject_name, baseline_plan = _subject_plan(parent, task)
    participating = tuple(sorted(baseline_plan.repositories))
    if variable_repository not in participating:
        raise GitBisectError(
            f"Bisect repository {variable_repository!r} is not part of {subject_kind} {subject_name!r}"
        )

    frozen = read_federation_source_lock(parent)
    if frozen is None:
        raise GitBisectError(
            "Federated bisect requires a persisted frozen federation source lock; run federation plan ... --freeze first"
        )
    if not _lock_subject_matches(frozen, subject_kind, subject_name):
        raise GitBisectError(
            "Persisted federation source lock belongs to a different workflow/group than the requested bisect task"
        )
    planned_graph_digest = str((getattr(baseline_plan, "source_lock", {}) or {}).get("graph_digest") or "")
    if planned_graph_digest and frozen.graph_digest and planned_graph_digest != frozen.graph_digest:
        raise GitBisectError(
            "Current federation graph no longer matches the frozen lock; refresh/freeze the intended graph explicitly before bisecting"
        )

    locked = {item.repository: item for item in frozen.repositories}
    missing_locked = sorted(set(participating) - set(locked))
    if missing_locked:
        raise GitBisectError("Frozen federation lock is missing participating repositories: " + ", ".join(missing_locked))

    explicit_pins: dict[str, str] = {}
    for raw in pins:
        name, revision = parse_pin(raw)
        if name in explicit_pins:
            raise GitBisectError(f"Duplicate --pin for repository {name!r}")
        if name == variable_repository:
            raise GitBisectError("The varying --repo repository cannot also be supplied through --pin")
        if name not in participating:
            raise GitBisectError(f"Pinned repository {name!r} is not part of {subject_kind} {subject_name!r}")
        explicit_pins[name] = revision

    repo_roots: dict[str, Path] = {}
    repo_source_identity: dict[str, Any] = {}
    for name in participating:
        enrollment = parent.federation.repositories.get(name)
        if enrollment is None or not enrollment.available:
            raise GitBisectError(f"Federation repository {name!r} is unavailable for bisect")
        root = Path(enrollment.root).expanduser().resolve()
        state = _assert_repo_safe_for_worktrees(root)
        repo_roots[name] = root
        repo_source_identity[name] = state

    variable_root = repo_roots[variable_repository]
    good_sha = _resolve_commit(variable_root, good_raw)
    bad_sha = _resolve_commit(variable_root, bad_raw)
    if good_sha == bad_sha:
        raise GitBisectError("Federated --repo GOOD and BAD resolve to the same commit")
    _assert_ancestor(variable_root, good_sha, bad_sha)

    pin_commits: dict[str, str] = {}
    pin_sources: dict[str, str] = {}
    for name in participating:
        if name == variable_repository:
            continue
        revision = explicit_pins.get(name) or str(locked[name].start_commit or "")
        if not revision:
            raise GitBisectError(f"Frozen lock has no committed source for pinned repository {name!r}")
        pin_commits[name] = _resolve_commit(repo_roots[name], revision)
        pin_sources[name] = "explicit" if name in explicit_pins else "frozen-lock"

    baseline_capability = _capability_generation(parent, baseline_plan)
    baseline_artifacts = _artifact_contract_identity(baseline_plan)
    source_identity = {
        "subject_kind": subject_kind,
        "subject": subject_name,
        "variable_repository": variable_repository,
        "repositories": {
            name: {
                "repository_id": repo_source_identity[name]["repository_id"],
                "repository_fingerprint": repo_source_identity[name]["repository_fingerprint"],
            }
            for name in participating
        },
        "frozen_lock": {
            "lock_digest": frozen.lock_digest,
            "local_digest": frozen.local_digest,
            "equivalence_digest": frozen.equivalence_digest,
            "graph_digest": frozen.graph_digest,
        },
        "baseline_capability_generation": baseline_capability,
        "baseline_artifact_inputs": baseline_artifacts,
        "pin_sources": pin_sources,
        "pins": dict(pin_commits),
        "good": good_sha,
        "bad": bad_sha,
    }
    session_id = _session_id("federation", task, source_identity)
    state_path, history_path = _persist_progress(
        session_id=session_id,
        mode="federation",
        task=task,
        variable_repository=variable_repository,
        initial_good=good_sha,
        initial_bad=bad_sha,
        source_identity=source_identity,
        candidates=(),
        status="running",
    )
    _append_history(history_path, {
        "event": "boundaries",
        "recorded_at": _now(),
        "good": good_sha,
        "bad": bad_sha,
        "source_identity": source_identity,
    })

    initial = {**pin_commits, variable_repository: good_sha}
    candidates: list[dict[str, Any]] = []
    temp_parent_root = Path(tempfile.mkdtemp(prefix="devtool-federation-bisect-parent-"))
    try:
        with _ManagedWorktrees(repo_roots, initial) as worktrees:
            def evaluate(candidate: str, index: int) -> tuple[BisectTaskOutcome, dict[str, Any]]:
                combination = {**pin_commits, variable_repository: candidate}
                actual = worktrees.checkout(combination)
                scoped = _scoped_candidate_config(parent, worktrees.paths, temp_parent_root)
                planning_error = ""
                try:
                    _kind, _name, candidate_plan = _subject_plan(scoped, task)
                    capability_generation = _capability_generation(scoped, candidate_plan)
                    artifact_contract = _artifact_contract_identity(candidate_plan)
                    graph_digest = str((getattr(candidate_plan, "source_lock", {}) or {}).get("graph_digest") or "")
                except Exception as exc:
                    candidate_plan = None
                    capability_generation = {"digest": "", "error": str(exc)}
                    artifact_contract = {"digest": "", "error": str(exc)}
                    graph_digest = ""
                    planning_error = str(exc)
                identity = {
                    "schema_version": 1,
                    "mode": "federation",
                    "subject_kind": subject_kind,
                    "subject": subject_name,
                    "variable_repository": variable_repository,
                    "combination": dict(actual),
                    "frozen_lock_digest": frozen.lock_digest,
                    "frozen_lock_equivalence_digest": frozen.equivalence_digest,
                    "frozen_graph_digest": frozen.graph_digest,
                    "candidate_graph_digest": graph_digest,
                    "capability_generation": capability_generation,
                    "artifact_inputs": artifact_contract,
                    "pin_sources": pin_sources,
                }
                started = time.monotonic()
                if planning_error:
                    outcome = BisectTaskOutcome(
                        1,
                        max(0.0, time.monotonic() - started),
                        metadata={"planning_error": planning_error},
                    )
                elif evaluator is not None:
                    outcome = _normalize_outcome(evaluator(scoped, actual, identity), started=started)
                else:
                    callback = reenter
                    if callback is None:
                        from .cli import main as callback
                    outcome = _default_federated_evaluator(
                        scoped,
                        subject_kind=subject_kind,
                        subject_name=subject_name,
                        reenter=callback,
                    )
                # Resolved artifact edges are execution results and therefore
                # augment, rather than rewrite, the pre-execution candidate id.
                if isinstance(outcome.metadata.get("resolved_artifact_inputs"), Mapping):
                    metadata = dict(outcome.metadata)
                    metadata["candidate_result_identity"] = _digest({
                        "candidate_identity": identity,
                        "resolved_artifact_inputs": metadata["resolved_artifact_inputs"],
                    })
                    outcome = BisectTaskOutcome(
                        outcome.exit_code,
                        outcome.duration_seconds,
                        outcome.output_digest,
                        metadata,
                    )
                return outcome, identity

            def progress(rows: Sequence[Mapping[str, Any]], status: str, first_bad: str) -> None:
                nonlocal state_path, history_path
                if len(rows) > len(candidates):
                    for row in rows[len(candidates):]:
                        _append_history(history_path, {"event": "candidate", **dict(row)})
                candidates[:] = [dict(row) for row in rows]
                state_path, history_path = _persist_progress(
                    session_id=session_id,
                    mode="federation",
                    task=task,
                    variable_repository=variable_repository,
                    initial_good=good_sha,
                    initial_bad=bad_sha,
                    source_identity=source_identity,
                    candidates=candidates,
                    status=status,
                    first_bad_commit=first_bad,
                )

            first_bad, final_candidates = _run_bisect_search(
                repository_root=variable_root,
                good=good_sha,
                bad=bad_sha,
                evaluate=evaluate,
                on_progress=progress,
            )
    except Exception as exc:
        _persist_failure(
            session_id=session_id,
            mode="federation",
            task=task,
            variable_repository=variable_repository,
            initial_good=good_sha,
            initial_bad=bad_sha,
            source_identity=source_identity,
            candidates=candidates,
            history_path=history_path,
            error=exc,
        )
        raise
    finally:
        shutil.rmtree(temp_parent_root, ignore_errors=True)

    _append_history(history_path, {
        "event": "finished",
        "recorded_at": _now(),
        "status": "succeeded",
        "first_bad_commit": first_bad,
        "candidate_count": len(final_candidates),
    })
    return BisectReport(
        session_id=session_id,
        mode="federation",
        task=task,
        variable_repository=variable_repository,
        initial_good=good_sha,
        initial_bad=bad_sha,
        first_bad_commit=first_bad,
        candidates=tuple(final_candidates),
        source_identity=source_identity,
        state_path=str(state_path),
        history_path=str(history_path),
    )


def _fixture_commit(root: Path, value: str, message: str) -> str:
    (root / "state.txt").write_text(value + "\n", encoding="utf-8")
    _require(_run_git(root, "add", "state.txt"), "fixture add failed")
    _require(_run_git(root, "commit", "-m", message), "fixture commit failed")
    return _resolve_commit(root, "HEAD")


def bisect_history_fixture() -> dict[str, Any]:
    """Read-only roadmap fixture using a disposable repository/history."""

    with tempfile.TemporaryDirectory(prefix="devtool-git-frx15-fixture-") as raw:
        root = Path(raw)
        _require(_run_git(root, "init", "-q"), "fixture git init failed")
        _require(_run_git(root, "config", "user.name", "Devtool Fixture"), "fixture git user.name failed")
        _require(_run_git(root, "config", "user.email", "devtool@example.invalid"), "fixture git user.email failed")
        commits = [
            _fixture_commit(root, "good-0", "good-0"),
            _fixture_commit(root, "good-1", "good-1"),
            _fixture_commit(root, "bad-2", "bad-2"),
            _fixture_commit(root, "bad-3", "bad-3"),
        ]
        first_bad_expected = commits[2]

        def evaluator(candidate_root: Path, _task: str, _identity: Mapping[str, Any]) -> int:
            value = (candidate_root / "state.txt").read_text(encoding="utf-8").strip()
            return 0 if value.startswith("good") else 1

        report = run_git_bisect(
            root,
            good=commits[0],
            bad=commits[-1],
            task="workflow:fixture",
            evaluator=evaluator,
        )
        if report.first_bad_commit != first_bad_expected:
            raise GitBisectError(
                f"Bisect fixture expected {first_bad_expected}, got {report.first_bad_commit}"
            )
        if (root / ".git" / "BISECT_LOG").exists() or (root / ".git" / "BISECT_START").exists():
            raise GitBisectError("Managed bisect fixture unexpectedly created global git bisect state")
        return {
            "schema_version": 1,
            "kind": "devtool.git-frx15.bisect-history-fixture",
            "status": "pass",
            "expected_first_bad": first_bad_expected,
            "actual_first_bad": report.first_bad_commit,
            "candidate_count": len(report.candidates),
            "managed_git_bisect_state": "unused",
        }


def _main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="GIT-FRX-15 managed bisect roadmap fixtures")
    sub = parser.add_subparsers(dest="command", required=True)
    fixture = sub.add_parser("fixture")
    fixture.add_argument("--json", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "fixture":
        payload = bisect_history_fixture()
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(_main())


__all__ = [
    "BISECT_SCHEMA_VERSION",
    "BisectReport",
    "BisectTaskOutcome",
    "GitBisectError",
    "bisect_history_fixture",
    "bisect_session_root",
    "parse_federated_range",
    "parse_pin",
    "run_federated_bisect",
    "run_git_bisect",
]
