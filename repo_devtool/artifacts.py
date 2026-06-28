from __future__ import annotations

import hashlib
import json
import os
import tarfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .console_utils import resolve_cli_path

MANIFEST_NAMES = {".devtool-artifact.json", "artifact.devtool.json", "devtool-artifact.json"}
DELETE_MANIFEST_NAME = ".devtool-delete"
ARTIFACT_METADATA_NAMES = MANIFEST_NAMES | {DELETE_MANIFEST_NAME}
HISTORY_PATH = Path(".devtool") / "artifacts.json"


def is_artifact_metadata_member(name: str) -> bool:
    """Return True for archive-side devtool metadata that must not be copied into repos."""
    return Path(name).name in ARTIFACT_METADATA_NAMES


@dataclass(slots=True)
class ArchiveMember:
    name: str
    is_dir: bool = False
    data: bytes | None = None


@dataclass(slots=True)
class ArtifactValidation:
    ok: bool
    messages: list[str]
    manifest: dict[str, Any]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_rel(name: str) -> Path:
    rel = Path(name)
    if rel.is_absolute() or ".." in rel.parts:
        raise RuntimeError(f"Unsafe archive path: {name}")
    return rel


def list_archive_members(artifact: str | Path, *, include_data: bool = False) -> list[ArchiveMember]:
    path = resolve_cli_path(str(artifact))
    members: list[ArchiveMember] = []
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                _safe_rel(info.filename)
                data = None if info.is_dir() or not include_data else zf.read(info.filename)
                members.append(ArchiveMember(info.filename.rstrip("/"), info.is_dir(), data))
        return members
    if path.suffix == ".tar" or path.name.endswith((".tar.gz", ".tgz")):
        mode = "r:gz" if path.name.endswith((".tar.gz", ".tgz")) else "r"
        with tarfile.open(path, mode) as tf:
            for member in tf.getmembers():
                _safe_rel(member.name)
                if member.islnk() or member.issym() or member.isdev():
                    raise RuntimeError(f"Unsafe tar member: {member.name}")
                data = None
                if member.isfile() and include_data:
                    fh = tf.extractfile(member)
                    if fh is not None:
                        with fh:
                            data = fh.read()
                members.append(ArchiveMember(member.name.rstrip("/"), member.isdir(), data))
        return members
    raise RuntimeError(f"Unsupported artifact type: {path}")


def read_artifact_manifest(artifact: str | Path) -> dict[str, Any]:
    artifact_path = resolve_cli_path(str(artifact))
    sidecars = [
        artifact_path.with_name(artifact_path.name + ".devtool.json"),
        artifact_path.with_name(artifact_path.name + ".manifest.json"),
    ]
    for candidate in sidecars:
        if not candidate.exists():
            continue
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    try:
        for member in list_archive_members(artifact_path, include_data=True):
            if Path(member.name).name not in MANIFEST_NAMES or member.data is None:
                continue
            data = json.loads(member.data.decode("utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}
    return {}


def validate_artifact(repo_root: Path, artifact: str | Path, *, artifact_type: str = "overlay") -> ArtifactValidation:
    manifest = read_artifact_manifest(artifact)
    messages: list[str] = []
    ok = True

    try:
        members = list_archive_members(artifact, include_data=False)
    except Exception as exc:
        return ArtifactValidation(False, [str(exc)], manifest)

    if not members:
        return ArtifactValidation(False, ["Artifact archive is empty."], manifest)

    expected_repo = str(manifest.get("repo") or manifest.get("repo_name") or manifest.get("expected_repo") or "").strip()
    if expected_repo and expected_repo not in {repo_root.name, str(repo_root)}:
        ok = False
        messages.append(f"Manifest expects repo {expected_repo!r}, current repo is {repo_root.name!r}.")

    expected_type = str(manifest.get("type") or manifest.get("artifact_type") or "").strip().lower()
    if expected_type and expected_type not in {artifact_type, "patch" if artifact_type == "overlay" else artifact_type}:
        ok = False
        messages.append(f"Manifest type is {expected_type!r}, but command expects {artifact_type!r}.")

    expected_solution = str(manifest.get("expected_solution") or manifest.get("solution") or "").strip()
    if expected_solution:
        solution_path = repo_root / expected_solution
        names = {m.name for m in members}
        if not solution_path.exists() and expected_solution not in names:
            messages.append(f"Manifest references solution {expected_solution!r}; it is not present before apply and is not included in the artifact.")

    declared_files = manifest.get("files")
    if isinstance(declared_files, list):
        member_files = {m.name for m in members if not m.is_dir}
        missing = [str(item) for item in declared_files if str(item) not in member_files]
        if missing:
            ok = False
            messages.append("Manifest lists files missing from archive: " + ", ".join(missing[:10]))

    if not messages:
        messages.append("Artifact validation passed.")
    return ArtifactValidation(ok, messages, manifest)



def read_delete_manifest(artifact: str | Path) -> list[str]:
    """Read overlay deletion entries from .devtool-delete without treating it as payload."""
    entries: list[str] = []
    try:
        for member in list_archive_members(artifact, include_data=True):
            if Path(member.name).name != DELETE_MANIFEST_NAME or member.data is None:
                continue
            text = member.data.decode("utf-8").strip()
            if not text:
                continue
            # Allow either a JSON string list or simple newline-delimited paths.
            if text.startswith("["):
                try:
                    data = json.loads(text)
                    if isinstance(data, list):
                        entries.extend(str(item).strip() for item in data if str(item).strip())
                        continue
                except Exception:
                    pass
            for raw_line in text.splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                entries.append(line)
    except Exception:
        return []
    return entries

def _bytes_sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def preview_artifact(repo_root: Path, artifact: str | Path, *, snapshot: bool = False) -> dict[str, Any]:
    members = list_archive_members(artifact, include_data=True)
    file_members = [m for m in members if not m.is_dir]
    incoming_files = {Path(m.name): m for m in file_members if not is_artifact_metadata_member(m.name)}
    changes: list[dict[str, str]] = []

    if snapshot:
        existing_files = {
            p.relative_to(repo_root)
            for p in repo_root.rglob("*")
            if p.is_file() and ".git" not in p.parts and HISTORY_PATH.parts[0] not in p.parts
        }
        incoming_paths = set(incoming_files)
        for rel in sorted(existing_files - incoming_paths):
            changes.append({"kind": "delete", "path": str(rel)})
    else:
        for delete_path in read_delete_manifest(artifact):
            changes.append({"kind": "delete", "path": delete_path})

    for rel, member in sorted(incoming_files.items(), key=lambda item: str(item[0])):
        target = repo_root / rel
        if not target.exists():
            changes.append({"kind": "add", "path": str(rel)})
            continue
        if target.is_dir():
            changes.append({"kind": "replace-dir", "path": str(rel)})
            continue
        current_hash = sha256_file(target)
        incoming_hash = _bytes_sha(member.data or b"")
        if current_hash != incoming_hash:
            changes.append({"kind": "modify", "path": str(rel)})
        else:
            changes.append({"kind": "unchanged", "path": str(rel)})

    return {
        "artifact": str(resolve_cli_path(str(artifact))),
        "snapshot": snapshot,
        "files": len(incoming_files),
        "changes": changes,
        "counts": {
            kind: sum(1 for change in changes if change["kind"] == kind)
            for kind in sorted({change["kind"] for change in changes})
        },
    }


def format_preview(preview: dict[str, Any], *, limit: int = 200) -> str:
    symbols = {
        "add": "+",
        "modify": "~",
        "delete": "-",
        "replace-dir": "!",
        "unchanged": "=",
    }
    lines = [f"Artifact preview: {preview.get('artifact', '-')}"]
    counts = preview.get("counts", {})
    if counts:
        lines.append("Changes: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    changes = list(preview.get("changes", []))
    for change in changes[:limit]:
        kind = str(change.get("kind", ""))
        lines.append(f"{symbols.get(kind, '?')} {kind:11} {change.get('path', '-')}")
    if len(changes) > limit:
        lines.append(f"... {len(changes) - limit} more change(s) omitted")
    return "\n".join(lines)


def history_file(repo_root: Path) -> Path:
    return repo_root / HISTORY_PATH


def load_history(repo_root: Path) -> list[dict[str, Any]]:
    path = history_file(repo_root)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            items = data.get("artifacts", [])
        else:
            items = data
        return items if isinstance(items, list) else []
    except Exception:
        return []


def save_history(repo_root: Path, entries: list[dict[str, Any]]) -> Path:
    path = history_file(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 1, "artifacts": entries}, indent=2) + "\n", encoding="utf-8")
    return path


def append_history(repo_root: Path, entry: dict[str, Any]) -> Path:
    entries = load_history(repo_root)
    entries.append(entry)
    return save_history(repo_root, entries)


def make_history_entry(
    repo_root: Path,
    artifact: str | Path,
    *,
    artifact_type: str,
    status: str,
    validation_status: str = "not-run",
    commit_message: str = "",
    commit_hash: str = "",
    preview: dict[str, Any] | None = None,
    manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = resolve_cli_path(str(artifact))
    digest = sha256_file(path) if path.exists() else ""
    return {
        "applied_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "artifact": str(path),
        "artifact_name": path.name,
        "type": artifact_type,
        "sha256": digest,
        "status": status,
        "validation_status": validation_status,
        "commit_message": commit_message,
        "commit_hash": commit_hash,
        "file_count": int((preview or {}).get("files", 0)),
        "change_counts": (preview or {}).get("counts", {}),
        "manifest": manifest or {},
    }


def latest_commit_hash(repo_root: Path) -> str:
    import subprocess

    proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_root, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    return proc.stdout.strip() if proc.returncode == 0 else ""
