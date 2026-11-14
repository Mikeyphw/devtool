"""Source-bound GitHub delivery and CI registration.

This module composes the existing Devtool Actions client and delivery journal.
It never dispatches a workflow: only GitHub push/PR side effects have explicit
journaled, user-approved lanes. CI observation is bounded and source-attested.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote
from urllib.request import Request

from .delivery_session import (
    DeliverySessionError, confirm_intent, record_ci_observation, record_intent,
    record_pr_observation, resume, status,
)
from .github_actions import (
    API_ORIGIN, API_VERSION, GitHubActionsClient, GitHubActionsError,
    _slug, resolve_token,
)

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_REF = re.compile(r"[A-Za-z0-9._/-]{1,200}\Z")
_MAX_API_BYTES = 2 * 1024 * 1024
_FINAL = frozenset({'success', 'failure', 'cancelled', 'timed_out',
                    'action_required', 'neutral', 'skipped', 'stale'})


def _checked(session_id: str, *, state_root: Path | None = None) -> dict[str, Any]:
    session = status(session_id, state_root=state_root)
    if session.get('source_binding') != 'matching':
        raise DeliverySessionError('Local source drift: delivery session is not bound to current HEAD')
    if not session.get('repository'):
        raise DeliverySessionError('Source Git remote does not resolve to a GitHub repository')
    if session['head'] == session['base']:
        raise DeliverySessionError('Refusing delivery from the target base branch')
    if not _SHA.fullmatch(str(session['head_sha'])):
        raise DeliverySessionError('Session source SHA is invalid')
    return session


def _ls_remote(repo: str, branch: str) -> str | None:
    """None means *verified absent*, not network uncertainty."""
    if not _REF.fullmatch(branch) or branch.startswith('/') or '..' in branch:
        raise DeliverySessionError('Invalid delivery branch')
    try:
        p = subprocess.run(['git', '-C', repo, 'ls-remote', '--exit-code',
                            'origin', f'refs/heads/{branch}'],
                           text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'}, timeout=35)
    except (OSError, subprocess.SubprocessError) as exc:
        raise DeliverySessionError('Cannot inspect remote branch safely') from exc
    if p.returncode == 2 and not p.stdout.strip():
        return None
    if p.returncode != 0:
        raise DeliverySessionError('Git remote inspection failed; remote absence was NOT established')
    lines = p.stdout.splitlines()
    if len(lines) != 1:
        raise DeliverySessionError('Ambiguous remote branch identity')
    sha, tab, ref = lines[0].partition('\t')
    if not tab or ref != f'refs/heads/{branch}' or not _SHA.fullmatch(sha.lower()):
        raise DeliverySessionError('Invalid Git remote branch response')
    return sha.lower()


def _assert_nonforce_fast_forward(repo: str, old_sha: str, new_sha: str) -> None:
    """Require an observed remote tip to be an existing ancestor in local Git.

    A regular `git push` then supplies a second server-side non-fast-forward
    check. No fetch, reset, force, or lease override is performed here.
    """
    if not _SHA.fullmatch(old_sha) or not _SHA.fullmatch(new_sha):
        raise DeliverySessionError('Remote and target commits must have full Git SHA identities')
    try:
        p = subprocess.run(['git', '-C', repo, 'merge-base', '--is-ancestor',
                            old_sha, new_sha],capture_output=True, text=True,
                           timeout=12,check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeliverySessionError('Cannot prove the existing remote head is an ancestor') from exc
    if p.returncode != 0:
        raise DeliverySessionError('Remote head differs from session source and is not a proven fast-forward ancestor')


def push(session_id: str, *, yes: bool = False, state_root: Path | None = None,
         remote_probe: Callable[[str, str], str | None] = _ls_remote,
         git_push: Callable[[str, str], int] | None = None) -> dict[str, Any]:
    item = _checked(session_id, state_root=state_root)
    key = f"{item['repository']}:refs/heads/{item['head']}:{item['head_sha']}"
    current = remote_probe(item['repo_root'], item['head'])
    if current == item['head_sha']:
        # Read-only reconciliation of an earlier uncertain push is safe.
        confirm_intent(session_id, 'push', key, {'head_sha': current}, state_root=state_root, optional=True)
        return {'state': 'PUSH_VERIFIED', 'pushed_now': False, 'head_sha': current,
                'repository': item['repository'], 'source_bound': True}
    if current is not None:
        _assert_nonforce_fast_forward(item['repo_root'], current, item['head_sha'])
    if not yes:
        return {'state': 'PUSH_REQUIRES_APPROVAL', 'pushed_now': False,
                'head_sha': item['head_sha'], 'remote_old_sha': current,
                'mode': 'fast-forward' if current else 'create', 'source_bound': False}
    intent = record_intent(session_id, 'push', key, approved=True, state_root=state_root)
    if not intent['new']:
        raise DeliverySessionError('Previous push intent cannot be replayed; reconcile remote branch')
    if git_push is None:
        def git_push(root: str, branch: str) -> int:
            p = subprocess.run(['git', '-C', root, 'push', '--porcelain', 'origin',
                                f'refs/heads/{branch}:refs/heads/{branch}'],
                               capture_output=True, text=True, timeout=150,
                               env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'})
            return p.returncode
    try:
        rc = git_push(item['repo_root'], item['head'])
    except (OSError, subprocess.SubprocessError):
        rc = 1
    # Remote observation, not subprocess exit, proves the write. A network failure
    # leaves the intent unresolved, never triggers an automatic replay.
    observed = remote_probe(item['repo_root'], item['head'])
    if observed != item['head_sha']:
        raise DeliverySessionError('Push outcome uncertain or mismatched; intent remains unresolved, no retry')
    confirm_intent(session_id, 'push', key, {'head_sha': observed}, state_root=state_root)
    return {'state': 'PUSH_VERIFIED', 'pushed_now': True, 'git_exit': rc,
            'head_sha': observed, 'repository': item['repository'], 'source_bound': True}


def _github_request(client: GitHubActionsClient, method: str, path: str,
                    payload: Mapping[str, Any] | None = None) -> Any:
    """Narrow REST extension for list-valued PR GET and PR creation POST.

    Retain Devtool's original urllib opener/auth transport and no-redirect safety.
    GitHub returns arrays for PR list; the canonical Actions _get expects dicts.
    """
    if method not in {'GET', 'POST'} or not path.startswith('/repos/') or '..' in path:
        raise DeliverySessionError('Unsafe GitHub PR request')
    from urllib.parse import urlsplit
    parts = urlsplit(API_ORIGIN + path)
    if parts.scheme != 'https' or parts.netloc != 'api.github.com' or parts.fragment:
        raise DeliverySessionError('GitHub PR request escaped canonical API origin')
    if method == 'POST' and not getattr(client, '_token', None):
        raise DeliverySessionError('GitHub PR creation requires authenticated API access')
    headers = {'Accept': 'application/vnd.github+json',
               'X-GitHub-Api-Version': API_VERSION,
               'User-Agent': 'devtool-delivery/1'}
    if getattr(client, '_token', None):
        headers['Authorization'] = 'Bearer ' + client._token
    encoded = None if payload is None else json.dumps(payload, separators=(',', ':')).encode('utf-8')
    if encoded is not None:
        headers['Content-Type'] = 'application/json'
    request = Request(API_ORIGIN + path, headers=headers, data=encoded, method=method)
    try:
        with client._opener.open(request, timeout=client.timeout) as response:
            if int(response.status) != (201 if method == 'POST' else 200):
                raise DeliverySessionError(f'Unexpected GitHub PR HTTP {response.status}')
            raw = response.read(_MAX_API_BYTES + 1)
    except HTTPError as exc:
        if method == 'POST':
            # HTTP 422 may mean duplicate PR, invalid scope, or branch drift.
            # Reconciliation MUST be read-only; never assume request was harmless.
            raise DeliverySessionError(f'GitHub PR mutation requires reconciliation (HTTP {exc.code})') from None
        raise DeliverySessionError(f'GitHub PR lookup denied or unavailable (HTTP {exc.code})') from None
    except (URLError, TimeoutError, OSError):
        if method == 'POST':
            raise DeliverySessionError('PR POST outcome uncertain; do not retry') from None
        raise DeliverySessionError('GitHub PR lookup unavailable; no mutation attempted') from None
    if len(raw) > _MAX_API_BYTES:
        raise DeliverySessionError('GitHub PR API response exceeds size limit')
    try:
        return json.loads(raw)
    except (UnicodeError, ValueError):
        raise DeliverySessionError('GitHub PR API returned invalid JSON') from None


def pull_requests(client: GitHubActionsClient, slug: str, head: str,
                  base: str) -> list[dict[str, Any]]:
    slug = _slug(slug)
    if not _REF.fullmatch(head) or not _REF.fullmatch(base):
        raise DeliverySessionError('Unsafe PR branch identity')
    owner = slug.split('/', 1)[0]
    query = urlencode({'state': 'open', 'head': f'{owner}:{head}',
                       'per_page': '100'})
    result = _github_request(client, 'GET', f'/repos/{slug}/pulls?{query}')
    if not isinstance(result, list) or len(result) >= 100:
        raise DeliverySessionError('PR inventory is ambiguous or truncated')
    if any(not isinstance(x, dict) for x in result):
        raise DeliverySessionError('Malformed PR inventory')
    return result


def _pr_match(item: dict[str, Any], slug: str, head: str, base: str,
              sha: str) -> dict[str, Any] | None:
    if item.get('state') != 'open':
        return None
    h, b = item.get('head'), item.get('base')
    if not isinstance(h, dict) or not isinstance(b, dict):
        raise DeliverySessionError('PR identity is incomplete')
    full_name = (h.get('repo') or {}).get('full_name')
    if not isinstance(full_name, str) or full_name.lower() != slug.lower():
        return None
    if h.get('ref') != head or b.get('ref') != base:
        return None
    if (b.get('repo') or {}).get('full_name', '').lower() != slug.lower():
        return None
    if h.get('sha') != sha:
        raise DeliverySessionError('Existing PR head differs from source session; refusing stale PR')
    number = item.get('number')
    if type(number) is not int or number <= 0:
        raise DeliverySessionError('PR lacks exact positive number')
    return {'number': number, 'head_sha': sha, 'head': head, 'base': base,
            'url': f'https://github.com/{slug}/pull/{number}',
            'merge_commit_sha': item.get('merge_commit_sha') or None}


def _remote_head(client: GitHubActionsClient, slug: str, branch: str) -> str:
    obj = client._get(f'/repos/{_slug(slug)}/branches/{quote(branch,safe="")}')
    sha = (obj.get('commit') or {}).get('sha')
    if not isinstance(sha, str) or not _SHA.fullmatch(sha.lower()):
        raise DeliverySessionError('Remote GitHub branch identity unavailable')
    return sha.lower()


def pr(session_id: str, *, yes: bool = False, title: str = '',
       body: str = '', auth: str = 'auto', state_root: Path | None = None,
       client: GitHubActionsClient | None = None) -> dict[str, Any]:
    item = _checked(session_id, state_root=state_root)
    slug, head, base, sha = (item[x] for x in ('repository','head','base','head_sha'))
    if client is None:
        token, _ = resolve_token(auth)
        client = GitHubActionsClient(token)
    remote = _remote_head(client, slug, head)
    if remote != sha:
        raise DeliverySessionError('Remote branch is missing or has different SHA; push/reconcile first')
    inventory = pull_requests(client, slug, head, base)
    for entry in inventory:
        h = entry.get('head') or {}
        b = entry.get('base') or {}
        if (entry.get('state') == 'open' and h.get('ref') == head and
            (h.get('repo') or {}).get('full_name','').lower() == slug.lower() and
            b.get('ref') != base):
            raise DeliverySessionError('Existing open PR targets another base; manual reconciliation required')
    found = [_pr_match(x, slug, head, base, sha) for x in inventory]
    found = [x for x in found if x is not None]
    if len(found) > 1:
        raise DeliverySessionError('Multiple matching PRs; refuse ambiguous reconciliation')
    key = f'{slug}:{base}:{head}:{sha}'
    if found:
        observed = found[0]
        record_pr_observation(session_id, observed, state_root=state_root)
        confirm_intent(session_id, 'pr_create', key,
                       {'head_sha': sha, 'pr_number': observed['number']},
                       state_root=state_root, optional=True)
        return {'state': 'PR_VERIFIED', 'created_now': False, **observed}
    if not yes:
        return {'state': 'PR_REQUIRES_APPROVAL', 'created_now': False,
                'head_sha': sha, 'source_bound': False}
    if not getattr(client, '_token', None):
        raise DeliverySessionError('Authenticated GitHub PR write access required; no intent recorded')
    if title and (len(title) > 160 or '\n' in title):
        raise DeliverySessionError('PR title must be one line and at most 160 characters')
    if len(body) > 16000:
        raise DeliverySessionError('PR body too large')
    intent = record_intent(session_id, 'pr_create', key, approved=True, state_root=state_root)
    if not intent['new']:
        raise DeliverySessionError('Previous PR creation outcome unresolved; reconcile, do not retry')
    payload = {'head': head, 'base': base,
               'title': title or f'Delivery: {head}', 'body': body,
               'draft': True}
    response = _github_request(client, 'POST', f'/repos/{slug}/pulls', payload)
    if not isinstance(response, dict):
        raise DeliverySessionError('PR creation returned non-object; outcome uncertain')
    created = _pr_match(response, slug, head, base, sha)
    if created is None:
        raise DeliverySessionError('PR response did not prove exact source identity; reconcile manually')
    # Confirm from a subsequent independent read, never just trust POST body.
    selected = [_pr_match(x,slug,head,base,sha) for x in pull_requests(client,slug,head,base)]
    verified = [x for x in selected if x and x['number'] == created['number']]
    if len(verified) != 1:
        raise DeliverySessionError('PR POST accepted but independent source verification pending')
    confirm_intent(session_id,'pr_create',key,{'head_sha':sha,'pr_number':created['number']},state_root=state_root)
    record_pr_observation(session_id, verified[0], state_root=state_root)
    return {'state': 'PR_VERIFIED', 'created_now': True, **verified[0]}


def _pr_merge_sha(client: GitHubActionsClient, slug: str, number: int) -> str | None:
    try:
        ref = client._get(f'/repos/{slug}/git/ref/pull/{number}/merge')
    except GitHubActionsError:
        return None
    sha = (ref.get('object') or {}).get('sha')
    return sha.lower() if isinstance(sha, str) and _SHA.fullmatch(sha.lower()) else None


def _ci_candidate(run: dict[str, Any], item: Mapping[str, Any],
                  pr_info: Mapping[str, Any] | None, merge_sha: str | None) -> dict[str, Any] | None:
    identity = run.get('id')
    if type(identity) is not int or identity < 1:
        return None
    if (run.get('repository') or {}).get('full_name', item['repository']).lower() != item['repository'].lower():
        return None
    event = run.get('event')
    sha = str(run.get('head_sha') or '').lower()
    branch = str(run.get('head_branch') or '')
    exact = event == 'push' and sha == item['head_sha'] and branch == item['head']
    if event == 'pull_request' and pr_info:
        # Synthetic merge SHA must be the *current*, independently observed PR
        # merge ref. A stale synthetic merge is not sufficient.
        number = pr_info['number']
        prs = run.get('pull_requests') or []
        relationship = isinstance(prs,list) and any(
            isinstance(p,dict) and p.get('number') == number and
            (p.get('head') or {}).get('sha') == item['head_sha']
            for p in prs)
        if relationship and sha in (item['head_sha'], merge_sha):
            exact = True
        elif merge_sha and sha == merge_sha and branch in (item['head'], str(number), ''):
            # A live remote merge ref + PR event constitutes identity only when
            # this observed PR is itself source-bound to our head.
            exact = True
    if not exact:
        return None
    conclusion = run.get('conclusion')
    state = run.get('status')
    if not isinstance(state,str) or state not in ('queued','in_progress','completed','waiting','pending','requested'):
        return None
    if state == 'completed' and conclusion not in _FINAL:
        return None
    return {'id': identity, 'attempt': int(run.get('run_attempt') or 1),
            'name': str(run.get('name') or '')[:160], 'workflow_id': run.get('workflow_id'),
            'event': event, 'head_sha': sha, 'status': state,
            'conclusion': conclusion, 'url': f"https://github.com/{item['repository']}/actions/runs/{identity}"}


def _failed_jobs(client: GitHubActionsClient, slug: str, run: Mapping[str, Any]) -> list[dict[str, Any]]:
    if run['status'] != 'completed' or run['conclusion'] == 'success':
        return []
    try:
        jobs = client.run_attempt_jobs(slug, run['id'], run['attempt'])
    except GitHubActionsError as exc:
        return [{'job':'unavailable','error':str(exc)[:150]}]
    failed = []
    for job in jobs:
        if job.get('conclusion') not in ('failure','timed_out','cancelled'):
            continue
        steps = [str(s.get('name') or '')[:120] for s in (job.get('steps') or [])
                 if isinstance(s,dict) and s.get('conclusion') in ('failure','timed_out','cancelled')]
        failed.append({'job': str(job.get('name') or '')[:160], 'steps': steps[:12]})
    return failed[:30]


def observe_ci(session_id: str, *, required: tuple[str,...] = (),
               checks: int = 1, interval: float = 5, state_root: Path | None = None,
               workflow_ids: Mapping[str, int] | None = None,
               required_event: str = '',
               policy_sha256: str = "", policy_guard: Callable[[], None] | None = None,
               client: GitHubActionsClient | None = None,
               auth: str = 'auto', progress: Callable[[dict[str,Any]], None] | None = None,
               sleep: Callable[[float], None] = time.sleep,
               strict_registration: bool = False, registration_horizon_seconds: int = 1800,
               dispatch_run_ids: Mapping[int, int] | None = None,
               clock: Callable[[], datetime] | None = None) -> dict[str, Any]:
    if type(checks) is not int or checks < 1 or checks > 60 or interval < 1 or interval > 120:
        raise DeliverySessionError('CI polling limits: 1..60 checks, 1..120 seconds between polls')
    required = tuple(dict.fromkeys(str(x).strip() for x in required))
    if len(required) > 25 or any(not x or len(x) > 160 for x in required):
        raise DeliverySessionError('Required workflow identifiers must be 1..25 bounded names')
    if required_event not in ('', 'pull_request', 'push', 'workflow_dispatch'):
        raise DeliverySessionError('Unsupported CI policy event')
    if workflow_ids is not None:
        if (set(workflow_ids) != set(required) or
                any(type(value) is not int or value <= 0 for value in workflow_ids.values()) or
                len(policy_sha256) != 64 or any(c not in '0123456789abcdef' for c in policy_sha256)):
            raise DeliverySessionError('Workflow IDs require the exact named required set and valid policy SHA-256')
    if policy_guard is not None:
        policy_guard()
    item = _checked(session_id, state_root=state_root)
    if client is None:
        token, _ = resolve_token(auth)
        client = GitHubActionsClient(token)
    pr_info = item.get('pr')
    if pr_info and pr_info.get('head_sha') != item['head_sha']:
        raise DeliverySessionError('Saved PR provenance is stale')
    registrar = None
    expectations: dict[str, str] = {}
    def provider_unavailable(phase: str, poll: int) -> dict[str, Any]:
        summary = {'state':'REGISTRATION_PROVIDER_UNAVAILABLE',
                   'checks_qualified':False, 'exact_run_evidence':None,
                   'source_sha':item['head_sha'], 'required':list(required),
                   'policy_sha256':policy_sha256 or None, 'policy_mismatches':[],
                   'missing':list(required), 'ambiguous':[], 'runs':[],
                   'failures':[], 'inventory_limit':500, 'poll':poll,
                   'registration':{'event':required_event, 'phase':phase,
                                   'status':'provider-unavailable',
                                   'unattributed_run_count':0},
                   'branch_rules':None, 'branch_rules_issue':None,
                   'release_qualified':False}
        record_ci_observation(session_id, summary, state_root=state_root)
        if progress:
            progress(summary)
        return {**summary, 'history':[{'poll':poll,
            'state':'REGISTRATION_PROVIDER_UNAVAILABLE', 'run_count':0}]}
    if strict_registration:
        if not required or workflow_ids is None or not required_event:
            raise DeliverySessionError('Strict CI registrar requires explicit policy IDs and event')
        from . import delivery_ci_registrar as registrar
        # Structural errors in policy/committed workflow definitions refuse
        # before a run can be selected. An API outage is never CI success.
        try:
            expectations = registrar.workflow_expectations(client, item, workflow_ids, required_event)
        except GitHubActionsError:
            return provider_unavailable('workflow-definitions', 0)

    def live_provenance() -> str | None:
        # Check the remote source on EVERY observation, even if no PR exists.
        # Never infer a successful check from an old head or merge ref.
        actual_head = _remote_head(client, item['repository'], item['head'])
        if actual_head != item['head_sha']:
            raise DeliverySessionError('Remote branch moved during CI; qualification blocked')
        if not pr_info:
            return None
        live = client._get(f"/repos/{item['repository']}/pulls/{pr_info['number']}")
        verified = _pr_match(live, item['repository'], item['head'], item['base'], item['head_sha'])
        if not verified or verified['number'] != pr_info['number']:
            raise DeliverySessionError('Current PR identity changed or closed during CI')
        return _pr_merge_sha(client, item['repository'], pr_info['number'])

    snapshots = []
    for i in range(checks):
        if policy_guard is not None:
            policy_guard()
        merge_sha = live_provenance()
        registration: dict[str, Any] | None = None
        if strict_registration:
            window = registrar.registration_window(
                item, registration_horizon_seconds,
                now=clock() if clock is not None else None)
            try:
                raw = registrar.collect_runs(client, item['repository'])
            except GitHubActionsError:
                return provider_unavailable('workflow-run-pages', i+1)
            def registration_match(row: dict[str, Any], source: Mapping[str, Any],
                                   pr: Mapping[str, Any] | None, merge: str | None) -> dict[str, Any] | None:
                # Exact run GET must corroborate the creation-window evidence,
                # not merely repeat a selected run ID and source SHA.
                if (row.get('event') != required_event or
                        type(row.get('run_attempt')) is not int or row['run_attempt'] < 1):
                    return None
                try:
                    at = registrar._date(row.get('created_at'))
                except DeliverySessionError:
                    return None
                if (at < window['began'] - timedelta(seconds=120) or
                        at > window['deadline'] + timedelta(seconds=120) or
                        at > window['now'] + timedelta(seconds=120)):
                    return None
                if required_event != 'workflow_dispatch':
                    return _ci_candidate(row, source, pr, merge)
                # A dispatch must be independently linked to an exact run ID.
                # Source equality alone is not evidence that a POST registered.
                if (row.get('head_sha') != source['head_sha'] or
                        row.get('head_branch') != source['head'] or
                        (row.get('repository') or {}).get('full_name', source['repository']).lower()
                        != source['repository'].lower()):
                    return None
                return {'id':row['id'], 'attempt':row['run_attempt'],
                        'name':row['name'], 'workflow_id':row['workflow_id'],
                        'event':'workflow_dispatch', 'head_sha':row['head_sha'],
                        'status':row['status'], 'conclusion':row.get('conclusion'),
                        'url':f"https://github.com/{source['repository']}/actions/runs/{row['id']}"}
            registration = registrar.select_registration(
                raw, item, event=required_event, required=workflow_ids,
                expected=expectations, merge_sha=merge_sha, pr_info=pr_info,
                window=window, match_run=registration_match,
                dispatch_run_ids=dispatch_run_ids)
            records = registration['runs']
            seen = {row['name']: row for row in records}
            missing = registration['missing']
            ambiguous = registration['ambiguous']
            mismatched_ids = []
        else:
            raw = client.runs(item['repository'], limit=100)
            seen: dict[str,dict[str,Any]] = {}
            ambiguous = []
            for candidate in raw:
                match = _ci_candidate(candidate,item,pr_info,merge_sha)
                if match is None or (required_event and match['event'] != required_event):
                    continue
                # Legacy read-only callers retain their prior selection behavior.
                name = match['name']
                if not name:
                    continue
                old = seen.get(name)
                if old and old['workflow_id'] != match['workflow_id']:
                    ambiguous.append(name)
                    continue
                if not old or (match['id'],match['attempt']) > (old['id'],old['attempt']):
                    seen[name] = match
            records = sorted(seen.values(), key=lambda x:x['name'])
            missing = [name for name in required if name not in seen]
            mismatched_ids = [name for name, expected in (workflow_ids or {}).items()
                              if name in seen and seen[name]['workflow_id'] != expected]
        failures = [r for r in records if r['name'] in required and r['conclusion'] not in ('success',None)]
        unfinished = [r for r in records if r['name'] in required and r['status'] != 'completed']
        if ambiguous or mismatched_ids:
            state='BLOCKED_AMBIGUOUS'
        elif registration and registration['indeterminate']:
            state='BLOCKED_CONDITIONAL'
        elif registration and registration['not_expected']:
            # Policy says *required*, but definition excludes this event/ref.
            # A future policy may explicitly label optional checks; do not
            # silently exempt required checks from CI qualification today.
            state='BLOCKED_TRIGGER'
        elif registration and registration['dispatch_uncorrelated']:
            state='DISPATCH_UNCORRELATED'
        elif not required:
            state='OBSERVED_ONLY'
        elif failures:
            state='CHECKS_FAILED'
        elif missing and registration and registration['expired']:
            state='REGISTRATION_TIMED_OUT'
        elif missing:
            state='WAITING_REGISTRATION'
        elif unfinished:
            state='CHECKS_RUNNING'
        else:
            state='REQUIRED_CHECKS_PASSED'
        exact_evidence = None
        if state == 'REQUIRED_CHECKS_PASSED' and workflow_ids is not None:
            # A run-list row is not proof that a current *attempt* finished.
            # The source/PR identity and the exact provider run+jobs are independently
            # checked, without dispatching, rerunning or guessing any IDs.
            from .delivery_ci_evidence import verify_exact_attempts
            exact_evidence = verify_exact_attempts(
                client, item['repository'], records, workflow_ids,
                validate_run=(lambda row: registration_match(row, item, pr_info, merge_sha))
                             if strict_registration else
                             (lambda row: _ci_candidate(row, item, pr_info, merge_sha)))
            if not exact_evidence['verified']:
                state = 'CHECKS_EVIDENCE_PENDING'
        branch_rules = None
        branch_rules_issue = None
        if state == 'REQUIRED_CHECKS_PASSED' and strict_registration:
            try:
                branch_rules = registrar.audit_branch_rules(client, item, records)
            except (DeliverySessionError, GitHubActionsError, OSError, ValueError) as exc:
                # No inferred empty ruleset on 401/403/rate limit/partial data.
                # Persist a blocked observation instead of retaining stale green.
                state = 'BRANCH_RULES_UNVERIFIED'
                branch_rules_issue = str(exc)[:220]
        if state == 'REQUIRED_CHECKS_PASSED':
            # Detect a policy/head/merge ref change while all remote calls were in flight.
            if policy_guard is not None:
                policy_guard()
            if live_provenance() != merge_sha:
                raise DeliverySessionError('PR merge identity changed during CI poll; qualification blocked')
        diag = []
        if state == 'CHECKS_FAILED':
            from .delivery_ci_evidence import failed_job_details
            for match in failures:
                diag.append({'run_id': match['id'], 'attempt': match['attempt'],
                             'name': match['name'],
                             'url': match['url'],
                             'jobs': _failed_jobs(client, item['repository'], match),
                             'detailed_jobs': failed_job_details(client, item['repository'], match),
                             'log_content_collected': False})
        summary = {'state':state, 'checks_qualified':state=='REQUIRED_CHECKS_PASSED',
                   'exact_run_evidence':exact_evidence,
                   'source_sha':item['head_sha'], 'required':list(required),
                   'policy_sha256':policy_sha256 or None,
                   'policy_mismatches':sorted(mismatched_ids),
                   'missing': missing, 'ambiguous': sorted(set(ambiguous)),
                   'runs': records, 'failures':diag,
                   'inventory_limit':500 if strict_registration else 100,
                   'registration':registration,
                   'branch_rules':branch_rules,
                   'branch_rules_issue':branch_rules_issue,
                   'poll':i+1, 'release_qualified':False}
        record_ci_observation(session_id, summary, state_root=state_root)
        snapshots.append({'poll':i+1,'state':state,'run_count':len(records)})
        if progress:
            progress(summary)
        if state not in ('WAITING_REGISTRATION','CHECKS_RUNNING','CHECKS_EVIDENCE_PENDING'):
            break
        if i+1 < checks:
            sleep(registrar.next_delay(interval, i, session_id) if strict_registration else interval)
    return {**summary,'history':snapshots}
