"""Presentation-only translation of authenticated GitHub run/job observations.

Never estimates Gradle percentage or qualifies an APK. The canonical child
process exit status remains the sole success/failure authority for the UI.
"""
from __future__ import annotations

import json
import re
from typing import Any, Mapping

EVENT_PREFIX = 'DEVTOOL_REMOTE_OBSERVATION:'
MAX_EVENT_LENGTH = 128 * 1024
_CONTROL = re.compile(r'[\x00-\x1f\x7f-\x9f]')


def _display_text(value: object, limit: int = 180) -> str:
    return _CONTROL.sub(' ', str(value or ''))[:limit]


def encode_observation(observation: Mapping[str, Any]) -> str:
    """Bounded one-line framing for a child->Textual host projection."""
    remote = observation.get('remote')
    if not isinstance(remote, Mapping):
        raise ValueError('GitHub observation missing run')
    jobs = observation.get('jobs', [])
    if not isinstance(jobs, list):
        jobs = []
    record = {
        'remote': {k: remote.get(k) for k in ('id', 'repository', 'status', 'conclusion',
                   'completed', 'run_attempt', 'head_sha', 'html_url')},
        'jobs': [{
            'name': _display_text(j.get('name')),
            'status': _display_text(j.get('status')), 'conclusion': _display_text(j.get('conclusion')),
            'steps': [{
                'name': _display_text(st.get('name')),
                'status': _display_text(st.get('status')), 'conclusion': _display_text(st.get('conclusion')),
            } for st in j.get('steps', [])[:100] if isinstance(st, dict)],
        } for j in jobs[:200] if isinstance(j, Mapping)],
        'source_binding': _display_text(observation.get('source_binding', 'unbound')),
    }
    encoded = json.dumps(record, ensure_ascii=True, separators=(',', ':'))
    if len(encoded) > MAX_EVENT_LENGTH:
        # Some workflows report hundreds of steps. Preserve the authenticated
        # run state while reducing UI-only job detail, never truncate JSON.
        record['jobs'] = [{**j, 'steps': j['steps'][:8]} for j in record['jobs'][:20]]
        encoded = json.dumps(record, ensure_ascii=True, separators=(',', ':'))
    if len(encoded) > MAX_EVENT_LENGTH:
        record['jobs'] = []
        encoded = json.dumps(record, ensure_ascii=True, separators=(',', ':'))
    return EVENT_PREFIX + encoded


def decode_observation(line: str) -> dict[str, Any] | None:
    if not line.startswith(EVENT_PREFIX):
        return None
    raw = line[len(EVENT_PREFIX):]
    if len(raw) > MAX_EVENT_LENGTH:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get('remote'), dict):
        return None
    if not isinstance(data.get('jobs'), list):
        return None
    return data


def project_observation(data: Mapping[str, Any]) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Expose real API job/step state without inventing execution progress."""
    run = data.get('remote', {})
    if not isinstance(run, Mapping):
        raise ValueError('Missing remote run')
    status = _display_text(run.get('status') or 'unknown')
    conclusion = _display_text(run.get('conclusion') or '')
    jobs = [j for j in data.get('jobs', []) if isinstance(j, Mapping)]
    completed = sum(str(j.get('status')) == 'completed' for j in jobs)
    active = next((j for j in jobs if j.get('status') == 'in_progress'), None)
    failed = next((j for j in jobs if j.get('conclusion') in ('failure', 'timed_out', 'cancelled')), None)
    focus = failed or active
    detail = f'{completed}/{len(jobs)} jobs finished' if jobs else f'Workflow {status}'
    lines: list[str] = []
    if focus is not None:
        steps = focus.get('steps') if isinstance(focus.get('steps'), list) else []
        chosen = next((s for s in steps if isinstance(s, Mapping)
                       and s.get('conclusion') in ('failure', 'timed_out', 'cancelled')), None)
        if chosen is None:
            chosen = next((s for s in steps if isinstance(s, Mapping)
                           and s.get('status') == 'in_progress'), None)
        job_name = _display_text(focus.get('name') or 'Job')
        detail += f' · {job_name}'
        if chosen is not None:
            detail += ' / ' + _display_text(chosen.get('name') or 'Step')
    if failed is not None:
        bad_steps = failed.get('steps') if isinstance(failed.get('steps'), list) else []
        failing_step = next((step for step in bad_steps if isinstance(step, Mapping)
                             and step.get('conclusion') in ('failure', 'timed_out', 'cancelled')), None)
        failure_location = _display_text(failed.get('name') or 'Job')
        if failing_step:
            failure_location += ' / ' + _display_text(failing_step.get('name') or 'Step')
        # Prominent, factual failure line even when raw GitHub log output is
        # unavailable. Never fabricate a Gradle exception from step metadata.
        lines.append(f'FAILED GitHub step: {failure_location}')
    for j in jobs:
        job_name = _display_text(j.get('name') or 'Job')
        jstate = _display_text(j.get('conclusion') or j.get('status') or 'unknown')
        lines.append(f'Job {job_name}: {jstate}')
        for step in j.get('steps', []) if isinstance(j.get('steps'), list) else []:
            if isinstance(step, Mapping):
                lines.append(f'  {_display_text(step.get("name") or "Step")}: '
                             f'{_display_text(step.get("conclusion") or step.get("status") or "unknown")}')
    # These are observed GitHub job/step statuses, never inferred CLI phases.
    stages: list[dict[str, str]] = []
    visible_jobs = jobs[:4]
    if failed is not None and failed not in visible_jobs:
        # GitHub may put a failed job after many successful matrix jobs.
        # Keep its actual status and step on screen at the end of the run.
        visible_jobs = jobs[:3] + [failed]
    for job in visible_jobs:
        job_status = _display_text(job.get('conclusion') or job.get('status') or 'unknown', 32)
        stages.append({'name': _display_text(job.get('name') or 'Job', 90), 'status': job_status})
        if job.get('status') == 'in_progress' or job.get('conclusion') in ('failure', 'cancelled', 'timed_out'):
            steps = job.get('steps') if isinstance(job.get('steps'), list) else []
            relevant = next((step for step in steps if isinstance(step, Mapping)
                and (step.get('conclusion') in ('failure', 'cancelled', 'timed_out')
                     or step.get('status') == 'in_progress')), None)
            if relevant is not None:
                stages.append({'name': '↳ ' + _display_text(relevant.get('name') or 'Step', 88),
                               'status': _display_text(relevant.get('conclusion') or relevant.get('status') or 'unknown', 32)})
    if status == 'completed':
        detail = f'GitHub workflow: {conclusion or "unknown"} · {detail}'
    if data.get('source_binding') == 'not-confirmed':
        detail = 'WARNING: source SHA mismatch · ' + detail
    return ({
        'command': 'GitHub Actions',
        'phase_name': f'Run #{run.get("id") or "?"}',
        'target': str(run.get('repository') or ''),
        'detail': detail,
        # Do not mark the UI finished until the command has verified the result.
        'completion_status': 'running',
        'percent': None,
        'stages': stages[:5],
    }, tuple(lines))
