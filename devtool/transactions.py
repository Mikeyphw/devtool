from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifacts import preview_artifact, read_delete_manifest
from .models import OperationResult
from .xdg import data_dir


def devtool_xdg_dir() -> Path:
    """Return the XDG data directory used for devtool state and backups.

    DEVTOOL_XDG_DIR is intentionally supported for tests, Termux, and power users
    that want the complete devtool state tree in a non-default location.
    """
    return data_dir()


def _repo_key(repo_root: Path) -> str:
    resolved = str(repo_root.resolve())
    slug = repo_root.resolve().name or "repo"
    safe_slug = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in slug).strip(".-") or "repo"
    digest = hashlib.sha256(resolved.encode("utf-8", "surrogatepass")).hexdigest()[:16]
    return f"{safe_slug}-{digest}"


def transaction_root(repo_root: Path) -> Path:
    return devtool_xdg_dir() / "transactions" / _repo_key(repo_root)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _process_start_token(pid: int) -> str:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace").split()
        return fields[21] if len(fields) > 21 else ""
    except OSError:
        return ""



def _process_is_alive(pid: int, start_token: str = "") -> bool:
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
    current = _process_start_token(pid)
    return not start_token or not current or current == start_token

def _safe_rel(text: str) -> Path:
    rel = Path(str(text).strip())
    if not str(rel):
        raise RuntimeError("Empty transaction path")
    if rel.is_absolute() or ".." in rel.parts:
        raise RuntimeError(f"Unsafe transaction path: {text}")
    if rel.parts and rel.parts[0] == ".git":
        raise RuntimeError(f"Refusing to transact .git path: {text}")
    return rel


def _relative_to_repo(repo_root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(repo_root.resolve()))
    except Exception:
        return str(path)


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"Invalid transaction JSON: {path}")
    return data


def _transaction_dir(repo_root: Path, transaction_id: str) -> Path:
    return transaction_root(repo_root) / transaction_id


def _transaction_file(tx_dir: Path) -> Path:
    return tx_dir / "transaction.json"


def _active_dir(repo_root: Path) -> Path:
    return transaction_root(repo_root) / "active"


def _active_file(repo_root: Path, transaction_id: str = "") -> Path:
    # Devtool now permits one active run per repository, so a single recovery
    # marker is authoritative. The active/ directory is still read for cleanup
    # compatibility with older releases.
    return transaction_root(repo_root) / "active.json"


def _remove_active_marker(repo_root: Path, transaction_path: Path, transaction_id: str = "") -> None:
    candidates = []
    if transaction_id:
        candidates.append(_active_file(repo_root, transaction_id))
    candidates.append(_active_file(repo_root))
    for active in candidates:
        if not active.exists():
            continue
        try:
            active_data = _read_json(active)
            if str(active_data.get("path")) == str(transaction_path):
                active.unlink(missing_ok=True)
        except Exception:
            # Only remove a corrupt per-transaction marker automatically. A
            # corrupt legacy global marker is left for explicit recovery.
            if active.parent.name == "active":
                active.unlink(missing_ok=True)


def _rsync() -> str:
    exe = shutil.which("rsync")
    if not exe:
        raise RuntimeError("rsync is required for transactional overlay backups but was not found in PATH.")
    return exe


def _run_rsync(argv: list[str], *, cwd: Path) -> str:
    proc = subprocess.run(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError("rsync failed with exit code " + str(proc.returncode) + "\n" + (proc.stdout or ""))
    return proc.stdout or ""


def _backup_existing_paths(repo_root: Path, backup_dir: Path, existing: list[str], tx_dir: Path) -> str:
    """Back up affected paths using copy-on-write where the filesystem permits it.

    GNU cp's ``--reflink=auto`` avoids duplicating unchanged file extents on
    btrfs/XFS while retaining normal copy semantics elsewhere. Hard links are
    deliberately not used because an in-place write to the working tree would
    mutate the transaction backup too. rsync remains the portable fallback.
    """
    cp = shutil.which("cp")
    if cp:
        try:
            for raw in existing:
                proc = subprocess.run(
                    [cp, "-a", "--reflink=auto", "--parents", "--", raw, str(backup_dir)],
                    cwd=repo_root,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    check=False,
                )
                if proc.returncode != 0:
                    raise RuntimeError(proc.stdout or f"cp exited with {proc.returncode}")
            return "reflink-auto"
        except (OSError, RuntimeError):
            shutil.rmtree(backup_dir, ignore_errors=True)
            backup_dir.mkdir(parents=True, exist_ok=True)

    list_file = tx_dir / "backup-files.list"
    list_file.write_bytes(b"".join(item.encode("utf-8") + b"\0" for item in existing))
    _run_rsync(
        [_rsync(), "-a", "--relative", "--files-from", str(list_file), "--from0", "./", str(backup_dir) + "/"],
        cwd=repo_root,
    )
    return "rsync"


def _path_kind(path: Path) -> str:
    if path.is_symlink():
        return "symlink"
    if path.is_dir():
        return "dir"
    if path.is_file():
        return "file"
    return "missing"


def _hash_path(path: Path) -> str:
    """Hash files, symlinks, and directory trees for rollback verification."""

    digest = hashlib.sha256()
    if path.is_symlink():
        digest.update(b"symlink\0")
        digest.update(os.readlink(path).encode("utf-8", errors="surrogateescape"))
        return digest.hexdigest()
    if path.is_file():
        digest.update(b"file\0")
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    if path.is_dir():
        digest.update(b"dir\0")
        for child in sorted(path.rglob("*"), key=lambda item: item.relative_to(path).as_posix()):
            rel = child.relative_to(path).as_posix()
            digest.update(rel.encode("utf-8", errors="surrogateescape"))
            digest.update(b"\0")
            digest.update(_hash_path(child).encode("ascii"))
            digest.update(b"\0")
        return digest.hexdigest()
    digest.update(b"missing\0")
    return digest.hexdigest()


def _copy_path(source: Path, destination: Path) -> None:
    _delete_path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink():
        destination.symlink_to(os.readlink(source), target_is_directory=source.is_dir())
    elif source.is_dir():
        shutil.copytree(source, destination, symlinks=True, copy_function=shutil.copy2)
    elif source.is_file():
        shutil.copy2(source, destination, follow_symlinks=False)
    else:
        raise RuntimeError(f"Rollback backup entry is missing: {source}")


def _history_dir(repo_root: Path) -> Path:
    return transaction_root(repo_root) / "history"


def _archive_transaction_summary(path: Path, tx: dict[str, Any]) -> None:
    repo_root = Path(str(tx.get("repo_root") or ".")).resolve()
    history = _history_dir(repo_root)
    history.mkdir(parents=True, exist_ok=True)
    payload = json.loads(json.dumps(tx))
    payload.pop("backup_dir", None)
    git_state = payload.get("git_state")
    if isinstance(git_state, dict):
        git_state.pop("index_backup", None)
    for item in payload.get("affected_paths", []) or []:
        if isinstance(item, dict):
            item.pop("staged_path", None)
            item.pop("displaced_path", None)
    _atomic_write_json(history / f"{tx.get('transaction_id') or path.parent.name}.json", payload)




def _git_capture(repo_root: Path, argv: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", *argv],
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return int(proc.returncode), (proc.stdout or "").strip()


def _capture_git_state(repo_root: Path, tx_dir: Path) -> dict[str, Any]:
    """Snapshot repository HEAD/index state for exact transactional rollback.

    Overlay transactions already preserve affected working-tree paths. Git staging
    mutates the repository index independently of those files, so atomic rollback
    must preserve the index too. This is deliberately best-effort for non-Git
    repositories and exact for real Git worktrees, including linked worktrees.
    """

    rc, inside = _git_capture(repo_root, ["rev-parse", "--is-inside-work-tree"])
    if rc != 0 or inside.lower() != "true":
        return {"is_git_repo": False}

    head_rc, initial_head = _git_capture(repo_root, ["rev-parse", "--verify", "HEAD"])
    ref_rc, head_ref = _git_capture(repo_root, ["symbolic-ref", "-q", "HEAD"])
    index_rc, index_text = _git_capture(repo_root, ["rev-parse", "--git-path", "index"])
    if index_rc != 0 or not index_text:
        raise RuntimeError("Unable to resolve Git index path for apply transaction")

    index_path = Path(index_text)
    if not index_path.is_absolute():
        index_path = (repo_root / index_path).resolve()
    backup_path = tx_dir / "git-index.backup"
    index_existed = index_path.exists()
    if index_existed:
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(index_path, backup_path)

    return {
        "is_git_repo": True,
        "initial_head": initial_head if head_rc == 0 else "",
        "head_ref": head_ref if ref_rc == 0 else "",
        "index_path": str(index_path),
        "index_existed": index_existed,
        "index_backup": str(backup_path),
        "applied_commit": "",
        "restored": False,
    }


def _current_git_head(repo_root: Path) -> str:
    rc, head = _git_capture(repo_root, ["rev-parse", "--verify", "HEAD"])
    return head if rc == 0 else ""


def record_transaction_git_commit(transaction_path: str | Path | None, commit_hash: str) -> None:
    """Record the commit created by an active apply transaction.

    This lets rollback safely rewind only the commit that devtool itself created;
    an unexpected later user commit is never reset automatically.
    """

    if not transaction_path or not str(commit_hash).strip():
        return
    path = Path(transaction_path)
    if not path.exists():
        return
    tx = _read_json(path)
    git_state = tx.get("git_state")
    if not isinstance(git_state, dict) or not bool(git_state.get("is_git_repo")):
        return
    git_state["applied_commit"] = str(commit_hash).strip()
    git_state["recorded_at"] = _utc_now()
    tx["git_state"] = git_state
    tx["updated_at"] = _utc_now()
    _atomic_write_json(path, tx)
    _write_transaction_active_marker(path, tx)


def _restore_git_state(repo_root: Path, tx: dict[str, Any]) -> str:
    git_state = tx.get("git_state")
    if not isinstance(git_state, dict) or not bool(git_state.get("is_git_repo")):
        return "not-applicable"
    if bool(git_state.get("restored")):
        return "already-restored"

    initial_head = str(git_state.get("initial_head") or "").strip()
    applied_commit = str(git_state.get("applied_commit") or "").strip()
    current_head = _current_git_head(repo_root)

    if applied_commit:
        if current_head != applied_commit:
            raise RuntimeError(
                "Refusing Git rollback because repository HEAD changed after the devtool apply commit: "
                f"expected {applied_commit or '<none>'}, found {current_head or '<unborn>'}"
            )
        if initial_head:
            proc = subprocess.run(
                ["git", "reset", "--soft", initial_head],
                cwd=repo_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
            if proc.returncode != 0:
                raise RuntimeError("Unable to rewind devtool apply commit during rollback: " + (proc.stdout or "").strip())
        else:
            head_ref = str(git_state.get("head_ref") or "").strip()
            if not head_ref:
                raise RuntimeError("Cannot safely rewind first devtool commit from a detached unborn Git HEAD")
            proc = subprocess.run(
                ["git", "update-ref", "-d", head_ref, applied_commit],
                cwd=repo_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
            if proc.returncode != 0:
                raise RuntimeError("Unable to remove first devtool apply commit during rollback: " + (proc.stdout or "").strip())
    elif current_head != initial_head:
        raise RuntimeError(
            "Refusing Git index rollback because repository HEAD changed during the active transaction: "
            f"expected {initial_head or '<unborn>'}, found {current_head or '<unborn>'}"
        )

    index_path = Path(str(git_state.get("index_path") or ""))
    if not str(index_path):
        raise RuntimeError("Apply transaction is missing its Git index path")
    index_existed = bool(git_state.get("index_existed"))
    backup_path = Path(str(git_state.get("index_backup") or ""))
    if index_existed:
        if not backup_path.exists():
            raise RuntimeError(f"Apply transaction Git index backup is missing: {backup_path}")
        index_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup_path, index_path)
    else:
        index_path.unlink(missing_ok=True)

    git_state["restored"] = True
    git_state["restored_at"] = _utc_now()
    tx["git_state"] = git_state
    return "restored"


def affected_paths_from_preview(preview: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for change in preview.get("changes", []) or []:
        if not isinstance(change, dict):
            continue
        kind = str(change.get("kind") or "")
        # Unchanged payload members don't need rollback protection.
        if kind == "unchanged":
            continue
        raw = str(change.get("path") or "").strip()
        if not raw:
            continue
        rel = str(_safe_rel(raw))
        if rel not in seen:
            seen.add(rel)
            paths.append(rel)
    return paths


def create_apply_transaction(
    repo_root: Path,
    artifact_path: Path,
    *,
    artifact_type: str,
    preview: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create an active local rsync backup for an overlay/snapshot apply.

    The transaction remains active until validation/commit succeeds. If devtool is
    killed before that point, the next devtool command can detect the active marker
    and restore the backup.
    """
    repo_root = repo_root.resolve()
    artifact_path = artifact_path.resolve()
    preview = preview or preview_artifact(repo_root, artifact_path, snapshot=artifact_type == "snapshot")
    affected = affected_paths_from_preview(preview)
    if artifact_type == "overlay":
        seen = set(affected)
        for raw in read_delete_manifest(artifact_path):
            rel = str(_safe_rel(raw))
            if rel not in seen:
                seen.add(rel)
                affected.append(rel)
    transaction_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:12]
    tx_dir = _transaction_dir(repo_root, transaction_id)
    backup_dir = tx_dir / "backup"
    tx_dir.mkdir(parents=True, exist_ok=False)
    backup_dir.mkdir(parents=True, exist_ok=True)

    entries: list[dict[str, Any]] = []
    existing: list[str] = []
    for raw in affected:
        rel = _safe_rel(raw)
        target = repo_root / rel
        existed = target.exists() or target.is_symlink()
        kind = _path_kind(target)
        entry = {
            "path": str(rel),
            "existed": existed,
            "kind": kind,
            "restore_state": "pending",
            "backup_sha256": "",
            "restored_sha256": "",
            "verified": False,
            "staged_path": "",
            "displaced_path": "",
            "error": "",
        }
        if existed:
            existing.append(str(rel))
        entries.append(entry)

    tx = {
        "schema_version": 2,
        "status": "active",
        "transaction_id": transaction_id,
        "path": str(_transaction_file(tx_dir)),
        "repo_root": str(repo_root),
        "artifact": str(artifact_path),
        "artifact_name": artifact_path.name,
        "artifact_type": artifact_type,
        "backup_dir": str(backup_dir),
        "devtool_xdg_dir": str(devtool_xdg_dir()),
        "repo_key": _repo_key(repo_root),
        "started_at": _utc_now(),
        "updated_at": _utc_now(),
        "pid": os.getpid(),
        "process_start": _process_start_token(os.getpid()),
        "run_id": os.environ.get("DEVTOOL_RUN_ID", ""),
        "affected_paths": entries,
        "preview_counts": preview.get("counts", {}),
        "git_state": _capture_git_state(repo_root, tx_dir),
    }
    _atomic_write_json(_transaction_file(tx_dir), tx)

    tx["backup_method"] = "none"
    if existing:
        tx["backup_method"] = _backup_existing_paths(repo_root, backup_dir, existing, tx_dir)
        for entry in entries:
            if not bool(entry.get("existed")):
                continue
            backup_path = backup_dir / _safe_rel(str(entry.get("path") or ""))
            entry["backup_sha256"] = _hash_path(backup_path)
    tx["status"] = "backup-ready"
    tx["updated_at"] = _utc_now()
    _atomic_write_json(_transaction_file(tx_dir), tx)
    _atomic_write_json(
        _active_file(repo_root, transaction_id),
        {
            "transaction_id": transaction_id,
            "path": str(_transaction_file(tx_dir)),
            "status": "backup-ready",
            "updated_at": _utc_now(),
            "pid": os.getpid(),
            "process_start": tx.get("process_start", ""),
            "run_id": tx.get("run_id", ""),
        },
    )
    return tx


def find_active_transactions(repo_root: Path, *, include_live: bool = False) -> list[Path]:
    repo_root = repo_root.resolve()
    markers: list[Path] = []
    legacy = _active_file(repo_root)
    if legacy.exists():
        markers.append(legacy)
    active_dir = _active_dir(repo_root)
    if active_dir.exists():
        markers.extend(sorted(active_dir.glob("*.json")))

    found: list[Path] = []
    for marker in markers:
        try:
            data = _read_json(marker)
            path = Path(str(data.get("path") or ""))
            if not path.exists():
                marker.unlink(missing_ok=True)
                continue
            pid = int(data.get("pid", 0) or 0)
            live = _process_is_alive(pid, str(data.get("process_start") or ""))
            if live and not include_live:
                continue
            found.append(path)
        except Exception:
            if marker == legacy:
                found.append(marker)
            else:
                marker.unlink(missing_ok=True)
    return found


def find_active_transaction(repo_root: Path) -> Path | None:
    stale = find_active_transactions(repo_root, include_live=False)
    return stale[0] if stale else None


def _cleanup_transaction_dir(path: Path) -> None:
    tx_dir = path.parent
    try:
        shutil.rmtree(tx_dir, ignore_errors=True)
    except Exception:
        pass


def mark_transaction_completed(transaction_path: str | Path | None) -> None:
    if not transaction_path:
        return
    path = Path(transaction_path)
    if not path.exists():
        return
    tx = _read_json(path)
    tx["status"] = "completed"
    tx["completed_at"] = _utc_now()
    tx["updated_at"] = _utc_now()
    _atomic_write_json(path, tx)
    repo_root = Path(str(tx.get("repo_root") or "."))
    _remove_active_marker(repo_root, path, str(tx.get("transaction_id") or ""))
    _archive_transaction_summary(path, tx)
    # Successful applies no longer leave backup payloads in the repository or XDG tree.
    _cleanup_transaction_dir(path)


def abandon_transaction(
    transaction_path: str | Path | None,
    *,
    reason: str = "validation session discarded while retaining applied changes",
) -> None:
    """Close a transaction while deliberately keeping the applied working tree."""

    if not transaction_path:
        return
    path = Path(transaction_path)
    if not path.exists():
        return
    tx = _read_json(path)
    tx.update({
        "status": "abandoned-kept-changes",
        "abandon_reason": reason,
        "abandoned_at": _utc_now(),
        "updated_at": _utc_now(),
        "pid": 0,
        "process_start": "",
        "run_id": "",
    })
    _atomic_write_json(path, tx)
    repo_root = Path(str(tx.get("repo_root") or ".")).resolve()
    _remove_active_marker(repo_root, path, str(tx.get("transaction_id") or ""))
    _archive_transaction_summary(path, tx)
    _cleanup_transaction_dir(path)

def _delete_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    elif path.exists() or path.is_symlink():
        path.unlink(missing_ok=True)


def rollback_transaction(
    transaction_path: str | Path,
    *,
    reason: str = "rollback requested",
    progress_callback=None,
) -> OperationResult:
    """Resume a journaled, verified rollback.

    Each affected path is staged beside its destination, verified, then swapped
    into place. The transaction JSON is checkpointed after every transition so
    another invocation can continue after process death or storage interruption.
    """

    start = time.perf_counter()
    path = Path(transaction_path)
    if path.name == "active.json" or path.parent.name == "active":
        active = _read_json(path)
        path = Path(str(active.get("path") or ""))
    argv = ["transaction", "rollback", str(path)]
    tx: dict[str, Any] = {}

    def persist() -> None:
        tx["updated_at"] = _utc_now()
        _atomic_write_json(path, tx)
        _write_transaction_active_marker(path, tx)

    def emit_progress(state: str, *, current_path: str = "", completed: int = 0, total: int = 0, error: str = "") -> None:
        if progress_callback is None:
            return
        try:
            progress_callback({
                "state": state,
                "current_path": current_path,
                "completed_paths": completed,
                "total_paths": total,
                "error": error,
            })
        except Exception:
            pass

    try:
        tx = _read_json(path)
        repo_root = Path(str(tx.get("repo_root") or ".")).resolve()
        backup_dir = Path(str(tx.get("backup_dir") or path.parent / "backup")).resolve()
        entries = [item for item in tx.get("affected_paths", []) or [] if isinstance(item, dict)]
        tx["schema_version"] = 2
        tx["status"] = "rollback-requested"
        tx["rollback_reason"] = reason
        tx["rollback_started_at"] = str(tx.get("rollback_started_at") or _utc_now())
        tx["pid"] = os.getpid()
        tx["process_start"] = _process_start_token(os.getpid())
        tx["run_id"] = os.environ.get("DEVTOOL_RUN_ID", "")
        persist()
        emit_progress("rollback-requested", total=len(entries))

        if not entries:
            git_restore_status = _restore_git_state(repo_root, tx)
            tx.update({
                "status": "rolled-back",
                "rolled_back_at": _utc_now(),
                "pid": 0,
                "process_start": "",
                "run_id": "",
            })
            _atomic_write_json(path, tx)
            _remove_active_marker(repo_root, path, str(tx.get("transaction_id") or ""))
            _archive_transaction_summary(path, tx)
            output = (
                "Cleared stale devtool apply transaction.\n"
                f"reason: {reason}\n"
                f"transaction: {_relative_to_repo(repo_root, path)}\n"
                "restored: 0\n"
                "removed_added_paths: 0\n"
                f"git_state: {git_restore_status}\n"
                "note: transaction had no affected paths recorded."
            )
            _cleanup_transaction_dir(path)
            return OperationResult("apply-rollback", argv, 0, time.perf_counter() - start, output, [], failure_kind="rollback")

        transaction_id = str(tx.get("transaction_id") or path.parent.name)
        token = transaction_id.replace("-", "")[-12:] or uuid.uuid4().hex[:12]
        restored: list[str] = []
        removed: list[str] = []
        tx["status"] = "rollback-staging"
        persist()
        emit_progress("rollback-staging", total=len(entries))

        for entry_index, entry in enumerate(entries, start=1):
            rel = _safe_rel(str(entry.get("path") or ""))
            target = repo_root / rel
            source = backup_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            stage = Path(str(entry.get("staged_path") or "")) if entry.get("staged_path") else target.with_name(f".{target.name}.devtool-rollback-stage-{token}")
            displaced = Path(str(entry.get("displaced_path") or "")) if entry.get("displaced_path") else target.with_name(f".{target.name}.devtool-rollback-old-{token}")
            entry["staged_path"] = str(stage)
            entry["displaced_path"] = str(displaced)
            entry["error"] = ""
            state = str(entry.get("restore_state") or "pending")

            if bool(entry.get("existed")):
                expected = str(entry.get("backup_sha256") or "")
                if not expected:
                    expected = _hash_path(source)
                    entry["backup_sha256"] = expected
                    persist()

                if state in {"pending", "staging", "rollback-failed"}:
                    entry["restore_state"] = "staging"
                    persist()
                    _delete_path(stage)
                    _copy_path(source, stage)
                    actual_stage = _hash_path(stage)
                    if actual_stage != expected:
                        raise RuntimeError(
                            f"Rollback staging verification failed for {rel}: expected {expected}, got {actual_stage}"
                        )
                    entry["restore_state"] = "staged"
                    state = "staged"
                    persist()

                if state == "staged":
                    tx["status"] = "rollback-restoring"
                    persist()
                    emit_progress("rollback-restoring", current_path=str(rel), completed=entry_index - 1, total=len(entries))
                    if displaced.exists() or displaced.is_symlink():
                        # A previous process may already have moved the target.
                        if target.exists() or target.is_symlink():
                            raise RuntimeError(f"Ambiguous rollback state for {rel}: target and displaced copy both exist")
                    elif target.exists() or target.is_symlink():
                        os.replace(target, displaced)
                    entry["restore_state"] = "target-displaced"
                    state = "target-displaced"
                    persist()

                if state == "target-displaced":
                    if target.exists() or target.is_symlink():
                        if stage.exists() or stage.is_symlink():
                            raise RuntimeError(f"Ambiguous rollback state for {rel}: target and staged copy both exist")
                    else:
                        if not (stage.exists() or stage.is_symlink()):
                            raise RuntimeError(f"Rollback staged copy disappeared for {rel}")
                        os.replace(stage, target)
                    entry["restore_state"] = "restored"
                    state = "restored"
                    persist()

                if state in {"restored", "verified"}:
                    actual = _hash_path(target)
                    entry["restored_sha256"] = actual
                    if actual != expected:
                        raise RuntimeError(
                            f"Rollback verification failed for {rel}: expected {expected}, got {actual}"
                        )
                    entry["verified"] = True
                    entry["restore_state"] = "verified"
                    persist()
                    _delete_path(displaced)
                    _delete_path(stage)
                    restored.append(str(rel))
                    emit_progress("rollback-progress", current_path=str(rel), completed=entry_index, total=len(entries))
            else:
                if state in {"pending", "staging", "staged", "rollback-failed"}:
                    if displaced.exists() or displaced.is_symlink():
                        if target.exists() or target.is_symlink():
                            raise RuntimeError(f"Ambiguous removal state for {rel}: target and quarantine both exist")
                    elif target.exists() or target.is_symlink():
                        os.replace(target, displaced)
                    entry["restore_state"] = "removed"
                    state = "removed"
                    persist()
                if state in {"removed", "verified"}:
                    if target.exists() or target.is_symlink():
                        raise RuntimeError(f"Rollback removal verification failed for {rel}")
                    entry["restored_sha256"] = _hash_path(target)
                    entry["verified"] = True
                    entry["restore_state"] = "verified"
                    persist()
                    _delete_path(displaced)
                    _delete_path(stage)
                    removed.append(str(rel))
                    emit_progress("rollback-progress", current_path=str(rel), completed=entry_index, total=len(entries))

        tx.update({
            "status": "rollback-verifying",
            "pid": os.getpid(),
            "process_start": _process_start_token(os.getpid()),
        })
        persist()
        emit_progress("rollback-verifying", completed=len(entries), total=len(entries))
        unverified = [str(item.get("path") or "") for item in entries if not bool(item.get("verified"))]
        if unverified:
            raise RuntimeError("Rollback journal contains unverified paths: " + ", ".join(unverified[:20]))

        git_restore_status = _restore_git_state(repo_root, tx)
        persist()

        tx.update({
            "status": "rolled-back",
            "rolled_back_at": _utc_now(),
            "pid": 0,
            "process_start": "",
            "run_id": "",
        })
        _atomic_write_json(path, tx)
        _remove_active_marker(repo_root, path, str(tx.get("transaction_id") or ""))
        _archive_transaction_summary(path, tx)
        emit_progress("rolled-back", completed=len(entries), total=len(entries))
        output = (
            "Rolled back devtool apply transaction.\n"
            f"reason: {reason}\n"
            f"transaction: {_relative_to_repo(repo_root, path)}\n"
            f"restored: {len(restored)}\n"
            f"removed_added_paths: {len(removed)}\n"
            f"verified_paths: {len(entries)}\n"
            f"git_state: {git_restore_status}\n"
            "backup_cleanup: removed"
        )
        if restored:
            output += "\nrestored_paths:\n" + "\n".join(f"  {item}" for item in restored[:80])
        if removed:
            output += "\nremoved_paths:\n" + "\n".join(f"  {item}" for item in removed[:80])
        _cleanup_transaction_dir(path)
        return OperationResult("apply-rollback", argv, 0, time.perf_counter() - start, output, [], failure_kind="rollback")
    except BaseException as exc:
        if tx:
            try:
                tx.update({
                    "status": "rollback-failed",
                    "rollback_error": f"{type(exc).__name__}: {exc}",
                    "rollback_failed_at": _utc_now(),
                    "pid": 0,
                    "process_start": "",
                    "run_id": "",
                })
                for item in tx.get("affected_paths", []) or []:
                    if isinstance(item, dict) and str(item.get("restore_state") or "") not in {"verified"}:
                        item.setdefault("error", "")
                _atomic_write_json(path, tx)
                _write_transaction_active_marker(path, tx)
            except Exception:
                pass
        emit_progress("rollback-failed", total=len(tx.get("affected_paths", []) or []) if tx else 0, error=f"{type(exc).__name__}: {exc}")
        output = (
            "Rollback failed and remains resumable.\n"
            f"transaction: {path}\n"
            f"reason: {reason}\n"
            f"error_type: {type(exc).__name__}\n"
            f"error: {exc}\n"
            "Run the rollback/recovery command again after correcting the reported problem."
        )
        return OperationResult("apply-rollback", argv, 1, time.perf_counter() - start, output, [], failure_kind="rollback_failure")

def read_transaction(transaction_path: str | Path) -> dict[str, Any]:
    """Read a transaction JSON path or active marker."""
    path = Path(transaction_path)
    if path.name == "active.json" or path.parent.name == "active":
        active = _read_json(path)
        path = Path(str(active.get("path") or ""))
    return _read_json(path)


def transaction_pending_validation(transaction_path: str | Path | None) -> bool:
    if not transaction_path:
        return False
    try:
        return str(read_transaction(transaction_path).get("status") or "") in {
            "applied-pending-validation",
            "applied-validation-deferred",
            "validation-running",
            "partially-validated",
            "validation-blocked",
            "validated",
            "validated-uncommitted",
            "rollback-requested",
            "rollback-staging",
            "rollback-restoring",
            "rollback-verifying",
            "rollback-failed",
        }
    except Exception:
        return False


def _write_transaction_active_marker(path: Path, tx: dict[str, Any]) -> None:
    repo_root = Path(str(tx.get("repo_root") or ".")).resolve()
    transaction_id = str(tx.get("transaction_id") or "")
    marker_payload = {
        "transaction_id": transaction_id,
        "path": str(path),
        "status": str(tx.get("status") or ""),
        "updated_at": str(tx.get("updated_at") or _utc_now()),
        "pid": int(tx.get("pid", 0) or 0),
        "process_start": str(tx.get("process_start") or ""),
        "run_id": str(tx.get("run_id") or ""),
        "validation_session_id": str(tx.get("validation_session_id") or ""),
        "next_validation_phase": str(tx.get("next_validation_phase") or ""),
    }
    _atomic_write_json(_active_file(repo_root, transaction_id), marker_payload)


def mark_transaction_validation_running(
    transaction_path: str | Path | None,
    *,
    session_id: str,
    phase: str,
) -> None:
    if not transaction_path:
        return
    path = Path(transaction_path)
    if not path.exists():
        return
    tx = _read_json(path)
    tx.update({
        "status": "validation-running",
        "validation_session_id": session_id,
        "current_validation_phase": phase,
        "next_validation_phase": phase,
        "pid": os.getpid(),
        "process_start": _process_start_token(os.getpid()),
        "run_id": os.environ.get("DEVTOOL_RUN_ID", ""),
        "updated_at": _utc_now(),
    })
    _atomic_write_json(path, tx)
    _write_transaction_active_marker(path, tx)


def mark_transaction_validation_blocked(
    transaction_path: str | Path | None,
    *,
    session_id: str,
    failed_phase: str,
    verdict: str = "code-failed",
) -> None:
    if not transaction_path:
        return
    path = Path(transaction_path)
    if not path.exists():
        return
    tx = _read_json(path)
    tx.update({
        "status": "validation-blocked",
        "validation_session_id": session_id,
        "current_validation_phase": "",
        "failed_validation_phase": failed_phase,
        "validation_verdict": verdict,
        "pid": 0,
        "process_start": "",
        "run_id": "",
        "updated_at": _utc_now(),
    })
    _atomic_write_json(path, tx)
    _write_transaction_active_marker(path, tx)


def mark_transaction_validated(
    transaction_path: str | Path | None,
    *,
    session_id: str,
    commit_pending: bool = False,
) -> None:
    if not transaction_path:
        return
    path = Path(transaction_path)
    if not path.exists():
        return
    tx = _read_json(path)
    tx.update({
        "status": "validated-uncommitted" if commit_pending else "validated",
        "validation_session_id": session_id,
        "current_validation_phase": "",
        "next_validation_phase": "",
        "validated_at": _utc_now(),
        "pid": 0,
        "process_start": "",
        "run_id": "",
        "updated_at": _utc_now(),
    })
    _atomic_write_json(path, tx)
    _write_transaction_active_marker(path, tx)


def mark_transaction_pending_validation(
    transaction_path: str | Path | None,
    *,
    session_id: str,
    next_phase: str = "",
    completed_phases: int = 0,
    phase_total: int = 0,
    deferred: bool = False,
) -> None:
    """Persist a transaction across devtool processes while validation is paused."""
    if not transaction_path:
        return
    path = Path(transaction_path)
    if not path.exists():
        return
    tx = _read_json(path)
    tx.update({
        "status": (
            "partially-validated"
            if completed_phases
            else "applied-validation-deferred"
            if deferred
            else "applied-pending-validation"
        ),
        "validation_session_id": session_id,
        "current_validation_phase": "",
        "next_validation_phase": next_phase,
        "validation_completed_phases": int(completed_phases),
        "validation_phase_total": int(phase_total),
        # A paused transaction deliberately has no live owner.  The session
        # marker distinguishes it from an abandoned legacy apply.
        "pid": 0,
        "process_start": "",
        "run_id": "",
        "updated_at": _utc_now(),
    })
    _atomic_write_json(path, tx)
    _write_transaction_active_marker(path, tx)
