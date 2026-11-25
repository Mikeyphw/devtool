"""GA-00 release-signing environment protection; read-only, fail-closed.

Environment names alone do NOT establish GitHub approval. Repository-wide
secrets are not environment secrets and must be separately retired by owner.
"""
from __future__ import annotations

import argparse
import json
from typing import Any, Mapping

SIGNING_ENVIRONMENT = 'devtool-android-signing'
APPROVAL_PROFILES = ('multi-reviewer', 'single-owner')


def personal_owner_id(metadata: Any, repository: str) -> int | None:
    if not isinstance(metadata, dict) or not isinstance(metadata.get('full_name'), str) or metadata['full_name'].casefold()!=repository.casefold():
        return None
    owner=metadata.get('owner')
    if (not isinstance(owner,dict) or owner.get('type')!='User' or
            not isinstance(owner.get('login'),str) or owner['login'].casefold()!=repository.split('/',1)[0].casefold() or
            type(owner.get('id')) is not int or owner['id']<1):
        return None
    return owner['id']


def evaluate_environment(raw: Any, *, approval_profile: str = "multi-reviewer", owner_id: int | None = None) -> dict[str, Any]:
    """Evaluate actual GitHub environment metadata without claiming deployment approval."""
    failures: list[str] = []
    if not isinstance(raw, dict) or raw.get('name') != SIGNING_ENVIRONMENT:
        return {'verified': False, 'status': 'INCONCLUSIVE',
                'reasons': ['ENVIRONMENT_IDENTITY_UNVERIFIED'], 'environment': SIGNING_ENVIRONMENT}
    if approval_profile not in APPROVAL_PROFILES:
        return {'verified':False,'status':'NOT_QUALIFIED','reasons':['UNKNOWN_APPROVAL_PROFILE'],'environment':SIGNING_ENVIRONMENT}
    protection=raw.get('protection_rules')
    reviewers=[];self_review=None
    rules=[rule for rule in protection if isinstance(rule,dict) and rule.get('type')=='required_reviewers'] if isinstance(protection,list) else []
    if len(rules)!=1:
        failures.append('REQUIRED_REVIEWER_RULE_NOT_PROVEN')
    else:
        rule=rules[0];selected=rule.get('reviewers');self_review=rule.get('prevent_self_review')
        if isinstance(selected,list) and selected and all(isinstance(p,dict) and p.get('type') in ('User','Team') and
             isinstance(p.get('reviewer'),dict) and type(p['reviewer'].get('id')) is int and p['reviewer']['id']>0 for p in selected):
            reviewers=selected
    if not reviewers or len({(p['type'],p['reviewer']['id']) for p in reviewers})!=len(reviewers):
        failures.append('REQUIRED_REVIEWERS_NOT_PROVEN')
    if approval_profile=='multi-reviewer':
        if self_review is not True: failures.append('SELF_REVIEW_RESTRICTION_NOT_PROVEN')
    else:
        # An explicitly selected personal-repository profile is not a generic
        # bypass: require exactly the independently observed repository owner.
        if type(owner_id) is not int or owner_id<1 or len(reviewers)!=1 or reviewers[0]['type']!='User' or reviewers[0]['reviewer']['id']!=owner_id:
            failures.append('SINGLE_OWNER_REVIEWER_IDENTITY_NOT_PROVEN')
        if self_review is not False:
            failures.append('SINGLE_OWNER_SELF_REVIEW_POLICY_NOT_PROVEN')
    policy = raw.get('deployment_branch_policy')
    branch_protected = (isinstance(policy, Mapping) and
                        policy.get('protected_branches') is True and
                        policy.get('custom_branch_policies') is False)
    if not branch_protected:
        failures.append('PROTECTED_BRANCH_DEPLOYMENT_POLICY_NOT_PROVEN')
    if raw.get('can_admins_bypass') is not False:
        failures.append('ADMIN_BYPASS_RESTRICTION_NOT_PROVEN')
    return {'environment': SIGNING_ENVIRONMENT,
            'verified': not failures,
            'environment_id': raw.get('id') if type(raw.get('id')) is int and raw['id'] > 0 else None,
            'status': 'VERIFIED_CONFIGURATION' if not failures else 'NOT_QUALIFIED',
            'required_reviewer_count': len(reviewers),
            'approval_profile':approval_profile,
            'owner_reviewer_id':owner_id if approval_profile=='single-owner' else None,
            'self_review_prevented':self_review is True,
            'self_review_policy_verified': self_review is (approval_profile=='multi-reviewer'),
            'protected_branches_only': branch_protected,
            'admin_bypass_disabled': raw.get('can_admins_bypass') is False,
            'reasons': failures,
            'deployment_approved': False,
            'secret_values_verified': False}


def inspect_environment(client: Any, repository: str, *, approval_profile: str = "multi-reviewer", expected_owner_id: int | None = None) -> dict[str, Any]:
    """Only a GitHub GET; unavailable metadata is not approval."""
    try:
        from .github_actions import _slug
        slug = _slug(repository)
        metadata = client._get('/repos/' + slug + '/environments/' + SIGNING_ENVIRONMENT)
        owner_id=None
        if approval_profile=='single-owner':
            owner_id=personal_owner_id(client.repository(slug),slug)
            if expected_owner_id is not None and owner_id!=expected_owner_id:
                return {'environment':SIGNING_ENVIRONMENT,'verified':False,'status':'NOT_QUALIFIED','reasons':['PINNED_OWNER_IDENTITY_CHANGED']}
        return evaluate_environment(metadata,approval_profile=approval_profile,owner_id=owner_id)
    except (Exception,):
        # Transport/provider exceptions may embed credentials. Never echo them.
        return {'environment': SIGNING_ENVIRONMENT, 'verified': False,
                'status': 'INCONCLUSIVE', 'reasons': ['GITHUB_ENVIRONMENT_GET_UNAVAILABLE'],
                'deployment_approved': False, 'secret_values_verified': False}


def verify_single_owner_approval(client: Any, repository: str, *, run_id: int, run_attempt: int,
                                 owner_id: int, environment_id: int | None) -> dict[str, Any]:
    """Verify real review history, without approving or replaying a deployment.

    GitHub's approvals endpoint is run-scoped, not attempt-scoped. Only the
    first attempt is unambiguous; do not promote reruns with older approvals.
    """
    from .github_actions import API_ORIGIN, GitHubActionsError, _slug
    slug = _slug(repository)
    if (type(run_id) is not int or run_id < 1 or type(run_attempt) is not int or run_attempt != 1
            or type(owner_id) is not int or owner_id < 1
            or type(environment_id) is not int or environment_id < 1):
        raise GitHubActionsError('Single-owner approval requires exact environment and first run attempt')
    rows, headers = client._request(f'{API_ORIGIN}/repos/{slug}/actions/runs/{run_id}/approvals', expect_list=True)
    if not isinstance(rows, list) or any(str(k).casefold() == 'link' and v for k, v in headers.items()):
        raise GitHubActionsError('Single-owner approval history is incomplete')
    matched = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('environments'), list):
            raise GitHubActionsError('Single-owner approval history is malformed')
        for env in row['environments']:
            if not isinstance(env, dict) or type(env.get('id')) is not int or not isinstance(env.get('name'), str):
                raise GitHubActionsError('Single-owner approval environment identity is malformed')
            if env['id'] == environment_id or env['name'] == SIGNING_ENVIRONMENT:
                if env['id'] != environment_id or env['name'] != SIGNING_ENVIRONMENT:
                    raise GitHubActionsError('Single-owner approval environment identity differs')
                matched.append(row)
    if len(matched) != 1:
        raise GitHubActionsError('Single-owner deployment approval is missing or ambiguous')
    review = matched[0]; actor = review.get('user')
    if (review.get('state') != 'approved' or not isinstance(actor, dict)
            or actor.get('type') != 'User' or type(actor.get('id')) is not int
            or actor['id'] != owner_id or not isinstance(actor.get('login'), str)
            or actor['login'].casefold() != slug.split('/', 1)[0].casefold()):
        raise GitHubActionsError('Single-owner deployment approval was not verified from the pinned owner')
    return {'deployment_approved': True, 'run_id': run_id, 'run_attempt': run_attempt,
            'environment': SIGNING_ENVIRONMENT, 'environment_id': environment_id, 'owner_reviewer_id': owner_id}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Read-only release environment policy verification')
    parser.add_argument('--repo', required=True, help='GitHub owner/repository')
    parser.add_argument('--approval-profile',choices=APPROVAL_PROFILES,default='multi-reviewer')
    args = parser.parse_args(argv)
    from .actions_setup import _gh
    from .github_actions import _slug
    slug = _slug(args.repo)
    try:
        obj = json.loads(_gh(['api', '--method', 'GET', 'repos/' + slug + '/environments/' + SIGNING_ENVIRONMENT]))
        owner_id=None
        if args.approval_profile=='single-owner':
            metadata=json.loads(_gh(['api','--method','GET','repos/'+slug]))
            owner_id=personal_owner_id(metadata,slug)
        result = evaluate_environment(obj,approval_profile=args.approval_profile,owner_id=owner_id)
    except (ValueError, OSError, Exception):
        result = {'environment': SIGNING_ENVIRONMENT, 'verified': False,
                  'status': 'INCONCLUSIVE', 'reasons': ['LIVE_ENVIRONMENT_UNAVAILABLE']}
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result['verified'] else 3


if __name__ == '__main__':
    raise SystemExit(main())
