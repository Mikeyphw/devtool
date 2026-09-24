# EXO TLJ-08 — Target-Local Jobs Plan Export

## Intent

Make target-local job identity visible in `devtool plan` dry-run output, not only at runtime. This lets an agent inspect a target workflow and confirm which target-owned job will execute before invoking validation, a wrapper, or a cross-target workflow.

## Delivered

- Upgraded workspace `devtool plan --json` payloads to schema version 2.
- Added a plan `summary` object with command/node/wave counts plus repository-job and target-local-job identity summaries.
- Added normalized target-local job identity to every plan command entry:
  - `workflow_target`, `workflow_name`, `workflow_ref`.
  - `resolution.scope`, `resolution.target`, `resolution.local_name`, `resolution.key`.
  - `job.scope`, `job.target`, `job.local_name`, `job.key`, `job.target_local`.
  - top-level `target_local_job` for filtering.
- Human `devtool plan --explain` now shows `job=<scope>:<key>` beside scheduler nodes.
- DOT plan output labels target-local job nodes as `job:<target>.<job>`.
- Added regression tests for JSON, human explain, and DOT plan export surfaces.
- Updated `docs/EXO_TARGET_LOCAL_JOBS.md` with the dry-run plan contract.

## Validation replay

- `tests/test_exo_target_local_jobs_plan_export.py`
- `tests/test_exo_target_local_jobs_inspection.py`
- `tests/test_exo_target_local_jobs_workflow_boundaries.py`
- `tests/test_exo_target_local_jobs_completion_doctor.py`
- `python -m py_compile devtool/cli.py tests/test_exo_target_local_jobs_plan_export.py`

Focused replay result: 13 passed.
