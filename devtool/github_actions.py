"""ACTIONS-01/02/03: bounded GitHub API discovery, dispatch and remote lifecycle.
Artifact promotion and execution qualification remain later phases.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

API_ORIGIN = "https://api.github.com"
API_VERSION = "2022-11-28"
_OWNER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})\Z")
_REPO = re.compile(r"[A-Za-z0-9_.-]{1,100}\Z")
_WORKFLOW = re.compile(r"(?:[1-9][0-9]*|[A-Za-z0-9][A-Za-z0-9_.-]*\.ya?ml)\Z")
_MAX_PAGES = 10
_MAX_BODY_BYTES = 4 * 1024 * 1024


class GitHubActionsError(RuntimeError):
    """Actionable errors with no token or response-body disclosure."""


class GitHubDispatchOutcomeUnknown(GitHubActionsError):
    """A dispatch POST might have succeeded; callers must not auto-retry."""


def _slug(value: str) -> str:
    parts = value.strip().split("/")
    if len(parts) != 2 or not _OWNER.fullmatch(parts[0]) or not _REPO.fullmatch(parts[1]) or parts[1] in (".", ".."):
        raise GitHubActionsError("GitHub repository must be an owner/repository slug")
    return f"{parts[0]}/{parts[1]}"


def parse_github_remote(value: str) -> str:
    """Accept only GitHub.com repository remotes; never expose credentialed URLs."""
    value = value.strip()
    if re.fullmatch(r"(?:git@)?github\.com:[^\s]+", value, flags=re.IGNORECASE):
        path = value.split(":", 1)[1]
    else:
        parsed = urlsplit(value)
        if (parsed.scheme not in ("https", "ssh", "git") or parsed.hostname != "github.com"
                or parsed.password is not None or parsed.port is not None
                or (parsed.username not in (None, "git") and parsed.scheme != "https")
                or parsed.query or parsed.fragment):
            raise GitHubActionsError("Origin is not a supported github.com repository remote")
        if parsed.username and parsed.scheme == "https":
            raise GitHubActionsError("Credentialed GitHub origin URLs are not supported")
        path = parsed.path.lstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return _slug(path)


def resolve_repository(explicit: str, local_root: Path) -> str:
    if explicit:
        return _slug(explicit)
    try:
        proc = subprocess.run(
            ["git", "-C", str(local_root), "config", "--get", "remote.origin.url"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitHubActionsError("Cannot inspect Git origin; use --github-repo OWNER/REPO") from exc
    if proc.returncode or not proc.stdout.strip():
        raise GitHubActionsError("Git origin is unset; use --github-repo OWNER/REPO")
    return parse_github_remote(proc.stdout)


def resolve_token(mode: str = "auto", *, environ: Mapping[str, str] | None = None) -> tuple[str | None, str]:
    """Credentials exist in memory only. gh is queried without a shell."""
    env = os.environ if environ is None else environ
    if mode not in ("auto", "env", "gh", "none"):
        raise GitHubActionsError("Unsupported authentication mode")
    if mode == "none":
        return None, "anonymous"
    if mode in ("auto", "env"):
        for key in ("GH_TOKEN", "GITHUB_TOKEN"):
            token = str(env.get(key, "")).strip()
            if token:
                if "\n" in token or "\r" in token:
                    raise GitHubActionsError("Invalid GitHub environment credential")
                return token, "environment"
        if mode == "env":
            raise GitHubActionsError("No GH_TOKEN or GITHUB_TOKEN available")
    if mode in ("auto", "gh") and shutil.which("gh"):
        try:
            # No logging of stdout/stderr: stdout contains a secret.
            proc = subprocess.run(
                ["gh", "auth", "token", "--hostname", "github.com"],
                capture_output=True, text=True, timeout=8, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            proc = None
        if proc and proc.returncode == 0 and proc.stdout.strip():
            token = proc.stdout.strip()
            if "\n" in token or "\r" in token:
                raise GitHubActionsError("Invalid GitHub CLI credential")
            return token, "github-cli"
    if mode == "gh":
        raise GitHubActionsError("GitHub CLI authentication unavailable (run gh auth login)")
    return None, "anonymous"


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def _link_next(headers: Mapping[str, str]) -> str | None:
    for part in headers.get("Link", "").split(","):
        if 'rel="next"' in part or "rel=next" in part:
            match = re.match(r"\s*<([^>]+)>\s*;", part)
            if match:
                return match.group(1)
    return None


class GitHubActionsClient:
    def __init__(self, token: str | None = None, *, timeout: float = 15.0, opener: Any = None):
        self._token = token
        self.timeout = timeout
        self._opener = opener if opener is not None else build_opener(_NoRedirect())

    def _request(self, path: str) -> tuple[dict[str, Any], Mapping[str, str]]:
        parsed = urlsplit(path)
        if (parsed.scheme != "https" or parsed.netloc != "api.github.com"
                or parsed.username or parsed.password or parsed.fragment):
            raise GitHubActionsError("GitHub API request rejected: unexpected origin")
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "devtool-actions-readonly/1",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        req = Request(path, headers=headers, method="GET")
        try:
            with self._opener.open(req, timeout=self.timeout) as response:
                status = int(response.status)
                if status != 200:
                    raise GitHubActionsError(f"GitHub API returned HTTP {status}")
                data = response.read(_MAX_BODY_BYTES + 1)
                response_headers = dict(response.headers.items())
        except HTTPError as exc:
            status = int(exc.code)
            try:
                remaining = str(exc.headers.get("X-RateLimit-Remaining", ""))
            except Exception:
                remaining = ""
            if status in (403, 429) and remaining == "0":
                reason = "GitHub API rate limit exceeded"
            elif status == 401:
                reason = "GitHub authentication failed; check token"
            elif status == 403:
                reason = "GitHub API access denied; check token permissions and SSO"
            elif status == 404:
                reason = "GitHub repository/resource not found or not accessible"
            else:
                reason = f"GitHub API HTTP {status}"
            raise GitHubActionsError(reason) from None
        except (URLError, TimeoutError, OSError) as exc:
            raise GitHubActionsError("GitHub API connection failed or timed out") from None
        if len(data) > _MAX_BODY_BYTES:
            raise GitHubActionsError("GitHub API response exceeded the size limit")
        try:
            result = json.loads(data)
        except (UnicodeDecodeError, ValueError):
            raise GitHubActionsError("GitHub API returned invalid JSON") from None
        if not isinstance(result, dict):
            raise GitHubActionsError("Unexpected GitHub API response shape")
        return result, response_headers

    def _get(self, path: str) -> dict[str, Any]:
        return self._request(API_ORIGIN + path)[0]

    def repository(self, slug: str) -> dict[str, Any]:
        slug = _slug(slug)
        try:
            obj = self._get(f"/repos/{slug}")
        except GitHubActionsError as exc:
            if "not found or not accessible" in str(exc):
                raise GitHubActionsError(f"Repository {slug} not found or inaccessible; verify Git origin, --github-repo and token access") from None
            raise
        canonical = obj.get("full_name")
        if not isinstance(canonical, str) or canonical.lower() != slug.lower():
            raise GitHubActionsError("GitHub repository identity mismatch")
        return {
            "full_name": canonical, "id": obj.get("id"),
            "private": bool(obj.get("private")),
            "default_branch": obj.get("default_branch"),
            "permissions": {k: v for k, v in (obj.get("permissions") or {}).items()
                            if k in ("push", "pull", "admin", "maintain") and type(v) is bool}
                           if isinstance(obj.get("permissions"), dict) else {},
            "html_url": f"https://github.com/{slug}",
        }

    def viewer(self) -> dict[str, Any]:
        if not self._token:
            raise GitHubActionsError("Authenticated identity requires a GitHub token")
        obj = self._get("/user")
        if not isinstance(obj.get("login"), str):
            raise GitHubActionsError("GitHub identity response was incomplete")
        return {"login": obj["login"], "id": obj.get("id")}

    def workflows(self, slug: str) -> list[dict[str, Any]]:
        slug = _slug(slug)
        prefix = f"/repos/{slug}/actions/workflows"
        url = API_ORIGIN + prefix + "?per_page=100"
        seen: set[str] = set()
        output: list[dict[str, Any]] = []
        for _ in range(_MAX_PAGES):
            if url in seen:
                raise GitHubActionsError("GitHub pagination loop detected")
            seen.add(url)
            parsed = urlsplit(url)
            if (parsed.scheme != "https" or parsed.netloc != "api.github.com" or
                    parsed.path.lower() != prefix.lower() or parsed.fragment or
                    any(k not in ("page", "per_page") for k in parse_qs(parsed.query))):
                raise GitHubActionsError("GitHub pagination link escaped repository scope")
            obj, headers = self._request(url)
            items = obj.get("workflows")
            if not isinstance(items, list):
                raise GitHubActionsError("Unexpected GitHub workflows response shape")
            for item in items:
                if not isinstance(item, dict) or not isinstance(item.get("id"), int):
                    raise GitHubActionsError("Unexpected GitHub workflow entry")
                output.append(self._workflow_fields(item))
            nxt = _link_next(headers)
            if not nxt:
                return output
            url = nxt
        raise GitHubActionsError("GitHub workflows exceed bounded pagination limit")

    @staticmethod
    def _workflow_fields(item: dict[str, Any]) -> dict[str, Any]:
        return {k: item.get(k) for k in ("id", "name", "path", "state", "created_at", "updated_at")}

    def workflow(self, slug: str, identifier: str) -> dict[str, Any]:
        slug = _slug(slug)
        if not _WORKFLOW.fullmatch(identifier) or identifier in (".", ".."):
            raise GitHubActionsError("Workflow must be a numeric ID or a .yml/.yaml filename")
        try:
            data = self._get(f"/repos/{slug}/actions/workflows/{quote(identifier, safe='')}")
        except GitHubActionsError as exc:
            if "not found or not accessible" in str(exc):
                raise GitHubActionsError(f"Workflow {identifier} is missing or inaccessible in repository {slug}") from None
            raise
        if not isinstance(data.get("id"), int):
            raise GitHubActionsError("Unexpected GitHub workflow response shape")
        return self._workflow_fields(data)

    def dispatch(self, slug: str, workflow_id: str, ref: str, inputs: dict[str, str]) -> dict[str, Any]:
        """POST once; 200 correlates by GitHub's returned run ID, 204 stays unlinked."""
        slug = _slug(slug)
        if not self._token:
            raise GitHubActionsError("Authenticated GitHub token required for dispatch")
        if not _WORKFLOW.fullmatch(workflow_id):
            raise GitHubActionsError("Invalid workflow identifier")
        if len(inputs) > 25:
            raise GitHubActionsError("Too many workflow_dispatch inputs")
        url = f"{API_ORIGIN}/repos/{slug}/actions/workflows/{quote(workflow_id,safe='')}/dispatches"
        encoded = json.dumps({"ref":ref,"inputs":inputs,"return_run_details":True},separators=(",",":")).encode("utf-8")
        if len(encoded) > _MAX_BODY_BYTES:
            raise GitHubActionsError("GitHub dispatch payload exceeds size limit")
        headers = {"Accept":"application/vnd.github+json", "X-GitHub-Api-Version":API_VERSION,
                   "User-Agent":"devtool-actions-dispatch/1", "Content-Type":"application/json",
                   "Authorization":f"Bearer {self._token}"}
        req=Request(url,data=encoded,headers=headers,method="POST")
        try:
            with self._opener.open(req,timeout=self.timeout) as response:
                status=int(response.status)
                data=response.read(_MAX_BODY_BYTES+1)
        except HTTPError as exc:
            status=int(exc.code)
            if status==401: reason="GitHub authentication failed"
            elif status==403: reason="GitHub Actions write permission denied or rate limited"
            elif status==404: reason="Workflow or selected ref not found or inaccessible"
            elif status==422: reason="GitHub rejected workflow ref or input validation"
            else: raise GitHubDispatchOutcomeUnknown(f"GitHub dispatch HTTP {status}; outcome uncertain") from None
            raise GitHubActionsError(reason) from None
        except (URLError,TimeoutError,OSError):
            raise GitHubDispatchOutcomeUnknown("GitHub dispatch transport failure (outcome uncertain)") from None
        if len(data)>_MAX_BODY_BYTES:
            raise GitHubDispatchOutcomeUnknown("GitHub dispatch response exceeded size limit")
        if status==204:
            return {"state":"accepted-uncorrelated", "run_id":None,"run_url":None}
        if status!=200:
            raise GitHubDispatchOutcomeUnknown(f"Unexpected GitHub dispatch HTTP {status}")
        try: obj=json.loads(data)
        except (UnicodeDecodeError,ValueError):
            raise GitHubDispatchOutcomeUnknown("GitHub dispatch returned invalid response") from None
        if not isinstance(obj,dict) or not isinstance(obj.get("workflow_run_id"),int) or obj["workflow_run_id"]<=0:
            raise GitHubDispatchOutcomeUnknown("GitHub dispatch did not return a valid workflow run ID")
        runid=obj["workflow_run_id"]
        expected=f"{API_ORIGIN}/repos/{slug}/actions/runs/{runid}"
        run_url=obj.get("run_url")
        html=obj.get("html_url")
        if run_url!=expected or html!=f"https://github.com/{slug}/actions/runs/{runid}":
            raise GitHubDispatchOutcomeUnknown("GitHub dispatch run identity or origin mismatch")
        return {"state":"accepted-correlated", "run_id":runid,"run_url":html}


    # ACTIONS-03: fixed-scope remote status and explicitly authorized mutations.
    def get_run(self, slug: str, identity: int) -> dict[str, Any]:
        slug = _slug(slug)
        if type(identity) is not int or identity <= 0:
            raise GitHubActionsError("Invalid workflow run ID")
        return self._get(f"/repos/{slug}/actions/runs/{identity}")

    def _paged(self, slug: str, path: str, key: str, *, max_results: int = 100) -> list[dict[str, Any]]:
        slug = _slug(slug)
        if not path.startswith(f"/repos/{slug}/actions/"):
            raise GitHubActionsError("GitHub list endpoint escaped repository scope")
        limit = min(max_results, _MAX_PAGES * 100)
        page = 1
        results: list[dict[str, Any]] = []
        while len(results) < limit:
            count = min(100, limit-len(results))
            data = self._get(f"{path}?per_page={count}&page={page}")
            items = data.get(key)
            if not isinstance(items, list) or any(not isinstance(x,dict) for x in items):
                raise GitHubActionsError("Invalid GitHub paginated response")
            results.extend(items)
            if len(items) < count:
                break
            page += 1
        return results

    def runs(self, slug: str, *, limit: int = 20) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100:
            raise GitHubActionsError("Runs limit must be between 1 and 100")
        slug = _slug(slug)
        return self._paged(slug,f"/repos/{slug}/actions/runs","workflow_runs",max_results=limit)

    def run_jobs(self, slug: str, identity: int) -> list[dict[str, Any]]:
        slug = _slug(slug)
        if type(identity) is not int or identity <= 0:
            raise GitHubActionsError("Invalid workflow run ID")
        return self._paged(slug,f"/repos/{slug}/actions/runs/{identity}/jobs","jobs",max_results=200)

    def run_attempt_jobs(self, slug: str, identity: int, attempt: int) -> list[dict[str, Any]]:
        """Fetch jobs from one exact attempt, never mix a rerun with old jobs."""
        slug = _slug(slug)
        if type(identity) is not int or identity <= 0 or type(attempt) is not int or attempt <= 0:
            raise GitHubActionsError("Invalid GitHub workflow run or attempt ID")
        return self._paged(slug, f"/repos/{slug}/actions/runs/{identity}/attempts/{attempt}/jobs",
                           "jobs", max_results=200)

    # ACTIONS-04: artifact metadata remains scoped to the exact workflow run.
    def run_artifacts(self, slug: str, identity: int) -> list[dict[str, Any]]:
        slug = _slug(slug)
        if type(identity) is not int or identity <= 0:
            raise GitHubActionsError("Invalid workflow run ID")
        return self._paged(slug, f"/repos/{slug}/actions/runs/{identity}/artifacts",
                           "artifacts", max_results=1000)

    def get_artifact(self, slug: str, identity: int) -> dict[str, Any]:
        slug = _slug(slug)
        if type(identity) is not int or identity <= 0:
            raise GitHubActionsError("Invalid artifact ID")
        return self._get(f"/repos/{slug}/actions/artifacts/{identity}")

    def artifact_download_location(self, slug: str, identity: int) -> str:
        """Read a redirect without following it with the GitHub Bearer token."""
        slug = _slug(slug)
        if type(identity) is not int or identity <= 0:
            raise GitHubActionsError("Invalid artifact ID")
        req = Request(f"{API_ORIGIN}/repos/{slug}/actions/artifacts/{identity}/zip",
                      method="GET", headers={
                          "Accept": "application/vnd.github+json",
                          "X-GitHub-Api-Version": API_VERSION,
                          "User-Agent": "devtool-actions-artifacts/1",
                          **({"Authorization": f"Bearer {self._token}"} if self._token else {})})
        try:
            with self._opener.open(req, timeout=self.timeout) as response:
                raise GitHubActionsError(f"GitHub artifact redirect unexpected HTTP {response.status}")
        except HTTPError as exc:
            if int(exc.code) == 302:
                location = exc.headers.get("Location", "")
                if not isinstance(location, str) or not location:
                    raise GitHubActionsError("GitHub artifact download location is missing") from None
                return location
            if int(exc.code) == 410:
                raise GitHubActionsError("GitHub artifact has expired") from None
            if int(exc.code) == 404:
                raise GitHubActionsError("GitHub artifact missing or inaccessible") from None
            raise GitHubActionsError(f"GitHub artifact redirect HTTP {exc.code}") from None
        except (URLError, OSError, TimeoutError):
            raise GitHubActionsError("GitHub artifact redirect request failed") from None

    def run_mutation(self, slug: str, identity: int, action: str) -> dict[str, Any]:
        slug = _slug(slug)
        if type(identity) is not int or identity <= 0 or action not in ("cancel","rerun","rerun-failed-jobs"):
            raise GitHubActionsError("Invalid GitHub run mutation")
        if not self._token:
            raise GitHubActionsError("GitHub Actions write token required")
        url=f"{API_ORIGIN}/repos/{slug}/actions/runs/{identity}/{action}"
        req=Request(url,data=b"",method="POST",headers={
            "Accept":"application/vnd.github+json", "X-GitHub-Api-Version":API_VERSION,
            "Authorization":f"Bearer {self._token}","User-Agent":"devtool-actions-control/1"})
        try:
            with self._opener.open(req,timeout=self.timeout) as response:
                status=int(response.status)
        except HTTPError as exc:
            if exc.code == 404:raise GitHubActionsError("Run is missing or inaccessible in selected repository") from None
            if exc.code in (401,403):raise GitHubActionsError("GitHub Actions write permission denied") from None
            if exc.code == 409:raise GitHubActionsError("GitHub rejected run control in its current state") from None
            raise GitHubActionsError(f"GitHub rejected run control (HTTP {exc.code}); verify state") from None
        except (URLError,OSError,TimeoutError):
            raise GitHubDispatchOutcomeUnknown("GitHub run control transport failed; outcome uncertain, do not retry blindly") from None
        if status not in (201,202,204):
            raise GitHubDispatchOutcomeUnknown(f"GitHub run control returned unexpected HTTP {status}; outcome uncertain")
        return {"state":"request-accepted", "run_id":identity,"action":action,
                "execution_complete":False,"outcome":"pending-remote-confirmation"}

    def run_log_location(self, slug: str, identity: int) -> str:
        slug = _slug(slug)
        if type(identity) is not int or identity <= 0:
            raise GitHubActionsError("Invalid workflow run ID")
        req=Request(f"{API_ORIGIN}/repos/{slug}/actions/runs/{identity}/logs",
                    headers={"Accept":"application/vnd.github+json",
                             "X-GitHub-Api-Version":API_VERSION,
                             "User-Agent":"devtool-actions-logs/1",
                             **({"Authorization":f"Bearer {self._token}"} if self._token else {})},method="GET")
        try:
            with self._opener.open(req,timeout=self.timeout) as response:
                raise GitHubActionsError(f"GitHub log URL response unexpectedly returned {response.status}")
        except HTTPError as exc:
            if exc.code == 302:
                url=exc.headers.get("Location", "")
                if isinstance(url,str) and url.startswith("https://"):
                    return url
                raise GitHubActionsError("GitHub log archive redirect URL is invalid") from None
            if exc.code==404: raise GitHubActionsError("Run logs not found or inaccessible") from None
            raise GitHubActionsError(f"GitHub log archive request failed (HTTP {exc.code})") from None
        except (URLError,OSError,TimeoutError):
            raise GitHubActionsError("GitHub log archive request timed out") from None


def actions_baseline() -> dict[str, Any]:
    return {
        "schema": 1,
        "phase": "ACTIONS-00+01+02+03+04+05",
        "provider": "github-actions",
        "api_origin": API_ORIGIN,
        "api_version": API_VERSION,
        "operations": ["baseline", "status", "workflows", "workflow", "plan", "run", "diagnose", "runs", "view", "jobs", "watch", "resume", "logs", "cancel", "rerun", "artifacts", "download", "verify", "android discover", "android plan", "android run", "android collect", "android verify", "pipeline plan", "pipeline run", "pipeline status", "pipeline resume", "release verify", "release setup", "release readiness", "release local-build", "release local-verify", "release local-evidence", "release local-handoff", "android unsigned-plan", "android unsigned-run", "android unsigned-import", "android unsigned-check", "android unsigned-sign", "android aab-plan", "android aab-run", "android aab-check", "android aab-import", "android aab-sign", "ci plan", "ci run", "ci check", "ci collect", "extensions metrics", "extensions runners", "extensions publish-plan", "extensions publish", "extensions gitlab-pipelines", "qualification report", "qualification check"],
        "mutation_operations": ["run (explicit --yes only)", "cancel (explicit --yes only)", "rerun (explicit --yes only)", "android run (explicit --yes only)", "pipeline run/resume (explicit --yes only)", "android unsigned-run (explicit --yes only)", "android unsigned-sign (explicit --yes only)", "android aab-run (explicit --yes only)", "android aab-sign (explicit --yes only)", "ci run (explicit --yes only)"],
        "execution_integration": "bounded foreground DAG coordinator over native invoke and GitHub Actions; private restart-safe journals, no daemon",
        "canonical_logs": "Devtool XDG state; phone mirror removed in V4",
        "remote_execution": False,
        "artifact_downloads": "sha256-verified ZIP, Android inspection, and ACTIONS-08 independent-policy attested release verification",
        "token_persistence": False,
    }


def actions_cli(args: Any, local_root: Path, *, observation_callback: Any = None) -> dict[str, Any]:
    action = str(getattr(args, "actions_command", "") or "")
    if action == "lineage":
        from .actions_lineage import lineage_snapshot
        repo = resolve_repository(str(getattr(args, "github_repo", "") or ""), local_root)
        return lineage_snapshot(local_root, repo, str(getattr(args, "pipeline_id", "") or ""))
    if action == 'qualification':
        from .actions_qualification import qualification_report
        command = str(getattr(args, 'qualification_command', '') or '')
        if command not in ('report', 'check'):
            raise GitHubActionsError('Unsupported G8 qualification operation')
        return qualification_report(local_root, strict_source=(command == 'check'))
    if action == 'extensions':
        from .actions_extensions import (ci_metrics, trusted_runners, upload_plan, publish_asset, gitlab_pipelines)
        sub=str(getattr(args,'extensions_command','') or '')
        if sub == 'gitlab-pipelines':
            if not bool(getattr(args,'enable_gitlab',False)):
                raise GitHubActionsError('G7 GitLab requires --enable-gitlab explicit opt-in')
            from os import environ
            project_raw=str(getattr(args,'gitlab_project_id','') or '')
            if not project_raw.isascii() or not project_raw.isdecimal() or len(project_raw)>16:
                raise GitHubActionsError('G7 GitLab requires numeric project ID')
            return gitlab_pipelines(project_id=int(project_raw),
                token=environ.get('GITLAB_TOKEN',''),limit=int(getattr(args,'limit',20)))
        slug=resolve_repository(str(getattr(args,'github_repo','') or ''),local_root)
        token,source=resolve_token(str(getattr(args,'auth','auto') or 'auto'))
        client=GitHubActionsClient(token)
        if sub=='metrics':
            return ci_metrics(client,slug,limit=int(getattr(args,'limit',30)))
        if sub=='runners':
            return trusted_runners(client,slug,policy_path=str(getattr(args,'trust_file','') or ''))
        if sub in ('publish-plan','publish'):
            options={'registry_id':str(getattr(args,'registry_id','') or ''),
              'tag':str(getattr(args,'tag','') or ''),
              'asset_name':str(getattr(args,'asset_name','') or ''),
              'expected_sha':str(getattr(args,'expected_sha','') or ''),
              'fmt':str(getattr(args,'format','apk') or 'apk')}
            if sub=='publish-plan':
                return upload_plan(client,slug,local_root,**options)[0]
            return publish_asset(client,slug,local_root,**options,
                yes=bool(getattr(args,'yes',False)),
                allow_unqualified=bool(getattr(args,'allow_unqualified_upload',False)))
        raise GitHubActionsError('Unknown G7 extension operation')
    if action in ('review', 'collect'):
        from .actions_handoff import review_run, collect_verified
        explicit = str(getattr(args, 'github_repo', '') or '')
        token, authentication = resolve_token(str(getattr(args, 'auth', 'auto') or 'auto'))
        client = GitHubActionsClient(token)
        request = str(getattr(args, 'request_id', '') or '')
        if not request:
            raise GitHubActionsError('Specify exact --request-id; no implicit run selection')
        if action == 'review':
            return {'authentication': authentication,
                    **review_run(client, request, repository=explicit)}
        return {'authentication': authentication,
                **collect_verified(client, request,
                     str(getattr(args, 'artifact_name', '') or ''), local_root,
                     yes=bool(getattr(args, 'yes', False)), repository=explicit)}
    if action in ('recent', 'detail', 'ui'):
        from .actions_workspace import local_recent, local_detail
        repository = resolve_repository(str(getattr(args,'github_repo','') or ''),local_root)
        if action == 'detail':
            return local_detail(local_root, repository,
                str(getattr(args,'kind','') or ''), str(getattr(args,'identity','') or ''))
        return local_recent(local_root, repository,limit=int(getattr(args,'limit',20)))
    if action == "baseline":
        return actions_baseline()
    # Purely local signing lane: must not depend on gh auth, GitHub plans,
    # branch rulesets, or ACTIONS-08 release qualification side effects.
    if action == 'release' and str(getattr(args, 'release_command', '') or '') == 'local-handoff':
        from .actions_private_handoff import private_handoff
        from .android.signing import AndroidSigningError
        try:
            return private_handoff(local_root,
                registry_id=str(getattr(args,'registry_id','') or ''),
                fmt=str(getattr(args,'format','apk') or 'apk'),
                identity_id=str(getattr(args,'signing_identity','') or ''),
                target=str(getattr(args,'signing_target','android_hud') or 'android_hud'),
                expected_source_sha=str(getattr(args,'expected_sha','') or ''),
                operation=str(getattr(args,'operation','') or ''),
                destination=str(getattr(args,'destination','') or ''),
                serial=str(getattr(args,'serial','') or ''),
                allow_unqualified=bool(getattr(args,'allow_unqualified',False)),
                yes=bool(getattr(args,'yes',False)))
        except AndroidSigningError:
            raise GitHubActionsError('A08 LIFE signing certificate inspection failed') from None
    if action == 'release' and str(getattr(args, 'release_command', '') or '') == 'local-evidence':
        from .actions_local_evidence import signed_local_evidence
        from .android.signing import AndroidSigningError
        try:
            return signed_local_evidence(local_root,
                registry_id=str(getattr(args,'registry_id','') or ''),
                fmt=str(getattr(args,'format','apk') or 'apk'),
                identity_id=str(getattr(args,'signing_identity','') or ''),
                target=str(getattr(args,'signing_target','android_hud') or 'android_hud'),
                expected_source_sha=str(getattr(args,'expected_sha','') or ''))
        except AndroidSigningError:
            raise GitHubActionsError('G6 LIFE signing certificate inspection failed') from None
    if action == 'release' and str(getattr(args, 'release_command', '') or '') in ('local-build', 'local-verify'):
        from .actions_local_release import local_build, local_verify
        from .android.signing import AndroidSigningError
        kw = dict(fmt=str(getattr(args, 'format', 'apk') or 'apk'),
                  identity_id=str(getattr(args, 'signing_identity', '') or ''),
                  target=str(getattr(args, 'signing_target', 'android_hud') or 'android_hud'),
                  application_id=str(getattr(args, 'application_id', '') or ''),
                  minimum_version_code=int(getattr(args, 'minimum_version_code', 1)),
                  project_dir=str(getattr(args, 'project_dir', 'android/devtool-live-hud') or 'android/devtool-live-hud'),
                  module=str(getattr(args, 'module', 'app') or 'app'))
        try:
            if str(args.release_command) == 'local-build':
                return local_build(local_root, execute=bool(getattr(args, 'yes', False)), **kw)
            return local_verify(local_root, artifact=str(getattr(args, 'artifact', '') or ''), **kw)
        except AndroidSigningError:
            raise GitHubActionsError('Local Android signing identity or artifact verification failed') from None
    if action == 'android' and str(getattr(args, 'android_command', '') or '') == 'unsigned-sign':
        # Offline LIFE signing never calls the GitHub API or requests a token.
        from .actions_unsigned import unsigned_local_sign
        from .android.signing import AndroidSigningError
        try:
            return unsigned_local_sign(local_root, registry_id=str(args.registry_id),
                identity_id=str(getattr(args, 'signing_identity', '') or ''),
                target=str(getattr(args, 'signing_target', 'android_hud') or 'android_hud'),
                execute=bool(getattr(args, 'yes', False)))
        except AndroidSigningError:
            raise GitHubActionsError('Local LIFE Android signer unavailable or invalid') from None
    if action == 'android' and str(getattr(args, 'android_command', '') or '') == 'aab-sign':
        # Signing remains offline and uses only the canonical LIFE identity.
        from .actions_aab import aab_local_sign
        from .android.signing import AndroidSigningError
        try:
            return aab_local_sign(local_root, registry_id=str(getattr(args,'registry_id','') or ''),
                identity_id=str(getattr(args, 'signing_identity', '') or ''),
                target=str(getattr(args, 'signing_target', 'android_hud') or 'android_hud'),
                execute=bool(getattr(args, 'yes', False)))
        except AndroidSigningError:
            raise GitHubActionsError('Local LIFE AAB signer unavailable or invalid') from None
    # Journal-only status never authenticates to GitHub or depends on remote availability.
    if action == "pipeline" and str(getattr(args, "pipeline_command", "")) == "status":
        from .actions_pipeline import pipeline_status
        return pipeline_status(str(getattr(args, "pipeline_id", "") or ""))
    if action == "pipeline" and str(getattr(args, "pipeline_command", "")) == "cancel":
        if not bool(getattr(args, 'yes', False)):
            raise GitHubActionsError('Explicit --yes is required to stop a pipeline')
        from .actions_pipeline import pipeline_cancel
        return pipeline_cancel(str(getattr(args, 'pipeline_id', '') or ''))
    request_id = str(getattr(args, "request_id", "") or "")
    explicit = str(getattr(args, "github_repo", "") or "")
    if action == "resume" and request_id:
        from .actions_runs import _read_dispatch_receipt
        _, pending_receipt = _read_dispatch_receipt(request_id)
        if pending_receipt.get("run_id") is None:
            if getattr(args, "run_id", None):
                raise GitHubActionsError("Uncorrelated dispatch cannot be bound with --run-id; inspect it using actions view --run-id")
            if explicit and _slug(explicit).lower() != _slug(pending_receipt["repository"]).lower():
                raise GitHubActionsError("Request receipt is bound to another repository")
            token, authentication = resolve_token(str(getattr(args, "auth", "auto") or "auto"))
            from .actions_dispatch import inspect_uncorrelated_dispatch
            return {"authentication":authentication, **inspect_uncorrelated_dispatch(
                GitHubActionsClient(token), request_id)}
    if request_id and action in ("view", "jobs", "watch", "resume", "logs", "cancel", "rerun", "artifacts", "download", "verify"):
        from .actions_runs import _safe_receipt
        _, bound = _safe_receipt(request_id)
        slug = _slug(explicit) if explicit else _slug(bound["repository"])
    else:
        slug = resolve_repository(explicit, local_root)
    token, authentication = resolve_token(str(getattr(args, "auth", "auto") or "auto"))
    client = GitHubActionsClient(token)
    if action == 'ci':
        from .actions_polyglot import ci_dispatch, ci_check, ci_collect
        sub = str(getattr(args, 'ci_command', '') or '')
        languages = str(getattr(args, 'languages', 'python,go,rust,java') or '')
        systems = str(getattr(args, 'systems', 'ubuntu-latest,windows-latest') or '')
        if sub in ('plan', 'run'):
            if sub == 'run' and not bool(getattr(args, 'yes', False)):
                raise GitHubActionsError('Explicit --yes required for G5 CI dispatch')
            return ci_dispatch(client, slug, local_root,
                ref=str(getattr(args,'ref','') or ''),languages=languages,
                systems=systems,execute=sub == 'run')
        from .actions_runs import run_id
        identity=run_id(getattr(args,'run_id',None))
        sha=str(getattr(args,'expected_sha','') or '')
        if sub == 'check':
            return ci_check(client,slug,run_id=identity,expected_sha=sha,
                            languages=languages,systems=systems)
        if sub == 'collect':
            return ci_collect(client,slug,local_root,run_id=identity,
                expected_sha=sha,languages=languages,systems=systems,
                download=bool(getattr(args,'download',False)),materialize=True)
        raise GitHubActionsError('Unsupported G5 CI command')
    if action == "pipeline":
        from .actions_pipeline import pipeline_plan, pipeline_run, pipeline_status, pipeline_resume
        sub = str(getattr(args, "pipeline_command", "") or "")
        if sub == "status":
            return pipeline_status(str(getattr(args, "pipeline_id", "") or ""))
        if sub in ("run", "resume") and not bool(getattr(args, "yes", False)):
            raise GitHubActionsError("Explicit --yes is required for pipeline mutation/resume")
        if sub == "plan":
            return pipeline_plan(client, slug, local_root, str(args.file))
        if sub == "run":
            return pipeline_run(client, slug, local_root, str(args.file))
        if sub == "resume":
            return pipeline_resume(client, slug, local_root, str(args.pipeline_id))
        raise GitHubActionsError("Unsupported Actions pipeline command")
    if action == "release" and str(getattr(args, 'release_command', '') or '') in ('setup', 'readiness'):
        from .actions_setup import release_setup, release_readiness
        branch = str(getattr(args, 'ref', 'main') or 'main')
        policy = str(getattr(args, 'policy', '') or '')
        if str(args.release_command) == 'readiness':
            return release_readiness(client, slug, local_root, ref=branch,
                policy_file=policy or str(Path.home()/'.config/devtool/release-policy.json'),
                signing_identity=str(getattr(args, 'signing_identity', '') or ''),
                target=str(getattr(args, 'signing_target', 'android_hud') or 'android_hud'))
        return release_setup(client, slug, local_root, ref=branch, policy_file=policy,
            signing_identity=str(getattr(args, 'signing_identity', '') or ''),
            target=str(getattr(args, 'signing_target', 'android_hud') or 'android_hud'),
            project_dir=str(getattr(args, 'project_dir', 'android/devtool-live-hud')),
            module=str(getattr(args, 'module', 'app')),
            application_id=str(getattr(args, 'application_id', '') or ''),
            fmt=str(getattr(args, 'format', 'apk')),
            minimum_version_code=int(getattr(args, 'minimum_version_code', 1)),
            execute=bool(getattr(args, 'yes', False)),
            rotate_secrets=bool(getattr(args, 'rotate_secrets', False)))
    if action == "release":
        from .actions_release import release_verify
        from .actions_runs import run_id
        from .actions_artifacts import _artifact_id
        if str(getattr(args, 'release_command', '') or '') != 'verify':
            raise GitHubActionsError('Unsupported Actions release command')
        return release_verify(client, slug, local_root, run_id=run_id(args.run_id),
                              artifact_id=_artifact_id(args.artifact_id),
                              policy_file=str(args.policy), member=str(args.member or ''))
    if action == "android":
        from .actions_android import android_discover, android_plan, android_collect
        sub = str(getattr(args,"android_command","") or "")
        if sub in ('aab-plan', 'aab-run'):
            from .actions_aab import aab_dispatch
            execute = sub == 'aab-run'
            if execute and not bool(getattr(args,'yes',False)):
                raise GitHubActionsError('Explicit --yes required for AAB dispatch')
            return aab_dispatch(client, slug, local_root, ref=str(getattr(args,'ref','') or ''), execute=execute)
        if sub in ('aab-check', 'aab-import'):
            from .actions_aab import aab_import
            from .actions_runs import run_id
            from .actions_artifacts import _artifact_id
            return aab_import(client, slug, local_root, run_id=run_id(args.run_id),
                artifact_id=_artifact_id(args.artifact_id),
                expected_sha=str(getattr(args,'expected_sha','') or ''),
                download=bool(getattr(args,'download',False)), materialize=sub == 'aab-import')
        if sub in ('unsigned-plan', 'unsigned-run'):
            from .actions_unsigned import unsigned_dispatch
            if sub == 'unsigned-run' and not bool(getattr(args, 'yes', False)):
                raise GitHubActionsError('Explicit --yes is required for unsigned CI dispatch')
            result = unsigned_dispatch(client, slug, local_root, ref=str(getattr(args, 'ref', '') or ''),
                                       execute=sub == 'unsigned-run')
            if sub == 'unsigned-run' and observation_callback is not None:
                # Only the actual correlated dispatch receipt may supply the run
                # identity. Never infer the latest run on a mutable branch.
                from .actions_runs import select_run, watch
                dispatch = result.get('dispatch', {})
                # Surface the accepted run ID immediately, before polling:
                # a later API error must not hide the already-created workflow.
                observation_callback({'remote': {
                    'id': dispatch.get('run_id'), 'repository': slug,
                    'status': 'requested', 'conclusion': None, 'completed': False,
                    'html_url': dispatch.get('run_url') or '',
                    'head_sha': None}, 'jobs': [], 'source_binding': 'unbound'})
                request_id = str(dispatch.get('request_id') or '')
                selected, identity, location, receipt = select_run('', None, request_id, lambda: slug)
                result['monitor'] = watch(client, selected, identity, location, receipt,
                                          max_checks=120, interval=10,
                                          on_observation=observation_callback)
            return result
        if sub in ('unsigned-import', 'unsigned-check'):
            from .actions_unsigned import unsigned_import
            from .actions_runs import run_id
            from .actions_artifacts import _artifact_id
            return unsigned_import(client, slug, local_root,
                run_id=run_id(args.run_id), artifact_id=_artifact_id(args.artifact_id),
                expected_sha=str(args.expected_sha),
                download=bool(getattr(args, 'download', False)),
                materialize=sub == 'unsigned-import')
        if sub == "discover":
            return android_discover(local_root,str(getattr(args,"project_dir","") or ""))
        if sub in ("plan", "run"):
            execute = sub == "run"
            if execute and not bool(getattr(args,"yes",False)):
                raise GitHubActionsError("Explicit --yes is required for Android workflow dispatch")
            return android_plan(client,slug,local_root,module=str(args.module),variant=str(args.variant),
                                fmt=str(args.format),ref=str(args.ref),workflow=str(args.workflow),
                                expected_package=str(getattr(args,"expected_package","") or ""),execute=execute,
                                project_dir=str(getattr(args,"project_dir","") or ""))
        if sub in ("collect", "verify"):
            from .actions_runs import run_id
            from .actions_artifacts import _artifact_id
            return android_collect(client,slug,local_root,run_id=run_id(args.run_id),
                                   artifact_id=_artifact_id(args.artifact_id),fmt=str(args.format),
                                   variant=str(args.variant),expected_package=str(args.expected_package or ""),
                                   expected_cert=str(args.expected_cert_sha256 or ""),
                                   member=str(args.member or ""), download=bool(getattr(args,"download",False)),
                                   materialize=sub=="collect",
                                   workflow=str(getattr(args,"workflow","devtool-android-build.yml") or "devtool-android-build.yml"),
                                   expected_source_sha=str(getattr(args,"expected_source_sha","") or ""),
                                   expected_run_attempt=int(getattr(args,"expected_run_attempt",0) or 0),
                                   apk_role=str(getattr(args,"apk_role","application") or "application"),
                                   expected_target_package=str(getattr(args,"expected_target_package","") or ""))
        raise GitHubActionsError("Unsupported Android Actions subcommand")
    if action == "status":
        repo = client.repository(slug)
        viewer = None
        viewer_status = "anonymous" if not token else "available"
        if token:
            try:
                viewer = client.viewer()
            except GitHubActionsError:
                # Installation and fine-grained tokens can have repository Actions
                # read permission but no /user entitlement. Do not infer missing
                # repository access when its own GET already succeeded.
                viewer_status = "not-authorized-or-unavailable"
        return {"repository": repo, "authentication": authentication,
                "viewer": viewer, "viewer_status": viewer_status, "read_only": True}
    if action == "workflows":
        return {"repository": slug, "authentication": authentication,
                "workflows": client.workflows(slug), "read_only": True}
    if action == "workflow":
        return {"repository": slug, "authentication": authentication,
                "workflow": client.workflow(slug, str(args.workflow_id)), "read_only": True}
    if action == "diagnose":
        checks=[]
        try:
            selected=client.repository(slug)
            checks.append({"name":"repository", "state":"accessible", "id":selected["id"]})
        except GitHubActionsError as exc:
            checks.append({"name":"repository", "state":"not-accessible", "reason":str(exc)})
            return {"phase":"ACTIONS-03","repository":slug,"authentication":authentication,
                    "ok":False,"checks":checks,"advice":"Verify Git origin or --github-repo, authentication, and repository visibility"}
        try:
            work=client.workflows(slug)
            checks.append({"name":"actions-workflows", "state":"accessible", "count":len(work)})
        except GitHubActionsError as exc:
            checks.append({"name":"actions-workflows", "state":"not-accessible", "reason":str(exc)})
        return {"phase":"ACTIONS-03", "repository":slug,"authentication":authentication,
                "ok":all(c["state"]=="accessible" for c in checks), "checks":checks}
    if action in ("runs", "view", "jobs", "watch", "resume", "logs", "cancel", "rerun", "artifacts", "download", "verify"):
        from .actions_runs import (select_run, snapshot, watch, _normalize_run, remote_logs, control_run)
        if action == "resume" and not str(getattr(args,"request_id","") or ""):
            raise GitHubActionsError("actions resume requires a prior --request-id (no guessed remote run)")
        if action == "runs":
            n=int(getattr(args,"limit",20))
            return {"phase":"ACTIONS-03", "repository":slug,"runs":[_normalize_run(x,slug) for x in client.runs(slug,limit=n)]}
        # A request ID is independently bound to its origin; don't infer skybind's Git origin.
        selected, identity, location, receipt=select_run(
            str(getattr(args,"github_repo","") or ""),getattr(args,"run_id",None),
            str(getattr(args,"request_id","") or ""),lambda:slug)
        if action in ("artifacts", "download", "verify"):
            from .actions_artifacts import artifact_list, download_artifact, verify_download
            if action == "artifacts":
                return artifact_list(client, selected, identity)
            artifact_id = getattr(args, "artifact_id", None)
            if artifact_id is None:
                raise GitHubActionsError("Use --artifact-id ID for artifact download or verification")
            if action == "download":
                return download_artifact(client, selected, identity, artifact_id, local_root)
            return verify_download(client, selected, identity, artifact_id, local_root)
        if action in ("view", "resume", "jobs"):

            return snapshot(client,selected,identity,location,receipt,with_jobs=action=="jobs")
        if action == "watch":
            return watch(client,selected,identity,location,receipt,
                         max_checks=int(getattr(args,"max_checks",10)),
                         interval=float(getattr(args,"interval",3)),
                         on_observation=observation_callback)
        if action == "logs":
            return {"phase":"ACTIONS-03","logs":remote_logs(client,selected,identity)}
        if not bool(getattr(args,"yes",False)):
            raise GitHubActionsError("Explicit --yes required for remote run control")
        method="cancel" if action=="cancel" else ("rerun-failed-jobs" if bool(getattr(args,"failed_only",False)) else "rerun")
        return {"phase":"ACTIONS-03", "repository":selected,
                "control":control_run(client,selected,identity,method)}
    if action in ("plan", "run"):
        from .actions_dispatch import _input_values, dispatch_workflow
        if action == "run" and not bool(getattr(args,"yes",False)):
            raise GitHubActionsError("Explicit --yes required to dispatch a workflow")
        values = _input_values(list(getattr(args,"input",[]) or []),str(getattr(args,"inputs_json","") or ""))
        payload = dispatch_workflow(client,slug,str(args.workflow_id),str(args.ref),values,execute=action=="run")
        return {"authentication": authentication, **payload}
    raise GitHubActionsError("Unknown GitHub Actions operation")

# ACTIONS-10: retained V3 integration included in repair commit.
