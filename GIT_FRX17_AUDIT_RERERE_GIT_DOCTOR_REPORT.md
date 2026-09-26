# GIT-FRX-17 — Federation-aware audit delta, rerere, and Git doctor

## Scope

This standalone non-gate milestone implements GIT-FRX-17 only. It is based on the exact post-GIT-FRX-16 Gate-2 v3 source state. GIT-FRX-18 signing, federated provenance-manifest generation, and final-seal policy are intentionally excluded.

## Delivered contracts

### Federation-aware audit passes

- Persists campaign-scoped audit passes under Devtool state.
- Captures every participating repository's canonical repository identity, commit, tree, dirty-delta identity, Devtool campaign/federation refs, inspected roadmap range, and evidence hashes.
- Adds `devtool audit record`, `devtool audit delta`, and `devtool audit status`.
- Exposes `verify_zero_gap_audit_freshness()` as the fail-closed consumer boundary for the later final seal.
- A previously zero-gap audit becomes stale when any participating repository source identity changes.

### Devtool-owned rerere

- Modes are `off`, `suggest`, and `validated-auto`.
- Conflict memory is scoped by repository identity, exact unmerged-index conflict preimage, and campaign context.
- No fuzzy or global conflict-memory reuse is permitted.
- `validated-auto` always reruns current validation before retaining an applied memory.
- Failed validation restores the original conflicted file content.
- Stale/corrupt exact memories fail closed.

### Git and federation doctor

- Adds `devtool git doctor`, `--json`, and `--repair-safe`.
- Adds `devtool federation doctor [<run-or-campaign>] [--json]`.
- Git doctor covers repository/head/upstream state, dirty/index/unmerged state, merge/rebase/cherry-pick/revert/bisect operations, shallow/partial clone state, object connectivity, worktree registrations, lock files, Devtool refs, transaction/recovery state, and signing configuration.
- `--repair-safe` deliberately avoids deleting transaction journals or recovery evidence; only demonstrably non-destructive repairs such as prunable worktree cleanup are allowed.
- Federation doctor checks participating repository health, parent/child start/result ref consistency, and required remote-attestation evidence.

### Native Devtool adoption

- Adds `roadmap-git-frx17` as a first-class Devtool workflow.
- Adds `roadmap-git-frx17` as a declared pytest test.
- Advances `roadmap-current` through GIT-FRX-17.
- Keeps GIT-FRX-18 outside the graph.
- Updates generated/static completion and architecture documentation.

## Audit loop evidence

Focused implementation and native-contract qualification completed successfully.

Roadmap regression was run in bounded chunks to avoid repeating the long Gate-2 apply-time mistake:

- GIT-FRX-01..08: **122/122 passed**.
- GIT-FRX-09..17: **71/71 passed**.
- FRX remote/operational/observability/final-seal plus FRX-Y10/Y11 authority regression: **58/58 passed**.
- Completion compatibility repair regression: **44/44 passed** after preserving historical GIT-FRX-10, FRX-12, and FRX-Y11 completion subsequences.

Native workflow qualification:

- plan: **4 nodes / 4 commands / 4 waves**;
- preflight: healthy under the explicit desktop-host qualification override;
- invoke: **4/4 stages**;
- tests: **45 passed / 0 failed / 0 skipped**;
- diagnostics: **0 warnings / 0 errors**.

## Audit repairs

The audit loop found no source/runtime semantic defect in the GIT-FRX-17 implementation. It did find compatibility regressions caused by inserting the new `federation doctor` completion token inside older stable action subsequences. Completion ordering was repaired so the new command is exposed while all historical completion contracts remain byte-substring compatible.

## Boundaries

- No signing policy is implemented here.
- No federated final provenance manifest is generated here.
- No final campaign seal is claimed here.
- Existing transaction, source-lock, remote transport, federation execution, and recovery authorities remain authoritative.
- GIT-FRX-18 remains the next Gate-3 milestone.

## Exact artifact replay qualification

The sealed payload was applied to a fresh copy of the exact post-GIT-FRX-16 v3 preimage with source guards enabled and validation disabled only for the replay application step.

- artifact apply: **PASS**, 0 warnings / 0 errors;
- payload byte equivalence against the qualified construction tree: **11/11**;
- replay focused/native-contract/completion regression: **48/48 passed**;
- replay native plan: **4 nodes / 4 commands / 4 waves**;
- replay preflight: healthy under the explicit desktop-host qualification override;
- replay `roadmap-git-frx17`: **4/4 stages, 45/45 tests, 0 warnings, 0 errors**.

The user-facing artifact remains a non-gate overlay and is intended to be applied with `--no-validate`; focused and native qualification is construction/replay evidence rather than an expensive apply-time historical sweep.
