"""Source-bound GitHub Actions projection and canonical-authority adapters.

No GitHub client, auth store, status authority, dispatch or mutation engine is
created here. The adapters delegate to existing Devtool API, durable journal,
source-bound dispatch and log-download authority. Fetches remain bounded.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
import re

RUN_STATES = frozenset({"queued", "in_progress", "completed", "requested", "waiting", "pending"})
TERMINAL_CONCLUSIONS = frozenset({"success", "failure", "cancelled", "timed_out", "skipped", "neutral", "action_required", "startup_failure", "stale"})


class GithubProjectionError(RuntimeError):
    """Only safe, actionable errors should be rendered in the terminal."""


@dataclass(frozen=True, slots=True)
class ActionsSelection:
    repository: str
    workflow_id: int | None = None
    run_id: int | None = None
    limit: int = 30
    run_identity: Mapping[str, Any] | None = None
    workflow_identity: Mapping[str, Any] | None = None
    auth_mode: str = "app"
    native_checks: bool = False
    journal_only: bool = False

    def __post_init__(self) -> None:
        if self.workflow_id is not None and (type(self.workflow_id) is not int or self.workflow_id <= 0):
            raise GithubProjectionError("Invalid workflow ID")
        if self.run_id is not None and (type(self.run_id) is not int or self.run_id <= 0):
            raise GithubProjectionError("Invalid run ID")
        if type(self.limit) is not int or not 1 <= self.limit <= 100:
            raise GithubProjectionError("Run display limit must be 1–100")
        if self.auth_mode not in ("app", "env", "gh", "none"):
            raise GithubProjectionError("Select explicit GitHub authentication")
        if self.run_identity is not None and self.run_identity.get("id") != self.run_id:
            raise GithubProjectionError("Run selection identity mismatch")
        if self.workflow_identity is not None and self.workflow_identity.get("id") != self.workflow_id:
            raise GithubProjectionError("Workflow selection identity mismatch")


def _int(value: Any) -> int | None:
    return value if type(value) is int and value > 0 else None


def _string(value: Any, *, limit: int = 160) -> str:
    """Prevent untrusted API text controlling a terminal or growing without bound."""
    if value is None:
        return ""
    from .logstore import redact_sensitive_text
    clean = re.sub(r"[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]", " ", redact_sensitive_text(str(value)))
    return clean[:limit]


def _workflow(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    identity = _int(raw.get("id"))
    if identity is None:
        return None
    return {
        "id": identity,
        "name": _string(raw.get("name")),
        "state": _string(raw.get("state"), limit=48),
        "path": _string(raw.get("path"), limit=260),
    }


def _run(raw: Mapping[str, Any], repository: str, *, native: bool = False) -> dict[str, Any] | None:
    identity = _int(raw.get("id"))
    if identity is None:
        return None
    observed_repo = raw.get("repository")
    observed_name = observed_repo if native else observed_repo.get("full_name") if isinstance(observed_repo, Mapping) else None
    if not isinstance(observed_name, str) or observed_name.casefold() != repository.casefold():
        raise GithubProjectionError("GitHub run belongs to a different repository")
    status = _string(raw.get("status") or "unknown", limit=40).casefold()
    conclusion = _string(raw.get("conclusion") or "", limit=40).casefold()
    return {
        "id": identity,
        "workflow_id": _int(raw.get("workflow_id")),
        "name": _string(raw.get("name"), limit=200),
        "status": status if status in RUN_STATES else "unknown",
        "conclusion": conclusion if conclusion in TERMINAL_CONCLUSIONS else "",
        "run_attempt": _int(raw.get("run_attempt")),
        "branch": _string(raw.get("head_branch"), limit=160),
        "sha": _string(raw.get("head_sha"), limit=40),
        "head_sha": raw.get("head_sha"),
        "repository": repository,
        "event": _string(raw.get("event"), limit=80),
        "created_at": _string(raw.get("created_at"), limit=48),
        "url": f"https://github.com/{repository}/actions/runs/{identity}",
    }


def _job(raw: Mapping[str, Any], run_id: int, attempt: int | None) -> dict[str, Any] | None:
    identity = _int(raw.get("id"))
    if identity is None:
        return None
    job_attempt = _int(raw.get("run_attempt"))
    if attempt is not None and job_attempt is not None and job_attempt != attempt:
        raise GithubProjectionError("GitHub returned jobs from a different run attempt")
    steps = raw.get("steps") or []
    if not isinstance(steps, list):
        raise GithubProjectionError("GitHub job steps were malformed")
    return {
        "id": identity,
        "name": _string(raw.get("name"), limit=180),
        "status": _string(raw.get("status"), limit=40),
        "conclusion": _string(raw.get("conclusion"), limit=40),
        "attempt": job_attempt,
        "run_attempt": job_attempt,
        "run_id": run_id,
        "steps": [
            {
                "number": step.get("number"),
                "started_at": _string(step.get("started_at"), limit=48),
                "completed_at": _string(step.get("completed_at"), limit=48),
                "name": _string(step.get("name"), limit=180),
                "status": _string(step.get("status"), limit=40),
                "conclusion": _string(step.get("conclusion"), limit=40),
            }
            for step in steps[:300] if isinstance(step, Mapping)
        ],
        "steps_truncated": len(steps) > 300,
    }


def load_github_actions(
    root: Path,
    *,
    selection: ActionsSelection | None = None,
    client: Any = None,
) -> dict[str, Any]:
    """Fetch selected GitHub API facts; no remote writes, no credential logging.

    If ``client`` is provided for testing, it must have the canonical client
    interface. The returned snapshot is a projection, never release evidence.
    """
    from .github_actions import GitHubActionsError, resolve_repository
    from .workspace_actions import workspace_client, workflow_runs, run_jobs

    if selection is not None and selection.native_checks:
        return load_native_checks(root, selection=selection, client=client)
    if selection is None:
        try:
            slug = resolve_repository("", Path(root))
        except GitHubActionsError as exc:
            raise GithubProjectionError(_string(exc, limit=180)) from None
        selection = ActionsSelection(slug)
    else:
        try:
            slug = resolve_repository(selection.repository, Path(root))
        except GitHubActionsError as exc:
            raise GithubProjectionError(_string(exc, limit=180)) from None
    auth_source = selection.auth_mode if client is None else "provided-client"
    try:
        if client is None:
            client = workspace_client(slug, selection.auth_mode)
        workflows_raw = client.workflows(slug)
        if not isinstance(workflows_raw, list) or any(not isinstance(w, Mapping) for w in workflows_raw):
            raise GithubProjectionError("GitHub workflow list was malformed")
        if selection.workflow_id is None:
            runs_raw = client.runs(slug, limit=selection.limit)
        else:
            matches = [w for w in workflows_raw if w.get("id") == selection.workflow_id]
            if len(matches) != 1:
                raise GithubProjectionError("Selected workflow is unavailable or ambiguous")
            identity = selection.workflow_identity or matches[0]
            runs_raw = workflow_runs(client, slug, identity)["items"]
        if not isinstance(workflows_raw, list) or not isinstance(runs_raw, list):
            raise GithubProjectionError("GitHub list response was malformed")
        workflows = [item for raw in workflows_raw[:1000] if isinstance(raw, Mapping) if (item := _workflow(raw))]
        runs = [item for raw in runs_raw[:selection.limit] if isinstance(raw, Mapping) if (item := _run(raw, slug, native=selection.workflow_id is not None))]
        if len({w["id"] for w in workflows}) != len(workflows) or len({r["id"] for r in runs}) != len(runs):
            raise GithubProjectionError("Duplicate native Actions identity")
        if selection.workflow_id is not None:
            runs = [run for run in runs if run["workflow_id"] == selection.workflow_id]
        jobs: list[dict[str, Any]] = []
        selected: dict[str, Any] | None = None
        jobs_scope = "none"
        jobs_truncated = False
        if selection.run_id is not None:
            # A run may be omitted from a bounded recent-results page: fetch its
            # identity directly instead of assuming it disappeared.
            raw = client.get_run(slug, selection.run_id)
            if not isinstance(raw, Mapping):
                raise GithubProjectionError("GitHub run detail was malformed")
            selected = _run(raw, slug)
            if selected is None or selected["id"] != selection.run_id:
                raise GithubProjectionError("GitHub selected run identity mismatch")
            attempt = selected["run_attempt"]
            if attempt is not None:
                if selection.workflow_id is not None and selected["workflow_id"] != selection.workflow_id:
                    raise GithubProjectionError("Selected run is not in selected workflow")
                expected = selection.run_identity or selected
                # The shared authority verifies all identity fields, then rechecks
                # the run after obtaining its exact attempt jobs.
                native = run_jobs(client, slug, expected)
                jobs_raw = native["items"]
                selected = _run(native["run"], slug, native=True)
                jobs_scope = "exact-run-attempt"
            else:
                # A missing attempt cannot be safely represented as current.
                if selection.run_identity is not None:
                    raise GithubProjectionError("Selected run attempt is no longer available")
                jobs_raw = []
                jobs_scope = "unavailable-no-attempt"
            if not isinstance(jobs_raw, list):
                raise GithubProjectionError("GitHub job response was malformed")
            jobs_truncated = len(jobs_raw) >= 200
            jobs = [item for raw in jobs_raw[:200] if isinstance(raw, Mapping)
                    if (item := _job(raw, selection.run_id, attempt))]
    except GitHubActionsError as exc:
        raise GithubProjectionError(_string(exc, limit=180)) from None
    return {
        "repository": slug,
        "authentication": auth_source,
        "workflows": workflows,
        "workflows_limit_reached": len(workflows_raw) >= 1000,
        "runs": runs,
        "runs_limit_reached": len(runs_raw) >= selection.limit,
        "selected_run": selected,
        "jobs": jobs,
        "jobs_scope": jobs_scope,
        "jobs_limit_reached": jobs_truncated,
        "read_only": True,
        "release_qualified": False,
    }



def _run_key(run: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(run.get(k) for k in ("id", "run_attempt", "head_sha", "workflow_id", "event"))


def _native_inventory(root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Read the existing native observer journal; never start another CI poller."""
    from .operator_status import latest_for_repo, project_delivery_status
    from .delivery_session import status
    projection = latest_for_repo(root)
    sid = projection.get("session", {}).get("id")
    if not sid:
        raise GithubProjectionError("No native delivery session for this worktree")
    record = status(sid)
    checked = project_delivery_status(record)
    ci = record.get("ci") or {}
    source = record.get("head_sha")
    if (Path(record.get("repo_root", "")).resolve() != Path(root).resolve()
            or record.get("source_binding") != "matching"
            or checked.get("pr", {}).get("state") != "RECORDED_IDENTITY"
            or ci.get("source_sha") != source):
        raise GithubProjectionError("Native PR-check observation is not bound to this source")
    slug = record.get("base_repository") or record["repository"]
    context = {"session_id": sid, "repository": slug, "source_sha": source,
               "pr_number": checked["pr"]["number"]}
    raw_runs = ci.get("runs") or []
    if not isinstance(raw_runs, list):
        raise GithubProjectionError("Native CI run inventory malformed")
    rows = []
    for raw in raw_runs:
        if not isinstance(raw, Mapping):
            raise GithubProjectionError("Native CI run identity malformed")
        row = _run({**raw, "repository": slug, "run_attempt": raw.get("attempt"),
                    "head_branch": record.get("head")}, slug, native=True)
        if (row is None or not row["run_attempt"] or not row["workflow_id"] or not row["event"]
                or not re.fullmatch(r"[0-9a-f]{40}", str(row["head_sha"] or ""))):
            raise GithubProjectionError("Native CI run lacks exact attempt identity")
        row["native_context"] = context
        rows.append(row)
    if len({row["id"] for row in rows}) != len(rows):
        raise GithubProjectionError("Duplicate native CI run identity")
    return context, rows


def _check_native_selection(root: Path, run: Mapping[str, Any]) -> str:
    context, rows = _native_inventory(root)
    if run.get("native_context") != context or not any(_run_key(row) == _run_key(run) for row in rows):
        raise GithubProjectionError("Native session or selected CI run changed")
    return context["repository"]


def load_native_checks(root: Path, *, selection: ActionsSelection, client: Any = None) -> dict[str, Any]:
    from .workspace_actions import run_jobs
    from .github_actions import GitHubActionsError
    context, rows = _native_inventory(root)
    slug = context["repository"]
    selected = None
    jobs = []
    if selection.run_id is not None:
        matches = [r for r in rows if r["id"] == selection.run_id]
        if len(matches) != 1:
            raise GithubProjectionError("Selected native CI run no longer observed")
        expected = selection.run_identity or matches[0]
        _check_native_selection(root, expected)
        if selection.journal_only:
            return {"repository": slug, "native_context": context, "authentication": selection.auth_mode,
                    "workflows": [], "runs": rows[:selection.limit], "selected_run": matches[0],
                    "journal_only": True, "runs_limit_reached": len(rows) > selection.limit,
                    "read_only": True, "release_qualified": False}
        try:
            client = _scoped_client(slug, selection.auth_mode, "actions_read", client)
            result = run_jobs(client, slug, expected)
            _check_native_selection(root, expected)
            selected = {**_run(result["run"], slug, native=True), "native_context": context}
            jobs = [_job(j, selected["id"], selected["run_attempt"]) for j in result["items"][:200]]
        except GitHubActionsError as exc:
            raise GithubProjectionError(_string(exc, limit=180)) from None
    return {"repository": slug, "native_context": context, "authentication": selection.auth_mode,
            "workflows": [], "runs": rows[:selection.limit], "selected_run": selected,
            "jobs": jobs, "jobs_scope": "exact-run-attempt" if selected else "none",
            "runs_limit_reached": len(rows) > selection.limit, "jobs_limit_reached": len(jobs) >= 200,
            "read_only": True, "release_qualified": False}

def search_actions(rows: list[Mapping[str, Any]], text: str) -> list[dict[str, Any]]:
    needle = _string(text, limit=120).casefold().strip()
    if not needle:
        return [dict(row) for row in rows]
    words = needle.split()
    return [dict(row) for row in rows if all(word in " ".join(str(v) for v in row.values()).casefold() for word in words)]


def display_status(item: Mapping[str, Any]) -> str:
    state = _string(item.get("status") or item.get("state") or "unknown", limit=30).lower()
    conclusion = _string(item.get("conclusion"), limit=30).lower()
    return conclusion if state == "completed" and conclusion else state


__all__ = ["ActionsSelection", "GithubProjectionError", "load_github_actions", "search_actions", "display_status"]


def _scoped_client(repository: str, auth_mode: str, capability: str, client: Any = None) -> Any:
    if client is not None:
        return client
    if auth_mode == "app":
        from .github_app import make_app_client
        return make_app_client(repository, capability)
    from .workspace_actions import workspace_client
    if auth_mode not in ("env", "gh", "none") or (auth_mode == "none" and capability != "actions_read"):
        raise GithubProjectionError("Explicit authentication required for this operation")
    return workspace_client(repository, auth_mode)


def read_github_log_preview(
    root: Path, *, run_id: int, attempt: int, head_sha: str,
    client: Any = None, max_lines: int = 350, auth_mode: str = "app",
    run: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Read the exact-attempt archive through the shared native authority."""
    from .delivery_remote_logs import fetch_selected_run_logs
    from .github_actions import GitHubActionsError, resolve_repository
    if type(run_id) is not int or run_id <= 0 or type(attempt) is not int or attempt <= 0:
        raise GithubProjectionError("Select an exact positive run and attempt before fetching logs")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", head_sha or ""):
        raise GithubProjectionError("Selected run lacks a verified 40-character head SHA")
    if not 1 <= max_lines <= 500:
        raise GithubProjectionError("Invalid log preview bound")
    try:
        slug = _check_native_selection(root, run) if run and run.get("native_context") else resolve_repository("", Path(root))
        client = _scoped_client(slug, auth_mode, "actions_read", client)
        identity = dict(run or {"id": run_id, "run_attempt": attempt, "head_sha": head_sha})
        if (identity.get("id"), identity.get("run_attempt"), identity.get("head_sha")) != (run_id, attempt, head_sha):
            raise GithubProjectionError("Selected log identity mismatch")
        lines = fetch_selected_run_logs(client, slug, identity)
        if identity.get("native_context"):
            _check_native_selection(root, identity)
    except GitHubActionsError as exc:
        raise GithubProjectionError(_string(exc, limit=180)) from None
    return {"repository": slug, "run_id": run_id, "run_attempt": attempt,
            "sha": head_sha.lower(), "lines": list(lines[-max_lines:]),
            "truncated": len(lines) > max_lines, "canonical_archive": True,
            "qualified_artifact": False}


def read_github_job_logs(root: Path, *, run: Mapping[str, Any], job: Mapping[str, Any],
                         client: Any = None, auth_mode: str = "app") -> dict[str, Any]:
    """Use the same verified exact-job logs as the Rich workspace."""
    from .github_actions import GitHubActionsError, resolve_repository
    from .workspace_actions import job_logs
    try:
        slug = _check_native_selection(root, run) if run.get("native_context") else resolve_repository("", Path(root))
        client = _scoped_client(slug, auth_mode, "actions_read", client)
        result = job_logs(client, slug, run, job)
        if run.get("native_context"):
            _check_native_selection(root, run)
    except GitHubActionsError as exc:
        raise GithubProjectionError(_string(exc, limit=180)) from None
    return {"repository": slug, "run_id": run["id"], "run_attempt": run["run_attempt"],
            "sha": run["head_sha"].lower(), "job_id": job["id"],
            "lines": list(result["log_lines"]), "truncated": False, "qualified_artifact": False}


def control_github_run(root: Path, *, run_id: int, attempt: int,
                       head_sha: str, action: str, client: Any = None, auth_mode: str = "app") -> dict[str, Any]:
    """One explicit remote control delegated to Devtool's journal-first authority.

    This function is called only after a separate Textual confirmation modal.
    No automatic retry occurs after an uncertain GitHub POST outcome.
    """
    from .actions_runs import control_run
    from .github_actions import GitHubActionsError, resolve_repository
    if action not in ("cancel", "rerun", "rerun-failed-jobs"):
        raise GithubProjectionError("Unsupported remote run control")
    if type(run_id) is not int or run_id <= 0 or type(attempt) is not int or attempt <= 0:
        raise GithubProjectionError("Run control requires a positive run and exact attempt")
    if not re.fullmatch(r"[0-9a-fA-F]{40}",head_sha or ""):
        raise GithubProjectionError("Run control requires an exact head SHA")
    try:
        slug=resolve_repository("",Path(root))
        client = _scoped_client(slug, auth_mode, "actions_control", client)
        raw=client.get_run(slug,run_id)
        if not isinstance(raw,Mapping):
            raise GithubProjectionError("Run detail cannot be verified")
        current=_run(raw,slug)
        if (current is None or current["id"] != run_id
                or current["run_attempt"] != attempt
                or current["sha"].lower() != head_sha.lower()):
            raise GithubProjectionError("Run changed since selection; no remote control sent")
        return control_run(client,slug,run_id,action)
    except GitHubActionsError as exc:
        raise GithubProjectionError(_string(exc,limit=180)) from None


def dispatch_github_workflow(root: Path, *, workflow_id: int, ref: str,
                             inputs: Mapping[str, Any], client: Any = None, auth_mode: str = "app") -> dict[str, Any]:
    """One confirmed dispatch through canonical source-bound ACTIONS-02 journal.

    Input schema, remote ref, source HEAD, existing uncertain dispatch and
    non-idempotent POST are entirely validated/owned by `actions_dispatch`.
    """
    from .actions_dispatch import dispatch_workflow, local_head_for_dispatch
    from .github_actions import GitHubActionsError, resolve_repository
    if type(workflow_id) is not int or workflow_id <= 0:
        raise GithubProjectionError("Select an exact workflow before dispatch")
    if not isinstance(ref,str) or not ref.strip():
        raise GithubProjectionError("Select a branch or tag for dispatch")
    if not isinstance(inputs,Mapping) or any(not isinstance(k,str) or not isinstance(v,(str,int,float,bool))
                                             or v is None for k,v in inputs.items()):
        raise GithubProjectionError("Dispatch inputs must be a JSON object of scalars")
    values={k:(str(v).lower() if isinstance(v,bool) else str(v)) for k,v in inputs.items()}
    try:
        slug=resolve_repository("",Path(root))
        client = _scoped_client(slug, auth_mode, "actions_dispatch", client)
        expected_sha=local_head_for_dispatch(Path(root))
        return dispatch_workflow(client,slug,str(workflow_id),ref.strip(),values,
                                 execute=True,expected_source_sha=expected_sha,
                                 resume_if_existing=True)
    except GitHubActionsError as exc:
        raise GithubProjectionError(_string(exc,limit=180)) from None
