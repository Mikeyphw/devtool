"""NEXT-01: evidence-gated historical *marker* retirement, never rollback.

Retirement only moves the two optional active pointer files to a private escrow.
It intentionally never changes transaction.json, backups, Git, or an isolated
worktree, and never calls legacy abandonment/integration paths.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
from pathlib import Path
from typing import Any

from .transactions import (
    _atomic_write_json, _process_is_alive, _safe_rel,
    reservation_coordinator, transaction_root,
)

_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12}$")
_MAX_JSON = 4 * 1024 * 1024


class HistoricalRetirementError(RuntimeError):
    """Blocked before an unsafe marker operation."""


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _regular(path: Path) -> None:
    mode = path.lstat().st_mode
    if not stat.S_ISREG(mode) or path.stat().st_size > _MAX_JSON:
        raise HistoricalRetirementError(f"Expected a bounded regular file: {path}")


def _json(path: Path) -> dict[str, Any]:
    _regular(path)
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError, OSError) as exc:
        raise HistoricalRetirementError(f"Unreadable JSON: {path}: {exc}") from exc
    if not isinstance(result, dict):
        raise HistoricalRetirementError(f"JSON must be an object: {path}")
    return result


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "--no-optional-locks", *args], cwd=repo,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"},
        check=False, timeout=20,
    )
    if proc.returncode:
        raise HistoricalRetirementError(f"Git {args[0]} failed: {proc.stderr.strip()[:200]}")
    return proc.stdout.strip()


def _index_digest(repo: Path) -> str:
    raw = Path(_git(repo, "rev-parse", "--git-path", "index"))
    index = raw if raw.is_absolute() else repo / raw
    if not index.exists() or index.is_symlink():
        raise HistoricalRetirementError("Git index absent or symlinked")
    _regular(index)
    return _sha(index)


def _owned_file(path: Path, root: Path) -> None:
    """No symlink (even in parents) from owned XDG root to the file."""
    if not path.is_relative_to(root):
        raise HistoricalRetirementError("Journal or marker escapes transaction root")
    current = root
    if root.is_symlink():
        raise HistoricalRetirementError("Transaction root is a symlink")
    for segment in path.relative_to(root).parts:
        current = current / segment
        if current.is_symlink():
            raise HistoricalRetirementError(f"Symlink in transaction state: {current}")


def _safe_affected(repo: Path, tx: dict[str, Any], tx_dir: Path) -> int:
    entries = tx.get("affected_paths")
    if not isinstance(entries, list) or not entries or len(entries) > 2048:
        raise HistoricalRetirementError("Missing or oversized affected-path journal")
    if any(not isinstance(row, dict) for row in entries):
        raise HistoricalRetirementError("Malformed affected-path record")
    if any(str(row.get("restore_state") or "pending") != "pending" for row in entries):
        raise HistoricalRetirementError("Restoration has started; cannot retire unresolved path states")
    if any(bool(row.get("verified")) for row in entries):
        raise HistoricalRetirementError("Verified/restored path cannot be treated as untouched")
    backup_root = tx_dir / "backup"
    _owned_file(backup_root, transaction_root(repo))
    if not backup_root.is_dir() or backup_root.is_symlink():
        raise HistoricalRetirementError("Transaction backup directory absent or unsafe")
    seen: set[str] = set()
    for row in entries:
        rel = _safe_rel(str(row.get("path") or ""))
        name = rel.as_posix()
        if name in {"", "."}:
            raise HistoricalRetirementError("Empty or current-directory affected path")
        if name in seen:
            raise HistoricalRetirementError("Duplicate affected path")
        seen.add(name)
        for base in (repo, backup_root):
            for n in range(1, len(rel.parts)+1):
                part = base.joinpath(*rel.parts[:n])
                if part.is_symlink():
                    raise HistoricalRetirementError(f"Symlink affected path: {name}")
        if bool(row.get("existed")):
            backup = backup_root / rel
            if not backup.exists() or (not backup.is_file() and not backup.is_dir()):
                raise HistoricalRetirementError(f"Missing original backup: {name}")
            if backup.is_dir() and any(p.is_symlink() for p in backup.rglob("*")):
                raise HistoricalRetirementError(f"Symlink inside original backup tree: {name}")
            expected = str(row.get("backup_sha256") or "")
            # Path backups can be directory trees, so require the previously
            # journaled identity rather than blindly hashing as a regular file.
            from .transactions import _hash_path
            if not expected or _hash_path(backup) != expected:
                raise HistoricalRetirementError(f"Original backup digest mismatch: {name}")
    return len(entries)


def _identities(repo: Path, tx: dict[str, Any]) -> dict[str, str]:
    if _git(repo, "rev-parse", "--is-inside-work-tree") != "true":
        raise HistoricalRetirementError("Repository is not a Git checkout")
    if _git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
        raise HistoricalRetirementError("Current checkout is dirty; retirement requires clean HEAD")
    current = _git(repo, "rev-parse", "HEAD")
    old = str((tx.get("git_state") or {}).get("initial_head") or "")
    if not re.fullmatch(r"[a-fA-F0-9]{40,64}", old):
        raise HistoricalRetirementError("No complete old Git HEAD in journal")
    old = _git(repo, "rev-parse", "--verify", old + "^{commit}")
    if old == current:
        raise HistoricalRetirementError("No advancing HEAD; historical supersession not established")
    # The earlier checkpoint must be an ancestor: unrelated force-push history
    # cannot justify retiring an active rollback reservation.
    rc = subprocess.run(["git", "merge-base", "--is-ancestor", old, current], cwd=repo,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"}, check=False, timeout=20).returncode
    if rc != 0:
        raise HistoricalRetirementError("Old HEAD is not an ancestor of current HEAD")
    return {"old_head": old, "current_head": current, "index_sha256": _index_digest(repo)}


def _context(repo_root: Path, txid: str) -> tuple[dict[str, Any], list[Path]]:
    repo = repo_root.resolve(strict=True)
    if not _ID.fullmatch(txid):
        raise HistoricalRetirementError("Exact transaction ID required")
    root = transaction_root(repo)
    if not root.is_dir() or root.is_symlink():
        raise HistoricalRetirementError("Transaction root missing or unsafe")
    tx_file = root / txid / "transaction.json"
    _owned_file(tx_file, root)
    tx = _json(tx_file)
    if (str(tx.get("transaction_id") or "") != txid
            or str(tx.get("path") or "") != str(tx_file)
            or str(tx.get("repo_root") or "") != str(repo)
            or str(tx.get("backup_dir") or "") != str(root / txid / "backup")):
        raise HistoricalRetirementError("Transaction journal identity mismatch")
    if str(tx.get("status") or "") != "rollback-failed":
        raise HistoricalRetirementError("Only preflight rollback-failed historical transactions are eligible")
    pid = int(tx.get("pid") or 0)
    if _process_is_alive(pid, str(tx.get("process_start") or "")):
        raise HistoricalRetirementError(f"Transaction owner PID {pid} is still alive")
    _safe_affected(repo, tx, tx_file.parent)
    markers = [root / "active.json", root / "active" / f"{txid}.json"]
    found = []
    for marker in markers:
        if not marker.exists() and not marker.is_symlink():
            continue
        _owned_file(marker, root)
        obj = _json(marker)
        if str(obj.get("path") or "") != str(tx_file) or str(obj.get("transaction_id") or "") != txid:
            raise HistoricalRetirementError(f"Active marker identity mismatch: {marker}")
        if _process_is_alive(int(obj.get("pid") or 0), str(obj.get("process_start") or "")):
            raise HistoricalRetirementError("Active marker still has a live owner")
        found.append(marker)
    if not found:
        raise HistoricalRetirementError("No current active markers; transaction may already be retired")
    return tx, found


def inspect_historical_transaction(repo_root: Path, txid: str) -> dict[str, Any]:
    """Read-only analysis; no auto marker pruning and no Git writes."""
    repo = repo_root.resolve()
    result: dict[str, Any] = {"schema_version": 1, "operation": "historical-retirement-preview",
                              "transaction_id": txid, "repo": str(repo), "read_only": True,
                              "eligible": False, "retired": False, "blockers": [],
                              "formal_qualification": False}
    try:
        tx, markers = _context(repo, txid)
        result.update(_identities(repo, tx))
        result["marker_paths"] = [str(p) for p in markers]
        result["marker_sha256"] = {p.name: _sha(p) for p in markers}
        result["journal_sha256"] = _sha(Path(str(tx["path"])))
        result["affected_paths"] = len(tx["affected_paths"])
        result["eligible"] = True
        result["classification"] = "historical_preflight_failure_candidate"
    except (HistoricalRetirementError, RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
        result["classification"] = "blocked"
        result["blockers"] = [str(exc)]
        # Reporting prior escrow is read-only and does not implicitly accept it.
        root = transaction_root(repo)
        receipt = root / txid / "historical-retirement" / "receipt.json"
        try:
            if _ID.fullmatch(txid) and receipt.exists():
                _owned_file(receipt, root)
                recorded = _json(receipt)
                if recorded.get("kind") == "devtool.historical-marker-retirement" and recorded.get("transaction_id") == txid:
                    result["retired"] = recorded.get("state") == "retired-preserve-current"
                    result["escrow_state"] = str(recorded.get("state") or "unknown")
        except (HistoricalRetirementError, OSError, ValueError):
            pass
    return result


def retire_historical_transaction(repo_root: Path, txid: str) -> dict[str, Any]:
    """Explicit marker-only retirement; read-only Git checked under coordinator."""
    repo = repo_root.resolve(strict=True)
    with reservation_coordinator(repo):
        preview = inspect_historical_transaction(repo, txid)
        if not preview["eligible"]:
            raise HistoricalRetirementError("Retirement blocked: " + "; ".join(preview["blockers"]))
        root = transaction_root(repo)
        escrow = root / txid / "historical-retirement"
        if escrow.exists() or escrow.is_symlink():
            raise HistoricalRetirementError("Escrow already exists; inspect interrupted retirement")
        markers = [Path(p) for p in preview["marker_paths"]]
        # Before marker mutations write a durable, non-success receipt containing
        # all original bytes' digests and the exact Git/index identity.
        escrow.mkdir(mode=0o700)
        record = {"schema_version": 1, "kind": "devtool.historical-marker-retirement",
                  "transaction_id": txid, "repo": str(repo), "state": "preparing",
                  "source_journal_sha256": preview["journal_sha256"],
                  "old_head": preview["old_head"], "current_head": preview["current_head"],
                  "index_sha256": preview["index_sha256"],
                  "markers": [{"relative_path": str(p.relative_to(root)), "sha256": _sha(p)} for p in markers],
                  "rollback_verified": False, "worktree_mutated": False,
                  "release_qualified": False}
        _atomic_write_json(escrow / "receipt.json", record)
        for marker in markers:
            target = escrow / ("legacy.json" if marker.name == "active.json" else "transaction-marker.json")
            os.replace(marker, target)
        record["state"] = "retired-preserve-current"
        _atomic_write_json(escrow / "receipt.json", record)
        return {**record, "escrow": str(escrow), "ok": True}


def unretire_historical_transaction(repo_root: Path, txid: str) -> dict[str, Any]:
    """Restore exact escrowed pointers, never change worktree or old journal."""
    repo = repo_root.resolve(strict=True)
    if not _ID.fullmatch(txid):
        raise HistoricalRetirementError("Exact transaction ID required")
    with reservation_coordinator(repo):
        root = transaction_root(repo)
        escrow = root / txid / "historical-retirement"
        _owned_file(escrow / "receipt.json", root)
        receipt = _json(escrow / "receipt.json")
        if (receipt.get("kind") != "devtool.historical-marker-retirement"
                or receipt.get("transaction_id") != txid or receipt.get("repo") != str(repo)
                or receipt.get("state") not in {"retired-preserve-current", "preparing", "restoring"}):
            raise HistoricalRetirementError("Escrow receipt identity/state mismatch")
        _owned_file(root / txid / "transaction.json", root)
        tx = _json(root / txid / "transaction.json")
        if _sha(root / txid / "transaction.json") != receipt.get("source_journal_sha256"):
            raise HistoricalRetirementError("Retained journal changed after retirement")
        if (str(tx.get("transaction_id")) != txid
                or str(tx.get("repo_root")) != str(repo)
                or str(tx.get("status")) != "rollback-failed"):
            raise HistoricalRetirementError("Retained journal identity/state changed")
        _safe_affected(repo, tx, root / txid)
        current = _identities(repo, tx)
        if current["current_head"] != receipt.get("current_head") or current["index_sha256"] != receipt.get("index_sha256"):
            raise HistoricalRetirementError("Current HEAD/index changed; refusing to reactivate historical reservations")
        entries = receipt.get("markers")
        if not isinstance(entries, list) or not (1 <= len(entries) <= 2):
            raise HistoricalRetirementError("Escrow marker inventory invalid")
        movements = []
        for item in entries:
            rel = str(item.get("relative_path") or "")
            if rel not in {"active.json", f"active/{txid}.json"}:
                raise HistoricalRetirementError("Unexpected escrow marker path")
            target = root / rel
            escrowed = escrow / ("legacy.json" if rel == "active.json" else "transaction-marker.json")
            _owned_file(target, root)
            _owned_file(escrowed, root)
            if target.exists() or target.is_symlink():
                # A previous unretire may have been interrupted after moving
                # one of two pointer files. Accept only byte-identical already
                # restored pointers, not a different active reservation.
                _regular(target)
                if _sha(target) != item.get("sha256") or escrowed.exists() or escrowed.is_symlink():
                    raise HistoricalRetirementError("Active marker collision or duplicate escrow pointer")
                continue
            _regular(escrowed)
            if _sha(escrowed) != item.get("sha256"):
                raise HistoricalRetirementError("Escrow marker digest mismatch")
            movements.append((escrowed, target))
        receipt["state"] = "restoring"
        _atomic_write_json(escrow / "receipt.json", receipt)
        for source, dest in movements:
            dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.replace(source, dest)
        receipt["state"] = "unretired"
        _atomic_write_json(escrow / "receipt.json", receipt)
        return {"ok": True, "state": "unretired", "transaction_id": txid,
                "worktree_mutated": False, "rollback_verified": False, "escrow": str(escrow)}
