from __future__ import annotations

"""Durable LIFE ownership for remote/provider resources.

DIST discovers provider staging/session/cursor resources, but LIFE owns whether
those resources remain protected or can be released.  This registry is a
projection of concrete provider descriptors into repository-private state; it
never stores secret values and never becomes an alternate execution/history
store.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

from .cleanup_lifecycle import CleanupReference, GeneratedOutputDescriptor


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _git_private_root(repo_root: str | Path) -> Path:
    root = Path(repo_root).expanduser().resolve(strict=False)
    dotgit = root / ".git"
    if dotgit.is_dir():
        return dotgit / "devtool"
    if dotgit.is_file():
        try:
            line = dotgit.read_text(encoding="utf-8", errors="replace").strip()
            if line.lower().startswith("gitdir:"):
                gitdir = Path(line.split(":", 1)[1].strip()).expanduser()
                if not gitdir.is_absolute():
                    gitdir = (root / gitdir).resolve(strict=False)
                return gitdir / "devtool"
        except OSError:
            pass
    return root / ".devtool" / "private"


def provider_resource_registry_path(repo_root: str | Path) -> Path:
    return _git_private_root(repo_root) / "provider-resources" / "registry.json"


def _load(repo_root: str | Path, *, strict: bool = False) -> dict[str, Any]:
    path = provider_resource_registry_path(repo_root)
    if not path.exists():
        return {"schema_version": 1, "resources": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        if strict:
            raise ValueError(f"provider resource registry is unreadable: {exc}") from exc
        return {"schema_version": 1, "resources": {}}
    if not isinstance(payload, dict) or not isinstance(payload.get("resources", {}), dict):
        if strict:
            raise ValueError("provider resource registry has invalid schema")
        return {"schema_version": 1, "resources": {}}
    return payload


def _save(repo_root: str | Path, payload: Mapping[str, Any]) -> Path:
    path = provider_resource_registry_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(dict(payload), indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)
    return path


def register_provider_resources(
    repo_root: str | Path,
    descriptors: Iterable[GeneratedOutputDescriptor],
) -> tuple[str, ...]:
    payload = _load(repo_root, strict=True)
    resources = dict(payload.get("resources") or {})
    ids: list[str] = []
    now = _utcnow()
    for descriptor in descriptors:
        item = descriptor.as_dict()
        resource_id = descriptor.resource_id
        existing = resources.get(resource_id)
        if isinstance(existing, dict):
            previous = dict(existing.get("descriptor") or {})
            # Re-registration is idempotent only for the exact same resource.
            if previous and previous != item:
                raise ValueError(f"provider resource identity collision: {resource_id}")
        resources[resource_id] = {
            "resource_id": resource_id,
            "descriptor": item,
            "state": "active",
            "registered_at": str((existing or {}).get("registered_at") or now) if isinstance(existing, dict) else now,
            "updated_at": now,
            "released_at": "",
            "release_reason": "",
        }
        ids.append(resource_id)
    _save(repo_root, {"schema_version": 1, "resources": resources})
    return tuple(ids)


def release_provider_resources(
    repo_root: str | Path,
    *,
    delegation_id: str = "",
    run_id: str = "",
    session: str = "",
    reason: str = "reconciled",
) -> tuple[str, ...]:
    payload = _load(repo_root, strict=True)
    resources = dict(payload.get("resources") or {})
    released: list[str] = []
    now = _utcnow()
    for resource_id, raw in list(resources.items()):
        if not isinstance(raw, dict) or str(raw.get("state") or "active") == "released":
            continue
        descriptor = dict(raw.get("descriptor") or {})
        provenance = dict(descriptor.get("provenance") or {})
        if delegation_id and str(provenance.get("delegation_id") or "") != delegation_id:
            continue
        if run_id and str(provenance.get("run_id") or "") != run_id:
            continue
        if session and str(provenance.get("session") or "") != session:
            continue
        if not any((delegation_id, run_id, session)):
            continue
        updated = dict(raw)
        updated.update({"state": "released", "updated_at": now, "released_at": now, "release_reason": str(reason or "released")})
        resources[resource_id] = updated
        released.append(resource_id)
    if released:
        _save(repo_root, {"schema_version": 1, "resources": resources})
    return tuple(released)


def provider_resource_projection(
    repo_root: str | Path,
    *,
    strict: bool = False,
) -> tuple[list[GeneratedOutputDescriptor], list[CleanupReference]]:
    payload = _load(repo_root, strict=strict)
    descriptors: list[GeneratedOutputDescriptor] = []
    references: list[CleanupReference] = []
    for resource_id, raw in sorted(dict(payload.get("resources") or {}).items()):
        if not isinstance(raw, dict):
            continue
        descriptor_raw = dict(raw.get("descriptor") or {})
        try:
            scope = descriptor_raw.get("scope")
            if isinstance(scope, str):
                from .cleanup_lifecycle import CleanupScope
                descriptor_raw["scope"] = CleanupScope(scope)
            descriptor = GeneratedOutputDescriptor(**descriptor_raw)
        except (TypeError, ValueError) as exc:
            if strict:
                raise ValueError(f"invalid provider resource descriptor {resource_id}: {exc}") from exc
            continue
        descriptors.append(descriptor)
        state = str(raw.get("state") or "active")
        references.append(CleanupReference(
            reference_id=f"provider-resource:{resource_id}",
            source="provider-resource-registry",
            resource_id=descriptor.resource_id,
            path="",
            state=state,
            protective=state != "released",
            reason=(
                "provider resource remains protected until import/reconciliation/reference release"
                if state != "released" else "provider resource ownership released after reconciliation"
            ),
            destination=descriptor.destination,
        ))
    return descriptors, references


__all__ = [
    "provider_resource_registry_path",
    "register_provider_resources",
    "release_provider_resources",
    "provider_resource_projection",
]
