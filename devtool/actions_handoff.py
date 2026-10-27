"""ACTIONS-UX02: exact-receipt GitHub review and verified archive handoff.

All reads use ACTIONS-03/04.  A reviewed successful run is NOT a release seal.
The only permitted local mutation is an explicitly requested archive download,
which remains unextracted and unqualified in the canonical artifact registry.
"""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from .actions_artifacts import artifact_list, download_artifact, verify_download
from .actions_runs import _read_dispatch_receipt, run_id, snapshot
from .github_actions import GitHubActionsClient, GitHubActionsError, _slug

_SHA = re.compile(r'[0-9a-fA-F]{40}\Z')
_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z')


def _receipt(request_id: str, repository: str = '', *, receipts: Path | None = None):
    # This reader also rejects symlinks, overlarge data and forged receipt IDs.
    _, record = _read_dispatch_receipt(request_id, receipts)
    slug = _slug(record['repository'])
    if repository and _slug(repository).casefold() != slug.casefold():
        raise GitHubActionsError('Exact request is bound to another repository')
    expected = record.get('pre_dispatch_sha')
    if not isinstance(expected, str) or not _SHA.fullmatch(expected):
        raise GitHubActionsError('Exact request has no valid frozen source SHA')
    wid = record.get('workflow_id')
    if type(wid) is not int or wid <= 0:
        raise GitHubActionsError('Exact request has no valid workflow identity')
    if record.get('run_id') is None:
        return slug, record, None
    return slug, record, run_id(record['run_id'])


def review_run(client: GitHubActionsClient, request_id: str, *, repository: str = '',
               receipts: Path | None = None) -> dict[str, Any]:
    slug, record, identity = _receipt(request_id, repository, receipts=receipts)
    base = {'phase': 'ACTIONS-UX02', 'request_id': request_id, 'repository': slug,
            'source_sha': record['pre_dispatch_sha'].lower(), 'workflow_id': record['workflow_id'],
            'read_only': True, 'qualified_release': False, 'remote_verified': False,
            'artifact_verified': False, 'automatic_retry': False}
    if identity is None:
        return {**base, 'state': 'uncorrelated', 'run_id': None,
                'message': 'No exact remote run correlation; inspect manually and do not redispatch.'}
    # None prevents journal mutation during review. The receipt itself remains immutable.
    inspection = snapshot(client, slug, identity, None, record, with_jobs=True)
    remote = inspection['remote']
    # ACTIONS-03 treats missing workflow/head_branch as potentially matching;
    # the consolidated handoff demands explicit source and workflow identity.
    source_ok = (isinstance(remote.get('head_sha'), str)
                 and remote['head_sha'].lower() == record['pre_dispatch_sha'].lower())
    workflow_ok = remote.get('workflow_id') == record['workflow_id']
    event_ok = remote.get('event') == 'workflow_dispatch'
    branch = remote.get('head_branch')
    ref = record.get('ref')
    branch_ok = (isinstance(ref, str) and bool(ref)
                 and isinstance(branch, str) and branch == ref)
    attempt = remote.get('run_attempt')
    attempt_ok = type(attempt) is int and attempt > 0
    jobs_scope_ok = (inspection.get('jobs_scope') == 'exact-run-attempt'
                     and inspection.get('jobs_truncated_possible') is False)
    jobs = inspection.get('jobs', [])
    jobs_ok = (isinstance(jobs, list) and bool(jobs) and
               all(job.get('status') == 'completed' and
                   job.get('conclusion') in ('success', 'skipped', 'neutral')
                   for job in jobs if isinstance(job, dict)) and
               all(isinstance(job, dict) for job in jobs))
    valid = all((source_ok, workflow_ok, event_ok, branch_ok, attempt_ok, jobs_scope_ok, jobs_ok))
    succeeded = (remote['completed'] is True and remote['conclusion'] == 'success')
    state = ('source-mismatch' if not valid else
             'succeeded' if succeeded else
             'incomplete' if not remote['completed'] else 'failed')
    return {**base, 'state': state, 'run_id': identity,
            'run_attempt': attempt, 'remote_status': remote.get('status'),
            'remote_conclusion': remote.get('conclusion'),
            'remote_verified': bool(valid and succeeded),
            'identity_checks': {'source': source_ok, 'workflow': workflow_ok,
                                'event': event_ok, 'branch': branch_ok,
                                'attempt': attempt_ok, 'jobs_exact': jobs_scope_ok, 'jobs_terminal':jobs_ok},
            'jobs_count': len(inspection.get('jobs', ())),
            'message': ('Remote source and exact job attempt verified, but archive and release remain unqualified'
                        if valid and succeeded else 'Do not collect or promote evidence; review remote run state and identity')}


def collect_verified(client: GitHubActionsClient, request_id: str, artifact_name: str,
                     repo_root: Path, *, yes: bool, repository: str = '',
                     receipts: Path | None = None) -> dict[str, Any]:
    if not yes:
        raise GitHubActionsError('Explicit --yes is required for verified artifact collection')
    if not isinstance(artifact_name, str) or not _NAME.fullmatch(artifact_name):
        raise GitHubActionsError('Artifact selection requires an exact bounded name')
    before = review_run(client, request_id, repository=repository, receipts=receipts)
    if not before['remote_verified']:
        raise GitHubActionsError('Remote result lacks exact successful source/attempt binding; no download')
    listed = artifact_list(client, before['repository'], before['run_id'])
    remote = listed['remote_run']
    if (remote.get('head_sha','').lower() != before['source_sha'] or
        remote.get('run_attempt') != before['run_attempt'] or
        remote.get('workflow_id') != before['workflow_id'] or
        remote.get('conclusion') != 'success' or not remote.get('completed')):
        raise GitHubActionsError('Run changed while enumerating artifacts; no download')
    matches = [x for x in listed['artifacts'] if x['name'] == artifact_name]
    if len(matches) != 1:
        raise GitHubActionsError('Expected exactly one artifact with that exact name; no download')
    item = matches[0]
    if item.get('run_attempt') != before['run_attempt']:
        raise GitHubActionsError('Artifact attempt differs from reviewed run')
    try:
        result = verify_download(client, before['repository'], before['run_id'], item['id'], repo_root)
        reused = True
    except GitHubActionsError as exc:
        if 'no registered download' not in str(exc).lower():
            raise
        download_artifact(client, before['repository'], before['run_id'], item['id'], repo_root)
        result = verify_download(client, before['repository'], before['run_id'], item['id'], repo_root)
        reused = False
    if result.get('archive_verified') is not True:
        raise GitHubActionsError('Artifact registry did not confirm canonical archive verification')
    after = review_run(client, request_id, repository=repository, receipts=receipts)
    if (not after['remote_verified'] or after['run_id'] != before['run_id']
        or after['run_attempt'] != before['run_attempt'] or after['source_sha'] != before['source_sha']):
        raise GitHubActionsError('Run changed during artifact handoff; archive stays unqualified')
    return {'phase':'ACTIONS-UX02','state':'archive-verified-unqualified',
            'request_id':request_id,'repository':before['repository'],
            'run_id':before['run_id'],'run_attempt':before['run_attempt'],
            'source_sha':before['source_sha'],'artifact_id':item['id'],
            'artifact_name':artifact_name,'archive_verified':True,
            'existing_archive_reused':reused,'qualified_release':False,
            'signing_verified':False,'extracted':False,
            'note':'SHA-256 verified archive is not release qualification'}
