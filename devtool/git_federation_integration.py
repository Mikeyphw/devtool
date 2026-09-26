from __future__ import annotations

"""GIT-FRX-10 dependency-aware federation integration and publish barrier.

Child execution/result creation stays owned by federation execution, EXO, and
GIT-FRX-09 lineage.  This module consumes the persisted FRX-Y02 checkpoint and
moves user-owned branch pointers only after validation has completed.  It never
pushes, publishes releases, or rolls back an already-published child release.
"""

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Iterable, Mapping, TYPE_CHECKING

from .federation_discovery import resolve_federated_repository
from .federation_plan import project_with_federation_group
from .federation_resume import load_checkpoint
from .git_state import GitStateError, collect_git_state
from .reservations import repo_key
from .xdg import state_dir

if TYPE_CHECKING:
    from .federation_execution import FederatedChildRun
    from .models import FederatedWorkflowConfig, ProjectConfig


SCHEMA_VERSION = 1
INTEGRATION_POLICIES = frozenset({
    "per-repo", "dependency-group", "all-or-nothing-pointer-update"
})


class FederationIntegrationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class IntegrationPointer:
    repository: str
    root: str
    step_ids: tuple[str, ...]
    ref: str
    start_commit: str
    result_commit: str
    status: str
    reason: str = ""
    checked_out_worktree: str = ""
    applied: bool = False
    rolled_back: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "root": self.root,
            "step_ids": list(self.step_ids),
            "ref": self.ref,
            "start_commit": self.start_commit,
            "result_commit": self.result_commit,
            "status": self.status,
            "reason": self.reason,
            "checked_out_worktree": self.checked_out_worktree,
            "applied": self.applied,
            "rolled_back": self.rolled_back,
        }


@dataclass(frozen=True, slots=True)
class FederationIntegrationReport:
    parent_run_id: str
    workflow: str
    group: str
    policy: str
    dry_run: bool
    status: str
    pointers: tuple[IntegrationPointer, ...]
    groups: tuple[dict[str, Any], ...] = ()
    automatic_push: bool = False
    published_release_rollback_owned: bool = False
    evidence_path: str = ""
    evidence_digest: str = ""

    @property
    def ok(self) -> bool:
        return self.status in {"succeeded", "planned", "no-op"}

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "devtool.federation.git-integration",
            "parent_run_id": self.parent_run_id,
            "workflow": self.workflow,
            "group": self.group,
            "policy": self.policy,
            "dry_run": self.dry_run,
            "status": self.status,
            "pointers": [item.as_dict() for item in self.pointers],
            "groups": [dict(item) for item in self.groups],
            "automatic_push": self.automatic_push,
            "published_release_rollback_owned": self.published_release_rollback_owned,
            "evidence_path": self.evidence_path,
            "evidence_digest": self.evidence_digest,
        }


def _run_git(root: Path, *args: str, timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args], text=True, capture_output=True,
            encoding="utf-8", errors="surrogateescape", check=False, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise FederationIntegrationError(f"Unable to execute git {' '.join(args)}: {exc}") from exc


def _git_value(root: Path, *args: str) -> str:
    result = _run_git(root, *args)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "Git command failed").strip()
        raise FederationIntegrationError(f"git {' '.join(args)} failed in {root}: {detail}")
    return (result.stdout or "").strip()


def _safe_ref(ref: str) -> str:
    text = str(ref or "").strip()
    if not text.startswith("refs/heads/"):
        raise FederationIntegrationError(
            f"Integration target {text or '<missing>'!r} is not a local branch ref; refusing pointer mutation"
        )
    if _run_git(Path.cwd(), "check-ref-format", text).returncode != 0:
        # check-ref-format is repository-independent but git -C requires a repo;
        # callers verify again against the actual repository below.
        pass
    return text


def _workflow_for_checkpoint(parent: "ProjectConfig", checkpoint: Mapping[str, Any]):
    group = str(checkpoint.get("group") or "")
    if group:
        scoped, workflow = project_with_federation_group(parent, group)
        return scoped, workflow
    name = str(checkpoint.get("workflow") or "")
    workflow = parent.federation.workflows.get(name)
    if workflow is None:
        raise FederationIntegrationError(f"Unknown federation workflow {name!r} for integration checkpoint")
    return parent, workflow


def publish_barrier_evidence(
    workflow: "FederatedWorkflowConfig", children: Iterable["FederatedChildRun"]
) -> dict[str, Any]:
    child_by_step = {child.step_id: child for child in children}
    prepare = [step.step_id for step in workflow.steps if step.stage == "prepare"]
    publish = [step.step_id for step in workflow.steps if step.stage == "publish"]
    qualified = [
        step_id for step_id in prepare
        if step_id in child_by_step and child_by_step[step_id].status == "succeeded" and child_by_step[step_id].exit_code == 0
    ]
    blocked = [step_id for step_id in prepare if step_id not in qualified]
    enabled = bool(workflow.publish_barrier)
    state = "disabled" if not enabled else "open" if prepare and not blocked else "closed"
    return {
        "schema_version": 1,
        "enabled": enabled,
        "state": state,
        "prepare_steps": prepare,
        "publish_steps": publish,
        "qualified_prepare_steps": qualified,
        "blocking_prepare_steps": blocked,
        "child_publish_owned": True,
        "federation_automatic_publish": False,
        "federation_rollback_published_release": False,
    }


def _dependency_components(workflow: "FederatedWorkflowConfig") -> list[set[str]]:
    by_step = {step.step_id: step for step in workflow.steps}
    adjacency: dict[str, set[str]] = {step_id: set() for step_id in by_step}
    for step in workflow.steps:
        for dep in step.depends_on:
            if dep in adjacency:
                adjacency[step.step_id].add(dep)
                adjacency[dep].add(step.step_id)
    components: list[set[str]] = []
    remaining = set(adjacency)
    while remaining:
        start = min(remaining)
        stack = [start]
        component: set[str] = set()
        while stack:
            current = stack.pop()
            if current in component:
                continue
            component.add(current)
            stack.extend(sorted(adjacency[current] - component))
        remaining -= component
        components.append(component)
    return components


def _checked_out_worktree(root: Path, ref: str) -> str:
    try:
        state = collect_git_state(root)
    except GitStateError as exc:
        raise FederationIntegrationError(f"Unable to inspect Git worktrees for {root}: {exc}") from exc
    # git_state normalizes worktree branches to their short local name while
    # integration lineage intentionally carries the canonical refs/heads/* ref.
    # Compare normalized identities so a checked-out branch can never bypass
    # dirty-worktree/active-operation protection.
    expected_branch = ref.removeprefix("refs/heads/") if ref.startswith("refs/heads/") else ref
    for item in state.worktrees:
        if item.branch == expected_branch:
            return item.root
    return ""


def _worktree_clean(root: Path) -> tuple[bool, str]:
    try:
        state = collect_git_state(root)
    except GitStateError as exc:
        return False, str(exc)
    # Devtool can leave untracked runtime evidence under .devtool/ while a
    # validated child repository is otherwise source-clean.  Ignore only those
    # untracked Devtool-owned paths; tracked/index changes and user untracked
    # paths remain hard blockers.  Git itself still refuses a fast-forward if
    # an ignored untracked runtime path would be overwritten by the result.
    user_untracked = [
        path for path in state.untracked
        if not (str(path).replace("\\", "/") == ".devtool" or str(path).replace("\\", "/").startswith(".devtool/"))
    ]
    if state.staged or state.unstaged or state.unmerged or user_untracked:
        return False, "target branch is checked out in a dirty worktree"
    declared_active = state.operations.get("active")
    if isinstance(declared_active, list):
        active_ops = [str(value) for value in declared_active if str(value)]
    else:
        active_ops = [
            key for key, value in state.operations.items()
            if key != "active" and isinstance(value, Mapping) and bool(value.get("active"))
        ]
    if active_ops:
        return False, f"target worktree has active Git operation(s): {', '.join(sorted(active_ops))}"
    return True, ""


def _preflight_pointer(
    root: Path, *, repository: str, step_ids: tuple[str, ...], ref: str, start: str, result: str
) -> IntegrationPointer:
    if not ref or not start or not result:
        return IntegrationPointer(repository, str(root), step_ids, ref, start, result, "blocked", "missing Git lineage ref/commit")
    if not ref.startswith("refs/heads/"):
        return IntegrationPointer(repository, str(root), step_ids, ref, start, result, "blocked", "integration target is not refs/heads/*")
    check = _run_git(root, "check-ref-format", ref)
    if check.returncode != 0:
        return IntegrationPointer(repository, str(root), step_ids, ref, start, result, "blocked", "invalid integration branch ref")
    for sha, label in ((start, "start"), (result, "result")):
        verify = _run_git(root, "cat-file", "-e", f"{sha}^{{commit}}")
        if verify.returncode != 0:
            return IntegrationPointer(repository, str(root), step_ids, ref, start, result, "blocked", f"{label} commit is unavailable")
    ff = _run_git(root, "merge-base", "--is-ancestor", start, result)
    if ff.returncode != 0:
        return IntegrationPointer(repository, str(root), step_ids, ref, start, result, "blocked", "result is not a fast-forward descendant of validated start")
    current_proc = _run_git(root, "rev-parse", "--verify", ref)
    if current_proc.returncode != 0:
        return IntegrationPointer(repository, str(root), step_ids, ref, start, result, "blocked", "integration branch ref is missing")
    current = current_proc.stdout.strip()
    checked_out = _checked_out_worktree(root, ref)
    if current == result:
        return IntegrationPointer(repository, str(root), step_ids, ref, start, result, "already-integrated", checked_out_worktree=checked_out)
    if current != start:
        return IntegrationPointer(repository, str(root), step_ids, ref, start, result, "blocked", f"branch drifted to {current}", checked_out_worktree=checked_out)
    if checked_out:
        clean, reason = _worktree_clean(Path(checked_out))
        if not clean:
            return IntegrationPointer(repository, str(root), step_ids, ref, start, result, "blocked", reason, checked_out_worktree=checked_out)
    return IntegrationPointer(repository, str(root), step_ids, ref, start, result, "ready", checked_out_worktree=checked_out)


def _move_pointer(item: IntegrationPointer, *, forward: bool) -> None:
    root = Path(item.root)
    old = item.start_commit if forward else item.result_commit
    new = item.result_commit if forward else item.start_commit
    if item.checked_out_worktree:
        worktree = Path(item.checked_out_worktree)
        current = _git_value(worktree, "rev-parse", "HEAD")
        if current != old:
            raise FederationIntegrationError(
                f"Integration pointer drift for {item.repository}: checked-out HEAD is {current}, expected {old}"
            )
        if forward:
            result = _run_git(worktree, "merge", "--ff-only", new)
        else:
            result = _run_git(worktree, "reset", "--keep", new)
    else:
        result = _run_git(root, "update-ref", item.ref, new, old)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "Git pointer update failed").strip()
        raise FederationIntegrationError(
            f"Unable to {'integrate' if forward else 'roll back pointer'} {item.repository} {item.ref}: {detail}"
        )


def _integration_evidence_path(parent: "ProjectConfig", run_id: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in str(run_id or "")) or "run"
    return state_dir() / "federation-integrations" / repo_key(Path(parent.repo_root)) / f"{safe}.json"


def _persist_report(parent: "ProjectConfig", report: FederationIntegrationReport) -> FederationIntegrationReport:
    path = _integration_evidence_path(parent, report.parent_run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = report.as_dict()
    payload["evidence_path"] = ""
    payload["evidence_digest"] = ""
    raw = (json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    final = FederationIntegrationReport(
        parent_run_id=report.parent_run_id, workflow=report.workflow, group=report.group,
        policy=report.policy, dry_run=report.dry_run, status=report.status,
        pointers=report.pointers, groups=report.groups,
        evidence_path=str(path), evidence_digest=digest,
    )
    data = (json.dumps(final.as_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return final


def integrate_federation_checkpoint(
    parent: "ProjectConfig", run_id: str, *, policy: str = "", dry_run: bool = False
) -> FederationIntegrationReport:
    checkpoint = load_checkpoint(parent, run_id)
    scoped, workflow = _workflow_for_checkpoint(parent, checkpoint)
    selected_policy = str(policy or workflow.integration_policy or "per-repo").strip().lower()
    if selected_policy not in INTEGRATION_POLICIES:
        raise FederationIntegrationError(f"Unsupported federation integration policy: {selected_policy!r}")

    child_rows = [row for row in checkpoint.get("children", []) if isinstance(row, Mapping)]
    by_step = {str(row.get("step_id") or ""): row for row in child_rows}
    step_order = {step.step_id: index for index, step in enumerate(workflow.steps)}
    repositories: dict[str, list[Mapping[str, Any]]] = {}
    for row in child_rows:
        repositories.setdefault(str(row.get("repository") or ""), []).append(row)

    pointers: dict[str, IntegrationPointer] = {}
    for repository, rows in repositories.items():
        step_ids = tuple(sorted((str(row.get("step_id") or "") for row in rows), key=lambda value: step_order.get(value, 10**9)))
        if any(str(row.get("status") or "") != "succeeded" or int(row.get("exit_code") or 0) != 0 for row in rows):
            pointers[repository] = IntegrationPointer(repository, "", step_ids, "", "", "", "blocked", "repository validation did not fully succeed")
            continue
        candidates: list[tuple[int, Mapping[str, Any]]] = []
        for row in rows:
            lineage = row.get("git_lineage") if isinstance(row.get("git_lineage"), Mapping) else {}
            if str(lineage.get("result_commit") or ""):
                candidates.append((step_order.get(str(row.get("step_id") or ""), -1), lineage))
        if not candidates:
            pointers[repository] = IntegrationPointer(repository, "", step_ids, "", "", "", "blocked", "no validated Git result commit")
            continue
        candidates.sort(key=lambda item: item[0])
        refs = {str(lineage.get("planned_head_ref") or "") for _, lineage in candidates}
        if len(refs) != 1:
            pointers[repository] = IntegrationPointer(repository, "", step_ids, "", "", "", "blocked", "validated steps disagree on integration branch ref")
            continue
        start = str(candidates[0][1].get("start_commit") or "")
        result = str(candidates[-1][1].get("result_commit") or "")
        ref = next(iter(refs))
        resolution = resolve_federated_repository(scoped, repository)
        if not resolution.available:
            pointers[repository] = IntegrationPointer(repository, str(resolution.root), step_ids, ref, start, result, "blocked", "repository unavailable")
            continue
        pointer = _preflight_pointer(
            Path(resolution.root), repository=repository, step_ids=step_ids,
            ref=ref, start=start, result=result,
        )
        pointers[repository] = pointer

    workflow_components = _dependency_components(workflow)
    component_repositories: list[set[str]] = []
    by_step_cfg = {step.step_id: step for step in workflow.steps}
    for component in workflow_components:
        repos = {by_step_cfg[step_id].repository for step_id in component}
        merged = repos
        survivors: list[set[str]] = []
        for existing in component_repositories:
            if existing & merged:
                merged |= existing
            else:
                survivors.append(existing)
        component_repositories = [*survivors, merged]

    if selected_policy == "per-repo":
        groups = [{"id": f"repo:{repo}", "repositories": [repo]} for repo in sorted(pointers)]
    elif selected_policy == "dependency-group":
        groups = [
            {"id": f"dependency-group:{index+1}", "repositories": sorted(repos)}
            for index, repos in enumerate(component_repositories)
        ]
    else:
        groups = [{"id": "all", "repositories": sorted(pointers)}]

    updated: dict[str, IntegrationPointer] = dict(pointers)
    group_evidence: list[dict[str, Any]] = []
    any_applied = False
    any_blocked = False
    for group in groups:
        repos = list(group["repositories"])
        items = [updated[repo] for repo in repos if repo in updated]
        blockers = [item for item in items if item.status == "blocked"]
        if blockers:
            any_blocked = True
            group_evidence.append({**group, "status": "blocked", "blocking_repositories": [item.repository for item in blockers]})
            if selected_policy == "per-repo":
                continue
            for item in items:
                if item.status == "ready":
                    updated[item.repository] = IntegrationPointer(**{**item.as_dict(), "step_ids": item.step_ids, "status": "withheld", "reason": "integration policy group contains blocked repository"})
            continue
        if dry_run:
            for item in items:
                if item.status == "ready":
                    updated[item.repository] = IntegrationPointer(**{**item.as_dict(), "step_ids": item.step_ids, "status": "planned"})
            group_evidence.append({**group, "status": "planned"})
            continue
        applied: list[IntegrationPointer] = []
        try:
            for item in items:
                if item.status == "already-integrated":
                    continue
                if item.status != "ready":
                    continue
                _move_pointer(item, forward=True)
                applied_item = IntegrationPointer(
                    item.repository, item.root, item.step_ids, item.ref, item.start_commit, item.result_commit,
                    "integrated", item.reason, item.checked_out_worktree, applied=True,
                )
                updated[item.repository] = applied_item
                applied.append(applied_item)
                any_applied = True
            group_evidence.append({**group, "status": "succeeded"})
        except Exception as exc:
            # The item that failed to move was never integrated; make that
            # explicit in durable evidence instead of leaving it in a stale
            # "ready" state.  Only pointers already moved inside this policy
            # group are rolled back.  Unrelated groups/repositories are never
            # rewritten by this recovery path.
            failed_item = item
            if updated.get(failed_item.repository, failed_item).status == "ready":
                updated[failed_item.repository] = IntegrationPointer(
                    failed_item.repository, failed_item.root, failed_item.step_ids, failed_item.ref,
                    failed_item.start_commit, failed_item.result_commit, "blocked", str(exc),
                    failed_item.checked_out_worktree, applied=False, rolled_back=False,
                )
            rollback_errors: list[str] = []
            for item in reversed(applied):
                try:
                    _move_pointer(item, forward=False)
                    updated[item.repository] = IntegrationPointer(
                        item.repository, item.root, item.step_ids, item.ref, item.start_commit, item.result_commit,
                        "rolled-back", str(exc), item.checked_out_worktree, applied=True, rolled_back=True,
                    )
                except Exception as rollback_exc:
                    rollback_errors.append(f"{item.repository}: {rollback_exc}")
            detail = str(exc)
            if rollback_errors:
                detail += "; rollback errors: " + "; ".join(rollback_errors)
            group_evidence.append({**group, "status": "failed", "error": detail})
            any_blocked = True
            if selected_policy != "per-repo":
                break

    values = tuple(updated[key] for key in sorted(updated))
    final_integrated = any(item.status == "integrated" for item in values)
    final_blocked = any(item.status in {"blocked", "withheld", "rolled-back"} for item in values)
    if dry_run:
        status = "planned" if not final_blocked else "partial"
    elif final_integrated and final_blocked:
        status = "partial"
    elif final_blocked:
        status = "blocked"
    elif final_integrated:
        status = "succeeded"
    else:
        status = "no-op"
    report = FederationIntegrationReport(
        parent_run_id=str(run_id), workflow=str(checkpoint.get("workflow") or ""),
        group=str(checkpoint.get("group") or ""), policy=selected_policy, dry_run=dry_run,
        status=status, pointers=values, groups=tuple(group_evidence),
    )
    return _persist_report(parent, report)


def load_federation_integration(parent: "ProjectConfig", run_id: str) -> dict[str, Any] | None:
    path = _integration_evidence_path(parent, run_id)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FederationIntegrationError(f"Cannot read federation integration evidence for {run_id!r}: {exc}") from exc
    if not isinstance(payload, dict) or str(payload.get("parent_run_id") or "") != str(run_id):
        raise FederationIntegrationError(f"Federation integration evidence identity mismatch for {run_id!r}")
    return payload


def recover_federation_checkpoint(parent: "ProjectConfig", run_id: str) -> dict[str, Any]:
    checkpoint = load_checkpoint(parent, run_id)
    integration = load_federation_integration(parent, run_id)
    children = [dict(row) for row in checkpoint.get("children", []) if isinstance(row, Mapping)]
    repositories: dict[str, dict[str, Any]] = {}
    for child in children:
        repository = str(child.get("repository") or "")
        lineage = child.get("git_lineage") if isinstance(child.get("git_lineage"), Mapping) else {}
        row = repositories.setdefault(repository, {"repository": repository, "steps": [], "states": set(), "result_refs": set(), "rollback_refs": set()})
        row["steps"].append(str(child.get("step_id") or ""))
        status = str(child.get("status") or "")
        exit_code = int(child.get("exit_code", 0) or 0)
        if status == "succeeded" and exit_code == 0:
            row["states"].add("validated-only")
        elif status == "cancelled" or exit_code == 130:
            row["states"].add("cancelled")
        else:
            row["states"].add(status or "failed")
        if lineage.get("result_ref"): row["result_refs"].add(str(lineage.get("result_ref")))
        if lineage.get("rollback_ref"): row["rollback_refs"].add(str(lineage.get("rollback_ref")))
    integrated = {}
    if integration:
        for pointer in integration.get("pointers", []):
            if not isinstance(pointer, Mapping): continue
            repository = str(pointer.get("repository") or "")
            integrated[repository] = dict(pointer)
            row = repositories.setdefault(repository, {"repository": repository, "steps": [], "states": set(), "result_refs": set(), "rollback_refs": set()})
            if pointer.get("status") in {"integrated", "already-integrated"} or pointer.get("applied"):
                row["states"].add("integrated")
    rows=[]
    for repository in sorted(repositories):
        row=repositories[repository]
        states=set(row.pop("states")); result_refs=set(row.pop("result_refs")); rollback_refs=set(row.pop("rollback_refs"))
        row.update({
            "states": sorted(states), "result_refs": sorted(result_refs), "rollback_refs": sorted(rollback_refs),
            "integration": integrated.get(repository, {}),
            "rollback_available": bool(rollback_refs or (integrated.get(repository) or {}).get("start_commit")),
        })
        rows.append(row)
    return {
        "schema_version": SCHEMA_VERSION, "kind": "devtool.federation.recovery", "parent_run_id": str(run_id),
        "workflow": str(checkpoint.get("workflow") or ""), "group": str(checkpoint.get("group") or ""),
        "checkpoint_status": str(checkpoint.get("status") or ""), "repositories": rows,
        "integration": integration or {}, "automatic_git_rollback": False,
        "recovery_commands": {
            "resume": f"devtool federation resume {run_id}", "retry": f"devtool federation retry {run_id}",
            "recover": f"devtool federation recover {run_id}",
        },
    }


def rollback_federation_checkpoint(
    parent: "ProjectConfig", run_id: str, *, repository: str = "", integration_group: str = "", dry_run: bool = False
) -> dict[str, Any]:
    if bool(repository) == bool(integration_group):
        raise FederationIntegrationError("federation rollback requires exactly one --repo or --integration-group")
    report = load_federation_integration(parent, run_id)
    if report is None:
        raise FederationIntegrationError(f"No federation integration evidence for run {run_id!r}")
    pointers = [dict(row) for row in report.get("pointers", []) if isinstance(row, Mapping)]
    if repository:
        selected = [row for row in pointers if str(row.get("repository") or "") == str(repository)]
        subject = f"repo:{repository}"
    else:
        group = next((dict(row) for row in report.get("groups", []) if isinstance(row, Mapping) and str(row.get("id") or "") == str(integration_group)), None)
        if group is None:
            raise FederationIntegrationError(f"Unknown integration group {integration_group!r} for run {run_id!r}")
        names = {str(value) for value in group.get("repositories", [])}
        selected = [row for row in pointers if str(row.get("repository") or "") in names]
        subject = str(integration_group)
    if not selected:
        raise FederationIntegrationError(f"No integrated repository pointers selected by {subject!r}")
    plans: list[IntegrationPointer] = []
    for row in selected:
        root = Path(str(row.get("root") or ""))
        item = _preflight_pointer(
            root, repository=str(row.get("repository") or ""), step_ids=tuple(str(x) for x in row.get("step_ids", []) or []),
            ref=str(row.get("ref") or ""), start=str(row.get("start_commit") or ""), result=str(row.get("result_commit") or ""),
        )
        if item.status != "already-integrated":
            raise FederationIntegrationError(
                f"Rollback refused for {item.repository}: integration pointer is not exactly at the validated result ({item.status}: {item.reason})"
            )
        if item.checked_out_worktree:
            clean, reason = _worktree_clean(Path(item.checked_out_worktree))
            if not clean:
                raise FederationIntegrationError(f"Rollback refused for {item.repository}: {reason}")
        plans.append(item)
    applied=[]
    if not dry_run:
        for item in reversed(plans):
            _move_pointer(item, forward=False)
            applied.append(item.repository)
    evidence = {
        "schema_version": SCHEMA_VERSION, "kind": "devtool.federation.rollback", "parent_run_id": str(run_id),
        "subject": subject, "dry_run": bool(dry_run), "repositories": [item.as_dict() for item in plans],
        "applied_repositories": applied, "automatic_push": False, "published_release_rollback_owned": False,
    }
    path = state_dir() / "federation-rollbacks" / repo_key(Path(parent.repo_root)) / f"{run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    evidence["evidence_path"] = str(path)
    return evidence


__all__ = ["FederationIntegrationError", "FederationIntegrationReport", "IntegrationPointer", "integrate_federation_checkpoint", "load_federation_integration", "publish_barrier_evidence", "recover_federation_checkpoint", "rollback_federation_checkpoint"]
