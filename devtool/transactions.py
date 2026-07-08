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
from .concurrency import process_is_alive



def _xdg_data_home() -> Path:
    raw = os.environ.get("XDG_DATA_HOME")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".local" / "share"


def devtool_xdg_dir() -> Path:
    """Return the XDG data directory used for devtool state and backups.

    DEVTOOL_XDG_DIR is intentionally supported for tests, Termux, and power users
    that want the complete devtool state tree in a non-default location.
    """
    raw = os.environ.get("DEVTOOL_XDG_DIR")
    if raw:
        return Path(raw).expanduser()
    return _xdg_data_home() / "devtool"


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
    # Keep the legacy active.json path readable for recovery, but new
    # transactions get one marker each so concurrent applies cannot overwrite
    # another run's recovery state.
    if not transaction_id:
        return transaction_root(repo_root) / "active.json"
    return _active_dir(repo_root) / f"{transaction_id}.json"


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


def _path_kind(path: Path) -> str:
    if path.is_symlink():
        return "symlink"
    if path.is_dir():
        return "dir"
    if path.is_file():
        return "file"
    return "missing"


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
        }
        if existed:
            existing.append(str(rel))
        entries.append(entry)

    tx = {
        "schema_version": 1,
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
    }
    _atomic_write_json(_transaction_file(tx_dir), tx)

    if existing:
        list_file = tx_dir / "backup-files.list"
        list_file.write_bytes(b"".join(item.encode("utf-8") + b"\0" for item in existing))
        _run_rsync([_rsync(), "-a", "--relative", "--files-from", str(list_file), "--from0", "./", str(backup_dir) + "/"], cwd=repo_root)
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
            live = process_is_alive(pid, str(data.get("process_start") or ""))
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
    # Successful applies no longer leave backup payloads in the repository or XDG tree.
    _cleanup_transaction_dir(path)

def _delete_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    elif path.exists() or path.is_symlink():
        path.unlink(missing_ok=True)


def rollback_transaction(transaction_path: str | Path, *, reason: str = "rollback requested") -> OperationResult:
    start = time.perf_counter()
    path = Path(transaction_path)
    if path.name == "active.json" or path.parent.name == "active":
        active = _read_json(path)
        path = Path(str(active.get("path") or ""))
    argv = ["rsync", "rollback", str(path)]
    try:
        tx = _read_json(path)
        repo_root = Path(str(tx.get("repo_root") or ".")).resolve()
        backup_dir = Path(str(tx.get("backup_dir") or path.parent / "backup")).resolve()
        entries = list(tx.get("affected_paths") or [])
        if not entries:
            # Older/interrupted apply transactions may have been created before
            # any affected path snapshot was recorded.  There is nothing safe or
            # useful to restore, but leaving active.json in place blocks every
            # future apply command.  Treat this as a successful stale-transaction
            # cleanup: mark it cleared, remove active.json if it points here, and
            # remove the empty transaction directory.
            tx["status"] = "cleared-empty"
            tx["rollback_reason"] = reason
            tx["rolled_back_at"] = _utc_now()
            tx["updated_at"] = _utc_now()
            _atomic_write_json(path, tx)
            _remove_active_marker(repo_root, path, str(tx.get("transaction_id") or ""))
            output = (
                "Cleared stale devtool apply transaction.\n"
                f"reason: {reason}\n"
                f"transaction: {_relative_to_repo(repo_root, path)}\n"
                "restored: 0\n"
                "removed_added_paths: 0\n"
                "note: transaction had no affected paths recorded, so there was nothing to restore."
            )
            _cleanup_transaction_dir(path)
            return OperationResult("apply-rollback", argv, 0, time.perf_counter() - start, output, [], failure_kind="rollback")

        restored: list[str] = []
        removed: list[str] = []
        existing: list[str] = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            rel = _safe_rel(str(item.get("path") or ""))
            target = repo_root / rel
            if bool(item.get("existed")):
                existing.append(str(rel))
                _delete_path(target)
                restored.append(str(rel))
            else:
                _delete_path(target)
                removed.append(str(rel))

        rsync_output = ""
        if existing:
            list_file = path.parent / "restore-files.list"
            list_file.write_bytes(b"".join(item.encode("utf-8") + b"\0" for item in existing))
            rsync_output = _run_rsync([_rsync(), "-a", "--relative", "--files-from", str(list_file), "--from0", str(backup_dir) + "/", str(repo_root) + "/"], cwd=repo_root)

        tx["status"] = "rolled-back"
        tx["rollback_reason"] = reason
        tx["rolled_back_at"] = _utc_now()
        tx["updated_at"] = _utc_now()
        _atomic_write_json(path, tx)
        _remove_active_marker(repo_root, path, str(tx.get("transaction_id") or ""))
        output = (
            "Rolled back devtool apply transaction.\n"
            f"reason: {reason}\n"
            f"transaction: {_relative_to_repo(repo_root, path)}\n"
            f"restored: {len(restored)}\n"
            f"removed_added_paths: {len(removed)}"
        )
        if restored:
            output += "\nrestored_paths:\n" + "\n".join(f"  {p}" for p in restored[:80])
            if len(restored) > 80:
                output += f"\n  ... {len(restored) - 80} more"
        if removed:
            output += "\nremoved_paths:\n" + "\n".join(f"  {p}" for p in removed[:80])
            if len(removed) > 80:
                output += f"\n  ... {len(removed) - 80} more"
        if rsync_output.strip():
            output += "\nrsync:\n" + rsync_output.strip()
        output += "\nbackup_cleanup: removed"
        _cleanup_transaction_dir(path)
        return OperationResult("apply-rollback", argv, 0, time.perf_counter() - start, output, [], failure_kind="rollback")
    except BaseException as exc:
        output = f"Rollback failed.\ntransaction: {path}\nreason: {reason}\nerror_type: {type(exc).__name__}\nerror: {exc}"
        return OperationResult("apply-rollback", argv, 1, time.perf_counter() - start, output, [], failure_kind="rollback_failure")
