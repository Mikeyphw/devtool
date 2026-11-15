# Delivery CI registrar — original roadmap S4, HF01

This is the S4 registration-aware discovery layer, **not** a new S5/S6
milestone. It extends the native Devtool `delivery session ci` operation;
Python/Rich remains a thin client of Devtool's persisted state.

## Required source and policy

The current repository has `config/delivery-required-ci.json` (schema 1),
which declares six explicitly required `pull_request` workflows, with names,
numeric provider workflow IDs, and `release_qualification: separate`.
The registrar does not infer required checks from successful historical runs.
The policy stays committed, hash-checked and unmodified by this overlay.

On each strict CI invocation, Devtool:

1. Resolves the exact session, source Git HEAD, branch, PR number/head and
   current synthetic merge ref, refusing drift.
2. Fetches each required workflow's provider ID, name, active state, and path;
   parses the exact committed workflow YAML from HEAD and checks event and
   target-branch filters. Unsupported/conditional filters are **indeterminate**,
   never assumed inapplicable or successful.
3. Lists recent Actions runs using bounded pagination (up to 500 rows).
   A run is eligible only if repo + workflow ID/name + event + source branch/PR
   relationship + exact source/merge ref + creation window all match. Rerun
   attempt is preserved distinctly from run ID. Concurrent eligible run IDs
   for the same workflow produce `BLOCKED_AMBIGUOUS`, never latest-wins.
4. Uses a fixed **30-minute registration window from session creation**,
   with 120-second clock-skew allowance for API timestamps. Missing runs yield
   `WAITING_REGISTRATION` until the deadline, then
   `REGISTRATION_TIMED_OUT`. Polls use bounded exponential backoff and
   deterministic jitter. An HTTP rate limit returns
   `REGISTRATION_PROVIDER_UNAVAILABLE` and records a non-green receipt.
5. Independently verifies every required completed run (GET by ID, current
   attempt's jobs), then inspects the target base branch's protection and
   effective branch ruleset checks. Any unavailable or unsatisfied provider
   requirement becomes `BRANCH_RULES_UNVERIFIED`, not a green result.
6. Only then records `REQUIRED_CHECKS_PASSED`. All CI reports have
   `release_qualified: false`; no release or PR merge is performed.

## Distinct event contracts

`push` and `pull_request` have different branch/PR matching rules. A
`workflow_dispatch` candidate is **never** attached by a matching SHA alone;
legacy HTTP 204 and uncertain dispatch POSTs remain `DISPATCH_UNCORRELATED`
without a separately correlated exact run ID. The current committed policy
requires `pull_request`; switching a repository's event requires an explicit
versioned policy change, not guessing from observed runs.

The registrar does not automatically rerun, dispatch, or create remote state.

## Conditional trigger and ruleset scope

Simple event/branch filters are checked exactly. Path filters, unsupported
GitHub glob constructs, and job conditions cannot be inferred from a run
name: they fail closed as `BLOCKED_CONDITIONAL` or `BLOCKED_TRIGGER` for
required checks. They must be represented in an explicit reviewed policy
extension before automatic exemption is permitted.

For GitHub branch protection and rulesets, the CLI needs permission to read:

- `GET /repos/{owner}/{repo}/branches/{base}`;
- `GET /repos/{owner}/{repo}/rules/branches/{base}`;
- if protected: `GET /repos/{owner}/{repo}/branches/{base}/protection/required_status_checks`.

GitHub Actions job contexts are reconciled only to exact successful jobs from
qualified required runs. External-app contexts, undeclared provider-required
workflows, inaccessible rules, and incomplete job inventories **block**.
A protected branch's required review/merge gates are separate from this CI
check and are **not** waived by this report.

## Native interface and recovery

```
python3 -m devtool --repo "$HOME/Code/devtool" delivery session ci \
  --id SESSION_ID --checks 25 --interval 8
```

Workflow names are loaded from the committed policy, not passed through
`--require-workflow`. `delivery session report --id SESSION_ID --json` reads
an immutable-source-bound observation. A fresh delivery commit requires a
**new session**; do not resume an older session against a moved source commit.

The included Python/Rich launcher continues after Devtool exits, supports
`--no-remote`, `--approve-remote`, and `--ci-only`, and keeps the existing
`REMOTE_SKIPPED_BY_USER` behavior. It does not run Gradle locally, force-push,
reset, or auto-release.

## Test authority and limitations

Local pytest and isolated Git fixture results are evidence of the source
implementation, not evidence that the new overlay applied in Termux or that
hosted CI passed on the new commit. The `pull_request`/provider registration
path is exercised by mocked API adversarial tests. GitHub Actions on the new
head and Termux exact-tree validation are separate acceptance gates.
