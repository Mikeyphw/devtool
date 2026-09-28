# LIFE transaction/recovery incident integration — phantom dirt and result publication

Date: 2026-09-28
Canonical owner: M006 LIFE — `LIFE-X01` implementation, `LIFE-G1` authoritative recovery gate.
Cross-consumers: TEST (adversarial fixtures/result truth), PRES (truthful diagnostics), RUN (durable receipt refs).
Plan impact: no new overlay; canonical 63-item order is unchanged.

## Incident

An interrupted/finalizing artifact transaction restored repository bytes to the pre-apply baseline, but Git/Devtool subsequently reported some restored paths as dirty even though `git diff` was empty and file bytes matched the baseline. A later overlay that legitimately modified the same paths was rejected by preserve-dirty conflict handling as if those paths were user edits.

A related lifecycle state was also observed: validation succeeded and a detached result commit had already been created, but interruption before publication left that validated commit unpublished. Recovery then restored the transaction worktree instead of idempotently completing publication.

The ARC `age_seconds=1` defect is explicitly excluded; it is ARC-owned, not a Devtool defect.

## New LIFE promises

- **LIFE-P123 — Recovery Git-state normalization.** After Devtool restores a path, recovery must normalize and verify index/worktree/stat state against the recovered baseline. A byte-identical restored path cannot remain classified as dirty solely because cached/stat/index refresh state is stale.
- **LIFE-P124 — Content-verified preserve-dirty conflicts.** Before rejecting an overwrite as `Cannot preserve dirty path`, Devtool must prove a real content/index delta relative to the relevant baseline/generation. Cached dirty state alone is insufficient. Genuine pre-existing user dirt remains protected.
- **LIFE-P125 — Idempotent result-commit publication.** Once validation has produced the transaction result commit, publication/integration is a durable resumable phase. Interruption after commit creation must allow the next invocation to finish publishing that exact validated result when preconditions still hold instead of blindly rolling back and requiring reapplication.
- **LIFE-P126 — User-dirt provenance across recovery.** Recovery must distinguish pre-existing user changes from Devtool-created/restored state and persist enough generation/provenance evidence to preserve only genuine user dirt on the next apply.
- **LIFE-P127 — Post-apply validation registry truth.** Artifact `validation.tests` refs and other repository-declared validation identities must resolve against the authoritative post-apply transaction candidate/config. A same-run config/test change must not silently execute an older declaration or project a stale test count. TEST owns result/selection accounting; LIFE owns lifecycle/config-root binding.

`LIFE-P123..P127` append to the existing LIFE promise space; previously reserved `LIFE-P118..P122` remain unchanged.

## Required LIFE-G1 adversarial fixtures

1. Recover a transaction that restored bytes but left index/stat metadata stale; a second overlay touching those paths must not see phantom user dirt.
2. Repeat with a genuine pre-existing user edit; preservation must still fail closed or preserve that edit according to policy.
3. Interrupt after validated result-commit creation but before branch publication; recovery must detect and idempotently publish/resume rather than discard the validated result.
4. Change a first-class test declaration in an overlay and use `validation.tests = [{ref = ...}]`; the same apply must execute the post-apply declaration or explicitly reject unsupported same-run rebinding.
5. Recovery/dirty diagnostics must state the baseline/generation and evidence used to classify each protected path.
