from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from ..artifacts import (
    MANIFEST_NAMES,
    artifact_inventory_entries,
    list_archive_members,
    read_artifact_manifest,
    resolve_artifact_manifest,
    resolve_artifact_path,
    sha256_file,
    validate_artifact,
    write_archive_entries,
)


@dataclass(frozen=True, slots=True)
class RebaseResult:
    source: str
    output: str
    changed_entries: int
    actions_changed: int
    validation_ok: bool
    messages: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _default_output(path: Path) -> Path:
    name = path.name
    for suffix in (".tar.zst", ".tar.xz", ".tar.bz2", ".tar.gz", ".tzst", ".txz", ".tbz2", ".tgz", ".tar", ".zip"):
        if name.lower().endswith(suffix):
            return path.with_name(name[:-len(suffix)] + ".rebased" + suffix)
    return path.with_name(name + ".rebased.zip")


def rebase_overlay(repo_root: Path, artifact: str | Path, *, output: str | Path | None = None) -> RebaseResult:
    repo_root = repo_root.resolve()
    source = resolve_artifact_path(artifact)
    resolution = resolve_artifact_manifest(source)
    if not resolution.valid or not resolution.present:
        raise RuntimeError(resolution.error or "Overlay must contain exactly one artifact manifest before it can be rebased.")
    manifest = read_artifact_manifest(source)
    kind = str(manifest.get("type") or manifest.get("artifact_type") or "overlay").strip().lower()
    if kind not in {"overlay", "patch"}:
        raise RuntimeError("Only single overlay artifacts can be rebased.")
    inventory = manifest.get("inventory") if isinstance(manifest.get("inventory"), dict) else None
    if inventory is None or not artifact_inventory_entries(manifest):
        raise RuntimeError("Overlay rebase requires a schema-v2 inventory.")

    members = list_archive_members(source, include_data=True)
    payload: dict[str, bytes] = {}
    manifest_member = ""
    for member in members:
        if member.is_dir or member.data is None:
            continue
        if Path(member.name).name in MANIFEST_NAMES:
            if not manifest_member:
                manifest_member = member.name
            continue
        payload[member.name] = member.data
    if not manifest_member:
        manifest_member = resolution.source if resolution.source in MANIFEST_NAMES else ".devtool-artifact.json"

    changed = 0
    actions_changed = 0
    new_entries: list[dict[str, Any]] = []
    for raw in artifact_inventory_entries(manifest):
        entry = dict(raw)
        path = str(entry.get("path") or "").strip()
        action = str(entry.get("action") or "").strip().lower()
        target = repo_root / path
        before = dict(entry)
        if action == "add":
            if target.exists():
                if not target.is_file():
                    raise RuntimeError(f"Cannot rebase add over non-file target: {path}")
                entry["action"] = "modify"
                entry["source_sha256"] = sha256_file(target)
                actions_changed += 1
            else:
                entry.pop("source_sha256", None)
        elif action == "modify":
            if target.is_file():
                entry["source_sha256"] = sha256_file(target)
            elif not target.exists():
                entry["action"] = "add"
                entry.pop("source_sha256", None)
                actions_changed += 1
            else:
                raise RuntimeError(f"Cannot rebase modify over non-file target: {path}")
        elif action == "delete":
            if not target.is_file():
                raise RuntimeError(f"Cannot rebase delete because source file is missing or not a file: {path}")
            entry["source_sha256"] = sha256_file(target)
        else:
            raise RuntimeError(f"Unsupported inventory action while rebasing {path!r}: {action!r}")

        effective_action = str(entry.get("action") or action)
        if effective_action != "delete":
            data = payload.get(path)
            if data is None:
                raise RuntimeError(f"Inventory payload missing while rebasing: {path}")
            entry["sha256"] = hashlib.sha256(data).hexdigest()
            entry["size"] = len(data)
        else:
            entry.pop("sha256", None)
            entry.pop("size", None)
        if entry != before:
            changed += 1
        new_entries.append(entry)

    manifest = dict(manifest)
    inventory = dict(inventory)
    inventory["entries"] = new_entries
    manifest["inventory"] = inventory
    payload[manifest_member] = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")

    destination = Path(output).expanduser() if output else _default_output(source)
    if not destination.is_absolute():
        destination = Path.cwd() / destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    created = write_archive_entries(destination, payload)
    validation = validate_artifact(repo_root, created, artifact_type="overlay", check_source_state=True)
    if not validation.ok:
        created.unlink(missing_ok=True)
        raise RuntimeError("Rebased overlay failed validation: " + "; ".join(validation.messages))
    return RebaseResult(
        source=str(source),
        output=str(created),
        changed_entries=changed,
        actions_changed=actions_changed,
        validation_ok=True,
        messages=tuple(validation.messages),
    )
