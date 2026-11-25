"""B3: live source-bound, non-mutating delivery qualification scopes.

Required PR CI remains under delivery_ci_policy and delivery_ci_evidence;
manual 24-shard regression remains under the independent Actions dispatch
journal. This module projects the two receipts but never manufactures either.
"""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Mapping

from .actions_dispatch import dispatch_resume_decision
from .delivery_ci_evidence import verify_exact_attempts
from .delivery_ci_policy import (load_policy, assert_observation,
                                 DeliveryCIPolicyError)
from .delivery_github import _ci_candidate, _pr_match, _pr_merge_sha, _remote_head
from .delivery_session import DeliverySessionError, status
from .github_actions import GitHubActionsClient, GitHubActionsError, resolve_token

_SHARD = re.compile(r'Python 3\.13 / shard ([1-9]|1[0-9]|2[0-4])/24\Z')
_SUMMARY = 'Summarize all 24 shards (fail closed)'
_WORKFLOW_FILE = 'devtool-full-repository-pytest.yml'
_WORKFLOW_PATH = '.github/workflows/' + _WORKFLOW_FILE
_WORKFLOW_NAME = 'Devtool Full Python Regression (24 Shards)'
_ARTIFACT_SUMMARY = 'devtool-full-pytest-summary'



def _live_pr(client: Any, item: Mapping[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Prove PR head/fork/upstream identity and current source without mutation."""
    head_repo = item.get('head_repository') or item['repository']
    base_repo = item.get('base_repository') or item['repository']
    if head_repo.casefold() != base_repo.casefold() and not item.get('base_sha'):
        raise DeliverySessionError('Fork qualification requires pinned upstream base SHA')
    if _remote_head(client, head_repo, item['head']) != item['head_sha']:
        raise DeliverySessionError('Provider head SHA drift; qualification blocked')
    pr = item.get('pr')
    if not isinstance(pr,dict) or type(pr.get('number')) is not int:
        raise DeliverySessionError('No saved source-bound PR identity')
    live = client._get(f"/repos/{base_repo}/pulls/{pr['number']}")
    matched = _pr_match(live, base_repo, item['head'], item['base'], item['head_sha'],
                        head_repository=head_repo, base_sha=item.get('base_sha'))
    if matched is None or matched['number'] != pr['number']:
        raise DeliverySessionError('Provider PR source/base identities changed')
    return matched, _pr_merge_sha(client, base_repo, pr['number'])


def _required_ci(client: Any, item: Mapping[str, Any], live_pr: dict[str, Any],
                 merge: str | None) -> dict[str, Any]:
    ci = item.get('ci')
    if not isinstance(ci, dict):
        return {'state':'NOT_OBSERVED','passed':False,'exact_attempts':[], 'scope':'required_delivery_ci'}
    if ci.get('source_sha') != item['head_sha'] or ci.get('state') != 'REQUIRED_CHECKS_PASSED' or ci.get('checks_qualified') is not True:
        return {'state':str(ci.get('state') or 'INVALID'), 'passed':False,
                'exact_attempts':[],'scope':'required_delivery_ci'}
    try:
        base_repo = item.get('base_repository') or item['repository']
        head_repo = item.get('head_repository') or item['repository']
        if base_repo.casefold() == head_repo.casefold():
            policy = load_policy(Path(item['repo_root']),
                                 expected_repository=base_repo,expected_base=item['base'])
        else:
            from .delivery_ci_policy import load_upstream_policy
            policy, pinned_base = load_upstream_policy(client,
                expected_repository=base_repo,expected_base=item['base'],
                expected_base_sha=item.get('base_sha'))
        assert_observation(ci, policy)
        source = {**item,'repository':policy.repository}
        evidence = verify_exact_attempts(
            client, policy.repository, ci['runs'], policy.workflow_ids,
            validate_run=lambda raw: _ci_candidate(raw,source,live_pr,merge))
    except (DeliveryCIPolicyError, DeliverySessionError, GitHubActionsError, OSError, ValueError, KeyError):
        return {'state':'REVERIFICATION_BLOCKED','passed':False,'exact_attempts':[],
                'scope':'required_delivery_ci'}
    if base_repo.casefold() != head_repo.casefold():
        try:
            from .delivery_ci_policy import load_upstream_policy
            _, final_base = load_upstream_policy(client,
                expected_repository=base_repo,expected_base=item['base'],
                expected_base_sha=pinned_base)
            if final_base != pinned_base:
                raise DeliverySessionError('Upstream base drifted during CI inspection')
        except (DeliverySessionError, DeliveryCIPolicyError, GitHubActionsError, OSError):
            return {'state':'BASE_DRIFT','passed':False,'exact_attempts':[],
                    'scope':'required_delivery_ci'}
    if not evidence.get('verified'):
        return {'state':'EXACT_ATTEMPT_PENDING','passed':False,'exact_attempts':evidence.get('issues',[]),
                'scope':'required_delivery_ci'}
    return {'state':'PASSED','passed':True,'exact_attempts':evidence.get('runs',[]),
            'policy_sha256':policy.sha256,'scope':'required_delivery_ci'}


def _regression(client: Any, item: Mapping[str, Any], request_id: str,
                *, state_root: Path | None = None) -> dict[str, Any]:
    if not request_id:
        return {'state':'NOT_OBSERVED','passed':False,'scope':'repository_regression'}
    try:
        journal = dispatch_resume_decision(request_id, state_root=state_root)
    except (GitHubActionsError, DeliverySessionError, OSError, ValueError):
        return {'state':'DISPATCH_UNCORRELATED','passed':False,
                'scope':'repository_regression'}
    if journal.get('decision') != 'EXISTING_CORRELATED':
        return {'state':'DISPATCH_UNCORRELATED','passed':False,
                'scope':'repository_regression','request_id':request_id}
    repository = item.get('head_repository') or item['repository']
    if (journal.get('repository','').casefold() != repository.casefold() or
            journal.get('source_sha') != item['head_sha'] or
            journal.get('ref') != item['head'] or
            type(journal.get('workflow_id')) is not int):
        return {'state':'DISPATCH_IDENTITY_MISMATCH','passed':False,
                'scope':'repository_regression','request_id':request_id}
    rid = journal['run_id']
    # A journal and a run can agree on an arbitrary workflow ID. Require an
    # independent provider workflow lookup before accepting any 24-job result.
    try:
        workflow = client.workflow(repository, _WORKFLOW_FILE)
        if (not isinstance(workflow, dict) or
                type(workflow.get('id')) is not int or
                workflow['id'] != journal['workflow_id'] or
                workflow.get('path') != _WORKFLOW_PATH or
                workflow.get('name') != _WORKFLOW_NAME or
                workflow.get('state') != 'active'):
            return {'state':'WORKFLOW_IDENTITY_UNVERIFIED','passed':False,
                    'scope':'repository_regression','request_id':request_id}
    except (GitHubActionsError, DeliverySessionError, OSError, ValueError,
            TypeError, AttributeError):
        return {'state':'WORKFLOW_IDENTITY_UNVERIFIED','passed':False,
                'scope':'repository_regression','request_id':request_id}
    try:
        run = client.get_run(repository, rid)
        attempt = run.get('run_attempt')
        if (run.get('id') != rid or run.get('workflow_id') != journal['workflow_id'] or
                run.get('event') != 'workflow_dispatch' or
                run.get('name',_WORKFLOW_NAME) != _WORKFLOW_NAME or
                run.get('path',_WORKFLOW_PATH) != _WORKFLOW_PATH or
                run.get('head_sha') != item['head_sha'] or
                run.get('head_branch') != item['head'] or
                run.get('status') != 'completed' or run.get('conclusion') != 'success' or
                type(attempt) is not int or attempt < 1 or
                (run.get('repository') or {}).get('full_name',repository).casefold() != repository.casefold()):
            raise ValueError('Exact hosted run identity/status not qualified')
        jobs = client.run_attempt_jobs(repository,rid,attempt)
    except (GitHubActionsError, OSError, ValueError, AttributeError, TypeError):
        return {'state':'EXACT_RUN_PENDING','passed':False,'scope':'repository_regression',
                'request_id':request_id,'run_id':rid}
    if not isinstance(jobs,list) or len(jobs)!=25:
        return {'state':'INCOMPLETE_SHARD_COVERAGE','passed':False,
                'scope':'repository_regression','request_id':request_id,'run_id':rid,
                'observed_jobs':len(jobs) if isinstance(jobs,list) else None}
    shards: set[int] = set()
    summary_count=0
    seen_jobs: set[int] = set()
    for job in jobs:
        if not isinstance(job, dict) or type(job.get('id')) is not int or job['id'] <= 0 or job['id'] in seen_jobs:
            break
        seen_jobs.add(job['id'])
        if (job.get('run_id',rid) != rid or job.get('run_attempt',attempt) != attempt or
                job.get('status') != 'completed' or job.get('conclusion') != 'success'):
            break
        name = job.get('name')
        m = _SHARD.fullmatch(name) if isinstance(name,str) else None
        if m:
            number = int(m.group(1))
            if number in shards:
                break
            shards.add(number)
        elif name == _SUMMARY:
            summary_count += 1
        else:
            break
    else:
        if shards == set(range(1,25)) and summary_count == 1:
            # An all-green run alone is not the full-suite evidence receipt.
            # The fail-closed GitHub summary workflow consumes all 24 uploaded
            # shard artifacts; verify their existence, run owner, and bounded
            # metadata without downloading or executing the ZIP payloads.
            try:
                artifacts = client.run_artifacts(repository, rid)
            except (GitHubActionsError, DeliverySessionError, OSError,
                    ValueError, TypeError, AttributeError):
                return {'state':'ARTIFACT_EVIDENCE_UNAVAILABLE','passed':False,
                        'scope':'repository_regression','request_id':request_id,'run_id':rid}
            if not isinstance(artifacts, list) or len(artifacts) != 25:
                return {'state':'ARTIFACT_COVERAGE_INCOMPLETE','passed':False,
                        'scope':'repository_regression','request_id':request_id,'run_id':rid}
            expected = {_ARTIFACT_SUMMARY} | {
                f'devtool-full-shard-{i}' for i in range(1,25)}
            ids: set[int] = set()
            names: set[str] = set()
            for artifact in artifacts:
                if not isinstance(artifact, dict):
                    break
                artifact_id = artifact.get('id')
                name = artifact.get('name')
                run_owner = artifact.get('workflow_run') or {}
                if (type(artifact_id) is not int or artifact_id <= 0 or artifact_id in ids or
                        name not in expected or name in names or
                        artifact.get('expired') is not False or
                        type(artifact.get('size_in_bytes')) is not int or
                        artifact['size_in_bytes'] <= 0 or
                        not isinstance(run_owner,dict) or
                        run_owner.get('id') != rid):
                    break
                ids.add(artifact_id)
                names.add(name)
            else:
                if names == expected:
                    return {'state':'PASSED','passed':True,'scope':'repository_regression',
                    'request_id':request_id,'run_id':rid,'attempt':attempt,
                    'source_sha':item['head_sha'], 'shards_passed':24,
                    'jobs_passed':25,'summary_passed':True,
                    'artifacts_verified':25,'artifact_ids':sorted(ids),
                    'workflow_id':workflow['id'],'workflow_path':_WORKFLOW_PATH,
                    'url':f'https://github.com/{repository}/actions/runs/{rid}',
                    'attribution_proven':True}
            return {'state':'ARTIFACT_COVERAGE_INVALID','passed':False,
                    'scope':'repository_regression','request_id':request_id,'run_id':rid}
    return {'state':'INVALID_SHARD_RESULTS','passed':False,
            'scope':'repository_regression','request_id':request_id,'run_id':rid}


def qualify(session_id: str, *, request_id: str = '', client: Any = None,
            auth: str = 'auto', session_state_root: Path | None = None,
            dispatch_state_root: Path | None = None) -> dict[str, Any]:
    """Read-only current-head verification; no new network mutation or rerun."""
    item = status(session_id, state_root=session_state_root)
    regression = {'state':'NOT_OBSERVED','passed':False,'scope':'repository_regression'}
    required = {'state':'NOT_OBSERVED','passed':False,'scope':'required_delivery_ci'}
    reason = None
    if not item['resumable'] or item.get('source_binding') != 'matching':
        reason='local source drift or unresolved mutation intent'
    else:
        try:
            if client is None:
                if auth == 'app':
                    # Qualify in the upstream/base repository using only the
                    # read-scoped installation; never borrow fork credentials.
                    from .github_app import make_app_client
                    client = make_app_client(item.get('base_repository') or item['repository'],
                                             'checks_read')
                else:
                    token,_ = resolve_token(auth)
                    client = GitHubActionsClient(token)
            live, merge = _live_pr(client,item)
            required = _required_ci(client,item,live,merge)
            regression = _regression(client,item,request_id,state_root=dispatch_state_root)
        except (DeliverySessionError, GitHubActionsError, ValueError, TypeError, OSError):
            reason='current remote PR or head could not be verified'
    premerge = bool(not reason and required['passed'] and regression['passed'])
    return {'schema':'devtool.delivery-qualification.v1','repository':item.get('repository'),
            'base_repository':item.get('base_repository'),
            'head_repository':item.get('head_repository'),
            'head_sha':item['head_sha'], 'session_id':session_id,
            'required_delivery_ci':required, 'repository_regression':regression,
            'pre_merge_qualification':'PASSED' if premerge else 'BLOCKED_OR_PENDING',
            'overall_acceptance':'PENDING_POST_MERGE_MAIN_HEAD' if premerge else 'BLOCKED_OR_PENDING',
            'blocking_reason':reason,
            'release_qualification':'NOT_PERFORMED','device_qualification':'NOT_PERFORMED',
            'remote_mutation_performed':False,'read_only':True}
