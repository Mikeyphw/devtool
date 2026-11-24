# B0 · M3/M4 existing-session upstream base-pin recovery

This change adds one **Devtool-native**, source-bound base-pin reconciliation module and wires it into the cumulative roadmap operator's `session advance-base` route. It is not a separate issuer, GitHub transport, session journal, PR creator, Git apply engine, CI evaluator or Rich host. `devtool.delivery_session` retains all journal authority and its `_locked`, `_read`, `_write` integrity/atomicity path; `devtool.delivery_github` retains PR and CI authority. The CLI uses M6's `operator_status` and existing Rich renderer for its session projection.

## Explicitly scoped first case

- GA-02 session `686c8049aa36465aaf41caa0dce833a1`, source `b6e6550e0d8e42f4d0009d56a81dcbdb35dc9d0f`, verified PR #48, repository `Mikeyphw/devtool`.
- Original upstream `main` pin `44f48e367be20cbc9ec39f96d6112a6351c18124`; live `main` `05e120d185ce8e81287b591bac6a221054abe94e`, old is ancestor by 13 commits.
- Native source COMMITTED, one confirmed push intent, zero unresolved intents, source-bound remote push verified. No other live session was modified by this source handoff.
- For GA-01 #46, discover and verify its separate original session and base pin before running this operation; never reuse GA-02 identity.

## Native workflow, not a bypass

1. Apply the independent M3 native v3 source artifact, incorporating this module, tests and the integrated v1.2 roadmap. It is pinned to the current `main` `05e120d...` and its tree, on a new review branch; source preflight/check, atomic commit, M3 adoption and PR/CI24 are separate honest gates.
2. The operation `devop-devtool session advance-base --id SESSION --repo GA02_WORKTREE --runtime-repo B0_VERIFIED_RUNTIME --old-base-sha OLD --new-base-sha NEW --pr-number 48 --yes` requires explicit approval.
3. It locks the *existing* M3 delivery journal, checks its SHA seal, source Git root and named branch identity, committed native apply, confirmed push and no unresolved intents. It requires same head/base repository for the initial scope.
4. It independently checks all three Git commit objects, old-base ancestry to new base, and new base ancestry to source HEAD (no Git fetch, force or working-tree mutation).
5. An operation-scoped M4 App client reads the exact upstream branch and exact existing PR twice; head SHA, repo, base ref/SHA, number and open state must match.
6. Only after those proofs, `delivery_session._write` atomically advances `base_sha` and journals the prior and new pin with exact source, PR, timestamp and two-GET verification. Original source history, events, intents and transaction links are preserved.
7. Devtool-native `delivery_github.pr(... yes=False)` re-reads PR identity and records the already-open PR as `PR_VERIFIED`; `yes=False` can never issue a POST if the provider PR disappears. If the second operation refuses, the pin event remains auditable and may be observed again without reapply.
8. Required CI is then observed by the existing Devtool native CI engine, with independent GitHub/CI24 evidence, App negative tests and Rich/PTy proof remaining separate acceptance gates.

## Safety/acceptance

No local Gradle. No GitHub write, PR creation, local Git mutation or journal mutation during `inspect`. No manual editing of JSON/checksums. Never automatically replay an uncertain native commit or remote write. Historical verified PR #48 and its existing session are not superseded by this docs/source change. All output is nonsecret; clipboard is invoked **only** through explicitly requested `collect` mode. The Rich status view is a projection over the existing M6 operator renderer, not another terminal host.

**This source handoff is not itself a Git commit, remote PR, or a completed native session reconciliation.** Termux native apply/delivery, source-bound CI24, real App grant/deny tests, and device UI/PTY acceptance remain to be independently proved.
