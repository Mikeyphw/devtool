# EXO TLJ-04 — Target-Local Jobs Execution Affinity

## Scope

This overlay completes the execution-affinity layer for target-local jobs introduced by TLJ-01 through TLJ-03.

## Delivered

- Target-local explicit `root = "..."` values now resolve relative to the owning target root.
- Repository-level jobs keep repository-root-relative root semantics.
- Target-local jobs without an explicit root continue to execute from the owning target root.
- Runtime command metadata now exposes `workflow_effective_cwd`.
- Job command metadata now exposes normalized target-local identity:
  - `workflow_job_scope`
  - `workflow_job_target`
  - `workflow_job_local_name`
  - `workflow_job_key`
  - `workflow_job_target_local`
- Target-local Gradle task shortcuts are verified to run from the target root and keep `gradle_tasks` metadata.
- Environment inheritance and explicit empty-job-env clearing are regression-tested.
- `docs/EXO_TARGET_LOCAL_JOBS.md` documents execution-affinity rules.

## Validation

- `tests/test_exo_target_local_jobs_execution_affinity.py`: 3 passed.
- TLJ focused suite through TLJ-04: 12 passed.
- Broader target/workflow compatibility suite: replayed before packaging.

## Notes

This overlay is non-gate and keeps artifact validation deferred. It preserves the established finalizer hook that refreshes the editable Devtool install while restoring `devtool.egg-info` so overlay commit boundaries stay clean.
