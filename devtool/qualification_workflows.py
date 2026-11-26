"""Source-bound CI24 / native G3R dogfood workflows (QW01).

Each operation is a declared Devtool wrapper workflow and delegates to the
existing ACTIONS-02/03/04 client and G3R v3 producer/independent verifier.
No alternate scheduler, run guessing, release promotion, or background job.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import uuid
from time import monotonic
from typing import Any, Iterator
from zipfile import ZipFile, BadZipFile

from .actions_dispatch import _durable_record, dispatch_workflow, GitHubDispatchPreflightError
from .actions_pipeline import _source, _git
from .actions_runs import _normalize_run
from .actions_artifacts import artifact_list, download_artifact, verify_download
from .g3r_evidence_gate import evaluate as verify_native
from .final_seal import final_seal_report, FINAL_SEAL_SCHEMA_VERSION
from .g3r_evidence_generate import generate as generate_native
from .github_actions import GitHubActionsClient, GitHubActionsError, GitHubGatewayReadUnavailable, resolve_repository, resolve_token
from .remed_r04b_fixed_point import G3R_REAL_SUBSTRATE_CARRY

WORKFLOW = 'devtool-full-repository-pytest.yml'
G3R_REMOTE_WORKFLOW = 'devtool-g3r-current-evidence.yml'
G3R_REMOTE_JOB = 'android-g1-hosted'
G3R_REMOTE_ARTIFACT = 'g3r-android-g1-hosted'
EXPECTED_JOB = re.compile(r'^Python 3\.13 / shard ([1-9]|1[0-9]|2[0-4])/24$')
SHA = re.compile(r'^[a-f0-9]{40}$')
EXPECTED_ARTIFACTS = {f'devtool-full-shard-{n}' for n in range(1, 25)} | {'devtool-full-pytest-summary'}
# The canonical FS03 contract is kept intact: missing, duplicate, or failed checks
# block even when a test fixture or future report mistakenly claims status=pass.
FS03_REQUIRED_CHECKS = frozenset({
    'config.canonical', 'config.parse', 'workspace.targets',
    'targets.final-seal-overlays', 'coverage.final-remediation',
    'compatibility.registry', 'schemas.versioned', 'docs.required',
    'examples.canonical', 'tests.roadmap-contracts', 'completion.synchronized',
    'validation.final-seal-profile', 'android.operations-roadmap',
    'artifacts.archive-formats', 'repository.backup-hygiene',
    'release.discoverability',
})


def _root() -> Path:
    # The entrypoint is imported from the selected repository, not a second installation.
    return Path(__file__).resolve().parents[1]


def _state_dirs(root: Path) -> tuple[Path, ...]:
    """Canonical first; retain legacy CI24/G3R receipts without moving them.

    A matching receipt in both locations is ambiguous, not a new dispatch
    opportunity. Invalid relative XDG paths never become cwd-relative state.
    """
    from .xdg import state_roots
    suffix = hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:20]
    return tuple(base / 'qualification-workflows' / suffix for base in state_roots())


def _state_dir(root: Path) -> Path:
    return _state_dirs(root)[0]


def _state_path(root: Path, sha: str, kind: str) -> Path:
    if not SHA.fullmatch(sha) or kind not in ('ci24', 'g3r', 'g3r-remote'):
        raise GitHubActionsError('Invalid qualification record identity')
    candidates = [parent / f'{sha}-{kind}.json' for parent in _state_dirs(root)]
    existing = [path for path in candidates if path.exists() or path.is_symlink()]
    if len(existing) > 1:
        raise GitHubActionsError('Conflicting canonical and legacy qualification receipts; no new dispatch')
    if existing:
        receipt = existing[0]
        if (receipt.is_symlink() or receipt.parent.is_symlink()
            or not receipt.is_file() or (os.name == 'posix'
                                    and receipt.parent.stat().st_mode & 0o077)):
            raise GitHubActionsError('Unsafe existing qualification receipt; no new dispatch')
        return receipt
    return candidates[0]


def _load(root: Path, head: str, kind: str) -> dict[str, Any]:
    path = _state_path(root, head, kind)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise GitHubActionsError(f'No saved {kind} receipt for current source')
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise GitHubActionsError('Invalid qualification receipt') from None
    if (not isinstance(data, dict) or data.get('schema') != 1
        or data.get('source_sha') != head or data.get('repo_root') != str(root.resolve())
        or data.get('kind') != kind):
        raise GitHubActionsError('Qualification receipt source/identity mismatch')
    return data


def _save(root: Path, sha: str, kind: str, data: dict[str, Any]) -> None:
    _durable_record(_state_path(root, sha, kind), data)


@contextmanager
def _lock(root: Path, head: str, kind: str) -> Iterator[None]:
    parents = _state_dirs(root)
    parent = parents[0]
    if parent.is_symlink():
        raise GitHubActionsError('Qualification state root is a symlink')
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name == 'posix' and parent.stat().st_mode & 0o077:
        raise GitHubActionsError('Qualification state directory is not private')
    # Do not claim a new exclusive operation when an older runtime's lock
    # still exists under the pre-unification state root.
    for old in parents[1:]:
        previous = old/f'{head}-{kind}.lock'
        if previous.exists() or previous.is_symlink():
            raise GitHubActionsError('Legacy qualification operation active or interrupted; inspect before resume')
    lock = parent/f'{head}-{kind}.lock'
    try:
        lock.mkdir(mode=0o700)
    except FileExistsError:
        raise GitHubActionsError('Qualification operation already active or interrupted; inspect its receipt/lock') from None
    try:
        yield
    finally:
        lock.rmdir()


def _identity(root: Path) -> tuple[str, str]:
    head = _source(root)  # Fails closed on an uncommitted source change.
    branch = _git(root, 'symbolic-ref', '--quiet', '--short', 'HEAD')
    if not branch or not re.fullmatch(r'[A-Za-z0-9._/-]{1,128}', branch) or '..' in branch:
        raise GitHubActionsError('Qualification requires a named Git branch (no detached HEAD)')
    return head, branch


def _client(root: Path) -> tuple[GitHubActionsClient, str]:
    token, _ = resolve_token()
    slug = resolve_repository('', root)
    return GitHubActionsClient(token), slug


def ci24_start(root: Path, *, client: GitHubActionsClient | None = None,
               slug: str | None = None) -> dict[str, Any]:
    head, branch = _identity(root)
    with _lock(root, head, 'ci24'):
        path = _state_path(root, head, 'ci24')
        previous_failures: list[dict[str, str]] = []
        if path.exists() or path.is_symlink():
            previous = _load(root, head, 'ci24')
            # Only an explicitly classified, zero-POST source preflight rejection
            # is safe to retry. Persist provenance of the earlier rejection.
            if (previous.get('state') != 'PREFLIGHT_REJECTED'
                or previous.get('request_id') is not None
                or previous.get('run_id') is not None):
                raise GitHubActionsError('Existing CI24 dispatch receipt for this source; resume or inspect it, NEVER dispatch twice')
            previous_failures = list(previous.get('preflight_failures') or [])
            previous_failures.append({'reason': str(previous.get('preflight_error') or ''),
                                      'source_sha': head})
        if client is None or slug is None:
            client, slug = _client(root)
        # Read-only GitHub and workflow validation first: errors here cannot
        # have dispatched anything. Avoid poisoning a source with a fake receipt.
        plan = dispatch_workflow(client, slug, WORKFLOW, branch, {}, execute=False,
                                 expected_source_sha=head)
        if (plan.get('pre_dispatch_sha') != head
            or plan.get('workflow_path') != f'.github/workflows/{WORKFLOW}'
            or type(plan.get('workflow_id')) is not int):
            raise GitHubActionsError('CI24 read-only preflight identity mismatch; no dispatch attempted')
        record = {'schema': 1, 'kind': 'ci24', 'repo_root': str(root.resolve()),
                  'source_sha': head, 'branch': branch, 'repository': slug,
                  'workflow': WORKFLOW, 'workflow_id': plan['workflow_id'],
                  'state': 'DISPATCH_OUTCOME_UNKNOWN', 'preflight_failures': previous_failures,
                  'qualified_release': False, 'run_id': None, 'request_id': None}
        _save(root, head, 'ci24', record)  # Durable before the POST path.
        try:
            result = dispatch_workflow(client, slug, WORKFLOW, branch, {}, execute=True,
                                       expected_source_sha=head)
            if result.get('pre_dispatch_sha') != head:
                raise GitHubActionsError('Dispatch result differs from expected source; cannot correlate')
            if (result.get('workflow_path') != f'.github/workflows/{WORKFLOW}'
                or result.get('workflow_id') != plan['workflow_id']):
                raise GitHubActionsError('Dispatch result workflow identity mismatch')
            record.update(request_id=result.get('request_id'),
                          run_id=result.get('run_id'), run_correlated=bool(result.get('run_id')),
                          state='DISPATCHED_UNOBSERVED')
        except GitHubDispatchPreflightError as exc:
            # Only this typed error guarantees that no POST took place.
            record.update(state='PREFLIGHT_REJECTED', preflight_error=str(exc))
            _save(root, head, 'ci24', record)
            raise GitHubActionsError('CI24 pre-dispatch source drift; no POST attempted: ' + str(exc)) from None
        except Exception as exc:
            record['state'] = 'DISPATCH_OUTCOME_UNKNOWN'
            _save(root, head, 'ci24', record)
            raise GitHubActionsError('CI24 dispatch unresolved; DO NOT RETRY: ' + str(exc)) from None
        _save(root, head, 'ci24', record)
        return _public_ci(record)


def _public_ci(record: dict[str, Any]) -> dict[str, Any]:
    return {'schema': 1, 'workflow': 'CI24', 'source_sha': record['source_sha'],
            'repository': record['repository'], 'run_id': record.get('run_id'),
            'request_id': record.get('request_id'), 'state': record['state'],
            'run_correlated': record.get('run_correlated', False),
            'ci_verified': record['state'] == 'PASS_CI24_VERIFIED',
            'formal_release_qualified': False,
            'next_action': ('ci24-resume (provide DEVTOOL_CI24_RUN_ID for an uncorrelated 204 response)'
                            if record['state'] != 'PASS_CI24_VERIFIED' else 'g3r-native and g3r-verify')}



class _RunScopedClient:
    """Reuse one observed GitHub run *within a single* CI24 check.

    ACTIONS-04 deliberately rereads the remote run in artifact_list,
    download_artifact and verify_download. Here the caller already fetched and
    normalized that run. Sharing that immutable observation avoids redundant
    network round trips while retaining every ACTIONS-04 identity, digest,
    registry and archive validation. A fresh remote read after those validations
    rejects source/attempt/status changes observed during the operation.

    Never persist or share this object between commands or qualification runs.
    """
    def __init__(self, delegate: GitHubActionsClient, slug: str, run_id: int,
                 raw_run: dict[str, Any]) -> None:
        self._delegate = delegate
        self._slug = slug.lower()
        self._run_id = run_id
        self._raw_run = deepcopy(raw_run)

    def get_run(self, slug: str, run_id: int) -> dict[str, Any]:
        if slug.lower() != self._slug or run_id != self._run_id:
            raise GitHubActionsError('CI24 observed-run scope mismatch')
        return deepcopy(self._raw_run)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


def _ci24_assert_same_remote_run(initial: dict[str, Any], final: dict[str, Any]) -> None:
    identity = ('id', 'head_sha', 'head_branch', 'event', 'workflow_id',
                'run_attempt', 'status', 'conclusion')
    if any(initial.get(key) != final.get(key) for key in identity):
        raise GitHubActionsError('GitHub CI24 run identity/attempt/status changed during verification')


def _ci24_verified_shard_receipt(file: Path, shard: int, source: str,
                                 slug: str) -> dict[str, Any]:
    """Prove a downloaded archive's own source, module inventory and logs.

    GitHub archive SHA-256 and attempt provenance are verified by ACTIONS-04
    before this function is called. Independent per-module hashes prevent a
    metadata-only PASS or a forged aggregate summary from qualifying shards.
    """
    try:
        with ZipFile(file) as z:
            names = z.namelist()
            if names.count('receipt.json') != 1 or z.getinfo('receipt.json').file_size > 512*1024:
                raise GitHubActionsError('CI24 shard receipt missing/duplicate/oversized')
            blob = z.read('receipt.json')
            if len(blob) > 512*1024:
                raise GitHubActionsError('CI24 shard receipt inflated beyond bound')
            record = json.loads(blob)
            if not isinstance(record, dict):
                raise GitHubActionsError('CI24 shard receipt is not an object')
            entries = record.get('modules')
            expected = record.get('modules_expected')
            if (record.get('schema_version') != 1 or
                record.get('source_sha') != source or
                record.get('shard') != shard or record.get('shards') != 24 or
                record.get('success') is not True or record.get('complete') is not True or
                record.get('formal_release_qualified') is not False or
                (record.get('repository') not in ('', slug)) or
                type(record.get('inventory_count')) is not int or record['inventory_count'] <= 0 or
                not re.fullmatch('[a-f0-9]{64}', str(record.get('inventory_sha256') or '')) or
                not isinstance(entries, list) or not entries or
                not isinstance(expected, list) or len(entries) != len(expected) or
                [e.get('module') if isinstance(e, dict) else None for e in entries] != expected):
                raise GitHubActionsError('CI24 shard source/inventory/success receipt invalid')
            test_sum = 0
            for entry in entries:
                if (not isinstance(entry, dict) or entry.get('status') != 'PASS' or
                    entry.get('exit_code') != 0 or entry.get('failures') != 0 or
                    entry.get('errors') != 0 or type(entry.get('tests')) is not int or
                    entry['tests'] <= 0):
                    raise GitHubActionsError('CI24 shard module did not pass')
                test_sum += entry['tests']
                for rel_key, digest_key, prefix in (
                    ('log', 'log_sha256', 'logs/'),
                    ('junit', 'junit_sha256', 'junit/')):
                    name = entry.get(rel_key)
                    if (not isinstance(name, str) or not name.startswith(prefix)
                        or '/' in name[len(prefix):] or '..' in name or '\\' in name
                        or names.count(name) != 1
                        or not re.fullmatch('[a-f0-9]{64}', str(entry.get(digest_key) or ''))):
                        raise GitHubActionsError('CI24 shard log/JUnit path or digest invalid')
                    info = z.getinfo(name)
                    if info.file_size > 64*1024*1024:
                        raise GitHubActionsError('CI24 shard log/JUnit exceeds safe size')
                    h = hashlib.sha256()
                    with z.open(info) as stream:
                        byte_count = 0
                        while part := stream.read(65536):
                            byte_count += len(part)
                            if byte_count > 64*1024*1024:
                                raise GitHubActionsError('CI24 shard log/JUnit inflated beyond bound')
                            h.update(part)
                    if h.hexdigest() != entry[digest_key]:
                        raise GitHubActionsError('CI24 shard log/JUnit SHA-256 mismatch')
            return {'shard': shard, 'modules': expected, 'tests': test_sum,
                    'inventory_count': record['inventory_count'],
                    'inventory_sha256': record['inventory_sha256']}
    except (BadZipFile, OSError, KeyError, ValueError) as exc:
        raise GitHubActionsError('CI24 shard evidence malformed: '+str(exc)) from None


def _ci24_evaluate(client: GitHubActionsClient, slug: str, root: Path, record: dict[str, Any],
                   identity: int) -> dict[str, Any]:
    if type(identity) is not int or identity <= 0:
        raise GitHubActionsError('An explicit positive GitHub run ID is required')
    phase_started = monotonic()
    def checkpoint(name: str) -> None:
        nonlocal phase_started
        now = monotonic()
        _qualification_progress('ci24.' + name, 'complete', now - phase_started)
        phase_started = now
    raw_run = client.get_run(slug, identity)
    run = _normalize_run(raw_run, slug, identity)
    # Always rebind to remote source and workflow, even if the dispatch was uncorrelated.
    if run.get('head_sha') != record['source_sha'] or run.get('head_branch') != record['branch']:
        raise GitHubActionsError('GitHub run does not match exact source SHA/branch')
    if (run.get('event') != 'workflow_dispatch' or type(record.get('workflow_id')) is not int
        or run.get('workflow_id') != record['workflow_id']):
        raise GitHubActionsError('GitHub workflow/event identity mismatch')
    checkpoint('remote-run')
    if run['status'] != 'completed':
        return {'status': 'WAITING_REMOTE', 'run_id': identity, 'complete': False}
    if run['conclusion'] != 'success':
        raise GitHubActionsError('GitHub 24-shard run failed: '+str(run['conclusion']))
    # Fail closed on re-run ambiguity: the artifacts API can omit run_attempt.
    if run.get('run_attempt') != 1:
        raise GitHubActionsError('Rerun attempt requires an attempt-bound evidence contract; original attempt only')
    # The same authenticated observation is bound to all downstream artifact
    # checks; no bypass of those checks and no cross-invocation success cache.
    scoped_client = _RunScopedClient(client, slug, identity, raw_run)
    jobs = scoped_client.run_attempt_jobs(slug, identity, 1)
    checkpoint('jobs-fetch')
    shards: set[int] = set()
    summary = 0
    for job in jobs:
        if job.get('run_attempt') not in (None, 1) or job.get('conclusion') != 'success' or job.get('status') != 'completed':
            raise GitHubActionsError('Non-success, wrong-attempt or incomplete CI job')
        match = EXPECTED_JOB.fullmatch(str(job.get('name') or ''))
        if match:
            shard = int(match.group(1))
            if shard in shards:
                raise GitHubActionsError('Duplicate CI shard job')
            shards.add(shard)
        elif job.get('name') == 'Summarize all 24 shards (fail closed)':
            summary += 1
        else:
            raise GitHubActionsError('Unexpected CI matrix job: '+str(job.get('name')))
    if shards != set(range(1, 25)) or summary != 1 or len(jobs) != 25:
        raise GitHubActionsError('CI is missing or duplicating shard/summary jobs')
    catalog = artifact_list(scoped_client, slug, identity)['artifacts']
    checkpoint('artifact-list')
    names = [a['name'] for a in catalog]
    if len(catalog) != 25 or set(names) != EXPECTED_ARTIFACTS:
        raise GitHubActionsError('CI24 artifact set does not contain exactly 24 shards and one summary')
    if not all(re.fullmatch(r'sha256:[a-fA-F0-9]{64}', str(a.get('digest') or '')) for a in catalog):
        raise GitHubActionsError('CI24 artifact missing authenticated archive SHA256')
    # Every shard's archive is independently hashed against authenticated GitHub
    # metadata, rebound to this exact run and attempt, and registered durably.
    # The historical 25-metadata/1-archive receipt is preserved as historical
    # evidence; fresh qualification demands all 25 archive bytes.
    verified_archives = {}
    shard_receipts = []
    summary_artifact = next(a for a in catalog if a['name'] == 'devtool-full-pytest-summary')
    registered = None
    for index, artifact in enumerate(sorted(catalog, key=lambda a: a['name']), 1):
        try:
            proven = verify_download(scoped_client, slug, identity, artifact['id'], root)
        except GitHubActionsError as exc:
            if 'no registered download' not in str(exc).lower():
                raise
            download_artifact(scoped_client, slug, identity, artifact['id'], root)
            proven = verify_download(scoped_client, slug, identity, artifact['id'], root)
        if (proven.get('archive_verified') is not True or
            proven.get('sha256') != artifact['digest'].split(':', 1)[1].lower()):
            raise GitHubActionsError('CI24 shard archive byte/digest verification failed')
        verified_archives[artifact['name']] = proven['sha256']
        if artifact['id'] == summary_artifact['id']:
            registered = proven
        else:
            shard_name = artifact['name']
            match = re.fullmatch(r'devtool-full-shard-([1-9]|1[0-9]|2[0-4])', shard_name)
            if not match:
                raise GitHubActionsError('Unexpected CI24 shard artifact identity')
            shard_receipts.append(_ci24_verified_shard_receipt(Path(proven['file']),
                                  int(match.group(1)), record['source_sha'], slug))
        if index % 6 == 0:
            checkpoint('artifact-bytes-'+str(index))
    checkpoint('all-25-artifact-archives')
    if not registered or not registered.get('archive_verified'):
        raise GitHubActionsError('CI summary archive integrity not proven')
    path = Path(registered['file'])
    with ZipFile(path) as z:
        if 'summary.json' not in z.namelist() or z.namelist().count('summary.json') != 1:
            raise GitHubActionsError('CI summary.json absent or ambiguous')
        if z.getinfo('summary.json').file_size > 128 * 1024:
            raise GitHubActionsError('CI24 summary exceeds bounded JSON size')
        with z.open('summary.json') as f:
            raw_summary = f.read(128 * 1024 + 1)
            if len(raw_summary) > 128 * 1024:
                raise GitHubActionsError('CI24 summary inflated beyond bounded JSON size')
            data = json.loads(raw_summary)
    if not isinstance(data, dict) or any([
        data.get('schema_version') != 1, data.get('status') != 'PASS',
        data.get('source_sha') != record['source_sha'], data.get('expected_shards') != 24,
        data.get('present_shards') != 24, data.get('formal_release_qualified') is not False,
        type(data.get('total_modules')) is not int or data['total_modules'] <= 0,
        data.get('covered_modules') != data.get('total_modules'),
        type(data.get('tests')) is not int or data['tests'] <= 0,
        data.get('failures') != 0, data.get('errors') != 0,
        data.get('issues') != [], data.get('failed_modules') != [],
    ]):
        raise GitHubActionsError('CI summary is not a complete exact-source 24-shard PASS')
    if len(shard_receipts) != 24:
        raise GitHubActionsError('CI24 shard evidence inventory incomplete')
    all_modules = [name for r in shard_receipts for name in r['modules']]
    inventories = {(r['inventory_count'], r['inventory_sha256']) for r in shard_receipts}
    if (len(set(all_modules)) != len(all_modules) or
        len(inventories) != 1 or next(iter(inventories))[0] != data['total_modules'] or
        len(all_modules) != data['total_modules'] or
        sum(r['tests'] for r in shard_receipts) != data['tests']):
        raise GitHubActionsError('CI24 24 shard receipts disagree with complete summary')
    # Reobserve the actual GitHub run after all local and remote artifact proofs.
    # The frozen snapshot is an optimization, NEVER an authority across runs.
    final_remote = _normalize_run(client.get_run(slug, identity), slug, identity)
    _ci24_assert_same_remote_run(run, final_remote)
    checkpoint('final-remote')
    return {'status': 'PASS_CI24_VERIFIED', 'run_id': identity,
            'run_attempt': 1, 'jobs_verified': 25, 'shards_verified': 24,
            'artifact_metadata_verified': 25, 'artifact_archives_verified': 25,
            'shard_archives_verified': 24,
            'artifact_archive_sha256': verified_archives,
            'summary_archives_verified': 1,
            'summary_archive_sha256': registered['sha256'],
            'summary_json_sha256': hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest(),
            'tests': data['tests'], 'modules': data['total_modules'], 'complete': True}


def _ci24_failure_code(exc: BaseException) -> str:
    """Bounded actionable classification; never persist provider text or secrets."""
    if isinstance(exc, GitHubGatewayReadUnavailable):
        return 'CI24_PROVIDER_GATEWAY_UNAVAILABLE'
    message = str(exc)
    failed = re.fullmatch(r'GitHub 24-shard run failed: (failure|cancelled|timed_out|action_required|neutral|skipped|stale|startup_failure)', message)
    if failed:
        return 'CI24_REMOTE_RUN_' + failed.group(1).upper()
    exact = {
        'GitHub run does not match exact source SHA/branch': 'CI24_SOURCE_MISMATCH',
        'GitHub workflow/event identity mismatch': 'CI24_WORKFLOW_MISMATCH',
        'Rerun attempt requires an attempt-bound evidence contract; original attempt only': 'CI24_RUN_ATTEMPT_UNSUPPORTED',
        'Non-success, wrong-attempt or incomplete CI job': 'CI24_JOB_EVIDENCE_REFUSED',
        'Duplicate CI shard job': 'CI24_JOB_EVIDENCE_REFUSED',
        'CI is missing or duplicating shard/summary jobs': 'CI24_JOB_EVIDENCE_REFUSED',
        'CI24 artifact set does not contain exactly 24 shards and one summary': 'CI24_ARTIFACT_INVENTORY_MISMATCH',
        'CI24 artifact missing authenticated archive SHA256': 'CI24_ARTIFACT_DIGEST_MISSING',
        'CI24 shard archive byte/digest verification failed': 'CI24_ARTIFACT_DIGEST_MISMATCH',
        'CI summary is not a complete exact-source 24-shard PASS': 'CI24_SUMMARY_REFUSED',
        'CI24 24 shard receipts disagree with complete summary': 'CI24_SUMMARY_REFUSED',
        'CI receipt repository changed': 'CI24_REPOSITORY_MISMATCH',
        'Run ID contradicts correlated dispatch receipt': 'CI24_RUN_ID_CONFLICT',
        'GitHub 204 dispatch was uncorrelated; specify DEVTOOL_CI24_RUN_ID explicitly; do not guess or redispatch': 'CI24_EXPLICIT_RUN_ID_REQUIRED',
        'Dispatch outcome unknown; inspect ACTIONS-02 receipt before trying another run': 'CI24_DISPATCH_OUTCOME_UNKNOWN',
    }
    if message in exact:
        return exact[message]
    if isinstance(exc, TimeoutError):
        return 'CI24_OBSERVATION_TIMEOUT'
    if isinstance(exc, OSError):
        return 'CI24_OBSERVATION_IO_ERROR'
    return 'CI24_NATIVE_EVIDENCE_REFUSED'


def ci24_resume(root: Path, *, explicit_run_id: int | None = None,
                client: GitHubActionsClient | None = None, slug: str | None = None) -> dict[str, Any]:
    head, _ = _identity(root)
    with _lock(root, head, 'ci24'):
        record = _load(root, head, 'ci24')
        observation_id = uuid.uuid4().hex
        original = record.get('run_id')
        try:
            if record.get('state') == 'DISPATCH_OUTCOME_UNKNOWN' and not record.get('workflow_id'):
                raise GitHubActionsError('Dispatch outcome unknown; inspect ACTIONS-02 receipt before trying another run')
            if client is None or slug is None:
                client, slug = _client(root)
            if record.get('repository', '').lower() != slug.lower():
                raise GitHubActionsError('CI receipt repository changed')
            if explicit_run_id is not None and original is not None and int(explicit_run_id) != original:
                raise GitHubActionsError('Run ID contradicts correlated dispatch receipt')
            rid = original if original is not None else explicit_run_id
            if rid is None:
                raise GitHubActionsError('GitHub 204 dispatch was uncorrelated; specify DEVTOOL_CI24_RUN_ID explicitly; do not guess or redispatch')
            # The native verifier owns every source/run/artifact check.
            outcome = _ci24_evaluate(client, slug, root, record, rid)
        except (GitHubActionsError, OSError, ValueError, RuntimeError) as exc:
            # Keep the previous successful observation and dispatch identity intact.
            # A failed fresh read is not WAITING_REMOTE and not fresh qualification.
            record['last_observation'] = {'id': observation_id, 'status': 'FAILED',
                'error_code': _ci24_failure_code(exc),
                'run_id': original if type(original) is int and original > 0 else None}
            try:
                _save(root, head, 'ci24', record)
            except (OSError, ValueError, RuntimeError):
                # A receipt write failure cannot replace the original native error.
                print('CI24_OBSERVATION_FAILURE_RECEIPT_WRITE_FAILED', file=sys.stderr)
            raise
        record.update(state=outcome['status'], run_id=rid,
                      run_correlated=bool(record.get('run_correlated')),
                      selected_uncorrelated=not bool(record.get('run_correlated')),
                      verification=outcome,
                      last_observation={'id': observation_id,
                          'status': 'VERIFIED' if outcome.get('complete') else 'PENDING',
                          'error_code': None, 'run_id': rid})
        _save(root, head, 'ci24', record)
        return {**_public_ci(record), 'verification': outcome,
                'last_observation': record['last_observation'],
                'observation_completed': True, 'qualification_complete': bool(outcome.get('complete')),
                'qualification_status': ('verified' if outcome.get('complete') else 'pending')}


def _read_hosted_g1_archive(file: Path, *, source: str, tree: str,
                            run_id: int) -> dict[str, Any]:
    """Reject unbound, duplicate, oversized or forged hosted evidence claims.

    Live run/job/artifact metadata is verified independently by the caller.
    The archive's bytes are independently checked by ACTIONS-04 first.
    """
    try:
        with ZipFile(file) as z:
            items = z.infolist()
            if (len(items) != 2 or
                {x.filename for x in items} != {'android_g1_receipt.json', 'android_g1.log'} or
                any(x.file_size > 4*1024*1024 or x.file_size < 1 or
                    ((x.external_attr >> 16) & 0o170000) not in (0, 0o100000)
                    for x in items)):
                raise GitHubActionsError('Hosted G1 artifact inventory/size/mode invalid')
            receipt_raw = z.read('android_g1_receipt.json')
            log = z.read('android_g1.log')
    except (BadZipFile, KeyError, OSError) as exc:
        raise GitHubActionsError('Hosted G1 archive invalid: '+str(exc)) from None
    if len(receipt_raw) > 32768:
        raise GitHubActionsError('Hosted G1 receipt exceeds bound')
    try:
        receipt = json.loads(receipt_raw)
        output = json.loads(log.split(b'\nSTDERR:\n', 1)[0])
    except (ValueError, UnicodeDecodeError) as exc:
        raise GitHubActionsError('Hosted G1 receipt or execution JSON malformed') from None
    if (not isinstance(receipt, dict) or
        receipt.get('schema') != 'devtool.g3r.hosted-g1.v1' or
        receipt.get('source_commit') != source or receipt.get('source_tree') != tree or
        receipt.get('run_id') != run_id or receipt.get('run_attempt') != 1 or
        receipt.get('job') != G3R_REMOTE_JOB or receipt.get('runner_os') != 'Linux' or
        receipt.get('status') != 'PASS' or receipt.get('exit_code') != 0 or
        receipt.get('formal_release_qualified') is not False or
        receipt.get('output_sha256') != hashlib.sha256(log).hexdigest() or
        not isinstance(output, dict) or output.get('gate') != 'ANDROID-G1' or
        output.get('status') != 'passed' or
        output.get('host', {}).get('termux') is not False or
        not output.get('standalone_runtime_sealed') or
        receipt.get('gradle_receipt_id') != output.get('gradle_receipt_id') or
        receipt.get('artifact_sha256') != output.get('artifact_sha256')):
        raise GitHubActionsError('Hosted G1 source/attempt/Gradle proof mismatch')
    return {'source_commit': source, 'source_tree': tree,
            'status': 'PASS', 'run_id': run_id, 'run_attempt': 1,
            'receipt': receipt, 'log': log}


def g3r_remote_start(root: Path, *, client: GitHubActionsClient | None = None,
                     slug: str | None = None) -> dict[str, Any]:
    head, branch = _identity(root)
    with _lock(root, head, 'g3r-remote'):
        path = _state_path(root, head, 'g3r-remote')
        if path.exists() or path.is_symlink():
            raise GitHubActionsError('Existing G3R remote dispatch; resume, NEVER dispatch twice')
        if client is None or slug is None:
            client, slug = _client(root)
        plan = dispatch_workflow(client, slug, G3R_REMOTE_WORKFLOW, branch, {},
                                 execute=False, expected_source_sha=head)
        if (plan.get('pre_dispatch_sha') != head or type(plan.get('workflow_id')) is not int
            or plan.get('workflow_path') != f'.github/workflows/{G3R_REMOTE_WORKFLOW}'):
            raise GitHubActionsError('G3R remote preflight mismatch; no dispatch')
        record = {'schema': 1, 'kind': 'g3r-remote', 'repo_root': str(root.resolve()),
                  'source_sha': head, 'source_tree': _git(root, 'rev-parse', 'HEAD^{tree}'),
                  'branch': branch, 'repository': slug,
                  'workflow_id': plan['workflow_id'], 'workflow': G3R_REMOTE_WORKFLOW,
                  'run_id': None, 'request_id': None, 'state': 'DISPATCH_OUTCOME_UNKNOWN'}
        _save(root, head, 'g3r-remote', record)
        try:
            dispatched = dispatch_workflow(client, slug, G3R_REMOTE_WORKFLOW,
                                           branch, {}, execute=True, expected_source_sha=head)
            if (dispatched.get('pre_dispatch_sha') != head or
                dispatched.get('workflow_id') != record['workflow_id'] or
                dispatched.get('workflow_path') != f'.github/workflows/{G3R_REMOTE_WORKFLOW}'):
                raise GitHubActionsError('G3R dispatch identity changed after POST')
            record.update(state='DISPATCHED_UNOBSERVED',
                          run_id=dispatched.get('run_id'),
                          request_id=dispatched.get('request_id'))
        except GitHubDispatchPreflightError as exc:
            record['state'] = 'PREFLIGHT_REJECTED'
            _save(root, head, 'g3r-remote', record)
            raise GitHubActionsError('G3R pre-dispatch source drift; no POST: '+str(exc)) from None
        except Exception as exc:
            _save(root, head, 'g3r-remote', record)
            raise GitHubActionsError('G3R dispatch outcome uncertain; DO NOT REDISPATCH: '+str(exc)) from None
        _save(root, head, 'g3r-remote', record)
        return {'source_sha': head, 'run_id': record['run_id'],
                'request_id': record['request_id'], 'state': record['state'],
                'remote_verified': False, 'formal_release_qualified': False}


def _g3r_remote_evidence(root: Path, client: GitHubActionsClient,
                         slug: str, record: dict[str, Any], rid: int) -> dict[str, Any]:
    observed = _normalize_run(client.get_run(slug, rid), slug, rid)
    if (observed.get('head_sha') != record['source_sha'] or
        observed.get('head_branch') != record['branch'] or
        observed.get('event') != 'workflow_dispatch' or
        observed.get('workflow_id') != record['workflow_id'] or
        observed.get('run_attempt') != 1):
        raise GitHubActionsError('G3R remote run source/branch/workflow/attempt mismatch')
    if observed.get('status') != 'completed':
        return {'status': 'WAITING_REMOTE', 'complete': False, 'run_id': rid}
    if observed.get('conclusion') != 'success':
        raise GitHubActionsError('G3R remote run did not succeed')
    scoped = _RunScopedClient(client, slug, rid, client.get_run(slug, rid))
    jobs = scoped.run_attempt_jobs(slug, rid, 1)
    hosted = [j for j in jobs if j.get('name') == G3R_REMOTE_JOB]
    if (len(hosted) != 1 or hosted[0].get('status') != 'completed' or
        hosted[0].get('conclusion') != 'success' or
        hosted[0].get('run_attempt') not in (None, 1)):
        raise GitHubActionsError('G3R hosted Gradle job not independently successful')
    matches = [x for x in artifact_list(scoped, slug, rid)['artifacts']
               if x.get('name') == G3R_REMOTE_ARTIFACT]
    if len(matches) != 1 or not re.fullmatch(r'sha256:[a-fA-F0-9]{64}',
                                            str(matches[0].get('digest') or '')):
        raise GitHubActionsError('G3R hosted Gradle artifact missing/ambiguous/unhashed')
    item = matches[0]
    try:
        proven = verify_download(scoped, slug, rid, item['id'], root)
    except GitHubActionsError as exc:
        if 'no registered download' not in str(exc).lower():
            raise
        download_artifact(scoped, slug, rid, item['id'], root)
        proven = verify_download(scoped, slug, rid, item['id'], root)
    if (proven.get('archive_verified') is not True or
        proven.get('sha256') != item['digest'].split(':', 1)[1].lower()):
        raise GitHubActionsError('Hosted G1 archive does not match authenticated digest')
    tree = record['source_tree']
    result = _read_hosted_g1_archive(Path(proven['file']), source=record['source_sha'],
                                     tree=tree, run_id=rid)
    last = _normalize_run(client.get_run(slug, rid), slug, rid)
    _ci24_assert_same_remote_run(observed, last)
    result['artifact_sha256'] = proven['sha256']
    result['file'] = proven['file']
    result['complete'] = True
    return result


def g3r_remote_resume(root: Path, *, explicit_run_id: int | None = None,
                      client: GitHubActionsClient | None = None,
                      slug: str | None = None) -> dict[str, Any]:
    head, _ = _identity(root)
    with _lock(root, head, 'g3r-remote'):
        record = _load(root, head, 'g3r-remote')
        if client is None or slug is None:
            client, slug = _client(root)
        if record['repository'].lower() != slug.lower():
            raise GitHubActionsError('G3R remote repository identity changed')
        rid = record.get('run_id')
        if rid is not None and explicit_run_id is not None and rid != explicit_run_id:
            raise GitHubActionsError('G3R remote run ID contradicts dispatch receipt')
        rid = rid if rid is not None else explicit_run_id
        if type(rid) is not int or rid <= 0:
            raise GitHubActionsError('G3R dispatch uncorrelated; explicit run ID required; no redispatch')
        evidence = _g3r_remote_evidence(root, client, slug, record, rid)
        if evidence.get('complete'):
            old = record.get('artifact_sha256')
            if old is not None and old != evidence['artifact_sha256']:
                raise GitHubActionsError('G3R hosted artifact changed across receipts')
            record.update(state='PASS_HOSTED_G1_VERIFIED', run_id=rid,
                          artifact_sha256=evidence['artifact_sha256'])
        else:
            record.update(state='WAITING_REMOTE', run_id=rid)
        _save(root, head, 'g3r-remote', record)
        return {'source_sha': head, 'run_id': rid, 'state': record['state'],
                'remote_verified': evidence.get('complete') is True,
                'artifact_sha256': record.get('artifact_sha256'),
                'formal_release_qualified': False}


def _g3r_live_evidence(root: Path) -> dict[str, Any]:
    head, _ = _identity(root)
    record = _load(root, head, 'g3r-remote')
    if record.get('state') != 'PASS_HOSTED_G1_VERIFIED':
        raise GitHubActionsError('G3R hosted Gradle gate not yet verified')
    client, slug = _client(root)
    evidence = _g3r_remote_evidence(root, client, slug, record, record['run_id'])
    if not evidence.get('complete') or evidence['artifact_sha256'] != record['artifact_sha256']:
        raise GitHubActionsError('G3R hosted Gradle gate changed since receipt')
    return evidence


def g3r_native(root: Path) -> dict[str, Any]:
    head, _ = _identity(root)
    with _lock(root, head, 'g3r'):
        hosted = _g3r_live_evidence(root)
        archive = generate_native(root, _state_dir(root)/'native', run_tests=True,
                                  native_gates=True, remote_android_g1=hosted)
        result = verify_native(archive, root, require_native=True)
        if (result['source_commit'] != head or result['status'] != 'PASS_CURRENT_SOURCE_AND_NATIVE_LANES'
            or result.get('hosted_g1_run_id') != hosted['run_id'] or
            result.get('hosted_g1_receipt_sha256') != hashlib.sha256(
                (json.dumps(hosted['receipt'], sort_keys=True, indent=2)+'\n').encode()).hexdigest()):
            raise GitHubActionsError('Native 13-lane/hosted-source verification did not pass')
        record = {'schema':1,'kind':'g3r','repo_root':str(root.resolve()), 'source_sha':head,
                  'candidate':str(archive),'status':'PASS_G3R_VERIFIED',
                  'candidate_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
                  'receipt':result,'formal_release_qualified':False}
        _save(root, head, 'g3r', record)
        return {'workflow':'G3R','candidate':str(archive),'status':record['status'],
                'source_sha':head, 'required_lanes':13,'formal_release_qualified':False}


def g3r_verify(root: Path, *, candidate: Path | None = None) -> dict[str, Any]:
    head, _ = _identity(root)
    with _lock(root, head, 'g3r'):
        try:
            existing = _load(root, head, 'g3r')
        except GitHubActionsError:
            existing = None
        archive = candidate if candidate is not None else (Path(existing['candidate']) if existing else None)
        if archive is None:
            raise GitHubActionsError('Missing G3R candidate; specify DEVTOOL_G3R_CANDIDATE or run g3r-native')
        result = verify_native(archive, root, require_native=True)
        hosted = _g3r_live_evidence(root)
        if (result['source_commit'] != head or result['status'] != 'PASS_CURRENT_SOURCE_AND_NATIVE_LANES'
            or result.get('hosted_g1_run_id') != hosted['run_id'] or
            result.get('hosted_g1_receipt_sha256') != hashlib.sha256(
                (json.dumps(hosted['receipt'], sort_keys=True, indent=2)+'\n').encode()).hexdigest()):
            raise GitHubActionsError('G3R exact-current-source 13-lane/hosted verification failed')
        record = {'schema':1,'kind':'g3r','repo_root':str(root.resolve()),'source_sha':head,
                  'candidate':str(archive.resolve()),'status':'PASS_G3R_VERIFIED',
                  'candidate_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
                  'receipt':result,'formal_release_qualified':False}
        _save(root, head, 'g3r', record)
        return {'workflow':'G3R','status':record['status'], 'candidate':str(archive),
                'source_sha':head,'required_lanes':result['required_lanes'],
                'passed_lanes':result['passed_lanes'], 'formal_release_qualified':False}


def qualify_status(root: Path) -> dict[str, Any]:
    head, _ = _identity(root)
    state = {'schema':1,'workflow':'SOURCE_BOUND_QUALIFICATION','source_sha':head,
             'ci24':'NOT_VERIFIED','g3r':'NOT_VERIFIED', 'g3r_remote':'NOT_VERIFIED',
             'automated_milestone_verified':False,'formal_release_qualified':False,
             'open_real_substrate_obligations':list(G3R_REAL_SUBSTRATE_CARRY)}
    for kind in ('ci24','g3r','g3r-remote'):
        try:
            receipt = _load(root, head, kind)
        except GitHubActionsError:
            continue
        state[kind.replace('-', '_')] = receipt.get('status') if kind == 'g3r' else receipt.get('state')
        state[kind.replace('-', '_')+'_last_recorded_only'] = True
        if kind == 'ci24':
            rid = receipt.get('run_id')
            state['ci24_run_id'] = rid if type(rid) is int and rid > 0 else None
            state['ci24_run_correlated'] = receipt.get('run_correlated') is True
            latest = receipt.get('last_observation')
            if isinstance(latest, dict) and re.fullmatch(r'[a-f0-9]{32}', str(latest.get('id') or '')) and latest.get('status') in ('FAILED', 'PENDING', 'VERIFIED'):
                error = latest.get('error_code')
                state['ci24_last_observation'] = {'id': latest['id'], 'status': latest['status'],
                    'error_code': error if isinstance(error, str) and re.fullmatch(r'CI24_[A-Z0-9_]{1,70}', error) else None,
                    'run_id': latest.get('run_id') if type(latest.get('run_id')) is int and latest['run_id'] > 0 else None}
    state['automated_milestone_last_recorded'] = (state['ci24'] == 'PASS_CI24_VERIFIED'
                                                   and state['g3r'] == 'PASS_G3R_VERIFIED')
    return state


def _fs03_strict_in_process(root: Path) -> dict[str, Any]:
    """Execute the same deterministic FS03 auditor used by Devtool's CLI.

    Do not spawn ``python -m devtool`` from inside an active Devtool invoke.
    Native Termux can stall on nested frontend initialization; a process
    timeout used to hide the underlying seal output.  This is NOT a cached
    verdict or a weakened substitute for the canonical audit.
    """
    last_check = monotonic()
    def on_check(identifier: str) -> None:
        nonlocal last_check
        current = monotonic()
        _qualification_progress('fs03.' + identifier, 'complete', current - last_check)
        last_check = current

    try:
        report = final_seal_report(root, on_check=on_check)
    except Exception as exc:
        raise GitHubActionsError(
            f'FS03 final-seal gate failed to execute: {type(exc).__name__}: {exc}'
        ) from None
    if not isinstance(report, dict):
        raise GitHubActionsError('FS03 final-seal returned an invalid report')
    summary = report.get('summary')
    checks = report.get('checks')
    if not isinstance(summary, dict) or not isinstance(checks, list):
        raise GitHubActionsError('FS03 final-seal returned incomplete report/checks')
    if any(not isinstance(item, dict) for item in checks):
        raise GitHubActionsError('FS03 final-seal returned malformed checks')
    ids = [item.get('id') for item in checks]
    failures = [str(item.get('id')) for item in checks if item.get('status') != 'pass']
    valid = (
        report.get('schema_version') == FINAL_SEAL_SCHEMA_VERSION
        and report.get('gate_scope') == 'repository_contracts_only'
        and report.get('repo_root') == str(root.resolve())
        and report.get('formal_release_qualified') is False
        and report.get('status') == 'pass'
        and len(checks) >= len(FS03_REQUIRED_CHECKS)
        and all(isinstance(ident, str) for ident in ids)
        and len(ids) == len(set(ids))
        and FS03_REQUIRED_CHECKS.issubset(set(ids))
        and not failures
        and type(summary.get('checks')) is int
        and summary['checks'] == len(checks)
        and type(summary.get('passed')) is int
        and summary['passed'] == len(checks)
        and type(summary.get('failed')) is int
        and summary['failed'] == 0
        and type(summary.get('compatibility_entries')) is int
        and summary['compatibility_entries'] >= 24
    )
    if not valid:
        details = ','.join(failures[:8]) or 'report identity, check inventory, summary, or scope mismatch'
        raise GitHubActionsError(f'FS03 final-seal gate failed (strict): {details}')
    return {'status': 'PASS', 'checks': len(checks), 'passed': len(checks),
            'failed': 0, 'schema_version': report['schema_version']}


def _qualification_progress(stage: str, event: str, elapsed: float | None = None) -> None:
    # stderr is consumed by Devtool's live command output. This is observability,
    # never proof or a substitute for machine receipts.
    suffix = f' ({elapsed:.3f}s)' if elapsed is not None else ''
    print(f'[QUALIFICATION] {stage}: {event}{suffix}', file=sys.stderr, flush=True)


def qualify_seal(root: Path, *, client: GitHubActionsClient | None = None,
                 slug: str | None = None) -> dict[str, Any]:
    # Every gate still revalidates live evidence. No cached-success shortcut.
    started = monotonic()
    durations: dict[str, float] = {}
    def stage(name: str, operation):
        began = monotonic()
        _qualification_progress(name, 'starting')
        try:
            result = operation()
        except Exception:
            _qualification_progress(name, 'blocked', monotonic() - began)
            raise
        durations[name] = round(monotonic() - began, 3)
        _qualification_progress(name, 'complete', durations[name])
        return result

    ci = stage('ci24', lambda: ci24_resume(root, client=client, slug=slug))
    if not ci.get('ci_verified'):
        raise GitHubActionsError('CI24 is incomplete; automated seal blocked')
    native = stage('g3r', lambda: g3r_verify(root))
    if native['status'] != 'PASS_G3R_VERIFIED':
        raise GitHubActionsError('G3R native verification blocked')
    fs03 = stage('fs03', lambda: _fs03_strict_in_process(root))
    head, _ = _identity(root)
    durations['total'] = round(monotonic() - started, 3)
    return {'schema':1,'workflow':'SOURCE_BOUND_QUALIFICATION','status':'PASS_AUTOMATED_MILESTONE_ONLY',
            'source_sha':head,'ci24_run_id':ci['run_id'], 'ci24_shards':24,
            'g3r_lanes':13,'fs03_final_seal':'PASS', 'fs03_evidence':fs03,
            'stage_durations_seconds':durations, 'automated_milestone_verified':True,
            'formal_release_qualified':False,
            'open_real_substrate_obligations':list(G3R_REAL_SUBSTRATE_CARRY)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('command', choices=('ci24-start','ci24-resume','g3r-remote-start','g3r-remote-resume', 'g3r-native','g3r-verify','qualify-status','qualify-seal'))
    ap.add_argument('--repo', type=Path, default=_root())
    ap.add_argument('--auth', choices=('auto','app'), default='auto', help='CI24 native operation-scoped authentication')
    ap.add_argument('--run-id', type=int, default=None, help='Explicit CI run identity (never guessed)')
    ap.add_argument('--candidate',type=Path,default=None,help='G3R evidence archive')
    args = ap.parse_args(argv)
    root = args.repo.resolve()
    run_id = args.run_id
    if run_id is None and os.getenv('DEVTOOL_CI24_RUN_ID'):
        try: run_id = int(os.environ['DEVTOOL_CI24_RUN_ID'])
        except ValueError: ap.error('DEVTOOL_CI24_RUN_ID must be an integer')
    candidate = args.candidate or (Path(os.environ['DEVTOOL_G3R_CANDIDATE'])
                                   if os.environ.get('DEVTOOL_G3R_CANDIDATE') else None)
    try:
        kwargs={}
        if args.auth=='app':
            if args.command not in ('ci24-start','ci24-resume','qualify-status'):
                raise GitHubActionsError('App authentication is admitted here only for CI24')
            if args.command!='qualify-status':
                from .github_app import make_app_client
                slug=resolve_repository('',root)
                kwargs={'client':make_app_client(slug,'actions_dispatch' if args.command=='ci24-start' else 'actions_read'),'slug':slug}
        if args.command == 'ci24-start': output = ci24_start(root, **kwargs)
        elif args.command == 'ci24-resume': output = ci24_resume(root, explicit_run_id=run_id, **kwargs)
        elif args.command == 'g3r-remote-start': output = g3r_remote_start(root)
        elif args.command == 'g3r-remote-resume': output = g3r_remote_resume(root, explicit_run_id=run_id)
        elif args.command == 'g3r-native': output = g3r_native(root)
        elif args.command == 'g3r-verify': output = g3r_verify(root, candidate=candidate)
        elif args.command == 'qualify-status': output = qualify_status(root)
        else: output = qualify_seal(root)
        print(json.dumps(output, sort_keys=True, indent=2))
        # The observation command succeeded even when the *remote qualification*
        # is pending. Keep ci_verified=false and seal blocked in machine output.
        return 0
    except (GitHubActionsError, OSError, ValueError, RuntimeError) as exc:
        print(f'Qualification workflow blocked: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
