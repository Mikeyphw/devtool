from __future__ import annotations

"""Repository-local registry for produced build artifacts."""

from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, Iterable
import uuid

REGISTRY_SCHEMA = 1

def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def registry_path(repo_root: Path) -> Path:
    return repo_root / ".devtool" / "artifacts" / "registry.json"

def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()

def _head(repo_root: Path) -> str:
    try:
        proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True, timeout=3, check=False)
        return proc.stdout.strip() if proc.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""

def load_registry(repo_root: Path) -> dict[str, Any]:
    path = registry_path(repo_root)
    if not path.is_file():
        return {"schema_version": REGISTRY_SCHEMA, "items": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema_version": REGISTRY_SCHEMA, "items": []}
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return {"schema_version": REGISTRY_SCHEMA, "items": []}
    return data

def save_registry(repo_root: Path, data: dict[str, Any]) -> Path:
    path = registry_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    data["schema_version"] = REGISTRY_SCHEMA
    data["updated_at"] = _now()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path

def resolve_artifact_path(repo_root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = repo_root / path
    return path.resolve()

def register_paths(repo_root: Path, paths: Iterable[Path], *, metadata: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    data = load_registry(repo_root)
    items = [item for item in data.get("items", []) if isinstance(item, dict)]
    by_path = {str(item.get("path") or ""): item for item in items}
    registered: list[dict[str, Any]] = []
    commit = _head(repo_root)
    for raw in paths:
        path = raw.resolve()
        if not path.is_file():
            continue
        try:
            display = str(path.relative_to(repo_root))
        except ValueError:
            display = str(path)
        record = by_path.get(display, {})
        record.update({
            "id": record.get("id") or uuid.uuid4().hex[:12],
            "name": path.name,
            "path": display,
            "type": path.suffix.lower().lstrip(".") or "file",
            "size": path.stat().st_size,
            "sha256": _sha256(path),
            "commit": commit,
            "created_at": record.get("created_at") or _now(),
            "registered_at": _now(),
            "valid": True,
        })
        if metadata:
            record["metadata"] = dict(metadata)
        by_path[display] = record
        registered.append(record)
    data["items"] = sorted(by_path.values(), key=lambda item: str(item.get("registered_at") or ""), reverse=True)
    save_registry(repo_root, data)
    return registered

def register_android_metadata(repo_root: Path, metadata: dict[str, Any]) -> list[dict[str, Any]]:
    paths: list[Path] = []
    item_metadata: dict[str, dict[str, Any]] = {}
    for item in metadata.get("items", []):
        if not isinstance(item, dict):
            continue
        value = str(item.get("path") or "").strip()
        if not value:
            continue
        path = resolve_artifact_path(repo_root, value)
        paths.append(path)
        item_metadata[str(path)] = item
    registered = register_paths(repo_root, paths)
    if not registered:
        return []
    data = load_registry(repo_root)
    for record in data.get("items", []):
        if not isinstance(record, dict):
            continue
        path = resolve_artifact_path(repo_root, str(record.get("path") or ""))
        source = item_metadata.get(str(path))
        if source:
            record["metadata"] = source
            record["valid"] = bool(source.get("valid", True))
    save_registry(repo_root, data)
    return registered

def find_record(repo_root: Path, selector: str) -> dict[str, Any] | None:
    data = load_registry(repo_root)
    selector = str(selector).strip()
    candidates = [item for item in data.get("items", []) if isinstance(item, dict)]
    for item in candidates:
        if selector in {str(item.get("id") or ""), str(item.get("path") or ""), str(item.get("name") or "")}:
            return item
    try:
        resolved = resolve_artifact_path(repo_root, selector)
    except OSError:
        return None
    for item in candidates:
        if resolve_artifact_path(repo_root, str(item.get("path") or "")) == resolved:
            return item
    return None

def verify_record(repo_root: Path, selector: str) -> dict[str, Any]:
    record = find_record(repo_root, selector)
    path = resolve_artifact_path(repo_root, str(record.get("path") if record else selector))
    result = dict(record or {"id": "", "name": path.name, "path": str(path)})
    exists = path.is_file()
    current = _sha256(path) if exists else ""
    expected = str(result.get("sha256") or "")
    valid = exists and (not expected or current == expected)
    result.update({"exists": exists, "current_sha256": current, "valid": valid, "verified_at": _now()})
    if record is not None:
        data = load_registry(repo_root)
        for item in data.get("items", []):
            if isinstance(item, dict) and item.get("id") == record.get("id"):
                item.update({"valid": valid, "verified_at": result["verified_at"]})
        save_registry(repo_root, data)
    return result

def prune_registry(repo_root: Path, *, older_than_days: float, delete_files: bool = False) -> dict[str, Any]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(0, older_than_days))
    data = load_registry(repo_root)
    kept: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    managed = (repo_root / ".devtool" / "build-artifacts").resolve()
    for item in data.get("items", []):
        if not isinstance(item, dict):
            continue
        raw = str(item.get("registered_at") or item.get("created_at") or "")
        try:
            stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            stamp = datetime.now(timezone.utc)
        if stamp >= cutoff:
            kept.append(item)
            continue
        path = resolve_artifact_path(repo_root, str(item.get("path") or ""))
        deleted = False
        if delete_files and path.is_file():
            try:
                path.relative_to(managed)
            except ValueError:
                pass
            else:
                path.unlink()
                deleted = True
        copy = dict(item)
        copy["file_deleted"] = deleted
        removed.append(copy)
    data["items"] = kept
    save_registry(repo_root, data)
    return {"removed": removed, "remaining": len(kept), "delete_files": delete_files}
