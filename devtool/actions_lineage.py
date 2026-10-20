"""R04 read-only, source/attempt-bound projection of existing Actions-06 evidence.

NOT an execution scheduler, provider verification, release seal, or durable store.
Offline journal observations are never silently promoted to remote authority.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any

from .actions_dispatch import _state_root
from .actions_pipeline import _read as read_pipeline
from .actions_runs import _read_dispatch_receipt, run_id
from .github_actions import GitHubActionsError, _slug

SHA40 = re.compile(r"[a-f0-9]{40}\Z")
SHA64 = re.compile(r"[a-f0-9]{64}\Z")
HEX32 = re.compile(r"[a-f0-9]{32}\Z")
MAX_REGISTRY = 8 * 1024 * 1024
MAX_ARTIFACT = 256 * 1024 * 1024
STATES = frozenset({"NOT_STARTED", "LOCAL_OBSERVED", "PREFLIGHT_BLOCKED",
                     "DISPATCH_UNCERTAIN", "WAITING_REMOTE", "CI_FAILED",
                     "CI_PASS_UNVERIFIED", "CI_PASS_REMOTE_OBSERVED", "ARCHIVE_NOT_VERIFIED",
                     "ARCHIVE_LOCAL_VERIFIED", "UNKNOWN"})


def _current_head(root: Path) -> str | None:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                                capture_output=True, text=True, timeout=4, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = result.stdout.strip().lower()
    return value if result.returncode == 0 and SHA40.fullmatch(value) else None


def _safe_registry(root: Path) -> list[dict[str, Any]]:
    # Unlike load_registry(), this reader NEVER creates a lock, backup or folder.
    folder = root
    for part in (".devtool", "artifacts", "registry.json"):
        folder = folder / part
        if folder.is_symlink():
            raise GitHubActionsError("R04 artifact registry path contains a symlink")
    if not folder.exists():
        return []
    if not folder.is_file() or folder.stat().st_size > MAX_REGISTRY:
        raise GitHubActionsError("R04 artifact registry is oversized or invalid")
    try:
        payload = json.loads(folder.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise GitHubActionsError("R04 artifact registry unreadable; no evidence promoted") from None
    if (not isinstance(payload, dict) or type(payload.get("schema_version", 1)) is not int
            or not isinstance(payload.get("items"), list) or len(payload["items"]) > 10000):
        raise GitHubActionsError("R04 artifact registry has invalid structure")
    return payload["items"]


def _archive(root: Path, slug: str, sha: str, rid: int | None,
             attempt: int | None, aid: int, entries: list[dict[str, Any]]) -> dict[str, Any]:
    result = {"id": aid, "sha256": None, "registry_id": None,
              "state": "ARCHIVE_NOT_VERIFIED", "digest_verified_locally": False,
              "remote_authenticated": False, "signing_verified": False, "release_qualified": False}
    if rid is None or attempt is None:
        return result
    matches = []
    for item in entries:
        if not isinstance(item, dict) or not isinstance(item.get("metadata"), dict):
            continue
        meta = item["metadata"]
        if meta.get("remote_artifact_id") != aid:
            continue
        # A same-ID registry item for another owner/run/attempt is not a match;
        # never borrow it for the selected lineage.
        if (str(meta.get("remote_repository", "")).casefold() != slug.casefold()
            or meta.get("remote_run_id") != rid
            or meta.get("remote_run_attempt") != attempt
            or meta.get("remote_head_sha") != sha):
            raise GitHubActionsError("R04 artifact registry provenance mismatch")
        matches.append(item)
    if not matches:
        return result
    if len(matches) != 1:
        raise GitHubActionsError("R04 duplicate artifact evidence identity")
    item = matches[0]
    digest = str(item.get("sha256") or "").lower()
    meta = item["metadata"]
    if not SHA64.fullmatch(digest) or meta.get("remote_digest") != "sha256:" + digest:
        raise GitHubActionsError("R04 artifact registry digest mismatch")
    rel = item.get("path")
    if not isinstance(rel, str) or not rel or Path(rel).is_absolute():
        raise GitHubActionsError("R04 artifact registry path invalid")
    # Only the native GitHub download directory is eligible; never hash arbitrary files.
    base = root / ".devtool" / "artifacts" / "github-downloads"
    if base.is_symlink() or base.parent.is_symlink() or base.parent.parent.is_symlink():
        raise GitHubActionsError("R04 downloaded archive folder is a symlink")
    file = root / rel
    if file.is_symlink() or file.resolve().parent != base.resolve():
        raise GitHubActionsError("R04 artifact evidence path escapes canonical download storage")
    if not file.is_file() or file.stat().st_size > MAX_ARTIFACT:
        return result
    h = hashlib.sha256()
    try:
        with file.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                h.update(block)
    except OSError:
        return result
    if h.hexdigest() != digest:
        raise GitHubActionsError("R04 artifact bytes do not match registry digest")
    from .actions_artifacts import check_zip
    check_zip(file)  # CRC, path/type, member count and expansion checks, no extraction.
    result.update(state="ARCHIVE_LOCAL_VERIFIED", sha256=digest,
                  registry_id=str(item.get("id") or ""), digest_verified_locally=True)
    return result


def _exo_child(root: Path, child_id: object) -> dict[str, Any]:
    if not isinstance(child_id, str) or not HEX32.fullmatch(child_id):
        raise GitHubActionsError("R04 child RUN identity is malformed")
    from .core_runtime import private_run_root
    from .run_store import RunCatalog
    catalog_dir = private_run_root(root)
    for part in (catalog_dir, catalog_dir.parent, catalog_dir / 'catalog.json'):
        if part.is_symlink():
            raise GitHubActionsError('R04 RUN catalog path is a symlink')
    try:
        catalog = RunCatalog.open(catalog_dir, create=False)
        row = catalog.runs.get(child_id)
    except (OSError, ValueError, TypeError):
        raise GitHubActionsError("R04 child RUN catalog unreadable") from None
    if not isinstance(row, dict) or row.get('run_id') != child_id or row.get('command_id') != 'invoke':
        raise GitHubActionsError("R04 child RUN identity does not match canonical catalog")
    success = (row.get('status') == 'succeeded'
               and type(row.get('exit_code')) is int and row['exit_code'] == 0
               and isinstance(row.get('finished_at'), str) and bool(row['finished_at']))
    if not success:
        raise GitHubActionsError("R04 child RUN has no finalized successful execution")
    return {'exo_run_id':child_id, 'exo_catalog_verified':True}


def _live_runs(client: Any, slug: str, sha: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Explicit GET-only observation, never attestation or release authority."""
    from .actions_runs import _normalize_run
    by_key: dict[tuple[int, int], dict[str, Any]] = {}
    for row in rows:
        if row['kind'] not in ('github-result', 'github-archive'):
            continue
        rid, attempt = row.get('run_id'), row.get('run_attempt')
        if rid is None:
            row['live_observation'] = 'UNAVAILABLE_UNCORRELATED'
            continue
        # An original dispatch receipt establishes the run ID. An independent
        # GET can observe its *current* attempt even when no ACTIONS-03 watch
        # has persisted one. Never write that observation back as a pin.
        key = (rid, attempt)
        if key not in by_key:
            remote = _normalize_run(client.get_run(slug, rid), slug, rid)
            if (remote.get('repository', '').casefold() != slug.casefold()
                or remote.get('head_sha', '').lower() != sha
                or remote.get('event') != 'workflow_dispatch'
                or type(remote.get('workflow_id')) is not int
                or type(remote.get('run_attempt')) is not int
                or remote['run_attempt'] <= 0
                or (attempt is not None and remote['run_attempt'] != attempt)):
                raise GitHubActionsError('R04 live GitHub run/source/attempt identity mismatch')
            by_key[key] = remote
        remote = by_key[key]
        # Exact request workflow identity from the pinned dispatch row.
        wid = row.get('workflow_id')
        if type(wid) is not int or wid != remote['workflow_id']:
            raise GitHubActionsError('R04 live GitHub workflow differs from original request')
        row['live_observation'] = {'status':remote['status'],
                                   'conclusion':remote['conclusion'],
                                   'run_attempt':remote['run_attempt'],
                                   'attempt_locally_pinned':attempt is not None,
                                   'source_matched':True,
                                   'scope':'authenticated-api-get-only'}
        if row['kind'] == 'github-result':
            if remote['completed'] and remote['conclusion'] == 'success' and row['journal_state'] == 'success':
                row['state'] = 'CI_PASS_REMOTE_OBSERVED'
            elif remote['completed'] and remote['conclusion'] != 'success':
                row['state'] = 'CI_FAILED'
            else:
                row['state'] = 'WAITING_REMOTE'
    return {'observed_runs':len(by_key), 'authentication':'credentialed-read',
            'authority':'read-only-provider-observation-not-source-attestation'}


def _receipt_binding(request: str, slug: str, sha: str, node: dict[str, Any],
                     dispatches: Path | None) -> dict[str, Any]:
    if not HEX32.fullmatch(request):
        raise GitHubActionsError("R04 dispatch request ID is malformed")
    _, rec = _read_dispatch_receipt(request, dispatches if dispatches is not None else _state_root())
    if (_slug(rec["repository"]).casefold() != slug.casefold()
            or str(rec.get("pre_dispatch_sha", "")).lower() != sha):
        raise GitHubActionsError("R04 dispatch request differs in repository or source")
    wid = rec.get("workflow_id")
    if type(wid) is not int or wid <= 0 or wid != node.get("workflow_id"):
        raise GitHubActionsError("R04 dispatch workflow identity mismatch")
    actual = rec.get("run_id")
    journal_run = node.get("run_id")
    if actual is not None and journal_run is not None and run_id(actual) != run_id(journal_run):
        raise GitHubActionsError("R04 pipeline journal and request disagree on run identity")
    if actual is None and journal_run is not None:
        raise GitHubActionsError("R04 journal run was not correlated by original request")
    attempt = None
    obs = rec.get("last_observation")
    if obs is not None:
        if not isinstance(obs, dict):
            raise GitHubActionsError("R04 malformed stored remote observation")
        if actual is None or obs.get("id") != run_id(actual):
            raise GitHubActionsError("R04 observed run does not match original request")
        if str(obs.get("head_sha", "")).lower() != sha:
            raise GitHubActionsError("R04 observed source SHA mismatch")
        at = obs.get("run_attempt")
        if at is not None and (type(at) is not int or at <= 0):
            raise GitHubActionsError("R04 stored run attempt invalid")
        attempt = at
        if str(obs.get("status") or "") not in ("queued", "in_progress", "completed", "waiting", "requested", "pending", "unknown"):
            raise GitHubActionsError("R04 remote observation status invalid")
        if str(obs.get("conclusion") or "") not in ("", "success", "failure", "cancelled", "timed_out", "neutral", "skipped", "action_required", "stale"):
            raise GitHubActionsError("R04 remote observation conclusion invalid")
    return {"request_id": request, "run_id": run_id(actual) if actual is not None else None,
            "run_attempt": attempt,
            "remote_observation": {"status": str(obs.get("status") or "unknown"),
                                   "conclusion": str(obs.get("conclusion") or "unknown")}
                                   if obs is not None else None,
            "receipt_source_matched": True, "remote_authenticated": False}


def lineage_snapshot(root: Path, slug: str, pipeline_id: str, *,
                     pipelines: Path | None = None, dispatches: Path | None = None,
                     live_client: Any | None = None) -> dict[str, Any]:
    """Read canonical journals. Never perform network calls or write state."""
    root = root.resolve()
    slug = _slug(slug)
    _, journal = read_pipeline(pipeline_id, state_root=pipelines)
    if (_slug(str(journal.get("repository") or "")).casefold() != slug.casefold()
        or str(journal.get("repo_root") or "") != str(root)):
        raise GitHubActionsError("R04 pipeline is bound to a different repository/worktree")
    sha = journal["head_sha"].lower()
    registry = None
    rows = []
    # Prefer exact current manifest edges. If the tracked manifest changed
    # since the journal was created, do not use its replacement as authority.
    manifest_edges: dict[str, str] = {}
    from .actions_pipeline import _manifest
    manifest_file = journal.get("manifest_file")
    if isinstance(manifest_file, str):
        try:
            manifest, digest = _manifest(root, manifest_file)
            if digest == journal.get("manifest_sha256"):
                manifest_edges = {n["id"]:n["from"] for n in manifest["nodes"]
                                  if n["kind"] in ("github-result", "github-archive")}
        except (GitHubActionsError, OSError):
            pass  # Historical evidence may outlive the original manifest.
    dispatch_nodes: dict[str, dict[str, Any]] = {}
    current_head = _current_head(root)
    for nid in journal["node_order"]:
        raw = journal["nodes"][nid]
        kind, raw_state = raw.get("kind"), raw["state"]
        row: dict[str, Any] = {"id":nid,"kind":kind,"journal_state":raw_state,
                               "state":"UNKNOWN", "exo_run_id":None,
                               "verified_remote":False, "qualified_device":False,
                               "qualified_release":False}
        if kind in ("local-git", "local-invoke"):
            row["state"] = "LOCAL_OBSERVED" if raw_state == "success" else (
                "NOT_STARTED" if raw_state == "pending" else "PREFLIGHT_BLOCKED" if raw_state in ("failure", "blocked") else "UNKNOWN")
            if kind == 'local-invoke' and raw.get('exo_run_id') is not None:
                if raw_state != 'success':
                    raise GitHubActionsError('R04 child RUN evidence conflicts with pipeline node state')
                row.update(_exo_child(root, raw['exo_run_id']))
            else:
                row['note'] = 'Historical node without stored RUN-V3 child; identity unknown'
        elif kind == "github-dispatch":
            request = raw.get("request_id")
            if request is not None:
                if not isinstance(request, str):
                    raise GitHubActionsError("R04 dispatch identity must be a string")
                bound = _receipt_binding(request,slug,sha,raw,dispatches)
                dispatch_nodes[nid] = bound
                row.update(bound)
            else:
                dispatch_nodes[nid] = {"request_id":None,"run_id":None,"run_attempt":None,
                                        "remote_authenticated":False}
                row.update(dispatch_nodes[nid])
                if raw.get("run_id") is not None:
                    raise GitHubActionsError("R04 no run correlation without exact dispatch receipt")
            row["state"] = "DISPATCH_UNCERTAIN" if raw_state == "uncertain" or (raw_state == "success" and not row.get("run_id")) else (
                "NOT_STARTED" if raw_state == "pending" else "PREFLIGHT_BLOCKED" if raw_state in ("failure","blocked") else "WAITING_REMOTE")
        elif kind in ("github-result", "github-archive"):
            # The pipeline manifest owns this relationship. Its "from" link is
            # stored in the manifest, not the result node. Resolve from the
            # unique preceding dispatch when possible, never pick 'latest'.
            selected = manifest_edges.get(nid)
            preceding = ([selected] if selected in dispatch_nodes else
                         [x for x in journal["node_order"] if x in dispatch_nodes]
                         if sum(journal["nodes"][key].get("kind") == "github-dispatch"
                                for key in journal["node_order"]) == 1 else [])
            if len(preceding) != 1:
                row["state"] = "UNKNOWN"
                row["note"] = "Ambiguous dispatch source; multiple dispatches require explicit manifest edge"
            else:
                bound = dispatch_nodes[preceding[0]]
                if raw.get("run_id") is not None and (bound.get("run_id") is None or
                    run_id(raw["run_id"]) != bound["run_id"]):
                    raise GitHubActionsError("R04 remote node run differs from dispatch")
                row.update({"request_id":bound.get("request_id"),"run_id":bound.get("run_id"),
                            "run_attempt":bound.get("run_attempt"),
                            "workflow_id":journal["nodes"][preceding[0]].get("workflow_id")})
                obs = bound.get("remote_observation")
                if kind == "github-result":
                    row["state"] = ("CI_FAILED" if raw_state in ("failure","blocked","cancelled") else
                                    "CI_PASS_UNVERIFIED" if raw_state == "success" and obs is not None and obs.get("status") == "completed" and obs.get("conclusion") == "success" and bound.get("run_attempt") is not None else
                                    "NOT_STARTED" if raw_state == "pending" else "WAITING_REMOTE")
                else:
                    row["state"] = "ARCHIVE_NOT_VERIFIED"
                    aid = raw.get("artifact_id")
                    if aid is not None:
                        if type(aid) is not int or aid <= 0:
                            raise GitHubActionsError("R04 invalid archive node artifact ID")
                        if registry is None:
                            registry = _safe_registry(root)
                        row["artifact"] = _archive(root,slug,sha,bound.get("run_id"),
                                                  bound.get("run_attempt"),aid,registry)
                        row["state"] = row["artifact"]["state"]
        else:
            raise GitHubActionsError("R04 unsupported journal node kind")
        if row["state"] not in STATES:
            raise GitHubActionsError("R04 unknown projected state")
        rows.append(row)
    live = _live_runs(live_client, slug, sha, rows) if live_client is not None else None
    return {"schema_version":2,"phase":"ACTIONS-R04","pipeline_id":pipeline_id,
            "repository":slug,"source_sha":sha,"source_matches_current":current_head == sha,
            "current_head":current_head,"read_only":True,"remote_dispatch":False,
            "remote_verified":False,"device_qualified":False,"release_qualified":False,
            "live_observation":live,"rows":rows,"qualification": {"ci":("live_api_observed_not_attested" if live is not None else "not_authenticated_in_offline_projection"),
            "device":"NOT_RUN", "release":"BLOCKED_NOT_QUALIFIED"},
            "note":"Read-only source-bound journal and optional live API observations; GitHub API success never qualifies source attestation, device or release."}


def lineage_text(view: dict[str, Any], *, width: int = 60) -> str:
    if type(width) is not int or width < 40 or width > 240:
        raise ValueError("R04 line width must be between 40 and 240")
    lines = ["Actions lineage (read-only evidence)",
             "Pipeline: " + str(view["pipeline_id"]),
             "Source: " + str(view["source_sha"]),
             "Current source: " + ("matching" if view["source_matches_current"] else "historical/unknown")]
    for row in view["rows"]:
        state = row["state"]
        label = str(row["id"])
        start = f" {state} "
        available = max(4, width - len(start) - 2)
        lines.append("-" + start + (label if len(label) <= available else label[:available-1] + "…"))
        if row.get("run_id") is not None:
            lines.append("  Run: " + str(row["run_id"]) + "/" + str(row.get("run_attempt") or "?"))
    if view.get("live_observation"):
        lines.append("CI API: LIVE OBSERVATION · not attestation")
    else:
        lines.append("CI API: OFFLINE · not attestation")
    lines.append("Device: NOT QUALIFIED")
    lines.append("Release: NOT QUALIFIED")
    return "\n".join(lines)
