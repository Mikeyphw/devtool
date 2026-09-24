# EXO Target-Local Jobs OV02 — Discovery and Preflight Truth

## Scope

Makes target-local jobs from OV01 truthful across discovery, config inspection, preflight/toolchain analysis, and safe-scheduler path accounting.

## Delivered

- Adds normalized job identity fields to `job_as_dict`:
  - `scope`
  - `target`
  - `local_name`
  - `target_local`
  - `job_key`
- Updates `devtool jobs --json` to emit schema version 2 with both:
  - `jobs`: all executable job keys
  - `target_jobs`: per-target local job names
- Updates `devtool targets --json` and human target rows to expose target-local job names.
- Fixes toolchain/preflight job-root analysis so target-local jobs without an explicit `root` use the owning target root, matching runtime execution and scheduler semantics.
- Extends `docs/EXO_TARGET_LOCAL_JOBS.md` with discovery and preflight usage.
- Adds focused tests for target-root preflight, JSON discovery parity, and scheduler reservation paths.

## Validation

Focused tests:

```text
python -m pytest tests/test_exo_target_local_jobs_schema.py tests/test_exo_target_local_jobs_preflight_discovery.py -q
```

Compatibility slice:

```text
python -m pytest \
  tests/test_exo_target_local_jobs_schema.py \
  tests/test_exo_target_local_jobs_preflight_discovery.py \
  tests/test_mp03_mp04_workflow_graph_runtime.py \
  tests/test_mp05_mp06_cross_target_workspace_groups.py \
  tests/test_mp09_mp10_safe_scheduler_plan.py \
  tests/test_completion_state.py \
  tests/test_rem03_cli_config_discovery.py \
  -q
```

## Notes

This overlay is non-gate. It does not alter wrapper behavior or enable a DustUI migration yet; it tightens the Devtool-side truth model required before project-specific overlays can rely on target-local job contracts.
