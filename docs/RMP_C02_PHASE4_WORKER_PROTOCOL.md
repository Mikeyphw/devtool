# RMP-C02 Phase 4 — Fresh cleanup worker protocol and caller recovery

Scope: Only the post-apply external source cleanup process response. Does not change authorization of unrelated repository cleanup or mutate old qualification evidence.

## Problem

A worker killed after unlink but before returning a JSON frame could be reported as a generic cleanup failure; empty stdout was normalized to `{}` and thus treated as though a parsed response existed. That output does not attest whether the source was deleted or a LIFE receipt was persisted.

## Behavior

- Valid worker JSON requires an object, integer `returncode` (not boolean), string `output`, and object `metadata`.
- On missing/invalid JSON or abnormal termination, the parent attempts read-only receipt reconciliation using the existing Phase 3 source-identity-bound plan and optional RUN attachment.
- Verified exact receipt and absent sources: recovered completion; no second worker spawn.
- No receipt and missing/ambiguous sources, signal termination, or malformed exit-zero frame: `artifact_cleanup_outcome_unknown`; no automatic replay.
- Controlled nonzero worker exit with **all original sources present** and no frame: `artifact_cleanup_failure`, retaining historical caller semantics.
- Structured success without a matching receipt remains refused as `artifact_cleanup_receipt_unverified`.

Tests use disposable files and mock interrupted worker responses, plus a real isolated overlay-apply regression. No real user downloads are deleted by the regression suite.

**State:** Offline source candidate; requires Phase 3 committed on the intended security branch before the source-bound Phase 4 overlay can be applied. No claim of formal C02 closure, Termux proof or release qualification.
