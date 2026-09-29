from __future__ import annotations

"""GIT-FRX-13 transactional dirty-tree capture and restoration.

The snapshot preserves the user's exact Git index plus the worktree bytes/modes
for every dirty path. It does not use ``git stash`` and never creates user-visible
commits. Restore is path-scoped and fail-closed against HEAD drift and unsafe
paths; callers may explicitly allow a Devtool-created HEAD advance when they
need to reapply the user's dirty state over an isolated transaction result.
"""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import tempfile
from typing import Any, Iterable, Mapping

from .git_state import GitStateError, collect_git_state
from .transaction_worktree import git_status_identity

DIRTY_TREE_SCHEMA_VERSION = 1
_OPERATIONAL_UNTRACKED_PREFIXES = (".devtool/state/", ".devtool/reports/validation/")


class GitDirtyStateError(RuntimeError):
    pass


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            check=False,
            timeout=12,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitDirtyStateError(f"Unable to execute git {' '.join(args)}: {exc}") from exc


def _require(proc: subprocess.CompletedProcess[str], context: str) -> str:
    if proc.returncode == 0:
        return (proc.stdout or "").strip()
    detail = (proc.stderr or proc.stdout or "git command failed").strip()
    raise GitDirtyStateError(f"{context}: {detail}")


def _safe_rel(raw: str) -> PurePosixPath:
    text = str(raw or "").replace("\\", "/").strip()
    rel = PurePosixPath(text)
    if not text or rel.is_absolute() or ".." in rel.parts or (rel.parts and rel.parts[0] == ".git"):
        raise GitDirtyStateError(f"Unsafe dirty-tree path: {raw!r}")
    return rel


def _index_path(root: Path) -> Path:
    raw = _require(_git(root, "rev-parse", "--git-path", "index"), "Unable to resolve Git index path")
    path = Path(raw)
    return path if path.is_absolute() else (root / path).resolve()


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _path_identity(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
    except OSError:
        return {"kind": "missing", "mode": None, "sha256": "", "target": ""}
    mode = stat.S_IMODE(info.st_mode)
    if stat.S_ISLNK(info.st_mode):
        target = os.readlink(path)
        return {
            "kind": "symlink",
            "mode": mode,
            "sha256": hashlib.sha256(target.encode("utf-8", "surrogatepass")).hexdigest(),
            "target": target,
        }
    if stat.S_ISREG(info.st_mode):
        return {"kind": "file", "mode": mode, "sha256": _file_sha(path), "target": ""}
    if stat.S_ISDIR(info.st_mode):
        digest = hashlib.sha256()
        for base, dirs, files in os.walk(path, followlinks=False):
            base_path = Path(base)
            dirs[:] = sorted(dirs)
            for name in sorted([*dirs, *files]):
                child = base_path / name
                rel = child.relative_to(path).as_posix()
                ident = _path_identity(child)
                digest.update(rel.encode("utf-8", "surrogateescape")); digest.update(b"\0")
                digest.update(json.dumps(ident, sort_keys=True, separators=(",", ":")).encode("utf-8")); digest.update(b"\0")
        return {"kind": "directory", "mode": mode, "sha256": digest.hexdigest(), "target": ""}
    return {"kind": "other", "mode": mode, "sha256": "", "target": ""}


def _copy_exact(source: Path, destination: Path) -> None:
    if destination.is_dir() and not destination.is_symlink():
        shutil.rmtree(destination)
    elif destination.exists() or destination.is_symlink():
        destination.unlink()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink():
        destination.symlink_to(os.readlink(source), target_is_directory=source.is_dir())
    elif source.is_dir():
        shutil.copytree(source, destination, symlinks=True, copy_function=shutil.copy2)
    elif source.is_file():
        shutil.copy2(source, destination, follow_symlinks=False)
    else:
        raise GitDirtyStateError(f"Unsupported dirty-tree path type: {source}")


def _delete(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=False)
    elif path.exists() or path.is_symlink():
        path.unlink()


def _dirty_paths(state: Any) -> tuple[str, ...]:
    paths: set[str] = set()
    for sequence_name in ("staged", "unstaged"):
        for item in getattr(state, sequence_name, ()) or ():
            for raw in (getattr(item, "path", ""), getattr(item, "old_path", "")):
                text = str(raw or "").replace("\\", "/")
                if text:
                    paths.add(text)
    for raw in getattr(state, "unmerged", ()) or ():
        if str(raw):
            paths.add(str(raw).replace("\\", "/"))
    for raw in getattr(state, "untracked", ()) or ():
        text = str(raw or "").replace("\\", "/")
        if text and not any(text.startswith(prefix) for prefix in _OPERATIONAL_UNTRACKED_PREFIXES):
            paths.add(text)
    return tuple(sorted(paths))


def _staged_paths(state: Any) -> tuple[str, ...]:
    paths: set[str] = set()
    for item in getattr(state, "staged", ()) or ():
        for raw in (getattr(item, "path", ""), getattr(item, "old_path", "")):
            text = str(raw or "").replace("\\", "/")
            if text:
                paths.add(text)
    return tuple(sorted(paths))


def _index_entries(root: Path, paths: Iterable[str]) -> tuple[str, ...]:
    selected = [str(_safe_rel(item)) for item in paths]
    if not selected:
        return ()
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--stage", "-z", "--", *selected],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=12,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitDirtyStateError(f"Unable to inspect staged index entries: {exc}") from exc
    if proc.returncode != 0:
        detail = bytes(proc.stderr or b"").decode("utf-8", "replace").strip()
        raise GitDirtyStateError(f"Unable to inspect staged index entries: {detail or 'git ls-files failed'}")
    return tuple(raw.decode("utf-8", "surrogateescape") for raw in bytes(proc.stdout or b"").split(b"\0") if raw)


def _restore_rebased_index(root: Path, payload: Mapping[str, Any]) -> None:
    staged_paths = [str(_safe_rel(item)) for item in payload.get("staged_paths", []) if str(item)]
    if not staged_paths:
        return
    remove = subprocess.run(
        ["git", "-C", str(root), "update-index", "--force-remove", "--", *staged_paths],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=12,
    )
    if remove.returncode != 0:
        detail = bytes(remove.stderr or remove.stdout or b"").decode("utf-8", "replace").strip()
        raise GitDirtyStateError(f"Unable to clear staged paths before dirty-state rebase: {detail}")
    entries = [str(item) for item in payload.get("staged_index_entries", []) if str(item)]
    if entries:
        body = "".join(item + "\n" for item in entries).encode("utf-8", "surrogateescape")
        apply = subprocess.run(
            ["git", "-C", str(root), "update-index", "--index-info"],
            input=body, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=12,
        )
        if apply.returncode != 0:
            detail = bytes(apply.stderr or apply.stdout or b"").decode("utf-8", "replace").strip()
            raise GitDirtyStateError(f"Unable to restore staged index entries over transaction result: {detail}")
    actual = _index_entries(root, staged_paths)
    if tuple(entries) != actual:
        raise GitDirtyStateError("Rebased dirty-tree index entries did not match the captured staged entries")


def capture_dirty_tree(repo_root: str | Path, snapshot_root: str | Path) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    destination = Path(snapshot_root).resolve()
    try:
        state = collect_git_state(root)
    except GitStateError as exc:
        raise GitDirtyStateError(f"Dirty-tree capture requires a Git worktree: {exc}") from exc
    destination.mkdir(parents=True, exist_ok=False)
    files_root = destination / "worktree"
    files_root.mkdir(parents=True, exist_ok=True)
    index = _index_path(root)
    index_backup = destination / "index"
    if index.exists():
        shutil.copy2(index, index_backup)
    paths = _dirty_paths(state)
    staged_paths = _staged_paths(state)
    staged_index_entries = _index_entries(root, staged_paths)
    entries: list[dict[str, Any]] = []
    for raw in paths:
        rel = _safe_rel(raw)
        source = root.joinpath(*rel.parts)
        identity = _path_identity(source)
        entry = {"path": rel.as_posix(), **identity}
        if identity["kind"] != "missing":
            _copy_exact(source, files_root.joinpath(*rel.parts))
        entries.append(entry)
    payload = {
        "schema_version": DIRTY_TREE_SCHEMA_VERSION,
        "kind": "devtool.git-dirty-tree-snapshot",
        "repository_root": str(root),
        "head": str(state.head.get("sha") or ""),
        "head_ref": str(state.head.get("symbolic_ref") or ""),
        "index_path": str(index),
        "index_existed": index.exists(),
        "index_sha256": _file_sha(index) if index.exists() else "",
        "status_identity": git_status_identity(root),
        "dirty": bool(paths),
        "paths": list(paths),
        "staged_paths": list(staged_paths),
        "staged_index_entries": list(staged_index_entries),
        "unmerged": sorted(str(item) for item in (getattr(state, "unmerged", ()) or ())),
        "entries": entries,
        "untracked": [
            str(item).replace("\\", "/") for item in (getattr(state, "untracked", ()) or ())
            if not any(str(item).replace("\\", "/").startswith(prefix) for prefix in _OPERATIONAL_UNTRACKED_PREFIXES)
        ],
    }
    payload["snapshot_digest"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    (destination / "manifest.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def load_dirty_tree_snapshot(snapshot_root: str | Path) -> dict[str, Any]:
    path = Path(snapshot_root).resolve() / "manifest.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GitDirtyStateError(f"Unable to read dirty-tree snapshot {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("kind") != "devtool.git-dirty-tree-snapshot":
        raise GitDirtyStateError(f"Invalid dirty-tree snapshot: {path}")
    return payload


def _result_tracks_path(root: Path, revision: str, rel: str) -> bool:
    proc = _git(root, "cat-file", "-e", f"{revision}:{rel}")
    return proc.returncode == 0


def _snapshot_entry_by_path(payload: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    entries: dict[str, Mapping[str, Any]] = {}
    for raw in payload.get("entries", []) if isinstance(payload.get("entries"), list) else []:
        if not isinstance(raw, Mapping):
            continue
        rel = _safe_rel(str(raw.get("path") or "")).as_posix()
        entries[rel] = raw
    return entries


def _result_path_identity(root: Path, revision: str, rel: str) -> dict[str, Any]:
    """Return a Git-commit path identity compatible with snapshot conflict checks.

    LIFE-X01 uses content identity, not a broad path-level diff alone, when
    deciding whether a transaction result truly conflicts with preserved user
    dirty work.  Git stores files and symlinks as blobs; directories are reduced
    to a deterministic tree digest for conflict evidence.
    """

    listing = _git(root, "ls-tree", "-z", revision, "--", rel)
    if listing.returncode != 0:
        detail = (listing.stderr or listing.stdout or "git ls-tree failed").strip()
        raise GitDirtyStateError(f"Unable to inspect result path {rel!r}: {detail}")
    raw = (listing.stdout or "").split("\0", 1)[0]
    if not raw:
        return {"kind": "missing", "mode": None, "sha256": "", "target": ""}
    meta, _sep, _path = raw.partition("\t")
    parts = meta.split()
    if len(parts) < 3:
        raise GitDirtyStateError(f"Unable to parse result path identity for {rel!r}")
    mode_text, object_kind, object_id = parts[0], parts[1], parts[2]
    if object_kind == "tree":
        tree = _git(root, "ls-tree", "-r", "-z", revision, "--", rel)
        if tree.returncode != 0:
            detail = (tree.stderr or tree.stdout or "git ls-tree failed").strip()
            raise GitDirtyStateError(f"Unable to inspect result tree {rel!r}: {detail}")
        digest = hashlib.sha256()
        for entry in (tree.stdout or "").split("\0"):
            if not entry:
                continue
            digest.update(entry.encode("utf-8", "surrogateescape")); digest.update(b"\0")
        return {"kind": "directory", "mode": int(mode_text[-3:], 8) if mode_text[-3:].isdigit() else None, "sha256": digest.hexdigest(), "target": ""}
    try:
        blob = subprocess.run(
            ["git", "-C", str(root), "cat-file", "blob", object_id],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=12,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitDirtyStateError(f"Unable to read result path {rel!r}: {exc}") from exc
    if blob.returncode != 0:
        detail = bytes(blob.stderr or blob.stdout or b"git cat-file failed").decode("utf-8", "replace").strip()
        raise GitDirtyStateError(f"Unable to read result path {rel!r}: {detail}")
    data = bytes(blob.stdout or b"")
    if mode_text == "120000":
        target = data.decode("utf-8", "surrogateescape")
        return {
            "kind": "symlink",
            "mode": 0o777,
            "sha256": hashlib.sha256(target.encode("utf-8", "surrogatepass")).hexdigest(),
            "target": target,
        }
    return {
        "kind": "file",
        "mode": 0o755 if mode_text == "100755" else 0o644,
        "sha256": hashlib.sha256(data).hexdigest(),
        "target": "",
    }


def _result_matches_preserved_entry(root: Path, revision: str, rel: str, entry: Mapping[str, Any] | None) -> bool:
    if not isinstance(entry, Mapping):
        return False
    expected_kind = str(entry.get("kind") or "")
    if expected_kind not in {"file", "symlink", "missing"}:
        return False
    actual = _result_path_identity(root, revision, rel)
    if actual.get("kind") != expected_kind:
        return False
    if str(actual.get("sha256") or "") != str(entry.get("sha256") or ""):
        return False
    if expected_kind == "symlink" and str(actual.get("target") or "") != str(entry.get("target") or ""):
        return False
    # Git tracks only the owner executable bit for regular files.  Snapshot
    # modes intentionally preserve the exact host mode (Termux commonly uses
    # 0600/0660 rather than 0644), so comparing the full POSIX mode against
    # Git's normalized 100644/100755 identity creates false conflicts.
    expected_mode = entry.get("mode")
    if expected_kind == "file" and expected_mode is not None:
        try:
            expected_executable = bool(int(expected_mode) & stat.S_IXUSR)
        except (TypeError, ValueError):
            return False
        actual_executable = actual.get("mode") == 0o755
        if expected_executable != actual_executable:
            return False
    return True


def validate_dirty_tree_rebase(snapshot_root: str | Path, *, result_commit: str) -> None:
    snapshot_dir = Path(snapshot_root).resolve()
    payload = load_dirty_tree_snapshot(snapshot_dir)
    root = Path(str(payload.get("repository_root") or "")).resolve()
    original_head = str(payload.get("head") or "").strip()
    if payload.get("unmerged"):
        raise GitDirtyStateError("Cannot transplant an unmerged/conflicted index across a transaction HEAD change")
    entries_by_path = _snapshot_entry_by_path(payload)
    for raw in payload.get("untracked", []) if isinstance(payload.get("untracked"), list) else []:
        rel = _safe_rel(str(raw)).as_posix()
        if _result_tracks_path(root, result_commit, rel):
            # Convergent add: if the transaction begins tracking a path the user
            # already had untracked, no user bytes are lost when the resulting
            # Git object is identical to the preserved entry.  Treat it the same
            # as the tracked dirty-path convergence rule below.  Real content,
            # symlink-target, or executable-bit differences remain fail-closed.
            if _result_matches_preserved_entry(root, result_commit, rel, entries_by_path.get(rel)):
                continue
            raise GitDirtyStateError(
                f"Cannot preserve untracked path {rel!r}: transaction result commit starts tracking that path with different content or Git mode"
            )
    if original_head and result_commit and original_head != result_commit:
        untracked_paths = set(str(item) for item in payload.get("untracked", []) if str(item))
        for raw in payload.get("paths", []) if isinstance(payload.get("paths"), list) else []:
            rel = _safe_rel(str(raw)).as_posix()
            if rel in untracked_paths:
                continue
            proc = _git(root, "diff", "--quiet", original_head, result_commit, "--", rel)
            if proc.returncode == 1:
                if _result_matches_preserved_entry(root, result_commit, rel, entries_by_path.get(rel)):
                    continue
                raise GitDirtyStateError(
                    f"Cannot preserve dirty path {rel!r}: transaction result also changed that path"
                )
            if proc.returncode not in {0, 1}:
                detail = (proc.stderr or proc.stdout or "git diff failed").strip()
                raise GitDirtyStateError(f"Unable to validate dirty path {rel!r} against transaction result: {detail}")


def restore_dirty_tree(
    snapshot_root: str | Path,
    *,
    allow_head_change: bool = False,
    expected_current_head: str = "",
) -> dict[str, Any]:
    snapshot_dir = Path(snapshot_root).resolve()
    payload = load_dirty_tree_snapshot(snapshot_dir)
    root = Path(str(payload.get("repository_root") or "")).resolve()
    current_head_proc = _git(root, "rev-parse", "--verify", "HEAD")
    current_head = (current_head_proc.stdout or "").strip() if current_head_proc.returncode == 0 else ""
    original_head = str(payload.get("head") or "")
    if expected_current_head and current_head != expected_current_head:
        raise GitDirtyStateError(
            f"Dirty-tree restore expected current HEAD {expected_current_head}, found {current_head or '<unborn>'}"
        )
    if not allow_head_change and current_head != original_head:
        raise GitDirtyStateError(
            f"Dirty-tree restore refuses HEAD drift: expected {original_head or '<unborn>'}, found {current_head or '<unborn>'}"
        )

    index = Path(str(payload.get("index_path") or ""))
    index_backup = snapshot_dir / "index"
    rebasing = bool(allow_head_change and current_head != original_head)
    if rebasing:
        _restore_rebased_index(root, payload)
    elif bool(payload.get("index_existed")):
        if not index_backup.is_file():
            raise GitDirtyStateError(f"Dirty-tree index backup is missing: {index_backup}")
        index.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(index_backup, index)
    else:
        index.unlink(missing_ok=True)

    files_root = snapshot_dir / "worktree"
    restored: list[str] = []
    for raw in payload.get("entries", []) if isinstance(payload.get("entries"), list) else []:
        if not isinstance(raw, Mapping):
            continue
        rel = _safe_rel(str(raw.get("path") or ""))
        target = root.joinpath(*rel.parts)
        kind = str(raw.get("kind") or "missing")
        if kind == "missing":
            _delete(target)
        else:
            source = files_root.joinpath(*rel.parts)
            if not (source.exists() or source.is_symlink()):
                raise GitDirtyStateError(f"Dirty-tree backup path is missing: {source}")
            _copy_exact(source, target)
        current = _path_identity(target)
        expected = {key: raw.get(key) for key in ("kind", "mode", "sha256", "target")}
        if current != expected:
            raise GitDirtyStateError(f"Dirty-tree restore verification failed for {rel.as_posix()}")
        restored.append(rel.as_posix())

    index_sha = _file_sha(index) if index.exists() else ""
    if not rebasing and index_sha != str(payload.get("index_sha256") or ""):
        raise GitDirtyStateError("Dirty-tree index restore verification failed")
    status_identity = git_status_identity(root)
    # ``git status`` may refresh stat-cache/TREE-extension bytes in the index even
    # when its logical contents are unchanged.  GIT-FRX-13 promises an exact
    # same-HEAD index snapshot, so put the captured bytes back *after* computing
    # the verification/status evidence.  Changed-HEAD restores intentionally use
    # transplanted staged entries instead of the obsolete full index image.
    if not rebasing and bool(payload.get("index_existed")):
        shutil.copy2(index_backup, index)
        index_sha = _file_sha(index)
        if index_sha != str(payload.get("index_sha256") or ""):
            raise GitDirtyStateError("Dirty-tree final index byte restore verification failed")

    return {
        "schema_version": DIRTY_TREE_SCHEMA_VERSION,
        "kind": "devtool.git-dirty-tree-restore",
        "snapshot_digest": str(payload.get("snapshot_digest") or ""),
        "repository_root": str(root),
        "original_head": original_head,
        "current_head": current_head,
        "restored_paths": restored,
        "restored_index_sha256": index_sha,
        "index_restore_mode": "rebased-staged-entries" if rebasing else "exact",
        "status_identity": status_identity,
    }


def dirty_paths(snapshot: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(str(item) for item in snapshot.get("paths", []) if str(item))


def _fixture() -> dict[str, Any]:
    """Exercise exact staged/unstaged/untracked capture and same-HEAD restore."""
    with tempfile.TemporaryDirectory(prefix="devtool-git-frx13-") as td:
        base = Path(td)
        root = base / "repo"
        root.mkdir()
        for args in (("init", "-q"), ("config", "user.name", "Devtool Fixture"), ("config", "user.email", "fixture@example.invalid")):
            _require(_git(root, *args), "Unable to initialize dirty-tree fixture")
        (root / "staged.txt").write_text("base\n", encoding="utf-8")
        (root / "unstaged.txt").write_text("base\n", encoding="utf-8")
        _require(_git(root, "add", "."), "Unable to stage dirty-tree fixture baseline")
        _require(_git(root, "commit", "-qm", "fixture"), "Unable to commit dirty-tree fixture baseline")
        (root / "staged.txt").write_text("staged\n", encoding="utf-8")
        _require(_git(root, "add", "staged.txt"), "Unable to stage fixture change")
        (root / "unstaged.txt").write_text("unstaged\n", encoding="utf-8")
        (root / "untracked.txt").write_text("untracked\n", encoding="utf-8")
        before_status = git_status_identity(root)
        snap_dir = base / "snapshot"
        captured = capture_dirty_tree(root, snap_dir)
        _require(_git(root, "reset", "--hard", "-q", "HEAD"), "Unable to clean fixture")
        _require(_git(root, "clean", "-fdq"), "Unable to clean fixture untracked paths")
        restored = restore_dirty_tree(snap_dir)
        index = _index_path(root)
        index_exact = _file_sha(index) == str(captured.get("index_sha256") or "")
        after_status = git_status_identity(root)
        if before_status != after_status:
            raise GitDirtyStateError("GIT-FRX-13 fixture did not restore the exact dirty status identity")
        if not index_exact:
            raise GitDirtyStateError("GIT-FRX-13 fixture did not restore exact index bytes")
        return {
            "schema_version": DIRTY_TREE_SCHEMA_VERSION,
            "roadmap_id": "GIT-FRX-13",
            "snapshot_digest": captured.get("snapshot_digest", ""),
            "dirty_paths": list(captured.get("paths", [])),
            "index_restore_mode": restored.get("index_restore_mode", ""),
            "status_identity": after_status,
        }


def _main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="python -m devtool.git_dirty_state")
    parser.add_argument("command", choices=["fixture"])
    args = parser.parse_args(argv)
    if args.command != "fixture":
        raise SystemExit(2)
    print(json.dumps(_fixture(), indent=2, sort_keys=True))
    return 0


__all__ = [
    "DIRTY_TREE_SCHEMA_VERSION",
    "GitDirtyStateError",
    "capture_dirty_tree",
    "dirty_paths",
    "load_dirty_tree_snapshot",
    "restore_dirty_tree",
    "validate_dirty_tree_rebase",
]


if __name__ == "__main__":
    raise SystemExit(_main())
