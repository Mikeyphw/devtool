"""Opt-in source-bound GitHub run log reading for the native Rich monitor.

Never fetch on mount or from a keyboard thread. The existing audited
``actions_runs.remote_logs`` is the sole network/download authority; this
module only verifies observed run identity and renders safe, bounded text.
The complete authenticated archive is retained by that native operation.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
from typing import Any, Mapping
import zipfile

from .github_actions import GitHubActionsClient, GitHubActionsError, _slug
from .logstore import redact_sensitive_text
from .display_graphemes import cluster_prefix
from .actions_runs import remote_logs

MAX_UNCOMPRESSED = 24 * 1024 * 1024
MAX_ENTRIES = 100
MAX_LINES = 50000
MAX_LINE = 280
_SHA = re.compile(r'[0-9a-f]{40}\Z')
_ANSI = re.compile(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))')


def _safe_text(value: str) -> str:
    """Visible, secret-redacted bounded text; not an untrusted Rich markup."""
    redacted = redact_sensitive_text(_ANSI.sub('', value))
    return cluster_prefix(''.join(' ' if (ord(c) < 32 or ord(c) == 127) else c
                                  for c in redacted), MAX_LINE)


def verified_remote_run(client: GitHubActionsClient, slug: str,
                        selected: Mapping[str, Any]) -> int:
    """Refuse a stale rerun, different repo/event/workflow, or absent commit."""
    slug = _slug(slug)
    rid = selected.get('id')
    attempt = selected.get('attempt', selected.get('run_attempt'))
    sha = selected.get('head_sha')
    if (type(rid) is not int or rid <= 0 or
            type(attempt) is not int or attempt <= 0 or
            not isinstance(sha, str) or not _SHA.fullmatch(sha.lower())):
        raise GitHubActionsError('Selected log run lacks exact ID, attempt or source SHA')
    current = client.get_run(slug, rid)
    if (current.get('id') != rid or
            current.get('run_attempt') != attempt or
            str(current.get('head_sha') or '').lower() != sha.lower()):
        raise GitHubActionsError('Remote run source/attempt changed; log download refused')
    source_repo = (current.get('repository') or {}).get('full_name')
    if not isinstance(source_repo, str) or source_repo.lower() != slug.lower():
        raise GitHubActionsError('Remote run belongs to a different repository')
    for key in ('event', 'workflow_id'):
        expected = selected.get(key)
        if expected is not None and current.get(key) != expected:
            raise GitHubActionsError('Remote run event/workflow identity changed')
    return rid


def sanitized_archive_lines(archive: Path, digest: str) -> tuple[str, ...]:
    """Review logs without extraction, zip traversal or unbounded decompression."""
    path = Path(archive)
    if (path.is_symlink() or not path.is_file() or
            not isinstance(digest,str) or not re.fullmatch(r'[0-9a-f]{64}',digest) or
            not 0 < path.stat().st_size <= 32 * 1024 * 1024):
        raise GitHubActionsError('Invalid native GitHub log archive')
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(65536), b''):
            h.update(block)
    if h.hexdigest() != digest:
        raise GitHubActionsError('Downloaded GitHub log archive SHA-256 mismatch')
    results: list[str] = []
    remaining = MAX_UNCOMPRESSED
    with zipfile.ZipFile(path) as z:
        entries = z.infolist()
        if not entries or len(entries)>MAX_ENTRIES:
            raise GitHubActionsError('Invalid or oversized GitHub log index')
        for entry in entries:
            name=entry.filename.replace('\\','/')
            parts=name.split('/')
            if (name.startswith('/') or '..' in parts or
                    any(x in ('','.') for x in (parts[:-1] if entry.is_dir() else parts)) or entry.flag_bits & 1 or
                    entry.file_size<0 or entry.file_size>remaining):
                raise GitHubActionsError('Unsafe or oversized GitHub log entry')
            if entry.is_dir():
                continue
            if not name.lower().endswith(('.txt','.log')):
                continue
            if remaining <= 0:
                raise GitHubActionsError('GitHub log uncompressed size exceeds safety limit')
            with z.open(entry,'r') as f:
                content = f.read(remaining+1)
            if len(content) != entry.file_size or len(content)>remaining:
                raise GitHubActionsError('GitHub log length cannot be verified')
            remaining -= len(content)
            results.append(_safe_text('[Log] '+name))
            # Redact the complete text *before* splitting, so multi-line
            # private-key blocks cannot escape per-line redaction.
            full = redact_sensitive_text(_ANSI.sub('', content.decode('utf-8',errors='replace')))
            for line in full.splitlines():
                if len(results)>=MAX_LINES:
                    raise GitHubActionsError('Log viewer exceeds bounded line count')
                results.append(_safe_text(line))
    if not results:
        raise GitHubActionsError('GitHub log archive contains no readable text')
    return tuple(results)


def fetch_selected_run_logs(client: GitHubActionsClient, repository: str,
                            selected: Mapping[str, Any]) -> tuple[str,...]:
    """Fetch once, only for user-selected source-attested run and current attempt."""
    rid = verified_remote_run(client,repository,selected)
    attempt = selected.get('attempt', selected.get('run_attempt'))
    # Exact-attempt endpoint prevents a rerun between GET and archive lookup
    # from substituting another attempt. No fallback to latest-run logs.
    receipt = remote_logs(client,_slug(repository),rid,attempt=attempt)
    if (receipt.get('run_id') != rid or receipt.get('repository') != _slug(repository)
            or type(receipt.get('run_attempt')) is not int or receipt['run_attempt'] != attempt):
        raise GitHubActionsError('Native log download receipt lost run identity')
    return sanitized_archive_lines(Path(str(receipt.get('archive') or '')),
                                   str(receipt.get('sha256') or ''))


def fetch_selected_job_logs(client: GitHubActionsClient, repository: str,
                            run: Mapping[str, Any], job: Mapping[str, Any]) -> tuple[str, ...]:
    """Verify membership before and after the exact job-log GET."""
    from .workspace_actions import run_jobs
    from .actions_runs import remote_job_logs
    repository = _slug(repository)
    rid = verified_remote_run(client, repository, run)
    attempt = run.get('run_attempt', run.get('attempt'))
    job_id = job.get('id')
    if type(job_id) is not int or job_id <= 0:
        raise GitHubActionsError('Invalid selected job identity')
    def verify_membership():
        rows = run_jobs(client, repository, run)['items']
        matches = [x for x in rows if x['id'] == job_id]
        if len(matches) != 1 or any(matches[0].get(k) != job.get(k) for k in ('id','run_id','run_attempt')):
            raise GitHubActionsError('Selected job is not a member of the verified run attempt')
    verify_membership()
    receipt = remote_job_logs(client, repository, rid, attempt, job_id)
    if any(receipt.get(k) != v for k, v in (('repository',repository),('run_id',rid),('run_attempt',attempt),('job_id',job_id))):
        raise GitHubActionsError('Job log receipt identity mismatch')
    verify_membership()
    path = Path(receipt['path'])
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 32*1024*1024:
        raise GitHubActionsError('Invalid native job log')
    raw = path.read_bytes()
    if len(raw) != receipt.get('size') or hashlib.sha256(raw).hexdigest() != receipt.get('sha256'):
        raise GitHubActionsError('Job log digest or size mismatch')
    full = redact_sensitive_text(_ANSI.sub('', raw.decode('utf-8', errors='replace')))
    lines = full.splitlines()
    if len(lines) > MAX_LINES:
        raise GitHubActionsError('Job log exceeds bounded line count')
    # Keep complete redacted lines for paging; unlike an inline preview, do not truncate.
    return tuple(''.join(' ' if ord(c)<32 or 127<=ord(c)<160 or ord(c) in (0x202a,0x202b,0x202c,0x202d,0x202e,0x2066,0x2067,0x2068,0x2069) else c for c in line) for line in lines)
