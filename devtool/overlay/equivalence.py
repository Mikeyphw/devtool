from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
from typing import Any

from ..artifact_base_contract import _base_path_identity, _current_path_identity
from ..artifacts import artifact_inventory_entries, list_archive_members, read_artifact_manifest, resolve_artifact_path


@dataclass(frozen=True, slots=True)
class PathPatchState:
    path: str
    action: str
    state: str
    base_kind: str
    current_kind: str
    incoming_kind: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PatchEquivalenceResult:
    artifact: str
    status: str
    overlay_patch_id: str
    current_patch_id: str
    base_commit: str
    base_tree: str
    paths: tuple[PathPatchState, ...]
    messages: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["paths"] = [item.to_dict() for item in self.paths]
        return data


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _run_git(repo: Path, *args: str, input_bytes: bytes | None = None, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip() or proc.stdout.decode("utf-8", "replace").strip()
        raise RuntimeError(detail or f"git exited {proc.returncode}")
    return proc


def _archive_payload(artifact: Path) -> tuple[dict[str, bytes], dict[str, int | None]]:
    payload: dict[str, bytes] = {}
    modes: dict[str, int | None] = {}
    for member in list_archive_members(artifact, include_data=True):
        if member.is_dir or member.data is None:
            continue
        payload[member.name] = member.data
        modes[member.name] = member.mode
    return payload, modes


def _incoming_identity(repo_root: Path, base_tree: str, entry: dict[str, Any], data: bytes | None, mode_hint: int | None) -> tuple[str, str, str | None]:
    path = str(entry.get("path") or "")
    action = str(entry.get("action") or "").strip().lower()
    if action == "delete":
        return ("missing", "000000", None)
    if data is None:
        raise RuntimeError(f"Inventory payload missing for {path}")
    base = _base_path_identity(repo_root, base_tree, path)
    executable = bool(mode_hint is not None and (mode_hint & 0o111))
    if action == "modify" and base.kind == "file":
        mode = "100755" if executable else base.mode
    else:
        mode = "100755" if executable else "100644"
    return ("file", mode, _sha(data))


def _identity_tuple(identity: Any) -> tuple[str, str, str | None]:
    return (str(identity.kind), str(identity.mode), identity.sha256)


def _state_bytes(repo_root: Path, base_tree: str, rel: str, *, source: str, payload: bytes | None = None) -> tuple[str, str, bytes | None]:
    if source == "base":
        ident = _base_path_identity(repo_root, base_tree, rel)
        if ident.kind == "missing":
            return ("missing", ident.mode, None)
        if ident.kind not in {"file", "symlink"}:
            return (ident.kind, ident.mode, None)
        proc = _run_git(repo_root, "show", f"{base_tree}:{rel}", check=False)
        if proc.returncode != 0:
            return (ident.kind, ident.mode, None)
        return (ident.kind, ident.mode, proc.stdout)
    if source == "current":
        ident = _current_path_identity(repo_root, rel)
        target = repo_root / rel
        if ident.kind == "file":
            return (ident.kind, ident.mode, target.read_bytes())
        if ident.kind == "symlink":
            return (ident.kind, ident.mode, os.readlink(target).encode("utf-8", "surrogateescape"))
        return (ident.kind, ident.mode, None)
    raise RuntimeError(f"Unsupported state source: {source}")


def _materialize_state(root: Path, rel: str, kind: str, mode: str, data: bytes | None) -> None:
    target = root / rel
    if target.exists() or target.is_symlink():
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink()
    if kind == "missing":
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if kind == "symlink":
        os.symlink((data or b"").decode("utf-8", "surrogateescape"), target)
        return
    if kind != "file":
        raise RuntimeError(f"Cannot materialize patch-id state {kind} for {rel}")
    target.write_bytes(data or b"")
    target.chmod(0o755 if mode == "100755" else 0o644)


def _commit_all(repo: Path, message: str) -> str:
    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-qm", message, "--allow-empty")
    return _run_git(repo, "rev-parse", "HEAD").stdout.decode().strip()


def _patch_id(diff_bytes: bytes) -> str:
    if not diff_bytes.strip():
        return ""
    proc = subprocess.run(["git", "patch-id", "--stable"], input=diff_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        return ""
    first = proc.stdout.decode("utf-8", "replace").strip().split()
    return first[0] if first else ""


def _compute_patch_ids(repo_root: Path, base_tree: str, entries: list[dict[str, Any]], payload: dict[str, bytes], modes: dict[str, int | None]) -> tuple[str, str]:
    with tempfile.TemporaryDirectory(prefix="devtool-patch-equivalence-") as tmp:
        repo = Path(tmp) / "repo"
        repo.mkdir()
        _run_git(repo, "init", "-q")
        _run_git(repo, "config", "user.email", "devtool-equivalence@example.invalid")
        _run_git(repo, "config", "user.name", "Devtool Equivalence")
        for entry in entries:
            rel = str(entry.get("path") or "")
            kind, mode, data = _state_bytes(repo_root, base_tree, rel, source="base")
            _materialize_state(repo, rel, kind, mode, data)
        base_commit = _commit_all(repo, "base")
        for entry in entries:
            rel = str(entry.get("path") or "")
            kind, mode, digest = _incoming_identity(repo_root, base_tree, entry, payload.get(rel), modes.get(rel))
            _materialize_state(repo, rel, kind, mode, payload.get(rel) if kind == "file" else None)
        incoming_commit = _commit_all(repo, "incoming")
        overlay_diff = _run_git(repo, "diff", "--binary", f"{base_commit}..{incoming_commit}").stdout
        _run_git(repo, "reset", "--hard", "-q", base_commit)
        for entry in entries:
            rel = str(entry.get("path") or "")
            kind, mode, data = _state_bytes(repo_root, base_tree, rel, source="current")
            _materialize_state(repo, rel, kind, mode, data)
        current_commit = _commit_all(repo, "current")
        current_diff = _run_git(repo, "diff", "--binary", f"{base_commit}..{current_commit}").stdout
        return _patch_id(overlay_diff), _patch_id(current_diff)


def inspect_patch_equivalence(repo_root: Path, artifact: str | Path) -> PatchEquivalenceResult:
    repo_root = repo_root.resolve()
    artifact_path = resolve_artifact_path(artifact)
    manifest = read_artifact_manifest(artifact_path)
    mode = str(manifest.get("base_contract_mode") or "").strip().lower()
    base_tree = str(manifest.get("base_tree") or "").strip().lower()
    base_commit = str(manifest.get("base_commit") or "").strip().lower()
    if mode not in {"exact-tree", "touched-files"} or not base_tree:
        return PatchEquivalenceResult(str(artifact_path), "legacy", "", "", base_commit, base_tree, (), ("Artifact has no GIT-FRX-05 base contract; patch equivalence is unavailable.",))
    entries = [dict(item) for item in artifact_inventory_entries(manifest)]
    payload, modes = _archive_payload(artifact_path)
    path_states: list[PathPatchState] = []
    divergent = False
    for entry in entries:
        rel = str(entry.get("path") or "")
        action = str(entry.get("action") or "").strip().lower()
        base = _base_path_identity(repo_root, base_tree, rel)
        current = _current_path_identity(repo_root, rel)
        incoming = _incoming_identity(repo_root, base_tree, entry, payload.get(rel), modes.get(rel))
        base_tuple = _identity_tuple(base)
        current_tuple = _identity_tuple(current)
        if current_tuple == incoming:
            state = "present"
        elif current_tuple == base_tuple:
            state = "absent"
        else:
            state = "divergent"
            divergent = True
        path_states.append(PathPatchState(rel, action, state, base.kind, current.kind, incoming[0]))
    states = [item.state for item in path_states]
    overlay_patch_id = ""
    current_patch_id = ""
    try:
        overlay_patch_id, current_patch_id = _compute_patch_ids(repo_root, base_tree, entries, payload, modes)
    except Exception:
        pass
    if states and all(state == "present" for state in states):
        status = "fully-present"
    elif states and all(state == "absent" for state in states):
        status = "absent"
    elif not divergent and any(state == "present" for state in states) and any(state == "absent" for state in states):
        status = "partially-present"
    elif overlay_patch_id and current_patch_id and overlay_patch_id == current_patch_id:
        status = "fully-present"
    else:
        status = "ambiguous"
    messages = [f"Patch equivalence: {status}."]
    if overlay_patch_id:
        messages.append(f"overlay_patch_id={overlay_patch_id}")
    if current_patch_id:
        messages.append(f"current_patch_id={current_patch_id}")
    return PatchEquivalenceResult(str(artifact_path), status, overlay_patch_id, current_patch_id, base_commit, base_tree, tuple(path_states), tuple(messages))
