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
_OPERATIONS = frozenset({"push", "pr_create", "dispatch", "rerun", "pr_comment", "pr_labels", "commit_status", "pr_body_set", "pr_labels_replace"})
_MAX_RECORD = 512 * 1024
_M8_CONFIRM_OPERATIONS = frozenset({"pr_comment", "pr_labels", "commit_status",
                                    "pr_body_set", "pr_labels_replace"})


class DeliverySessionError(RuntimeError):
    """Cannot prove the identity or safety of a delivery operation."""


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(obj: Mapping[str, Any]) -> str:
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _state_root(state_root: Path | None = None, *, identity: str | None = None) -> Path:
    if state_root is not None:
        return Path(state_root).expanduser().absolute()
    from .xdg import state_roots
    candidates = tuple(root / "delivery" / "sessions" for root in state_roots())
    if identity is not None:
        if not _HEX32.fullmatch(identity):
            raise DeliverySessionError("Invalid delivery session identity")
        found = [path for path in candidates if (path / (identity + ".json")).exists()
                 or (path / (identity + ".json")).is_symlink()]
        if len(found) > 1:
            raise DeliverySessionError("Conflicting canonical and legacy delivery journals; inspect before migration")
        if found:
            _directory(found[0], create=False)
            return found[0]  # Read/update the verified existing journal in place.
    return candidates[0]


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
    publications = raw.get("check_publications", [])
    if not isinstance(publications, list) or len(publications) > 48 or any(
        not isinstance(item, dict) or item.get("phase") not in ("create_intent", "created", "complete_intent", "confirmed")
        or item.get("kind") not in ("source", "integration", "federation")
        or type(item.get("attempt")) is not int or not 1 <= item["attempt"] <= 16
        for item in publications
    ):
        raise DeliverySessionError("Invalid Check publication journal")
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
    # GA-02 is explicitly opt-in: an owner-pinned policy gates cross-owner
    # fork delivery *before* session creation. M4/M5 still own write authority.
    from .github_relationships import guard_delivery_plan, RelationshipRefused
    try:
        ga02_guard = guard_delivery_plan(
            head_repository=result["head_repository"],
            base_repository=result["base_repository"],
            head_branch=result["head"], base_branch=result["base"],
            head_sha=result["head_sha"], base_sha=result["base_sha"])
    except RelationshipRefused as exc:
        raise DeliverySessionError(str(exc)) from None
    if ga02_guard is not None:
        result["ga02_relationship_guard"] = ga02_guard

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
        key: record[key] for key in ("pr", "ci", "native_apply", "check_publications") if key in record
    }


def _recovery_pair_valid(old: Mapping[str, Any], new: Mapping[str, Any], *, terminal: bool) -> bool:
    """Validate a bounded predecessor/successor chain, never a replay grant."""
    if (old.get('state') != 'reconciled_no_remote_change' or
        new.get('state') not in (('confirmed',) if terminal else ('confirmed','unresolved')) or
        new.get('attempt') != 2 or new.get('predecessor_intent_id') != old.get('id') or
        old.get('operation') != new.get('operation') or old.get('key_sha256') != new.get('key_sha256')):
        return False
    proof = old.get('reconciliation')
    if not isinstance(proof, dict) or proof.get('result') != 'NOT_APPLIED_AT_RECONCILIATION':
        return False
    if old.get('operation') == 'push':
        return (new.get('observed_remote_before') == proof.get('remote_head_sha') and
                (new.get('observed_remote_before') is not None or
                 (new.get('remote_observation') == 'VERIFIED_ABSENT' and proof.get('remote_observation') == 'VERIFIED_ABSENT')))
    if old.get('operation') == 'pr_create':
        request = proof.get('request_sha256')
        return (isinstance(request,str) and re.fullmatch(r'[0-9a-f]{64}',request) is not None and
                new.get('request_sha256') == request and old.get('request_sha256',request) == request and
                proof.get('remote_observation') == 'ALL_STATE_PR_ABSENT' and
                _pr_absence_observations_valid(proof.get('observations'),proof.get('source_head_sha'),proof.get('base_sha')))
    return False


def _intents_terminal_and_valid(intents: list[dict[str, Any]]) -> bool:
    """All intent groups must be independently terminal; retain recovered push/PR pairs."""
    if not isinstance(intents, list) or len(intents) > 128 or not all(isinstance(x,dict) for x in intents):
        return False
    ids=[x.get('id') for x in intents]
    if not all(isinstance(i,str) and _HEX32.fullmatch(i) for i in ids) or len(set(ids)) != len(ids):
        return False
    groups: dict[tuple[str,str], list[dict[str,Any]]] = {}
    for item in intents:
        operation,key=item.get('operation'),item.get('key_sha256')
        if operation not in _OPERATIONS or not isinstance(key,str) or not re.fullmatch(r'[0-9a-f]{64}',key):
            return False
        groups.setdefault((operation,key),[]).append(item)
    return all((len(pair)==1 and pair[0].get('state')=='confirmed' and not pair[0].get('predecessor_intent_id')) or
               (len(pair)==2 and _recovery_pair_valid(*pair,terminal=True)) for pair in groups.values())


def _pr_absence_observations_valid(rows: Any, source: Any, base: Any) -> bool:
    if (not isinstance(source,str) or not _SHA.fullmatch(source) or not isinstance(base,str) or not _SHA.fullmatch(base)
            or not isinstance(rows,list) or len(rows)!=3):
        return False
    times=[]
    for row in rows:
        if (not isinstance(row,dict) or set(row)!={'source_before','source_after','base_before','base_after','all_state_pr_count','observed_at'}
                or row['source_before']!=source or row['source_after']!=source or row['base_before']!=base or row['base_after']!=base
                or type(row['all_state_pr_count']) is not int or row['all_state_pr_count']!=0):
            return False
        try:
            ts=datetime.fromisoformat(row['observed_at'])
            if ts.tzinfo is None: return False
            times.append(ts)
        except (ValueError,TypeError): return False
    return all((b-a).total_seconds()>=1 for a,b in zip(times,times[1:]))


def _pr_intent_group(record: Mapping[str,Any], key: str) -> list[dict[str,Any]]:
    exact=hashlib.sha256(f"{record.get('base_repository',record['repository'])}:{record['base']}:{record.get('head_repository',record['repository'])}:{record['head']}:{record['head_sha']}".encode()).hexdigest()
    if key!=exact: raise DeliverySessionError('PR recovery request identity differs')
    digest=hashlib.sha256(key.encode()).hexdigest()
    return [x for x in record['intents'] if x.get('operation')=='pr_create' and x.get('key_sha256')==digest]


def pr_intent_status(identity: str, key: str, *, state_root: Path | None=None) -> dict[str,Any] | None:
    folder=_state_root(state_root,identity=identity)
    _directory(folder,create=False)
    record=_read(_path(folder,identity))
    matches=_pr_intent_group(record,key)
    if not matches:return None
    if len(matches)==2 and _recovery_pair_valid(*matches,terminal=False):
        active=matches[-1]
        return {'id':active['id'],'state':active['state'],'attempt':2,'request_sha256':active['request_sha256']}
    if len(matches)!=1 or matches[0].get('state') not in ('unresolved','confirmed'):
        raise DeliverySessionError('Malformed PR recovery chain; no replay')
    item=matches[0]
    return {'id':item['id'],'state':item['state'],'attempt':1,'request_sha256':item.get('request_sha256')}


def recover_pr_intent(identity: str, key: str, prior_intent_id: str, *, approved: bool,
                      prior_process_finished: bool, acknowledge_original_request: bool,
                      request_sha256: str, observations: list[dict[str,Any]],
                      state_root: Path | None=None) -> dict[str,Any]:
    """Atomically journal one explicit successor after observed absence (not permanent proof)."""
    if (approved is not True or prior_process_finished is not True or not _HEX32.fullmatch(prior_intent_id or '')
            or not re.fullmatch(r'[0-9a-f]{64}',request_sha256 or '')):
        raise DeliverySessionError('PR recovery requires explicit approval, original intent and completed prior process')
    folder=_state_root(state_root,identity=identity)
    with _locked(folder):
        path=_path(folder,identity);record=_read(path)
        matches=_pr_intent_group(record,key)
        if len(matches)!=1 or matches[0].get('id')!=prior_intent_id or matches[0].get('state')!='unresolved':
            raise DeliverySessionError('Original unresolved PR intent missing or successor already recorded; no further POST')
        old=matches[0]
        current=_identity(Path(record['repo_root']),head=record['head'],base=record['base'])
        if any(current[k]!=record[k] for k in ('head_sha','git_dir','repository')) or record.get('pr'):
            raise DeliverySessionError('PR recovery source drift or historical PR already journaled')
        if _git(Path(record['repo_root']),'status','--porcelain'):
            raise DeliverySessionError('PR recovery requires clean source')
        if (not _intents_terminal_and_valid([x for x in record['intents'] if x is not old]) or
                any(c.get('phase')!='confirmed' for c in record.get('check_publications',[]))):
            raise DeliverySessionError('Other unresolved mutation prohibits PR recovery')
        saved=old.get('request_sha256')
        if saved is None and acknowledge_original_request is not True:
            raise DeliverySessionError('Legacy PR intent requires explicit original request acknowledgement')
        if saved is not None and saved!=request_sha256:
            raise DeliverySessionError('PR recovery request digest differs from original')
        if not _pr_absence_observations_valid(observations,record['head_sha'],record.get('base_sha')):
            raise DeliverySessionError('PR recovery requires three complete spaced exact-source all-state absence observations')
        stamp=_utc();old['state']='reconciled_no_remote_change';old['reconciled_at']=stamp
        old['reconciliation']={'result':'NOT_APPLIED_AT_RECONCILIATION','remote_observation':'ALL_STATE_PR_ABSENT',
            'source_head_sha':record['head_sha'],'base_sha':record['base_sha'],'request_sha256':request_sha256,
            'request_authority':'EXPLICIT_LEGACY_REQUEST_ACKNOWLEDGEMENT' if saved is None else 'ORIGINAL_REQUEST_DIGEST',
            'observations':observations,'prior_process_finished':True}
        fresh={'id':uuid.uuid4().hex,'operation':'pr_create','key_sha256':old['key_sha256'],'state':'unresolved',
               'created_at':stamp,'attempt':2,'predecessor_intent_id':prior_intent_id,'request_sha256':request_sha256}
        record['intents'].append(fresh)
        record['events'].extend([{'kind':'pr_negative_reconciliation','intent_id':prior_intent_id,'at':stamp},
             {'kind':'mutation_intent','operation':'pr_create','intent_id':fresh['id'],'attempt':2,'at':stamp}])
        record['updated_at']=stamp;_write(path,record)
        return {'intent_id':fresh['id'],'new':True,'attempt':2,'safe_to_replay':False}


def _status_record(record: dict[str, Any], *, inspect_git: bool = True) -> dict[str, Any]:
    result = _public(record)
    result["intents"] = [{"id": x["id"], "operation": x["operation"], "state": x["state"]}
                         for x in record["intents"]]
    result["events_count"] = len(record["events"])
    result["check_reconciliation_required"] = any(c.get("phase") != "confirmed" for c in record.get("check_publications", []))
    result["resumable"] = _intents_terminal_and_valid(record["intents"]) and not result["check_reconciliation_required"]
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


def status(identity: str, *, state_root: Path | None = None, inspect_git: bool = True) -> dict[str, Any]:
    folder = _state_root(state_root, identity=identity)
    _directory(folder, create=False)
    return _status_record(_read(_path(folder, identity)), inspect_git=inspect_git)


def _m8_pending(record: Mapping[str, Any], operation: str, key: str,
                intent_id: str | None) -> str:
    """Prove exactly one matching unresolved M8 intent and all other intents terminal.

    This is confirmation authority, never permission to issue a remote mutation.
    It deliberately does not promote the session's general resumable flag.
    """
    if (operation not in _M8_CONFIRM_OPERATIONS or not isinstance(key, str)
            or not 1 <= len(key) <= 160 or (intent_id is not None and
            (not isinstance(intent_id, str) or not _HEX32.fullmatch(intent_id)))):
        raise DeliverySessionError('Invalid M8 confirmation identity')
    intents = record['intents']
    if (not isinstance(intents, list) or not 1 <= len(intents) <= 128
            or not all(isinstance(x, dict) for x in intents)):
        raise DeliverySessionError('Invalid M8 confirmation journal')
    digest = hashlib.sha256(key.encode('utf-8')).hexdigest()
    matches = [x for x in intents if x.get('operation') == operation
               and x.get('key_sha256') == digest]
    if len(matches) != 1 or matches[0].get('state') != 'unresolved':
        raise DeliverySessionError('Exact unresolved M8 intent is unavailable')
    pending = matches[0]
    found = pending.get('id')
    if (not isinstance(found, str) or not _HEX32.fullmatch(found)
            or (intent_id is not None and found != intent_id)
            or sum(x.get('id') == found for x in intents) != 1
            or not _intents_terminal_and_valid([x for x in intents if x is not pending])):
        raise DeliverySessionError('M8 confirmation intent or other journal state differs')
    current = _identity(Path(record['repo_root']), head=record['head'], base=record['base'])
    if any(current[k] != record[k] for k in ('head_sha', 'git_dir', 'repository')):
        raise DeliverySessionError('M8 confirmation source binding changed')
    return found


def m8_confirmation_status(identity: str, operation: str, key: str, *,
                           intent_id: str | None = None,
                           state_root: Path | None = None) -> dict[str, Any]:
    """Read-only confirmation-specific view of the integrity-checked native journal."""
    folder = _state_root(state_root, identity=identity)
    _directory(folder, create=False)
    record = _read(_path(folder, identity))
    pending = _m8_pending(record, operation, key, intent_id)
    result = _status_record(record)
    if result['source_binding'] != 'matching' or result['resumable'] is not False:
        raise DeliverySessionError('M8 confirmation source or session state differs')
    result['confirmation_intent_id'] = pending
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
                  state_root: Path | None = None,
                  push_observation: dict[str, Any] | None = None,
                  request_sha256: str | None = None) -> dict[str, Any]:
    """Record an authorized non-idempotent intent *before* a remote mutation.

    A repeat returns the unresolved original, never a new permission to retry.
    """
    if operation not in _OPERATIONS or not isinstance(key, str) or not 1 <= len(key) <= 160:
        raise DeliverySessionError("Invalid delivery operation identity")
    # Persist a digest, not a possibly sensitive API request or URL.
    key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
    if not approved:
        raise DeliverySessionError("Explicit authorization required before remote mutation intent")
    folder = _state_root(state_root, identity=identity)
    with _locked(folder):
        path = _path(folder, identity)
        record = _read(path)
        current = _identity(Path(record["repo_root"]), head=record["head"], base=record["base"])
        if (current["head_sha"] != record["head_sha"] or
            current["git_dir"] != record["git_dir"] or
            current["repository"] != record["repository"]):
            raise DeliverySessionError("Source drift: refusing to create a mutation intent")
        if any(c.get("phase") != "confirmed" for c in record.get("check_publications", [])):
            raise DeliverySessionError("Unresolved GA03 check publication requires reconciliation")
        for item in record["intents"]:
            if item["operation"] == operation and item["key_sha256"] == key_hash:
                return {"intent_id": item["id"], "state": item["state"], "new": False,
                        "safe_to_replay": False}
            if item["state"] != "confirmed" and not _intents_terminal_and_valid(record["intents"]):
                raise DeliverySessionError("Unresolved mutation intent requires reconciliation before another mutation")
        at = _utc()
        intent = {"id": uuid.uuid4().hex, "operation": operation, "key_sha256": key_hash,
                  "state": "unresolved", "created_at": at}
        if request_sha256 is not None:
            if operation != "pr_create" or not re.fullmatch(r"[0-9a-f]{64}",request_sha256):
                raise DeliverySessionError("Invalid PR request digest")
            intent["request_sha256"]=request_sha256
        if push_observation is not None:
            remote = push_observation.get('remote_head_sha')
            if (operation != 'push' or set(push_observation) != {'remote_head_sha'}
                    or (remote is not None and (not isinstance(remote,str) or not _SHA.fullmatch(remote)))):
                raise DeliverySessionError('Invalid initial push remote observation')
            intent['observed_remote_before'] = remote
            intent['remote_observation'] = 'VERIFIED_ABSENT' if remote is None else 'VERIFIED_PRESENT'
        record["intents"].append(intent)
        record["events"].append({"kind": "mutation_intent", "operation": operation,
                                 "intent_id": intent["id"], "at": at})
        record["updated_at"] = at
        _write(path, record)
    return {"intent_id": intent["id"], "state": "unresolved", "new": True,
            "safe_to_replay": False}



def recover_push_intent(identity: str, key: str, prior_intent_id: str, *,
                        remote_old_sha: str | None, approved: bool, state_root: Path | None = None,
                        prior_branch_absent: bool = False) -> dict[str, Any]:
    """Explicit second push attempt after independent negative remote verification.

    Caller proves the previous foreground Git process exited and independently
    re-observed the exact unchanged remote twice. This does NOT make a Git
    write; it atomically preserves the first attempt and journals a NEW one.
    The subsequent normal non-force push is protected by GitHub's own FF gate.
    """
    if not approved or not _HEX32.fullmatch(prior_intent_id or ''):
        raise DeliverySessionError('Push recovery requires explicit approval and original intent ID')
    if remote_old_sha is not None and not _SHA.fullmatch(remote_old_sha):
        raise DeliverySessionError('Cannot recover without an exact remote observation')
    folder = _state_root(state_root, identity=identity)
    with _locked(folder):
        path = _path(folder, identity)
        record = _read(path)
        exact = f"{record['repository']}:refs/heads/{record['head']}:{record['head_sha']}"
        if key != exact or record.get('remote_verified') is True:
            raise DeliverySessionError('Push recovery source/key already verified or changed')
        current = _identity(Path(record['repo_root']), head=record['head'], base=record['base'])
        if any(current[k] != record[k] for k in ('head_sha','git_dir','repository')):
            raise DeliverySessionError('Push recovery source binding changed')
        # Single-parent remediation is deliberately narrow: remote is exactly
        # the previous source, not an arbitrary ancestor or different PR.
        parents = _git(Path(record['repo_root']), 'rev-list', '--parents', '-n', '1', 'HEAD').split()
        if len(parents) != 2 or parents[0] != record['head_sha']:
            raise DeliverySessionError('Push recovery requires a single-parent committed source')
        if remote_old_sha is not None and parents[1] != remote_old_sha:
            raise DeliverySessionError('Push recovery requires exact parent as remote head')
        if remote_old_sha is None:
            if not prior_branch_absent:
                raise DeliverySessionError('Prior branch absence acknowledgement required')
            native = record.get('native_apply') or {}
            if (native.get('state') != 'COMMITTED' or native.get('result_commit') != record['head_sha']
                    or native.get('result_parent') != parents[1] or native.get('validation_status') != 'ok'
                    or record.get('base_sha') != parents[1]
                    or native.get('result_tree') != _git(Path(record['repo_root']), 'rev-parse', 'HEAD^{tree}')
                    or native.get('transaction_status') != 'completed'
                    or not native.get('transaction_id') or not native.get('history_id')
                    or _git(Path(record['repo_root']), 'status', '--porcelain')):
                raise DeliverySessionError('Absent-branch recovery requires exact validated native single-parent commit and base')
        key_hash = hashlib.sha256(key.encode('utf-8')).hexdigest()
        matches = [x for x in record['intents'] if x.get('operation') == 'push'
                   and x.get('key_sha256') == key_hash]
        if len(matches) != 1 or matches[0].get('id') != prior_intent_id or matches[0].get('state') != 'unresolved':
            raise DeliverySessionError('Original unresolved push intent missing or already recovered')
        if not _intents_terminal_and_valid([x for x in record['intents'] if x is not matches[0]]):
            raise DeliverySessionError('Other unresolved mutation prohibits push recovery')
        if any(c.get('phase') != 'confirmed' for c in record.get('check_publications', [])):
            raise DeliverySessionError('Unresolved GA03 check publication requires reconciliation')
        if remote_old_sha is None:
            original_observation = matches[0].get('remote_observation')
            if original_observation not in (None, 'VERIFIED_ABSENT') or (original_observation == 'VERIFIED_ABSENT' and matches[0].get('observed_remote_before') is not None):
                raise DeliverySessionError('Initial push did not observe an absent remote branch')
            completed = [e for e in record['events'] if e.get('kind') == 'push_attempt_result'
                         and e.get('intent_id') == prior_intent_id and type(e.get('exit_code')) is int
                         and e['exit_code'] > 0]
            if len(completed) != 1:
                raise DeliverySessionError('Absent-branch recovery requires recorded nonzero completed Git attempt')
        stamp = _utc()
        prior = matches[0]
        prior['state'] = 'reconciled_no_remote_change'
        prior['reconciled_at'] = stamp
        prior['reconciliation'] = {'remote_head_sha': remote_old_sha,
                                  'source_head_sha': record['head_sha'],
                                  'remote_observation': 'VERIFIED_ABSENT' if remote_old_sha is None else 'EXACT_PARENT',
                                  'prior_absence_authority': ('ORIGINAL_NATIVE_REMOTE_OBSERVATION' if prior.get('remote_observation') == 'VERIFIED_ABSENT' else 'EXPLICIT_HISTORICAL_PREFLIGHT_ACKNOWLEDGEMENT') if remote_old_sha is None else None,
                                  'result': 'NOT_APPLIED_AT_RECONCILIATION'}
        fresh = {'id': uuid.uuid4().hex, 'operation': 'push', 'key_sha256': key_hash,
                 'state': 'unresolved', 'created_at': stamp, 'attempt': 2,
                 'predecessor_intent_id': prior_intent_id,
                 'observed_remote_before': remote_old_sha,
                 'remote_observation': 'VERIFIED_ABSENT' if remote_old_sha is None else 'EXACT_PARENT'}
        record['intents'].append(fresh)
        record['events'].append({'kind':'push_negative_reconciliation','intent_id':prior_intent_id,
                                 'remote_sha':remote_old_sha,'at':stamp})
        record['events'].append({'kind':'mutation_intent','operation':'push',
                                 'intent_id':fresh['id'],'at':stamp,'attempt':2})
        record['updated_at'] = stamp
        _write(path,record)
        return {'intent_id':fresh['id'],'previous_intent_id':prior_intent_id,
                'state':'unresolved','attempt':2,'safe_to_replay':False}



def record_push_diagnostic(identity: str, intent_id: str, code: str, exit_code: int,
                           *, state_root: Path | None = None) -> None:
    """Store only a fixed classification, never raw Git stderr or credential data."""
    allowed = {'GIT_WORKFLOW_PERMISSION_REJECTED', 'GIT_AUTH_REJECTED',
               'GIT_NON_FAST_FORWARD', 'GIT_REMOTE_REJECTED',
               'GIT_TRANSPORT_FAILURE', 'GIT_EXIT_NONZERO', 'GIT_EXIT_ZERO',
               'GIT_PROCESS_UNAVAILABLE', 'GIT_ASKPASS_UNAVAILABLE',
               'GIT_TLS_CERTIFICATE_FAILURE', 'GIT_DNS_FAILURE', 'GIT_REF_MISSING',
               'GIT_PROCESS_SIGNAL', 'GIT_HTTP_403', 'GIT_REPOSITORY_UNAVAILABLE'}
    if code not in allowed or type(exit_code) is not int or not -128 <= exit_code <= 255:
        raise DeliverySessionError('Unsafe Git push diagnostic')
    folder = _state_root(state_root, identity=identity)
    with _locked(folder):
        path = _path(folder, identity)
        record = _read(path)
        selected = [x for x in record['intents'] if x['id'] == intent_id
                    and x['operation'] == 'push' and x['state'] == 'unresolved']
        if len(selected) != 1:
            raise DeliverySessionError('Push diagnostic not bound to an unresolved intent')
        if any(e.get('kind') == 'push_attempt_result' and e.get('intent_id') == intent_id
               for e in record['events']):
            raise DeliverySessionError('Push attempt diagnostic was already recorded')
        entry = {'kind':'push_attempt_result','at':_utc(), 'intent_id':intent_id,
                 'classification':code, 'exit_code':exit_code}
        record['events'].append(entry)
        record['updated_at'] = entry['at']
        _write(path,record)


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
                   optional: bool = False, expected_intent_id: str | None = None) -> dict[str, Any]:
    """Resolve a previously journaled mutation using independently observed proof.

    The caller must read the external provider *after* a possibly uncertain
    operation; this function never performs the mutation and never authorizes a
    replay. An absent intent is acceptable only for pure read-only discovery.
    """
    if operation not in _OPERATIONS or not isinstance(key, str):
        raise DeliverySessionError('Invalid mutation intent reconciliation')
    proof = dict(evidence)
    folder = _state_root(state_root, identity=identity)
    with _locked(folder):
        path = _path(folder, identity)
        record = _read(path)
        if expected_intent_id is not None:
            _m8_pending(record, operation, key, expected_intent_id)
        if proof.get('head_sha') != record['head_sha']:
            raise DeliverySessionError('Mutation evidence source SHA does not match session')
        if operation == 'pr_create':
            _pr_intent_group(record,key)
            live=_identity(Path(record['repo_root']),head=record['head'],base=record['base'])
            if any(live[k]!=record[k] for k in ('head_sha','git_dir','repository')):
                raise DeliverySessionError('PR confirmation source binding changed')
            value = proof.get('pr_number')
            if type(value) is not int or value < 1:
                raise DeliverySessionError('PR evidence requires an exact PR number')
        if operation == 'push':
            # This records ONLY an independently observed exact native push.
            # Never equate an arbitrary confirmed intent with remote verification.
            exact = f"{record['repository']}:refs/heads/{record['head']}:{record['head_sha']}"
            if key != exact or set(proof) != {'head_sha'}:
                raise DeliverySessionError('Push verification must bind exact origin/ref/HEAD')
            live = _identity(Path(record['repo_root']), head=record['head'], base=record['base'])
            if (live['head_sha'] != record['head_sha'] or
                    live['git_dir'] != record['git_dir'] or
                    live['repository'].casefold() != record['repository'].casefold()):
                raise DeliverySessionError('Push verification source identity changed')
        if operation in ('pr_comment', 'pr_labels', 'commit_status',
                          'pr_body_set', 'pr_labels_replace'):
            pr_number = (record.get('pr') or {}).get('number')
            if type(pr_number) is not int or pr_number < 1:
                raise DeliverySessionError('M8 mutation requires exact saved PR identity')
            if operation == 'pr_comment':
                if (set(proof) != {'head_sha', 'pr_number', 'comment_id'} or
                        proof.get('pr_number') != pr_number or
                        type(proof.get('comment_id')) is not int or proof['comment_id'] < 1):
                    raise DeliverySessionError('M8 comment proof must bind saved PR and comment ID')
            elif operation == 'pr_labels':
                labels = proof.get('labels')
                if (set(proof) != {'head_sha', 'pr_number', 'labels'} or
                        proof.get('pr_number') != pr_number or
                        not isinstance(labels, list) or not 1 <= len(labels) <= 8 or
                        len(set(str(v) for v in labels)) != len(labels) or
                        any(not isinstance(v, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,48}', v) for v in labels)):
                    raise DeliverySessionError('M8 labels proof is not exact and bounded')
            elif operation == 'pr_body_set':
                if (set(proof) != {'head_sha', 'pr_number', 'body_sha256'} or
                        proof.get('pr_number') != pr_number or
                        type(proof.get('body_sha256')) is not str or
                        not re.fullmatch(r'[a-f0-9]{64}', proof['body_sha256'])):
                    raise DeliverySessionError('GA01 PR body proof requires bound PR and SHA256')
            elif operation == 'pr_labels_replace':
                labels = proof.get('labels')
                if (set(proof) != {'head_sha', 'pr_number', 'labels'} or
                        proof.get('pr_number') != pr_number or
                        type(labels) is not list or not 0 <= len(labels) <= 8 or
                        len(set(str(v) for v in labels)) != len(labels) or
                        any(type(v) is not str or
                            not re.fullmatch(r'[A-Za-z0-9_.-]{1,48}', v)
                            for v in labels)):
                    raise DeliverySessionError('GA01 replacement labels proof invalid')
            else:
                if (set(proof) != {'head_sha', 'status_id', 'context'} or
                        type(proof.get('status_id')) is not int or proof['status_id'] < 1 or
                        not isinstance(proof.get('context'), str) or
                        not re.fullmatch(r'devtool/m8/[A-Za-z0-9_.-]{1,60}', proof['context'])):
                    raise DeliverySessionError('M8 commit status proof invalid')
        key_hash = hashlib.sha256(key.encode('utf-8')).hexdigest()
        items = [x for x in record['intents'] if x['operation'] == operation and x['key_sha256'] == key_hash]
        if not items:
            if optional:
                return {'state':'not-recorded','confirmed':False}
            raise DeliverySessionError('Mutation intent was never recorded')
        if len(items) != 1:
            if len(items)!=2 or not _recovery_pair_valid(*items,terminal=False):
                raise DeliverySessionError('Duplicate or malformed mutation journal entries')
            found = items[-1]
        else:
            found = items[0]
        if found['state'] == 'confirmed':
            if found.get('evidence') != proof:
                raise DeliverySessionError('Conflicting mutation reconciliation evidence')
            if operation == 'push' and record.get('remote_verified') is not True:
                # Idempotent migration for previously confirmed native push
                # intents: caller has just read the same HEAD from origin.
                record['remote_verified'] = True
                record['updated_at'] = _utc()
                record['events'].append({'kind':'remote_head_verified',
                    'at':record['updated_at'],'commit':record['head_sha']})
                _write(path, record)
            return {'state':'confirmed','confirmed':True,'reconciled':False}
        if found['state'] != 'unresolved':
            raise DeliverySessionError('Mutation intent state is invalid')
        found['state'] = 'confirmed'
        found['evidence'] = proof
        found['confirmed_at'] = _utc()
        record['updated_at'] = _utc()
        record['events'].append({'kind':'mutation_confirmed','at':record['updated_at'],
                                 'operation':operation, 'intent_id':found['id']})
        if operation == 'push':
            record['remote_verified'] = True
            record['events'].append({'kind':'remote_head_verified',
                'at':record['updated_at'],'commit':record['head_sha']})
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
    folder = _state_root(state_root, identity=identity)
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
    folder = _state_root(state_root, identity=identity)
    with _locked(folder):
        path = _path(folder, identity)
        record = _read(path)
        if obs.get('source_sha') != record['head_sha']:
            raise DeliverySessionError('CI observation source SHA differs from session')
        record['ci'] = obs
        record['updated_at'] = _utc()
        event = {'kind':'ci_observed','at':record['updated_at'],
                 'state':obs['state'],'runs':len(obs['runs'])}
        registration = obs.get('registration')
        if isinstance(registration, dict) and registration.get('gateway_http_status') in (502,503,504):
            # Only fixed numeric transport evidence enters the bounded journal.
            if obs['state'] == 'REGISTRATION_PROVIDER_UNAVAILABLE' and registration.get('read_attempts') == 3:
                event.update(gateway_http_status=registration['gateway_http_status'], read_attempts=3)
        record['events'].append(event)
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



def _legacy_required_artifact_test_outcomes(receipt: Mapping[str, Any],
                                            manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    # Re-attest old schema-v2 required-test receipt from native children.
    # The original source-bound outcomes are read only, never written.
    if manifest.get('schema_version') != 2:
        return []
    validation = manifest.get('validation')
    if not isinstance(validation, dict) or validation.get('required') is not True:
        return []
    if any(validation.get(key) is not False for key in ('restore', 'build', 'test')):
        return []
    if any(validation.get(key) for key in ('script', 'refs', 'tasks', 'gradle_tasks')):
        return []
    tests = validation.get('tests')
    if not isinstance(tests, list) or not 1 <= len(tests) <= 64:
        return []
    names = []
    for entry in tests:
        if not isinstance(entry, dict) or entry.get('required') is not True:
            return []
        if entry.get('runner') != 'python' or not isinstance(entry.get('command'), list):
            return []
        if not entry['command'] or not all(isinstance(p, str) and p for p in entry['command']):
            return []
        identity = entry.get('id')
        if not isinstance(identity, str) or not identity or len(identity) > 128:
            return []
        names.append('artifact-test:' + identity)
    if len(names) != len(set(names)):
        return []
    children = receipt.get('child_outcomes')
    if not isinstance(children, list) or not 1 <= len(children) <= 64:
        return []
    if any(not isinstance(item, dict) or type(item.get('returncode')) is not int
           or item['returncode'] != 0 or item.get('skipped') is not False
           or item.get('failure_kind') not in (None, '') for item in children):
        return []
    actual = [item['name'] for item in children
              if isinstance(item.get('name'), str) and item['name'].startswith('artifact-test:')]
    if len(actual) != len(names) or set(actual) != set(names):
        return []
    if not any(item.get('name') == 'git-commit' for item in children):
        return []
    return [{'name': name, 'outcome': 'passed', 'returncode': 0, 'skipped': False}
            for name in names]




def _legacy_static_validation_outcomes(receipt: Mapping[str, Any], manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Interpret only the known DT37 static-only history defect; never rewrite it."""
    if not isinstance(manifest, Mapping): return []
    validation = manifest.get('validation')
    if (manifest.get('schema_version') != 3 or receipt.get('validation_status') != 'not-run'
            or not isinstance(validation,dict) or validation.get('required') is not True
            or any(validation.get(k) is not False for k in ('restore','build','test'))
            or any(validation.get(k) for k in ('refs','tasks','gradle_tasks'))):
        return []
    names=[]
    if validation.get('script'):
        if not isinstance(validation['script'],dict): return []
        names.append('artifact-validate-script')
    tests = validation.get('tests') or []
    if not isinstance(tests,list): return []
    for t in tests:
        if (not isinstance(t,dict) or t.get('required') is not True or t.get('runner')!='python'
                or not isinstance(t.get('id'),str) or not t['id']): return []
        names.append('artifact-test:'+t['id'])
    if not names or len(names)!=len(set(names)) or len(names)>64: return []
    plan=receipt.get('validation_plan')
    if not isinstance(plan,dict): return []
    steps=plan.get('validation_step_outcomes')
    children=receipt.get('child_outcomes')
    if not isinstance(steps,list) or not isinstance(children,list): return []
    def passed(x):
        return isinstance(x,dict) and type(x.get('returncode')) is int and x['returncode']==0 and x.get('skipped') is False
    if not all(passed(x) for x in children) or not any(x.get('name')=='git-commit' for x in children): return []
    actual=[x for x in steps if isinstance(x,dict) and x.get('name') in names]
    if (len(actual)!=len(names) or {x['name'] for x in actual}!=set(names)
            or not all(passed(x) and x.get('outcome')=='passed' for x in steps)
            or not all(sum(x.get('name')==name for x in children)==1 for name in names)):
        return []
    return [{'name':x['name'],'outcome':'passed','returncode':0,'skipped':False} for x in actual]


def _committed_safe_path(value: str) -> str:
    """Reject noncanonical or unsafe tracked paths before native scope checks."""
    from pathlib import PurePosixPath
    if (not isinstance(value, str) or not value or value.startswith('/')
            or "\\" in value or "\x00" in value
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)):
        raise DeliverySessionError('Unsafe committed path or hook declaration')
    rel = PurePosixPath(value)
    parts = value.split('/')
    if (rel.is_absolute() or rel.as_posix() != value or
            any(part in ('', '.', '..') for part in parts) or
            parts[0] == '.git' or ':' in parts[0]):
        raise DeliverySessionError('Unsafe committed path or hook declaration')
    return value


def _committed_git_mode(root: Path, revision: str, rel: str) -> str:
    """Read the Git tree mode; a symlink is never a verified output directory."""
    # -z avoids Git's quote-path ambiguity; path input was validated above.
    raw = _git(root, 'ls-tree', '-z', revision, '--', rel, optional=True)
    modes = []
    for line in raw.split('\x00'):
        metadata, separator, path = line.partition('\t')
        if separator and path == rel:
            modes.append(metadata.split(' ', 1)[0])
    if len(modes) > 1:
        raise DeliverySessionError('Ambiguous committed Git path mode')
    return modes[0] if modes else ''


def _committed_guard_symlinks(root: Path, rel: str) -> None:
    """Check every component in HEAD and its parent, including deleted paths."""
    pieces = rel.split('/')
    for index in range(1, len(pieces) + 1):
        prefix = '/'.join(pieces[:index])
        if (root / prefix).is_symlink():
            raise DeliverySessionError('Committed hook output symlink escape refused')
        for revision in ('HEAD', 'HEAD^'):
            if _committed_git_mode(root, revision, prefix) == '120000':
                raise DeliverySessionError('Committed hook output symlink escape refused')


def _committed_hook_output_scopes(root: Path, manifest: Mapping[str, Any],
                                  receipt: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    """Reuse native hook write_paths and boundary rules without widening payloads.

    A declaration has no effect unless its hook actually passed in the original
    native child outcomes. Only Git tree objects (in HEAD or the parent) grant
    descendant scope; an explicitly declared file remains exact-path only.
    """
    from .artifacts import (
        artifact_preflight_write_paths, artifact_validation_write_paths,
        artifact_finalize_write_paths, artifact_finalize_phases,
        artifact_preflight_script, artifact_validation_script,
        artifact_finalize_script,
    )
    children = receipt.get('child_outcomes')
    successful = {str(row.get('name')) for row in
                  (children if isinstance(children, list) else [])
                  if isinstance(row, dict) and type(row.get('returncode')) is int
                  and row['returncode'] == 0 and row.get('skipped') is False
                  and row.get('failure_kind') in ('', None)}
    declared = (
        ('artifact-preflight-script', artifact_preflight_write_paths,
         artifact_preflight_script, True),
        ('artifact-validate-script', artifact_validation_write_paths,
         artifact_validation_script, True),
        ('artifact-finalize-script', artifact_finalize_write_paths,
         artifact_finalize_script,
         any(p != 'after_commit' for p in artifact_finalize_phases(dict(manifest)))),
    )
    exact: set[str] = set()
    directories: set[str] = set()
    for event, get_paths, get_script, can_write in declared:
        # The validator operation is called 'validate' in native history, but
        # its canonical manifest policy remains named 'validation'.
        kind = ('validation' if event == 'artifact-validate-script' else
                event.removeprefix('artifact-').removesuffix('-script'))
        policy = manifest.get(kind)
        if policy is not None and not isinstance(policy, dict):
            raise DeliverySessionError('Invalid committed hook policy')
        raw_values = (policy or {}).get('write_paths')
        raw_values = ([raw_values] if isinstance(raw_values, str)
                      else raw_values if isinstance(raw_values, list) else [])
        # Native extraction normalizes a harmless trailing slash. Never let
        # normalization of backslashes, dot segments or empty roots widen it.
        for raw in raw_values:
            if not isinstance(raw, str):
                raise DeliverySessionError('Invalid committed hook declaration')
            candidate = raw[:-1] if raw.endswith('/') else raw
            _committed_safe_path(candidate)
        paths = get_paths(dict(manifest))
        if not paths or not can_write or not get_script(dict(manifest)):
            continue
        # Do not accept a declaration without its real successful native hook.
        if event not in successful:
            continue
        for name in paths:
            relative = _committed_safe_path(name)
            _committed_guard_symlinks(root, relative)
            current = _committed_git_mode(root, 'HEAD', relative)
            previous = _committed_git_mode(root, 'HEAD^', relative)
            if '040000' in (current, previous):
                directories.add(relative)
            else:
                exact.add(relative)
    return exact, directories


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
                 and (str(item.get('validation_status') or '').lower() in {'ok', 'passed', 'success', 'succeeded'}
                      or bool(_legacy_static_validation_outcomes(item,item.get('manifest') or {})))
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
    legacy_static = _legacy_static_validation_outcomes(receipt, manifest)
    if legacy_static:
        outcomes = legacy_static
    legacy_child_evidence = False
    if validation.get('required') is True:
        if not isinstance(outcomes, list) or not outcomes:
            # The original native child receipts are durable and source-bound;
            # never change their bytes or claim synthetic validation-plan steps.
            outcomes = _legacy_required_artifact_test_outcomes(receipt, manifest)
            legacy_child_evidence = bool(outcomes)
        if (not isinstance(outcomes, list) or not outcomes or any(
            not isinstance(item, dict) or item.get('skipped') is not False
            or item.get('outcome') != 'passed' or type(item.get('returncode')) is not int
            or item['returncode'] != 0 for item in outcomes)):
            raise DeliverySessionError('Required native validation must have passing, unskipped step evidence')
    native_apply = {
        'state': 'COMMITTED', 'commit': inspected['head_sha'],
        'history_id': str(receipt.get('history_id') or ''),
        'operation_id': str(receipt.get('operation_id') or ''),
        'transaction_id': str(tx['id']), 'transaction_status': tx['status'],
        'validation_status': 'ok' if legacy_static else str(receipt['validation_status']).lower(),
        'source_tree': str(manifest.get('base_tree') or ''),
        'artifact_sha256': digest,
    }
    if legacy_static:
        native_apply['validation_evidence_source'] = 'original-static-child-outcomes'
    if legacy_child_evidence:
        # Explicit provenance: this is an interpretation of persisted native
        # child outcomes, NOT an invented or rewritten validation_plan receipt.
        native_apply['validation_evidence_source'] = 'original-native-child-outcomes'
        native_apply['validation_steps'] = outcomes
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
    # Exact payload paths stay exact; only genuine, native-validated hook
    # output *directories* may authorize committed descendant files. With
    # --no-renames, both sides of a move are independently scope-checked.
    exact_payload = {_committed_safe_path(str(item.get('path') or ''))
                     for item in entries if isinstance(item, dict)}
    if len(exact_payload) != len(entries):
        raise DeliverySessionError('Applied overlay inventory paths are not unique')
    hook_files, hook_dirs = _committed_hook_output_scopes(root, manifest, receipt)
    raw_changes = _git(root, 'diff-tree', '--no-commit-id', '--name-only',
                       '-r', '--no-renames', '-z', 'HEAD')
    actual_mutations = {_committed_safe_path(p) for p in raw_changes.split('\x00') if p}
    from .hooks.isolation import path_is_declared
    unverified = {p for p in actual_mutations
                  if p not in exact_payload and p not in hook_files
                  and not path_is_declared(p, hook_dirs)}
    if not actual_mutations or unverified:
        raise DeliverySessionError('Applied commit contains unverified or empty mutation inventory')
    # A symlink introduced as hook output, or symlinked ancestor in either
    # revision, must never gain directory scope through string-prefix matching.
    for path in sorted(actual_mutations - exact_payload):
        _committed_guard_symlinks(root, path)
    for row in entries:
        if not isinstance(row, dict):
            raise DeliverySessionError('Applied overlay inventory shape invalid')
        path = str(row.get('path') or '')
        action = str(row.get('action') or '').lower()
        if not path or path.startswith('/') or '..' in path.split('/') or '\\' in path:
            raise DeliverySessionError('Applied overlay inventory path invalid')
        if action == 'delete':
            if manifest.get('schema_version') == 3 and (path not in expected or expected[path] is not None):
                raise DeliverySessionError('Applied deletion must have an explicit null postimage')
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
    # New journals belong in the canonical location. Previously committed M3
    # sessions may still live under the old root: lock and inspect both before
    # authorizing a second adoption. Never move or silently delete the old one.
    from contextlib import ExitStack
    from .xdg import state_roots
    folders = (folder,) if state_root is not None else tuple(
        base / 'delivery' / 'sessions' for base in state_roots())
    with ExitStack() as locks:
        for journal_root in folders:
            if journal_root == folder:
                locks.enter_context(_locked(journal_root))
            elif journal_root.exists() or journal_root.is_symlink():
                _directory(journal_root, create=False)
                locks.enter_context(_locked(journal_root))
        same_commit: list[tuple[dict[str, Any], Path]] = []
        for journal_root in folders:
            if not journal_root.exists():
                continue
            for candidate in journal_root.glob('*.json'):
                existing = _read(candidate)  # Corrupt journal blocks guessing.
                if (existing['repo_root'] == inspected['repo_root']
                        and existing['head_sha'] == inspected['head_sha']
                        and existing['git_dir'] == inspected['git_dir']
                        and existing['head'] == inspected['head']
                        and existing['base'] == inspected['base']):
                    same_commit.append((existing, journal_root))
        if len(same_commit) > 1:
            raise DeliverySessionError('Ambiguous native sessions already bound to applied commit')
        if same_commit:
            existing, existing_root = same_commit[0]
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
                _write(_path(existing_root, existing['id']), existing)
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
