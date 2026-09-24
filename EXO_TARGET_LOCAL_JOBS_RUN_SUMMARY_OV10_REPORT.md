# EXO TLJ-10 — Target-Local Jobs Run Summary Export

## Scope

TLJ-10 closes the runtime reporting gap after plan and preflight exports.
Target-local job identity is now represented in JSON summaries and durable run
logs using the same normalized vocabulary introduced by TLJ-03 through TLJ-09.

## Delivered

- `build_json_summary()` workflow payload upgraded to `schema_version = 2`.
- Workflow nodes now include a normalized `job` object with `scope`, `target`,
  `local_name`, `key`, and `target_local`.
- Workflow JSON now includes `workflow.summary.target_local_jobs[]`,
  `repository_jobs[]`, counts, and targets with target-local jobs.
- Durable run log final summaries now include target-local job counts and compact
  per-job detail lines.
- Added focused run-summary regression tests.

## Validation replay

- `tests/test_exo_target_local_jobs_run_summary_export.py` passed.
- Related TLJ compatibility tests passed.
- `py_compile` and `compileall` passed.
