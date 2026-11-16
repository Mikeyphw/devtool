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

    def __post_init__(self) -> None:
        if self.workflow_id is not None and (type(self.workflow_id) is not int or self.workflow_id <= 0):
            raise GithubProjectionError("Invalid workflow ID")
        if self.run_id is not None and (type(self.run_id) is not int or self.run_id <= 0):
            raise GithubProjectionError("Invalid run ID")
        if type(self.limit) is not int or not 1 <= self.limit <= 100:
            raise GithubProjectionError("Run display limit must be 1–100")


def _int(value: Any) -> int | None:
    return value if type(value) is int and value > 0 else None


def _string(value: Any, *, limit: int = 160) -> str:
    """Prevent untrusted API text controlling a terminal or growing without bound."""
    if value is None:
        return ""
    clean = re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", str(value))
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


def _run(raw: Mapping[str, Any], repository: str) -> dict[str, Any] | None:
    identity = _int(raw.get("id"))
    if identity is None:
        return None
    observed_repo = raw.get("repository")
    if isinstance(observed_repo, Mapping) and observed_repo.get("full_name"):
        if str(observed_repo["full_name"]).casefold() != repository.casefold():
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
        "event": _string(raw.get("event"), limit=60),
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
        "run_id": run_id,
        "steps": [
            {
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
    from .github_actions import GitHubActionsClient, GitHubActionsError, resolve_repository, resolve_token

    if selection is None:
        try:
            slug = resolve_repository("", Path(root))
        except GitHubActionsError as exc:
            raise GithubProjectionError(str(exc)) from None
        selection = ActionsSelection(slug)
    else:
        try:
            slug = resolve_repository(selection.repository, Path(root))
        except GitHubActionsError as exc:
            raise GithubProjectionError(str(exc)) from None
    if client is None:
        token, auth_source = resolve_token("auto")
        client = GitHubActionsClient(token)
    else:
        auth_source = "provided-client"
    try:
        workflows_raw = client.workflows(slug)
        runs_raw = client.runs(slug, limit=selection.limit)
        if not isinstance(workflows_raw, list) or not isinstance(runs_raw, list):
            raise GithubProjectionError("GitHub list response was malformed")
        workflows = [item for raw in workflows_raw[:1000] if isinstance(raw, Mapping) if (item := _workflow(raw))]
        runs = [item for raw in runs_raw[:selection.limit] if isinstance(raw, Mapping) if (item := _run(raw, slug))]
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
                jobs_raw = client.run_attempt_jobs(slug, selection.run_id, attempt)
                jobs_scope = "exact-run-attempt"
            else:
                # A missing attempt cannot be safely represented as current.
                jobs_raw = []
                jobs_scope = "unavailable-no-attempt"
            if not isinstance(jobs_raw, list):
                raise GithubProjectionError("GitHub job response was malformed")
            jobs_truncated = len(jobs_raw) >= 200
            jobs = [item for raw in jobs_raw[:200] if isinstance(raw, Mapping)
                    if (item := _job(raw, selection.run_id, attempt))]
    except GitHubActionsError as exc:
        raise GithubProjectionError(str(exc)) from None
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


def read_github_log_preview(
    root: Path, *, run_id: int, attempt: int, head_sha: str,
    client: Any = None, max_lines: int = 350,
) -> dict[str, Any]:
    """Explicit, bounded preview via the existing canonical remote-logs authority.

    No signed redirect URL, token or archive bytes is emitted in the UI state.
    Log files are *never extracted*: the canonical safe ZIP is opened read-only.
    A run changing attempts while the log archive downloads is refused.
    """
    import zipfile
    from .actions_runs import remote_logs
    from .github_actions import GitHubActionsClient, GitHubActionsError, resolve_repository, resolve_token

    if type(run_id) is not int or run_id <= 0 or type(attempt) is not int or attempt <= 0:
        raise GithubProjectionError("Select an exact positive run and attempt before fetching logs")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", head_sha or ""):
        raise GithubProjectionError("Selected run lacks a verified 40-character head SHA")
    if not 1 <= max_lines <= 500:
        raise GithubProjectionError("Invalid log preview bound")
    try:
        slug = resolve_repository("", Path(root))
    except GitHubActionsError as exc:
        raise GithubProjectionError(_string(exc, limit=180)) from None
    if client is None:
        token, _source = resolve_token("auto")
        client = GitHubActionsClient(token)

    def verified_run() -> None:
        raw = client.get_run(slug, run_id)
        if not isinstance(raw, Mapping):
            raise GithubProjectionError("Run identity response is malformed")
        record = _run(raw, slug)
        if (record is None or record["id"] != run_id or record["run_attempt"] != attempt
                or record["sha"].casefold() != head_sha.casefold()):
            raise GithubProjectionError("Run identity, HEAD or attempt changed; reselect before fetching logs")

    try:
        verified_run()
        # This delegated helper owns redirect validation, auth boundaries,
        # request limits and durable archive writes. Do not reimplement them.
        receipt = remote_logs(client, slug, run_id)
        verified_run()
    except GitHubActionsError as exc:
        raise GithubProjectionError(_string(exc, limit=180)) from None
    archive = Path(str(receipt.get("archive") or ""))
    if archive.is_symlink() or not archive.is_file():
        raise GithubProjectionError("Canonical log archive is unavailable")
    lines: list[str] = []
    partial = False
    try:
        with zipfile.ZipFile(archive) as z:
            members = [item for item in z.infolist() if not item.is_dir()]
            # Each member is individually read to a small bounded prefix; an
            # attacker-supplied ZIP cannot exhaust RAM through high expansion.
            if len(members) > 16:
                partial = True
            for member in members[:16]:
                if member.filename.startswith("/") or ".." in Path(member.filename).parts:
                    partial = True
                    continue
                title = _string(member.filename, limit=140)
                lines.append(f"── {title} ──")
                with z.open(member) as stream:
                    raw = stream.read(65_537)
                if len(raw) > 65_536 or member.file_size > 65_536:
                    raw = raw[:65_536]
                    partial = True
                for line in raw.decode("utf-8", "replace").splitlines():
                    lines.append(_string(line, limit=500))
                    if len(lines) > max_lines:
                        lines = lines[-max_lines:]
                        partial = True
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError, EOFError) as exc:
        raise GithubProjectionError("Canonical GitHub log ZIP is unreadable (" + type(exc).__name__ + ")") from None
    return {
        "repository": slug, "run_id": run_id, "run_attempt": attempt,
        "sha": head_sha.lower(), "lines": lines[-max_lines:],
        "truncated": partial, "canonical_archive": True,
        "qualified_artifact": False,
    }


def control_github_run(root: Path, *, run_id: int, attempt: int,
                       head_sha: str, action: str, client: Any = None) -> dict[str, Any]:
    """One explicit remote control delegated to Devtool's journal-first authority.

    This function is called only after a separate Textual confirmation modal.
    No automatic retry occurs after an uncertain GitHub POST outcome.
    """
    from .actions_runs import control_run
    from .github_actions import GitHubActionsClient, GitHubActionsError, resolve_repository, resolve_token
    if action not in ("cancel", "rerun", "rerun-failed-jobs"):
        raise GithubProjectionError("Unsupported remote run control")
    if type(run_id) is not int or run_id <= 0 or type(attempt) is not int or attempt <= 0:
        raise GithubProjectionError("Run control requires a positive run and exact attempt")
    if not re.fullmatch(r"[0-9a-fA-F]{40}",head_sha or ""):
        raise GithubProjectionError("Run control requires an exact head SHA")
    try:
        slug=resolve_repository("",Path(root))
        if client is None:
            token,_source=resolve_token("auto")
            client=GitHubActionsClient(token)
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
                             inputs: Mapping[str, Any], client: Any = None) -> dict[str, Any]:
    """One confirmed dispatch through canonical source-bound ACTIONS-02 journal.

    Input schema, remote ref, source HEAD, existing uncertain dispatch and
    non-idempotent POST are entirely validated/owned by `actions_dispatch`.
    """
    from .actions_dispatch import dispatch_workflow, local_head_for_dispatch
    from .github_actions import GitHubActionsClient, GitHubActionsError, resolve_repository, resolve_token
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
        if client is None:
            token,_source=resolve_token("auto")
            client=GitHubActionsClient(token)
        expected_sha=local_head_for_dispatch(Path(root))
        return dispatch_workflow(client,slug,str(workflow_id),ref.strip(),values,
                                 execute=True,expected_source_sha=expected_sha,
                                 resume_if_existing=True)
    except GitHubActionsError as exc:
        raise GithubProjectionError(_string(exc,limit=180)) from None
