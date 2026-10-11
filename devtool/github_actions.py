"""ACTIONS-01: read-only, fail-closed GitHub Actions discovery.

No workflow dispatch, token storage, remote execution, artifact promotion or
state mutation is authorized by this provider.  ACTIONS-02+ own those features.
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
        obj = self._get(f"/repos/{slug}")
        canonical = obj.get("full_name")
        if not isinstance(canonical, str) or canonical.lower() != slug.lower():
            raise GitHubActionsError("GitHub repository identity mismatch")
        return {
            "full_name": canonical, "id": obj.get("id"),
            "private": bool(obj.get("private")),
            "default_branch": obj.get("default_branch"),
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
        data = self._get(f"/repos/{slug}/actions/workflows/{quote(identifier, safe='')}")
        if not isinstance(data.get("id"), int):
            raise GitHubActionsError("Unexpected GitHub workflow response shape")
        return self._workflow_fields(data)


def actions_baseline() -> dict[str, Any]:
    return {
        "schema": 1,
        "phase": "ACTIONS-00+01",
        "provider": "github-actions",
        "api_origin": API_ORIGIN,
        "api_version": API_VERSION,
        "operations": ["baseline", "status", "workflows", "workflow"],
        "mutation_operations": [],
        "execution_integration": "not implemented (ACTIONS-02 onwards)",
        "canonical_logs": "Devtool XDG state; phone mirror removed in V4",
        "remote_execution": False,
        "token_persistence": False,
    }


def actions_cli(args: Any, local_root: Path) -> dict[str, Any]:
    action = str(getattr(args, "actions_command", "") or "")
    if action == "baseline":
        return actions_baseline()
    slug = resolve_repository(str(getattr(args, "github_repo", "") or ""), local_root)
    token, authentication = resolve_token(str(getattr(args, "auth", "auto") or "auto"))
    client = GitHubActionsClient(token)
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
    raise GitHubActionsError("Unknown read-only GitHub Actions operation")
