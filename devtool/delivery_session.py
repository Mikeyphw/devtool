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


def _identity(repo: Path, *, head: str | None, base: str,
              base_repository: str | None = None,
              head_repository: str | None = None,
              base_sha: str | None = None) -> dict[str, Any]:
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
    # The checked-out origin is authoritative for the *head*. A caller may
    # designate a distinct upstream *base*, but cannot silently substitute a
    # different fork or change the Git remote this session can push.
    h_repo = head_repository or slug
    b_repo = base_repository or slug
    if not slug or not _REPO.fullmatch(slug):
        raise DeliverySessionError("Delivery requires a verified GitHub origin")
    if (not isinstance(h_repo, str) or not _REPO.fullmatch(h_repo) or
            h_repo.casefold() != slug.casefold()):
        raise DeliverySessionError("Head repository must match the checked-out Git origin")
    if not isinstance(b_repo, str) or not _REPO.fullmatch(b_repo):
        raise DeliverySessionError("Base repository must be an explicit valid GitHub slug")
    if base_sha is not None and (not isinstance(base_sha,str) or not _SHA.fullmatch(base_sha)):
        raise DeliverySessionError("Pinned base commit must be a full SHA-1 identity")
    return {"repo_root": str(root), "git_dir": _git(root, "rev-parse", "--absolute-git-dir"),
            "repository": slug, "head_repository": h_repo, "base_repository": b_repo,
            "base_sha": base_sha, "head": selected, "base": base, "head_sha": source}


def plan(repo: Path, *, base: str = "main", head: str | None = None,
         overlay: Path | None = None, base_repository: str | None = None,
         head_repository: str | None = None, base_sha: str | None = None) -> dict[str, Any]:
    """Read-only source inspection. Does not create a journal or mutate Git."""
    result = _identity(Path(repo), head=head, base=base,
                       base_repository=base_repository, head_repository=head_repository,
                       base_sha=base_sha)
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
          overlay: Path | None = None, state_root: Path | None = None,
          base_repository: str | None = None, head_repository: str | None = None,
          base_sha: str | None = None) -> dict[str, Any]:
    """Create a PLANNED session only. No overlay or remote operation executes."""
    inspected = plan(repo, base=base, head=head, overlay=overlay,
                     base_repository=base_repository, head_repository=head_repository,
                     base_sha=base_sha)
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
                  "head_repository": inspected["head_repository"],
                  "base_repository": inspected["base_repository"],
                  "base_sha": inspected["base_sha"],
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
        "head_repository": record.get("head_repository", record["repository"]),
        "base_repository": record.get("base_repository", record["repository"]),
        "base_sha": record.get("base_sha"),
    } | {
        key: record[key] for key in ("pr", "ci", "native_apply") if key in record
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
    from .delivery_receipt import build_stream
    try:
        result["event_stream"] = build_stream(record)
    except ValueError as exc:
        raise DeliverySessionError("Delivery event stream invalid") from exc
    return result


def resume(identity: str, *, state_root: Path | None = None) -> dict[str, Any]:
    """Resume inspection, not an implicit retry of a remote or local mutation."""
    result = status(identity, state_root=state_root)
    if not result["resumable"]:
        result["next_action"] = "Review source drift or unresolved intents; no operation was retried"
        result["disposition"] = "BLOCKED_RECONCILIATION"
    else:
        result["next_action"] = (
            "Native commit already adopted; use delivery session push/pr/ci, never reapply"
            if result.get('native_apply', {}).get('state') == 'COMMITTED'
            else "Proceed through an explicitly authorized delivery coordinator stage")
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
    safe_ci = bool(result['resumable'] and ci.get('checks_qualified'))
    result['result'] = ('required_checks_passed' if safe_ci else
                        'pending' if result['resumable'] else 'requires_reconciliation')
    result['delivery_completed'] = bool(result.get('pr') and safe_ci)
    result['release_qualified'] = False
    result['message'] = ('Local and remote evidence only; formal release qualification not performed')
    from .delivery_receipt import build_receipt, render_human
    result['receipt'] = build_receipt(result)
    result['human_report'] = render_human(result['receipt'])
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
        base_repo = record.get('base_repository', record['repository'])
        head_repo = record.get('head_repository', record['repository'])
        if (pr.get('base_repository', base_repo).casefold() != base_repo.casefold() or
                pr.get('head_repository', head_repo).casefold() != head_repo.casefold()):
            raise DeliverySessionError('PR observation belongs to an unexpected fork or base')
        if record.get('base_sha') and pr.get('base_sha') != record['base_sha']:
            raise DeliverySessionError('PR base branch has drifted from pinned SHA')
        expected = f"https://github.com/{base_repo}/pull/{pr['number']}"
        if pr['url'] != expected:
            raise DeliverySessionError('PR URL does not belong to session repository')
        prior = record.get('pr')
        if prior is not None and prior['number'] != pr['number']:
            raise DeliverySessionError('PR observation conflicts with bound PR number')
        record['pr'] = {k:pr.get(k) for k in ('number','head','base','head_sha','url','merge_commit_sha')}
        record['pr']['head_repository'] = head_repo
        record['pr']['base_repository'] = base_repo
        record['pr']['base_sha'] = pr.get('base_sha')
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
                                 'CHECKS_FAILED','REQUIRED_CHECKS_PASSED','BLOCKED_AMBIGUOUS',
                                     'CHECKS_EVIDENCE_PENDING','REGISTRATION_TIMED_OUT',
                                     'BLOCKED_CONDITIONAL','BLOCKED_TRIGGER','DISPATCH_UNCORRELATED',
                                     'BRANCH_RULES_UNVERIFIED','REGISTRATION_PROVIDER_UNAVAILABLE'}:
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



def adopt_applied(repo: Path, overlay: Path, *, base: str = 'main',
                  head: str | None = None, state_root: Path | None = None,
                  base_repository: str | None = None,
                  head_repository: str | None = None,
                  base_sha: str | None = None) -> dict[str, Any]:
    """Idempotently adopt a *committed and validated* native artifact receipt.

    Native apply may clean its source archive after commit, so the durable
    Devtool history is authoritative; an existing archive is rehashed, and a
    removed one is *not* silently regenerated. This never applies anything or
    mutates a GitHub remote.
    """
    from .artifacts import (load_history, artifact_inventory_entries,
                            artifact_preflight_write_paths, artifact_validation_write_paths,
                            artifact_finalize_write_paths)

    inspected = _identity(Path(repo), head=head, base=base,
                          base_repository=base_repository,head_repository=head_repository,
                          base_sha=base_sha)
    root = Path(inspected['repo_root'])
    supplied = Path(overlay).expanduser()
    if supplied.is_symlink():
        raise DeliverySessionError('Applied artifact path is a symlink')
    artifact_path = str(supplied.resolve(strict=False))
    if not inspected['repository']:
        raise DeliverySessionError('Applied overlay adoption requires a verified GitHub origin')
    if _git(root, 'status', '--porcelain'):
        raise DeliverySessionError('Applied overlay adoption requires a clean checkout')

    historical = [item for item in load_history(root)
                  if item.get('commit_hash') == inspected['head_sha']
                  and item.get('artifact') == artifact_path]
    qualified = [item for item in historical
                 if str(item.get('status') or '').lower() in {'ok', 'applied', 'success', 'succeeded'}
                 and str(item.get('validation_status') or '').lower() in {'ok', 'passed', 'success', 'succeeded'}
                 and isinstance(item.get('manifest'), dict)
                 and isinstance(item.get('sha256'), str)
                 and re.fullmatch(r'[0-9a-f]{64}', item['sha256'])]
    if len(historical) != 1 or len(qualified) != 1:
        raise DeliverySessionError('Unique qualified native applied-commit history receipt not found')
    receipt = qualified[0]
    manifest = receipt['manifest']
    digest = receipt['sha256']
    tx = receipt.get('transaction')
    if not isinstance(tx, dict) or tx.get('status') != 'completed' or not tx.get('id'):
        raise DeliverySessionError('Applied native history lacks completed transaction proof')
    validation = manifest.get('validation') if isinstance(manifest.get('validation'), dict) else {}
    outcomes = (receipt.get('validation_plan') or {}).get('validation_step_outcomes')
    if validation.get('required') is True:
        if (not isinstance(outcomes, list) or not outcomes or any(
            not isinstance(item, dict) or item.get('skipped') is True
            or item.get('outcome') != 'passed' or item.get('returncode') != 0
            for item in outcomes)):
            raise DeliverySessionError('Required native validation must have passing, unskipped step evidence')
    native_apply = {
        'state': 'COMMITTED', 'commit': inspected['head_sha'],
        'history_id': str(receipt.get('history_id') or ''),
        'operation_id': str(receipt.get('operation_id') or ''),
        'transaction_id': str(tx['id']), 'transaction_status': tx['status'],
        'validation_status': str(receipt['validation_status']).lower(),
        'source_tree': str(manifest.get('base_tree') or ''),
        'artifact_sha256': digest,
    }
    if supplied.exists():
        if not supplied.is_file():
            raise DeliverySessionError('Applied artifact is not a regular file')
        stored = hashlib.sha256(supplied.read_bytes()).hexdigest()
        if stored != digest:
            raise DeliverySessionError('Remaining artifact does not match committed native history')
    inspected['overlay'] = {'path': artifact_path, 'sha256': digest,
                            'size': (receipt.get('content_identity') or {}).get('size')}
    old_commit = str(manifest.get('base_commit') or '').lower()
    if not _SHA.fullmatch(old_commit):
        raise DeliverySessionError('Applied overlay requires a source-bound base commit')
    if _git(root, 'rev-parse', 'HEAD^').lower() != old_commit:
        raise DeliverySessionError('Applied overlay commit parent does not match source binding')
    old_tree = str(manifest.get('base_tree') or '').lower()
    if _SHA.fullmatch(old_tree) and _git(root, 'rev-parse', 'HEAD~1^{tree}') != old_tree:
        raise DeliverySessionError('Applied overlay base tree mismatch')
    expected = manifest.get('expected_post_sha256')
    entries = artifact_inventory_entries(manifest)
    if not isinstance(expected, dict) or not entries:
        raise DeliverySessionError('Applied overlay requires exact committed payload inventory')
    allowed_mutations = {
        str(item.get('path') or '') for item in entries if isinstance(item, dict)
    }
    allowed_mutations.update(artifact_preflight_write_paths(manifest))
    allowed_mutations.update(artifact_validation_write_paths(manifest))
    allowed_mutations.update(artifact_finalize_write_paths(manifest))
    actual_mutations = set(_git(root, 'diff-tree', '--no-commit-id', '--name-only', '-r', 'HEAD').splitlines())
    if not actual_mutations or actual_mutations - allowed_mutations:
        raise DeliverySessionError('Applied commit contains unverified or empty mutation inventory')
    for row in entries:
        if not isinstance(row, dict):
            raise DeliverySessionError('Applied overlay inventory shape invalid')
        path = str(row.get('path') or '')
        action = str(row.get('action') or '').lower()
        if not path or path.startswith('/') or '..' in path.split('/') or '\\' in path:
            raise DeliverySessionError('Applied overlay inventory path invalid')
        if action == 'delete':
            present = subprocess.run(['git', '-C', str(root), 'cat-file', '-e', f'HEAD:{path}'],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
            if present.returncode == 0:
                raise DeliverySessionError(f'Applied deletion not present in committed tree: {path}')
            continue
        digest_post = expected.get(path)
        if not isinstance(digest_post, str) or not re.fullmatch(r'[0-9a-f]{64}', digest_post):
            raise DeliverySessionError(f'Applied expected payload hash missing: {path}')
        blob = subprocess.run(['git', '-C', str(root), 'show', f'HEAD:{path}'],
                              capture_output=True, timeout=15)
        if blob.returncode != 0 or hashlib.sha256(blob.stdout).hexdigest() != digest_post:
            raise DeliverySessionError(f'Applied committed payload does not match overlay: {path}')

    # M3 extends *only* v3 receipts with exact committed Git evidence;
    # existing v1/v2 durable records remain byte-for-byte compatible.
    if manifest.get('schema_version') == 3:
        from .native_delivery_contract_v3 import normalize_delivery_contract
        normalize_delivery_contract(manifest)
        if not native_apply['history_id'] or not native_apply['operation_id']:
            raise DeliverySessionError('M3 requires durable native history and operation identities')
        if not {str(item['path']) for item in entries}.issubset(actual_mutations):
            raise DeliverySessionError('M3 committed path inventory omits a declared mutation')
        if not 1 <= len(actual_mutations) <= 256 or not 1 <= len(outcomes or []) <= 64:
            raise DeliverySessionError('M3 committed paths/validation evidence exceeds bounded contract')
        committed_blobs = {}
        for path in sorted(actual_mutations):
            ref = _git(root, 'rev-parse', 'HEAD:' + path, optional=True)
            if ref and not _SHA.fullmatch(ref):
                raise DeliverySessionError('M3 invalid committed Git blob identity: ' + path)
            committed_blobs[path] = ref or None
        native_apply.update({
            'source_commit': old_commit,
            'result_parent': _git(root, 'rev-parse', 'HEAD^'),
            'result_commit': inspected['head_sha'],
            'result_tree': _git(root, 'rev-parse', 'HEAD^{tree}'),
            'changed_paths': sorted(actual_mutations),
            'committed_blobs': committed_blobs,
            'validation_steps': [
                {'name': str(outcome.get('name') or ''),
                 'outcome': str(outcome['outcome']), 'returncode': outcome['returncode'],
                 'skipped': False}
                for outcome in outcomes
            ],
        })
    folder = _state_root(state_root)
    with _locked(folder):
        same_commit: list[dict[str, Any]] = []
        for candidate in folder.glob('*.json'):
            existing = _read(candidate)  # Corrupt journal blocks guessing.
            if (existing['repo_root'] == inspected['repo_root']
                    and existing['head_sha'] == inspected['head_sha']
                    and existing['git_dir'] == inspected['git_dir']
                    and existing['head'] == inspected['head']
                    and existing['base'] == inspected['base']):
                same_commit.append(existing)
        if len(same_commit) > 1:
            raise DeliverySessionError('Ambiguous native sessions already bound to applied commit')
        if same_commit:
            existing = same_commit[0]
            if (existing.get('base_repository',existing['repository']).casefold() !=
                    inspected['base_repository'].casefold() or
                    existing.get('head_repository',existing['repository']).casefold() !=
                    inspected['head_repository'].casefold() or
                    existing.get('base_sha') != inspected['base_sha']):
                raise DeliverySessionError('Existing native delivery session has conflicting fork/base identity')
            prior = existing.get('overlay')
            if existing.get('native_apply') not in (None, native_apply):
                raise DeliverySessionError('Existing native transaction proof differs from applied history')
            if prior is not None and prior != inspected['overlay']:
                raise DeliverySessionError('Existing native session binds another overlay for this commit')
            # An earlier launcher can start a PLANNED session with the exact
            # artifact already bound, but without the postcommit native receipt.
            # Reconcile that case through the SAME verified history and atomic
            # journal writer, never by starting another session or reapplying.
            if prior is None or existing.get('native_apply') is None:
                if (existing.get('intents') or existing.get('pr') or existing.get('ci')
                        or existing.get('stage') != 'PLANNED'):
                    raise DeliverySessionError('Existing remote-mutated or progressed session cannot be rebound')
                now = _utc()
                existing['overlay'] = inspected['overlay']
                existing['native_apply'] = native_apply
                existing['events'].append({'kind': 'committed_apply_adopted', 'at': now,
                                           'commit': inspected['head_sha'],
                                           'overlay_sha256': digest})
                existing['updated_at'] = now
                _write(_path(folder, existing['id']), existing)
            return {**_public(existing), 'reused': True,
                    'native_apply_replayed': False}
        identity = uuid.uuid4().hex
        now = _utc()
        record = {'schema': _SCHEMA, 'id': identity, 'created_at': now, 'updated_at': now,
                  'stage': 'PLANNED', 'repo_root': inspected['repo_root'],
                  'git_dir': inspected['git_dir'], 'repository': inspected['repository'],
                  'head': inspected['head'], 'base': inspected['base'],
                  'head_repository': inspected['head_repository'],
                  'base_repository': inspected['base_repository'],
                  'base_sha': inspected['base_sha'],
                  'head_sha': inspected['head_sha'], 'overlay': inspected['overlay'],
                  'native_apply': native_apply,
                  'intents': [], 'events': [
                      {'kind': 'committed_apply_adopted', 'at': now, 'commit': inspected['head_sha'],
                       'overlay_sha256': digest}],
                  'remote_verified': False, 'release_qualified': False}
        _write(_path(folder, identity), record)
        return {**_public(record), 'reused': False, 'native_apply_replayed': False}
