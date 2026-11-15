"""Original S5: bounded, source-bound CI diagnostics over Devtool's Actions data.

Read-only. Never use a diagnosis to qualify CI/release, dispatch or rerun a job.
Remote logs are optional and are never stored raw. Provider redirects, credentials,
command output, and signed URLs must never be recorded in receipts.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request

_MAX_MATRIX = 25
_MAX_FAILURES = 25
_MAX_JOBS = 8
_MAX_BYTES = 256 * 1024
_MAX_SNIPPETS = 6
_MAX_TEXT = 180
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_MARKER = re.compile(r"(?i)(?:^|\s)(?:error:|fatal:|failed\b|failure\b|assertionerror\b|exception\b|timed?\s*out\b|##\[error\]|::error\b)")
_SECRET = re.compile(r"(?i)(?:authorization|bearer|password|secret|credential|api[_-]?key|token|private[_-]?key|sig=|signature=|\?[^\s]*=|-----BEGIN|::add-mask::)")
_HEX = re.compile(r"(?i)\b(?:gh[pousr]_[A-Za-z0-9_]{10,}|github_pat_[A-Za-z0-9_]{10,}|[a-f0-9]{32,})\b")
_URL = re.compile(r"(?i)https?://[^\s<>\"']+")


# These labels classify observed evidence; they are never provider verdicts.
_STATES = {
    'REGISTRATION_TIMED_OUT': ('registration-timeout','One or more required runs failed to register before the bounded deadline'),
    'WAITING_REGISTRATION': ('registration-pending','Required GitHub runs are not visible yet'),
    'REGISTRATION_PROVIDER_UNAVAILABLE': ('provider-unavailable','GitHub registration discovery could not be verified'),
    'CHECKS_RUNNING': ('running','Some required workflow attempts have not completed'),
    'CHECKS_EVIDENCE_PENDING': ('evidence-pending','Exact run/attempt/job corroboration is incomplete'),
    'BLOCKED_AMBIGUOUS': ('ambiguous-attribution','More than one source-eligible run or mismatched workflow identity exists'),
    'DISPATCH_UNCORRELATED': ('uncorrelated-dispatch','Dispatch is not linked to an independently trusted run ID'),
    'BLOCKED_CONDITIONAL': ('unproven-conditional-trigger','Conditional workflow applicability is unproven'),
    'BLOCKED_TRIGGER': ('workflow-configuration','Required workflow excludes the requested event or branch'),
    'BRANCH_RULES_UNVERIFIED': ('branch-rule-unavailable','Provider branch protection/ruleset verification is unavailable'),
    'CHECKS_FAILED': ('required-check-failed','At least one applicable required check completed unsuccessfully'),
    'REQUIRED_CHECKS_PASSED': ('required-ci-passed','All required CI gates were independently verified'),
    'OBSERVED_ONLY': ('observed-only','No authoritative required workflow set was selected'),
}
_FAILURE = {'failure', 'cancelled', 'timed_out', 'action_required', 'stale', 'skipped', 'neutral'}


def _hint_step(name: str, conclusion: str) -> str:
    """Advisory classification; job/step evidence is the authority."""
    text = name.lower()
    if conclusion == 'timed_out':
        return 'infrastructure-timeout'
    if conclusion == 'cancelled':
        return 'cancelled'
    if 'permission' in text or 'token' in text or 'auth' in text:
        return 'authorization'
    if any(x in text for x in ('checkout', 'setup', 'download', 'install', 'runner', 'cache')):
        return 'infrastructure-or-dependency'
    if any(x in text for x in ('pytest', 'xunit', 'test', 'lint', 'compile', 'build', 'go test')):
        return 'source-or-test-failure'
    if any(x in text for x in ('yaml', 'workflow', 'matrix', 'configuration')):
        return 'workflow-configuration'
    return 'unclassified-failure'


def safe_excerpt(raw: bytes | str) -> list[str]:
    """Allowlist signal lines, redact with canonical Devtool logstore, then restrict again.

    Fail closed if that redactor is unavailable. Hard-cap size and line count;
    URLs/signed query strings, credential assignments and high-entropy tokens
    must never be echoed even if GitHub masking was misconfigured.
    """
    try:
        from .logstore import redact_sensitive_text
    except ImportError:
        return []
    if not isinstance(raw, (bytes, str)):
        return []
    data = raw[:_MAX_BYTES]
    if isinstance(data, bytes):
        data = data.decode('utf-8', 'replace')
    lines = []
    for candidate in data.splitlines():
        if len(lines) >= _MAX_SNIPPETS:
            break
        line = candidate.strip()
        if not _MARKER.search(line) or len(line) > 512 or _SECRET.search(line):
            continue
        line = _URL.sub('[URL REDACTED]', line)
        line = _HEX.sub('[IDENTIFIER REDACTED]', line)
        line = redact_sensitive_text(line)
        if not isinstance(line, str) or _SECRET.search(line) or len(line) > _MAX_TEXT:
            continue
        # GitHub timestamps and workflow command escape markers contain no proof.
        if line and line not in lines:
            lines.append(line)
    return lines


def _signed_host(url: str) -> bool:
    try:
        parts = urlsplit(url)
        host = (parts.hostname or '').lower()
        port = parts.port
    except ValueError:
        return False
    return (parts.scheme == 'https' and not parts.username and not parts.password
            and port in (None, 443) and not parts.fragment and bool(host)
            and (host.endswith('.blob.core.windows.net') or host.endswith('.actions.githubusercontent.com')
                 or host.endswith('.githubusercontent.com')))


def _safe_label(value: Any, limit: int) -> str:
    value = ' '.join(str(value or '').split())[:512]
    if _SECRET.search(value) or '\x00' in value:
        return '[REDACTED PROVIDER LABEL]'
    return _HEX.sub('[IDENTIFIER REDACTED]', _URL.sub('[URL REDACTED]', value))[:limit]


def sanitize_failed_metadata(items: list[dict[str, Any]], *, detailed: bool) -> list[dict[str, Any]]:
    """Scrub provider-supplied job/step labels before writing the session journal.

    The older S4 fields remain for API compatibility, but their raw provider
    text must not be persisted alongside the new redacted S5 diagnosis.
    """
    clean=[]
    for item in items[:30]:
        if not isinstance(item,dict):
            continue
        value=dict(item)
        value['job']=_safe_label(value.get('job'),160)
        if 'error' in value:
            value['error']='provider-job-inventory-unavailable'
        raw_steps=value.get('steps')
        if detailed:
            value['steps']=[{**step,'name':_safe_label(step.get('name'),140),
                             'hint':_safe_label(step.get('hint'),180)}
                            for step in raw_steps[:20] if isinstance(step,dict)] if isinstance(raw_steps,list) else []
        else:
            value['steps']=[_safe_label(step,140) for step in raw_steps[:12]] if isinstance(raw_steps,list) else []
        clean.append(value)
    return clean


def fetch_bounded_job_log(client: Any, slug: str, job_id: int) -> bytes | None:
    """Fetch a small *read-only* GitHub job log, never record its signed URL.

    Only a GitHub API 302 followed by one HTTPS, origin-allowlisted redirect is
    accepted. Authorization is sent to api.github.com only, not the signed URL.
    No network fetch occurs if client has no compatible safe opener.
    """
    if type(job_id) is not int or job_id <= 0 or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', slug):
        return None
    opener = getattr(client, '_opener', None)
    if opener is None or not callable(getattr(opener, 'open', None)):
        return None
    from .github_actions import API_ORIGIN, API_VERSION
    headers = {'Accept':'application/vnd.github+json', 'User-Agent':'devtool-delivery-diagnostics/1',
               'X-GitHub-Api-Version': API_VERSION}
    token = getattr(client, '_token', None)
    if token:
        headers['Authorization'] = 'Bearer ' + token
    try:
        request = Request(f'{API_ORIGIN}/repos/{slug}/actions/jobs/{job_id}/logs',
                          headers=headers, method='GET')
        try:
            with opener.open(request, timeout=min(float(client.timeout), 15.0)):
                return None  # unexpected 200; only bounded redirect is supported
        except HTTPError as exc:
            if exc.code != 302:
                return None
            signed = exc.headers.get('Location', '')
        if not isinstance(signed, str) or not _signed_host(signed):
            return None
        # The canonical no-redirect opener must also be used for the signed URL.
        with opener.open(Request(signed, headers={'User-Agent':'devtool-delivery-diagnostics/1'},
                                 method='GET'), timeout=min(float(client.timeout), 15.0)) as response:
            if int(response.status) not in (200, 206):
                return None
            size = response.read(_MAX_BYTES + 1)
            if len(size) > _MAX_BYTES:
                return None
            return size
    except (HTTPError, URLError, OSError, TimeoutError, ValueError, TypeError):
        return None


def _bounded_identity(row: Mapping[str, Any], source: str) -> dict[str, Any]:
    identity = row.get('id')
    attempt = row.get('attempt')
    url = row.get('url')
    if type(identity) is not int or identity <= 0 or type(attempt) is not int or attempt <= 0:
        raise ValueError('CI diagnostic run identity is missing')
    if not isinstance(url, str) or not re.fullmatch(r'https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/actions/runs/[1-9][0-9]*', url):
        raise ValueError('CI diagnostic run URL is untrusted')
    return {'name': _safe_label(row.get('name'),160), 'id': identity, 'attempt':attempt,
            'workflow_id':row.get('workflow_id'), 'event': str(row.get('event') or '')[:32],
            'source_sha':str(row.get('head_sha') or '')[:40],
            'status':str(row.get('status') or '')[:32],
            'conclusion':str(row.get('conclusion') or '')[:32],
            'url':url, 'head_matches_session': row.get('head_sha') == source,
            'release_qualified': False}


def diagnose_snapshot(snapshot: Mapping[str, Any], *, slug: str,
                     client: Any = None, fetch_logs: bool = True,
                     log_reader: Callable[[Any, str, int], bytes | None] = fetch_bounded_job_log
                     ) -> dict[str, Any]:
    """Create one bounded matrix and aggregate verdict for a CI observation.

    This function is observational. Source binding of synthetic PR merge SHAs
    remains the registrar's responsibility, never the diagnostic classifier's.
    """
    state = str(snapshot.get('state') or '')
    classification, hint = _STATES.get(state, ('unknown-provider-state', 'Inspect source-bound CI session'))
    raw_runs = snapshot.get('runs', [])
    if not isinstance(raw_runs, list) or len(raw_runs) > _MAX_MATRIX:
        raise ValueError('CI diagnostic run inventory is too large or malformed')
    source = str(snapshot.get('source_sha') or '')
    if not _SHA.fullmatch(source):
        raise ValueError('CI diagnostic source SHA is invalid')
    matrix = [_bounded_identity(x, source) for x in raw_runs]
    failures = snapshot.get('failures', [])
    if not isinstance(failures, list) or len(failures) > _MAX_FAILURES:
        raise ValueError('CI diagnostic failures are malformed')
    diagnostics = []
    log_budget = 3  # A failing observation must never trigger unbounded log downloads.
    failure_index = {(x.get('run_id'), x.get('attempt')): x for x in failures if isinstance(x, dict)}
    for run in matrix:
        problem = failure_index.get((run['id'], run['attempt']))
        if run['conclusion'] not in _FAILURE and not problem:
            continue
        details = problem.get('detailed_jobs') if isinstance(problem, dict) else []
        if not isinstance(details, list):
            details = []
        jobs = []
        for job in details[:_MAX_JOBS]:
            if not isinstance(job, dict) or type(job.get('job_id')) is not int or job['job_id'] <= 0:
                continue
            name = _safe_label(job.get('job'),160)
            job_steps = job.get('steps') or []
            if not isinstance(job_steps, list):
                job_steps = []
            steps = [{'name':_safe_label(s.get('name'),140),
                      'conclusion':str(s.get('conclusion') or '')[:40],
                      'classification':_hint_step(str(s.get('name') or ''),str(s.get('conclusion') or ''))}
                     for s in job_steps[:12] if isinstance(s, dict)]
            category = steps[0]['classification'] if steps else _hint_step(name, str(job.get('conclusion') or ''))
            snippet=[]
            if client is not None and fetch_logs and log_budget > 0:
                log_budget -= 1
                try:
                    snippet = safe_excerpt(log_reader(client, slug, job['job_id']) or b'')
                except (OSError, ValueError, TimeoutError):
                    snippet = []  # A provider outage is never a reason to expose raw logs.
            jobs.append({'job_id':job['job_id'], 'name':name, 'conclusion':str(job.get('conclusion') or '')[:40],
                         'classification':category, 'steps':steps,
                         'excerpt': snippet, 'excerpt_scope':'bounded-redacted-signal-lines',
                         'url':f'https://github.com/{slug}/actions/runs/{run["id"]}/attempts/{run["attempt"]}/job/{job["job_id"]}'})
        diagnostics.append({'name':run['name'],'run_id':run['id'],'attempt':run['attempt'],
                            'classification':jobs[0]['classification'] if jobs else
                               ('skipped-required-check' if run['conclusion'] in ('skipped','neutral') else
                                'required-check-failed'),
                            'jobs': jobs[:_MAX_JOBS], 'url':run['url']})
    missing = list(snapshot.get('missing') or [])[:_MAX_MATRIX]
    ambiguous = list(snapshot.get('ambiguous') or [])[:_MAX_MATRIX]
    evidence = snapshot.get('exact_run_evidence') or {}
    issues = evidence.get('issues', []) if isinstance(evidence, dict) else []
    return {'schema_version':1, 'state':state, 'classification':classification,
            'hint':hint, 'matrix':matrix, 'failures':diagnostics[:_MAX_FAILURES],
            'missing':missing, 'ambiguous':ambiguous,
            'evidence_issues':issues[:_MAX_MATRIX] if isinstance(issues,list) else [],
            'aggregate':('pass' if state == 'REQUIRED_CHECKS_PASSED' and
                         snapshot.get('checks_qualified') is True else
                         'fail' if state == 'CHECKS_FAILED' else 'pending-or-blocked'),
            'source_sha':source, 'release_qualified':False,
            'raw_log_content_persisted':False, 'remote_mutations':False}
