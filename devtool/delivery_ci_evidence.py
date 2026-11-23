"""S4: exact-run, exact-attempt CI corroboration and bounded failure guidance.

This module performs only read-only GitHub Actions calls through Devtool's
existing GitHubActionsClient. The committed required-CI policy and the current
PR/head checks remain the *independent* qualification authority.

Never use a guessed run ID, a prior attempt's jobs, a release status, or a
workflow name alone as proof. Avoid copying remote job log text (may contain
credentials); provide exact provider job URLs and actionable step names.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping

from .github_actions import GitHubActionsError

_FINAL = frozenset({'success', 'failure', 'cancelled', 'timed_out', 'action_required',
                    'neutral', 'skipped', 'stale'})
_STOP = frozenset({'failure', 'cancelled', 'timed_out', 'action_required', 'stale'})
_MAX_JOB_ENTRIES = 200


def _reason(step: str, conclusion: str) -> tuple[str, str]:
    """Hints are deliberately non-authoritative; job logs remain source of truth."""
    text = step.casefold()
    if conclusion == 'timed_out':
        return 'timeout', "Inspect job duration and the failing step's hosted log"
    if conclusion == 'cancelled':
        return 'cancelled', 'Inspect who or what cancelled this workflow attempt'
    if any(w in text for w in ('permission', 'auth', 'token', 'credential')):
        return 'possible-authentication', 'Inspect GitHub token permissions and job log'
    if any(w in text for w in ('checkout', 'setup', 'install', 'download', 'cache', 'runner')):
        return 'possible-environment', 'Inspect runner provisioning and network log'
    if any(w in text for w in ('test', 'pytest', 'xunit', 'gradle', 'go test', 'unit')):
        return 'possible-test-failure', 'Inspect exact test failure in job log'
    return 'unspecified', 'Inspect exact failed step in the GitHub Actions job'


def failed_job_details(client: Any, slug: str, run: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Report exact-attempt job and step metadata, never download raw logs."""
    if run.get('status') != 'completed' or run.get('conclusion') == 'success':
        return []
    rid, attempt = run.get('id'), run.get('attempt')
    if type(rid) is not int or type(attempt) is not int or rid <= 0 or attempt <= 0:
        return [{'classification': 'invalid-run-identity', 'hint': 'Verify exact GitHub run ID and attempt'}]
    try:
        jobs = client.run_attempt_jobs(slug, rid, attempt)
    except (GitHubActionsError, OSError, ValueError):
        return [{'classification': 'job-details-unavailable', 'hint': 'Inspect the exact hosted workflow run'}]
    if not isinstance(jobs, list) or len(jobs) >= _MAX_JOB_ENTRIES:
        return [{'classification': 'job-inventory-unbounded', 'hint': 'Inspect full provider job inventory'}]
    details: list[dict[str, Any]] = []
    for job in jobs:
        if not isinstance(job, dict) or job.get('conclusion') not in _STOP:
            continue
        job_id = job.get('id')
        if type(job_id) is not int or job_id <= 0:
            continue
        failed_steps = []
        steps = job.get('steps')
        if not isinstance(steps, list):
            steps = []
        for step in steps[:120]:
            if not isinstance(step, dict) or step.get('conclusion') not in _STOP:
                continue
            name = str(step.get('name') or '')[:140]
            kind, hint = _reason(name, str(step['conclusion']))
            failed_steps.append({'name': name, 'conclusion': step['conclusion'],
                                 'classification': kind, 'hint': hint})
        kind, hint = (_reason(failed_steps[0]['name'], job['conclusion']) if failed_steps
                      else _reason('', job['conclusion']))
        details.append({
            'job_id': job_id, 'job': str(job.get('name') or '')[:160],
            'conclusion': job['conclusion'], 'classification': kind,
            'hint': hint, 'steps': failed_steps[:20],
            'url': f'https://github.com/{slug}/actions/runs/{rid}/attempts/{attempt}/job/{job_id}',
            'log_content_collected': False,
        })
    return details[:30]


def verify_exact_attempts(client: Any, slug: str, records: list[dict[str, Any]],
                          workflow_ids: Mapping[str, int], *,
                          validate_run: Callable[[Mapping[str, Any]], dict[str, Any] | None]
                          ) -> dict[str, Any]:
    """Corroborate policy-required runs through individual GETs + exact-attempt jobs.

    A read-only API outage/partial provider state is *pending*, never green.
    No failures in this function can authorize a dispatch or rerun.
    """
    by_name = {row.get('name'): row for row in records}
    evidence: list[dict[str, Any]] = []
    issues: list[dict[str, str]] = []
    for name, workflow_id in workflow_ids.items():
        row = by_name.get(name)
        if not isinstance(row, dict) or row.get('workflow_id') != workflow_id:
            issues.append({'workflow': name, 'reason': 'policy-workflow-mismatch'})
            continue
        rid, attempt = row.get('id'), row.get('attempt')
        if type(rid) is not int or type(attempt) is not int or rid <= 0 or attempt <= 0:
            issues.append({'workflow': name, 'reason': 'invalid-run-or-attempt'})
            continue
        try:
            # Exact run GET must match the run originally selected from the list.
            live = client.get_run(slug, rid)
        except (GitHubActionsError, OSError, ValueError):
            issues.append({'workflow': name, 'reason': 'exact-run-unavailable'})
            continue
        if not isinstance(live, dict):
            issues.append({'workflow': name, 'reason': 'invalid-exact-run'})
            continue
        matched = validate_run(live)
        if (not isinstance(matched, dict) or any(matched.get(key) != row.get(key)
                    for key in ('id', 'attempt', 'name', 'workflow_id', 'event',
                                'head_sha'))):
            issues.append({'workflow': name, 'reason': 'exact-run-identity-changed'})
            continue
        # A list snapshot can be queued while the exact GET is completed, or
        # vice versa. Qualify only the fresh GET state and its exact attempt.
        if matched.get('status') != 'completed' or matched.get('conclusion') != 'success':
            issues.append({'workflow': name, 'reason': 'run-not-successful'})
            continue
        try:
            jobs = client.run_attempt_jobs(slug, rid, attempt)
        except (GitHubActionsError, OSError, ValueError):
            issues.append({'workflow': name, 'reason': 'exact-attempt-jobs-unavailable'})
            continue
        if not isinstance(jobs, list) or not jobs or len(jobs) >= _MAX_JOB_ENTRIES:
            issues.append({'workflow': name, 'reason': 'incomplete-job-inventory'})
            continue
        successful = 0
        skipped = 0
        invalid = False
        for job in jobs:
            if not isinstance(job, dict) or type(job.get('id')) is not int or job['id'] <= 0:
                invalid = True
                break
            if job.get('run_id') is not None and job.get('run_id') != rid:
                invalid = True
                break
            if job.get('run_attempt') is not None and job.get('run_attempt') != attempt:
                invalid = True
                break
            if job.get('status') != 'completed' or job.get('conclusion') not in _FINAL:
                invalid = True
                break
            if job['conclusion'] == 'success':
                successful += 1
            elif job['conclusion'] in ('skipped', 'neutral'):
                skipped += 1
            else:
                invalid = True
                break
        if invalid or successful < 1:
            issues.append({'workflow': name, 'reason': 'job-results-not-fully-successful'})
            continue
        evidence.append({'name': name, 'run_id': rid, 'attempt': attempt,
                         'workflow_id': workflow_id, 'event': row['event'],
                         'source_sha': row['head_sha'], 'job_count': len(jobs),
                         'jobs_success': successful, 'jobs_skipped_or_neutral': skipped,
                         'source_bound': True,
                         'jobs_scope': 'exact-run-attempt',
                         'url': row.get('url'), 'release_qualified': False})
    return {'verified': not issues and len(evidence) == len(workflow_ids),
            'runs': evidence, 'issues': issues,
            'evidence_scope': 'per-run-GET-and-exact-attempt-jobs',
            'release_qualified': False}
