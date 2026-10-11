"""ACTIONS-03: source-correlated remote run observation and explicit control.

One-shot/bounded polling; no daemon or inferred success. All state is a local,
private observation of GitHub, never a substitute for GitHub's status.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
import uuid
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request

from .actions_dispatch import _durable_record, _state_root
from .github_actions import GitHubActionsClient, GitHubActionsError, GitHubDispatchOutcomeUnknown, _slug

_ID = re.compile(r"[1-9][0-9]{0,18}\Z")
_REQUEST = re.compile(r"[0-9a-f]{32}\Z")
_STATES = {"queued", "in_progress", "completed", "requested", "waiting", "pending"}
_FINAL = {"success", "failure", "cancelled", "timed_out", "neutral", "skipped", "action_required", "stale"}
_LOG_LIMIT = 32 * 1024 * 1024


def run_id(value: object) -> int:
    raw = str(value or "")
    if not _ID.fullmatch(raw):
        raise GitHubActionsError("Remote run ID must be a positive integer")
    return int(raw)


def _safe_receipt(request_id: str, root: Path | None = None) -> tuple[Path, dict[str, Any]]:
    if not _REQUEST.fullmatch(request_id):
        raise GitHubActionsError("Dispatch request ID must be a 32-character hexadecimal identifier")
    folder = root if root is not None else _state_root()
    path = folder / f"{request_id}.json"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 256 * 1024:
        raise GitHubActionsError("Matching local dispatch receipt not found")
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        raise GitHubActionsError("Invalid local dispatch receipt") from None
    if (not isinstance(obj, dict) or obj.get("schema") != 1
        or obj.get("request_id") != request_id
        or not isinstance(obj.get("repository"), str)
        or obj.get("run_id") is None):
        raise GitHubActionsError("Receipt has no correlated remote run ID; automatic guessing is prohibited")
    _slug(obj["repository"])
    run_id(obj["run_id"])
    return path, obj


def select_run(explicit_slug: str, raw_run_id: object, request_id: str, local_slug: Callable[[], str],
               *, root: Path | None = None) -> tuple[str, int, Path | None, dict[str, Any] | None]:
    if request_id:
        path, receipt = _safe_receipt(request_id, root)
        slug = _slug(receipt["repository"])
        if explicit_slug and _slug(explicit_slug).lower() != slug.lower():
            raise GitHubActionsError("Request receipt is bound to a different GitHub repository")
        identity = run_id(receipt["run_id"])
        if raw_run_id and run_id(raw_run_id) != identity:
            raise GitHubActionsError("Request receipt is bound to a different remote run ID")
        return slug, identity, path, receipt
    if not raw_run_id:
        raise GitHubActionsError("Use --run-id ID or --request-id ID (no implicit latest-run selection)")
    return _slug(explicit_slug or local_slug()), run_id(raw_run_id), None, None


def _normalize_run(raw: dict[str, Any], slug: str, expected: int | None = None) -> dict[str, Any]:
    identity = raw.get("id")
    if isinstance(identity, bool) or not isinstance(identity, int) or identity <= 0:
        raise GitHubActionsError("GitHub workflow run response lacks a valid ID")
    if expected and identity != expected:
        raise GitHubActionsError("GitHub workflow run identity mismatch")
    repo = raw.get("repository")
    if isinstance(repo, dict) and repo.get("full_name") and str(repo["full_name"]).lower() != slug.lower():
        raise GitHubActionsError("GitHub workflow run belongs to another repository")
    status = str(raw.get("status") or "unknown")
    conclusion = raw.get("conclusion")
    if conclusion is not None and not isinstance(conclusion, str):
        raise GitHubActionsError("GitHub workflow run conclusion has unexpected type")
    complete = status == "completed" and conclusion in _FINAL
    return {"id": identity, "repository": slug, "workflow_id": raw.get("workflow_id"),
            "name": raw.get("name"), "status": status if status in _STATES else "unknown",
            "conclusion": conclusion, "completed": complete,
            "run_attempt": raw.get("run_attempt"), "event": raw.get("event"),
            "head_sha": raw.get("head_sha"), "head_branch": raw.get("head_branch"),
            "created_at": raw.get("created_at"), "updated_at": raw.get("updated_at"),
            "html_url": f"https://github.com/{slug}/actions/runs/{identity}"}


def _normalize_job(raw: dict[str, Any]) -> dict[str, Any]:
    identity = raw.get("id")
    if isinstance(identity, bool) or not isinstance(identity, int) or identity <= 0:
        raise GitHubActionsError("Invalid GitHub Actions job identity")
    steps = raw.get("steps") or []
    if not isinstance(steps, list):
        raise GitHubActionsError("Invalid GitHub Actions job steps")
    safe_steps = []
    for x in steps:
        if not isinstance(x, dict):
            raise GitHubActionsError("Invalid GitHub Actions step entry")
        safe_steps.append({k:x.get(k) for k in ("name", "number", "status", "conclusion", "started_at", "completed_at")})
    return {"id":identity, "run_id":raw.get("run_id"), "run_attempt":raw.get("run_attempt"),
            "name":raw.get("name"), "status":raw.get("status"), "conclusion":raw.get("conclusion"),
            "started_at":raw.get("started_at"), "completed_at":raw.get("completed_at"),
            "steps":safe_steps}


def _record_observation(path: Path | None, receipt: dict[str, Any] | None,
                        remote: dict[str, Any]) -> dict[str, Any]:
    # Never turn an API outage into successful completion; preserve the last observation.
    if path is None or receipt is None:
        return {"persisted": False}
    if receipt["run_id"] != remote["id"] or receipt["repository"].lower() != remote["repository"].lower():
        raise GitHubActionsError("Remote observation is not bound to the local dispatch receipt")
    observation = {k: remote[k] for k in ("id", "status", "conclusion", "completed", "run_attempt", "head_sha", "updated_at")}
    receipt = dict(receipt)
    receipt["last_observation"] = observation
    receipt["observed_at"] = datetime.now(timezone.utc).isoformat()
    # Keep existing dispatch state separate; never mark accepted == completed.
    _durable_record(path, receipt)
    return {"persisted": True, "request_id": receipt["request_id"]}


def snapshot(client: GitHubActionsClient, slug: str, identity: int, path: Path | None = None,
             receipt: dict[str, Any] | None = None, *, with_jobs: bool = False) -> dict[str, Any]:
    remote = _normalize_run(client.get_run(slug, identity), slug, identity)
    payload = {"phase":"ACTIONS-03", "remote":remote, "journal":_record_observation(path, receipt, remote)}
    if with_jobs:
        payload["jobs"] = [_normalize_job(x) for x in client.run_jobs(slug, identity)]
    if receipt is not None:
        bound = receipt.get("pre_dispatch_sha")
        payload["source_binding"] = "matching" if isinstance(bound, str) and bound == remote["head_sha"] else "not-confirmed"
        # A mutable ref can move between dispatch validation and run creation.
        if payload["source_binding"] != "matching":
            payload["warning"] = "Remote run commit differs from pre-dispatch observation; do not qualify release"
    return payload


def watch(client: GitHubActionsClient, slug: str, identity: int, path: Path | None,
          receipt: dict[str, Any] | None, *, max_checks: int = 10, interval: float = 3,
          sleep: Callable[[float], Any] = time.sleep) -> dict[str, Any]:
    if max_checks < 1 or max_checks > 120 or interval < 1 or interval > 300:
        raise GitHubActionsError("Watch bounds: 1..120 checks, 1..300 seconds interval")
    observations = []
    for i in range(max_checks):
        result = snapshot(client, slug, identity, path, receipt)
        observations.append({"status":result["remote"]["status"],
                             "conclusion":result["remote"]["conclusion"],
                             "attempt":result["remote"]["run_attempt"]})
        if result["remote"]["completed"]:
            break
        if i + 1 < max_checks:
            sleep(interval)
    return {"phase":"ACTIONS-03", "checks":len(observations),
            "complete":result["remote"]["completed"], "last":result,
            "history":observations, "polling":"bounded-foreground-no-daemon"}


def remote_logs(client: GitHubActionsClient, slug: str, identity: int,
                *, root: Path | None = None) -> dict[str, Any]:
    # Do not place expiring signed URLs in stdout or persistent metadata.
    url = client.run_log_location(slug, identity)
    target = urlsplit(url)
    host = (target.hostname or "").lower()
    if (target.scheme != "https" or not host or target.port not in (None, 443)
        or target.username or target.password or target.fragment
        or not (host.endswith(".actions.githubusercontent.com")
                or host.endswith(".blob.core.windows.net")
                or host == "objects.githubusercontent.com")):
        raise GitHubActionsError("Unsupported GitHub log archive download host; no credentials forwarded")
    req = Request(url, headers={"User-Agent":"devtool-actions-logs/1"}, method="GET")
    try:
        with client._opener.open(req, timeout=client.timeout) as response:
            if int(response.status) != 200:
                raise GitHubActionsError("GitHub logs download did not succeed")
            # No redirect chaining, no Authorization header, bounded total bytes.
            content = response.read(_LOG_LIMIT + 1)
    except HTTPError as exc:
        raise GitHubActionsError(f"GitHub log archive HTTP {exc.code}") from None
    except (OSError, URLError, TimeoutError):
        raise GitHubActionsError("GitHub logs download failed") from None
    if len(content) > _LOG_LIMIT:
        raise GitHubActionsError("GitHub logs archive exceeds 32 MiB limit")
    import io, zipfile
    if not zipfile.is_zipfile(io.BytesIO(content)):
        raise GitHubActionsError("GitHub logs archive is not a ZIP")
    folder = (root if root is not None else _state_root().parent/"remote-logs")
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    if folder.is_symlink():
        raise GitHubActionsError("Log destination is a symlink")
    digest = hashlib.sha256(content).hexdigest()
    path = folder / (f"{slug.replace('/','_')}-{identity}-{digest[:12]}.zip")
    fd, tmp = tempfile.mkstemp(prefix=".github-log-",dir=folder)
    try:
        with os.fdopen(fd, "wb") as fp:
            os.fchmod(fp.fileno(),0o600)
            fp.write(content)
            fp.flush();os.fsync(fp.fileno())
        os.replace(tmp,path)
        dfd=os.open(folder,os.O_RDONLY)
        try:os.fsync(dfd)
        finally:os.close(dfd)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
    return {"repository":slug,"run_id":identity,"archive":str(path),"sha256":digest,
            "size":len(content),"extracted":False,"qualified_artifact":False}


def control_run(client: GitHubActionsClient, slug: str, identity: int, method: str,
                *, root: Path | None = None) -> dict[str, Any]:
    """Record the non-idempotent intent before POST; never automatically resend."""
    slug=_slug(slug)
    identity=run_id(identity)
    if method not in ("cancel", "rerun", "rerun-failed-jobs"):
        raise GitHubActionsError("Unsupported remote run control")
    if not client._token:
        raise GitHubActionsError("GitHub Actions write token required")
    rid=uuid.uuid4().hex
    path=(root if root is not None else _state_root().parent/"controls")/(rid+".json")
    record={"schema":1,"control_id":rid,"repository":slug,"run_id":identity,
            "method":method,"created_at":datetime.now(timezone.utc).isoformat(),
            "state":"outcome-unknown"}
    _durable_record(path,record)
    try:
        answer=client.run_mutation(slug,identity,method)
    except GitHubDispatchOutcomeUnknown:
        raise GitHubActionsError(f"Remote {method} outcome uncertain (control {rid}); inspect run before retrying") from None
    except GitHubActionsError:
        record["state"]="rejected-by-api"
        _durable_record(path,record)
        raise
    record["state"]="accepted-pending-remote-confirmation"
    try:
        _durable_record(path,record)
    except OSError:
        raise GitHubActionsError(f"GitHub accepted control but journal update failed (control {rid}); do not retry blindly") from None
    return {"control_id":rid,**answer}
