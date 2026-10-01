# LIFE-X01 — Immediate lifecycle safety

Date: 2026-09-29
Campaign position: gate-window 1 of 3; full-plan 31 of 70.
Validation policy: implementation overlay; apply with `--no-validate`. Targeted non-Gradle regression was run while constructing the overlay.

## Merge-window decision

LIFE-X01 remains separate. LIFE-X02 owns the canonical ArtifactEnvelope/qualification/resolved-plan/trust/pack model and LIFE-G1 is the separate authoritative mutation/recovery gate.

## Current-source convergence

The supplied source already contains the bulk of the immediate-safety substrate inherited by LIFE-X01: transaction journaling and staged rollback, undeclared-write containment, private artifact copies, active-transaction cleanup protection, scoped cache/space cleanup, destructive provider metadata, and scoped run reservations. LIFE-X01 therefore does not replace those working authorities.

## Delta implemented here

Recovery no longer self-blocks an older or preflight-failed `rollback-failed` journal merely because an unverified entry lacks `resume_target_identity` while its restore state proves the live target has not yet been mutated (`pending`, `staging`, or `staged`).

Once rollback crosses a live-target mutation boundary (`target-displaced`, `restored`, or `removed`), the checkpoint remains mandatory and resume stays fail-closed. Existing user-drift regressions remain authoritative.

This directly repairs the High rollback-reservation incident where a failed rollback without per-path checkpoints could retain the transaction reservation and make explicit recovery reject itself forever.

## Targeted construction validation

- `tests/test_transactions.py`: resumable journal + no-checkpoint pre-mutation retry — PASS
- `tests/test_ov01_transactional_safety.py -k mr085`: real post-failure user/mode drift refusal — PASS
- `tests/test_ov18_validation_recovery_controls.py -k rollback`: recovery controls — PASS
- Python compile check for `devtool/transactions.py` — PASS

Observed combined result: 9 passed in the focused rollback/recovery replay, plus 2 focused transaction tests in the first replay.

## Gate obligations retained

LIFE-G1 must still prove the complete LIFE-X01 + LIFE-X02 boundary from fresh processes, including rollback preflight failure versus partially-applied resumable rollback, phantom-dirt normalization, genuine user dirt preservation, result publication recovery, post-apply validation registry truth, signing-secret non-persistence, and pack atomic-publication safety.
