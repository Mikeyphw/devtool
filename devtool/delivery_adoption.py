"""S7 P01: source-bound consumer adoption audit and Devtool overlay scaffold.

No consumer repository mutation, GitHub mutation, command execution from plans,
or release/CI qualification. This module generates data-only Devtool artifacts;
Devtool's existing transactional apply engine remains the sole executor.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
import zipfile
from typing import Any
from .launcher_validation_contract import inspect_delivery_plan, ValidationContractError

_SOURCE = re.compile(r"[0-9a-f]{40}\Z")
_REMOTE = re.compile(r"(?:git@github\.com:|ssh://git@github\.com/|https://github\.com/)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(?:\.git)?/?\Z")
_MAX_CHANGES = 32
_MAX_FILE = 1024 * 1024
_MAX_TOTAL = 4 * 1024 * 1024
_MAX_PLAN = 64 * 1024
_ALLOWED = {"add", "modify"}
_DUPLICATE_HINTS = ("gh pr create", "gh run watch", "gh run list", "git push", "workflow_dispatch", "gh workflow run")


class AdoptionError(RuntimeError):
    """Unsafe or unverified consumer source/overlay plan."""


def _git(root: Path, *args: str, optional: bool = False) -> str:
    p = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                       text=True, timeout=15, check=False)
    if p.returncode:
        if optional:
            return ""
        raise AdoptionError("Cannot corroborate consumer Git source identity")
    return p.stdout.strip()


def _root(value: Path) -> Path:
    if value.is_symlink():
        raise AdoptionError("Consumer repo root is a symlink")
    root = value.expanduser().resolve(strict=True)
    if not root.is_dir() or Path(_git(root, "rev-parse", "--show-toplevel")).resolve() != root:
        raise AdoptionError("Select the exact consumer Git worktree root")
    return root


def _identity(root: Path) -> dict[str, str]:
    sha = _git(root, "rev-parse", "HEAD")
    if not _SOURCE.fullmatch(sha):
        raise AdoptionError("Non-SHA-1 consumer HEAD is unsupported by S7 P01")
    branch = _git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    if not branch or _git(root, "check-ref-format", "--branch", branch) != branch:
        raise AdoptionError("Consumer must have a valid checked-out branch")
    remote = _git(root, "remote", "get-url", "origin")
    match = _REMOTE.fullmatch(remote)
    if not match:
        raise AdoptionError("Consumer origin must be a canonical GitHub repository")
    slug = match.group(1).removesuffix(".git")
    return {"source_sha": sha, "branch": branch, "repository": slug,
            "remote": "https://github.com/" + slug + ".git"}


def _relative(name: Any) -> str:
    if not isinstance(name, str) or not name or len(name) > 220 or "\\" in name or "\x00" in name:
        raise AdoptionError("Invalid overlay inventory path")
    p = PurePosixPath(name)
    if (p.is_absolute() or name != p.as_posix() or any(c in ("", ".", "..") for c in name.split("/"))
            or any(c == ".git" for c in p.parts) or p.name == ".devtool-artifact.json"):
        raise AdoptionError("Unsafe overlay inventory path")
    return name


def _no_symlink_components(root: Path, relative: str) -> Path:
    path = root
    for component in PurePosixPath(relative).parts:
        path = path / component
        if path.is_symlink():
            raise AdoptionError("Overlay path includes a symlink")
    return path


def _candidate_launchers(root: Path) -> tuple[list[str], list[str]]:
    """Bounded advisory only; no raw script content is retained or shown."""
    found: list[str] = []
    hints: list[str] = []
    inspected = 0
    for parent in (root / "scripts", root / "tools", root / ".github" / "scripts"):
        if parent.is_symlink() or not parent.is_dir():
            continue
        for script in sorted(parent.rglob("*")):
            inspected += 1
            if inspected > 250:
                break
            if script.is_symlink() or not script.is_file() or script.suffix not in (".sh", ".py", ".zsh"):
                continue
            if script.stat().st_size > 64 * 1024:
                continue
            relative = script.relative_to(root).as_posix()
            try:
                content = script.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            if any(needle in content.lower() for needle in _DUPLICATE_HINTS):
                found.append(relative)
                hints.append("Operator review recommended: possible duplicated delivery orchestration")
        if inspected > 250:
            break
    return found[:25], sorted(set(hints))


def audit_consumer(repo: Path) -> dict[str, Any]:
    """Read-only adoption assessment. No positive CI/release assertion."""
    root = _root(Path(repo))
    identity = _identity(root)
    dirty = bool(_git(root, "status", "--porcelain"))
    candidates, hints = _candidate_launchers(root)
    workflows = root / ".github" / "workflows"
    count = (len([f for f in workflows.iterdir() if f.is_file() and not f.is_symlink() and
                  f.suffix in (".yml", ".yaml")][:100]) if workflows.is_dir() and not workflows.is_symlink() else 0)
    return {"schema": "devtool.delivery-adoption-audit.v1", **identity,
            "worktree_clean": not dirty, "devtool_config_present": (root / ".devtool.toml").is_file(),
            "workflow_file_count_bounded": count, "candidate_duplicate_launchers": candidates,
            "advisory_hints": hints, "scaffolding_ready": not dirty,
            "ci_qualified": False, "release_qualified": False,
            "evidence_scope": "local-git-and-static-advisory-only"}


def _read_plan(path: Path) -> list[dict[str, str]]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_PLAN:
        raise AdoptionError("Plan must be a bounded regular JSON file")
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise AdoptionError("Invalid scaffold plan JSON") from exc
    if (not isinstance(obj, dict) or obj.get("schema_version") != 1
            or not {"schema_version", "changes"}.issubset(obj)):
        raise AdoptionError("Expected schema_version=1 and changes")
    try:
        inspect_delivery_plan(obj)
    except ValidationContractError as exc:
        raise AdoptionError(str(exc)) from exc
    changes = obj["changes"]
    if not isinstance(changes, list) or not 1 <= len(changes) <= _MAX_CHANGES:
        raise AdoptionError("Scaffold plan requires 1..32 changes")
    seen: set[str] = set()
    normalized = []
    for obj in changes:
        if not isinstance(obj, dict) or set(obj) != {"path", "action"}:
            raise AdoptionError("Plan change must have only path and action")
        name = _relative(obj["path"])
        action = obj["action"]
        if action not in _ALLOWED or name in seen:
            raise AdoptionError("Duplicate path or unsupported action")
        seen.add(name)
        normalized.append({"path": name, "action": action})
    return normalized



def _publish_exclusive(source: Path, target: Path) -> None:
    """Publish a checked ZIP without os.link() or overwriting an existing path.

    Termux Python builds may omit os.link. O_CREAT|O_EXCL provides the
    no-clobber invariant even when another process creates the output after
    our earlier check. Any ordinary failed write removes only the inode we
    created; an interrupted process may leave an incomplete output, which a
    later attempt must refuse rather than overwrite.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    fd = os.open(target, flags, 0o600)
    own = os.fstat(fd)
    try:
        with os.fdopen(fd, "wb") as output:
            with source.open("rb") as data:
                while True:
                    chunk = data.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        current = target.lstat()
        if not stat.S_ISREG(current.st_mode) or (current.st_dev, current.st_ino) != (own.st_dev, own.st_ino):
            raise AdoptionError("Scaffold output identity changed during publication")
        if hashlib.sha256(source.read_bytes()).digest() != hashlib.sha256(target.read_bytes()).digest():
            raise AdoptionError("Scaffold output failed post-write SHA-256 verification")
        if os.name == "posix":
            directory_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except BaseException:
        # If fdopen itself failed, the original descriptor still needs closing.
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            current = target.lstat()
            if (current.st_dev, current.st_ino) == (own.st_dev, own.st_ino):
                target.unlink()
        except FileNotFoundError:
            pass
        raise

def scaffold_overlay(repo: Path, payload: Path, plan: Path, output: Path, *,
                     expected_head: str) -> dict[str, Any]:
    """Create a data-only Devtool v2 overlay; no consumer source is modified."""
    root = _root(Path(repo))
    identity = _identity(root)
    if not _SOURCE.fullmatch(expected_head) or expected_head != identity["source_sha"]:
        raise AdoptionError("Expected source SHA differs from consumer HEAD")
    if _git(root, "status", "--porcelain"):
        raise AdoptionError("Consumer worktree must be clean before scaffolding")
    payload_path = Path(payload).expanduser()
    if payload_path.is_symlink():
        raise AdoptionError("Payload root cannot be a symlink")
    source = payload_path.resolve(strict=True)
    if not source.is_dir() or source == root or source.is_relative_to(root) or root.is_relative_to(source):
        raise AdoptionError("Payload directory must be separate from the consumer repository")
    changes = _read_plan(Path(plan).expanduser())
    try:
        plan_obj = json.loads(Path(plan).read_text(encoding="utf-8"))
        extension, hook_files = inspect_delivery_plan(plan_obj, source)
    except (ValidationContractError, OSError, UnicodeError, ValueError) as exc:
        raise AdoptionError("Invalid immutable validation contract: " + str(exc)) from exc
    target = Path(output).expanduser().absolute()
    if (target.is_relative_to(root) or target.is_relative_to(source) or target.exists()
            or target.is_symlink() or target.parent.is_symlink() or not target.parent.is_dir()):
        raise AdoptionError("Output must be a new regular path outside repo/payload")
    inventory = []
    data: dict[str, bytes] = {}
    total = 0
    for item in changes:
        name, action = item["path"], item["action"]
        original = _no_symlink_components(root, name)
        supplied = _no_symlink_components(source, name)
        if not supplied.is_file() or supplied.stat().st_size > _MAX_FILE or not stat.S_ISREG(supplied.stat().st_mode):
            raise AdoptionError("Payload input is missing, oversized, or non-regular")
        content = supplied.read_bytes()
        total += len(content)
        if total > _MAX_TOTAL:
            raise AdoptionError("Payload exceeds S7 byte budget")
        entry: dict[str, Any] = {"path": name, "action": action,
                                 "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
        tracked = bool(_git(root, "ls-files", "--error-unmatch", "--", name, optional=True))
        if action == "modify":
            if not tracked or not original.is_file() or original.is_symlink():
                raise AdoptionError("Modified path must be a tracked regular source")
            # Check the committed blob matches the source bytes, including mode.
            before = original.read_bytes()
            if hashlib.sha256(before).hexdigest() != hashlib.sha256(
                    subprocess.run(["git", "-C", str(root), "show", f"HEAD:{name}"],
                                   check=True, capture_output=True, timeout=15).stdout).hexdigest():
                raise AdoptionError("Tracked source bytes differ from HEAD")
            entry["source_sha256"] = hashlib.sha256(before).hexdigest()
        elif tracked or original.exists() or original.is_symlink():
            raise AdoptionError("Added path already exists or is tracked")
        inventory.append(entry)
        data[name] = content
    # Hooks are metadata-only, never tracked repository payload. The current
    # artifact engine owns their execution and has its own native hook receipts.
    for hook_path, hook_data in hook_files.items():
        if hook_path in data:
            raise AdoptionError("Hook path collides with source inventory")
        data[hook_path] = hook_data
    # One stable identifier for exact identity and contents. Do not pretend this
    # is a PR/CI/release qualification or automatically apply the artifact.
    binding_input = {"source": identity["source_sha"], "inventory": inventory}
    if extension:
        binding_input["validation_contract"] = extension
        binding_input["hook_metadata"] = [
            {"path": path, "sha256": hashlib.sha256(contents).hexdigest()}
            for path, contents in sorted(hook_files.items())]
    binding = hashlib.sha256(json.dumps(binding_input, sort_keys=True).encode()).hexdigest()[:20]
    # Devtool's canonical GIT-FRX-05 *exact-tree* contract prevents an archive
    # created today being silently applied to a different future HEAD.
    from .artifact_base_contract import build_base_contract
    base_contract = build_base_contract(root, inventory, mode="exact-tree")
    if base_contract["base_commit"] != expected_head:
        raise AdoptionError("Consumer HEAD changed during scaffold construction")
    manifest = {"schema_version": 2, "artifact_id": "DEVTOOL_S7_SCAFFOLD_" + binding,
                "name": "Devtool S7 source-bound consumer scaffold", "type": "overlay",
                "repository": {"remote": identity["remote"], "enforce_remote": True,
                               "branch": identity["branch"], "enforce_branch": True},
                "apply": {"atomic": True, "conflict_policy": "fail", "requires_clean_worktree": True},
                "inventory": {"format": 1, "entries": inventory},
                "validation": {"mode": "disabled", "restore": False, "build": False, "test": False},
                **({"notes": ["Explicit static-only validation: " + extension["delivery_static_justification"]]}
                   if "delivery_static_justification" in extension else {}),
                **{key: value for key, value in extension.items()
                   if key in ("validation", "preflight", "finalize")},
                **base_contract}
    data[".devtool-artifact.json"] = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    fd, temp_name = tempfile.mkstemp(prefix=".s7-scaffold-", suffix=".zip", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            with zipfile.ZipFile(f, mode="w", compression=zipfile.ZIP_DEFLATED) as z:
                for name in sorted(data):
                    info = zipfile.ZipInfo(name, (2026, 11, 14, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = 0o100644 << 16
                    z.writestr(info, data[name])
            f.flush()
            os.fsync(f.fileno())
        with zipfile.ZipFile(temp_name) as z:
            if z.testzip() or sorted(z.namelist()) != sorted(data):
                raise AdoptionError("Generated ZIP integrity check failed")
        if target.exists() or target.is_symlink():
            raise AdoptionError("Output appeared while scaffolding; refusing overwrite")
        os.chmod(temp_name, 0o600)
        _publish_exclusive(Path(temp_name), target)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return {"schema": "devtool.delivery-scaffold-receipt.v1", **identity,
            "output": str(target), "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "inventory": inventory, "source_bound": True, "applied": False,
            "remote_mutations": False, "ci_qualified": False, "release_qualified": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Devtool S7 consumer adoption (no repo writes)")
    subs = parser.add_subparsers(dest="command", required=True)
    a = subs.add_parser("audit", help="Read-only local adoption and duplicate launcher diagnostics")
    a.add_argument("--repo", required=True)
    a.add_argument("--json", action="store_true")
    g = subs.add_parser("scaffold", help="Generate, but never apply, a source-pinned Devtool v2 overlay")
    g.add_argument("--repo", required=True)
    g.add_argument("--payload", required=True)
    g.add_argument("--plan", required=True)
    g.add_argument("--output", required=True)
    g.add_argument("--expected-head", required=True)
    args = parser.parse_args(argv)
    try:
        result = (audit_consumer(Path(args.repo)) if args.command == "audit" else
                  scaffold_overlay(Path(args.repo), Path(args.payload), Path(args.plan),
                                   Path(args.output), expected_head=args.expected_head))
    except (AdoptionError, OSError, subprocess.SubprocessError) as exc:
        print("DEVTOOL_S7_REFUSED: " + str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
