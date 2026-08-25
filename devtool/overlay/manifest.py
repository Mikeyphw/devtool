from __future__ import annotations

from pathlib import Path
from typing import Any

from ..artifacts import artifact_inventory_entries, read_artifact_manifest


def manifest_summary(artifact: str | Path) -> dict[str, Any]:
    manifest = read_artifact_manifest(artifact)
    apply = manifest.get("apply") if isinstance(manifest.get("apply"), dict) else {}
    commit = apply.get("commit") if isinstance(apply.get("commit"), dict) else {}
    repository = manifest.get("repository") if isinstance(manifest.get("repository"), dict) else {}
    entries = artifact_inventory_entries(manifest)
    return {
        "schema_version": manifest.get("schema_version"),
        "type": manifest.get("type") or manifest.get("artifact_type") or "overlay",
        "name": manifest.get("name") or Path(artifact).name,
        "repository": dict(repository),
        "inventory_entries": len(entries),
        "commit": {
            "enabled": commit.get("enabled") is True,
            "strategy": str(commit.get("strategy") or "single"),
            "message": str(commit.get("message") or ""),
        },
        "conflict_policy": str(apply.get("conflict_policy") or "fail"),
    }
