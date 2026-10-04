# REMED-R04B artifact cleanup fresh-worker split B

## Purpose

Wire post-apply source-artifact cleanup to the fresh-process LIFE worker introduced by split A.

The triggering defect was a Devtool self-update hazard: the apply process could rewrite `cleanup_lifecycle.py` and later execute cleanup through the already-imported pre-update module graph. The policy on disk was correct, but the long-lived process still enforced the old rule.

## Delivered invariants

- Post-apply source cleanup runs in a fresh Python process.
- Self-updates prefer the just-applied repository source on `PYTHONPATH`.
- The worker returns a structured cleanup result that is normalized back into the canonical `OperationResult`.
- Worker process failures remain classified as `artifact_cleanup_failure`.
- The v2 safety model remains unchanged: repository-active sessions still protect repository-owned cleanup while external consumed apply sources may be deleted when no direct durable reference protects them.

## Closure

After this split, future Devtool self-updating overlays no longer depend on stale in-memory cleanup policy. The next campaign overlay remains CORE-G3R, full-plan position 79/93, after real Termux confirmation of A and B.
