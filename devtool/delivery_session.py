"""Durable, source-bound delivery sessions.

A delivery session *observes* Devtool's existing artifact/Actions receipts. It
never applies overlays, pushes Git refs, creates PRs, or dispatches workflows.
Non-idempotent operations must be recorded before an authorized caller sends
anything and remain unresolved until independently reconciled.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import uuid
from typing import Any, Iterator, Mapping

_SCHEMA = "devtool.delivery-session.v1"
_HEX32 = re.compile(r"[0-9a-f]{32}\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_OPERATIONS = frozenset({"push", "pr_create", "dispatch", "rerun"})
_MAX_RECORD = 512 * 1024


class DeliverySessionError(RuntimeError):
    """Cannot prove the identity or safety of a delivery operation."""


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(obj: Mapping[str, Any]) -> str:
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _state_root(state_root: Path | None = None) -> Path:
    if state_root is not None:
        return Path(state_root).expanduser().absolute()
    home = Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state"))
    return home.expanduser().absolute() / "devtool" / "delivery" / "sessions"


def _directory(path: Path, *, create: bool) -> None:
    # Reject symlinks in Devtool-owned state components. Do not reject system
    # ancestors: Termux commonly reaches HOME through /data/data -> /data/user/0.
    for component in (path, path.parent, path.parent.parent):
        if component.is_symlink():
            raise DeliverySessionError("Delivery state path contains a symlink")
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir():
        raise DeliverySessionError("Delivery state directory is unavailable")
    if os.name == "posix" and path.stat().st_mode & 0o077:
        # Refuse rather than silently altering a shared state directory.
        raise DeliverySessionError("Delivery state directory must be private (0700)")


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    _directory(path, create=True)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    lock_path = path / ".delivery.lock"
    if lock_path.is_symlink():
        raise DeliverySessionError("Delivery lock is a symlink")
    fd = os.open(lock_path, flags, 0o600)
    try:
        if os.name == "nt":
            import msvcrt
            os.write(fd, b"0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _path(state_root: Path, identity: str) -> Path:
    if not isinstance(identity, str) or not _HEX32.fullmatch(identity):
        raise DeliverySessionError("Delivery ID must contain exactly 32 lowercase hex characters")
    return state_root / (identity + ".json")


def _write(path: Path, record: dict[str, Any]) -> None:
    if path.is_symlink():
        raise DeliverySessionError("Delivery journal is a symlink")
    document = dict(record)
    document.pop("integrity_sha256", None)
    document["integrity_sha256"] = _digest(document)
    raw = (json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    if len(raw) > _MAX_RECORD:
        raise DeliverySessionError("Delivery journal exceeds the maximum size")
    fd, temp = tempfile.mkstemp(prefix=".delivery-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            if hasattr(os, "fchmod"):
                os.fchmod(stream.fileno(), 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        if os.name == "posix":
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _read(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise DeliverySessionError("Delivery journal is a symlink")
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            meta = os.fstat(stream.fileno())
            if not stat.S_ISREG(meta.st_mode) or meta.st_size > _MAX_RECORD:
                raise DeliverySessionError("Delivery journal is not a bounded regular file")
            if os.name == "posix" and meta.st_mode & 0o077:
                raise DeliverySessionError("Delivery journal permissions are not private")
            raw = json.loads(stream.read(_MAX_RECORD + 1).decode("utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise DeliverySessionError("Delivery journal missing or corrupted") from exc
    if not isinstance(raw, dict) or raw.get("schema") != _SCHEMA or raw.get("id") != path.stem:
        raise DeliverySessionError("Delivery journal schema or identity mismatch")
    checksum = raw.get("integrity_sha256")
    body = dict(raw)
    body.pop("integrity_sha256", None)
    if not isinstance(checksum, str) or checksum != _digest(body):
        raise DeliverySessionError("Delivery journal integrity mismatch")
    if not _SHA.fullmatch(str(raw.get("head_sha") or "")):
        raise DeliverySessionError("Delivery journal has invalid source SHA")
    if not isinstance(raw.get("intents"), list) or not isinstance(raw.get("events"), list):
        raise DeliverySessionError("Delivery journal contains invalid event collections")
    return raw


def _git(root: Path, *args: str, optional: bool = False) -> str:
    try:
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                                text=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise DeliverySessionError("Cannot inspect Git repository") from exc
    if result.returncode:
        if optional:
            return ""
        raise DeliverySessionError(f"Cannot inspect Git repository ({' '.join(args)})")
    return result.stdout.strip()


def _identity(repo: Path, *, head: str | None, base: str) -> dict[str, Any]:
    root = Path(repo).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise DeliverySessionError("Repository path is not a directory")
    actual = Path(_git(root, "rev-parse", "--show-toplevel")).resolve(strict=True)
    if actual != root:
        raise DeliverySessionError("Select the Git worktree root (not a nested directory)")
    source = _git(root, "rev-parse", "HEAD").lower()
    if not _SHA.fullmatch(source):
        raise DeliverySessionError("Current Git commit is not a full SHA-1 identity")
    selected = head or _git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    if not selected or _git(root, "check-ref-format", "--branch", selected, optional=True) != selected:
        raise DeliverySessionError("Head must be the current named Git branch")
    if _git(root, "symbolic-ref", "--quiet", "--short", "HEAD", optional=True) != selected:
        raise DeliverySessionError("Selected head differs from checked out branch")
    if not base or _git(root, "check-ref-format", "--branch", base, optional=True) != base:
        raise DeliverySessionError("Invalid target base branch")
    remote = _git(root, "remote", "get-url", "origin", optional=True)
    slug = None
    if remote:
        found = re.fullmatch(r"(?:git@github\.com:|ssh://git@github\.com/|https://github\.com/)([^\s]+?)(?:\.git)?/?", remote)
        if found and _REPO.fullmatch(found.group(1)):
            slug = found.group(1)
    return {"repo_root": str(root), "git_dir": _git(root, "rev-parse", "--absolute-git-dir"),
            "repository": slug, "head": selected, "base": base, "head_sha": source}


def plan(repo: Path, *, base: str = "main", head: str | None = None,
         overlay: Path | None = None) -> dict[str, Any]:
    """Read-only source inspection. Does not create a journal or mutate Git."""
    result = _identity(Path(repo), head=head, base=base)
    result.update({"schema": "devtool.delivery-plan.v1", "stage": "PLANNED",
                   "remote_verified": False, "release_qualified": False})
    if overlay is not None:
        supplied = Path(overlay).expanduser()
        if supplied.is_symlink():
            raise DeliverySessionError("Overlay must not be a symlink")
        path = supplied.resolve(strict=True)
        if not path.is_file():
            raise DeliverySessionError("Overlay must be a regular file")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for part in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(part)
        result["overlay"] = {"path": str(path), "sha256": digest.hexdigest(),
                             "size": path.stat().st_size}
    else:
        result["overlay"] = None
    return result


def start(repo: Path, *, base: str = "main", head: str | None = None,
          overlay: Path | None = None, state_root: Path | None = None) -> dict[str, Any]:
    """Create a PLANNED session only. No overlay or remote operation executes."""
    inspected = plan(repo, base=base, head=head, overlay=overlay)
    folder = _state_root(state_root)
    with _locked(folder):
        identity = uuid.uuid4().hex
        path = _path(folder, identity)
        if path.exists() or path.is_symlink():
            raise DeliverySessionError("Delivery session ID collision")
        now = _utc()
        record = {"schema": _SCHEMA, "id": identity, "created_at": now, "updated_at": now,
                  "stage": "PLANNED", "repo_root": inspected["repo_root"],
                  "git_dir": inspected["git_dir"], "repository": inspected["repository"],
                  "head": inspected["head"], "base": inspected["base"],
                  "head_sha": inspected["head_sha"], "overlay": inspected["overlay"],
                  "intents": [], "events": [{"kind": "session_started", "at": now, "stage": "PLANNED"}],
                  "remote_verified": False, "release_qualified": False}
        _write(path, record)
    return _public(record)


def _public(record: Mapping[str, Any]) -> dict[str, Any]:
    # Do not advertise success until relevant remote evidence has been verified.
    return {key: record[key] for key in ("schema", "id", "stage", "repo_root", "repository",
        "head", "base", "head_sha", "overlay", "created_at", "updated_at",
        "remote_verified", "release_qualified")} | {
        key: record[key] for key in ("pr", "ci") if key in record
    }


def status(identity: str, *, state_root: Path | None = None, inspect_git: bool = True) -> dict[str, Any]:
    folder = _state_root(state_root)
    _directory(folder, create=False)
    record = _read(_path(folder, identity))
    result = _public(record)
    result["intents"] = [{"id": x["id"], "operation": x["operation"], "state": x["state"]}
                         for x in record["intents"]]
    result["events_count"] = len(record["events"])
    result["resumable"] = all(x["state"] == "confirmed" for x in record["intents"])
    if inspect_git:
        try:
            latest = _identity(Path(record["repo_root"]), head=record["head"], base=record["base"])
            result["source_binding"] = "matching" if (latest["head_sha"] == record["head_sha"]
                and latest["git_dir"] == record["git_dir"]
                and latest["repository"] == record["repository"]) else "drifted"
        except (DeliverySessionError, OSError):
            result["source_binding"] = "unavailable"
        result["resumable"] = bool(result["resumable"] and result["source_binding"] == "matching")
    return result


def resume(identity: str, *, state_root: Path | None = None) -> dict[str, Any]:
    """Resume inspection, not an implicit retry of a remote or local mutation."""
    result = status(identity, state_root=state_root)
    if not result["resumable"]:
        result["next_action"] = "Review source drift or unresolved intents; no operation was retried"
        result["disposition"] = "BLOCKED_RECONCILIATION"
    else:
        result["next_action"] = "Proceed through an explicitly authorized delivery coordinator stage"
        result["disposition"] = "READY_FOR_EXPLICIT_STAGE"
    result["operation_replayed"] = False
    return result


def record_intent(identity: str, operation: str, key: str, *, approved: bool,
                  state_root: Path | None = None) -> dict[str, Any]:
    """Record an authorized non-idempotent intent *before* a remote mutation.

    A repeat returns the unresolved original, never a new permission to retry.
    """
    if operation not in _OPERATIONS or not isinstance(key, str) or not 1 <= len(key) <= 160:
        raise DeliverySessionError("Invalid delivery operation identity")
    # Persist a digest, not a possibly sensitive API request or URL.
    key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
    if not approved:
        raise DeliverySessionError("Explicit authorization required before remote mutation intent")
    folder = _state_root(state_root)
    with _locked(folder):
        path = _path(folder, identity)
        record = _read(path)
        current = _identity(Path(record["repo_root"]), head=record["head"], base=record["base"])
        if (current["head_sha"] != record["head_sha"] or
            current["git_dir"] != record["git_dir"] or
            current["repository"] != record["repository"]):
            raise DeliverySessionError("Source drift: refusing to create a mutation intent")
        for item in record["intents"]:
            if item["operation"] == operation and item["key_sha256"] == key_hash:
                return {"intent_id": item["id"], "state": item["state"], "new": False,
                        "safe_to_replay": False}
            if item["state"] != "confirmed":
                raise DeliverySessionError("Unresolved mutation intent requires reconciliation before another mutation")
        at = _utc()
        intent = {"id": uuid.uuid4().hex, "operation": operation, "key_sha256": key_hash,
                  "state": "unresolved", "created_at": at}
        record["intents"].append(intent)
        record["events"].append({"kind": "mutation_intent", "operation": operation,
                                 "intent_id": intent["id"], "at": at})
        record["updated_at"] = at
        _write(path, record)
    return {"intent_id": intent["id"], "state": "unresolved", "new": True,
            "safe_to_replay": False}


def report(identity: str, *, state_root: Path | None = None) -> dict[str, Any]:
    result = status(identity, state_root=state_root)
    ci = result.get('ci') or {}
    result['result'] = ('required_checks_passed' if ci.get('checks_qualified') else
                        'pending' if result['resumable'] else 'requires_reconciliation')
    result['delivery_completed'] = bool(result.get('pr') and ci.get('checks_qualified'))
    result['release_qualified'] = False
    result['message'] = ('Local and remote evidence only; formal release qualification not performed')
    return result


def confirm_intent(identity: str, operation: str, key: str,
                   evidence: Mapping[str, Any], *, state_root: Path | None = None,
                   optional: bool = False) -> dict[str, Any]:
    """Resolve a previously journaled mutation using independently observed proof.

    The caller must read the external provider *after* a possibly uncertain
    operation; this function never performs the mutation and never authorizes a
    replay. An absent intent is acceptable only for pure read-only discovery.
    """
    if operation not in _OPERATIONS or not isinstance(key, str):
        raise DeliverySessionError('Invalid mutation intent reconciliation')
    proof = dict(evidence)
    folder = _state_root(state_root)
    with _locked(folder):
        path = _path(folder, identity)
        record = _read(path)
        if proof.get('head_sha') != record['head_sha']:
            raise DeliverySessionError('Mutation evidence source SHA does not match session')
        if operation == 'pr_create':
            value = proof.get('pr_number')
            if type(value) is not int or value < 1:
                raise DeliverySessionError('PR evidence requires an exact PR number')
        key_hash = hashlib.sha256(key.encode('utf-8')).hexdigest()
        items = [x for x in record['intents'] if x['operation'] == operation and x['key_sha256'] == key_hash]
        if not items:
            if optional:
                return {'state':'not-recorded','confirmed':False}
            raise DeliverySessionError('Mutation intent was never recorded')
        if len(items) != 1:
            raise DeliverySessionError('Duplicate mutation journal entries')
        found = items[0]
        if found['state'] == 'confirmed':
            if found.get('evidence') != proof:
                raise DeliverySessionError('Conflicting mutation reconciliation evidence')
            return {'state':'confirmed','confirmed':True,'reconciled':False}
        if found['state'] != 'unresolved':
            raise DeliverySessionError('Mutation intent state is invalid')
        found['state'] = 'confirmed'
        found['evidence'] = proof
        found['confirmed_at'] = _utc()
        record['updated_at'] = _utc()
        record['events'].append({'kind':'mutation_confirmed','at':record['updated_at'],
                                 'operation':operation, 'intent_id':found['id']})
        _write(path,record)
        return {'state':'confirmed','confirmed':True,'reconciled':True}


def record_pr_observation(identity: str, observation: Mapping[str, Any], *,
                          state_root: Path | None = None) -> dict[str, Any]:
    """Persist independently verified PR identity; never a POST intent."""
    pr = dict(observation)
    if type(pr.get('number')) is not int or pr['number'] <= 0:
        raise DeliverySessionError('Invalid PR observation identity')
    if not isinstance(pr.get('url'), str) or len(pr['url']) > 300:
        raise DeliverySessionError('Invalid PR observation URL')
    folder = _state_root(state_root)
    with _locked(folder):
        path = _path(folder, identity)
        record = _read(path)
        if any(pr.get(field) != record[field] for field in ('head_sha','head','base')):
            raise DeliverySessionError('PR observation source, head or base differs from session')
        expected = f"https://github.com/{record['repository']}/pull/{pr['number']}"
        if pr['url'] != expected:
            raise DeliverySessionError('PR URL does not belong to session repository')
        prior = record.get('pr')
        if prior is not None and prior['number'] != pr['number']:
            raise DeliverySessionError('PR observation conflicts with bound PR number')
        record['pr'] = {k:pr.get(k) for k in ('number','head','base','head_sha','url','merge_commit_sha')}
        record['updated_at'] = _utc()
        if prior != record['pr']:
            record['events'].append({'kind':'pr_observed','at':record['updated_at'],
                                     'number':pr['number']})
        _write(path,record)
    return dict(record['pr'])


def record_ci_observation(identity: str, observation: Mapping[str, Any], *,
                          state_root: Path | None = None) -> dict[str, Any]:
    """Checkpoint a bounded, source-bound CI snapshot; never invent success."""
    obs = dict(observation)
    if obs.get('state') not in {'OBSERVED_ONLY','WAITING_REGISTRATION','CHECKS_RUNNING',
                                 'CHECKS_FAILED','REQUIRED_CHECKS_PASSED','BLOCKED_AMBIGUOUS'}:
        raise DeliverySessionError('Unknown CI observation state')
    if not isinstance(obs.get('runs'), list) or len(obs['runs']) > 100:
        raise DeliverySessionError('Invalid bounded CI run inventory')
    if not isinstance(obs.get('required'), list) or len(obs['required']) > 25:
        raise DeliverySessionError('Invalid required check declaration')
    if bool(obs.get('checks_qualified')) != (obs['state'] == 'REQUIRED_CHECKS_PASSED'):
        raise DeliverySessionError('CI qualification state is inconsistent')
    if len(json.dumps(obs, ensure_ascii=False)) > 140_000:
        raise DeliverySessionError('CI observation exceeds allowed budget')
    folder = _state_root(state_root)
    with _locked(folder):
        path = _path(folder, identity)
        record = _read(path)
        if obs.get('source_sha') != record['head_sha']:
            raise DeliverySessionError('CI observation source SHA differs from session')
        record['ci'] = obs
        record['updated_at'] = _utc()
        record['events'].append({'kind':'ci_observed','at':record['updated_at'],
                                 'state':obs['state'],'runs':len(obs['runs'])})
        if len(record['events']) > 500:
            # Observational progress is expendable; preserve durable mutation
            # intent/confirmation events and the most recent CI observations.
            critical = [event for event in record['events']
                        if event.get('kind') != 'ci_observed']
            recent = [event for event in record['events']
                      if event.get('kind') == 'ci_observed'][-100:]
            record['events'] = sorted([*critical,*recent],key=lambda x:x['at'])
        _write(path,record)
    return {'state':obs['state'],'checks_qualified':obs['checks_qualified']}
