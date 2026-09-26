from __future__ import annotations

"""GIT-FRX-17 Devtool-owned, validation-gated conflict memories."""

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Callable, Iterable, Mapping

from .git_state import GitStateError, collect_git_state
from .xdg import state_dir

RERERE_SCHEMA_VERSION = 1
RERERE_MODES = ("off", "suggest", "validated-auto")
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class GitRerereError(RuntimeError):
    pass


def normalize_rerere_mode(value: object) -> str:
    mode = str(value or "off").strip().lower().replace("_", "-")
    if mode not in RERERE_MODES:
        raise GitRerereError(f"Unsupported rerere mode {value!r}; expected off, suggest, or validated-auto")
    return mode


def _safe(value: str, fallback: str) -> str:
    return _SAFE.sub("-", str(value or "").strip()).strip(".-") or fallback


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(root), *args], text=True, encoding="utf-8", errors="surrogateescape", stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=15)


def _repository_id(root: Path) -> str:
    try:
        return str(collect_git_state(root).repository_id or "")
    except GitStateError as exc:
        raise GitRerereError(str(exc)) from exc


def capture_conflict_preimage(repo_root: str | Path, *, campaign_id: str = "") -> dict[str, Any]:
    root = Path(repo_root).expanduser().resolve()
    proc = _git(root, "ls-files", "-u", "-z")
    if proc.returncode != 0:
        raise GitRerereError((proc.stderr or proc.stdout or "Unable to inspect conflicts").strip())
    entries: list[dict[str, Any]] = []
    for raw in (proc.stdout or "").split("\0"):
        if not raw:
            continue
        meta, _, path = raw.partition("\t")
        bits = meta.split()
        if len(bits) >= 3 and path:
            entries.append({"mode": bits[0], "blob": bits[1], "stage": bits[2], "path": path})
    if not entries:
        raise GitRerereError("No unresolved Git conflicts are present")
    payload = {
        "schema_version": RERERE_SCHEMA_VERSION,
        "repository_id": _repository_id(root),
        "campaign_id": str(campaign_id or ""),
        "entries": sorted(entries, key=lambda row: (row["path"], row["stage"], row["blob"])),
    }
    payload["preimage_digest"] = _digest(payload)
    return payload


def _memory_path(repository_id: str, campaign_id: str, preimage_digest: str) -> Path:
    return state_dir() / "git-rerere" / _safe(repository_id, "repo") / _safe(campaign_id, "no-campaign") / preimage_digest / "resolution.json"


def _pending_path(repository_id: str, campaign_id: str) -> Path:
    return state_dir() / "git-rerere" / _safe(repository_id, "repo") / _safe(campaign_id, "no-campaign") / "pending-preimage.json"

def capture_pending_preimage(repo_root: str | Path, *, campaign_id: str = "") -> dict[str, Any]:
    preimage = capture_conflict_preimage(repo_root, campaign_id=campaign_id)
    path = _pending_path(str(preimage["repository_id"]), str(campaign_id or ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(preimage, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return {"schema_version": RERERE_SCHEMA_VERSION, "kind": "devtool.git-rerere-preimage", "preimage": preimage, "path": str(path)}

def load_pending_preimage(repo_root: str | Path, *, campaign_id: str = "") -> dict[str, Any]:
    root = Path(repo_root).expanduser().resolve()
    repository_id = _repository_id(root)
    path = _pending_path(repository_id, str(campaign_id or ""))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise GitRerereError("No captured conflict preimage; run 'devtool git rerere capture' before resolving the conflict") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise GitRerereError(f"Unable to read captured conflict preimage: {exc}") from exc
    if not isinstance(payload, dict) or str(payload.get("repository_id") or "") != repository_id or str(payload.get("campaign_id") or "") != str(campaign_id or ""):
        raise GitRerereError("Captured conflict preimage does not match the current repository/campaign identity")
    return payload

def record_pending_resolution(
    repo_root: str | Path, *, campaign_id: str = "", resolved_paths: Iterable[str], validation: Mapping[str, Any]
) -> dict[str, Any]:
    root = Path(repo_root).expanduser().resolve()
    preimage = load_pending_preimage(root, campaign_id=campaign_id)
    payload = record_resolution(root, preimage, resolved_paths=resolved_paths, campaign_id=campaign_id, validation=validation)
    try:
        _pending_path(str(preimage["repository_id"]), str(campaign_id or "")).unlink()
    except FileNotFoundError:
        pass
    return payload


def record_resolution(
    repo_root: str | Path,
    preimage: Mapping[str, Any],
    *,
    resolved_paths: Iterable[str],
    campaign_id: str = "",
    validation: Mapping[str, Any],
) -> dict[str, Any]:
    root = Path(repo_root).expanduser().resolve()
    repository_id = _repository_id(root)
    if repository_id != str(preimage.get("repository_id") or ""):
        raise GitRerereError("Conflict preimage belongs to a different repository identity")
    if str(preimage.get("campaign_id") or "") != str(campaign_id or ""):
        raise GitRerereError("Conflict preimage belongs to a different campaign context")
    if str(validation.get("status") or "").lower() not in {"pass", "passed", "succeeded", "success"}:
        raise GitRerereError("Rerere resolution memory requires successful validation evidence")
    files: list[dict[str, Any]] = []
    for rel in sorted(set(str(item) for item in resolved_paths if str(item).strip())):
        path = (root / rel).resolve(strict=False)
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise GitRerereError(f"Resolution path escapes repository: {rel}") from exc
        if not path.is_file():
            raise GitRerereError(f"Resolved conflict path is not a file: {rel}")
        data = path.read_bytes()
        files.append({"path": rel, "sha256": hashlib.sha256(data).hexdigest(), "content_b64": base64.b64encode(data).decode("ascii")})
    if not files:
        raise GitRerereError("No resolved paths were supplied")
    payload = {
        "schema_version": RERERE_SCHEMA_VERSION,
        "kind": "devtool.git-rerere-memory",
        "repository_id": repository_id,
        "campaign_id": str(campaign_id or ""),
        "preimage_digest": str(preimage.get("preimage_digest") or ""),
        "preimage_entries": list(preimage.get("entries") or []),
        "files": files,
        "validation": dict(validation),
    }
    payload["resolution_digest"] = _digest({key: value for key, value in payload.items() if key != "resolution_digest"})
    path = _memory_path(repository_id, str(campaign_id or ""), payload["preimage_digest"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    payload["path"] = str(path)
    return payload


def suggest_resolution(repo_root: str | Path, *, campaign_id: str = "") -> dict[str, Any]:
    preimage = capture_conflict_preimage(repo_root, campaign_id=campaign_id)
    path = _memory_path(str(preimage["repository_id"]), str(campaign_id or ""), str(preimage["preimage_digest"]))
    if not path.is_file():
        pending = _pending_path(str(preimage["repository_id"]), str(campaign_id or ""))
        pending.parent.mkdir(parents=True, exist_ok=True)
        tmp = pending.with_suffix(pending.suffix + ".tmp")
        tmp.write_text(json.dumps(preimage, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, pending)
        return {"schema_version": RERERE_SCHEMA_VERSION, "kind": "devtool.git-rerere-suggestion", "matched": False, "preimage": preimage, "pending_preimage_path": str(pending)}
    try:
        memory = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GitRerereError(f"Unable to read rerere memory: {exc}") from exc
    # Exact scoped identity is mandatory. There is deliberately no fuzzy or
    # global conflict matching: stale memories fail closed.
    valid = (
        isinstance(memory, dict)
        and str(memory.get("repository_id") or "") == str(preimage["repository_id"])
        and str(memory.get("campaign_id") or "") == str(campaign_id or "")
        and str(memory.get("preimage_digest") or "") == str(preimage["preimage_digest"])
        and str((memory.get("validation") or {}).get("status") or "").lower() in {"pass", "passed", "succeeded", "success"}
    )
    if not valid:
        raise GitRerereError("Stored rerere resolution is stale or outside the current repository/campaign conflict identity")
    return {"schema_version": RERERE_SCHEMA_VERSION, "kind": "devtool.git-rerere-suggestion", "matched": True, "preimage": preimage, "memory": memory}


def apply_validated_resolution(
    repo_root: str | Path,
    *,
    campaign_id: str = "",
    mode: str = "suggest",
    validate: Callable[[], bool],
) -> dict[str, Any]:
    normalized = normalize_rerere_mode(mode)
    suggestion = suggest_resolution(repo_root, campaign_id=campaign_id)
    if not suggestion.get("matched"):
        return {**suggestion, "applied": False, "reason": "no exact conflict memory"}
    if normalized != "validated-auto":
        return {**suggestion, "applied": False, "reason": f"rerere mode {normalized} does not auto-apply"}
    root = Path(repo_root).expanduser().resolve()
    memory = suggestion["memory"]
    backups: dict[str, bytes | None] = {}
    try:
        for row in memory.get("files", []):
            rel = str(row.get("path") or "")
            path = (root / rel).resolve(strict=False)
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise GitRerereError(f"Stored rerere path escapes repository: {rel}") from exc
            backups[rel] = path.read_bytes() if path.exists() and path.is_file() else None
            data = base64.b64decode(str(row.get("content_b64") or ""), validate=True)
            if hashlib.sha256(data).hexdigest() != str(row.get("sha256") or ""):
                raise GitRerereError(f"Stored rerere resolution content digest is stale for {rel}")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        validation_ok = bool(validate())
        if not validation_ok:
            raise GitRerereError("Reused rerere resolution failed current validation")
    except Exception:
        for rel, prior in backups.items():
            path = root / rel
            if prior is None:
                try: path.unlink()
                except FileNotFoundError: pass
            else:
                path.write_bytes(prior)
        raise
    return {**suggestion, "applied": True, "validation_rerun": True, "resolution_digest": str(memory.get("resolution_digest") or "")}


__all__ = [
    "GitRerereError", "RERERE_MODES", "apply_validated_resolution", "capture_conflict_preimage",
    "capture_pending_preimage", "load_pending_preimage", "normalize_rerere_mode", "record_pending_resolution",
    "record_resolution", "suggest_resolution",
]
