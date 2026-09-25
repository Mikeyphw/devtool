# GIT-FRX-02 — Private transaction refs and journals

## Scope

This milestone implements the authoritative GIT-FRX-02 transaction-recovery foundation from `DEVTOOL_GIT_FEDERATION_ROADMAP_V3.md` without introducing a second transaction engine. The existing artifact transaction journal in `devtool.transactions` remains authoritative; GIT-FRX-02 extends that same durable record with verified Git recovery anchors before artifact extraction may mutate the worktree.

GIT-FRX-02 was kept standalone after evaluating the current three-item merge window (GIT-FRX-02 + GIT-FRX-03 + GIT-FRX-04). GIT-FRX-03 changes the execution/mutation environment to Devtool-owned worktrees, while GIT-FRX-04 changes commit/history policy. Combining those independently risky transaction-state transitions would materially reduce fault isolation. This overlay is not a validation gate and contains no Devtool reinstall/finalize hook.

## Delivered contract

### Pre-mutation recovery authority

`devtool/git_transaction_journal.py` establishes recovery state before artifact worktree mutation:

- authoritative repository identity is taken from the GIT-FRX-01 Git-state model;
- committed repositories create and verify private recovery refs under `refs/devtool/...`;
- every run receives `refs/devtool/runs/<run>/start` and `refs/devtool/backups/<run>/head`;
- campaign and federation runs additionally receive namespaced start refs;
- existing same-SHA private refs may be reused; conflicting refs fail closed and are never overwritten;
- source-state drift between initial transaction capture and recovery establishment fails closed;
- newly created refs are removed if durable transaction-journal persistence fails before mutation;
- private recovery refs are never represented as user branches or tags.

### Durable transaction journal

The existing transaction schema advances to version 4 and embeds a versioned `git_recovery` contract containing:

- transaction ID and EXO run ID;
- repository ID;
- start symbolic ref/SHA, branch/detached state, or explicit unborn state;
- intended mutation;
- transaction worktree path;
- current commit mode (`new` or `none`; later policy expansion remains GIT-FRX-04 scope);
- campaign/federation identifiers;
- required recovery refs and whether each was newly created or reused;
- recovery lifecycle state;
- finalization lifecycle state;
- result commit when Devtool creates a commit;
- rollback failure evidence where applicable.

Unborn repositories explicitly record `ready-unborn`; non-Git paths explicitly record recovery as `not-applicable`. Neither case invents a commit/ref that does not exist.

### Lifecycle convergence

The same authoritative transaction record transitions through the existing apply lifecycle:

- recovery ready → mutation applied;
- mutation applied → commit recorded when a Devtool commit exists;
- completed → completed finalization;
- abandon/keep changes → abandoned finalization;
- successful rollback → rolled-back finalization;
- failed rollback → rollback-failed with typed error evidence.

Active transaction markers now surface repository ID, recovery state, and finalization state for recovery/inspection without creating a parallel journal.

## Native roadmap execution contract

`.devtool.toml` adds the first-class declared test identity `roadmap-git-frx02` with unique JUnit evidence and the native workflow:

```text
syntax → git-frx01 → git-frx02 → workflow-exo
```

`roadmap-current` now includes GIT-FRX-02 after GIT-FRX-01. The ROADMAP-00 bootstrap contract was evolved to audit the new declared test/workflow rather than frozen at its bootstrap state.

Native plan construction resolves four nodes in dependency order. Native preflight/invocation on the construction host correctly fail at the repository's `native-termux` execution-environment boundary because the construction host reports `desktop-linux`; the target contract was not weakened to make host-local invocation pass.

## Audit loop

### GIT-FRX-02 focused contract

`tests/test_git_frx02_private_transaction_journal.py` covers twelve independently named promises:

1. verified private refs plus complete durable pre-state journal;
2. campaign/federation namespaced private refs;
3. explicit unborn state without fake refs;
4. explicit non-Git/not-applicable state;
5. fail-closed recovery bootstrap before mutation;
6. cleanup of newly created refs if journal persistence fails;
7. private-ref collision protection;
8. applied/commit transition in the same journal;
9. completed/archive finalization while retaining private recovery anchor;
10. rolled-back finalization;
11. abandoned/kept-changes finalization;
12. recovery-ref drift detection.

Result: **12 passed** with a valid first-class JUnit report at `.devtool/reports/roadmap/roadmap-git-frx02.xml`.

### Regression groups

Bounded audit groups were used so failures remain attributable:

- transaction/reservation/concurrency/current transaction contracts: **99 passed**;
- GIT-FRX-01 + ROADMAP-00/native workflow + FRX-Y01 scheduler contracts: **50 passed**;
- artifact fail-closed/pipeline contracts: **30 passed**.

Total bounded regression evidence: **179 passed**, in addition to the independently replayed 12-test declared GIT-FRX-02 identity (the focused tests are intentionally represented in the first group as well, so these figures are evidence groups rather than a deduplicated test count).

A selected legacy CLI test, `CliTests::test_apply_overlay_continues_after_successful_stale_rollback`, still fails because its pre-existing `_run_apply` mock does not accept the already-existing `tasker_progress` keyword. The exact node fails identically on the pre-GIT-FRX-02 FRX-Y01 baseline; it is not introduced by this milestone. Ten sibling selected CLI/history contracts passed.

### Source/package checks

- Python source compilation: PASS.
- First-class workflow plan: PASS; nodes `syntax`, `git-frx01`, `git-frx02`, `workflow-exo`.
- Recovery setup is integrated into `devtool.transactions`; no second transaction lifecycle authority was added.
- The overlay is a normal non-gate artifact and must be applied with `--no-validate`.
- No `pip install -e ~/Code/devtool` hook is present.

## Deferred by design

This milestone does **not** implement:

- Devtool-owned isolated transaction worktrees — GIT-FRX-03;
- full `new/amend/fixup/none` history policy and published-history protection — GIT-FRX-04;
- touched-file base contracts — GIT-FRX-05;
- three-way overlay rebase/equivalence — GIT-FRX-06 / Gate 1;
- parent-owned rewriting of child repository history.

These boundaries are preserved so later milestones build on one recovery authority rather than replacing it.
