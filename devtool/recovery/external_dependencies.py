"""Explicit external credential dependency and hosted federation evidence.

The known MMRL consumer profile is a fail-closed exception to the general
impossibility of discovering inbound dependencies from a target repository.
No repository or workflow is mutated by this module.  Receipt acceptance is
independent of the PAT supplied to the local CLI.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import tempfile
import urllib.error
import zipfile

TARGET = 'Mikeyphw/MMRL'
HOST = 'Mikeyphw/MMRL-Shared'
OTHER = 'Mikeyphw/MMRL-LSPosed'
SECRET = 'MMRL_FEDERATION_TOKEN'
WORKFLOW = '.github/workflows/n05f-propagate.yml'
WORKFLOW_NAME = 'N05F Shared Kotlin Consumer PR Propagation'
# Reviewed live workflow blob. A source contract change requires an explicit review.
WORKFLOW_BLOB = 'b295fe0a6bf8568ade67c4f3af863a2954408174'
SHA40 = re.compile(r'[0-9a-f]{40}\Z')
SHA256 = re.compile(r'[0-9a-f]{64}\Z')
MAX_RECEIPT = 1024 * 1024


def _rr():
    from . import engine
    return engine


def applies(repo):
    return isinstance(repo, str) and repo.casefold() == TARGET.casefold()


def profile(repo):
    if not applies(repo):
        return []
    return [{'name': SECRET, 'kind': 'fine_grained_pat', 'secret_hosts': [HOST],
             'repositories': [TARGET, OTHER], 'workflow_path': WORKFLOW,
             'live_federation_required': True}]


def validate_policy(policy):
    if applies(policy['repository']):
        return _item(policy)
    return None


def _item(policy):
    rr = _rr()
    rows = [x for x in policy['credentials'] if x['name'] == SECRET]
    rr.require(len(rows) == 1, 'MMRL_EXTERNAL_POLICY_REQUIRED: declare MMRL_FEDERATION_TOKEN')
    item = rows[0]
    rr.require(item['kind'] == 'fine_grained_pat' and
               set(item['repositories']) >= {TARGET, OTHER} and
               set(item.get('secret_hosts', [])) == {HOST} and bool(item.get('probe')),
               'MMRL_EXTERNAL_POLICY_SCOPE_MISMATCH: actual secret host is MMRL-Shared; include both consumers and protected PAT probe')
    return item


def _repo_id(repo):
    rr = _rr()
    row = rr.repo_info(repo)
    rr.require(isinstance(row, dict) and row.get('full_name') == repo and
               type(row.get('id')) is int and row['id'] > 0,
               'EXTERNAL_REPOSITORY_IDENTITY_UNVERIFIED: ' + repo)
    return row['id']


def _branch_sha(repo, branch):
    rr = _rr()
    value = rr.api(f'repos/{repo}/git/ref/heads/{branch}')
    sha = (value.get('object') or {}).get('sha', '')
    rr.require(value.get('ref') == 'refs/heads/' + branch and
               isinstance(sha, str) and SHA40.fullmatch(sha),
               'EXTERNAL_SOURCE_REF_UNVERIFIED')
    return sha


def _source_at(sha):
    rr = _rr()
    obj = rr.api(f'repos/{HOST}/contents/{WORKFLOW}?ref={sha}')
    rr.require(isinstance(obj, dict) and obj.get('type') == 'file' and
               obj.get('path') == WORKFLOW and obj.get('encoding') == 'base64',
               'MMRL_FEDERATION_WORKFLOW_SOURCE_UNVERIFIED')
    source = base64.b64decode(''.join(obj['content'].split()), validate=True)
    rr.require(len(source) <= MAX_RECEIPT, 'MMRL_FEDERATION_WORKFLOW_OVERSIZE')
    blob = hashlib.sha1(b'blob ' + str(len(source)).encode() + b'\0' + source).hexdigest()
    rr.require(obj.get('sha') == blob == WORKFLOW_BLOB,
               'MMRL_FEDERATION_WORKFLOW_BLOB_CHANGED: review the actual workflow contract')
    # Match the known consuming workflow, not arbitrary references in other repositories.
    # Hosted qualification additionally requires immutable run, job and artifact evidence.
    rr.require(re.search(rb'(?m)^\s*GH_TOKEN:\s*\$\{\{\s*secrets\.MMRL_FEDERATION_TOKEN\s*\}\}\s*$', source) and
               b'scripts/n05f_propagate.py apply' in source,
               'MMRL_FEDERATION_CONTRACT_CHANGED: review and update explicit dependency policy')
    return hashlib.sha256(source).hexdigest()


def _secret_metadata():
    rr = _rr()
    row = rr.api(f'repos/{HOST}/actions/secrets/{SECRET}')
    rr.require(isinstance(row, dict) and row.get('name') == SECRET and
               isinstance(row.get('updated_at'), str),
               'MMRL_FEDERATION_SECRET_HOST_UNVERIFIED')
    _utc(row['updated_at'])
    return row['updated_at']


def _utc(value):
    stamp = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('timezone required')
    return stamp.astimezone(dt.timezone.utc)


def observe(policy, *, expected_target_id=None, prior=None, probe_token=True):
    """Verify the known host, consumers and the actual protected token access.

    `permissions.push` is advisory; it cannot qualify deployed-workflow writes.
    Do not claim unknown inbound dependencies are automatically discoverable.
    """
    rr = _rr()
    from . import credentials
    item = validate_policy(policy)
    declared = {policy['repository']}
    for dependency in policy['credentials']:
        declared.update(dependency['repositories'])
        declared.update(dependency.get('secret_hosts', []))
    ids_declared = {repo: _repo_id(repo) for repo in sorted(declared)}
    if expected_target_id is not None:
        rr.require(ids_declared[policy['repository']] == expected_target_id,
                   'DECLARED_TARGET_REPOSITORY_ID_DRIFT')
    if prior is not None:
        previous = prior.get('declared_repository_ids')
        rr.require(isinstance(previous, dict) and set(previous) == set(ids_declared) and
                   all(previous[repo] == loc_id for repo, loc_id in ids_declared.items()
                       if repo != policy['repository']),
                   'DECLARED_EXTERNAL_REPOSITORY_ID_DRIFT')
    hosts = []
    for dependency in policy['credentials']:
        for host in dependency.get('secret_hosts', []):
            meta = rr.api(f'repos/{host}/actions/secrets/{dependency["name"]}', missing_ok=True)
            rr.require(meta is None or (isinstance(meta,dict) and meta.get('name') == dependency['name'] and
                       isinstance(meta.get('updated_at'), str)), 'DECLARED_SECRET_METADATA_UNVERIFIED')
            if meta is not None:
                _utc(meta['updated_at'])
            hosts.append({'repository':host, 'repository_id':ids_declared[host],
                          'name':dependency['name'], 'updated_at':meta.get('updated_at') if meta else None,
                          'status':'present-unverified' if meta else 'not-observed'})
    common = {'declared_repository_ids':ids_declared, 'declared_secret_hosts':hosts,
              'inventory_scope':'declared-only; known MMRL inbound dependency profile additionally enforced'}
    if not applies(policy['repository']):
        rr.require({repo: _repo_id(repo) for repo in ids_declared} == ids_declared,
                   'DECLARED_EXTERNAL_OBSERVATION_DRIFT')
        return {'profile': 'none-known', **common}
    ids = {repo: ids_declared[repo] for repo in (HOST, TARGET, OTHER)}
    if expected_target_id is not None:
        rr.require(ids[TARGET] == expected_target_id, 'MMRL_TARGET_REPOSITORY_ID_DRIFT')
    if prior:
        for repo in (HOST, OTHER):
            rr.require(prior['repository_ids'][repo] == ids[repo],
                       'MMRL_EXTERNAL_REPOSITORY_ID_DRIFT: ' + repo)
    sha = _branch_sha(HOST, 'main')
    digest = _source_at(sha)
    updated = _secret_metadata()
    if probe_token:
        rr.require(credentials.os.environ.get(item['token_env']),
                   'MMRL_EXTERNAL_PAT_SOURCE_UNAVAILABLE')
        actor=credentials._token_api('user',item['token_env'])
        rr.require(isinstance(actor,dict) and bool(actor.get('login')),
                   'MMRL_EXTERNAL_PAT_AUTHENTICATION_UNVERIFIED')
        for repo in item['repositories']:
            row = credentials._token_api('repos/' + repo, item['token_env'])
            rr.require(row.get('full_name') == repo and row.get('id') == _repo_id(repo) and
                       (row.get('permissions') or {}).get('push') is True,
                       'MMRL_EXTERNAL_PAT_ID_OR_CONTENTS_WRITE_HINT_UNVERIFIED: ' + repo)
            data = credentials._token_api('repos/' + repo + '/' +
                                          credentials.AUTHENTICATED_PROBES[item['probe']], item['token_env'])
            rr.require(isinstance(data, dict), 'MMRL_EXTERNAL_PAT_PROTECTED_PROBE_UNVERIFIED')
    rr.require(_branch_sha(HOST, 'main') == sha and
               {repo: _repo_id(repo) for repo in ids} == ids and
               _secret_metadata() == updated, 'MMRL_EXTERNAL_OBSERVATION_DRIFT')
    return {'profile': 'mmrl-federation-v1', **common, 'repository_ids': ids,
            'host_source_sha': sha, 'workflow_sha256': digest,
            'secret_updated_at': updated,
            'protected_pat_probe': bool(probe_token),
            'deployed_workflow_qualified': False}


def _receipt(payload, observation, run):
    rr = _rr()
    rr.require(isinstance(payload, dict) and payload.get('schema') == 'mmrl.n05f.propagation.v2' and
               payload.get('mode') == 'apply' and payload.get('source') == HOST and
               payload.get('source_sha') == observation['host_source_sha'] and
               payload.get('auto_merge') is False and payload.get('releases') is False,
               'MMRL_FEDERATION_RECEIPT_SOURCE_UNVERIFIED')
    rows = payload.get('consumers')
    rr.require(isinstance(rows, list) and len(rows) == 2 and
               {r.get('repository') for r in rows} == {TARGET, OTHER},
               'MMRL_FEDERATION_CONSUMERS_INCOMPLETE')
    evidence = []
    for row in rows:
        name = row['repository']
        rr.require(row.get('repository_id') == observation['repository_ids'][name] and
                   row.get('target_sha') == observation['host_source_sha'] and
                   row.get('base') == ('next' if name == TARGET else 'main'),
                   'MMRL_FEDERATION_CONSUMER_ID_OR_SOURCE_MISMATCH')
        # A no-op can establish read access, but is not proof of actual writes.
        # Require the hosted workflow to have exercised the intended write path
        # for both consumers.  We do not manufacture a commit or trigger CI.
        rr.require(row.get('result') in ('created-pr', 'updated-pr') and
                   type(row.get('pr_number')) is int and row['pr_number'] > 0 and
                   isinstance(row.get('new_head'), str) and SHA40.fullmatch(row['new_head']),
                   'MMRL_FEDERATION_ACTUAL_WRITE_NOT_PROVED: ' + name)
        pr = rr.api(f'repos/{name}/pulls/{row["pr_number"]}')
        rr.require(pr.get('number') == row['pr_number'] and
                   (pr.get('head') or {}).get('sha') == row['new_head'] and
                   (pr.get('head') or {}).get('ref') == row.get('branch') and
                   ((pr.get('head') or {}).get('repo') or {}).get('id') == row['repository_id'] and
                   ((pr.get('base') or {}).get('repo') or {}).get('id') == row['repository_id'] and
                   (pr.get('base') or {}).get('ref') == row['base'],
                   'MMRL_FEDERATION_LIVE_CONSUMER_PR_MISMATCH')
        evidence.append({'repository': name, 'id': row['repository_id'],
                         'pr_number': row['pr_number'], 'head_sha': row['new_head']})
    return evidence


def qualify(session: Path, state: dict, policy: dict):
    """Read-only live hosted federation qualification, or explicit pending result.

    Accept only a current source, successful exact-attempt workflow after both
    recreation and the current secret metadata update.  Download and hash-check
    the bounded receipt from the exact GitHub run (not a user-supplied JSON).
    Only independent observed evidence is returned; no dispatch or mutation.
    """
    rr = _rr()
    if not applies(state['repo']):
        return {'status': 'not-applicable', 'qualified': False}
    result = {'status': 'FEDERATION_NOT_VERIFIED', 'qualified': False,
              'repository': state['repo'], 'replacement_id': state.get('created_new_id'),
              'workflow_path': WORKFLOW, 'read_only': True}
    try:
        rr.require(type(state.get('created_new_id')) is int, 'MMRL_REPLACEMENT_ID_MISSING')
        prior = state.get('external_credential_inventory')
        observed = observe(policy, expected_target_id=state['created_new_id'], prior=prior,
                           probe_token=True)
        current = rr.get_new_repo(state)
        cutoff = max(_utc(current['created_at']), _utc(observed['secret_updated_at']))
        workflows = rr.collect(f'repos/{HOST}/actions/workflows', key='workflows')
        matches = [w for w in workflows if w.get('path') == WORKFLOW and
                   w.get('name') == WORKFLOW_NAME and w.get('state') == 'active']
        rr.require(len(matches) == 1 and type(matches[0].get('id')) is int,
                   'MMRL_FEDERATION_WORKFLOW_REGISTRATION_UNVERIFIED')
        wid = matches[0]['id']
        # No broad search and no fallback to an older successful run after a
        # newer failed attempt.  Exact current SHA and event are mandatory.
        listing = rr.api(f'repos/{HOST}/actions/workflows/{wid}/runs?branch=main&event=workflow_run&per_page=100')
        rr.require(isinstance(listing, dict) and isinstance(listing.get('workflow_runs'), list),
                   'MMRL_FEDERATION_RUN_INVENTORY_UNVERIFIED')
        candidates = [r for r in listing['workflow_runs'] if
                      r.get('head_sha') == observed['host_source_sha'] and
                      _utc(r['created_at']) > cutoff]
        rr.require(bool(candidates), 'MMRL_FEDERATION_FRESH_HOSTED_RUN_REQUIRED')
        candidates.sort(key=lambda r: (_utc(r['created_at']), r['id']), reverse=True)
        rid = candidates[0]['id']
        rr.require(type(rid) is int and rid > 0, 'MMRL_FEDERATION_RUN_ID_INVALID')
        run_path = f'repos/{HOST}/actions/runs/{rid}'
        run = rr.api(run_path)
        attempt = run.get('run_attempt')
        rr.require(run.get('id') == rid and run.get('workflow_id') == wid and
                   type(attempt) is int and attempt > 0 and
                   run.get('event') == 'workflow_run' and run.get('head_branch') == 'main' and
                   run.get('head_sha') == observed['host_source_sha'] and
                   (run.get('repository') or {}).get('id') == observed['repository_ids'][HOST] and
                   (run.get('head_repository') or {}).get('id') == observed['repository_ids'][HOST] and
                   run.get('status') == 'completed' and run.get('conclusion') == 'success' and
                   _utc(run['created_at']) > cutoff,
                   'MMRL_FEDERATION_EXACT_RUN_NOT_QUALIFIED')
        jobs = rr.collect(run_path + f'/attempts/{attempt}/jobs', key='jobs')
        rr.require(len(jobs) == 1 and jobs[0].get('name') == 'propagate' and
                   jobs[0].get('status') == 'completed' and jobs[0].get('conclusion') == 'success',
                   'MMRL_FEDERATION_EXACT_JOB_NOT_QUALIFIED')
        steps = jobs[0].get('steps') or []
        for step in ('Verify exact validated source and plan both consumers',
                     'Open or update source-pinned consumer PRs (never merge)',
                     'Publish source-bound propagation receipt'):
            rr.require(sum(x.get('name') == step and x.get('conclusion') == 'success' for x in steps) == 1,
                       'MMRL_FEDERATION_REQUIRED_STEP_UNVERIFIED')
        artifacts = rr.collect(run_path + '/artifacts', key='artifacts')
        expected_name = f'mmrl-n05f-propagation-{rid}-attempt{attempt}'
        items = [a for a in artifacts if a.get('name') == expected_name]
        rr.require(len(items) == 1, 'MMRL_FEDERATION_EXACT_ARTIFACT_MISSING')
        artifact = items[0]
        aid, size, digest = artifact.get('id'), artifact.get('size_in_bytes'), artifact.get('digest', '')
        rr.require(type(aid) is int and aid > 0 and type(size) is int and 0 < size <= MAX_RECEIPT and
                   artifact.get('expired') is False and isinstance(digest, str) and
                   digest.startswith('sha256:') and SHA256.fullmatch(digest[7:]),
                   'MMRL_FEDERATION_ARTIFACT_METADATA_UNVERIFIED')
        token = rr.run(['gh','auth','token','--hostname','github.com']).stdout.strip()
        rr.require(bool(token), 'MMRL_FEDERATION_ARTIFACT_READ_AUTH_UNAVAILABLE')
        with tempfile.TemporaryDirectory(prefix='.federation-proof-', dir=session) as td:
            archive = Path(td) / 'receipt.zip'
            rr.download_api_binary(f'repos/{HOST}/actions/artifacts/{aid}/zip', archive,
                                   token=token, max_bytes=MAX_RECEIPT, expected_size=size)
            rr.require(rr.sha256(archive) == digest[7:], 'MMRL_FEDERATION_ARTIFACT_DIGEST_MISMATCH')
            with zipfile.ZipFile(archive) as z:
                members = z.infolist()
                rr.require(len(members) == 1 and members[0].filename == 'n05f-propagation.json' and
                           0 < members[0].file_size <= MAX_RECEIPT and
                           (members[0].external_attr >> 16) & 0o170000 != 0o120000,
                           'MMRL_FEDERATION_RECEIPT_ZIP_UNSAFE')
                payload = json.loads(z.read(members[0]))
            consumers = _receipt(payload, observed, run)
        # Re-query after the receipt and PR reads; never reuse a stale qualification.
        rr.require(rr.api(run_path) == run and
                   observe(policy, expected_target_id=state['created_new_id'], prior=prior,
                           probe_token=True) == observed,
                   'MMRL_FEDERATION_LIVE_EVIDENCE_DRIFT')
        result.update(status='verified', qualified=True, host_source_sha=observed['host_source_sha'],
                      run_id=rid, attempt=attempt, artifact_id=aid, artifact_sha256=digest[7:],
                      consumers=consumers, secret_updated_at=observed['secret_updated_at'])
    except (rr.Refused, KeyError, TypeError, ValueError, OSError, AttributeError,
            urllib.error.URLError, zipfile.BadZipFile) as exc:
        # Do not emit untrusted provider response, token, or artifact contents.
        reason = str(exc)
        result['reason'] = reason if isinstance(exc, rr.Refused) and re.fullmatch(
            r'MMRL_[A-Z0-9_]+(?:: [A-Za-z0-9_./ -]+)?', reason) else 'MMRL_FEDERATION_EVIDENCE_INCOMPLETE'
    return result
