# EXO TLJ-05 — Target-Local Jobs Workflow Boundaries

## Purpose

TLJ-05 hardens target-local job ownership in workflow DAGs. Previous TLJ overlays made target-local jobs executable and discoverable through internal keys such as `web.assets`. That is useful for machine output, scheduling, and evidence, but it must not turn a target-local job into a foreign target API.

## Delivered

- Blocks foreign target-local job references during config load.
- Keeps same-target references valid by both local and scoped job keys.
- Requires cross-target orchestration to use `target:<target>#<workflow>`.
- Preserves target-local execution affinity when a parent target invokes a child target workflow.
- Adds regression tests for direct foreign references, same-target scoped references, and cross-target workflow composition.
- Updates `docs/EXO_TARGET_LOCAL_JOBS.md` with workflow ownership boundary guidance.

## Validation

- Focused TLJ-05 tests passed.
- TLJ focused/compatibility test slices were replayed from the exact ZIP tree.
- Python compile checks passed.
- Finalize hook simulation preserved packaging metadata.
