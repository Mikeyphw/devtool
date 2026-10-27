from __future__ import annotations

"""RM-01: reviewed, typed output scopes for workflow scheduling.

This module only describes and safely provisions declared output roots. It is
not a sandbox for commands: declarations are scheduling/cleanup contracts and
must not be taken as permission to write elsewhere.
"""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import secrets
try:
    import fcntl
except ImportError:  # Windows: descriptor-lock recovery cannot be promised
    fcntl = None
from typing import Any, Mapping

from .reservations import repo_key
from .xdg import state_dir


class OutputScopeError(ValueError):
    pass


_KINDS = frozenset({"repository", "run-temp", "shared-cache", "external"})
_LIFETIMES = frozenset({"node", "run", "persistent"})
_CLEANUPS = frozenset({"none", "on-terminal"})
_HASH_POLICIES = frozenset({"never", "on-success", "always"})
_ALLOWED_FIELDS = frozenset({"kind", "path", "lifetime", "cleanup", "hash_retention", "reservation"})
_SLUG = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")


@dataclass(frozen=True, slots=True)
class OutputScope:
    kind: str
    path: str
    lifetime: str
    cleanup: str
    hash_retention: str
    reservation: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "path": self.path, "lifetime": self.lifetime,
                "cleanup": self.cleanup, "hash_retention": self.hash_retention,
                "reservation": self.reservation}


def _relative_path(value: str, *, context: str) -> str:
    if "\\" in value or "\x00" in value:
        raise OutputScopeError(f"{context}: backslash or NUL not allowed")
    path = PurePosixPath(value)
    if not value or path.is_absolute() or any(part in {"..", ".git"} for part in path.parts):
        raise OutputScopeError(f"{context}: path must be relative, non-traversing, outside .git")
    if path.as_posix() in {".", ""}:
        raise OutputScopeError(f"{context}: root path is not a permitted output")
    return path.as_posix()


def parse_output_scopes(raw: object, *, context: str) -> tuple[OutputScope, ...]:
    if raw in (None, ""):
        return ()
    if not isinstance(raw, list):
        raise OutputScopeError(f"{context}: expected an array of output-scope tables")
    parsed: list[OutputScope] = []
    for index, entry in enumerate(raw):
        at = f"{context}[{index}]"
        if not isinstance(entry, Mapping):
            raise OutputScopeError(f"{at}: expected table")
        unknown = set(entry) - _ALLOWED_FIELDS
        if unknown:
            raise OutputScopeError(f"{at}: unknown keys: {', '.join(sorted(unknown))}")
        kind = entry.get("kind")
        if not isinstance(kind, str) or kind not in _KINDS:
            raise OutputScopeError(f"{at}: kind must be repository, run-temp, shared-cache, or external")
        path = entry.get("path")
        if not isinstance(path, str) or not path.strip() or path != path.strip():
            raise OutputScopeError(f"{at}: path must be a nonempty, unpadded string")
        if kind == "external":
            if not Path(path).is_absolute() or ".." in Path(path).parts or "\x00" in path:
                raise OutputScopeError(f"{at}: external path requires a literal absolute path without traversal")
            if Path(path).anchor == path:
                raise OutputScopeError(f"{at}: cannot reserve the filesystem root")
        else:
            path = _relative_path(path, context=at)
        default_life = "run" if kind == "run-temp" else "persistent"
        lifetime = entry.get("lifetime", default_life)
        cleanup = entry.get("cleanup", "on-terminal" if kind == "run-temp" else "none")
        hash_retention = entry.get("hash_retention", "never")
        reservation = entry.get("reservation", "")
        if lifetime not in _LIFETIMES or cleanup not in _CLEANUPS or hash_retention not in _HASH_POLICIES:
            raise OutputScopeError(f"{at}: invalid lifetime, cleanup, or hash_retention")
        if kind == "run-temp":
            if lifetime != "run" or cleanup != "on-terminal":
                raise OutputScopeError(f"{at}: run-temp requires lifetime=run and cleanup=on-terminal")
        elif lifetime != "persistent" or cleanup != "none" or hash_retention != "never":
            raise OutputScopeError(f"{at}: persistent output needs lifetime=persistent, cleanup=none, hash_retention=never")
        if not isinstance(reservation, str) or (reservation and not _SLUG.fullmatch(reservation)):
            raise OutputScopeError(f"{at}: invalid shared resource reservation key")
        if kind == "external" and not reservation:
            raise OutputScopeError(f"{at}: external destinations require an explicit reservation key")
        if kind == "run-temp" and reservation:
            raise OutputScopeError(f"{at}: run-temp outputs are already isolated by node")
        parsed.append(OutputScope(kind, path, lifetime, cleanup, hash_retention, reservation))
    if len({(item.kind, item.path) for item in parsed}) != len(parsed):
        raise OutputScopeError(f"{context}: duplicate output scope")
    return tuple(parsed)


def _ensure_no_symlinks(path: Path, *, stop: Path | None = None) -> None:
    """Check existing components; keep root ownership checked at cleanup too."""
    current = path
    while True:
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            pass
        else:
            if stat.S_ISLNK(mode):
                raise OutputScopeError(f"output path contains symlink: {current}")
            if current != path and not stat.S_ISDIR(mode):
                raise OutputScopeError(f"output parent is not a directory: {current}")
        if current == current.parent or current == stop:
            return
        current = current.parent


def reservation_scope(scope: OutputScope, *, node_key: str, repo_root: Path, job_root: Path) -> str:
    """Return normalized, comparable logical path (not a real write path)."""
    if scope.kind == "repository":
        # Resolve is a validation check, never a permission to escape the repository.
        absolute = (job_root / scope.path)
        _ensure_no_symlinks(absolute, stop=repo_root)
        try:
            rel = absolute.resolve(strict=False).relative_to(repo_root.resolve())
        except ValueError as exc:
            raise OutputScopeError(f"repository output escapes worktree: {scope.path}") from exc
        return rel.as_posix()
    if scope.kind == "run-temp":
        node_hash = hashlib.sha256(node_key.encode()).hexdigest()[:24]
        return f"@run-temp/{node_hash}/{scope.path}"
    if scope.kind == "shared-cache":
        return f"@shared-cache/{repo_key(repo_root)}/{scope.path}"
    # Two external paths must compare conservatively even when job authors
    # gave distinct reservation tokens for their overlapping destinations.
    return "@external" + str(Path(scope.path))


# FC-01: operate exclusively on descriptors anchored at / and opened without
# following links. A successful pathname preflight is NOT sufficient authority
# for a later pathname open, hash, unlink or rmtree.
#
# Android/Termux can prohibit O_RDONLY on / and /data (even though the
# application is allowed to traverse them). Linux O_PATH opens a *search*
# descriptor without requesting directory read permission. Keep O_RDONLY on
# the final owned directory because os.listdir(fd) requires a readable fd.
_DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
_WALK_FLAGS = (getattr(os, "O_PATH", os.O_RDONLY) | getattr(os, "O_DIRECTORY", 0)
               | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
_FILE_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
_LEASES: dict[tuple[str, tuple[int, int]], tuple[int, Path, tuple[int, int]]] = {}


def _components(path: Path) -> tuple[str, ...]:
    absolute = path.absolute()
    if not absolute.is_absolute() or any(part in ("..", "") for part in absolute.parts[1:]):
        raise OutputScopeError("invalid anchored output location")
    return absolute.parts[1:]


def _open_dir(path: Path, *, create: bool = False) -> int:
    """Walk from / using a distinct dirfd for every component, never resolve()."""
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise OutputScopeError("safe anchored directory operations unsupported on this host")
    parts = _components(path)
    # Only the last directory is opened for reading; all ancestors are
    # traversal-only descriptors. No symlink is followed, even when a parent
    # is replaced during the walk. Non-Linux hosts without O_PATH retain the
    # original no-follow O_RDONLY behavior (and fail closed on permission errors).
    fd = os.open("/", _WALK_FLAGS if parts else _DIR_FLAGS)
    try:
        for index, part in enumerate(parts):
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            next_fd = os.open(part, _DIR_FLAGS if index == len(parts) - 1 else _WALK_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def _identity(fd: int) -> tuple[int, int]:
    st = os.fstat(fd)
    return st.st_dev, st.st_ino


def _entry_identity(parent_fd: int, name: str) -> tuple[int, int] | None:
    try:
        st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    return st.st_dev, st.st_ino


def _owned_recursive_remove(fd: int) -> None:
    """Delete only entries reached through pinned owned-directory descriptors."""
    for name in os.listdir(fd):
        if name in (".", ".."):
            continue
        before = os.stat(name, dir_fd=fd, follow_symlinks=False)
        stamp = (before.st_dev, before.st_ino)
        if stat.S_ISDIR(before.st_mode):
            child = os.open(name, _DIR_FLAGS, dir_fd=fd)
            try:
                if _identity(child) != stamp:
                    raise OutputScopeError("output child replaced before opening")
                _owned_recursive_remove(child)
                if _entry_identity(fd, name) != stamp:
                    raise OutputScopeError("output child replaced during cleanup")
                os.rmdir(name, dir_fd=fd)
            finally:
                os.close(child)
        elif stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode):
            # Unlinking a hardlink only removes the name in our owned root;
            # never open or read its external alias. Links are not followed.
            if _entry_identity(fd, name) != stamp:
                raise OutputScopeError("output file replaced during cleanup")
            os.unlink(name, dir_fd=fd)
        else:
            raise OutputScopeError("refusing special output file during cleanup")


def _journal_name(path: Path) -> str:
    return ".owner-" + path.name + ".json"


def _boot_id() -> str:
    try:
        value = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
        return value if re.fullmatch(r"[a-fA-F0-9-]{32,36}", value) else ""
    except OSError:
        return ""


def _journal_payload(path: Path, identity: tuple[int, int], repo_root: Path) -> dict[str, object]:
    return {"schema": "fc01-owned-output-v1", "node": path.name,
            "run": path.parent.name, "repo_key": repo_key(repo_root),
            "device": identity[0], "inode": identity[1],
            "pid": os.getpid(), "boot_id": _boot_id(),
            "nonce": secrets.token_hex(16)}


def _drop_lease(path: Path, identity: tuple[int, int], *, root_removed: bool) -> None:
    lease = _LEASES.pop((str(path), identity), None)
    if lease is None:
        return
    fd, parent_path, journal_id = lease
    try:
        if root_removed:
            parent_fd = _open_dir(parent_path)
            try:
                name = _journal_name(path)
                if _entry_identity(parent_fd, name) == journal_id:
                    os.unlink(name, dir_fd=parent_fd)
                    os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
    finally:
        os.close(fd)


def _lock_journal(fd: int, *, nonblocking: bool) -> bool:
    if fcntl is None:
        raise OutputScopeError("owned-output lock unavailable on this host")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0))
    except (BlockingIOError, OSError):
        return False
    return True


def provision_node_temp(repo_root: Path, run_id: str, node_key: str) -> tuple[Path, tuple[int, int]]:
    """Provision a private root and fsync a leased ownership journal."""
    if not run_id or "/" in run_id or "\\" in run_id or ".." in run_id:
        raise OutputScopeError("invalid run ID for temporary root")
    if fcntl is None or not hasattr(os, "O_NOFOLLOW"):
        raise OutputScopeError("safe owned-output lifecycle is unsupported on this host")
    node_hash = hashlib.sha256(node_key.encode()).hexdigest()[:24]
    run_hash = hashlib.sha256(run_id.encode()).hexdigest()[:24]
    base = state_dir() / "workflow-outputs" / repo_key(repo_root)
    root = base / run_hash / node_hash
    # The recovery pass cannot delete active roots (journal lock held) or
    # malformed/unregistered roots. It is safe to run before a new allocation.
    recover_orphaned_temps(repo_root, dry_run=False)
    parent_fd = _open_dir(root.parent, create=True)
    try:
        pst = os.fstat(parent_fd)
        if pst.st_uid != os.getuid() or pst.st_mode & 0o022:
            raise OutputScopeError("temporary output parent is not privately owned")
        try:
            os.mkdir(root.name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError as exc:
            raise OutputScopeError("owned temporary root already exists") from exc
        root_fd = os.open(root.name, _DIR_FLAGS, dir_fd=parent_fd)
        try:
            identity = _identity(root_fd)
            name = _journal_name(root)
            try:
                journal_fd = os.open(name, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=parent_fd)
            except BaseException:
                os.rmdir(root.name, dir_fd=parent_fd)
                raise
            try:
                if not _lock_journal(journal_fd, nonblocking=True):
                    raise OutputScopeError("ownership journal is already leased")
                encoded = (json.dumps(_journal_payload(root, identity, repo_root), sort_keys=True) + "\n").encode()
                os.write(journal_fd, encoded)
                os.fsync(journal_fd)
                os.fsync(parent_fd)
                _LEASES[(str(root), identity)] = (journal_fd, root.parent, _identity(journal_fd))
            except BaseException:
                os.close(journal_fd)
                if _entry_identity(parent_fd, name):
                    os.unlink(name, dir_fd=parent_fd)
                if _entry_identity(parent_fd, root.name) == identity:
                    os.rmdir(root.name, dir_fd=parent_fd)
                raise
        finally:
            os.close(root_fd)
        return root, identity
    finally:
        os.close(parent_fd)


def cleanup_owned_temp(path: Path, identity: tuple[int, int]) -> bool:
    """Delete only the originally owned inode through no-follow dirfds."""
    try:
        parent_fd = _open_dir(path.parent)
    except FileNotFoundError:
        _drop_lease(path, identity, root_removed=True)
        return True
    except (OSError, OutputScopeError):
        return False
    removed = False
    try:
        try:
            root_fd = os.open(path.name, _DIR_FLAGS, dir_fd=parent_fd)
        except FileNotFoundError:
            removed = True
            return True
        try:
            if _identity(root_fd) != identity:
                return False
            _owned_recursive_remove(root_fd)
            # Refuse a swapped name; never traverse or recursively delete it.
            if _entry_identity(parent_fd, path.name) != identity:
                return False
            os.rmdir(path.name, dir_fd=parent_fd)
            os.fsync(parent_fd)
            removed = True
            return True
        finally:
            os.close(root_fd)
    except (OSError, OutputScopeError):
        return False
    finally:
        os.close(parent_fd)
        # Retain the journal/lease on refusal; recovery must never treat this
        # still-active owner as an abandoned artifact.
        if removed:
            _drop_lease(path, identity, root_removed=True)


def hash_owned_output(temp_root: Path, relative: str, *, identity: tuple[int, int] | None = None) -> dict[str, object]:
    """Hash via no-follow dirfds pinned to the verified owned root."""
    path = _relative_path(relative, context="temporary artifact")
    if identity is None:
        known = [stamp for (root, stamp) in _LEASES if root == str(temp_root)]
        identity = known[0] if len(known) == 1 else None
    if identity is None:
        return {"path": relative, "status": "unavailable", "reason": "unknown_owner"}
    try:
        root_fd = _open_dir(temp_root)
        if _identity(root_fd) != identity:
            os.close(root_fd)
            return {"path": relative, "status": "unavailable", "reason": "root_replaced"}
    except (OSError, OutputScopeError) as exc:
        return {"path": relative, "status": "unavailable", "reason": type(exc).__name__}
    fd = root_fd
    try:
        parts = path.split("/")
        for part in parts[:-1]:
            new_fd = os.open(part, _DIR_FLAGS, dir_fd=fd)
            if fd != root_fd:
                os.close(fd)
            fd = new_fd
        name = parts[-1]
        file_fd = os.open(name, _FILE_FLAGS, dir_fd=fd)
        try:
            info = os.fstat(file_fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                return {"path": relative, "status": "unsupported", "reason": "not_private_regular_file"}
            baseline = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            sha = hashlib.sha256()
            while True:
                chunk = os.read(file_fd, 1024 * 1024)
                if not chunk:
                    break
                sha.update(chunk)
            after = os.fstat(file_fd)
            current = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            if current != baseline or _entry_identity(fd, name) != baseline[:2]:
                return {"path": relative, "status": "unavailable", "reason": "file_changed"}
            # A replaced root after traversal may not authorize a receipt.
            parent_fd = _open_dir(temp_root.parent)
            try:
                if _entry_identity(parent_fd, temp_root.name) != identity:
                    return {"path": relative, "status": "unavailable", "reason": "root_replaced"}
            finally:
                os.close(parent_fd)
            return {"path": relative, "status": "hashed", "sha256": sha.hexdigest(), "size": after.st_size}
        finally:
            os.close(file_fd)
    except (OSError, OutputScopeError) as exc:
        return {"path": relative, "status": "unavailable", "reason": type(exc).__name__}
    finally:
        if fd != root_fd:
            os.close(fd)
        os.close(root_fd)


def recover_orphaned_temps(repo_root: Path, *, dry_run: bool = True,
                         approve_same_boot: bool = False) -> list[dict[str, object]]:
    """Conservatively reconcile dead-owner journals; never adopt unknown roots.

    flock protects living owners including PID reuse. A dead supervisor does
    not prove its worker subprocesses have stopped: unattended reclamation
    therefore requires a recorded different boot identity. Same-boot cases
    require explicit operator approval (approve_same_boot=True). An absent
    boot identity remains unknown. Damaged/absent journals cannot authorize
    deletion. Unknown roots remain explicit orphan candidates.
    """
    base = state_dir() / "workflow-outputs" / repo_key(repo_root)
    try:
        base_fd = _open_dir(base)
    except FileNotFoundError:
        return []
    except (OSError, OutputScopeError):
        return [{"status": "refused", "reason": "unsafe_base"}]
    results: list[dict[str, object]] = []
    try:
        for run in os.listdir(base_fd):
            try:
                if not re.fullmatch(r"[a-f0-9]{24}", run):
                    continue
                run_fd = os.open(run, _DIR_FLAGS, dir_fd=base_fd)
            except OSError:
                results.append({"status": "refused", "reason": "unsafe_run", "run": run})
                continue
            try:
                recorded: set[str] = set()
                for journal in os.listdir(run_fd):
                    matched = re.fullmatch(r"\.owner-([a-f0-9]{24})\.json", journal)
                    if not matched:
                        continue
                    node = matched.group(1)
                    recorded.add(node)
                    try:
                        jfd = os.open(journal, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=run_fd)
                    except OSError:
                        results.append({"status": "refused", "run": run, "node": node, "reason": "unsafe_journal"})
                        continue
                    try:
                        js = os.fstat(jfd)
                        if not stat.S_ISREG(js.st_mode) or js.st_uid != os.getuid() or js.st_mode & 0o022:
                            results.append({"status": "refused", "run": run, "node": node, "reason": "journal_permissions"})
                            continue
                        if not _lock_journal(jfd, nonblocking=True):
                            results.append({"status": "preserved", "run": run, "node": node, "reason": "owner_active"})
                            continue
                        record = json.loads(os.read(jfd, 8192))
                        if not (isinstance(record, dict) and record.get("schema") == "fc01-owned-output-v1"
                                and record.get("node") == node and record.get("run") == run
                                and record.get("repo_key") == repo_key(repo_root)
                                and type(record.get("device")) is int and type(record.get("inode")) is int
                                and isinstance(record.get("nonce"), str) and len(record["nonce"]) == 32
                                and isinstance(record.get("boot_id"), str)):
                            results.append({"status": "refused", "run": run, "node": node, "reason": "invalid_journal"})
                            continue
                        stamp = (record["device"], record["inode"])
                        if _entry_identity(run_fd, node) != stamp:
                            results.append({"status": "refused", "run": run, "node": node, "reason": "inode_mismatch"})
                            continue
                        current_boot = _boot_id()
                        safe_after_reboot = bool(current_boot and record["boot_id"]
                                                 and current_boot != record["boot_id"])
                        if dry_run or not (safe_after_reboot or approve_same_boot):
                            results.append({"status": "orphan_candidate", "run": run, "node": node,
                                            "reason": "approval_required_same_or_unknown_boot"
                                            if not safe_after_reboot else "reviewed_reboot_candidate"})
                            continue
                        path = base / run / node
                        if cleanup_owned_temp(path, stamp):
                            if _entry_identity(run_fd, journal) == (js.st_dev, js.st_ino):
                                os.unlink(journal, dir_fd=run_fd)
                                os.fsync(run_fd)
                            results.append({"status": "recovered", "run": run, "node": node})
                        else:
                            results.append({"status": "refused", "run": run, "node": node, "reason": "cleanup_race"})
                    except (OSError, ValueError, TypeError, OutputScopeError):
                        results.append({"status": "refused", "run": run, "node": node, "reason": "invalid_journal"})
                    finally:
                        os.close(jfd)
                for name in os.listdir(run_fd):
                    if re.fullmatch(r"[a-f0-9]{24}", name) and name not in recorded:
                        results.append({"status": "orphan_candidate", "run": run, "node": name, "reason": "unregistered_root"})
            finally:
                os.close(run_fd)
    finally:
        os.close(base_fd)
    return results


def _recovery_cli() -> int:
    """Read-only by default, explicit approval for on-boot process descendants."""
    import argparse
    parser = argparse.ArgumentParser(description="Audit/reconcile FC-01 owned workflow output journals")
    parser.add_argument("--repo", required=True, type=Path, help="repository whose recorded roots are inspected")
    parser.add_argument("--recover", action="store_true", help="reclaim only verifiably stale records")
    parser.add_argument("--approve-same-boot", action="store_true", help="confirm worker descendants are no longer using orphan roots")
    args = parser.parse_args()
    if args.approve_same_boot and not args.recover:
        parser.error("--approve-same-boot requires --recover")
    result = recover_orphaned_temps(args.repo, dry_run=not args.recover,
                                   approve_same_boot=args.approve_same_boot)
    print(json.dumps({"schema": "fc01-recovery-receipt-v1", "repo": str(args.repo.absolute()),
                      "mode": "recover" if args.recover else "inspect", "results": result}, indent=2))
    return 1 if any(item["status"] == "refused" for item in result) else 0


if __name__ == "__main__":
    raise SystemExit(_recovery_cli())
