"""GA-00 release-signing environment protection; read-only, fail-closed.

Environment names alone do NOT establish GitHub approval. Repository-wide
secrets are not environment secrets and must be separately retired by owner.
"""
from __future__ import annotations

import argparse
import json
from typing import Any, Mapping

SIGNING_ENVIRONMENT = 'devtool-android-signing'


def evaluate_environment(raw: Any) -> dict[str, Any]:
    """Evaluate actual GitHub environment metadata without claiming deployment approval."""
    failures: list[str] = []
    if not isinstance(raw, dict) or raw.get('name') != SIGNING_ENVIRONMENT:
        return {'verified': False, 'status': 'INCONCLUSIVE',
                'reasons': ['ENVIRONMENT_IDENTITY_UNVERIFIED'], 'environment': SIGNING_ENVIRONMENT}
    protection = raw.get('protection_rules')
    reviewers = []
    self_review_prevented = False
    if not isinstance(protection, list):
        failures.append('PROTECTION_RULES_UNVERIFIED')
    else:
        for rule in protection:
            if not isinstance(rule, dict) or rule.get('type') != 'required_reviewers':
                continue
            selected = rule.get('reviewers')
            if isinstance(selected, list):
                reviewers = [p for p in selected if isinstance(p, dict)
                             and p.get('type') in ('User', 'Team')
                             and isinstance(p.get('reviewer'), dict)
                             and type(p['reviewer'].get('id')) is int and p['reviewer']['id'] > 0]
            self_review_prevented = rule.get('prevent_self_review') is True
    if not reviewers:
        failures.append('REQUIRED_REVIEWERS_NOT_PROVEN')
    if not self_review_prevented:
        failures.append('SELF_REVIEW_RESTRICTION_NOT_PROVEN')
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
            'status': 'VERIFIED_CONFIGURATION' if not failures else 'NOT_QUALIFIED',
            'required_reviewer_count': len(reviewers),
            'self_review_prevented': self_review_prevented,
            'protected_branches_only': branch_protected,
            'admin_bypass_disabled': raw.get('can_admins_bypass') is False,
            'reasons': failures,
            'deployment_approved': False,
            'secret_values_verified': False}


def inspect_environment(client: Any, repository: str) -> dict[str, Any]:
    """Only a GitHub GET; unavailable metadata is not approval."""
    try:
        from .github_actions import _slug
        slug = _slug(repository)
        metadata = client._get('/repos/' + slug + '/environments/' + SIGNING_ENVIRONMENT)
        return evaluate_environment(metadata)
    except (Exception,):
        # Transport/provider exceptions may embed credentials. Never echo them.
        return {'environment': SIGNING_ENVIRONMENT, 'verified': False,
                'status': 'INCONCLUSIVE', 'reasons': ['GITHUB_ENVIRONMENT_GET_UNAVAILABLE'],
                'deployment_approved': False, 'secret_values_verified': False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Read-only release environment policy verification')
    parser.add_argument('--repo', required=True, help='GitHub owner/repository')
    args = parser.parse_args(argv)
    from .actions_setup import _gh
    from .github_actions import _slug
    slug = _slug(args.repo)
    try:
        obj = json.loads(_gh(['api', '--method', 'GET', 'repos/' + slug + '/environments/' + SIGNING_ENVIRONMENT]))
        result = evaluate_environment(obj)
    except (ValueError, OSError, Exception):
        result = {'environment': SIGNING_ENVIRONMENT, 'verified': False,
                  'status': 'INCONCLUSIVE', 'reasons': ['LIVE_ENVIRONMENT_UNAVAILABLE']}
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result['verified'] else 3


if __name__ == '__main__':
    raise SystemExit(main())
