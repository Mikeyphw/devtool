from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from .artifacts import (
    artifact_commit_policy,
    artifact_inventory_entries,
    artifact_solution,
    artifact_target,
    artifact_validation_policy,
    artifact_workspaces,
    diff_artifact,
    is_overlay_bundle,
    is_artifact_metadata_member,
    list_archive_members,
    materialize_overlay_bundle_entries,
    preview_artifact,
    read_artifact_manifest,
    resolve_artifact_path,
    validate_artifact,
    validate_overlay_bundle,
)


def _repository_info(manifest: dict[str, Any]) -> dict[str, Any]:
    repository = manifest.get("repository")
    if isinstance(repository, dict):
        return {str(k): v for k, v in repository.items()}
    legacy = str(manifest.get("repo_name") or manifest.get("repo") or "").strip()
    return {"name": legacy} if legacy else {}


def _bundle_entries(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    raw = manifest.get("overlays") or manifest.get("apply_order") or []
    if not isinstance(raw, list):
        return []
    result: list[dict[str, Any]] = []
    for index, value in enumerate(raw, 1):
        if isinstance(value, str):
            result.append({
                "index": index,
                "id": Path(value).stem,
                "label": Path(value).name,
                "path": value,
                "required": True,
                "after": [],
                "requires": [],
            })
            continue
        if not isinstance(value, dict):
            continue
        path = str(value.get("artifact") or value.get("path") or "")
        result.append({
            "index": index,
            "id": str(value.get("id") or Path(path).stem or f"overlay-{index}"),
            "label": str(value.get("label") or value.get("name") or Path(path).name or f"Overlay {index}"),
            "path": path,
            "target": str(value.get("target") or ""),
            "required": bool(value.get("required", True)),
            "after": [str(item) for item in value.get("after", [])] if isinstance(value.get("after"), list) else [],
            "requires": [str(item) for item in value.get("requires", [])] if isinstance(value.get("requires"), list) else [],
        })
    return result


def _clean_inventory(entries: list[dict[str, Any]]) -> list[dict[str, str]]:
    cleaned: list[dict[str, str]] = []
    for entry in entries:
        path = str(entry.get("path") or "").strip()
        if not path:
            continue
        cleaned.append({
            "path": path,
            "action": str(entry.get("action") or entry.get("kind") or "payload").strip().lower() or "payload",
        })
    return cleaned


def _bundle_inventory(path: Path) -> list[dict[str, str]]:
    inventory: list[dict[str, str]] = []
    with tempfile.TemporaryDirectory(prefix="devtool-inspect-bundle-") as tmp:
        _manifest, entries = materialize_overlay_bundle_entries(path, tmp)
        for entry in entries:
            nested_manifest = read_artifact_manifest(entry.path)
            nested = artifact_inventory_entries(nested_manifest)
            if not nested:
                nested = [
                    {"path": member.name, "action": "payload"}
                    for member in list_archive_members(entry.path, include_data=False)
                    if not member.is_dir and not is_artifact_metadata_member(member.name)
                ]
            inventory.extend(_clean_inventory(nested))
    return inventory


def inspect_artifact(
    artifact: str | Path,
    *,
    repo_root: str | Path | None = None,
    include_diff: bool = False,
    diff_context: int = 3,
    max_diff_lines: int = 4000,
) -> dict[str, Any]:
    path = resolve_artifact_path(artifact)
    manifest = read_artifact_manifest(path)
    bundle = is_overlay_bundle(path)
    repository = _repository_info(manifest)
    temporary_root: tempfile.TemporaryDirectory[str] | None = None
    if repo_root is not None:
        repo = Path(repo_root).expanduser().resolve()
    else:
        temporary_root = tempfile.TemporaryDirectory(prefix="devtool-inspect-")
        expected_name = str(repository.get("name") or "artifact-repository").strip() or "artifact-repository"
        repo = Path(temporary_root.name) / Path(expected_name).name
        repo.mkdir(parents=True, exist_ok=True)
    try:
        validation = validate_overlay_bundle(repo, path) if bundle else validate_artifact(
            repo,
            path,
            check_source_state=repo_root is not None,
        )
    finally:
        if temporary_root is not None:
            temporary_root.cleanup()
    preview: dict[str, Any] = {}
    diff_text = ""
    if repo_root is not None:
        preview = preview_artifact(repo, path, snapshot=False)
        if include_diff:
            diff_text = diff_artifact(repo, path, snapshot=False, context=diff_context, max_lines=max_diff_lines)

    inventory = _bundle_inventory(path) if bundle else _clean_inventory(artifact_inventory_entries(manifest))
    if not inventory and preview:
        inventory = _clean_inventory([
            {
                "path": str(change.get("path") or ""),
                "action": str(change.get("kind") or "unknown"),
            }
            for change in preview.get("changes", [])
            if isinstance(change, dict)
        ])
    if not inventory:
        declared = manifest.get("files")
        if isinstance(declared, list):
            inventory = [{"path": str(item), "action": "payload"} for item in declared if str(item).strip()]
        else:
            inventory = [
                {"path": member.name, "action": "payload", "size": member.size}
                for member in list_archive_members(path, include_data=False)
                if not member.is_dir and not is_artifact_metadata_member(member.name)
            ]
    inventory = _clean_inventory(inventory)
    commit_enabled, commit_message, commit_strategy = artifact_commit_policy(manifest)
    validation_policy = artifact_validation_policy(manifest)
    counts: dict[str, int] = {"add": 0, "modify": 0, "delete": 0, "other": 0}
    for entry in inventory:
        action = str(entry.get("action") or entry.get("kind") or "other").lower()
        key = action if action in counts else "other"
        counts[key] += 1

    return {
        "artifact": str(path),
        "filename": path.name,
        "size": path.stat().st_size,
        "kind": "overlay_bundle" if bundle else str(manifest.get("type") or "overlay"),
        "schema_version": int(manifest.get("schema_version", manifest.get("version", 1)) or 1),
        "name": str(manifest.get("name") or path.stem),
        "description": str(manifest.get("description") or ""),
        "repository": repository,
        "target": artifact_target(manifest),
        "solution": artifact_solution(manifest),
        "workspaces": artifact_workspaces(manifest),
        "inventory": inventory,
        "counts": counts,
        "payload_files": len(inventory),
        "overlays": _bundle_entries(manifest),
        "validation": {
            "ok": validation.ok,
            "messages": list(validation.messages),
            "policy": validation_policy,
        },
        "commit": {
            "enabled": commit_enabled,
            "message": commit_message,
            "strategy": commit_strategy,
        },
        "preview": preview,
        "diff": diff_text,
        "manifest": manifest,
        "manifest_json": json.dumps(manifest, indent=2, ensure_ascii=False),
    }
