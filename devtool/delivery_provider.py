"""Read-only GitHub capability inspection and bounded PR API transport.

Uses the existing GitHubActionsClient opener/authentication. Delivery sessions
and Actions journals remain the sole authorities for remote mutation intents;
this module never sends a POST except when its caller has already journaled and
approved the exact operation.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, quote
from urllib.request import Request

from .delivery_session import DeliverySessionError
from .github_actions import API_ORIGIN, API_VERSION, GitHubActionsClient, GitHubActionsError, _slug

_MAX_BYTES = 2 * 1024 * 1024
_ALLOWED = re.compile(r'/repos/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/(?:pulls(?:\?.*)?|rulesets(?:\?.*)?|commits/[0-9a-f]{40}/check-runs(?:\?.*)?)$')


def classify_provider_error(exc: BaseException) -> str:
    """Fail-closed classification; unknown 404 is never proof of absence."""
    if isinstance(exc, DeliverySessionError):
        # request_json emits these sanitized, typed failure messages. Only
        # their exact known prefix is reused; arbitrary provider prose stays
        # unknown, never authorizes a mutation.
        for reason in ('permission_unavailable','provider_unavailable',
                       'resource_unverified','mutation_requires_reconciliation'):
            if str(exc).startswith('GitHub GET '+reason):
                return reason
    if isinstance(exc, HTTPError):
        code = int(exc.code)
        if code == 401:
            return 'permission_unavailable'
        if code == 403:
            try:
                return 'provider_unavailable' if exc.headers.get('X-RateLimit-Remaining') == '0' else 'permission_unavailable'
            except (AttributeError, TypeError):
                return 'permission_unavailable'
        if code == 429 or code >= 500:
            return 'provider_unavailable'
        if code == 404:
            return 'resource_unverified'  # may be hidden by permissions
        if code == 422:
            return 'mutation_requires_reconciliation'
    if isinstance(exc, (URLError, TimeoutError, OSError)):
        return 'provider_unavailable'
    if isinstance(exc, GitHubActionsError):
        text = str(exc).casefold()
        if 'rate limit' in text or 'timed out' in text or 'connection failed' in text:
            return 'provider_unavailable'
        if 'permission' in text or 'authentication' in text or 'access denied' in text:
            return 'permission_unavailable'
    return 'unknown'


def request_json(client: GitHubActionsClient, method: str, path: str,
                 payload: Mapping[str, Any] | None = None) -> Any:
    """Shared Actions opener/token; no redirects, cross-repository paths or leaks."""
    if method not in ('GET', 'POST') or not isinstance(path, str) or not _ALLOWED.fullmatch(path):
        raise DeliverySessionError('Unsafe or unsupported GitHub provider request')
    parsed = urlsplit(API_ORIGIN + path)
    if (parsed.scheme != 'https' or parsed.netloc != 'api.github.com' or
            parsed.fragment or parsed.username or parsed.password or '..' in parsed.path):
        raise DeliverySessionError('GitHub provider request escaped canonical origin')
    if method == 'POST' and (not path.endswith('/pulls') or not getattr(client, '_token', None)):
        raise DeliverySessionError('GitHub PR creation requires authenticated approved access')
    headers = {'Accept': 'application/vnd.github+json',
               'X-GitHub-Api-Version': API_VERSION, 'User-Agent': 'devtool-delivery/1'}
    if getattr(client, '_token', None):
        headers['Authorization'] = 'Bearer ' + client._token
    raw_request = None if payload is None else json.dumps(payload, separators=(',', ':')).encode('utf-8')
    if raw_request is not None:
        if method != 'POST' or len(raw_request) > _MAX_BYTES:
            raise DeliverySessionError('Invalid GitHub mutation request payload')
        headers['Content-Type'] = 'application/json'
    req = Request(API_ORIGIN + path, headers=headers, data=raw_request, method=method)
    # Only GET requests may be retried, and only once for a transient
    # provider/rate-limit error. Never replay an uncertain POST.
    for attempt in range(2 if method == 'GET' else 1):
        try:
            with client._opener.open(req, timeout=client.timeout) as response:
                if int(response.status) != (201 if method == 'POST' else 200):
                    raise DeliverySessionError('Unexpected GitHub API status')
                raw = response.read(_MAX_BYTES + 1)
            break
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            reason = classify_provider_error(exc)
            if method == 'GET' and attempt == 0 and reason == 'provider_unavailable':
                time.sleep(0.1)
                continue
            suffix = ' — outcome uncertain; do not retry' if method == 'POST' else ''
            raise DeliverySessionError(f'GitHub {method} {reason}{suffix}') from None
    if len(raw) > _MAX_BYTES:
        raise DeliverySessionError('GitHub API response exceeds the size limit')
    try:
        return json.loads(raw.decode('utf-8'))
    except (UnicodeError, ValueError):
        raise DeliverySessionError('GitHub API response has invalid JSON') from None


def preflight(client: GitHubActionsClient, repository: str, capability: str,
              *, workflow: str = '') -> dict[str, Any]:
    """Read-only capability classification; an unknown permission is not an allow.

    Action capability is intentionally independent of Git push's SSH key: an
    Actions token with no push scope does not preclude an authorized SSH push.
    """
    repo = _slug(repository)
    allowed = {'repository_read', 'pr_read', 'pr_create', 'branch_write',
               'actions_dispatch', 'checks_read'}
    if capability not in allowed:
        raise DeliverySessionError('Unknown provider capability')
    if capability in ('pr_create','actions_dispatch') and not getattr(client, '_token', None):
        return {'repository':repo,'capability':capability,'state':'permission_unavailable',
                'reason':'authenticated token required', 'read_only':True}
    try:
        info = client.repository(repo)
        permissions = info.get('permissions') or {}
        if capability == 'branch_write':
            # Git SSH push is independently proven through git ls-remote; do
            # not confuse API token permissions with Git SSH permissions.
            state = 'unknown'
            reason = 'SSH branch-write permission requires native git remote preflight'
        elif capability == 'pr_create':
            state = 'unknown'
            reason = 'PR write scope is not independently established by repository GET'
        elif capability == 'actions_dispatch':
            if not workflow:
                raise DeliverySessionError('Actions dispatch capability requires a workflow ID')
            workflow_data = client.workflow(repo, workflow)
            # Enabled workflow != token has Actions:write. The dispatch
            # permission is only established by the native journaled POST.
            state = 'unknown' if workflow_data.get('state') == 'active' else 'disabled'
            reason = ('workflow active; dispatch permission not independently proven'
                      if state == 'unknown' else 'workflow not active')
        elif capability == 'checks_read':
            # Probe actual read privileges, not repository metadata permissions.
            # This is visibility only: the existing delivery CI registrar is
            # still responsible for evaluating applicable protection/rulesets.
            branch = info.get('default_branch')
            if not isinstance(branch,str) or not re.fullmatch(r'[A-Za-z0-9._/-]{1,200}',branch) or '..' in branch:
                state = 'unknown'
                reason = 'default branch unavailable for exact read-capability probe'
            else:
                tip = client._get(f'/repos/{repo}/branches/{quote(branch,safe="")}')
                sha = (tip.get('commit') or {}).get('sha') if isinstance(tip,dict) else None
                if not isinstance(sha,str) or not re.fullmatch(r'[0-9a-f]{40}',sha):
                    state = 'unknown'
                    reason = 'default branch head unavailable for checks read-capability probe'
                else:
                    checks = request_json(client,'GET',f'/repos/{repo}/commits/{sha}/check-runs?per_page=1')
                    rules = request_json(client,'GET',f'/repos/{repo}/rulesets?per_page=1')
                    if not isinstance(checks,dict) or not isinstance(checks.get('check_runs'),list) or not isinstance(rules,list):
                        state = 'unknown'
                        reason = 'checks/rulesets response shape is incomplete'
                    else:
                        state = 'available'
                        reason = 'checks and rulesets endpoints readable; rule applicability requires separate verification'
        elif capability == 'pr_read':
            result = request_json(client,'GET',f'/repos/{repo}/pulls?state=open&per_page=1')
            if not isinstance(result,list) or len(result)>1:
                raise DeliverySessionError('PR read response is incomplete')
            state = 'available'
            reason = 'read-only pull request list accessible'
        else:
            state = 'available'
            reason = 'canonical repository identity verified'
    except (GitHubActionsError, DeliverySessionError) as exc:
        state = classify_provider_error(exc)
        reason = state
    return {'repository':repo,'capability':capability,'state':state,
            'reason':reason,'read_only':True}
