"""GA-00: read-only governance policy comparison, not ruleset mutation.

GitHub branch rules require exact status-check contexts; Devtool's six required
workflow IDs are a different source of truth. This audit never claims they are
equivalent just because both lists are present or independently passing.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Mapping, Sequence

_REPO = re.compile(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}\Z')


class GovernanceError(ValueError):
    pass


def _repository(value: str) -> str:
    if not _REPO.fullmatch(value) or '..' in value:
        raise GovernanceError('GA00_REPOSITORY_INVALID')
    return value


def _load_json(path: Path) -> Any:
    if not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise GovernanceError('GA00_POLICY_OR_SNAPSHOT_UNAVAILABLE')
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, UnicodeError, OSError):
        raise GovernanceError('GA00_POLICY_OR_SNAPSHOT_INVALID') from None


def _gh_get(path: str) -> Any:
    gh = shutil.which('gh')
    if not gh:
        raise GovernanceError('GA00_GH_UNAVAILABLE')
    try:
        p = subprocess.run([gh, 'api', '--method', 'GET', path],
                           capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise GovernanceError('GA00_GITHUB_UNAVAILABLE') from None
    if p.returncode or len(p.stdout) > 1024 * 1024:
        raise GovernanceError('GA00_GITHUB_POLICY_UNVERIFIED')
    try:
        return json.loads(p.stdout)
    except ValueError:
        raise GovernanceError('GA00_GITHUB_POLICY_MALFORMED') from None


def live_rulesets(repository: str) -> list[dict[str, Any]]:
    slug = _repository(repository)
    listing = _gh_get(f'repos/{slug}/rulesets?includes_parents=false')
    if not isinstance(listing, list):
        raise GovernanceError('GA00_GITHUB_RULESET_LIST_INVALID')
    if len(listing) > 100:
        raise GovernanceError('GA00_GITHUB_RULESET_LIST_TOO_LARGE')
    result = []
    for item in listing:
        if not isinstance(item, dict) or type(item.get('id')) is not int or item['id'] <= 0:
            raise GovernanceError('GA00_GITHUB_RULESET_ID_INVALID')
        full = _gh_get(f'repos/{slug}/rulesets/{item["id"]}')
        if not isinstance(full, dict) or full.get('id') != item['id']:
            raise GovernanceError('GA00_GITHUB_RULESET_ID_DRIFT')
        result.append(full)
    return result


def audit(policy: Mapping[str, Any], rulesets: Sequence[Mapping[str, Any]], *,
          repository: str) -> dict[str, Any]:
    slug = _repository(repository)
    if policy.get('repository', '').casefold() != slug.casefold():
        raise GovernanceError('GA00_REQUIRED_CI_REPOSITORY_MISMATCH')
    branch = policy.get('base_branch')
    workflows = policy.get('required_workflows')
    if (not isinstance(branch, str) or not re.fullmatch('[A-Za-z0-9_.-]{1,100}', branch)
            or not isinstance(workflows, list) or not workflows
            or any(not isinstance(x, dict) or type(x.get('workflow_id')) is not int
                   or x['workflow_id'] <= 0 or not isinstance(x.get('name'), str)
                   or not x['name'] for x in workflows)):
        raise GovernanceError('GA00_REQUIRED_CI_POLICY_INVALID')
    all_contexts = set()
    applicable = []
    reviews = []
    uncertain = False
    for ruleset in rulesets:
        if not isinstance(ruleset, Mapping):
            raise GovernanceError('GA00_RULESET_SHAPE_INVALID')
        if ruleset.get('target') != 'branch' or ruleset.get('enforcement') != 'active':
            continue
        conditions = ruleset.get('conditions')
        patterns = (conditions or {}).get('ref_name') if isinstance(conditions, dict) else None
        if not isinstance(patterns, dict):
            uncertain = True
            continue
        include = patterns.get('include', [])
        exclude = patterns.get('exclude', [])
        exact = 'refs/heads/' + branch
        if not isinstance(include, list) or not isinstance(exclude, list):
            uncertain = True
            continue
        # Glob patterns, ~DEFAULT_BRANCH or other predicates need GitHub's
        # evaluator; don't infer that they definitely apply or don't apply.
        if any(x.startswith('~') or '*' in x or '?' in x for x in include + exclude if isinstance(x, str)):
            uncertain = True
            continue
        if exact not in include or exact in exclude:
            continue
        rid = ruleset.get('id')
        if type(rid) is not int:
            uncertain = True
            continue
        applicable.append(rid)
        rows = ruleset.get('rules')
        if not isinstance(rows, list):
            uncertain = True
            continue
        for rule in rows:
            if not isinstance(rule, dict):
                uncertain = True
                continue
            params = rule.get('parameters')
            if rule.get('type') == 'required_status_checks':
                checks = (params or {}).get('required_status_checks') if isinstance(params, dict) else None
                if not isinstance(checks, list):
                    uncertain = True
                else:
                    all_contexts.update(str(x.get('context')) for x in checks
                                        if isinstance(x, dict) and isinstance(x.get('context'), str))
            if rule.get('type') == 'pull_request':
                number = (params or {}).get('required_approving_review_count') if isinstance(params, dict) else None
                if type(number) is int and number >= 0:
                    reviews.append(number)
                else:
                    uncertain = True
    return {
        'schema': 'devtool.ga00.governance-audit.v1',
        'repository': slug, 'branch': branch,
        'required_workflows': [{'name': x['name'], 'workflow_id': x['workflow_id']} for x in workflows],
        'applicable_exact_ruleset_ids': sorted(applicable),
        'observed_required_status_contexts': sorted(all_contexts),
        'minimum_approving_reviews_observed': max(reviews) if reviews else None,
        'policy_alignment': 'INCONCLUSIVE_OWNER_REVIEW_REQUIRED',
        'uncertain_rule_applicability': uncertain,
        'reason': 'Workflow IDs and status contexts are distinct; exact protected-branch policy must be reviewed independently',
        'governance_mutated': False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Read-only GA-00 branch governance audit')
    parser.add_argument('--repository', required=True)
    parser.add_argument('--repo', type=Path, default=Path.cwd())
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument('--rulesets-json', type=Path, help='Existing GitHub ruleset detail list fixture')
    src.add_argument('--live', action='store_true', help='Use gh api GET only; no writes')
    args = parser.parse_args(argv)
    try:
        policy = _load_json(args.repo / 'config/delivery-required-ci.json')
        rulesets = live_rulesets(args.repository) if args.live else _load_json(args.rulesets_json)
        if not isinstance(rulesets, list):
            raise GovernanceError('GA00_RULESET_SNAPSHOT_INVALID')
        print(json.dumps(audit(policy, rulesets, repository=args.repository), sort_keys=True, indent=2))
        return 0
    except (GovernanceError, OSError) as exc:
        print(str(exc))
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
