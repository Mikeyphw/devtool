# Git/Federation Post-Gate-3 Roadmap Audit and Promise Remediation

## Verdict

The fresh post-Gate-3 snapshot did **not** initially satisfy every promise retained by the Git/Federation roadmap. The final Gate-3 feature set itself was present, but the audit found six material delivery gaps concentrated in GIT-FRX-12/13/14/17 and cross-surface completion parity.

This remediation closes those gaps without replacing the existing Git, EXO, FRX, frozen-source, scheduler, artifact, SSH, or final-seal authorities. After the repairs and a restart-at-01 bounded audit, **no remaining material roadmap gaps were found**.

Authoritative audit source: `devtool-20260926-130757-391870.tar.gz`, supplied after the user applied Gate 3 at reported live commit `64368e6`.

## Material gaps found and repaired

1. **GIT-FRX-12 — campaign lifecycle was incomplete.** The repository had campaign start/checkpoint/status/range-diff, but the retained roadmap promised top-level `devtool campaign status/checkpoint/rollback/range-diff/supersede`, named checkpoint/step identity, private campaign base/step/tip refs, rollback without rewriting user branches, supersession evidence, and range-diff classification/evidence deltas. These are now implemented.
2. **GIT-FRX-13 — `--preserve-dirty` was missing as a public contract.** Dirty state was already transactionally protected by the transaction engine, but the promised explicit flag and durable evidence that it was user-requested were absent. The flag now records `preserve_dirty_requested=true` and an explicit preservation policy in the transaction journal while retaining the existing stronger automatic protection.
3. **GIT-FRX-14 — explicit federation recovery/rollback surfaces were missing.** `devtool federation recover <run-id>` and scoped `federation rollback` are now backed by persisted FRX-Y02 checkpoint and GIT-FRX-10 integration evidence. Rollback is fail-closed, never pushes, requires exact validated-result pointer state, refuses dirty checked-out worktrees, and does not reset unrelated repositories. During the audit loop an intermediate parser-only implementation was detected and repaired before sealing.
4. **GIT-FRX-17 — rerere was library-only.** `[git].rerere` is now typed/validated as `off | suggest | validated-auto`; the CLI can capture exact unresolved conflict identity, validate and record a manual resolution, suggest an exact memory, and apply a matched `validated-auto` memory only with a current repository-declared Devtool test rerun. Stale/cross-repository/cross-campaign memories fail closed and a failed validation restores the working-tree content.
5. **GIT-FRX-17 — federation doctor/QoL delivery was incomplete.** Doctor now projects capability health, interface conformance, frozen-lock drift, resume/checkpoint evidence, artifact provenance, provider placement, hierarchy lineage, and bootstrap/sync recovery state. `federation graph --group`, `diff`, `artifacts`, `logs`, and `failures` project existing authoritative FRX/EXO state without introducing a second store.
6. **Cross-surface completion parity drifted during remediation.** The generated Zsh completion and checked-in `completions/_devtool` are byte-identical again and covered by a regression test.

## Authority preservation

The remediation deliberately reuses existing authorities:

- campaign source/contract identity: GIT-FRX-08/Y03 frozen plan/lock + GIT-FRX-12 campaign state;
- recovery/retry: FRX-Y02 checkpoint store;
- integration/rollback: GIT-FRX-10 integration evidence;
- artifacts: FRX-Y08 artifact identities;
- placement: FRX-Y09 provider/resource evidence;
- hierarchy: FRX-Y10 lineage;
- bootstrap/sync: FRX-Y11 journals;
- rerere memory: GIT-FRX-17 scoped XDG state only;
- final provenance/seal: GIT-FRX-18 + FRX-Y12 remains the sole final authority.

No second scheduler, run ledger, source lock, artifact registry, SSH history store, or final-seal authority was added.

## Audit-loop validation

Bounded restart from the beginning after repairs:

- GIT-FRX-01..08: **122/122**
- GIT-FRX-09..14: **35/35**
- GIT-FRX-15..18: **55/55**
- post-Gate-3 promise remediation contracts: **12/12**
- FRX-01..12 + full-roadmap reseal: **119/119**
- FRX-X01..X03: **35/35**
- FRX-X04: **8/8**
- FRX-Y01/Y02/Y04..Y07: **64/64**
- FRX-Y08..Y12: **36/36**
- SSH/SSHW: **192/192**
- chroot/PRoot/OCI: **232 passed + 7 subtests**

Campaign total: **910 passed + 7 subtests**.

Native Devtool `roadmap-gate3` after remediation:

- **6/6 stages**
- **63/63 tests**
- **0 warnings**
- **0 errors**
- plan: healthy
- preflight: healthy under explicit host qualification in the construction container

## Final status

The post-remediation source satisfies the retained v1 promises and V4 campaign extensions audited here. The final Gate-3 workflow now includes the promise-remediation contract before the EXO baseline, so this source-changing post-seal repair is treated as a Gate-3 reseal rather than an ordinary non-gate patch.
