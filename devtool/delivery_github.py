"""Source-bound GitHub delivery and CI registration.

This module composes the existing Devtool Actions client and delivery journal.
It never dispatches a workflow: only GitHub push/PR side effects have explicit
journaled, user-approved lanes. CI observation is bounded and source-attested.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import json
import hashlib
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
    if session.get('head_repository', session['repository']).casefold() != session['repository'].casefold():
        raise DeliverySessionError('Head GitHub repository differs from checked-out origin')
    if session['head'] == session['base']:
        raise DeliverySessionError('Refusing delivery from the target base branch')
    if not _SHA.fullmatch(str(session['head_sha'])):
        raise DeliverySessionError('Session source SHA is invalid')
    return session


def _ls_remote(repo: str, branch: str, *, runner=None) -> str | None:
    """None means *verified absent*, not network uncertainty."""
    if not _REF.fullmatch(branch) or branch.startswith('/') or '..' in branch:
        raise DeliverySessionError('Invalid delivery branch')
    try:
        if runner is None:
            p = subprocess.run(['git', '-C', repo, 'ls-remote', '--exit-code',
                                'origin', f'refs/heads/{branch}'],
                               text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'}, timeout=35)
        else:
            # M4 uses the same canonical remote-result parser; only the
            # one-operation credentialed Git transport differs.
            p = runner(repo, ['ls-remote', '--exit-code', 'origin',
                              f'refs/heads/{branch}'], timeout=35)
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
         git_push: Callable[[str, str], int] | None = None,
         auth: str = 'auto') -> dict[str, Any]:
    if auth == 'app':
        # One scoped credential, checked before any mutation intent.
        # Native push(), intent journal and read-only reconciliation stay authoritative.
        if remote_probe is not _ls_remote or git_push is not None:
            raise DeliverySessionError('App push cannot override native credential transport')
        from .github_app import AppInstallationProvider, run_git_authenticated
        bound = _checked(session_id, state_root=state_root)
        provider = AppInstallationProvider.from_environment()
        credential = provider.issue(bound['repository'], 'branch_write')
        def app_remote_probe(root, branch):
            return _ls_remote(root, branch, runner=lambda r, argv, timeout:
                run_git_authenticated(r, argv, credential, timeout=timeout))
        def app_git_push(root, branch):
            return run_git_authenticated(root,
                ['push','--porcelain','origin',f'refs/heads/{branch}:refs/heads/{branch}'],
                credential,timeout=150).returncode
        result = push(session_id,yes=yes,state_root=state_root,
                      remote_probe=app_remote_probe,git_push=app_git_push,auth='auto')
        result['credential'] = credential.public_receipt()
        return result
    if auth not in ('auto',):
        raise DeliverySessionError('Unsupported GitHub push authentication mode')
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
    """Shared, bounded GitHub provider transport over the Actions opener."""
    from .delivery_provider import request_json
    return request_json(client, method, path, payload)


def pull_requests(client: GitHubActionsClient, slug: str, head: str,
                  base: str, *, head_repository: str | None = None) -> list[dict[str, Any]]:
    slug = _slug(slug)
    if not _REF.fullmatch(head) or not _REF.fullmatch(base):
        raise DeliverySessionError('Unsafe PR branch identity')
    owner = _slug(head_repository or slug).split('/', 1)[0]
    # Do not filter by base: wrong-base PRs must remain visible to the
    # collision guard instead of disappearing from the provider result.
    query = urlencode({'state': 'open', 'head': f'{owner}:{head}',
                       'per_page': '100'})
    results: list[dict[str, Any]] = []
    numbers: set[int] = set()
    # GitHub PR lists do not expose total_count. Exhaust bounded pages instead
    # of treating a full first page as authoritative or quietly truncating it.
    for page in range(1, 6):
        rows = _github_request(client, 'GET', f'/repos/{slug}/pulls?{query}&page={page}')
        if not isinstance(rows, list) or len(rows) > 100:
            raise DeliverySessionError('Malformed GitHub PR pagination response')
        for row in rows:
            if not isinstance(row, dict) or type(row.get('number')) is not int or row['number'] <= 0:
                raise DeliverySessionError('Malformed GitHub PR inventory identity')
            if row['number'] in numbers:
                raise DeliverySessionError('Duplicate GitHub PR across pages')
            numbers.add(row['number'])
            results.append(row)
        if len(rows) < 100:
            return results
    raise DeliverySessionError('PR inventory exceeds bounded five-page capacity')


def _pr_match(item: dict[str, Any], slug: str, head: str, base: str,
              sha: str, *, head_repository: str | None = None,
              base_sha: str | None = None) -> dict[str, Any] | None:
    if item.get('state') != 'open':
        return None
    h, b = item.get('head'), item.get('base')
    if not isinstance(h, dict) or not isinstance(b, dict):
        raise DeliverySessionError('PR identity is incomplete')
    head_repo = _slug(head_repository or slug)
    full_name = (h.get('repo') or {}).get('full_name')
    if not isinstance(full_name, str) or full_name.casefold() != head_repo.casefold():
        return None
    if h.get('ref') != head or b.get('ref') != base:
        return None
    if (b.get('repo') or {}).get('full_name', '').casefold() != slug.casefold():
        return None
    if head_repo.casefold() != slug.casefold():
        # Forks require the full provider head label to distinguish two forks
        # exposing the same short branch name.
        if h.get('label') != f"{head_repo.split('/',1)[0]}:{head}":
            return None
    elif h.get('label') != f"{head_repo.split('/',1)[0]}:{head}":
        return None
    actual_base_sha = b.get('sha')
    if base_sha is not None and actual_base_sha != base_sha:
        raise DeliverySessionError('PR base branch moved since its pinned SHA')
    if h.get('sha') != sha:
        raise DeliverySessionError('Existing PR head differs from source session; refusing stale PR')
    number = item.get('number')
    if type(number) is not int or number <= 0:
        raise DeliverySessionError('PR lacks exact positive number')
    return {'number': number, 'head_sha': sha, 'head': head, 'base': base,
            'base_repository': slug, 'head_repository': head_repo,
            'base_sha': actual_base_sha,
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
    base_repo = _slug(item.get('base_repository') or slug)
    head_repo = _slug(item.get('head_repository') or slug)
    pinned_base = item.get('base_sha')
    if auth == 'app':
        from .github_app import make_app_client
    if client is None:
        if auth == 'app':
            client = make_app_client(base_repo,'pr_create')
        else:
            token, _ = resolve_token(auth)
            client = GitHubActionsClient(token)
    head_client = (make_app_client(head_repo,'repository_read',role='head')
                   if auth == 'app' and head_repo.casefold()!=base_repo.casefold()
                   else client)
    remote = _remote_head(head_client, head_repo, head)
    if remote != sha:
        raise DeliverySessionError('Remote branch is missing or has different SHA; push/reconcile first')
    base_tip = _remote_head(client, base_repo, base)
    if pinned_base is not None and base_tip != pinned_base:
        raise DeliverySessionError('Base branch drift: pinned upstream commit differs')
    def inventory_read() -> list[dict[str, Any]]:
        if base_repo.casefold() == head_repo.casefold():
            return pull_requests(client, base_repo, head, base)
        return pull_requests(client, base_repo, head, base, head_repository=head_repo)
    inventory = inventory_read()
    for entry in inventory:
        h = entry.get('head') or {}
        b = entry.get('base') or {}
        if (entry.get('state') == 'open' and h.get('ref') == head and
            (h.get('repo') or {}).get('full_name','').casefold() == head_repo.casefold() and
            b.get('ref') != base):
            raise DeliverySessionError('Existing open PR targets another base; manual reconciliation required')
    found = [_pr_match(x, base_repo, head, base, sha,
                       head_repository=head_repo, base_sha=pinned_base) for x in inventory]
    found = [x for x in found if x is not None]
    if len(found) > 1:
        raise DeliverySessionError('Multiple matching PRs; refuse ambiguous reconciliation')
    # Fixed-size key keeps the delivery intent within its bounded journal.
    key = hashlib.sha256(f'{base_repo}:{base}:{head_repo}:{head}:{sha}'.encode()).hexdigest()
    if found:
        observed = found[0]
        record_pr_observation(session_id, observed, state_root=state_root)
        confirm_intent(session_id, 'pr_create', key,
                       {'head_sha': sha, 'pr_number': observed['number']},
                       state_root=state_root, optional=True)
        return {'state': 'PR_VERIFIED', 'created_now': False, **observed}
    if yes and base_repo.casefold() != head_repo.casefold() and pinned_base is None:
        raise DeliverySessionError('Fork PR creation requires pinned upstream base SHA')
    if not yes:
        return {'state': 'PR_REQUIRES_APPROVAL', 'created_now': False,
                'head_sha': sha, 'source_bound': False}
    if not getattr(client, '_token', None):
        raise DeliverySessionError('Authenticated GitHub PR write access required; no intent recorded')
    if isinstance(client, GitHubActionsClient):
        from .delivery_provider import preflight
        capability = preflight(client, base_repo, 'pr_create')
        if capability['state'] in ('permission_unavailable','provider_unavailable','resource_unverified'):
            raise DeliverySessionError('Upstream PR capability unavailable; no mutation intent recorded')
    if title and (len(title) > 160 or '\n' in title):
        raise DeliverySessionError('PR title must be one line and at most 160 characters')
    if len(body) > 16000:
        raise DeliverySessionError('PR body too large')
    intent = record_intent(session_id, 'pr_create', key, approved=True, state_root=state_root)
    if not intent['new']:
        raise DeliverySessionError('Previous PR creation outcome unresolved; reconcile, do not retry')
    payload = {'head': head if base_repo.casefold() == head_repo.casefold() else
               f"{head_repo.split('/',1)[0]}:{head}", 'base': base,
               'title': title or f'Delivery: {head}', 'body': body,
               'draft': True}
    response = _github_request(client, 'POST', f'/repos/{base_repo}/pulls', payload)
    if not isinstance(response, dict):
        raise DeliverySessionError('PR creation returned non-object; outcome uncertain')
    created = _pr_match(response, base_repo, head, base, sha,
                        head_repository=head_repo, base_sha=pinned_base)
    if created is None:
        raise DeliverySessionError('PR response did not prove exact source identity; reconcile manually')
    # Confirm from a subsequent independent read, never just trust POST body.
    if _remote_head(head_client, head_repo, head) != sha or (
            pinned_base is not None and _remote_head(client, base_repo, base) != pinned_base):
        raise DeliverySessionError('Remote source or pinned base moved after PR POST; reconcile without retry')
    selected = [_pr_match(x,base_repo,head,base,sha,
                          head_repository=head_repo, base_sha=pinned_base)
                for x in inventory_read()]
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
    exact = (event == 'push' and
             item.get('head_repository', item['repository']).casefold() == item['repository'].casefold() and
             sha == item['head_sha'] and branch == item['head'])
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
    head_repo = item.get('head_repository', item['repository'])
    base_repo = item.get('base_repository', item['repository'])
    if base_repo.casefold() != head_repo.casefold() and required_event != 'pull_request':
        raise DeliverySessionError('Fork qualification requires upstream pull_request event')
    # Required PR CI executes in the upstream repository, but the branch
    # source is independently checked in the fork on every observation.
    item = {**item, 'repository': base_repo}
    if auth == 'app':
        from .github_app import make_app_client
    if client is None:
        if auth == 'app':
            client = make_app_client(base_repo,'checks_read')
        else:
            token, _ = resolve_token(auth)
            client = GitHubActionsClient(token)
    head_client = (make_app_client(head_repo,'repository_read',role='head')
                   if auth == 'app' and head_repo.casefold()!=base_repo.casefold()
                   else client)
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
        from .delivery_ci_diagnostics import diagnose_snapshot
        summary['diagnosis'] = diagnose_snapshot(summary, slug=item['repository'], client=None)
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
        actual_head = _remote_head(head_client, head_repo, item['head'])
        if actual_head != item['head_sha']:
            raise DeliverySessionError('Remote branch moved during CI; qualification blocked')
        if not pr_info:
            return None
        live = client._get(f"/repos/{item['repository']}/pulls/{pr_info['number']}")
        verified = _pr_match(live, base_repo, item['head'], item['base'], item['head_sha'],
                             head_repository=head_repo, base_sha=item.get('base_sha'))
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
                raw = registrar.collect_runs(client, item['repository'],
                                             window=window, event=required_event)
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
                from .delivery_ci_diagnostics import _safe_label
                branch_rules_issue = _safe_label(str(exc), 220)
        if state == 'REQUIRED_CHECKS_PASSED':
            # Detect a policy/head/merge ref change while all remote calls were in flight.
            if policy_guard is not None:
                policy_guard()
            if live_provenance() != merge_sha:
                raise DeliverySessionError('PR merge identity changed during CI poll; qualification blocked')
        diag = []
        if state == 'CHECKS_FAILED':
            from .delivery_ci_evidence import failed_job_details
            from .delivery_ci_diagnostics import sanitize_failed_metadata
            for match in failures:
                # S5 also sanitizes the compatibility fields S4 already wrote.
                # Raw provider metadata never bypasses the new bounded receipt.
                diag.append({'run_id': match['id'], 'attempt': match['attempt'],
                             'name': match['name'], 'url': match['url'],
                             'jobs': sanitize_failed_metadata(
                                 _failed_jobs(client, item['repository'], match), detailed=False),
                             'detailed_jobs': sanitize_failed_metadata(
                                 failed_job_details(client, item['repository'], match), detailed=True),
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
        # S5: diagnosis is observational and never changes CI qualification.
        # Only bounded, redacted error snippets may enter the durable receipt;
        # no signed log URLs, raw logs, auth tokens, or automatic reruns.
        from .delivery_ci_diagnostics import diagnose_snapshot
        summary['diagnosis'] = diagnose_snapshot(
            summary, slug=item['repository'], client=client,
            fetch_logs=(state == 'CHECKS_FAILED'))
        record_ci_observation(session_id, summary, state_root=state_root)
        snapshots.append({'poll':i+1,'state':state,'run_count':len(records)})
        if progress:
            progress(summary)
        if state not in ('WAITING_REGISTRATION','CHECKS_RUNNING','CHECKS_EVIDENCE_PENDING'):
            break
        if i+1 < checks:
            sleep(registrar.next_delay(interval, i, session_id) if strict_registration else interval)
    return {**summary,'history':snapshots}
