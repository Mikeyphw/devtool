"""NEXT-05: source-locked multi-repository adoption coordinator.

Never performs cross-repository rollback. Each prepared repository is committed by
Devtool's existing single-repository overlay transaction and audited individually.
A receipt is a local record, not remote or release authority.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
from typing import Any, Iterator
import tomllib
import zipfile

from .config_migration import migrate_config_text
from .wrapper_launcher import expected_launcher_texts
from .overlay.checker import check_overlay

SCHEMA = "devtool.multi-repo-adoption.v1"
SHA = re.compile(r"^[a-f0-9]{40}$")
DIGEST = re.compile(r"^[a-f0-9]{64}$")
NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
WRAPPER = ("devtoolw", "devtoolw.cmd", ".devtool-wrapper.lock")

class AdoptionBlocked(ValueError):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(data: Any) -> bytes:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def _git(repo: Path, *args: str, check: bool = True) -> str:
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=20)
    if check and p.returncode:
        raise AdoptionBlocked(f"Git {args[0]} refused in {repo.name}: {(p.stderr or p.stdout).strip()[:220]}")
    return p.stdout.strip() if p.returncode == 0 else ""


def _path(repo: Path, name: str) -> Path:
    if not isinstance(name, str) or not name or "\\" in name or name.startswith("/"):
        raise AdoptionBlocked(f"invalid target-relative path: {name!r}")
    p = PurePosixPath(name)
    if any(x in ("", ".", "..") for x in p.parts) or ".git" in p.parts or p.as_posix() != name:
        raise AdoptionBlocked(f"unsafe repository path: {name!r}")
    node = repo
    for part in p.parts:
        node = node / part
        if node.is_symlink():
            raise AdoptionBlocked(f"symlink in touched path: {name}")
    if node.exists() and not node.is_file():
        raise AdoptionBlocked(f"not a regular file: {name}")
    return node


def _read_config(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 2_000_000:
        raise AdoptionBlocked("manifest missing, symlink, or too large")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise AdoptionBlocked(f"invalid adoption manifest: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise AdoptionBlocked("unsupported adoption manifest schema")
    repos = payload.get("repositories")
    if not isinstance(repos, list) or not 1 <= len(repos) <= 32:
        raise AdoptionBlocked("repositories must contain 1-32 entries")
    ids: set[str] = set()
    roots: set[str] = set()
    for row in repos:
        if not isinstance(row, dict) or not NAME.fullmatch(str(row.get("id", ""))):
            raise AdoptionBlocked("each entry needs a bounded unique id")
        if row["id"] in ids:
            raise AdoptionBlocked("duplicate repository id")
        ids.add(row["id"])
        root = Path(str(row.get("path", ""))).expanduser()
        if not root.is_absolute() or not root.is_dir() or root.is_symlink():
            raise AdoptionBlocked(f"{row['id']}: absolute existing regular repository path required")
        resolved = str(root.resolve())
        if resolved in roots or any(Path(resolved) in Path(other).parents or Path(other) in Path(resolved).parents for other in roots):
            raise AdoptionBlocked("duplicate or nested repository path")
        roots.add(resolved)
        for field in ("source_head", "source_tree"):
            if not SHA.fullmatch(str(row.get(field, ""))):
                raise AdoptionBlocked(f"{row['id']}: exact {field} required")
        for field in ("branch", "remote", "remote_url"):
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise AdoptionBlocked(f"{row['id']}: {field} must be explicit")
        archive = Path(str(row.get("overlay", ""))).expanduser()
        if not archive.is_absolute() or not archive.is_file() or archive.is_symlink():
            raise AdoptionBlocked(f"{row['id']}: absolute overlay archive required")
        if not DIGEST.fullmatch(str(row.get("overlay_sha256", ""))):
            raise AdoptionBlocked(f"{row['id']}: archive SHA-256 must be pinned")
        for flag in ("wrapper", "migrate_config"):
            if flag in row and not isinstance(row[flag], bool):
                raise AdoptionBlocked(f"{row['id']}: {flag} must be bool")
    return payload


def _archive(repo: Path, row: dict[str, Any]) -> tuple[dict[str, bytes | None], dict[str, bytes | None], dict[str, Any]]:
    """Inspect a source-bound overlay; never run hooks or stage changes."""
    archive = Path(row["overlay"])
    if _sha(archive.read_bytes()) != row["overlay_sha256"]:
        raise AdoptionBlocked("overlay SHA-256 changed")
    if archive.stat().st_size > 150_000_000:
        raise AdoptionBlocked("overlay exceeds bounded size")
    try:
        with zipfile.ZipFile(archive) as z:
            names = z.namelist()
            if len(names) != len(set(names)) or len(names) > 5000:
                raise AdoptionBlocked("ambiguous/oversized overlay inventory")
            meta = json.loads(z.read(".devtool-artifact.json"))
            entries = meta.get("inventory", {}).get("entries", [])
            if not isinstance(entries, list) or not entries:
                raise AdoptionBlocked("overlay must declare files")
            data: dict[str, bytes | None] = {}
            before: dict[str, bytes | None] = {}
            for entry in entries:
                rel = str(entry.get("path", ""))
                target = _path(repo, rel)
                if rel in data:
                    raise AdoptionBlocked("duplicate overlay inventory path")
                action = entry.get("action")
                if action not in ("add", "modify", "delete"):
                    raise AdoptionBlocked("unsupported overlay operation")
                old = target.read_bytes() if target.is_file() else None
                before[rel] = old
                if action == "delete":
                    data[rel] = None
                else:
                    info = z.getinfo(rel)
                    if info.file_size > 24_000_000:
                        raise AdoptionBlocked("oversized overlay member")
                    if (info.external_attr >> 16) & 0o170000 == stat.S_IFLNK:
                        raise AdoptionBlocked("overlay symlink is prohibited")
                    content = z.read(rel)
                    if _sha(content) != entry.get("sha256"):
                        raise AdoptionBlocked("overlay member checksum mismatch")
                    data[rel] = content
    except (zipfile.BadZipFile, KeyError, UnicodeError, ValueError) as exc:
        raise AdoptionBlocked(f"overlay parse refused: {exc}") from exc
    return data, before, meta


def _verify_head_preimages(repo: Path, manifest: dict[str, Any]) -> None:
    """On a partial checkout, trust tracked HEAD bytes, never the edited worktree."""
    entries = manifest.get("inventory", {}).get("entries", [])
    for item in entries:
        name = item.get("path")
        _path(repo, str(name))
        kind = item.get("action")
        raw = subprocess.run(["git", "-C", str(repo), "show", "HEAD:" + name],
                             capture_output=True, timeout=15)
        if kind == "add":
            if raw.returncode == 0:
                raise AdoptionBlocked(f"add path already tracked at source HEAD: {name}")
        elif raw.returncode or _sha(raw.stdout) != item.get("source_sha256"):
            raise AdoptionBlocked(f"source commit preimage mismatch: {name}")


def _desired(repo: Path, row: dict[str, Any]) -> tuple[dict[str, bytes | None], dict[str, bytes | None]]:
    contents, before, _ = _archive(repo, row)
    if row.get("migrate_config"):
        key = ".devtool.toml"
        if key in contents:
            raise AdoptionBlocked("cannot safely merge an overlay-owned config with a separate legacy migration")
        file = _path(repo, key)
        if not file.is_file():
            raise AdoptionBlocked("legacy migration requires an existing config")
        original = file.read_bytes()
        try:
            txt = original.decode("utf-8")
            translated, findings = migrate_config_text(txt)
            tomllib.loads(translated)
        except (UnicodeError, ValueError, tomllib.TOMLDecodeError) as exc:
            raise AdoptionBlocked(f"ambiguous legacy config: {exc}") from exc
        if any(not f.safe for f in findings):
            raise AdoptionBlocked("ambiguous legacy config keys; manual reconciliation required")
        if translated != txt:
            contents[key] = translated.encode("utf-8")
            before[key] = original
    if row.get("wrapper"):
        expected = {**expected_launcher_texts(), ".devtool-wrapper.lock": ""}
        for name in WRAPPER:
            if name in contents:
                raise AdoptionBlocked(f"overlay already owns generated wrapper path: {name}")
            file = _path(repo, name)
            old = file.read_bytes() if file.is_file() else None
            target = expected[name].encode("utf-8")
            if old is not None and old != target:
                raise AdoptionBlocked(f"existing generated wrapper differs: {name}")
            if name == "devtoolw" and old == target and not os.access(file, os.X_OK):
                raise AdoptionBlocked("existing devtoolw launcher is not executable")
            if old != target:
                contents[name] = target
                before[name] = old
    if not contents:
        raise AdoptionBlocked("empty candidate adoption")
    return contents, before


def _source(repo: Path, row: dict[str, Any]) -> dict[str, str]:
    head = _git(repo, "rev-parse", "HEAD")
    tree = _git(repo, "rev-parse", "HEAD^{tree}")
    branch = _git(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
    remote = _git(repo, "remote", "get-url", row["remote"])
    return {"head": head, "tree": tree, "branch": branch, "remote": remote}


def _clean(repo: Path) -> bool:
    return not _git(repo, "status", "--porcelain=v1", "--untracked-files=all")


def _matches(repo: Path, contents: dict[str, bytes | None]) -> bool:
    for name, value in contents.items():
        file = _path(repo, name)
        actual = file.read_bytes() if file.is_file() else None
        if value != actual:
            return False
    return True


def _checked_state_path(state_dir: Path, repos: list[dict[str, Any]]) -> None:
    if not state_dir.is_absolute():
        raise AdoptionBlocked("state dir must be absolute")
    if state_dir.is_symlink():
        raise AdoptionBlocked("state directory symlink refused")
    resolved = state_dir.resolve()
    for row in repos:
        root = Path(row["path"]).resolve()
        if resolved == root or root in resolved.parents:
            raise AdoptionBlocked("journal must live outside adopted Git repositories")


def _journal(state_dir: Path, digest: str) -> Path:
    return state_dir / f"{digest}.json"


def _read_journal(file: Path, digest: str) -> dict[str, Any]:
    if not file.exists():
        return {"schema": SCHEMA, "manifest_sha256": digest, "repositories": {}, "overall": "PREPARED"}
    if file.is_symlink() or file.stat().st_size > 2_000_000:
        raise AdoptionBlocked("journal unsafe")
    obj = json.loads(file.read_text())
    if obj.get("schema") != SCHEMA or obj.get("manifest_sha256") != digest or not isinstance(obj.get("repositories"), dict):
        raise AdoptionBlocked("journal identity mismatch")
    return obj


def _save(file: Path, payload: dict[str, Any]) -> None:
    file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, path = tempfile.mkstemp(prefix=".adoption.", dir=file.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(json.dumps(payload, indent=2, sort_keys=True).encode() + b"\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(path, 0o600)
        os.replace(path, file)
        fd = os.open(file.parent, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally:
        Path(path).unlink(missing_ok=True)


@contextmanager
def _lock(state_dir: Path) -> Iterator[None]:
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    file = state_dir / ".adoption.lock"
    if file.is_symlink():
        raise AdoptionBlocked("state lock symlink refused")
    with file.open("a+b") as fh:
        if os.name == "posix":
            import fcntl
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise AdoptionBlocked("another adoption coordinator owns the state lock") from exc
        yield


def _changed_paths(repo: Path) -> set[str]:
    process = subprocess.run(["git", "-C", str(repo), "-c", "core.quotepath=false", "status",
                              "--porcelain=v1", "--untracked-files=all"],
                             capture_output=True, text=True, timeout=20, check=True)
    raw = process.stdout.rstrip("\n")
    paths = set()
    for line in raw.splitlines():
        if not line or len(line) < 4:
            raise AdoptionBlocked("ambiguous Git status")
        tail = line[3:]
        if " -> " in tail or tail.startswith('"'):
            raise AdoptionBlocked("renames or escaped Git names require manual review")
        paths.add(tail)
    return paths


def _assessment(repo: Path, row: dict[str, Any], saved: dict[str, Any]) -> dict[str, Any]:
    info = {"id": row["id"], "path": str(repo), "state": "BLOCKED", "reason": "", "commit": None}
    try:
        identity = _source(repo, row)
        info["identity"] = identity
        if identity["branch"] != row["branch"] or identity["remote"] != row["remote_url"]:
            raise AdoptionBlocked("branch or remote identity differs")
        if saved.get("state") == "COMMITTED":
            if identity["head"] != saved.get("commit") or identity["tree"] != saved.get("tree"):
                raise AdoptionBlocked("previously committed repository has since changed; manual reconciliation required")
            info.update(state="COMMITTED", commit=identity["head"])
            return info
        if identity["head"] != row["source_head"] or identity["tree"] != row["source_tree"]:
            raise AdoptionBlocked("HEAD/tree drift from source lock")
        from .overlay.recovery import overlay_recovery_plan
        recovery = overlay_recovery_plan(repo, limit=25)
        if recovery.get("marker_issues") or recovery.get("active_transactions"):
            raise AdoptionBlocked("existing overlay transaction markers require explicit review/retirement before batch adoption")
        desired, before = _desired(repo, row)
        _, _, native_manifest = _archive(repo, row)
        _verify_head_preimages(repo, native_manifest)
        info["paths"] = sorted(desired)
        info["path_hashes"] = {k: _sha(v) if v is not None else None for k,v in desired.items()}
        status = _changed_paths(repo)
        if status:
            if status.issubset(desired.keys()) and _matches(repo, desired):
                # No unrelated staged modifications are allowed.
                staged = set(_git(repo, "diff", "--cached", "--name-only").splitlines())
                if staged:
                    raise AdoptionBlocked("preexisting staged index changes require manual review")
                info["state"] = "APPLIED_UNCOMMITTED"
            elif status & desired.keys():
                info["state"] = "PARTIAL"
                info["reason"] = "partial or divergent touched paths; no automatic overwrite"
            else:
                raise AdoptionBlocked("unrelated worktree changes")
        else:
            check = check_overlay(repo, row["overlay"])
            if not check.ok or not check.source_state_ok:
                info.update(state="DRIFTED", reason="overlay source conflict or unverified equivalence")
            elif all((before.get(p) == content) for p,content in desired.items()):
                info.update(state="BLOCKED", reason="no changes required; adoption provenance unverified")
            else:
                info["state"] = "FRESH"
        return info
    except (AdoptionBlocked, OSError, RuntimeError, ValueError) as exc:
        info.update(state="BLOCKED", reason=str(exc))
        return info


def plan(manifest_path: Path, state_dir: Path) -> dict[str, Any]:
    source = _read_config(manifest_path)
    digest = _sha(_json(source))
    _checked_state_path(state_dir, source["repositories"])
    state = _read_journal(_journal(state_dir, digest), digest)
    result = [_assessment(Path(r["path"]), r, state["repositories"].get(r["id"], {})) for r in source["repositories"]]
    allowed = all(i["state"] in ("FRESH", "APPLIED_UNCOMMITTED", "COMMITTED") for i in result)
    return {"schema": SCHEMA, "manifest_sha256": digest, "read_only": True,
            "safe_to_apply": allowed, "overall": "READY" if allowed else "BLOCKED",
            "repositories": result, "formal_release_qualified": False,
            "cross_repository_atomic": False, "unverified_external_authority": True}


def _stage_zip(repo: Path, row: dict[str, Any], target: Path) -> tuple[dict[str, bytes | None], list[str]]:
    desired, before = _desired(repo, row)
    entries = []
    preimages = {}
    images = {}
    for name, data in sorted(desired.items()):
        previous = before.get(name)
        action = "delete" if data is None else ("add" if previous is None else "modify")
        item: dict[str, Any] = {"path": name, "action": action}
        if data is not None:
            item.update(sha256=_sha(data), size=len(data))
            images[name] = _sha(data)
        if previous is not None:
            item["source_sha256"] = _sha(previous)
        preimages[name] = _sha(previous) if previous is not None else None
        entries.append(item)
    manifest = {"schema_version": 2, "type": "overlay", "name": "devtool-next05-" + row["id"],
                "inventory": {"format": 1, "entries": entries}, "validation": {"required": False},
                "base_contract_mode": "touched-files", "base_commit": row["source_head"],
                "base_tree": row["source_tree"], "touched_paths": sorted(desired),
                "source_sha256": preimages, "expected_post_sha256": images,
                "apply": {"requires_clean_worktree": True,
                          "commit": {"enabled": True, "mode": "new", "strategy": "single",
                                     "message": "devtool: adopt " + row["id"]}}}
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in sorted(desired.items()):
            if content is None:
                continue
            item = zipfile.ZipInfo(name)
            item.compress_type = zipfile.ZIP_DEFLATED
            item.external_attr = ((0o100755 if name == "devtoolw" else 0o100644) << 16)
            z.writestr(item, content)
        z.writestr(".devtool-artifact.json", json.dumps(manifest, indent=2) + "\n")
    return desired, sorted(desired)


def _commit_partial(repo: Path, names: list[str], message: str) -> None:
    if _git(repo, "diff", "--cached", "--name-only"):
        raise AdoptionBlocked("index must be clean before scoped commit")
    subprocess.run(["git", "-C", str(repo), "add", "--", *names], check=True, timeout=20)
    try:
        subprocess.run(["git", "-C", str(repo), "commit", "-m", message, "--", *names], check=True, timeout=40,
                       capture_output=True, text=True)
    except (subprocess.CalledProcessError, OSError):
        raise AdoptionBlocked("commit interrupted; preserve index/worktree for explicit resume")


def _reconcile_after_crash(repo: Path, row: dict[str, Any]) -> dict[str, Any] | None:
    """A saved COMMITTING intent can be repaired only after exact one-parent proof."""
    cur = _source(repo, row)
    if cur["head"] == row["source_head"]:
        return None
    if _git(repo, "rev-list", "--parents", "-n", "1", "HEAD").split() != [cur["head"], row["source_head"]]:
        raise AdoptionBlocked("unexpected HEAD ancestry after interrupted commit")
    if _git(repo, "log", "-1", "--format=%s") != "devtool: adopt " + row["id"]:
        raise AdoptionBlocked("unexpected commit message after interruption")
    if not _clean(repo):
        raise AdoptionBlocked("dirty checkout after interrupted commit")
    # Verify the exact reviewed candidate path set and contents after a crash.
    if not isinstance(row.get("_expected_path_hashes"), dict):
        raise AdoptionBlocked("missing durable expected outputs for interrupted commit")
    permitted = set(row["_expected_path_hashes"])
    actual = set(_git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").splitlines())
    if not actual or actual - permitted:
        raise AdoptionBlocked("interrupted commit changed undeclared paths")
    for name, digest in row["_expected_path_hashes"].items():
        file = _path(repo, name)
        content = file.read_bytes() if file.is_file() else None
        if (_sha(content) if content is not None else None) != digest:
            raise AdoptionBlocked(f"interrupted commit candidate differs: {name}")
    return {"state": "COMMITTED", "commit": cur["head"], "tree": cur["tree"],
            "reconciled": True}


def apply(manifest_path: Path, state_dir: Path, *, yes: bool = False) -> dict[str, Any]:
    if not yes:
        raise AdoptionBlocked("mutation requires --yes")
    source = _read_config(manifest_path)
    _checked_state_path(state_dir, source["repositories"])
    digest = _sha(_json(source))
    with _lock(state_dir):
        file = _journal(state_dir, digest)
        state = _read_journal(file, digest)
        for row in source["repositories"]:
            record = state["repositories"].get(row["id"])
            if record and record.get("state") == "COMMITTING":
                fixed = _reconcile_after_crash(Path(row["path"]), {
                    **row, "_expected_path_hashes": record.get("expected_path_hashes")})
                if fixed is not None:
                    record.update(fixed)
                    _save(file, state)
        # Global PREPARE BARRIER: no mutation until every checkout is independently admitted.
        preflight = plan(manifest_path, state_dir)
        if not preflight["safe_to_apply"]:
            raise AdoptionBlocked("global prepare blocked: " + "; ".join(
                f"{e['id']}={e['state']}:{e.get('reason','')}" for e in preflight["repositories"] if e["state"] not in ("FRESH", "APPLIED_UNCOMMITTED", "COMMITTED")))
        # Validate every generated composite artifact before changing the FIRST checkout.
        with tempfile.TemporaryDirectory(prefix="devtool-adoption-prepare-") as prepare_dir:
            for row, status in zip(source["repositories"], preflight["repositories"]):
                if status["state"] == "FRESH":
                    stage = Path(prepare_dir) / f"{row['id']}.zip"
                    contents, _ = _stage_zip(Path(row["path"]), row, stage)
                    if {k: (_sha(v) if v is not None else None) for k,v in contents.items()} != status["path_hashes"]:
                        raise AdoptionBlocked(f"{row['id']}: projection changed after global plan")
                    checked = check_overlay(Path(row["path"]), stage)
                    if not checked.ok:
                        raise AdoptionBlocked(f"{row['id']}: generated composite overlay preflight refused: {'; '.join(checked.messages[:2])}")
        state["overall"] = "APPLYING"
        state["source_lock"] = [{"id": i["id"], "identity": i.get("identity"),
                                "paths": i.get("path_hashes")} for i in preflight["repositories"]]
        _save(file, state)
        for row, status in zip(source["repositories"], preflight["repositories"]):
            if status["state"] == "COMMITTED":
                continue
            repo = Path(row["path"])
            before = _source(repo, row)
            if before["head"] != row["source_head"] or before["tree"] != row["source_tree"]:
                raise AdoptionBlocked(f"{row['id']}: checkout changed after prepare")
            state["repositories"][row["id"]] = {"state": "COMMITTING", "before": before,
                                                    "expected_path_hashes": status["path_hashes"]}
            _save(file, state)
            try:
                with tempfile.TemporaryDirectory(prefix="devtool-next05-") as temporary:
                    artifact = Path(temporary) / "adoption.zip"
                    desired, names = _stage_zip(repo, row, artifact)
                    if status["state"] == "FRESH":
                        # Leave execution, rollback and commit ownership in existing Devtool.
                        check = check_overlay(repo, artifact)
                        if not check.ok:
                            raise AdoptionBlocked("generated overlay failed Devtool native preflight")
                        p = subprocess.run([sys.executable, "-m", "devtool", "--no-hud", "-r", str(repo),
                                            "--yes", "apply-overlay", str(artifact),
                                            "--commit-mode", "new", "--commit", "devtool: adopt " + row["id"]],
                                           cwd=repo, capture_output=True, text=True, timeout=900)
                        if p.returncode:
                            raise AdoptionBlocked(f"native overlay refused (exit={p.returncode}); inspect its transaction journal: {(p.stderr or p.stdout)[-350:]}")
                    else:
                        if not _matches(repo, desired) or _changed_paths(repo) - set(names):
                            raise AdoptionBlocked("APPLIED_UNCOMMITTED paths changed after prepare")
                        _commit_partial(repo, names, "devtool: adopt " + row["id"])
                now = _source(repo, row)
                if now["head"] == before["head"] or _git(repo, "rev-list", "--parents", "-n", "1", "HEAD").split() != [now["head"], before["head"]]:
                    raise AdoptionBlocked("expected exactly one new commit")
                if not _clean(repo):
                    raise AdoptionBlocked("repository not clean after native apply")
                # Compare immutable prepared digests; never claim arbitrary commit as success.
                for name, digest_value in status["path_hashes"].items():
                    target = _path(repo, name)
                    value = target.read_bytes() if target.is_file() else None
                    if (_sha(value) if value is not None else None) != digest_value:
                        raise AdoptionBlocked(f"post-commit output mismatch: {name}")
                if row.get("wrapper"):
                    posix = repo / "devtoolw"
                    if not posix.is_file() or not os.access(posix, os.X_OK):
                        raise AdoptionBlocked("committed POSIX launcher lacks executable mode")
                    index = _git(repo, "ls-files", "--stage", "--", "devtoolw")
                    if not index.startswith("100755 "):
                        raise AdoptionBlocked("Git launcher mode is not executable")
                record = state["repositories"][row["id"]]
                record.update(state="COMMITTED", commit=now["head"], tree=now["tree"])
                _save(file, state)
            except Exception as exc:
                state["overall"] = "PARTIAL"
                state["failure"] = {"repository": row["id"], "reason": str(exc)}
                _save(file, state)
                raise AdoptionBlocked(f"partial batch: {row['id']} blocked; earlier per-repo commits retained: {exc}") from exc
        state["overall"] = "COMPLETE"
        state.pop("failure", None)
        _save(file, state)
        return {"schema": SCHEMA, "ok": True, "overall": "COMPLETE",
                "manifest_sha256": digest, "journal": str(file), "repositories": state["repositories"],
                "cross_repository_atomic": False, "formal_release_qualified": False}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Source-locked multi-repository Devtool adoption")
    p.add_argument("operation", choices=("plan", "apply", "resume", "status"))
    p.add_argument("manifest", type=Path)
    p.add_argument("--state-dir", type=Path, default=Path.home() / ".local/state/devtool/adoption-batches")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    try:
        if args.operation in ("apply", "resume"):
            result = apply(args.manifest, args.state_dir, yes=args.yes)
        else:
            result = plan(args.manifest, args.state_dir)
    except (AdoptionBlocked, OSError, ValueError, RuntimeError) as exc:
        result = {"schema": SCHEMA, "ok": False, "overall": "BLOCKED", "reason": str(exc),
                  "cross_repository_atomic": False, "formal_release_qualified": False}
    print(json.dumps(result, sort_keys=True, indent=2, ensure_ascii=False))
    return 0 if result.get("ok", result.get("safe_to_apply", False)) else 2


if __name__ == "__main__":
    raise SystemExit(main())
