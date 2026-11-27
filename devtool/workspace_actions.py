"""Explicit read-only native Actions workspace navigation.

No network on render, selection, back or refresh. Every remote request is
bound to the selected native identity and uses the selected auth mode.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any
import re

from .github_actions import GitHubActionsClient, GitHubActionsError, _slug, resolve_token
from .actions_runs import _normalize_job, _normalize_run

_SHA = re.compile(r'[0-9a-f]{40}\Z')


def workspace_client(repository: str, mode: str = 'auto') -> GitHubActionsClient:
    repository = _slug(repository)
    if mode == 'app':
        from .github_app import make_app_client
        return make_app_client(repository, 'actions_read')
    token, _ = resolve_token(mode)
    if not token and mode != 'none':
        raise GitHubActionsError('Actions GET requires an explicitly configured credential')
    return GitHubActionsClient(token)


def _positive(value: Any) -> int:
    if type(value) is not int or value <= 0:
        raise GitHubActionsError('Missing exact positive Actions identity')
    return value


def _bound_run(raw: Mapping[str, Any], repository: str, workflow: int) -> dict[str, Any]:
    repo = raw.get('repository')
    if (not isinstance(repo, Mapping) or not isinstance(repo.get('full_name'), str)
            or repo['full_name'].casefold() != repository.casefold()
            or raw.get('workflow_id') != workflow
            or not isinstance(raw.get('head_sha'), str) or not _SHA.fullmatch(raw['head_sha'])):
        raise GitHubActionsError('Remote run repository, workflow or source identity mismatch')
    _positive(raw.get('id'))
    _positive(raw.get('run_attempt'))
    return _normalize_run(dict(raw), repository)


def _verified_run(client: GitHubActionsClient, repository: str, selected: Mapping[str, Any]) -> dict[str, Any]:
    rid = _positive(selected.get('id'))
    current = _bound_run(client.get_run(repository, rid), repository, _positive(selected.get('workflow_id')))
    for key in ('id', 'run_attempt', 'head_sha', 'workflow_id', 'event'):
        if current.get(key) != selected.get(key):
            raise GitHubActionsError('Selected run identity changed')
    return current


def _unique(rows: list[dict[str, Any]], field: str) -> None:
    keys = [_positive(row.get(field)) for row in rows]
    if len(set(keys)) != len(keys):
        raise GitHubActionsError('Duplicate native Actions identity')


def workflow_runs(client: GitHubActionsClient, repository: str, workflow: Mapping[str, Any]) -> dict[str, Any]:
    repository = _slug(repository)
    identity = _positive(workflow.get('id'))
    observed = client.workflow(repository, str(identity))
    if observed.get('id') != identity or observed.get('path') != workflow.get('path'):
        raise GitHubActionsError('Selected workflow identity changed')
    rows = [_bound_run(raw, repository, identity) for raw in client.workflow_runs(repository, identity)]
    _unique(rows, 'id')
    return {'kind': 'workflow-runs', 'repository': repository, 'workflow': dict(workflow),
            'items': rows, 'read_only': True, 'qualified_release': False}


def run_jobs(client: GitHubActionsClient, repository: str, run: Mapping[str, Any]) -> dict[str, Any]:
    repository = _slug(repository)
    current = _verified_run(client, repository, run)
    jobs = []
    for raw in client.run_attempt_jobs(repository, current['id'], current['run_attempt']):
        if raw.get('run_id') != current['id'] or raw.get('run_attempt') != current['run_attempt']:
            raise GitHubActionsError('Job is not a member of the selected run attempt')
        job = _normalize_job(raw)
        _unique(job['steps'], 'number')
        jobs.append(job)
    _unique(jobs, 'id')
    _verified_run(client, repository, current)
    return {'kind': 'run-jobs', 'repository': repository, 'run': current,
            'items': jobs, 'read_only': True, 'qualified_release': False}


def job_logs(client: GitHubActionsClient, repository: str, run: Mapping[str, Any],
             job: Mapping[str, Any]) -> dict[str, Any]:
    from .delivery_remote_logs import fetch_selected_job_logs
    lines = fetch_selected_job_logs(client, repository, run, job)
    return {'kind': 'job-logs', 'repository': repository, 'run': dict(run),
            'job': dict(job), 'log_lines': lines, 'read_only': True,
            'qualified_release': False}
