from __future__ import annotations

"""GIT-FRX-09 parent/child Git lineage evidence.

Planned lineage is derived from the frozen federation source lock before a
child starts.  Final lineage is captured after the child terminates and is
persisted independently of the child's terminal status, so cancellation and
failure remain inspectable.
"""

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Mapping

from .artifacts import load_history
from .git_state import GitStateError, collect_git_state
from .transactions import list_active_transaction_records, read_transaction, transaction_root
from .xdg import state_dir


FEDERATION_GIT_LINEAGE_SCHEMA_VERSION = 1


class FederationGitLineageError(RuntimeError):
    pass


def _safe_ref_component(value: object, *, fallback: str) -> str:
    text = str(value or "").strip()
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in text).strip(".-")
    return cleaned or fallback


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source_repository(source_lock: Mapping[str, Any], repository: str) -> dict[str, Any]:
    for raw in source_lock.get("repositories", []) if isinstance(source_lock, Mapping) else []:
        if isinstance(raw, Mapping) and str(raw.get("repository") or "") == repository:
            return dict(raw)
    return {}


def planned_federated_git_lineage(
    *,
    source_lock: Mapping[str, Any],
    repository: str,
    parent_run_id: str,
    parent_node_id: str,
    child_run_id: str,
    target: str,
    contract_type: str,
    contract_name: str,
    contract_ref: str,
    federation_workflow: str,
    federation_step: str,
) -> dict[str, Any]:
    source = _source_repository(source_lock, repository)
    return {
        "schema_version": FEDERATION_GIT_LINEAGE_SCHEMA_VERSION,
        "kind": "federation-git-lineage",
        "state": "planned",
        "parent_run_id": str(parent_run_id or ""),
        "parent_node_id": str(parent_node_id or ""),
        "child_run_id": str(child_run_id or ""),
        "repository": str(repository or ""),
        "repository_id": str(source.get("repository_id") or ""),
        "target": str(target or ""),
        "contract_type": str(contract_type or "workflow"),
        "contract_name": str(contract_name or ""),
        "contract_ref": str(contract_ref or ""),
        "federation_workflow": str(federation_workflow or ""),
        "federation_step": str(federation_step or ""),
        "frozen_plan_identity": str(source_lock.get("lock_digest") or ""),
        "frozen_plan_equivalence_identity": str(source_lock.get("equivalence_digest") or ""),
        "frozen_repository_source_identity": str(source.get("source_digest") or ""),
        "config_digest": str(source.get("config_digest") or ""),
        "head_tree": str(source.get("head_tree") or ""),
        "remote_locator": str(source.get("remote_locator") or ""),
        "remote_locator_digest": str(source.get("remote_locator_digest") or ""),
        "start_commit": str(source.get("start_commit") or ""),
        "start_unborn": bool(source.get("unborn", False)),
        "planned_branch": str(source.get("branch") or ""),
        "planned_head_ref": str(source.get("head_ref") or ""),
        "expected_result_ref": str(source.get("expected_result_ref") or ""),
        "commit_mode": str(source.get("commit_policy") or "none"),
        "result_commit": "",
        "result_ref": "",
        "rollback_ref": "",
        "overlay_digests": [],
        "supersession_pairs": [],
        "transactions": [],
        "terminal_status": "",
    }


def direct_federated_git_lineage(
    repo_root: str | Path,
    *,
    repository: str,
    parent_run_id: str,
    parent_node_id: str,
    child_run_id: str,
    target: str,
    contract_type: str,
    contract_name: str,
    contract_ref: str,
    federation_step: str,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    repository_id = ""
    start_commit = ""
    start_unborn = False
    branch = ""
    head_ref = ""
    try:
        state = collect_git_state(root)
    except GitStateError:
        state = None
    if state is not None:
        repository_id = str(state.repository_id or "")
        start_commit = str(state.head.get("sha") or "")
        start_unborn = bool(state.head.get("unborn", False))
        branch = str(state.head.get("branch") or "")
        head_ref = str(state.head.get("symbolic_ref") or "")
    return {
        "schema_version": FEDERATION_GIT_LINEAGE_SCHEMA_VERSION,
        "kind": "federation-git-lineage",
        "state": "planned",
        "parent_run_id": str(parent_run_id or ""),
        "parent_node_id": str(parent_node_id or ""),
        "child_run_id": str(child_run_id or ""),
        "repository": str(repository or ""),
        "repository_id": repository_id,
        "target": str(target or ""),
        "contract_type": str(contract_type or ""),
        "contract_name": str(contract_name or ""),
        "contract_ref": str(contract_ref or ""),
        "federation_workflow": "@direct",
        "federation_step": str(federation_step or ""),
        "frozen_plan_identity": "",
        "frozen_plan_equivalence_identity": "",
        "frozen_repository_source_identity": "",
        "config_digest": "",
        "head_tree": "",
        "remote_locator": "",
        "remote_locator_digest": "",
        "start_commit": start_commit,
        "start_unborn": start_unborn,
        "planned_branch": branch,
        "planned_head_ref": head_ref,
        "expected_result_ref": "",
        "commit_mode": "none",
        "result_commit": "",
        "result_ref": "",
        "rollback_ref": "",
        "overlay_digests": [],
        "supersession_pairs": [],
        "transactions": [],
        "terminal_status": "",
    }


def git_lineage_environment(lineage: Mapping[str, Any]) -> dict[str, str]:
    mapping = {
        "DEVTOOL_FEDERATION_GIT_REPOSITORY_ID": lineage.get("repository_id"),
        "DEVTOOL_FEDERATION_GIT_START_COMMIT": lineage.get("start_commit"),
        "DEVTOOL_FEDERATION_GIT_START_UNBORN": "1" if lineage.get("start_unborn") else "0",
        "DEVTOOL_FEDERATION_GIT_EXPECTED_RESULT_REF": lineage.get("expected_result_ref"),
        "DEVTOOL_FEDERATION_GIT_COMMIT_MODE": lineage.get("commit_mode"),
        "DEVTOOL_FEDERATION_FROZEN_PLAN_IDENTITY": lineage.get("frozen_plan_identity"),
        "DEVTOOL_FEDERATION_FROZEN_PLAN_EQUIVALENCE_IDENTITY": lineage.get("frozen_plan_equivalence_identity"),
        "DEVTOOL_FEDERATION_FROZEN_REPOSITORY_SOURCE_IDENTITY": lineage.get("frozen_repository_source_identity"),
        "DEVTOOL_FEDERATION_GIT_CONFIG_DIGEST": lineage.get("config_digest"),
        "DEVTOOL_FEDERATION_GIT_HEAD_TREE": lineage.get("head_tree"),
        "DEVTOOL_FEDERATION_GIT_REMOTE_LOCATOR": lineage.get("remote_locator"),
        "DEVTOOL_FEDERATION_GIT_REMOTE_LOCATOR_DIGEST": lineage.get("remote_locator_digest"),
    }
    return {key: str(value) for key, value in mapping.items() if str(value or "")}


def _transaction_records(repo_root: Path, run_id: str) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    history = transaction_root(repo_root) / "history"
    if history.exists():
        for path in sorted(history.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(payload, dict) and str(payload.get("run_id") or "") == run_id:
                found[str(payload.get("transaction_id") or path.stem)] = payload
    try:
        active = list_active_transaction_records(repo_root)
    except Exception:
        active = []
    for marker in active:
        if str(marker.get("run_id") or "") != run_id:
            continue
        try:
            payload = read_transaction(str(marker.get("path") or ""))
        except Exception:
            continue
        if isinstance(payload, dict):
            found[str(payload.get("transaction_id") or marker.get("transaction_id") or "active")] = payload
    return sorted(found.values(), key=lambda item: (str(item.get("updated_at") or ""), str(item.get("transaction_id") or "")))


def _history_overlay_digests(repo_root: Path, run_id: str) -> list[str]:
    values: list[str] = []
    try:
        entries = load_history(repo_root)
    except Exception:
        entries = []
    for entry in entries:
        if str(entry.get("operation_id") or "") != run_id:
            continue
        digest = str(entry.get("sha256") or "").strip()
        if digest and digest not in values:
            values.append(digest)
    return values


def _private_lineage_refs(repo_root: Path, lineage: Mapping[str, Any], *, result_commit: str) -> tuple[str, str]:
    repository_id = str(lineage.get("repository_id") or "").strip()
    parent_run_id = str(lineage.get("parent_run_id") or "").strip()
    start_commit = str(lineage.get("start_commit") or "").strip()
    if not repository_id or not parent_run_id:
        return "", ""
    federation = _safe_ref_component(parent_run_id, fallback="federation")
    repo = _safe_ref_component(repository_id, fallback="repo")
    expected = str(lineage.get("expected_result_ref") or "").strip()
    result_ref = expected.replace("{federation_run_id}", federation) if expected else f"refs/devtool/federations/{federation}/repos/{repo}/result"
    rollback_ref = (result_ref[:-len("/result")] + "/rollback") if result_ref.endswith("/result") else f"refs/devtool/federations/{federation}/repos/{repo}/rollback"
    updates: list[tuple[str, str]] = []
    if result_commit:
        updates.append((result_ref, result_commit))
    if start_commit:
        updates.append((rollback_ref, start_commit))
    for ref, sha in updates:
        check = subprocess.run(["git", "-C", str(repo_root), "check-ref-format", ref], text=True, capture_output=True, check=False)
        if check.returncode != 0:
            raise FederationGitLineageError(f"Invalid federation Git lineage ref {ref!r}")
        current = subprocess.run(["git", "-C", str(repo_root), "rev-parse", "--verify", "--quiet", ref], text=True, capture_output=True, check=False)
        existing = current.stdout.strip() if current.returncode == 0 else ""
        if existing and existing != sha:
            raise FederationGitLineageError(
                f"Federation Git lineage ref collision: {ref} points to {existing}, expected {sha}"
            )
        if not existing:
            created = subprocess.run(["git", "-C", str(repo_root), "update-ref", ref, sha], text=True, capture_output=True, check=False)
            if created.returncode != 0:
                detail = (created.stderr or created.stdout or "git update-ref failed").strip()
                raise FederationGitLineageError(f"Unable to create federation Git lineage ref {ref}: {detail}")
    return (result_ref if result_commit else "", rollback_ref if start_commit else "")


def finalize_federated_git_lineage(
    repo_root: str | Path,
    planned: Mapping[str, Any],
    *,
    terminal_status: str,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    record = dict(planned)
    child_run_id = str(record.get("child_run_id") or "")
    transactions = _transaction_records(root, child_run_id)
    tx_evidence: list[dict[str, Any]] = []
    modes: list[str] = []
    supersession_pairs: list[dict[str, str]] = []
    recovery_rollback_ref = ""
    for tx in transactions:
        policy = tx.get("commit_policy") if isinstance(tx.get("commit_policy"), dict) else {}
        recovery = tx.get("git_recovery") if isinstance(tx.get("git_recovery"), dict) else {}
        recovery_state = recovery.get("recovery") if isinstance(recovery.get("recovery"), dict) else {}
        mode = str(policy.get("mode") or recovery.get("commit_mode") or "none").strip() or "none"
        modes.append(mode)
        old = str(policy.get("supersedes_commit") or tx.get("supersedes_commit") or "")
        new = str(policy.get("result_commit") or recovery_state.get("result_commit") or "")
        if old or new:
            supersession_pairs.append({"superseded": old, "result": new})
        for raw in recovery_state.get("refs", []) if isinstance(recovery_state.get("refs"), list) else []:
            if not isinstance(raw, Mapping):
                continue
            if str(raw.get("role") or "") in {"federation-start", "backup-head"} and str(raw.get("ref") or ""):
                recovery_rollback_ref = str(raw.get("ref"))
                if str(raw.get("role") or "") == "federation-start":
                    break
        tx_evidence.append({
            "transaction_id": str(tx.get("transaction_id") or ""),
            "status": str(tx.get("status") or ""),
            "artifact_name": str(tx.get("artifact_name") or ""),
            "commit_mode": mode,
            "result_commit": new,
            "supersedes_commit": old,
            "finalization_state": str((recovery.get("finalization") or {}).get("state") or "") if isinstance(recovery.get("finalization"), dict) else "",
        })

    result_commit = ""
    try:
        state = collect_git_state(root)
    except GitStateError:
        state = None
    if state is not None:
        result_commit = str(state.head.get("sha") or "")
        if not record.get("repository_id"):
            record["repository_id"] = str(state.repository_id or "")

    result_ref = ""
    rollback_ref = recovery_rollback_ref
    if state is not None:
        result_ref, canonical_rollback = _private_lineage_refs(root, record, result_commit=result_commit)
        rollback_ref = canonical_rollback or rollback_ref

    distinct_modes = sorted({mode for mode in modes if mode})
    record.update({
        "state": "finalized",
        "terminal_status": str(terminal_status or ""),
        "result_commit": result_commit,
        "result_ref": result_ref,
        "rollback_ref": rollback_ref,
        "commit_mode": distinct_modes[0] if len(distinct_modes) == 1 else "mixed" if distinct_modes else str(record.get("commit_mode") or "none"),
        "overlay_digests": _history_overlay_digests(root, child_run_id),
        "supersession_pairs": supersession_pairs,
        "transactions": tx_evidence,
    })
    return record


def lineage_evidence_path(parent_run_id: str, child_run_id: str) -> Path:
    return (
        state_dir()
        / "federation-git-lineage"
        / _safe_ref_component(parent_run_id, fallback="federation")
        / f"{_safe_ref_component(child_run_id, fallback='child')}.json"
    )


def persist_federated_git_lineage(record: Mapping[str, Any]) -> tuple[Path, str, int]:
    path = lineage_evidence_path(str(record.get("parent_run_id") or ""), str(record.get("child_run_id") or ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(dict(record), indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)
    return path, hashlib.sha256(data).hexdigest(), len(data)


__all__ = [
    "FEDERATION_GIT_LINEAGE_SCHEMA_VERSION",
    "FederationGitLineageError",
    "direct_federated_git_lineage",
    "finalize_federated_git_lineage",
    "git_lineage_environment",
    "lineage_evidence_path",
    "persist_federated_git_lineage",
    "planned_federated_git_lineage",
]
