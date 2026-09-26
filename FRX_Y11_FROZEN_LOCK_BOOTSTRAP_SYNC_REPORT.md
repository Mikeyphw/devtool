# FRX-Y11 — Frozen-Lock Multi-Repository Bootstrap / Sync

Status: **implemented and qualified**.

## Delivered contract

FRX-Y11 makes repository source materialization an explicit operation separate from normal federation plan/run:

```text
devtool federation bootstrap
devtool federation sync
devtool federation sync --repo <repo>
```

Both commands are preview-by-default. Mutation requires `--apply` and normal Devtool confirmation semantics (`-y/--yes` for non-interactive operation).

The implementation consumes the existing GIT-FRX-08 / FRX-Y03 frozen source lock and adds only sanitized clone-locator evidence required to materialize a missing clean Git repository. Core lock/equivalence digests remain compatible with the pre-Y11 frozen-source identity.

Source management guarantees:

- required missing children are tolerated only for explicit `bootstrap` / `sync`; ordinary federation discovery/plan/run remains fail-closed;
- bootstrap stages a clone beside the destination and atomically installs it only after the locked commit is available;
- sync refuses dirty worktrees, remote mismatches, branch switches, non-fast-forward rewrites and frozen dirty-state reconstruction;
- managed checkout updates establish GIT-FRX-02 private recovery refs/journal evidence before mutation;
- updated/bootstrap checkouts are verified against locked commit, branch/ref, config digest, tree, dirty state and remote identity;
- unavailable/failing fetches are reported as failures without moving HEAD;
- multi-repository partial availability is represented truthfully;
- sync/bootstrap never mutate the frozen lock; lock changes remain an explicit `federation lock --update` operation;
- SSH prepare/workspace transport has no source-management authority.

## Native Devtool execution

- Target: `devtool`
- Workflow: `roadmap-frx-y11`
- Declared test: `roadmap-frx-y11`
- TLJ fixture: `frx-y11-bootstrap-sync-fixture`
- Interim milestone: user apply uses `--no-validate`.

The standalone native workflow completed successfully with **5/5 stages, 49/49 tests, 0 warnings, 0 errors**.

## Focused evidence

The final FRX-Y10/Y11 focused contract set passed **22/22**. Y11 coverage includes lock locator sanitization, missing-repository bootstrap preview/apply, normal discovery fail-closed behavior, fast-forward sync with recovery evidence, dirty/remote/non-fast-forward refusal, old-lock truthfulness, JSON and human CLI preview, SSH ownership separation, partial multi-repository availability, and fetch-failure preservation of HEAD.
