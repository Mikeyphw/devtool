"""Source-bound, opt-in post-recreation workflow and pull-request CI qualification.

All GitHub mutations require an exact repository/PR acknowledgement.  GitHub
Actions PR runs may have a synthetic merge SHA: prove association through the
run's pull_requests metadata rather than incorrectly comparing run.head_sha to
PR head SHA.  Legacy workflow IDs are never reused as replacement IDs.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import time
import urllib.parse


def _core():
    from . import engine as cli
    return cli


def _utc(value):
    return dt.datetime.fromisoformat(value.replace('Z', '+00:00'))


def _ensure_restored(session, state):
    rr = _core()
    rr.require(state['stage'] in ('done', 'done-with-action-required', 'done-manual-attested'),
               'repository restoration must finish before CI qualification')
    rr.verify_pair(session, state)
    rr.require_independent_backup(session, state)
    live = rr.get_new_repo(state)
    rr.require(live['id'] == state['created_new_id'], 'replacement repository ID drifted')
    return live


def _branch_sources(repo, refs):
    """Read workflow source across restored branch SHAs, not historical registrations.

    The contents API must return a complete directory listing. A refused branch
    is an audit error, never silently reported as an empty directory.
    """
    rr = _core()
    names = [r for r in refs if r.startswith('refs/heads/')]
    rr.require(len(names) <= 200, 'over 200 branches; explicit audit partition required')
    found = {}
    gaps = []
    for ref in sorted(names):
        sha = refs[ref]
        rr.require(re.fullmatch(r'[0-9a-f]{40}', sha or '') is not None,
                   'non-SHA source ref: ' + ref)
        endpoint = 'repos/' + repo + '/contents/.github/workflows?ref=' + sha
        data = rr.api(endpoint, missing_ok=True)
        if data is None:
            continue
        rr.require(isinstance(data, list), 'unexpected workflow source directory response')
        for obj in data:
            path = obj.get('path', '')
            if obj.get('type') != 'file' or not path.endswith(('.yml', '.yaml')):
                continue
            rr.require(path.startswith('.github/workflows/') and
                       '/' not in path[len('.github/workflows/'):],
                       'workflow source path is not a direct workflow file')
            found.setdefault(path, []).append({'ref': ref, 'sha': sha,
                                                 'blob': obj.get('sha')})
    return found, gaps


def workflow_inventory(session):
    """Read-only discovery. Audit registrations separately from branch source."""
    rr = _core()
    state = rr.load_state(session)
    repo_info = _ensure_restored(session, state)
    repo = state['repo']
    refs = rr.live_refs(repo)
    # After recovery, ordinary merges/pushes may legitimately advance branch
    # tips (MMRL's N05F merge advanced `next`). This is a read-only *current*
    # workflow inventory, not another immutable restoration attestation. Preserve
    # immutable restoration expectations separately and report all differences.
    expected, _ = rr.expected_ref_mapping(state, session/'primary', refs)
    changes = []
    for ref in sorted(expected):
        wanted, actual = expected[ref], refs.get(ref)
        if actual != wanted:
            changes.append({'ref': ref, 'expected_sha': wanted, 'current_sha': actual,
                            'change': 'removed' if actual is None else 'advanced-or-replaced'})
    unexpected = sorted(set(refs) - set(expected) - set(state.get('fork_seed_refs', {})))
    for ref in unexpected:
        changes.append({'ref': ref, 'expected_sha': None,
                        'current_sha': refs[ref], 'change': 'added-after-restoration'})
    branch_files, gaps = _branch_sources(repo, refs)
    live = rr.collect(f'repos/{repo}/actions/workflows', key='workflows')
    original = rr.read_json(session/'primary'/'actions'/'workflows.json')
    registered = {w['path']: w for w in live if isinstance(w.get('path'), str)}
    original_paths = {w['path'] for w in original if w.get('state') != 'deleted'}
    default_ref = 'refs/heads/' + repo_info['default_branch']
    rows = []
    for path in sorted(set(branch_files) | original_paths | set(registered)):
        sources = branch_files.get(path, [])
        registration = registered.get(path)
        if registration and registration.get('state') == 'active':
            status = 'registered-active'
        elif registration:
            status = 'registered-inactive'
        elif not sources:
            status = 'source-missing'
        elif not any(s['ref'] == default_ref for s in sources):
            status = 'branch-only-unregistered'
        else:
            status = 'default-source-unregistered'
        rows.append({'path': path, 'status': status,
                     'source_branches': [s['ref'][len('refs/heads/'):] for s in sources],
                     'default_source_present': any(s['ref'] == default_ref for s in sources),
                     'registered_id': registration.get('id') if registration else None,
                     'registered_name': registration.get('name') if registration else None,
                     'original_registration': path in original_paths})
    return {'schema': 1, 'repository': repo, 'replacement_id': state['created_new_id'],
            'default_branch': repo_info['default_branch'],
            'actions_enabled': rr.api(f'repos/{repo}/actions/permissions').get('enabled'),
            'branches_checked': sum(1 for p in refs if p.startswith('refs/heads/')),
            'workflows': rows, 'scan_gaps': gaps,
            'restoration_snapshot_matches_current_refs': not bool(changes),
            'post_restoration_ref_changes': changes,
            'snapshot_note': ('Current sources are audited at observed branch SHAs. '
                              'Differences from the immutable recovery snapshot are not '
                              'accepted as restoration evidence.'),
            'note': 'Branch-only source is preserved but dispatch/registration and hosted success are separate.'}


def audit_workflows(session, *, record=False):
    rr = _core()
    if record:
        with rr.locked(session):
            result = workflow_inventory(session)
            rr.write_json(session/'workflow-registration-audit.json', result)
            report_path = session/'report.json'
            if report_path.is_file():
                report = rr.read_json(report_path)
                report['workflow_registration_audit'] = 'workflow-registration-audit.json'
                report['workflow_registration_audit_at'] = rr.now()
                rr.write_json(report_path, report)
    else:
        result = workflow_inventory(session)
    rr.log(f'WORKFLOW SOURCE AND REGISTRATION: {result["repository"]}', style='bold')
    for item in result['post_restoration_ref_changes']:
        rr.log(f'POST-RECOVERY REF {item["change"]}: {item["ref"]} '
               f'expected={item["expected_sha"]} current={item["current_sha"]}',
               style='yellow')
    for item in result['workflows']:
        rr.log(f'{item["status"]:31} {item["path"]}  '
               f'[{", ".join(item["source_branches"])}]',
               style='green' if item['status']=='registered-active' else 'yellow')
    return result


def _current_pr(repo, number):
    rr = _core()
    rr.require(isinstance(number, int) and number > 0, 'invalid pull request number')
    pr = rr.api(f'repos/{repo}/pulls/{number}')
    head = pr.get('head') or {}
    base = pr.get('base') or {}
    rr.require(pr.get('number') == number and
               re.fullmatch('[0-9a-f]{40}', head.get('sha') or '') and
               re.fullmatch('[0-9a-f]{40}', base.get('sha') or '') and
               isinstance(head.get('ref'), str) and isinstance(base.get('ref'), str),
               'PR identity or immutable head/base refs unavailable')
    # Historical, *read-only* CI evidence for a merged PR is meaningful, but
    # never qualifies a currently open PR or authorizes a retrigger. GitHub
    # preserves the merged PR's immutable source/base identity separately from
    # the later branch tip, so inspect that PR object rather than the live base.
    if pr.get('merged_at') is not None:
        rr.require(pr.get('state') == 'closed' and
                   isinstance(pr.get('merged_at'), str) and
                   bool(pr['merged_at']),
                   'merged PR has inconsistent closed/merge identity')
    return pr


def _same_pr_identity(pr, identity):
    return (pr.get('number') == identity['number'] and
            (pr.get('head') or {}).get('sha') == identity['head_sha'] and
            (pr.get('base') or {}).get('sha') == identity['base_sha'] and
            (pr.get('head') or {}).get('ref') == identity['head_ref'] and
            (pr.get('base') or {}).get('ref') == identity['base_ref'] and
            (pr.get('head', {}).get('repo') or {}).get('id') == identity['head_repo_id'])


def _identity(pr):
    return {'number':pr['number'], 'head_sha':pr['head']['sha'],
            'base_sha':pr['base']['sha'], 'head_ref':pr['head']['ref'],
            'base_ref':pr['base']['ref'],
            'head_repo_id':(pr['head'].get('repo') or {}).get('id')}


def _policy_from_base(repo, base_sha, path):
    rr = _core()
    rr.require(path == 'config/delivery-required-ci.json',
               'only the reviewed config/delivery-required-ci.json path is supported')
    endpoint = ('repos/' + repo + '/contents/' + path + '?ref=' +
                urllib.parse.quote(base_sha, safe=''))
    obj = rr.api(endpoint, missing_ok=True)
    if obj is None:
        return None, None
    rr.require(obj.get('type') == 'file' and obj.get('encoding') == 'base64' and
               isinstance(obj.get('content'), str),
               'required-CI policy must be a regular base64 GitHub file')
    try:
        raw = base64.b64decode(''.join(obj['content'].split()), validate=True)
    except (ValueError, base64.binascii.Error) as e:
        raise rr.Refused('required-CI policy returned malformed base64') from e
    rr.require(len(raw) <= 128*1024, 'required-CI policy file too large')
    try:
        policy = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as e:
        raise rr.Refused('required-CI policy is not valid JSON') from e
    rr.require(isinstance(policy, dict) and policy.get('schema_version') == 1 and
               str(policy.get('repository', '')).lower() == repo.lower() and
               policy.get('event') == 'pull_request' and
               isinstance(policy.get('required_workflows'), list) and
               bool(policy.get('required_workflows')),
               'required-CI policy must be schema 1 for this repository and pull_request')
    return policy, hashlib.sha256(raw).hexdigest()


def _required_workflows(state, pr, explicit):
    rr = _core()
    policy, policy_sha = _policy_from_base(state['repo'], pr['base']['sha'],
                                           'config/delivery-required-ci.json')
    if policy:
        rr.require(policy.get('base_branch') == pr['base']['ref'],
                   'required-CI policy targets a different base branch')
        specs = policy['required_workflows']
        rr.require(all(isinstance(x, dict) and isinstance(x.get('name'), str) and
                       x['name'] for x in specs),
                   'required-CI policy must supply stable workflow names')
        require_names = [x['name'] for x in specs]
        # Explicit caller scope cannot silently weaken or replace a repository policy.
        if explicit:
            rr.require(set(require_names) <= set(explicit),
                       'explicit required workflows cannot weaken the on-branch Devtool policy')
            names = list(dict.fromkeys([*require_names, *explicit]))
        else:
            names = require_names
        origin = 'verified-base-policy'
    else:
        rr.require(bool(explicit),
                   'no config/delivery-required-ci.json at PR base; provide --required-workflow '
                   'for each reviewed CI workflow (no automatic guessing)')
        names = list(dict.fromkeys(explicit))
        origin = 'explicit-operator-selection'
    rr.require(0 < len(names) <= 30 and all(isinstance(n, str) and 0 < len(n) <= 200 for n in names),
               'expected 1–30 nonempty workflow identifiers')
    return names, origin, policy_sha


def _resolve_workflows(repo, specifiers, base_sha):
    rr = _core()
    all_live = rr.collect(f'repos/{repo}/actions/workflows', key='workflows')
    matched = []
    for identifier in specifiers:
        matches = [w for w in all_live if w.get('path') == identifier or
                   w.get('name') == identifier]
        rr.require(len(matches) == 1, f'required workflow absent or ambiguous: {identifier}')
        wf = matches[0]
        rr.require(wf.get('state') == 'active' and isinstance(wf.get('id'), int),
                   f'required workflow not enabled or missing ID: {identifier}')
        rr.require(wf.get('path', '').startswith('.github/workflows/'),
                   f'workflow has unexpected source path: {identifier}')
        source_endpoint = (f'repos/{repo}/contents/' +
                           urllib.parse.quote(wf['path'],safe='/') + '?ref=' + base_sha)
        source = rr.api(source_endpoint,missing_ok=True)
        rr.require(isinstance(source,dict) and source.get('type')=='file' and
                   source.get('path')==wf['path'] and
                   re.fullmatch(r'[0-9a-f]{40}', source.get('sha') or ''),
                   f'required workflow not source-proven at exact PR base {base_sha}: {identifier}')
        matched.append({'name':wf['name'], 'path':wf['path'], 'id':wf['id'],
                        'base_source_blob':source['sha']})
    rr.require(len({w['id'] for w in matched}) == len(matched),
               'multiple policy entries point to one workflow; remove duplicate selections')
    return matched


def _runs(repo, workflow_id):
    rr = _core()
    endpoint = f'repos/{repo}/actions/workflows/{workflow_id}/runs?event=pull_request'
    # A restored repository may have many historical PR runs. Never silently
    # decide from a truncated response; ask for narrower evidence instead.
    return rr.collect(endpoint, key='workflow_runs', limit_pages=10)


def _associated_with_pr(run, pr, workflow, replacement_id, *, not_before=None):
    if (run.get('workflow_id') != workflow['id'] or
        run.get('event') != 'pull_request' or
        run.get('status') not in ('queued','in_progress','completed','requested','waiting','pending')):
        return False
    if (run.get('path') or '').split('@',1)[0] != workflow['path']:
        return False
    repository = run.get('repository') or {}
    if repository.get('id') is not None and repository.get('id') != replacement_id:
        return False
    if not_before and (not run.get('created_at') or
                       _utc(run['created_at']) < _utc(not_before) - dt.timedelta(seconds=5)):
        return False
    for linked in run.get('pull_requests') or []:
        if linked.get('number') != pr['number']:
            continue
        head = linked.get('head') or {}
        base = linked.get('base') or {}
        if head.get('sha') != pr['head']['sha']:
            continue
        if base.get('ref') != pr['base']['ref']:
            continue
        # run.head_sha may be the synthetic refs/pull/N/merge SHA; do not
        # require it to equal the PR source branch SHA.
        return True
    return False


def _run_jobs(repo, run_id):
    rr = _core()
    jobs = rr.collect(f'repos/{repo}/actions/runs/{run_id}/jobs', key='jobs')
    rr.require(isinstance(jobs,list), 'Actions jobs payload is not a list')
    return [{'id':j.get('id'), 'name':j.get('name'), 'status':j.get('status'),
             'conclusion':j.get('conclusion')} for j in jobs]


def _evaluate(state, pr, expected, *, not_before=None):
    result = []
    for workflow in expected:
        matches = [r for r in _runs(state['repo'], workflow['id'])
                   if _associated_with_pr(r,pr,workflow,state['created_new_id'],
                                          not_before=not_before)]
        matches.sort(key=lambda x:(x.get('created_at') or '', int(x.get('id') or 0)),
                     reverse=True)
        current = matches[0] if matches else None
        jobs = (_run_jobs(state['repo'],current['id']) if current and
                current.get('status')=='completed' and
                current.get('conclusion')=='success' else [])
        jobs_pass = bool(jobs) and all(j['status']=='completed' and
                                        j['conclusion']=='success' for j in jobs)
        result.append({'workflow': workflow, 'status': current.get('status') if current else 'missing',
                       'conclusion': current.get('conclusion') if current else None,
                       'run_id':current.get('id') if current else None,
                       'run_attempt': current.get('run_attempt') if current else None,
                       'run_head_sha':current.get('head_sha') if current else None,
                       'url':current.get('html_url') if current else None,
                       'jobs': jobs, 'jobs_verified_success':jobs_pass,
                       'verified_success':bool(current and current.get('status') == 'completed' and
                                               current.get('conclusion') == 'success' and jobs_pass)})
    return result


def _events_since(repo, number, actor, started):
    rr = _core()
    events = rr.collect(f'repos/{repo}/issues/{number}/events')
    return [e.get('event') for e in events if e.get('event') in ('closed','reopened') and
            (e.get('actor') or {}).get('login', '').lower() == actor.lower() and
            e.get('created_at') and
            _utc(e['created_at']) >= _utc(started) - dt.timedelta(seconds=5)]


def _retrigger(session, state, pr, expected, acknowledgement):
    rr = _core()
    repo = state['repo']
    number = pr['number']
    rr.require(acknowledgement == f'{repo}#{number}',
               f'PR close/reopen requires --ack-retrigger {repo}#{number}')
    rr.require(pr['state'] in ('open','closed') and pr.get('merged_at') is None,
               'PR cannot be retriggered after merge')
    key = str(number)
    record = state.setdefault('pr_ci_retriggers', {}).get(key)
    identity = _identity(pr)
    desired = sorted(w['path'] for w in expected)
    if record is None:
        rr.require(pr['state'] == 'open', 'initial PR retrigger requires an open PR')
        actor = rr.api('user').get('login')
        rr.require(isinstance(actor,str) and actor, 'authenticated GitHub actor missing')
        record = {'schema':1, 'phase':'intent', 'identity':identity,
                  'required_paths': desired, 'actor':actor, 'started_at':rr.now(),
                  'approver': acknowledgement}
        state['pr_ci_retriggers'][key] = record
        rr.save_state(session,state)
    else:
        rr.require(record.get('identity') == identity and record.get('required_paths') == desired and
                   record.get('approver') == acknowledgement,
                   'PR head/base or approved required-CI policy changed during retrigger')
        rr.require(rr.api('user').get('login', '').lower() == record.get('actor', '').lower(),
                   'authenticated GitHub account differs from original retrigger actor')
    if record['phase'] == 'reopened':
        rr.require(pr['state'] == 'open', 'journaled reopened PR is no longer open')
        return record
    events = _events_since(repo, number, record['actor'], record['started_at'])
    if pr['state'] == 'open' and 'reopened' in events:
        rr.require('closed' in events,
                   'reopened event found without the matching close event')
        record['phase'] = 'reopened'
        record['completed_at'] = rr.now()
        rr.save_state(session,state)
        return record
    if pr['state'] == 'open':
        rr.require(record['phase'] == 'intent' and 'closed' not in events,
                   'PR is open but a previous close/reopen cannot be safely reconciled')
        rr.api(f'repos/{repo}/pulls/{number}',method='PATCH',payload={'state':'closed'})
        record['phase'] = 'closed'
        rr.save_state(session,state)
    else:
        rr.require('closed' in events or record['phase'] == 'closed',
                   'closed PR lacks this transaction close provenance')
        record['phase'] = 'closed'
        rr.save_state(session,state)
    updated = _current_pr(repo,number)
    rr.require(_same_pr_identity(updated, identity) and updated['state'] == 'closed',
               'PR is not closed at the approved immutable source; do not reopen blindly')
    rr.api(f'repos/{repo}/pulls/{number}',method='PATCH',payload={'state':'open'})
    updated = _current_pr(repo,number)
    rr.require(updated['state'] == 'open' and _same_pr_identity(updated,identity),
               'reopening PR failed or changed its head/base; journal preserved')
    record['phase'] = 'reopened'
    record['completed_at'] = rr.now()
    rr.save_state(session,state)
    return record


def _persist_result(session, state, identity, entries, source, policy_sha, *,
                    retrigger=None, merged_at=None):
    rr = _core()
    verified = all(e['verified_success'] for e in entries)
    historical = merged_at is not None
    scope = {'pr':identity['number'], 'head_sha':identity['head_sha'],
             'base_sha':identity['base_sha'], 'base_branch':identity['base_ref'],
             'event':'pull_request', 'source':source, 'policy_sha256':policy_sha,
             'retrigger':bool(retrigger), 'retrigger_at':retrigger.get('started_at') if retrigger else None,
             'workflows':entries, 'verified_success':verified, 'checked_at':rr.now(),
             'historical_merged_pr':historical, 'merged_at':merged_at,
             'live_pr_qualification':bool(verified and not historical),
             'release_qualification':False}
    key = 'historical_merged_pr_ci_evidence' if historical else 'pr_ci_qualifications'
    state.setdefault(key,{})[str(identity['number'])] = scope
    rr.save_state(session,state)
    report = rr.read_json(session/'report.json')
    report.setdefault(key,{})[str(identity['number'])] = scope
    # A merged PR may have passed the same CI before merge, but cannot be
    # retroactively classified as an *open* PR ready to merge.
    merged_numbers = set(state.get('historical_merged_pr_ci_evidence', {}))
    report['required_ci_qualified_for'] = sorted(int(k) for k,v in
                                                 state.get('pr_ci_qualifications',{}).items()
                                                 if k not in merged_numbers and
                                                 v.get('verified_success') and
                                                 not v.get('historical_merged_pr'))
    report['ci_execution'] = (
        'Verified historical PR runs at their recorded head/base; not live merge eligibility'
        if historical and verified else
        'Merged PR historical CI evidence incomplete; not live merge eligibility'
        if historical else
        'Verified explicitly scoped PR workflows at their restored PR HEAD and base'
        if verified else
        'PR hosted checks not yet qualified; see pr_ci_qualifications')
    report['fully_operationally_verified'] = False
    if verified and not historical:
        report['recovery_classification'] = 'RESTORED_SCOPED_PR_CI_QUALIFIED_HISTORICAL_LIMITATIONS_REMAIN'
    rr.write_json(session/'report.json',report)
    return scope


def qualify_pr(session, *, number, required=(), ack_retrigger=None, watch=False,
               timeout_seconds=1800, poll_seconds=15):
    """Read-only by default; explicitly approved, journaled PR close/reopen when requested."""
    rr = _core()
    rr.require(60 <= timeout_seconds <= 7200, 'CI watch timeout must be 60–7200 seconds')
    rr.require(5 <= poll_seconds <= 120, 'CI watch polling must be 5–120 seconds')
    with rr.locked(session):
        state = rr.load_state(session)
        _ensure_restored(session,state)
        rr.require(rr.api(f'repos/{state["repo"]}/actions/permissions').get('enabled') is True,
                   'Actions are disabled; finish safe activation first')
        pr = _current_pr(state['repo'], number)
        merged_at = pr.get('merged_at')
        if merged_at is not None:
            rr.require(ack_retrigger is None and not watch,
                       'merged PR supports read-only historical CI evidence only; '
                       'retrigger/watch is forbidden')
        else:
            rr.require(pr['state'] == 'open' or bool(ack_retrigger),
                       'qualification requires an open PR')
        identity = _identity(pr)
        names,origin,policy_sha = _required_workflows(state,pr,tuple(required))
        expected = _resolve_workflows(state['repo'],names,pr['base']['sha'])
        existing = state.get('pr_ci_retriggers',{}).get(str(number))
        if ack_retrigger is not None:
            before = _evaluate(state,pr,expected)
            rr.require(not all(x['verified_success'] for x in before) or existing,
                       'all required checks already passed; no need to close/reopen this PR')
            record = _retrigger(session,state,pr,expected,ack_retrigger)
            pr = _current_pr(state['repo'],number)
            rr.require(_same_pr_identity(pr,identity) and pr['state']=='open',
                       'PR changed after retrigger')
        else:
            record = existing if existing and existing.get('phase')=='reopened' else None
            if record:
                rr.require(record.get('identity') == identity,
                           'PR head/base changed since prior retrigger; refuse stale evidence')
        deadline=time.monotonic()+timeout_seconds
        while True:
            entries=_evaluate(state,pr,expected,
                              not_before=record['started_at'] if record else None)
            live_pr=_current_pr(state['repo'],number)
            rr.require(_same_pr_identity(live_pr,identity) and
                       live_pr.get('merged_at') == merged_at and
                       live_pr['state'] == ('closed' if merged_at is not None else 'open'),
                       'PR head/base/state drifted while qualifying CI')
            scope=_persist_result(session,state,identity,entries,origin,policy_sha,
                                  retrigger=record,merged_at=merged_at)
            for item in entries:
                rr.log(f'{item["workflow"]["name"]}: '
                       f'{item["status"]}/{item["conclusion"] or "pending"} '
                       f'run={item["run_id"]}',
                       style='green' if item['verified_success'] else 'yellow')
            if scope['verified_success']:
                if merged_at is not None:
                    rr.log(f'MERGED PR #{number}: HISTORICAL HOSTED CI EVIDENCE VERIFIED '
                           f'@ {identity["head_sha"]} (NOT live/release acceptance)',
                           style='bold green')
                else:
                    rr.log(f'PR #{number}: ALL SCOPED REQUIRED HOSTED CHECKS VERIFIED '
                           f'@ {identity["head_sha"]}',style='bold green')
                return scope
            if not watch or time.monotonic() >= deadline:
                return scope
            time.sleep(poll_seconds)
