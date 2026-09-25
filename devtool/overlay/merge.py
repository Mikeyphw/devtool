from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any

from ..artifact_base_contract import build_base_contract, path_source_sha256, _base_path_identity, _current_path_identity
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
from .equivalence import inspect_patch_equivalence


@dataclass(frozen=True, slots=True)
class RebaseResult:
    source: str
    output: str
    changed_entries: int
    actions_changed: int
    validation_ok: bool
    equivalence_before: str = ""
    source_patch_id: str = ""
    target_patch_id: str = ""
    conflict_artifact: str = ""
    noop: bool = False
    messages: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class OverlayRebaseConflict(RuntimeError):
    def __init__(self, message: str, *, conflict_artifact: str = "") -> None:
        super().__init__(message)
        self.conflict_artifact = conflict_artifact


def _default_output(path: Path) -> Path:
    name = path.name
    for suffix in (".tar.zst", ".tar.xz", ".tar.bz2", ".tar.gz", ".tzst", ".txz", ".tbz2", ".tgz", ".tar", ".zip"):
        if name.lower().endswith(suffix):
            return path.with_name(name[:-len(suffix)] + ".rebased" + suffix)
    return path.with_name(name + ".rebased.zip")


def _conflict_output(source: Path, output: str | Path | None) -> Path:
    if output:
        p = Path(output).expanduser()
        if not p.is_absolute():
            p = Path.cwd() / p
        name = p.name
        if ".rebased" in name:
            name = name.replace(".rebased", ".conflicts", 1)
        else:
            name = name + ".conflicts.zip"
        return p.with_name(name)
    name = source.name
    for suffix in (".tar.zst", ".tar.xz", ".tar.bz2", ".tar.gz", ".tzst", ".txz", ".tbz2", ".tgz", ".tar", ".zip"):
        if name.lower().endswith(suffix):
            return source.with_name(name[:-len(suffix)] + ".conflicts.zip")
    return source.with_name(name + ".conflicts.zip")


def _git_show(repo: Path, treeish: str, rel: str) -> bytes | None:
    proc = subprocess.run(["git", "-C", str(repo), "show", f"{treeish}:{rel}"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return proc.stdout if proc.returncode == 0 else None


def _current_bytes(repo: Path, rel: str) -> bytes | None:
    target = repo / rel
    ident = _current_path_identity(repo, rel)
    if ident.kind == "file":
        return target.read_bytes()
    if ident.kind == "symlink":
        return os.readlink(target).encode("utf-8", "surrogateescape")
    return None


def _merge_text(current: bytes, base: bytes, incoming: bytes) -> tuple[int, bytes]:
    with tempfile.TemporaryDirectory(prefix="devtool-three-way-") as tmp:
        root = Path(tmp)
        current_p = root / "current"
        base_p = root / "base"
        incoming_p = root / "incoming"
        current_p.write_bytes(current)
        base_p.write_bytes(base)
        incoming_p.write_bytes(incoming)
        proc = subprocess.run(
            ["git", "merge-file", "-p", str(current_p), str(base_p), str(incoming_p)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if proc.returncode not in {0, 1}:
            raise RuntimeError(proc.stderr.decode("utf-8", "replace").strip() or "git merge-file failed")
        return proc.returncode, proc.stdout


def _write_conflict_artifact(
    repo_root: Path,
    source: Path,
    manifest: dict[str, Any],
    conflicts: list[dict[str, Any]],
    *,
    output: str | Path | None,
) -> Path:
    destination = _conflict_output(source, output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    entries: dict[str, bytes] = {}
    public_conflicts: list[dict[str, Any]] = []
    base_tree = str(manifest.get("base_tree") or "")
    for index, conflict in enumerate(conflicts, 1):
        rel = str(conflict["path"])
        safe = rel.replace("/", "__")
        base = _git_show(repo_root, base_tree, rel)
        current = _current_bytes(repo_root, rel)
        incoming = conflict.get("incoming")
        merged = conflict.get("merged")
        prefix = f".devtool-conflicts/{index:03d}-{safe}"
        if base is not None:
            entries[prefix + ".base"] = base
        if current is not None:
            entries[prefix + ".current"] = current
        if isinstance(incoming, bytes):
            entries[prefix + ".incoming"] = incoming
        if isinstance(merged, bytes):
            entries[prefix + ".merge"] = merged
        public_conflicts.append({k: v for k, v in conflict.items() if k not in {"incoming", "merged"}})
    evidence = {
        "schema_version": 1,
        "type": "devtool-overlay-rebase-conflicts",
        "source_artifact": str(source),
        "source_artifact_sha256": sha256_file(source),
        "base_commit": str(manifest.get("base_commit") or ""),
        "base_tree": base_tree,
        "conflicts": public_conflicts,
    }
    entries[".devtool-conflicts.json"] = (json.dumps(evidence, indent=2, ensure_ascii=False) + "\n").encode()
    return write_archive_entries(destination, entries)


def _legacy_rebase(repo_root: Path, source: Path, manifest: dict[str, Any], payload: dict[str, bytes], manifest_member: str, *, output: str | Path | None) -> RebaseResult:
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
            if target.exists() or target.is_symlink():
                if not target.is_file() and not target.is_symlink():
                    raise RuntimeError(f"Cannot rebase add over non-file target: {path}")
                entry["action"] = "modify"
                entry["source_sha256"] = path_source_sha256(target)
                actions_changed += 1
            else:
                entry.pop("source_sha256", None)
        elif action == "modify":
            if target.is_file() or target.is_symlink():
                entry["source_sha256"] = path_source_sha256(target)
            elif not (target.exists() or target.is_symlink()):
                entry["action"] = "add"
                entry.pop("source_sha256", None)
                actions_changed += 1
            else:
                raise RuntimeError(f"Cannot rebase modify over non-file target: {path}")
        elif action == "delete":
            if not target.is_file() and not target.is_symlink():
                raise RuntimeError(f"Cannot rebase delete because source file is missing or not a file: {path}")
            entry["source_sha256"] = path_source_sha256(target)
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
    inventory = dict(manifest.get("inventory") or {})
    inventory["entries"] = new_entries
    manifest["inventory"] = inventory
    payload[manifest_member] = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode()
    destination = Path(output).expanduser() if output else _default_output(source)
    if not destination.is_absolute():
        destination = Path.cwd() / destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    created = write_archive_entries(destination, payload)
    validation = validate_artifact(repo_root, created, artifact_type="overlay", check_source_state=True)
    if not validation.ok:
        created.unlink(missing_ok=True)
        raise RuntimeError("Rebased overlay failed validation: " + "; ".join(validation.messages))
    return RebaseResult(str(source), str(created), changed, actions_changed, True, messages=tuple(validation.messages))


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
    modes: dict[str, int | None] = {}
    for member in members:
        if member.is_dir or member.data is None:
            continue
        if Path(member.name).name in MANIFEST_NAMES:
            if not manifest_member:
                manifest_member = member.name
            continue
        payload[member.name] = member.data
        modes[member.name] = member.mode
    if not manifest_member:
        manifest_member = resolution.source if resolution.source in MANIFEST_NAMES else ".devtool-artifact.json"

    base_mode = str(manifest.get("base_contract_mode") or "").strip().lower()
    if base_mode not in {"exact-tree", "touched-files"}:
        return _legacy_rebase(repo_root, source, manifest, payload, manifest_member, output=output)

    equivalence = inspect_patch_equivalence(repo_root, source)
    if equivalence.status == "fully-present":
        return RebaseResult(
            str(source), "", 0, 0, True,
            equivalence_before=equivalence.status,
            source_patch_id=equivalence.overlay_patch_id,
            target_patch_id=equivalence.current_patch_id,
            noop=True,
            messages=("Overlay patch is already fully present; no rebased artifact is required.",),
        )

    base_tree = str(manifest.get("base_tree") or "")
    conflicts: list[dict[str, Any]] = []
    new_entries: list[dict[str, Any]] = []
    new_payload: dict[str, bytes] = {}
    actions_changed = 0
    changed = 0
    state_by_path = {item.path: item.state for item in equivalence.paths}
    for raw in artifact_inventory_entries(manifest):
        entry = dict(raw)
        rel = str(entry.get("path") or "")
        action = str(entry.get("action") or "").strip().lower()
        state = state_by_path.get(rel, "divergent")
        if state == "present":
            changed += 1
            continue
        current = _current_path_identity(repo_root, rel)
        base = _base_path_identity(repo_root, base_tree, rel)
        incoming = payload.get(rel)
        post_bytes = incoming
        post_missing = action == "delete"
        if state == "divergent":
            if action == "modify" and base.kind == current.kind == "file" and current.mode == base.mode and incoming is not None:
                base_bytes = _git_show(repo_root, base_tree, rel)
                current_bytes = _current_bytes(repo_root, rel)
                if base_bytes is None or current_bytes is None:
                    conflicts.append({"path": rel, "kind": "missing-merge-input", "detail": "Cannot reconstruct all three merge inputs.", "incoming": incoming})
                    continue
                rc, merged = _merge_text(current_bytes, base_bytes, incoming)
                if rc != 0:
                    conflicts.append({"path": rel, "kind": "content-conflict", "detail": "Three-way merge produced conflicts.", "incoming": incoming, "merged": merged})
                    continue
                post_bytes = merged
            else:
                conflicts.append({
                    "path": rel,
                    "kind": "structural-conflict",
                    "detail": f"Cannot three-way merge action={action} base={base.kind}/{base.mode} current={current.kind}/{current.mode}.",
                    "incoming": incoming,
                })
                continue
        target = repo_root / rel
        before = dict(entry)
        if post_missing:
            if current.kind == "missing":
                changed += 1
                continue
            entry["action"] = "delete"
            entry["source_sha256"] = path_source_sha256(target)
            entry.pop("sha256", None)
            entry.pop("size", None)
        else:
            if post_bytes is None:
                raise RuntimeError(f"Inventory payload missing while rebasing: {rel}")
            if current.kind == "missing":
                entry["action"] = "add"
                entry.pop("source_sha256", None)
            elif current.kind in {"file", "symlink"}:
                entry["action"] = "modify"
                entry["source_sha256"] = path_source_sha256(target)
            else:
                conflicts.append({"path": rel, "kind": "structural-conflict", "detail": f"Current target is {current.kind}; cannot apply file payload.", "incoming": post_bytes})
                continue
            entry["sha256"] = hashlib.sha256(post_bytes).hexdigest()
            entry["size"] = len(post_bytes)
            new_payload[rel] = post_bytes
        if str(entry.get("action")) != action:
            actions_changed += 1
        if entry != before or post_bytes != incoming:
            changed += 1
        new_entries.append(entry)

    if conflicts:
        conflict_path = _write_conflict_artifact(repo_root, source, manifest, conflicts, output=output)
        raise OverlayRebaseConflict(
            f"Three-way overlay rebase has {len(conflicts)} unresolved conflict(s). Conflict evidence: {conflict_path}",
            conflict_artifact=str(conflict_path),
        )
    if not new_entries:
        return RebaseResult(
            str(source), "", changed, actions_changed, True,
            equivalence_before=equivalence.status,
            source_patch_id=equivalence.overlay_patch_id,
            target_patch_id=equivalence.current_patch_id,
            noop=True,
            messages=("All overlay paths are already present after partial-patch analysis.",),
        )

    manifest = dict(manifest)
    inventory = dict(inventory)
    inventory["entries"] = new_entries
    manifest["inventory"] = inventory
    manifest.update(build_base_contract(repo_root, new_entries, mode=base_mode))
    manifest["rebase"] = {
        "schema_version": 1,
        "source_artifact_sha256": sha256_file(source),
        "source_base_commit": str(equivalence.base_commit),
        "source_base_tree": str(equivalence.base_tree),
        "source_patch_id": equivalence.overlay_patch_id,
        "equivalence_before": equivalence.status,
        "target_commit": str(manifest.get("base_commit") or ""),
        "target_tree": str(manifest.get("base_tree") or ""),
    }
    new_payload[manifest_member] = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")

    destination = Path(output).expanduser() if output else _default_output(source)
    if not destination.is_absolute():
        destination = Path.cwd() / destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    created = write_archive_entries(destination, new_payload)
    validation = validate_artifact(repo_root, created, artifact_type="overlay", check_source_state=True)
    if not validation.ok:
        created.unlink(missing_ok=True)
        raise RuntimeError("Rebased overlay failed validation: " + "; ".join(validation.messages))
    rebased_equivalence = inspect_patch_equivalence(repo_root, created)
    return RebaseResult(
        str(source), str(created), changed, actions_changed, True,
        equivalence_before=equivalence.status,
        source_patch_id=equivalence.overlay_patch_id,
        target_patch_id=rebased_equivalence.overlay_patch_id,
        messages=tuple(validation.messages),
    )
