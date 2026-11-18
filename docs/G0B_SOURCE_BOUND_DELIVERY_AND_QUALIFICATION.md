# Milestone B — source-bound GitHub delivery and qualification

**Roadmap:** 2026-11-17, B1–B3. **Scope:** same-repository/fork delivery,
read-only provider capabilities, upstream CI policy and exact hosted scopes.
The existing atomic overlay transaction, delivery mutation journal, and Actions
dispatch journal remain the only authorities. This milestone adds no Git or
HTTP mutation engine, no release qualification, no local Gradle requirement.

## Session source identities and fork safety

A new delivery session can explicitly bind the *upstream base* while the
checked-out `origin` remains the *head fork*:

```sh
devtool --repo "$PWD" delivery session plan --base main \
  --base-repo upstream/project --head-repo forkowner/project --base-sha "$BASE_SHA"
devtool --repo "$PWD" delivery session start --base main \
  --base-repo upstream/project --head-repo forkowner/project --base-sha "$BASE_SHA"
```

The `--head-repo` must match the Git origin; a different fork never becomes
valid just because the short branch name matches. `--base-sha` is optional but
strongly recommended for a pinned upstream branch. A missing pin is **not**
reported as a base-commit guarantee.

If the native overlay is **already committed**, adopt its durable native
receipt, never reapply it:

```sh
python3 -B -m devtool.native_overlay_delivery adopt-committed \
  --repo "$PWD" --overlay /path/to/consumed-overlay.zip --base main \
  --base-repo upstream/project --head-repo forkowner/project --base-sha "$BASE_SHA"
```

Existing adopted sessions cannot be silently rebound to another upstream.
If the session was adopted with the wrong upstream identity, stop and review
the exact journal; do not create duplicate sessions or replay a Git push.

`delivery session push --id SESSION --yes` still uses the **head fork's**
checked-out origin, enforces source HEAD and non-force fast-forward, and
journals intent before any write. `delivery session pr --id SESSION` first
performs read-only reconciliation. For a new PR, explicit `--yes` is required;
GitHub receives `head=owner:branch`, `base=branch` on the **upstream** repo.
The provider must independently return exact `head.repo`, `head.label`,
`head.sha`, `base.repo`, `base.ref`, and (when pinned) `base.sha`. Duplicate,
wrong-upstream, moved-source and unresolved POST cases block without replay.

## B1: capability and provider diagnostics

`devtool.delivery_provider.preflight` exposes read-only checks for
`repository_read`, `pr_read`, `pr_create`, `branch_write`,
`actions_dispatch` and `checks_read`. It distinguishes confirmed access,
`permission_unavailable`, `disabled`, `provider_unavailable`,
`resource_unverified` and `unknown`. It **never** treats an unknown GitHub PR
write scope, a 404 potentially hiding a private resource, or a missing
branch-protection read as granted access. Git SSH branch push is independently
proven by native `ls-remote`/fast-forward checks, not by GitHub API token hints.

The existing `GitHubActionsClient` opener/token and URL-origin protection are
reused by the bounded PR REST helper. GitHub mutations still belong to their
original journal owners. Provider errors never echo tokens or signed URLs.

## B3: distinct acceptance scopes

The **required_delivery_ci** scope uses Devtool's committed required-CI policy,
PR registrar, exact run/attempt/job checks, and branch/ruleset verification.
For a fork, the policy is fetched from the upstream **base commit**, with its
Git blob verified and upstream branch identity rechecked during observation;
policy bytes from the fork do not qualify upstream required checks.

The **repository_regression** scope requires a preexisting, natively
correlated `actions dispatch` request, a remote `workflow_dispatch` run at the
**exact session HEAD**, successful current run attempt, 24 distinct Python
3.13 shards and exactly one successful fail-closed summary job. Skipped jobs,
missing shards, ambiguous/uncorrelated POSTs, attempt changes and wrong
repositories are never successes.

After the native Actions dispatch has returned `request_id` and the required
CI observation has been saved for the session, run a read-only combined report:

```sh
devtool --repo "$PWD" delivery session report --id "$SESSION_ID" \
  --regression-request-id "$REQUEST_ID" --json
```

A passed pre-merge scope remains `PENDING_POST_MERGE_MAIN_HEAD` for final
acceptance. Release qualification and device qualification remain **NOT
PERFORMED**. There is no automatic merge, CI rerun, workflow dispatch,
new GitHub mutation, local Android Gradle invocation or clipboard copy.

## Compatibility, gates and recovery

The existing `delivery session plan/start/report/push/pr/ci` syntax remains
valid for same-repository consumers. Existing journals lacking new fork fields
are interpreted as **same-repository only**; their bytes are never silently
migrated or repinned. Recovery from unknown PR POST or push does read-only
provider reconciliation and requires a new approval for any distinct mutation.

Regression coverage: same-repo and fork identities, wrong namespace/label,
wrong base and base SHA, exact Git origin, ambiguous/uncertain POST, source
mismatch, upstream policy Git blob/integrity/drift, permission states, one
correlated 24-shard dispatch, skipped/missing/duplicate shard or summary,
changed hosted attempt, required CI not reported as optional, and current
canonical CLI schema/generated help/completions. No obsolete safety gate was
removed. Runtime executable tests are locally Python-only; GitHub/Termux
acceptance requires the exact resulting PR HEAD and hosted run IDs.

## Consolidated R2 acceptance hardening (audit follow-up)

This immutable revision closes gaps detected in the original Milestone B package:

- **Independent regression workflow authority:** the Actions dispatch journal is
  not sufficient on its own. Qualification fetches the provider's registered
  `devtool-full-repository-pytest.yml` workflow and verifies its numeric ID,
  canonical workflow path, name, and active status. The exact run's workflow ID,
  path, event, commit SHA, branch, repository, attempt, and conclusion must also
  match. A different workflow with identically named jobs cannot qualify.
- **All 25 hosted artifacts required:** the exact run must expose 24 unique,
  non-expired `devtool-full-shard-N` artifacts plus exactly one
  `devtool-full-pytest-summary` artifact. Each artifact's ID, run ownership,
  and positive size are checked. These are metadata receipts, not claims of
  downloaded or re-hashed artifact contents; the hosted summary's own
  fail-closed evidence validator remains authoritative for contents.
- **Fork upstream source:** new fork PR creation requires an explicit pinned
  upstream `--base-sha`; read-only fork qualification likewise blocks if the
  session lacks a pin. Earlier unpinned same-repository sessions remain valid.
  Existing unpinned fork sessions can still be inspected and reconciled
  read-only, but cannot create a new PR or obtain a premerge acceptance verdict.
- **Full PR identity:** same-repository and fork PRs both require a verified
  `head.label`, not merely matching short branch names or a matching SHA.
- **Bounded API reads:** GitHub PR discovery walks at most five complete pages,
  refusing duplicates or unbounded results. Transient GitHub GET requests may
  retry once with bounded delay; PR POST requests are never retried.
- **Permission evidence:** an active workflow is **not** proof of an Actions
  dispatch token scope. The `checks_read` probe checks the provider's exact
  default-branch check-runs and rulesets endpoints independently; this proves
  visibility only and does not supersede the registrar's branch/ruleset
  applicability and exact-check verification.

These changes preserve all existing native mutation journals and source-bound
apply, and update fixture shapes for the current provider identity contract.
No historical security check was removed. R2's local test results do not qualify
an unseen hosted run, device, release, or post-merge `main` HEAD.
