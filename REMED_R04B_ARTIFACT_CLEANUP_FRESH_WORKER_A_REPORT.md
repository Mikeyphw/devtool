# REMED-R04B artifact cleanup fresh-worker split A

## Purpose

Introduce the fresh-process LIFE cleanup worker as a standalone runtime component without changing the current post-apply call path yet.

This is deliberately the first half of the self-update fix. The previous v2 run already committed the cleanup-policy correction. Because this overlay does not replace the CLI cleanup dispatcher, its own source artifact is still finalized by the existing in-process path using the v2 policy loaded at process start.

## Delivered invariants

- External consumed apply-source artifacts can be evaluated by a fresh Python process.
- The worker builds the same canonical LIFE ownership view and persists the canonical cleanup receipt.
- Repository-active validation sessions remain protection roots for repository-internal cleanup candidates.
- External consumed apply-source artifacts only bypass repository-global blockers under the v2 descriptor policy; direct LIFE references remain authoritative.

## Continuation

Apply split B next. Split B changes the CLI dispatcher to invoke this worker so future Devtool self-updates never rely on stale imported cleanup modules.
