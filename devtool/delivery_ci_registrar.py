"""S4 registrar: source-bound automatic GitHub check registration.

Separate event mechanisms are never conflated. A workflow_dispatch response
without an independently correlated run ID stays uncorrelated. Delayed
registration cannot be marked failed before a fixed, session-bound deadline.

The registrar does not dispatch, rerun, push, update PRs, or qualify releases.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from fnmatch import fnmatchcase
from pathlib import Path
from urllib.parse import quote
import re
import subprocess
from typing import Any, Mapping

from .delivery_session import DeliverySessionError

MAX_RESULTS = 500
DEFAULT_HORIZON_SECONDS = 1800
_CLOCK_SKEW_SECONDS = 120
_WORKFLOW_PATH = re.compile(r'\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml\Z')
_EVENTS = frozenset({'push', 'pull_request', 'workflow_dispatch'})


def _date(value: Any) -> datetime:
    if not isinstance(value, str) or len(value) > 50:
        raise DeliverySessionError('GitHub registration time is missing or invalid')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise DeliverySessionError('GitHub registration time is malformed') from None
    if result.utcoffset() is None:
        raise DeliverySessionError('GitHub registration time has no UTC offset')
    return result.astimezone(timezone.utc)





def registration_window(session: Mapping[str, Any], horizon_seconds: int,
                        *, now: datetime | None = None,
                        verified_pr_times: tuple[str | None, str | None] | None = None) -> dict[str, Any]:
    """Build finite event windows from independently verified PR timestamps.

    A delivery session may precede its PR by hours. Each verified PR anchor
    receives its own *bounded* horizon; do not widen a session window across
    arbitrary time or infer an event timestamp from a matching workflow run.
    Retain the creation window if a later PR update/comment changes updated_at.
    Exact workflow/PR/SHA/attempt checks still own qualification.
    """
    if type(horizon_seconds) is not int or not 60 <= horizon_seconds <= 7200:
        raise DeliverySessionError('CI registration horizon must be 60..7200 seconds')
    began = _date(session.get('created_at'))
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if began > now + timedelta(seconds=_CLOCK_SKEW_SECONDS):
        raise DeliverySessionError('Delivery session timestamp is in the future')
    anchors: list[tuple[str, datetime]] = [('session_created_at', began)]
    if verified_pr_times is not None:
        if not isinstance(verified_pr_times, tuple) or len(verified_pr_times) != 2:
            raise DeliverySessionError('Invalid verified PR registration timestamps')
        created_raw, updated_raw = verified_pr_times
        if created_raw is not None:
            created = _date(created_raw)
            if created > now + timedelta(seconds=_CLOCK_SKEW_SECONDS):
                raise DeliverySessionError('Verified PR creation is in the future')
            if created > began:
                anchors.append(('verified_pr_created_at', created))
            if updated_raw is not None:
                updated = _date(updated_raw)
                if updated < created or updated > now + timedelta(seconds=_CLOCK_SKEW_SECONDS):
                    raise DeliverySessionError('Verified PR update time is invalid')
                if updated > began and updated > created:
                    anchors.append(('verified_pr_updated_at', updated))
        elif updated_raw is not None:
            raise DeliverySessionError('PR update timestamp lacks creation evidence')
    # At most three bounded windows; avoid duplicate anchors without relying on
    # the undocumented ordering of provider-side run lists.
    ranges: list[dict[str, Any]] = []
    seen: set[datetime] = set()
    for source, anchor in anchors:
        if anchor in seen:
            continue
        seen.add(anchor)
        ranges.append({'began': anchor, 'deadline': anchor + timedelta(seconds=horizon_seconds),
                       'source': source})
    newest = max(ranges, key=lambda x: x['began'])
    return {'began': newest['began'], 'deadline': newest['deadline'], 'now': now,
            'expired': all(now > x['deadline'] for x in ranges),
            'started_at': newest['began'].isoformat(),
            'deadline_at': newest['deadline'].isoformat(),
            'horizon_seconds': horizon_seconds, 'anchor_source': newest['source'],
            'ranges': tuple(ranges)}


def collect_runs(client: Any, slug: str, *, window: Mapping[str, Any] | None = None,
                 event: str = '') -> list[dict[str, Any]]:
    """Exhaustively scan event/time-scoped runs, without guessing identities.

    Each registered window is <= 2 hours. Disjoint PR/session windows are
    queried independently and deduplicated by immutable provider run ID.
    Missing pages, inconsistent totals, duplicate IDs with different bodies,
    or a combined inventory >= 500 all fail closed.
    """
    from .github_actions import GitHubActionsClient
    if window is not None and isinstance(client, GitHubActionsClient):
        from urllib.parse import urlencode
        if event not in _EVENTS:
            raise DeliverySessionError('Invalid scoped CI event')
        spans = tuple(window.get('ranges') or (window,))
        if len(spans) < 1 or len(spans) > 3:
            raise DeliverySessionError('Invalid bounded CI registration anchor count')

        def utc_stamp(value: datetime) -> str:
            return value.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

        unique: dict[int, dict[str, Any]] = {}
        for span in spans:
            start = span['began'] - timedelta(seconds=_CLOCK_SKEW_SECONDS)
            end = min(span['deadline'], window['now']) + timedelta(seconds=_CLOCK_SKEW_SECONDS)
            if end < start:
                raise DeliverySessionError('Invalid scoped CI registration time window')
            date_filter = utc_stamp(start) + '..' + utc_stamp(end)
            count: int | None = None
            found: set[int] = set()
            for page in range(1, 1 + (MAX_RESULTS + 99) // 100):
                query = urlencode({'event': event, 'created': date_filter,
                                   'per_page': 100, 'page': page})
                result = client._get(f'/repos/{slug}/actions/runs?{query}')
                total = result.get('total_count')
                items = result.get('workflow_runs')
                if (type(total) is not int or total < 0 or total >= MAX_RESULTS or
                        (count is not None and count != total) or
                        not isinstance(items, list) or len(items) > 100):
                    raise DeliverySessionError('CI registration pagination totals are incomplete or inconsistent')
                count = total
                for item in items:
                    if not isinstance(item, dict) or item.get('event') != event:
                        raise DeliverySessionError('CI registration API event filter was not honored')
                    rid = item.get('id')
                    if type(rid) is not int or rid <= 0 or rid in found:
                        raise DeliverySessionError('CI registration pagination duplicate or invalid run ID')
                    found.add(rid)
                    created = _date(item.get('created_at'))
                    if not start <= created <= end:
                        raise DeliverySessionError('CI registration API created filter was not honored')
                    if rid in unique and unique[rid] != item:
                        raise DeliverySessionError('CI run changed across registration anchors')
                    unique[rid] = item
                if len(found) > total or len(unique) >= MAX_RESULTS:
                    raise DeliverySessionError('CI registration inventory exceeds bounded limit')
                if len(found) == total:
                    break
                if len(items) != 100:
                    raise DeliverySessionError('CI registration pagination ended before declared total')
            else:
                raise DeliverySessionError('CI registration pagination exceeded bounded session window capacity')
        return list(unique.values())
    if callable(getattr(client, '_paged', None)):
        rows = client._paged(slug, f'/repos/{slug}/actions/runs', 'workflow_runs',
                             max_results=MAX_RESULTS)
    else:
        rows = client.runs(slug, limit=100)
        if len(rows) >= 100:
            raise DeliverySessionError('CI inventory is truncated; pagination unavailable')
    if not isinstance(rows, list) or len(rows) >= MAX_RESULTS or any(not isinstance(x, dict) for x in rows):
        raise DeliverySessionError('CI run pagination is incomplete or invalid')
    return rows


def _matches(value: str, patterns: Any) -> bool | None:
    """Only support a GitHub-glob subset whose meaning we can prove."""
    if not isinstance(patterns, list) or not patterns:
        return None
    found = False
    for pattern in patterns:
        if not isinstance(pattern, str) or not pattern or any(x in pattern for x in ('!', '?', '[', ']')):
            return None
        if fnmatchcase(value, pattern):
            found = True
    return found


def _workflow_yaml(root: Path, path: str) -> dict[str, Any]:
    if not _WORKFLOW_PATH.fullmatch(path):
        raise DeliverySessionError('Provider workflow path escaped .github/workflows')
    try:
        p = subprocess.run(['git', '-C', str(root), 'show', 'HEAD:' + path],
                           capture_output=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise DeliverySessionError('Cannot inspect committed workflow trigger') from None
    if p.returncode or len(p.stdout) > 512 * 1024:
        raise DeliverySessionError('Provider workflow is missing from exact source commit')
    try:
        import yaml
        document = yaml.safe_load(p.stdout)
    except Exception as exc:
        raise DeliverySessionError('Cannot parse committed workflow trigger safely') from exc
    if not isinstance(document, dict):
        raise DeliverySessionError('Committed GitHub workflow is not an object')
    return document


def workflow_expectations(client: Any, session: Mapping[str, Any],
                          workflow_ids: Mapping[str, int], event: str) -> dict[str, str]:
    """Inspect real provider workflow ID, committed event and branch trigger.

    `not_expected` may be used only for a proven branch-filter exclusion;
    trigger paths or unsupported conditional constructs are `indeterminate`.
    An indeterminate trigger requires an independently proven registered run.
    """
    if event not in _EVENTS:
        raise DeliverySessionError('Unsupported CI registration event')
    root = Path(str(session['repo_root']))
    slug = str(session['repository'])
    ref = str(session['base'] if event == 'pull_request' else session['head'])
    result: dict[str, str] = {}
    for name, identity in workflow_ids.items():
        if type(identity) is not int or identity < 1:
            raise DeliverySessionError('Invalid required workflow ID')
        item = client.workflow(slug, str(identity))
        if item.get('id') != identity or item.get('name') != name:
            raise DeliverySessionError('GitHub workflow name/ID no longer matches committed CI policy')
        if item.get('state') not in (None, 'active'):
            raise DeliverySessionError('Required GitHub workflow is not active')
        doc = _workflow_yaml(root, str(item.get('path') or ''))
        if doc.get('name') != name:
            raise DeliverySessionError('Committed workflow name differs from provider/policy')
        # PyYAML 1.1 treats the key `on` as bool True; GitHub treats it literally.
        triggers = doc.get('on', doc.get(True))
        if isinstance(triggers, str):
            triggers = {triggers: {}}
        if isinstance(triggers, list):
            triggers = {k: {} for k in triggers if isinstance(k, str)}
        if not isinstance(triggers, dict) or event not in triggers:
            result[name] = 'not_expected'
            continue
        trigger = triggers[event]
        if trigger is None:
            trigger = {}
        if not isinstance(trigger, dict):
            result[name] = 'indeterminate'
            continue
        if any(k in trigger for k in ('paths', 'paths-ignore', 'types', 'tags', 'tags-ignore')):
            # Path filters, skipped conditional jobs and action types are not
            # provable from workflow names alone: fail closed, not skipped.
            result[name] = 'indeterminate'
            continue
        allow = trigger.get('branches')
        deny = trigger.get('branches-ignore')
        if allow is not None and deny is not None:
            result[name] = 'indeterminate'
            continue
        if allow is not None:
            found = _matches(ref, allow)
            result[name] = ('expected' if found else 'not_expected') if found is not None else 'indeterminate'
        elif deny is not None:
            found = _matches(ref, deny)
            result[name] = ('not_expected' if found else 'expected') if found is not None else 'indeterminate'
        else:
            result[name] = 'expected'
    return result


def select_registration(rows: list[dict[str, Any]], session: Mapping[str, Any],
                        *, event: str, required: Mapping[str, int],
                        expected: Mapping[str, str], merge_sha: str | None,
                        pr_info: Mapping[str, Any] | None,
                        window: Mapping[str, Any], match_run: Any,
                        dispatch_run_ids: Mapping[int, int] | None = None) -> dict[str, Any]:
    """Select exact event/run provenance; never guess same-source concurrent IDs."""
    seen: dict[str, dict[str, Any]] = {}
    alternatives: dict[str, set[int]] = {}
    unknown_time: set[str] = set()
    dispatch_run_ids = dispatch_run_ids or {}
    for row in rows:
        name = row.get('name')
        if (name not in required or expected.get(name) not in ('expected', 'indeterminate')
                or row.get('workflow_id') != required[name]):
            continue
        if row.get('event') != event:
            continue
        try:
            registered = _date(row.get('created_at'))
        except DeliverySessionError:
            unknown_time.add(name)
            continue
        spans = window.get('ranges') or (window,)
        in_any_window = any(
            span['began'] - timedelta(seconds=_CLOCK_SKEW_SECONDS) <= registered
            <= span['deadline'] + timedelta(seconds=_CLOCK_SKEW_SECONDS)
            for span in spans
        )
        if not in_any_window or registered > window['now'] + timedelta(seconds=_CLOCK_SKEW_SECONDS):
            continue
        if event == 'workflow_dispatch':
            # A legacy 204, unknown POST, or merely same-branch candidate is
            # NOT correlated; caller must supply a trusted receipt's exact ID.
            if dispatch_run_ids.get(required[name]) != row.get('id'):
                continue
        matched = match_run(row, session, pr_info, merge_sha)
        if not isinstance(matched, dict):
            continue
        if (type(row.get('run_attempt')) is not int or row['run_attempt'] < 1 or
                matched.get('attempt') != row['run_attempt']):
            continue
        old = seen.get(name)
        rid = matched['id']
        alternatives.setdefault(name, set()).add(rid)
        if old is None or (rid, matched['attempt']) > (old['id'], old['attempt']):
            seen[name] = matched
    ambiguous = sorted(name for name, ids in alternatives.items() if len(ids) > 1)
    missing = [name for name in required if expected.get(name) in ('expected', 'indeterminate')
               and name not in seen]
    not_expected = [name for name in required if expected.get(name) == 'not_expected']
    # Successful *registration* resolves uncertain static trigger filters.
    # CI success still requires independent exact-run/attempt verification.
    conditional = [name for name in required if expected.get(name) == 'indeterminate'
                   and name not in seen]
    return {'runs': sorted(seen.values(), key=lambda r:r['name']), 'missing':missing,
            'ambiguous':ambiguous, 'not_expected':not_expected,
            'indeterminate':conditional, 'unknown_time':sorted(unknown_time),
            'run_count': len(rows), 'event':event,
            'deadline_at':window['deadline_at'], 'started_at':window['started_at'],
            'horizon_seconds':window['horizon_seconds'], 'expired':window['expired'],
            'anchor_source':window.get('anchor_source', 'session_created_at'),
            'dispatch_uncorrelated': event == 'workflow_dispatch' and not dispatch_run_ids}


def next_delay(interval: float, poll: int, seed: str) -> float:
    """Bounded deterministic jitter avoids a synchronized registration thundering herd."""
    import hashlib
    if interval < 1 or interval > 120 or poll < 0:
        raise DeliverySessionError('Invalid CI polling/backoff configuration')
    rnd = int(hashlib.sha256(f'{seed}:{poll}'.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return min(120.0, max(1.0, interval * min(8, 2**poll) * (0.85 + 0.3 * rnd)))


def audit_branch_rules(client: Any, session: Mapping[str, Any],
                       records: list[dict[str, Any]],
                       *, rules_reader: Any = None) -> dict[str, Any]:
    """Reconcile GitHub branch/ruleset required check *contexts* to exact runs.

    Inaccessible provider rules cannot be represented as an empty required set.
    This is CI verification only, not merge or release authorization. Contexts
    from an unrelated app are never treated as matching a Devtool workflow.
    """
    slug = str(session['repository'])
    base = str(session['base'])
    if not re.fullmatch(r'[A-Za-z0-9._/-]{1,200}', base) or '..' in base:
        raise DeliverySessionError('Unsafe base branch identity for GitHub branch-rule read')
    branch = client._get(f'/repos/{slug}/branches/{base}')
    protected = branch.get('protected')
    if type(protected) is not bool:
        raise DeliverySessionError('GitHub branch protection status not independently verifiable')
    if rules_reader is None and callable(getattr(client, 'branch_rules', None)):
        rules_reader = lambda c, s, b: c.branch_rules(s, b)
    if rules_reader is None:
        from .delivery_github import _github_request
        rules = _github_request(client, 'GET', f'/repos/{slug}/rules/branches/{quote(base, safe="")}')
    else:
        rules = rules_reader(client, slug, base)
    if not isinstance(rules, list) or len(rules) > 100 or any(not isinstance(r, dict) for r in rules):
        raise DeliverySessionError('Branch ruleset response is incomplete or invalid')
    contexts: list[str] = []
    if protected:
        # GitHub's `protected` flag includes *ruleset-only* protection. The
        # classic `.../protection/required_status_checks` endpoint needs
        # Administration(read) and may return 403 to a normal Actions token,
        # even when no classic protection exists. Do not require that endpoint
        # if the branch GET itself proves classic protection is disabled.
        classic = branch.get('protection')
        classic_disabled = isinstance(classic, dict) and classic.get('enabled') is False
        if classic_disabled:
            # A mere `enabled:false` is not enough: fail closed unless the
            # same provider response explicitly reports *no* classic contexts.
            advertised = classic.get('required_status_checks')
            if (not isinstance(advertised, dict)
                    or advertised.get('enforcement_level') != 'off'
                    or advertised.get('contexts') != []
                    or advertised.get('checks') != []):
                raise DeliverySessionError('Disabled classic protection lacks complete off/empty evidence')
            if not rules:
                raise DeliverySessionError('Protected branch has no independently verified effective rules')
        else:
            # Classic protection is active or its state is ambiguous. Retain
            # the independent endpoint read and block on 401/403/404/partial
            # evidence rather than treating any uncertainty as a CI pass.
            required = client._get(f'/repos/{slug}/branches/{base}/protection/required_status_checks')
            if not isinstance(required, dict):
                raise DeliverySessionError('Required protected-branch status checks unavailable')
            raw_contexts = required.get('contexts', [])
            raw_checks = required.get('checks', [])
            if not isinstance(raw_contexts, list) or not isinstance(raw_checks, list):
                raise DeliverySessionError('Protected-branch status check inventory invalid')
            contexts.extend(raw_contexts)
            for check in raw_checks:
                if not isinstance(check, dict) or not isinstance(check.get('context'), str):
                    raise DeliverySessionError('Invalid protected-branch context')
                # GitHub Actions is app 15368; app_id null means unconstrained.
                if check.get('app_id') not in (None, 15368):
                    raise DeliverySessionError('Protected-branch check belongs to an external app')
                contexts.append(check['context'])
    for rule in rules:
        kind = rule.get('type')
        if kind == 'required_workflows':
            raise DeliverySessionError('Ruleset adds provider-required workflows not declared in CI policy')
        if kind != 'required_status_checks':
            continue  # Reviews and merge rules are distinct from CI success.
        params = rule.get('parameters')
        if not isinstance(params, dict):
            raise DeliverySessionError('Ruleset required status check parameters missing')
        checks = params.get('required_status_checks')
        if not isinstance(checks, list):
            raise DeliverySessionError('Ruleset required status check context inventory missing')
        for entry in checks:
            if not isinstance(entry, dict) or not isinstance(entry.get('context'), str):
                raise DeliverySessionError('Invalid ruleset check context')
            if entry.get('integration_id') not in (None, 15368):
                raise DeliverySessionError('Ruleset check belongs to an external GitHub app')
            contexts.append(entry['context'])
    contexts = sorted(set(contexts))
    if len(contexts) > 100 or any(not c or len(c) > 160 for c in contexts):
        raise DeliverySessionError('GitHub branch CI context list exceeds safety bounds')
    if contexts:
        successful_jobs: set[str] = set()
        for row in records:
            if row.get('status') != 'completed' or row.get('conclusion') != 'success':
                continue
            jobs = client.run_attempt_jobs(slug, row['id'], row['attempt'])
            if not isinstance(jobs, list) or not jobs or len(jobs) >= 200:
                raise DeliverySessionError('Protected-branch job list is incomplete')
            for job in jobs:
                if (not isinstance(job, dict) or job.get('run_id') not in (None, row['id'])
                        or job.get('run_attempt') not in (None, row['attempt'])):
                    raise DeliverySessionError('Protected-branch check job source mismatch')
                if job.get('status') == 'completed' and job.get('conclusion') == 'success':
                    successful_jobs.add(str(job.get('name') or ''))
        missing = [context for context in contexts if context not in successful_jobs]
        if missing:
            raise DeliverySessionError('Provider-required status contexts are not fully satisfied: '+ ', '.join(missing[:4]))
    return {'protection_checked': True, 'protected': protected,
            'ruleset_checked': True, 'required_status_contexts':contexts,
            'required_status_contexts_verified': True,
            'release_qualified': False}
