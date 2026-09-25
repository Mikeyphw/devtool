from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import os
from pathlib import Path
import stat
import subprocess
from typing import Any


_VALID_MODES = {"exact-tree", "touched-files"}


@dataclass(frozen=True, slots=True)
class PathIdentity:
    path: str
    kind: str
    mode: str
    sha256: str | None
    git_oid: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class BaseContractResult:
    ok: bool
    mode: str
    base_commit: str
    base_tree: str
    touched_paths: tuple[str, ...]
    messages: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _run_git(repo_root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or f"git exited {proc.returncode}"
        raise RuntimeError(detail)
    return proc


def _hex_object_id(value: Any) -> str:
    text = str(value or "").strip().lower()
    if len(text) not in {40, 64} or any(ch not in "0123456789abcdef" for ch in text):
        return ""
    return text


def _safe_path(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    path = Path(text)
    if not text or path.is_absolute() or ".." in path.parts or text in {".", ".."}:
        raise RuntimeError(f"Invalid touched path: {text!r}")
    return path.as_posix()


def _blob_sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _current_path_identity(repo_root: Path, rel: str) -> PathIdentity:
    target = repo_root / rel
    try:
        st = target.lstat()
    except FileNotFoundError:
        return PathIdentity(rel, "missing", "000000", None, None)
    if stat.S_ISLNK(st.st_mode):
        payload = os.readlink(target).encode("utf-8", "surrogateescape")
        return PathIdentity(rel, "symlink", "120000", _blob_sha(payload), None)
    if stat.S_ISREG(st.st_mode):
        mode = "100755" if (st.st_mode & 0o111) else "100644"
        return PathIdentity(rel, "file", mode, _blob_sha(target.read_bytes()), None)
    if stat.S_ISDIR(st.st_mode):
        return PathIdentity(rel, "directory", "040000", None, None)
    return PathIdentity(rel, "other", f"{stat.S_IFMT(st.st_mode):06o}", None, None)


def path_source_sha256(path: Path) -> str:
    """Hash a repository path without following symlinks."""
    try:
        st = path.lstat()
    except FileNotFoundError as exc:
        raise RuntimeError(f"Path does not exist: {path}") from exc
    if stat.S_ISLNK(st.st_mode):
        return _blob_sha(os.readlink(path).encode("utf-8", "surrogateescape"))
    if stat.S_ISREG(st.st_mode):
        return _blob_sha(path.read_bytes())
    raise RuntimeError(f"Path is not a file or symlink: {path}")


def _base_path_identity(repo_root: Path, treeish: str, rel: str) -> PathIdentity:
    proc = _run_git(repo_root, "ls-tree", "-z", treeish, "--", rel, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"Cannot inspect base path {rel}")
    raw = proc.stdout
    if not raw:
        return PathIdentity(rel, "missing", "000000", None, None)
    record = raw.split("\0", 1)[0]
    header, sep, path = record.partition("\t")
    if not sep or path != rel:
        # A directory prefix may produce descendants with recursive forms in future
        # Git versions; touched-file contracts only support exact file-like paths.
        return PathIdentity(rel, "missing", "000000", None, None)
    parts = header.split()
    if len(parts) != 3:
        raise RuntimeError(f"Unexpected git ls-tree record for {rel}")
    mode, object_type, oid = parts
    if object_type == "blob":
        blob = subprocess.run(
            ["git", "-C", str(repo_root), "cat-file", "blob", oid],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if blob.returncode != 0:
            raise RuntimeError(blob.stderr.decode("utf-8", "replace").strip() or f"Cannot read base blob {oid}")
        kind = "symlink" if mode == "120000" else "file"
        return PathIdentity(rel, kind, mode, _blob_sha(blob.stdout), oid)
    if object_type == "tree":
        return PathIdentity(rel, "directory", mode, None, oid)
    return PathIdentity(rel, object_type or "other", mode, None, oid)


def _mapping(raw: Any, *, name: str) -> dict[str, str | None]:
    if not isinstance(raw, dict):
        raise RuntimeError(f"{name} must be an object keyed by touched path.")
    out: dict[str, str | None] = {}
    for key, value in raw.items():
        path = _safe_path(key)
        if value is None:
            out[path] = None
            continue
        digest = str(value).strip().lower()
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise RuntimeError(f"{name}[{path!r}] must be a SHA-256 digest or null.")
        out[path] = digest
    return out


def validate_base_contract_shape(manifest: dict[str, Any], inventory_entries: list[dict[str, Any]], messages: list[str]) -> bool:
    """Validate the GIT-FRX-05 manifest contract without consulting repository state."""
    declared = any(
        key in manifest
        for key in ("base_commit", "base_tree", "base_contract_mode", "touched_paths", "source_sha256", "expected_post_sha256")
    )
    if not declared:
        return True
    ok = True
    mode = str(manifest.get("base_contract_mode") or "").strip().lower()
    if mode not in _VALID_MODES:
        messages.append("base_contract_mode must be one of: exact-tree, touched-files.")
        ok = False
    base_commit = _hex_object_id(manifest.get("base_commit"))
    base_tree = _hex_object_id(manifest.get("base_tree"))
    if not base_commit:
        messages.append("base_commit must be a 40- or 64-character Git object ID.")
        ok = False
    if not base_tree:
        messages.append("base_tree must be a 40- or 64-character Git object ID.")
        ok = False

    raw_paths = manifest.get("touched_paths")
    if not isinstance(raw_paths, list) or not raw_paths:
        messages.append("touched_paths must be a non-empty list when a base contract is declared.")
        return False
    try:
        touched = tuple(_safe_path(item) for item in raw_paths)
    except RuntimeError as exc:
        messages.append(str(exc))
        return False
    if len(set(touched)) != len(touched):
        messages.append("touched_paths must not contain duplicates.")
        ok = False

    try:
        sources = _mapping(manifest.get("source_sha256"), name="source_sha256")
        posts = _mapping(manifest.get("expected_post_sha256"), name="expected_post_sha256")
    except RuntimeError as exc:
        messages.append(str(exc))
        return False
    touched_set = set(touched)
    if set(sources) != touched_set:
        messages.append("source_sha256 keys must exactly match touched_paths.")
        ok = False
    if set(posts) != touched_set:
        messages.append("expected_post_sha256 keys must exactly match touched_paths.")
        ok = False

    inventory: dict[str, dict[str, Any]] = {}
    for raw in inventory_entries:
        path = str(raw.get("path") or "").strip().replace("\\", "/")
        if path:
            inventory[path] = raw
    if set(inventory) != touched_set:
        messages.append("touched_paths must exactly match the artifact inventory paths.")
        ok = False

    for path in sorted(touched_set & set(inventory)):
        entry = inventory[path]
        action = str(entry.get("action") or "").strip().lower()
        inv_source = str(entry.get("source_sha256") or "").strip().lower() or None
        inv_post = str(entry.get("sha256") or "").strip().lower() or None
        source = sources.get(path)
        post = posts.get(path)
        if action == "add":
            if source is not None:
                messages.append(f"source_sha256[{path!r}] must be null for an add.")
                ok = False
            if post != inv_post:
                messages.append(f"expected_post_sha256[{path!r}] must match the inventory payload SHA-256.")
                ok = False
        elif action == "modify":
            if source is None:
                messages.append(f"source_sha256[{path!r}] must be a SHA-256 digest for a modify.")
                ok = False
            if post != inv_post:
                messages.append(f"expected_post_sha256[{path!r}] must match the inventory payload SHA-256.")
                ok = False
        elif action == "delete":
            if source is None:
                messages.append(f"source_sha256[{path!r}] must be a SHA-256 digest for a delete.")
                ok = False
            if post is not None:
                messages.append(f"expected_post_sha256[{path!r}] must be null for a delete.")
                ok = False
    return ok


def validate_base_contract(repo_root: Path, manifest: dict[str, Any], inventory_entries: list[dict[str, Any]]) -> BaseContractResult:
    """Verify a GIT-FRX-05 base contract against the current repository/worktree."""
    messages: list[str] = []
    mode = str(manifest.get("base_contract_mode") or "").strip().lower()
    if mode not in _VALID_MODES:
        return BaseContractResult(False, mode, "", "", (), ("Base contract is not declared or has invalid mode.",))
    base_commit = _hex_object_id(manifest.get("base_commit"))
    base_tree = _hex_object_id(manifest.get("base_tree"))
    touched = tuple(_safe_path(item) for item in manifest.get("touched_paths") or [])
    sources = _mapping(manifest.get("source_sha256"), name="source_sha256")

    inside = _run_git(repo_root, "rev-parse", "--is-inside-work-tree", check=False)
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return BaseContractResult(False, mode, base_commit, base_tree, touched, ("Base contracts require a Git worktree.",))
    tree_probe = _run_git(repo_root, "cat-file", "-e", f"{base_tree}^{{tree}}", check=False)
    if tree_probe.returncode != 0:
        return BaseContractResult(False, mode, base_commit, base_tree, touched, (f"base_tree is not available: {base_tree}",))
    commit_probe = _run_git(repo_root, "cat-file", "-e", f"{base_commit}^{{commit}}", check=False)
    commit_available = commit_probe.returncode == 0
    if commit_available:
        actual_base_tree = _run_git(repo_root, "show", "-s", "--format=%T", base_commit).stdout.strip().lower()
        if actual_base_tree != base_tree:
            return BaseContractResult(False, mode, base_commit, base_tree, touched, (f"base_tree does not match base_commit: expected {base_tree}, commit has {actual_base_tree}.",))
    elif mode == "exact-tree":
        return BaseContractResult(False, mode, base_commit, base_tree, touched, (f"base_commit is not available: {base_commit}",))

    if mode == "exact-tree":
        head = _run_git(repo_root, "rev-parse", "HEAD", check=False)
        if head.returncode != 0:
            messages.append("exact-tree base contract requires a committed HEAD.")
        elif head.stdout.strip().lower() != base_commit:
            messages.append(f"exact-tree base_commit mismatch: expected {base_commit}, found {head.stdout.strip().lower()}.")
        head_tree = _run_git(repo_root, "rev-parse", "HEAD^{tree}", check=False)
        if head_tree.returncode != 0 or head_tree.stdout.strip().lower() != base_tree:
            found = head_tree.stdout.strip().lower() if head_tree.returncode == 0 else "<unavailable>"
            messages.append(f"exact-tree base_tree mismatch: expected {base_tree}, found {found}.")
        status = _run_git(repo_root, "status", "--porcelain=v1", "--untracked-files=all", check=False)
        if status.returncode != 0:
            messages.append("exact-tree base contract could not inspect worktree drift.")
        elif status.stdout.strip():
            messages.append("exact-tree base contract refuses a dirty worktree.")
    else:
        inventory = {str(item.get("path") or "").strip().replace("\\", "/"): item for item in inventory_entries}
        for path in touched:
            entry = inventory[path]
            action = str(entry.get("action") or "").strip().lower()
            base_state = _base_path_identity(repo_root, base_tree, path)
            current_state = _current_path_identity(repo_root, path)
            if action == "add":
                if base_state.kind != "missing":
                    messages.append(f"Touched add path existed at base: {path} ({base_state.kind} {base_state.mode}).")
                if current_state.kind != "missing":
                    messages.append(f"Touched add path drifted from missing state: {path} is {current_state.kind} {current_state.mode}.")
                continue
            if base_state.kind not in {"file", "symlink"}:
                messages.append(f"Touched {action} path is not a file/symlink at base: {path} ({base_state.kind}).")
                continue
            if current_state.kind != base_state.kind:
                messages.append(f"Touched path type changed for {path}: base={base_state.kind}, current={current_state.kind}.")
                continue
            if current_state.mode != base_state.mode:
                messages.append(f"Touched path mode changed for {path}: base={base_state.mode}, current={current_state.mode}.")
            if current_state.sha256 != base_state.sha256:
                detail = "symlink target" if base_state.kind == "symlink" else "content"
                messages.append(f"Touched path {detail} changed for {path}.")
            expected_source = sources.get(path)
            if expected_source is not None and current_state.sha256 != expected_source:
                messages.append(f"Touched path source SHA-256 mismatch for {path}.")

    ok = not messages
    if ok:
        if mode == "touched-files" and not commit_available:
            messages.append("base_commit is unavailable locally; touched-files verification used the declared base_tree object.")
        messages.append(f"Base contract verified ({mode}) for {len(touched)} touched path(s).")
    return BaseContractResult(ok, mode, base_commit, base_tree, touched, tuple(messages))


def build_base_contract(repo_root: Path, inventory_entries: list[dict[str, Any]], *, mode: str = "touched-files") -> dict[str, Any]:
    """Build a manifest fragment for the current HEAD and inventory."""
    mode = str(mode).strip().lower()
    if mode not in _VALID_MODES:
        raise RuntimeError(f"Unsupported base contract mode: {mode}")
    head = _run_git(repo_root, "rev-parse", "HEAD", check=False)
    if head.returncode != 0:
        raise RuntimeError("Base contracts require a committed HEAD.")
    base_commit = head.stdout.strip().lower()
    base_tree = _run_git(repo_root, "rev-parse", "HEAD^{tree}").stdout.strip().lower()
    touched: list[str] = []
    sources: dict[str, str | None] = {}
    posts: dict[str, str | None] = {}
    for raw in inventory_entries:
        path = _safe_path(raw.get("path"))
        action = str(raw.get("action") or "").strip().lower()
        touched.append(path)
        current = _current_path_identity(repo_root, path)
        sources[path] = None if action == "add" else current.sha256
        posts[path] = None if action == "delete" else (str(raw.get("sha256") or "").strip().lower() or None)
    return {
        "base_commit": base_commit,
        "base_tree": base_tree,
        "base_contract_mode": mode,
        "touched_paths": touched,
        "source_sha256": sources,
        "expected_post_sha256": posts,
    }


def _fixture() -> dict[str, Any]:
    import json
    import tempfile

    with tempfile.TemporaryDirectory(prefix="devtool-frx05-base-") as tmp:
        root = Path(tmp) / "repo"
        root.mkdir()
        _run_git(root, "init", "-q")
        _run_git(root, "config", "user.email", "devtool-fixture@example.invalid")
        _run_git(root, "config", "user.name", "Devtool Fixture")
        touched = root / "touched.txt"
        touched.write_text("before\n", encoding="utf-8")
        (root / "unrelated.txt").write_text("v1\n", encoding="utf-8")
        _run_git(root, "add", ".")
        _run_git(root, "commit", "-qm", "base")
        after = b"after\n"
        entry = {
            "path": "touched.txt",
            "action": "modify",
            "source_sha256": path_source_sha256(touched),
            "sha256": _blob_sha(after),
            "size": len(after),
        }
        manifest: dict[str, Any] = {
            "schema_version": 2,
            "type": "overlay",
            "inventory": {"format": 1, "entries": [entry]},
        }
        manifest.update(build_base_contract(root, [entry], mode="touched-files"))
        _run_git(root, "checkout", "--", "unrelated.txt")
        result = validate_base_contract(root, manifest, [entry])
        return {
            "schema_version": 1,
            "roadmap_id": "GIT-FRX-05",
            "contract": {key: manifest[key] for key in (
                "base_commit", "base_tree", "base_contract_mode", "touched_paths", "source_sha256", "expected_post_sha256"
            )},
            "verification": result.to_dict(),
        }


def _main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Devtool overlay base-contract utilities")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("fixture", help="emit a deterministic touched-file base-contract fixture")
    materialize = sub.add_parser("materialize", help="materialize a base-contract fragment from inventory JSON")
    materialize.add_argument("--repo", default=".")
    materialize.add_argument("--inventory-json", required=True)
    materialize.add_argument("--mode", choices=sorted(_VALID_MODES), default="touched-files")
    args = parser.parse_args(argv)
    if args.command == "fixture":
        print(json.dumps(_fixture(), indent=2, sort_keys=True))
        return 0
    raw = json.loads(Path(args.inventory_json).read_text(encoding="utf-8"))
    entries = raw.get("entries") if isinstance(raw, dict) else raw
    if not isinstance(entries, list) or any(not isinstance(item, dict) for item in entries):
        parser.error("inventory JSON must be a list of entries or an object with entries[]")
    payload = build_base_contract(Path(args.repo).resolve(), [dict(item) for item in entries], mode=args.mode)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
