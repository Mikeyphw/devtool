from __future__ import annotations

"""Durable repository-pack lifecycle authority (REMED-R03B / LIFE-A08).

The registry is source-repository local. It records published pack identities and
retention policy without making filenames, age, or the registry row alone a
deletion authority. Pack GC is lowered through ``cleanup_lifecycle`` and retains
tombstoned history rather than erasing evidence.
"""

from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

SCHEMA_VERSION = 1
_PROTECTED_RETENTION = {"release", "final", "forever", "keep", "pinned"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def repository_pack_registry_path(repo_root: str | Path) -> Path:
    root = Path(repo_root).expanduser().resolve(strict=False)
    dotgit = root / ".git"
    if dotgit.is_dir():
        return dotgit / "devtool" / "repository-packs" / "registry.json"
    if dotgit.is_file():
        try:
            first = dotgit.read_text(encoding="utf-8").splitlines()[0].strip()
            if first.lower().startswith("gitdir:"):
                raw = first.split(":", 1)[1].strip()
                gitdir = Path(raw)
                if not gitdir.is_absolute():
                    gitdir = (root / gitdir).resolve(strict=False)
                return gitdir / "devtool" / "repository-packs" / "registry.json"
        except (OSError, IndexError):
            pass
    return root / ".devtool" / "private" / "repository-packs" / "registry.json"


def load_repository_pack_registry(repo_root: str | Path, *, strict: bool = False) -> dict[str, Any]:
    path = repository_pack_registry_path(repo_root)
    if not path.is_file():
        return {"schema_version": SCHEMA_VERSION, "items": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        if strict:
            raise ValueError(f"unreadable repository-pack registry {path}: {type(exc).__name__}: {exc}") from exc
        return {"schema_version": SCHEMA_VERSION, "items": [], "warnings": [str(exc)]}
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        if strict:
            raise ValueError(f"invalid repository-pack registry {path}: expected object with items list")
        return {"schema_version": SCHEMA_VERSION, "items": [], "warnings": ["invalid registry structure"]}
    if int(payload.get("schema_version") or 0) != SCHEMA_VERSION:
        if strict:
            raise ValueError(f"unsupported repository-pack registry schema in {path}")
    return payload


def _save(repo_root: str | Path, payload: Mapping[str, Any]) -> Path:
    path = repository_pack_registry_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = dict(payload)
    data["schema_version"] = SCHEMA_VERSION
    data["updated_at"] = _now()
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(raw)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, sort_keys=True, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return path


def register_repository_pack(repo_root: str | Path, *, plan: Any, receipt: Any, repository: Any) -> dict[str, Any]:
    root = Path(repo_root).expanduser().resolve(strict=False)
    output = Path(str(receipt.output)).expanduser().resolve(strict=False)
    if not output.is_file():
        raise ValueError(f"cannot register missing repository pack: {output}")
    current = _sha256(output)
    if current != str(receipt.sha256):
        raise ValueError("repository pack bytes changed before lifecycle registration")
    payload = load_repository_pack_registry(root, strict=True)
    items = [dict(item) for item in payload.get("items", []) if isinstance(item, Mapping)]
    record = {
        "pack_id": str(receipt.pack_id),
        "receipt_id": str(receipt.receipt_id),
        "plan_id": str(receipt.plan_id),
        "output": str(output),
        "sha256": str(receipt.sha256),
        "size": int(receipt.size),
        "created_at": _now(),
        "state": "published",
        "pin": bool(receipt.pin),
        "retention": str(receipt.retention or "policy"),
        "mode": str(getattr(plan.mode, "value", plan.mode)),
        "compression": str(getattr(plan.compression, "value", plan.compression)),
        "source_repository": str(root),
        "source_repository_id": str(getattr(repository.identity, "fingerprint", "")),
        "source_head": str(getattr(repository, "head", "")),
        "selected_commit": str(getattr(repository, "selected_commit", "")),
        "source_fingerprint": str(getattr(repository, "source_fingerprint", "")),
    }
    # Publication is immutable by pack identity. Re-registering the same pack at
    # the same output refreshes no lifecycle state; pin/retention changes use the
    # explicit mutation API below.
    existing = next((item for item in items if item.get("pack_id") == record["pack_id"] and item.get("output") == record["output"] and item.get("state") != "tombstoned"), None)
    if existing is None:
        items.insert(0, record)
        payload["items"] = items
        _save(root, payload)
        return record
    return dict(existing)


def list_repository_packs(repo_root: str | Path, *, include_tombstoned: bool = False) -> list[dict[str, Any]]:
    rows = [dict(item) for item in load_repository_pack_registry(repo_root, strict=True).get("items", []) if isinstance(item, Mapping)]
    return rows if include_tombstoned else [item for item in rows if str(item.get("state") or "published") != "tombstoned"]


def _select(repo_root: str | Path, selector: str) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = load_repository_pack_registry(repo_root, strict=True)
    value = str(selector or "").strip()
    for item in payload.get("items", []):
        if not isinstance(item, Mapping):
            continue
        if value in {str(item.get("pack_id") or ""), str(item.get("receipt_id") or ""), str(item.get("output") or "")}:
            return payload, dict(item)
    raise KeyError(value)


def repository_pack_status(repo_root: str | Path, selector: str) -> dict[str, Any]:
    _payload, item = _select(repo_root, selector)
    output = Path(str(item.get("output") or "")).expanduser()
    exists = output.is_file()
    current = _sha256(output) if exists else ""
    expected = str(item.get("sha256") or "")
    return {
        **item,
        "exists": exists,
        "current_sha256": current,
        "verified": bool(exists and expected and current == expected),
        "retention_protected": bool(item.get("pin")) or str(item.get("retention") or "policy").lower() in _PROTECTED_RETENTION,
    }


def set_repository_pack_pin(repo_root: str | Path, selector: str, *, pin: bool) -> dict[str, Any]:
    root = Path(repo_root).expanduser().resolve(strict=False)
    payload, selected = _select(root, selector)
    items: list[dict[str, Any]] = []
    updated: dict[str, Any] | None = None
    for raw in payload.get("items", []):
        item = dict(raw) if isinstance(raw, Mapping) else {}
        if item.get("pack_id") == selected.get("pack_id") and item.get("output") == selected.get("output") and item.get("state") != "tombstoned":
            item["pin"] = bool(pin)
            item["retention_updated_at"] = _now()
            updated = item
        items.append(item)
    if updated is None:
        raise KeyError(selector)
    payload["items"] = items
    _save(root, payload)
    return updated


def prune_repository_packs(repo_root: str | Path, *, older_than_days: float, dry_run: bool = False) -> dict[str, Any]:
    from .cleanup_lifecycle import (
        CleanupScope,
        GeneratedOutputDescriptor,
        apply_cleanup_plan,
        build_cleanup_ownership_view,
        compile_cleanup_plan,
        persist_cleanup_receipt,
    )

    root = Path(repo_root).expanduser().resolve(strict=False)
    payload = load_repository_pack_registry(root, strict=True)
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(0.0, float(older_than_days)))
    candidates: list[tuple[dict[str, Any], GeneratedOutputDescriptor]] = []
    protective_refs = []
    for raw in payload.get("items", []):
        if not isinstance(raw, Mapping):
            continue
        item = dict(raw)
        if str(item.get("state") or "published") == "tombstoned":
            continue
        if bool(item.get("pin")) or str(item.get("retention") or "policy").lower() in _PROTECTED_RETENTION:
            continue
        try:
            created = datetime.fromisoformat(str(item.get("created_at") or "").replace("Z", "+00:00"))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if created.astimezone(timezone.utc) >= cutoff:
            continue
        output = str(item.get("output") or "").strip()
        if not output:
            continue
        record_id = str(item.get("pack_id") or item.get("receipt_id") or output)
        descriptor = GeneratedOutputDescriptor.create(
            output, owner="repository-pack", provider="repository-pack", category="repository-pack",
            producer_id=record_id, lifecycle="rebuildable",
            rebuild_cost="high", scope=CleanupScope.DURABLE_HISTORY,
            provenance={"pack_id": item.get("pack_id"), "retention": item.get("retention", "policy")},
            managed=True, published=False,
        )
        expected_sha = str(item.get("sha256") or "")
        output_path = Path(output).expanduser()
        if output_path.is_file() and expected_sha:
            current_sha = _sha256(output_path)
            if current_sha != expected_sha:
                from .cleanup_lifecycle import CleanupReference
                protective_refs.append(CleanupReference(
                    reference_id=f"repository-pack-drift:{record_id}",
                    source="repository-pack",
                    resource_id=descriptor.resource_id,
                    path=str(output_path),
                    state="byte-drift",
                    protective=True,
                    reason="repository pack bytes differ from the registered sha256; manual reconciliation is required before GC",
                ))
        candidates.append((item, descriptor))
    if not candidates:
        return {"status": "no-op", "removed": [], "protected": [], "remaining": len(list_repository_packs(root))}
    ownership = build_cleanup_ownership_view(
        root,
        [descriptor for _, descriptor in candidates],
        policy_generation=f"repository-pack-gc-v1:{float(older_than_days):.6f}",
        references=protective_refs,
    )
    plan = compile_cleanup_plan(ownership, scope=CleanupScope.DURABLE_HISTORY, destination="local")
    receipt = apply_cleanup_plan(plan, ownership=ownership, dry_run=dry_run, confirm=True)
    if not dry_run:
        persist_cleanup_receipt(root, receipt)
    result_by_resource = {item.resource_id: item for item in receipt.results}
    removed: list[str] = []
    protected: list[str] = []
    if not dry_run:
        new_items: list[dict[str, Any]] = []
        for raw in payload.get("items", []):
            item = dict(raw) if isinstance(raw, Mapping) else {}
            match = next(((record, descriptor) for record, descriptor in candidates if record.get("pack_id") == item.get("pack_id") and record.get("output") == item.get("output")), None)
            if match is not None:
                _record, descriptor = match
                result = result_by_resource.get(descriptor.resource_id)
                if result is not None and result.status == "applied":
                    item["state"] = "tombstoned"
                    item["tombstoned_at"] = _now()
                    item["cleanup_receipt_id"] = receipt.receipt_id
                    removed.append(str(item.get("pack_id") or item.get("output") or ""))
                elif result is not None:
                    protected.append(str(item.get("pack_id") or item.get("output") or ""))
            new_items.append(item)
        payload["items"] = new_items
        _save(root, payload)
    else:
        for record, descriptor in candidates:
            result = result_by_resource.get(descriptor.resource_id)
            (removed if result is not None and result.status == "planned" else protected).append(str(record.get("pack_id") or record.get("output") or ""))
    return {
        "status": receipt.status,
        "cleanup_receipt_id": receipt.receipt_id,
        "removed": removed,
        "protected": protected,
        "remaining": len(list_repository_packs(root)) if not dry_run else len(list_repository_packs(root)),
    }


__all__ = [
    "list_repository_packs",
    "load_repository_pack_registry",
    "prune_repository_packs",
    "register_repository_pack",
    "repository_pack_registry_path",
    "repository_pack_status",
    "set_repository_pack_pin",
]
