from __future__ import annotations

"""Repository-local immutable registry for produced build artifacts."""

from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from typing import Any, Iterable, Iterator
import uuid

from .core.events import ArtifactCreated, publish_event
from .federation_ownership import attach_federation_operational_owner

REGISTRY_SCHEMA = 2

_ARCHIVE_SUFFIXES = (
    ".tar.zst", ".tar.gz", ".tar.xz", ".tar.bz2", ".tgz", ".txz", ".tbz2",
    ".whl", ".nupkg", ".snupkg", ".apk", ".aab", ".aar", ".jar", ".war",
    ".ear", ".crate", ".deb", ".rpm", ".zip", ".tar",
)
_TRUE_STRINGS = {"1", "true", "yes", "y", "on"}
_FALSE_STRINGS = {"0", "false", "no", "n", "off", ""}
_BOOLEAN_METADATA_KEYS = {
    "valid", "verification_requested", "verified", "signed", "copied", "required",
    "used_cached_outputs", "external_tools", "external_required", "debuggable",
    "release", "instrumented", "exported", "didWork", "did_work",
}


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUE_STRINGS:
            return True
        if lowered in _FALSE_STRINGS:
            return False
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _artifact_type(path: Path) -> str:
    name = path.name.lower()
    for suffix in _ARCHIVE_SUFFIXES:
        if name.endswith(suffix):
            return suffix.lstrip(".")
    return path.suffix.lower().lstrip(".") or "file"


def _normalize_metadata_booleans(value: Any, *, key: str = "") -> Any:
    if isinstance(value, dict):
        return {str(k): _normalize_metadata_booleans(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize_metadata_booleans(item) for item in value]
    if key in _BOOLEAN_METADATA_KEYS:
        return _coerce_bool(value, False)
    return value


def _parse_registry_schema(value: Any) -> int | None:
    if value in (None, ""):
        return 1
    if isinstance(value, bool):
        return None
    try:
        schema = int(value)
    except (TypeError, ValueError):
        return None
    return schema if schema > 0 else None


def _parse_timestamp(value: Any) -> datetime:
    raw = str(value or "")
    try:
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        return stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


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
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        return proc.stdout.strip() if proc.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _empty_registry(*, warning: str = "") -> dict[str, Any]:
    data: dict[str, Any] = {"schema_version": REGISTRY_SCHEMA, "items": [], "verifications": []}
    if warning:
        data["warnings"] = [warning]
    return data


def _backup_corrupt_registry(path: Path, reason: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"registry.corrupt-{stamp}-{uuid.uuid4().hex[:8]}.json")
    try:
        shutil.copy2(path, backup)
        return f"Artifact registry was malformed and preserved as {backup.name}: {reason}"
    except OSError as exc:
        return f"Artifact registry is malformed ({reason}) and backup failed: {exc}"


def _load_registry_unlocked(repo_root: Path, *, preserve_corrupt: bool = True) -> dict[str, Any]:
    path = registry_path(repo_root)
    if not path.is_file():
        return _empty_registry()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        warning = _backup_corrupt_registry(path, str(exc)) if preserve_corrupt else str(exc)
        return _empty_registry(warning=warning)
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        warning = _backup_corrupt_registry(path, "expected object with items list") if preserve_corrupt else "invalid registry structure"
        return _empty_registry(warning=warning)
    # Schema 1 records remain readable. New writes are always schema 2.
    schema = _parse_registry_schema(data.get("schema_version"))
    if schema is None:
        warning = _backup_corrupt_registry(path, "invalid schema_version") if preserve_corrupt else "invalid schema_version"
        return _empty_registry(warning=warning)
    data["schema_version"] = schema
    if not isinstance(data.get("verifications"), list):
        data["verifications"] = []
    return data


@contextmanager
def _registry_lock(repo_root: Path, *, timeout: float = 15.0) -> Iterator[None]:
    lock_path = registry_path(repo_root).with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    if os.name == "nt":
        # msvcrt.locking() needs a byte range that exists. Seed the otherwise
        # empty lock file once; the byte is only a synchronization sentinel.
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
    deadline = time.monotonic() + timeout
    acquired = False
    try:
        if os.name == "nt":
            import msvcrt
            while not acquired:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    acquired = True
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"Timed out locking artifact registry: {lock_path}")
                    time.sleep(0.05)
        else:
            import fcntl
            while not acquired:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"Timed out locking artifact registry: {lock_path}")
                    time.sleep(0.05)
        yield
    finally:
        if acquired:
            try:
                if os.name == "nt":
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()


def load_registry(repo_root: Path) -> dict[str, Any]:
    with _registry_lock(repo_root):
        return _load_registry_unlocked(repo_root)


def _save_registry_unlocked(repo_root: Path, data: dict[str, Any]) -> Path:
    path = registry_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(data)
    payload["schema_version"] = REGISTRY_SCHEMA
    payload["updated_at"] = _now()
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        if os.name == "posix":
            directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        tmp.unlink(missing_ok=True)
    return path


def _append_only_merge(current: list[Any], incoming: list[Any]) -> list[Any]:
    """Merge event lists without rewriting or dropping existing events."""
    existing = [dict(item) for item in current if isinstance(item, dict)]
    known = {str(item.get("id") or item.get("event_key") or "") for item in existing}
    additions: list[dict[str, Any]] = []
    for raw in incoming:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        identity = str(item.get("id") or item.get("event_key") or "")
        if identity and identity in known:
            # Existing production/verification events are immutable. A stale
            # caller may re-submit them, but cannot rewrite them.
            continue
        additions.append(item)
        if identity:
            known.add(identity)
    return [*additions, *existing]


def save_registry(repo_root: Path, data: dict[str, Any]) -> Path:
    """Safely merge append-only registry data under the registry lock.

    Mutating operations that intentionally remove history (currently only
    ``prune_registry``) operate under the same lock directly. This generic save
    helper cannot lose concurrent events or rewrite an existing event by id.
    """
    with _registry_lock(repo_root):
        current = _load_registry_unlocked(repo_root)
        merged = dict(current)
        for key, value in data.items():
            if key in {"items", "verifications"} and isinstance(value, list):
                merged[key] = _append_only_merge(list(current.get(key, [])), value)
            elif key not in {"schema_version", "updated_at"}:
                merged[key] = value
        if current.get("warnings") and not merged.get("warnings"):
            merged["warnings"] = current["warnings"]
        return _save_registry_unlocked(repo_root, merged)


def resolve_artifact_path(repo_root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = repo_root / path
    return path.resolve()


def _display_path(repo_root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)


def register_paths(
    repo_root: Path,
    paths: Iterable[Path],
    *,
    metadata: dict[str, Any] | None = None,
    item_metadata: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Append immutable artifact production events to the registry.

    Re-registering the same path intentionally creates a new record. The record
    represents a build/package event, not mutable cache state at a filesystem
    location. Consumers that want the latest path naturally receive the first
    matching item because records are newest-first.
    """
    candidates = [Path(raw).expanduser().resolve() for raw in paths]
    candidates = list(dict.fromkeys(path for path in candidates if path.is_file()))
    if not candidates:
        return []
    commit = _head(repo_root)
    shared = dict(metadata or {})
    run_id = str(shared.get("run_id") or os.environ.get("DEVTOOL_RUN_ID") or "").strip()
    registered: list[dict[str, Any]] = []
    with _registry_lock(repo_root):
        data = _load_registry_unlocked(repo_root)
        items = [item for item in data.get("items", []) if isinstance(item, dict)]
        for path in candidates:
            display = _display_path(repo_root, path)
            specific: dict[str, Any] = {}
            if item_metadata:
                specific = dict(item_metadata.get(str(path)) or item_metadata.get(display) or {})
            combined_metadata = dict(shared)
            combined_metadata.update(specific)
            combined_metadata = attach_federation_operational_owner(combined_metadata, repo_root, run_id=run_id)
            combined_metadata = _normalize_metadata_booleans(combined_metadata)
            sha256 = _sha256(path)
            registered_at = _now()
            source_path = str(combined_metadata.get("source_path") or display)
            record: dict[str, Any] = {
                "id": uuid.uuid4().hex[:12],
                "name": path.name,
                "path": display,
                "canonical_path": display,
                "source_path": source_path,
                "exported_path": display if source_path != display else "",
                "type": str(combined_metadata.get("type") or _artifact_type(path)),
                "size": path.stat().st_size,
                "sha256": sha256,
                "commit": commit,
                "run_id": run_id,
                "producer": str(combined_metadata.get("producer") or ""),
                "command": str(combined_metadata.get("command") or ""),
                "target": str(combined_metadata.get("target") or ""),
                "variant": str(combined_metadata.get("variant") or ""),
                "state": str(combined_metadata.get("state") or "discovered"),
                "producer_task": str(combined_metadata.get("producer_task") or ""),
                "producer_node_id": str(combined_metadata.get("producer_node_id") or combined_metadata.get("node_id") or combined_metadata.get("workflow_node_key") or ""),
                "process_id": str(combined_metadata.get("process_id") or combined_metadata.get("execution_process_id") or ""),
                "verification_requested": _coerce_bool(combined_metadata.get("verification_requested", False), False),
                "verification_status": str(combined_metadata.get("verification_status") or ""),
                "created_at": registered_at,
                "registered_at": registered_at,
                "valid": _coerce_bool(combined_metadata.get("valid", True), True),
                "metadata": combined_metadata,
            }
            # Stable event identity is useful for cross-process reconciliation
            # without making the record itself mutable.
            record["event_key"] = f"{run_id or 'runless'}:{sha256}:{record['id']}"
            items.insert(0, record)
            registered.append(record)
            publish_event(ArtifactCreated(
                path=display,
                artifact_id=str(record["id"]),
                sha256=sha256,
                size=int(record["size"]),
                artifact_type=str(record["type"]),
                producer_task=str(record.get("producer_task") or ""),
                producer_node_id=str(record.get("producer_node_id") or ""),
                process_id=str(record.get("process_id") or ""),
                verification_status=str(record.get("verification_status") or ""),
                run_id=str(run_id or ""),
                node_id=str(record.get("producer_node_id") or ""),
                parent_id=str(combined_metadata.get("parent_id") or combined_metadata.get("workflow_parent_id") or ""),
                source="artifact_registry",
                metadata={
                    "id": record["id"], "sha256": sha256,
                    "size": record["size"], "type": record["type"],
                    "run_id": run_id,
                },
            ))
        data["items"] = items
        _save_registry_unlocked(repo_root, data)
    return registered


def register_android_metadata(repo_root: Path, metadata: dict[str, Any]) -> list[dict[str, Any]]:
    paths: list[Path] = []
    per_item: dict[str, dict[str, Any]] = {}
    shared = {
        key: value for key, value in metadata.items()
        if key not in {"items", "registry"}
    }
    for item in metadata.get("items", []):
        if not isinstance(item, dict):
            continue
        value = str(item.get("path") or "").strip()
        if not value:
            continue
        path = resolve_artifact_path(repo_root, value)
        if not path.is_file():
            continue
        paths.append(path)
        per_item[str(path)] = dict(item)
    return register_paths(repo_root, paths, metadata=shared, item_metadata=per_item)


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
        try:
            candidate = resolve_artifact_path(repo_root, str(item.get("path") or ""))
        except OSError:
            continue
        if candidate == resolved:
            return item
    return None


def verify_record(repo_root: Path, selector: str) -> dict[str, Any]:
    """Verify an artifact without mutating its immutable production record.

    Verification is a new event in ``verifications``.  The production record
    keeps the validity state established when the artifact was originally
    produced/registered, while callers receive the current filesystem result.
    """
    record = find_record(repo_root, selector)
    path = resolve_artifact_path(repo_root, str(record.get("path") if record else selector))
    result = dict(record or {"id": "", "name": path.name, "path": str(path)})
    exists = path.is_file()
    current = _sha256(path) if exists else ""
    expected = str(result.get("sha256") or "")
    valid = exists and (not expected or current == expected)
    verified_at = _now()
    result.update({"exists": exists, "current_sha256": current, "valid": valid, "verified_at": verified_at})
    if record is not None:
        event = {
            "id": uuid.uuid4().hex[:12],
            "artifact_id": str(record.get("id") or ""),
            "path": str(record.get("path") or ""),
            "expected_sha256": expected,
            "current_sha256": current,
            "exists": exists,
            "valid": valid,
            "verified_at": verified_at,
        }
        with _registry_lock(repo_root):
            data = _load_registry_unlocked(repo_root)
            verifications = [item for item in data.get("verifications", []) if isinstance(item, dict)]
            verifications.insert(0, event)
            data["verifications"] = verifications
            _save_registry_unlocked(repo_root, data)
        result["verification_id"] = event["id"]
    return result


def prune_registry(repo_root: Path, *, older_than_days: float, delete_files: bool = False) -> dict[str, Any]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(0, older_than_days))
    managed = (repo_root / ".devtool" / "build-artifacts").resolve()
    with _registry_lock(repo_root):
        data = _load_registry_unlocked(repo_root)
        original_items = [item for item in data.get("items", []) if isinstance(item, dict)]
        kept: list[dict[str, Any]] = []
        removed: list[dict[str, Any]] = []
        retained_paths_all: set[str] = set()
        for item in original_items:
            stamp = _parse_timestamp(item.get("registered_at") or item.get("created_at"))
            if stamp >= cutoff:
                kept.append(item)
                retained_paths_all.add(str(item.get("path") or ""))

        for item in original_items:
            if item in kept:
                continue
            path_value = str(item.get("path") or "")
            path = resolve_artifact_path(repo_root, path_value)
            deleted = False
            if delete_files and path.is_file():
                try:
                    path.relative_to(managed)
                except ValueError:
                    pass
                else:
                    # A historical record may share its path with a retained
                    # newer record that appears later in the registry. Compute
                    # all retained paths before deleting anything so registry
                    # order cannot make pruning destructive.
                    if path_value not in retained_paths_all:
                        path.unlink()
                        deleted = True
            copy = dict(item)
            copy["file_deleted"] = deleted
            removed.append(copy)
        data["items"] = kept
        _save_registry_unlocked(repo_root, data)
    return {"removed": removed, "remaining": len(kept), "delete_files": delete_files}
