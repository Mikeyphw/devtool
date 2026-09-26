from __future__ import annotations

"""GIT-FRX-12 multi-repository campaign generations and range-diff evidence."""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any, Mapping

from .federation_discovery import resolve_federated_repository
from .federation_plan import build_federated_group_plan, build_federated_plan, project_with_federation_group
from .git_dirty_state import capture_dirty_tree, load_dirty_tree_snapshot, restore_dirty_tree, validate_dirty_tree_rebase
from .git_execution_notes import GitExecutionNoteError, read_execution_note
from .git_state import GitStateError, collect_git_state
from .xdg import state_dir

CAMPAIGN_SCHEMA_VERSION = 1
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class GitCampaignError(RuntimeError):
    pass


def _safe(value: str, fallback: str = "campaign") -> str:
    raw = str(value or "").strip()
    clean = _SAFE.sub("-", raw).strip(".-")
    if not clean or clean.endswith(".lock") or "@{" in clean:
        clean = fallback
    if len(clean) > 96:
        clean = clean[:72].rstrip(".-") + "-" + hashlib.sha256(raw.encode()).hexdigest()[:16]
    return clean


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitCampaignError(f"Unable to execute git {' '.join(args)}: {exc}") from exc


def _require(proc: subprocess.CompletedProcess[str], context: str) -> str:
    if proc.returncode == 0:
        return (proc.stdout or "").strip()
    detail = (proc.stderr or proc.stdout or "git command failed").strip()
    raise GitCampaignError(f"{context}: {detail}")


def campaign_root(campaign_id: str) -> Path:
    return state_dir() / "git-campaigns" / _safe(campaign_id)


def campaign_path(campaign_id: str) -> Path:
    return campaign_root(campaign_id) / "campaign.json"


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(dict(payload), indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def load_campaign(campaign_id: str) -> dict[str, Any]:
    path = campaign_path(campaign_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GitCampaignError(f"Unable to read Git campaign {campaign_id!r}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("kind") != "devtool.git-campaign":
        raise GitCampaignError(f"Invalid Git campaign state: {path}")
    return payload


def _subject_plan(parent: Any, *, workflow: str = "", group: str = "") -> tuple[Any, Any, str, str]:
    workflow = str(workflow or "").strip()
    group = str(group or "").strip()
    if bool(workflow) == bool(group):
        raise GitCampaignError("Exactly one of workflow or group is required")
    if group:
        scoped, wf = project_with_federation_group(parent, group)
        return scoped, build_federated_group_plan(parent, group), wf.name, group
    return parent, build_federated_plan(parent, workflow), workflow, ""


def _contract_identity(scoped: Any, plan: Any, workflow_name: str, group_name: str) -> dict[str, Any]:
    workflow = scoped.federation.workflows.get(workflow_name)
    steps = []
    if workflow is not None:
        for step in workflow.steps:
            steps.append({
                "step_id": step.step_id,
                "ref": step.ref,
                "depends_on": list(step.depends_on),
                "stage": step.stage,
                "artifact_inputs": [item.as_dict() for item in step.artifact_inputs],
            })
    payload = {
        "subject_kind": "group" if group_name else "workflow",
        "workflow": workflow_name,
        "group": group_name,
        "capability_graph": dict(getattr(plan, "capability_graph", {}) or {}),
        "steps": steps,
        "integration_policy": str(getattr(workflow, "integration_policy", "") or ""),
        "publish_barrier": bool(getattr(workflow, "publish_barrier", False)),
        "node_contracts": [
            {
                "key": node.key,
                "repository": node.repository,
                "target": node.target,
                "depends_on": list(node.depends_on),
                "contract_ref": str(node.metadata.get("contract_ref") or node.metadata.get("federation_ref") or ""),
                "artifact_inputs": list(node.metadata.get("artifact_inputs") or []),
                "stage": str(node.metadata.get("federation_stage") or ""),
            }
            for node in plan.nodes if not node.synthetic
        ],
    }
    payload["digest"] = _digest(payload)
    return payload


def _execution_note_identity(root: Path, commit: str) -> tuple[str, list[str]]:
    try:
        note = read_execution_note(root, commit)
    except GitExecutionNoteError:
        return "", []
    records = note.get("records") if isinstance(note.get("records"), list) else []
    run_ids: list[str] = []
    for row in records:
        if not isinstance(row, Mapping):
            continue
        for key in ("run_id", "child_run_id", "parent_run_id", "validation_run_id"):
            value = str(row.get(key) or "").strip()
            if value and value not in run_ids:
                run_ids.append(value)
    return _digest(note), run_ids


def _campaign_repo_ref(campaign_id: str, repository_id: str, repository: str, suffix: str) -> str:
    repo = _safe(repository_id or repository, "repo")
    return f"refs/devtool/campaigns/{_safe(campaign_id)}/repos/{repo}/{suffix.strip('/')}"


def _set_private_ref(root: Path, ref: str, commit: str, *, expected: str = "") -> None:
    _require(_git(root, "check-ref-format", ref), f"Invalid campaign ref {ref}")
    args = ["update-ref", ref, commit]
    if expected:
        args.append(expected)
    _require(_git(root, *args), f"Unable to update campaign ref {ref}")


def _resolve_generation_selector(payload: Mapping[str, Any], selector: str | int) -> Mapping[str, Any]:
    text = str(selector).strip()
    if text.isdigit():
        return _generation_by_number(payload, int(text))
    for item in payload.get("generations", []) if isinstance(payload.get("generations"), list) else []:
        if isinstance(item, Mapping) and str(item.get("step_id") or "") == text:
            return item
    raise GitCampaignError(f"Campaign generation/step {selector!r} does not exist")


def _repository_by_name(generation: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {str(item.get("repository") or ""): item for item in generation.get("repositories", []) if isinstance(item, Mapping)}


def _repository_state(parent: Any, repository: str) -> dict[str, Any]:
    resolution = resolve_federated_repository(parent, repository)
    if not resolution.available:
        raise GitCampaignError(f"Federation repository {repository!r} is unavailable")
    try:
        state = collect_git_state(Path(resolution.root))
    except GitStateError as exc:
        raise GitCampaignError(f"Campaign repository {repository!r} is not a Git worktree: {exc}") from exc
    commit = str(state.head.get("sha") or "")
    if not commit:
        raise GitCampaignError(f"Campaign repository {repository!r} has no committed HEAD")
    note_digest, validation_run_ids = _execution_note_identity(Path(resolution.root), commit)
    return {
        "repository": repository,
        "root": str(Path(resolution.root).resolve()),
        "repository_id": str(state.repository_id or ""),
        "commit": commit,
        "head_ref": str(state.head.get("symbolic_ref") or ""),
        "branch": str(state.head.get("branch") or ""),
        "dirty": bool(state.staged or state.unstaged or state.untracked or state.unmerged),
        "execution_note_digest": note_digest,
        "validation_run_ids": validation_run_ids,
    }


def _generation_ref(campaign_id: str, repository_id: str, repository: str, generation: int) -> str:
    repo = _safe(repository_id or repository, "repo")
    return f"refs/devtool/campaigns/{_safe(campaign_id)}/repos/{repo}/generations/{int(generation)}"


def _set_generation_ref(root: Path, ref: str, commit: str) -> None:
    check = _git(root, "check-ref-format", ref)
    _require(check, f"Invalid campaign ref {ref}")
    current = _git(root, "rev-parse", "--verify", "--quiet", ref)
    existing = (current.stdout or "").strip() if current.returncode == 0 else ""
    if existing and existing != commit:
        raise GitCampaignError(f"Campaign generation ref collision: {ref} -> {existing}, expected {commit}")
    if not existing:
        _require(_git(root, "update-ref", ref, commit), f"Unable to create campaign generation ref {ref}")


def _generation(parent: Any, plan: Any, campaign_id: str, generation: int, *, step_id: str = "") -> dict[str, Any]:
    repositories = []
    for repository in plan.repositories:
        row = _repository_state(parent, repository)
        ref = _generation_ref(campaign_id, row["repository_id"], repository, generation)
        _set_generation_ref(Path(row["root"]), ref, row["commit"])
        row["generation_ref"] = ref
        repositories.append(row)
    source_lock = dict(getattr(plan, "source_lock", {}) or {})
    payload = {
        "generation": int(generation),
        "step_id": str(step_id or ("base" if int(generation) == 0 else f"generation-{int(generation)}")),
        "repositories": repositories,
        "federation_lock": {
            "lock_digest": str(source_lock.get("lock_digest") or ""),
            "local_digest": str(source_lock.get("local_digest") or ""),
            "equivalence_digest": str(source_lock.get("equivalence_digest") or ""),
            "graph_digest": str(source_lock.get("graph_digest") or ""),
        },
    }
    payload["generation_digest"] = _digest(payload)
    return payload


def start_campaign(parent: Any, campaign_id: str, *, workflow: str = "", group: str = "") -> dict[str, Any]:
    cid = str(campaign_id or "").strip()
    if not cid:
        raise GitCampaignError("Campaign id is required")
    path = campaign_path(cid)
    if path.exists():
        raise GitCampaignError(f"Campaign {cid!r} already exists")
    scoped, plan, workflow_name, group_name = _subject_plan(parent, workflow=workflow, group=group)
    root = campaign_root(cid)
    dirty_rows = []
    for repository in plan.repositories:
        resolution = resolve_federated_repository(parent, repository)
        snapshot_dir = root / "dirty" / _safe(repository, "repo")
        snapshot = capture_dirty_tree(resolution.root, snapshot_dir)
        dirty_rows.append({
            "repository": repository,
            "snapshot": str(snapshot_dir),
            "snapshot_digest": str(snapshot.get("snapshot_digest") or ""),
            "dirty": bool(snapshot.get("dirty")),
            "paths": list(snapshot.get("paths") or []),
        })
    generation = _generation(parent, plan, cid, 0, step_id="base")
    contract = _contract_identity(scoped, plan, workflow_name, group_name)
    payload = {
        "schema_version": CAMPAIGN_SCHEMA_VERSION,
        "kind": "devtool.git-campaign",
        "campaign_id": cid,
        "subject_kind": "group" if group_name else "workflow",
        "workflow": workflow_name,
        "group": group_name,
        "contract_identity": contract,
        "dirty_tree_snapshots": dirty_rows,
        "generations": [generation],
        "active_generation": 0,
        "step_generations": {"base": 0},
        "rollbacks": [],
        "supersessions": [],
        "automatic_push": False,
    }
    for row in generation["repositories"]:
        root = Path(str(row["root"]))
        base_ref = _campaign_repo_ref(cid, str(row.get("repository_id") or ""), str(row.get("repository") or ""), "base")
        tip_ref = _campaign_repo_ref(cid, str(row.get("repository_id") or ""), str(row.get("repository") or ""), "tip")
        step_ref = _campaign_repo_ref(cid, str(row.get("repository_id") or ""), str(row.get("repository") or ""), "steps/base")
        _set_private_ref(root, base_ref, str(row["commit"]))
        _set_private_ref(root, tip_ref, str(row["commit"]))
        _set_private_ref(root, step_ref, str(row["commit"]))
        row["base_ref"] = base_ref
        row["tip_ref"] = tip_ref
        row["step_ref"] = step_ref
    payload["campaign_digest"] = _digest({key: value for key, value in payload.items() if key != "campaign_digest"})
    _atomic_write(path, payload)
    return payload


def checkpoint_campaign(parent: Any, campaign_id: str, *, step_id: str = "") -> dict[str, Any]:
    payload = load_campaign(campaign_id)
    scoped, plan, workflow_name, group_name = _subject_plan(
        parent,
        workflow=str(payload.get("workflow") or "") if not payload.get("group") else "",
        group=str(payload.get("group") or ""),
    )
    contract = _contract_identity(scoped, plan, workflow_name, group_name)
    prior_contract = payload.get("contract_identity") if isinstance(payload.get("contract_identity"), Mapping) else {}
    if str(prior_contract.get("digest") or "") != str(contract.get("digest") or ""):
        raise GitCampaignError("Campaign contract identity drifted; start a new campaign instead of mixing contract generations")
    generation_number = len(payload.get("generations", []))
    resolved_step = str(step_id or f"generation-{generation_number}").strip()
    if resolved_step in dict(payload.get("step_generations") or {}):
        raise GitCampaignError(f"Campaign step {resolved_step!r} already has a generation")
    generation = _generation(parent, plan, campaign_id, generation_number, step_id=resolved_step)
    prior = _generation_by_number(payload, int(payload.get("active_generation", generation_number - 1) or 0))
    prior_by = _repository_by_name(prior)
    for row in generation["repositories"]:
        root = Path(str(row["root"]))
        repo = str(row.get("repository") or "")
        tip_ref = _campaign_repo_ref(campaign_id, str(row.get("repository_id") or ""), repo, "tip")
        step_ref = _campaign_repo_ref(campaign_id, str(row.get("repository_id") or ""), repo, f"steps/{_safe(resolved_step, 'step')}")
        expected = str((prior_by.get(repo) or {}).get("commit") or "")
        _set_private_ref(root, tip_ref, str(row["commit"]), expected=expected)
        _set_private_ref(root, step_ref, str(row["commit"]))
        row["tip_ref"] = tip_ref
        row["step_ref"] = step_ref
    payload.setdefault("generations", []).append(generation)
    payload.setdefault("step_generations", {})[resolved_step] = generation_number
    payload["active_generation"] = generation_number
    payload["campaign_digest"] = _digest({key: value for key, value in payload.items() if key != "campaign_digest"})
    _atomic_write(campaign_path(campaign_id), payload)
    return generation


def _generation_by_number(payload: Mapping[str, Any], number: int) -> Mapping[str, Any]:
    for item in payload.get("generations", []) if isinstance(payload.get("generations"), list) else []:
        if isinstance(item, Mapping) and int(item.get("generation", -1)) == int(number):
            return item
    raise GitCampaignError(f"Campaign generation {number} does not exist")


def range_diff_campaign(campaign_id: str, *, old_generation: int, new_generation: int) -> dict[str, Any]:
    payload = load_campaign(campaign_id)
    base = _generation_by_number(payload, 0)
    old = _generation_by_number(payload, old_generation)
    new = _generation_by_number(payload, new_generation)
    base_by = {str(item.get("repository")): item for item in base.get("repositories", []) if isinstance(item, Mapping)}
    old_by = {str(item.get("repository")): item for item in old.get("repositories", []) if isinstance(item, Mapping)}
    new_by = {str(item.get("repository")): item for item in new.get("repositories", []) if isinstance(item, Mapping)}
    rows = []
    for repository in sorted(set(base_by) & set(old_by) & set(new_by)):
        root = Path(str(new_by[repository].get("root") or old_by[repository].get("root") or base_by[repository].get("root") or "")).resolve()
        base_commit = str(base_by[repository].get("commit") or "")
        old_commit = str(old_by[repository].get("commit") or "")
        new_commit = str(new_by[repository].get("commit") or "")
        proc = _git(root, "range-diff", "--no-color", f"{base_commit}..{old_commit}", f"{base_commit}..{new_commit}")
        if proc.returncode not in {0, 1}:
            detail = (proc.stderr or proc.stdout or "git range-diff failed").strip()
            raise GitCampaignError(f"Unable to range-diff campaign repository {repository!r}: {detail}")
        text = (proc.stdout or "").rstrip()
        if old_commit == new_commit:
            change_kind = "unchanged"
        elif _git(root, "merge-base", "--is-ancestor", old_commit, new_commit).returncode == 0:
            change_kind = "forward"
        elif _git(root, "merge-base", "--is-ancestor", new_commit, old_commit).returncode == 0:
            change_kind = "rewind"
        else:
            change_kind = "replacement"
        explicit = [
            row for row in payload.get("supersessions", []) if isinstance(row, Mapping)
            and int(row.get("old_generation", -1)) == int(old_generation)
            and int(row.get("new_generation", -1)) == int(new_generation)
        ]
        if explicit:
            change_kind = "superseded"
        old_note = str(old_by[repository].get("execution_note_digest") or "")
        new_note = str(new_by[repository].get("execution_note_digest") or "")
        rows.append({
            "repository": repository,
            "base_commit": base_commit,
            "old_commit": old_commit,
            "new_commit": new_commit,
            "old_step": str(old.get("step_id") or ""),
            "new_step": str(new.get("step_id") or ""),
            "change_kind": change_kind,
            "validation_evidence_changed": old_note != new_note,
            "old_validation_evidence_digest": old_note,
            "new_validation_evidence_digest": new_note,
            "range_diff": text,
            "range_diff_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        })
    report = {
        "schema_version": CAMPAIGN_SCHEMA_VERSION,
        "kind": "devtool.git-campaign-range-diff",
        "campaign_id": str(payload.get("campaign_id") or campaign_id),
        "old_generation": int(old_generation),
        "new_generation": int(new_generation),
        "federation_lock_generations": [
            {
                "generation": int(item.get("generation", 0)),
                "lock_digest": str((item.get("federation_lock") or {}).get("lock_digest") or "") if isinstance(item.get("federation_lock"), Mapping) else "",
            }
            for item in (old, new)
        ],
        "contract_digest": str((payload.get("contract_identity") or {}).get("digest") or "") if isinstance(payload.get("contract_identity"), Mapping) else "",
        "repositories": rows,
    }
    report["report_digest"] = _digest(report)
    return report


def rollback_campaign(campaign_id: str, selector: str | int) -> dict[str, Any]:
    payload = load_campaign(campaign_id)
    target = _resolve_generation_selector(payload, selector)
    current = _resolve_generation_selector(payload, int(payload.get("active_generation", 0) or 0))
    current_by = _repository_by_name(current)
    target_by = _repository_by_name(target)
    rows: list[dict[str, Any]] = []
    for repository in sorted(target_by):
        target_row = target_by[repository]
        current_row = current_by.get(repository) or {}
        root = Path(str(target_row.get("root") or "")).resolve()
        tip_ref = _campaign_repo_ref(campaign_id, str(target_row.get("repository_id") or ""), repository, "tip")
        expected = str(current_row.get("commit") or "")
        _set_private_ref(root, tip_ref, str(target_row.get("commit") or ""), expected=expected)
        rows.append({"repository": repository, "tip_ref": tip_ref, "from": expected, "to": str(target_row.get("commit") or "")})
    event = {
        "from_generation": int(current.get("generation", 0)),
        "to_generation": int(target.get("generation", 0)),
        "to_step": str(target.get("step_id") or ""),
        "repositories": rows,
        "user_branch_mutation": False,
    }
    payload.setdefault("rollbacks", []).append(event)
    payload["active_generation"] = int(target.get("generation", 0))
    payload["campaign_digest"] = _digest({key: value for key, value in payload.items() if key != "campaign_digest"})
    _atomic_write(campaign_path(campaign_id), payload)
    return {"schema_version": CAMPAIGN_SCHEMA_VERSION, "kind": "devtool.git-campaign-rollback", "campaign_id": campaign_id, **event}


def supersede_campaign(campaign_id: str, old_generation: int, new_generation: int) -> dict[str, Any]:
    payload = load_campaign(campaign_id)
    old = _generation_by_number(payload, old_generation)
    new = _generation_by_number(payload, new_generation)
    if int(old_generation) == int(new_generation):
        raise GitCampaignError("A campaign generation cannot supersede itself")
    old_by = _repository_by_name(old); new_by = _repository_by_name(new)
    if set(old_by) != set(new_by):
        raise GitCampaignError("Supersede generations do not contain the same repository set")
    event = {
        "old_generation": int(old_generation),
        "new_generation": int(new_generation),
        "old_step": str(old.get("step_id") or ""),
        "new_step": str(new.get("step_id") or ""),
        "repositories": [],
        "user_branch_mutation": False,
    }
    active = _resolve_generation_selector(payload, int(payload.get("active_generation", 0) or 0))
    active_by = _repository_by_name(active)
    for repository in sorted(new_by):
        row = new_by[repository]
        root = Path(str(row.get("root") or "")).resolve()
        tip_ref = _campaign_repo_ref(campaign_id, str(row.get("repository_id") or ""), repository, "tip")
        expected = str((active_by.get(repository) or {}).get("commit") or "")
        _set_private_ref(root, tip_ref, str(row.get("commit") or ""), expected=expected)
        event["repositories"].append({"repository": repository, "tip_ref": tip_ref, "from": str(old_by[repository].get("commit") or ""), "to": str(row.get("commit") or "")})
    payload.setdefault("supersessions", []).append(event)
    payload["active_generation"] = int(new_generation)
    payload["campaign_digest"] = _digest({key: value for key, value in payload.items() if key != "campaign_digest"})
    _atomic_write(campaign_path(campaign_id), payload)
    return {"schema_version": CAMPAIGN_SCHEMA_VERSION, "kind": "devtool.git-campaign-supersession", "campaign_id": campaign_id, **event}


def restore_campaign_dirty_tree(campaign_id: str, *, repository: str = "") -> dict[str, Any]:
    payload = load_campaign(campaign_id)
    selected = []
    for row in payload.get("dirty_tree_snapshots", []) if isinstance(payload.get("dirty_tree_snapshots"), list) else []:
        if not isinstance(row, Mapping):
            continue
        name = str(row.get("repository") or "")
        if repository and name != repository:
            continue
        snapshot_path = str(row.get("snapshot") or "")
        snapshot = load_dirty_tree_snapshot(snapshot_path)
        root = Path(str(snapshot.get("repository_root") or "")).resolve()
        current = _require(_git(root, "rev-parse", "--verify", "HEAD"), f"Unable to resolve current HEAD for campaign repository {name!r}")
        if current != str(snapshot.get("head") or ""):
            validate_dirty_tree_rebase(snapshot_path, result_commit=current)
        result = restore_dirty_tree(snapshot_path, allow_head_change=True, expected_current_head=current)
        selected.append({"repository": name, "restore": result})
    if repository and not selected:
        raise GitCampaignError(f"Campaign {campaign_id!r} has no dirty-tree snapshot for repository {repository!r}")
    return {
        "schema_version": CAMPAIGN_SCHEMA_VERSION,
        "kind": "devtool.git-campaign-dirty-restore",
        "campaign_id": campaign_id,
        "repositories": selected,
    }


def _fixture_child(root: Path, label: str) -> str:
    root.mkdir()
    for args in (("init", "-q"), ("config", "user.name", "Devtool Fixture"), ("config", "user.email", "fixture@example.invalid")):
        _require(_git(root, *args), "Unable to initialize campaign fixture child")
    (root / ".devtool.toml").write_text(
        '[project]\nrunner="command"\n[workspace]\ndefault_target="app"\n'
        '[targets.app]\nrunner="command"\n[targets.app.jobs.check]\nrunner="command"\n'
        'command=["python","-c","print(\'ok\')"]\nmetadata={read_only=true}\n'
        '[targets.app.workflows]\nvalidate=["job:check"]\n', encoding="utf-8")
    (root / "stack.txt").write_text(f"{label}-base\n", encoding="utf-8")
    _require(_git(root, "add", "."), "Unable to stage campaign fixture child")
    _require(_git(root, "commit", "-qm", "base"), "Unable to commit campaign fixture child")
    return _require(_git(root, "rev-parse", "HEAD"), "Unable to resolve campaign fixture base")


def _fixture_commit(root: Path, text: str, message: str) -> str:
    (root / "stack.txt").write_text(text + "\n", encoding="utf-8")
    _require(_git(root, "add", "stack.txt"), "Unable to stage campaign fixture generation")
    _require(_git(root, "commit", "-qm", message), "Unable to commit campaign fixture generation")
    return _require(_git(root, "rev-parse", "HEAD"), "Unable to resolve campaign fixture generation")


def _fixture() -> dict[str, Any]:
    """Build two campaign generations and verify per-repository range-diff evidence."""
    from .config import load_project_config

    with tempfile.TemporaryDirectory(prefix="devtool-git-frx12-") as td:
        base = Path(td)
        previous_xdg = os.environ.get("DEVTOOL_XDG_DIR")
        os.environ["DEVTOOL_XDG_DIR"] = str(base / "xdg")
        try:
            parent = base / "parent"; a = base / "a"; b = base / "b"
            parent.mkdir()
            a_base = _fixture_child(a, "a")
            b_base = _fixture_child(b, "b")
            (parent / ".devtool.toml").write_text(
                '[project]\nrunner="command"\n[workspace]\ndefault_target="root"\n[targets.root]\nrunner="command"\n'
                '[federation]\nschema=1\n[federation.repositories.a]\npath="../a"\n'
                '[federation.repositories.a.exports]\ncapabilities=["lib/v1"]\n'
                '[federation.repositories.b]\npath="../b"\n[federation.repositories.b.requires]\ncapabilities=["lib/v1"]\n'
                '[federation.workflows.release]\nsteps=['
                '{id="a",ref="a::app::validate"},'
                '{id="b",ref="b::app::validate",depends_on=["a"]}]\n', encoding="utf-8")
            config = load_project_config(parent, parent / ".devtool.toml", target_name="", allow_ambiguous_target=True, allow_ambiguous_project_kind=True)
            started = start_campaign(config, "fixture-campaign", workflow="release")
            _fixture_commit(a, "a-v1", "a v1"); _fixture_commit(b, "b-v1", "b v1")
            generation1 = checkpoint_campaign(config, "fixture-campaign")
            _require(_git(a, "reset", "--hard", "-q", a_base), "Unable to rewind campaign fixture a")
            _require(_git(b, "reset", "--hard", "-q", b_base), "Unable to rewind campaign fixture b")
            _fixture_commit(a, "a-v2", "a v2"); _fixture_commit(b, "b-v2", "b v2")
            generation2 = checkpoint_campaign(config, "fixture-campaign")
            diff = range_diff_campaign("fixture-campaign", old_generation=1, new_generation=2)
            if len(diff.get("repositories", [])) != 2:
                raise GitCampaignError("GIT-FRX-12 fixture did not produce a range-diff for each repository")
            return {
                "schema_version": CAMPAIGN_SCHEMA_VERSION,
                "roadmap_id": "GIT-FRX-12",
                "campaign_id": started.get("campaign_id", "fixture-campaign"),
                "contract_digest": (started.get("contract_identity") or {}).get("digest", ""),
                "generations": [0, generation1.get("generation"), generation2.get("generation")],
                "range_diff_report_digest": diff.get("report_digest", ""),
                "repositories": [row.get("repository") for row in diff.get("repositories", [])],
                "automatic_push": started.get("automatic_push", True),
            }
        finally:
            if previous_xdg is None:
                os.environ.pop("DEVTOOL_XDG_DIR", None)
            else:
                os.environ["DEVTOOL_XDG_DIR"] = previous_xdg


def _main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="python -m devtool.git_campaign")
    parser.add_argument("command", choices=["fixture"])
    args = parser.parse_args(argv)
    if args.command != "fixture":
        raise SystemExit(2)
    print(json.dumps(_fixture(), indent=2, sort_keys=True))
    return 0


__all__ = [
    "CAMPAIGN_SCHEMA_VERSION",
    "GitCampaignError",
    "campaign_path",
    "campaign_root",
    "checkpoint_campaign",
    "load_campaign",
    "range_diff_campaign",
    "rollback_campaign",
    "supersede_campaign",
    "restore_campaign_dirty_tree",
    "start_campaign",
]


if __name__ == "__main__":
    raise SystemExit(_main())
