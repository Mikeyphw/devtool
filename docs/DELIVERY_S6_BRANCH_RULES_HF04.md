# Original S6 HF04 — Ruleset-only CI qualification

## Incident

The source-bound S6 commit `5fab18ae94f7b4003191d03de91014ee1617eeba`
was pushed and reconciled on existing `Mikeyphw/devtool` PR #17. Its six
committed-policy-required hosted workflows were completed successfully, and the
S6 Unified Delivery Evidence workflow also completed successfully. The S4
registrar refused with `BRANCH_RULES_UNVERIFIED` and classified the diagnosis
as `branch-rule-unavailable`, correctly withholding CI qualification.

GitHub exposes `main.protected == true` for a branch guarded only by an active
ruleset. On this repository, the GET branch response explicitly reports that
**classic protection is disabled** (`protection.enabled == false`) and that
classic required status checks have enforcement `off`, with empty `contexts` and
`checks`. Nevertheless, the old registrar attempted the classic
`/branches/main/protection/required_status_checks` call, which returned 403
`Resource not accessible by integration` to an installation token that does not
have repository Administration(read).

## Narrow correction

Only when the independently fetched branch GET attests ALL of the following:

* `protected` is a literal boolean `true`;
* `protection.enabled` is the literal `false`;
* the embedded `required_status_checks.enforcement_level` is exactly `off`;
* embedded `contexts` and `checks` are both explicitly empty lists;
* the effective ruleset API returns a nonempty, structurally valid rule list;

Devtool skips the **inapplicable classic branch-protection endpoint**. It still
reads effective branch rules and verifies each ruleset-required GitHub Actions
job context against exact successful run-attempt job evidence from the current
source-bound CI observation. Source/policy/PR identity checks are unchanged.

If any classic-protection indicator is missing, inconsistent, or active, the
original protected-branch endpoint is still required. A 401/403/404 or provider
outage remains a CI blocker. Unknown rules, external-app check contexts, missing
required jobs, and provider-required workflow rules that cannot be proven still
fail closed. This change does not mark a release, merge, device acceptance or
signing as qualified.

## Current verification

Focused S4/S5 registrar and diagnostic tests, including new adversarial cases,
were executed locally on the exact S4 registrar/test blobs. Re-qualification of
S6 HF04 needs a **new commit and a new delivery session**: old S6 session
`c52a9fc2a46547ae9a96cf22a2a902db` stays an immutable failed/blocked
receipt for its older commit and must not be reused to qualify the hotfix.

Launch with the attached full Python/Rich source-pinned HF04 ZIP. It uses
Devtool's native overlay transaction and only pushes/reconciles the existing
PR with `--approve-remote`; never force-pushes or auto-retries uncertain writes.
No local Gradle or device writes are performed.
