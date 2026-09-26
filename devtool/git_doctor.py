from __future__ import annotations

"""GIT-FRX-17 Git and federation health diagnostics with conservative repair."""

import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any, Mapping

from .federation_discovery import resolve_federated_repository
from .git_state import GitStateError, collect_git_state
from .transactions import list_active_transaction_records, transaction_root
from .xdg import state_dir

DOCTOR_SCHEMA_VERSION = 1


class GitDoctorError(RuntimeError):
    pass


def _git(root: Path, *args: str, timeout: float = 20.0) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(["git", "-C", str(root), *args], text=True, encoding="utf-8", errors="surrogateescape", stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitDoctorError(f"Unable to execute git {' '.join(args)}: {exc}") from exc


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _check(checks: list[dict[str, Any]], name: str, status: str, detail: str, *, repairable: bool = False, **data: Any) -> None:
    checks.append({"name": name, "status": status, "detail": detail, "repairable": repairable, **data})


def git_doctor(repo_root: str | Path, *, repair_safe: bool = False) -> dict[str, Any]:
    root = Path(repo_root).expanduser().resolve(strict=False)
    checks: list[dict[str, Any]] = []
    repairs: list[dict[str, Any]] = []
    try:
        state = collect_git_state(root)
    except GitStateError as exc:
        return {"schema_version": DOCTOR_SCHEMA_VERSION, "kind": "devtool.git-doctor", "ok": False, "repository": str(root), "checks": [{"name": "repository", "status": "error", "detail": str(exc), "repairable": False}], "repairs": []}

    _check(checks, "repository", "ok", f"repository_id={state.repository_id}")
    head = state.head
    if not str(head.get("sha") or ""):
        _check(checks, "head", "warning", "repository has an unborn HEAD")
    elif bool(head.get("detached")):
        _check(checks, "head", "warning", f"detached HEAD at {head.get('sha')}")
    else:
        _check(checks, "head", "ok", str(head.get("symbolic_ref") or head.get("branch") or head.get("sha")))

    upstream = state.upstream
    if upstream.get("configured"):
        ahead = int(upstream.get("ahead", 0) or 0); behind = int(upstream.get("behind", 0) or 0)
        status = "warning" if behind or ahead else "ok"
        _check(checks, "upstream", status, f"ahead={ahead} behind={behind}; {upstream.get('ref') or upstream.get('name') or ''}")
    else:
        _check(checks, "upstream", "info", "no upstream configured")

    dirty_detail = f"staged={len(state.staged)} unstaged={len(state.unstaged)} untracked={len(state.untracked)} unmerged={len(state.unmerged)}"
    _check(checks, "working-tree", "warning" if state.dirty else "ok", dirty_detail)
    active_operations = [name for name, value in state.operations.items() if bool(value)]
    _check(checks, "git-operation", "warning" if active_operations else "ok", ", ".join(active_operations) if active_operations else "none")
    _check(checks, "clone-shape", "warning" if (state.shallow or state.partial_clone) else "ok", f"shallow={state.shallow} partial={state.partial_clone}")

    fsck = _git(root, "fsck", "--connectivity-only", "--no-dangling", timeout=30)
    _check(checks, "objects", "ok" if fsck.returncode == 0 else "error", (fsck.stderr or fsck.stdout or "object connectivity ok").strip() or "object connectivity ok")

    prunable = [row for row in state.worktrees if row.prunable]
    _check(checks, "worktrees", "warning" if prunable else "ok", f"registered={len(state.worktrees)} prunable={len(prunable)}", repairable=bool(prunable))
    if repair_safe and prunable:
        proc = _git(root, "worktree", "prune")
        repairs.append({"kind": "worktree-prune", "ok": proc.returncode == 0, "detail": (proc.stderr or proc.stdout or "pruned stale worktree registrations").strip()})

    common = Path(state.git_common_dir)
    locks = [path for name in ("index.lock", "packed-refs.lock", "shallow.lock", "config.lock") if (path := common / name).exists()]
    _check(checks, "locks", "warning" if locks else "ok", ", ".join(str(path) for path in locks) if locks else "none", repairable=False)

    refs = _git(root, "for-each-ref", "--format=%(refname)%00%(objectname)", "refs/devtool")
    devtool_refs = [line for line in (refs.stdout or "").splitlines() if line] if refs.returncode == 0 else []
    _check(checks, "devtool-refs", "ok", f"count={len(devtool_refs)}")

    transactions = list_active_transaction_records(root)
    stale = [tx for tx in transactions if not _alive(int(tx.get("pid", 0) or 0))]
    live = [tx for tx in transactions if tx not in stale]
    _check(checks, "transactions", "warning" if stale else "ok", f"active={len(transactions)} live={len(live)} stale={len(stale)}", repairable=False, stale_transactions=[str(tx.get("transaction_id") or "") for tx in stale])

    gpg_format = (_git(root, "config", "--get", "gpg.format").stdout or "").strip()
    signing_key = (_git(root, "config", "--get", "user.signingkey").stdout or "").strip()
    commit_sign = (_git(root, "config", "--bool", "--get", "commit.gpgsign").stdout or "").strip()
    _check(checks, "signing", "ok" if signing_key else "info", f"format={gpg_format or 'default'} key={'configured' if signing_key else 'none'} commit.gpgsign={commit_sign or 'false'}")

    ok = not any(row["status"] == "error" for row in checks)
    return {"schema_version": DOCTOR_SCHEMA_VERSION, "kind": "devtool.git-doctor", "ok": ok, "repository": str(root), "repository_id": state.repository_id, "checks": checks, "repairs": repairs, "transaction_root": str(transaction_root(root))}


def _load_lineage_records() -> list[dict[str, Any]]:
    root = state_dir() / "federation-git-lineage"
    result: list[dict[str, Any]] = []
    if not root.is_dir(): return result
    for path in root.glob("*/*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            payload["_path"] = str(path)
            result.append(payload)
    return result


def federation_doctor(config: Any, subject: str = "") -> dict[str, Any]:
    wanted = str(subject or "").strip()
    repositories: list[dict[str, Any]] = []
    for name in sorted(getattr(config.federation, "repositories", {}) or {}):
        resolution = resolve_federated_repository(config, name)
        if not resolution.available:
            repositories.append({"repository": name, "ok": False, "error": resolution.boundary_detail})
            continue
        repositories.append({"repository": name, "ok": True, "doctor": git_doctor(resolution.root)})

    lineage_rows = _load_lineage_records()
    if wanted:
        lineage_rows = [row for row in lineage_rows if wanted in {str(row.get("parent_run_id") or ""), str(row.get("child_run_id") or ""), str(row.get("campaign_id") or ""), str(row.get("federation_id") or "")}]
    inconsistencies: list[dict[str, Any]] = []
    for row in lineage_rows:
        repository = str(row.get("repository") or "")
        try:
            resolution = resolve_federated_repository(config, repository)
        except Exception:
            resolution = None
        if resolution is None or not resolution.available:
            inconsistencies.append({"repository": repository, "kind": "missing-repository", "lineage": row})
            continue
        root = Path(resolution.root)
        start_ref = str(row.get("start_ref") or "")
        start_commit = str(row.get("start_commit") or "")
        result_ref = str(row.get("result_ref") or "")
        result_commit = str(row.get("result_commit") or "")
        for label, ref, commit in (("start", start_ref, start_commit), ("result", result_ref, result_commit)):
            if not ref or not commit: continue
            proc = _git(root, "rev-parse", "--verify", "--quiet", ref)
            actual = (proc.stdout or "").strip() if proc.returncode == 0 else ""
            if actual != commit:
                inconsistencies.append({"repository": repository, "kind": f"{label}-ref-mismatch", "ref": ref, "expected": commit, "actual": actual, "lineage_path": row.get("_path")})
        attestation = row.get("remote_attestation") if isinstance(row.get("remote_attestation"), Mapping) else {}
        if str(row.get("execution_provider") or "").startswith("ssh") and not attestation and not str(row.get("source_transport_evidence_digest") or ""):
            inconsistencies.append({"repository": repository, "kind": "remote-attestation-missing", "lineage_path": row.get("_path")})

    return {
        "schema_version": DOCTOR_SCHEMA_VERSION,
        "kind": "devtool.federation-git-doctor",
        "subject": wanted,
        "ok": all(bool(row.get("ok")) and bool((row.get("doctor") or {}).get("ok", True)) for row in repositories) and not inconsistencies,
        "repositories": repositories,
        "lineage_records": len(lineage_rows),
        "inconsistencies": inconsistencies,
    }


__all__ = ["DOCTOR_SCHEMA_VERSION", "GitDoctorError", "federation_doctor", "git_doctor"]
