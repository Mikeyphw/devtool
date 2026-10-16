"""Source-bound CI24 / native G3R dogfood workflows (QW01).

Each operation is a declared Devtool wrapper workflow and delegates to the
existing ACTIONS-02/03/04 client and G3R v3 producer/independent verifier.
No alternate scheduler, run guessing, release promotion, or background job.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from time import monotonic
from typing import Any, Iterator
from zipfile import ZipFile

from .actions_dispatch import _durable_record, dispatch_workflow, GitHubDispatchPreflightError
from .actions_pipeline import _source, _git
from .actions_runs import _normalize_run
from .actions_artifacts import artifact_list, download_artifact, verify_download
from .g3r_evidence_gate import evaluate as verify_native
from .final_seal import final_seal_report, FINAL_SEAL_SCHEMA_VERSION
from .g3r_evidence_generate import generate as generate_native
from .github_actions import GitHubActionsClient, GitHubActionsError, resolve_repository, resolve_token
from .remed_r04b_fixed_point import G3R_REAL_SUBSTRATE_CARRY

WORKFLOW = 'devtool-full-repository-pytest.yml'
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


def _state_dir(root: Path) -> Path:
    basis = Path(os.environ.get('XDG_STATE_HOME') or Path.home()/'.local/state')
    return basis/'devtool'/'qualification-workflows'/hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:20]


def _state_path(root: Path, sha: str, kind: str) -> Path:
    if not SHA.fullmatch(sha) or kind not in ('ci24', 'g3r'):
        raise GitHubActionsError('Invalid qualification record identity')
    return _state_dir(root)/f'{sha}-{kind}.json'


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
    parent = _state_dir(root)
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
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


def _ci24_evaluate(client: GitHubActionsClient, slug: str, root: Path, record: dict[str, Any],
                   identity: int) -> dict[str, Any]:
    if type(identity) is not int or identity <= 0:
        raise GitHubActionsError('An explicit positive GitHub run ID is required')
    run = _normalize_run(client.get_run(slug, identity), slug, identity)
    # Always rebind to remote source and workflow, even if the dispatch was uncorrelated.
    if run.get('head_sha') != record['source_sha'] or run.get('head_branch') != record['branch']:
        raise GitHubActionsError('GitHub run does not match exact source SHA/branch')
    if (run.get('event') != 'workflow_dispatch' or type(record.get('workflow_id')) is not int
        or run.get('workflow_id') != record['workflow_id']):
        raise GitHubActionsError('GitHub workflow/event identity mismatch')
    if run['status'] != 'completed':
        return {'status': 'WAITING_REMOTE', 'run_id': identity, 'complete': False}
    if run['conclusion'] != 'success':
        raise GitHubActionsError('GitHub 24-shard run failed: '+str(run['conclusion']))
    # Fail closed on re-run ambiguity: the artifacts API can omit run_attempt.
    if run.get('run_attempt') != 1:
        raise GitHubActionsError('Rerun attempt requires an attempt-bound evidence contract; original attempt only')
    jobs = client.run_attempt_jobs(slug, identity, 1)
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
    catalog = artifact_list(client, slug, identity)['artifacts']
    names = [a['name'] for a in catalog]
    if len(catalog) != 25 or set(names) != EXPECTED_ARTIFACTS:
        raise GitHubActionsError('CI24 artifact set does not contain exactly 24 shards and one summary')
    if not all(re.fullmatch(r'sha256:[a-fA-F0-9]{64}', str(a.get('digest') or '')) for a in catalog):
        raise GitHubActionsError('CI24 artifact missing authenticated archive SHA256')
    summary_artifact = next(a for a in catalog if a['name'] == 'devtool-full-pytest-summary')
    try:
        registered = verify_download(client, slug, identity, summary_artifact['id'], root)
    except GitHubActionsError as exc:
        if 'no registered download' not in str(exc).lower():
            raise
        download_artifact(client, slug, identity, summary_artifact['id'], root)
        registered = verify_download(client, slug, identity, summary_artifact['id'], root)
    if not registered.get('archive_verified'):
        raise GitHubActionsError('CI summary archive integrity not proven')
    path = Path(registered['file'])
    with ZipFile(path) as z:
        if 'summary.json' not in z.namelist() or z.namelist().count('summary.json') != 1:
            raise GitHubActionsError('CI summary.json absent or ambiguous')
        with z.open('summary.json') as f:
            data = json.loads(f.read(128 * 1024 + 1))
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
    return {'status': 'PASS_CI24_VERIFIED', 'run_id': identity,
            'run_attempt': 1, 'jobs_verified': 25, 'shards_verified': 24,
            'artifact_metadata_verified': 25, 'summary_archives_verified': 1,
            'summary_archive_sha256': registered['sha256'],
            'summary_json_sha256': hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest(),
            'tests': data['tests'], 'modules': data['total_modules'], 'complete': True}


def ci24_resume(root: Path, *, explicit_run_id: int | None = None,
                client: GitHubActionsClient | None = None, slug: str | None = None) -> dict[str, Any]:
    head, _ = _identity(root)
    with _lock(root, head, 'ci24'):
        record = _load(root, head, 'ci24')
        if record.get('state') == 'DISPATCH_OUTCOME_UNKNOWN' and not record.get('workflow_id'):
            raise GitHubActionsError('Dispatch outcome unknown; inspect ACTIONS-02 receipt before trying another run')
        if client is None or slug is None:
            client, slug = _client(root)
        if record.get('repository', '').lower() != slug.lower():
            raise GitHubActionsError('CI receipt repository changed')
        original = record.get('run_id')
        if explicit_run_id is not None and original is not None and int(explicit_run_id) != original:
            raise GitHubActionsError('Run ID contradicts correlated dispatch receipt')
        rid = original if original is not None else explicit_run_id
        if rid is None:
            raise GitHubActionsError('GitHub 204 dispatch was uncorrelated; specify DEVTOOL_CI24_RUN_ID explicitly; do not guess or redispatch')
        # Existing explicitly selected run may constitute CI evidence even without
        # proof of which HTTP 204 request triggered it; never call it correlated.
        outcome = _ci24_evaluate(client, slug, root, record, rid)
        record.update(state=outcome['status'], run_id=rid,
                      run_correlated=bool(record.get('run_correlated')),
                      selected_uncorrelated=not bool(record.get('run_correlated')),
                      verification=outcome)
        _save(root, head, 'ci24', record)
        return {**_public_ci(record), 'verification': outcome,
                'observation_completed': True, 'qualification_complete': bool(outcome.get('complete')),
                'qualification_status': ('verified' if outcome.get('complete') else 'pending')}


def g3r_native(root: Path) -> dict[str, Any]:
    head, _ = _identity(root)
    with _lock(root, head, 'g3r'):
        archive = generate_native(root, _state_dir(root)/'native', run_tests=True, native_gates=True)
        result = verify_native(archive, root, require_native=True)
        if result['source_commit'] != head or result['status'] != 'PASS_CURRENT_SOURCE_AND_NATIVE_LANES':
            raise GitHubActionsError('Native 13-lane verification did not pass')
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
        if result['source_commit'] != head or result['status'] != 'PASS_CURRENT_SOURCE_AND_NATIVE_LANES':
            raise GitHubActionsError('G3R exact-current-source 13-lane verification failed')
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
             'ci24':'NOT_VERIFIED','g3r':'NOT_VERIFIED',
             'automated_milestone_verified':False,'formal_release_qualified':False,
             'open_real_substrate_obligations':list(G3R_REAL_SUBSTRATE_CARRY)}
    for kind in ('ci24','g3r'):
        try:
            receipt = _load(root, head, kind)
        except GitHubActionsError:
            continue
        state[kind] = receipt.get('state') if kind == 'ci24' else receipt.get('status')
        state[kind+'_last_recorded_only'] = True
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
    try:
        report = final_seal_report(root)
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
    ap.add_argument('command', choices=('ci24-start','ci24-resume','g3r-native','g3r-verify','qualify-status','qualify-seal'))
    ap.add_argument('--repo', type=Path, default=_root())
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
        if args.command == 'ci24-start': output = ci24_start(root)
        elif args.command == 'ci24-resume': output = ci24_resume(root, explicit_run_id=run_id)
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
